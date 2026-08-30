"""Collect E2 from the job traces.

Everything reported here is recomputed from the PAMPI event log and the
protocol tables; nothing is taken from what an agent said it did.  That
distinction matters in an agent experiment, because an agent's self-report is
itself model output and can be wrong.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))

from ampi import util  # noqa: E402
from ampi.analysis import summarise  # noqa: E402
from ampi.device import SqliteDevice  # noqa: E402


def _device(job_dir: str) -> tuple[SqliteDevice, str]:
    dev = SqliteDevice(os.path.join(job_dir, "job.db"))
    dev.initialize()
    return dev, os.path.basename(job_dir.rstrip("/"))


def collect_one(job_dir: str, algo: str) -> dict:
    dev, job_id = _device(job_dir)
    try:
        upcalls = dev.query(
            "SELECT assignee, step, state, created_at, settled_at, operands, result "
            "FROM pending_op WHERE job_id=? ORDER BY created_at", (job_id,))
        done = [u for u in upcalls if u["state"] == "done"]
        by_rank: dict[int, int] = {}
        for u in done:
            by_rank[int(u["assignee"])] = by_rank.get(int(u["assignee"]), 0) + 1

        coll = dev.query_one(
            "SELECT * FROM coll WHERE job_id=? AND op='allreduce' ORDER BY created_at LIMIT 1",
            (job_id,))
        contribs = dev.query(
            "SELECT rank, body, arrived_at FROM coll_contrib WHERE coll_id=? ORDER BY rank",
            (coll["coll_id"],)) if coll else []

        ranks = dev.query("SELECT * FROM rank WHERE job_id=? ORDER BY rank", (job_id,))
        joined = [r for r in ranks if r["started_at"]]
        started = min([r["started_at"] for r in joined], default=None)
        last_event = dev.query_one(
            "SELECT MAX(ts) AS t FROM event WHERE job_id=?", (job_id,))
        wall = (last_event["t"] - started) if (started and last_event["t"]) else None

        # Turnaround of each operator evaluation: the model's share of the
        # critical path, as distinct from the protocol's.
        turnarounds = [round(u["settled_at"] - u["created_at"], 2) for u in done
                       if u["settled_at"] and u["created_at"]]
        critical = max(by_rank.values()) if by_rank else 0

        detections = dev.query(
            "SELECT rank, detected_at, reason FROM failure WHERE job_id=?", (job_id,))
        retractions = dev.query(
            "SELECT rank, ts FROM event WHERE job_id=? AND op='AMPI_Failure_retracted'",
            (job_id,))
        # A condemnation that is later withdrawn is an oscillation, not a
        # failure; reporting the raw count would badly overstate how unstable
        # the job was, and reporting only distinct ranks would hide the
        # detector pathology entirely.
        condemned_ranks = sorted({int(d["rank"]) for d in detections})
        permanently_failed = sorted({int(r["rank"]) for r in ranks
                                     if r["state"] == "failed"})

        final = None
        for u in reversed(done):
            if u["result"]:
                final = util.loads(u["result"], None)
                break

        messages = dev.query_one(
            "SELECT COUNT(*) AS n, SUM(tokens) AS tok FROM message WHERE job_id=?", (job_id,))
        retention_stats = retention(dev, job_id, final)
        conformance_stats = conformance(dev, job_dir, job_id)

        return {
            "retention": retention_stats,
            "conformance": conformance_stats,
            "algo": algo,
            "p": len(ranks),
            "joined": len(joined),
            "upcalls_total": len(done),
            "upcalls_pending": len(upcalls) - len(done),
            "upcalls_by_rank": {str(k): v for k, v in sorted(by_rank.items())},
            "upcalls_critical_path": critical,
            "operator_turnaround_s": {
                "n": len(turnarounds),
                "median": sorted(turnarounds)[len(turnarounds) // 2] if turnarounds else None,
                "total": round(sum(turnarounds), 1) if turnarounds else None,
                "values": turnarounds,
            },
            "wall_seconds": round(wall, 1) if wall else None,
            "messages": messages["n"],
            "tokens_moved": messages["tok"],
            "collective_state": coll["state"] if coll else None,
            "ranks_reaching_result": len(contribs),
            "suspicion_events": len(detections),
            "retraction_events": len(retractions),
            "ranks_ever_condemned": condemned_ranks,
            "ranks_condemned_at_end": permanently_failed,
            "spurious_condemnations": len(detections) - len(permanently_failed),
            "oscillations_per_rank": (round(len(detections) / max(1, len(condemned_ranks)), 1)),
            "final_result": final,
            "final_tokens": util.count_tokens(util.dumps(final)) if final else 0,
        }
    finally:
        dev.close()


# One distinctive, objectively checkable string from each rank's seeded style
# guide.  These let us separate two very different questions: did the protocol
# deliver every contribution to the operator, and did the operator keep it?
RETENTION_PROBES: dict[int, tuple[str, list[str]]] = {
    0: ("1. Introduction", ["acronym"]),
    1: ("2. Architecture", ["Speicher", "storage"]),
    2: ("3. The scheduler", ["pseudocode"]),
    3: ("4. Memory management", ["page fault"]),
    4: ("5. The filesystem", ["Verzeichnis", "directory"]),
    5: ("6. Networking", ["small caps", "RFC"]),
    6: ("7. Security", ["capability", "block quote"]),
    7: ("8. Evaluation", ["significant figures"]),
}


def conformance(dev: SqliteDevice, job_dir: str, job_id: str) -> dict:
    """Did each rank actually contribute the input it was assigned?

    An agent is an unreliable interpreter of its own instructions, and this is
    the cheapest place to see it: every rank was handed a file and told to pass
    that file to the collective, so the payload the library recorded can be
    compared against the file byte for byte.  A mismatch is not a transport
    fault --- the protocol delivered exactly what it was given --- but it bounds
    what any guarantee above the transport can be worth, and it is the number
    a harness author most needs to know.
    """
    assigned: dict[int, str] = {}
    for rank in range(64):
        path = os.path.join(job_dir, "ranks", str(rank), "notes.json")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            assigned[rank] = json.load(fh).get("chapter", "")

    sent: dict[int, str] = {}
    for row in dev.query(
        "SELECT src, body FROM message WHERE job_id=? AND body IS NOT NULL ORDER BY msg_id",
        (job_id,),
    ):
        rank = int(row["src"])
        if rank in sent:
            continue  # the first contribution is the one the rank was asked for
        payload = util.loads(row["body"], {})
        value = payload.get("v") if isinstance(payload, dict) else None
        if isinstance(value, dict) and "chapter" in value:
            sent[rank] = value["chapter"]

    # A rank that evaluates the operator contributes locally rather than by
    # sending, so its own value appears as the first operand of its first
    # upcall.  Without this the root always looks silent.
    for row in dev.query(
        "SELECT assignee, operands FROM pending_op WHERE job_id=? ORDER BY created_at",
        (job_id,),
    ):
        rank = int(row["assignee"])
        if rank in sent:
            continue
        operands = util.loads(row["operands"], [])
        if operands and isinstance(operands[0], dict) and "chapter" in operands[0]:
            sent[rank] = operands[0]["chapter"]

    matches, mismatches, silent = [], [], []
    for rank, expected in sorted(assigned.items()):
        if rank not in sent:
            silent.append(rank)
        elif sent[rank].strip() == expected.strip():
            matches.append(rank)
        else:
            mismatches.append({"rank": rank, "assigned": expected, "sent": sent[rank]})
    contributing = len(assigned) - len(silent)
    return {
        "ranks_assigned_an_input": len(assigned),
        "ranks_that_contributed": contributing,
        "conforming": matches,
        "mismatched": mismatches,
        "never_contributed": silent,
        "conformance_rate": round(len(matches) / max(1, len(assigned)), 4),
        "conformance_rate_among_contributors": round(len(matches) / max(1, contributing), 4),
    }


def retention(dev: SqliteDevice, job_id: str, final: object) -> dict:
    """Did every rank's contribution reach the operator, and did it survive?

    The two are different guarantees and only one of them is the protocol's.
    Delivery is checked against the operands the library actually handed to the
    operator, recorded in the pending_op table; survival is checked against the
    final artifact.  A gap between them is an operator defect, not a transport
    defect, and it is the agent-specific analogue of a reduction that is not
    faithful to its inputs.
    """
    operand_text = " ".join(
        (u["operands"] or "") for u in
        dev.query("SELECT operands FROM pending_op WHERE job_id=?", (job_id,)))
    final_text = final if isinstance(final, str) else util.dumps(final)
    delivered, survived = [], []
    for rank, (_chapter, probes) in sorted(RETENTION_PROBES.items()):
        if any(pr.lower() in operand_text.lower() for pr in probes):
            delivered.append(rank)
        if final_text and any(pr.lower() in final_text.lower() for pr in probes):
            survived.append(rank)
    n = len(RETENTION_PROBES)
    return {
        "probes": {str(r): {"chapter": c, "markers": pr}
                   for r, (c, pr) in sorted(RETENTION_PROBES.items())},
        "delivered_to_operator": delivered,
        "delivered_fraction": round(len(delivered) / n, 4),
        "survived_in_result": survived,
        "retention_fraction": round(len(survived) / n, 4),
        "lost_by_operator": sorted(set(delivered) - set(survived)),
        "never_delivered": sorted(set(range(n)) - set(delivered)),
    }


def compare(results: list[dict]) -> dict:
    """How much does re-association change the answer?

    The comparison is deliberately crude --- a character-level similarity on the
    merged text, plus whether each seeded conflict was explicitly resolved.  A
    sharper metric would need a judge, and a judge is another model whose
    reliability we would then have to argue about.  The honest claim here is
    about *whether the conflicts survived the merge*, which is checkable.
    """
    texts = {}
    for r in results:
        final = r.get("final_result")
        if not final:
            continue
        texts[r["algo"]] = final if isinstance(final, str) else util.dumps(final)
    out: dict[str, object] = {"algorithms_compared": sorted(texts)}
    if len(texts) == 2:
        (a_name, a), (b_name, b) = sorted(texts.items())
        out["similarity"] = round(difflib.SequenceMatcher(None, a, b).ratio(), 4)
        out["length_tokens"] = {a_name: util.count_tokens(a), b_name: util.count_tokens(b)}
    # Did the merge preserve the seeded terminology conflict at all?
    probes = {
        "rechner_conflict_surfaced": lambda t: "Rechner" in t or "rechner" in t.lower(),
        "explicit_resolution_marker": lambda t: "RESOLVED" in t,
        "mentions_host_and_machine": lambda t: "host" in t.lower() and "machine" in t.lower(),
    }
    out["content_probes"] = {
        name: {algo: bool(fn(text)) for algo, text in sorted(texts.items())}
        for name, fn in probes.items()
    }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=os.path.join(os.path.dirname(__file__), "runs"))
    parser.add_argument("--algos", default="linear,binomial")
    parser.add_argument("--out", default=os.path.join(os.path.dirname(__file__),
                                                      "..", "..", "results",
                                                      "ampi_e2_semantic.json"))
    args = parser.parse_args()

    configurations = []
    for algo in args.algos.split(","):
        job_dir = os.path.join(os.path.abspath(args.root), f"job-{algo}")
        if not os.path.exists(os.path.join(job_dir, "job.db")):
            print(f"skipping {algo}: no job database")
            continue
        entry = collect_one(job_dir, algo)
        entry["trace_summary"] = summarise(job_dir)
        configurations.append(entry)

    payload = {
        "experiment": "e2-semantic-allreduce",
        "ranks": "Cursor subagents, one per rank",
        "operator": "AMPI_SYNTHESIZE (semantic, declared neither associative nor commutative)",
        "note": "The binomial schedule is forced with --algo; the decision function would "
                "refuse it for this operator, and the point of the comparison is to measure "
                "what the declaration is worth and what forcing it costs.",
        "configurations": configurations,
        "comparison": compare(configurations),
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)

    for c in configurations:
        print(f"{c['algo']:>10}: upcalls={c['upcalls_total']} "
              f"critical_path={c['upcalls_critical_path']} "
              f"wall={c['wall_seconds']}s "
              f"per_rank={c['upcalls_by_rank']} "
              f"suspicions={c['suspicion_events']} "
              f"(spurious {c['spurious_condemnations']}, "
              f"{len(c['ranks_ever_condemned'])} ranks) "
              f"delivered={c['retention']['delivered_fraction']:.2f} "
              f"retained={c['retention']['retention_fraction']:.2f} "
              f"lost_by_operator={c['retention']['lost_by_operator']} "
              f"conformance={c['conformance']['conformance_rate']:.2f}")
    print(json.dumps(payload["comparison"], indent=2))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
