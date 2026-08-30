"""The ``ampi`` command-line tool.

This is the piece that makes AgentMPI usable by the executors that actually
exist.  A coding agent cannot be handed a Python object or a socket, but
every one of them can run a shell command and read a file.  So the protocol
gets a command-line binding, and the rule becomes:

    **anything that can run a shell command can be an AgentMPI rank.**

The parallel is exact.  MPI's success owed a great deal to there being
bindings for the languages people actually wrote HPC code in -- Fortran
first, then C, then C++ and Python -- rather than a single blessed one.  An
agent protocol that only has a Python binding is a Python framework.  The
CLI is what makes AgentMPI a protocol: a rank implemented by Claude Code, by
a Cursor subagent, by a shell script, or by a Python program are
indistinguishable on the wire, and they interoperate in one job.

Each invocation is a complete, short-lived process: it initialises, performs
one operation, persists its protocol counters, and exits.  Blocking
operations really do block, which is what an agent wants -- ``ampi recv``
returns when the message arrives, and until then the agent is simply waiting
at a shell prompt.

Every subcommand takes ``--root`` (or ``$AMPI_ROOT``) and ``--rank`` (or
``$AMPI_RANK``); those two values are the whole of a rank's identity.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .constants import ANY_SOURCE, ANY_TAG, CollAlgorithm, SendMode
from .errors import AmpiError
from .group import RankSpec
from .runtime import RUN_MANIFEST, RunManifest, Runtime, init


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _read_value(args: argparse.Namespace) -> Any:
    """Resolve the payload from --text / --file / --json / stdin."""
    if getattr(args, "json", None) is not None:
        return json.loads(args.json)
    if getattr(args, "text", None) is not None:
        return args.text
    if getattr(args, "file", None):
        text = Path(args.file).read_text(encoding="utf-8")
        if getattr(args, "type", "text") == "json":
            return json.loads(text)
        return text
    if getattr(args, "stdin", False) or not sys.stdin.isatty():
        text = sys.stdin.read()
        if text:
            if getattr(args, "type", "text") == "json":
                return json.loads(text)
            return text
    return None


def _emit(args: argparse.Namespace, value: Any, extra: dict[str, Any] | None = None) -> None:
    """Write the result where the agent asked for it, and print a summary."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    if getattr(args, "out", None):
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text if text is not None else "", encoding="utf-8")
        summary = {"written": args.out, "chars": len(text or "")}
        if extra:
            summary.update(extra)
        print(json.dumps(summary, ensure_ascii=False))
    elif getattr(args, "quiet", False):
        if extra:
            print(json.dumps(extra, ensure_ascii=False))
    else:
        print(text if text is not None else "")


def _require_run(root: str) -> None:
    """Fail with a diagnosis, not a traceback, when the run is gone.

    A rank outliving its run directory is not exotic: the run may have been
    cleaned up, moved, or recreated while this rank was blocked in a
    collective. The rank cannot repair that and should say so plainly, since
    the alternative is a stack trace about a missing file that tells the
    operator nothing about what actually happened.
    """
    if not os.path.isdir(root):
        raise SystemExit(json.dumps({
            "error": "ERR_NO_RUN",
            "message": f"the run directory {root} does not exist; it was "
                       f"removed or moved while this rank was running",
        }))
    if not os.path.exists(os.path.join(root, RUN_MANIFEST)):
        raise SystemExit(json.dumps({
            "error": "ERR_NO_RUN",
            "message": f"{root} has no {RUN_MANIFEST}; the run was deleted or "
                       f"is being recreated. This rank cannot continue and "
                       f"should stop rather than issue further collectives.",
        }))


