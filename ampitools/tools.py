"""Research tools for a model executor: the smallest body a raw API call can be given.

A translator who cannot look anything up is not a translator; the legacy project
this protocol replaces was staffed by agents that could search, and a raw
chat-completions call cannot.  These three functions are the difference.  They are
deliberately few and deliberately dull: a page fetch, an encyclopaedia search, and
an encyclopaedia article, which together settle the questions a period biography
raises --- who a person was, what an institution did, what a slang term meant ---
without giving the model a general-purpose browser it would spend its context
wandering through.

Every call is cached on disk under ``AMPI_TOOL_CACHE`` (default ``work/tool-cache``).
Two hundred and fifty-six ranks researching the same forty terms is exactly the
duplication the protocol's shared research window exists to prevent, and where the
protocol does not prevent it --- a rank checking a name during translation --- the
cache does, and the host being queried is spared.
"""

from __future__ import annotations

import contextlib
import hashlib
import html
import json
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .model import Tool

try:  # the per-host throttle needs an advisory lock; not every platform has one
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

__all__ = ["fetch_url", "wiki_search", "wiki_page", "research_tools", "TOOLS", "iri_to_uri"]

ENV_CACHE = "AMPI_TOOL_CACHE"
USER_AGENT = "AgentMPI/1.0 (research tool; https://github.com/agentmpi/AgentMPI)"
_TAG = re.compile(r"<(script|style|noscript|nav|header|footer|aside)[^>]*>.*?</\1>", re.S | re.I)
_MAIN = re.compile(r'(<main[\s>]|id="mw-content-text"|<article[\s>])', re.I)
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_NL = re.compile(r"\n{3,}")
#: Minimum gap between two requests to the same host from this machine.  Every
#: rank on a node is a separate process; the gap is kept in a lock file so the
#: whole node, not each process, stays under the host's rate limit.
MIN_GAP_S = float(os.environ.get("AMPI_TOOL_MIN_GAP_S", "0.7"))
MAX_RETRY_AFTER_S = 30.0


def _cache_dir() -> Path:
    p = Path(os.environ.get(ENV_CACHE) or "work/tool-cache")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _cached(key: str, fn: Any) -> str:
    path = _cache_dir() / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))["text"]
        except (json.JSONDecodeError, KeyError, OSError):
            pass
    text = fn()
    if text.startswith("error:"):
        return text  # never cache a failure: the next rank may succeed
    with contextlib.suppress(OSError):
        path.write_text(json.dumps({"key": key, "at": time.time(), "text": text}),
                        encoding="utf-8")
    return text


def iri_to_uri(url: str) -> str:
    """Percent-encode an IRI so ``urllib`` will send it.

    Models write URLs the way people do --- ``https://ru.wikipedia.org/wiki/Дуров``
    --- and ``http.client`` refuses anything outside ASCII.  In the first 128-rank
    run every such fetch failed with a ``UnicodeEncodeError`` and the rank fell
    back to a search that was itself being rate-limited.  Characters that are
    already percent-encoded are left alone, so a URL the model encoded itself is
    not encoded twice.
    """
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    with contextlib.suppress(UnicodeError):
        host = host.encode("idna").decode("ascii")
    netloc = host
    if parts.port:
        netloc += f":{parts.port}"
    if parts.username:
        cred = parts.username + (f":{parts.password}" if parts.password else "")
        netloc = f"{cred}@{netloc}"
    safe = "-._~:/?#[]@!$&'()*+,;=%"
    return urllib.parse.urlunsplit((
        parts.scheme,
        netloc,
        urllib.parse.quote(parts.path, safe=safe),
        urllib.parse.quote(parts.query, safe=safe),
        urllib.parse.quote(parts.fragment, safe=safe),
    ))


