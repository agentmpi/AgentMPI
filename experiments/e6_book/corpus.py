"""The E6 corpora: a book, partitioned by page and by rank.

Two sources, selected by ``Config.corpus``:

``chairs``  Ilf and Petrov, *Двенадцать стульев* (1928), Part One (chapters
            I–XXI), from Russian Wikisource.  The production series runs on
            this.  It is in the public domain (both authors died before 1943;
            first published 1928), and it is exactly as hard as the task needs:
            NEP-era institutions and acronyms, Odessa and Moscow slang, a
            narrator's deadpan irony, jokes that turn on 1920s Soviet realia
            and on pre-revolutionary ones, verse, and allusions a Russian
            reader of 1928 caught without help.  Rendering it is a comparative
            literary and historical problem, which is what makes the
            terminology coupling between pages real rather than contrived.
``durov``   N. V. Kononov, *Код Дурова* (2013), by way of the page extraction
            in the legacy translation project this experiment replaces.  The
            harness was written against it and stays compatible with it, so a
            rights holder can run the same series on it.  The series was not
            run on it here: when asked for a sentence-by-sentence rendering of
            a page as one of a hundred being translated in parallel, the
            executors declined to reproduce an in-copyright book in full, and
            that judgement was not engineered around.  See the README.

Neither text is vendored.  Each machine fetches its corpus at run time into an
untracked working directory (a git clone for ``durov``; forty raw-wikitext
requests for ``chairs``, cached), and what the repository carries is a
*manifest*: per page a chapter, a character and token count, and a digest of
the exact bytes a rank was given, which is enough to show two runs cut the
same book and that every rank translated what it was handed.

The unit of work is the **page**, because that is the unit of the legacy
deliverable: one JSON file per page, sentence-aligned across four languages,
which its PDF compiler consumes.  Pages are assigned to ranks as *contiguous*
segments balanced by character count, not strided: the difficulty of literary
text is local, and contiguity is what gives the seam exchange something to
reconcile.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ampi.tokens import count_tokens

__all__ = [
    "LEGACY_REPO", "SOURCES", "Page", "Corpus", "ensure_legacy", "load_pages", "partition",
    "seed_glossary", "build", "manifest", "write_manifest", "parse_pages", "clean", "wikitext_to_text",
]

LEGACY_REPO = "https://github.com/XinShuo-ph/durov_code_translation_multi_agent"
ENV_LEGACY_DIR = "E6_LEGACY_DIR"
N_PAGES = 99
WIKISOURCE = "https://ru.wikisource.org/w/index.php"
CHAIRS_TITLE = "Двенадцать стульев (Ильф и Петров)"
CHAIRS_CHAPTERS = 21  # Part One, «Старгородский лев»
#: Target page size for a source that has no pages of its own.  The Durov
#: extraction averages 3.3k characters per page, and matching it keeps the two
#: corpora comparable per task.
PAGE_CHARS = 3300

ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII", "XIV",
         "XV", "XVI", "XVII", "XVIII", "XIX", "XX", "XXI", "XXII", "XXIII", "XXIV", "XXV", "XXVI",
         "XXVII", "XXVIII", "XXIX", "XXX", "XXXI", "XXXII", "XXXIII", "XXXIV", "XXXV", "XXXVI",
         "XXXVII", "XXXVIII", "XXXIX", "XL"]

SOURCES: dict[str, dict[str, Any]] = {
    "chairs": {
        "title": "Двенадцать стульев",
        "author": "Илья Ильф и Евгений Петров",
        "year": 1928,
        "rights": "public domain (authors died 1937 and 1942; first published 1928); "
                  "text from Russian Wikisource, licence PD-old-70",
        "source": f"{WIKISOURCE}?title={urllib.parse.quote(CHAIRS_TITLE.replace(' ', '_'))}",
        "brief": """\
## The book

*Двенадцать стульев* (Ilf and Petrov, 1928) is a satirical novel of the NEP
years: a former marshal of the nobility and a young con man chase twelve
dining-room chairs, one of which has a fortune in jewels sewn into its seat,
across provincial Russia and Moscow. It is written in a deadpan comic
narration dense with 1920s Soviet institutions and acronyms, church and
pre-revolutionary vocabulary that the characters half-remember, Odessa and
Moscow slang, bureaucratic jargon, parodies of newspaper prose, songs and
verse, and allusions to Pushkin, Gogol, the Bible and the popular culture of
the day that a Russian reader of 1928 caught without help. Its phrases became
proverbs; a translator must decide which jokes can be carried and which need
a note.

