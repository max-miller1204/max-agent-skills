"""Import a text file into a COPY of an Anki collection and report what Anki would create.

Run with the pip `anki` package that matches the installed app:
  uv run -q --no-project --python 3.12 --with "anki==<version>" python -I test_import.py \
      "<profile>/collection.anki2" <import.txt> <empty work dir> "<deck prefix, e.g. CMDA3654::Quiz 2>"

Never point this at the real collection path as the work dir: it copies the collection first.
"""
import re
import shutil
import sys
from collections import Counter

from anki.collection import Collection, ImportCsvRequest

src, txt, work, deck_prefix = sys.argv[1:5]
dst = f"{work}/test.anki2"
for ext in ("", "-wal"):
    try:
        shutil.copy(src + ext, dst + ext)
    except FileNotFoundError:
        pass

col = Collection(dst)
before = col.note_count()
meta = col.get_csv_metadata(path=txt, delimiter=None)
log = col.import_csv(ImportCsvRequest(path=txt, metadata=meta)).log
problems = {k: len(getattr(log, k)) for k in
            ("duplicate", "conflicting", "first_field_match", "missing_notetype", "missing_deck", "empty_first_field")}
print("new:", len(log.new), "| updated:", len(log.updated), "| problems:", {k: v for k, v in problems.items() if v} or "none")
print("notes added:", col.note_count() - before)

search = f'deck:"{deck_prefix}*"'
nids = col.find_notes(search)
cids = col.find_cards(search)
print("by note type:", dict(Counter(col.get_note(n).note_type()["name"] for n in nids)))
print("cards:", len(cids))
for d, n in sorted(Counter(col.decks.name(col.get_card(c).did) for c in cids).items()):
    print(f"  {d}: {n}")

custom = {col.get_note(n).note_type()["name"] for n in nids} - {
    "Basic", "Basic (and reversed card)", "Basic (optional reversed card)", "Basic (type in the answer)",
    "Cloze", "Image Occlusion"}
print("non-stock note types:", custom or "none")
print("cards with empty question:", sum(1 for c in cids if not col.get_card(c).question().strip()))

for nid in col.find_notes(search + ' note:"Image Occlusion"'):
    n = col.get_note(nid)
    q = n.cards()[0].question()
    shapes = len(re.findall(r'data-shape="rect"', q))
    print(f"IO '{n['Header']}': {len(n.cards())} cards, {shapes} shapes parsed")
col.close()
