"""The agent prompts of E6, and the contracts their results must satisfy.

Checked in because they are the method: the population's behaviour depends on
them and a reader cannot reproduce a run without them.

What they do not contain is the point.  No prompt mentions a barrier, a
reduction, a window, a lock, a claim, a fence, a neighbour exchange, or the
existence of other ranks as parties to a protocol.  An agent is told what
artifact to produce and what shape it must have.  The one place peers are
mentioned is the survey, which says other translators are proposing renderings
too and that disagreements are settled elsewhere --- not an instruction, there
is nothing for the agent to do about it, but load-bearing: a rank asked for
*the* rendering of a term hedges, and a hedge gives the reduction nothing real
to lift.

The output schema of the translation tasks is the legacy project's page schema
verbatim, so that the run's product is the deliverable that project set out to
produce and its own PDF compiler can consume it.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = [
    "SURVEY_CONTRACT", "ARBITRATE_CONTRACT", "RESEARCH_CONTRACT", "TRANSLATE_CONTRACT",
    "REVIEW_CONTRACT", "SEAM_CONTRACT", "LANG_NAMES",
    "survey_prompt", "arbitrate_prompt", "research_prompt", "translate_prompt",
    "review_prompt", "revise_prompt", "seam_prompt",
]

LANG_NAMES = {"en": "English", "zh": "Chinese (简体中文, Simplified)", "ja": "Japanese (日本語)"}

#: Measured on the earlier production attempt: one term entry with a Cyrillic
#: source term and three proposals costs about 150 tokens under cl100k.  The
#: survey budget is derived from this, not chosen, because a budget set by feel
#: made every executor silently drop terms it had judged load-bearing.
TOKENS_PER_TERM = 150
SURVEY_TERMS = 20

SURVEY_CONTRACT: dict[str, Any] = {
    "kind": "json", "name": "survey",
    "required": ["rank", "terms", "chapter_titles", "conventions"],
    "expect": {"rank": "{rank}"},
    "max_tokens": SURVEY_TERMS * TOKENS_PER_TERM * 2 + 1200,
    "semantics": "The terms a translator must render consistently across the book, "
                 "each with a first-pass rendering; chapter titles found; conventions proposed.",
}

ARBITRATE_CONTRACT: dict[str, Any] = {
    "kind": "json", "name": "arbitration",
    "required": ["rulings"], "nonempty": ["rulings"],
    "semantics": "One ruling per contested term, chosen from or improving on the candidates.",
}

RESEARCH_CONTRACT: dict[str, Any] = {
    "kind": "json", "name": "research",
    "required": ["term", "finding", "rendering", "sources", "confidence"],
    "nonempty": ["finding", "rendering"],
    "max_tokens": 1800,
    "semantics": "A researched note on one term and the rendering to bind for the whole book.",
}

TRANSLATE_CONTRACT: dict[str, Any] = {
    "kind": "json", "name": "page",
    "required": ["page", "chapter", "chapter_title", "sentences", "translator_notes",
                 "total_sentences", "page_type"],
    "nonempty": ["sentences"],
    "semantics": "One page in the legacy schema: every source sentence with all three renderings.",
}

REVIEW_CONTRACT: dict[str, Any] = {
    "kind": "json", "name": "review",
    "required": ["page", "verdict", "issues"],
    "max_tokens": 3000,
    "semantics": "A reviewer's verdict on a peer's page and the issues that justify it.",
}

SEAM_CONTRACT: dict[str, Any] = {
    "kind": "json", "name": "seam",
    "required": ["rank", "changed", "revised"],
    "expect": {"rank": "{rank}"},
    "max_tokens": 4000,
    "semantics": "The boundary sentences of this segment revised to join its neighbours.",
}


def _langs(languages: list[str]) -> str:
    return "\n".join(f"- **{c}** — {LANG_NAMES.get(c, c)}" for c in languages)


def _brief() -> str:
    return """\
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
"""


# ---------------------------------------------------------------------------
# Survey
# ---------------------------------------------------------------------------


def survey_prompt(rank: int, pages: list[dict[str, Any]], languages: list[str],
                  seed: dict[str, dict[str, str]]) -> str:
    seed_lines = "\n".join(
        f"- {ru}: " + ", ".join(f"{c}={v.get(c, '')}" for c in languages)
        for ru, v in list(seed.items())[:60]
    )
    body = "\n\n".join(
        f"### Page {p['page']} (chapter {p['chapter']}, {p['chapter_title']})\n\n{p['text']}"
        for p in pages
    )
    term_shape = ", ".join(f'"{c}": "<proposed {c} rendering>"' for c in languages)
    return f"""\
# Rank {rank}: survey your pages

