"""``ampi``: the command-line interface.

Two audiences, and the split between them is a design statement.

**Operators** use ``ampi init``, ``ampi status``, ``ampi ranks``, ``ampi trace``,
``ampi report``, ``ampi doctor``.  These are the tools that make a run
inspectable, which matters more here than for MPI because an agent run cannot be
cheaply re-executed to reproduce a problem.

**Agent ranks** use ``ampi worker``, ``ampi send``, ``ampi recv``, ``ampi probe``,
``ampi win get/put/accumulate/lock``, ``ampi barrier``.  An agent with shell
access can therefore *be* a rank without any vendor integration: the protocol is
reachable through a process boundary, exactly as MPI is reachable through a
library boundary.

The ``worker`` subcommand is the important one.  It is a loop that blocks for the
next invocation assigned to a rank, prints the prompt, and waits for the agent to
submit a result.  A Cursor subagent, a headless coding CLI, or a person can all
run it, and the harness cannot tell which.  That interchangeability is what makes
the experiments in this repository reproducible with a different agent vendor.

A note on the two ways to write a harness
-----------------------------------------
The commands above make it possible to put the protocol *in the prompt*: tell the
model "you are rank 3, call ``ampi barrier`` when you are done".  This works, and
is sometimes the only option.  But it makes protocol conformance a matter of
model behaviour, and models forget, improvise, and skip steps.  The recommended
form is the SPMD host-side harness (:func:`agentmpi.launch`), where the protocol
is executed by trusted code and the model is invoked only to transform artifacts.
``experiments/`` contains one harness of each kind and the paper reports the
measured difference in protocol-violation rate.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from . import cost as cost_mod
from . import executor as broker_mod
from . import ft as ft_mod
from . import rma as rma_mod
from .comm import Communicator, make_world
from .constants import ANY_SOURCE, ANY_TAG, BarrierPolicy, LockType, Mode, RankState
from .errors import AmpiError
from .fabric import Fabric, open_fabric
from .ops import get_op
from .rank import RankRuntime
from .runtime import create_job
from .schema import View


def _fabric(args: argparse.Namespace, *, create: bool = False) -> Fabric:
    root = args.root or os.environ.get("AMPI_ROOT")
    if not root:
        raise SystemExit("no fabric root: pass --root or set $AMPI_ROOT")
    return Fabric(root, create=create)


def _comm(args: argparse.Namespace) -> tuple[Fabric, Communicator]:
    fabric = _fabric(args)
    rank = args.rank if args.rank is not None else int(os.environ.get("AMPI_RANK", "-1"))
    if rank < 0:
        raise SystemExit("no rank: pass --rank or set $AMPI_RANK")
    row = fabric.query_one("SELECT context_budget, eager_limit, unexpected_limit FROM ranks WHERE rank=?", (rank,))
    rt = RankRuntime(
        fabric,
        rank,
        context_budget=int(row["context_budget"]) if row else 128_000,
        eager_limit=int(row["eager_limit"]) if row else 2048,
        unexpected_limit=int(row["unexpected_limit"]) if row else None,
        strict_context=False,
    )
    if row is None:
        rt.register(executor_name="cli")
    else:
        rt.incarnation = int(
            fabric.query_one("SELECT incarnation FROM ranks WHERE rank=?", (rank,))["incarnation"]
        )
    ctx = args.ctx if getattr(args, "ctx", None) is not None else 0
    comm = Communicator(fabric, ctx, rt, name=f"ctx{ctx}") if ctx else make_world(fabric, rt)
    return fabric, comm


def _out(obj: Any, *, raw: bool = False) -> None:
    if raw and isinstance(obj, str):
        sys.stdout.write(obj)
        if not obj.endswith("\n"):
            sys.stdout.write("\n")
    else:
        sys.stdout.write(json.dumps(obj, indent=2, ensure_ascii=False, default=str) + "\n")


def _read_payload(args: argparse.Namespace) -> Any:
    if getattr(args, "file", None):
        text = Path(args.file).read_text(encoding="utf-8")
    elif getattr(args, "text", None) is not None:
        text = args.text
    else:
        text = sys.stdin.read()
    if getattr(args, "json", False):
        return json.loads(text)
    return text


# ============================================================================
# operator commands
# ============================================================================


def cmd_init(args: argparse.Namespace) -> int:
    fabric = create_job(args.root, args.size, label=args.label or "")
    _out({"root": str(Path(args.root).resolve()), "job_id": fabric.job_id, "size": args.size})
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    fabric = _fabric(args)
    ranks = fabric.query("SELECT COUNT(*) AS n FROM ranks")[0]["n"]
    summary = cost_mod.summarise(fabric)
    _out(
        {
            "job_id": fabric.job_id,
            "root": str(fabric.root),
            "world_size": int(fabric.get_meta("world_size") or 0),
            "ranks_registered": int(ranks),
            "broker": broker_mod.pending_summary(fabric),
            "summary": summary.as_dict(),
        }
    )
    return 0


def cmd_ranks(args: argparse.Namespace) -> int:
    fabric = _fabric(args)
    reports = ft_mod.health(fabric)
    _out(
        [
            {
                "rank": r.rank,
                "state": r.state,
                "alive": r.alive,
                "lease_age_s": round(r.lease_age, 1),
                "context_occupancy": round(r.context_occupancy, 3),
                "agent_calls": r.n_calls,
                "suspected": r.suspected.value if r.suspected else None,
                "detail": r.detail,
            }
            for r in reports
        ]
    )
    return 0


def cmd_trace(args: argparse.Namespace) -> int:
    fabric = _fabric(args)
    kinds = args.kind or None
    events = fabric.events(since=args.since, kinds=kinds, limit=args.limit)
    if args.format == "jsonl":
        for e in events:
            sys.stdout.write(json.dumps(e, default=str, ensure_ascii=False) + "\n")
    else:
        _out(events)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    fabric = _fabric(args)
    params = cost_mod.calibrate(fabric)
    summary = cost_mod.summarise(fabric)
    report: dict[str, Any] = {
        "job_id": fabric.job_id,
        "calibration": params.as_dict(),
        "summary": summary.as_dict(),
        "per_rank": summary.per_rank,
        "health": [
            {"rank": r.rank, "state": r.state, "suspected": r.suspected.value if r.suspected else None}
            for r in ft_mod.health(fabric)
        ],
    }
    wins = [r["name"] for r in fabric.query("SELECT name FROM windows")]
    if wins:
        report["windows"] = {w: rma_mod.contention_report(fabric, w) for w in wins}
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    _out(report)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Diagnose a stuck or finished run.

    Answers the three questions a harness author asks when a run hangs: who has
    not reported, what is everyone blocked on, and where did the tokens go.  The
    corresponding MPI question is answered by attaching a debugger to 512
    processes, which is why nobody does it.
    """
    fabric = _fabric(args)
    findings: list[dict[str, Any]] = []
    for r in ft_mod.health(fabric):
        if r.suspected:
            findings.append({"severity": "error", "rank": r.rank, "issue": r.suspected.value, "detail": r.detail})
    unmatched = fabric.query(
        "SELECT dst, tag, COUNT(*) AS n, SUM(tokens) AS toks FROM messages WHERE state='queued'"
        " GROUP BY dst, tag ORDER BY n DESC LIMIT 20"
    )
    for row in unmatched:
        findings.append(
            {
                "severity": "warning",
                "issue": "unmatched messages waiting in a mailbox",
                "dst_world_rank": int(row["dst"]),
                "tag": row["tag"],
                "count": int(row["n"]),
                "tokens": int(row["toks"] or 0),
            }
        )
    open_colls = fabric.query(
        "SELECT c.cid, c.ctx, c.op, c.epoch, COUNT(p.crank) AS arrived FROM collectives c"
        " LEFT JOIN coll_parts p ON p.cid=c.cid WHERE c.state='open' GROUP BY c.cid ORDER BY c.cid DESC LIMIT 20"
    )
    for row in open_colls:
        n_members = fabric.query_one("SELECT COUNT(*) AS n FROM comm_members WHERE ctx=?", (int(row["ctx"]),))
        total = int(n_members["n"]) if n_members else 0
        if int(row["arrived"]) < total:
            arrived = {int(x["crank"]) for x in fabric.query("SELECT crank FROM coll_parts WHERE cid=?", (int(row["cid"]),))}
            findings.append(
                {
                    "severity": "error",
                    "issue": "incomplete collective",
                    "op": row["op"],
                    "ctx": int(row["ctx"]),
                    "arrived": sorted(arrived),
                    "missing": sorted(set(range(total)) - arrived),
                }
            )
    locks = fabric.query("SELECT win, slot, holder, mode, expires FROM win_locks")
    now = time.time()
    for row in locks:
        findings.append(
            {
                "severity": "warning" if float(row["expires"]) > now else "error",
                "issue": "window lock held" if float(row["expires"]) > now else "window lock expired but not released",
                "window": row["win"],
                "slot": row["slot"],
                "holder": int(row["holder"]),
                "mode": row["mode"],
            }
        )
    stale = fabric.query(
        "SELECT COUNT(*) AS n FROM events WHERE kind='win.put' AND payload LIKE '%\"stale_write\": true%'"
    )
    if stale and int(stale[0]["n"]) > 0:
        findings.append(
            {
                "severity": "warning",
                "issue": "writes issued from a stale view of shared state (lost-update risk)",
                "count": int(stale[0]["n"]),
            }
        )
    _out({"job_id": fabric.job_id, "n_findings": len(findings), "findings": findings})
    return 1 if any(f["severity"] == "error" for f in findings) else 0


