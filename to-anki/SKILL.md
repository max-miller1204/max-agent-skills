---
name: to-anki
description: "Turn course materials (lecture PDFs, slides, lab notebooks, notes) into an Anki deck that uses three cloned note types with Extra and Source fields (Basic+, Basic+ (type in), Cloze+) plus stock Image Occlusion, with slide images, verified code-output cards, and a study plan and settings check for the test date. Use when the user asks for Anki flashcards or a study deck from a folder of materials, or to add a new lecture to an existing deck."
---

# Course materials to Anki

Build one Anki text-import file plus media. Notes use the skill's three note types, defined in `scripts/notetypes.py`, plus stock Image Occlusion:

- **Basic+**: Front, Back, Extra, Source, Add Reverse (a reverse card only when Add Reverse is filled)
- **Basic+ (type in)**: Front, Back, Extra, Source (Extra shows under the typed comparison)
- **Cloze+**: Text, Back Extra, Source

They are clones of the stock types with shared CSS (left-aligned code and tables, night mode) and no JavaScript, so they render the same on desktop and mobile. Never create any other note type, and never use genanki. The only `.apkg` is the one-time installer from `notetypes.py`.

Scripts live in `scripts/` next to this file. Run Python with `uv run -q --no-project --with <pkgs> python -I <script> ... </dev/null`. Write every script to a file in the scratchpad first; inline `python -` heredocs have hung in this shell.

## 0. Ask up front, then pick the mode

Ask in one message for whatever the user hasn't said:
- what the test covers (or the syllabus or review sheet that says)
- the test date (skip it if that test's `build.py` already has `Deck(test=...)`; if the user gives a new date, update it there)
- whether a cheat sheet is allowed
- their daily study budget, only if `~/anki-settings/settings/study.json` doesn't exist (minutes per day and lectures per week across all classes; write the file for them)

`study.json` sets the budget for every class at once: reviews/day = minutes × 60 ÷ seconds_per_review, new cards/day for all classes = reviews/day ÷ 10, and the **target cards per lecture** = new/day × 7 ÷ lectures_per_week (about 16 at 45 minutes and 11.5 lectures). `study_plan.py` prints these numbers.

Then look for an existing deck for this course: `<course folder>/*/anki/build.py`.
- **Adding a lecture** (a build for the upcoming test exists): follow "Adding to an existing deck" below, not steps 1 to 5 in full.
- **New deck**: steps 1 to 8. A lecture for a test that has no build yet also starts a new build, in a new `<course>/<test>/anki/` folder with its own media prefix and exam tag, in the same course deck.

Spacing needs days, so the skill pays off most when it runs after each lecture. Say so once if the user asks for a whole deck close to the test. With 2 days or less to go, it's cram mode: build the highest-yield cards first (what the test format asks: code output, calculations, the review sheet's topics), keep the deck lean (the user sees about 60 new cards an hour), and hand over the topic order with the deck.

### Adding to an existing deck

1. Read the existing `build.py`, its `_guids.json` and the import file, so the new cards match the deck's style, setups and naming. The test date is already in `Deck(test=...)`. Check that every deck in `decks=` still exists in the collection: if the user moved the course under the parent deck (e.g. `Fall (2026-2027)::`), update the names, or new notes recreate the old decks.
2. Scope only the new material (step 1), and add it as a new topic key in `topics=` and `decks=` (a new key may point at the same deck). Give every new card a new `id=`.
3. Leave existing cards alone unless the new material shows one is wrong, and then edit it under its own id. Never reuse an id for a different fact.
4. Rebuild, then run steps 6 and 7. The test import should show the new cards as new and every other note as a duplicate (unchanged) or updated.

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

Copy `collection.anki2` (and `-wal`) to the scratchpad and read the copy only. Get the note types with their field names, the deck names and the existing tags (the `fields` and `tags` tables need a `unicase` collation, so read them from Python with `create_collation`). Mention any empty decks that look meant for this, but don't touch them.

