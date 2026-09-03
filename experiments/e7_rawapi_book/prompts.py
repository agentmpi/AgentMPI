"""The five model prompts of E7, and the contracts their results must satisfy.

Checked in because they are the method.  As in E3, no prompt mentions a barrier,
a reduction, a window, a lock, a neighbour exchange, or the existence of other
ranks as parties to a protocol: the executor is told what artifact to produce and
what shape it must have, and every coordination decision is made by the harness.

E7 adds one prompt E3 did not have.  E3's root settled lifted conflicts with the
runtime's default rule, which picks a candidate but exercises no judgement.  Here
the root *asks the model*: arbitration is an agent-evaluated step, given every
candidate the population proposed and asked to choose once for the whole book.
That is the step conflict lifting exists to make possible --- the disagreement
reaches one place, intact, and is decided there exactly once.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = [
    "SURVEY_CONTRACT", "RESEARCH_CONTRACT", "ARBITRATE_CONTRACT", "TRANSLATE_CONTRACT",
    "SEAM_CONTRACT", "SYSTEM",
    "survey_prompt", "research_prompt", "arbitrate_prompt", "translate_prompt", "seam_prompt",
]

#: Measured on the first p=16 production attempt: kimi-k3 writes a term entry
#: with a three-language proposal and two one-line glosses in about 190 tokens
#: under the structural counter, and seven of sixteen surveys overflowed a
#: 150-per-term budget on their first try.  A budget the harness author sets
#: without measuring is a budget that silently truncates the population's output.
TOKENS_PER_TERM = 200
SURVEY_TERMS = 24

SYSTEM = (
    "You are one translator in a team rendering a 2013 Russian reported biography "
    "into several languages. You answer with exactly the JSON artifact requested and "
    "nothing else: no preamble, no markdown fence, no commentary after the object."
)

SURVEY_CONTRACT: dict[str, Any] = {
    "kind": "json", "name": "survey",
    "required": ["rank", "terms"], "expect": {"rank": "{rank}"},
    "max_tokens": SURVEY_TERMS * TOKENS_PER_TERM + 800,
    "semantics": "The terms in this segment a translator must render consistently, glossed.",
}

RESEARCH_CONTRACT: dict[str, Any] = {
    "kind": "json", "name": "research",
    "required": ["term", "finding", "rendering"], "nonempty": ["finding", "rendering"],
    "max_tokens": 1600,
    "semantics": "A researched note on one term and the rendering to use in each language.",
}

ARBITRATE_CONTRACT: dict[str, Any] = {
    "kind": "json", "name": "arbitration",
    "required": ["rulings"], "nonempty": ["rulings"],
    "semantics": "One rendering per language for every contested term, chosen for the whole book.",
}

TRANSLATE_CONTRACT: dict[str, Any] = {
    "kind": "json", "name": "translation",
    "required": ["rank", "units"], "expect": {"rank": "{rank}"}, "nonempty": ["units"],
    "semantics": "Every paragraph of the segment rendered into every target language.",
}

SEAM_CONTRACT: dict[str, Any] = {
    "kind": "json", "name": "seam",
    "required": ["rank", "revised", "changed"], "expect": {"rank": "{rank}"},
    "max_tokens": 3000,
    "semantics": "The boundary paragraphs of this segment, revised to join its neighbours.",
}

_AUDIENCES = {
    "en": "an English-language reader with no Russian context",
    "zh": "a Chinese reader (simplified characters), for whom Russian internet culture "
          "of the 2000s needs an analogue, not a footnote",
    "ja": "a Japanese reader, with the register of a serious business biography",
}


def _languages(langs: list[str]) -> str:
    return "\n".join(f"- **{code}** — for {_AUDIENCES.get(code, code)}" for code in langs)


def _units_block(units: list[dict[str, Any]]) -> str:
    return "\n\n".join(f"[{u['i']}] (page {u['page']}) {u['ru']}" for u in units)


def survey_prompt(segment: dict, languages: list[str], rank: int) -> str:
    n = len(segment["units"])
    target = max(4, min(SURVEY_TERMS, 4 * n))
    return f"""\
