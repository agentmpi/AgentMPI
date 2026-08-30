#!/usr/bin/env python3
"""Generate the paper's figures from the recorded run data.

Everything here reads from ``experiments/results/`` and writes to
``paper/figures/``; no number in a figure is typed by hand.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "experiments" / "results"
FIGS = REPO / "paper" / "figures"
FIGS.mkdir(parents=True, exist_ok=True)

plt.rcParams.update({
    "font.size": 8,
    "axes.grid": True,
    "grid.alpha": 0.3,
    "grid.linewidth": 0.5,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "figure.dpi": 200,
    "savefig.bbox": "tight",
    "legend.frameon": False,
})

C = {"prefix": "#1f4e79", "chain": "#c0392b", "ring": "#e67e22",
     "tree": "#27ae60", "flat": "#7f8c8d", "budget": "#8e44ad"}


def load(name: str):
    path = RESULTS / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


# ---------------------------------------------------------------- figure 1
def fig_rounds(mb) -> None:
    """Rounds on the critical path: the quantity that sets wall-clock."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(6.6, 2.4))

    scan = mb["scan"]
    ps = [r["p"] for r in scan]
    ax1.plot(ps, [r["chain_rounds"] for r in scan], "o-", color=C["chain"],
             label="chain (sequential)", ms=3.5)
    ax1.plot(ps, [r["recursive_doubling_rounds"] for r in scan], "s-",
             color=C["prefix"], label="Hillis--Steele prefix", ms=3.5)
    ax1.set_xscale("log", base=2)
    ax1.set_yscale("log", base=2)
    ax1.set_xlabel("ranks $p$")
    ax1.set_ylabel("rounds on critical path")
    ax1.set_title("(a) exclusive scan", fontsize=8)
    ax1.legend(fontsize=7)

    ar = mb["allreduce"]
    ps2 = [r["p"] for r in ar]
    ax2.plot(ps2, [r.get("ring_rounds") for r in ar], "o-", color=C["ring"],
             label="ring", ms=3.5)
    ax2.plot(ps2, [r.get("recursive_doubling_rounds") for r in ar], "s-",
             color=C["prefix"], label="recursive doubling", ms=3.5)
    ax2.plot(ps2, [r.get("flat_rounds") for r in ar], "^-", color=C["flat"],
             label="reduce+bcast", ms=3.5)
    ax2.set_xscale("log", base=2)
    ax2.set_yscale("log", base=2)
    ax2.set_xlabel("ranks $p$")
    ax2.set_title("(b) allreduce", fontsize=8)
    ax2.legend(fontsize=7)

    fig.savefig(FIGS / "rounds.pdf")
    plt.close(fig)


# ---------------------------------------------------------------- figure 2
def fig_scan_speedup(mb) -> None:
    """Measured wall-clock advantage of the parallel prefix."""
    scan = mb["scan"]
    ps = [r["p"] for r in scan]
    fig, ax = plt.subplots(figsize=(3.3, 2.3))
    ax.plot(ps, [r["chain_collective_s"] for r in scan], "o-", color=C["chain"],
            label="chain", ms=3.5)
    ax.plot(ps, [r["recursive_doubling_collective_s"] for r in scan], "s-",
            color=C["prefix"], label="parallel prefix", ms=3.5)
    ax.set_xscale("log", base=2)
    ax.set_yscale("log")
    ax.set_xlabel("ranks $p$")
    ax.set_ylabel("collective time (s)")
    for r in scan:
        if r["p"] in (16, 64):
            ax.annotate(f"{r['wall_speedup']:.0f}$\\times$",
                        (r["p"], r["recursive_doubling_collective_s"]),
                        textcoords="offset points", xytext=(2, -12), fontsize=7,
                        color=C["prefix"])
    ax.legend(fontsize=7)
    fig.savefig(FIGS / "scan_speedup.pdf")
    plt.close(fig)