**Note types.** If Basic+, Basic+ (type in) and Cloze+ are missing, build the installer with `scripts/notetypes.py` (usage in its docstring), copy `note_types.apkg` next to the deck, and have the user import it once (File > Import), then delete the "Note types (delete me)" deck it creates. If they exist but their fields differ from the list above, stop and ask: the user may have edited them, and changing fields forces a one-way sync. A deck built earlier with stock types gets converted once with Browse > Notes > Change Note Type (Basic and Basic (and reversed card) → Basic+, Basic (type in the answer) → Basic+ (type in), Cloze → Cloze+, default mapping), which keeps review history and costs one one-way sync. Rehearse it on a collection copy first with `col.models.change_notetype_info()` / `change_notetype_of_notes()` (the pip `anki` equivalent of the dialog), then import the rebuilt file and confirm cards, decks and review counts are unchanged.

**Decks and tags.** One deck per test, under the course, under the parent deck from `study.json`: `<parent>::<Course>::<Exam>`, e.g. `Fall (2026-2027)::CMDA3654::Quiz3` (the import creates missing decks). The user can then study everything (the parent), one class, or one test with a single click; with "Limits start from top" on, every choice stays within the parent's total (verified with 26.9.3: cards studied in a subdeck count against the parent's and the course's daily totals). No deck per lecture: tags carry that, and a test deck interleaves its lectures. `Deck(course=..., exam=..., topics=...)` writes one tag tree per course:

- `CMDA3654::exam::Quiz2`: the test the note was made for
- `CMDA3654::topic::L09_Probability`: the lecture or lab
- `CMDA3654::cheatsheet::Quiz2`: only via `d.tag(d.cheatsheet_tag, ...)`

Tag names can't contain spaces. Anki tags are case-insensitive and keep the casing of a tag that already exists, so match the course root's existing casing. Tags still allow any other slice (a filtered deck with `tag:CMDA3654::topic::L12_KMeans`, say). A deck that already has per-lecture subdecks keeps them: re-import never moves existing cards (only new notes use the deck column), so new notes for an old test go in its old deck.

## 3. Choose a card type per fact

| Fact | Note type |
|---|---|
| Formula, definition, list of named items | **Cloze+** via `Deck.cloze` (`{{c1::...}}`, one cloze per item to recall; explanation in Back Extra) |
| Term with a definition worth recalling both ways | **Basic+** with a reverse card, via `Deck.rev` |
| Short exact answer: a number, fraction, shape, default value | **Basic+ (type in)** via `Deck.typed`; say the format in the question ("fraction", "Format: (rows, cols)"), explanation in `why=` |
| Explanation, why or compare questions, multi-line code output | **Basic+** via `Deck.basic` / `Deck.code_card` |
| DataFrame result (filter, groupby, merge, reshape) | **Basic+** via `Deck.table_card`: one targeted question, full result in Extra. Reverse: `Deck.which_code` |
| Labeled diagram or a slide where key terms can be hidden | **Image Occlusion** (stock) |
| Plot or figure to interpret | **Basic+** with the cropped image on the front |

Write **all math as LaTeX** (MathJax `\( ... \)` inline, `\[ ... \]` display) in Python raw strings, not Unicode symbols with `<sub>` tags. Clozes can sit inside a formula: `\(P(A \mid B) = \dfrac{ {{c1::P(A \land B)}} }{ {{c2::P(B)}} }\)`. Type-in answers stay plain text. Use the slides' notation (∧ → `\land`, ∨ → `\lor`), and on the first card that uses it, add a short note saying what the notation means (∧ = AND = ∩).

**Card budget.** Aim for about the target cards per lecture from `study.json` (step 0). It's a target, not a quota: a lecture with nothing new gets few or no cards, and a dense one can go over. When the material supports far more, keep what the test asks (code output, calculations, worked examples, traps, compare cards for look-alikes) and drop trivia, slogans and recall-only facts on the cheat sheet first. Say in the summary how many cards each lecture got against the target, and what you left out.

Aim for a mix. Cover worked examples from the slides with real numbers, the common mistakes, and every point the slides list. For quizzes that ask "what is the output", write code cards and **execute them** (`Deck.code_card` / `code_typed`) so answers are real output, never written from memory. Add standard library facts that make good output questions (defaults, error cases) and mark them as beyond the slides in the summary.

### Every card must stand alone

Anki shuffles (interleaves) cards, so the user meets any card cold, with no memory of the card before it. Write each card so a person who knows the course but has not seen any other card can answer it from the front alone.