# ============================================================================
# rank-side commands
# ============================================================================


def cmd_send(args: argparse.Namespace) -> int:
    fabric, comm = _comm(args)
    payload = _read_payload(args)
    mid = comm.send(payload, args.to, args.tag, mode=Mode(args.mode), timeout=args.timeout)
    _out({"mid": mid, "to": args.to, "tag": args.tag, "mode": args.mode})
    return 0


def cmd_recv(args: argparse.Namespace) -> int:
    fabric, comm = _comm(args)
    view = View.from_json(json.loads(args.view)) if args.view else None
    msg = comm.recv(
        source=args.source if args.source is not None else ANY_SOURCE,
        tag=args.tag or ANY_TAG,
        view=view,
        admit=None if args.admit is None else bool(args.admit),
        timeout=args.timeout,
    )
    if args.raw:
        _out(msg.payload if msg.payload is not None else "", raw=True)
    else:
        _out(
            {
                "source": msg.source,
                "tag": msg.tag,
                "mode": msg.mode,
                "tokens": msg.tokens,
                "digest": msg.digest,
                "synopsis": msg.synopsis,
                "payload": msg.payload,
            }
        )
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    fabric, comm = _comm(args)
    st = comm.iprobe(args.source if args.source is not None else ANY_SOURCE, args.tag or ANY_TAG)
    if st is None:
        _out({"pending": False})
        return 1
    _out(
        {
            "pending": True,
            "source": st.source,
            "tag": st.tag,
            "mode": st.mode,
            "tokens": st.tokens,
            "digest": st.digest,
            "synopsis": st.synopsis,
            "contract": st.contract.name if st.contract else None,
        }
    )
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    fabric, comm = _comm(args)
    view = View.from_json(json.loads(args.view)) if args.view else None
    payload = comm.fetch(args.digest, view=view, admit=not args.no_admit)
    _out(payload, raw=args.raw)
    return 0