def _expected_type(args: argparse.Namespace, rank: int):
    """Build the receive datatype, attaching any ``--expect`` assertions.

    The motivating case is a self-identifying payload. A work assignment that
    carries its own ``rank`` field can be checked against the rank that
    received it, and four of our agents did that check by hand after noticing
    the numbers disagreed. One of them then reasoned its way to the right
    action and the others did not, which is the usual outcome when a
    correctness check lives in an agent's judgement rather than in the type.
    Declaring it turns a silent misdelivery into ``AMPI_ERR_CONTRACT`` at the
    point of receipt, naming both values.
    """
    from .datatypes import lookup, type_contract

    base = lookup(getattr(args, "type", "text"))
    clauses = getattr(args, "expect", None)
    if not clauses:
        return base

    wanted: dict[str, str] = {}
    for clause in clauses:
        key, _, value = clause.partition("=")
        if not key or not _:
            raise SystemExit(f"error: --expect needs KEY=VALUE, got {clause!r}")
        wanted[key.strip()] = value.strip().replace("{rank}", str(rank))

    def validate(value):
        problems = []
        if not isinstance(value, dict):
            return (f"expected a JSON object to check {sorted(wanted)} against, "
                    f"got {type(value).__name__}",)
        for key, expected in wanted.items():
            if key not in value:
                problems.append(f"payload has no field {key!r}")
            elif str(value[key]) != expected:
                problems.append(
                    f"field {key!r} is {value[key]!r}, expected {expected!r} "
                    f"-- this payload was not addressed to rank {rank}")
        return tuple(problems)

    return type_contract(base, validators=[validate],
                         name=f"{base.name}/expect")


def _open(args: argparse.Namespace) -> Runtime:
    root = getattr(args, "run_root", None) or os.environ.get("AMPI_ROOT")
    if not root:
        raise SystemExit("error: --root or $AMPI_ROOT is required")
    _require_run(str(root))
    rank = args.rank if args.rank is not None else os.environ.get("AMPI_RANK")
    if rank is None:
        raise SystemExit("error: --rank or $AMPI_RANK is required")
    cvars: dict[str, Any] = {}
    if getattr(args, "capacity", None):
        cvars["ampi_context_capacity"] = args.capacity
    if getattr(args, "no_admission", False):
        cvars["ampi_admission_control"] = False
    if getattr(args, "expect", None):
        # An explicit assertion is meant to fail, not to be noted. The
        # default is lenient because most contract violations are a quality
        # signal a harness may want to see and continue past; --expect is the
        # caller saying this one is not.
        cvars["ampi_strict_contracts"] = True
    return init(root=str(root), rank=int(rank), device="journal", cvars=cvars)


def _comm(rt: Runtime, args: argparse.Namespace):
    comm = rt.world
    name = getattr(args, "comm", None)
    if name and name != "world":
        # A named subcommunicator is derived deterministically from the world
        # context so that every rank resolves the same name to the same
        # context id without an extra round of agreement.
        from .comm import Communicator

        comm = rt._register_comm(
            Communicator(rt, f"world/{name}", rt.world.group, name=name, epoch=rt.world.epoch)
        )
    return comm


def _finish(rt: Runtime) -> None:
    rt.save_state()
    rt.finalize()


