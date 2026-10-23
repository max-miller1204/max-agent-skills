r"""Build an Anki text-import file that uses Anki's stock note types.

Import this from a per-course build script:

    import sys; sys.path.insert(0, "<skill>/scripts")
    from deck import Deck

    d = Deck(prefix="cmda3654q2_", tags="cmda3654 quiz2", decks={
        "L09": "CMDA3654::Quiz 2::L09 Probability",
        "LAB6": "CMDA3654::Quiz 2::Lab 6 pandas",
    })
    d.cloze("L09", r"\(P(A \mid B) = \dfrac{ {{c1::P(A \land B)}} }{ {{c2::P(B)}} }\)", "Conditional = Joint / Marginal")
    d.typed("L09", r"Find \(P(F \mid H)\) (fraction).", "1/8")
    d.basic("L09", d.img("L09-17") + r"<br>Why is \(P(F \mid H)\) small?", "...")
    d.occlusion("L12", "L12-11", boxes, "K-means algorithm", "back extra")
    d.code_card("LAB6", "pd.concat([a, b]).shape", setup="import pandas as pd; a = ...")
    d.tag("on-cheatsheet", "Chain rule:")       # only when a cheat sheet is allowed; see SKILL.md
    d.write("out/Course_import.txt")            # add keep_guids_from=<old file> when revising
    d.summary()

Image names passed to img()/occlusion() are media basenames without the prefix or ".png".
Write math as MathJax in raw strings (r"..."): \( inline \), \[ display \]. Use \gt / \lt, not > / <.
"""
import contextlib
import csv
import hashlib
import html
import io
import re

BASIC = "Basic"
REV = "Basic (and reversed card)"
TYPE = "Basic (type in the answer)"
CLOZE = "Cloze"
OCCL = "Image Occlusion"
NFIELDS = 5  # widest stock note type (Image Occlusion: Occlusion, Image, Header, Back Extra, Comments)

PRE_STYLE = ("text-align:left; display:inline-block; font-size:15px; padding:6px 10px; "
             "border-left:3px solid #3b6ea5; margin:4px 0")


