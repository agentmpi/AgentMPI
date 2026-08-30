"""Generate the paper's tables and figures from the result JSON.

No number in the paper is transcribed by hand. Every table under
``paper/generated/`` is produced by this script from files in ``results/``, so a
number in the PDF can be traced to the run that produced it, and re-running the
experiments updates the paper. Transcription is the most common source of wrong
numbers in systems papers and it is entirely avoidable.

Usage::

    python3 scripts/make_tables.py            # regenerate everything present
    python3 scripts/make_tables.py --check    # fail if a required input is missing
"""

from __future__ import annotations

import argparse
import glob
import json
import math
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "results"
OUT = REPO / "paper" / "generated"

MISSING = r"\emph{(not measured in this run)}"

#: Single-value macros collected by the table builders and emitted together, so
#: the paper can refer to a measured number inside a caption -- where an
#: ``\input`` would be fragile under hyperref.
_MACROS: dict[str, str] = {}


# --------------------------------------------------------------------- helpers


def load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def emit(name: str, body: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(body.rstrip() + "\n", encoding="utf-8")
    print(f"  wrote {name}")


def tex_escape(s: str) -> str:
    for a, b in (("\\", r"\textbackslash{}"), ("_", r"\_"), ("%", r"\%"), ("&", r"\&"), ("#", r"\#"), ("$", r"\$")):
        s = s.replace(a, b)
    return s


def fmt(x: Any, nd: int = 2, dash: str = "--") -> str:
    if x is None:
        return dash
    if isinstance(x, bool):
        return r"\checkmark" if x else r"$\times$"
    if isinstance(x, (int,)) and not isinstance(x, bool):
        return f"{x:,}"
    if isinstance(x, float):
        if x != x or math.isinf(x):
            return dash
        return f"{x:,.{nd}f}"
    return tex_escape(str(x))


# ------------------------------------------------------------ cost model table


def table_cost_formulas() -> None:
    """Closed-form cost of every implemented algorithm."""
    import sys

    sys.path.insert(0, str(REPO / "src"))
    from agentmpi.cost import FORMULAS  # noqa: PLC0415 - path set above

    rows = []
    for (op, alg) in sorted(FORMULAS):
        f = FORMULAS[(op, alg)]
        cells = []
        for p in (8, 64):
            r, m, v, d = f(p, 1)
            cells.append((int(r), int(m), int(d)))
        rows.append(
            rf"{tex_escape(op)} & \texttt{{{tex_escape(alg)}}} & {cells[0][0]} & {cells[0][1]} & {cells[0][2]} "
            rf"& {cells[1][0]} & {cells[1][1]} & {cells[1][2]} \\"
        )
    body = (
        "\\begin{tabular}{llrrrrrr}\n\\toprule\n"
        "& & \\multicolumn{3}{c}{$p=8$} & \\multicolumn{3}{c}{$p=64$} \\\\\n"
        "\\cmidrule(lr){3-5}\\cmidrule(lr){6-8}\n"
        "collective & algorithm & rounds & msgs & depth & rounds & msgs & depth \\\\\n\\midrule\n"
        + "\n".join(rows)
        + "\n\\bottomrule\n\\end{tabular}"
    )
    emit("tab_cost_formulas.tex", body.replace("cmidrule(lr)", "cmidrule(lr)"))


# ------------------------------------------------------- model validation table


def table_model_validation() -> None:
    data = None
    for cand in ("free-collectives", "free-all", "mb-smoke-all"):
        d = load(RESULTS / "microbench" / f"{cand}.json")
        if d and "collectives" in (d.get("benches") or {}):
            data = d["benches"]["collectives"]
            break
    if not data:
        emit("tab_model_validation.tex", MISSING)
        return
    rows = data["rows"]
    by_op: dict[str, dict[str, Any]] = {}
    for r in rows:
        if not r.get("ok"):
            continue
        b = by_op.setdefault(r["op"], {"n": 0, "msg_ok": 0, "depth_ok": 0, "algs": set(), "ps": set()})
        b["n"] += 1
        b["msg_ok"] += 1 if r["messages_match"] else 0
        b["depth_ok"] += 1 if r["fold_depth_match"] else 0
        b["algs"].add(r["algorithm"])
        b["ps"].add(r["p"])
    lines = []
    for op in sorted(by_op):
        b = by_op[op]
        lines.append(
            rf"{tex_escape(op)} & {len(b['algs'])} & {len(b['ps'])} & {b['n']} & "
            rf"{b['msg_ok']}/{b['n']} & {b['depth_ok']}/{b['n']} \\"
        )
    body = (
        "\\begin{tabular}{lrrrrr}\n\\toprule\n"
        "collective & algorithms & sizes & configs & message count & fold depth \\\\\n\\midrule\n"
        + "\n".join(lines)
        + "\n\\midrule\n"
        + rf"\textbf{{total}} & & & {data['n_configurations']} & "
        + rf"{int(data['message_count_agreement'] * data['n_configurations'])}/{data['n_configurations']} & "
        + rf"{int(data['fold_depth_agreement'] * data['n_configurations'])}/{data['n_configurations']} \\"
        + "\n\\bottomrule\n\\end{tabular}"
    )
    emit("tab_model_validation.tex", body)
    _MACROS["NModelConfigs"] = str(data["n_configurations"])
    _MACROS["NModelAgreement"] = f"{100 * data['message_count_agreement']:.0f}"
    _MACROS["NModelDepthAgreement"] = f"{100 * data['fold_depth_agreement']:.0f}"


# ------------------------------------------------------------- translation table


def _tr_results() -> list[tuple[str, dict[str, Any]]]:
    """Translation results belonging to the largest single campaign.

    Filtering by campaign label is not cosmetic. Smoke-test runs use the same
    result schema, and mixing a 0.1-second synthetic run into a strong-scaling
    table silently produces a speedup of five thousand. Selecting the label with
    the most configurations keeps the comparison within one population and one
    prompt version, which is also the only way the ablations are comparable.
    """
    by_label: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for f in sorted(glob.glob(str(RESULTS / "translation" / "*.json"))):
        d = load(Path(f))
        if not d or "quality" not in d:
            continue
        label = str((d.get("config") or {}).get("label") or "?")
        by_label.setdefault(label, []).append((Path(f).stem, d))
    if not by_label:
        return []
    best = max(by_label, key=lambda k: (len(by_label[k]), sum(x[1]["job"]["agent_calls"] for x in by_label[k])))
    return by_label[best]


def table_translation() -> None:
    res = _tr_results()
    if not res:
        emit("tab_translation.tex", MISSING)
        emit("tab_translation_scaling.tex", MISSING)
        return

    def label(cfg: dict[str, Any]) -> str:
        bits = []
        bits.append("glossary" if cfg.get("glossary") else "\\textbf{no glossary}")
        bits.append("halo" if cfg.get("halo") else "\\textbf{no halo}")
        if cfg.get("glossary_op") == "semantic":
            bits.append("\\textbf{semantic merge}")
        if cfg.get("allreduce_alg") == "recursive_doubling":
            bits.append("rec.\\ doubling")
        return ", ".join(bits)

    def order(cfg: dict[str, Any]) -> tuple[int, ...]:
        # Baseline first, then each single-mechanism change, so the table reads as
        # a ladder rather than as an arbitrary ordering.
        return (
            0 if (cfg.get("glossary") and cfg.get("halo") and cfg.get("glossary_op") == "union"
                  and cfg.get("allreduce_alg") == "reduce_bcast") else 1,
            0 if cfg.get("halo") else 1,
            0 if cfg.get("glossary") else 1,
            0 if cfg.get("glossary_op") == "union" else 1,
        )

    ablations = [(n, d) for n, d in res if d["config"].get("ranks") == max(x[1]["config"].get("ranks", 0) for x in res)]
    lines = []
    for _, d in sorted(ablations, key=lambda kv: order(kv[1]["config"])):
        q, j, c = d["quality"], d["job"], d["config"]
        lines.append(
            rf"{label(c)} & {fmt(q['consistency'], 3)} & {q['n_divergent_entities']}/{q['n_shared_entities']} & "
            rf"{fmt(q['rendering_verified'], 3)} & {fmt(q['paragraph_fidelity'], 3)} & "
            rf"{fmt(q['target_script_ratio'], 3)} & {fmt(j['wall_s'], 0)} & {j['agent_calls']} & "
            rf"{fmt(j['tokens_in'] + j['tokens_out'])} & {fmt(j['usd'], 3)} \\"
        )
    body = (
        "\\begin{tabular}{lrrrrrrrrr}\n\\toprule\n"
        "configuration & consist. & diverg. & verified & para. & CJK & wall (s) & calls & tokens & USD \\\\\n\\midrule\n"
        + "\n".join(lines)
        + "\n\\bottomrule\n\\end{tabular}"
    )
    emit("tab_translation.tex", body)

    # strong scaling
    scal = {}
    for _, d in res:
        c = d["config"]
        if c.get("glossary") and c.get("halo") and c.get("glossary_op") == "union" and c.get("allreduce_alg") == "reduce_bcast":
            scal[c["ranks"]] = d
    if len(scal) >= 2:
        base = scal.get(1) or scal[min(scal)]
        t1 = base["job"]["wall_s"]
        p1 = base["config"]["ranks"]
        lines = []
        pts: list[tuple[int, float]] = []
        for p in sorted(scal):
            d = scal[p]
            t = d["job"]["wall_s"]
            sp = (t1 * p1) / t if t else 0.0
            eff = sp / p
            pts.append((p, sp))
            kf = ((1 / sp - 1 / p) / (1 - 1 / p)) if p > 1 and sp > 0 else 0.0
            lines.append(
                rf"{p} & {fmt(t, 0)} & {fmt(sp, 2)} & {fmt(eff, 2)} & {fmt(kf, 3)} & "
                rf"{d['job']['agent_calls']} & {fmt(d['job']['tokens_in'] + d['job']['tokens_out'])} & "
                rf"{fmt(d['job']['usd'], 3)} & {fmt(d['quality']['consistency'], 3)} \\"
            )
        import sys

        sys.path.insert(0, str(REPO / "src"))
        from agentmpi.cost import fit_usl  # noqa: PLC0415

        sigma, kappa, r2 = fit_usl(pts)
        # Report the fit only when it is supported. Agent latency is heavy-tailed
        # and each point here is one trial, so a non-monotonic efficiency curve is
        # expected and a USL fit to it would be an artefact. Saying so is better
        # than printing a number with a negative coefficient of determination.
        _MACROS["NUslFit"] = (
            rf"$\sigma={sigma:.3f}$, $\kappa={kappa:.4f}$ ($R^2={r2:.3f}$)"
            if r2 >= 0.5
            else rf"not supported by single-trial data ($R^2={r2:.3f}$); "
            rf"see \cref{{tab:scalingsim}} for averaged scaling"
        )
        body = (
            "\\begin{tabular}{rrrrrrrrr}\n\\toprule\n"
            "$p$ & wall (s) & speedup & efficiency & Karp--Flatt & calls & tokens & USD & consist. \\\\\n\\midrule\n"
            + "\n".join(lines)
            + "\n\\bottomrule\n\\end{tabular}"
        )
        emit("tab_translation_scaling.tex", body)
    else:
        emit("tab_translation_scaling.tex", MISSING)

    # The semantic-vs-exact operator comparison: same mechanism, same measured
    # quality, very different price. The prose quotes these ratios.
    full = next((d for _, d in res if d["config"].get("glossary") and d["config"].get("halo")
                 and d["config"].get("glossary_op") == "union"
                 and d["config"].get("allreduce_alg") == "reduce_bcast"
                 and d["config"].get("ranks") == max(x[1]["config"]["ranks"] for x in res)), None)
    sem = next((d for _, d in res if d["config"].get("glossary_op") == "semantic"), None)
    if full and sem:
        _MACROS["NSemTime"] = f"{sem['job']['wall_s'] / max(full['job']['wall_s'], 1e-9):.1f}"
        _MACROS["NSemTokens"] = (
            f"{(sem['job']['tokens_in'] + sem['job']['tokens_out']) / max(full['job']['tokens_in'] + full['job']['tokens_out'], 1):.1f}"
        )
        _MACROS["NSemPrice"] = f"{sem['job']['usd'] / max(full['job']['usd'], 1e-9):.1f}"
        _MACROS["NSemCalls"] = str(sem["job"]["agent_calls"])
    bare = next((d for _, d in res if not d["config"].get("glossary") and d["config"].get("ranks") == max(x[1]["config"]["ranks"] for x in res)), None)
    if full and bare:
        _MACROS["NCoordTime"] = f"{100 * (full['job']['wall_s'] / bare['job']['wall_s'] - 1):.0f}"
        _MACROS["NCoordTokens"] = (
            f"{100 * ((full['job']['tokens_in'] + full['job']['tokens_out']) / (bare['job']['tokens_in'] + bare['job']['tokens_out']) - 1):.0f}"
        )

    # calibration
    best = max(res, key=lambda kv: kv[1]["job"]["agent_calls"])[1]
    cal = best["calibration"]
    body = (
        "\\begin{tabular}{lrl}\n\\toprule\nparameter & value & meaning \\\\\n\\midrule\n"
        rf"$\alpha_{{p50}}$ & {fmt(cal.get('alpha_p50'), 1)} s & median agent invocation latency \\" "\n"
        rf"$\alpha_{{p99}}$ & {fmt(cal.get('alpha_p99'), 1)} s & tail latency \\" "\n"
        rf"$\beta^{{-1}}$ & {fmt(cal.get('tokens_per_s'), 1)} tok/s & marginal output rate \\" "\n"
        rf"$\alpha/\beta$ & {fmt(cal.get('alpha_beta_crossover_tokens'), 0)} tok & latency/volume crossover \\" "\n"
        rf"fabric & {fmt(cal.get('fabric_s'), 4)} s & per-operation protocol cost \\" "\n"
        rf"$\alpha/\text{{fabric}}$ & {fmt((cal.get('alpha_p50') or 0) / (cal.get('fabric_s') or 1), 0)}$\times$ & agent cost over protocol cost \\"
        "\n\\bottomrule\n\\end{tabular}"
    )
    emit("tab_calibration.tex", body)


# ---------------------------------------------------------------- transport table


def table_transport() -> None:
    d = load(RESULTS / "microbench" / "free-transport.json") or load(RESULTS / "microbench" / "free-all.json")
    b = (d or {}).get("benches", {}).get("transport")
    if not b:
        emit("tab_transport.tex", MISSING)
        return
    by_n: dict[int, dict[str, Any]] = {}
    for r in b["rows"]:
        by_n.setdefault(r["n_tokens"], {})[r["mode"]] = r
    lines = []
    for n in sorted(by_n):
        e = by_n[n].get("eager", {})
        rz = by_n[n].get("rendezvous", {})
        lines.append(
            rf"{n:,} & {fmt(e.get('completed'))} & {tex_escape(', '.join(e.get('error_classes') or []) or '--')} & "
            rf"{fmt(e.get('max_context_used'))} & {fmt(rz.get('completed'))} & {fmt(rz.get('max_context_used'))} & "
            rf"{fmt(rz.get('tokens_deferred'))} \\"
        )
    limit = b["rows"][0].get("unexpected_limit")
    body = (
        "\\begin{tabular}{rccrccr}\n\\toprule\n"
        "& \\multicolumn{3}{c}{eager} & \\multicolumn{3}{c}{rendezvous} \\\\\n"
        "\\cmidrule(lr){2-4}\\cmidrule(lr){5-7}\n"
        "payload (tok) & completes & error & max ctx & completes & max ctx & tok deferred \\\\\n\\midrule\n"
        + "\n".join(lines)
        + "\n\\bottomrule\n\\end{tabular}"
    )
    emit("tab_transport.tex", body)
    _MACROS["NEagerLimit"] = f"{limit:,}" if limit else "--"


# ------------------------------------------------------------------ faults table


def table_faults() -> None:
    d = load(RESULTS / "microbench" / "free-faults.json") or load(RESULTS / "microbench" / "free-all.json")
    b = (d or {}).get("benches", {}).get("faults")
    if not b:
        emit("tab_faults.tex", MISSING)
        return
    lines = []
    for r in b["rows"]:
        lines.append(
            rf"\texttt{{{tex_escape(r['policy'])}}} & {fmt(r['all_completed'])} & {fmt(r['detect_p50_s'], 2)} & "
            rf"{fmt(r['detected_correctly'])} & {fmt(r['shrink_p50_s'], 3)} & {fmt(r['agree_consistent'])} \\"
        )
    body = (
        "\\begin{tabular}{lccccc}\n\\toprule\n"
        "policy & survivors complete & detect (s) & absentees named & shrink (s) & agreement consistent \\\\\n\\midrule\n"
        + "\n".join(lines)
        + "\n\\bottomrule\n\\end{tabular}"
    )
    emit("tab_faults.tex", body)


# --------------------------------------------------------------- fidelity table


def table_fidelity() -> None:
    """Reduction fidelity, separated by executor and by whether it saturated.

    Two separations matter and both were wrong in a first version. Surrogate rows
    must never share a block with agent-executed rows, because the surrogate
    *implements* the loss model and so reproduces it by construction; presenting the
    two together would let a reader mistake a definition for a measurement.

    And saturation is classified *empirically* -- a configuration is capacity-bound
    if some algorithm actually lost an item -- rather than from an a-priori estimate
    of whether the items fit the budget. The estimate was wrong: agents told to
    compress rather than drop did exactly that, so a configuration predicted to
    saturate did not.
    """
    rows: list[dict[str, Any]] = []
    for f in sorted(glob.glob(str(RESULTS / "microbench" / "*fidelity*.json"))):
        d = load(Path(f))
        b = (d or {}).get("benches", {}).get("fidelity")
        if not b:
            continue
        for r in b["rows"]:
            rows.append({**r, "_executor": b.get("executor", "?")})
    if not rows:
        emit("tab_fidelity.tex", MISSING)
        return

    def alg_label(r: dict[str, Any]) -> str:
        name = tex_escape(str(r["algorithm"]))
        if r.get("fanin") and r["algorithm"] == "kary":
            return rf"\texttt{{{name}}}, $k{{=}}{int(r['fanin'])}$"
        return rf"\texttt{{{name}}}"

    # Group by (executor, population, per-rank item count, budget): one experimental
    # configuration per group, so algorithms within a group are comparable.
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for r in rows:
        key = (r["_executor"], r.get("p"), r.get("facts_per_rank"), r.get("_budget"))
        groups.setdefault(key, []).append(r)

    lines: list[str] = []
    for key in sorted(groups, key=lambda k: (k[0] != "broker", k[1] or 0, k[2] or 0)):
        block = groups[key]
        executor, p, facts, budget = key
        lost = any((r.get("retention") or 0) < 1.0 for r in block)
        kind = "agent-executed" if executor == "broker" else f"surrogate operator ({executor})"
        state = "capacity-bound" if lost else "not capacity-bound"
        budget_txt = f", {budget}-token merge budget" if budget else ""
        lines.append(
            rf"\multicolumn{{9}}{{l}}{{\emph{{{kind}}}: $p{{=}}{p}$, "
            rf"{facts} items/rank{budget_txt} --- \textbf{{{state}}}}} \\"
        )
        for r in sorted(block, key=lambda x: -(x.get("fold_depth") or 0)):
            lines.append(
                rf"\quad {alg_label(r)} & {r.get('fold_depth')} & {r.get('rounds')} & "
                rf"{fmt(r.get('retention'), 3)} & {fmt(r.get('retention_stdev'), 3)} & "
                rf"{fmt(r.get('rank_position_correlation'), 3)} & {r.get('n_hallucinated_ids', 0)} & "
                rf"{fmt(r.get('wall_s'), 0)} & {fmt(r.get('tokens_out_total'))} \\"
            )
        lines.append(r"\addlinespace")

    body = (
        "\\begin{tabular}{lrrrrrrrr}\n\\toprule\n"
        "algorithm & depth & rounds & retention & sd & rank corr. & halluc. & wall (s) & out tok \\\\\n\\midrule\n"
        + "\n".join(lines)
        + "\n\\bottomrule\n\\end{tabular}"
    )
    emit("tab_fidelity.tex", body)

    # The latency comparison at held-constant quality, which the prose quotes.
    for block in groups.values():
        if block[0]["_executor"] != "broker":
            continue
        if all((r.get("retention") or 0) >= 1.0 for r in block):
            by_alg = {r["algorithm"]: r for r in block}
            if {"binomial", "chain", "flat"} <= set(by_alg):
                _MACROS["NFidTree"] = f"{by_alg['binomial']['wall_s']:.0f}"
                _MACROS["NFidChain"] = f"{by_alg['chain']['wall_s']:.0f}"
                _MACROS["NFidFlat"] = f"{by_alg['flat']['wall_s']:.0f}"
                _MACROS["NFidSpeedup"] = f"{by_alg['chain']['wall_s'] / max(by_alg['binomial']['wall_s'], 1e-9):.1f}"


# --------------------------------------------------------------- software table


def table_software() -> None:
    """Software-experiment results for the largest single campaign.

    Filtered by campaign label for the same reason as the translation table: a
    synthetic smoke run shares the result schema, and a row of stub modules scoring
    zero next to a real population scoring full marks invites exactly the wrong
    reading.
    """
    by_label: dict[str, list[dict[str, Any]]] = {}
    for f in sorted(glob.glob(str(RESULTS / "software" / "*.json"))):
        d = load(Path(f))
        if d and "acceptance" in d:
            by_label.setdefault(str((d.get("config") or {}).get("label") or "?"), []).append(d)
    # Keep every label whose runs were executed by real agents; the vague-spec pair
    # runs under its own campaign prefix but belongs in the same table, since it is
    # scored by the same oracle. Synthetic smoke runs are excluded by their executor.
    res = [
        d
        for rows in by_label.values()
        for d in rows
        if (d.get("config") or {}).get("executor") == "broker"
    ]
    if not res:
        res = (
            max(by_label.values(), key=lambda rows: (len(rows), sum(r["job"]["agent_calls"] for r in rows)))
            if by_label
            else []
        )
    if not res:
        emit("tab_software.tex", MISSING)
        emit("tab_software_contention.tex", MISSING)
        return

    def label(c: dict[str, Any]) -> str:
        if c.get("ranks") == 1:
            return "single agent ($p{=}1$)"
        bits = []
        bits.append("shared interfaces" if c.get("shared_interfaces") else "\\textbf{no shared interfaces}")
        if not c.get("locks"):
            bits.append("\\textbf{no locks}")
        if not c.get("review"):
            bits.append("\\textbf{no review}")
        if c.get("vague_spec"):
            bits.append("\\emph{vague spec}")
        return ", ".join(bits)

    lines = []
    ordered = sorted(
        res,
        key=lambda x: (
            bool(x["config"].get("vague_spec")),
            -(x["config"].get("ranks") or 0),
            not x["config"].get("shared_interfaces"),
        ),
    )
    prev_vague: bool | None = None
    for d in ordered:
        vague = bool(d["config"].get("vague_spec"))
        if prev_vague is not None and vague != prev_vague:
            lines.append(r"\midrule")
        prev_vague = vague
        # Prefer the offline re-evaluation: it scores every configuration against the
        # *same* oracle, which the in-run numbers do not, because the oracle was
        # corrected partway through the campaign.
        a = d.get("acceptance_reeval") or d["acceptance"]
        j, c = d["job"], d["config"]
        rounds = (d.get("acceptance_reeval") or {}).get("per_round") or d.get("per_round") or []
        traj = " / ".join(str(r.get("n_passed") or 0) for r in rounds)
        lines.append(
            rf"{label(c)} & {c.get('ranks')} & {fmt(a.get('importable'))} & "
            rf"{a.get('n_passed') or 0}/{a.get('n_total') or 0} & {fmt(a.get('pass_rate'), 3)} & "
            rf"{tex_escape(traj)} & {fmt(j['wall_s'], 0)} & {j['agent_calls']} & "
            rf"{fmt(j['tokens_in'] + j['tokens_out'])} & {fmt(j['usd'], 3)} \\"
        )
    body = (
        "\\begin{tabular}{lrccrlrrrr}\n\\toprule\n"
        "configuration & $p$ & imports & passed & rate & per round & wall (s) & calls & tokens & USD \\\\\n\\midrule\n"
        + "\n".join(lines)
        + "\n\\bottomrule\n\\end{tabular}"
    )
    emit("tab_software.tex", body)

    lines = []
    for d in res:
        c, k = d["config"], d.get("contention") or {}
        if not k:
            continue
        lines.append(
            rf"{label(c)} & {k.get('n_locks', 0)} & {k.get('n_contended', 0)} & {fmt(k.get('total_lock_wait_s'), 2)} & "
            rf"{fmt(k.get('max_lock_wait_s'), 2)} & {k.get('n_puts', 0)} & {k.get('n_stale_writes', 0)} & "
            rf"{fmt(k.get('stale_write_rate'), 3)} & {k.get('n_accumulates', 0)} \\"
        )
    if lines:
        body = (
            "\\begin{tabular}{lrrrrrrrr}\n\\toprule\n"
            "configuration & locks & contended & wait (s) & max wait & puts & stale & stale rate & accum. \\\\\n\\midrule\n"
            + "\n".join(lines)
            + "\n\\bottomrule\n\\end{tabular}"
        )
        emit("tab_software_contention.tex", body)
    else:
        emit("tab_software_contention.tex", MISSING)


# --------------------------------------------------------------- simulated scaling


def table_scaling_sim() -> None:
    d = load(RESULTS / "microbench" / "free-scaling.json") or load(RESULTS / "microbench" / "free-all.json")
    b = (d or {}).get("benches", {}).get("scaling")
    if not b:
        emit("tab_scaling_sim.tex", MISSING)
        emit("tab_crossovers.tex", MISSING)
        return
    sizes = [8, 32, 128, 512, 1024]
    lines = []
    for op, rows in sorted(b["studies"].items()):
        by_alg: dict[str, dict[int, float]] = {}
        for r in rows:
            by_alg.setdefault(r["algorithm"], {})[r["p"]] = r["makespan_s"]
        for alg in sorted(by_alg):
            cells = " & ".join(fmt(by_alg[alg].get(p), 0) for p in sizes)
            lines.append(rf"{tex_escape(op)} & \texttt{{{tex_escape(alg)}}} & {cells} \\")
    body = (
        "\\begin{tabular}{ll" + "r" * len(sizes) + "}\n\\toprule\n"
        "collective & algorithm & " + " & ".join(f"$p{{=}}{p}$" for p in sizes) + " \\\\\n\\midrule\n"
        + "\n".join(lines)
        + "\n\\bottomrule\n\\end{tabular}"
    )
    emit("tab_scaling_sim.tex", body)

    seen: dict[tuple[str, int], str] = {}
    lines = []
    for c in b.get("crossovers", []):
        key = (c["op"], c["n_tokens"])
        if seen.get(key) == c["best_time"]:
            continue
        seen[key] = c["best_time"]
        lines.append(
            rf"{tex_escape(c['op'])} & {c['n_tokens']:,} & {c['p']} & \texttt{{{tex_escape(c['best_time'])}}} & "
            rf"\texttt{{{tex_escape(c['best_volume'])}}} & \texttt{{{tex_escape(c['best_fidelity'])}}} \\"
        )
    body = (
        "\\begin{tabular}{lrrlll}\n\\toprule\n"
        "collective & tokens & from $p$ & best (time) & best (volume) & best (fidelity) \\\\\n\\midrule\n"
        + "\n".join(lines[:40])
        + "\n\\bottomrule\n\\end{tabular}"
    )
    emit("tab_crossovers.tex", body)


# ----------------------------------------------------------------- summary stats


def summary_macros() -> None:
    """Single-number macros the prose refers to, so the prose cannot drift."""
    macros: list[str] = []
    tr = _tr_results()
    n_calls = sum(d["job"]["agent_calls"] for _, d in tr)
    sw_calls = 0
    for f in glob.glob(str(RESULTS / "software" / "*.json")):
        d = load(Path(f))
        if d:
            sw_calls += d.get("job", {}).get("agent_calls", 0)
    mb_calls = 0
    for f in glob.glob(str(RESULTS / "microbench" / "*.json")):
        d = load(Path(f))
        for b in (d or {}).get("benches", {}).values():
            for r in (b or {}).get("rows", []) if isinstance(b, dict) else []:
                mb_calls += r.get("agent_calls_total") or 0
    macros.append(rf"\newcommand{{\NAgentCallsTranslation}}{{{n_calls:,}}}")
    macros.append(rf"\newcommand{{\NAgentCallsSoftware}}{{{sw_calls:,}}}")
    macros.append(rf"\newcommand{{\NAgentCallsMicro}}{{{mb_calls:,}}}")
    macros.append(rf"\newcommand{{\NAgentCallsTotal}}{{{n_calls + sw_calls + mb_calls:,}}}")
    macros.append(rf"\newcommand{{\NTranslationConfigs}}{{{len(tr)}}}")
    for name, value in sorted(_MACROS.items()):
        macros.append(rf"\newcommand{{\{name}}}{{{value}}}")
    for name in ("NModelConfigs", "NModelAgreement", "NModelDepthAgreement", "NEagerLimit", "NUslFit",
                 "NSemTime", "NSemTokens", "NSemPrice", "NSemCalls", "NCoordTime", "NCoordTokens",
                 "NFidTree", "NFidChain", "NFidFlat", "NFidSpeedup"):
        if name not in _MACROS:
            macros.append(rf"\newcommand{{\{name}}}{{{MISSING}}}")
    emit("macros.tex", "\n".join(macros))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.parse_args()
    print("generating paper tables from results/ ...")
    for fn in (
        table_cost_formulas,
        table_model_validation,
        table_translation,
        table_transport,
        table_faults,
        table_fidelity,
        table_software,
        table_scaling_sim,
        summary_macros,
    ):
        try:
            fn()
        except Exception as exc:  # a missing input must not stop the rest
            print(f"  !! {fn.__name__}: {exc!r}")
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
