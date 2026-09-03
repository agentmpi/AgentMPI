"""Tests for E6: the production book-translation harness.

Everything here runs against the stub executor and a checkout of the legacy
corpus.  When the checkout is not available (no network, no cache) the tests
that need it are skipped rather than failed, because a missing corpus is not a
defect in the harness.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "experiments"))

from e6_book import corpus as corpus_mod  # noqa: E402
from e6_book.executors import split_sentences, stub_executor, validate_page  # noqa: E402
from e6_book.harness import PAGES_WIN, BookHarness, Config, assemble_cells, rotate  # noqa: E402
from e6_book.rank import session_prompt  # noqa: E402


def _legacy() -> Path | None:
    for candidate in (os.environ.get("E6_LEGACY_DIR"), ROOT / "work" / "e6" / "legacy",
                      Path("/home/user/xinshuo-ph/durov_code_translation_multi_agent")):
        if candidate and (Path(candidate) / "extracted" / "pages").is_dir():
            return Path(candidate)
    return None


@pytest.fixture(scope="module")
def legacy() -> Path:
    p = _legacy()
    if p is None:
        pytest.skip("no legacy corpus checkout available")
    return p


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------


def test_partition_is_contiguous_balanced_and_covers_every_page(legacy):
    pages = corpus_mod.load_pages(legacy)
    assert 90 <= len(pages) <= 99
    for size in (1, 4, 16, 32, 64):
        segs = corpus_mod.partition(pages, size)
        assert len(segs) == size
        flat = [n for s in segs for n in s]
        assert flat == sorted(pages), "every page exactly once, in order"
        assert all(s == list(range(s[0], s[-1] + 1)) or all(n in pages for n in s) for s in segs)
        chars = [sum(pages[n].chars for n in s) for s in segs]
        if size <= 16:
            assert max(chars) < 2.0 * min(chars), f"p={size}: imbalance {chars}"
    with pytest.raises(ValueError):
        corpus_mod.partition(pages, len(pages) + 1)


def test_manifest_carries_metadata_and_never_text(legacy, tmp_path):
    corpus = corpus_mod.build(tmp_path, 8, legacy_dir=legacy)
    m = corpus_mod.manifest(corpus)
    assert m["n_segments"] == 8 and m["n_pages"] == len(corpus.pages)
    assert all("text" not in p for p in m["pages"])
    assert all(len(p["sha256_16"]) == 16 for p in m["pages"])
    out = corpus_mod.write_manifest(corpus, tmp_path / "m.json")
    assert "Мальчик" not in out.read_text(encoding="utf-8")
    assert len(corpus.seed_glossary) > 20
    assert corpus.seed_glossary["Павел Дуров"]["en"] == "Pavel Durov"


def test_clean_strips_the_running_header_and_reflows_paragraphs():
    raw = ("Н. В. Кононов. «Код Дурова. Реальная история «ВКонтакте» и ее создателя»\n\n"
           "      Первый абзац начинается здесь\nи продолжается на второй строке.\n"
           "      Второй абзац.\n")
    text = corpus_mod.clean(raw)
    assert "Кононов" not in text
    assert text.split("\n\n") == ["Первый абзац начинается здесь и продолжается на второй строке.",
                                  "Второй абзац."]


def test_page_subset_and_rotation():
    assert corpus_mod.parse_pages("13-15,40") == {13, 14, 15, 40}
    assert corpus_mod.parse_pages("") is None
    items = list(range(10))
    seen = {tuple(rotate(items, r, 5))[0] for r in range(5)}
    assert len(seen) == 5, "ranks start at different agenda items"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_validate_page_catches_gaps_missing_languages_and_summaries():
    page = {"page": 13, "chars": 1000, "page_type": "narrative"}
    good = {"page": 13, "sentences": [{"id": 1, "ru": "x" * 700, "en": "a", "zh": "b", "ja": "c"}],
            "total_sentences": 1}
    assert validate_page(good, page, ["en", "zh", "ja"]) == []
    bad = {"page": 12, "sentences": [{"id": 2, "ru": "x", "en": "", "zh": "b", "ja": "c"}],
           "total_sentences": 3}
    v = validate_page(bad, page, ["en", "zh", "ja"])
    assert any("page is" in x for x in v)
    assert any("ids must be" in x for x in v)
    assert any("'en'" in x for x in v)
    assert any("total_sentences" in x for x in v)
    assert any("summarised" in x for x in v)
    assert len(split_sentences("Один. Два! Три?")) == 3


# ---------------------------------------------------------------------------
# The harness, with the stub
# ---------------------------------------------------------------------------


def _run_local(tmp_path, legacy, size, *, pages="5-20", kill=None, **cfg_kw):
    from ampi.harness import Harness

    cfg = Config(name="t", size=size, phase_timeout_s=120, task_timeout_s=30, pages=pages,
                 review_cap=2, research_cap=6, **cfg_kw)
    corpus = corpus_mod.build(tmp_path, size, legacy_dir=legacy, pages=pages)
    h = Harness(root=str(tmp_path / "job"), size=size, device="sqlite", ctx_budget=cfg.ctx_budget,
                force=True)
    h.create()
    executor = stub_executor(corpus, cfg.languages, latency_s=0.3 if kill is not None else 0.0)
    harness = BookHarness(cfg, corpus, executor, tmp_path / "work")
    if kill is not None:
        def assassin() -> None:
            time.sleep(1.0)
            v = h.attach(kill)
            try:
                v.kill(kill, reason="test")
            finally:
                v.close()
        threading.Thread(target=assassin, daemon=True).start()
    results = h.run(harness.rank_main, timeout=600)
    root = h.attach(0)
    try:
        space = root._space(PAGES_WIN)
        cells = {c.key: root.device.read(space, c.key).value for c in root.device.keys(space)}
        book = assemble_cells(cells, tmp_path / "out", corpus)
        events = root.events()
    finally:
        root.close()
    return cfg, corpus, results, book, events


def test_every_phase_runs_and_the_book_assembles(tmp_path, legacy):
    cfg, corpus, results, book, events = _run_local(tmp_path, legacy, 4)
    assert all(r.ok for r in results), [r.error for r in results if not r.ok]
    assert book["missing"] == [] and book["n_pages"] == len(corpus.pages)
    kinds = {e["kind"] for e in events}
    for kind in ("barrier", "bcast", "scatter", "allreduce", "gather", "exscan",
                 "neighbor_allgather", "win.lock", "win.unlock", "win.cas", "win.fence",
                 "op.arbitrate", "page.done", "cart.create"):
        assert kind in kinds, f"{kind} never happened"
    labels = {e.get("label") for e in events if e["kind"] == "allreduce"}
    assert {"census", "glossary"} <= labels
    glossary_conflicts = [e.get("conflicts") for e in events
                          if e["kind"] == "allreduce" and e.get("label") == "glossary"]
    assert all(not c for c in glossary_conflicts), "the binding glossary must lift nothing"
    census = [e for e in events if e["kind"] == "allreduce" and e.get("label") == "census"]
    assert any(e.get("conflicts") for e in census), "the stub disagrees, so the census must lift"
    # Every page file is in the legacy schema.
    one = json.loads(next((tmp_path / "out" / "pages").glob("page_*.json")).read_text())
    assert {"page", "chapter", "chapter_title", "sentences", "translator_notes",
            "total_sentences", "page_type"} <= set(one)
    assert all({"id", "ru", "en", "zh", "ja"} <= set(s) for s in one["sentences"])
    # The registry lock was taken and released by every rank that had something to say.
    assert sum(1 for e in events if e["kind"] == "win.lock") >= 1
    assert results[0].value["assembled"] == len(corpus.pages)


def test_a_killed_rank_has_its_pages_stolen_and_the_book_still_assembles(tmp_path, legacy):
    cfg, corpus, results, book, events = _run_local(tmp_path, legacy, 6, kill=2, pages="5-40")
    assert results[2].ok is False and results[2].error_class == "AMPI_ERR_FENCED"
    assert all(r.ok for r in results if r.rank != 2)
    assert book["missing"] == [], book
    stolen = [e for e in events if e["kind"] == "page.steal"]
    assert stolen and all(e["victim"] == 2 for e in stolen)
    assert set(int(k) for k in book["stolen"]) == set(corpus.segment_of(2)) - {
        int(e["page"]) for e in events if e["kind"] == "page.done" and e["rank"] == 2}
    assert any(e["kind"] == "coll.dropped" and 2 in e["dropped"] for e in events)


def test_ablation_arms_skip_their_mechanism(tmp_path, legacy):
    cfg, corpus, results, book, events = _run_local(tmp_path, legacy, 3, arm="noglossary",
                                                     review=False, seam=False)
    assert all(r.ok for r in results)
    kinds = {e["kind"] for e in events}
    assert "allreduce" not in kinds and "neighbor_allgather" not in kinds
    assert book["missing"] == []


# ---------------------------------------------------------------------------
# The git device, across processes, against a local bare remote
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_three_processes_complete_a_job_over_the_git_device(tmp_path, legacy):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    env = dict(os.environ, E6_LEGACY_DIR=str(legacy))
    rank_py = str(ROOT / "experiments" / "e6_book" / "rank.py")
    name = f"t-git-{os.getpid()}"
    common = ["--name", name, "--remote", str(remote)]
    subprocess.run([sys.executable, rank_py, "create", *common, "--size", "3",
                    "--root", str(tmp_path / "create"), "--pages", "5-12", "--phase-timeout", "120",
                    "--task-timeout", "30", "--join-deadline", "120", "--research-cap", "4",
                    "--review-cap", "1"], check=True, env=env, capture_output=True)
    procs = [
        subprocess.Popen([sys.executable, rank_py, "run", *common, "--rank", str(r),
                          "--root", str(tmp_path / f"root{r}"), "--executor", "stub"],
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for r in range(3)
    ]
    outs = [p.communicate(timeout=400)[0] for p in procs]
    for p, out in zip(procs, outs, strict=True):
        assert p.returncode == 0, out[-2000:]
        assert '"ok": true' in out, out[-2000:]
    r = subprocess.run([sys.executable, rank_py, "collect", *common, "--root",
                        str(tmp_path / "collect"), "--no-analysis"], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    report = json.loads((ROOT / "runs" / name / "report.json").read_text())
    try:
        assert report["rank_states"] == {"finalised": 3}
        assert report["book"]["missing"] == []
        assert report["commits_on_branch"] > 50
        trace = ROOT / "runs" / name / "harness.trace.jsonl"
        kinds = {json.loads(line)["kind"] for line in trace.read_text().splitlines()}
        assert {"barrier", "allreduce", "win.fence", "page.done", "finalize"} <= kinds
    finally:
        import shutil

        shutil.rmtree(ROOT / "runs" / name, ignore_errors=True)
        shutil.rmtree(ROOT / "work" / "e6" / name, ignore_errors=True)


def test_session_prompt_mentions_no_protocol():
    text = session_prompt("job", 3, 16, "https://example.invalid/r", "main")
    assert "rank 3 of 16" in text and "--rank 3 --expect-rank 3" in text
    # "fence" is deliberately absent from this list: the prompt forbids a
    # *markdown* fence around the JSON, which is not a protocol word.
    import re

    for word in ("barrier", "allreduce", "bcast", "reduce", "window", "win_fence", "lock",
                 "collective", "communicator", "rank card"):
        assert not re.search(rf"\b{word}\b", text.lower()), word