- **Put every given on the front.** If the answer uses a number, table, prior, dataset, or definition, show it on the front or restate it there. "Joint table: how do you compute P(a or b)?" with no table is broken. So is "Why does the pink curve rise?" when the answer depends on priors the card never states.
- **Repeat shared setups instead of pointing at them.** A running example (the candy bags, the flu numbers, the Lab 6 tables) gets restated in full on every card that uses it, in one short line. Never write "the example above", "same as before", "the earlier card", or "as in the candy example" on either side of a card.
- **Name the object.** "Basic change detection", "the black line", "same 6 points" mean nothing without context. Say what the thing is, or show the image that defines it. An image on the front must show everything the question refers to, and the question must say what the image is.
- **One idea per card.** If the back needs a formula, two calculations, a crossover point and an intuition line, split it into a rule card, a calculation card with the numbers given, and a short "why" card. Prefer several small cards over one that has to be recalled in a single go.
- **Interpretation cards still need the numbers.** For "why does this curve rise then fall" questions, state the inputs on the front, and ask for the one reason, not the full derivation. Put the derivation in a separate calculation card with the numbers given.

Before building, read every front as if it were the only card in the deck. For each one ask: could I answer this if I had never seen another card? Fix or split any that fail. In the final summary, say how many cards you restated or split for this reason.

### Card quality rules (from the Anki manual and Wozniak's "20 rules of formulating knowledge")

Sources: docs.ankiweb.net/editing.html, supermemo.com/en/blog/twenty-rules-of-formulating-knowledge, notes.lesleylai.info/Concepts/how-to-design-flashcards.

- **Understand before memorizing.** A card can't teach a concept the user hasn't grasped. For any non-obvious idea (a derivation, a curve, an algorithm step), include one plain-language intuition card ("why does X happen") next to the calculation cards, and say in the summary which topics need a lecture or notes read first. Don't pack the explanation into a recall card.
- **Cite the source on every card** with `src="L12 slide 11"` or `src="Lab 6 cell 14"`. When the user fails a card, the fix is to reread that slide, not to drill harder.
- **Ask one question about a table, not "sketch the table".** Recalling a whole 6-row result can't be graded honestly. Ask what the operation decides: which rows remain, the value of one cell (Salad under `map` vs `replace`), the index labels, the row count. `table_card` takes `answer=lambda result: ...` so the answer comes from running the code, and shows the full result on the back as context.
- **Label row behavior by the operation, never by the counts.** A groupby that returns as many rows as it got is still row-reducing. Pass `kind="row-reducing"` (or `row-preserving` / `row-adding`) only when the card teaches it; `deck.py` checks the claim against the counts and notes "but here every group has one row" when they match.
- **Calculation cards practice the method, not the number.** After a few reviews the user remembers "P(sunny) = 0.2" instead of how to get it. Keep at most 3 type-in cards per table or dataset, and make the others ask for the setup ("which cells do you add for \(P(a \lor b)\)?") or the rule.
- **Skip what nobody gets wrong.** "Probabilities sum to {{c1::1}}" and lecture-specific workflow slogans cost reviews and teach nothing. Every card should take a moment of real recall.
- **Minimum information.** One fact, one calculation, or one reason per card. Short questions, short answers. A back with more than about three lines or two separate ideas is a split candidate. Extra context goes in `why=` (the Extra field) or Back Extra, not in the part being recalled.
- **Name the subject on the front.** Start with a label when the term is ambiguous or course-specific ("K-means:", "Anomaly detection:", "pandas merge:", "Bayesian learning:"). This is the safe way to shorten a question without losing context.
- **Avoid sets and long enumerations.** "List the four reasons...", "five applications..." are hard to grade because partial recall is common. Turn them into one cloze per item (each its own card), or an ordered cloze with the neighbors shown. Only keep a list as one card if the user must recite it in order.
- **Combat interference.** Look-alike items (h4 vs h5, left vs right merge, supervised vs unsupervised, pivot vs pivot_table) get confused. Make each card's wording unambiguous, and where two items are commonly mixed up, add one compare card ("pivot vs pivot_table: which aggregates?").
- **Ask "why" and "how", not only "what".** Prefer questions that need an explanation or a computation over bare definitions, once the definition card exists.
- **Avoid vague or cue-able questions.** No "give an example of...", no open prompts with many correct answers, and no yes/no questions unless the answer must include the reason ("No, because...").
- **Attribute opinion.** If slides and a cheat sheet disagree, or a claim is the lecturer's view, say so on the card ("according to the lecture...").
- **Redundancy is fine** when it helps: a reversed pair, or a rule card plus a worked-example card, is not duplication. Duplicated setups across cards is the intended way to keep cards independent.
- **Images.** Use Image Occlusion for labeled diagrams, and for plots show only the series the question asks about, or say which one to look at, so the figure doesn't add confusion.
- **Don't group by predictable order.** Keep few decks and use tags for extra structure. Many tiny decks make answers guessable from the neighboring card.