{_brief()}
Your job in this task is **not** to translate. It is to read your pages and find
everything a translator must decide *once* and apply consistently across the
whole book, and to propose a first rendering for each.

## Your pages

{body}

## Target languages

{_langs(languages)}

## A translator's seed glossary (a starting point, not a decision)

{seed_lines or "(none)"}

## What to write

Write ONLY a JSON object, no prose around it and no markdown fence:

{{"rank": {rank},
 "terms": [
   {{"term": "<the term exactly as it appears in the Russian>",
     "kind": "person|org|place|institution|slang|idiom|metaphor|cultural_reference|technical|title",
     "gloss": "<what it denotes, one line of English>",
     "why_hard": "<what a careless translator would get wrong, one line>",
     "needs_research": true|false,
     "proposed": {{{term_shape}}}}}
 ],
 "chapter_titles": {{"<page number>": "<a chapter or section title that begins on that page, in Russian, exactly as printed>"}},
 "conventions": ["<a book-wide convention you propose, one line, e.g. how to render the narrator's asides, ты/вы, dialogue dashes, honorifics in Japanese>"],
 "page_types": {{"<page number>": "front_matter|narrative|dialogue|about_author"}}}}

Rules.

- Aim for about {SURVEY_TERMS} terms. Include recurring names, organisations,
  places, institutions, epithets, slang, recurring metaphors and culture-specific
  references — the things where an inconsistent rendering costs the reader most.
  Skip ordinary vocabulary. Prefer dropping a term to compressing every entry
  until the glosses stop being useful.
- `needs_research` is true when rendering the term well requires knowing
  something outside the text: who a person is, what an institution did, what a
  slang term connoted around 2013, what an allusion points at.
- `proposed` is a first pass, not a commitment. Other translators are surveying
  other pages and some will propose different renderings for the same term.
  That is expected; the disagreements are collected and settled elsewhere and
  one rendering will be bound for the whole book. Propose what you would
  actually use.
- `chapter_titles` may be empty if no chapter or section begins on your pages.
  `conventions` may be empty; propose at most three.
"""


# ---------------------------------------------------------------------------
# Arbitration
# ---------------------------------------------------------------------------


def arbitrate_prompt(conflicts: dict[str, list[Any]], context: dict[str, dict[str, Any]],
                     languages: list[str]) -> str:
    items = []
    for term, candidates in conflicts.items():
        meta = context.get(term, {})
        items.append({
            "term": term, "kind": meta.get("kind", ""), "gloss": meta.get("gloss", ""),
            "why_hard": meta.get("why_hard", ""), "candidates": candidates,
        })
    shape = ", ".join(f'"{c}": "<ruling>"' for c in languages)
    return f"""\
# Arbitrate the contested renderings

{_brief()}
Several translators, each reading different pages, proposed renderings for the
terms below, and they disagreed. One rendering per term must now be bound for
the whole book. You decide.

## The contested terms

{json.dumps(items, ensure_ascii=False, indent=1)}

## What to write

Write ONLY a JSON object, no prose around it and no markdown fence:

{{"rulings": {{"<term>": {{{shape}}}, "...": {{}}}},
 "reasons": {{"<term>": "<one line: why this one>"}}}}

Rules.

- Rule on **every** term listed, by its exact key. Choose one candidate or, if
  every candidate is wrong, write a better rendering in every language.
- Prefer the rendering the target reader will recognise (established English
  names, standard Chinese and Japanese transliterations for well-known people
  and places), then the one that preserves the connotation the Russian carries.
- Keep `reasons` to one line each.
"""


# ---------------------------------------------------------------------------
# Research
# ---------------------------------------------------------------------------


def research_prompt(rank: int, item: dict[str, Any], languages: list[str]) -> str:
    shape = ", ".join(f'"{c}": "<the rendering to bind>"' for c in languages)
    return f"""\
# Rank {rank}: research one term

{_brief()}
One term needs a grounded rendering decision before the book is translated:

    term:  {item["term"]}
    kind:  {item.get("kind", "unknown")}
    gloss: {item.get("gloss", "(none)")}
    why it is hard: {item.get("why_hard", "(not stated)")}
    contested among translators: {"yes" if item.get("contested") else "no"}

Renderings proposed so far by translators who met the term in their own pages:

{json.dumps(item.get("proposed", {}), ensure_ascii=False, indent=1)}

## What to do

Establish what this refers to and what it carried for a Russian reader around
2013. **Use web search** (and fetch pages where a search result is not enough).
Do not rely on recall alone for a person, an organisation, an institution, a
date, a place, or a period slang term: those are exactly the cases where a
confident wrong answer is most likely and most costly. Look for how the term is
already rendered in existing English, Chinese and Japanese coverage of Durov
and VKontakte, and prefer an established rendering to a novel one.

