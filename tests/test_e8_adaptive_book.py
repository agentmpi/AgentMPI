"""E8: the page plan, and a whole stub population through the pool."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from experiments.e8_adaptive_book import harness as h

ROOT = Path(__file__).resolve().parent.parent
PAGES = ROOT / "work" / "e7" / "pages"

pytestmark = pytest.mark.skipif(not (PAGES / "page_010.txt").exists(),
                                reason="the book's pages are not cached on this machine")


def test_plan_cuts_contiguous_balanced_blocks_that_cover_every_page(tmp_path):
    cfg = h.Config(name="t", size=8)
    plan = h.build_plan(ROOT / "work" / "e7", cfg)
    pages = [p["page"] for p in plan["pages"]]
    assert pages == sorted(pages) and len(pages) >= 90
    flat = [n for b in plan["blocks"] for n in b]
    assert flat == pages and len(plan["blocks"]) == 8 and all(plan["blocks"])
    chars = [b["chars"] for b in plan["meta"]["blocks"]]
    assert max(chars) < 2.2 * min(chars)
    seeds = h.seeds_of(plan)
    assert len(seeds) == len(pages) and all(s["id"].startswith("p") for s in seeds)
    assert {s["group"] for s in seeds} == {f"b{i}" for i in range(8)}


def test_stub_population_drains_the_pool_and_covers_the_book(tmp_path):
    a = argparse.Namespace(
        name="e8-test", size=4, languages="en,zh", executor="stub", model="stub",
        arbiter_model="", reasoning="low", fallback_model="", device="sqlite", launch="threads",
        nodes=1, node=0, rejoin=False, remote=None, branch=None, task_timeout=60.0,
        phase_timeout=120.0, lease=60.0, quorum=1.0, algorithm=None, die_fraction=0.0,
        respawn=0, ctx_budget=200000, first_page=5, last_page=24, no_steal=False,
        source_dir=None, run_dir=str(tmp_path / "run"), work_dir=str(tmp_path / "work"),
        quiet=True,
    )
    summary = h.cmd_run(a)
    assert summary["population_complete"] and summary["ranks_finalised"] == 4
    assert summary["book"]["missing"] == 0 and summary["pages"]["translated"] == summary["pages"]["total"]
    assert summary["pool"]["drained"] and summary["pool"]["items"] == 2 * summary["pages"]["total"] - 1
    trace = (tmp_path / "run" / "harness.trace.jsonl").read_text(encoding="utf-8")
    ev = [json.loads(line) for line in trace.splitlines() if line.strip()]
    kinds = {e["kind"] for e in ev}
    assert {"pool.create", "pool.claim", "pool.done", "pool.add", "pool.drained", "ibcast",
            "win.accumulate"} <= kinds
    claims = [e for e in ev if e["kind"] == "pool.claim"]
    assert len(claims) == summary["pool"]["items"]
    assert len({e["item"] for e in claims}) == len(claims)          # every item claimed once
    seam_claims = [e for e in claims if e["item"].startswith("s")]
    assert seam_claims and all(e["item"].count("-") == 1 for e in seam_claims)