def _throttle(host: str) -> None:
    """Hold this process until ``MIN_GAP_S`` has passed since the last request the
    machine made to ``host``.  The clock is the lock file's mtime; the lock keeps
    two ranks from reading it at once and both concluding the gap is theirs."""
    if MIN_GAP_S <= 0 or fcntl is None:
        return
    path = _cache_dir() / f".gap-{re.sub(r'[^a-z0-9.-]', '_', host.lower())}"
    try:
        with open(path, "a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                wait = MIN_GAP_S - (time.time() - os.fstat(fh.fileno()).st_mtime)
                if 0 < wait <= MIN_GAP_S:
                    time.sleep(wait)
                os.utime(fh.fileno())
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
    except OSError:  # a read-only or missing cache dir must not break the tool
        return


def _get(url: str, *, timeout: float = 30.0, tries: int = 5) -> bytes:
    """GET with a retry on 429/5xx.

    Wikimedia rate-limits by source address, and every rank in a sandbox fleet
    leaves through the same egress, so a burst of research from a population looks
    to the encyclopaedia like one very busy client.  Three things keep a transient
    refusal from becoming a wrong translation: the per-host gap in ``_throttle``,
    a ``Retry-After`` header honoured when the host sends one, and a backoff that
    grows with each refusal.
    """
    url = iri_to_uri(url)
    host = urllib.parse.urlsplit(url).hostname or ""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last: Exception | None = None
    for attempt in range(tries):
        _throttle(host)
        delay = 1.5 * (attempt + 1) + random.uniform(0.0, 1.0)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - the URL is the tool's input
                return r.read()
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in (429, 500, 502, 503, 504) or attempt == tries - 1:
                raise
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            if retry_after and str(retry_after).strip().isdigit():
                delay = max(delay, min(float(retry_after), MAX_RETRY_AFTER_S))
            elif exc.code == 429:
                delay = min(2.0 * delay, MAX_RETRY_AFTER_S)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            if attempt == tries - 1:
                raise
        time.sleep(delay)
    raise last  # pragma: no cover - unreachable


def _text_of_html(raw: bytes) -> str:
    text = raw.decode("utf-8", "replace")
    m = _MAIN.search(text)
    if m:
        text = text[m.start():]
    text = _TAG.sub(" ", text)
    text = re.sub(r"<br\s*/?>|</p>|</div>|</h\d>|</li>|</tr>", "\n", text, flags=re.I)
    text = _TAGS.sub(" ", text)
    text = html.unescape(text)
    text = _WS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return _NL.sub("\n\n", text).strip()


def fetch_url(url: str, max_chars: int = 6000) -> str:
    """Fetch a web page and return its visible text, truncated."""
    if not re.match(r"^https?://", url or ""):
        return "error: only http(s) URLs can be fetched"

    def go() -> str:
        try:
            raw = _get(url)
        except urllib.error.HTTPError as exc:
            return f"error: HTTP {exc.code} fetching {url}"
        except Exception as exc:  # noqa: BLE001
            return f"error: could not fetch {url}: {type(exc).__name__}: {exc}"
        return _text_of_html(raw)

    text = _cached(f"fetch:{url}", go)
    return text[:max_chars]


def wiki_search(query: str, lang: str = "ru", limit: int = 5) -> str:
    """Search Wikipedia in one language; return titles and snippets."""
    lang = re.sub(r"[^a-z-]", "", (lang or "ru").lower()) or "ru"
    limit = max(1, min(int(limit or 5), 10))
    q = urllib.parse.urlencode({
        "action": "query", "list": "search", "srsearch": query, "srlimit": limit,
        "format": "json", "utf8": 1,
    })
    url = f"https://{lang}.wikipedia.org/w/api.php?{q}"

    def go() -> str:
        try:
            data = json.loads(_get(url))
        except Exception as exc:  # noqa: BLE001
            return f"error: search failed: {type(exc).__name__}: {exc}"
        hits = (data.get("query") or {}).get("search") or []
        if not hits:
            return f"no results on {lang}.wikipedia.org for {query!r}"
        lines = []
        for h in hits:
            snippet = _text_of_html(str(h.get("snippet", "")).encode("utf-8"))
            lines.append(f"- {h.get('title')}: {snippet}")
        return "\n".join(lines)

    return _cached(f"wsearch:{lang}:{query}:{limit}", go)


def wiki_page(title: str, lang: str = "ru", max_chars: int = 3500) -> str:
    """The plain-text lead of one Wikipedia article.

    The REST summary endpoint first --- it is served from a cache and is the one
    Wikimedia asks high-volume readers to use --- and the action API's extract as
    the fallback when the summary is too short to settle anything.
    """
    lang = re.sub(r"[^a-z-]", "", (lang or "ru").lower()) or "ru"
    slug = urllib.parse.quote(title.replace(" ", "_"), safe="")
    summary_url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{slug}"
    q = urllib.parse.urlencode({
        "action": "query", "prop": "extracts", "explaintext": 1, "redirects": 1,
        "titles": title, "format": "json", "utf8": 1,
    })
    extract_url = f"https://{lang}.wikipedia.org/w/api.php?{q}"

    def go() -> str:
        head = ""
        try:
            data = json.loads(_get(summary_url))
            if data.get("extract"):
                head = f"# {data.get('title')}\n\n{data['extract']}"
        except Exception:  # noqa: BLE001 - fall through to the extract
            head = ""
        if len(head) >= max_chars // 2:
            return head
        try:
            data = json.loads(_get(extract_url))
        except Exception as exc:  # noqa: BLE001
            return head or f"error: could not read {title!r}: {type(exc).__name__}: {exc}"
        pages = (data.get("query") or {}).get("pages") or {}
        for p in pages.values():
            if "extract" in p and p["extract"]:
                return f"# {p.get('title')}\n\n{p['extract']}"
        return head or f"no article titled {title!r} on {lang}.wikipedia.org"

    return _cached(f"wpage:{lang}:{title}", go)[:max_chars]


TOOLS: dict[str, Tool] = {
    "fetch_url": Tool(
        name="fetch_url",
        description="Fetch a web page and return its visible text (truncated). Use for a "
                    "specific source you already know the URL of.",
        parameters={"type": "object", "properties": {
            "url": {"type": "string", "description": "an http(s) URL"},
            "max_chars": {"type": "integer", "description": "truncate the text here (default 6000)"},
        }, "required": ["url"]},
        fn=fetch_url,
        max_chars=3500,
    ),
    "wiki_search": Tool(
        name="wiki_search",
        description="Search Wikipedia. Use lang 'ru' for Russian people, places, institutions "
                    "and slang; 'en', 'zh' or 'ja' to see how a term is rendered for that "
                    "audience.",
        parameters={"type": "object", "properties": {
            "query": {"type": "string"},
            "lang": {"type": "string", "description": "ru | en | zh | ja (default ru)"},
            "limit": {"type": "integer", "description": "results, 1-10 (default 5)"},
        }, "required": ["query"]},
        fn=wiki_search,
        max_chars=2500,
    ),
    "wiki_page": Tool(
        name="wiki_page",
        description="Read the lead of one Wikipedia article by exact title, in the given "
                    "language edition.",
        parameters={"type": "object", "properties": {
            "title": {"type": "string"},
            "lang": {"type": "string", "description": "ru | en | zh | ja (default ru)"},
            "max_chars": {"type": "integer", "description": "truncate here (default 3500)"},
        }, "required": ["title"]},
        fn=wiki_page,
        max_chars=3500,
    ),
}


def research_tools() -> list[Tool]:
    return [TOOLS["wiki_search"], TOOLS["wiki_page"], TOOLS["fetch_url"]]
