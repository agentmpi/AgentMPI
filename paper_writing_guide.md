# Writing & Editing Checkpoints

Atomic pass-by-pass checklist for scientific manuscripts. Each box is one thing to check, one decision to make.

Adapted from [Nicole Yunger Halpern, "Nicole's guide to writing and editing," *Quantum Frontiers*, 26 Aug 2026](https://quantumfrontiers.com/2026/08/26/nicoles-guide-to-writing-and-editing/). Underlying authorities: *The Elements of Style* and the [Physical Review style guide](https://cdn.journals.aps.org/files/styleguide-pr.pdf).

**How to use:** run one pass per section. Don't mix passes — checking structure and hyphens simultaneously means doing neither well. Within each section, earlier boxes matter more than later ones.

---

## Pass 1 — Structure

- [ ] Paper follows motivation → result → physical significance, in that order
- [ ] Every paragraph opens with a topic sentence
- [ ] Every section (except intro and conclusion) opens with its takeaway
- [ ] Every section then gives a roadmap, with links to its subsections
- [ ] Same treatment applied down the hierarchy: subsections, subsubsections
- [ ] No subsection starts immediately under a section heading — overview text sits between them
- [ ] Every paragraph runs at least three sentences
- [ ] Every equation is prefaced by what it means and where it came from, before the symbols appear
- [ ] Every story (derivation, proof, experimental procedure) runs start to finish, no rewinding
- [ ] Nested objects are introduced outermost-first (generators, then group, then subgroup, then element) — never the reverse
- [ ] Each sentence bridges to the next: shared idea first, new idea second
- [ ] No transition reads as a non sequitur
- [ ] Citations sit at the end of a sentence or of a comma-closed phrase, unless there's a specific reason otherwise
- [ ] Title reads like a headline — subject plus predicate, asserting a claim

## Pass 2 — Word choice

- [ ] Nouns and verbs carry the meaning; adjectives and adverbs aren't propping up weak ones
- [ ] Verbs are specific (prepare, evolve, measure) rather than generic (*to be*, *take*)
- [ ] No "we investigate / study / analyze" — replaced with what was actually achieved (prove, test, confirm, discover, find)
- [ ] No editorializing adverbs: *importantly*, *remarkably*, *interestingly*. Show it; let the reader conclude it
- [ ] Active voice throughout
- [ ] No contractions
- [ ] Possessives used freely — they're not contractions and they shorten sentences
- [ ] "Only" sits directly before the thing it limits
- [ ] "General" means *subsumes every case*; if any case escapes, the word is wrong
- [ ] Every *general / generally / in general* that adds nothing is deleted
- [ ] "Generic" reserved for *typical* or *common*
- [ ] "Then" appears only in chronology or in if–then constructions
- [ ] "For" checked against its actual meaning — often the right word is *if*, *per*, or *at*
- [ ] "Admit of" keeps its *of*
- [ ] Ordinals written *first, second, last* — not *firstly, secondly, lastly*
- [ ] Only humans assume and suppose; equations and protocols don't
- [ ] Factors are multiplied, terms are summed — the two words aren't swapped
- [ ] Equality stated as equality, not as *agrees with*, *matches*, or *we identify X with Y*
- [ ] *Complex* not used where *nonreal* is meant — every real number is complex
- [ ] Regime (x ≪ y) not described as a limit; limits are limits

### Concision substitutions

- [ ] `is equal to` → **equals**
- [ ] `is able to` → **can**
- [ ] `places an upper bound on` → **upper-bounds** (same for lower)
- [ ] `a large number of` → **many**; `a small number of` → **few**
- [ ] `we refer to X as Y` → **we call X Y**
- [ ] `as long as` → **if**
- [ ] `strictly greater than` → **greater than** (`>` already says it)
- [ ] `so-called` → *cut*
- [ ] `note that` / `we note that` → *cut*
- [ ] `we have that` → *cut*; lead with the derivation or a prose gloss instead

## Pass 3 — Person and voice

- [ ] First person used only where it's needed
- [ ] Definitional statements de-personalized ("The superscript denotes…" over "We use the superscript to denote…")
- [ ] Forward-looking statements use *one*, not *we*
- [ ] First-person plural retained where it walks the reader through a derivation
- [ ] Sole-authored papers use *I*, never *we*
- [ ] "We measure / we evolve" claimed only by experimentalists who did it; otherwise use *consider*, *suppose*, or imperative instructions

## Pass 4 — Grammar traps

- [ ] No dangling modifiers
- [ ] No empty subjects
- [ ] Every verb has the noun that actually performs it (equations don't calculate sums)
- [ ] Singular and plural agree across noun, verb, and adjective
- [ ] Equations aren't justified by "we used that…" — use *we applied*, *follows from*, or *since*
- [ ] Tense is consistent when describing what was accomplished
- [ ] Experiments described in past tense
- [ ] Proof steps described in present tense

## Pass 5 — Punctuation

- [ ] Lists of three or more: commas normally, semicolons if any item already contains a comma
- [ ] Semicolons (outside lists) are followed by a complete clause
- [ ] Quotation marks follow one national convention consistently — periods and commas inside for American English
- [ ] LaTeX quotation marks use the backtick/apostrophe forms, not the keyboard `"` key
- [ ] Em dashes carry no surrounding spaces
- [ ] En dashes used for compound modifiers built from two names (Feynman–Kitaev)
- [ ] Compound adjectives hyphenated
- [ ] No hyphen after an *-ly* adverb
- [ ] Every prefix hyphen checked against the Physical Review style guide

## Pass 6 — Math and notation

- [ ] Every symbol earns its place; symbols appearing once are deleted, symbols appearing twice are challenged
- [ ] Every symbol is defined before its first use — exceptions only for universally known symbols where early definition would break the flow
- [ ] Sentences containing math still obey grammar and end with punctuation
- [ ] Lists of expressions take an *and* before the last; three or more take commas
- [ ] New functions get single-letter names
- [ ] Word-like sub/superscripts are upright, not italic
- [ ] Variable superscripts are parenthesized so they don't read as exponents; word-like ones are not
- [ ] Definitions use `\coloneqq` / `\eqqcolon` when the defined symbol stands alone
- [ ] `\equiv` used only when the defined symbol doesn't stand alone
- [ ] Axes written as italic letter plus *-axis*, with no hat, arrow, or boldface
- [ ] Indices avoid `i` (reads as √−1) — use `j`, unless the audience is engineers
- [ ] Indices avoid lowercase `l` — use `\ell`
- [ ] `\approx` and `~` are distinguished; prefer the approximation, which says more
- [ ] Big-O and `~` not used together
- [ ] `\ldots` (not `\cdots`) stands in for a pattern
- [ ] At least two explicit examples precede any `\ldots` — one can't establish a pattern
- [ ] Nested delimiters follow the Physical Review ordering
- [ ] `\left` / `\right` used so nothing protrudes past its delimiters
- [ ] Operators and matrices linked by an arrow, never an equals sign
- [ ] Symbols introduced inline sit right after the noun naming them, with no comma or colon

## Pass 7 — LaTeX mechanics

- [ ] Every displayed equation is numbered
- [ ] Displayed math uses `align`, not `equation`
- [ ] Alignment `&` sits just left of the first relation symbol, and of any relation opening a later line
- [ ] Continuation lines starting with an operator begin under the symbol just right of the first `=`
- [ ] The operator opening a continuation line sits at the start of that line, not trailing the previous one
- [ ] Default alignment overridden only to save space in a *PRL* or to pull a far-right `=` back
- [ ] Blank lines appear only where a new paragraph is intended — including around displayed equations

## Pass 8 — Abbreviations and acronyms

- [ ] No sentence opens with an abbreviation
- [ ] *Figure, Section, Professor, Appendix* abbreviated mid-sentence
- [ ] *Sections* (plural) never abbreviated
- [ ] Acronyms set in capitals
- [ ] Each acronym defined at first use
- [ ] After that, the acronym alone appears in body text; spelled-out forms allowed in headings, figure captions, and table titles where they aid clarity

## Pass 9 — Sentence-level polish

- [ ] Sentences are short enough to hold in one pass
- [ ] Sentence structure is flat, not nested — if you have to diagram it with brackets, split it
- [ ] Every remaining sentence has been read aloud once

---

## When the checklist runs out

1. Physical Review style guide
2. *The Elements of Style*
3. Writing guides from university writing centres or working human editors — weight these over generic online advice
4. Not physics papers

## Using this with an LLM

- [ ] Feed this file to the model and ask it to flag violations by number, not to rewrite
- [ ] Have it stop proposing fixes after the first few passes, so you're the one solving the problems
