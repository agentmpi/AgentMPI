"""The E6 corpus: the legacy project's page extraction, partitioned by rank.

The source is N. V. Kononov, *Код Дурова* (Mann, Ivanov & Ferber, 2013), by way
of the page-level text extraction that the legacy translation project this
experiment replaces published in its repository.  It is the right corpus for the
question: rendering it is a comparative literary and historical problem rather
than a lexical one --- period slang, institutional names, internet-culture
allusions, a preface full of metaphors --- so the terminology coupling between
segments is real, and that coupling is exactly what the reductions and the
shared-research window exist to manage.

The text is never vendored here.  Each machine clones the legacy repository at
run time (a git read is the one network operation every sandbox can perform)
into an untracked working directory; what this repository carries is a
*manifest* --- per page a chapter, a character and token count, and a digest of
the exact bytes a rank was given --- which is enough to check that two runs at
different scales cut the same book and that every rank translated what it was
handed.

The unit of work is the **page**, because that is the unit of the deliverable:
the legacy project's output schema is one JSON file per page, sentence-aligned
across four languages, which its PDF compiler consumes.  Pages are assigned to
ranks as *contiguous* segments, not strided: the difficulty of literary text is
local (a scene, an argument, a run of dialogue), and contiguity is what gives the
seam exchange something to reconcile.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ampi.tokens import count_tokens

__all__ = [
    "LEGACY_REPO", "Page", "Corpus", "ensure_legacy", "load_pages", "partition",
    "seed_glossary", "build", "manifest", "write_manifest",
]

LEGACY_REPO = "https://github.com/XinShuo-ph/durov_code_translation_multi_agent"
ENV_LEGACY_DIR = "E6_LEGACY_DIR"
N_PAGES = 99
TITLE = "Код Дурова. Реальная история «ВКонтакте» и ее создателя"
AUTHOR = "Николай В. Кононов"

#: (first page, last page, chapter number, working title, page type).  The
#: chapter numbers and ranges come from the legacy project's STATE.md; the titles
#: of chapters 2-7 are not in that file, so the survey phase reports the titles
#: it finds at chapter openings and the population registers them under a lock.
CHAPTERS: tuple[tuple[int, int, int, str, str], ...] = (
    (1, 4, 0, "Титульный лист и содержание (Front matter)", "front_matter"),
    (5, 6, 0, "Предисловие (Preface)", "narrative"),
    (7, 12, 0, "Пролог (Prologue)", "narrative"),
    (13, 22, 1, "Ботанический сад (Botanical Garden)", "narrative"),
    (23, 37, 2, "Глава 2 (University years)", "narrative"),
    (38, 50, 3, "Глава 3 (The founding of VKontakte)", "narrative"),
    (51, 64, 4, "Глава 4 (Growth)", "narrative"),
    (65, 78, 5, "Глава 5 (Conflicts)", "narrative"),
    (79, 91, 6, "Глава 6 (Philosophy)", "narrative"),
    (92, 98, 7, "Глава 7 (The future)", "narrative"),
    (99, 99, 8, "Об авторе (About the author)", "about_author"),
)

#: The running header the extraction repeats on every page.  Left in, ninety-nine
#: ranks would each propose a rendering for the book's own title and manufacture
#: a terminology conflict that is an artefact of the PDF.
_HEADER = re.compile(r"^\s*Н\.\s*В\.\s*Кононов\.\s*«Код Дурова.*$", re.M)


@dataclass
class Page:
    n: int
    text: str = field(repr=False)
    chapter: int
    chapter_title: str
    page_type: str

    @property
    def chars(self) -> int:
        return len(self.text)

    @property
    def tokens(self) -> int:
        return count_tokens(self.text)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:16]

    def metadata(self) -> dict[str, Any]:
        """Everything about the page except the page."""
        return {"page": self.n, "chapter": self.chapter, "chapter_title": self.chapter_title,
                "page_type": self.page_type, "chars": self.chars, "tokens": self.tokens,
                "sha256_16": self.digest}


@dataclass
class Corpus:
    pages: dict[int, Page]
    segments: list[list[int]]
    seed_glossary: dict[str, dict[str, str]]
    summaries: str
    legacy_dir: str
    legacy_commit: str

    @property
    def size(self) -> int:
        return len(self.segments)

    def segment_of(self, rank: int) -> list[int]:
        return list(self.segments[rank])

    def owner_of(self, page: int) -> int:
        for r, seg in enumerate(self.segments):
            if page in seg:
                return r
        raise KeyError(page)


def chapter_of(n: int) -> tuple[int, str, str]:
    for first, last, ch, title, kind in CHAPTERS:
        if first <= n <= last:
            return ch, title, kind
    return 0, "", "narrative"


# ---------------------------------------------------------------------------
# Fetching and cleaning
# ---------------------------------------------------------------------------


def ensure_legacy(work_dir: str | Path, legacy_dir: str | Path | None = None) -> Path:
    """Return a checkout of the legacy repository, cloning it if necessary.

    Cloned rather than fetched file by file: the sandboxes these ranks run in
    admit git traffic to the hosting service and little else, and one shallow
    clone is also simply faster than a hundred HTTP requests.
    """
    candidate = legacy_dir or os.environ.get(ENV_LEGACY_DIR)
    if candidate:
        p = Path(candidate)
        if (p / "extracted" / "pages").is_dir():
            return p
        raise FileNotFoundError(f"{p} has no extracted/pages directory")
    target = Path(work_dir) / "legacy"
    if not (target / "extracted" / "pages").is_dir():
        target.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, GIT_LFS_SKIP_SMUDGE="1")
        subprocess.run(["git", "clone", "-q", "--depth", "1", LEGACY_REPO, str(target)],
                       check=True, env=env)
    return target


def legacy_commit(legacy: Path) -> str:
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(legacy),
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def clean(raw: str) -> str:
    """Strip the running header and restore paragraphs.

    The extraction wraps at the PDF's line width and marks a paragraph's first
    line with a deep indent, so a paragraph arrives as one indented line followed
    by flush continuation lines.  Rejoining them gives the translator (and the
    seam exchange) the unit they actually work in.
    """
    raw = _HEADER.sub("", raw)
    paras: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            if paras and paras[-1] != "":
                paras.append("")
            continue
        new_para = bool(re.match(r"^\s{3,}\S", line)) or not paras or paras[-1] == ""
        if new_para:
            paras.append(stripped)
        else:
            paras[-1] = paras[-1] + " " + stripped
    text = "\n\n".join(p for p in paras if p)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def load_pages(legacy: Path) -> dict[int, Page]:
    pages: dict[int, Page] = {}
    for n in range(1, N_PAGES + 1):
        f = legacy / "extracted" / "pages" / f"page_{n:03d}.txt"
        if not f.exists():
            continue
        text = clean(f.read_text(encoding="utf-8", errors="replace"))
        if len(text) < 40:
            # Page 1 of the extraction is empty; a rank handed it would have
            # nothing to translate and everything to distort in a load measure.
            continue
        ch, title, kind = chapter_of(n)
        pages[n] = Page(n, text, ch, title, kind)
    return pages


# ---------------------------------------------------------------------------
# Partitioning
# ---------------------------------------------------------------------------


def partition(pages: dict[int, Page], size: int) -> list[list[int]]:
    """Contiguous segments balanced by character count, one per rank.

    Balanced by characters rather than by page count because pages are uneven
    (a chapter's last page may be a third full) and the translation cost is in
    the characters.  Contiguous because the seam exchange needs neighbours to be
    neighbours in the book.  Every rank gets at least one page; a population
    larger than the book is refused rather than padded with idle ranks.
    """
    order = sorted(pages)
    if size > len(order):
        raise ValueError(f"{size} ranks but only {len(order)} pages")
    if size <= 0:
        raise ValueError("size must be positive")
    total = sum(pages[n].chars for n in order)
    segments: list[list[int]] = []
    i = 0
    acc = 0
    for r in range(size):
        remaining_ranks = size - r
        remaining_pages = len(order) - i
        seg = [order[i]]
        acc += pages[order[i]].chars
        i += 1
        target = (r + 1) * total / size
        # Take pages while under target, but never so many that a later rank is
        # left without one.
        while i < len(order) and (len(order) - i) > (remaining_ranks - 1) \
                and acc + pages[order[i]].chars / 2 < target:
            seg.append(order[i])
            acc += pages[order[i]].chars
            i += 1
        # The last rank takes whatever is left.
        if r == size - 1:
            while i < len(order):
                seg.append(order[i])
                i += 1
        segments.append(seg)
        del remaining_pages
    return segments


# ---------------------------------------------------------------------------
# The legacy project's research, as seed material
# ---------------------------------------------------------------------------


def seed_glossary(legacy: Path) -> dict[str, dict[str, str]]:
    """Parse the legacy glossary's markdown tables into ``term -> renderings``.

    Seed material, not a binding glossary.  The population surveys the book
    itself and decides; the seed is what a translator would have been handed on
    day one, and the survey prompt says so.
    """
    f = legacy / "research" / "glossary.md"
    if not f.exists():
        return {}
    out: dict[str, dict[str, str]] = {}
    for line in f.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4 or cells[0] in ("Russian", "") or set(cells[0]) <= {"-", " "}:
            continue
        ru, en, zh, ja = cells[0], cells[1], cells[2], cells[3]
        notes = cells[4] if len(cells) > 4 else ""
        out[ru] = {"en": en, "zh": zh, "ja": ja, "notes": notes}
    return out


def chapter_summaries(legacy: Path, *, limit: int = 7000) -> str:
    f = legacy / "research" / "chapter_summaries.md"
    if not f.exists():
        return ""
    return f.read_text(encoding="utf-8")[:limit]


# ---------------------------------------------------------------------------
# Building and describing a corpus
# ---------------------------------------------------------------------------


def parse_pages(spec: str | None) -> set[int] | None:
    """``"13-16,40"`` -> ``{13, 14, 15, 16, 40}``; empty means the whole book."""
    if not spec:
        return None
    out: set[int] = set()
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out


def build(work_dir: str | Path, size: int, *, legacy_dir: str | Path | None = None,
          pages: str | None = None) -> Corpus:
    """Build the corpus.  ``pages`` restricts it to a subset, for smoke tests."""
    legacy = ensure_legacy(work_dir, legacy_dir)
    loaded = load_pages(legacy)
    keep = parse_pages(pages)
    if keep is not None:
        loaded = {n: p for n, p in loaded.items() if n in keep}
        if not loaded:
            raise ValueError(f"no pages match {pages!r}")
    pages_ = loaded
    return _build(pages_, size, legacy)


def _build(pages: dict[int, Page], size: int, legacy: Path) -> Corpus:
    return Corpus(
        pages=pages,
        segments=partition(pages, size),
        seed_glossary=seed_glossary(legacy),
        summaries=chapter_summaries(legacy),
        legacy_dir=str(legacy),
        legacy_commit=legacy_commit(legacy),
    )


def manifest(corpus: Corpus) -> dict[str, Any]:
    return {
        "title": TITLE,
        "author": AUTHOR,
        "source": LEGACY_REPO,
        "source_commit": corpus.legacy_commit,
        "rights": "in copyright; cloned from the legacy project at run time, not vendored",
        "n_pages": len(corpus.pages),
        "n_segments": corpus.size,
        "total_chars": sum(p.chars for p in corpus.pages.values()),
        "total_tokens": sum(p.tokens for p in corpus.pages.values()),
        "seed_terms": len(corpus.seed_glossary),
        "segments": [
            {"rank": r, "pages": seg, "chars": sum(corpus.pages[n].chars for n in seg),
             "tokens": sum(corpus.pages[n].tokens for n in seg)}
            for r, seg in enumerate(corpus.segments)
        ],
        "pages": [corpus.pages[n].metadata() for n in sorted(corpus.pages)],
    }


def write_manifest(corpus: Corpus, path: str | Path) -> Path:
    m = manifest(corpus)
    for entry in m["pages"]:
        if "text" in entry:  # pragma: no cover - the guard the policy rests on
            raise RuntimeError("refusing to write page text into the manifest")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
    return p
