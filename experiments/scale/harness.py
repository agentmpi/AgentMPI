#!/usr/bin/env python3
"""100-rank scale study.

Every rank receives one (or a few) Aesop fables via scatter, produces a
structured analysis (length, moral line, keyword vote), then participates
in allreduce / allgather / gather. This is the weak-scaling analog of an
MPI map-reduce over a corpus, and is the job we also dispatch to Cursor
subagents (one executor per rank).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentmpi.types import Op
from experiments.common import run_spmd, write_result
from experiments.data.build_corpus import shard as shard_fables

WORD = re.compile(r"[A-Za-zÆæ']+")


def analyze(fable: dict) -> dict:
    words = [w.lower() for w in WORD.findall(fable["text"])]
    moral = ""
    for ln in fable["text"].splitlines():
        if ln.startswith("    ") and len(ln.strip()) > 20:
            moral = ln.strip()
            break
    return {
        "title": fable["title"],
        "n_words": len(words),
        "n_chars": len(fable["text"]),
        "moral": moral,
        "top": Counter(words).most_common(3),
    }


def make_fn(shards: list[dict]):
    def fn(comm):
        mine = comm.scatter(shards if comm.rank == 0 else None, root=0, timeout_s=120)
        reports = [analyze(f) for f in mine.get("fables", [])]
        local_words = sum(r["n_words"] for r in reports)
        total_words = comm.allreduce(local_words, op=Op.SUM, timeout_s=120)
        gathered = comm.gather(reports, root=0, timeout_s=120)
        if comm.rank == 0:
            flat = [r for part in (gathered or []) for r in part]
            return {"n_reports": len(flat), "total_words": total_words, "sample": flat[:5]}
        return {"local_words": local_words, "total_words": total_words}

    return fn


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", type=int, default=100)
    parser.add_argument("--out", default="experiments/results/scale.json")
    args = parser.parse_args()
    corpus = json.loads((ROOT / "experiments/data/aesop_fables.json").read_text())
    # Use as many fables as we have; 100 ranks get ~2-3 each from 285.
    shards = shard_fables(corpus, args.n)
    home = ROOT / "experiments/results/.ampi" / f"scale-{args.n}"
    results, summary = run_spmd(home, args.n, make_fn(shards), context_budget=500_000)
    payload = {
        "experiment": "scale",
        "n": args.n,
        "root": results[0],
        "agreement": len({r["total_words"] for r in results if isinstance(r, dict) and "total_words" in r}) == 1,
        **summary,
    }
    write_result(Path(args.out), payload)
    print(f"scale n={args.n} reports={results[0]['n_reports']} words={results[0]['total_words']} elapsed={summary['elapsed_s']:.3f}s sends={summary['sends']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