# Rank {rank}: survey your segment

You are working on one segment of *{segment.get("title", "Код Дурова")}*, a 2013
Russian reported biography of Pavel Durov and the history of VKontakte: dense with
period slang, institutional names, internet culture, St Petersburg geography, and
allusions a Russian reader of the time would catch without explanation.

Your job in this task is **not** to translate. It is to find everything in your
segment that a translator would have to decide once and then apply consistently,
and to say what you think each one means.

## Your segment (segment {segment["index"]}, pages {segment["pages"][0]}–{segment["pages"][1]}, {n} paragraphs)

{_units_block(segment["units"])}

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
  outside the text — who a person is, what an institution did, what a slang term
  connoted, what an allusion points at.
- Aim for about {target} terms for a segment of this size. Do not pad. If you must
  choose, keep the terms where an inconsistent rendering would cost a reader the
  most, and prefer dropping a term to compressing every gloss into uselessness.
- `proposed` is your first pass, not a commitment. Other translators are surveying
  other segments and some will propose different renderings for the same term.
  That is expected: the disagreements are collected and settled elsewhere, once
  for the whole book. Propose what you would actually use.
"""


def research_prompt(term: dict, languages: list[str], rank: int, *, tools: bool) -> str:
    how = (
        "**Use the tools.** Search Wikipedia in Russian for the person, place, institution "
        "or term; read the article; check the English, Chinese or Japanese edition to see how "
        "that audience already knows it. Do not rely on recall alone for a name, an "
        "institution, a date, or period slang: those are exactly the cases where a confident "
        "wrong answer is most likely and most costly."
        if tools else
        "Use what you know, and say plainly in `finding` where you are unsure."
    )
    return f"""\
# Rank {rank}: research one term

One term from a 2013 Russian reported biography of Pavel Durov and VKontakte needs
a grounded rendering decision. Yours is:

    term:  {term["term"]}
    kind:  {term.get("kind", "unknown")}
    gloss: {term.get("gloss", "(none proposed)")}
    why it is hard: {term.get("why_hard", "(not stated)")}

Renderings proposed so far by translators who met this term in their own segments:

{json.dumps(term.get("proposed", {}), ensure_ascii=False, indent=1)}

## What to do

Establish what this actually refers to and what it carried for a Russian reader
around 2013. {how}

Then decide how to carry it into each target language:

{_languages(languages)}

A good decision states which of denotation, connotation and register it preserves
and which it sacrifices, because for this material you usually cannot keep all
three.

## What to write

Write ONLY a JSON object, no prose around it and no markdown fence:

{{"term": {json.dumps(term["term"], ensure_ascii=False)},
 "finding": "<what it is and what it connoted, 2–5 sentences, concrete>",
 "sources": ["<url or work consulted>", "..."],
 "register": "<formal|neutral|colloquial|slang|jargon|ironic>",
 "rendering": {{{", ".join(f'"{c}": "<the rendering to use>"' for c in languages)}}},
 "rationale": "<what you preserved and what you gave up, one or two sentences>",
 "confidence": "high|medium|low"}}

If research does not settle it, say so in `finding`, set `confidence` to "low", and
give the best defensible rendering anyway. Do not invent a source: an empty
`sources` list with an honest `finding` is worth more than a citation that does
not exist.
"""


def arbitrate_prompt(conflicts: dict[str, list[Any]], languages: list[str], rank: int,
                     stage: str) -> str:
    what = ("first-pass proposals from the survey" if stage == "census"
            else "researched renderings and survey proposals")
    return f"""\
# Rank {rank}: arbitrate the contested terms

The team surveyed the whole of a 2013 Russian reported biography of Pavel Durov
and VKontakte in parallel. For the terms below, translators working on different
segments proposed **different** renderings ({what}). Each must be settled once,
for the whole book, so that every segment uses the same rendering.

## Target languages

{_languages(languages)}

## The contested terms, with every candidate proposed

{json.dumps(conflicts, ensure_ascii=False, indent=1)}