Then decide how to carry it into each target language:

{_langs(languages)}

A good decision states which of denotation, connotation and register it keeps
and which it gives up, because for this material you usually cannot keep all
three.

## What to write

Write ONLY a JSON object, no prose around it and no markdown fence:

{{"term": {json.dumps(item["term"], ensure_ascii=False)},
 "finding": "<what it is and what it connoted, 2–5 concrete sentences>",
 "sources": ["<URL consulted>", "..."],
 "register": "formal|neutral|colloquial|slang|jargon|ironic",
 "rendering": {{{shape}}},
 "note_for_reader": "<a one-line translator's note if the reader needs one, else empty>",
 "rationale": "<what you preserved and what you gave up, one or two sentences>",
 "confidence": "high|medium|low"}}

If search does not settle it, say so in `finding`, set `confidence` to "low",
and give the best defensible rendering anyway. Do not invent a source: an empty
`sources` list with an honest `finding` is worth more than a plausible citation
that does not exist.
"""


# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------


def _schema(languages: list[str]) -> str:
    langs = ", ".join(f'"{c}": "<{LANG_NAMES.get(c, c)} rendering>"' for c in languages)
    return f"""\
{{"page": <page number>,
 "chapter": <chapter number>,
 "chapter_title": "<chapter title: Russian, then the English in parentheses>",
 "sentences": [
   {{"id": 1, "ru": "<the first Russian sentence, verbatim>", {langs}}},
   {{"id": 2, "ru": "<the second>", {langs}}}
 ],
 "translator_notes": ["<a short note where a reader would otherwise be misled>"],
 "total_sentences": <the number of entries in sentences>,
 "page_type": "front_matter|narrative|dialogue|about_author"}}"""


def translate_prompt(rank: int, page: dict[str, Any], languages: list[str],
                     glossary: dict[str, Any], conventions: list[str],
                     context_before: str, context_after: str) -> str:
    gloss_block = (
        json.dumps(glossary, ensure_ascii=False, indent=1) if glossary else "(no bound terms)"
    )
    conv_block = "\n".join(f"- {c}" for c in conventions) or "(none)"
    before = f"\n\n(… the previous page ends:)\n\n{context_before}" if context_before else ""
    after = f"\n\n(… the next page begins:)\n\n{context_after}" if context_after else ""
    return f"""\
# Rank {rank}: translate page {page["page"]}

{_brief()}
Translate the page below from Russian into every target language, sentence by
sentence, into the exact JSON schema given. This page is one of about a hundred
being translated in parallel by other translators, so the shared decisions
below are binding.

## The binding glossary

These renderings were agreed for the whole book. Use them exactly, every time
the source term appears, even where you would have chosen differently:
consistency across pages is worth more than any local improvement.

{gloss_block}

## Book-wide conventions

{conv_block}

## Target languages

{_langs(languages)}

## The page (page {page["page"]}, chapter {page["chapter"]}: {page["chapter_title"]}; page type {page["page_type"]})

The surrounding text is given for context only — translate the page, not the context.
{before}

--- PAGE {page["page"]} BEGINS ---

{page["text"]}

--- PAGE {page["page"]} ENDS ---
{after}

## What to write

Write ONLY a JSON object, no prose around it and no markdown fence, in exactly
this schema:

{_schema(languages)}

Rules.

- Split the page into sentences; `ru` is each source sentence **verbatim**
  (keep its punctuation and quotation marks). Include **every** sentence, in
  order, ids 1, 2, 3, … with no gaps. Headings, captions and list items are
  sentences too. Do not merge, skip or summarise anything.
- Every sentence has every target language, non-empty.
- `page` must be {page["page"]}, `chapter` {page["chapter"]}, and
  `total_sentences` the number of entries you wrote.
- Natural literary prose in each language; preserve register, irony and
  Durov's voice; Chinese in simplified characters; Japanese with katakana for
  foreign names and a consistent formal register for narration.
- Where the glossary covers a term, the glossary wins.
- `translator_notes`: at most three short notes, only where the reader would
  otherwise be misled; an empty list is fine.
- Write the result with a file-writing tool, not by echoing through a shell,
  so that quoting cannot corrupt it.
"""


def fix_prompt(original: str, violations: list[str]) -> str:
    """Re-issue a task whose result failed validation, with the violations named."""
    listed = "\n".join(f"- {v}" for v in violations)
    return (
        original
        + "\n\n## Your previous answer was rejected\n\n"
        "It failed these mechanical checks:\n\n" + listed +
        "\n\nProduce the complete corrected JSON object again, satisfying every rule above.\n"
    )


# ---------------------------------------------------------------------------
# Review and revision
# ---------------------------------------------------------------------------


def review_prompt(rank: int, page: dict[str, Any], draft: dict[str, Any],
                  languages: list[str], glossary: dict[str, Any]) -> str:
    return f"""\