### If the user can bring a cheat sheet

Only when the user says a cheat sheet is allowed (theirs, or one you made). Read the sheet first. Cards should then train what the sheet can't do for them in a timed test. Still build every card, but tag the recall-only ones with `d.tag_ids(d.cheatsheet_tag, <id>, ...)` (or `d.tag(d.cheatsheet_tag, <front text start>, ...)`), e.g. `CMDA3654::cheatsheet::Quiz2`, instead of leaving them out. A later exam may not allow a sheet, and tagged cards can be suspended now and brought back then.

Tag a note with the cheat-sheet tag when **both** are true:
- its answer is printed on the sheet (a formula, definition, list, table row, default value, or an output shown verbatim)
- the card only asks you to recite it

Leave these untagged even when the sheet has them:
- **calculations and worked problems**: the sheet gives the formula, but speed comes from practice, and the test uses new numbers
- **traps and conceptual why-questions**: they tell you what a question is asking and where to look
- **code-output cards**, unless that exact snippet and output is on the sheet
- **image and occlusion cards**
- **a few core items** used in almost every question, where looking them up each time would eat the clock (e.g. the conditional probability formula, Bayes' rule, the main algorithm's steps, the common join types)

Report the tagged and kept counts per topic. Tell the user how to suspend the tagged cards: Browse, search `tag:CMDA3654::cheatsheet::Quiz2`, select all, Cards > Toggle Suspend (Ctrl/Cmd+J). Unsuspend the same way.

## 4. Images

1. `slides.py render <pdf> <page> hi/<name>.png` for each candidate slide (1200px wide).
2. Review candidates cheaply: `slides.py montage` several low-res renders into one picture.
3. Crop figures with `slides.py crop` so the slide text doesn't give away the answer. Check the crops in a montage. Paint over stray elements that intrude (Pillow `ImageDraw.rectangle`).
4. Image Occlusion: `slides.py grid` to read off coordinates, write boxes as fractions of the **full** image, then `slides.py boxes` and look at the result before using them. For text in the PDF layer, `pdftotext -bbox -f N -l N` gives exact word boxes (divide by the page size). Boxes that share a cloze number hide together.
5. Name media with a unique prefix (e.g. `cmda3654q2_L12-11.png`) so it can't collide with existing files.

## 5. Build the import file

Write a per-course build script that imports `scripts/deck.py` (usage in its docstring) and writes `<course folder>/<test>/anki/<Name>_import.txt` plus `anki/media/`. Keep the build script next to the import file (`anki/build.py`), not in the scratchpad, so a later session can revise the deck. Format details and pitfalls: [references/anki-format.md](references/anki-format.md).

Pass the test date as `Deck(..., test="YYYY-MM-DD")`; `write()` saves it with the exam tag in `<Name>_import_plan.json` for step 7b. Give **every** card `id=` (a short slug like `"L09-flu-bayes"`) and `src=`. The GUID comes from the id and is saved in `<Name>_import_guids.json`, so later edits update the note in place and keep its review history.

