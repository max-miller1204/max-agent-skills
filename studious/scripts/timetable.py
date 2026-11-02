#!/usr/bin/env python3
"""Retrospective timetables (Ali Abdaal style) from state.json, one note per active goal.

    python3 timetable.py state.json [--vault DIR]

Writes <vault>/timetable/<course>-<goal>.md. Rows are the goal's topics (lesson subdecks).
- "Now" is the mean FSRS retrievability of the topic's reviewed cards today, with seen/total counts.
  Unseen cards are counted, never averaged in.
- Each date column is a day you reviewed that topic, colored by how that session went: the share
  of reviews you didn't press Again on. (Anki keeps only today's memory state, so past days show
  session results rather than a reconstructed retrievability.)
Colors: green >= rag_green (0.90), amber >= rag_amber (0.75), red below, grey unseen.
"""
import argparse
import datetime
import html
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

COLORS = {'green': 'rgba(46,160,67,.35)', 'amber': 'rgba(210,153,34,.40)', 'red': 'rgba(218,54,51,.40)',
          'grey': 'rgba(128,128,128,.18)'}
MAX_DATES = 12


def rag(value, cfg):
    if value is None:
        return 'grey'
    if value >= float(cfg.get('rag_green', 0.90)):
        return 'green'
    return 'amber' if value >= float(cfg.get('rag_amber', 0.75)) else 'red'


def cell(text, color, title=''):
    tip = f' title="{html.escape(title)}"' if title else ''
    return f'<td style="background:{COLORS[color]};text-align:center"{tip}>{html.escape(text)}</td>'


def review_day(ms, tz):
    # Anki's day rolls over at 4am; a 1am review counts for the day before.
    return (datetime.datetime.fromtimestamp(ms / 1000, tz) - datetime.timedelta(hours=4)).date()


def render(state, goal):
    cfg = state['config']
    tz = ZoneInfo(cfg.get('timezone') or 'America/New_York')
    today = datetime.date.fromisoformat(state['today'])
    course = state['courses'].get(goal['course']) or {}
    tag = f"{goal['course']}::exam::{goal['id']}".lower()
    cards = [c for c in course.get('cards') or [] if any(t.lower() == tag for t in c.get('tags') or [])]
    lessons = {l['deck']: l for l in state['lessons'] if l['course'] == goal['course'] and l.get('deck')
               and goal['id'] in l['goals']}
    decks = sorted({c['deck'] for c in cards} | set(lessons),
                   key=lambda d: (lessons[d]['order'] if d in lessons else 999, d))
    by_id = {c['cardId']: c for c in cards}

    sessions = {}  # (deck, date) -> [reviews, passed]
    for r in course.get('revlog') or []:
        c = by_id.get(r['cardId'])
        if c is None or r['kind'] == 4 or r['ease'] == 0:  # skip manual reschedules
            continue
        key = (c['deck'], review_day(r['id'], tz))
        s = sessions.setdefault(key, [0, 0])
        s[0] += 1
        s[1] += r['ease'] > 1
    dates = sorted({d for _, d in sessions})[-MAX_DATES:]

    days_left = (datetime.date.fromisoformat(goal['date']) - today).days if goal.get('date') else None
    left = 'today' if days_left == 0 else f"{days_left} day{'' if days_left == 1 else 's'} left"
    when = f" · {goal['date']} ({left})" if days_left is not None else ''
    vault = cfg.get('vault_name', 'studious')
    rows = []
    for deck in decks:
        mine = [c for c in cards if c['deck'] == deck]
        reviewed = [c['retrievability'] for c in mine if c.get('retrievability') is not None]
        now = sum(reviewed) / len(reviewed) if reviewed else None
        name = deck.split('::')[-1]
        lesson = lessons.get(deck)
        if lesson:
            link = f"obsidian://open?vault={quote(vault)}&file={quote(lesson['path'][:-3], safe='')}"
            name_html = f'<a href="{link}">{html.escape(lesson["title"])}</a>'
            status = '✓' if lesson['done'] else ''
        else:
            name_html, status = html.escape(name), ''
        seen = sum(1 for c in mine if c['type'] != 0)
        now_text = f'{now:.0%} · {seen}/{len(mine)}' if now is not None else f'unseen · {seen}/{len(mine)}'
        tds = [f'<td>{name_html}</td>', f'<td style="text-align:center">{status}</td>',
               cell(now_text, rag(now, cfg), 'FSRS recall estimate today, reviewed cards only')]
        for d in dates:
            s = sessions.get((deck, d))
            if s:
                tds.append(cell(f'{s[1] / s[0]:.0%}', rag(s[1] / s[0], cfg), f'{s[0]} reviews'))
            else:
                tds.append('<td></td>')
        rows.append('<tr>' + ''.join(tds) + '</tr>')
    head = ''.join(f'<th>{d.strftime("%b")} {d.day}</th>' for d in dates)
    table = ('<table>\n<tr><th>Topic</th><th>Lesson</th><th>Now</th>' + head + '</tr>\n'
             + '\n'.join(rows) + '\n</table>') if rows else '*No cards or lessons in scope yet.*'
    return (f"---\ntype: timetable\ngoal: {goal['id']}\ncourse: {goal['course']}\nupdated: {state['today']}\n---\n"
            f"# {goal['title']}{when}\n\n"
            "> [!info]- How to read this\n"
            "> **Now**: FSRS recall estimate today for the topic's reviewed cards, then seen/total cards. "
            "Dates: days you reviewed the topic, colored by that session (share of reviews not pressed Again). "
            "Green ≥ 90%, amber ≥ 75%, red below, grey unseen. Regenerated by studious every run; don't edit.\n\n"
            f"{table}\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('state')
    parser.add_argument('--vault', default=os.environ.get('STUDIOUS_VAULT', str(Path.home() / 'studious')))
    args = parser.parse_args(argv)
    with open(args.state, encoding='utf-8') as f:
        state = json.load(f)
    out_dir = Path(args.vault) / 'timetable'
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for goal in state['goals']:
        if goal.get('status', 'active') != 'active':
            continue
        path = out_dir / f"{goal['course']}-{goal['id']}.md"
        path.write_text(render(state, goal), encoding='utf-8')
        written.append(str(path))
    print(json.dumps(written, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
