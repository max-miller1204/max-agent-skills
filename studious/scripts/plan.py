#!/usr/bin/env python3
"""The studious planner: a pure function from state.json (snapshot.py) to plan.json.

    python3 plan.py state.json [--out plan.json]

No I/O besides reading the state and writing the plan; same input, same output.

Rules (Anki and FSRS guidance on workload, plus lesson pacing):
- New cards/day budget for all courses = minutes x 60 / seconds_per_review / reviews_per_new_card.
- Overdue reviews above the backlog threshold -> no new cards for that course until they're done,
  unless one of its goals is within the cram window.
- Deadline countdown: new/day = ceil(unseen in scope / (days left - 1)), so every card is seen by the
  day before; with 1 day or less, every remaining card goes in today (the "today only" override).
  Overlapping goals of one course count the union of their scopes, nearest deadline first.
- Lessons are paced to be done `lesson_buffer_days` before the deadline, at most
  `max_lessons_per_day` across all goals, nearest deadline first. A goal whose lessons or cards don't
  fit switches to cram mode: lessons may run until the day before (all of them scheduled, over the
  per-day cap if need be, with a warning), cards are pushed without waiting
  for lessons, and for the last `cram_days` a filtered deck of its weakest cards is built.
- A course with new cards but no deadline pressure gets `casual_new_per_day`.
- A course marked `done: true` in the config gets no new cards (its decks move to the done preset).
- seconds_per_review and reviews_per_new_card are measured from the last 28 days of reviews once there
  is enough history (see calibrate); values set in the config always win.
"""
import argparse
import datetime
import json
import math
import sys
from urllib.parse import quote
from zoneinfo import ZoneInfo

WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']


def day(s):
    return datetime.date.fromisoformat(str(s)[:10])


def exam_tag(goal):
    return f"{goal['course']}::exam::{goal['id']}".lower()


def in_scope(card, goal):
    tag = exam_tag(goal)
    return any(t.lower() == tag for t in card.get('tags') or [])


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def obsidian_link(vault_name, path):
    file = path[:-3] if path.endswith('.md') else path
    return f"obsidian://open?vault={quote(vault_name)}&file={quote(file, safe='')}"


CALIBRATION_DAYS = 28


def calibrate(state):
    """seconds_per_review and reviews_per_new_card from every course's review log when there is
    enough of it, else the default. A value set in the config always wins.

    - seconds per review: mean answer time over the last 28 days (each answer capped at 2 minutes),
      once there are 100 answers.
    - reviews per new card: answers per day / new cards per day over the last 28 days, once the
      review log spans 56 days and 30 cards were new in the window (a card's first answer in the
      log counts as new). Clamped to 4-20: early on it reads low, because reviews of new cards
      haven't piled up yet.
    """
    cfg, explicit = state['config'], set(state.get('config_set') or [])
    today = day(state['today'])
    tz = ZoneInfo(cfg.get('timezone') or 'America/New_York')

    def age(r):
        return (today - datetime.datetime.fromtimestamp(r['id'] / 1000, tz).date()).days

    answers = [r for c in (state.get('courses') or {}).values() for r in c.get('revlog') or []
               if r['kind'] in (0, 1, 2, 3)]
    recent = [r for r in answers if 0 <= age(r) < CALIBRATION_DAYS]
    out = {}
    timed = [min(r['ms'], 120000) for r in recent if r['ms'] > 0]
    if 'seconds_per_review' in explicit:
        out['seconds_per_review'] = dict(value=cfg['seconds_per_review'], source='config')
    elif len(timed) >= 100:
        out['seconds_per_review'] = dict(value=max(1.0, round(sum(timed) / len(timed) / 1000, 1)),
                                         source='measured', answers=len(timed))
    else:
        out['seconds_per_review'] = dict(value=cfg['seconds_per_review'], source='default', answers=len(timed))
    first = {}
    for r in answers:
        first[r['cardId']] = max(first.get(r['cardId'], -1), age(r))
    new_recent = sum(1 for a in first.values() if a < CALIBRATION_DAYS)
    history = max(first.values(), default=0)
    if 'reviews_per_new_card' in explicit:
        out['reviews_per_new_card'] = dict(value=cfg['reviews_per_new_card'], source='config')
    elif history >= 2 * CALIBRATION_DAYS and new_recent >= 30:
        out['reviews_per_new_card'] = dict(value=round(min(20.0, max(4.0, len(recent) / new_recent)), 1),
                                           source='measured', new_cards=new_recent, answers=len(recent))
    else:
        out['reviews_per_new_card'] = dict(value=cfg['reviews_per_new_card'], source='default',
                                           history_days=history, new_cards=new_recent)
    return out


