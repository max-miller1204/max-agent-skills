#!/usr/bin/env python3
"""The studious planner: a pure function from state.json (snapshot.py) to plan.json.

    python3 plan.py state.json [--out plan.json]

No I/O besides reading the state and writing the plan; same input, same output.

Rules (Anki and FSRS guidance on workload, plus lesson pacing):
- New cards/day budget for all courses = minutes x 60 / seconds_per_review / reviews_per_new_card.
  A course with a deadline within `cram_days` takes what it needs over the budget; the others share
  what's left, and a short one is warned with the minutes that would fit it (or, when the urgent
  courses take the whole budget, the date it frees up).
- Overdue reviews above the backlog threshold -> no new cards for that course until they're done,
  unless one of its goals is within the cram window.
- Deadline countdown: new/day = ceil(unseen in scope / (days left - 1)), so every card is seen by the
  day before; on the day before, every remaining card goes in today (the "today only" override),
  and on the exam day none do (the course keeps its other goals' pace or the casual one).
  Overlapping goals of one course count the union of their scopes, nearest deadline first.
- Lessons are paced to be done `lesson_buffer_days` before the deadline, at most
  `max_lessons_per_day` across all goals, nearest deadline first. A goal whose lessons or cards don't
  fit switches to cram mode: lessons may run until the day before (all of them scheduled, over the
  per-day cap if need be, with a warning), cards are pushed without waiting
  for lessons, and for the last `cram_days` a filtered deck of its weakest cards is built. A goal
  that leaves cram mode loses its cram deck.
- Only verified lessons are scheduled to read; unverified ones wait in the inbox.
- A lesson whose cards in Anki are out of date is pushed again: a card block edited since its last
  push (state `pushed`), or exam tags that differ from its `goals` (a goal carried or dropped).
  Unless it's done or in cram, only the cards already in Anki are updated.
- A course with new cards but no deadline pressure gets `casual_new_per_day`.
- A course marked `done: true` in the config gets no new cards (its decks move to the done preset).
- A course marked `retired: true` drops out of planning (no lessons, pushes, goals or inbox items) and
  its cards are suspended; the cards studious suspended are recorded (state `retired`), so taking the
  flag off unsuspends exactly those and leaves cards suspended by hand alone.
- The calendar's later days are a forecast: each day re-runs the allocation with the deadlines that
  much nearer and the cards shown so far counted as seen, assuming that day's reviews get done.
- seconds_per_review and reviews_per_new_card are measured from the last 28 days of reviews once there
  is enough history (see calibrate); values set in the config always win.
"""
import argparse
import datetime
import json
import math
import sys
from zoneinfo import ZoneInfo

WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']


def day(s):
    return datetime.date.fromisoformat(str(s)[:10])


def exam_tag(goal):
    return f"{goal['course']}::exam::{goal['id']}".lower()


def cram_name(goal):
    """Inside the course deck, so studying the course includes the cram cards."""
    return f"{goal['course']}::{goal['id']} cram"


def in_scope(card, goal):
    tag = exam_tag(goal)
    return any(t.lower() == tag for t in card.get('tags') or [])


def countdown(unseen, days_left):
    """New cards/day so every unseen card is seen by the day before the deadline. On the exam day
    itself it's too late for new cards: none (due reviews and the cram deck stay)."""
    if days_left <= 0:
        return 0
    return unseen if days_left == 1 else math.ceil(unseen / (days_left - 1))


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


CALIBRATION_DAYS = 28


