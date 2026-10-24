r"""Build an Anki text-import file for the skill's note types (notetypes.py) plus stock Image Occlusion.

Import this from a per-course build script:

    import sys; sys.path.insert(0, "<skill>/scripts")
    from deck import Deck

    d = Deck(prefix="cmda3654q2_", course="CMDA3654", exam="Quiz2", test="2026-10-30",
             decks={"L09": "CMDA3654", "LAB6": "CMDA3654"},
             topics={"L09": "L09_Probability", "LAB6": "Lab06_Reshaping"})
    d.cloze("L09", r"\(P(A \mid B) = \dfrac{ {{c1::P(A \land B)}} }{ {{c2::P(B)}} }\)", "Conditional = Joint / Marginal",
            id="L09-conditional", src="L09 slide 12")
    d.typed("L09", r"Find \(P(F \mid H)\) (fraction).", "1/8", id="L09-flu-fh", src="L09 slide 17")
    d.basic("L09", d.img("L09-17") + r"<br>Why is \(P(F \mid H)\) small?", "...", id="L09-flu-why")
    d.occlusion("L12", "L12-11", boxes, "K-means algorithm", "back extra", id="L12-kmeans-io")
    d.code_card("LAB6", "pd.concat([a, b]).shape", setup="import pandas as pd; a = ...", id="LAB6-concat-shape")
    d.table_card("LAB6", 'orders[orders["rating"] >= 4]', setup=SETUP, inputs=["orders"],
                 question="Which order_ids remain?", answer=lambda r: ", ".join(map(str, r["order_id"])),
                 kind="row-reducing", id="LAB6-filter-rating")
    d.which_code("LAB6", 'orders.groupby("location", as_index=False)["quantity"].sum()', setup=SETUP,
                 inputs=["orders"], kind="row-reducing", id="LAB6-groupby-sum-rev")
    d.tag(d.cheatsheet_tag, "Chain rule:")      # only when a cheat sheet is allowed; see SKILL.md
    d.write("out/Course_import.txt")            # add keep_guids_from=<old file> once, to migrate an old deck
    d.summary()

Every card method also takes these keywords:
  id=     a stable slug. The GUID comes from it, so editing the card's text keeps its review history.
          write() records id -> GUID in <out>_guids.json next to the import file and reuses it on every build.
  src=    where the fact comes from ("L12 slide 11", "Lab 6 cell 14"). It goes in the Source field.
  why=    (basic, rev, typed, code cards) an explanation for the Extra field, shown under the answer.
  allow=  lint codes to skip for this note, e.g. allow=("long",), when a warning is a false positive.

Tags form one tree per course, so the Browse sidebar stays tidy and a search on a parent tag also
finds its children:
  CMDA3654::exam::Quiz2            which test the note was made for (add ::Final etc. later by search)
  CMDA3654::topic::L09_Probability the lecture or lab, from topics= (defaults to the deck key)
  CMDA3654::cheatsheet::Quiz2      d.cheatsheet_tag, for recall-only notes printed on that test's sheet
Note types are not tagged: search note:Cloze instead.

test= is the test date (YYYY-MM-DD). write() saves it with the exam tag in <out>_plan.json, which
study_plan.py reads, so later sessions don't have to ask for the date again.

Image names passed to img()/occlusion() are media basenames without the prefix or ".png".
Write math as MathJax in raw strings (r"..."): \( inline \), \[ display \]. Use \gt / \lt, not > / <.
Table cards need pandas (and tables.py next to this file).
"""
import ast
import contextlib
import csv
import datetime
import hashlib
import html
import io
import json
import os
import re

BASIC = "Basic+"            # Front, Back, Extra, Source, Add Reverse
TYPE = "Basic+ (type in)"   # Front, Back, Extra, Source
CLOZE = "Cloze+"            # Text, Back Extra, Source
OCCL = "Image Occlusion"    # stock: Occlusion, Image, Header, Back Extra, Comments
NFIELDS = 5

PRE_STYLE = ("text-align:left; display:inline-block; font-size:15px; padding:6px 10px; "
             "border-left:3px solid #3b6ea5; margin:4px 0")
SRC_STYLE = "font-size:12px; opacity:.6; margin-top:8px"
ROWS_STYLE = "font-size:13px; opacity:.8"
ROW_KINDS = ("row-preserving", "row-reducing", "row-adding")

