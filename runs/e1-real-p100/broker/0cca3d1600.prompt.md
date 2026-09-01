# AgentMPI rank 0: propose renderings

You are translating one passage of *The Wonderful Wizard of Oz* into **Spanish**.
Before translating, the population agrees on how to render the names and coined
terms that recur across the whole book, so that every passage uses the same ones.

## Your passage (passage 0 of the book)

[Illustration]

The Wonderful Wizard of Oz

by L. Frank Baum

This book is dedicated to my good friend & comrade
My Wife
L.F.B.

Contents

Introduction
 Chapter I. The Cyclone
 Chapter II. The Council with the Munchkins
 Chapter III. How Dorothy Saved the Scarecrow
 Chapter IV. The Road Through the Forest
 Chapter V. The Rescue of the Tin Woodman
 Chapter VI.  The Cowardly Lion
 Chapter VII. The Journey to the Great Oz
 Chapter VIII. The Deadly Poppy Field
 Chapter IX. The Queen of the Field Mice
 Chapter X. The Guardian of the Gates
 Chapter XI. The Emerald City of Oz
 Chapter XII. The Search for the Wicked Witch
 Chapter XIII. The Rescue
 Chapter XIV. The Winged Monkeys
 Chapter XV. The Discovery of Oz, the Terrible
 Chapter XVI. The Magic Art of the Great Humbug
 Chapter XVII. How the Balloon Was Launched
 Chapter XVIII. Away to the South
 Chapter XIX. Attacked by the Fighting Trees
 Chapter XX. The Dainty China Country
 Chapter XXI. The Lion Becomes the King of Beasts
 Chapter XXII. The Country of the Quadlings
 Chapter XXIII. Glinda The Good Witch Grants Dorothy’s Wish
 Chapter XXIV. Home Again

Introduction

Folklore, legends, myths and fairy tales have followed childhood
through the ages, for every healthy youngster has a wholesome and
instinctive love for stories fantastic, marvelous and manifestly
unreal. The winged fairies of Grimm and Andersen have brought more
happiness to childish hearts than all other human creations.

Yet the old time fairy tale, having served for generations, may now be
classed as “historical” in the children’s library; for the time has
come for a series of newer “wonder tales” in which the stereotyped
genie, dwarf and fairy are eliminated, together with all the horrible
and blood-curdling incidents devised by their authors to point a
fearsome moral to each tale. Modern education includes morality;
therefore the modern child seeks only entertainment in its wonder tales
and gladly dispenses with all disagreeable incident.

Having this thought in mind, the story of “The Wonderful Wizard of Oz”
was written solely to please children of today. It aspires to being a
modernized fairy tale, in which the wonderment and joy are retained and
the heartaches and nightmares are left out.

## The recurring terms that appear in your passage

["Cowardly Lion", "Dorothy", "Emerald City", "Glinda", "Guardian", "King", "Munchkins", "Quadlings", "Queen", "Scarecrow", "South", "Winged Monkeys", "Witch", "Wizard", "Woodman"]

## What to write

Write ONLY a JSON object, with no prose around it and no markdown fence:

{"rank": 0,
 "renderings": {"<source term>": "<your proposed Spanish rendering>", ...}}

Include every term in the list above and nothing else.  Propose the rendering you
would actually use in a published literary translation.  Do not explain.

Your peers are doing the same for their passages.  Where two of you propose
different renderings for one term, the runtime will surface the disagreement and
one rank will settle it; you do not need to guess what anyone else will say.
