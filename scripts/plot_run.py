"""Render trace analysis figures when matplotlib is available."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.patches as patches
    import matplotlib.pyplot as plt
except ImportError:  # pragma: no cover - exercised in environments without the extra
    matplotlib = None
    patches = None
    plt = None

from ampi.analysis import Analysis, analyse_path
from ampi.trace_style import (
    BACKGROUND,
    FOREGROUND,
    LINE,
    MUTED,
    PANEL,
    PANEL_ALT,
    ROLE_COLOR,
    ROLE_LABEL,
    ROLE_ORDER,
    style_for,
)

MATPLOTLIB_AVAILABLE = plt is not None
FigureBuilder = Callable[[Analysis, Path], Path | None]


def _require_matplotlib() -> None:
    if not MATPLOTLIB_AVAILABLE:
        raise RuntimeError("matplotlib is not installed; install agentmpi[plots] to render figures")


def _axes(width: float, height: float) -> tuple[Any, Any]:
    _require_matplotlib()
    figure, axis = plt.subplots(figsize=(width, height))
    figure.patch.set_facecolor(BACKGROUND)
    axis.set_facecolor(PANEL)
    for spine in axis.spines.values():
        spine.set_color(LINE)
    axis.tick_params(colors=MUTED, labelsize=8)
    axis.xaxis.label.set_color(MUTED)
    axis.yaxis.label.set_color(MUTED)
    axis.title.set_color(FOREGROUND)
    axis.grid(True, axis="x", color=LINE, linewidth=0.5, alpha=0.6)
    axis.set_axisbelow(True)
    return figure, axis


def _save(figure: Any, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=180, bbox_inches="tight", facecolor=figure.get_facecolor())
    plt.close(figure)
    return path


def timeline(analysis: Analysis, path: Path) -> Path | None:
    """Draw broker claim-to-submit intervals and classified event instants."""
    ranks = sorted(analysis.ranks)
    if not ranks:
        return None
    figure, axis = _axes(10.0, max(2.4, 0.38 * len(ranks) + 1.2))
    index = {rank: position for position, rank in enumerate(ranks)}
    lane_height = 0.64
    for position in range(len(ranks)):
        axis.add_patch(
            patches.Rectangle(
                (0, position - lane_height / 2),
                max(analysis.wall_s, 1e-6),
                lane_height,
                facecolor=PANEL_ALT,
                edgecolor="none",
            )
        )
    for rank, start, end, _label in analysis.work_spans:
        axis.add_patch(
            patches.Rectangle(
                (start - analysis.t0, index[rank] - lane_height / 2 + 0.06),
                max(end - start, analysis.wall_s * 0.0005),
                lane_height - 0.12,
                facecolor=ROLE_COLOR["work"],
                edgecolor="none",
                alpha=0.9,
            )
        )
    roles = {"work"} if analysis.work_spans else set()
    for event in analysis.events:
        rank_value = event.get("rank")
        if not isinstance(rank_value, int) or rank_value not in index:
            continue
        kind = event["kind"]
        if kind in {"broker.claim", "broker.submit", "broker.publish"}:
            continue
        style = style_for(kind)
        if style.role == "other":
            continue
        roles.add(style.role)
        timestamp = float(event["ts"]) - analysis.t0
        position = index[rank_value]
        if style.glyph == "tick":
            axis.plot(
                [timestamp, timestamp],
                [position - 0.2, position + 0.2],
                color=style.color,
                linewidth=0.7,
            )
        else:
            axis.plot(
                [timestamp],
                [position],
                marker="D",
                markersize=3,
                color=style.color,
                markeredgewidth=0,
            )
    axis.set_xlim(0, max(analysis.wall_s, 1e-6))
    axis.set_ylim(-0.7, len(ranks) - 0.3)
    axis.set_yticks(range(len(ranks)))
    axis.set_yticklabels([f"rank {rank}" for rank in ranks])
    axis.invert_yaxis()
    axis.set_xlabel("seconds from first event")
    axis.grid(False, axis="y")
    handles = [
        patches.Patch(color=ROLE_COLOR[role], label=ROLE_LABEL[role])
        for role in ROLE_ORDER
        if role in roles
    ]
    if handles:
        legend = axis.legend(
            handles=handles,
            loc="upper center",
            bbox_to_anchor=(0.5, -0.15),
            ncol=min(4, len(handles)),
            frameon=False,
            fontsize=7,
        )
        for text in legend.get_texts():
            text.set_color(MUTED)
    return _save(figure, path)


def concurrency(analysis: Analysis, path: Path) -> Path | None:
    """Draw exact concurrency change points from broker spans."""
    profile = analysis.concurrency
    if not profile.times:
        return None
    figure, axis = _axes(10.0, 2.8)
    axis.fill_between(
        profile.times,
        profile.busy,
        step="post",
        color=ROLE_COLOR["work"],
        alpha=0.42,
    )
    axis.step(profile.times, profile.busy, where="post", color=ROLE_COLOR["work"])
    axis.axhline(analysis.world_size, color=MUTED, linestyle=":", linewidth=0.8)
    axis.axhline(
        profile.achieved_parallelism,
        color=ROLE_COLOR["recovery"],
        linestyle="--",
        linewidth=0.9,
    )
    axis.set_xlim(0, max(analysis.wall_s, 1e-6))
    axis.set_ylim(0, max(1.0, analysis.world_size, profile.max_busy) * 1.15)
    axis.set_xlabel("seconds from first event")
    axis.set_ylabel("ranks busy")
    return _save(figure, path)


def collectives(analysis: Analysis, path: Path) -> Path | None:
    """Draw participants and input volume for every reconstructed invocation."""
    if not analysis.collectives:
        return None
    invocations = analysis.collectives
    figure, left = _axes(10.0, max(2.6, len(invocations) * 0.35 + 1.2))
    right = left.twiny()
    positions = list(range(len(invocations)))
    left.barh(
        positions,
        [len(invocation.participants) for invocation in invocations],
        height=0.56,
        color=ROLE_COLOR["collective"],
        alpha=0.8,
    )
    right.plot(
        [invocation.input_tokens for invocation in invocations],
        positions,
        color=ROLE_COLOR["message"],
        marker="o",
        markersize=3,
        linewidth=0.8,
    )
    labels = [
        f"{invocation.kind} · {invocation.label or '(unlabelled)'} #{invocation.index}"
        for invocation in invocations
    ]
    left.set_yticks(positions)
    left.set_yticklabels(labels, fontsize=7)
    left.invert_yaxis()
    left.set_xlabel("participants")
    right.set_xlabel("input tokens", color=ROLE_COLOR["message"])
    right.tick_params(colors=ROLE_COLOR["message"], labelsize=8)
    right.spines["top"].set_color(LINE)
    return _save(figure, path)


def rank_profile(analysis: Analysis, path: Path) -> Path | None:
    """Draw per-rank occupancy and context pressure events."""
    profiles = sorted(analysis.ranks.values(), key=lambda profile: profile.rank)
    if not profiles:
        return None
    _require_matplotlib()
    figure, (left, right) = plt.subplots(
        1,
        2,
        figsize=(10.0, max(2.6, len(profiles) * 0.34 + 1.2)),
    )
    figure.patch.set_facecolor(BACKGROUND)
    for axis in (left, right):
        axis.set_facecolor(PANEL)
        axis.tick_params(colors=MUTED, labelsize=8)
        axis.xaxis.label.set_color(MUTED)
        axis.grid(True, axis="x", color=LINE, linewidth=0.5, alpha=0.6)
        for spine in axis.spines.values():
            spine.set_color(LINE)
    positions = list(range(len(profiles)))
    left.barh(
        positions,
        [profile.occupancy * 100 for profile in profiles],
        color=ROLE_COLOR["work"],
        height=0.6,
    )
    left.set_yticks(positions)
    left.set_yticklabels([f"rank {profile.rank}" for profile in profiles])
    left.invert_yaxis()
    left.set_xlim(0, 100)
    left.set_xlabel("broker occupancy (%)")
    pressure = [
        profile.context_degrades + profile.context_stalls for profile in profiles
    ]
    right.barh(
        positions,
        pressure,
        color=ROLE_COLOR["context"],
        height=0.6,
    )
    right.set_yticks(positions)
    right.set_yticklabels([])
    right.invert_yaxis()
    right.set_xlabel("context degradation / stall events")
    return _save(figure, path)


FIGURES: dict[str, FigureBuilder] = {
    "timeline": timeline,
    "concurrency": concurrency,
    "collectives": collectives,
    "rank_profile": rank_profile,
}


def render_all(analysis: Analysis, output_dir: Path) -> dict[str, Path]:
    """Render every applicable figure and return its path by name."""
    _require_matplotlib()
    made: dict[str, Path] = {}
    for name, builder in FIGURES.items():
        path = builder(analysis, output_dir / f"{name}.png")
        if path is not None:
            made[name] = path
    return made


def resolve_trace(path: str | Path) -> Path:
    candidate = Path(path)
    return candidate / "harness.trace.jsonl" if candidate.is_dir() else candidate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", help="run directory or harness.trace.jsonl")
    parser.add_argument("--output", type=Path, help="figure directory")
    args = parser.parse_args(argv)
    trace_path = resolve_trace(args.run)
    output = args.output or trace_path.parent / "analysis" / "figures"
    try:
        made = render_all(analyse_path(trace_path), output)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"plot_run: {exc}\n")
    print(f"wrote {len(made)} figure(s) to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
