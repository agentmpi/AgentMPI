"""Generate the paper's figures from the measured result files.

Every figure reads a JSON file produced by an experiment script; none of them
recompute anything, so a figure and the number quoted for it in the text cannot
drift apart.
"""

from __future__ import annotations

import argparse
import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "..", "results")
FIGDIR = os.path.join(HERE, "..", "..", "paper", "figures")

plt.rcParams.update({
    "font.size": 8,
    "axes.labelsize": 8,
    "axes.titlesize": 8.5,
    "legend.fontsize": 7,
    "xtick.labelsize": 7,
    "ytick.labelsize": 7,
    "figure.dpi": 200,
    "savefig.bbox": "tight",
    "axes.grid": True,
    "grid.alpha": 0.25,
    "grid.linewidth": 0.4,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "lines.linewidth": 1.3,
    "lines.markersize": 4,
})

MARKERS = ["o", "s", "^", "D", "v", "P", "X", "*"]
COLORS = ["#1f4e79", "#c0504d", "#2e7d32", "#7b4397", "#d68910", "#00838f", "#5d4037"]


def _load(name: str) -> dict:
    with open(os.path.join(RESULTS, name), encoding="utf-8") as fh:
        return json.load(fh)


def _save(fig, name: str) -> str:
    os.makedirs(FIGDIR, exist_ok=True)
    path = os.path.join(FIGDIR, name)
    fig.savefig(path)
    plt.close(fig)
    print(f"  wrote {os.path.relpath(path, os.path.join(HERE, '..', '..'))}")
    return path


# ---------------------------------------------------------------------------


def fig_residency(data: dict) -> None:
    """Peak resident tokens as a fraction of the vector, against p.

    The feasibility figure: the reduce-scatter family is the only one whose
    residency falls with the communicator size, so it is the only one that can
    reduce a vector larger than a single agent's context window.
    """
    rows = data["residency"]
    if not rows:
        return
    entries = sorted({r["entries_per_rank"] for r in rows})[-1]
    rows = [r for r in rows if r["entries_per_rank"] == entries]
    ps = sorted({r["p"] for r in rows})
    algos = sorted({a for r in rows for a in r["peak_over_n"]})

    pretty = {
        "allreduce/recursive_doubling": "allreduce, recursive doubling",
        "allreduce/binomial": "allreduce, binomial tree",
        "allreduce/linear": "allreduce, linear",
        "reduce_scatter/recursive_doubling": "reduce-scatter, recursive halving",
        "reduce_scatter/ring": "reduce-scatter, ring",
    }
    order = ["allreduce/recursive_doubling", "allreduce/binomial", "allreduce/linear",
             "reduce_scatter/ring", "reduce_scatter/recursive_doubling"]
    algos = [a for a in order if a in algos] + [a for a in algos if a not in order]

    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(7.0, 2.7))
    fig.subplots_adjust(wspace=0.34)
    # Recursive doubling and the binomial tree have identical residency, so one
    # would hide the other entirely; dashing makes the coincidence visible
    # rather than looking like a missing series.
    styles = ["-", "--", "-", "-", "--"]
    for i, algo in enumerate(algos):
        ys = [next((r["peak_over_n"][algo] for r in rows if r["p"] == p), None) for p in ps]
        ax.plot(ps, ys, styles[i % len(styles)], marker=MARKERS[i % len(MARKERS)],
                color=COLORS[i % len(COLORS)], label=pretty.get(algo, algo),
                markerfacecolor="none" if styles[i % len(styles)] == "--" else None)
    ax.plot(ps, [1.5 / p for p in ps], "k:", linewidth=1.0, label=r"$\propto 1/p$")
    ax.set_xscale("log", base=2)
    ax.set_yscale("log")
    ax.set_xticks(ps)
    ax.set_xticklabels([str(p) for p in ps])
    ax.set_xlabel("communicator size $p$")
    ax.set_ylabel(r"peak resident tokens $/\, n$")
    ax.set_title("(a) residency vs. communicator size", pad=6)

    limit = 128_000
    for i, algo in enumerate(algos):
        pts = sorted([r for r in rows], key=lambda r: r["p"])
        ax2.plot([r["p"] for r in pts], [r["peak_resident_tokens"][algo] for r in pts],
                 styles[i % len(styles)], marker=MARKERS[i % len(MARKERS)],
                 color=COLORS[i % len(COLORS)],
                 markerfacecolor="none" if styles[i % len(styles)] == "--" else None)
    ax2.axhline(limit, color="crimson", linestyle="--", linewidth=1.0)
    ax2.text(0.02, 0.60, "128k-token context window", color="crimson", fontsize=6.5,
             ha="left", va="bottom", transform=ax2.transAxes)
    ax2.set_xscale("log", base=2)
    ax2.set_yscale("log")
    ax2.set_xticks(ps)
    ax2.set_xticklabels([str(p) for p in ps])
    ax2.set_xlabel("communicator size $p$")
    ax2.set_ylabel("peak resident tokens")
    ax2.set_title(f"(b) absolute residency, {rows[0]['entries_per_rank']} entries/rank", pad=6)

    handles, labels = ax.get_legend_handles_labels()
    fig.legend(handles, labels, frameon=False, ncol=3, loc="lower center",
               bbox_to_anchor=(0.5, -0.20), columnspacing=1.4, handlelength=1.8)
    _save(fig, "residency.pdf")


