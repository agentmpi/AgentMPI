from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from ampi.analysis import analyse_path
from experiments.e3_durov.harness import SUPPORTED_RANKS, build_parser, main
from experiments.e3_durov.prepare_corpus import RESEARCH_FILES, _public_repo_url, prepare_corpus
from experiments.e3_durov.summarize import summarize


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _corpus(path: Path) -> Path:
    pages = [
        {
            "page": page,
            "source_path": f"extracted/pages/page_{page:03d}.txt",
            "sha256": _sha(f"Источник {page}."),
            "text": f"Источник {page}.",
        }
        for page in range(1, 100)
    ]
    corpus = {
        "schema": "ampi.durov-corpus/v1",
        "title": "test",
        "author": "test",
        "provenance": {
            "source_repo_url": "https://example.com/source.git",
            "source_commit": "a" * 40,
            "page_count": 99,
            "translations_imported": False,
        },
        "pages": pages,
        "research": [
            {
                "name": "glossary",
                "source_path": "research/glossary.md",
                "sha256": _sha("Research fixture."),
                "text": "Research fixture.",
            }
        ],
    }
    path.write_text(json.dumps(corpus, ensure_ascii=False), encoding="utf-8")
    return path


def test_prepare_corpus_imports_only_source_and_records_git_provenance(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    pages = legacy / "extracted" / "pages"
    research = legacy / "research"
    translations = legacy / "translations"
    pages.mkdir(parents=True)
    research.mkdir()
    translations.mkdir()
    for page in range(1, 100):
        (pages / f"page_{page:03d}.txt").write_text(f"Страница {page}", encoding="utf-8")
    for name in RESEARCH_FILES:
        (research / name).write_text(f"# {name}", encoding="utf-8")
    (translations / "page_001.json").write_text('{"copied":false}', encoding="utf-8")

    subprocess.run(["git", "init", "-q", str(legacy)], check=True)
    subprocess.run(["git", "-C", str(legacy), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(legacy), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(["git", "-C", str(legacy), "add", "."], check=True)
    subprocess.run(["git", "-C", str(legacy), "commit", "-qm", "fixture"], check=True)

    output = tmp_path / "source" / "corpus.json"
    corpus = prepare_corpus(
        legacy,
        output,
        source_repo_url="https://example.com/legacy.git",
    )

    assert len(corpus["pages"]) == 99
    assert len(corpus["research"]) == len(RESEARCH_FILES)
    assert corpus["provenance"]["source_repo_url"] == "https://example.com/legacy.git"
    assert len(corpus["provenance"]["source_commit"]) == 40
    assert corpus["provenance"]["translations_imported"] is False
    assert "copied" not in output.read_text(encoding="utf-8")


def test_prepare_corpus_rejects_incomplete_page_set(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    (legacy / "extracted" / "pages").mkdir(parents=True)
    (legacy / "research").mkdir()
    for name in RESEARCH_FILES:
        (legacy / "research" / name).write_text("fixture", encoding="utf-8")

    with pytest.raises(ValueError, match="page_001.txt"):
        prepare_corpus(legacy, tmp_path / "corpus.json", source_repo_url="https://example.com")


def test_provenance_strips_credentials_from_https_remotes() -> None:
    assert (
        _public_repo_url("https://x-access-token:secret@github.com/example/project.git")
        == "https://github.com/example/project.git"
    )
    assert _public_repo_url("git@github.com:example/project.git") == (
        "git@github.com:example/project.git"
    )


def test_stub_run_exercises_production_protocol_and_assembles_pages(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _corpus(source / "durov_corpus.json")
    run_dir = tmp_path / "run"

    report = main(
        [
            "--name",
            "test",
            "--source-dir",
            str(source),
            "--run-dir",
            str(run_dir),
            "--size",
            "16",
            "--executor",
            "stub",
            "--test-stub",
            "--device",
            "memory",
            "--task-timeout",
            "2",
            "--phase-timeout",
            "10",
        ]
    )

    assert report["succeeded"] == 16
    assert report["failed"] == 0
    assert report["assembled_pages"] == 99
    assert report["stub"] is True
    events = report["events"]
    for kind in (
        "bcast",
        "scatter",
        "allreduce",
        "op.arbitrate",
        "barrier",
        "gather",
        "win.accumulate",
        "win.cas",
        "win.lock",
        "win.unlock",
        "win.fence",
    ):
        assert events[kind] > 0

    rows = [
        json.loads(line)
        for line in (run_dir / "assembled.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [row["page"] for row in rows] == list(range(1, 100))
    assert all(row["reviewer_rank"] != row["author_rank"] for row in rows)
    assert len(list((run_dir / "out").glob("rank_*.json"))) == 16
    launch = json.loads((run_dir / "launch_plan.json").read_text(encoding="utf-8"))
    assert launch["supported_sizes"] == [16, 32, 64]
    assert launch["bounds"]["contracts"]["translation"]["max_tokens"] == 12000
    assert len(launch["executors"]) == 10
    assert sorted(rank for executor in launch["executors"] for rank in executor["serves"]) == (
        list(range(16))
    )
    assert all(
        command["next_command"].startswith(f"AMPI_WORKER_ID={command['worker_id']} ")
        for command in launch["executors"]
    )

    analysis_dir = run_dir / "analysis"
    analysis_dir.mkdir()
    (analysis_dir / "metrics.json").write_text(
        json.dumps(analyse_path(run_dir / "harness.trace.jsonl").as_dict()),
        encoding="utf-8",
    )
    public = summarize(run_dir)
    assert public["artifact"]["complete"] is True
    assert public["artifact"]["pages"] == 99
    assert public["source"]["licensed_payloads_committed"] is False


def test_stub_requires_explicit_test_flag_and_rank_choices() -> None:
    args = build_parser().parse_args(["--name", "x", "--executor", "stub"])
    assert args.test_stub is False
    assert SUPPORTED_RANKS == (16, 32, 64)
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--name", "x", "--size", "8"])