def cmd_barrier(args: argparse.Namespace) -> int:
    fabric, comm = _comm(args)
    res = comm.barrier(timeout=args.timeout, policy=BarrierPolicy(args.policy), label=args.label or "")
    _out(
        {
            "complete": res.complete,
            "algorithm": res.algorithm,
            "arrived": list(res.arrived),
            "absent": list(res.absent),
            "wall_s": round(res.wall_s, 3),
        }
    )
    return 0 if res.complete else 2


def cmd_win(args: argparse.Namespace) -> int:
    fabric, comm = _comm(args)
    win = rma_mod.Window(comm, args.window, create=(args.win_cmd == "create"))
    if args.win_cmd == "create":
        _out({"window": args.window, "model": win.model.value})
    elif args.win_cmd == "list":
        _out([{"slot": s.slot, "version": s.version, "tokens": s.tokens, "updated_by": s.updated_by} for s in win.slots()])
    elif args.win_cmd == "get":
        payload = win.get(args.slot, admit=False)
        _out(payload, raw=args.raw)
    elif args.win_cmd == "put":
        v = win.put(args.slot, _read_payload(args), expect_version=args.expect_version)
        rep = win.staleness(args.slot)
        _out({"slot": args.slot, "version": v, "stale_lag": rep.lag})
    elif args.win_cmd == "accumulate":
        v = win.accumulate(args.slot, _read_payload(args), get_op(args.op))
        _out({"slot": args.slot, "version": v, "op": args.op})
    elif args.win_cmd == "lock":
        win.lock(args.slot, mode=LockType(args.lock_mode), timeout=args.timeout)
        _out({"locked": args.slot, "mode": args.lock_mode})
    elif args.win_cmd == "unlock":
        win.unlock(args.slot)
        _out({"unlocked": args.slot})
    elif args.win_cmd == "sync":
        win.sync()
        _out({"synced": args.window})
    elif args.win_cmd == "report":
        _out(rma_mod.contention_report(fabric, args.window))
    return 0


