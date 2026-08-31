"""``ampi``: the command-line binding, and the one agents actually use.

Design constraints that come from the caller being a language model rather than
a compiler:

* **Every result is JSON on stdout, every failure is JSON on stdout with a
  non-zero exit status.**  An agent parses what it is given; a protocol that
  sometimes prints a table and sometimes a stack trace cannot be followed.

* **Large payloads spill to files by default.**  A binding that printed a
  200-kilotoken artifact to stdout would defeat the entire context-management
  design one layer above it, because the agent's harness pastes tool output
  straight into the model's context.  Above ``--inline-limit`` tokens the CLI
  writes the payload to the rank's scratch directory and returns the path, the
  token count and a free structural digest.  This is the rendezvous protocol
  made visible at the binding layer.

* **Errors say what to do next.**  Every error carries the AgentMPI error class
  and, where there is an obvious remedy (retry with a projection, shrink the
  communicator, release context), the remedy is in the message.  An agent that
  is told "AMPI_ERR_CONTEXT_EXHAUSTED" and nothing else will improvise; an
  agent that is told to retry with ``--projection digest`` will do that.

* **Calls are idempotent where they can be.**  ``init`` on an already-running
  rank succeeds, and re-issuing a suspended collective resumes it rather than
  starting a second one.  Agents retry.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from . import util
from .constants import (
    ALGO_AUTO,
    AMPI_ANY_SOURCE,
    AMPI_ANY_TAG,
    AMPI_COMM_WORLD,
    DEFAULT_CTX_LIMIT,
    DEFAULT_ROLL_CALL_TIMEOUT,
    LOCK_EXCLUSIVE,
    LOCK_SHARED,
    PROJ_DIGEST,
    PROJ_FULL,
    PROJ_REF,
    PROJ_SCHEMA,
)
from .core import collectives as coll
from .core.collectives import SemanticUpcall
from .core.comm import cart_shift, neighbours
from .core.ops import PREDEFINED, get_op
from .core.runtime import Runtime
from .device import open_device
from .errors import AmpiArgError, AmpiError

INLINE_LIMIT_DEFAULT = 800


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def emit(payload: dict[str, Any], code: int = 0) -> int:
    payload.setdefault("ok", code == 0)
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n")
    return code


def scratch_dir(job_dir: str, rank: int | None) -> str:
    path = os.path.join(job_dir, "ranks", str(rank if rank is not None else "x"))
    os.makedirs(path, exist_ok=True)
    return path


def spill(text: str, job_dir: str, rank: int | None, name: str, inline_limit: int,
          out: str | None = None) -> dict[str, Any]:
    """Return a payload inline if it is small, otherwise write it to a file.

    The threshold is the binding's half of the rendezvous protocol: the runtime
    decided whether to *transfer* the body, and the binding decides whether to
    put it in front of the model.
    """
    tokens = util.count_tokens(text)
    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text)
        return {"payload_file": os.path.abspath(out), "tokens": tokens,
                "digest": util.structural_digest(text, 80)}
    if tokens <= inline_limit:
        return {"payload": text, "tokens": tokens}
    path = os.path.join(scratch_dir(job_dir, rank), name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return {
        "payload_file": path,
        "tokens": tokens,
        "digest": util.structural_digest(text, 120),
        "note": (
            f"payload is {tokens} tokens, above the {inline_limit}-token inline limit, so it "
            "was written to a file instead of returned. Read the file only if you need the "
            "full text; the digest above may be enough."
        ),
    }


def read_payload(args: argparse.Namespace) -> Any:
    """Collect a payload from --text / --file / --json / stdin."""
    if getattr(args, "json", None):
        return json.loads(args.json)
    if getattr(args, "json_file", None):
        with open(args.json_file, encoding="utf-8") as fh:
            return json.load(fh)
    if getattr(args, "file", None):
        with open(args.file, encoding="utf-8") as fh:
            return fh.read()
    if getattr(args, "text", None) is not None:
        return args.text
    if getattr(args, "stdin", False):
        return sys.stdin.read()
    return None


def resolve_job_dir(args: argparse.Namespace) -> str:
    job_dir = args.job or os.environ.get("AMPI_JOB_DIR")
    if not job_dir:
        raise AmpiArgError(
            "no job directory: pass --job <dir> or set AMPI_JOB_DIR",
        )
    return os.path.abspath(job_dir)


def resolve_rank(args: argparse.Namespace) -> int | None:
    if getattr(args, "rank", None) is not None:
        return int(args.rank)
    env = os.environ.get("AMPI_RANK")
    return int(env) if env not in (None, "") else None


RANKLESS_COMMANDS = {"new", "status", "doctor", "trace", "ops", "plan", "comm-list",
                     "failures"}


def make_runtime(args: argparse.Namespace) -> tuple[Runtime, str]:
    job_dir = resolve_job_dir(args)
    device = open_device(os.path.join(job_dir, "job.db"))
    job_id = os.path.basename(job_dir.rstrip("/"))
    rank = resolve_rank(args)
    if rank is None and getattr(args, "command", None) not in RANKLESS_COMMANDS:
        raise AmpiArgError(
            f"`ampi {getattr(args, 'command', '?')}` acts on behalf of a rank, but no rank "
            "was given: pass --rank N or set AMPI_RANK. Refusing rather than acting "
            "anonymously, because an operation attributed to no rank is unauditable "
            "and, in the case of revoke, unrecoverable.",
            command=getattr(args, "command", None),
        )
    rt = Runtime(device, job_id, rank)
    if getattr(args, "failure_timeout", None):
        rt.failure_timeout = float(args.failure_timeout)
    return rt, job_dir


def default_comm(args: argparse.Namespace) -> str:
    return getattr(args, "comm", None) or os.environ.get("AMPI_COMM") or AMPI_COMM_WORLD


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_new(args: argparse.Namespace) -> int:
    job_dir = os.path.abspath(args.job)
    os.makedirs(job_dir, exist_ok=True)
    device = open_device(os.path.join(job_dir, "job.db"))
    job_id = os.path.basename(job_dir.rstrip("/"))
    runtime = Runtime.create_job(
        device,
        job_id,
        args.n,
        ctx_limit=args.ctx_limit,
        roll_call_timeout=args.roll_call_timeout,
        meta=json.loads(args.meta) if args.meta else {},
    )
    for r in range(args.n):
        scratch_dir(job_dir, r)
    return emit(
        {
            "job_id": job_id,
            "run_id": runtime.run_id,
            "job_dir": job_dir,
            "world_size": args.n,
            "ctx_limit": args.ctx_limit,
            "roll_call_timeout": args.roll_call_timeout,
            "db": os.path.join(job_dir, "job.db"),
        }
    )


def cmd_init(args: argparse.Namespace) -> int:
    rt, job_dir = make_runtime(args)
    rank = resolve_rank(args)
    if rank is None:
        raise AmpiArgError("init requires --rank or the AMPI_RANK environment variable")
    row = rt.init(rank, role=args.role, ctx_limit=args.ctx_limit,
                  meta=json.loads(args.meta) if args.meta else None)
    comm = rt.comms.get(default_comm(args))
    return emit({
        "rank": rank,
        "world_size": rt.job()["world_size"],
        "comm": comm.name,
        "comm_rank": comm.rank_of(rank),
        "comm_size": comm.size,
        "generation": row["generation"],
        "ctx_limit": row["ctx_limit"],
        "ctx_used": row["ctx_used"],
        "scratch": scratch_dir(job_dir, rank),
    })


def cmd_finalize(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    return emit({"finalized": rt.finalize(args.note)["rank"]})


def cmd_hb(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    row = rt.heartbeat(args.expect_idle)
    return emit({"rank": row["rank"], "last_heartbeat": row["last_heartbeat"],
                 "hb_deadline": row["hb_deadline"]})


def cmd_send(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    payload = read_payload(args)
    if payload is None:
        raise AmpiArgError("send requires a payload: --text, --file, --json or --json-file")
    result = rt.send(default_comm(args), args.to, args.tag, payload,
                     projection=args.projection, digest_budget=args.digest_budget,
                     force_mode=args.mode)
    return emit(result)


def cmd_recv(args: argparse.Namespace) -> int:
    rt, job_dir = make_runtime(args)
    got = rt.recv(default_comm(args), args.source, args.tag, timeout=args.timeout,
                  blocking=not args.nonblocking, deref=args.deref, max_tokens=args.max_tokens)
    if got is None:
        return emit({"received": False, "note": "no matching message was posted"})
    text = got["payload"] if isinstance(got["payload"], str) else util.dumps(got["payload"])
    body = spill(text or "", job_dir, rt.rank, f"recv-{got['msg_id']}.txt", args.inline_limit,
                 args.out)
    return emit({"received": True, "msg_id": got["msg_id"], "src": got["src"], "tag": got["tag"],
                 "mode": got["mode"], "handle": got["handle"], "digest": got["digest"],
                 "materialised": got["materialised"], **body})


def cmd_probe(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    hit = rt.probe(default_comm(args), args.source, args.tag)
    return emit({"found": hit is not None, **(hit or {})})


def cmd_deref(args: argparse.Namespace) -> int:
    rt, job_dir = make_runtime(args)
    got = rt.deref(args.handle, max_tokens=args.max_tokens)
    body = spill(got["payload"], job_dir, rt.rank, f"obj-{args.handle}.txt", args.inline_limit,
                 args.out)
    return emit({"handle": args.handle, "sha256": got["sha256"], **body})


def cmd_sendrecv(args: argparse.Namespace) -> int:
    rt, job_dir = make_runtime(args)
    payload = read_payload(args)
    result = rt.sendrecv(default_comm(args), args.to, args.tag, payload, args.source,
                         args.recv_tag if args.recv_tag is not None else args.tag,
                         timeout=args.timeout)
    got = result["received"]
    text = got["payload"] if isinstance(got["payload"], str) else util.dumps(got["payload"])
    body = spill(text or "", job_dir, rt.rank, f"recv-{got['msg_id']}.txt", args.inline_limit)
    return emit({"sent": result["sent"], "src": got["src"], "tag": got["tag"], **body})


# -- collectives ------------------------------------------------------------


def _collective(args: argparse.Namespace, fn: Any, *fnargs: Any, **fnkw: Any) -> int:
    rt, job_dir = make_runtime(args)
    try:
        result = fn(rt, default_comm(args), *fnargs, **fnkw)
    except SemanticUpcall as upcall:
        payload = upcall.to_dict()
        path = os.path.join(scratch_dir(job_dir, rt.rank), f"op-{upcall.op_token.split(':')[-2]}-"
                            f"{upcall.step}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload["operands"], fh, ensure_ascii=False, indent=2)
        payload["operands_file"] = path
        if util.count_tokens(util.dumps(payload["operands"])) > args.inline_limit:
            payload["operands"] = f"[written to {path}]"
        return emit(payload, code=0)
    return emit(_render_collective(result, args, job_dir, rt))


def _render_collective(result: dict[str, Any], args: argparse.Namespace, job_dir: str,
                       rt: Runtime) -> dict[str, Any]:
    # The decision function's reasoning is valuable to a human reading a trace
    # and pure noise in an agent's context, so it is opt-in.  The full record is
    # persisted in the coll table either way.
    skip = {"result"} if getattr(args, "explain", False) else {"result", "considered"}
    out = {k: v for k, v in result.items() if k not in skip}
    out["status"] = "ok"
    if "result" in result:
        text = result["result"] if isinstance(result["result"], str) \
            else util.dumps(result["result"])
        out.update(spill(text or "", job_dir, rt.rank,
                         f"coll-{result.get('seq', 0)}.json", args.inline_limit, args.out))
    return out


def cmd_barrier(args: argparse.Namespace) -> int:
    return _collective(args, coll.barrier, algo=args.algo, timeout=args.timeout)


def cmd_bcast(args: argparse.Namespace) -> int:
    return _collective(args, coll.bcast, args.root, read_payload(args), algo=args.algo,
                       timeout=args.timeout)


def cmd_reduce(args: argparse.Namespace) -> int:
    return _collective(args, coll.reduce_, args.root, read_payload(args), args.op,
                       algo=args.algo, timeout=args.timeout, datatype=args.datatype)


def cmd_allreduce(args: argparse.Namespace) -> int:
    return _collective(args, coll.allreduce, read_payload(args), args.op, algo=args.algo,
                       timeout=args.timeout, datatype=args.datatype)


def cmd_allgather(args: argparse.Namespace) -> int:
    return _collective(args, coll.allgather, read_payload(args), algo=args.algo,
                       timeout=args.timeout)


def cmd_alltoall(args: argparse.Namespace) -> int:
    return _collective(args, coll.alltoall, read_payload(args), algo=args.algo,
                       timeout=args.timeout)


def cmd_gather(args: argparse.Namespace) -> int:
    return _collective(args, coll.gather, args.root, read_payload(args), timeout=args.timeout)


def cmd_scatter(args: argparse.Namespace) -> int:
    return _collective(args, coll.scatter, args.root, read_payload(args), timeout=args.timeout)


def cmd_reduce_scatter(args: argparse.Namespace) -> int:
    return _collective(args, coll.reduce_scatter, read_payload(args), args.op, algo=args.algo,
                       timeout=args.timeout, datatype=args.datatype)


def cmd_scan(args: argparse.Namespace) -> int:
    return _collective(args, coll.scan, read_payload(args), args.op, algo=args.algo,
                       timeout=args.timeout, datatype=args.datatype)


def cmd_op_submit(args: argparse.Namespace) -> int:
    """Deliver the result of a semantic operator evaluation back to the library."""
    rt, _ = make_runtime(args)
    payload = read_payload(args)
    if payload is None:
        raise AmpiArgError("op-submit requires --result-file, --text or --json")
    row = rt.device.query_one("SELECT * FROM pending_op WHERE op_token=?", (args.op_token,))
    if row is None:
        raise AmpiArgError(f"no pending operator evaluation with token {args.op_token!r}")
    with rt.device.write_tx():
        rt.device.execute(
            "UPDATE pending_op SET state='done', result=?, settled_at=? WHERE op_token=?",
            (util.dumps(payload), util.now(), args.op_token),
        )
        rt.tracer.emit("AMPI_Op_upcall", "exit", operator=row["op_name"], step=int(row["step"]),
                       tokens=util.count_tokens(util.dumps(payload)))
    return emit({"op_token": args.op_token, "op": row["op_name"], "state": "done",
                 "next": "re-issue the identical collective call to resume it"})


def cmd_ops(args: argparse.Namespace) -> int:
    return emit({"operators": [op.to_dict() for op in PREDEFINED.values()]})


def cmd_plan(args: argparse.Namespace) -> int:
    """Show the decision function's reasoning without running anything."""
    op = get_op(args.op) if args.op else None
    algo, considered = coll.select_algorithm(args.collective, args.p, args.n, op,
                                             args.ctx_limit, args.vector)
    return emit({"collective": args.collective, "p": args.p, "n_tokens": args.n,
                 "op": op.name if op else None, "ctx_limit": args.ctx_limit,
                 "vector": args.vector, "chosen": algo, "considered": considered})


