#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["markdown-it-py>=3", "pygments>=2.17", "pyyaml>=6"]
# ///
"""Collect everything the planner needs into one state.json: vault config, goals, lessons, and
per-course Anki state (via anki.py's AnkiConnect client), the review load forecast from Anki's
FSRS simulator (patch v8; without it the planner uses its built-in default curve), and the source
files each course has that aren't ingested yet (manifest.py's diff).

    uv run snapshot.py [--vault DIR] [--today YYYY-MM-DD] [--out state.json]

The only script that reads live data; plan.py and timetable.py work from its output.
"""
import argparse
import datetime
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('studious_anki', HERE / 'anki.py')
anki = importlib.util.module_from_spec(spec)
spec.loader.exec_module(anki)
spec = importlib.util.spec_from_file_location('studious_manifest', HERE / 'manifest.py')
manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manifest)

DEFAULTS = {
    'cards_per_day': 150,          # Anki cards a day for all courses together: new cards + reviews
    'max_lessons_per_day': 2,
    'lesson_buffer_days': 3,       # lessons done this many days before a deadline
    'prep_days': 2,                # a goal's prep deck in its final days (was cram_days)
    'exam_recall_target': 0.90,    # prep deck: cards whose recall on exam day would be below this
    'casual_new_per_day': 5,       # courses with no deadline goal, and casual goals
    'budget_new_per_day': 10,      # self-paced budgeted goals
    'backlog_overdue_threshold': 10,  # overdue reviews above this -> no new cards for that course
    'calendar_days': 7,
}


def card_count(card):
    """How many Anki cards a card block makes."""
    kind, fields = card['meta'].get('type'), card['fields']
    if kind in ('Cloze+', 'Image Occlusion'):
        text = fields.get('Text' if kind == 'Cloze+' else 'Occlusion', '')
        return len(set(re.findall(r'\{\{c(\d+)::', text))) or 1
    if kind == 'Basic+' and fields.get('Add Reverse', '').strip():
        return 2
    return 1


def lesson_paths(vault):
    """Every `lessons/*.md` under courses/ (`courses/[<group>/]<CODE>/lessons`), without walking into
    practice code, dot-folders or the lessons folders themselves."""
    found = []
    for dirpath, dirnames, filenames in os.walk(vault / 'courses'):
        if os.path.basename(dirpath) == 'lessons':
            found += [Path(dirpath) / f for f in filenames if f.endswith('.md')]
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d != 'practice' and not d.startswith('.')]
    return sorted(found)


def lessons(vault, config=None):
    config = anki.vault_config(vault) if config is None else config
    out = []
    for path in lesson_paths(vault):
        text = path.read_text(encoding='utf-8')
        fm, _ = anki.read_frontmatter(text)
        if not fm.get('course'):
            continue
        m = re.match(r'(\d+)', path.stem)
        blocks = anki.parse_cards(text)
        try:  # the full deck, so it matches the cards' decks in Anki; push reports a bad one
            deck = anki.lesson_deck(config, fm['course'], fm['deck']) if fm.get('deck') else None
        except anki.Refused:
            deck = fm.get('deck')
        out.append(dict(
            path=str(path.relative_to(vault)), title=fm.get('title') or path.stem, course=str(fm['course']),
            deck=deck, order=int(m.group(1)) if m else 999, goals=anki.as_list(fm.get('goals')),
            done=bool(fm.get('done')), verified=bool(fm.get('verified')),
            cards=[dict(guid=b['meta'].get('id'), count=card_count(b), hash=anki.card_hash(text, b)) for b in blocks]))
    return out


def goals(vault):
    out = []
    for path in sorted((vault / 'goals').glob('**/*.md')):  # goals/[<group>/]<COURSE>-<id>.md
        fm, _ = anki.read_frontmatter(path.read_text(encoding='utf-8'))
        if fm.get('type', 'goal') != 'goal' or not fm.get('id') or not fm.get('course'):
            continue
        date = fm.get('date')
        out.append(dict(id=str(fm['id']), course=str(fm['course']), title=fm.get('title') or fm['id'],
                        mode=fm.get('mode', 'deadline'), date=str(date) if date else None,
                        status=fm.get('status', 'active'), cram=bool(fm.get('cram')),
                        lessons_per_week=fm.get('lessons_per_week'), path=str(path.relative_to(vault))))
    return out