def calibrate(state):
    """seconds_per_review and reviews_per_new_card from every course's review log when there is
    enough of it, else the default. A value set in the config always wins.

    - seconds per review: mean answer time over the last 28 days (each answer capped at 2 minutes),
      once there are 100 answers.
    - reviews per new card: answers per day / new cards per day over the last 28 days, once the
      review log spans 56 days and 30 cards were new in the window (a card is new when its first
      answer in the log is a learning step: the log only reaches back `--since-days`, so a mature
      card can have its first logged answer in the window too). Clamped to 4-20: early on it reads
      low, because reviews of new cards haven't piled up yet.
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
    first = {}  # cardId -> its oldest answer in the log
    for r in answers:
        if r['cardId'] not in first or r['id'] < first[r['cardId']]['id']:
            first[r['cardId']] = r
    new_recent = sum(1 for r in first.values() if age(r) < CALIBRATION_DAYS and r['kind'] == 0)
    history = max((age(r) for r in first.values()), default=0)
    if 'reviews_per_new_card' in explicit:
        out['reviews_per_new_card'] = dict(value=cfg['reviews_per_new_card'], source='config')
    elif history >= 2 * CALIBRATION_DAYS and new_recent >= 30:
        out['reviews_per_new_card'] = dict(value=round(min(20.0, max(4.0, len(recent) / new_recent)), 1),
                                           source='measured', new_cards=new_recent, answers=len(recent))
    else:
        out['reviews_per_new_card'] = dict(value=cfg['reviews_per_new_card'], source='default',
                                           history_days=history, new_cards=new_recent)
    return out


def new_cap(minutes, calibration):
    """New cards/day for all courses that `minutes` of Anki a day leave room for."""
    return int(minutes * 60 / calibration['seconds_per_review']['value']
               / calibration['reviews_per_new_card']['value'])


def minutes_for(cap, calibration):
    """The fewest anki_minutes_per_day whose budget is at least `cap` new cards/day."""
    minutes = math.ceil(cap * calibration['seconds_per_review']['value']
                        * calibration['reviews_per_new_card']['value'] / 60)
    while new_cap(minutes, calibration) < cap:  # float rounding
        minutes += 1
    return minutes


def budget_warning(code, required, granted, claimed, urgent_takes, own_open, total_cap, calibration):
    """Why a course gets fewer new cards than its deadline needs, and what would change that.

    claimed: new cards/day the courses ahead of it would take with no budget limit.
    urgent_takes: {course: (new cards/day, deadline)} for the courses within cram_days.
    own_open: the day this course's own nearest deadline enters cram_days.
    """
    minutes = minutes_for(claimed + required, calibration)
    head = f"{code}: needs {required} new cards/day to finish in time, but "
    taken = sum(n for n, _ in urgent_takes.values())
    if taken < total_cap:
        return head + (f"the daily budget leaves {granted}. Raise anki_minutes_per_day to {minutes} "
                       f"or cut scope.")
    hogs = sorted(urgent_takes.items(), key=lambda kv: (kv[1][1], kv[0]))
    names = ' and '.join(f"{c} ({d.isoformat()})" for c, (_, d) in hogs)
    free = max(d for _, d in urgent_takes.values()) + datetime.timedelta(days=1)
    if own_open is not None and own_open < free:
        until = f"{own_open.isoformat()}, when its own deadline is within cram_days"
    else:
        until = f"{free.isoformat()}, when the budget frees up"
    one = len(hogs) == 1
    return head + (f"{names} {'is' if one else 'are'} within cram_days of {'its' if one else 'their'} "
                   f"deadline{'' if one else 's'} and take{'s' if one else ''} {taken} new cards/day, "
                   f"the whole budget of {total_cap}, so it gets none until {until}. "
                   f"Fitting it in before then would take anki_minutes_per_day: {minutes}.")


def plan(state):
    today = day(state['today'])
    cfg = state['config']
    calibration = calibrate(state)
    total_cap = new_cap(cfg['anki_minutes_per_day'], calibration)
    horizon = int(cfg['calendar_days'])
    max_lessons = int(cfg['max_lessons_per_day'])
    courses = state.get('courses') or {}
    course_cfg = cfg.get('courses') or {}
    retired = {code for code, c in course_cfg.items() if (c or {}).get('retired')}
    lessons = sorted((l for l in state.get('lessons') or [] if l['course'] not in retired),
                     key=lambda l: (l['course'], l['order'], l['path']))
    warnings, inbox = [], []

    anki_guids = {code: {c['guid'] for c in course.get('cards') or []} for code, course in courses.items()}

    def unpushed(lesson):
        guids = anki_guids.get(lesson['course'], set())
        return sum(c['count'] for c in lesson['cards'] if not c['guid'] or c['guid'] not in guids)

    anki_exam_tags = {code: {c['guid']: {t.lower() for t in c.get('tags') or []
                                         if t.lower().startswith(f'{code.lower()}::exam::')}
                             for c in course.get('cards') or []} for code, course in courses.items()}

    def stale_tags(lesson):
        """Cards already in Anki whose exam tags no longer match the lesson's goals."""
        tags = anki_exam_tags.get(lesson['course'], {})
        want = {exam_tag(dict(course=lesson['course'], id=g)) for g in lesson['goals']}
        return any(c['guid'] in tags and tags[c['guid']] != want for c in lesson['cards'])

    pushed_hashes = state.get('pushed') or {}

    def edited(lesson):
        """Cards already in Anki whose block changed since its last push (or was never recorded)."""
        guids = anki_guids.get(lesson['course'], set())
        return any(c['guid'] in guids and pushed_hashes.get(c['guid']) != c.get('hash') for c in lesson['cards'])

    def outdated(lesson):
        return stale_tags(lesson) or edited(lesson)

    # ---- goals ---------------------------------------------------------------------------------
    goals_out, active = [], []
    cram_delete = []
    for g in sorted(state.get('goals') or [], key=lambda g: (g.get('date') or '9999', g['course'], g['id'])):
        if g.get('status', 'active') != 'active':
            continue
        if g['course'] in retired:
            cram_delete.append(cram_name(g))
            continue
        days_left = (day(g['date']) - today).days if g.get('date') else None
        if days_left is not None and days_left < 0:
            inbox.append(f"{g['title']} was on {g['date']}: archive it, or carry its unread lessons into a later goal?")
            cram_delete.append(cram_name(g))
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
        g['cumulative'] = len(ids) + unpushed_union
        rate = countdown(g['cumulative'], g['days_left'])
        g['required_new_per_day'] = rate
        per_course_required[g['course']] = max(per_course_required.get(g['course'], 0), rate)
        if rate > total_cap:
            g['cram'] = True
    for g in deadline_goals:  # last day to do lessons: the buffer before the deadline, or the day before in cram
        window = g['days_left'] - int(cfg['lesson_buffer_days'])
        g['lessons_due_by'] = max(0, (g['days_left'] - 1) if g['cram'] else window - 1)

    # ---- per-course new cards/day ----------------------------------------------------------------
    codes = sorted(set(courses) | {l['course'] for l in lessons})
    fixed = {}  # courses that get no new cards: retired or done
    for code in codes:
        if code in retired:
            fixed[code] = dict(new_per_day=0, new_today=None, overdue=0, new_pool=0, reposition=None,
                               retired=True, done=bool((course_cfg.get(code) or {}).get('done')))
        elif (course_cfg.get(code) or {}).get('done'):
            fixed[code] = dict(new_per_day=0, new_today=None, overdue=0, new_pool=0, reposition=None, done=True)
    planned_codes = [c for c in codes if c not in fixed]
    budget_courses = {g['course'] for g in active if g['mode'] == 'self-paced-budget'}

    def allocate(goals_left, pool, overdue, warn=False):
        """New cards/day per course for one day: {code: (new/day, today-only limit or None)}.
        goals_left: [(goal, days left, cards/day its countdown needs)]. A course within cram_days of a
        deadline takes what it needs; the others share the budget, nearest deadline first."""
        required, urgency, last_day = {}, {}, set()
        for g, left, rate in goals_left:
            required[g['course']] = max(required.get(g['course'], 0), rate)
            urgency[g['course']] = min(urgency.get(g['course'], 10 ** 6), left)
            if left <= 1:
                last_day.add(g['course'])
        out, remaining_cap, urgent_takes = {}, total_cap, {}
        claimed = 0  # new cards/day the courses so far would take with no budget limit
        for code in sorted(planned_codes, key=lambda c: (urgency.get(c, 10 ** 6), c)):
            need = required.get(code, 0)
            floor = int(cfg['casual_new_per_day']) if pool.get(code) else 0
            if code in budget_courses:
                floor = max(floor, int(cfg['budget_new_per_day']))
            want = max(need, floor)
            urgent = urgency.get(code, 10 ** 6) <= int(cfg['cram_days'])
            if overdue.get(code, 0) > int(cfg['backlog_overdue_threshold']) and not urgent:
                want = 0
                if warn:
                    warnings.append(f"{code}: {plural(overdue[code], 'overdue review')}; no new cards until they're done.")
            if urgent:
                granted = want  # heavier load accepted right before a deadline
                if granted:
                    urgent_takes[code] = (granted, today + datetime.timedelta(days=urgency[code]))
            else:
                granted = min(want, max(remaining_cap, 0))
                if warn and granted < need:
                    own_open = (today + datetime.timedelta(days=urgency[code] - int(cfg['cram_days']))
                                if code in urgency else None)
                    warnings.append(budget_warning(code, need, granted, claimed, urgent_takes, own_open,
                                                   total_cap, calibration))
            claimed += want
            remaining_cap -= granted
            # The last day's limit is also set as "today only", so it lapses if the next run doesn't happen.
            out[code] = (granted, granted if urgent and code in last_day else None)
        return out

    new_pool, overdue = {}, {}
    for code in planned_codes:
        course = courses.get(code) or {}
        cards = course.get('cards') or []
        today_num = course.get('today')
        new_pool[code] = sum(1 for c in cards if c['type'] == 0 and not c['suspended'])
        overdue[code] = sum(1 for c in cards if c['type'] == 2 and c['queue'] == 2 and today_num is not None
                            and c['due'] < today_num)
    todays = allocate([(g, g['days_left'], g['required_new_per_day']) for g in deadline_goals], new_pool, overdue,
                      warn=True)
    course_out = {}
    for code in codes:
        if code in fixed:
            course_out[code] = fixed[code]
        else:
            granted, new_today = todays[code]
            course_out[code] = dict(new_per_day=granted, new_today=new_today, overdue=overdue[code],
                                    new_pool=new_pool[code], reposition=None)

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
    by_path = {l['path']: l for l in lessons}
    # Only verified lessons are put on the calendar to read (unverified ones are in the inbox).
    queue = {g['id'] + '@' + g['course']: [p for p in g['lessons_left'] if by_path[p]['verified']] for g in active}
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
                if take > max_lessons:
                    warnings.append(f"{g['title']}: {plural(len(pending), 'lesson')} left in "
                                    f"{plural(days_to_go, 'day')}, more than max_lessons_per_day; all are scheduled.")
                else:
                    warnings.append(f"{g['title']}: today's lessons go over max_lessons_per_day, since nearer "
                                    f"deadlines use today's slots; all are scheduled.")
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
        if not l['verified'] or not (unpushed(l) or outdated(l)):
            continue
        if l['done']:
            push.append(dict(path=l['path'], ungated=False))
        elif any(l['course'] == g['course'] and g['id'] in l['goals'] for g in cram_goals):
            push.append(dict(path=l['path'], ungated=True))
        elif outdated(l):  # the gate stays shut: only the cards already in Anki are updated
            push.append(dict(path=l['path'], ungated=False))
    for l in lessons:
        if not l['verified'] and l['cards']:
            inbox.append(f"{l['title']} isn't verified yet: run the verification pass.")

    cram = []
    for g in cram_goals:
        if g['days_left'] <= int(cfg['cram_days']):
            cram.append(dict(name=cram_name(g), limit=9999,
                             search=f'deck:"{g["course"]}" tag:{g["course"]}::exam::{g["id"]} prop:r<0.85'))
    built = {d['name'] for d in cram}  # a goal that left cram mode (caught up, or `cram` unset) loses its deck
    cram_delete += [cram_name(g) for g in goals_out if cram_name(g) not in built]

    # ---- the next days: card counts and calendar text ---------------------------------------------
    pool = {code: out['new_pool'] for code, out in course_out.items()}
    pushed_now = {p['path'] for p in push if p['ungated'] or by_path[p['path']]['done']}  # not just updated
    for p in pushed_now:  # this run pushes these, so their cards are in today's pool
        pool[by_path[p]['course']] = pool.get(by_path[p]['course'], 0) + unpushed(by_path[p])
    days = []
    carry = 0  # estimate: each new card comes back for one review the next day
    introduced = {}  # new cards the forecast has shown per course so far
    for k in range(horizon):
        date = today + datetime.timedelta(days=k)
        new, due = 0, carry
        if k == 0:
            rates = {code: out['new_per_day'] if out['new_today'] is None else out['new_today']
                     for code, out in course_out.items()}
        else:  # a later day: deadlines are k days nearer and the cards shown so far are seen (reviews assumed done)
            left = [(g, g['days_left'] - k,
                     countdown(max(0, g['cumulative'] - introduced.get(g['course'], 0)), g['days_left'] - k))
                    for g in deadline_goals if g['days_left'] >= k]
            rates = {code: granted for code, (granted, _) in allocate(left, pool, {}).items()}
        for code, out in course_out.items():
            course = courses.get(code) or {}
            used = min(rates.get(code, 0), pool[code])
            pool[code] -= used
            introduced[code] = introduced.get(code, 0) + used
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
        days.append(dict(date=date.isoformat(), weekday=WEEKDAYS[date.weekday()], new=new, due=due, cards=cards_total,
                         lessons=[dict(title=l['title'], path=l['path']) for l in todays],
                         title=title, description='\n'.join(lines)))

    for g in goals_out:
        g.pop('scope_new', None)
        g.pop('cumulative', None)
    # ---- retired courses: suspend their cards; unsuspend what studious suspended once retired is off ----
    record = {code: list(ids) for code, ids in (state.get('retired') or {}).items()}
    suspend, unsuspend = [], []
    for code in sorted(set(record) | retired):
        cards = (courses.get(code) or {}).get('cards') or []
        if code in retired:
            ids = [c['cardId'] for c in cards if not c['suspended']]
            suspend += ids
            record[code] = sorted(set(record.get(code) or []) | set(ids))
        else:
            mine = set(record.pop(code))
            unsuspend += [c['cardId'] for c in cards if c['cardId'] in mine and c['suspended']]

    for code in sorted({g['course'] for g in state.get('goals') or []} - retired):
        mine = [g for g in state.get('goals') or [] if g['course'] == code and g.get('date')]
        finals = [g for g in mine if 'final' in f"{g['id']} {g.get('title', '')}".lower()]
        done = ((cfg.get('courses') or {}).get(code) or {}).get('done')
        if finals and not done and all(day(g['date']) < today for g in mine):
            inbox.append(f"{code}'s final is over: mark it `done: true` under courses in the config, so its "
                         f"cards move to the done preset (lower retention, fewer reviews)?")
    return dict(today=today.isoformat(), total_new_per_day=total_cap, calibration=calibration,
                goals=goals_out, courses=course_out,
                push=push, cram=cram, cram_delete=sorted(set(cram_delete)), days=days,
                suspend=suspend, unsuspend=unsuspend, retired=record, inbox=inbox, warnings=warnings)


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
