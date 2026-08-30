#!/usr/bin/env python3
"""Rank 0 of the translation experiment.

Rank 0 runs a deterministic program.  That is not a concession, it is the
point: in MPI, rank 0 is an ordinary rank whose program happens to be the
one that owns the input and the output, and nothing about the protocol
distinguishes it.  Here the same holds -- rank 0 issues exactly the same
collectives as every other rank, in the same order, and contains no model
call at all.  Every model call in this experiment happens in ranks 1..p-1,
which are agents.

Keeping the coordinator deterministic also means the experiment measures the
protocol and the agents, not the coordinator's luck.

Program (identical shape on every rank):

    bcast   root=0    the task specification
    scatter root=0    one chunk per rank
    exscan  UNION     the terminology fixed by all preceding chunks
    gather  root=0    the translations

The exclusive scan is the interesting one.  Chunk *i* must render the
recurring proper nouns the way chunks 0..i-1 rendered them, or the book
reads as though twelve people translated it -- which they did.  Threading a
glossary sequentially would cost p-1 rounds and destroy the parallelism;
``AMPI_Exscan`` delivers the same prefix in ceil(log2 p) rounds.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import agentmpi as ampi

HERE = Path(__file__).resolve().parent


SPEC_TEMPLATE = """\
# Translation task

Translate your assigned chunk of *Alice's Adventures in Wonderland* by Lewis
Carroll into **{language}**.

## Requirements

1. Translate the prose faithfully and idiomatically. Keep paragraph breaks.
   Do not summarise, do not omit, do not add commentary.
2. Keep the chapter heading, translated.
3. Verse stays verse.
4. **Terminology.** The recurring names below must be rendered the same way
   in every chunk of the book. You will be told which renderings earlier
   chunks already committed to; adopt those exactly. Only choose a rendering
   yourself for a name that has not been fixed yet.

## Names that must be consistent across the whole book

{terms}

## Output contract

Your result must be a single JSON object with exactly these keys:

- `chunk_id`   (string) the id you were given
- `glossary`   (object) every name from the list above that occurs in *your*
               chunk, mapped to the exact {language} rendering you used
- `translation` (string) the full {language} translation of your chunk

Nothing else. No markdown fence around the JSON.
"""


def build_spec(corpus: dict) -> str:
    terms = "\n".join(f"- {t}" for t in corpus["pivot_terms"])
    return SPEC_TEMPLATE.format(language=corpus["target_language"], terms=terms)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="AgentMPI run directory")
    ap.add_argument("--corpus", default=str(HERE.parent / "data" / "corpus.json"))
    ap.add_argument("--out", required=True, help="where to write results")
    ap.add_argument("--mode", default="glossary",
                    choices=["glossary", "baseline", "chain"],
                    help="glossary = parallel-prefix scan; chain = sequential "
                         "propagation; baseline = no terminology coordination")
    ap.add_argument("--timeout", type=float, default=3600.0)
    args = ap.parse_args()

    corpus = json.loads(Path(args.corpus).read_text(encoding="utf-8"))
    rt = ampi.init(root=args.root, rank=0, device="journal")
    rt.start_heartbeat()
    comm = rt.world
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    marks: dict[str, float] = {}

    def mark(name: str) -> None:
        marks[name] = round(time.time() - t0, 2)

    # --- phase 1: broadcast the specification -----------------------------
    spec = build_spec(corpus)
    comm.bcast(spec, root=0, datatype="text", timeout=args.timeout)
    mark("bcast_done")

    # --- phase 2: scatter the work ----------------------------------------
    # Rank 0 takes no chunk; ranks 1..p-1 each take one.  Chunks are ordered
    # by their position in the book so that the exclusive scan's "preceding
    # ranks" really are the preceding chapters.
    chunks = corpus["chunks"]
    workers = comm.size - 1
    assignment: list = [None]
    for i in range(workers):
        assignment.append(chunks[i] if i < len(chunks) else None)
    comm.scatterv(assignment, root=0, datatype="json", timeout=args.timeout)
    mark("scatter_done")

    # --- phase 3: terminology prefix --------------------------------------
    if args.mode != "baseline":
        algorithm = "chain" if args.mode == "chain" else "recursive_doubling"
        comm.exscan({}, ampi.UNION, datatype="json",
                    algorithm=algorithm, timeout=args.timeout)
        mark("exscan_done")

    # --- phase 4: collect -------------------------------------------------
    gathered = comm.gather(None, root=0, datatype="json", timeout=args.timeout)
    mark("gather_done")

    results = []
    for rank, payload in enumerate(gathered or []):
        if rank == 0 or payload is None:
            continue
        results.append({"rank": rank, **(payload if isinstance(payload, dict)
                                         else {"raw": payload})})
    (out / "translations.json").write_text(
        json.dumps({"mode": args.mode, "ranks": comm.size, "workers": workers,
                    "results": results, "marks": marks,
                    "wall_s": round(time.time() - t0, 2)},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "spec.md").write_text(spec, encoding="utf-8")

    summary = {
        "mode": args.mode,
        "ranks": comm.size,
        "workers": workers,
        "returned": len(results),
        "wall_s": round(time.time() - t0, 2),
        "marks": marks,
        "pvars": rt.pvars.snapshot(),
    }
    (out / "coordinator.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    ampi.finalize()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