def plan(state):
    today = day(state['today'])
    cfg = state['config']
    calibration = calibrate(state)
    total_cap = int(cfg['anki_minutes_per_day'] * 60 / calibration['seconds_per_review']['value']
                    / calibration['reviews_per_new_card']['value'])
    horizon = int(cfg['calendar_days'])
    max_lessons = int(cfg['max_lessons_per_day'])
    courses = state.get('courses') or {}
    lessons = sorted(state.get('lessons') or [], key=lambda l: (l['course'], l['order'], l['path']))
    warnings, inbox = [], []

    anki_guids = {code: {c['guid'] for c in course.get('cards') or []} for code, course in courses.items()}

    def unpushed(lesson):
        guids = anki_guids.get(lesson['course'], set())
        return sum(c['count'] for c in lesson['cards'] if not c['guid'] or c['guid'] not in guids)

    # ---- goals ---------------------------------------------------------------------------------
    goals_out, active = [], []
    cram_delete = []
    for g in sorted(state.get('goals') or [], key=lambda g: (g.get('date') or '9999', g['course'], g['id'])):
        if g.get('status', 'active') != 'active':
            continue
        days_left = (day(g['date']) - today).days if g.get('date') else None
        if days_left is not None and days_left < 0:
            inbox.append(f"{g['title']} was on {g['date']}: keep, archive or carry its cards?")
            cram_delete.append(f"{g['course']} {g['id']} cram")
            continue
        scope_lessons = [l for l in lessons if l['course'] == g['course'] and g['id'] in l['goals']]
        cards = courses.get(g['course'], {}).get('cards') or []
        scope_new = [c for c in cards if c['type'] == 0 and not c['suspended'] and in_scope(c, g)]
        unseen = len(scope_new) + sum(unpushed(l) for l in scope_lessons)
        remaining = [l for l in scope_lessons if not l['done']]
        entry = dict(id=g['id'], course=g['course'], title=g['title'], mode=g.get('mode', 'deadline'),
                     date=g.get('date'), days_left=days_left, unseen=unseen, scope_new=[c['cardId'] for c in scope_new],
                     lessons_left=[l['path'] for l in remaining], cram=bool(g.get('cram')), required_new_per_day=0)
        if not scope_lessons and not scope_new:
            if days_left is not None and days_left <= 21:
                inbox.append(f"{g['title']} is in {plural(days_left, 'day')} and has no material yet: ingest its notes.")
            entry['empty'] = True
        goals_out.append(entry)
        if not entry.get('empty'):
            active.append(entry)

    deadline_goals = [g for g in active if g['mode'] == 'deadline' and g['days_left'] is not None]

    # Lessons: can each deadline goal finish at the normal pace? If not, it's cram mode.
    for g in deadline_goals:
        n = len(g['lessons_left'])
        window = g['days_left'] - int(cfg['lesson_buffer_days'])
        if n and (window < 1 or math.ceil(n / window) > max_lessons):
            g['cram'] = True

    # Cards: union of scopes through each deadline, nearest first.
    per_course_required = {}
    seen_ids = {}
    for g in deadline_goals:
        ids = seen_ids.setdefault(g['course'], set())
        ids.update(g['scope_new'])
        unpushed_union = sum(unpushed(l) for l in lessons if l['course'] == g['course']
                             and any(x['id'] in l['goals'] for x in deadline_goals
                                     if x['course'] == g['course'] and x['days_left'] <= g['days_left']))
        cumulative = len(ids) + unpushed_union
        rate = cumulative if g['days_left'] <= 1 else math.ceil(cumulative / (g['days_left'] - 1))
        g['required_new_per_day'] = rate
        per_course_required[g['course']] = max(per_course_required.get(g['course'], 0), rate)
        if rate > total_cap:
            g['cram'] = True
    for g in deadline_goals:  # last day to do lessons: the buffer before the deadline, or the day before in cram
        window = g['days_left'] - int(cfg['lesson_buffer_days'])
        g['lessons_due_by'] = max(0, (g['days_left'] - 1) if g['cram'] else window - 1)

    # ---- per-course new cards/day ----------------------------------------------------------------
    course_out = {}
    urgency = {}
    for g in deadline_goals:
        urgency[g['course']] = min(urgency.get(g['course'], 10 ** 6), g['days_left'])
    codes = sorted(set(courses) | {l['course'] for l in lessons}, key=lambda c: (urgency.get(c, 10 ** 6), c))
    remaining_cap = total_cap
    for code in codes:
        course = courses.get(code) or {}
        cards = course.get('cards') or []
        if ((cfg.get('courses') or {}).get(code) or {}).get('done'):
            course_out[code] = dict(new_per_day=0, new_today=None, overdue=0, new_pool=0, reposition=None, done=True)
            continue
        today_num = course.get('today')
        new_pool = sum(1 for c in cards if c['type'] == 0 and not c['suspended'])
        overdue = sum(1 for c in cards if c['type'] == 2 and c['queue'] == 2 and today_num is not None
                      and c['due'] < today_num)
        required = per_course_required.get(code, 0)
        floor = int(cfg['casual_new_per_day']) if new_pool else 0
        if any(g['course'] == code and g['mode'] == 'self-paced-budget' for g in active):
            floor = max(floor, int(cfg['budget_new_per_day']))
        want = max(required, floor)
        urgent = urgency.get(code, 10 ** 6) <= int(cfg['cram_days'])
        new_today = None
        if overdue > int(cfg['backlog_overdue_threshold']) and not urgent:
            want = 0
            warnings.append(f"{code}: {plural(overdue, 'overdue review')}; no new cards until they're done.")
        if urgent:
            granted = want  # heavier load accepted right before a deadline
            last_day = [g for g in deadline_goals if g['course'] == code and g['days_left'] <= 1]
            if last_day:
                new_today = sum(g['required_new_per_day'] for g in last_day[:1])
        else:
            granted = min(want, max(remaining_cap, 0))
            if granted < required:
                warnings.append(f"{code}: needs {required} new cards/day to finish in time, but the daily "
                                f"budget leaves {granted}. Raise anki_minutes_per_day or cut scope.")
        remaining_cap -= granted
        course_out[code] = dict(new_per_day=granted, new_today=new_today, overdue=overdue, new_pool=new_pool,
                                reposition=None)

    # ---- reposition: nearest goal's scope first, lessons interleaved -------------------------------
    order_of = {l['deck']: (l['order'], l['path']) for l in lessons if l.get('deck')}
    for code, out in course_out.items():
        cards = [c for c in (courses.get(code) or {}).get('cards') or [] if c['type'] == 0 and not c['suspended']]
        if not cards:
            continue
        placed, order = set(), []
        groups = [g for g in deadline_goals if g['course'] == code] + [None]
        for g in groups:
            batch = [c for c in cards if c['cardId'] not in placed and (g is None or in_scope(c, g))]
            by_deck = {}
            for c in sorted(batch, key=lambda c: (c['due'], c['cardId'])):
                by_deck.setdefault(c['deck'], []).append(c)
            decks = sorted(by_deck, key=lambda d: (order_of.get(d, (999, d)), d))
            while any(by_deck[d] for d in decks):
                for d in decks:
                    if by_deck[d]:
                        c = by_deck[d].pop(0)
                        order.append(c['cardId'])
                        placed.add(c['cardId'])
        current = [c['cardId'] for c in sorted(cards, key=lambda c: (c['due'], c['cardId']))]
        if order != current:
            out['reposition'] = order

    # ---- lessons per day ------------------------------------------------------------------------
    queue = {g['id'] + '@' + g['course']: list(g['lessons_left']) for g in active}
    by_path = {l['path']: l for l in lessons}
    scheduled, days_lessons = set(), []
    budget_goals = [g for g in active if g['mode'] == 'self-paced-budget']
    for k in range(horizon):
        capacity, todays = max_lessons, []
        for g in deadline_goals:
            pending = [p for p in queue[g['id'] + '@' + g['course']] if p not in scheduled]
            if not pending or (capacity <= 0 and not g['cram']) or k > g['days_left'] - 1:
                continue
            days_to_go = max(1, g['lessons_due_by'] - k + 1)
            take = math.ceil(len(pending) / days_to_go)
            if not g['cram']:
                take = min(capacity, take)
            elif take > capacity and k == 0:  # cram: every lesson gets a day, even over the cap
                warnings.append(f"{g['title']}: {plural(len(pending), 'lesson')} left in "
                                f"{plural(days_to_go, 'day')}, more than max_lessons_per_day; all are scheduled.")
            for p in pending[:take]:
                todays.append(p)
                scheduled.add(p)
            capacity -= take
        for g in budget_goals:
            per_week = max(1, round(float(g.get('weekly_hours') or 3) * 60 / 45))
            if capacity > 0 and math.floor((k + 1) * per_week / 7) > math.floor(k * per_week / 7):
                pending = [p for p in queue[g['id'] + '@' + g['course']] if p not in scheduled]
                if pending:
                    todays.append(pending[0])
                    scheduled.add(pending[0])
                    capacity -= 1
        days_lessons.append(todays)

    # ---- push: verified lessons whose new cards may enter Anki ------------------------------------
    cram_goals = [g for g in deadline_goals if g['cram']]
    push = []
    for l in lessons:
        if not l['verified'] or not unpushed(l):
            continue
        if l['done']:
            push.append(dict(path=l['path'], ungated=False))
        elif any(l['course'] == g['course'] and g['id'] in l['goals'] for g in cram_goals):
            push.append(dict(path=l['path'], ungated=True))
    for l in lessons:
        if not l['verified'] and l['cards']:
            inbox.append(f"{l['title']} isn't verified yet: run the verification pass.")

    cram = []
    for g in cram_goals:
        if g['days_left'] <= int(cfg['cram_days']):
            cram.append(dict(name=f"{g['course']} {g['id']} cram", limit=9999,
                             search=f'deck:"{g["course"]}" tag:{g["course"]}::exam::{g["id"]} prop:r<0.85'))

    # ---- the next days: card counts and calendar text ---------------------------------------------
    pool = {code: out['new_pool'] for code, out in course_out.items()}
    pushed_now = {p['path'] for p in push}
    for p in pushed_now:  # this run pushes these, so their cards are in today's pool
        pool[by_path[p]['course']] = pool.get(by_path[p]['course'], 0) + unpushed(by_path[p])
    days = []
    carry = 0  # estimate: each new card comes back for one review the next day
    for k in range(horizon):
        date = today + datetime.timedelta(days=k)
        new, due = 0, carry
        for code, out in course_out.items():
            course = courses.get(code) or {}
            rate = out['new_today'] if (k == 0 and out['new_today'] is not None) else out['new_per_day']
            used = min(rate, pool[code])
            pool[code] -= used
            new += used
            today_num = course.get('today')
            if k == 0:
                due += (course.get('counts') or {}).get('review', 0) + (course.get('counts') or {}).get('learning', 0)
            elif today_num is not None:
                due += sum(1 for c in course.get('cards') or [] if c['type'] == 2 and not c['suspended']
                           and c['queue'] == 2 and c['due'] == today_num + k)
        for p in days_lessons[k]:  # a lesson done today releases its cards from tomorrow
            if p not in pushed_now:
                l = by_path[p]
                pool[l['course']] = pool.get(l['course'], 0) + unpushed(l)
        carry = new
        todays = [by_path[p] for p in days_lessons[k]]
        cards_total = new + due
        title = f"[studious] Daily quota: {plural(cards_total, 'card')} · {plural(len(todays), 'lesson')}"
        lines = ['Today', f"• Anki: {plural(cards_total, 'card')}"]
        lines += [f"• Lesson: {l['title']}" for l in todays] or ['• Lesson: none']
        for g in cram_goals:
            left = g['days_left'] - k
            if left >= 0:
                lines.append(f"• Cram: {g['course']} {g['id']} ({'today' if left == 0 else plural(left, 'day') + ' left'})")
        if k == 0 and inbox:
            lines.append(f"• Inbox: {plural(len(inbox), 'decision')}")
        if todays:
            lines.append('')
            lines += [f"{l['title']}: {obsidian_link(cfg['vault_name'], l['path'])}" for l in todays]
        days.append(dict(date=date.isoformat(), weekday=WEEKDAYS[date.weekday()], new=new, due=due, cards=cards_total,
                         lessons=[dict(title=l['title'], path=l['path']) for l in todays],
                         title=title, description='\n'.join(lines)))

    for g in goals_out:
        g.pop('scope_new', None)
    for code in sorted({g['course'] for g in state.get('goals') or []}):
        mine = [g for g in state.get('goals') or [] if g['course'] == code and g.get('date')]
        finals = [g for g in mine if 'final' in f"{g['id']} {g.get('title', '')}".lower()]
        done = ((cfg.get('courses') or {}).get(code) or {}).get('done')
        if finals and not done and all(day(g['date']) < today for g in mine):
            inbox.append(f"{code}'s final is over: mark it `done: true` under courses in the config, so its "
                         f"cards move to the done preset (lower retention, fewer reviews)?")
    return dict(today=today.isoformat(), total_new_per_day=total_cap, calibration=calibration,
                goals=goals_out, courses=course_out,
                push=push, cram=cram, cram_delete=sorted(set(cram_delete)), days=days,
                inbox=inbox, warnings=warnings)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('state')
    parser.add_argument('--out')
    args = parser.parse_args(argv)
    with open(args.state, encoding='utf-8') as f:
        result = plan(json.load(f))
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as f:
            f.write(text + '\n')
    else:
        print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