# ---------------------------------------------------------------- figure 3
def fig_context(mb) -> None:
    """The feasibility frontier: where each collective stops fitting."""
    ctx = mb["context"]
    ps = [r["p"] for r in ctx]
    budget = ctx[0]["budget_tokens"]
    fig, ax = plt.subplots(figsize=(3.3, 2.3))
    ax.plot(ps, [r["allgather_peak_tokens"] for r in ctx], "o-", color=C["ring"],
            label="allgather", ms=3.5)
    ax.plot(ps, [r["flat_reduce_peak_tokens"] for r in ctx], "^-", color=C["flat"],
            label="flat reduce", ms=3.5)
    ax.plot(ps, [r["tree_reduce_peak_tokens"] for r in ctx], "s-", color=C["tree"],
            label="capacity-aware tree", ms=3.5)
    ax.plot(ps, [r["bcast_peak_tokens"] for r in ctx], "d-", color=C["prefix"],
            label="broadcast", ms=3.5)
    ax.axhline(budget, color=C["budget"], ls="--", lw=1)
    ax.text(ps[0], budget * 1.15, "context budget", color=C["budget"], fontsize=7)
    ax.set_xscale("log", base=2)
    ax.set_yscale("log")
    ax.set_xlabel("ranks $p$")
    ax.set_ylabel("peak ingest per rank (tokens)")
    ax.legend(fontsize=6.5, loc="upper left")
    fig.savefig(FIGS / "context_frontier.pdf")
    plt.close(fig)


# ---------------------------------------------------------------- figure 4
def fig_fanout(mb) -> None:
    """Predicted bound versus measured cumulative ingest."""
    fo = mb["fanout"]
    ps = [r["p"] for r in fo]
    x = range(len(ps))
    fig, ax = plt.subplots(figsize=(3.3, 2.3))
    w = 0.38
    ax.bar([i - w / 2 for i in x], [r["predicted_peak_tokens"] for r in fo],
           w, label="predicted bound", color=C["prefix"], alpha=0.85)
    ax.bar([i + w / 2 for i in x], [r["measured_peak_tokens"] for r in fo],
           w, label="measured", color=C["tree"], alpha=0.85)
    ax.axhline(fo[0]["budget"], color=C["budget"], ls="--", lw=1)
    ax.set_xticks(list(x))
    ax.set_xticklabels([f"{p}\n$k$={r['predicted_fanout']}, $d$={r['predicted_rounds']}"
                        for p, r in zip(ps, fo)], fontsize=6)
    ax.set_xlabel("ranks $p$")
    ax.set_ylabel("root ingest (tokens)")
    ax.legend(fontsize=7)
    fig.savefig(FIGS / "fanout.pdf")
    plt.close(fig)


# ---------------------------------------------------------------- figure 5
def fig_translation(tr) -> None:
    """Terminology consistency, with and without the prefix scan."""
    arms = [a for a in tr if a is not None]
    if not arms:
        return
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(6.6, 2.3))
    labels = [a["label"] for a in arms]
    colours = [C["chain"] if "no coordination" in a["label"] else C["prefix"]
               for a in arms]
    vals = [a["declared_consistency"] for a in arms]
    ax1.bar(range(len(arms)), vals, color=colours, alpha=0.85, width=0.55)
    for i, v in enumerate(vals):
        ax1.text(i, v + 0.02, f"{v:.2f}", ha="center", fontsize=7.5)
    ax1.set_xticks(range(len(arms)))
    ax1.set_xticklabels(labels, fontsize=7)
    ax1.set_ylim(0, 1.15)
    ax1.set_ylabel("terminology consistency")
    ax1.set_title("(a) quality", fontsize=8)

    ax2.bar(range(len(arms)), [a["inconsistent_terms"] for a in arms],
            color=colours, alpha=0.85, width=0.55)
    ax2.set_xticks(range(len(arms)))
    ax2.set_xticklabels(labels, fontsize=7)
    ax2.set_ylabel("terms rendered inconsistently")
    ax2.set_title("(b) failures", fontsize=8)
    fig.savefig(FIGS / "translation.pdf")
    plt.close(fig)


def main() -> int:
    mb = load("microbench.json")
    if mb:
        fig_rounds(mb)
        fig_scan_speedup(mb)
        fig_context(mb)
        fig_fanout(mb)
        print("wrote rounds.pdf scan_speedup.pdf context_frontier.pdf fanout.pdf")
    tr = load("translation_summary.json")
    if tr:
        fig_translation(tr["arms"])
        print("wrote translation.pdf")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
