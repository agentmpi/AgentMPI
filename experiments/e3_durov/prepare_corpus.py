"""Import the authorized Durov source checkout into a compact, provenance-rich corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

PAGE_COUNT = 99
RESEARCH_FILES = (
    "chapter_structure.md",
    "chapter_summaries.md",
    "durov_bio.md",
    "glossary.md",
    "russia_context.md",
    "vk_history.md",
)


def _git(checkout: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(checkout), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _public_repo_url(url: str) -> str:
    """Strip credentials from an HTTP(S) git remote before recording provenance."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        return url
    host = parsed.hostname
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, parsed.fragment))


def prepare_corpus(
    legacy_checkout: Path,
    output: Path,
    *,
    source_repo_url: str | None = None,
) -> dict[str, Any]:
    """Copy source texts, never translations, and bind them to their git provenance."""
    checkout = legacy_checkout.resolve()
    pages_dir = checkout / "extracted" / "pages"
    expected = {f"page_{page:03d}.txt" for page in range(1, PAGE_COUNT + 1)}
    found = {path.name for path in pages_dir.glob("page_*.txt")}
    missing = sorted(expected - found)
    extra = sorted(found - expected)
    if missing or extra:
        raise ValueError(
            f"expected exactly pages 001-099; missing={missing or 'none'}, "
            f"extra={extra or 'none'}"
        )

    research_dir = checkout / "research"
    absent_research = [name for name in RESEARCH_FILES if not (research_dir / name).is_file()]
    if absent_research:
        raise ValueError(f"missing required research files: {absent_research}")

    commit = _git(checkout, "rev-parse", "HEAD")
    url = source_repo_url
    if url is None:
        try:
            url = _git(checkout, "remote", "get-url", "origin")
        except subprocess.CalledProcessError as exc:
            raise ValueError("source repository URL is required when origin is unavailable") from exc
    url = _public_repo_url(url)

    pages = []
    for page in range(1, PAGE_COUNT + 1):
        relative = f"extracted/pages/page_{page:03d}.txt"
        text = (checkout / relative).read_text(encoding="utf-8")
        pages.append(
            {
                "page": page,
                "source_path": relative,
                "sha256": _sha256(text),
                "text": text,
            }
        )

    research = []
    for name in RESEARCH_FILES:
        relative = f"research/{name}"
        text = (checkout / relative).read_text(encoding="utf-8")
        research.append(
            {
                "name": name.removesuffix(".md"),
                "source_path": relative,
                "sha256": _sha256(text),
                "text": text,
            }
        )

    corpus = {
        "schema": "ampi.durov-corpus/v1",
        "title": "Код Дурова. Реальная история «ВКонтакте» и ее создателя",
        "author": "Николай В. Кононов",
        "provenance": {
            "source_repo_url": url,
            "source_commit": commit,
            "legacy_checkout": str(checkout),
            "page_count": PAGE_COUNT,
            "research_files": list(RESEARCH_FILES),
            "translations_imported": False,
        },
        "pages": pages,
        "research": research,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(corpus, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return corpus


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("legacy_checkout", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source-repo-url")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    corpus = prepare_corpus(
        args.legacy_checkout,
        args.output,
        source_repo_url=args.source_repo_url,
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "pages": len(corpus["pages"]),
                "research_files": len(corpus["research"]),
                "provenance": corpus["provenance"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
