#!/usr/bin/env python3
"""Retrospective timetables (Ali Abdaal style) from state.json, one note per active goal.

    python3 timetable.py state.json [--vault DIR]

Writes <vault>/timetable/<course>-<goal> timetable.md. Rows are the goal's topics (lesson subdecks).
- "Now" is the mean FSRS retrievability of the topic's reviewed cards today, with seen/total counts.
  Unseen cards are counted, never averaged in.
- Each date column is a day you reviewed that topic, colored by how that session went: the share
  of reviews you didn't press Again on. (Anki keeps only today's memory state, so past days show
  session results rather than a reconstructed retrievability.)
  A day with fewer than MIN_REVIEWS reviews of the topic stays grey and shows the count: one or two
  reviews say too little to color.
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
MIN_REVIEWS = 3  # fewer reviews of a topic on a day: a grey cell with the count, not a percentage


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


def filename(goal):
    """Not the goal note's own name, so the two are separate nodes in Obsidian's graph."""
    return f"{goal['course']}-{goal['id']} timetable.md"


def render(state, goal, planned=None):
    """The timetable note for one goal. `planned` is the goal's entry in the plan (plan.py), whose
    verdict (cram mode, what's left) goes into the properties for the dashboard's Goals view."""
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
    if days_left is not None and days_left < 0:
        left = f"{-days_left} day{'' if days_left == -1 else 's'} ago"
    else:
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
            if s and s[0] < MIN_REVIEWS:
                text = f"{s[0]} review{'' if s[0] == 1 else 's'}"
                tds.append(cell(text, 'grey', f'{text}: too few to color'))
            elif s:
                tds.append(cell(f'{s[1] / s[0]:.0%}', rag(s[1] / s[0], cfg), f'{s[0]} reviews'))
            else:
                tds.append('<td></td>')
        rows.append('<tr>' + ''.join(tds) + '</tr>')
    head = ''.join(f'<th>{d.strftime("%b")} {d.day}</th>' for d in dates)
    table = ('<table>\n<tr><th>Topic</th><th>Lesson</th><th>Now</th>' + head + '</tr>\n'
             + '\n'.join(rows) + '\n</table>') if rows else '*No cards or lessons in scope yet.*'
    # Links in properties count as links in Obsidian, so lessons show this timetable in their backlinks.
    def link(path, title):
        return json.dumps(f"[[{path[:-3]}|{title}]]", ensure_ascii=False)
    in_scope = sorted(lessons.values(), key=lambda l: l['order'])
    links = ''.join(f"\n  - {link(l['path'], l['title'])}" for l in in_scope)
    goal_link = f"\ngoal_note: {link(goal['path'], goal['title'])}" if goal.get('path') else ''
    verdict = f"\ndate: {goal.get('date') or ''}"  # the planner leaves past goals out, so only the date
    if planned:
        left = planned.get('lessons_left') or []
        verdict += (f"\ncram: {'true' if planned.get('cram') else 'false'}"
                   f"\nlessons_left: {len(left)}\ncards_left: {planned.get('unseen') or 0}"
                   f"\nnew_cards_today: {planned.get('required_new_per_day') or 0}")
    group = f"\ngroup: {json.dumps(goal['group'])}" if goal.get('group') else ''
    return (f"---\ntype: timetable\ngoal: {goal['id']}\ncourse: {goal['course']}{group}{goal_link}{verdict}\n"
            f"lessons:{links or ' []'}\nupdated: {state['today']}\n---\n"
            f"# {goal['title']}{when}\n\n"
            "> [!info]- How to read this\n"
            "> **Now**: FSRS recall estimate today for the topic's reviewed cards, then seen/total cards. "
            "Dates: days you reviewed the topic, colored by that session (share of reviews not pressed Again). "
            "Green ≥ 90%, amber ≥ 75%, red below, grey unseen or fewer than 3 reviews that day. Regenerated by studious every run; don't edit.\n\n"
            f"{table}\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('state')
    parser.add_argument('--plan', help="plan.py's output, for the cram verdict and what's left")
    parser.add_argument('--vault', default=os.environ.get('STUDIOUS_VAULT', str(Path.home() / 'studious')))
    args = parser.parse_args(argv)
    with open(args.state, encoding='utf-8') as f:
        state = json.load(f)
    status = {}
    if args.plan:
        with open(args.plan, encoding='utf-8') as f:
            status = {(g['course'], g['id']): g for g in json.load(f)['goals']}
    out_dir = Path(args.vault) / 'timetable'
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for goal in state['goals']:
        path = out_dir / filename(goal)
        if goal.get('status', 'active') != 'active':
            path.unlink(missing_ok=True)  # archived and done goals leave the Goals view
            continue
        path.write_text(render(state, goal, status.get((goal['course'], goal['id']))), encoding='utf-8')
        written.append(str(path))
    print(json.dumps(written, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
