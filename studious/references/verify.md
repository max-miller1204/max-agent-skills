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
  cards.md (two objectives, needs context, trivia, duplicate). Remove it from the file.

Also check:
- Content beyond the sources is inside `> [!supplementary]` and says where it comes from.
- Practice answers are right, and runnable exercises actually build and behave as described
  (run them).
- Every card has a `Source`, one objective, and the right note type for its answer.
- Code snippets match the course code they cite.

When everything is pass or fixed, set `verified: true` in the lesson's frontmatter and write a short
report to `studious/reports/<date>-verify-<lesson file name>.md`. Its first line after the title is
`Lesson: [[courses/<COURSE>/lessons/<NN>-<slug>]]` (a link, so the report shows in the lesson's
backlinks), then counts of pass/fix/drop and one line per fix or drop.
