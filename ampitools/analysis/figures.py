"""Figures for one AgentMPI run, drawn in a single visual language.

Six figures, each answering a question the others cannot.  A figure is produced
only when the run holds the data for it: an empty axis implies a measurement was
made and came out zero, which is a stronger and usually false claim than saying
nothing.

``timeline``
    The Jumpshot-style Gantt: one lane per rank, agent work as bars, protocol
    events as ticks and diamonds.  This is where a coordination bug shows itself,
    because such bugs are almost always a *shape* --- a rank idle while its peers
    work, a fan-in serialising at a root, a barrier whose last arrival is minutes
    after its first.

``concurrency``
    How many ranks were simultaneously busy.  A run can register sixty-four ranks
    and never exceed three, and no scalar summary separates that from real
    parallelism.

``waterfall``
    Per-collective blocking, ordered in time, with the straggler named.  The
    figure the timeline cannot be: it charges each collective its rank-seconds and
    attributes them.

``ranks``
    Per-rank busy time and context occupancy side by side --- the two independent
    ways a rank becomes a bottleneck, and the two that are confused most often.

``cost``
    Measured blocking against the closed form for the schedule each collective
    actually used.  Where the implementation and the cost model are held to each
    other.

``phases``
    The harness's own memo segmentation, with coordination overlaid, so a reader
    can see which phase paid for the protocol.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import matplotlib
except ImportError as exc:  # pragma: no cover - depends on the installation
    raise ImportError(
        "the figures need matplotlib: install the analysis extra, "
        "pip install 'agentmpi[analysis]'") from exc

matplotlib.use("Agg")

import matplotlib.patches as mpatches  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import MaxNLocator  # noqa: E402

from . import style as st  # noqa: E402
from .model import Analysis  # noqa: E402

__all__ = ["FIGURES", "render_all"]

LANE_H = 0.62


def _style_axes(ax: Any, *, grid_axis: str = "both") -> None:
    ax.set_facecolor(st.PANEL)
    for spine in ax.spines.values():
        spine.set_color(st.LINE)
    ax.tick_params(colors=st.MUTED, labelsize=8, which="both")
    ax.xaxis.label.set_color(st.MUTED)
    ax.yaxis.label.set_color(st.MUTED)
    ax.title.set_color(st.FOREGROUND)
    ax.grid(True, axis=grid_axis, color=st.LINE, linewidth=0.5, alpha=0.55)
    ax.set_axisbelow(True)


def _figure(width: float, height: float) -> tuple[Any, Any]:
    fig, ax = plt.subplots(figsize=(width, height))
    fig.patch.set_facecolor(st.BACKGROUND)
    _style_axes(ax)
    return fig, ax


def _save(fig: Any, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # `facecolor` must be passed explicitly: savefig otherwise reverts to white
    # and the dark styling silently applies to everything except the margins.
    fig.savefig(path, dpi=180, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


def _legend(ax: Any, handles: list[Any], **kw: Any) -> None:
    if not handles:
        return
    legend = ax.legend(handles=handles, frameon=False, fontsize=7.5, **kw)
    for text in legend.get_texts():
        text.set_color(st.MUTED)


# ---------------------------------------------------------------------------


def timeline(a: Analysis, out: Path, *, max_ticks: int = 6000) -> Path | None:
    """One lane per rank: work as bars, protocol events as instants."""
    ranks = a.participating_ranks
    if not ranks:
        return None
    index = {r: i for i, r in enumerate(ranks)}

    fig, ax = _figure(9.4, max(2.2, 0.26 * len(ranks) + 1.2))
    span = max(a.wall_s, 1e-6)
    for i in range(len(ranks)):
        ax.add_patch(
            mpatches.Rectangle(
                (0, i - LANE_H / 2), span, LANE_H,
                facecolor=st.PANEL_ALT, edgecolor="none", zorder=0,
            )
        )

    roles_present: set[str] = set()
    for rank, start, end, _label in a.work_spans:
        if rank not in index:
            continue
        roles_present.add("work")
        ax.add_patch(
            mpatches.Rectangle(
                (start, index[rank] - LANE_H / 2 + 0.06),
                max(end - start, span * 0.0006), LANE_H - 0.12,
                facecolor=st.ROLE_COLOR["work"], edgecolor="none", alpha=0.9, zorder=2,
            )
        )

    drawn = 0
    for e in a.events:
        rank = int(e.get("rank", -1))
        if rank not in index or drawn > max_ticks:
            continue
        kind = e["kind"]
        if kind in ("broker.claim", "broker.submit", "task.start", "task.done", "coll.join"):
            continue
        style = st.style_for(kind)
        roles_present.add(style.role)
        y = index[rank]
        t = e["ts"] - a.t0
        if style.glyph == "tick":
            ax.plot([t, t], [y - LANE_H / 2 + 0.1, y + LANE_H / 2 - 0.1],
                    color=style.color, linewidth=0.7, alpha=0.85, zorder=3)
        else:
            ax.plot([t], [y], marker="D", markersize=3.2, color=style.color,
                    markeredgewidth=0, zorder=4)
        drawn += 1

    ax.set_ylim(-0.7, len(ranks) - 0.3)
    ax.set_xlim(0, span)
    ax.set_yticks(range(len(ranks)))
    ax.set_yticklabels([f"r{r}" for r in ranks], fontsize=6.5)
    ax.invert_yaxis()
    ax.set_xlabel("seconds from first event")
    ax.grid(True, axis="x", color=st.LINE, linewidth=0.5, alpha=0.55)
    ax.grid(False, axis="y")
    _legend(
        ax,
        [
            mpatches.Patch(color=st.ROLE_COLOR[r], label=st.ROLE_LABEL[r])
            for r in st.ROLE_ORDER
            if r in roles_present
        ],
        loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=4,
    )
    return _save(fig, out)


def concurrency(a: Analysis, out: Path) -> Path | None:
    """Ranks simultaneously busy over time, with collective boundaries marked."""
    profile = a.concurrency
    if not profile.times or profile.max_busy == 0:
        return None

    fig, ax = _figure(9.4, 2.6)
    ax.fill_between(profile.times, profile.busy, step="mid",
                    color=st.ROLE_COLOR["work"], alpha=0.45, linewidth=0)
    ax.plot(profile.times, profile.busy, drawstyle="steps-mid",
            color=st.ROLE_COLOR["work"], linewidth=1.0)
    ax.axhline(a.world_size, color=st.MUTED, linewidth=0.8, linestyle=":")
    ax.text(profile.times[-1], a.world_size, f" world size {a.world_size} ",
            color=st.MUTED, fontsize=7, va="bottom", ha="right")
    ax.axhline(profile.achieved_parallelism, color=st.ROLE_COLOR["recovery"],
               linewidth=0.9, linestyle="--")
    ax.text(0, profile.achieved_parallelism,
            f" achieved {profile.achieved_parallelism:.2f}x",
            color=st.ROLE_COLOR["recovery"], fontsize=7, va="bottom")
    for c in a.collectives:
        ax.axvline(c.t_last, color=st.ROLE_COLOR["collective"], linewidth=0.5, alpha=0.3)

    ax.set_xlim(0, max(a.wall_s, 1e-6))
    ax.set_ylim(0, max(a.world_size, profile.max_busy) * 1.18)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_xlabel("seconds from first event")
    ax.set_ylabel("ranks busy")
    return _save(fig, out)


def waterfall(a: Analysis, out: Path, *, top: int = 28) -> Path | None:
    """Rank-seconds blocked per collective, with the straggler named.

    Sorted by cost rather than by time, because the question this figure answers
    is "what did coordination cost and who owed it", and an operator scanning a
    long run needs the expensive ones first.
    """
    costly = sorted(
        [c for c in a.collectives if c.rank_wait_s > 0],
        key=lambda c: -c.rank_wait_s,
    )[:top]
    if not costly:
        return None

    fig, ax = _figure(9.4, max(2.2, 0.28 * len(costly) + 1.1))
    positions = range(len(costly))
    ax.barh(
        list(positions), [c.rank_wait_s for c in costly], height=0.6,
        color=[
            st.ROLE_COLOR["consensus"] if c.op in ("reduce", "allreduce", "scan", "exscan")
            else st.ROLE_COLOR["trouble"] if not c.complete
            else st.ROLE_COLOR["collective"]
            for c in costly
        ],
    )
    for i, c in enumerate(costly):
        if c.straggler is not None:
            ax.text(c.rank_wait_s, i, f"  last in: r{c.straggler}",
                    color=st.MUTED, fontsize=6.5, va="center")
    ax.set_yticks(list(positions))
    ax.set_yticklabels(
        [f"{c.op}:{c.label}"[:38] + ("" if c.complete else " (partial)") for c in costly],
        fontsize=6.5,
    )
    ax.invert_yaxis()
    ax.set_xlabel("rank-seconds blocked")
    ax.grid(False, axis="y")
    _legend(
        ax,
        [
            mpatches.Patch(color=st.ROLE_COLOR["collective"], label="data movement"),
            mpatches.Patch(color=st.ROLE_COLOR["consensus"], label="reduction"),
            mpatches.Patch(color=st.ROLE_COLOR["trouble"], label="incomplete"),
        ],
        loc="lower right",
    )
    return _save(fig, out)


def rank_profile(a: Analysis, out: Path) -> Path | None:
    """Per-rank busy, blocked and idle time beside context occupancy."""
    ranks = a.participating_ranks
    profiles = [a.ranks[r] for r in ranks]
    if not profiles:
        return None

    fig, (left, right) = plt.subplots(
        1, 2, figsize=(9.4, max(2.2, 0.22 * len(ranks) + 1.2)),
        gridspec_kw={"width_ratios": [2.1, 1.0]},
    )
    fig.patch.set_facecolor(st.BACKGROUND)
    for ax in (left, right):
        _style_axes(ax, grid_axis="x")
        ax.grid(False, axis="y")

    positions = list(range(len(profiles)))
    busy = [p.busy_s for p in profiles]
    blocked = [p.blocked_s for p in profiles]
    idle = [max(0.0, a.wall_s - b - k) for b, k in zip(busy, blocked, strict=True)]
    left.barh(positions, busy, color=st.ROLE_COLOR["work"], height=0.62, label="working")
    left.barh(positions, blocked, left=busy, color=st.ROLE_COLOR["collective"],
              height=0.62, label="blocked in collectives")
    left.barh(positions, idle, left=[b + k for b, k in zip(busy, blocked, strict=True)],
              color=st.LINE, height=0.62, label="idle")
    left.set_yticks(positions)
    left.set_yticklabels([f"r{p.rank}" for p in profiles], fontsize=6.5)
    left.invert_yaxis()
    left.set_xlabel("seconds")
    left.set_title("where each rank's wall clock went", fontsize=9, loc="left")
    _legend(left, [
        mpatches.Patch(color=st.ROLE_COLOR["work"], label="working"),
        mpatches.Patch(color=st.ROLE_COLOR["collective"], label="blocked"),
        mpatches.Patch(color=st.LINE, label="idle"),
    ], loc="lower right")

    occupancy = [p.context_occupancy * 100 for p in profiles]
    right.barh(
        positions, occupancy, height=0.62,
        color=[
            st.ROLE_COLOR["trouble"] if o > 80
            else st.ROLE_COLOR["rma"] if o > 50
            else st.ROLE_COLOR["message"]
            for o in occupancy
        ],
    )
    right.set_yticks(positions)
    right.set_yticklabels([])
    right.invert_yaxis()
    right.set_xlim(0, max(100.0, max(occupancy) * 1.1 if occupancy else 100.0))
    right.set_xlabel("context used (% of budget)")
    right.set_title("context pressure", fontsize=9, loc="left")
    return _save(fig, out)


def cost_agreement(a: Analysis, out: Path, *, top: int = 24) -> Path | None:
    """Measured blocking against the closed form for the schedule actually used.

    The comparison is deliberately *not* messages sent.  AgentMPI's collectives
    fold in the shared journal, so a bar chart of message counts would compare the
    model against a transport the implementation does not use.  What is checkable
    is the term the selection argument turns on: rounds on the critical path, and
    the protocol seconds they imply, against what a rank actually waited.
    """
    checked = [c for c in a.costed_collectives if c.max_wait_s > 0][:top]
    if not checked:
        return None

    fig, ax = _figure(9.4, max(2.2, 0.3 * len(checked) + 1.2))
    positions = range(len(checked))
    ax.barh([p + 0.19 for p in positions], [c.max_wait_s * 1000 for c in checked],
            height=0.36, color=st.ROLE_COLOR["collective"], label="measured (slowest rank)")
    ax.barh([p - 0.19 for p in positions],
            [(c.predicted_protocol_s or 0.0) * 1000 for c in checked],
            height=0.36, color=st.ROLE_COLOR["recovery"], label="closed form (protocol only)")
    ax.set_xscale("symlog", linthresh=1.0)
    ax.set_yticks(list(positions))
    ax.set_yticklabels(
        [f"{c.op}/{c.algorithm or 'journal'} p={c.size}" for c in checked], fontsize=6.5
    )
    ax.invert_yaxis()
    ax.set_xlabel("milliseconds (symlog)")
    ax.grid(False, axis="y")
    _legend(ax, [
        mpatches.Patch(color=st.ROLE_COLOR["collective"], label="measured (slowest rank)"),
        mpatches.Patch(color=st.ROLE_COLOR["recovery"], label="closed form (protocol only)"),
    ], loc="lower right")
    ax.set_title(
        "the gap is executor time, which the protocol model does not claim to predict",
        fontsize=8, loc="left", color=st.MUTED,
    )
    return _save(fig, out)


def phases(a: Analysis, out: Path) -> Path | None:
    """The harness's own phase segmentation, with coordination overlaid."""
    if not a.phases:
        return None
    fig, ax = _figure(9.4, max(2.0, 0.34 * len(a.phases) + 1.0))
    positions = range(len(a.phases))
    for i, ph in enumerate(a.phases):
        ax.add_patch(
            mpatches.Rectangle(
                (ph.t_start, i - 0.3), max(ph.duration_s, a.wall_s * 0.001), 0.6,
                facecolor=st.ROLE_COLOR["work"], alpha=0.55, edgecolor="none",
            )
        )
        ax.text(ph.t_start, i - 0.4, f" {len(ph.ranks)} ranks, {ph.duration_s:.0f}s",
                color=st.MUTED, fontsize=6.5, va="top")
    for c in a.collectives:
        ax.axvline(c.t_last, color=st.ROLE_COLOR["collective"], linewidth=0.6, alpha=0.35)

    ax.set_yticks(list(positions))
    ax.set_yticklabels([ph.name[:44] for ph in a.phases], fontsize=7)
    ax.invert_yaxis()
    ax.set_xlim(0, max(a.wall_s, 1e-6))
    ax.set_ylim(len(a.phases) - 0.3, -0.8)
    ax.set_xlabel("seconds from first event")
    ax.grid(False, axis="y")
    return _save(fig, out)


#: Figure name to builder.  Each returns the path written, or ``None`` when the
#: run has no data for it.
FIGURES = {
    "timeline": timeline,
    "concurrency": concurrency,
    "waterfall": waterfall,
    "ranks": rank_profile,
    "cost": cost_agreement,
    "phases": phases,
}


def render_all(a: Analysis, outdir: Path, *, fmt: str = "pdf") -> dict[str, Path]:
    made: dict[str, Path] = {}
    for name, builder in FIGURES.items():
        path = builder(a, Path(outdir) / f"{name}.{fmt}")
        if path is not None:
            made[name] = path
    return made
