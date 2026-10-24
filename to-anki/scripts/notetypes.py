"""The skill's three note types, and a one-time .apkg that installs them.

Each is a clone of a stock type with Extra and Source fields and shared CSS (left-aligned code and
tables, a muted source line, night-mode colors). No JavaScript, so they render the same on desktop,
AnkiMobile and AnkiDroid. Image Occlusion stays stock.

  Basic+            Front, Back, Extra, Source, Add Reverse   (card 2 only when Add Reverse is filled)
  Basic+ (type in)  Front, Back, Extra, Source                (Extra shows under the typed comparison)
  Cloze+            Text, Back Extra, Source

Build the installer (run with the pip `anki` package that matches the installed app):
  uv run -q --no-project --python 3.12 --with "anki==<version>" python -I notetypes.py <empty work dir> <out.apkg>

The user imports it once with File > Import and then deletes the "Note types (delete me)" deck.
Changing fields later forces a one-way sync, so only change CSS and templates after that.
"""
import os
import sys

NAMES = ("Basic+", "Basic+ (type in)", "Cloze+")

CSS = """.card {
  font-family: arial;
  font-size: 20px;
  line-height: 1.5;
  text-align: center;
  color: black;
  background-color: white;
}
pre, table { text-align: left; }
pre {
  display: inline-block;
  max-width: 100%;
  overflow-x: auto;
  font-size: 15px;
  padding: 6px 10px;
  margin: 4px 0;
  border-left: 3px solid #3b6ea5;
}
.extra { margin-top: 1em; font-size: 0.9em; opacity: 0.85; }
.source { margin-top: 1em; font-size: 12px; opacity: 0.6; }
.cloze { font-weight: bold; color: blue; }
.nightMode .cloze { color: lightblue; }
.nightMode.card { color: white; background-color: #2f2f31; }
.mobile pre { font-size: 13px; }
"""

EXTRA = "{{#Extra}}<div class=extra>{{Extra}}</div>{{/Extra}}"
SOURCE = "{{#Source}}<div class=source>Source: {{Source}}</div>{{/Source}}"

SPEC = {
    "Basic+": {
        "base": "Basic (optional reversed card)",
        "fields": ["Front", "Back", "Extra", "Source", "Add Reverse"],
        "templates": [
            ("Card 1", "{{Front}}", "{{FrontSide}}\n\n<hr id=answer>\n\n{{Back}}\n" + EXTRA + "\n" + SOURCE),
            ("Card 2", "{{#Add Reverse}}{{Back}}{{/Add Reverse}}",
             "{{FrontSide}}\n\n<hr id=answer>\n\n{{Front}}\n" + EXTRA + "\n" + SOURCE),
        ],
        "sample": ["Basic+ sample front", "Sample back", "Sample extra", "Sample source", ""],
    },
    "Basic+ (type in)": {
        "base": "Basic (type in the answer)",
        "fields": ["Front", "Back", "Extra", "Source"],
        "templates": [
            ("Card 1", "{{Front}}\n\n{{type:Back}}",
             "{{Front}}\n\n<hr id=answer>\n\n{{type:Back}}\n" + EXTRA + "\n" + SOURCE),
        ],
        "sample": ["Basic+ (type in) sample front", "answer", "Sample extra", "Sample source"],
    },
    "Cloze+": {
        "base": "Cloze",
        "fields": ["Text", "Back Extra", "Source"],
        "templates": [
            ("Cloze", "{{cloze:Text}}",
             "{{cloze:Text}}\n{{#Back Extra}}<div class=extra>{{Back Extra}}</div>{{/Back Extra}}\n" + SOURCE),
        ],
        "sample": ["Cloze+ {{c1::sample}}", "Sample extra", "Sample source"],
    },
}


def add_notetypes(col):
    """Create the note types in `col` from clones of the stock ones. Returns {name: notetype id}."""
    mm = col.models
    ids = {}
    for name, spec in SPEC.items():
        existing = mm.by_name(name)
        if existing:
            ids[name] = existing["id"]
            continue
        nt = mm.copy(mm.by_name(spec["base"]), add=False)
        nt["name"] = name
        have = [f["name"] for f in nt["flds"]]
        for f in spec["fields"]:
            if f not in have:
                mm.add_field(nt, mm.new_field(f))
        for i, f in enumerate(spec["fields"]):
            mm.reposition_field(nt, next(x for x in nt["flds"] if x["name"] == f), i)
        assert [f["name"] for f in nt["flds"]] == spec["fields"], (name, [f["name"] for f in nt["flds"]])
        assert len(nt["tmpls"]) == len(spec["templates"]), name
        for t, (tname, qfmt, afmt) in zip(nt["tmpls"], spec["templates"]):
            t["name"], t["qfmt"], t["afmt"] = tname, qfmt, afmt
        nt["css"] = CSS
        mm.add_dict(nt)
        ids[name] = mm.by_name(name)["id"]
    return ids


def build_apkg(work, out):
    from anki.collection import Collection, DeckIdLimit, ExportAnkiPackageOptions
    col = Collection(os.path.join(work, "notetypes.anki2"))
    ids = add_notetypes(col)
    did = col.decks.id("Note types (delete me)")
    for name, spec in SPEC.items():
        note = col.new_note(col.models.get(ids[name]))
        for f, v in zip(spec["fields"], spec["sample"]):
            note[f] = v
        col.add_note(note, did)
    col.export_anki_package(out_path=out, limit=DeckIdLimit(did), options=ExportAnkiPackageOptions(
        with_scheduling=False, with_deck_configs=False, with_media=False, legacy=False))
    col.close()


if __name__ == "__main__":
    build_apkg(sys.argv[1], sys.argv[2])
