#!/usr/bin/env python3
"""Generate the paper's figures from the measurement JSON.

Every figure is produced from a results file, never by hand, so that a rerun of
the benchmarks regenerates the paper. Style is deliberately plain: no colour
required to read any figure, no chartjunk, and axis labels that state units.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

plt.rcParams.update(
    {
        "figure.dpi": 150,
        "savefig.dpi": 150,
        "font.size": 9,
        "axes.titlesize": 9.5,
        "axes.labelsize": 9,
        "legend.fontsize": 8,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "axes.grid": True,
        "grid.alpha": 0.25,
        "grid.linewidth": 0.5,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "lines.linewidth": 1.4,
        "lines.markersize": 4,
        "figure.autolayout": True,
    }
)

MARKS = ["o", "s", "^", "v", "D", "x", "P", "*"]


def _save(fig: plt.Figure, out: Path, name: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for ext in ("pdf", "png"):
        fig.savefig(out / f"{name}.{ext}", bbox_inches="tight")
    plt.close(fig)
    print(f"  {name}.pdf")


# --------------------------------------------------------------------------


def fig_latency(mb: Dict[str, Any], out: Path) -> None:
    rows = mb["suites"]["latency"]["rows"]
    fit = mb["suites"]["latency"]["fit_eager"]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.0, 2.6))
    for i, mode in enumerate(("eager", "rendezvous")):
        xs = [r["payload_tokens"] for r in rows if r["delivery"] == mode]
        ys = [r["latency_s"]["p50"] * 1e3 for r in rows if r["delivery"] == mode]
        ax1.plot(xs, ys, marker=MARKS[i], label=mode)
    if fit.get("alpha_s") is not None:
        xs = sorted({r["payload_tokens"] for r in rows})
        ax1.plot(
            xs,
            [(fit["alpha_s"] + fit["beta_s_per_token"] * x) * 1e3 for x in xs],
            "k--", lw=0.9,
            label=rf"$\alpha+\beta n$: $\alpha$={fit['alpha_s']*1e3:.1f} ms, "
                  rf"$\beta$={fit['beta_s_per_token']*1e6:.2f} $\mu$s/tok",
        )
    ax1.set_xscale("log")
    ax1.set_xlabel("payload (tokens)")
    ax1.set_ylabel("half round-trip (ms)")
    ax1.set_title("(a) control-plane latency")
    ax1.legend(frameon=False)

    for i, mode in enumerate(("eager", "rendezvous")):
        xs = [r["payload_tokens"] for r in rows if r["delivery"] == mode]
        ys = [r["context_charged_per_msg"]["p50"] for r in rows if r["delivery"] == mode]
        ax2.plot(xs, ys, marker=MARKS[i], label=mode)
    ax2.set_xscale("log")
    ax2.set_yscale("log")
    ax2.set_xlabel("payload (tokens)")
    ax2.set_ylabel("context charged per message (tokens)")
    ax2.set_title("(b) what delivery actually costs")
    ax2.legend(frameon=False)
    _save(fig, out, "fig_latency")


def fig_collective_volume(mb: Dict[str, Any], out: Path) -> None:
    rows = mb["suites"]["collectives"]["rows"]
    groups = {
        "(a) allreduce": [("flat", "flat (journal fold)"),
                          ("reduce_bcast", "reduce+bcast"),
                          ("recursive_doubling", "recursive doubling")],
        "(b) allgather": [("flat", "flat (handles)"),
                          ("recursive_doubling", "recursive doubling"),
                          ("ring", "ring")],
        "(c) barrier": [("central", "central (journal)"),
                        ("linear", "linear"),
                        ("dissemination", "dissemination")],
    }
    op_of = {"(a) allreduce": "allreduce", "(b) allgather": "allgather", "(c) barrier": "barrier"}
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.5))
    for ax, (title, algos) in zip(axes, groups.items()):
        op = op_of[title]
        for i, (algo, label) in enumerate(algos):
            pts = sorted(
                (r["P"], r["message_tokens"] if op != "barrier" else r["messages"])
                for r in rows if r["op"] == op and r["algo"] == algo
            )
            if not pts:
                continue
            xs = [p for p, _ in pts]
            ys = [max(v, 0.5) for _, v in pts]
            ax.plot(xs, ys, marker=MARKS[i], label=label)
        ax.set_xscale("log", base=2)
        ax.set_yscale("log")
        ax.set_xlabel("ranks $P$")
        ax.set_title(title)
        ax.legend(frameon=False, loc="upper left")
    axes[0].set_ylabel("payload tokens moved")
    axes[2].set_ylabel("messages")
    _save(fig, out, "fig_collective_volume")


def fig_agent_reduce(mb: Dict[str, Any], out: Path) -> None:
    rows = mb["suites"]["scaling"].get("agent_reduce") or []
    if not rows:
        return
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.0, 2.6))
    for i, algo in enumerate(sorted({r["algo"] for r in rows})):
        pts = sorted((r["P"], r["effective_serial_depth"] or 0) for r in rows if r["algo"] == algo)
        ax1.plot([p for p, _ in pts], [v for _, v in pts], marker=MARKS[i], label=algo)
        pts2 = sorted((r["P"], r["wall_s"]) for r in rows if r["algo"] == algo)
        ax2.plot([p for p, _ in pts2], [v for _, v in pts2], marker=MARKS[i], label=algo)
    ps = sorted({r["P"] for r in rows})
    ax1.plot(ps, [math.ceil(math.log2(p)) for p in ps], "k--", lw=0.9,
             label=r"$\lceil \log_2 P \rceil$")
    ax1.plot(ps, [p - 1 for p in ps], "k:", lw=0.9, label=r"$P-1$")
    ax1.set_xscale("log", base=2)
    ax1.set_yscale("log")
    ax1.set_xlabel("ranks $P$")
    ax1.set_ylabel("effective serialised operator applications")
    ax1.set_title("(a) critical path, agent operator")
    ax1.legend(frameon=False, loc="upper left")
    ax2.set_xscale("log", base=2)
    ax2.set_xlabel("ranks $P$")
    ax2.set_ylabel("makespan (s)")
    ax2.set_title(f"(b) makespan, {rows[0]['merge_cost_s']}s per application")
    ax2.legend(frameon=False)
    _save(fig, out, "fig_agent_reduce")


def fig_context(mb: Dict[str, Any], out: Path) -> None:
    rows = mb["suites"]["context"]["rows"]
    order = ["inline", "handle", "view400"]
    labels = {"inline": "inline bodies", "handle": "handles only", "view400": "400-token views"}
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.0, 2.6))
    width = 0.35
    for k, op in enumerate(("bcast", "allgather")):
        vals = [next((r["total_context_tokens"] for r in rows
                      if r["op"] == op and r["discipline"] == d), 0) for d in order]
        peak = [next((r["per_rank_hwm"]["max"] or 0 for r in rows
                      if r["op"] == op and r["discipline"] == d), 0) for d in order]
        xs = [i + (k - 0.5) * width for i in range(len(order))]
        ax1.bar(xs, vals, width, label=op, edgecolor="black", linewidth=0.4,
                color=("0.35" if k == 0 else "0.75"))
        ax2.bar(xs, peak, width, label=op, edgecolor="black", linewidth=0.4,
                color=("0.35" if k == 0 else "0.75"))
    for ax, ttl, yl in ((ax1, "(a) total tokens into all contexts", "tokens"),
                        (ax2, "(b) peak tokens in one context", "tokens")):
        ax.set_xticks(range(len(order)))
        ax.set_xticklabels([labels[d] for d in order])
        ax.set_yscale("log")
        ax.set_ylabel(yl)
        ax.set_title(ttl)
        ax.legend(frameon=False)
    p = mb["suites"]["context"]["P"]
    ax2.axhline(200_000, color="black", ls="--", lw=0.9)
    ax2.text(2.3, 220_000, "200k context window", ha="right", fontsize=7)
    fig.suptitle(f"Context cost of collectives at $P$={p}, 4000-token payloads", fontsize=9.5)
    _save(fig, out, "fig_context")


def fig_matching(mb: Dict[str, Any], out: Path) -> None:
    rows = mb["suites"]["matching"]["rows"]
    fig, ax = plt.subplots(figsize=(3.5, 2.4))
    xs = [max(1, r["queue_depth"]) for r in rows]
    ax.plot(xs, [r["recv_s"]["p50"] * 1e3 for r in rows], marker="o", label="p50")
    ax.plot(xs, [r["recv_s"]["p90"] * 1e3 for r in rows], marker="s", label="p90")
    ax.set_xscale("log")
    ax.set_xlabel("unexpected-queue depth (messages)")
    ax.set_ylabel("receive latency (ms)")
    ax.set_ylim(bottom=0)
    ax.set_title("Matching cost vs queue depth")
    ax.legend(frameon=False)
    _save(fig, out, "fig_matching")


def fig_barrier_scaling(mb: Dict[str, Any], out: Path) -> None:
    rows = mb["suites"]["scaling"]["rows"]
    fig, ax = plt.subplots(figsize=(3.5, 2.4))
    for i, (op, algo, label) in enumerate((
        ("barrier", "central", "barrier: central (journal)"),
        ("barrier", "dissemination", "barrier: dissemination"),
        ("allreduce", "flat", "allreduce: flat"),
    )):
        pts = sorted((r["P"], r["wall_s"]) for r in rows
                     if r["op"] == op and r["algo"] == algo)
        if pts:
            ax.plot([p for p, _ in pts], [v for _, v in pts], marker=MARKS[i], label=label)
    ax.set_xscale("log", base=2)
    ax.set_yscale("log")
    ax.set_xlabel("ranks $P$")
    ax.set_ylabel("makespan (s)")
    ax.set_title("Control collectives, stub executors")
    ax.legend(frameon=False, loc="upper left")
    _save(fig, out, "fig_barrier_scaling")


# --------------------------------------------------------------------------


def fig_e1(e1: Optional[Dict[str, Any]], out: Path) -> None:
    if not e1 or "arms" not in e1:
        return
    arms = e1["arms"]
    names = [n for n in ("naive", "ampi") if n in arms]
    if len(names) < 1:
        return
    fig, axes = plt.subplots(1, 3, figsize=(7.4, 2.5))
    label = {"naive": "no protocol", "ampi": "AgentMPI"}

    ax = axes[0]
    vals = [arms[n]["terminology"]["mean_modal_share"] or 0 for n in names]
    ax.bar(range(len(names)), vals, 0.55, color=["0.75", "0.35"][: len(names)],
           edgecolor="black", linewidth=0.4)
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels([label[n] for n in names])
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("mean modal share")
    ax.set_title("(a) terminology consistency")
    for i, v in enumerate(vals):
        ax.text(i, v + 0.02, f"{v:.2f}", ha="center", fontsize=8)

    ax = axes[1]
    vals = [arms[n]["terminology"]["mean_distinct_renderings"] or 0 for n in names]
    ax.bar(range(len(names)), vals, 0.55, color=["0.75", "0.35"][: len(names)],
           edgecolor="black", linewidth=0.4)
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels([label[n] for n in names])
    ax.set_ylabel("distinct renderings per term")
    ax.set_title("(b) terminology divergence")
    for i, v in enumerate(vals):
        ax.text(i, v + 0.03, f"{v:.2f}", ha="center", fontsize=8)

    ax = axes[2]
    vals = [arms[n]["protocol"]["context"]["hwm"]["max"] or 0 for n in names]
    ax.bar(range(len(names)), vals, 0.55, color=["0.75", "0.35"][: len(names)],
           edgecolor="black", linewidth=0.4)
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels([label[n] for n in names])
    ax.set_yscale("log")
    ax.set_ylabel("peak context in one rank (tokens)")
    ax.set_title("(c) context high-water mark")
    _save(fig, out, "fig_e1")


def fig_e2(e2: Optional[Dict[str, Any]], out: Path) -> None:
    if not e2 or "arms" not in e2:
        return
    arms = e2["arms"]
    names = [n for n in ("naive", "ampi") if n in arms]
    if not names:
        return
    cats = sorted({c for n in names for c in arms[n]["by_category"]})
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.4, 2.6),
                                   gridspec_kw={"width_ratios": [1, 2.4]})
    label = {"naive": "no protocol", "ampi": "AgentMPI"}
    vals = [arms[n]["pass_rate"] for n in names]
    ax1.bar(range(len(names)), vals, 0.55, color=["0.75", "0.35"][: len(names)],
            edgecolor="black", linewidth=0.4)
    ax1.set_xticks(range(len(names)))
    ax1.set_xticklabels([label[n] for n in names])
    ax1.set_ylim(0, 1.05)
    ax1.set_ylabel("held-out pass rate")
    ax1.set_title("(a) overall")
    for i, v in enumerate(vals):
        ax1.text(i, v + 0.02, f"{v:.2f}", ha="center", fontsize=8)

    width = 0.8 / max(1, len(names))
    for k, n in enumerate(names):
        ys = [arms[n]["by_category"].get(c, {}).get("pass_rate", 0) for c in cats]
        xs = [i + k * width - 0.4 + width / 2 for i in range(len(cats))]
        ax2.bar(xs, ys, width, label=label[n], edgecolor="black", linewidth=0.4,
                color=["0.75", "0.35"][k])
    ax2.set_xticks(range(len(cats)))
    ax2.set_xticklabels(cats, rotation=30, ha="right")
    ax2.set_ylim(0, 1.05)
    ax2.set_ylabel("pass rate")
    ax2.set_title("(b) by specification area")
    ax2.legend(frameon=False)
    _save(fig, out, "fig_e2")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--microbench", default="results/microbench.json")
    ap.add_argument("--e1", default="results/e1_metrics.json")
    ap.add_argument("--e2", default="results/e2_grade.json")
    ap.add_argument("--out", default="paper/figures")
    args = ap.parse_args()
    out = Path(args.out)
    print("figures:")
    mb_path = Path(args.microbench)
    if mb_path.exists():
        mb = json.loads(mb_path.read_text())
        suites = mb.get("suites", {})
        if "latency" in suites:
            fig_latency(mb, out)
        if "collectives" in suites:
            fig_collective_volume(mb, out)
        if "scaling" in suites:
            fig_agent_reduce(mb, out)
            fig_barrier_scaling(mb, out)
        if "context" in suites:
            fig_context(mb, out)
        if "matching" in suites:
            fig_matching(mb, out)
    else:
        print(f"  (no {mb_path})")
    for path, fn in ((Path(args.e1), fig_e1), (Path(args.e2), fig_e2)):
        if path.exists():
            fn(json.loads(path.read_text()), out)
        else:
            print(f"  (no {path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