# -- communicators ----------------------------------------------------------


def cmd_comm_create(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    with rt.device.write_tx():
        comm = rt.comms.create(args.name, [int(x) for x in args.members.split(",")])
    return emit(comm.to_dict())


def cmd_comm_dup(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    with rt.device.write_tx():
        comm = rt.comms.dup(default_comm(args), args.name)
    return emit(comm.to_dict())


def cmd_comm_split(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    colors = {int(k): (None if v in ("", "none") else int(v))
              for k, v in (pair.split(":") for pair in args.colors.split(","))}
    keys = {}
    if args.keys:
        keys = {int(k): int(v) for k, v in (pair.split(":") for pair in args.keys.split(","))}
    with rt.device.write_tx():
        made = rt.comms.split(default_comm(args), colors, keys)
    return emit({"created": {str(c): comm.to_dict() for c, comm in made.items()}})


def cmd_comm_list(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    return emit({"comms": [
        {"name": c["name"], "size": len(util.loads(c["members"], [])),
         "members": util.loads(c["members"], []), "revoked": bool(c["revoked"]),
         "context_id": c["context_id"], "topology": util.loads(c["topology"], None)}
        for c in rt.comms.list()
    ]})


def cmd_cart_create(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    dims = [int(x) for x in args.dims.split(",")]
    periods = [p.strip().lower() in ("1", "true", "yes") for p in args.periods.split(",")]
    with rt.device.write_tx():
        comm = rt.comms.cart_create(default_comm(args), dims, periods, args.name)
    return emit(comm.to_dict())


def cmd_neighbors(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    comm = rt.comms.get(default_comm(args))
    me = comm.rank_of(rt.rank)
    result: dict[str, Any] = {"comm": comm.name, "rank": me,
                              "neighbours": neighbours(comm, me)}
    if comm.topology and comm.topology.get("kind") == "cart":
        shifts = {}
        for d in range(len(comm.topology["dims"])):
            src, dst = cart_shift(comm.topology["dims"], comm.topology["periods"], me, d, 1)
            shifts[f"dim{d}"] = {"source": src, "dest": dst}
        result["cart_shift"] = shifts
    return emit(result)


def cmd_graph_create(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    adjacency = {int(k): [int(x) for x in v.split("|") if x != ""]
                 for k, v in (pair.split(":") for pair in args.adj.split(","))}
    with rt.device.write_tx():
        comm = rt.comms.dist_graph_create(default_comm(args), adjacency, args.name)
    return emit(comm.to_dict())


# -- windows ----------------------------------------------------------------


def cmd_win_create(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    return emit(dict(rt.win_create(default_comm(args), args.name)))


def cmd_win_put(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    value = read_payload(args)
    result = rt.win_put(args.win, args.key, value, expected_version=args.if_version)
    if not result["ok"]:
        return emit({**result, "note": "compare-and-swap failed: the cell changed under you; "
                                       "re-read it, re-apply your edit, and retry"}, code=3)
    return emit(result)


def cmd_win_get(args: argparse.Namespace) -> int:
    rt, job_dir = make_runtime(args)
    result = rt.win_get(args.win, args.key, charge_context=args.charge)
    if not result["found"]:
        return emit(result)
    value = result["value"]
    text = value if isinstance(value, str) else util.dumps(value)
    body = spill(text, job_dir, rt.rank, f"win-{args.key.replace('/', '_')}.txt",
                 args.inline_limit, args.out)
    out = {"key": args.key, "found": True, "version": result["version"],
           "updated_by": result["updated_by"], **body}
    # Also hand back a scalar under the obvious name. Counters are the common
    # case for a window read -- a rank polling for "have all six finished?" --
    # and two agents wrote poll loops against `value` or `current`, read
    # nothing, and never took their early exit. Returning only `payload` was
    # technically complete and practically a trap.
    if isinstance(value, (int, float, bool)) or (isinstance(value, str) and len(value) <= 200):
        out["value"] = value
    return emit(out)


def cmd_win_list(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    return emit({"cells": rt.win_list(args.win, args.prefix)})


def cmd_win_accumulate(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    return emit(rt.win_accumulate(args.win, args.key, read_payload(args), args.op))


def cmd_win_fetch_add(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    return emit(rt.win_fetch_and_op(args.win, args.key, args.delta))


def cmd_win_claim(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    result = rt.win_claim(args.win, args.key, note=args.note or "")
    if not result["claimed"]:
        return emit({**result, "note": f"already claimed by rank {result.get('owner')}; "
                                       "pick a different item"}, code=4)
    return emit(result)


def cmd_win_lock(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    return emit(rt.win_lock(args.win, args.key, mode=args.mode, ttl=args.ttl,
                            timeout=args.timeout))


def cmd_win_unlock(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    return emit(rt.win_unlock(args.lock_id))


def cmd_win_fence(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    return emit(rt.win_fence(args.win, default_comm(args), timeout=args.timeout))


# -- context ----------------------------------------------------------------


def cmd_ctx(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    row = rt.rank_row()
    used, limit = int(row["ctx_used"]), int(row["ctx_limit"])
    return emit({"rank": row["rank"], "used": used, "limit": limit, "free": limit - used,
                 "peak": row["ctx_peak"], "occupancy": round(used / max(1, limit), 4),
                 "tokens_sent": row["tokens_sent"], "tokens_received": row["tokens_recvd"]})


def cmd_ctx_release(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    row = rt.ctx.release(rt.rank, args.tokens)
    with rt.device.write_tx():
        rt.tracer.emit("AMPI_Ctx_release", "exit", tokens=args.tokens, reason=args.reason)
    return emit({"rank": row["rank"], "used": row["ctx_used"], "limit": row["ctx_limit"],
                 "released": args.tokens, "reason": args.reason})


# -- fault tolerance --------------------------------------------------------


def cmd_comm_resync(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    return emit(rt.comm_resync(default_comm(args)))


def cmd_revoke(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    return emit(rt.comm_revoke(default_comm(args)))


def cmd_shrink(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    return emit(rt.comm_shrink(default_comm(args), args.name))


def cmd_agree(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    value = args.value.strip().lower() in ("1", "true", "yes", "y")
    return emit(rt.comm_agree(default_comm(args), value, timeout=args.timeout))


def cmd_respawn(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    return emit(rt.respawn(args.target))


def cmd_kill(args: argparse.Namespace) -> int:
    """Fault injection: declare a rank failed without its cooperation."""
    rt, _ = make_runtime(args)
    rt.declare_failed(args.target, args.reason or "injected fault", confirmed=True)
    return emit({"killed": args.target, "reason": args.reason or "injected fault"})


def cmd_failures(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    classification = rt.refresh_failures()
    return emit({
        "run_id": rt.run_id,
        "failed": sorted(rt.failed_ranks()),
        "suspected": rt.suspected(),
        "never_joined": classification["never_joined"],
        "records": rt.device.query(
            "SELECT rank, generation, detected_at, detected_by, reason FROM failure "
            "WHERE job_id=? ORDER BY failure_id", (rt.job_id,)),
    })


def cmd_inbox(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    target = args.target if args.target is not None else rt.rank
    rows = rt.replay_inbox(target, default_comm(args))
    return emit({"rank": target, "count": len(rows), "messages": [
        {"msg_id": r["msg_id"], "src": r["src"], "tag": r["tag"], "state": r["state"],
         "tokens": r["tokens"], "handle": r["handle"],
         "digest": r["digest"] or util.structural_digest(r["body"] or "", 40)}
        for r in rows
    ]})


def cmd_checkpoint(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    return emit(rt.checkpoint(read_payload(args), args.label))


def cmd_restore(args: argparse.Namespace) -> int:
    rt, job_dir = make_runtime(args)
    row = rt.restore(rank=args.target, label=args.label)
    if row is None:
        return emit({"found": False})
    body = spill(row["state"], job_dir, rt.rank, "restore.json", args.inline_limit, args.out)
    return emit({"found": True, "ckpt_id": row["ckpt_id"], "label": row["label"],
                 "generation": row["generation"], **body})


# -- introspection ----------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    job = rt.job()
    ranks = rt.all_ranks()
    return emit({
        "job_id": job["job_id"], "run_id": job["run_id"], "world_size": job["world_size"],
        "spec_version": job["spec_version"],
        "roll_call_timeout": job["roll_call_timeout"],
        "ranks": [{"rank": r["rank"], "state": r["state"], "role": r["role"],
                   "generation": r["generation"], "ctx_used": r["ctx_used"],
                   "ctx_limit": r["ctx_limit"], "tokens_sent": r["tokens_sent"],
                   "tokens_received": r["tokens_recvd"]} for r in ranks],
        "messages": rt.device.query_one(
            "SELECT COUNT(*) AS n, SUM(tokens) AS tok FROM message WHERE job_id=?",
            (rt.job_id,)),
        "collectives": rt.device.query_one(
            "SELECT COUNT(*) AS n, SUM(state='complete') AS complete FROM coll WHERE job_id=?",
            (rt.job_id,)),
    })


def cmd_doctor(args: argparse.Namespace) -> int:
    """Health check: suspected failures, wait-for cycles, stuck collectives."""
    rt, _ = make_runtime(args)
    classification = rt.refresh_failures()
    cycle = rt.detect_deadlock()
    waiting = rt.device.query(
        "SELECT owner, kind, src, tag, posted_at FROM request WHERE job_id=? AND state='posted' "
        "ORDER BY owner", (rt.job_id,))
    stuck = rt.device.query(
        "SELECT c.coll_id, c.comm_id, c.op, c.seq, c.expected, c.created_at, "
        "COUNT(cc.rank) AS arrived "
        "FROM coll c LEFT JOIN coll_contrib cc ON cc.coll_id=c.coll_id "
        "WHERE c.job_id=? AND c.state='open' GROUP BY c.coll_id ORDER BY c.created_at",
        (rt.job_id,))
    # Naming which rank has not yet entered an open collective is the whole
    # point of the diagnostic. Seven ranks blocked in a barrier is a symptom;
    # the actionable fact is the eighth rank that has not called it, and the
    # per-rank sequence counters say exactly who that is.
    for entry in stuck:
        comm = rt.comms.get(entry["comm_id"])
        laggards = []
        for local in range(comm.size):
            row = rt.device.query_one(
                "SELECT value FROM counter WHERE job_id=? AND name=?",
                (rt.job_id, f"collseq:{comm.comm_id}:{local}"))
            if int(row["value"] if row else 0) < int(entry["seq"]):
                laggards.append(local)
        entry["not_yet_entered"] = laggards
        entry["open_for_seconds"] = round(util.now() - entry["created_at"], 1)
        entry["comm"] = comm.name
        del entry["created_at"]
    pending = rt.device.query(
        "SELECT op_token, assignee, op_name, step FROM pending_op WHERE job_id=? "
        "AND state='pending'", (rt.job_id,))
    return emit({
        "deadlock_cycle": cycle,
        "suspected": rt.suspected(),
        "never_joined": classification["never_joined"],
        "failed": sorted(rt.failed_ranks()),
        "blocked_receives": waiting,
        "open_collectives": stuck,
        "pending_operator_upcalls": pending,
        "verdict": ("deadlock" if cycle else
                    "degraded" if (rt.suspected() or rt.failed_ranks()) else
                    "stalled" if any(c["not_yet_entered"] and c["open_for_seconds"] > 120
                                     for c in stuck) else "healthy"),
        "advice": next(
            (f"collective #{c['seq']} ({c['op']}) on {c['comm']!r} has been open for "
             f"{c['open_for_seconds']:.0f}s; rank(s) {c['not_yet_entered']} have not "
             "called it yet. They are not failed, only late: wait, or if they will never "
             "call it, declare them failed and use `ampi comm-resync` or "
             "`ampi shrink`."
             for c in stuck if c["not_yet_entered"] and c["open_for_seconds"] > 120),
            None),
    })


def cmd_trace(args: argparse.Namespace) -> int:
    rt, _ = make_runtime(args)
    sql = "SELECT * FROM event WHERE job_id=?"
    params: list[Any] = [rt.job_id]
    if args.target is not None:
        sql += " AND rank=?"
        params.append(args.target)
    if args.op:
        sql += " AND op LIKE ?"
        params.append(f"%{args.op}%")
    sql += " ORDER BY event_id DESC LIMIT ?"
    params.append(args.limit)
    rows = list(reversed(rt.device.query(sql, params)))
    return emit({"events": rows, "count": len(rows)})


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ampi",
        description="AgentMPI: message passing for multi-agent harnesses. "
                    "Every command prints JSON.",
    )
    parser.add_argument("--job", help="job directory (default: $AMPI_JOB_DIR)")
    parser.add_argument("--rank", type=int, help="this rank (default: $AMPI_RANK)")
    parser.add_argument("--comm", help="communicator name (default: $AMPI_COMM or 'world')")
    parser.add_argument("--inline-limit", type=int, default=INLINE_LIMIT_DEFAULT,
                        help="payloads above this many tokens spill to a file")
    parser.add_argument("--failure-timeout", type=float,
                        help="seconds without a heartbeat before a peer is suspected")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, fn: Any, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text, description=help_text)
        p.set_defaults(func=fn)
        return p

    def payload_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--text", help="inline text payload")
        p.add_argument("--file", help="read the payload from a text file")
        p.add_argument("--json", help="inline JSON payload")
        p.add_argument("--json-file", help="read a JSON payload from a file")
        p.add_argument("--stdin", action="store_true", help="read the payload from stdin")

    def out_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--out", help="write the payload to this file instead of returning it")

    # lifecycle
    p = add("new", cmd_new, "Create a new AgentMPI job")
    p.add_argument("-n", type=int, required=True, help="world size")
    p.add_argument("--ctx-limit", type=int, default=DEFAULT_CTX_LIMIT)
    p.add_argument("--roll-call-timeout", type=float, default=DEFAULT_ROLL_CALL_TIMEOUT)
    p.add_argument("--meta")

    p = add("init", cmd_init, "AMPI_Init: join the job as a rank")
    p.add_argument("--role")
    p.add_argument("--ctx-limit", type=int)
    p.add_argument("--meta")

    p = add("finalize", cmd_finalize, "AMPI_Finalize: leave the job cleanly")
    p.add_argument("--note")

    p = add("hb", cmd_hb, "AMPI_Heartbeat, optionally declaring an expected quiet period")
    p.add_argument("--expect-idle", type=float,
                   help="seconds this rank expects to be busy without calling ampi")

    # point to point
    p = add("send", cmd_send, "AMPI_Send")
    p.add_argument("--to", type=int, required=True)
    p.add_argument("--tag", type=int, default=0)
    p.add_argument("--projection", default=PROJ_FULL,
                   choices=[PROJ_FULL, PROJ_DIGEST, PROJ_SCHEMA, PROJ_REF])
    p.add_argument("--digest-budget", type=int, default=400)
    p.add_argument("--mode", choices=["eager", "rendezvous"],
                   help="override the receiver-driven transfer mode")
    payload_args(p)

    p = add("recv", cmd_recv, "AMPI_Recv")
    p.add_argument("--source", type=int, default=AMPI_ANY_SOURCE,
                   help="-1 for AMPI_ANY_SOURCE")
    p.add_argument("--tag", type=int, default=AMPI_ANY_TAG, help="-1 for AMPI_ANY_TAG")
    p.add_argument("--timeout", type=float, default=600.0)
    p.add_argument("--nonblocking", action="store_true")
    p.add_argument("--deref", action="store_true",
                   help="materialise a rendezvous body instead of taking the digest")
    p.add_argument("--max-tokens", type=int)
    out_args(p)

    p = add("probe", cmd_probe, "AMPI_Iprobe: look without consuming")
    p.add_argument("--source", type=int, default=AMPI_ANY_SOURCE)
    p.add_argument("--tag", type=int, default=AMPI_ANY_TAG)

    p = add("deref", cmd_deref, "AMPI_Deref: pull a rendezvous body into context")
    p.add_argument("--handle", required=True)
    p.add_argument("--max-tokens", type=int)
    out_args(p)

    p = add("sendrecv", cmd_sendrecv, "AMPI_Sendrecv: deadlock-free paired exchange")
    p.add_argument("--to", type=int, required=True)
    p.add_argument("--tag", type=int, default=0)
    p.add_argument("--source", type=int, required=True)
    p.add_argument("--recv-tag", type=int)
    p.add_argument("--timeout", type=float, default=600.0)
    payload_args(p)

    # collectives
    for name, fn, help_text in [
        ("barrier", cmd_barrier, "AMPI_Barrier"),
        ("bcast", cmd_bcast, "AMPI_Bcast"),
        ("reduce", cmd_reduce, "AMPI_Reduce"),
        ("allreduce", cmd_allreduce, "AMPI_Allreduce"),
        ("allgather", cmd_allgather, "AMPI_Allgather"),
        ("alltoall", cmd_alltoall, "AMPI_Alltoall"),
        ("gather", cmd_gather, "AMPI_Gather"),
        ("scatter", cmd_scatter, "AMPI_Scatter"),
        ("scan", cmd_scan, "AMPI_Scan"),
        ("reduce-scatter", cmd_reduce_scatter,
         "AMPI_Reduce_scatter_block: reduce, then keep only your block"),
    ]:
        p = add(name, fn, help_text)
        p.add_argument("--timeout", type=float, default=1800.0)
        p.add_argument("--algo", default=ALGO_AUTO,
                       help="force a collective algorithm instead of using the decision function")
        p.add_argument("--explain", action="store_true",
                       help="also report which algorithms were considered and why")
        out_args(p)
        if name in ("bcast", "reduce", "gather", "scatter"):
            p.add_argument("--root", type=int, default=0)
        if name in ("reduce", "allreduce", "scan", "reduce-scatter"):
            p.add_argument("--op", required=True, help="reduction operator (see `ampi ops`)")
            p.add_argument("--datatype", default="auto", choices=["auto", "scalar", "vector"],
                           help="apply the operator to the payload as a whole (scalar) or "
                                "element-wise to a keyed collection (vector)")
        if name != "barrier":
            payload_args(p)

    p = add("op-submit", cmd_op_submit, "Return the result of a semantic operator evaluation")
    p.add_argument("--op-token", required=True)
    p.add_argument("--result-file", dest="file")
    p.add_argument("--text")
    p.add_argument("--json")
    p.add_argument("--json-file")

    add("ops", cmd_ops, "List reduction operators and their declared algebra")

    p = add("plan", cmd_plan, "Explain which collective algorithm would be chosen, and why")
    p.add_argument("--collective", required=True)
    p.add_argument("-p", type=int, required=True, help="communicator size")
    p.add_argument("-n", type=int, required=True, help="payload size in tokens")
    p.add_argument("--op")
    p.add_argument("--ctx-limit", type=int, default=DEFAULT_CTX_LIMIT)
    p.add_argument("--vector", action="store_true")

    # communicators
    p = add("comm-create", cmd_comm_create, "Create a communicator over explicit world ranks")
    p.add_argument("--name", required=True)
    p.add_argument("--members", required=True, help="comma-separated world ranks")

    p = add("comm-dup", cmd_comm_dup, "AMPI_Comm_dup: same group, isolated message space")
    p.add_argument("--name", required=True)

    p = add("comm-split", cmd_comm_split, "AMPI_Comm_split by colour and key")
    p.add_argument("--colors", required=True, help="rank:colour,rank:colour (colour '' excludes)")
    p.add_argument("--keys", help="rank:key,rank:key")

    add("comm-list", cmd_comm_list, "List communicators")

    p = add("cart-create", cmd_cart_create, "AMPI_Cart_create: a grid topology")
    p.add_argument("--dims", required=True, help="comma-separated dimensions")
    p.add_argument("--periods", required=True, help="comma-separated true/false")
    p.add_argument("--name")

    p = add("graph-create", cmd_graph_create, "AMPI_Dist_graph_create: an explicit topology")
    p.add_argument("--adj", required=True, help="rank:n1|n2,rank:n3|n4")
    p.add_argument("--name")

    add("neighbors", cmd_neighbors, "Neighbours implied by the communicator topology")

    # windows
    p = add("win-create", cmd_win_create, "AMPI_Win_create: a shared artifact space")
    p.add_argument("--name", required=True)

    p = add("win-put", cmd_win_put, "AMPI_Put, or compare-and-swap with --if-version")
    p.add_argument("--win", required=True)
    p.add_argument("--key", required=True)
    p.add_argument("--if-version", type=int)
    payload_args(p)

    p = add("win-get", cmd_win_get, "AMPI_Get")
    p.add_argument("--win", required=True)
    p.add_argument("--key", required=True)
    p.add_argument("--charge", action="store_true", help="charge the read to your context budget")
    out_args(p)

    p = add("win-list", cmd_win_list, "List window cells")
    p.add_argument("--win", required=True)
    p.add_argument("--prefix", default="")

    p = add("win-accumulate", cmd_win_accumulate, "AMPI_Accumulate: atomic read-modify-write")
    p.add_argument("--win", required=True)
    p.add_argument("--key", required=True)
    p.add_argument("--op", required=True)
    payload_args(p)

    p = add("win-fetch-add", cmd_win_fetch_add, "AMPI_Fetch_and_op on a counter")
    p.add_argument("--win", required=True)
    p.add_argument("--key", required=True)
    p.add_argument("--delta", type=float, default=1.0)

    p = add("win-claim", cmd_win_claim, "Compare-and-swap a work item to yourself")
    p.add_argument("--win", required=True)
    p.add_argument("--key", required=True)
    p.add_argument("--note")

    p = add("win-lock", cmd_win_lock, "AMPI_Win_lock as an expiring lease")
    p.add_argument("--win", required=True)
    p.add_argument("--key", required=True)
    p.add_argument("--mode", default=LOCK_EXCLUSIVE, choices=[LOCK_SHARED, LOCK_EXCLUSIVE])
    p.add_argument("--ttl", type=float, default=300.0)
    p.add_argument("--timeout", type=float, default=120.0)

    p = add("win-unlock", cmd_win_unlock, "AMPI_Win_unlock")
    p.add_argument("--lock-id", required=True)

    p = add("win-fence", cmd_win_fence, "AMPI_Win_fence: barrier plus epoch boundary")
    p.add_argument("--win", required=True)
    p.add_argument("--timeout", type=float, default=1800.0)

    # context
    add("ctx", cmd_ctx, "Show this rank's context budget")
    p = add("ctx-release", cmd_ctx_release, "AMPI_Ctx_release: you compacted, reclaim the budget")
    p.add_argument("--tokens", type=int, required=True)
    p.add_argument("--reason", default="compaction")

    # fault tolerance
    add("revoke", cmd_revoke, "AMPI_Comm_revoke: fail every operation on the communicator")
    p = add("shrink", cmd_shrink, "AMPI_Comm_shrink: rebuild over the survivors")
    p.add_argument("--name")
    p = add("agree", cmd_agree, "AMPI_Comm_agree: fault-tolerant agreement")
    p.add_argument("--value", required=True)
    p.add_argument("--timeout", type=float, default=300.0)
    p = add("respawn", cmd_respawn, "Reset a failed rank for a replacement agent")
    p.add_argument("--target", type=int, required=True)
    p = add("kill", cmd_kill, "Fault injection: declare a rank failed")
    p.add_argument("--target", type=int, required=True)
    p.add_argument("--reason")
    add("comm-resync", cmd_comm_resync,
        "Abandon the in-flight collectives on a communicator and realign sequence numbers")
    add("failures", cmd_failures, "Failure detector state")
    p = add("inbox", cmd_inbox, "Replay everything ever addressed to a rank")
    p.add_argument("--target", type=int)

    p = add("checkpoint", cmd_checkpoint, "Save recoverable rank state")
    p.add_argument("--label")
    payload_args(p)
    p = add("restore", cmd_restore, "Load the latest checkpoint")
    p.add_argument("--label")
    p.add_argument("--target", type=int)
    out_args(p)

    # introspection
    add("status", cmd_status, "Job and rank status")
    add("doctor", cmd_doctor, "Detect deadlock, stuck collectives and suspected failures")
    p = add("trace", cmd_trace, "Dump PAMPI trace events")
    p.add_argument("--target", type=int)
    p.add_argument("--op")
    p.add_argument("--limit", type=int, default=200)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except AmpiError as exc:
        return emit(exc.to_dict(), code=2)
    except FileNotFoundError as exc:
        return emit({"ok": False, "error": "FileNotFound", "message": str(exc)}, code=2)
    except BrokenPipeError:
        return 0
    except Exception as exc:  # noqa: BLE001 - the binding must never leak a traceback
        return emit({"ok": False, "error": type(exc).__name__, "message": str(exc)}, code=2)


if __name__ == "__main__":
    raise SystemExit(main())
