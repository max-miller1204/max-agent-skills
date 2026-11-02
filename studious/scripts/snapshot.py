#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["markdown-it-py>=3", "pygments>=2.17", "pyyaml>=6"]
# ///
"""Collect everything the planner needs into one state.json: vault config, goals, lessons, and
per-course Anki state (via anki.py's AnkiConnect client).

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

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('studious_anki', HERE / 'anki.py')
anki = importlib.util.module_from_spec(spec)
spec.loader.exec_module(anki)

DEFAULTS = {
    'anki_minutes_per_day': 45,    # review time budget for all courses together
    'seconds_per_review': 10,
    'reviews_per_new_card': 10,    # each new card/day turns into ~10 reviews/day
    'max_lessons_per_day': 2,
    'lesson_buffer_days': 3,       # lessons done this many days before a deadline
    'cram_days': 2,                # cram deck for the final days of a goal in cram mode
    'casual_new_per_day': 5,       # courses with no deadline goal, and casual goals
    'budget_new_per_day': 10,      # self-paced budgeted goals
    'backlog_overdue_threshold': 0,  # overdue reviews above this -> no new cards for that course
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


def lessons(vault):
    out = []
    for path in sorted((vault / 'courses').glob('*/lessons/*.md')):
        text = path.read_text(encoding='utf-8')
        fm, _ = anki.read_frontmatter(text)
        if not fm.get('course'):
            continue
        m = re.match(r'(\d+)', path.stem)
        blocks = anki.parse_cards(text)
        out.append(dict(
            path=str(path.relative_to(vault)), title=fm.get('title') or path.stem, course=str(fm['course']),
            deck=fm.get('deck'), order=int(m.group(1)) if m else 999, goals=[str(g) for g in fm.get('goals') or []],
            done=bool(fm.get('done')), verified=bool(fm.get('verified')), minutes=fm.get('minutes'),
            cards=[dict(guid=b['meta'].get('id'), count=card_count(b)) for b in blocks]))
    return out


def goals(vault):
    out = []
    for path in sorted((vault / 'goals').glob('*.md')):
        fm, _ = anki.read_frontmatter(path.read_text(encoding='utf-8'))
        if fm.get('type', 'goal') != 'goal' or not fm.get('id') or not fm.get('course'):
            continue
        date = fm.get('date')
        out.append(dict(id=str(fm['id']), course=str(fm['course']), title=fm.get('title') or fm['id'],
                        mode=fm.get('mode', 'deadline'), date=str(date) if date else None,
                        status=fm.get('status', 'active'), cram=bool(fm.get('cram')),
                        path=str(path.relative_to(vault))))
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--vault', default=os.environ.get('STUDIOUS_VAULT', str(Path.home() / 'studious')))
    parser.add_argument('--today', help='YYYY-MM-DD (default: today)')
    parser.add_argument('--since-days', type=int, default=120)
    parser.add_argument('--out')
    args = parser.parse_args(argv)
    vault = Path(args.vault)
    config = {**DEFAULTS, 'vault_name': vault.name,
              **{k: v for k, v in anki.vault_config(vault).items() if v is not None}}
    state = dict(today=args.today or datetime.date.today().isoformat(), config=config,
                 goals=goals(vault), lessons=lessons(vault), courses={}, errors=[])
    client = anki.Anki(int(config.get('anki_port') or 8765), os.environ.get('STUDIOUS_ANKI_PROFILE') or config.get('anki_profile'))
    since = int((time.time() - args.since_days * 86400) * 1000)
    codes = sorted({g['course'] for g in state['goals']} | {l['course'] for l in state['lessons']})
    for code in codes:
        try:
            seen = client.call('studiousObserve', root=code, sinceMs=since)
            state['courses'][code] = dict(
                root=code, today=seen['today'], counts=seen['counts'], preset=seen['preset'], revlog=seen['revlog'],
                cards=[{k: c[k] for k in ('cardId', 'noteId', 'guid', 'deck', 'type', 'queue', 'due', 'reps',
                                          'lapses', 'flag', 'suspended', 'tags', 'retrievability')} for c in seen['cards']])
        except anki.Refused as exc:
            if 'does not exist' in str(exc):
                state['courses'][code] = dict(root=code, today=None, counts=dict(new=0, learning=0, review=0),
                                              preset=None, revlog=[], cards=[])
            else:
                state['errors'].append(f'{code}: {exc}')
    text = json.dumps(state, indent=1, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text, encoding='utf-8')
    else:
        print(text)
    return 1 if state['errors'] else 0


if __name__ == '__main__':
    sys.exit(main())
