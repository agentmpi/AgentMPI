"""The E6 corpus: the same book as E3, cut at the paragraph so that p can reach 256.

E3 partitioned the book by page and therefore could not run more ranks than there
are prose pages (95).  This experiment asks how the protocol behaves as the
population grows past the point where the work units are small, so the unit is
the *paragraph*: the extraction marks one with a hanging indent, which survives
the PDF-to-text conversion, and a paragraph is also the unit a translator and a
seam exchange both work in.  The book has about six hundred of them, so a rank at
p=256 receives two or three, and at p=16 about forty.

Everything else is as in E3, and for the same reasons.  Segments are contiguous,
balanced on characters, and never empty.  The source is in copyright; it is read
from a local checkout of the legacy project or fetched into an untracked working
directory, and the repository carries only a manifest --- index, page range,
paragraph range, sizes, and a digest of the exact bytes each rank received.  The
manifest writer refuses segment text mechanically.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ampi.tokens import count_tokens

__all__ = ["Paragraph", "Segment", "Corpus", "build", "write_manifest", "chapter_of"]

#: Pinned to the commit this experiment read, so every node cuts the same bytes.
LEGACY_COMMIT = "a616037cda8f47c4fb4be7c0335ff32c929c71cc"
RAW = (
    "https://raw.githubusercontent.com/XinShuo-ph/durov_code_translation_multi_agent/"
    f"{LEGACY_COMMIT}/extracted/pages/page_{{n:03d}}.txt"
)
LOCAL_PAGES = Path("/home/user/xinshuo-ph/durov_code_translation_multi_agent/extracted/pages")
N_PAGES = 99
FIRST_PROSE_PAGE = 5
TITLE = "Код Дурова. Реальная история «ВКонтакте» и ее создателя"
AUTHOR = "Николай В. Кононов"

#: Chapter boundaries by page, from the legacy project's ``chapter_structure.md``.
CHAPTERS: list[tuple[int, int, str, str]] = [
    (5, 6, "preface", "Предисловие"),
    (7, 12, "prologue", "Пролог"),
    (13, 22, "ch1", "Глава 1. Ботанический сад"),
    (23, 37, "ch2", "Глава 2"),
    (38, 50, "ch3", "Глава 3"),
    (51, 64, "ch4", "Глава 4"),
    (65, 78, "ch5", "Глава 5"),
    (79, 91, "ch6", "Глава 6"),
    (92, 98, "ch7", "Глава 7"),
    (99, 99, "about", "Об авторе"),
]

_HEADER = re.compile(r"^\s*Н\.\s*В\.\s*Кононов\..*$", re.M)
_PAGE_NUMBER = re.compile(r"^\s*\d{1,3}\s*$")
_PARA_START = re.compile(r"^\s{3,}\S")


def chapter_of(page: int) -> str:
    for lo, hi, key, _title in CHAPTERS:
        if lo <= page <= hi:
            return key
    return "front" if page < FIRST_PROSE_PAGE else "unknown"


@dataclass
class Paragraph:
    index: int
    page: int
    text: str = field(repr=False, default="")

    @property
    def chapter(self) -> str:
        return chapter_of(self.page)


@dataclass
class Segment:
    """One contiguous run of paragraphs, assigned to exactly one rank."""

    index: int
    paragraphs: list[Paragraph] = field(repr=False, default_factory=list)

    @property
    def first_page(self) -> int:
        return self.paragraphs[0].page

    @property
    def last_page(self) -> int:
        return self.paragraphs[-1].page

    @property
    def text(self) -> str:
        return "\n\n".join(p.text for p in self.paragraphs)

    @property
    def chars(self) -> int:
        return len(self.text)

    @property
    def tokens(self) -> int:
        return count_tokens(self.text)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:16]

    @property
    def chapters(self) -> list[str]:
        out: list[str] = []
        for p in self.paragraphs:
            if p.chapter not in out:
                out.append(p.chapter)
        return out

    def metadata(self) -> dict[str, Any]:
        """Everything about this segment except the segment."""
        return {
            "index": self.index,
            "pages": [self.first_page, self.last_page],
            "paragraphs": [self.paragraphs[0].index, self.paragraphs[-1].index],
            "n_paragraphs": len(self.paragraphs),
            "chapters": self.chapters,
            "chars": self.chars,
            "tokens": self.tokens,
            "sha256_16": self.digest,
        }

    def payload(self) -> dict[str, Any]:
        """What the root scatters to this segment's rank: metadata plus the text."""
        return {
            **self.metadata(),
            "units": [{"i": p.index, "page": p.page, "chapter": p.chapter, "ru": p.text}
                      for p in self.paragraphs],
        }


@dataclass
class Corpus:
    title: str
    author: str
    source: str
    paragraphs: list[Paragraph]
    segments: list[Segment]

    @property
    def size(self) -> int:
        return len(self.segments)

    def metadata(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "author": self.author,
            "source": self.source,
            "rights": (
                "in copyright; read from a local checkout or fetched at run time and not "
                "redistributed. This manifest carries segment metadata only."
            ),
            "n_paragraphs": len(self.paragraphs),
            "n_segments": self.size,
            "total_chars": sum(s.chars for s in self.segments),
            "total_tokens": sum(s.tokens for s in self.segments),
            "unit": "paragraph",
            "segments": [s.metadata() for s in self.segments],
        }


# ---------------------------------------------------------------------------
# Reading and cleaning
# ---------------------------------------------------------------------------


