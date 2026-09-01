"""The four agent prompts of E3, and the contracts their results must satisfy.

The prompts are checked in because they are the experimental method: the
population's behaviour depends on them, and a reader cannot reproduce the result
without them.

Note what they do *not* contain.  No prompt mentions a barrier, a reduction, a
window, a lock, a neighbour exchange, or the existence of other ranks as parties
to a protocol.  The agent is told what artifact to produce and what shape it must
have; every coordination decision is made by host-side harness code.  That is the
central design claim of AgentMPI expressed as an artifact, and it is what makes
protocol conformance a property of the runtime rather than of a model's memory.

The one place peers are mentioned is the survey prompt, which says that other
ranks are proposing renderings too and that disagreements will be settled
elsewhere.  That is not a coordination instruction --- there is nothing for the
agent to do about it --- but it is load bearing: without it a rank asked for "the"
rendering of a term treats the question as a commitment it must get right alone,
and hedges.  Telling it that disagreement is expected and will be arbitrated is
what makes the proposals honest, which is what gives the reduction something real
to lift.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = [
    "SURVEY_CONTRACT",
    "RESEARCH_CONTRACT",
    "TRANSLATE_CONTRACT",
    "SEAM_CONTRACT",
    "survey_prompt",
    "research_prompt",
    "translate_prompt",
    "seam_prompt",
]

#: Measured cost of one term entry under the ``structural-v1`` counter, with a
#: Cyrillic source term and three target-language proposals.  Not guessed: the
#: p=16 production run's executors each measured it with ``check_size`` and
#: reported between 130 and 170, converging on ~150.
TOKENS_PER_TERM = 150
#: Terms a survey should return.  The budget is derived from this rather than the
#: other way round.
SURVEY_TERMS = 24

SURVEY_CONTRACT: dict[str, Any] = {
    "kind": "json",
    "name": "survey",
    "required": ["rank", "terms"],
    "expect": {"rank": "{rank}"},
    # Derived, not chosen.  The first version of this contract asked for "12 to 30
    # terms" under a flat 2600-token cap, and the two were incompatible: at ~150
    # tokens per entry, 2600 tokens buys 17 terms, not 30.  Every one of the eight
    # executors in the p=16 run discovered this independently with `check_size`,
    # and each resolved it the only way it could -- by dropping terms it had
    # judged load-bearing, silently and differently from its peers.  A budget the
    # harness author sets without measuring is a budget that quietly truncates the
    # population's output, and the failure is invisible in the trace because every
    # rank returns a conforming result.
    "max_tokens": SURVEY_TERMS * TOKENS_PER_TERM + 400,
    "semantics": (
        "The terms, names, idioms and historical references in this segment that a "
        "translator must render consistently, each with a first-pass gloss."
    ),
}

RESEARCH_CONTRACT: dict[str, Any] = {
    "kind": "json",
    "name": "research",
    "required": ["term", "finding"],
    "nonempty": ["finding"],
    "max_tokens": 1400,
    "semantics": (
        "A researched note on one term: what it denotes, what it connotes to a "
        "Russian reader of the period, and how to carry that into each target language."
    ),
}

TRANSLATE_CONTRACT: dict[str, Any] = {
    "kind": "json",
    "name": "translation",
    "required": ["rank", "units"],
    "expect": {"rank": "{rank}"},
    "semantics": (
        "The segment rendered into every target language, obeying the binding glossary."
    ),
}

SEAM_CONTRACT: dict[str, Any] = {
    "kind": "json",
    "name": "seam",
    "required": ["rank", "revised"],
    "expect": {"rank": "{rank}"},
    "max_tokens": 2200,
    "semantics": "The boundary paragraphs of this segment, revised to join its neighbours.",
}

_AUDIENCES = {
    "en": "an English-language reader with no Russian context",
    "zh": "a Chinese reader (simplified characters)",
    "ja": "a Japanese reader",
}


def _languages(langs: list[str]) -> str:
    return "\n".join(f"- **{code}** — for {_AUDIENCES.get(code, code)}" for code in langs)


def survey_prompt(segment: dict, languages: list[str], rank: int) -> str:
    return f"""\
# Rank {rank}: survey your segment

You are working on one segment of a Russian non-fiction book: a reported
biography of a technology founder, written in 2013, dense with period slang,
institutional names, internet culture, and allusions a Russian reader of the time
would catch without explanation.

Your job in this task is **not** to translate. It is to find everything in your
segment that a translator would have to decide once and then apply consistently,
and to say what you think each one means.

## Your segment (segment {segment["index"]}, pages {segment["pages"][0]}–{segment["pages"][1]})

{segment["text"]}

## Target languages

{_languages(languages)}

## What to write

Write ONLY a JSON object, no prose around it and no markdown fence:

{{"rank": {rank},
 "terms": [
   {{"term": "<the term exactly as it appears in the Russian>",
     "kind": "person|org|place|institution|slang|idiom|metaphor|cultural_reference|technical",
     "gloss": "<what it denotes, in one line of English>",
     "why_hard": "<what a careless translator would get wrong, one line>",
     "needs_research": true|false,
     "proposed": {{{", ".join(f'"{c}": "<your proposed {c} rendering>"' for c in languages)}}}}}
 ]}}

Rules.

- Include everything a *consistency* decision hangs on: people, organisations,
  places, institutions, recurring epithets, slang, metaphors that recur, and
  culture-specific references. Skip ordinary vocabulary.
- Set `needs_research` to true when rendering it well requires knowing something
  outside the text — who a person is, what an institution did, what a period
  slang term connoted, what an allusion points at. Set it to false when the text
  itself is sufficient.
