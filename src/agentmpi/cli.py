"""Command-line interface for humans, scripts, and autonomous agent executors."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .model import ANY_SOURCE, ANY_TAG, DeliveryMode, ReduceOp
from .runtime import Runtime


def _json_value(raw: str | None, path: str | None) -> Any:
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    if raw is None:
        return None
    return json.loads(raw)


def _runtime(args: argparse.Namespace) -> Runtime:
    return Runtime(args.db, args.session, args.rank)


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentmpi",
        description="Durable message passing for independent AI agent executors.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="initialize a session")
    init.add_argument("--db", required=True)
    init.add_argument("--session", default="default")
    init.add_argument("--size", required=True, type=int)
    init.add_argument("--context-budget", type=int, default=32_000)
    init.add_argument("--mailbox-bytes", type=int, default=8 * 1024 * 1024)
    init.add_argument("--inline-token-limit", type=int, default=2_048)
    init.add_argument("--heartbeat-ttl", type=float, default=30.0)

    for name in (
        "join",
        "heartbeat",
        "finalize",
        "send",
        "recv",
        "probe",
        "barrier",
        "bcast",
        "scatter",
        "gather",
        "allgather",
        "reduce",
        "allreduce",
        "agree",
        "revoke",
        "shrink",
        "fail",
        "lock",
        "unlock",
        "status",
        "trace",
        "compact",
    ):
        command = sub.add_parser(name)
        command.add_argument("--db", required=True)
        command.add_argument("--session", default="default")
        command.add_argument("--rank", type=int, default=0)

    sub.choices["join"].add_argument("--context-budget", type=int)

    send = sub.choices["send"]
    send.add_argument("--dest", required=True, type=int)
    send.add_argument("--tag", default="default")
    send.add_argument("--json")
    send.add_argument("--json-file")
    send.add_argument(
        "--mode",
        choices=[mode.value for mode in DeliveryMode],
        default=DeliveryMode.STANDARD.value,
    )
    send.add_argument("--timeout", type=float)

    for name in ("recv", "probe"):
        receive = sub.choices[name]
        receive.add_argument("--source", type=int, default=ANY_SOURCE)
        receive.add_argument("--tag", default=ANY_TAG)
    sub.choices["recv"].add_argument("--timeout", type=float)
    sub.choices["recv"].add_argument("--no-context-charge", action="store_true")

    collective_names = (
        "barrier",
        "bcast",
        "scatter",
        "gather",
        "allgather",
        "reduce",
        "allreduce",
        "agree",
    )
    for name in collective_names:
        sub.choices[name].add_argument("--timeout", type=float, default=120.0)

    for name in ("bcast", "scatter", "gather", "allgather", "reduce", "allreduce", "agree"):
        sub.choices[name].add_argument("--json")
        sub.choices[name].add_argument("--json-file")

    for name in ("bcast", "scatter", "gather", "reduce"):
        sub.choices[name].add_argument("--root", type=int, default=0)

    for name in ("reduce", "allreduce"):
        sub.choices[name].add_argument(
            "--op",
            choices=[op.value for op in ReduceOp],
            default=ReduceOp.SUM.value,
        )

    sub.choices["fail"].add_argument("--target", required=True, type=int)
    sub.choices["fail"].add_argument("--reason", default="injected")
    sub.choices["lock"].add_argument("--name", required=True)
    sub.choices["lock"].add_argument("--lease-seconds", type=float, default=30.0)
    sub.choices["unlock"].add_argument("--name", required=True)
    sub.choices["unlock"].add_argument("--token", required=True, type=int)
    sub.choices["compact"].add_argument("--used", type=int, default=0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "init":
        Runtime.initialize(
            args.db,
            size=args.size,
            session_id=args.session,
            context_budget=args.context_budget,
            mailbox_bytes=args.mailbox_bytes,
            inline_token_limit=args.inline_token_limit,
            heartbeat_ttl=args.heartbeat_ttl,
        )
        _print({"db": args.db, "session": args.session, "size": args.size})
        return 0
    if args.command == "join":
        runtime = Runtime.attach(
            args.db,
            args.session,
            args.rank,
            context_budget=args.context_budget,
        )
        runtime.close()
        _print({"rank": args.rank, "state": "active"})
        return 0

    runtime = _runtime(args)
    try:
        if args.command == "heartbeat":
            runtime.heartbeat()
            _print({"rank": args.rank, "heartbeat": "ok"})
        elif args.command == "finalize":
            runtime.finalize()
            _print({"rank": args.rank, "state": "finalized"})
        elif args.command == "send":
            status = runtime.send(
                _json_value(args.json, args.json_file),
                args.dest,
                tag=args.tag,
                mode=DeliveryMode(args.mode),
                timeout=args.timeout,
            )
            _print({"status": status.__dict__})
        elif args.command == "recv":
            received = runtime.recv(
                source=args.source,
                tag=args.tag,
                timeout=args.timeout,
                charge_context=not args.no_context_charge,
            )
            _print(
                {
                    "payload": received.payload,
                    "status": received.status.__dict__,
                }
            )
        elif args.command == "probe":
            _print({"status": runtime.probe(source=args.source, tag=args.tag).__dict__})
        elif args.command == "barrier":
            runtime.barrier(timeout=args.timeout)
            _print({"barrier": "complete"})
        elif args.command == "bcast":
            _print(
                {
                    "value": runtime.bcast(
                        _json_value(args.json, args.json_file),
                        root=args.root,
                        timeout=args.timeout,
                    )
                }
            )
        elif args.command == "scatter":
            _print(
                {
                    "value": runtime.scatter(
                        _json_value(args.json, args.json_file),
                        root=args.root,
                        timeout=args.timeout,
                    )
                }
            )
        elif args.command == "gather":
            _print(
                {
                    "value": runtime.gather(
                        _json_value(args.json, args.json_file),
                        root=args.root,
                        timeout=args.timeout,
                    )
                }
            )
        elif args.command == "allgather":
            _print(
                {
                    "value": runtime.allgather(
                        _json_value(args.json, args.json_file),
                        timeout=args.timeout,
                    )
                }
            )
        elif args.command == "reduce":
            _print(
                {
                    "value": runtime.reduce(
                        _json_value(args.json, args.json_file),
                        op=ReduceOp(args.op),
                        root=args.root,
                        timeout=args.timeout,
                    )
                }
            )
        elif args.command == "allreduce":
            _print(
                {
                    "value": runtime.allreduce(
                        _json_value(args.json, args.json_file),
                        op=ReduceOp(args.op),
                        timeout=args.timeout,
                    )
                }
            )
        elif args.command == "agree":
            _print(
                {
                    "value": runtime.agree(
                        bool(_json_value(args.json, args.json_file)),
                        timeout=args.timeout,
                    )
                }
            )
        elif args.command == "revoke":
            runtime.revoke()
            _print({"communicator": runtime.world.id, "revoked": True})
        elif args.command == "shrink":
            _print({"communicator": runtime.shrink().__dict__})
        elif args.command == "fail":
            runtime.fail_rank(args.target, reason=args.reason)
            _print({"rank": args.target, "state": "failed"})
        elif args.command == "lock":
            token = runtime.acquire_lock(args.name, lease_seconds=args.lease_seconds)
            _print({"name": args.name, "fencing_token": token})
        elif args.command == "unlock":
            runtime.release_lock(args.name, args.token)
            _print({"name": args.name, "released": True})
        elif args.command == "status":
            _print(
                {
                    "agents": [
                        {
                            **info.__dict__,
                            "state": info.state.value,
                        }
                        for info in runtime.agent_info()
                    ],
                    "communicator": runtime.world.__dict__,
                }
            )
        elif args.command == "trace":
            _print({"events": runtime.trace()})
        elif args.command == "compact":
            runtime.reset_context(used=args.used)
            _print({"rank": args.rank, "context_used": args.used})
        else:
            raise AssertionError(f"unhandled command: {args.command}")
        return 0
    finally:
        runtime.close()


if __name__ == "__main__":
    sys.exit(main())
