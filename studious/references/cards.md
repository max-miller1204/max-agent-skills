# Cards

Cards live at the bottom of the lesson that teaches them, under `## Cards`, and `scripts/anki.py push`
sends them to Anki. They are review for what the lesson taught: if a card needs the lesson to make
sense, the card is wrong.

## Block format

```
> [!card]- Short label shown in Obsidian
> %% type: Basic+ · tags: cheatsheet %%
> #### Front
> Markdown. Math as $...$, code in fences, images as ![[name.png]].
> #### Back
> ...
> #### Extra
> Why the answer is right, or the common wrong answer and why it fails.
> #### Source
> L_CondVars slide 9
```

- Every line of the block starts with `>`. Each `#### Name` heading starts a field; the name must
  be one of the note type's fields. `Source` is required on every card.
- The `%% … %%` line holds `type` (required), optional `tags`, and the `id` that `push` adds. Never
  write or change an `id` yourself.
- `tags: cheatsheet` marks something that belongs on that exam's cheat sheet (formulas, API
  signatures, templates). Exam tags come from the lesson's `goals`; don't add them by hand.

## Note types

| Type | Fields | Use it for |
|---|---|---|
| `Basic+` | Front, Back, Extra, Source, Add Reverse | Why/how questions, contrasts, applications. Put `y` in `Add Reverse` only when the reverse direction is a real question too (term ↔ definition). |
| `Basic+ (type in)` | Front, Back, Extra, Source | One exact short answer: a number, an output, a token. Back is plain text, no Markdown. The front states the format: `(number)`, `(letters only)`, `(rows, cols)`. |
| `Cloze+` | Text, Back Extra, Source | Definitions, steps of a procedure (one deletion per step), parts of a formula. |
| `Code+` | Prompt, Code, Answer, Extra, Source | Predict the output, spot the bug, fill the blank line (`____`), what happens when… Code goes in a fenced block in `Code`. |
| `Image Occlusion` | Image, Occlusion, Header, Back Extra (+ Source) | Labeled figures and diagrams. One shape per line in `Occlusion`; `Source` is moved under Back Extra. |

## What makes a good card

- **One objective per card.** An answer longer than about 3 lines gets split.
- **Stands alone.** Restate the setup on the front; never "as in the earlier card".
- **Ask for understanding, not wording.** Mix kinds by what the material needs: mechanism (why
  does X happen), application (which tool here, and why), prediction (what does this print / which
  thread runs next), contrast (X vs Y), debugging (what's wrong with this code), boundary (when
  does X stop being true), and essential facts (API names, definitions, formulas) where recall
  itself matters.
- **Lists become one cloze per item**, not "name the five…".
- **Calculation cards practice the method**: at most 3 per dataset, each with a different twist.
- **Skip what nobody would get wrong.** No quota: a dense lecture might need 20 cards, a light one 6.
- `Extra` carries the reason, or the tempting wrong answer and why it fails.
- `Source` is short: `L_CondVars slide 9`, `coordination_queue.c lines 22-37`, `Test2 review guide 2.6`.

## Formatting rules (enforced by push)

- Math: `$...$` / `$$...$$` in the vault; push turns them into MathJax `\(...\)` / `\[...\]`. Inside
  a cloze, put a space before the closing braces when the LaTeX ends in `}`: `{{c1::\frac{a}{b} }}`.
- Code: fenced with the language (` ```c `); push highlights it. Keep it under about 15 lines. No
  cloze deletions inside code fences (highlighting would split them): use a `Code+` fill-the-blank.
- Images: `![[name.png]]` from `attachments/`. Mermaid does not render in Anki: export the diagram
  to an image first, or ask in words.
- Image occlusion shapes: `{{c1::image-occlusion:rect:left=0.1000:top=0.2000:width=0.3000:height=0.1000:oi=1}}`,
  fractions of the image. Find them with `slides.py grid` and check them with `slides.py boxes`.

## Push and gating

```bash
uv run scripts/anki.py push courses/CS3214/lessons/07-condition-variables.md
```

Push assigns ids, refuses lessons that aren't `verified: true`, and creates new cards only after
the lesson is `done: true` (or `--ungated` in cram mode). Cards already in Anki are updated in place,
keeping their review history. Every push records each card's fingerprint in `studious/pushed.json`,
and the daily run pushes again any lesson whose cards in Anki were edited since. The lesson's `deck`
must sit under its course deck (`CS3214::...`).
