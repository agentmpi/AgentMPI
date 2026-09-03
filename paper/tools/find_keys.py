"""Look up BibTeX keys by topic, so citations in the paper name real entries.

A paper that cites a key the bibliography does not define produces a silent
question mark in the PDF, which is the single easiest way to ship an embarrassing
artifact.  This is the lookup that avoids it; ``paper/tools/check_tex.py`` is the check
that catches it if this is skipped.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BIB = Path(__file__).resolve().parent / ".." / "refs.bib"


def entries() -> dict[str, str]:
    text = BIB.read_text(encoding="utf-8", errors="replace")
    out: dict[str, str] = {}
    for m in re.finditer(r"@\w+\{([^,]+),(.*?)(?=\n@|\Z)", text, re.S):
        key, body = m.group(1).strip(), m.group(2)
        t = re.search(r"title\s*=\s*[{\"](.+?)[}\"]\s*,", body, re.S | re.I)
        a = re.search(r"author\s*=\s*[{\"](.+?)[}\"]\s*,", body, re.S | re.I)
        title = re.sub(r"[{}\s]+", " ", t.group(1)).strip() if t else ""
        author = re.sub(r"[{}\s]+", " ", a.group(1)).strip() if a else ""
        out[key] = f"{author[:40]} | {title[:90]}"
    return out


def main() -> None:
    db = entries()
    for term in sys.argv[1:]:
        print(f"== {term}")
        n = 0
        for key, meta in db.items():
            if term.lower() in (key + " " + meta).lower():
                print(f"   {key}: {meta}")
                n += 1
                if n >= 5:
                    break
        if not n:
            print("   (nothing)")


if __name__ == "__main__":
    main()