## The reader, and what the translation is for

A multilingual edition: every Russian sentence followed by its English, Chinese
and Japanese renderings, read side by side. The reader is a bilingual or
trilingual technical reader — a graduate student in the sciences who programs
and is curious about Russian culture — not a specialist in Russia. Cultural
references must therefore *carry over*: a Soviet institution, a NEP-era job, a
pre-revolutionary rank, a Petersburg or Odessa turn of phrase each needs a
rendering the target reader understands without a footnote, and a short
translator's note only where no rendering can do that.

The narrators' voice is dry, precise, mock-solemn, and very funny. Keep it.
""",
    },
    "durov": {
        "title": "Код Дурова. Реальная история «ВКонтакте» и ее создателя",
        "author": "Николай В. Кононов",
        "year": 2013,
        "rights": "in copyright; cloned from the legacy project at run time, not vendored",
        "source": LEGACY_REPO,
        "brief": """\
## The book

*Код Дурова* (Nikolai Kononov, 2013) is a reported biography of Pavel Durov and
the early history of VKontakte: non-fiction, close to its subjects, wry, dense
with St Petersburg detail, 2000s Russian internet culture, university and
start-up slang, and metaphors a Russian reader of 2013 caught without help.

## The reader, and what the translation is for

A multilingual edition: every Russian sentence followed by its English, Chinese
and Japanese renderings, read side by side. The reader is a bilingual or
trilingual technical reader — a graduate student in the sciences who programs,
uses Telegram, and is curious about Durov's worldview — not a specialist in
Russia. Cultural references must therefore *carry over*: a Soviet-era housing
term, a Petersburg district, a Russian academic institution, a piece of net
slang each needs a rendering the target reader understands without a footnote,
and a short translator's note only where no rendering can do that.

Durov's voice is sharp, direct, provocative, precise about code. Keep it.
""",
    },
}

#: The Durov extraction's chapter ranges, from the legacy project's STATE.md.
DUROV_CHAPTERS: tuple[tuple[int, int, int, str, str], ...] = (
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

#: The running header the Durov extraction repeats on every page.  Left in,
#: ninety-nine ranks would each propose a rendering for the book's own title.
_HEADER = re.compile(r"^\s*Н\.\s*В\.\s*Кононов\.\s*«Код Дурова.*$", re.M)


@dataclass
class Page:
    n: int
    text: str = field(repr=False)
    chapter: int
    chapter_title: str
    page_type: str
    #: The first page of the page's chapter, which is where the survey reports a
    #: chapter title it found and where the registry keys it.
    chapter_first_page: int = 0

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
                "chapter_first_page": self.chapter_first_page, "page_type": self.page_type,
                "chars": self.chars, "tokens": self.tokens, "sha256_16": self.digest}


@dataclass
class Corpus:
    source: str
    pages: dict[int, Page]
    segments: list[list[int]]
    seed_glossary: dict[str, dict[str, str]]
    summaries: str
    origin: str
    origin_commit: str

    @property
    def title(self) -> str:
        return SOURCES[self.source]["title"]

    @property
    def author(self) -> str:
        return SOURCES[self.source]["author"]

    @property
    def brief(self) -> str:
        return SOURCES[self.source]["brief"]

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


# ---------------------------------------------------------------------------
# Source: durov (the legacy extraction)
# ---------------------------------------------------------------------------


def ensure_legacy(work_dir: str | Path, legacy_dir: str | Path | None = None) -> Path:
    """Return a checkout of the legacy repository, cloning it if necessary."""
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
    """Strip the Durov extraction's running header and restore paragraphs."""
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


def _durov_chapter(n: int) -> tuple[int, str, str, int]:
    for first, last, ch, title, kind in DUROV_CHAPTERS:
        if first <= n <= last:
            return ch, title, kind, first
    return 0, "", "narrative", n


def load_pages_durov(legacy: Path) -> dict[int, Page]:
    pages: dict[int, Page] = {}
    for n in range(1, N_PAGES + 1):
        f = legacy / "extracted" / "pages" / f"page_{n:03d}.txt"
        if not f.exists():
            continue
        text = clean(f.read_text(encoding="utf-8", errors="replace"))
        if len(text) < 40:
            continue
        ch, title, kind, first = _durov_chapter(n)
        pages[n] = Page(n, text, ch, title, kind, first)
    return pages


