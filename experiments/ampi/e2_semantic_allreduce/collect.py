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
            "SELECT * FROM coll WHERE job_id=? AND op IN ('reduce','allreduce') "
            "ORDER BY created_at LIMIT 1", (job_id,))
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
        path_stats = critical_path(dev, job_id, algo, len(ranks))
        # When ranks were admitted to the executor pool matters enormously and
        # has nothing to do with the schedule, so report it separately rather
        # than letting it hide inside a wall-clock number.
        join_times = sorted(r["started_at"] for r in joined)
        admission_spread = round(join_times[-1] - join_times[0], 1) if len(join_times) > 1 else 0.0

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
            "critical_path": path_stats,
            "executor_admission_spread_s": admission_spread,
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


def critical_path(dev: SqliteDevice, job_id: str, algo: str, p: int) -> dict:
    """Model time along the longest dependency chain of the reduction.

    Wall clock is not a usable comparison here.  Ranks are admitted to the
    executor pool a few at a time, so a run's elapsed time is dominated by when
    the platform happened to schedule its agents: our linear arm stalled for
    1570 s in the middle of the reduction waiting for a rank that could not be
    launched, which says nothing about the schedule under test.

    What is comparable is the sum of measured operator turnarounds along the
    longest chain of *dependent* evaluations, since that is what a schedule
    actually controls.  The dependency structure comes from the schedule
    itself: under a linear reduction every evaluation is on the root and they
    are strictly sequential; under a binomial tree, rank r's evaluation at
    round k cannot start until its child's subtree is complete, and sibling
    subtrees proceed in parallel.
    """
    ups = dev.query(
        "SELECT assignee, step, created_at, settled_at FROM pending_op "
        "WHERE job_id=? AND state='done' ORDER BY assignee, step", (job_id,))
    if not ups:
        return {}
    dur: dict[tuple[int, int], float] = {}
    for u in ups:
        if u["settled_at"] and u["created_at"]:
            dur[(int(u["assignee"]), int(u["step"]))] = u["settled_at"] - u["created_at"]

    by_rank: dict[int, list[float]] = {}
    for (rank, _step), d in sorted(dur.items()):
        by_rank.setdefault(rank, []).append(d)

    if algo != "binomial":
        # Strictly sequential at the root.
        root_chain = sum(by_rank.get(0, []))
        return {"model": "sequential at the root",
                "root_evaluations": len(by_rank.get(0, [])),
                "root_model_seconds": round(root_chain, 1),
                "critical_path_seconds": round(root_chain, 1)}

    # Binomial tree rooted at 0: rank r receives from r | (1 << k) at round k,
    # for each k where bit k of r is zero, ascending. Its j-th evaluation
    # therefore depends on that child's whole subtree.
    def children(rank: int) -> list[int]:
        out = []
        mask = 1
        while mask < p:
            if rank & mask:
                break
            child = rank | mask
            if child < p:
                out.append(child)
            mask <<= 1
        return out

    def finish(rank: int) -> float:
        """When rank finishes its subtree, in model-seconds from its own start."""
        elapsed = 0.0
        mine = list(by_rank.get(rank, []))
        for j, child in enumerate(children(rank)):
            ready = finish(child)
            elapsed = max(elapsed, ready)
            if j < len(mine):
                elapsed += mine[j]
        return elapsed

    return {"model": "binomial tree; sibling subtrees overlap",
            "root_evaluations": len(by_rank.get(0, [])),
            "root_model_seconds": round(sum(by_rank.get(0, [])), 1),
            "critical_path_seconds": round(finish(0), 1)}


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

    # A rank that evaluates the operator folds its own value in locally, so its
    # contribution is the first operand of its *first* upcall.  Take that in
    # preference to anything it sent: under a tree schedule what an internal
    # rank sends is its merged subtree, not its own chapter, and reading the
    # message would score a correct rank as non-conforming.
    sent: dict[int, str] = {}
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

    # Leaves never evaluate the operator, so for them the first message sent is
    # the contribution.
    for row in dev.query(
        "SELECT src, body FROM message WHERE job_id=? AND body IS NOT NULL ORDER BY msg_id",
        (job_id,),
    ):
        rank = int(row["src"])
        if rank in sent:
            continue
        payload = util.loads(row["body"], {})
        value = payload.get("v") if isinstance(payload, dict) else None
        if isinstance(value, dict) and "chapter" in value:
            sent[rank] = value["chapter"]

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
    parser.add_argument("--job", action="append", default=[], metavar="ALGO=PATH",
                        help="explicit job directory for one arm, repeatable; overrides --root")
    parser.add_argument("--out", default=os.path.join(os.path.dirname(__file__),
                                                      "..", "..", "results",
                                                      "ampi_e2_semantic.json"))
    args = parser.parse_args()

    explicit = dict(pair.split("=", 1) for pair in args.job)
    configurations = []
    for algo in args.algos.split(","):
        job_dir = os.path.abspath(explicit.get(algo,
                                               os.path.join(args.root, f"job-{algo}")))
        if not os.path.exists(os.path.join(job_dir, "job.db")):
            print(f"skipping {algo}: no job database")
            continue
        entry = collect_one(job_dir, algo)
        entry["trace_summary"] = summarise(job_dir)
        configurations.append(entry)

    payload = {
        "experiment": "e2-semantic-reduction",
        "ranks": "Cursor subagents, one per rank",
        "collective": "AMPI_Reduce to root 0",
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
              f"critpath={c['critical_path'].get('critical_path_seconds')}s "
              f"root_model={c['critical_path'].get('root_model_seconds')}s "
              f"admission_spread={c['executor_admission_spread_s']}s "
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
