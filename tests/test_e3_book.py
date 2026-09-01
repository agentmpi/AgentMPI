"""E3's harness, partition, and the data policy that keeps the corpus out of git.

The corpus tests need the network, so they skip cleanly when it is absent; the
harness tests do not, because the surrogate executor exists precisely so that the
protocol can be regression tested without agents or a network.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from experiments.e3_book import corpus as corpus_mod
from experiments.e3_book.harness import _agenda_of, _edges_of, _my_order, _slug


def _corpus(tmp_path, size):
    try:
        return corpus_mod.build(tmp_path / "work", size)
    except (urllib.error.URLError, OSError) as exc:  # pragma: no cover - offline
        pytest.skip(f"corpus fetch unavailable: {exc}")


# -- the data policy --------------------------------------------------------


def test_manifest_never_carries_corpus_text(tmp_path):
    """The policy is mechanical, not a matter of discipline.

    A README saying "do not commit the book" is one convenience field away from
    being violated. ``metadata()`` is the only serialisation path and it does not
    emit text.
    """
    c = _corpus(tmp_path, 4)
    path = corpus_mod.write_manifest(c, tmp_path / "manifest.json")
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)

    for segment in payload["segments"]:
        assert "text" not in segment
        assert set(segment) == {"index", "pages", "chars", "tokens", "sha256_16"}

    # And the strong form: no substantial run of the source survives anywhere in
    # the file, including in a field somebody adds later without thinking.
    for seg in c.segments:
        body = seg.text.strip()
        assert body[:120] not in raw


def test_segment_metadata_identifies_without_reproducing(tmp_path):
    c = _corpus(tmp_path, 4)
    for s in c.segments:
        meta = s.metadata()
        assert meta["chars"] > 0 and meta["tokens"] > 0
        assert len(meta["sha256_16"]) == 16
        assert meta["pages"][0] <= meta["pages"][1]


# -- the partition ----------------------------------------------------------


@pytest.mark.parametrize("size", [4, 16, 32])
def test_partition_is_complete_contiguous_and_balanced(tmp_path, size):
    c = _corpus(tmp_path, size)
    assert c.size == size

    # Contiguous and non-overlapping: a rank's difficulty is local, and striding
    # would destroy the seam structure the halo exchange exists for.
    pages = [(s.first_page, s.last_page) for s in c.segments]
    assert pages == sorted(pages)
    for (_lo, hi), (nlo, _nhi) in zip(pages, pages[1:], strict=False):
        assert nlo == hi + 1

    # No empty rank. An empty rank still enters every collective and is
    # indistinguishable in the trace from a rank whose executor died.
    assert all(s.chars > 0 for s in c.segments)

    chars = [s.chars for s in c.segments]
    assert max(chars) / (sum(chars) / len(chars)) < 2.0


def test_partition_refuses_more_segments_than_pages(tmp_path):
    with pytest.raises(SystemExit):
        _corpus(tmp_path, 500)


def test_running_header_is_stripped(tmp_path):
    """Left in, the header would recur ~99 times and every rank would propose a
    rendering for it, manufacturing a terminology conflict out of a PDF artefact."""
    c = _corpus(tmp_path, 4)
    for s in c.segments:
        assert "Кононов. «Код Дурова" not in s.text


# -- harness helpers --------------------------------------------------------


def test_slug_is_shell_and_filename_safe():
    for term in ["ВКонтакте", "Дом Зингера", "hack/slash", "a b  c", "…"]:
        slug = _slug(term)
        assert slug.replace("-", "").isalnum()
        assert slug == _slug(term), "slug must be stable"
    assert _slug("ВКонтакте") != _slug("Дуров")


def test_agenda_only_contains_terms_needing_research():
    terms = {
        "A": {"needs_research": True, "kind": "person", "proposed": {"en": "a"}},
        "B": {"needs_research": False, "kind": "org", "proposed": {"en": "b"}},
    }
    agenda = _agenda_of(terms, {"A": {"en": "arb"}}, {})
    assert [i["term"] for i in agenda] == ["A"]
    # The arbitrated proposal wins over the local one: that is the whole point of
    # having arbitrated it.
    assert agenda[0]["proposed"] == {"en": "arb"}


def test_agenda_deduplicates():
    terms = {t: {"needs_research": True, "proposed": {}} for t in ("X", "Y")}
    assert len({i["key"] for i in _agenda_of(terms, {}, {})}) == 2


def test_agenda_puts_contested_terms_first_and_respects_the_cap():
    """The reduction already computed the right research priority; use it.

    A term in the conflict set is one two ranks reading different parts of the
    book proposed to render differently -- exactly where inconsistency would show,
    and exactly what a single translator would never notice was contentious.
    """
    terms = {t: {"needs_research": True, "proposed": {}} for t in ("A", "B", "C")}
    agenda = _agenda_of(terms, {}, {"C": ["x", "y"]})
    assert [i["term"] for i in agenda] == ["C", "A", "B"]
    assert agenda[0]["contested"] is True and agenda[1]["contested"] is False

    # The cap bounds the agenda independently of p, which is what makes shared
    # research a saving rather than a cost that grows with the population.
    assert [i["term"] for i in _agenda_of(terms, {}, {"C": ["x", "y"]}, cap=2)] == ["C", "A"]


def test_claim_order_is_rotated_per_rank():
    """Every rank scanning in the same order makes the claim loop a thundering herd."""
    agenda = [{"key": f"k{i}"} for i in range(8)]
    firsts = {_my_order(agenda, r, 8)[0]["key"] for r in range(8)}
    assert len(firsts) > 1, "all ranks would contend on the same first item"
    for r in range(8):
        assert len(_my_order(agenda, r, 8)) == len(agenda), "rotation must not drop items"


def test_claim_order_handles_an_empty_agenda():
    assert _my_order([], 3, 8) == []


def test_edges_of_reads_first_and_last_unit():
    units = [{"i": 0, "en": "first"}, {"i": 1, "en": "middle"}, {"i": 2, "en": "last"}]
    edges = _edges_of(units, ["en"])
    assert edges["head"]["en"] == "first"
    assert edges["tail"]["en"] == "last"
    # A rank whose agent returned nothing must still produce a well-formed edge,
    # or its neighbours' seam prompts are malformed rather than merely empty.
    assert _edges_of([], ["en"])["head"] == {"en": ""}


# -- the harness end to end -------------------------------------------------


def _run(tmp_path, size, arm="full"):
    from experiments.e3_book import harness as h

    h.RUNS = tmp_path / "runs"
    return h.main([
        "--name", f"unit-{arm}-{size}", "--size", str(size), "--executor", "stub",
        "--arm", arm, "--work-dir", str(tmp_path / "work"),
        "--phase-timeout", "120", "--task-timeout", "120",
    ])


def test_harness_completes_and_exercises_every_collective(tmp_path):
    try:
        report = _run(tmp_path, 4)
    except SystemExit as exc:  # pragma: no cover - offline
        pytest.skip(f"corpus unavailable: {exc}")

    assert report["failed"] == 0 and report["succeeded"] == 4

    trace = tmp_path / "runs" / "unit-full-4" / "harness.trace.jsonl"
    kinds = {json.loads(line)["kind"] for line in trace.read_text().splitlines() if line.strip()}
    for expected in ("bcast", "scatter", "allreduce", "barrier", "exscan",
                     "gather", "neighbor_allgather", "win.create", "win.fence"):
        assert expected in kinds, f"E3 never issued {expected}"


def test_ablations_remove_only_their_mechanism(tmp_path):
    try:
        _run(tmp_path, 4, arm="noseams")
        _run(tmp_path, 4, arm="noglossary")
    except SystemExit as exc:  # pragma: no cover - offline
        pytest.skip(f"corpus unavailable: {exc}")

    def kinds_of(name):
        trace = tmp_path / "runs" / name / "harness.trace.jsonl"
        return {json.loads(x)["kind"] for x in trace.read_text().splitlines() if x.strip()}

    noseams = kinds_of("unit-noseams-4")
    assert "neighbor_allgather" not in noseams
    assert "allreduce" in noseams, "noseams must not disturb the glossary"

    noglossary = kinds_of("unit-noglossary-4")
    assert "allreduce" not in noglossary
    assert "exscan" in noglossary and "gather" in noglossary


def test_run_directory_holds_evidence_and_no_corpus(tmp_path):
    try:
        _run(tmp_path, 4)
    except SystemExit as exc:  # pragma: no cover - offline
        pytest.skip(f"corpus unavailable: {exc}")

    run_dir = tmp_path / "runs" / "unit-full-4"
    for name in ("corpus_manifest.json", "launch_plan.json", "report.json",
                 "harness.trace.jsonl"):
        assert (run_dir / name).exists(), f"{name} missing from the committed evidence"

    # The translation itself belongs to the untracked working directory, never to
    # the run directory that gets committed.
    assert not (run_dir / "out").exists()
    assert (tmp_path / "work" / "unit-full-4" / "out").exists()


def test_analysis_reads_an_e3_trace(tmp_path):
    from ampi.analysis import analyse, load_events

    try:
        _run(tmp_path, 4)
    except SystemExit as exc:  # pragma: no cover - offline
        pytest.skip(f"corpus unavailable: {exc}")

    a = analyse(load_events(tmp_path / "runs" / "unit-full-4" / "harness.trace.jsonl"))
    assert a.world_size == 4
    assert {p.name for p in a.phases} >= {"survey", "translate", "done"}
    assert not a.incomplete_collectives