# ---------- lint patterns (see lint()) ----------
REF = re.compile(
    r"\b(?:see above|as before|(?:card|example|table|question)s? above"
    r"|the (?:earlier|previous|last|next|other) (?:card|example|question|table)"
    r"|same (?:answer|example|setup|numbers|points|data|table|question) as"
    r"|as (?:in|with) the \w+ (?:example|card))\b|'[^']{3,60}' card\b", re.I)
LIST = re.compile(
    r"\b(?:two|three|four|five|six|seven|eight|nine|ten|\d+)\s+(?:[\w-]+\s+)?(?:reasons|steps|types|kinds|ways"
    r"|applications|examples|advantages|disadvantages|benefits|checks|properties|parts|stages|methods|problems"
    r"|challenges|causes|uses|features|rules|factors|components|assumptions|limitations)\b"
    r"|\b(?:list|enumerate|name) (?:the|all)\b", re.I)
GIVEN = re.compile(r"\b(?:the|this|these) (?:joint )?(?:table|data|dataset|plot|figure|graph|image|diagram|curve"
                   r"|black line|points|output)\b", re.I)
BLOCK = re.compile(r"<(pre|table)\b.*?</\1>", re.I | re.S)


def _text(h):
    return html.unescape(re.sub(r"<[^>]+>", " ", h))