# ============================================================================
# worker loop
# ============================================================================

WORKER_BANNER = """\
AgentMPI worker: rank {rank} (incarnation {incarnation}) of job {job}
Protocol: run `ampi worker next --rank {rank}` to receive the next task.
          Do the work, write the result to a file, then run
          `ampi worker done --rank {rank} --aid <AID> --file <RESULT>`.
          If you cannot do the task, run
          `ampi worker fail --rank {rank} --aid <AID> --error "<why>"`.
"""


def cmd_worker(args: argparse.Namespace) -> int:
    fabric = _fabric(args)
    rank = args.rank if args.rank is not None else int(os.environ.get("AMPI_RANK", "-1"))
    if rank < 0:
        raise SystemExit("no rank: pass --rank or set $AMPI_RANK")

    if args.worker_cmd == "hello":
        row = fabric.query_one("SELECT incarnation FROM ranks WHERE rank=?", (rank,))
        inc = int(row["incarnation"]) if row else 0
        rt = RankRuntime(fabric, rank, strict_context=False)
        rt.register(executor_name="worker")
        sys.stdout.write(WORKER_BANNER.format(rank=rank, incarnation=rt.incarnation, job=fabric.job_id))
        _out({"rank": rank, "incarnation": rt.incarnation, "job_id": fabric.job_id, "root": str(fabric.root)})
        return 0

    if args.worker_cmd == "next":
        deadline = time.time() + args.timeout
        while True:
            row = broker_mod.claim_next(fabric, rank, lease_s=args.lease)
            if row is not None:
                spool = fabric.root / "spool"
                spool.mkdir(parents=True, exist_ok=True)
                prompt = fabric.blobs.get_text(row["prompt_digest"])
                pfile = spool / f"call-{row['aid']}.prompt.md"
                pfile.write_text(prompt, encoding="utf-8")
                meta = json.loads(row["meta"] or "{}")
                _out(
                    {
                        "status": "task",
                        "aid": int(row["aid"]),
                        "rank": rank,
                        "label": row["label"],
                        "attempt": int(row["attempt"]),
                        "prompt_tokens": int(row["prompt_tokens"]),
                        "prompt_file": str(pfile),
                        "result_file": str(spool / f"call-{row['aid']}.result"),
                        "contract": json.loads(row["contract"]) if row["contract"] else None,
                        "meta": meta,
                        "prompt": prompt if args.inline else None,
                    }
                )
                return 0
            # Renew the lease while idle so the failure detector does not fire on
            # a worker that is merely waiting for work.
            with fabric.write() as cur:
                cur.execute(
                    "UPDATE ranks SET lease_expires=?, last_seen=? WHERE rank=?",
                    (time.time() + args.lease, time.time(), rank),
                )
            if time.time() > deadline:
                done = fabric.query_one(
                    "SELECT value FROM meta WHERE key=?", (f"worker_stop:{rank}",)
                ) or fabric.query_one("SELECT value FROM meta WHERE key='worker_stop:all'")
                _out({"status": "exit" if done else "idle", "rank": rank})
                return 0 if done else 3
            time.sleep(args.poll)

    if args.worker_cmd == "done":
        payload: Any
        if args.file:
            text = Path(args.file).read_text(encoding="utf-8")
            payload = json.loads(text) if args.json else text
        else:
            text = sys.stdin.read()
            payload = json.loads(text) if args.json else text
        broker_mod.complete(fabric, args.aid, payload)
        _out({"status": "done", "aid": args.aid})
        return 0

    if args.worker_cmd == "fail":
        broker_mod.fail(fabric, args.aid, args.error or "unspecified")
        _out({"status": "failed", "aid": args.aid})
        return 0

    if args.worker_cmd == "stop":
        fabric.set_meta(f"worker_stop:{rank}" if rank >= 0 else "worker_stop:all", "1")
        _out({"status": "stop_requested", "rank": rank})
        return 0

    if args.worker_cmd == "stop-all":
        fabric.set_meta("worker_stop:all", "1")
        _out({"status": "stop_requested", "rank": "all"})
        return 0

    raise SystemExit(f"unknown worker subcommand {args.worker_cmd}")


