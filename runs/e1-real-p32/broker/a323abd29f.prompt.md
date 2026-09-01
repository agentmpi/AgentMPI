# AgentMPI rank 23: propose renderings

You are translating one passage of *The Wonderful Wizard of Oz* into **Spanish**.
Before translating, the population agrees on how to render the names and coined
terms that recur across the whole book, so that every passage uses the same ones.

## Your passage (passage 23 of the book)

“Oh! He’s a curious animal and seems remarkably small, now that I look
at him. No one would think of biting such a little thing, except a
coward like me,” continued the Lion sadly.

“What makes you a coward?” asked Dorothy, looking at the great beast in
wonder, for he was as big as a small horse.

“It’s a mystery,” replied the Lion. “I suppose I was born that way. All
the other animals in the forest naturally expect me to be brave, for
the Lion is everywhere thought to be the King of Beasts. I learned that
if I roared very loudly every living thing was frightened and got out
of my way. Whenever I’ve met a man I’ve been awfully scared; but I just
roared at him, and he has always run away as fast as he could go. If
the elephants and the tigers and the bears had ever tried to fight me,
I should have run myself—I’m such a coward; but just as soon as they
hear me roar they all try to get away from me, and of course I let them
go.”

“But that isn’t right. The King of Beasts shouldn’t be a coward,” said
the Scarecrow.

“I know it,” returned the Lion, wiping a tear from his eye with the tip
of his tail. “It is my great sorrow, and makes my life very unhappy.
But whenever there is danger, my heart begins to beat fast.”

“Perhaps you have heart disease,” said the Tin Woodman.

“It may be,” said the Lion.

“If you have,” continued the Tin Woodman, “you ought to be glad, for it
proves you have a heart. For my part, I have no heart; so I cannot have
heart disease.”

“Perhaps,” said the Lion thoughtfully, “if I had no heart I should not
be a coward.”

“Have you brains?” asked the Scarecrow.

“I suppose so. I’ve never looked to see,” replied the Lion.

“I am going to the Great Oz to ask him to give me some,” remarked the
Scarecrow, “for my head is stuffed with straw.”

“And I am going to ask him to give me a heart,” said the Woodman.

“And I am going to ask him to send Toto and me back to Kansas,” added
Dorothy.

“Do you think Oz could give me courage?” asked the Cowardly Lion.

“Just as easily as he could give me brains,” said the Scarecrow.

## The recurring terms that appear in your passage

["Cowardly Lion", "Dorothy", "Kansas", "King", "Scarecrow", "Toto", "Woodman"]

## What to write

Write ONLY a JSON object, with no prose around it and no markdown fence:

{"rank": 23,
 "renderings": {"<source term>": "<your proposed Spanish rendering>", ...}}

Include every term in the list above and nothing else.  Propose the rendering you
would actually use in a published literary translation.  Do not explain.

Your peers are doing the same for their passages.  Where two of you propose
different renderings for one term, the runtime will surface the disagreement and
one rank will settle it; you do not need to guess what anyone else will say.