FORECAST_DAYS = 90


def forecast(client, roots):
    """Expected reviews per day from Anki's FSRS simulator: per_new_card (the curve one new card
    follows) and load (per course code, the cards already reviewed). `roots` maps each course code
    to its root deck. None when the open Anki's patch is older than v8 or FSRS is off: the planner
    then uses its default curve and the cards' due dates."""
    try:
        fc = client.call('studiousForecast', roots=list(roots.values()), days=FORECAST_DAYS)
    except anki.Refused as exc:
        return dict(source='unavailable', reason=str(exc))
    return dict(source='anki', preset=fc['preset'], params=fc['params'], per_new_card=fc['perNewCard'],
                decay=fc.get('decay'),  # patch v11; the planner falls back to FSRS-6's default
                load={code: fc['load'][root] for code, root in roots.items() if root in fc['load']})


def orphan_cards(client, record):
    """The cards of each note studious suspended as an orphan, as Anki has them now, wherever they sit:
    {guid: [{cardId, deck, suspended}]}, without the guids whose note was deleted. None when Anki
    couldn't say, so the planner keeps the record as it is."""
    if not record:
        return {}
    try:
        notes = client.call('studiousNotes', guids=sorted(record))
    except anki.Refused:
        return None
    return {n['guid']: [dict(cardId=c['cardId'], deck=c['deck'], suspended=c['queue'] == -1) for c in n['cards']]
            for n in notes}


def sources(vault, config):
    """Per course (not done or retired), the new and changed source files that teach or set scope
    (course-material, exam-blueprint; never my-work or artifacts), from manifest.py: {code: {new:
    {folder: {kind: n}}, changed: n, base: [source folders]}}. Folders are relative to the home folder
    and grouped one level under the course's source folder (`Fall-2026/cs4824/test2`, however deep
    the files are); files right in the source folder count under the folder itself."""
    out = {}
    for code, c in (config.get('courses') or {}).items():
        c = c or {}
        if c.get('done') or c.get('retired') or not c.get('folders'):
            continue
        known, now = manifest.load(vault, code), manifest.scan(c['folders'])
        keep = ('course-material', 'exam-blueprint')
        bases = sorted((str(b).rstrip('/') for b in c['folders']), key=len, reverse=True)

        def group(path):
            parent = str(Path(path).parent)
            for b in bases:
                if parent == b or parent.startswith(b + '/'):
                    rest = parent[len(b) + 1:]
                    return f"{b}/{rest.split('/')[0]}" if rest else b
            return parent
        new = {}
        for path, f in now.items():
            if path not in known and f['kind'] in keep:
                folder = new.setdefault(group(path), {})
                folder[f['kind']] = folder.get(f['kind'], 0) + 1
        changed = sum(1 for path, f in now.items() if path in known and f['kind'] in keep
                      and known[path]['hash'] != f['hash'] and known[path].get('status') != 'ignored')
        if new or changed:
            out[str(code)] = dict(new=new, changed=changed, base=[str(b).rstrip('/') for b in c['folders']])
    return out


def spell_courses(config, items):
    """The planner matches course codes exactly, Anki ignores case: spell them as the config does."""
    for item in items:
        item['course'] = anki.course_code(config, item['course'])


