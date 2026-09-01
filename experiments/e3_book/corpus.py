"""The E3 corpus: fetched at run time, partitioned, and never redistributed.

The source is a commercial in-copyright book --- N. V. Kononov, *Код Дурова*
(Mann, Ivanov & Ferber, 2013, ISBN 978-5-91657-546-0) --- taken from the page
extraction published by the legacy translation project this experiment replaces.
It is the right corpus for the question: rendering it is a comparative literary
and historical problem rather than a lexical one, so the terminology coupling
between segments is real rather than contrived, which is exactly the coupling
AgentMPI's reductions exist to manage.

It is the wrong corpus to vendor.  So this module fetches the text into a working
directory that is not tracked, and the only corpus artifact the repository carries
is a *manifest*: for each segment an index, a page range, a character and token
count, and a SHA-256 of the exact bytes that rank was given.  That is enough to
verify that a run partitioned what it said it partitioned and that two runs at
different scales cut the same book, and it contains none of the book.

The rule is enforced rather than documented.  :func:`write_manifest` writes
metadata and refuses to serialise segment text, so a later change cannot quietly
start committing the corpus by adding a field.  See ``DATA_POLICY.md``.
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

__all__ = ["Segment", "Corpus", "fetch_pages", "build", "write_manifest"]

RAW = (
    "https://raw.githubusercontent.com/XinShuo-ph/"
    "durov_code_translation_multi_agent/main/extracted/pages/page_{n:03d}.txt"
)
N_PAGES = 99
#: Front matter: title page, imprint, table of contents, publisher advertising.
#: Excluded because it is not prose and would give whichever rank drew it a
#: segment with no translation problem in it, which distorts every load-balance
#: measurement in the experiment.
FIRST_PROSE_PAGE = 5

#: The running header repeated on every page of the extraction.  Left in, it
#: would appear ~99 times in the corpus and every rank would propose a rendering
#: for it, manufacturing a terminology conflict that is an artefact of the PDF.
_HEADER = re.compile(r"^\s*Н\.\s*В\.\s*Кононов\..*$", re.M)


@dataclass
class Segment:
    """One contiguous stretch of the book, assigned to exactly one rank.

    Contiguous, not strided.  A literary segment's difficulty is local --- a
    scene, an argument, a run of dialogue --- and striding would hand each rank a
    set of unrelated fragments, destroying the seam structure that makes the
    neighbourhood exchange meaningful and making every segment equally
    context-free, which is precisely the property real translation lacks.
    """

    index: int
    first_page: int
    last_page: int
    text: str = field(repr=False, default="")

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
        """Everything about this segment except the segment."""
        return {
            "index": self.index,
            "pages": [self.first_page, self.last_page],
            "chars": self.chars,
            "tokens": self.tokens,
            "sha256_16": self.digest,
        }


@dataclass
class Corpus:
    title: str
    author: str
    source: str
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
                "in copyright; fetched at run time and not redistributed. "
                "This manifest carries segment metadata only."
            ),
            "n_segments": self.size,
            "total_chars": sum(s.chars for s in self.segments),
            "total_tokens": sum(s.tokens for s in self.segments),
            "segments": [s.metadata() for s in self.segments],
        }


def fetch_pages(work_dir: str | Path, *, pages: int = N_PAGES) -> list[str]:
    """Download the page extraction into an untracked working directory.

    Cached on disk, because a sixty-four rank run is launched several times while
    a harness is being debugged and re-downloading a hundred files each time is
    both slow and rude to the host.
    """
    cache = Path(work_dir) / "pages"
    cache.mkdir(parents=True, exist_ok=True)
    out: list[str] = []
    for n in range(1, pages + 1):
        local = cache / f"page_{n:03d}.txt"
        if not local.exists():
            with urllib.request.urlopen(RAW.format(n=n), timeout=60) as fh:  # noqa: S310
                local.write_bytes(fh.read())
        out.append(local.read_text(encoding="utf-8", errors="replace"))
    return out


def _clean(page: str) -> str:
    page = _HEADER.sub("", page)
    # The extraction wraps at the PDF's line width, so a paragraph arrives as a
    # column of short lines.  Rejoining on blank lines restores paragraphs, which
    # is the unit a translator and a seam exchange both work in.
    page = re.sub(r"[ \t]+", " ", page)
    page = re.sub(r"\n{3,}", "\n\n", page)
    return page.strip()


def _close(index: int, bucket: list[tuple[int, str]]) -> Segment:
    return Segment(index, bucket[0][0], bucket[-1][0], "\n\n".join(p for _n, p in bucket))


def build(work_dir: str | Path, size: int, *, pages: int = N_PAGES) -> Corpus:
    """Partition the book into ``size`` contiguous, roughly equal segments.

    Balanced on characters rather than on pages, because the extraction's pages
    differ in density by more than a factor of two and a page-equal split would
    hand some ranks twice the work of others --- which would show up in the
    analysis as load imbalance and be read as a property of the protocol.
    """
    raw = fetch_pages(work_dir, pages=pages)
    cleaned = [(n, _clean(p)) for n, p in enumerate(raw, start=1) if n >= FIRST_PROSE_PAGE]
    cleaned = [(n, p) for n, p in cleaned if p]
    if size > len(cleaned):
        raise SystemExit(
            f"cannot cut {len(cleaned)} prose pages into {size} segments; "
            "use a smaller --size or a finer unit than a page"
        )

    total = sum(len(p) for _n, p in cleaned)
    segments: list[Segment] = []
    bucket: list[tuple[int, str]] = []
    acc = 0
    consumed = 0
    for i, (n, page) in enumerate(cleaned):
        bucket.append((n, page))
        acc += len(page)
        consumed += len(page)
        pages_left = len(cleaned) - i - 1
        slots_left = size - len(segments) - 1
        # The target is recomputed from what is actually left rather than fixed at
        # total/size, so that a run of dense pages early does not starve the tail:
        # with a fixed target the first segments overshoot, and the last ones are
        # padded out of whatever remains.
        target = (total - (consumed - acc)) / max(1, size - len(segments))
        # Close the segment once it has its share, but never so eagerly that the
        # pages left cannot fill the segments left.  A partition that runs out of
        # pages produces empty ranks, and an empty rank still enters every
        # collective --- indistinguishable in the trace from a rank whose executor
        # died, which is the one confusion this experiment must not introduce.
        if slots_left > 0:
            # Close once this segment has its share, or once the pages left are
            # exactly the segments left --- whichever comes first.  The second
            # clause is the guard: without it a partition can run out of pages and
            # produce empty ranks, and an empty rank still enters every collective,
            # indistinguishable in the trace from a rank whose executor died.
            has_share = acc >= target and pages_left > slots_left
            must_close = pages_left == slots_left
            if has_share or must_close:
                segments.append(_close(len(segments), bucket))
                bucket, acc = [], 0
    if bucket:
        segments.append(_close(len(segments), bucket))
    if len(segments) != size:  # pragma: no cover - guarded by the size check above
        raise SystemExit(f"partition produced {len(segments)} segments, expected {size}")

    return Corpus(
        title="Код Дурова. Реальная история «ВКонтакте» и ее создателя",
        author="Николай В. Кононов",
        source="XinShuo-ph/durov_code_translation_multi_agent@main extracted/pages",
        segments=segments,
    )


def write_manifest(corpus: Corpus, path: str | Path) -> Path:
    """Write segment metadata.  Never segment text.

    The refusal is mechanical on purpose.  A policy that lives only in a README is
    one field away from being violated by someone adding a convenience, and the
    convenience here would be "just include the text so the manifest is
    self-contained".
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = corpus.metadata()
    leaked = [s for s in payload["segments"] if "text" in s]
    if leaked:  # pragma: no cover - the metadata() contract makes this unreachable
        raise SystemExit("refusing to write a manifest containing corpus text")
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return p


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="build and describe the E3 corpus partition")
    ap.add_argument("--work-dir", default="work/e3", help="untracked cache for the fetched text")
    ap.add_argument("--size", type=int, required=True)
    ap.add_argument("--out", default="", help="write the manifest here")
    a = ap.parse_args()

    c = build(a.work_dir, a.size)
    if a.out:
        write_manifest(c, a.out)
    print(json.dumps({k: v for k, v in c.metadata().items() if k != "segments"}, indent=2,
                     ensure_ascii=False))
    for s in c.segments:
        print(f"  seg{s.index:>3}  pages {s.first_page:>3}-{s.last_page:<3} "
              f"{s.chars:>7} chars  {s.tokens:>6} tokens  {s.digest}")
