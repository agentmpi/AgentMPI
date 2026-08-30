#!/usr/bin/env python3
"""Embarrassingly-parallel book translation harness written against AgentMPI.

This is the agent analog of a data-parallel MPI map: rank 0 scatters fable
shards, every rank translates its shard, results are gathered, and a
hierarchical SYNTHESIZE reduce builds a table of contents. Process mode
uses a deterministic stand-in translator so the protocol path is measurable.
Cursor-agent mode leaves a work file for an LLM executor.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentmpi.types import Op
from experiments.common import run_spmd, write_result
from experiments.data.build_corpus import shard as shard_fables


def mock_translate(fable: dict, target: str) -> dict:
    """Deterministic stand-in: Spanish-flavored register, not a real MT system.

    Used to exercise scatter/gather/reduce without an LLM. Cursor subagents
    replace this function with an actual translation.
    """
    text = fable["text"]
    # A reversible, checkable transform plus a target-language wrapper.
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    rendered = " ".join(lines)
    return {
        "title": fable["title"],
        "target": target,
        "translation": f"[{target}] {rendered}",
        "n_chars": len(rendered),
        "n_lines": len(lines),
    }


def make_fn(shards: list[dict], target: str, mode: str):
    def fn(comm):
        mine = comm.scatter(shards if comm.rank == 0 else None, root=0, timeout_s=60)
        translated = []
        for fable in mine.get("fables", []):
            if mode == "agent":
                work = comm.home / "work" / f"rank{comm.rank}-{fable['title'][:40].replace(' ', '_')}.json"
                work.parent.mkdir(parents=True, exist_ok=True)
                work.write_text(json.dumps({"fable": fable, "target": target}, ensure_ascii=False, indent=2))
                reply = work.with_suffix(".out.json")
                # Agent executors write the reply; process mode falls through.
                if reply.exists():
                    translated.append(json.loads(reply.read_text()))
                    continue
            translated.append(mock_translate(fable, target))
        gathered = comm.gather(translated, root=0, timeout_s=60)
        toc = comm.reduce(
            {"rank": comm.rank, "count": len(translated), "titles": [t["title"] for t in translated]},
            op=Op.MERGE,
            root=0,
            timeout_s=60,
        )
        if comm.rank == 0:
            book = []
            for part in gathered or []:
                book.extend(part)
            return {"book": book, "toc": toc, "n_fables": len(book)}
        return {"n_local": len(translated)}

    return fn


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", type=int, default=16)
    parser.add_argument("--target", default="es")
    parser.add_argument("--mode", choices=("process", "agent"), default="process")
    parser.add_argument("--limit", type=int, default=64, help="max fables to translate")
    parser.add_argument("--out", default="experiments/results/translation.json")
    args = parser.parse_args()
    corpus = json.loads((ROOT / "experiments/data/aesop_fables.json").read_text())[: args.limit]
    shards = shard_fables(corpus, args.n)
    home = ROOT / "experiments/results/.ampi" / f"translate-{args.n}"
    results, summary = run_spmd(home, args.n, make_fn(shards, args.target, args.mode))
    book = results[0]["book"]
    payload = {
        "experiment": "translation",
        "n": args.n,
        "target": args.target,
        "mode": args.mode,
        "fables": len(book),
        "sample": book[:3],
        "toc": results[0]["toc"],
        **summary,
    }
    write_result(Path(args.out), payload)
    print(f"translated {len(book)} fables on {args.n} ranks in {summary['elapsed_s']:.3f}s  sends={summary['sends']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
