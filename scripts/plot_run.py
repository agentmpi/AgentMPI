"""Figures for one AgentMPI run, drawn in the trace viewer's own visual language.

These figures go into analysis documents that a reader consults alongside the dashboard, so
they deliberately reuse the viewer's palette, glyph conventions, and dark panel styling from
``agentmpi.trace_style``. A figure that looked like it came from a different tool would make
the reader re-learn the encoding on every page, and worse, invite them to assume a difference
in meaning where there is none.

Five figures, each answering a question the others cannot:

``timeline``
    The Jumpshot-style Gantt: one lane per rank, work as bars, messages as ticks, window and
    fault events as diamonds. This is where a message-passing bug shows itself, because such
    bugs are almost always a *shape* --- a rank idle while its peers work, a fan-in serialising
    at a root, a barrier whose last arrival is minutes after its first.

``concurrency``
    How many ranks were simultaneously busy. A run can register sixteen ranks and never exceed
    two concurrently, and no scalar summary distinguishes that from real parallelism.

``comm``
    The communication matrix. Makes the algorithm's shape visible --- a binomial tree, a ring, a
    root that everyone talks to --- and makes an unintended all-to-all impossible to miss.

``ranks``
    Per-rank occupancy against context budget, sorted, so imbalance and context pressure are
    legible as a distribution rather than as a maximum.

``collectives``
    Measured message counts against the closed-form prediction per invocation, which is where
    the implementation and the cost model are held to each other.

Figures are only produced when the run has the data for them: a collective-validation sweep
has no agent work to draw, and an empty axis is worse than an absent figure because it implies
something was looked for and found to be zero.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.patches as mpatches  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import MaxNLocator  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agentmpi import trace_style as ts  # noqa: E402
from agentmpi.analysis import Analysis  # noqa: E402

LANE_H = 0.62


def _style_axes(ax: Any, *, grid_axis: str = "both") -> None:
    ax.set_facecolor(ts.PANEL)
    for spine in ax.spines.values():
        spine.set_color(ts.LINE)
    ax.tick_params(colors=ts.MUTED, labelsize=8, which="both")
    ax.xaxis.label.set_color(ts.MUTED)
    ax.yaxis.label.set_color(ts.MUTED)
    ax.title.set_color(ts.FOREGROUND)
    ax.grid(True, axis=grid_axis, color=ts.LINE, linewidth=0.5, alpha=0.55)
    ax.set_axisbelow(True)


def _figure(width: float, height: float) -> tuple[Any, Any]:
    fig, ax = plt.subplots(figsize=(width, height))
    fig.patch.set_facecolor(ts.BACKGROUND)
    _style_axes(ax)
    return fig, ax


def _save(fig: Any, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # `facecolor` must be passed explicitly: savefig otherwise reverts to white and the dark
    # styling silently applies to everything except the margins.
    fig.savefig(path, dpi=200, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


# --------------------------------------------------------------------------------------


def timeline(a: Analysis, out: Path, *, max_ticks: int = 4000) -> Path | None:
    """One lane per rank: work as bars, instants as ticks and diamonds."""
    ranks = a.participating_ranks or sorted(a.ranks)
    if not ranks:
        return None
    index = {r: i for i, r in enumerate(ranks)}

    height = max(2.0, 0.34 * len(ranks) + 1.0)
    fig, ax = _figure(9.2, height)

    for i in range(len(ranks)):
        ax.add_patch(
            mpatches.Rectangle(
                (0, i - LANE_H / 2), a.wall_s or 1.0, LANE_H,
                facecolor=ts.PANEL_ALT, edgecolor="none", zorder=0,
            )
        )

    work_style = ts.style_for("work")
    for rank, start, end, _label in a.work_spans:
        if rank not in index:
            continue
        ax.add_patch(
            mpatches.Rectangle(
                (start, index[rank] - LANE_H / 2 + 0.06), max(end - start, a.wall_s * 0.0008), LANE_H - 0.12,
                facecolor=work_style.color, edgecolor="none", alpha=0.9, zorder=2,
            )
        )

    # Instants are drawn from the event log rather than from spans so the fault and lifecycle
    # events appear even on runs that did no work at all -- which is exactly the fault-injection case.
    drawn = 0
    roles_present = {"work"} if a.work_spans else set()
    for rank, prof in a.ranks.items():
        if rank not in index:
            continue
        for kind in prof.kinds:
            roles_present.add(ts.role_of(kind))
    for event_rank, t, kind in _instants(a):
        if event_rank not in index or drawn > max_ticks:
            continue
        style = ts.style_for(kind)
        if style.glyph == "bar":
            continue
        y = index[event_rank]
        if style.glyph == "tick":
            ax.plot([t, t], [y - LANE_H / 2 + 0.1, y + LANE_H / 2 - 0.1],
                    color=style.color, linewidth=0.7, alpha=0.85, zorder=3)
        else:
            ax.plot([t], [y], marker="D", markersize=3.4, color=style.color,
                    markeredgewidth=0, zorder=4)
        drawn += 1

    ax.set_ylim(-0.7, len(ranks) - 0.3)
    ax.set_xlim(0, max(a.wall_s, 1e-6))
    ax.set_yticks(range(len(ranks)))
    ax.set_yticklabels([f"rank {r}" for r in ranks], fontsize=7.5)
    ax.invert_yaxis()
    ax.set_xlabel("seconds from first event")
    ax.grid(True, axis="x", color=ts.LINE, linewidth=0.5, alpha=0.55)
    ax.grid(False, axis="y")

    handles = [
        mpatches.Patch(color=ts.ROLE_COLOR[role], label=ts.ROLE_LABEL[role])
        for role in ts.ROLE_ORDER
        if role in roles_present
    ]
    if handles:
        legend = ax.legend(
            handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.16 - 0.02 * (len(ranks) > 12)),
            ncol=min(len(handles), 3), frameon=False, fontsize=7.5,
        )
        for text in legend.get_texts():
            text.set_color(ts.MUTED)
    return _save(fig, out)


def _instants(a: Analysis) -> list[tuple[int, float, str]]:
    """Instant events per rank: everything that is a moment rather than a duration.

    Collective, job, and communicator-setup events are excluded because they are already
    summarised in their own figure and table; drawing them here would bury the messages and
    faults under a wall of ticks that carries no per-rank information.
    """
    out = []
    for e in a.events:
        rank = e.get("rank")
        if rank is None:
            continue
        kind = e["kind"]
        if kind in ("broker.claim", "broker.complete") or kind.startswith(("coll.", "job.", "comm.", "topo.")):
            continue
        if kind not in ts.STYLE:
            continue
        out.append((int(rank), e["ts"] - a.t0, kind))
    return out


def concurrency(a: Analysis, out: Path) -> Path | None:
    """Ranks simultaneously busy over time, with collective boundaries marked."""
    profile = a.concurrency
    if not profile.times or profile.max_busy == 0:
        return None

    fig, ax = _figure(9.2, 2.5)
    ax.fill_between(profile.times, profile.busy, step="mid",
                    color=ts.ROLE_COLOR["work"], alpha=0.45, linewidth=0)
    ax.plot(profile.times, profile.busy, drawstyle="steps-mid",
            color=ts.ROLE_COLOR["work"], linewidth=1.0)
    ax.axhline(a.world_size, color=ts.MUTED, linewidth=0.8, linestyle=":")
    ax.text(profile.times[-1], a.world_size, f" world size {a.world_size}",
            color=ts.MUTED, fontsize=7, va="bottom", ha="right")
    ax.axhline(profile.achieved_parallelism, color=ts.ROLE_COLOR["recovery"],
               linewidth=0.9, linestyle="--")
    ax.text(0, profile.achieved_parallelism,
            f" achieved {profile.achieved_parallelism:.2f}×",
            color=ts.ROLE_COLOR["recovery"], fontsize=7, va="bottom")

    for c in a.collectives:
        ax.axvline(c.t_first, color=ts.ROLE_COLOR["message"], linewidth=0.5, alpha=0.35)

    ax.set_xlim(0, max(a.wall_s, 1e-6))
    ax.set_ylim(0, max(a.world_size, profile.max_busy) * 1.18)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_xlabel("seconds from first event")
    ax.set_ylabel("ranks busy")
    return _save(fig, out)


def comm_matrix(a: Analysis, out: Path) -> Path | None:
    """Token volume sent from each rank to each rank."""
    if not a.comm:
        return None
    ranks = sorted({r for pair in a.comm for r in pair})
    index = {r: i for i, r in enumerate(ranks)}
    grid = [[0 for _ in ranks] for _ in ranks]
    for (src, dst), (_n, tokens) in a.comm.items():
        grid[index[src]][index[dst]] += tokens

    size = max(3.0, min(7.2, 0.42 * len(ranks) + 1.7))
    fig, ax = _figure(size, size * 0.92)
    # A log-ish normalisation would hide zeros; a linear scale on token counts makes the
    # tree structure legible because the root's row genuinely dominates.
    image = ax.imshow(grid, cmap="viridis", aspect="equal", interpolation="nearest")
    ax.set_xticks(range(len(ranks)))
    ax.set_yticks(range(len(ranks)))
    ax.set_xticklabels(ranks, fontsize=7)
    ax.set_yticklabels(ranks, fontsize=7)
    ax.set_xlabel("destination rank")
    ax.set_ylabel("source rank")
    ax.grid(False)
    bar = fig.colorbar(image, ax=ax, fraction=0.046, pad=0.04)
    bar.set_label("tokens sent", color=ts.MUTED, fontsize=8)
    bar.ax.tick_params(colors=ts.MUTED, labelsize=7)
    bar.outline.set_edgecolor(ts.LINE)
    return _save(fig, out)


def rank_profile(a: Analysis, out: Path) -> Path | None:
    """Per-rank busy time and context occupancy, the two ways a rank becomes a bottleneck."""
    ranks = a.participating_ranks
    profiles = [a.ranks[r] for r in ranks]
    if not profiles or all(p.busy_s == 0 for p in profiles):
        return None

    fig, (left, right) = plt.subplots(1, 2, figsize=(9.2, max(2.2, 0.26 * len(ranks) + 1.2)))
    fig.patch.set_facecolor(ts.BACKGROUND)
    for ax in (left, right):
        _style_axes(ax, grid_axis="x")
        ax.grid(False, axis="y")

    positions = range(len(profiles))
    left.barh(list(positions), [p.busy_s for p in profiles],
              color=ts.ROLE_COLOR["work"], height=0.62)
    idle = [max(0.0, a.wall_s - p.busy_s) for p in profiles]
    left.barh(list(positions), idle, left=[p.busy_s for p in profiles],
              color=ts.LINE, height=0.62)
    left.set_yticks(list(positions))
    left.set_yticklabels([f"rank {p.rank}" for p in profiles], fontsize=7)
    left.invert_yaxis()
    left.set_xlabel("seconds (busy, then idle to wall)")
    left.set_title("occupancy", fontsize=9, loc="left")

    occupancy = [p.context_occupancy * 100 for p in profiles]
    colors = [
        ts.ROLE_COLOR["trouble"] if o > 80 else ts.ROLE_COLOR["rma"] if o > 50 else ts.ROLE_COLOR["message"]
        for o in occupancy
    ]
    right.barh(list(positions), occupancy, color=colors, height=0.62)
    right.set_yticks(list(positions))
    right.set_yticklabels([])
    right.invert_yaxis()
    right.set_xlim(0, max(100.0, max(occupancy) * 1.1 if occupancy else 100.0))
    right.set_xlabel("context occupancy (% of budget)")
    right.set_title("context pressure", fontsize=9, loc="left")
    return _save(fig, out)


def collective_validation(a: Analysis, out: Path) -> Path | None:
    """Logged message count against the closed-form prediction, per invocation."""
    checked = [c for c in a.collectives if c.predicted_messages is not None and c.complete]
    if not checked:
        return None

    fig, ax = _figure(9.2, max(2.2, 0.3 * len(checked) + 1.1))
    positions = range(len(checked))
    actual = [
        (c.logged_messages if c.logged_messages is not None else c.effective_messages) for c in checked
    ]
    predicted = [c.predicted_messages or 0 for c in checked]

    ax.barh([p + 0.19 for p in positions], actual, height=0.36,
            color=ts.ROLE_COLOR["message"], label="logged")
    ax.barh([p - 0.19 for p in positions], predicted, height=0.36,
            color=ts.ROLE_COLOR["recovery"], label="closed form")
    for i, (act, pred) in enumerate(zip(actual, predicted, strict=True)):
        if act != pred:
            ax.plot([max(act, pred) * 1.04], [i], marker="D", markersize=4,
                    color=ts.ROLE_COLOR["trouble"], markeredgewidth=0)

    ax.set_yticks(list(positions))
    ax.set_yticklabels(
        [f"{c.op}/{c.algorithm}" + (f" · {c.label}" if c.label else "") for c in checked], fontsize=7
    )
    ax.invert_yaxis()
    ax.set_xlabel("messages")
    ax.grid(False, axis="y")
    legend = ax.legend(loc="lower right", frameon=False, fontsize=7.5)
    for text in legend.get_texts():
        text.set_color(ts.MUTED)
    return _save(fig, out)


#: Figure name to builder. Each returns the path written, or ``None`` when the run has no data
#: for it --- an empty axis implies a measurement was made and came out zero, which is a
#: different and false claim.
FIGURES = {
    "timeline": timeline,
    "concurrency": concurrency,
    "comm": comm_matrix,
    "ranks": rank_profile,
    "collectives": collective_validation,
}


def render_all(a: Analysis, outdir: Path) -> dict[str, Path]:
    made: dict[str, Path] = {}
    for name, builder in FIGURES.items():
        path = builder(a, outdir / f"{name}.pdf")
        if path is not None:
            made[name] = path
    return made