class Deck:
    def __init__(self, prefix, decks, tags=""):
        self.prefix = prefix
        self.decks = decks
        self.tags = tags
        self.rows = []
        self.counts = {}

    # ---------- formatting helpers ----------
    def img(self, name):
        return f'<img src="{self.prefix}{name}.png">'

    @staticmethod
    def pre(code):
        # Stock note types center text, so pin code blocks left or columns of output misalign.
        return f'<pre style="{PRE_STYLE}">{html.escape(code.strip(chr(10)))}</pre>'

    # ---------- note types ----------
    def _add(self, key, notetype, *fields):
        # The import format is one line per note: raw newlines become <br> (they render inside <pre> too).
        fields = [f.replace("\n", "<br>") for f in fields]
        guid = self.prefix + hashlib.sha1(f"{notetype}|{fields[0]}".encode()).hexdigest()[:12]
        tags = f"{self.tags} {self.prefix.rstrip('_')}::{key.lower()} {notetype.split()[0].lower()}".strip()
        self.rows.append([notetype, self.decks[key], tags, guid] + fields + [""] * (NFIELDS - len(fields)))
        self.counts[(key, notetype)] = self.counts.get((key, notetype), 0) + 1

    def basic(self, key, front, back, why=None):
        if why:
            back += f"<br><br><i>{why}</i>"
        self._add(key, BASIC, front, back)

    def rev(self, key, term, definition):
        self._add(key, REV, term, definition)

    def typed(self, key, front, answer):
        # Anki compares the typed text to Back exactly and shows only that comparison, so keep Back plain
        # and state the expected format in the question.
        self._add(key, TYPE, front, answer)

    def cloze(self, key, text, extra=""):
        self._add(key, CLOZE, text, extra)

    def occlusion(self, key, image, boxes, header, back_extra=""):
        """boxes: [(cloze_number, left, top, width, height), ...] as fractions of the full image (0-1).
        Boxes sharing a cloze number are hidden/revealed together on one card."""
        shapes = [f"{{{{c{n}::image-occlusion:rect:left={l:.4f}:top={t:.4f}:width={w:.4f}:height={h:.4f}:oi=1}}}}"
                  for n, l, t, w, h in boxes]
        self._add(key, OCCL, "<br>".join(shapes), self.img(image), header, back_extra, "")

    # ---------- executed code-output cards ----------
    @staticmethod
    def run(setup, code):
        """Run setup + code and return what a notebook cell would show (prints + last expression)."""
        ns = {}
        exec(setup, ns)
        *body, last = code.strip("\n").split("\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exec("\n".join(body), ns)
            val = eval(last, ns)
            if val is not None:
                print(val)
        return buf.getvalue().rstrip()

    def _code_front(self, code, shown_setup, prompt):
        return f"{prompt}<br>" + (self.pre(shown_setup) + "<br>" if shown_setup else "") + self.pre(code)

    def code_card(self, key, code, setup="", shown_setup=None, why=None, prompt="What is the output?"):
        """Basic card whose answer is the real output. `setup` runs hidden; `shown_setup` is displayed."""
        out = self.run(setup, code)
        self.basic(key, self._code_front(code, shown_setup, prompt), self.pre(out), why)

    def code_typed(self, key, code, setup="", shown_setup=None, prompt="What is the output?"):
        """Type-in card for short one-line outputs such as shapes, lengths or scalars."""
        out = self.run(setup, code)
        if "\n" in out:
            raise ValueError(f"multi-line output does not suit a type-in card: {code!r}")
        self.typed(key, self._code_front(code, shown_setup, prompt), out)

    def tag(self, tag, *front_starts):
        """Add `tag` to the notes whose first field starts with each given text. Each text must match
        exactly one note, so a typo or an ambiguous prefix fails loudly instead of tagging the wrong card."""
        for start in front_starts:
            hits = [r for r in self.rows if r[4].startswith(start.replace("\n", "<br>"))]
            assert len(hits) == 1, f"{len(hits)} notes start with {start!r}"
            if tag not in hits[0][2].split():
                hits[0][2] += f" {tag}"

    # ---------- output ----------
    def check(self):
        """Catch LaTeX that breaks clozes: a "}}" inside a deletion ends it early (write "\\frac{a}{b} }}")."""
        for r in self.rows:
            for field in r[4:]:
                for content in re.findall(r"\{\{c\d+::(.*?)\}\}", field):
                    assert content.count("{") == content.count("}"), f"cloze closed early: {content!r}"
                assert field.count(r"\(") == field.count(r"\)"), f"unbalanced \\( \\): {field[:80]!r}"
                assert field.count(r"\[") == field.count(r"\]"), f"unbalanced \\[ \\]: {field[:80]!r}"
                # r"...\"..." keeps the backslash: the card shows \" and MathJax reads it as an accent.
                assert '\\"' not in field, f"literal \\\" (raw-string quote): {field[:80]!r}"

    def write(self, path, keep_guids_from=None):
        """keep_guids_from: a previously imported file with the same notes in the same order. Reusing its
        GUIDs makes re-import update those notes in place (review history kept) instead of duplicating.
        Notes added since then go at the end and keep their own new GUIDs."""
        self.check()
        if keep_guids_from:
            with open(keep_guids_from, encoding="utf-8") as f:
                old = list(csv.reader((line for line in f if not line.startswith("#")), delimiter="\t"))
            assert len(old) <= len(self.rows), f"{len(old)} old notes vs {len(self.rows)} new: don't remove notes"
            for new, prev in zip(self.rows, old):
                assert new[0] == prev[0], f"note type changed: {prev[0]} -> {new[0]}"
                new[3] = prev[3]
        with open(path, "w", newline="", encoding="utf-8") as f:
            f.write("#separator:tab\n#html:true\n#notetype column:1\n#deck column:2\n#tags column:3\n#guid column:4\n")
            csv.writer(f, delimiter="\t", quoting=csv.QUOTE_MINIMAL, lineterminator="\n").writerows(self.rows)

    def summary(self):
        for (k, nt), n in sorted(self.counts.items()):
            print(f"{k:6} {nt:28} {n}")
        print("total notes:", len(self.rows))