def placement(config, state):
    """Warnings for notes outside their course's folders (a course whose group changed), and one
    goal per (course, id): a goal in two notes would be planned twice and share one timetable. The
    copy in the course's goal folder wins."""
    warnings = []
    for l in state['lessons']:
        if anki.course_key(config, l['course']) is None:
            continue
        home = anki.course_dir(config, l['course']) + '/lessons/'
        if not l['path'].lower().startswith(home.lower()):
            warnings.append(f"{l['path']} is outside {l['course']}'s folder: move it to {home}")
    by_goal = {}
    for g in state['goals']:
        by_goal.setdefault((g['course'].lower(), g['id'].lower()), []).append(g)
    kept = []
    for copies in by_goal.values():
        home = anki.goal_dir(config, copies[0]['course']) + '/'
        here = [g for g in copies if g['path'].lower().startswith(home.lower()) and '/' not in g['path'][len(home):]]
        keep = (here or copies)[0]
        kept.append(keep)
        if len(copies) > 1:
            warnings.append(f"{keep['course']} {keep['id']} is in {len(copies)} goal notes "
                            f"({', '.join(g['path'] for g in copies)}): studious uses {keep['path']}; delete the others.")
        elif not here and anki.course_key(config, keep['course']) is not None:
            warnings.append(f"{keep['path']} is outside {keep['course']}'s goal folder: move it to {home}")
    state['goals'] = [g for g in state['goals'] if g in kept]
    return warnings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--vault', default=os.environ.get('STUDIOUS_VAULT', str(Path.home() / 'studious')))
    parser.add_argument('--today', help='YYYY-MM-DD (default: today)')
    parser.add_argument('--since-days', type=int, default=120)
    parser.add_argument('--out')
    args = parser.parse_args(argv)
    vault = Path(args.vault)
    mine = {k: v for k, v in anki.vault_config(vault).items() if v is not None}
    config = {**DEFAULTS, 'vault_name': vault.name, **mine}
    today = datetime.datetime.now(ZoneInfo(config.get('timezone') or 'America/New_York')).date()
    state = dict(today=args.today or today.isoformat(), config=config, config_set=sorted(mine),
                 goals=goals(vault), lessons=[], courses={}, errors=[], warnings=[],
                 retired=anki.vault_record(vault, 'retired')[1], pushed=anki.vault_record(vault, 'pushed')[1],
                 orphaned=anki.vault_record(vault, 'orphaned')[1], reading=anki.vault_record(vault, 'reading')[1])
    try:
        state['sources'] = sources(vault, config)
    except Exception as exc:  # a source folder that can't be read shouldn't stop the plan
        state['sources'] = {}
        state['warnings'].append(f"Couldn't check the course folders for new material: {exc}")
    try:
        state['lessons'] = lessons(vault, config)
        spell_courses(config, state['goals'] + state['lessons'])
        state['warnings'] += placement(config, state)
        for goal in state['goals']:
            goal['group'] = '/'.join(anki.course_group(config, goal['course']))
        roots = {code: anki.course_root(config, code) for code in
                 sorted({g['course'] for g in state['goals']} | {l['course'] for l in state['lessons']})}
    except anki.Refused as exc:  # a bad `group` in the config
        state['errors'].append(str(exc))
        roots = {}
    # every card block in the vault, for orphan detection: the lessons' guids, then the other notes
    state['vault_guids'] = sorted({c['guid'] for l in state['lessons'] for c in l['cards'] if c['guid']}
                                  | anki.vault_guids(vault, skip={vault / l['path'] for l in state['lessons']}))
    client = anki.Anki(int(config.get('anki_port') or 8765), os.environ.get('STUDIOUS_ANKI_PROFILE') or config.get('anki_profile'))
    since = int((time.time() - args.since_days * 86400) * 1000)
    try:  # the open Anki's patch: what the prep decks can use (a second filter from v11)
        state['patch'] = int(client.call('studiousVersion').get('patch') or 0)
    except anki.Refused:
        state['patch'] = None
    codes = sorted(roots)
    for code in codes:
        try:
            seen = client.call('studiousObserve', root=roots[code], sinceMs=since)
            state['courses'][code] = dict(
                root=roots[code], today=seen['today'], counts=seen['counts'], preset=seen['preset'], revlog=seen['revlog'],
                cards=[{**{k: c[k] for k in ('cardId', 'noteId', 'guid', 'deck', 'type', 'queue', 'due', 'reps',
                                             'lapses', 'flag', 'suspended', 'tags', 'retrievability')},
                        'stability': c.get('stability'),
                        'owned': anki.studious_owned(c['guid'], state['pushed'])} for c in seen['cards']])
        except anki.Refused as exc:
            if 'does not exist' in str(exc):
                state['courses'][code] = dict(root=roots[code], today=None, counts=dict(new=0, learning=0, review=0),
                                              preset=None, revlog=[], cards=[])
            else:
                state['errors'].append(f'{code}: {exc}')
    state['forecast'] = forecast(client, {c: roots[c] for c in codes if state['courses'].get(c, {}).get('today') is not None})
    state['orphaned_cards'] = orphan_cards(client, state['orphaned'])
    text = json.dumps(state, indent=1, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text, encoding='utf-8')
    else:
        print(text)
    return 1 if state['errors'] else 0


if __name__ == '__main__':
    sys.exit(main())