## What to write

Write ONLY a JSON object, no prose around it and no markdown fence:

{{"rulings": {{
   "<term exactly as given above>": {{{", ".join(f'"{c}": "<the rendering to use>"' for c in languages)}}},
   "...": {{}}
 }},
 "notes": ["<one line per ruling that was not obvious: what decided it>"]}}

Rules.

- Rule on **every** term listed, using the term string exactly as the key.
- Prefer one of the candidates; compose a new rendering only when every candidate
  is wrong in the same way. You may take the `en` from one candidate and the `zh`
  from another.
- Decide for the book, not for a segment: a name is rendered the same way in a
  childhood chapter and a boardroom chapter.
- Keep company and product names in their established form (VKontakte / VK,
  Mail.ru, Telegram); transliterate personal names by the standard convention of
  each target language.
"""


def translate_prompt(segment: dict, languages: list[str], rank: int,
                     glossary: dict | None, *, previous: dict | None = None,
                     part: tuple[int, int] = (1, 1)) -> str:
    prev_block = ""
    if previous:
        prev_block = f"""\
## Continuity

This is part {part[0]} of {part[1]} of your segment. The paragraph immediately
before this part was rendered as follows; continue in the same voice, with the
same names and the same tense, and do not re-translate it:

{json.dumps({k: previous.get(k, "") for k in ("ru", *languages)}, ensure_ascii=False, indent=1)}

"""
    gloss_block = ""
    if glossary:
        gloss_block = f"""\
## The binding glossary

These renderings were agreed across the whole book. Use them exactly, every time
the source term appears in your segment. Do not substitute your own preference,
even where you would have chosen differently: consistency across segments is
worth more here than any single local improvement.

{json.dumps(glossary, ensure_ascii=False, indent=1)}

"""
    n = len(segment["units"])
    return f"""\
# Rank {rank}: translate segment {segment["index"]}

Translate every paragraph below from Russian into each target language. Produce a
faithful literary translation: natural prose in the target language, preserving
the register of the original, which is reported non-fiction, close to its
subjects, often wry. This is a comparative cultural task as much as a linguistic
one: a Russian allusion, slang term or metaphor should land for the target
audience the way it landed for a Russian reader in 2013, which sometimes means an
equivalent rather than a literal rendering.

{gloss_block}{prev_block}## Target languages

{_languages(languages)}

## Your paragraphs (pages {segment["pages"][0]}–{segment["pages"][1]}, {n} paragraphs, part {part[0]} of {part[1]})

{_units_block(segment["units"])}

## What to write

Write ONLY a JSON object, no prose around it and no markdown fence:

{{"rank": {rank},
 "units": [
   {{"i": <the paragraph number in brackets above>,
     {", ".join(f'"{c}": "<the {c} rendering of that paragraph>"' for c in languages)}}}
 ],
 "new_terms": {{"<a recurring term you had to decide that the glossary does not cover>":
                {{{", ".join(f'"{c}": "<rendering used>"' for c in languages)}}}}},
 "notes": ["<a translator's note, only where a reader would otherwise be misled>"]}}

Rules.

- Exactly one entry per paragraph, {n} entries, `i` matching the bracketed numbers,
  in order. Do not merge, split, skip or summarise a paragraph. A heading is a
  paragraph too.
- Do not repeat the Russian text in your answer.
- Where the glossary covers a term, the glossary wins.
- `new_terms` is for names and terms you settled yourself because the glossary did
  not; leave it empty if there were none. Keep `notes` short.
"""


def seam_prompt(rank: int, my_edges: dict, neighbours: list[dict], languages: list[str]) -> str:
    return f"""\
# Rank {rank}: reconcile your segment's seams

Your segment sits between two others that were translated at the same time by
other translators who could not see yours. The joins are where that shows: a
pronoun whose antecedent is in the previous segment, a sentence continued across
the boundary, a tense or a form of address that shifts, a name introduced in full
on both sides or on neither.

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
  more often than not.
- Do not change terminology that the glossary fixed.
"""
