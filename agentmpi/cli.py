"""Language-neutral CLI binding.

Cursor subagents and non-Python harnesses speak AgentMPI through this CLI.
Each invocation is one protocol call against AMPI_HOME, matching how
`mpiexec` programs historically wrapped vendor MPI libraries.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from agentmpi.comm import Communicator
from agentmpi.constants import ANY_SOURCE, ANY_TAG, COMM_WORLD_NAME
from agentmpi.runtime import ENV_COMM, ENV_HOME, ENV_RANK, ENV_SIZE
from agentmpi.types import Lifecycle, Op
from agentmpi.util import atomic_write_json, read_json


def _comm(ns: argparse.Namespace) -> Communicator:
    home = ns.home or os.environ.get(ENV_HOME)
    rank = ns.rank if ns.rank is not None else int(os.environ[ENV_RANK])
    size = ns.size if ns.size is not None else int(os.environ[ENV_SIZE])
    name = ns.comm or os.environ.get(ENV_COMM, COMM_WORLD_NAME)
    if not home:
        raise SystemExit("AMPI_HOME / --home is required")
    return Communicator(home, rank=rank, size=size, name=name, role=ns.role)


def _load(ns: argparse.Namespace):
    if getattr(ns, "file", None):
        return read_json(Path(ns.file))
    if getattr(ns, "payload", None) is not None:
        return json.loads(ns.payload)
    if not sys.stdin.isatty():
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else None
    return None


def _dump(obj, out: str | None) -> None:
    text = json.dumps(obj, indent=2, default=str, ensure_ascii=False)
    if out:
        atomic_write_json(Path(out), obj)
    else:
        print(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentmpi", description="AgentMPI command-line binding")
    parser.add_argument("--home", default=None)
    parser.add_argument("--rank", type=int, default=None)
    parser.add_argument("--size", type=int, default=None)
    parser.add_argument("--comm", default=None)
    parser.add_argument("--role", default="worker")
    parser.add_argument("--timeout", type=float, default=60.0)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init")
    p.add_argument("--bootstrap", action="store_true")

    sub.add_parser("finalize")
    p = sub.add_parser("heartbeat")
    p.add_argument("--state", default="active")
    p.add_argument("--note", default="")

    p = sub.add_parser("send")
    p.add_argument("--dest", type=int, required=True)
    p.add_argument("--tag", type=int, default=0)
    p.add_argument("--file", default=None)
    p.add_argument("--payload", default=None)

    p = sub.add_parser("recv")
    p.add_argument("--source", type=int, default=ANY_SOURCE)
    p.add_argument("--tag", type=int, default=ANY_TAG)
    p.add_argument("--out", default=None)

    p = sub.add_parser("probe")
    p.add_argument("--source", type=int, default=ANY_SOURCE)
    p.add_argument("--tag", type=int, default=ANY_TAG)

    p = sub.add_parser("bcast")
    p.add_argument("--root", type=int, default=0)
    p.add_argument("--file", default=None)
    p.add_argument("--payload", default=None)
    p.add_argument("--out", default=None)

    sub.add_parser("barrier")

    p = sub.add_parser("scatter")
    p.add_argument("--root", type=int, default=0)
    p.add_argument("--file", default=None)
    p.add_argument("--out", default=None)

    p = sub.add_parser("gather")
    p.add_argument("--root", type=int, default=0)
    p.add_argument("--file", default=None)
    p.add_argument("--payload", default=None)
    p.add_argument("--out", default=None)

    p = sub.add_parser("reduce")
    p.add_argument("--root", type=int, default=0)
    p.add_argument("--op", default="sum")
    p.add_argument("--file", default=None)
    p.add_argument("--payload", default=None)
    p.add_argument("--out", default=None)

    p = sub.add_parser("allreduce")
    p.add_argument("--op", default="sum")
    p.add_argument("--file", default=None)
    p.add_argument("--payload", default=None)
    p.add_argument("--out", default=None)

    p = sub.add_parser("allgather")
    p.add_argument("--file", default=None)
    p.add_argument("--payload", default=None)
    p.add_argument("--out", default=None)

    p = sub.add_parser("lock")
    p.add_argument("--window", required=True)
    p.add_argument("--exclusive", action="store_true")

    p = sub.add_parser("unlock")
    p.add_argument("--window", required=True)
    p.add_argument("--exclusive", action="store_true")

    p = sub.add_parser("put")
    p.add_argument("--window", required=True)
    p.add_argument("--file", default=None)
    p.add_argument("--payload", default=None)

    p = sub.add_parser("get")
    p.add_argument("--window", required=True)
    p.add_argument("--out", default=None)

    p = sub.add_parser("context-put")
    p.add_argument("--file", default=None)
    p.add_argument("--payload", default=None)

    p = sub.add_parser("context-get")
    p.add_argument("--out", default=None)

    p = sub.add_parser("context-compact")
    p.add_argument("--file", default=None)
    p.add_argument("--payload", default=None)

    p = sub.add_parser("win-create")
    p.add_argument("--window", required=True)
    p.add_argument("--payload", default=None)

    sub.add_parser("failures")
    sub.add_parser("revoke")
    p = sub.add_parser("agree")
    p.add_argument("--payload", required=True)
    p.add_argument("--out", default=None)
    sub.add_parser("shrink")
    p = sub.add_parser("split")
    p.add_argument("--color", type=int, required=True)
    p.add_argument("--key", type=int, default=None)

    p = sub.add_parser("status")
    args = parser.parse_args(argv)
    comm = _comm(args)

    if args.cmd == "init":
        if args.bootstrap:
            comm.transport.bootstrap(comm.size)
        comm.heartbeat(Lifecycle.ACTIVE)
        _dump({"rank": comm.rank, "size": comm.size, "home": str(comm.home)}, None)
    elif args.cmd == "finalize":
        comm.finalize()
    elif args.cmd == "heartbeat":
        comm.heartbeat(Lifecycle(args.state), note=args.note)
    elif args.cmd == "send":
        comm.send(_load(args), dest=args.dest, tag=args.tag)
    elif args.cmd == "recv":
        _dump(comm.recv(source=args.source, tag=args.tag, timeout_s=args.timeout), args.out)
    elif args.cmd == "probe":
        env = comm.irecv_probe(source=args.source, tag=args.tag)
        _dump(env.to_dict() if env else None, None)
    elif args.cmd == "bcast":
        obj = _load(args) if comm.rank == getattr(args, "root", 0) else None
        _dump(comm.bcast(obj, root=args.root, timeout_s=args.timeout), args.out)
    elif args.cmd == "barrier":
        comm.barrier(timeout_s=args.timeout)
    elif args.cmd == "scatter":
        buf = _load(args) if comm.rank == args.root else None
        _dump(comm.scatter(buf, root=args.root, timeout_s=args.timeout), args.out)
    elif args.cmd == "gather":
        _dump(comm.gather(_load(args), root=args.root, timeout_s=args.timeout), args.out)
    elif args.cmd == "reduce":
        _dump(comm.reduce(_load(args), op=Op(args.op), root=args.root, timeout_s=args.timeout), args.out)
    elif args.cmd == "allreduce":
        _dump(comm.allreduce(_load(args), op=Op(args.op), timeout_s=args.timeout), args.out)
    elif args.cmd == "allgather":
        _dump(comm.allgather(_load(args), timeout_s=args.timeout), args.out)
    elif args.cmd == "lock":
        kind = "exclusive" if args.exclusive else "shared"
        ok = comm.win_lock(args.window, kind, timeout_s=args.timeout)
        _dump({"locked": ok}, None)
        return 0 if ok else 2
    elif args.cmd == "unlock":
        comm.win_unlock(args.window, "exclusive" if args.exclusive else "shared")
    elif args.cmd == "put":
        comm.put(args.window, _load(args))
    elif args.cmd == "get":
        _dump(comm.get(args.window), args.out)
    elif args.cmd == "context-put":
        comm.win_create("context", [])
        comm.context_put(_load(args))
    elif args.cmd == "context-get":
        _dump(comm.context_get(), args.out)
    elif args.cmd == "context-compact":
        _dump(comm.context_compact(_load(args)), None)
    elif args.cmd == "win-create":
        initial = json.loads(args.payload) if args.payload else None
        comm.win_create(args.window, initial)
    elif args.cmd == "failures":
        _dump(comm.probe_failures(), None)
    elif args.cmd == "revoke":
        comm.revoke()
    elif args.cmd == "agree":
        _dump(comm.agree(json.loads(args.payload), timeout_s=args.timeout), args.out)
    elif args.cmd == "shrink":
        new = comm.shrink()
        _dump({"name": new.name, "rank": new.rank, "size": new.size}, None)
    elif args.cmd == "split":
        new = comm.comm_split(args.color, args.key)
        _dump({"name": new.name, "rank": new.rank, "size": new.size}, None)
    elif args.cmd == "status":
        st = comm.transport.read_status(comm.rank)
        _dump(st.to_dict() if st else None, None)
    else:
        raise SystemExit(f"unknown command {args.cmd}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
