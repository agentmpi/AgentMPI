from __future__ import annotations

import argparse
from kvstore.store import KVStore

_STORE = KVStore()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kv")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("get"); g.add_argument("key")
    u = sub.add_parser("put"); u.add_argument("key"); u.add_argument("value")
    d = sub.add_parser("delete"); d.add_argument("key")
    sub.add_parser("keys")
    args = p.parse_args(argv)
    if args.cmd == "get":
        print(_STORE.get(args.key) or "")
    elif args.cmd == "put":
        print(_STORE.put(args.key, args.value))
    elif args.cmd == "delete":
        print("ok" if _STORE.delete(args.key) else "missing")
    elif args.cmd == "keys":
        print("\n".join(_STORE.keys()))
    return 0
