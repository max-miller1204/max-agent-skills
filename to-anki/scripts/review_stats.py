"""List the cards the user keeps failing, so they can be rewritten or split. Reads a COPY of the collection.

Run with the pip `anki` package that matches the installed app:
  uv run -q --no-project --python 3.12 --with "anki==<version>" python -I review_stats.py \
      "<profile>/collection.anki2" <empty work dir> "<search, e.g. tag:CMDA3654::*>" [min_again=3] [guids.json]

A card is listed when it has at least min_again Again presses or carries the leech tag. With the deck's
<out>_guids.json, each row shows the build script id= of its note, so the card is easy to find and edit.
"""
import html
import json
import re
import shutil
import sys

from anki.collection import Collection

src, work, search = sys.argv[1:4]
min_again = int(sys.argv[4]) if len(sys.argv) > 4 else 3
ids = {}
if len(sys.argv) > 5:
    with open(sys.argv[5], encoding="utf-8") as f:
        ids = {guid: i for i, guid in json.load(f).items()}

dst = f"{work}/review.anki2"
for ext in ("", "-wal"):
    try:
        shutil.copy(src + ext, dst + ext)
    except FileNotFoundError:
        pass
col = Collection(dst)


def plain(h):
    h = re.sub(r"<style>.*?</style>", " ", h, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h))).strip()


rows = []
for cid in col.find_cards(search):
    card = col.get_card(cid)
    eases = col.db.list("select ease from revlog where cid = ? and type < 4", cid)
    again = sum(e == 1 for e in eases)
    note = card.note()
    if again >= min_again or note.has_tag("leech"):
        d = card.memory_state.difficulty if card.memory_state else None
        rows.append((again, len(eases), card.lapses, d, ids.get(note.guid, note.guid), plain(card.question())[:120]))

rows.sort(key=lambda r: (-r[0], -(r[3] or 0)))
print(f"{len(rows)} cards with >= {min_again} Again or a leech tag (search: {search})")
print("again reviews lapses difficulty id/guid | front")
for again, n, lapses, d, ident, front in rows:
    print(f"{again:5} {n:7} {lapses:6} {d if d is None else round(d, 1)!s:>10} {ident} | {front}")
col.close()