def read_pages(work_dir: str | Path, *, source_dir: str | Path | None = None,
               pages: int = N_PAGES) -> list[str]:
    """Read the page extraction: a local checkout if present, else a cached fetch."""
    src = Path(source_dir) if source_dir else LOCAL_PAGES
    cache = Path(work_dir) / "pages"
    cache.mkdir(parents=True, exist_ok=True)
    out: list[str] = []
    for n in range(1, pages + 1):
        local = cache / f"page_{n:03d}.txt"
        if not local.exists():
            here = src / f"page_{n:03d}.txt"
            if here.exists():
                local.write_bytes(here.read_bytes())
            else:
                with urllib.request.urlopen(RAW.format(n=n), timeout=60) as fh:  # noqa: S310
                    local.write_bytes(fh.read())
        out.append(local.read_text(encoding="utf-8", errors="replace"))
    return out


def paragraphs_of(page_text: str, page: int, start_index: int) -> list[Paragraph]:
    """Split one extracted page into paragraphs.

    The extraction wraps every paragraph at the PDF's line width and marks a new
    paragraph with a hanging indent of several spaces.  Lines that are only a page
    number are dropped, and the running header is removed before anything else.
    A page's first lines may continue a paragraph from the previous page; they are
    kept as their own paragraph, because joining across pages would need the
    previous page in hand and the error --- one paragraph rendered as two --- is
    invisible to a reader once the translation is assembled.
    """
    text = _HEADER.sub("", page_text)
    paras: list[str] = []
    buf: list[str] = []
    for line in text.splitlines():
        if not line.strip() or _PAGE_NUMBER.match(line):
            continue
        if _PARA_START.match(line) and buf:
            paras.append(" ".join(buf))
            buf = []
        buf.append(line.strip())
    if buf:
        paras.append(" ".join(buf))
    cleaned = [re.sub(r"\s+", " ", p).strip() for p in paras]
    return [Paragraph(start_index + i, page, p) for i, p in enumerate(cleaned) if p]


def partition(paragraphs: list[Paragraph], size: int) -> list[Segment]:
    """Cut the paragraphs into ``size`` contiguous, character-balanced segments.

    Recomputes the target from what is left so dense early pages do not starve
    the tail, and never closes a segment when the paragraphs left cannot fill the
    segments left, so no rank is empty.  An empty rank still enters every
    collective and is indistinguishable in the trace from a rank whose executor
    died --- the one confusion this experiment must not manufacture.
    """
    if size > len(paragraphs):
        raise SystemExit(
            f"cannot cut {len(paragraphs)} paragraphs into {size} segments; use a smaller --size"
        )
    total = sum(len(p.text) for p in paragraphs)
    segments: list[Segment] = []
    bucket: list[Paragraph] = []
    acc = 0
    consumed = 0
    for i, para in enumerate(paragraphs):
        bucket.append(para)
        acc += len(para.text)
        consumed += len(para.text)
        left = len(paragraphs) - i - 1
        slots_left = size - len(segments) - 1
        target = (total - (consumed - acc)) / max(1, size - len(segments))
        if slots_left > 0:
            has_share = acc >= target and left > slots_left
            must_close = left == slots_left
            if has_share or must_close:
                segments.append(Segment(len(segments), bucket))
                bucket, acc = [], 0
    if bucket:
        segments.append(Segment(len(segments), bucket))
    if len(segments) != size:  # pragma: no cover - guarded above
        raise SystemExit(f"partition produced {len(segments)} segments, expected {size}")
    return segments


def build(work_dir: str | Path, size: int, *, source_dir: str | Path | None = None,
          pages: int = N_PAGES, first_page: int = FIRST_PROSE_PAGE,
          last_page: int | None = None) -> Corpus:
    raw = read_pages(work_dir, source_dir=source_dir, pages=pages)
    paragraphs: list[Paragraph] = []
    for n, page in enumerate(raw, start=1):
        if n < first_page or (last_page is not None and n > last_page):
            continue
        paragraphs.extend(paragraphs_of(page, n, len(paragraphs)))
    return Corpus(
        title=TITLE, author=AUTHOR,
        source=f"XinShuo-ph/durov_code_translation_multi_agent@{LEGACY_COMMIT[:12]} "
               "extracted/pages",
        paragraphs=paragraphs, segments=partition(paragraphs, size),
    )


def write_manifest(corpus: Corpus, path: str | Path) -> Path:
    """Write segment metadata.  Never segment text."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = corpus.metadata()
    if any("text" in s or "units" in s for s in payload["segments"]):  # pragma: no cover
        raise SystemExit("refusing to write a manifest containing corpus text")
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def dump_segments(corpus: Corpus, path: str | Path) -> Path:
    """Write the full partition, text included, into the *untracked* working directory.

    Every rank process reads its slice from this file rather than re-cutting the
    book, so the partition a run used is one artifact and not p re-derivations
    that happen to agree.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"meta": {k: v for k, v in corpus.metadata().items()
                                      if k != "segments"},
                             "segments": [s.payload() for s in corpus.segments]},
                            ensure_ascii=False), encoding="utf-8")
    return p


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="cut the E6 corpus and describe the partition")
    ap.add_argument("--work-dir", default="work/e6")
    ap.add_argument("--size", type=int, required=True)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    c = build(a.work_dir, a.size)
    if a.out:
        write_manifest(c, a.out)
    print(json.dumps({k: v for k, v in c.metadata().items() if k != "segments"}, indent=2,
                     ensure_ascii=False))
    for s in c.segments:
        print(f"  seg{s.index:>3}  pages {s.first_page:>3}-{s.last_page:<3} paras "
              f"{s.paragraphs[0].index:>3}-{s.paragraphs[-1].index:<3} {s.chars:>6} chars "
              f"{s.tokens:>5} tokens  {s.digest}")