def fig_collectives(data: dict) -> None:
    """Barrier and allreduce latency against communicator size."""
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(7.0, 2.5))
    fig.subplots_adjust(wspace=0.30)

    algos = sorted({r["algo"] for r in data["barrier"]})
    for i, algo in enumerate(algos):
        rows = sorted([r for r in data["barrier"] if r["algo"] == algo], key=lambda r: r["p"])
        ax.plot([r["p"] for r in rows], [r["latency_ms"]["p50"] for r in rows],
                marker=MARKERS[i], color=COLORS[i],
                label=f"{algo} ({rows[-1]['steps_per_rank']} steps at $p$={rows[-1]['p']})")
    ax.set_xscale("log", base=2)
    ax.set_yscale("log")
    ax.set_xlabel("communicator size $p$")
    ax.set_ylabel("barrier latency (ms)")
    ax.set_title("(a) barrier", pad=6)
    ax.legend(frameon=False, loc="upper left", fontsize=6.5)

    algos = sorted({r["algo"] for r in data["allreduce"]})
    for i, algo in enumerate(algos):
        rows = sorted([r for r in data["allreduce"] if r["algo"] == algo], key=lambda r: r["p"])
        ax2.plot([r["p"] for r in rows], [r["latency_ms"]["p50"] for r in rows],
                 marker=MARKERS[i % len(MARKERS)], color=COLORS[i % len(COLORS)], label=algo)
    ax2.set_xscale("log", base=2)
    ax2.set_yscale("log")
    ax2.set_xlabel("communicator size $p$")
    ax2.set_ylabel("allreduce latency (ms)")
    ax2.set_title("(b) allreduce, 64-entry vector per rank", pad=6)
    ax2.legend(frameon=False, ncol=2, loc="upper left", fontsize=6.5, columnspacing=1.0)
    _save(fig, "collectives.pdf")


def fig_pingpong(data: dict) -> None:
    """Half round-trip against payload, with the fitted alpha-beta line."""
    rows = sorted(data["pingpong"], key=lambda r: r["payload_tokens"])
    fit = data.get("alpha_beta_fit", {})
    fig, ax = plt.subplots(figsize=(3.3, 2.3))
    xs = [max(1, r["payload_tokens"]) for r in rows]
    ys = [r["half_roundtrip_ms"]["p50"] for r in rows]
    points = list(zip(xs, ys, rows, strict=True))
    eager = [(x, y) for x, y, row in points if row["mode"] == "eager"]
    rend = [(x, y) for x, y, row in points if row["mode"] != "eager"]
    if eager:
        ax.plot(
            *zip(*eager, strict=True),
            "o-",
            color=COLORS[0],
            label="eager (inline)",
        )
    if rend:
        ax.plot(
            *zip(*rend, strict=True),
            "s-",
            color=COLORS[1],
            label="rendezvous (by handle)",
        )
    if fit:
        model = [(fit["alpha_seconds"] + fit["beta_seconds_per_token"] * x) * 1000 for x in xs]
        ax.plot(xs, model, "k:", linewidth=1.0,
                label=rf"$\alpha+\beta n$, $\alpha$={fit['alpha_seconds']*1000:.2f} ms")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("payload (tokens)")
    ax.set_ylabel("half round-trip (ms)")
    ax.set_title("point-to-point latency")
    ax.legend(frameon=False, loc="upper left")
    _save(fig, "pingpong.pdf")


def fig_contention(data: dict) -> None:
    """Compare-and-swap retries against the number of contending ranks."""
    rows = sorted(data["window"], key=lambda r: r["p"])
    fig, ax = plt.subplots(figsize=(3.3, 2.3))
    ps = [r["p"] for r in rows]
    retries = [r["cas_retries"] for r in rows]
    ax.plot(ps, retries, "o-", color=COLORS[1], label="observed retries")
    iters = rows[0]["iterations"]
    ax.plot(ps, [iters * p * (p - 1) / 2 / max(1, p) for p in ps], "k:", linewidth=1.0,
            label="linear-in-$p$ reference")
    ax.set_xscale("log", base=2)
    ax.set_xticks(ps)
    ax.set_xticklabels([str(p) for p in ps])
    ax.set_xlabel("contending ranks $p$")
    ax.set_ylabel("failed compare-and-swaps")
    ax.set_title("contention on one shared cell")
    ax.legend(frameon=False, loc="upper left")
    _save(fig, "contention.pdf")


def fig_semantic(path: str) -> None:
    """Linear versus tree schedule for a semantic reduction, with real agents."""
    if not os.path.exists(path):
        print(f"  (skipping semantic figure: {path} not found)")
        return
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    configs = [c for c in data.get("configurations", []) if c.get("upcalls_total")]
    if not configs:
        print("  (skipping semantic figure: no completed configurations)")
        return
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(6.6, 2.4))
    labels = [c["algo"] for c in configs]
    x = range(len(labels))
    ax.bar([i - 0.2 for i in x], [c["upcalls_total"] for c in configs], width=0.4,
           color=COLORS[0], label="operator evaluations, total")
    ax.bar([i + 0.2 for i in x], [c["upcalls_critical_path"] for c in configs], width=0.4,
           color=COLORS[1], label="on the critical path")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_ylabel("model evaluations")
    ax.set_title(f"(a) semantic reduction, $p={configs[0].get('p', '?')}$")
    ax.legend(frameon=False)

    ax2.bar([i for i in x], [c.get("wall_seconds") or 0 for c in configs], width=0.5,
            color=COLORS[2])
    ax2.set_xticks(list(x))
    ax2.set_xticklabels(labels)
    ax2.set_ylabel("wall clock (s)")
    ax2.set_title("(b) end-to-end time with real agents")
    _save(fig, "semantic.pdf")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--microbench", default="ampi_microbench.json")
    parser.add_argument("--semantic", default=os.path.join(RESULTS, "ampi_e2_semantic.json"))
    args = parser.parse_args()

    print("figures:")
    micro = _load(args.microbench)
    fig_residency(micro)
    fig_collectives(micro)
    fig_pingpong(micro)
    fig_contention(micro)
    fig_semantic(args.semantic)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