def seed_glossary(legacy: Path) -> dict[str, dict[str, str]]:
    """Parse the legacy glossary's markdown tables into ``term -> renderings``."""
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
# Source: chairs (Wikisource)
# ---------------------------------------------------------------------------


def fetch_chapters(work_dir: str | Path, *, chapters: int = CHAIRS_CHAPTERS) -> list[str]:
    """Fetch the raw wikitext of the first ``chapters`` chapters, cached on disk."""
    cache = Path(work_dir) / "chairs" / "raw"
    cache.mkdir(parents=True, exist_ok=True)
    out: list[str] = []
    for i in range(1, chapters + 1):
        f = cache / f"chapter_{i:02d}.wiki"
        if not f.exists():
            title = f"{CHAIRS_TITLE}/Глава {ROMAN[i - 1]}"
            url = f"{WIKISOURCE}?title={urllib.parse.quote(title.replace(' ', '_'))}&action=raw"
            req = urllib.request.Request(
                url, headers={"User-Agent": "AgentMPI-E6/1.0 (research corpus fetch)"})
            with urllib.request.urlopen(req, timeout=60) as fh:  # noqa: S310
                f.write_bytes(fh.read())
            time.sleep(0.3)
        out.append(f.read_text(encoding="utf-8"))
    return out


_DROP_TEMPLATES = {"акут", "рамка2", "конец рамки2", "indent", "noindent", "heading",
                   "block center/s", "block center/e", "multicol", "multicol-break",
                   "multicol-end", "^", "отексте"}


def _template(m: re.Match[str]) -> str:
    inner = m.group(1)
    parts = [p for p in inner.split("|")]
    name = parts[0].strip().lower()
    args = parts[1:]
    if name in _DROP_TEMPLATES:
        return ""
    if name == "gap":
        return " "
    if name == "опечатка2":
        return args[-1] if args else ""
    if name == "так в тексте":
        return args[0] if args else ""
    if name == "poemx1":
        return "\n" + "\n".join(a for a in args if "=" not in a[:20]) + "\n"
    positional = [a for a in args if "=" not in a[:20]]
    if not positional:
        return ""
    return max(positional, key=len)


def wikitext_to_text(raw: str) -> tuple[str, str]:
    """Wikitext to plain paragraphs.  Returns ``(chapter title, text)``."""
    s = re.sub(r"^\{\{Отексте.*?\n\}\}\n", "", raw, flags=re.S)
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"<noinclude>.*?</noinclude>", "", s, flags=re.S)
    s = re.sub(r"<ref[^>]*>.*?</ref>", "", s, flags=re.S)
    title_m = re.search(r"^=+\s*(.*?)\s*=+\s*$", s, flags=re.M)
    title = re.sub(r"\s+", " ", title_m.group(1)).strip() if title_m else ""
    s = re.sub(r"^=+.*?=+\s*$", "", s, flags=re.M)
    for _ in range(8):
        s2 = re.sub(r"\{\{([^{}]*)\}\}", _template, s)
        if s2 == s:
            break
        s = s2
    s = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\[\[([^\]]*)\]\]", r"\1", s)
    # Wikitables (a theatre bill, a timetable) keep their cell text and lose
    # their markup: table rows begin with |, !, |- or |} and tables with {|.
    s = re.sub(r"^\{\|.*$|^\|\}\s*$|^\|-.*$", "", s, flags=re.M)
    s = re.sub(r"^[|!]\s*(?:[^|]*\|(?!\|))?", "", s, flags=re.M)
    s = re.sub(r"^[:;#*]+\s*", "", s, flags=re.M)  # list and indent markers
    s = re.sub(r"<br\s*/?>", "\n", s)
    s = re.sub(r"</?(center|small|i|b|poem|div|span|p)[^>]*>", "", s)
    s = s.replace("'''", "").replace("''", "")
    s = s.replace("\xa0", " ").replace("&nbsp;", " ")
    paras: list[str] = []
    for block in re.split(r"\n\s*\n", s):
        lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in block.splitlines()]
        lines = [ln for ln in lines if ln]
        if not lines:
            continue
        # Verse keeps its line breaks; prose is one line per paragraph already.
        paras.append("\n".join(lines) if len(lines) > 1 and all(len(ln) < 80 for ln in lines)
                     else " ".join(lines))
    return title, "\n\n".join(paras).strip()