- Aim for about {SURVEY_TERMS} terms. Do not pad, and do not omit an obviously
  recurring name because it seems too easy. If you must choose, keep the terms
  where an inconsistent rendering would cost a reader the most, and prefer
  dropping a term outright to compressing every entry until the glosses stop
  being useful.
- `proposed` is your first pass, not a commitment. Other ranks are surveying
  other segments and some of them will propose different renderings for the same
  term. That is expected: the disagreements are collected and settled elsewhere,
  and one of them will be settled for the whole book. Propose what you would
  actually use, not what you guess others will say.
"""


def research_prompt(term: dict, languages: list[str], rank: int) -> str:
    return f"""\
# Rank {rank}: research one term

One term from a 2013 Russian reported biography of a technology founder needs a
grounded rendering decision. Yours is:

    term:  {term["term"]}
    kind:  {term.get("kind", "unknown")}
    gloss: {term.get("gloss", "(none proposed)")}
    why it is hard: {term.get("why_hard", "(not stated)")}

Renderings proposed so far by ranks that met this term in their own segments:

{json.dumps(term.get("proposed", {}), ensure_ascii=False, indent=1)}

## What to do

Establish what this actually refers to and what it carried for a Russian reader
around 2013. **Use web search.** Do not rely on recall alone for a person,
an organisation, an institution, a date, or a period slang term: those are
exactly the cases where a confident wrong answer is most likely and most costly.

Then decide how to carry it into each target language:

{_languages(languages)}

A good decision states which of denotation, connotation and register it is
preserving and which it is sacrificing, because for this material you usually
cannot keep all three.

## What to write

Write ONLY a JSON object, no prose around it and no markdown fence:

{{"term": {json.dumps(term["term"], ensure_ascii=False)},
 "finding": "<what it is and what it connoted, 2–5 sentences, concrete>",
 "sources": ["<url or work consulted>", "..."],
 "register": "<formal|neutral|colloquial|slang|jargon|ironic>",
 "rendering": {{{", ".join(f'"{c}": "<the rendering to use>"' for c in languages)}}},
 "rationale": "<what you preserved and what you gave up, one or two sentences>",
 "confidence": "high|medium|low"}}

If search does not settle it, say so in `finding`, set `confidence` to "low", and
give the best defensible rendering anyway. Do not invent a source: an empty
`sources` list with an honest `finding` is worth more than a plausible citation
that does not exist, and it is checked.
"""


def translate_prompt(
    segment: dict, languages: list[str], rank: int, glossary: dict | None, units: int
) -> str:
    gloss_block = ""
    if glossary:
        gloss_block = f"""\
## The binding glossary

These renderings were agreed across the whole book. Use them exactly, every time
the source term appears in your segment. Do not substitute your own preference,
even where you would have chosen differently — consistency across segments is
worth more here than any single local improvement.

{json.dumps(glossary, ensure_ascii=False, indent=1)}
"""
    return f"""\
# Rank {rank}: translate segment {segment["index"]}

Translate the segment below from Russian into each target language. Produce a
faithful literary translation: natural prose in the target language, preserving
paragraph breaks, dialogue, and the register of the original — which is reported
non-fiction, close to its subjects, often wry.

{gloss_block}
## Target languages

{_languages(languages)}

## Your segment (pages {segment["pages"][0]}–{segment["pages"][1]})

{segment["text"]}

## What to write

Write ONLY a JSON object, no prose around it and no markdown fence:

{{"rank": {rank},
 "units": [
   {{"i": 0,
     "ru": "<the source paragraph, verbatim>",
     {", ".join(f'"{c}": "<the {c} rendering>"' for c in languages)}}}
 ],
 "notes": ["<a translator's note, only where a choice genuinely needs one>"]}}

Rules.

- One entry per source paragraph, in order, `i` counting from 0. Aim for about
  {units} entries; follow the source's own paragraphing rather than a target.
- Translate the whole segment. Do not summarise, do not skip a paragraph because
  it is difficult, and do not merge paragraphs to save effort.
- Where the glossary covers a term, the glossary wins.
- Keep `notes` short and only where a reader would otherwise be misled.
"""


def seam_prompt(
    rank: int, my_edges: dict, neighbours: list[dict], languages: list[str]
) -> str:
    return f"""\
# Rank {rank}: reconcile your segment's seams

Your segment sits between two others that were translated at the same time by
other translators who could not see yours. The joins are where that shows:
a pronoun whose antecedent is in the previous segment, a sentence continued
across the boundary, a tense or a form of address that shifts, a name introduced
in full on both sides or on neither.

## Your own boundary paragraphs

{json.dumps(my_edges, ensure_ascii=False, indent=1)}

## What your neighbours have at the adjoining edges

{json.dumps(neighbours, ensure_ascii=False, indent=1)}

## What to write

Write ONLY a JSON object, no prose around it and no markdown fence:

{{"rank": {rank},
 "revised": {{"head": {{{", ".join(f'"{c}": "<revised first paragraph>"' for c in languages)}}},
              "tail": {{{", ".join(f'"{c}": "<revised last paragraph>"' for c in languages)}}}}},
 "changed": true|false,
 "reason": "<what you changed and why, one or two sentences; or why nothing needed changing>"}}

Rules.

- Revise **only** your own head and tail paragraphs. You cannot edit a
  neighbour's text, and an edit that only works if the neighbour also changes is
  not a fix.
- If the seams already read correctly, set `changed` to false, return your
  paragraphs unmodified, and say why. Leaving good text alone is the right answer
  more often than not, and a gratuitous rewrite is worse than no rewrite.
- Do not change terminology that the glossary fixed.
"""
