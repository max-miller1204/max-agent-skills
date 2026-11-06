# Verification pass

Run after drafting a lesson and before anything reaches Anki. Use a fresh sub-agent when the host
has them (it has not seen the drafting, so it reads what is actually written); otherwise do it as a
separate, explicit second pass.

Give the verifier the lesson file, the source files it cites, and this rubric. It checks every
lesson section, checkpoint, practice answer and card against the cited source text (open the PDF
pages, code lines and so on, don't trust memory) and returns, per item:

- **pass**: supported by the cited source, correct, clear.
- **fix**: the correction, applied directly in the file (wrong detail, missing assumption,
  ambiguous question, answer that accepts the wrong thing, bad citation, Unicode math).
- **drop**: unsupported and not fixable from the sources, or a card that breaks the rules in
  cards.md (two objectives, needs context, trivia, duplicate). Remove it from the file; if it was
  already in Anki, the daily run suspends its note (cards.md, Removing a card).

Also check:
- Content beyond the sources is inside `> [!supplementary]` and says where it comes from. (An
  `[!analogy]` needs no source, but must not contradict them.)
- Practice answers are right, and runnable exercises actually build and behave as described
  (run them).
- Every practice problem earns its slot (lessons.md, Practice): one that only repeats another
  problem, a checkpoint or a card is a **drop**, and an idea worth practicing with no problem is a
  **fix**.
- Every card has a `Source`, one objective, and the right note type for its answer.
- Code snippets match the course code they cite.
- Coverage: go through every slide or page of each source, including figure-only ones (look at the
  page images, not only extracted text), and add what the lesson is missing. A rewrite inherits the
  old lesson's gaps unless you check the source itself.
- Tool versions and outputs quoted in supplementary notes (compiler, library versions) still hold on
  this machine; rerun and update them.
- The lesson has the shape in lessons.md: plain TL;DR, builds-on line, a breakdown map whose links
  all reach their sections, and one checkpoint per section, each with an answer (and a
  "A good answer mentions:" checklist on an `Explain:` checkpoint).

Feynman check, read as a newcomer would (each problem is a **fix**):
- A term used before the lesson explains it, including in the TL;DR, an opener or a checkpoint.
- A section whose first paragraph isn't plain words, or plain words that replace the precise
  version instead of leading into it.
- A precise part that retells its opener (the same idea said twice in a row) instead of picking up
  where it stopped.
- A skipped step: a "so" or "therefore" that doesn't follow from what came before.
- A word standing in for a reason ("basically", "simply", "just", "obviously", "it turns out").
- An `[!analogy]` with no "Where it breaks:" line, or one that contradicts the sources.
- Padding or repetition that adds length and no understanding.

When the lesson is a rewrite of an existing one, also compare it with the previous version (from the
vault's git history: `git -C <vault> show HEAD:<lesson path>` before the rewrite was committed, or
the commit the rewrite replaced). Every term, definition, fact, result, code example and equation the
old lesson taught from the sources must still be in the new one, or replaced by something better
from the sources. Restore anything lost by accident (a **fix**); a deliberate removal gets one line
in the report saying why.

When everything is pass or fixed, set `verified: true` in the lesson's frontmatter and write a short
report to `studious/reports/<date>-verify-<lesson file name>.md`. Its first line after the title is
`Lesson: [[<lesson path without .md>]]` (for example `[[courses/Fall-2026/CS3214/lessons/07-condition-variables]]`; a link, so the report shows in the lesson's
backlinks), then counts of pass/fix/drop and one line per fix or drop.