# Rank {rank}: review a peer's translation of page {page["page"]}

{_brief()}
Another translator rendered the page below. Review it against the source. You
are checking for what the translator could not see: completeness, fidelity,
consistency with the binding glossary, and naturalness in each language.

## The source (page {page["page"]}, chapter {page["chapter"]}: {page["chapter_title"]})

{page["text"]}

## The binding glossary

{json.dumps(glossary, ensure_ascii=False, indent=1) if glossary else "(no bound terms)"}

## The draft

{json.dumps(draft, ensure_ascii=False, indent=1)}

## What to write

Write ONLY a JSON object, no prose around it and no markdown fence:

{{"page": {page["page"]},
 "verdict": "accept|revise",
 "issues": [
   {{"id": <sentence id>, "lang": "{"|".join(languages)}|ru",
     "severity": "major|minor",
     "problem": "<what is wrong, one line>",
     "suggestion": "<the corrected rendering, or a concrete instruction>"}}
 ],
 "summary": "<two or three sentences on the draft's overall quality>"}}

Rules.

- A **major** issue is a missing or merged sentence, a mistranslation that
  changes the meaning, a glossary term rendered otherwise, or a sentence that a
  native reader would stumble on. Anything else is minor.
- `verdict` is "revise" if there is at least one major issue, else "accept".
- At most twelve issues, the most important first. Be concrete: a suggestion
  the translator can apply verbatim is worth more than a description.
- Do not rewrite the page. Do not report as an issue a choice that is merely
  different from yours.
"""


def revise_prompt(rank: int, page: dict[str, Any], draft: dict[str, Any],
                  review: dict[str, Any], languages: list[str], glossary: dict[str, Any]) -> str:
    return f"""\
# Rank {rank}: revise page {page["page"]} after review

A reviewer read your translation of the page against the source and found the
issues below. Produce the revised page: the complete JSON object in the same
schema, with every major issue fixed, minor ones fixed where the reviewer is
right, and everything else left exactly as it was.

## The source

{page["text"]}

## The binding glossary

{json.dumps(glossary, ensure_ascii=False, indent=1) if glossary else "(no bound terms)"}

## Your draft

{json.dumps(draft, ensure_ascii=False, indent=1)}

## The review

{json.dumps(review, ensure_ascii=False, indent=1)}

## What to write

Write ONLY the complete revised JSON object, no prose around it and no markdown
fence, in exactly this schema:

{_schema(languages)}

`page` must be {page["page"]}, ids must stay 1, 2, 3, … with every source
sentence present, and `total_sentences` must equal the number of entries.
Reject a reviewer's suggestion only when it is wrong, and say why in
`translator_notes`.
"""


# ---------------------------------------------------------------------------
# Seams
# ---------------------------------------------------------------------------


def seam_prompt(rank: int, mine: dict[str, Any], left: dict[str, Any] | None,
                right: dict[str, Any] | None, languages: list[str]) -> str:
    shape = ", ".join(f'"{c}": "<revised>"' for c in languages)
    return f"""\
# Rank {rank}: reconcile the seams of your pages

Your pages sit between pages translated at the same time by other translators
who could not see your work. The joins are where that shows: a pronoun whose
antecedent is on the previous page, a sentence continued across the page
break, a shift of tense or of form of address, a name given in full on both
sides or on neither, a term rendered two ways.

## The end of the previous translator's last page (their text; you cannot edit it)

{json.dumps(left, ensure_ascii=False, indent=1) if left else "(none: your first page opens the book, or the neighbour produced nothing)"}

## The beginning of your first page, and the end of your last page (yours to edit)

{json.dumps(mine, ensure_ascii=False, indent=1)}

## The beginning of the next translator's first page (their text; you cannot edit it)

{json.dumps(right, ensure_ascii=False, indent=1) if right else "(none: your last page closes the book, or the neighbour produced nothing)"}

## What to write

Write ONLY a JSON object, no prose around it and no markdown fence:

{{"rank": {rank},
 "changed": true|false,
 "revised": {{"head": [{{"id": <sentence id on your first page>, {shape}}}],
              "tail": [{{"id": <sentence id on your last page>, {shape}}}]}},
 "reason": "<what you changed and why, or why nothing needed changing>"}}

Rules.

- Revise **only** the sentences shown as yours, and only where the join
  demands it. Leaving good text alone is the right answer more often than not;
  a gratuitous rewrite is worse than none.
- Do not change terminology the glossary fixed. Do not translate anything new.
- If nothing needs changing, set `changed` to false and return empty lists.
"""
