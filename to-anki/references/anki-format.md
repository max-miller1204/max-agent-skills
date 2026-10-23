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

- **Note type and deck names must match exactly**, including `Basic (type in the answer)`. Decks that don't exist yet are created on import. `::` nests decks.
- **GUID column**: a stable hash per note, so re-importing a fixed file updates notes instead of duplicating them.
- **No raw newlines in fields.** `deck.py` converts `\n` to `<br>`, which also breaks lines inside `<pre>`.
- Python's `csv` writer (tab, `QUOTE_MINIMAL`) quotes fields that contain `"`; Anki reads that correctly.

## Stock note type fields

| Note type | Fields |
|---|---|
| Basic | Front, Back |
| Basic (and reversed card) | Front, Back |
| Basic (optional reversed card) | Front, Back, Add Reverse |
| Basic (type in the answer) | Front, Back |
| Cloze | Text, Back Extra |
| Image Occlusion | Occlusion, Image, Header, Back Extra, Comments |

Check the user's actual fields in step 2; renamed fields shift the mapping.

## Rendering pitfalls with stock templates

- The stock CSS centers everything. A plain `<pre>` then centers each line and breaks DataFrame alignment. Use `Deck.pre()`, which sets `text-align:left; display:inline-block`.
- **Type-in** compares the typed text to Back exactly, and its back template shows only that comparison. Keep Back plain text with no explanation, and state the format in the question.
- Tables: inline `border="1" cellpadding="4"` styling, since there's no custom CSS.
- Explanations on Basic cards go after `<br><br><i>...</i>`; on Cloze and IO they go in Back Extra.
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
