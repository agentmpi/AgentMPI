"""Generate paper/results.tex from committed run data.

Every quantity in the paper is a macro defined here, and every macro is computed
from a file under experiments/results/ or runs/.  Nothing is typed by hand.  The
discipline costs a little friction and buys the property that matters: a number in
the paper cannot drift from the run that produced it, and a reader can regenerate
the whole table from the artifacts.

Missing data yields ``n/a`` rather than a build failure, so the paper builds from
a partial set of runs while the remaining ones are still going.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS = ROOT / "experiments" / "results"
RUNS = ROOT / "runs"
OUT = ROOT / "paper" / "results.tex"

_macros: dict[str, str] = {}

# LaTeX control sequences may contain only letters, so a macro named after a rank
# count has to spell the digits.  Getting this wrong produces "You already have
# nine parameters", which is not a helpful way to learn it.
_DIGITS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
}
_NUMBERS = {
    "8": "eight", "16": "sixteen", "32": "thirtytwo", "64": "sixtyfour",
    "128": "onetwentyeight", "256": "twofiftysix",
}


def texname(name: str) -> str:
    for number, word in sorted(_NUMBERS.items(), key=lambda kv: -len(kv[0])):
        if name.endswith(number):
            return name[: -len(number)] + word
    return "".join(_DIGITS.get(c, c) for c in name)


def put(name: str, value: Any, *, fmt: str = "") -> None:
    name = texname(name)
    if value is None:
        _macros[name] = r"\textit{n/a}"
        return
    if isinstance(value, float) and fmt:
        _macros[name] = format(value, fmt)
    elif isinstance(value, int):
        _macros[name] = f"{value:,}".replace(",", "{,}")
    else:
        _macros[name] = str(value)


def load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


# --------------------------------------------------------------------------
# Protocol constants and the catalogue (always available: pure computation)
# --------------------------------------------------------------------------


def protocol_macros() -> None:
    import sys

    sys.path.insert(0, str(ROOT))
    from ampi.constants import CONFORMANCE_LEVELS, EAGER_THRESHOLD_TOKENS
    from ampi.core.algorithms import build_schedule, explain_selection
    from ampi.core.context import ResidencyModel

    put("eagerThreshold", EAGER_THRESHOLD_TOKENS)
    put("nLevels", len(CONFORMANCE_LEVELS))

    # Residency at the scale where inlining becomes impossible.
    m = ResidencyModel(p=128, n=4000)
    ag_inline_total, ag_inline_peak = m.allgather_inline()
    ag_handle_total, ag_handle_peak = m.allgather_handle()
    put("ctxP", 128)
    put("ctxN", 4000)
    put("agInlinePeak", ag_inline_peak)
    put("agHandlePeak", ag_handle_peak)
    put("agInlineTotal", ag_inline_total)
    put("agHandleTotal", ag_handle_total)
    put("agPeakRatio", int(round(ag_inline_peak / ag_handle_peak)))

    # The two schedules that tie on latency and differ sixfold in price.
    for p in (8, 16, 32, 64, 128):
        rd = build_schedule("allreduce", "recursive_doubling", p, tokens=4000, inline=False)
        rb = build_schedule("allreduce", "reduce_bcast", p, tokens=4000, inline=False)
        put(f"rdApps{p}", rd.applications)
        put(f"rbApps{p}", rb.applications)
        put(f"rdRatio{p}", round(rd.applications / rb.applications, 1), fmt=".1f")
    flat = build_schedule("reduce", "flat", 64, tokens=4000, inline=False)
    tree = build_schedule("reduce", "binomial", 64, tokens=4000, inline=False)
    put("flatCritical", flat.critical_path_applications)
    put("treeCritical", tree.critical_path_applications)

    # Where the winner changes as the operator gets expensive.
    sweep = {r["gamma_s"]: r for r in explain_selection("allreduce", 64, tokens=4000)}
    put("winnerGammaZero", sweep[0.0]["winner"].replace("_", r"\_"))
    put("winnerGammaThirty", sweep[30.0]["winner"].replace("_", r"\_"))
    put("flatGammaThirty", round(sweep[30.0]["flat"], 1), fmt=".1f")
    put("bestGammaThirty", round(sweep[30.0]["reduce_bcast"], 1), fmt=".1f")
    put("flatPenalty", int(round(sweep[30.0]["flat"] / sweep[30.0]["reduce_bcast"])))

    # Barrier: the crossover MPI has, that a shared control plane removes.
    for p in (8, 64, 256):
        c = build_schedule("barrier", "central", p)
        d = build_schedule("barrier", "dissemination", p)
        put(f"barCentral{p}", c.n_messages)
        put(f"barDissem{p}", d.n_messages)


# --------------------------------------------------------------------------
# E0 microbenchmarks
# --------------------------------------------------------------------------


def e0_macros() -> None:
    d = load(RESULTS / "e0_micro.json")
    if not d:
        for k in ("alphaSqlite", "betaSqlite", "nHalfSqlite", "alphaJournal", "nHalfJournal",
                  "alphaMemory", "betaSpread", "protoFracOne", "protoFracThirty", "protoMsP"):
            put(k, None)
        return
    pp = d["pingpong"]
    put("alphaSqlite", round(pp["sqlite"]["alpha_s"] * 1e3, 3), fmt=".3f")
    put("betaSqlite", round(pp["sqlite"]["beta_s_per_token"] * 1e6, 3), fmt=".3f")
    put("nHalfSqlite", int(round(pp["sqlite"]["n_half"])))
    put("alphaJournal", round(pp["journal"]["alpha_s"] * 1e3, 3), fmt=".3f")
    put("nHalfJournal", int(round(pp["journal"]["n_half"])))
    put("alphaMemory", round(pp["memory"]["alpha_s"] * 1e3, 3), fmt=".3f")
    betas = [v["beta_s_per_token"] for v in pp.values()]
    put("betaSpread", round(100 * (max(betas) - min(betas)) / min(betas), 1), fmt=".1f")
    rows = {(r["p"], r["gamma_s"]): r for r in d["relative_cost"]["rows"]}
    put("protoMsP", round(rows[(128, 0.0)]["protocol_s"] * 1e3, 2), fmt=".2f")
    put("protoFracOne", round(100 * rows[(128, 1.0)]["protocol_fraction"], 3), fmt=".3f")
    put("protoFracThirty", round(100 * rows[(128, 30.0)]["protocol_fraction"], 4), fmt=".4f")
    put("e0Devices", len(pp))


# --------------------------------------------------------------------------
# E1 translation
# --------------------------------------------------------------------------


def e1_macros() -> None:
    d = load(RESULTS / "e1_scores.json")
    if not d:
        for k in ("eOneGlossConsistency", "eOneCtrlConsistency", "eOneGlossWall",
                  "eOneCtrlWall", "eOneWallRatio", "eOneExecutors", "eOneTermsScored"):
            put(k, None)
    else:
        by_arm = {r["arm"]: r for r in d["comparison"] if r["size"] == 8}
        g, c = by_arm.get("glossary"), by_arm.get("nogloss")
        if g and c:
            put("eOneGlossConsistency", round(g["consistency"], 3), fmt=".3f")
            put("eOneCtrlConsistency", round(c["consistency"], 3), fmt=".3f")
            put("eOneGlossStrict", round(g["consistency_strict"], 4), fmt=".4f")
            put("eOneCtrlStrict", round(c["consistency_strict"], 4), fmt=".4f")
            put("eOneGlossWall", int(round(g["wall_s"])))
            put("eOneCtrlWall", int(round(c["wall_s"])))
            put("eOneWallRatio", round(g["wall_s"] / c["wall_s"], 1), fmt=".1f")
            put("eOneTermsScored", g["scored"])
            put("eOneExecutors", g["executors"] + c["executors"])
            put("eOneGlossTokens", g["tokens"])
            put("eOneCtrlTokens", c["tokens"])

    # The glossary reduction's own behaviour, read from the run's journal.
    g = load(RUNS / "e1-real-p8" / "report.json")
    if g:
        put("eOneP", g.get("size"))
        put("eOneVerdict", g.get("diagnosis", {}).get("verdict", "n/a"))

    conflicts = load(RESULTS / "e1_conflicts.json")
    if conflicts:
        for run, key in (("e1-real-p8", "Small"), ("e1-real-p32", "Large")):
            c = conflicts.get(run)
            if c:
                put(f"gloss{key}P", c["size"])
                put(f"gloss{key}Agreed", c["agreed"])
                put(f"gloss{key}Lifted", c["lifted"])
    else:
        for key in ("Small", "Large"):
            for f in ("P", "Agreed", "Lifted"):
                put(f"gloss{key}{f}", None)

    if d:
        by = {(r["size"], r["arm"]): r for r in d["comparison"]}
        g32, c32 = by.get((32, "glossary")), by.get((32, "nogloss"))
        if g32 and c32:
            put("eLargeGlossConsistency", round(g32["consistency"], 3), fmt=".3f")
            put("eLargeCtrlConsistency", round(c32["consistency"], 3), fmt=".3f")
            put("eLargeGlossStrict", round(g32["consistency_strict"], 4), fmt=".4f")
            put("eLargeCtrlStrict", round(c32["consistency_strict"], 4), fmt=".4f")
            put("eLargeGlossWall", int(round(g32["wall_s"])))
            put("eLargeCtrlWall", int(round(c32["wall_s"])))
            put("eLargeWallRatio", round(g32["wall_s"] / c32["wall_s"], 1), fmt=".1f")
            put("eLargeTermsScored", g32["scored"])
            put("eLargeExecutors", g32["executors"])
            put("eLargeGlossTokens", g32["tokens"])
            put("eLargeCtrlTokens", c32["tokens"])
            put("eLargeP", 32)
        else:
            for k in ("eLargeGlossConsistency", "eLargeCtrlConsistency", "eLargeGlossStrict",
                      "eLargeCtrlStrict", "eLargeGlossWall", "eLargeCtrlWall",
                      "eLargeWallRatio", "eLargeTermsScored", "eLargeExecutors",
                      "eLargeGlossTokens", "eLargeCtrlTokens", "eLargeP"):
                put(k, None)

    scale = load(RESULTS / "e1_e1-real-p100.json")
    if scale:
        put("eScaleRanks", scale.get("size"))
        put("eScaleSucceeded", scale.get("succeeded"))
        put("eScaleExecutors", len((scale.get("broker") or {}).get("executors", [])))
        put("eScaleWall", int(round(scale.get("wall_s", 0))))
        put("eScaleTokens", (scale.get("broker") or {}).get("result_tokens"))
        put("eScaleOversub", round(
            scale["size"] / max(1, len((scale.get("broker") or {}).get("executors", []))), 1
        ), fmt=".1f")
        put("eScaleContext", scale.get("context_total"))
    else:
        for k in ("eScaleRanks", "eScaleSucceeded", "eScaleExecutors", "eScaleWall",
                  "eScaleTokens", "eScaleOversub", "eScaleContext"):
            put(k, None)

    scores = load(RESULTS / "e1_scores.json")
    if scores:
        big = [r for r in scores["comparison"] if (r.get("size") or 0) > 8]
        if big:
            put("eScaleConsistency", round(big[0]["consistency"], 3), fmt=".3f")
            put("eScaleStrict", round(big[0]["consistency_strict"], 4), fmt=".4f")
            put("eScaleTermsScored", big[0]["scored"])
            put("eScaleDisagree", big[0]["disagreements_strict"])
        else:
            for k in ("eScaleConsistency", "eScaleStrict", "eScaleTermsScored", "eScaleDisagree"):
                put(k, None)


# --------------------------------------------------------------------------
# Suite sizes, read from the suite rather than asserted
# --------------------------------------------------------------------------


def provenance_macros() -> None:
    d = load(RESULTS / "e1_provenance.json")
    if not d:
        for k in ("provTasks", "provExecutors", "provDrift", "provDriftPct",
                  "provRefused", "provSmallDrift"):
            put(k, None)
        return
    by = {a["run"]: a for a in d["audits"]}
    big = by.get("e1-real-p100")
    if big:
        put("provTasks", big["tasks"])
        put("provExecutors", big["distinct_provenance_labels"])
        put("provRanks", big["ranks_with_a_task"])
        put("provDrift", big["provenance_label_drift"]["count"])
        put("provDriftPct", round(100 * big["provenance_label_drift"]["fraction"], 1), fmt=".1f")
        put("provRefused", big["protocol_identity_violations"]["rejected_at_submit"])
    small = by.get("e1-real-p8")
    if small:
        put("provSmallDrift", small["provenance_label_drift"]["count"])


def e2_macros() -> None:
    d = load(RESULTS / "e2_faults.json")
    if not d:
        for k in ("eTwoUpheld", "eTwoTotal", "eTwoKilled", "eTwoSurvivors", "eTwoRevokeS",
                  "eTwoShrinkers", "eTwoComms", "eTwoTasks", "eTwoCompleted",
                  "eTwoOrphaned", "eTwoDuplicated", "eTwoSize"):
            put(k, None)
        return
    put("eTwoUpheld", d["claims_upheld"])
    put("eTwoTotal", d["claims_total"])
    put("eTwoSize", d["size"])
    by = {s["scenario"]: s for s in d["scenarios"]}
    s1 = by["kill_before_collective"]
    put("eTwoKilled", len(s1["killed"]))
    put("eTwoSurvivors", s1["contributors"])
    put("eTwoRevokeS", round(by["revoke_unblocks_a_blocked_survivor"]["freed_after_s"], 2), fmt=".2f")
    s3 = by["concurrent_shrink_converges"]
    put("eTwoShrinkers", s3["shrinking_ranks"])
    put("eTwoComms", s3["distinct_communicators"])
    s5 = by["claimed_work_survives_its_claimant"]
    put("eTwoTasks", s5["tasks"])
    put("eTwoCompleted", s5["tasks_completed"])
    put("eTwoOrphaned", len(s5["tasks_orphaned"]))
    put("eTwoDuplicated", len(s5["tasks_done_twice"]))
    s4 = by["recovery_briefing"]
    put("eTwoRecovered", s4["published_recovered"])
    put("eTwoExpected", s4["published_expected"])


def e3_macros() -> None:
    """The production translation series: one block of macros per scale.

    Reads the series table rather than each run's report, because the quantities
    the paper cites about E3 are cross-scale by nature --- how coordination,
    disagreement and achieved parallelism move as ``p`` goes 16, 32, 64 --- and
    recomputing them per run would let the paper cite a figure the series plot
    does not agree with.
    """
    rows = load(RUNS / "e3-series" / "series.json")
    scales = (16, 32, 64)
    keys = ("Wall", "WallH", "Tasks", "Execs", "Coord", "Par", "Eff",
            "Conf", "Imbal", "MaxWait", "Blocked", "Work", "Incomplete")

    def emit(prefix: str, p: int, row: Any) -> None:
        if not row:
            for key in keys:
                put(f"{prefix}{key}{p}", None)
            return
        put(f"{prefix}Wall{p}", int(round(row["wall_s"])))
        put(f"{prefix}WallH{p}", round(row["wall_s"] / 3600, 2), fmt=".2f")
        put(f"{prefix}Tasks{p}", row["tasks"])
        put(f"{prefix}Execs{p}", row["executors"])
        put(f"{prefix}Coord{p}", round(row["coordination_share"] * 100, 1), fmt=".1f")
        put(f"{prefix}Par{p}", round(row["achieved_parallelism"], 2), fmt=".2f")
        put(f"{prefix}Eff{p}", round(row["parallel_efficiency"] * 100, 1), fmt=".1f")
        put(f"{prefix}Conf{p}", row["conflicts"])
        put(f"{prefix}Imbal{p}", round(row["imbalance"], 2), fmt=".2f")
        put(f"{prefix}MaxWait{p}", int(round(row["max_single_wait_s"])))
        put(f"{prefix}Blocked{p}", int(round(row["blocked_rank_s"])))
        put(f"{prefix}Work{p}", int(round(row["work_rank_s"])))
        put(f"{prefix}Incomplete{p}", row["incomplete"])

    if not rows:
        for p in scales:
            emit("eThreeStub", p, None)
            emit("eThreeReal", p, None)
        for key in ("Researched", "Duplicated", "Saved", "Sources", "Starved"):
            put(f"eThree{key}", None)
        return

    # Two series, kept apart on purpose.  The surrogate runs measure the protocol
    # with the operator cost set to zero; the broker run measures the regime where
    # an executor turn dominates everything else.  Averaging them, or letting one
    # macro mean either, would produce a number that describes neither.
    stub = {r["p"]: r for r in rows if r["executor"] != "broker"}
    real = {r["p"]: r for r in rows if r["executor"] == "broker"}
    for p in scales:
        emit("eThreeStub", p, stub.get(p))
        emit("eThreeReal", p, real.get(p))

    # The research-sharing saving, which is the window's whole justification: the
    # agenda is bounded and independent of p, so what a run avoids is every rank
    # researching every contested term.
    glossary = load(RUNS / "e3-real-p16" / "glossary.json")
    if glossary:
        researched = len(glossary)
        put("eThreeResearched", researched)
        put("eThreeSources", sum(len(v.get("sources") or []) for v in glossary.values()))
        # What the window bought: without it every rank that met a term would have
        # researched it, so the saving is the population minus the one rank that
        # actually did the work.
        biggest = max(real, default=16)
        put("eThreeDuplicated", researched * biggest)
        put("eThreeSaved", researched * (biggest - 1))
    else:
        for key in ("Researched", "Duplicated", "Saved", "Sources"):
            put(f"eThree{key}", None)

    metrics = load(RUNS / "e3-real-p16" / "analysis" / "metrics.json")
    if metrics:
        put("eThreeStarved", len(metrics.get("starved_tasks", [])))
        # The enqueue-to-claim wait, which is a different quantity from the longest
        # wait *inside* a collective and answers a different question: not "how long
        # did coordination take" but "how long before anybody turned up".
        put("eThreeClaimWait", int(round(metrics.get("max_claim_wait_s", 0))))
        put("eThreeRequeued", (metrics.get("tasks") or {}).get("requeued", 0))
        put("eThreeSubmitted", (metrics.get("tasks") or {}).get("submitted", 0))
        # Work the population finished and the harness threw away, because it had
        # already given up on the rank that asked for it.  The one measure of the
        # three that says the population was capable and the configuration was not.
        wasted = metrics.get("wasted_submissions") or []
        put("eThreeWasted", len(wasted))
        put("eThreeWastedRanks", len({w["rank"] for w in wasted}))
    else:
        for key in ("Starved", "ClaimWait", "Requeued", "Submitted", "Wasted", "WastedRanks"):
            put(f"eThree{key}", None)


def suite_macros() -> None:
    d = load(RESULTS / "suite.json")
    if not d:
        for k in ("nTests", "nProtocolTests", "nDeviceTests", "nDevices", "implLines"):
            put(k, None)
        return
    for k, v in d.items():
        put(k, v)


def main() -> None:
    protocol_macros()
    e0_macros()
    e1_macros()
    e2_macros()
    e3_macros()
    provenance_macros()
    suite_macros()

    lines = [
        "% Generated by paper/tools/make_macros.py from committed run data.",
        "% Do not edit: every number here is computed from experiments/results/ or runs/.",
        "",
    ]
    for name in sorted(_macros):
        lines.append(rf"\newcommand{{\{name}}}{{{_macros[name]}\xspace}}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    missing = [k for k, v in _macros.items() if "n/a" in v]
    print(f"wrote {OUT} with {len(_macros)} macros ({len(missing)} unavailable)")
    if missing:
        print("  unavailable:", ", ".join(sorted(missing)))


if __name__ == "__main__":
    main()
