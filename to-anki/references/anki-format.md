# Anki text-import format

Verified with Anki 26.9.3 by importing into a collection copy through the pip `anki` package.

## File header

```
#separator:tab
#html:true
#notetype column:1
#deck column:2
#tags column:3
#guid column:4
```

Columns 5+ are the note's fields in order. Rows for note types with fewer fields get padded with empty columns up to 5; Anki ignores the extras. `deck.py` writes this.

- **Note type and deck names must match exactly**, including `Basic+ (type in)`. The importer can't create note types: install them first with the `.apkg` from `notetypes.py`. Decks that don't exist yet are created on import. `::` nests decks.
- **GUID column**: a stable hash of the note's `id=` (or of note type + first field when there is no id), saved in `<Name>_import_guids.json`. Re-importing a fixed file updates notes instead of duplicating them.
- **What re-import changes** (verified with 26.9.3): fields and tags of a note with a matching GUID are replaced by the file's. Its cards stay in their current deck: the deck column only applies to new notes. A note whose fields, tags and note type are unchanged is logged as a duplicate.
- **Tags**: space-separated, no spaces inside a tag, `::` nests. Matching is case-insensitive and an existing tag's casing wins (an existing `cmda3654` turns an imported `CMDA3654::exam::Quiz2` into `cmda3654::exam::Quiz2`).
- **No raw newlines in fields.** `deck.py` converts `\n` to `<br>`, which also breaks lines inside `<pre>`.
- Python's `csv` writer (tab, `QUOTE_MINIMAL`) quotes fields that contain `"`; Anki reads that correctly.

## Note type fields

| Note type | Fields |
|---|---|
| Basic+ | Front, Back, Extra, Source, Add Reverse |
| Basic+ (type in) | Front, Back, Extra, Source |
| Cloze+ | Text, Back Extra, Source |
| Image Occlusion (stock) | Occlusion, Image, Header, Back Extra, Comments |

**Changing a note's type through import doesn't work**: a GUID match with a different note type is a conflict. Convert existing notes first with Browse > Notes > Change Note Type (verified with 26.9.3: cards, decks, scheduling and review history are kept, reverse cards map Card 1 → Card 1 and Card 2 → Card 2, and it needs one one-way sync).

Check the user's actual fields in step 2; renamed fields shift the mapping.

## Rendering pitfalls

- Card text is centered. The note types' CSS left-aligns `<pre>` and tables, and `Deck.pre()` also inlines `text-align:left; display:inline-block` so code stays aligned on stock Image Occlusion cards.
- **Type-in** compares the typed text to Back exactly. Keep Back plain text, state the format in the question, and put any explanation in Extra (it shows under the comparison).
- Tables: `tables.py` inlines its styles with translucent colors, so they read in light and night mode and on stock IO cards too.
- Explanations go in Extra (Basic+, Basic+ (type in)) or Back Extra (Cloze+, IO). Sources go in Source; IO has no Source field, so `deck.py` appends it to Back Extra.
- Cloze text can't contain `}}` or `::` inside a deletion. `Deck.check()` (run by `write()`) catches a deletion that closes early.

## Math (MathJax)

Anki renders MathJax on desktop, AnkiMobile and AnkiDroid: `\( ... \)` inline, `\[ ... \]` display. Don't use `$...$`.

- Clozes work inside a formula, and hidden parts show as `[...]` within the math. Verified: `\(P(A \mid B) = \dfrac{ {{c1::P(A \land B)}} }{ {{c2::P(B)}} }\)`.
- **Brace collisions:** LaTeX that ends in `}` right before the cloze's `}}` (e.g. `\mu_{c^{(i)}}`) closes the cloze early. Add a space: `\mu_{c^{(i)} }`, and `{{c1::\frac{N}{M_d} }}`. Use `{ {{c1::...}} }` when a cloze is a whole `\frac` argument.
- Fields are HTML: write `\gt` / `\lt` instead of `>` / `<`.
- Build strings as Python raw strings (`r"..."`). Inside a raw string `\"` keeps its backslash, so use a different outer quote (`r'...'` or `r"""..."""`) when the text has `"`.
- Type-in Back must stay plain (`1/8`, `(15, 5)`); put the LaTeX in Front only.
- The printed cheat sheet or slides may use Unicode; that's fine there, but cards should use LaTeX.

## Image Occlusion field

One shape per cloze, joined with `<br>`:

```
{{c1::image-occlusion:rect:left=0.5000:top=0.3250:width=0.2400:height=0.0360:oi=1}}
```

- Coordinates are **fractions (0-1) of the image** set in the Image field (`<img src="prefix_name.png">`).
- `oi=1` = hide all, guess one. Shapes sharing `cN` reveal together on card N.
- When parsed, the card HTML has one `data-shape="rect"` div per shape; `test_import.py` counts them.

## Media

- Files go in `<profile>/collection.media/` under the exact name in `src`.
- Use a course/test prefix on every file and copy with `cp -n`.
- Anki shows them immediately; Tools > Check Media also registers them.
