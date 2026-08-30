#!/usr/bin/env python3
"""A non-agent worker that runs exactly the CLI sequence an agent runs.

This exists so that the harness can be validated -- and re-validated in CI --
without spending a single inference call.  It is the same programme, the same
commands, the same collectives, in the same order; only the "translate"
step is replaced by a deterministic transformation.

Having this is what makes the agent runs interpretable.  If a run with real
agents fails, the stub run tells us immediately whether the failure is in
the protocol or in the agents, which is otherwise the hardest thing to
distinguish in a multi-agent experiment.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AMPI = [sys.executable, "-m", "agentmpi.cli"]

#: A deterministic stand-in for a translator's terminology choice.  Each
#: worker picks from a small set, seeded by its rank, so that the baseline
#: mode genuinely disagrees across ranks and the glossary mode genuinely has
#: something to reconcile.
RENDERINGS = {
    "the Mock Turtle": ["la Simili-Tortue", "la Fausse-Tortue", "la Tortue Fantaisie"],
    "the Cheshire Cat": ["le Chat du Cheshire", "le Chat de Chester"],
    "the Knave of Hearts": ["le Valet de Coeur", "le Jack de Coeur"],
    "the March Hare": ["le Lievre de Mars", "le Lievre de Mars fou"],
    "the Mad Hatter": ["le Chapelier Fou", "le Chapelier Toque"],
    "the Duchess": ["la Duchesse"],
    "the Caterpillar": ["la Chenille", "le Ver a Soie"],
    "the White Rabbit": ["le Lapin Blanc"],
    "the Queen of Hearts": ["la Reine de Coeur", "la Reine des Coeurs"],
    "the Gryphon": ["le Griffon", "la Gryphe"],
    "the Dormouse": ["le Loir", "la Marmotte"],
    "the Caucus-race": ["la course au Caucus", "la course en comite"],
    "the Lobster Quadrille": ["le Quadrille des Homards", "la Danse du Homard"],
    "the Rabbit-Hole": ["le terrier du Lapin", "le trou du Lapin"],
    "the Pool of Tears": ["la Mare de Larmes", "l'Etang des Larmes"],
    "Wonderland": ["le Pays des Merveilles"],
}


def run(env: dict[str, str], *args: str, timeout: float = 3600) -> str:
    proc = subprocess.run(AMPI + list(args), capture_output=True, text=True,
                          env=env, cwd=str(REPO), timeout=timeout)
    if proc.returncode != 0:
        raise SystemExit(f"rank {env['AMPI_RANK']} `ampi {' '.join(args)}` failed:\n"
                         f"{proc.stdout}\n{proc.stderr}")
    return proc.stdout


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--rank", type=int, required=True)
    ap.add_argument("--mode", default="glossary",
                    choices=["glossary", "baseline", "chain"])
    ap.add_argument("--scratch", default=None)
    ap.add_argument("--think", type=float, default=0.0,
                    help="seconds of simulated thinking per turn")
    ap.add_argument("--die-after", default="",
                    help="'chunk' to exit silently after receiving the chunk")
    args = ap.parse_args()

    env = {**os.environ, "AMPI_ROOT": args.root, "AMPI_RANK": str(args.rank),
           "PYTHONPATH": str(REPO / "src")}
    scratch = Path(args.scratch or (REPO / "runs" / "scratch" / f"rank-{args.rank}"))
    scratch.mkdir(parents=True, exist_ok=True)

    rng = random.Random(args.rank * 7919)
    t0 = time.time()

    # Step 1: specification
    run(env, "bcast", "--root", "0", "--out", str(scratch / "spec.md"))

    # Step 2: chunk
    run(env, "scatter", "--root", "0", "--type", "json",
        "--out", str(scratch / "chunk.json"))
    chunk = json.loads((scratch / "chunk.json").read_text(encoding="utf-8"))

    if args.die_after == "chunk":
        # A silent death: no finalize, no error, no goodbye.  The only signal
        # the survivors get is the absence of further evidence.
        print(json.dumps({"rank": args.rank, "died": "after_chunk"}))
        os._exit(0)

    if chunk is None:
        run(env, "gather", "--root", "0", "--json", "null", "--type", "json")
        return 0

    text = chunk.get("text", "")
    present = [term for term in RENDERINGS
               if term.lower().removeprefix("the ") in text.lower()]

    glossary: dict[str, str] = {}
    from_prefix = 0
    if args.mode != "baseline":
        proposal = {t: [rng.choice(RENDERINGS[t])] for t in present}
        (scratch / "proposal.json").write_text(json.dumps(proposal, ensure_ascii=False))
        # Step 4: exclusive parallel prefix over the preceding chunks
        run(env, "scan", "--exclusive", "--op", "ampi_first",
            "--file", str(scratch / "proposal.json"), "--type", "json",
            "--out", str(scratch / "prefix.json"))
        prefix = json.loads((scratch / "prefix.json").read_text() or "{}") or {}
        for term in present:
            if term in prefix and prefix[term]:
                glossary[term] = prefix[term][0] if isinstance(prefix[term], list) else prefix[term]
                from_prefix += 1
            else:
                glossary[term] = proposal[term][0]
    else:
        glossary = {t: rng.choice(RENDERINGS[t]) for t in present}

    if args.think:
        time.sleep(args.think * rng.lognormvariate(0.0, 0.4))

    translation = _stub_translate(text, glossary)
    result = {"chunk_id": chunk["id"], "glossary": glossary, "translation": translation}
    (scratch / "result.json").write_text(json.dumps(result, ensure_ascii=False),
                                         encoding="utf-8")

    run(env, "gather", "--root", "0", "--file", str(scratch / "result.json"),
        "--type", "json")
    run(env, "progress")

    print(json.dumps({"rank": args.rank, "chunk": chunk["id"],
                      "terms": len(glossary), "from_prefix": from_prefix,
                      "chars": len(translation),
                      "wall_s": round(time.time() - t0, 2)}))
    return 0


def _stub_translate(text: str, glossary: dict[str, str]) -> str:
    """Substitute the glossary into the source; a placeholder for translation.

    The point is only that the declared glossary genuinely appears in the
    produced text, so the consistency metric measures the same thing for stub
    workers and for agents.
    """
    out = text
    for term, rendering in glossary.items():
        for variant in (term, term.replace("the ", "The "),
                        term.removeprefix("the ")):
            out = out.replace(variant, rendering)
    return f"[FR] {out}"


if __name__ == "__main__":
    raise SystemExit(main())
