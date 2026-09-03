"""E7: the paragraph partition, the harness helpers, and a whole stub run."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments.e7_rawapi_book import corpus as c
from experiments.e7_rawapi_book import harness as h

PAGE = """\
Н. В. Кононов. «Код Дурова. Реальная история «ВКонтакте» и ее создателя»


                                    Глава 1
                               Ботанический сад
      Мальчик с томом Сервантеса выходит из подъезда, огибает автомобиль, который
какой-то негодяй поставил так, что пешеходы еле протискиваются мимо.
      Архитектор раскрасил панели домов в оранжевый и бордовый, чтобы однообразное
серое не свело район с ума.
      Сканируя пространство на предмет гопников, мальчик с книгой идет к трассе.


                                                                                       13
"""


def test_paragraphs_are_cut_on_the_hanging_indent_and_headers_dropped():
    paras = c.paragraphs_of(PAGE, 13, 0)
    texts = [p.text for p in paras]
    assert texts[0] == "Глава 1" and texts[1] == "Ботанический сад"
    assert texts[2].startswith("Мальчик с томом") and texts[2].endswith("протискиваются мимо.")
    assert len(paras) == 5 and all(p.page == 13 and p.chapter == "ch1" for p in paras)
    assert not any("Кононов" in t or t.strip() == "13" for t in texts)


@pytest.mark.parametrize("size", [1, 3, 7, 40])
def test_partition_is_contiguous_balanced_and_never_empty(size):
    paras = [c.Paragraph(i, 5 + i // 10, "x" * (50 + (i * 37) % 400)) for i in range(120)]
    segs = c.partition(paras, size)
    assert len(segs) == size
    assert [p.index for s in segs for p in s.paragraphs] == list(range(120))
    assert all(s.paragraphs for s in segs)
    if size > 1:
        sizes = [s.chars for s in segs]
        share = sum(sizes) / size
        assert max(sizes) <= 2 * share + max(len(p.text) for p in paras)


def test_partition_refuses_more_segments_than_paragraphs():
    with pytest.raises(SystemExit):
        c.partition([c.Paragraph(0, 5, "a")], 2)


def test_manifest_and_segment_dump_keep_text_out_and_in_respectively(tmp_path):
    paras = [c.Paragraph(i, 5, f"para {i} " * 5) for i in range(6)]
    corpus = c.Corpus("t", "a", "s", paras, c.partition(paras, 2))
    m = json.loads(c.write_manifest(corpus, tmp_path / "m.json").read_text())
    assert m["n_segments"] == 2 and "units" not in m["segments"][0]
    assert "para 0" not in (tmp_path / "m.json").read_text()
    d = json.loads(c.dump_segments(corpus, tmp_path / "s.json").read_text())
    assert d["segments"][0]["units"][0]["ru"].startswith("para 0")


def test_agenda_puts_contested_researchable_terms_first_and_caps():
    terms = {t: {"term": t, "needs_research": t != "easy", "proposed": {"en": t}}
             for t in ("zeta", "alpha", "easy", "mid")}
    agenda = h._agenda_of(terms, {}, {"mid": [1, 2]}, cap=2)
    assert [a["term"] for a in agenda] == ["mid", "alpha"]
    assert agenda[0]["contested"] and not agenda[1]["contested"]


def test_relevant_glossary_filters_by_segment_text_and_keeps_researched():
    glossary = {f"term{i}": {"en": str(i)} for i in range(300)}
    glossary["Дуров"] = {"en": "Durov"}
    glossary["Зингер"] = {"en": "Singer"}
    units = [{"ru": "Павел Дуров вышел из дома Зингера."}]
    out = h._relevant_glossary(glossary, units, researched={"term7"})
    assert set(out) >= {"Дуров", "term7"}
    assert "term8" not in out and len(out) <= h.GLOSSARY_CAP


def test_align_marks_missing_paragraphs_instead_of_dropping_them():
    src = [{"i": 3, "page": 5, "chapter": "ch1", "ru": "a"}, {"i": 4, "page": 5, "chapter": "ch1", "ru": "b"}]
    out = h._align(src, [{"i": 4, "en": "B", "zh": "乙"}], ["en", "zh"])
    assert out[0]["missing"] and out[0]["en"] == "" and out[1]["en"] == "B"
    assert "missing" not in out[1]


def _run(tmp_path: Path, **kw) -> dict:
    args = ["run", "--name", "t", "--executor", "stub", "--launch", "threads",
            "--run-dir", str(tmp_path / "run"), "--work-dir", str(tmp_path / "work"),
            "--phase-timeout", "120", "-q", "--last-page", "8"]
    for k, v in kw.items():
        args += [f"--{k.replace('_', '-')}", str(v)]
    return h.main(args)


@pytest.mark.slow
def test_stub_run_exercises_every_collective_and_assembles(tmp_path):
    summary = _run(tmp_path, size=4)
    assert summary["launch"]["failed"] == 0
    assert summary["book"]["segments"] == 4 and summary["book"]["missing"] == 0
    assert summary["evidence"]["glossary_terms"] > 0 and summary["evidence"]["findings"] > 0
    assert summary["evidence"]["amendments"] > 0
    trace = (tmp_path / "run" / "harness.trace.jsonl").read_text().splitlines()
    kinds = {json.loads(line)["kind"] for line in trace}
    for k in ("bcast", "scatter", "allreduce", "barrier", "win.cas", "win.fence", "exscan",
              "neighbor_allgather", "gather", "win.lock", "win.unlock", "op.arbitrate"):
        assert k in kinds, k
    # the arbitration was agent-evaluated, spread over the ranks, and committed once
    reports = [json.loads(p.read_text()) for p in (tmp_path / "work" / "out").glob("report*.json")]
    assert sum(r.get("arbitrated_census", 0) for r in reports) >= 1
    assert next(r for r in reports if r["rank"] == 0)["rulings_census"] >= 1
    assert sum(1 for line in trace if '"kind": "op.arbitrate"' in line) >= 1
    assert (tmp_path / "work" / "out" / "book.parallel.md").exists()
    assert (tmp_path / "run" / "glossary.json").exists()
    assert not (tmp_path / "run" / "out").exists(), "no corpus text is promoted into the run dir"


@pytest.mark.slow
def test_ablations_remove_only_their_mechanism(tmp_path):
    s = _run(tmp_path, size=3, arm="noseams")
    trace = (tmp_path / "run" / "harness.trace.jsonl").read_text()
    assert '"kind": "neighbor_allgather"' not in trace and s["book"]["missing"] == 0
    s = _run(tmp_path, size=3, arm="noglossary")
    assert "glossary_terms" not in s["evidence"]