class Deck:
    def __init__(self, prefix, decks, course, exam=None, topics=None, tags="", test=None):
        """course: tag root, e.g. "CMDA3654". exam: the test these cards are for, e.g. "Quiz2".
        test: the test date, "YYYY-MM-DD" (saved for study_plan.py).
        topics: deck key -> topic tag name, e.g. {"L09": "L09_Probability"}. tags: extra tags for every note."""
        for name in [course, exam or ""] + list((topics or {}).values()):
            assert not re.search(r"\s|::", name), f"tag part can't contain spaces or '::': {name!r}"
        self.prefix = prefix
        self.decks = decks
        self.course = course
        self.exam = exam
        self.topics = topics or {}
        self.tags = tags
        self.cheatsheet_tag = f"{course}::cheatsheet::{exam or 'all'}"
        if test:
            datetime.date.fromisoformat(test)  # fail early on a typo
        self.test = test
        self.rows = []
        self.meta = []  # per row: what lint() needs (front, recalled part, id, allowed codes)
        self.counts = {}

    # ---------- formatting helpers ----------
    def img(self, name):
        return f'<img src="{self.prefix}{name}.png">'

    @staticmethod
    def pre(code):
        # Stock note types center text, so pin code blocks left or columns of output misalign.
        return f'<pre style="{PRE_STYLE}">{html.escape(code.strip(chr(10)))}</pre>'

    @staticmethod
    def _src(src):
        return f'<div style="{SRC_STYLE}">Source: {html.escape(src)}</div>' if src else ""

    # ---------- note types ----------
    def _add(self, key, notetype, *fields, id=None, allow=(), recall=None, both=False):
        # The import format is one line per note: raw newlines become <br> (they render inside <pre> too).
        fields = [f.replace("\n", "<br>") for f in fields]
        seed = f"id|{id}" if id else f"{notetype}|{fields[0]}"
        guid = self.prefix + hashlib.sha1(seed.encode()).hexdigest()[:12]
        tags = [f"{self.course}::topic::{self.topics.get(key, key)}"] + self.tags.split()
        if self.exam:
            tags.insert(0, f"{self.course}::exam::{self.exam}")
        self.rows.append([notetype, self.decks[key], " ".join(tags), guid] + fields + [""] * (NFIELDS - len(fields)))
        self.meta.append({"id": id, "allow": set(allow), "front": fields[0], "both": both,
                          "recall": (recall if recall is not None else fields[1]).replace("\n", "<br>")})
        self.counts[(key, notetype)] = self.counts.get((key, notetype), 0) + 1

    def basic(self, key, front, back, why=None, src=None, **meta):
        self._add(key, BASIC, front, back, why or "", src or "", "", **meta)

    def rev(self, key, term, definition, why=None, src=None, **meta):
        # Basic+ with Add Reverse filled. Both sides are recalled, so lint checks the term too.
        self._add(key, BASIC, term, definition, why or "", src or "", "y", both=True, **meta)

    def typed(self, key, front, answer, why=None, src=None, **meta):
        # Anki compares the typed text to Back exactly, so keep Back plain and state the expected
        # format in the question. Explanations go in Extra, which shows under the comparison.
        self._add(key, TYPE, front, answer, why or "", src or "", **meta)

    def cloze(self, key, text, extra="", src=None, **meta):
        self._add(key, CLOZE, text, extra, src or "", recall=text, **meta)

    def occlusion(self, key, image, boxes, header, back_extra="", src=None, **meta):
        """boxes: [(cloze_number, left, top, width, height), ...] as fractions of the full image (0-1).
        Boxes sharing a cloze number are hidden/revealed together on one card."""
        shapes = [f"{{{{c{n}::image-occlusion:rect:left={l:.4f}:top={t:.4f}:width={w:.4f}:height={h:.4f}:oi=1}}}}"
                  for n, l, t, w, h in boxes]
        # Image Occlusion is stock and has no Source field, so the source goes at the end of Back Extra.
        self._add(key, OCCL, "<br>".join(shapes), self.img(image), header, back_extra + self._src(src), "",
                  recall="", **meta)

    # ---------- executed code-output cards ----------
    @staticmethod
    def _execute(setup, code, inputs=()):
        """Run setup, copy the named input tables (before the code can change them), then run code.
        Returns (input copies, value of the last expression or None, printed text)."""
        ns = {}
        exec(setup, ns)
        before = {n: ns[n].copy() for n in inputs}
        # Split off the last statement (it may span several lines) so its value shows like a notebook cell.
        tree = ast.parse(code.strip("\n"))
        last = tree.body.pop() if tree.body and isinstance(tree.body[-1], ast.Expr) else None
        buf = io.StringIO()
        val = None
        with contextlib.redirect_stdout(buf):
            exec(compile(tree, "<card>", "exec"), ns)
            if last is not None:
                val = eval(compile(ast.Expression(last.value), "<card>", "eval"), ns)
                if val is not None:
                    print(val)
        return before, val, buf.getvalue().rstrip()

    @classmethod
    def run(cls, setup, code):
        """Run setup + code and return what a notebook cell would show (prints + last expression)."""
        return cls._execute(setup, code)[2]

    @staticmethod
    def _show(before):
        from tables import table
        return "".join(table(df, caption=name) for name, df in before.items())

    @staticmethod
    def _rows_line(before, val, kind):
        """Row counts are facts; the kind is a claim about the operation, so the caller states it and
        it is checked against the counts. A groupby that happens to return as many rows as it got
        is still row-reducing."""
        if kind is None:
            return ""
        if kind not in ROW_KINDS:
            raise ValueError(f"kind must be one of {ROW_KINDS}, not {kind!r}")
        if len(before) != 1:
            raise ValueError("kind= needs exactly one input table to compare row counts against")
        n_in, n_out = len(next(iter(before.values()))), len(val)
        if (kind == "row-preserving" and n_out != n_in) or (kind == "row-reducing" and n_out > n_in) or \
                (kind == "row-adding" and n_out < n_in):
            raise ValueError(f"{kind} but rows went {n_in} -> {n_out}")
        note = ""
        if kind != "row-preserving" and n_out == n_in:
            note = ", but here every group has one row" if kind == "row-reducing" else ", but here nothing was added"
        return f'<div style="{ROWS_STYLE}">Rows: {n_in} → {n_out} ({kind}{note})</div>'

    def _result_html(self, before, val, base=None, kind=None):
        import pandas as pd
        from tables import LEGEND, scalar, table
        if not isinstance(val, (pd.DataFrame, pd.Series)):
            return scalar(val, "result")
        out = table(val, caption="result", base=before[base] if base else None)
        out += self._rows_line(before, val, kind)
        if "rgba(59,130,246" in out or "rgba(245,158,11" in out or "rgba(239,68,68" in out:
            out += LEGEND
        return out

    def _code_front(self, code, shown_setup, prompt, before=None):
        return (f"{prompt}<br>" + (self.pre(shown_setup) + "<br>" if shown_setup else "")
                + (self._show(before) + "<br>" if before else "") + self.pre(code))

    def code_card(self, key, code, setup="", shown_setup=None, why=None, prompt="What is the output?",
                  inputs=None, base=None, kind=None, **meta):
        """Basic card whose answer is the real output. `setup` runs hidden; `shown_setup` is displayed.
        With inputs=[names] the front shows those tables and the back shows the result as a table
        (base=<name> highlights new columns and changed cells). For a result bigger than a few cells,
        use table_card() and ask one question about it instead."""
        if inputs is None:
            out = self.run(setup, code)
            self.basic(key, self._code_front(code, shown_setup, prompt), self.pre(out), why, **meta)
            return
        before, val, _ = self._execute(setup, code, inputs)
        self.basic(key, self._code_front(code, shown_setup, prompt, before), self._result_html(before, val, base, kind),
                   why, **meta)

    def code_typed(self, key, code, setup="", shown_setup=None, prompt="What is the output?", inputs=(), why=None,
                   **meta):
        """Type-in card for short one-line outputs such as shapes, lengths or scalars."""
        before, _, out = self._execute(setup, code, inputs)
        if "\n" in out:
            raise ValueError(f"multi-line output does not suit a type-in card: {code!r}")
        self.typed(key, self._code_front(code, shown_setup, prompt, before), out, why, **meta)

    def table_card(self, key, code, setup, inputs, question, answer, base=None, kind=None, why=None, src=None,
                   **meta):
        """One targeted question about a DataFrame result ("Which order_ids remain?"). The front shows the
        input tables, the code and the question; the back shows the short answer, and Extra holds the full
        result table as context. `answer` is a function of the result, so it comes from running the code."""
        before, val, _ = self._execute(setup, code, inputs)
        ans = html.escape(str(answer(val) if callable(answer) else answer))
        front = f"{question}<br>" + self._show(before) + "<br>" + self.pre(code)
        extra = self._result_html(before, val, base, kind) + (f"<br>{why}" if why else "")
        self._add(key, BASIC, front, ans, extra, src or "", "", **meta)

    def which_code(self, key, code, setup, inputs, base=None, kind=None, hint="", why=None, src=None, **meta):
        """Reverse card: see the before and after tables, write the pandas that does it."""
        before, val, _ = self._execute(setup, code, inputs)
        front = (f"Write the pandas code that turns the input into the result.{(' ' + hint) if hint else ''}<br>"
                 + self._show(before) + '<span style="font-size:22px; vertical-align:middle">⟶</span>'
                 + self._result_html(before, val, base, kind))
        self.basic(key, front, self.pre(code), why, src, **meta)

    def tag(self, tag, *front_starts):
        """Add `tag` to the notes whose first field starts with each given text. Each text must match
        exactly one note, so a typo or an ambiguous prefix fails loudly instead of tagging the wrong card."""
        for start in front_starts:
            hits = [r for r in self.rows if r[4].startswith(start.replace("\n", "<br>"))]
            assert len(hits) == 1, f"{len(hits)} notes start with {start!r}"
            if tag not in hits[0][2].split():
                hits[0][2] += f" {tag}"

    def tag_ids(self, tag, *ids):
        """Add `tag` to the notes with these id= values. An unknown id fails loudly."""
        where = {m["id"]: r for r, m in zip(self.rows, self.meta) if m["id"]}
        for i in ids:
            assert i in where, f"no note with id {i!r}"
            if tag not in where[i][2].split():
                where[i][2] += f" {tag}"

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
        ids = [m["id"] for m in self.meta if m["id"]]
        dup_ids = {i for i in ids if ids.count(i) > 1}
        assert not dup_ids, f"duplicate id= values: {sorted(dup_ids)}"
        guids = [r[3] for r in self.rows]
        dups = [r[4][:60] for r in self.rows if guids.count(r[3]) > 1]
        assert not dups, f"notes share a GUID (same note type and first field; give them id=): {dups}"

    def lint(self):
        """Card-quality warnings from SKILL.md, as (code, row number, message, front). Codes:
        ref    points at another card or example instead of restating it
        list   asks for a set or enumeration on a non-cloze card
        given  refers to a table/plot/data that the front does not show
        long   the recalled part is more than ~3 lines or ~45 words, or a cloze has more than 5 deletions
        big    the recalled part is a table or code block bigger than a few cells or lines
        typed  a type-in answer with HTML or more than 40 characters
        shared more than 3 type-in cards ask about the same table (they get memorized as numbers)"""
        out, shared = [], {}
        for i, (r, m) in enumerate(zip(self.rows, self.meta)):
            nt, front, recall = r[0], m["front"], m["recall"]
            found = []
            sides = [front] if nt == OCCL else [front] + [f for f in r[5:] if f]
            if any(REF.search(_text(s)) for s in sides):
                found.append(("ref", "points at another card or example; restate it"))
            if nt in (BASIC, TYPE) and LIST.search(_text(front)):
                found.append(("list", "asks for a set; use one cloze per item, or ask about one item"))
            if nt != OCCL and GIVEN.search(_text(front)) and not re.search(r"<(?:table|img|pre)\b", front, re.I):
                found.append(("given", "mentions a table/plot/data the front doesn't show"))
            if nt == CLOZE:
                n = len(set(re.findall(r"\{\{c(\d+)::", front)))
                if n > 5:
                    found.append(("long", f"{n} cloze deletions in one note; split it"))
            elif nt != OCCL:
                parts = [recall] + ([front] if m["both"] else [])
                for p in parts:
                    plain = BLOCK.sub(" ", p)
                    lines = [s for s in re.split(r"<br\s*/?>|</div>|</p>|</tr>", plain, flags=re.I) if _text(s).strip()]
                    if len(lines) > 3 or len(_text(plain).split()) > 45:
                        found.append(("long", "answer has more than ~3 lines or ~45 words; split it"))
                    for b in BLOCK.finditer(p):
                        cells = len(re.findall(r"<td\b", b.group(0), re.I))
                        pre_lines = b.group(0).count("<br>") + 1 if b.group(1).lower() == "pre" else 0
                        if cells > 20 or pre_lines > 8:
                            found.append(("big", "answer is a big table or output; ask one question about it "
                                                 "(table_card) and show the full result as context"))
                            break
            if nt == TYPE:
                if "<" in recall or len(recall) > 40:
                    found.append(("typed", "type-in answer should be short plain text"))
                for t in re.findall(r"<table\b.*?</table>", front, re.I | re.S):
                    shared.setdefault(t, []).append(i)
            for code, msg in found:
                if code not in m["allow"]:
                    out.append((code, i, msg, _text(front)[:90].strip()))
        for rows in shared.values():
            if len(rows) > 3 and not all("shared" in self.meta[i]["allow"] for i in rows):
                out.append(("shared", rows[3], f"{len(rows)} type-in cards ask about the same table (rows {rows}); "
                            "keep 3 and ask for the method in the others", _text(self.rows[rows[3]][4])[:90].strip()))
        return out

    def write(self, path, keep_guids_from=None, strict=True):
        """Writes the import file, <path stem>_guids.json (id -> GUID) and <path stem>_plan.json (exam tag and
        test date, for study_plan.py). The GUID map is reused on every build, so a note with id= keeps its
        GUID through edits, reordering and deletions elsewhere.

        keep_guids_from: an import file written before ids existed, with the same notes in the same order.
        Its GUIDs are copied by position (notes added since go at the end), then saved in the GUID map, so
        this is needed only once. Re-import then updates those notes in place and keeps review history.

        strict: raise if lint() reports anything. Fix the card, or pass allow=(code,) on a false positive."""
        gmap_path = os.path.splitext(path)[0] + "_guids.json"
        gmap = {}
        if os.path.exists(gmap_path):
            with open(gmap_path, encoding="utf-8") as f:
                gmap = json.load(f)
        for r, m in zip(self.rows, self.meta):
            if m["id"] in gmap:
                r[3] = gmap[m["id"]]
        if keep_guids_from:
            with open(keep_guids_from, encoding="utf-8") as f:
                old = list(csv.reader((line for line in f if not line.startswith("#")), delimiter="\t"))
            assert len(old) <= len(self.rows), f"{len(old)} old notes vs {len(self.rows)} new: don't remove notes"
            for new, prev in zip(self.rows, old):
                assert new[0] == prev[0], f"note type changed: {prev[0]} -> {new[0]}"
                new[3] = prev[3]
        self.check()
        problems = self.lint()
        for code, i, msg, front in problems:
            print(f"lint {code:6} row {i}: {msg}\n    {front}")
        if problems and strict:
            raise SystemExit(f"{len(problems)} lint problems: fix them or allow=(code,) on false positives")
        with open(path, "w", newline="", encoding="utf-8") as f:
            f.write("#separator:tab\n#html:true\n#notetype column:1\n#deck column:2\n#tags column:3\n#guid column:4\n")
            csv.writer(f, delimiter="\t", quoting=csv.QUOTE_MINIMAL, lineterminator="\n").writerows(self.rows)
        gmap.update({m["id"]: r[3] for r, m in zip(self.rows, self.meta) if m["id"]})
        with open(gmap_path, "w", encoding="utf-8") as f:
            json.dump(gmap, f, indent=1, sort_keys=True)
        plan = {"exam_tag": f"{self.course}::exam::{self.exam}" if self.exam else None, "test": self.test}
        with open(os.path.splitext(path)[0] + "_plan.json", "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=1)

    def summary(self):
        for (k, nt), n in sorted(self.counts.items()):
            print(f"{k:6} {nt:28} {n}")
        print("total notes:", len(self.rows))
