# Translation task

Translate your assigned chunk of *Alice's Adventures in Wonderland* by Lewis
Carroll into **French**.

## Requirements

1. Translate the prose faithfully and idiomatically. Keep paragraph breaks.
   Do not summarise, do not omit, do not add commentary.
2. Keep the chapter heading, translated.
3. Verse stays verse.
4. **Terminology.** The recurring names below must be rendered the same way
   in every chunk of the book. You will be told which renderings earlier
   chunks already committed to; adopt those exactly. Only choose a rendering
   yourself for a name that has not been fixed yet.

## Names that must be consistent across the whole book

- the Mock Turtle
- the Cheshire Cat
- the Knave of Hearts
- the March Hare
- the Mad Hatter
- the Duchess
- the Caterpillar
- the White Rabbit
- the Queen of Hearts
- the Gryphon
- the Dormouse
- the Caucus-race
- the Lobster Quadrille
- the Rabbit-Hole
- the Pool of Tears
- Wonderland

## Output contract

Your result must be a single JSON object with exactly these keys:

- `chunk_id`   (string) the id you were given
- `glossary`   (object) every name from the list above that occurs in *your*
               chunk, mapped to the exact French rendering you used
- `translation` (string) the full French translation of your chunk

Nothing else. No markdown fence around the JSON.
