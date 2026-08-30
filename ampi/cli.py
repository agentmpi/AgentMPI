"""The ``ampi`` command-line interface: AgentMPI's language binding.

MPI has bindings for C, Fortran and (once) C++. The binding for an LLM agent is
a command-line tool, because that is the only interface an agent reliably has to
a stateful library: it cannot hold a handle across turns, it cannot link against
a shared object, and its "function calls" are shell invocations whose output
lands in its context window.

Designing this binding well turned out to matter as much as designing the
protocol. Four properties earn their complexity:

* **Ambient identity.** Rank and job come from the environment, never from
  arguments. Every early failure we observed involved an agent passing the wrong
  ``--rank``.
* **Terse, structured, agent-readable output.** Human-readable text by default
  (JSON on request), with payload bodies clearly delimited, sizes stated in
  tokens, and -- crucially -- an explicit ``next:`` line telling the agent what
  to do. Agents follow instructions far more reliably than they infer them.
* **Errors that prescribe.** Every error prints its class, a one-line
  explanation, and a concrete command to run next. ``AMPI_ERR_TIMEOUT`` says
  "re-run this exact command".
* **Idempotence by default.** Collectives are joined by ``--label``, sends take
  idempotency keys, and timed-out blocking calls resume rather than restart.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import collectives, ft, launcher, p2p, rma, topology, trace, views
from . import ops as ops_mod
from .core import (
    ANY_SOURCE,
    ANY_TAG,
    Config,
    Ctx,
    bind,
    comm_members,
    comm_row,
    ctx_state,
    detect_failures,
    failed_ranks,
    finalize_rank,
    init_rank,
    resolve_peer,
    resolve_tag,
)
from .errors import RETRYABLE, AmpiError, ArgError, ErrClass, exit_code
from .journal import find_root, now_ns, open_journal
from .version import PROTOCOL_VERSION, __version__

# --------------------------------------------------------------------------
# Output rendering
# --------------------------------------------------------------------------

BODY_OPEN = "----- payload -----"
BODY_CLOSE = "----- end payload -----"


def render(result: Dict[str, Any], *, as_json: bool) -> str:
    if as_json:
        return json.dumps(result, indent=2, ensure_ascii=False)
    return _render_text(result)


_ORDER = [
    "ok", "rank", "epoch", "world_size", "role", "comm", "size", "your_rank",
    "coll", "algo", "op", "root", "index", "source", "source_world", "tag", "seq",
    "handle", "tokens", "mode", "version", "writer", "found", "swapped", "complete",
    "action_required", "step", "round",
]


def _render_text(res: Dict[str, Any]) -> str:
    lines: List[str] = []
    body = res.get("body")
    directive = res.get("directive")
    note = res.get("note")
    nxt = res.get("next")
    hint = res.get("hint")
    skip = {"body", "directive", "note", "next", "hint"}
    skip = skip | {"_identity"}
    keys = [k for k in _ORDER if k in res and k not in skip]
    keys += [k for k in res if k not in keys and k not in skip]
    kv: List[str] = []
    for k in keys:
        v = res[k]
        if v is None or (isinstance(v, (list, dict)) and not v):
            continue
        if isinstance(v, (list, dict)):
            kv.append(f"{k}={json.dumps(v, ensure_ascii=False)}")
        elif isinstance(v, bool):
            kv.append(f"{k}={'true' if v else 'false'}")
        else:
            kv.append(f"{k}={v}")
    ident = res.pop("_identity", None)
    if kv:
        lines.append("AMPI_SUCCESS " + "  ".join(kv))
    else:
        lines.append("AMPI_SUCCESS")
    # Every command echoes who it acted as. Agents asked for this repeatedly and
    # they were right: when identity is ambient, the only defence against it
    # having changed is being told, on every call, who you just were.
    if ident:
        lines.append(f"[acting as rank {ident['rank']} of job {ident['job']}]")
    if body is not None:
        lines.append(BODY_OPEN)
        lines.append(body if isinstance(body, str) else json.dumps(body, indent=2, ensure_ascii=False))
        lines.append(BODY_CLOSE)
    if directive:
        lines.append("")
        lines.append(directive)
    if note:
        lines.append(f"note: {note}")
    if nxt:
        lines.append(f"next: {nxt}")
    if hint:
        lines.append(f"hint: {hint}")
    return "\n".join(lines)


def read_payload(spec: Optional[str], *, required: bool = True, what: str = "--in") -> Optional[str]:
    """Resolve a payload argument: literal text, ``@path``, or ``-`` for stdin."""
    if spec is None:
        if required:
            raise ArgError(
                f"{what} is required",
                hint=f"pass {what} 'literal text', {what} @path/to/file, or {what} - to read stdin",
            )
        return None
    if spec == "-":
        return sys.stdin.read()
    if spec.startswith("@"):
        p = Path(spec[1:]).expanduser()
        if not p.exists():
            raise ArgError(f"no such file: {p}", hint="check the path; use @ only for files")
        return p.read_text(encoding="utf-8")
    return spec


def read_parts(spec: str, n: int) -> List[str]:
    """Resolve a multi-part payload for scatter/alltoall.

    Accepts ``@dir`` (files sorted by name), ``@file.json`` (a JSON array), or a
    literal JSON array.
    """
    if spec.startswith("@"):
        p = Path(spec[1:]).expanduser()
        if p.is_dir():
            files = sorted(x for x in p.iterdir() if x.is_file())
            if len(files) != n:
                raise ArgError(f"directory {p} has {len(files)} files but {n} parts are needed")
            return [f.read_text(encoding="utf-8") for f in files]
        text = p.read_text(encoding="utf-8")
    else:
        text = spec
    try:
        data = json.loads(text)
    except Exception as exc:
        raise ArgError("parts must be a JSON array, a JSON file, or a directory of files") from exc
    if not isinstance(data, list):
        raise ArgError("parts must be a JSON array")
    return [x if isinstance(x, str) else json.dumps(x, ensure_ascii=False) for x in data]


def secs(v: Optional[float]) -> Optional[int]:
    return None if v is None else int(float(v) * 1_000_000_000)


# --------------------------------------------------------------------------
# Argument plumbing
# --------------------------------------------------------------------------


def add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--comm", default="world", help="communicator name (default: world)")
    p.add_argument("--rank", type=int, default=None, help="override the ambient AMPI_RANK (avoid)")
    # Note: `--root` is reserved for the *root rank* of a collective, as in MPI.
    # The job's filesystem root is `--job-root`, and is almost never needed
    # because it is discovered from $AMPI_ROOT or by walking up from the cwd.
    p.add_argument("--job-root", dest="job_root", default=None,
                   help="job root directory; takes precedence over $AMPI_ROOT")
    # Identity assertions. Ambient identity is still the default -- an agent should
    # not have to thread its rank through every call -- but in our multi-agent runs
    # the host's shell sessions were shared and AMPI_RANK was silently rewritten
    # between calls, so every agent's first `init` ran as somebody else. There was
    # no way to say "I intend to be rank 5, fail if the environment disagrees."
    # Now there is, and the rank prompts require it.
    p.add_argument("--expect-rank", dest="expect_rank", type=int, default=None,
                   help="fail with AMPI_ERR_IDENTITY unless the ambient rank is this")
    p.add_argument("--expect-job", dest="expect_job", default=None,
                   help="fail with AMPI_ERR_IDENTITY unless the journal is this job")
    p.add_argument("--json", action="store_true", help="machine-readable output")


def add_payload(p: argparse.ArgumentParser, *, required: bool = False) -> None:
    p.add_argument("--in", dest="payload", default=None,
                   help="payload: literal text, @file, or - for stdin")


def add_wait(p: argparse.ArgumentParser) -> None:
    p.add_argument("--timeout", type=float, default=None, help="deadline in seconds")
    # The pilot run taught us that agents give up retrying far earlier than they
    # are told to: instructed to retry a timed-out call up to 20 times, one rank
    # stopped after two and stalled its whole reduction tree. A protocol that
    # depends on an agent's persistence is not a protocol. So the binding retries
    # internally by default: one shell invocation now covers several deadlines,
    # and giving up requires the agent to do nothing rather than something.
    p.add_argument("--retries", type=int, default=2,
                   help="extra internal attempts after a timeout (default 2)")


def add_recvopts(p: argparse.ArgumentParser) -> None:
    p.add_argument("--materialize", action="store_true", help="read the body into context now")
    p.add_argument("--no-materialize", action="store_true", help="never read the body; give me a handle")
    p.add_argument("--budget", type=int, default=None, help="clip the body to at most N tokens")


def mat_of(a: argparse.Namespace) -> Optional[bool]:
    if getattr(a, "materialize", False):
        return True
    if getattr(a, "no_materialize", False):
        return False
    return None


def _ctx(a: argparse.Namespace, *, require_init: bool = True, beat: bool = True) -> Ctx:
    j = open_journal(a.job_root)
    return bind(
        j, rank=a.rank, comm=a.comm, require_init=require_init, beat=beat,
        expect_rank=getattr(a, "expect_rank", None),
        expect_job=getattr(a, "expect_job", None),
    )


# --------------------------------------------------------------------------
# Command implementations
# --------------------------------------------------------------------------


def cmd_init(a: argparse.Namespace) -> Dict[str, Any]:
    j = open_journal(a.job_root)
    rank = a.rank if a.rank is not None else int(os.environ.get("AMPI_RANK", "-1"))
    if rank < 0:
        raise ArgError("cannot tell which rank you are",
                       hint="set AMPI_RANK in the environment, or pass --rank R")
    from .core import assert_identity

    assert_identity(j, rank, expect_rank=getattr(a, "expect_rank", None),
                    expect_job=getattr(a, "expect_job", None))
    reinit = bool(a.reinit or os.environ.get("AMPI_REINIT"))
    info = init_rank(j, rank, agent_id=(a.agent_id or os.environ.get("AMPI_AGENT_ID")),
                     role=a.role, reinit=reinit)
    members = comm_members(j, a.comm)
    out: Dict[str, Any] = {
        "protocol": PROTOCOL_VERSION,
        "job": j.job_id,
        "comm": a.comm,
        "size": len(members),
        **info,
    }
    if reinit or info["epoch"] > 0:
        brief = ft.recover(j, rank, a.comm)
        p = j.dir / "prompts" / f"rank{rank:04d}.recovery.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(brief, indent=2, ensure_ascii=False), encoding="utf-8")
        out["recovery_brief"] = str(p)
        out["predecessor_epochs"] = info["epoch"]
        out["next"] = (
            f"you are a REPLACEMENT for a failed rank (epoch {info['epoch']}). Read "
            f"{p} to learn what your predecessor was assigned, published and left "
            "outstanding, then continue its work. Do not redo committed work."
        )
    else:
        out["next"] = "run `ampi man` if you have not read the manual, then start your task"
    return out


def cmd_fini(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    finalize_rank(ctx.j, ctx.rank, ctx.epoch, status=a.status)
    return {"rank": ctx.rank, "finalized": True, "status": a.status}


def cmd_info(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a, require_init=False)
    row = comm_row(ctx.j, a.comm)
    st = ctx_state(ctx.j, ctx.rank)
    return {
        "protocol": PROTOCOL_VERSION,
        "runtime": __version__,
        "job": ctx.j.job_id,
        "rank": ctx.rank,
        "epoch": ctx.epoch,
        "comm": str(row["name"]),
        "comm_rank": ctx.crank,
        "size": ctx.size,
        "world_size": int(ctx.j.job_row()["world_size"]),
        "role": ctx.j.q1("SELECT role FROM rank WHERE job=? AND rank=?", (ctx.j.job_id, ctx.rank))["role"],
        "revoked": bool(row["revoked"]),
        "generation": int(row["generation"]),
        "ctx": st,
        "eager_tokens": ctx.cfg.eager_tokens,
        "failed_ranks": failed_ranks(ctx.j, a.comm),
        "root": str(ctx.j.root),
    }


def cmd_whoami(a: argparse.Namespace) -> Dict[str, Any]:
    """Report and optionally assert the caller's identity.

    A one-command answer to "am I who I think I am", which is the question an
    agent in a shared environment most needs to be able to ask cheaply.
    """
    j = open_journal(a.job_root)
    env_rank = os.environ.get("AMPI_RANK")
    rank = a.rank if a.rank is not None else (int(env_rank) if env_rank not in (None, "") else None)
    out: Dict[str, Any] = {
        "job": j.job_id,
        "root": str(j.root),
        "ambient_rank": rank,
        "env_AMPI_RANK": env_rank,
        "env_AMPI_ROOT": os.environ.get("AMPI_ROOT"),
        "env_AMPI_TOKEN_set": bool(os.environ.get("AMPI_TOKEN")),
    }
    if rank is None:
        out["note"] = "AMPI_RANK is not set; no ambient identity"
        return out
    from .core import assert_identity

    assert_identity(j, rank, expect_rank=a.expect_rank, expect_job=a.expect_job)
    row = j.q1("SELECT state, epoch, role, calls FROM rank WHERE job=? AND rank=?",
               (j.job_id, rank))
    if row is not None:
        out.update({"state": str(row["state"]), "epoch": int(row["epoch"]),
                    "role": row["role"], "calls": int(row["calls"])})
    inits = int(j.scalar("SELECT COUNT(*) FROM event WHERE job=? AND rank=? AND kind='init'",
                         (j.job_id, rank), 0))
    out["inits_seen"] = inits
    if inits > 1:
        out["warning"] = (
            f"rank {rank} has been initialised {inits} times. If you did not retry, "
            "another agent has acted as you, or you are acting as another agent."
        )
    out["next"] = (
        "pass --expect-rank on every command, and prefix AMPI_RANK/AMPI_ROOT inline "
        "rather than relying on the shell session to keep them"
    )
    return out


def cmd_man(a: argparse.Namespace) -> Dict[str, Any]:
    for cand in (
        Path(__file__).resolve().parent.parent / "bindings" / "AGENT_GUIDE.md",
        find_root(a.job_root) / ".ampi" / "AGENT_GUIDE.md",
    ):
        if cand.exists():
            return {"body": cand.read_text(encoding="utf-8")}
    return {"body": launcher.QUICKREF}


def cmd_hb(a: argparse.Namespace) -> Dict[str, Any]:
    from .core import extend_lease

    ctx = _ctx(a, require_init=False, beat=False)
    with ctx.j.tx() as c:
        res = extend_lease(ctx.j, ctx.rank, ctx.epoch, float(a.extend or 0.0), conn=c)
    res["note"] = (
        "your lease now lasts at least this long without another call. Run this "
        "before any step that will take more than a minute."
    )
    return res


def cmd_ctx(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a, require_init=False)
    if a.grant:
        with ctx.j.tx() as c:
            c.execute(
                "UPDATE rank SET ctx_budget=ctx_budget+? WHERE job=? AND rank=?",
                (int(a.grant), ctx.j.job_id, ctx.rank),
            )
    if a.release:
        from .core import ctx_release

        ctx_release(ctx.j, ctx.rank, ctx.epoch, int(a.release))
    st = ctx_state(ctx.j, ctx.rank)
    frac = st["used"] / st["budget"] if st["budget"] else 0.0
    out = {"rank": ctx.rank, **st, "used_fraction": round(frac, 3)}
    if frac > 0.8:
        out["note"] = (
            "you are near your context budget. Stop materialising payloads: use "
            "`ampi view <handle> --budget 400`, summarise your own state into a window cell, "
            "and record continuation notes with `ampi memo put`."
        )
    return out


# ---- point to point -------------------------------------------------------


def cmd_send(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    text = read_payload(a.payload)
    dest = resolve_peer(ctx, a.to)
    tag = resolve_tag(a.tag)
    res = p2p.send(
        ctx, dest, tag, text or "", mode=a.mode, schema=a.schema, idem=a.idem,
        timeout_ns=secs(a.timeout),
    )
    res["to"] = dest
    res["tag"] = tag
    if res.get("duplicate"):
        res["note"] = "identical idempotency key seen before; not sent twice"
    return res


def cmd_recv(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    src = resolve_peer(ctx, a.frm) if a.frm else ANY_SOURCE
    tag = resolve_tag(a.tag) if a.tag else ANY_TAG
    res = p2p.recv(
        ctx, src, tag, timeout_ns=secs(a.timeout), materialize=mat_of(a), budget=a.budget
    )
    if "body" not in res:
        res["next"] = f"read it when you need it: ampi view {res['handle']} --budget 800"
    return res


def cmd_probe(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    src = resolve_peer(ctx, a.frm) if a.frm else ANY_SOURCE
    tag = resolve_tag(a.tag) if a.tag else ANY_TAG
    res = p2p.probe(ctx, src, tag, blocking=a.blocking, timeout_ns=secs(a.timeout))
    if res is None:
        return {"waiting": False, "note": "nothing matching is queued for you right now"}
    return {"waiting": True, **res}


def cmd_inbox(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    items = p2p.pending(ctx, limit=a.limit)
    return {
        "count": len(items),
        "messages": items,
        "total_tokens": sum(i["tokens"] for i in items),
        "note": ("nothing pending" if not items else
                 "these have NOT entered your context; `ampi recv` to take one"),
    }


def cmd_isend(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    return p2p.isend(ctx, resolve_peer(ctx, a.to), resolve_tag(a.tag),
                     read_payload(a.payload) or "", schema=a.schema, idem=a.idem)


def cmd_irecv(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    src = resolve_peer(ctx, a.frm) if a.frm else ANY_SOURCE
    tag = resolve_tag(a.tag) if a.tag else ANY_TAG
    res = p2p.irecv(ctx, src, tag, materialize=mat_of(a))
    res["next"] = f"go do other work, then: ampi wait {res['request']}"
    return res


def cmd_wait(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    return p2p.wait(ctx, a.requests, mode=a.mode, timeout_ns=secs(a.timeout),
                    materialize=mat_of(a))


def cmd_test(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    return p2p.test(ctx, a.request, materialize=mat_of(a))


def cmd_cancel(a: argparse.Namespace) -> Dict[str, Any]:
    return p2p.cancel(_ctx(a), a.request)


def cmd_req_list(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    rows = ctx.j.q(
        "SELECT id,op,peer,tag,state,created_ns FROM request WHERE job=? AND rank=? ORDER BY created_ns",
        (ctx.j.job_id, ctx.rank),
    )
    return {
        "requests": [
            {"request": str(r["id"]), "op": str(r["op"]), "peer": r["peer"], "tag": r["tag"],
             "state": str(r["state"]),
             "age_s": round((now_ns() - int(r["created_ns"])) / 1e9, 1)}
            for r in rows
        ]
    }


# ---- collectives ----------------------------------------------------------


def cmd_barrier(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    return collectives.barrier(ctx, label=a.label, algo=a.algo, quorum=a.quorum,
                               timeout_ns=secs(a.timeout))


def cmd_bcast(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    root = int(a.root)
    text = read_payload(a.payload, required=(ctx.crank == root))
    return collectives.bcast(ctx, root=root, text=text, label=a.label, algo=a.algo,
                             timeout_ns=secs(a.timeout), materialize=mat_of(a),
                             budget=a.budget, schema=a.schema)


def cmd_bcast_relay_forward(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    return collectives.relay_forward(ctx, a.coll, read_payload(a.payload) or "")


def cmd_scatter(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    root = int(a.root)
    parts = read_parts(a.parts, ctx.size) if (ctx.crank == root and a.parts) else None
    return collectives.scatter(ctx, root=root, parts=parts, label=a.label, algo=a.algo,
                               timeout_ns=secs(a.timeout), materialize=mat_of(a), budget=a.budget)


def cmd_gather(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    return collectives.gather(ctx, text=read_payload(a.payload) or "", root=int(a.root),
                              all_=False, label=a.label, algo=a.algo, quorum=a.quorum,
                              timeout_ns=secs(a.timeout), budget=a.budget,
                              materialize=mat_of(a))


def cmd_allgather(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    return collectives.gather(ctx, text=read_payload(a.payload) or "", root=0, all_=True,
                              label=a.label, algo=a.algo, quorum=a.quorum,
                              timeout_ns=secs(a.timeout), budget=a.budget,
                              materialize=mat_of(a))


def cmd_reduce(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    return collectives.reduce_(
        ctx, op=a.op, text=read_payload(a.payload, required=False), root=int(a.root),
        label=a.label, algo=a.algo, all_=False, commute=_commute(a),
        timeout_ns=secs(a.timeout), quorum=a.quorum, materialize=mat_of(a),
        budget=a.budget, operand_budget=a.operand_budget,
    )


def cmd_allreduce(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    return collectives.reduce_(
        ctx, op=a.op, text=read_payload(a.payload, required=False), root=int(a.root),
        label=a.label, algo=a.algo, all_=True, commute=_commute(a),
        timeout_ns=secs(a.timeout), quorum=a.quorum, materialize=mat_of(a),
        budget=a.budget, operand_budget=a.operand_budget,
    )


def _commute(a: argparse.Namespace) -> Optional[bool]:
    if getattr(a, "commute", False):
        return True
    if getattr(a, "no_commute", False):
        return False
    return None


def cmd_reduce_commit(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    res = collectives.reduce_commit(ctx, a.step, read_payload(a.payload) or "",
                                    materialize=mat_of(a), budget=a.budget,
                                    timeout_ns=secs(a.timeout))
    step = ctx.j.q1("SELECT coll FROM reduce_step WHERE id=?", (a.step,))
    if step is not None:
        coll = ctx.j.q1("SELECT * FROM coll WHERE id=?", (step["coll"],))
        if coll is not None and str(coll["op"]) in ("scan", "exscan"):
            return collectives._resume_scan_after_commit(ctx, coll)
    return res


def cmd_scan(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    return collectives.scan(
        ctx, op=a.op, text=read_payload(a.payload) or "", exclusive=a.exclusive,
        label=a.label, algo=a.algo, commute=_commute(a), timeout_ns=secs(a.timeout),
        materialize=mat_of(a), budget=a.budget, operand_budget=a.operand_budget,
    )


def cmd_alltoall(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    return collectives.alltoall(ctx, parts=read_parts(a.parts, ctx.size), label=a.label,
                                algo=a.algo, timeout_ns=secs(a.timeout), budget=a.budget)


def cmd_reduce_scatter(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    return collectives.reduce_scatter(ctx, op=a.op, parts=read_parts(a.parts, ctx.size),
                                      label=a.label, timeout_ns=secs(a.timeout),
                                      materialize=mat_of(a))


def cmd_icoll(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    params: Dict[str, Any] = {
        "label": a.label, "root": int(a.root) if a.root is not None else None,
        "algo": a.algo, "quorum": a.quorum, "op": getattr(a, "op", None),
        "text": read_payload(a.payload, required=False),
    }
    res = collectives.icollective(ctx, a.collective, params)
    res["next"] = f"do independent work, then: ampi wait {res['request']}"
    return res


# ---- windows --------------------------------------------------------------


def cmd_win_create(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    return rma.create(ctx, a.name, model=a.model)


def cmd_win_put(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    return rma.put(ctx, a.win, a.key, read_payload(a.payload) or "",
                   expect_version=a.expect_version, schema=a.schema, note=a.note)


def cmd_win_get(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    return rma.get(ctx, a.win, a.key, materialize=mat_of(a), budget=a.budget,
                   view=a.view, version=a.version)


def cmd_win_acc(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    return rma.accumulate(ctx, a.win, a.key, read_payload(a.payload) or "", op=a.op, note=a.note)


def cmd_win_cas(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    res = rma.compare_and_swap(ctx, a.win, a.key, expect=a.expect, value=a.value)
    res["note"] = ("you own it" if res["swapped"] else
                   f"lost the race; current value is {res['previous'][:120]!r}")
    return res


def cmd_win_faop(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    return rma.fetch_and_op(ctx, a.win, a.key, op=a.op, value=a.value)


def cmd_win_ls(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    return rma.listing(ctx, a.win, prefix=a.prefix, limit=a.limit)


def cmd_win_hist(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    return rma.history(ctx, a.win, a.key, limit=a.limit)


def cmd_win_lock(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    res = rma.lock(ctx, a.win, key=a.key, mode=a.mode, timeout_ns=secs(a.timeout))
    res["next"] = f"do your writes, then: ampi win unlock --win {a.win} --key {a.key}"
    return res


def cmd_win_unlock(a: argparse.Namespace) -> Dict[str, Any]:
    return rma.unlock(_ctx(a), a.win, key=a.key)


def cmd_win_fence(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    return rma.fence(ctx, a.win, label=a.label, timeout_ns=secs(a.timeout), quorum=a.quorum)


def cmd_win_list(a: argparse.Namespace) -> Dict[str, Any]:
    j = open_journal(a.job_root)
    rows = j.q("SELECT id,name,comm,created_ns FROM win WHERE job=? ORDER BY created_ns", (j.job_id,))
    return {"windows": [{"win": str(r["id"]), "name": str(r["name"])} for r in rows]}


# ---- communicators --------------------------------------------------------


def cmd_comm_list(a: argparse.Namespace) -> Dict[str, Any]:
    j = open_journal(a.job_root)
    rows = j.q("SELECT * FROM comm WHERE job=? ORDER BY created_ns", (j.job_id,))
    return {
        "communicators": [
            {"name": str(r["name"]), "size": int(r["size"]), "kind": str(r["kind"]),
             "generation": int(r["generation"]), "revoked": bool(r["revoked"]),
             "members_world": comm_members(j, str(r["id"]))[:64]}
            for r in rows
        ]
    }


def cmd_comm_split(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    color = None if a.color is None or str(a.color).lower() in ("none", "undefined") else int(a.color)
    return topology.split(ctx, color=color, key=a.key, name=a.name, timeout_ns=secs(a.timeout))


def cmd_comm_create(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    return topology.create_from_group(ctx, members=[int(x) for x in a.members.split(",")], name=a.name)


def cmd_comm_dup(a: argparse.Namespace) -> Dict[str, Any]:
    return topology.dup(_ctx(a), name=a.name)


def cmd_comm_cart(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    dims = [int(x) for x in a.dims.split(",")]
    per = [x.strip().lower() in ("1", "true", "yes") for x in (a.periodic or ",".join(["false"] * len(dims))).split(",")]
    return topology.cart_create(ctx, dims=dims, periodic=per, name=a.name)


def cmd_comm_shift(a: argparse.Namespace) -> Dict[str, Any]:
    return topology.cart_shift(_ctx(a), direction=a.direction, disp=a.disp)


def cmd_comm_graph(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    edges = json.loads(read_payload(a.payload) or "{}")
    return topology.graph_create(ctx, edges={int(k): v for k, v in edges.items()}, name=a.name)


def cmd_comm_neighbors(a: argparse.Namespace) -> Dict[str, Any]:
    return topology.neighbors(_ctx(a))


def cmd_neighbor_allgather(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    return topology.neighbor_allgather(ctx, text=read_payload(a.payload) or "", label=a.label,
                                       timeout_ns=secs(a.timeout), budget=a.budget)


def cmd_comm_revoke(a: argparse.Namespace) -> Dict[str, Any]:
    return ft.revoke(_ctx(a), reason=a.reason)


def cmd_comm_shrink(a: argparse.Namespace) -> Dict[str, Any]:
    return ft.shrink(_ctx(a), name=a.name, timeout_ns=secs(a.timeout), quorum=a.quorum)


# ---- fault tolerance ------------------------------------------------------


def cmd_agree(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    flag = str(a.flag).lower() not in ("false", "0", "no")
    return ft.agree(ctx, label=a.label, flag=flag, value=a.value, timeout_ns=secs(a.timeout),
                    quorum=a.quorum)


def cmd_failed(a: argparse.Namespace) -> Dict[str, Any]:
    return ft.get_failed(_ctx(a, require_init=False))


def cmd_ack(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a)
    res = ft.failure_ack(ctx)
    res["next"] = "wildcard receives on this communicator will work again"
    return res


def cmd_kill(a: argparse.Namespace) -> Dict[str, Any]:
    j = open_journal(a.job_root)
    return ft.declare_failed(j, int(a.target), kind=a.kind, by=a.rank,
                             detail={"injected": True, "note": a.note})


def cmd_respawn(a: argparse.Namespace) -> Dict[str, Any]:
    j = open_journal(a.job_root)
    info = ft.respawn(j, int(a.target), role=a.role)
    brief = ft.recover(j, int(a.target), a.comm)
    p = j.dir / "prompts" / f"rank{int(a.target):04d}.recovery.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(brief, indent=2, ensure_ascii=False), encoding="utf-8")
    info["recovery_brief"] = str(p)
    return info


def cmd_recover(a: argparse.Namespace) -> Dict[str, Any]:
    j = open_journal(a.job_root)
    rank = a.rank if a.rank is not None else int(os.environ.get("AMPI_RANK", "-1"))
    brief = ft.recover(j, rank, a.comm)
    if a.out:
        Path(a.out).write_text(json.dumps(brief, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"written": a.out, "rank": rank}
    return brief


def cmd_memo(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a, require_init=False)
    if a.action == "put":
        if a.value is None:
            raise ArgError("memo put needs a VALUE")
        return ft.memo_put(ctx.j, ctx.rank, ctx.epoch, a.key, read_payload(a.value) or "")
    return ft.memo_get(ctx.j, ctx.rank, a.key)


# ---- objects and views ----------------------------------------------------


def cmd_view(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a, require_init=False)
    spec = views.parse_spec(a.op or ("full" if not a.budget else f"headtail:{a.budget}"))
    if a.budget:
        spec["budget"] = int(a.budget)
    meta = ctx.j.object_meta(a.handle)
    v = views.render_view(ctx.j, a.handle, spec)
    from .core import ctx_charge

    if a.out:
        # Writing to disk costs no context, because nothing enters the window.
        # Several agents worked around the absence of this by reaching into the
        # object store directly rather than pay to see what was already a file.
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(v["body"], encoding="utf-8")
        return {
            "handle": a.handle,
            "view": v["id"],
            "op": spec["op"],
            "payload_tokens": int(meta["tokens"]),
            "view_tokens": v["tokens"],
            "context_charged": 0,
            "written": a.out,
            "note": "written to disk; nothing entered your context",
        }
    with ctx.j.tx() as c:
        ctx_charge(ctx.j, ctx.rank, ctx.epoch, v["tokens"], conn=c, force=True, what="view")
    return {
        "handle": a.handle,
        "view": v["id"],
        "op": spec["op"],
        "payload_tokens": int(meta["tokens"]),
        "view_tokens": v["tokens"],
        "context_charged": v["tokens"],
        "body": v["body"],
    }


def cmd_obj(a: argparse.Namespace) -> Dict[str, Any]:
    ctx = _ctx(a, require_init=False)
    if a.save:
        Path(a.save).write_text(ctx.j.object_text(a.handle), encoding="utf-8")
        return {"handle": a.handle, "saved": a.save, "note": "written to disk without entering your context"}
    return ctx.j.object_meta(a.handle)


def cmd_ops(a: argparse.Namespace) -> Dict[str, Any]:
    return {"ops": ops_mod.describe_ops(), "algorithms": collectives.ALGORITHMS,
            "defaults": collectives.DEFAULT_ALGO}


# ---- job level ------------------------------------------------------------


def cmd_status(a: argparse.Namespace) -> Dict[str, Any]:
    j = open_journal(a.job_root)
    with j.tx() as c:
        detect_failures(j, conn=c)
    return launcher.status(j, comm=a.comm)


def cmd_trace(a: argparse.Namespace) -> Dict[str, Any]:
    j = open_journal(a.job_root)
    if a.timeline:
        return {"body": trace.text_timeline(j, width=a.width)}
    data = trace.export(j, limit=a.limit)
    if a.out:
        Path(a.out).write_text(json.dumps(data, indent=(2 if a.pretty else None), ensure_ascii=False),
                               encoding="utf-8")
        return {"written": a.out, "events": len(data["events"]), "messages": len(data["messages"])}
    if a.summary:
        return data["summary"]
    return data


def cmd_run(a: argparse.Namespace) -> Dict[str, Any]:
    spec_path = Path(a.spec) if a.spec else None
    root = Path(a.job_root or os.getcwd()).resolve()
    if spec_path:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        root = Path(spec.get("root", root)).resolve()
        ranks = [
            launcher.RankSpec(
                rank=int(r["rank"]), role=r.get("role", "worker"), task=r.get("task", ""),
                env={k: str(v) for k, v in (r.get("env") or {}).items()},
            )
            for r in spec["ranks"]
        ]
        cfg = Config.from_dict(spec.get("config") or {})
        return launcher.create(
            root, np=len(ranks), label=spec.get("label", "job"), ranks=ranks, cfg=cfg,
            preamble=spec.get("preamble", ""), fresh=not a.keep,
        )
    if not a.np:
        raise ArgError("give --np N or --spec FILE")
    cfg = Config()
    if a.eager_tokens:
        cfg.eager_tokens = a.eager_tokens
    if a.ctx_budget:
        cfg.ctx_budget = a.ctx_budget
    if a.lease:
        cfg.lease_ns = secs(a.lease) or cfg.lease_ns
    return launcher.create(root, np=int(a.np), label=a.label or "job", cfg=cfg, fresh=not a.keep)


def cmd_supervise(a: argparse.Namespace) -> Dict[str, Any]:
    j = open_journal(a.job_root)
    return launcher.supervise(j, policy=a.policy, max_restarts=a.max_restarts)


def cmd_bench(a: argparse.Namespace) -> Dict[str, Any]:
    from . import bench

    return bench.run(a)


def cmd_serve(a: argparse.Namespace) -> Dict[str, Any]:
    from . import server

    return server.serve(a)


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ampi",
        description=f"AgentMPI {PROTOCOL_VERSION} reference runtime {__version__}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="run `ampi man` for the full protocol manual",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def S(name: str, help_: str, fn: Callable[..., Dict[str, Any]], *, common: bool = True) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_)
        if common:
            add_common(sp)
        sp.set_defaults(func=fn)
        return sp

    # lifecycle
    sp = S("init", "join the job (AMPI_Init)", cmd_init)
    sp.add_argument("--role", default=None)
    sp.add_argument("--agent-id", default=None)
    sp.add_argument("--reinit", action="store_true", help="I am a replacement for a failed rank")
    sp = S("fini", "leave the job (AMPI_Finalize)", cmd_fini)
    sp.add_argument("--status", default="ok")
    S("info", "my identity and the shape of the job", cmd_info)
    S("whoami", "who am I, and assert it", cmd_whoami)
    S("man", "print the full protocol manual", cmd_man)
    sp = S("hb", "renew/extend my lease before a long step", cmd_hb)
    sp.add_argument("--extend", type=float, default=600.0,
                    help="guarantee my lease survives at least this many more seconds")
    sp = S("ctx", "my context budget", cmd_ctx)
    sp.add_argument("--grant", type=int, default=None, help="raise my budget by N tokens")
    sp.add_argument("--release", type=int, default=None, help="return N tokens after compacting")

    # point to point
    sp = S("send", "AMPI_Send", cmd_send)
    sp.add_argument("--to", required=True)
    sp.add_argument("--tag", default="0")
    sp.add_argument("--mode", choices=["standard", "sync", "ready"], default="standard")
    sp.add_argument("--schema", default=None, help="declare the payload's shape (free text or JSON schema)")
    sp.add_argument("--idem", default=None, help="idempotency key: a retry with the same key is a no-op")
    add_payload(sp)
    add_wait(sp)

    sp = S("recv", "AMPI_Recv", cmd_recv)
    sp.add_argument("--from", dest="frm", default=None, help="source rank, or 'any'")
    sp.add_argument("--tag", default=None, help="tag, or 'any'")
    add_wait(sp)
    add_recvopts(sp)

    sp = S("probe", "AMPI_Probe / AMPI_Iprobe", cmd_probe)
    sp.add_argument("--from", dest="frm", default=None)
    sp.add_argument("--tag", default=None)
    sp.add_argument("--blocking", action="store_true")
    add_wait(sp)

    sp = S("inbox", "list pending messages without receiving them", cmd_inbox)
    sp.add_argument("--limit", type=int, default=50)

    sp = S("isend", "AMPI_Isend", cmd_isend)
    sp.add_argument("--to", required=True)
    sp.add_argument("--tag", default="0")
    sp.add_argument("--schema", default=None)
    sp.add_argument("--idem", default=None)
    add_payload(sp)

    sp = S("irecv", "AMPI_Irecv", cmd_irecv)
    sp.add_argument("--from", dest="frm", default=None)
    sp.add_argument("--tag", default=None)
    add_recvopts(sp)

    sp = S("wait", "AMPI_Wait / Waitall / Waitany", cmd_wait)
    sp.add_argument("requests", nargs="+")
    sp.add_argument("--mode", choices=["all", "any", "some"], default="all")
    add_wait(sp)
    add_recvopts(sp)

    sp = S("test", "AMPI_Test", cmd_test)
    sp.add_argument("request")
    add_recvopts(sp)

    sp = S("cancel", "AMPI_Cancel", cmd_cancel)
    sp.add_argument("request")

    # collectives
    sp = S("barrier", "AMPI_Barrier", cmd_barrier)
    sp.add_argument("--label", default=None, help="the collective's name; strongly recommended")
    sp.add_argument("--algo", default=None, choices=collectives.ALGORITHMS["barrier"] + ["auto"])
    sp.add_argument("--quorum", type=float, default=None, help="release at this fraction of live ranks")
    add_wait(sp)

    sp = S("bcast", "AMPI_Bcast", cmd_bcast)
    sp.add_argument("--root", default="0")
    sp.add_argument("--label", default=None)
    sp.add_argument("--algo", default=None, choices=collectives.ALGORITHMS["bcast"] + ["auto"])
    sp.add_argument("--schema", default=None)
    add_payload(sp)
    add_wait(sp)
    add_recvopts(sp)
    bs = sub.add_parser("bcast-relay-forward", help="forward an adapted brief in a relay bcast")
    add_common(bs)
    bs.add_argument("--coll", required=True)
    add_payload(bs)
    bs.set_defaults(func=cmd_bcast_relay_forward)

    sp = S("scatter", "AMPI_Scatter", cmd_scatter)
    sp.add_argument("--root", default="0")
    sp.add_argument("--parts", default=None, help="@dir, @file.json, or a literal JSON array")
    sp.add_argument("--label", default=None)
    sp.add_argument("--algo", default=None, choices=collectives.ALGORITHMS["scatter"] + ["auto"])
    add_wait(sp)
    add_recvopts(sp)

    for nm, fn, help_ in (("gather", cmd_gather, "AMPI_Gather"),
                          ("allgather", cmd_allgather, "AMPI_Allgather")):
        sp = S(nm, help_, fn)
        sp.add_argument("--root", default="0")
        sp.add_argument("--label", default=None)
        sp.add_argument("--algo", default=None,
                        choices=collectives.ALGORITHMS["allgather" if nm == "allgather" else "gather"] + ["auto"])
        sp.add_argument("--quorum", type=float, default=None)
        add_payload(sp)
        add_wait(sp)
        add_recvopts(sp)

    for nm, fn, help_ in (("reduce", cmd_reduce, "AMPI_Reduce"),
                          ("allreduce", cmd_allreduce, "AMPI_Allreduce")):
        sp = S(nm, help_, fn)
        sp.add_argument("--op", required=True, help="built-in op or agent:<label> (see `ampi ops`)")
        sp.add_argument("--root", default="0")
        sp.add_argument("--label", default=None)
        sp.add_argument("--algo", default=None,
                        choices=collectives.ALGORITHMS["allreduce" if nm == "allreduce" else "reduce"] + ["auto"])
        sp.add_argument("--quorum", type=float, default=None)
        sp.add_argument("--commute", action="store_true", help="the operator is order-insensitive")
        sp.add_argument("--no-commute", action="store_true", help="pin a canonical tree (default)")
        sp.add_argument("--operand-budget", type=int, default=None,
                        help="clip each merge operand to N tokens")
        add_payload(sp)
        add_wait(sp)
        add_recvopts(sp)

    rc = sub.add_parser("reduce-commit", help="commit an agent-evaluated merge step")
    add_common(rc)
    rc.add_argument("--step", required=True)
    add_payload(rc)
    add_wait(rc)
    add_recvopts(rc)
    rc.set_defaults(func=cmd_reduce_commit)

    for nm, excl in (("scan", False), ("exscan", True)):
        sp = S(nm, f"AMPI_{'Exscan' if excl else 'Scan'} (prefix reduction)", cmd_scan)
        sp.add_argument("--op", required=True)
        sp.add_argument("--label", default=None)
        sp.add_argument("--algo", default=None, choices=collectives.ALGORITHMS[nm] + ["auto"])
        sp.add_argument("--commute", action="store_true")
        sp.add_argument("--no-commute", action="store_true")
        sp.add_argument("--operand-budget", type=int, default=None)
        add_payload(sp)
        add_wait(sp)
        add_recvopts(sp)
        sp.set_defaults(exclusive=excl)

    sp = S("alltoall", "AMPI_Alltoall", cmd_alltoall)
    sp.add_argument("--parts", required=True)
    sp.add_argument("--label", default=None)
    sp.add_argument("--algo", default=None, choices=collectives.ALGORITHMS["alltoall"] + ["auto"])
    add_wait(sp)
    sp.add_argument("--budget", type=int, default=None)

    sp = S("reduce-scatter", "AMPI_Reduce_scatter", cmd_reduce_scatter)
    sp.add_argument("--op", required=True)
    sp.add_argument("--parts", required=True)
    sp.add_argument("--label", default=None)
    add_wait(sp)
    add_recvopts(sp)

    sp = S("icoll", "nonblocking collective (AMPI_Ibarrier / Ibcast / ...)", cmd_icoll)
    sp.add_argument("collective", choices=list(collectives.ALGORITHMS))
    sp.add_argument("--label", default=None)
    sp.add_argument("--root", default=None)
    sp.add_argument("--algo", default=None)
    sp.add_argument("--op", default=None)
    sp.add_argument("--quorum", type=float, default=None)
    add_payload(sp)

    # windows
    win = sub.add_parser("win", help="one-sided shared state (windows)")
    wsub = win.add_subparsers(dest="wcmd", required=True)

    def W(name: str, help_: str, fn: Callable[..., Dict[str, Any]]) -> argparse.ArgumentParser:
        sp = wsub.add_parser(name, help=help_)
        add_common(sp)
        sp.set_defaults(func=fn)
        return sp

    sp = W("create", "AMPI_Win_create", cmd_win_create)
    sp.add_argument("--name", required=True)
    sp.add_argument("--model", choices=["unified", "separate"], default="unified")
    sp = W("put", "AMPI_Put", cmd_win_put)
    sp.add_argument("--win", required=True)
    sp.add_argument("--key", required=True)
    sp.add_argument("--expect-version", type=int, default=None,
                    help="only write if the cell is still at this version")
    sp.add_argument("--schema", default=None)
    sp.add_argument("--note", default=None)
    add_payload(sp)
    sp = W("get", "AMPI_Get", cmd_win_get)
    sp.add_argument("--win", required=True)
    sp.add_argument("--key", required=True)
    sp.add_argument("--view", default=None, help="view spec, e.g. head:600 or keys:a,b")
    sp.add_argument("--version", type=int, default=None)
    add_recvopts(sp)
    sp = W("acc", "AMPI_Accumulate", cmd_win_acc)
    sp.add_argument("--win", required=True)
    sp.add_argument("--key", required=True)
    sp.add_argument("--op", default="union")
    sp.add_argument("--note", default=None)
    add_payload(sp)
    sp = W("cas", "AMPI_Compare_and_swap", cmd_win_cas)
    sp.add_argument("--win", required=True)
    sp.add_argument("--key", required=True)
    sp.add_argument("--expect", required=True)
    sp.add_argument("--value", required=True)
    sp = W("faop", "AMPI_Fetch_and_op", cmd_win_faop)
    sp.add_argument("--win", required=True)
    sp.add_argument("--key", required=True)
    sp.add_argument("--op", default="sum")
    sp.add_argument("--value", default="1")
    sp = W("ls", "list keys without reading them", cmd_win_ls)
    sp.add_argument("--win", required=True)
    sp.add_argument("--prefix", default=None)
    sp.add_argument("--limit", type=int, default=200)
    sp = W("hist", "version history of a cell", cmd_win_hist)
    sp.add_argument("--win", required=True)
    sp.add_argument("--key", required=True)
    sp.add_argument("--limit", type=int, default=20)
    sp = W("lock", "AMPI_Win_lock", cmd_win_lock)
    sp.add_argument("--win", required=True)
    sp.add_argument("--key", default="*")
    sp.add_argument("--mode", choices=["exclusive", "shared"], default="exclusive")
    add_wait(sp)
    sp = W("unlock", "AMPI_Win_unlock", cmd_win_unlock)
    sp.add_argument("--win", required=True)
    sp.add_argument("--key", default="*")
    sp = W("fence", "AMPI_Win_fence", cmd_win_fence)
    sp.add_argument("--win", required=True)
    sp.add_argument("--label", default=None)
    sp.add_argument("--quorum", type=float, default=None)
    add_wait(sp)
    W("list", "list windows", cmd_win_list)

    # communicators
    comm = sub.add_parser("comm", help="communicators, groups and topologies")
    csub = comm.add_subparsers(dest="ccmd", required=True)

    def C(name: str, help_: str, fn: Callable[..., Dict[str, Any]]) -> argparse.ArgumentParser:
        sp = csub.add_parser(name, help=help_)
        add_common(sp)
        sp.set_defaults(func=fn)
        return sp

    C("list", "list communicators", cmd_comm_list)
    sp = C("split", "AMPI_Comm_split", cmd_comm_split)
    sp.add_argument("--color", default=None, help="integer colour, or 'none' for AMPI_UNDEFINED")
    sp.add_argument("--key", type=int, default=None)
    sp.add_argument("--name", default=None)
    add_wait(sp)
    sp = C("create", "AMPI_Comm_create from an explicit group", cmd_comm_create)
    sp.add_argument("--members", required=True, help="comma-separated world ranks")
    sp.add_argument("--name", required=True)
    sp = C("dup", "AMPI_Comm_dup", cmd_comm_dup)
    sp.add_argument("--name", required=True)
    sp = C("cart", "AMPI_Cart_create", cmd_comm_cart)
    sp.add_argument("--dims", required=True, help="e.g. 4,3")
    sp.add_argument("--periodic", default=None, help="e.g. false,true")
    sp.add_argument("--name", default=None)
    sp = C("shift", "AMPI_Cart_shift", cmd_comm_shift)
    sp.add_argument("--direction", type=int, default=0)
    sp.add_argument("--disp", type=int, default=1)
    sp = C("graph", "AMPI_Dist_graph_create", cmd_comm_graph)
    sp.add_argument("--name", default=None)
    add_payload(sp)
    C("neighbors", "my declared neighbours", cmd_comm_neighbors)
    sp = C("revoke", "AMPI_Comm_revoke", cmd_comm_revoke)
    sp.add_argument("--reason", default=None)
    sp = C("shrink", "AMPI_Comm_shrink", cmd_comm_shrink)
    sp.add_argument("--name", default=None)
    sp.add_argument("--quorum", type=float, default=None)
    add_wait(sp)

    sp = S("neighbor-allgather", "AMPI_Neighbor_allgather", cmd_neighbor_allgather)
    sp.add_argument("--label", default=None)
    add_payload(sp)
    add_wait(sp)
    sp.add_argument("--budget", type=int, default=None)

    # fault tolerance
    sp = S("agree", "AMPI_Comm_agree", cmd_agree)
    sp.add_argument("--label", required=True)
    sp.add_argument("--flag", default="true")
    sp.add_argument("--value", default=None)
    sp.add_argument("--quorum", type=float, default=None)
    add_wait(sp)
    S("failed", "who has failed (AMPI_Comm_get_failed)", cmd_failed)
    S("ack", "AMPI_Comm_failure_ack", cmd_ack)
    sp = S("kill", "declare a rank failed (fault injection / supervisor)", cmd_kill)
    sp.add_argument("target", type=int)
    sp.add_argument("--kind", default="killed", choices=list(ft.FAILURE_KINDS))
    sp.add_argument("--note", default=None)
    sp = S("respawn", "prepare a replacement for a failed rank", cmd_respawn)
    sp.add_argument("target", type=int)
    sp.add_argument("--role", default=None)
    sp = S("recover", "the recovery briefing for a rank", cmd_recover)
    sp.add_argument("--out", default=None)
    sp = S("memo", "durable notes for my replacement", cmd_memo)
    sp.add_argument("action", choices=["put", "get"])
    sp.add_argument("key", nargs="?", default=None)
    sp.add_argument("value", nargs="?", default=None)

    # objects
    sp = S("view", "read a bounded projection of a payload (AMPI_Type_view)", cmd_view)
    sp.add_argument("handle")
    sp.add_argument("--op", default=None,
                    help="head:800 | tail:400 | headtail:800 | lines:10-40 | keys:a,b | "
                         "grep:PATTERN | outline | shape | stat | chunk:2/8 | full")
    sp.add_argument("--budget", type=int, default=None)
    sp.add_argument("--out", default=None,
                    help="write the view to this path instead of your context (free)")
    sp = S("obj", "payload metadata, or save it to disk", cmd_obj)
    sp.add_argument("handle")
    sp.add_argument("--save", default=None, help="write the payload to this path (no context cost)")
    S("ops", "list reduction operators and collective algorithms", cmd_ops)

    # job level
    S("status", "job-wide progress", cmd_status)
    sp = S("trace", "export or print the event trace", cmd_trace)
    sp.add_argument("--out", default=None)
    sp.add_argument("--summary", action="store_true")
    sp.add_argument("--timeline", action="store_true")
    sp.add_argument("--width", type=int, default=96)
    sp.add_argument("--limit", type=int, default=None)
    sp.add_argument("--pretty", action="store_true")

    sp = S("run", "create a job and materialise rank prompts (mpirun)", cmd_run)
    sp.add_argument("--np", type=int, default=None)
    sp.add_argument("--label", default=None)
    sp.add_argument("--spec", default=None, help="JSON job specification")
    sp.add_argument("--keep", action="store_true", help="do not wipe an existing journal")
    sp.add_argument("--eager-tokens", type=int, default=None)
    sp.add_argument("--ctx-budget", type=int, default=None)
    sp.add_argument("--lease", type=float, default=None, help="lease seconds")

    sp = S("supervise", "one supervisor pass: detect failures and apply a restart policy", cmd_supervise)
    sp.add_argument("--policy", choices=["none", "restart", "shrink"], default="restart")
    sp.add_argument("--max-restarts", type=int, default=2)

    sp = S("bench", "run microbenchmarks with stub executors", cmd_bench)
    sp.add_argument("suite", choices=["latency", "collectives", "context", "scaling", "matching", "all"])
    sp.add_argument("--np", type=int, default=16)
    sp.add_argument("--reps", type=int, default=5)
    sp.add_argument("--sizes", default="16,64,256,1024,4096,16384")
    sp.add_argument("--out", default=None)
    sp.add_argument("--workdir", default=None)
    sp.add_argument("--merge-cost", type=float, default=0.0,
                    help="simulated seconds per agent merge step")
    sp.add_argument("--procs", type=int, default=None, help="max concurrent stub processes")

    sp = S("serve", "serve the trace viewer API", cmd_serve)
    sp.add_argument("--port", type=int, default=47913)
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--runs", default="runs", help="directory of job roots to expose")

    return p


def _identity_of(a: argparse.Namespace) -> Optional[Dict[str, Any]]:
    """Best-effort (rank, job) for the identity echo. Never raises."""
    try:
        rank = getattr(a, "rank", None)
        if rank is None:
            env = os.environ.get("AMPI_RANK")
            rank = int(env) if env not in (None, "") else None
        if rank is None:
            return None
        j = open_journal(getattr(a, "job_root", None))
        out = {"rank": int(rank), "job": j.job_id}
        j.close()
        return out
    except Exception:
        return None


def _trace_error(a: argparse.Namespace, exc: AmpiError) -> None:
    """Best-effort: log an error event. Never let logging mask the real error."""
    try:
        j = open_journal(getattr(a, "job_root", None))
        rank = getattr(a, "rank", None)
        if rank is None:
            env = os.environ.get("AMPI_RANK")
            rank = int(env) if env not in (None, "") else None
        with j.tx() as c:
            j.trace(
                "error",
                rank=rank,
                status=exc.err_class,
                detail={"cmd": getattr(a, "cmd", None), "message": exc.message[:200]},
                conn=c,
            )
        j.close()
    except Exception:
        pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    a = parser.parse_args(argv)
    as_json = bool(getattr(a, "json", False))
    attempts = max(1, 1 + int(getattr(a, "retries", 0) or 0))
    try:
        res = None
        for attempt in range(attempts):
            try:
                res = a.func(a)
                break
            except AmpiError as exc:
                last = attempt == attempts - 1
                if exc.err_class not in RETRYABLE or last:
                    raise
                # Progress output matters: it tells the caller's tool that the
                # command is alive rather than hung.
                print(
                    f"AMPI_RETRY attempt {attempt + 1}/{attempts} after {exc.err_class}: "
                    f"{exc.message[:120]}",
                    file=sys.stderr, flush=True,
                )
    except AmpiError as exc:
        # Record the error in the trace. This is what makes retry behaviour
        # measurable: the interesting number in an agent run is not how many
        # calls succeeded but how many times a rank had to re-issue a
        # deadline-bounded call, and that is invisible unless the binding logs it.
        _trace_error(a, exc)
        payload = exc.to_dict()
        payload["retryable"] = exc.err_class in RETRYABLE
        if as_json:
            print(json.dumps(payload, indent=2, ensure_ascii=False), file=sys.stderr)
        else:
            print(f"{exc.err_class} {exc.message}", file=sys.stderr)
            if exc.detail:
                print(f"detail: {json.dumps(exc.detail, ensure_ascii=False)}", file=sys.stderr)
            if exc.hint:
                print(f"hint: {exc.hint}", file=sys.stderr)
            if exc.err_class in RETRYABLE:
                print("retryable: yes -- re-running this exact command is safe", file=sys.stderr)
        return exit_code(exc.err_class)
    except BrokenPipeError:  # pragma: no cover
        return 0
    except KeyboardInterrupt:  # pragma: no cover
        print("AMPI_ERR_TIMEOUT interrupted", file=sys.stderr)
        return exit_code(ErrClass.TIMEOUT)
    if res is None:
        res = {}
    if isinstance(res, dict) and "_identity" not in res:
        ident = _identity_of(a)
        if ident:
            res["_identity"] = ident
    print(render(res, as_json=as_json))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