def cmd_broker(args: argparse.Namespace) -> int:
    fabric = _fabric(args)
    if args.broker_cmd == "status":
        _out(broker_mod.pending_summary(fabric))
        return 0
    if args.broker_cmd == "list":
        rows = fabric.query(
            "SELECT aid, rank, label, state, prompt_tokens, result_tokens, attempt, created_at FROM agent_calls"
            " WHERE (? IS NULL OR state=?) ORDER BY aid DESC LIMIT ?",
            (args.state, args.state, args.limit),
        )
        _out([dict(r) for r in rows])
        return 0
    if args.broker_cmd == "prompt":
        row = fabric.query_one("SELECT prompt_digest FROM agent_calls WHERE aid=?", (args.aid,))
        if row is None:
            raise SystemExit(f"no such call {args.aid}")
        _out(fabric.blobs.get_text(row["prompt_digest"]), raw=True)
        return 0
    if args.broker_cmd == "wait":
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            s = broker_mod.pending_summary(fabric)
            if s["n_pending"] >= args.n:
                _out(s)
                return 0
            time.sleep(1.0)
        _out(broker_mod.pending_summary(fabric))
        return 3
    raise SystemExit(f"unknown broker subcommand {args.broker_cmd}")


# ============================================================================
# argument parsing
# ============================================================================


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ampi", description="AgentMPI command line interface")
    p.add_argument("--root", help="fabric directory (default $AMPI_ROOT)")
    sub = p.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("init", help="create a fabric and fix the world size")
    q.add_argument("--size", type=int, required=True)
    q.add_argument("--label", default="")
    q.set_defaults(func=cmd_init)

    q = sub.add_parser("status", help="job overview")
    q.set_defaults(func=cmd_status)

    q = sub.add_parser("ranks", help="per-rank health")
    q.set_defaults(func=cmd_ranks)

    q = sub.add_parser("trace", help="dump the event log")
    q.add_argument("--since", type=int, default=0)
    q.add_argument("--kind", action="append")
    q.add_argument("--limit", type=int, default=100000)
    q.add_argument("--format", choices=["json", "jsonl"], default="jsonl")
    q.set_defaults(func=cmd_trace)

    q = sub.add_parser("report", help="calibrated cost report")
    q.add_argument("--out")
    q.set_defaults(func=cmd_report)

    q = sub.add_parser("doctor", help="diagnose a stuck or finished run")
    q.set_defaults(func=cmd_doctor)

    common_rank = argparse.ArgumentParser(add_help=False)
    common_rank.add_argument("--rank", type=int)
    common_rank.add_argument("--ctx", type=int, default=0)

    q = sub.add_parser("send", parents=[common_rank], help="send a message")
    q.add_argument("--to", type=int, required=True)
    q.add_argument("--tag", default="default")
    q.add_argument("--file")
    q.add_argument("--text")
    q.add_argument("--json", action="store_true")
    q.add_argument("--mode", choices=[m.value for m in Mode], default=Mode.AUTO.value)
    q.add_argument("--timeout", type=float, default=300.0)
    q.set_defaults(func=cmd_send)

    q = sub.add_parser("recv", parents=[common_rank], help="receive a message")
    q.add_argument("--source", type=int)
    q.add_argument("--tag")
    q.add_argument("--view", help="JSON view specification")
    q.add_argument("--admit", type=int, choices=[0, 1])
    q.add_argument("--raw", action="store_true")
    q.add_argument("--timeout", type=float, default=600.0)
    q.set_defaults(func=cmd_recv)

    q = sub.add_parser("probe", parents=[common_rank], help="inspect the next message without receiving it")
    q.add_argument("--source", type=int)
    q.add_argument("--tag")
    q.set_defaults(func=cmd_probe)

    q = sub.add_parser("fetch", parents=[common_rank], help="materialise an artifact by digest")
    q.add_argument("digest")
    q.add_argument("--view")
    q.add_argument("--no-admit", action="store_true")
    q.add_argument("--raw", action="store_true")
    q.set_defaults(func=cmd_fetch)

    q = sub.add_parser("barrier", parents=[common_rank], help="enter a barrier")
    q.add_argument("--timeout", type=float, default=900.0)
    q.add_argument("--policy", choices=[b.value for b in BarrierPolicy], default=BarrierPolicy.PROCEED.value)
    q.add_argument("--label", default="")
    q.set_defaults(func=cmd_barrier)

    q = sub.add_parser("win", parents=[common_rank], help="one-sided shared state")
    q.add_argument("window")
    wsub = q.add_subparsers(dest="win_cmd", required=True)
    for name in ("create", "list", "sync", "report"):
        wsub.add_parser(name)
    wg = wsub.add_parser("get")
    wg.add_argument("slot")
    wg.add_argument("--raw", action="store_true")
    wp = wsub.add_parser("put")
    wp.add_argument("slot")
    wp.add_argument("--file")
    wp.add_argument("--text")
    wp.add_argument("--json", action="store_true")
    wp.add_argument("--expect-version", type=int)
    wa = wsub.add_parser("accumulate")
    wa.add_argument("slot")
    wa.add_argument("--op", default="UNION")
    wa.add_argument("--file")
    wa.add_argument("--text")
    wa.add_argument("--json", action="store_true")
    wl = wsub.add_parser("lock")
    wl.add_argument("slot")
    wl.add_argument("--lock-mode", choices=[m.value for m in LockType], default=LockType.EXCLUSIVE.value)
    wl.add_argument("--timeout", type=float, default=300.0)
    wu = wsub.add_parser("unlock")
    wu.add_argument("slot")
    q.set_defaults(func=cmd_win)

    q = sub.add_parser("worker", help="act as an agent rank")
    q.add_argument("--rank", type=int)
    wsub2 = q.add_subparsers(dest="worker_cmd", required=True)
    wsub2.add_parser("hello")
    wn = wsub2.add_parser("next")
    wn.add_argument("--timeout", type=float, default=240.0, help="how long to block waiting for work")
    wn.add_argument("--poll", type=float, default=2.0)
    wn.add_argument("--lease", type=float, default=1800.0)
    wn.add_argument("--inline", action="store_true", help="include the prompt text in the JSON output")
    wd = wsub2.add_parser("done")
    wd.add_argument("--aid", type=int, required=True)
    wd.add_argument("--file")
    wd.add_argument("--json", action="store_true")
    wfa = wsub2.add_parser("fail")
    wfa.add_argument("--aid", type=int, required=True)
    wfa.add_argument("--error")
    wsub2.add_parser("stop")
    wsub2.add_parser("stop-all")
    q.set_defaults(func=cmd_worker)

    q = sub.add_parser("broker", help="inspect the agent-invocation queue")
    bsub = q.add_subparsers(dest="broker_cmd", required=True)
    bsub.add_parser("status")
    bl = bsub.add_parser("list")
    bl.add_argument("--state")
    bl.add_argument("--limit", type=int, default=50)
    bp = bsub.add_parser("prompt")
    bp.add_argument("aid", type=int)
    bw = bsub.add_parser("wait")
    bw.add_argument("--n", type=int, default=1)
    bw.add_argument("--timeout", type=float, default=600.0)
    q.set_defaults(func=cmd_broker)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except AmpiError as exc:
        _out({"error": type(exc).__name__, "class": exc.cls_name, "message": str(exc)})
        return 4
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