# --------------------------------------------------------------------------
# subcommands
# --------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> int:
    """Create a run directory and its manifest."""
    root = Path(args.root)
    existing = root / RUN_MANIFEST
    if existing.exists() and not args.force:
        # Refuse to layer a new run over an old one.
        #
        # Writing a fresh manifest into a directory that still holds another
        # run's inbox, blobs and key-value state produces a job that starts
        # and then delivers the previous run's messages: we watched ranks
        # receive one another's work assignments this way, "shifted by one
        # rank", which is impossible to diagnose from inside a rank. Making
        # the operator say so explicitly is much cheaper than making the
        # protocol survive it.
        try:
            prior = RunManifest.load(existing).run_id
        except Exception:
            prior = "unreadable"
        raise SystemExit(json.dumps({
            "error": "ERR_RUN_EXISTS",
            "message": f"{root} already holds run {prior!r}. Remove the "
                       f"directory, choose another path, or pass --force to "
                       f"clear the transport and start a new run here.",
        }))
    if args.force and root.exists():
        for sub in ("blobs", "kv", "inbox", "seen", "ack", "journal", "hb", "locks"):
            shutil.rmtree(root / sub, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    roles: list[dict[str, Any]] = []
    if args.roles:
        roles = json.loads(Path(args.roles).read_text(encoding="utf-8"))
    ranks = []
    for i in range(args.ranks):
        entry = {"rank": i, "role": "worker", "model": args.model,
                 "context_capacity": args.capacity or 128000}
        if i < len(roles):
            entry.update(roles[i])
            entry["rank"] = i
        ranks.append(entry)
    cvars: dict[str, Any] = {}
    if args.capacity:
        cvars["ampi_context_capacity"] = args.capacity
    if args.cvar:
        for item in args.cvar:
            key, _, value = item.partition("=")
            cvars[key] = _coerce(value)
    manifest = RunManifest(
        run_id=args.label or f"run-{int(time.time())}",
        size=args.ranks, created_at=time.time(), device="journal",
        label=args.label or "", ranks=ranks, cvars=cvars,
    )
    (root / RUN_MANIFEST).write_text(manifest.to_json(), encoding="utf-8")
    for sub in ("blobs", "kv", "inbox", "seen", "ack", "journal", "hb", "locks"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    print(json.dumps({"root": str(root), "run_id": manifest.run_id, "size": args.ranks}))
    return 0


def _coerce(value: str) -> Any:
    low = value.strip().lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def cmd_info(args: argparse.Namespace) -> int:
    root = Path(args.run_root or os.environ.get("AMPI_ROOT", "."))
    _require_run(str(root))
    manifest = RunManifest.load(root / RUN_MANIFEST)
    print(json.dumps({
        "run_id": manifest.run_id, "size": manifest.size, "label": manifest.label,
        "created_at": manifest.created_at, "cvars": manifest.cvars,
        "ranks": manifest.ranks,
    }, indent=2))
    return 0


def cmd_rank(args: argparse.Namespace) -> int:
    rt = _open(args)
    print(json.dumps({"rank": rt.world_rank, "size": rt.world_size,
                      "role": rt.spec.role, "model": rt.spec.model,
                      "context_capacity": rt.budget.capacity,
                      "usable_tokens": rt.budget.usable}))
    _finish(rt)
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    rt = _open(args)
    comm = _comm(rt, args)
    value = _read_value(args)
    status = comm.send(value, args.dest, args.tag, args.type,
                       mode=SendMode(args.mode), timeout=args.timeout)
    print(json.dumps({"sent": True, "dest": args.dest, "tag": args.tag,
                      "tokens": status.tokens, "seq": status.seq,
                      "reduced": status.reduced}))
    _finish(rt)
    return 0


def cmd_recv(args: argparse.Namespace) -> int:
    rt = _open(args)
    comm = _comm(rt, args)
    value, status = comm.recv(args.source, args.tag, _expected_type(args, comm.rank),
                              timeout=args.timeout)
    _emit(args, value, {"source": status.source, "tag": status.tag,
                        "tokens": status.tokens, "waited_s": round(status.wait_time_s, 2),
                        "contract_ok": status.contract_ok,
                        "violations": list(status.violations)})
    _finish(rt)
    return 0


def cmd_barrier(args: argparse.Namespace) -> int:
    rt = _open(args)
    comm = _comm(rt, args)
    t0 = time.time()
    comm.barrier(timeout=args.timeout)
    print(json.dumps({"barrier": "released", "rank": comm.rank,
                      "waited_s": round(time.time() - t0, 2)}))
    _finish(rt)
    return 0


def cmd_bcast(args: argparse.Namespace) -> int:
    rt = _open(args)
    comm = _comm(rt, args)
    value = _read_value(args) if comm.rank == args.root else None
    got = comm.bcast(value, args.root, datatype=_expected_type(args, comm.rank),
                     algorithm=CollAlgorithm(args.algorithm), timeout=args.timeout)
    _emit(args, got, {"root": args.root})
    _finish(rt)
    return 0


def cmd_scatter(args: argparse.Namespace) -> int:
    rt = _open(args)
    comm = _comm(rt, args)
    values = None
    if comm.rank == args.root:
        values = _read_value(args)
        if not isinstance(values, list):
            raise SystemExit("error: the root of a scatter must supply a JSON list")
    mine = comm.scatterv(values, args.root, datatype=_expected_type(args, comm.rank),
                         timeout=args.timeout)
    _emit(args, mine, {"root": args.root})
    _finish(rt)
    return 0


def cmd_gather(args: argparse.Namespace) -> int:
    rt = _open(args)
    comm = _comm(rt, args)
    value = _read_value(args)
    got = comm.gather(value, args.root, datatype=args.type, timeout=args.timeout)
    if comm.rank == args.root:
        _emit(args, got, {"gathered": len(got or [])})
    else:
        print(json.dumps({"contributed": True, "root": args.root}))
    _finish(rt)
    return 0


def cmd_allgather(args: argparse.Namespace) -> int:
    rt = _open(args)
    comm = _comm(rt, args)
    got = comm.allgather(_read_value(args), datatype=args.type, timeout=args.timeout)
    _emit(args, got, {"gathered": len(got)})
    _finish(rt)
    return 0


def cmd_reduce(args: argparse.Namespace) -> int:
    rt = _open(args)
    comm = _comm(rt, args)
    got = comm.reduce(_read_value(args), args.op, args.root,
                      algorithm=CollAlgorithm(args.algorithm), timeout=args.timeout)
    if comm.rank == args.root:
        _emit(args, got, {"op": args.op})
    else:
        print(json.dumps({"contributed": True, "root": args.root, "op": args.op}))
    _finish(rt)
    return 0


def cmd_allreduce(args: argparse.Namespace) -> int:
    rt = _open(args)
    comm = _comm(rt, args)
    got = comm.allreduce(_read_value(args), args.op,
                         algorithm=CollAlgorithm(args.algorithm), timeout=args.timeout)
    _emit(args, got, {"op": args.op})
    _finish(rt)
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    rt = _open(args)
    comm = _comm(rt, args)
    got = comm.scan(_read_value(args), args.op, timeout=args.timeout) if args.inclusive \
        else comm.exscan(_read_value(args), args.op, timeout=args.timeout)
    _emit(args, got, {"op": args.op, "inclusive": args.inclusive})
    _finish(rt)
    return 0


def cmd_alltoall(args: argparse.Namespace) -> int:
    rt = _open(args)
    comm = _comm(rt, args)
    values = _read_value(args)
    if not isinstance(values, list) or len(values) != comm.size:
        raise SystemExit(f"error: alltoall needs a JSON list of exactly {comm.size} values")
    got = comm.alltoall(values, timeout=args.timeout)
    _emit(args, got)
    _finish(rt)
    return 0


def cmd_win(args: argparse.Namespace) -> int:
    from .win import win_create

    rt = _open(args)
    comm = _comm(rt, args)
    win = win_create(comm, args.window)
    op = args.win_op

    if op == "put":
        ref = win.put(args.key, _read_value(args), tag=args.tag_text or "")
        print(json.dumps({"key": ref.key, "version": ref.version, "tokens": ref.tokens}))
    elif op == "get":
        ref = win.get(args.key)
        if ref is None:
            print(json.dumps({"key": args.key, "found": False}))
        elif args.materialize:
            _emit(args, win.materialize(ref, budget=args.budget),
                  {"key": args.key, "version": ref.version, "tokens": ref.tokens})
        else:
            print(json.dumps(ref.to_json(), indent=2))
    elif op == "acc":
        ref = win.accumulate(args.key, _read_value(args), args.op)
        print(json.dumps({"key": ref.key, "version": ref.version, "tokens": ref.tokens}))
    elif op == "index":
        _emit(args, win.index(), {"entries": len(win.index())})
    elif op == "query":
        result = win.query(args.question, budget=args.budget)
        _emit(args, result, {"entries_returned": result["entries_returned"],
                             "tokens": result["tokens"]})
    elif op == "fetch-add":
        previous = win.fetch_and_op(args.key, args.delta)
        print(json.dumps({"key": args.key, "previous": previous,
                          "now": previous + args.delta}))
    elif op == "fence":
        win.fence(timeout=args.timeout)
        print(json.dumps({"window": args.window, "epoch": win.epoch_id}))
    else:
        raise SystemExit(f"unknown window operation {op!r}")
    _finish(rt)
    return 0


def cmd_file(args: argparse.Namespace) -> int:
    from .fileio import FileView, file_open

    rt = _open(args)
    comm = _comm(rt, args)
    handle = file_open(comm, args.path, aggregators=args.aggregators)
    if args.file_op == "write-at-all":
        if args.start is not None:
            handle.set_view(FileView(path=args.path, start=args.start,
                                     length=args.length, unit=args.unit), check=False)
        value = _read_value(args)
        artifact = handle.write_at_all(value if isinstance(value, str) else json.dumps(value),
                                       timeout=args.timeout)
        print(json.dumps({"path": args.path, "aggregator": handle._aggregator_for(args.path),
                          "published": artifact is not None,
                          "version": artifact.version if artifact else None}))
    elif args.file_op == "append":
        artifact = handle.write_shared(_read_value(args) or "")
        print(json.dumps({"path": args.path, "slot": artifact.version}))
    elif args.file_op == "read":
        _emit(args, handle.read_all())
    else:
        raise SystemExit(f"unknown file operation {args.file_op!r}")
    _finish(rt)
    return 0


def cmd_progress(args: argparse.Namespace) -> int:
    """Announce that this rank finished a turn.

    Distinct from a heartbeat on purpose: a heartbeat says the process is
    alive, a progress report says it got somewhere.  A harness that only
    heartbeats cannot distinguish a working agent from one stuck in a loop,
    which is the single most common way an agent job wedges.
    """
    rt = _open(args)
    rt.note_progress()
    if args.spend:
        rt.spend(args.spend)
    print(json.dumps({"rank": rt.world_rank, "turn": rt.turn,
                      "pressure": round(rt.budget.pressure, 3)}))
    _finish(rt)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    rt = _open(args)
    status = rt.status()
    peers = {}
    for r in range(rt.world_size):
        health = rt.peer_health(r)
        peers[r] = {
            "state": (health or {}).get("state", "unknown"),
            "turn": (health or {}).get("turn"),
            "age_s": round(time.time() - float((health or {}).get("ts", time.time())), 1)
            if health else None,
        }
    status["peers"] = peers
    print(json.dumps(status, indent=2))
    _finish(rt)
    return 0


def cmd_peers(args: argparse.Namespace) -> int:
    """Read-only view of the job, without joining it.

    Distinct from ``status`` on purpose.  ``status`` is a rank reporting on
    itself and therefore joins the job, publishes a heartbeat, and persists
    protocol counters; running it from a monitoring script would have that
    script impersonate a rank that already exists.  Observation must not
    perturb the thing observed, so ``peers`` opens the device and reads.
    """
    from .transport import JournalDevice

    root = args.run_root or os.environ.get("AMPI_ROOT")
    if not root:
        raise SystemExit("error: --root or $AMPI_ROOT is required")
    _require_run(str(root))
    device = JournalDevice(root, owner="observer")
    manifest = RunManifest.load(Path(root) / RUN_MANIFEST)
    now = time.time()
    rows = []
    for rank in range(manifest.size):
        raw = device.kv_get(f"hb/{rank}")
        health = json.loads(raw) if raw else None
        rows.append({
            "rank": rank,
            "state": (health or {}).get("state", "unseen"),
            "turn": (health or {}).get("turn"),
            "tokens": (health or {}).get("tokens"),
            "pressure": (health or {}).get("pressure"),
            "age_s": round(now - float(health["ts"]), 1) if health else None,
        })
    alive = [r for r in rows if r["age_s"] is not None and r["age_s"] < 120]
    print(json.dumps({
        "run": manifest.run_id, "size": manifest.size,
        "seen": sum(1 for r in rows if r["age_s"] is not None),
        "recent": len(alive),
        "turns_total": sum(r["turn"] or 0 for r in rows),
        "tokens_total": sum(r["tokens"] or 0 for r in rows),
        "peers": rows,
    }, indent=2))
    return 0


def cmd_checkpoint(args: argparse.Namespace) -> int:
    from .ft import checkpoint, restore

    rt = _open(args)
    comm = _comm(rt, args)
    if args.restore:
        cp = restore(comm)
        print(json.dumps({"restored": cp is not None,
                          "turn": cp.turn if cp else None,
                          "state": cp.state if cp else None}, indent=2))
    else:
        state = _read_value(args) or {}
        cp = checkpoint(comm, state if isinstance(state, dict) else {"value": state})
        print(json.dumps({"checkpointed": True, "turn": cp.turn}))
    _finish(rt)
    return 0


def cmd_ft(args: argparse.Namespace) -> int:
    from .ft import comm_agree, comm_revoke, comm_shrink

    rt = _open(args)
    comm = _comm(rt, args)
    if args.ft_op == "revoke":
        comm_revoke(comm)
        print(json.dumps({"revoked": comm.context}))
    elif args.ft_op == "agree":
        value = comm_agree(comm, _read_value(args), op=args.op, timeout=args.timeout)
        _emit(args, value)
    elif args.ft_op == "shrink":
        shrunk = comm_shrink(comm, timeout=args.timeout)
        print(json.dumps({"from": comm.size, "to": shrunk.size,
                          "epoch": shrunk.epoch,
                          "members": list(shrunk.group.members)}))
    elif args.ft_op == "detect":
        rt.check_failures(comm)
        print(json.dumps({"failed": sorted(comm.failed),
                          "alive": [r for r in range(comm.size) if r not in comm.failed]}))
    _finish(rt)
    return 0


def cmd_trace(args: argparse.Namespace) -> int:
    from .trace import communication_matrix, summarize
    from .transport import JournalDevice

    root = args.root or os.environ.get("AMPI_ROOT")
    device = JournalDevice(root)
    events = list(device.read_journal("trace"))
    if args.trace_op == "summary":
        manifest = RunManifest.load(Path(root) / RUN_MANIFEST)
        report = summarize(events)
        report["matrix"] = communication_matrix(events, manifest.size)
        report["lifecycle"] = list(device.read_journal("lifecycle"))
        print(json.dumps(report, indent=2))
    elif args.trace_op == "export":
        out = Path(args.out or "trace.jsonl")
        out.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
        print(json.dumps({"written": str(out), "events": len(events)}))
    return 0


def cmd_launchplan(args: argparse.Namespace) -> int:
    from .launch import write_launch_plan

    root = Path(args.root)
    manifest = RunManifest.load(root / RUN_MANIFEST)
    out = write_launch_plan(root, manifest, program=args.program,
                            out_dir=Path(args.out) if args.out else None)
    print(json.dumps({"written": out, "ranks": manifest.size}))
    return 0


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ampi",
        description="AgentMPI: message passing for multi-agent harnesses. "
                    "Any process that can run this command can be a rank.",
    )
    p.add_argument("--version", action="version", version=f"ampi {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp, *, needs_rank: bool = True) -> None:
        # The run directory is spelled --run-root on rank commands so that
        # --root can keep its MPI meaning there: the root *rank* of a
        # collective.  In practice neither is typed, because $AMPI_ROOT and
        # $AMPI_RANK are exported into every rank's shell by the launcher.
        names = ["--run-root"] if needs_rank else ["--run-root", "--root"]
        sp.add_argument(*names, dest="run_root",
                        default=os.environ.get("AMPI_ROOT"),
                        help="run directory (default $AMPI_ROOT)")
        if needs_rank:
            sp.add_argument("--rank", type=int,
                            default=int(os.environ["AMPI_RANK"])
                            if os.environ.get("AMPI_RANK") else None,
                            help="this rank's index (default $AMPI_RANK)")
            sp.add_argument("--comm", default="world", help="communicator name")
            sp.add_argument("--capacity", type=int, default=None,
                            help="override this rank's context capacity, in tokens")
            sp.add_argument("--no-admission", action="store_true",
                            help="disable context admission control")

    def payload(sp) -> None:
        sp.add_argument("--text", help="payload given inline")
        sp.add_argument("--json", help="payload given inline as JSON")
        sp.add_argument("--file", help="read the payload from a file")
        sp.add_argument("--stdin", action="store_true", help="read the payload from stdin")
        sp.add_argument("--type", default="text",
                        choices=["text", "json", "patch", "artifact", "toolcall", "digest"],
                        help="datatype (contract) of the payload")

    def output(sp) -> None:
        sp.add_argument("--out", help="write the result to this file instead of stdout")
        sp.add_argument("--quiet", action="store_true")

    def timeout(sp, default: float = 1800.0) -> None:
        sp.add_argument("--timeout", type=float, default=default,
                        help="seconds to block before giving up")

    sp = sub.add_parser("init", help="create a run directory")
    sp.add_argument("--root", required=True)
    sp.add_argument("--ranks", type=int, required=True)
    sp.add_argument("--roles", help="JSON file with one object per rank")
    sp.add_argument("--model", default="unknown")
    sp.add_argument("--capacity", type=int, default=None)
    sp.add_argument("--label", default="")
    sp.add_argument("--cvar", action="append", help="control variable, name=value")
    sp.add_argument("--force", action="store_true",
                    help="clear an existing run's transport and start over here")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("info", help="print the run manifest")
    common(sp, needs_rank=False)
    sp.set_defaults(func=cmd_info)

    sp = sub.add_parser("rank", help="print this rank's identity")
    common(sp)
    sp.set_defaults(func=cmd_rank)

    sp = sub.add_parser("send", help="send a message to one rank")
    common(sp)
    payload(sp)
    timeout(sp)
    sp.add_argument("--dest", type=int, required=True)
    sp.add_argument("--tag", type=int, default=0)
    sp.add_argument("--mode", default="standard",
                    choices=[m.value for m in SendMode],
                    help="'synchronous' completes only once the peer ingests it")
    sp.set_defaults(func=cmd_send)

    sp = sub.add_parser("recv", help="receive one message (blocks)")
    common(sp)
    output(sp)
    timeout(sp)
    sp.add_argument("--source", type=int, default=ANY_SOURCE, help="-1 = any source")
    sp.add_argument("--tag", type=int, default=ANY_TAG, help="-1 = any tag")
    sp.add_argument("--type", default="text",
                    choices=["text", "json", "patch", "artifact", "toolcall", "digest"])
    sp.add_argument(
        "--expect", action="append",
        help="assert a top-level field of the received JSON, as KEY=VALUE; "
             "{rank} expands to this rank. A mismatch raises "
             "AMPI_ERR_CONTRACT rather than being silently accepted.")
    sp.set_defaults(func=cmd_recv)

    sp = sub.add_parser("barrier", help="wait for every rank to arrive")
    common(sp)
    timeout(sp)
    sp.set_defaults(func=cmd_barrier)

    sp = sub.add_parser("bcast", help="broadcast from a root to everyone")
    common(sp)
    payload(sp)
    output(sp)
    timeout(sp)
    sp.add_argument("--root", type=int, default=0)
    sp.add_argument("--algorithm", default="auto", choices=[a.value for a in CollAlgorithm])
    sp.add_argument(
        "--expect", action="append",
        help="assert a top-level field of the received JSON, as KEY=VALUE; "
             "{rank} expands to this rank. A mismatch raises "
             "AMPI_ERR_CONTRACT rather than being silently accepted.")
    sp.set_defaults(func=cmd_bcast)

    sp = sub.add_parser("scatter", help="deal one piece of a list to each rank")
    common(sp)
    payload(sp)
    output(sp)
    timeout(sp)
    sp.add_argument("--root", type=int, default=0)
    sp.add_argument(
        "--expect", action="append",
        help="assert a top-level field of the received JSON, as KEY=VALUE; "
             "{rank} expands to this rank. A mismatch raises "
             "AMPI_ERR_CONTRACT rather than being silently accepted.")
    sp.set_defaults(func=cmd_scatter)

    sp = sub.add_parser("gather", help="collect one contribution per rank at a root")
    common(sp)
    payload(sp)
    output(sp)
    timeout(sp)
    sp.add_argument("--root", type=int, default=0)
    sp.set_defaults(func=cmd_gather)

    sp = sub.add_parser("allgather", help="every rank receives every contribution")
    common(sp)
    payload(sp)
    output(sp)
    timeout(sp)
    sp.set_defaults(func=cmd_allgather)

    sp = sub.add_parser("reduce", help="combine contributions at a root")
    common(sp)
    payload(sp)
    output(sp)
    timeout(sp)
    sp.add_argument("--op", default="ampi_concat")
    sp.add_argument("--root", type=int, default=0)
    sp.add_argument("--algorithm", default="auto", choices=[a.value for a in CollAlgorithm])
    sp.set_defaults(func=cmd_reduce)

    sp = sub.add_parser("allreduce", help="combine contributions, result to everyone")
    common(sp)
    payload(sp)
    output(sp)
    timeout(sp)
    sp.add_argument("--op", default="ampi_union")
    sp.add_argument("--algorithm", default="auto", choices=[a.value for a in CollAlgorithm])
    sp.set_defaults(func=cmd_allreduce)

    sp = sub.add_parser("scan", help="parallel prefix over the ranks")
    common(sp)
    payload(sp)
    output(sp)
    timeout(sp)
    sp.add_argument("--op", default="ampi_union")
    sp.add_argument("--exclusive", dest="inclusive", action="store_false",
                    help="exclude this rank's own contribution (exscan)")
    sp.set_defaults(func=cmd_scan, inclusive=True)

    sp = sub.add_parser("alltoall", help="each rank sends a distinct value to each rank")
    common(sp)
    payload(sp)
    output(sp)
    timeout(sp)
    sp.set_defaults(func=cmd_alltoall)

    sp = sub.add_parser("win", help="shared blackboard (one-sided) operations")
    common(sp)
    payload(sp)
    output(sp)
    timeout(sp)
    sp.add_argument("win_op", choices=["put", "get", "acc", "index", "query",
                                       "fetch-add", "fence"])
    sp.add_argument("--window", default="blackboard")
    sp.add_argument("--key", default="")
    sp.add_argument("--op", default="ampi_union")
    sp.add_argument("--question", default="")
    sp.add_argument("--budget", type=int, default=2000,
                    help="token budget for a materialising read or a query")
    sp.add_argument("--materialize", action="store_true",
                    help="read the content into context instead of returning a reference")
    sp.add_argument("--delta", type=float, default=1.0)
    sp.add_argument("--tag-text", default="", help="catalogue tag for this entry")
    sp.set_defaults(func=cmd_win)

    sp = sub.add_parser("file", help="collective writes to a shared artifact")
    common(sp)
    payload(sp)
    output(sp)
    timeout(sp)
    sp.add_argument("file_op", choices=["write-at-all", "append", "read"])
    sp.add_argument("--path", required=True)
    sp.add_argument("--aggregators", type=int, default=1)
    sp.add_argument("--start", type=int, default=None)
    sp.add_argument("--length", type=int, default=None)
    sp.add_argument("--unit", default="section", choices=["line", "section", "file"])
    sp.set_defaults(func=cmd_file)

    sp = sub.add_parser("progress", help="report that this rank completed a turn")
    common(sp)
    sp.add_argument("--spend", type=float, default=0.0,
                    help="currency spent during the turn")
    sp.set_defaults(func=cmd_progress)

    sp = sub.add_parser("status", help="this rank's state and its view of its peers")
    common(sp)
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("peers", help="read-only view of every rank (does not join)")
    common(sp, needs_rank=False)
    sp.set_defaults(func=cmd_peers)

    sp = sub.add_parser("checkpoint", help="save or restore this rank's state")
    common(sp)
    payload(sp)
    sp.add_argument("--restore", action="store_true")
    sp.set_defaults(func=cmd_checkpoint)

    sp = sub.add_parser("ft", help="fault-tolerance operations")
    common(sp)
    payload(sp)
    output(sp)
    timeout(sp)
    sp.add_argument("ft_op", choices=["revoke", "shrink", "agree", "detect"])
    sp.add_argument("--op", default="ampi_land")
    sp.set_defaults(func=cmd_ft)

    sp = sub.add_parser("trace", help="inspect the run's trace")
    sp.add_argument("trace_op", choices=["summary", "export"])
    sp.add_argument("--root", default=os.environ.get("AMPI_ROOT"))
    sp.add_argument("--out")
    sp.set_defaults(func=cmd_trace)

    sp = sub.add_parser("launchplan", help="emit per-rank launch instructions")
    sp.add_argument("--root", required=True)
    sp.add_argument("--program", required=True,
                    help="path to the harness program each rank runs")
    sp.add_argument("--out")
    sp.set_defaults(func=cmd_launchplan)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except AmpiError as exc:
        # Structured errors so that an agent reading stderr can branch on the
        # error class rather than on a prose message.
        payload = {"error": exc.error_class.name,
                   "code": int(exc.error_class),
                   "message": exc.message,
                   "context": {k: str(v) for k, v in exc.context.items()}}
        # The violation list is the diagnosis, not decoration: it is the
        # difference between "the payload was wrong" and "field 'rank' is 2,
        # expected 1". Dropping it leaves the reader with the useless half.
        violations = getattr(exc, "violations", ())
        if violations:
            payload["violations"] = list(violations)
        print(json.dumps(payload), file=sys.stderr)
        return 1 + int(exc.error_class) % 100
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