`write()` runs `lint()` and refuses to write while any warning is open. Codes: `ref` (points at another card), `list` (asks for a set), `given` (mentions a table or plot the front doesn't show), `long` (answer over ~3 lines, or a cloze with more than 5 deletions), `big` (answer is a big table or output), `typed` (type-in answer not short and plain), `shared` (more than 3 type-in cards on one table). Fix the card. Only when a warning is a false positive, pass `allow=("code",)` on that card, and list those in the summary.

**Revising a deck the user already imported:**
- With ids and the `_guids.json` file: edit, reorder, add or delete freely. Re-import updates notes in place, replaces their tags with the file's tags, and leaves their decks alone. Notes that didn't change show up as "duplicate" in the log, which is expected. Deleting a note from the build does not delete it from Anki: tell the user which ones to delete in Browse.
- Deck built before ids existed: add ids to every card, keep the notes in the same order with the same note types, and pass `keep_guids_from=<previous import file>` to `write()` once. That copies the old GUIDs by position into `_guids.json`; after that the ids alone are enough.

## 6. Verify before handing over

Run `scripts/test_import.py` against the collection **copy** with the pip `anki` matching the app version (`--python 3.12 --with "anki==<version>"`). Pass `note_types.apkg` as the last argument when the user hasn't installed the note types yet. Confirm: every note is new (or updated, for a revision), no problems listed, no note types outside the skill's set, no empty questions, and each Image Occlusion note parses the expected number of shapes. Fix and rebuild until clean. The lint catches the mechanical problems; it can't judge meaning. So also do a stand-alone pass: print every front with HTML stripped and check that each one can be answered without any other card (see "Every card must stand alone"), that it takes real recall, and that its answer is one thing.

## 7. Install media and hand over

Copy the media into `<profile>/collection.media/` with `cp -n` (new files only, never overwrite). State this in the plan before doing it. Don't write to `collection.anki2` itself: Anki may be open.

Then make the study plan and check the settings (step 7b). Tell the user:
- the study plan, topic order and settings changes from step 7b
- the card counts by note type and deck, plus the image and occlusion cards
- anything beyond the slides, any slide errors, and how you handled them
- import steps: first `note_types.apkg` if they don't have the note types yet (then delete the sample deck), then **File > Import**, choose the `.txt` (note types and decks are set inside it), click Import
- that type-in cards compare exactly, so answer in the stated format; if the only difference is formatting, grade it Good anyway
- grading: Again for anything they couldn't produce before seeing the answer (including "oh right, I knew that"), Good otherwise. Hard is a pass, so pressing it on a forgotten card makes FSRS schedule it too far out
- if an earlier deck from you exists that the new file doesn't update in place, ask them to delete it first (Browse)
- after the import, old tags nobody uses any more stay in the sidebar until they right-click the Tags heading > Clear Unused Tags

### 7b. Study plan and settings check

```sh
uv run -q --no-project --python 3.12 --with "anki==<version>" python -I scripts/study_plan.py \
    "<profile>/collection.anki2" <empty scratch dir> --import <anki>/<Name>_import.txt
```

The exam tag and test date come from `<Name>_import_plan.json`, and the budget from `study.json` (pass `--exam-tag` / `--test` / `--budget` to override). It reads a copy of the collection plus the import file (so it counts cards not imported yet) and prints:
- **Budget**: the total new cards/day for all classes (on the parent deck's This deck tab), the per-course ceiling (the preset's New cards/day) and the cards-per-lecture target. When a test needs more than the ceiling, the plan says to raise the course deck's This deck limit too.
- **PLAN**: this test's new-cards/day limit and where it goes, as a share of the total. That's the deck's **This deck** tab for a test more than a day away, or **Today only** for a test tomorrow, with the cheat-sheet cards suspended first when a sheet is allowed.
- **By topic**: active and new cards per topic, flagging any topic far over the per-lecture target. Turn it into an order to study in when time is short, highest-yield first, using what you know about the test format.
- **SETTINGS**: every deck option that breaks the rules, with where to click: Limits start from top on, the parent's This deck limit = the total, the preset = the per-course ceiling, FSRS on, retention 85 to 95%, one learning and one relearning step under a day, reviews/day 9999, all three burying options on, Random notes gather order, Tag Only leeches, Optimize once there are 400+ reviews, FSRS Helper installed.

Pass its output on in plain words with exact click paths (Deck options > tab > field). Never change settings yourself: the user does it in Anki. If the user keeps an `anki-settings` repo (e.g. `~/anki-settings`), remind them to run `./run.sh export` there after changing settings.

## 8. Revise from review data

After the user has studied a deck for about a week, or when they ask, find the cards that aren't working:

```sh
uv run -q --no-project --python 3.12 --with "anki==<version>" python -I scripts/review_stats.py \
    "<profile>/collection.anki2" <empty scratch dir> "tag:CMDA3654::*" 3 <anki>/<Name>_import_guids.json
```

It lists cards with 3 or more Again presses or a `leech` tag, with their build-script id. For each one, work out why it fails before changing it: too much on the back (split it), a look-alike card (add a compare card and sharpen both), a concept the user doesn't understand yet (add an intuition card and point to the source slide), or a front that can be answered several ways (make it precise). Edit the card under the same id so the note updates in place, add new cards with new ids, rebuild, and run step 6 again.
