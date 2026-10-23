---
name: to-anki
description: "Turn course materials (lecture PDFs, slides, lab notebooks, notes) into an Anki deck that uses Anki's built-in note types (Basic, reversed, type-in, Cloze, Image Occlusion), with slide images and verified code-output cards. Use when the user asks for Anki flashcards or a study deck from a folder of materials."
---

# Course materials to Anki

Build one Anki text-import file plus media. Use the user's **stock note types only**. Never create a custom note type and never use genanki or an `.apkg`: both add a new note type to the user's collection.

Scripts live in `scripts/` next to this file. Run Python with `uv run -q --no-project --with <pkgs> python -I <script> ... </dev/null`. Write every script to a file in the scratchpad first; inline `python -` heredocs have hung in this shell.

## 1. Scope the material

- Find what the test covers (the user's paste, a syllabus or review sheet). Only use those lectures and labs.
- Extract text: `pdftotext -layout <pdf> <out.txt>`. Notebooks: load the JSON and print each cell's source and outputs.
- Many slides are pictures, so `pdftotext` misses them. Render the slides that have little text (`scripts/slides.py render`) and look at them. Diagrams, plots and formula images often hold facts that are not in the text layer.
- Note slide typos or contradictions and tell the user how you handled them.

## 2. Find the user's Anki setup

```sh
defaults read /Applications/Anki.app/Contents/Info.plist CFBundleShortVersionString   # Anki version
ls "$HOME/Library/Application Support/Anki2"                                           # profile folders
```

Copy `collection.anki2` (and `-wal`) to the scratchpad and read the copy only. Get the field names per note type and the deck names (the `fields` table needs a `unicase` collation, so read it from Python with `create_collation`). If a course parent deck exists (e.g. `CMDA3654`), nest the new decks under it: `CMDA3654::Quiz 2::L09 Topic`. Mention any empty decks that look meant for this, but don't touch them.

## 3. Choose a card type per fact

| Fact | Note type |
|---|---|
| Formula, definition, list of named items | **Cloze** (`{{c1::...}}`, one cloze per item to recall; put the explanation in Back Extra) |
| Term with a definition worth recalling both ways | **Basic (and reversed card)** |
| Short exact answer: a number, fraction, shape, default value | **Basic (type in the answer)**; say the format in the question ("fraction", "Format: (rows, cols)") |
| Explanation, why or compare questions, multi-line code output | **Basic** |
| Labeled diagram or a slide where key terms can be hidden | **Image Occlusion** |
| Plot or figure to interpret | **Basic** with the cropped image on the front |

Write **all math as LaTeX** (MathJax `\( ... \)` inline, `\[ ... \]` display) in Python raw strings, not Unicode symbols with `<sub>` tags. Clozes can sit inside a formula: `\(P(A \mid B) = \dfrac{ {{c1::P(A \land B)}} }{ {{c2::P(B)}} }\)`. Type-in answers stay plain text. Use the slides' notation (∧ → `\land`, ∨ → `\lor`), and on the first card that uses it, add a short note saying what the notation means (∧ = AND = ∩).

Aim for a mix. Cover worked examples from the slides with real numbers, the common mistakes, and every point the slides list. For quizzes that ask "what is the output", write code cards and **execute them** (`Deck.code_card` / `code_typed`) so answers are real output, never written from memory. Add standard library facts that make good output questions (defaults, error cases) and mark them as beyond the slides in the summary.

### If the user can bring a cheat sheet

Only when the user says a cheat sheet is allowed (theirs, or one you made). Read the sheet first. Cards should then train what the sheet can't do for them in a timed test. Still build every card, but tag the recall-only ones `on-cheatsheet` with `Deck.tag("on-cheatsheet", <front text start>, ...)` instead of leaving them out. A later exam may not allow a sheet, and tagged cards can be suspended now and brought back then.

Tag a note `on-cheatsheet` when **both** are true:
- its answer is printed on the sheet (a formula, definition, list, table row, default value, or an output shown verbatim)
- the card only asks you to recite it

Leave these untagged even when the sheet has them:
- **calculations and worked problems**: the sheet gives the formula, but speed comes from practice, and the test uses new numbers
- **traps and conceptual why-questions**: they tell you what a question is asking and where to look
- **code-output cards**, unless that exact snippet and output is on the sheet
- **image and occlusion cards**
- **a few core items** used in almost every question, where looking them up each time would eat the clock (e.g. the conditional probability formula, Bayes' rule, the main algorithm's steps, the common join types)

Report the tagged and kept counts per deck. Tell the user how to suspend the tagged cards: Browse, search `tag:on-cheatsheet`, select all, Cards > Toggle Suspend (Ctrl/Cmd+J). Unsuspend the same way.

## 4. Images

1. `slides.py render <pdf> <page> hi/<name>.png` for each candidate slide (1200px wide).
2. Review candidates cheaply: `slides.py montage` several low-res renders into one picture.
3. Crop figures with `slides.py crop` so the slide text doesn't give away the answer. Check the crops in a montage. Paint over stray elements that intrude (Pillow `ImageDraw.rectangle`).
4. Image Occlusion: `slides.py grid` to read off coordinates, write boxes as fractions of the **full** image, then `slides.py boxes` and look at the result before using them. For text in the PDF layer, `pdftotext -bbox -f N -l N` gives exact word boxes (divide by the page size). Boxes that share a cloze number hide together.
5. Name media with a unique prefix (e.g. `cmda3654q2_L12-11.png`) so it can't collide with existing files.

## 5. Build the import file

Write a per-course build script that imports `scripts/deck.py` (usage in its docstring) and writes `<course folder>/<test>/anki/<Name>_import.txt` plus `anki/media/`. Format details and pitfalls: [references/anki-format.md](references/anki-format.md).

**Revising a deck the user already imported:** keep the notes in the same order with the same note types, and pass `keep_guids_from=<previous import file>` to `write()`. Re-import then updates the notes in place and keeps review history. Notes that didn't change show up as "duplicate" in the log, which is expected. Adding or removing notes breaks the order mapping, so give new notes their own rows at the end instead.

## 6. Verify before handing over

Run `scripts/test_import.py` against the collection **copy** with the pip `anki` matching the app version (`--python 3.12 --with "anki==<version>"`). Confirm: every note is new, no problems listed, no non-stock note types, no empty questions, and each Image Occlusion note parses the expected number of shapes. Fix and rebuild until clean.

## 7. Install media and hand over

Copy the media into `<profile>/collection.media/` with `cp -n` (new files only, never overwrite). State this in the plan before doing it. Don't write to `collection.anki2` itself: Anki may be open.

Tell the user:
- the card counts by note type and deck, plus the image and occlusion cards
- anything beyond the slides, any slide errors, and how you handled them
- import steps: **File > Import**, choose the `.txt` (note types and decks are set inside it), click Import
- that type-in cards compare exactly, so answer in the stated format
- if an earlier deck or custom note type from you exists, ask them to delete it first (Browse or Tools > Manage Note Types)