def _paginate(paras: list[str], target: int) -> list[str]:
    """Greedy paragraph packing to about ``target`` characters per page."""
    pages: list[str] = []
    cur: list[str] = []
    size = 0
    for p in paras:
        if cur and size + len(p) / 2 > target:
            pages.append("\n\n".join(cur))
            cur, size = [], 0
        cur.append(p)
        size += len(p)
    if cur:
        # A chapter's last few lines do not deserve a page of their own; a page
        # that small would be a rank's whole assignment at p = 64.
        if pages and size < target / 4:
            pages[-1] = pages[-1] + "\n\n" + "\n\n".join(cur)
        else:
            pages.append("\n\n".join(cur))
    return pages


def load_pages_chairs(work_dir: str | Path, *, chapters: int = CHAIRS_CHAPTERS,
                      page_chars: int = PAGE_CHARS) -> dict[int, Page]:
    pages: dict[int, Page] = {}
    n = 0
    for i, raw in enumerate(fetch_chapters(work_dir, chapters=chapters), 1):
        title, text = wikitext_to_text(raw)
        paras = [p for p in text.split("\n\n") if p.strip()]
        first = n + 1
        for chunk in _paginate(paras, page_chars):
            n += 1
            pages[n] = Page(n, chunk, i, title or f"Глава {ROMAN[i - 1]}", "narrative", first)
    return pages


# ---------------------------------------------------------------------------
# Partitioning
# ---------------------------------------------------------------------------


def partition(pages: dict[int, Page], size: int) -> list[list[int]]:
    """Contiguous segments balanced by character count, one per rank.

    Balanced by characters rather than by page count because pages are uneven
    and the translation cost is in the characters; contiguous because the seam
    exchange needs neighbours to be neighbours in the book.  Every rank gets at
    least one page; a population larger than the book is refused.
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
        seg = [order[i]]
        acc += pages[order[i]].chars
        i += 1
        target = (r + 1) * total / size
        while i < len(order) and (len(order) - i) > (remaining_ranks - 1) \
                and acc + pages[order[i]].chars / 2 < target:
            seg.append(order[i])
            acc += pages[order[i]].chars
            i += 1
        if r == size - 1:
            while i < len(order):
                seg.append(order[i])
                i += 1
        segments.append(seg)
    return segments


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


# ---------------------------------------------------------------------------
# Building and describing a corpus
# ---------------------------------------------------------------------------


def load_pages(work_dir: str | Path, corpus: str = "chairs", *,
               legacy_dir: str | Path | None = None) -> tuple[dict[int, Page], dict[str, Any]]:
    """Load a source's pages and the provenance to record beside them."""
    if corpus == "durov":
        legacy = ensure_legacy(work_dir, legacy_dir)
        return load_pages_durov(legacy), {
            "origin": str(legacy), "commit": legacy_commit(legacy),
            "seed": seed_glossary(legacy), "summaries": chapter_summaries(legacy)}
    if corpus == "chairs":
        pages = load_pages_chairs(work_dir)
        raw = Path(work_dir) / "chairs" / "raw"
        digest = hashlib.sha256()
        for f in sorted(raw.glob("chapter_*.wiki")):
            digest.update(f.read_bytes())
        return pages, {"origin": SOURCES["chairs"]["source"], "commit": digest.hexdigest()[:16],
                       "seed": {}, "summaries": ""}
    raise ValueError(f"unknown corpus {corpus!r}; choose from {sorted(SOURCES)}")


def build(work_dir: str | Path, size: int, *, corpus: str = "chairs",
          legacy_dir: str | Path | None = None, pages: str | None = None) -> Corpus:
    """Build the corpus.  ``pages`` restricts it to a subset, for smoke tests."""
    loaded, prov = load_pages(work_dir, corpus, legacy_dir=legacy_dir)
    keep = parse_pages(pages)
    if keep is not None:
        loaded = {n: p for n, p in loaded.items() if n in keep}
        if not loaded:
            raise ValueError(f"no pages match {pages!r}")
    return Corpus(
        source=corpus, pages=loaded, segments=partition(loaded, size),
        seed_glossary=prov["seed"], summaries=prov["summaries"],
        origin=prov["origin"], origin_commit=prov["commit"],
    )


def manifest(corpus: Corpus) -> dict[str, Any]:
    src = SOURCES[corpus.source]
    return {
        "corpus": corpus.source,
        "title": src["title"], "author": src["author"], "year": src["year"],
        "source": src["source"], "source_digest": corpus.origin_commit,
        "rights": src["rights"],
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
