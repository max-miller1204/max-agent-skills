#!/usr/bin/env python3
"""The studious planner: a pure function from state.json (snapshot.py) to plan.json.

    python3 plan.py state.json [--out plan.json]

No I/O besides reading the state and writing the plan; same input, same output.

Rules (Anki and FSRS guidance on workload, plus lesson pacing):
- One cap, `cards_per_day`: Anki cards a day for all courses together, new cards plus reviews. The
  reviews come from Anki's FSRS simulator (state `forecast`, from snapshot.py): the cards already
  reviewed give each day's load, and one new card adds `per_new_card[j]` reviews j days after it's
  shown (about 1 in the first 3 days, 4 in 30). Without the simulator (an older Anki patch) a
  built-in default curve and the cards' due dates stand in.
- Load balancing: new cards are placed day by day where they add least to the busiest days (a
  sum-of-squares score over the next SPAN days, plus one on new cards per day so they stay steady), so a deadline's cards are spread over the days
  before it instead of piling up at the end. Deadline goals go first, nearest first; then each
  course's casual or budget floor fills the room left under the cap.
- Overdue reviews above the backlog threshold -> no new cards today for that course, unless one of
  its goals is within `cram_days`.
- Deadline goals: every card in scope is shown by the day before the deadline, and by two days
  before when they fit, so each comes back at least once before the exam. If they fit under
  the cap they stay under it; if they don't, the goal is in cram mode and they go in anyway, on the
  least busy days, with a warning that names the busiest day. On the day before, the limit is also
  set as "today only", so it lapses if the next run doesn't happen. On the exam day the goal is over
  for pacing: the course gets no casual cards (they'd be that exam's), only its other goals' cards;
  a goal in cram mode keeps its cram deck that day. Overlapping goals of one course count the union
  of their scopes, nearest deadline first. Cards of unverified lessons can't be shown, so they're
  left out (the inbox asks for the verification).
- Lessons are paced to be done `lesson_buffer_days` before the deadline, at most
  `max_lessons_per_day` across all goals, nearest deadline first. A goal whose lessons or cards don't
  fit switches to cram mode: lessons may run until the day before (all of them scheduled, over the
  per-day cap if need be, with a warning), cards are pushed without waiting
  for lessons, and for the last `cram_days` a filtered deck of its weakest cards is built. A goal
  that leaves cram mode, or is archived or done, loses its cram deck. A lesson's cards can be shown
  from the day after it's read (or right away when pushed ungated).
- Only verified lessons are scheduled to read; unverified ones wait in the inbox.
- A lesson whose cards in Anki are out of date is pushed again: a card block edited since its last
  push (state `pushed`), or exam tags that differ from its `goals` (a goal carried or dropped).
  Unless it's done or in cram, only the cards already in Anki are updated.
- A course with new cards but no deadline pressure gets `casual_new_per_day`, room permitting.
- A course marked `done: true` in the config gets no new cards (its decks move to the done preset).
- A course marked `retired: true` drops out of planning (no lessons, pushes, goals or inbox items) and
  its cards are suspended; the cards studious suspended are recorded (state `retired`), so taking the
  flag off unsuspends exactly those and leaves cards suspended by hand alone.
- The calendar's later days come from the same placement: each day's new cards and the reviews
  expected that day, assuming each day's reviews get done.
"""
import argparse
import datetime
import json
import math
import sys

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


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


INDENT = '\u00a0' * 4  # calendar sub-bullets
SPAN = 14  # days after a card is shown whose load decides where it goes (most of its early reviews)
OBSOLETE_KEYS = ('anki_minutes_per_day', 'seconds_per_review', 'reviews_per_new_card')

# Reviews j days after a new card is first shown, from Anki's FSRS simulator with the default FSRS
# parameters, 90% retention and one learning and one relearning step (20,000 simulated cards). Used
# only when the snapshot has no forecast from Anki itself.
DEFAULT_PER_NEW_CARD = [
    0.0, 0.329, 0.5, 0.378, 0.184, 0.178, 0.167, 0.175, 0.169, 0.162, 0.154, 0.155, 0.141, 0.133, 0.123,
    0.117, 0.107, 0.099, 0.091, 0.086, 0.083, 0.076, 0.072, 0.068, 0.067, 0.063, 0.059, 0.059, 0.057, 0.057,
    0.055, 0.055, 0.056, 0.056, 0.055, 0.054, 0.052, 0.053, 0.052, 0.053, 0.05, 0.05, 0.05, 0.05, 0.049,
    0.049, 0.048, 0.048, 0.049, 0.047, 0.046, 0.047, 0.047, 0.046, 0.046, 0.046, 0.046, 0.044, 0.045, 0.043,
    0.043, 0.044, 0.043, 0.042, 0.042, 0.041, 0.039, 0.04, 0.039, 0.038, 0.039, 0.037, 0.037, 0.036, 0.035,
    0.035, 0.034, 0.035, 0.034, 0.033, 0.034, 0.032, 0.032, 0.03, 0.032, 0.031, 0.031, 0.031, 0.031, 0.03,
]


class Load:
    """Expected Anki cards per day (new + reviews) as new cards are placed: base is the reviews of
    cards already reviewed, and a card shown on day t adds 1 + f[0] that day and f[j] on day t + j."""

    def __init__(self, base, f, cap):
        self.days, self.cap, self.f = len(base), cap, f
        self.load = [float(x) for x in base]
        self.new = [0] * len(base)
        self.w = [1 + f[0]] + list(f[1:])

    def span(self, t):
        return range(t, min(self.days, t + SPAN))

    def fits(self, t, x):
        return all(self.load[d] + x * self.w[d - t] <= self.cap + 1e-9 for d in self.span(t))

    def room(self, t):
        """The most new cards day t can take without any day of its span going over the cap."""
        out = None
        for d in self.span(t):
            w = self.w[d - t]
            if w > 0:
                r = math.floor((self.cap - self.load[d]) / w + 1e-9)
                out = r if out is None else min(out, r)
        return max(0, out or 0)

    def score(self, t, x):
        """How much x cards on day t raise the sum of squares of the days they touch, plus the sum of
        squares of new cards per day: level days, and steady new cards rather than 18, 0, 18, 0."""
        return (sum(2 * self.load[d] * x * self.w[d - t] + (x * self.w[d - t]) ** 2 for d in self.span(t))
                + 2 * self.new[t] * x + x * x)

    def add(self, t, x):
        self.new[t] += x
        for d in range(t, self.days):
            self.load[d] += x * self.w[d - t]


def plan(state):
    today = day(state['today'])
    cfg = state['config']
    cap = int(cfg.get('cards_per_day', 150))
    horizon = int(cfg['calendar_days'])
    max_lessons = int(cfg['max_lessons_per_day'])
    courses = state.get('courses') or {}
    course_cfg = cfg.get('courses') or {}
    retired = {code for code, c in course_cfg.items() if (c or {}).get('retired')}
    lessons = sorted((l for l in state.get('lessons') or [] if l['course'] not in retired),
                     key=lambda l: (l['course'], l['order'], l['path']))
    warnings, inbox = [], []
    for key in OBSOLETE_KEYS:
        if key in (state.get('config_set') or []):
            warnings.append(f"studious/config.md sets {key}, which is no longer used: the daily budget is "
                            f"cards_per_day (new cards plus reviews). Remove it.")

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
            cram_delete.append(cram_name(g))  # archived or done, maybe before the run after its exam
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
                     lessons_left=[l['path'] for l in remaining], cram=bool(g.get('cram')), required_new_per_day=0,
                     lessons_per_week=g.get('lessons_per_week'))
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

    # A goal whose exam is today is over for pacing: no placement, urgency, card order or cram pushes.
    # It keeps a cram verdict (the day before's test: would its unseen cards have fit?) for its cram
    # deck and the calendar line.
    exam_today = [g for g in deadline_goals if g['days_left'] == 0]
    deadline_goals = [g for g in deadline_goals if g['days_left'] > 0]

    # Cards each deadline must have shown: the union of scopes through it, nearest first. Unverified
    # lessons' cards can't be pushed, so they're left out.
    seen_ids = {}
    for g in deadline_goals:
        ids = seen_ids.setdefault(g['course'], set())
        ids.update(g['scope_new'])
        g['cumulative'] = len(ids) + sum(
            unpushed(l) for l in lessons if l['course'] == g['course'] and l['verified']
            and any(x['id'] in l['goals'] for x in deadline_goals
                    if x['course'] == g['course'] and x['days_left'] <= g['days_left']))

    # ---- the review load the placement starts from -----------------------------------------------
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

    forecast = state.get('forecast') or {}
    from_anki = forecast.get('source') == 'anki' and forecast.get('per_new_card')
    f = list(forecast['per_new_card']) if from_anki else list(DEFAULT_PER_NEW_CARD)
    days_n = len(f)
    if forecast.get('source') == 'unavailable':
        warnings.append(f"Review load estimated from FSRS defaults: Anki's forecast isn't available "
                        f"({forecast.get('reason')}). Run the connectors' anki/apply.py and restart Anki.")
    base = [0.0] * days_n
    for code in codes:  # every course's reviews count, done ones included: they're still your cards/day
        if code in retired:
            continue
        course = courses.get(code) or {}
        counts = course.get('counts') or {}
        base[0] += counts.get('review', 0) + counts.get('learning', 0)  # today: exactly what Anki shows
        load = (forecast.get('load') or {}).get(code) if from_anki else None
        today_num = course.get('today')
        for k in range(1, days_n):
            if load is not None:
                base[k] += load[k] if k < len(load) else 0
            elif today_num is not None:
                base[k] += sum(1 for c in course.get('cards') or [] if c['type'] == 2 and not c['suspended']
                               and c['queue'] == 2 and c['due'] == today_num + k)

    new_pool, overdue = {}, {}
    for code in planned_codes:
        course = courses.get(code) or {}
        cards = course.get('cards') or []
        today_num = course.get('today')
        new_pool[code] = sum(1 for c in cards if c['type'] == 0 and not c['suspended'])
        overdue[code] = sum(1 for c in cards if c['type'] == 2 and c['queue'] == 2 and today_num is not None
                            and c['due'] < today_num)
    nearest = {}
    for g in deadline_goals:
        nearest[g['course']] = min(nearest.get(g['course'], 10 ** 6), g['days_left'])
    backlog = {code for code in planned_codes if overdue[code] > int(cfg['backlog_overdue_threshold'])
               and nearest.get(code, 10 ** 6) > int(cfg['cram_days'])}
    for code in sorted(backlog):
        warnings.append(f"{code}: {plural(overdue[code], 'overdue review')}; no new cards until they're done.")

    by_path = {l['path']: l for l in lessons}
    budget_goals = [g for g in active if g['mode'] == 'self-paced-budget']

    # ---- lessons per day (also over the placement's days, since a read lesson releases its cards) ---
    def schedule_lessons():
        for g in deadline_goals:  # last day to do lessons: the buffer before the deadline, or the day before in cram
            window = g['days_left'] - int(cfg['lesson_buffer_days'])
            g['lessons_due_by'] = max(0, (g['days_left'] - 1) if g['cram'] else window - 1)
        notes = []
        # Only verified lessons are put on the calendar to read (unverified ones are in the inbox).
        queue = {g['id'] + '@' + g['course']: [p for p in g['lessons_left'] if by_path[p]['verified']] for g in active}
        scheduled, out = set(), []
        for k in range(days_n):
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
                        notes.append(f"{g['title']}: {plural(len(pending), 'lesson')} left in "
                                     f"{plural(days_to_go, 'day')}, more than max_lessons_per_day; all are scheduled.")
                    else:
                        notes.append(f"{g['title']}: today's lessons go over max_lessons_per_day, since nearer "
                                     f"deadlines use today's slots; all are scheduled.")
                for p in pending[:take]:
                    todays.append(p)
                    scheduled.add(p)
                capacity -= take
            for g in budget_goals:
                per_week = max(1, int(g.get('lessons_per_week') or 4))
                if capacity > 0 and math.floor((k + 1) * per_week / 7) > math.floor(k * per_week / 7):
                    pending = [p for p in queue[g['id'] + '@' + g['course']] if p not in scheduled]
                    if pending:
                        todays.append(pending[0])
                        scheduled.add(pending[0])
                        capacity -= 1
            out.append(todays)
        return out, notes

    # ---- push: verified lessons whose new cards may enter Anki ------------------------------------
    def push_list():
        cram_goals = [g for g in deadline_goals if g['cram']]  # pushes cards ungated; not on the exam day
        out = []
        for l in lessons:
            if not l['verified'] or not (unpushed(l) or outdated(l)):
                continue
            if l['done']:
                out.append(dict(path=l['path'], ungated=False))
            elif any(l['course'] == g['course'] and g['id'] in l['goals'] for g in cram_goals):
                out.append(dict(path=l['path'], ungated=True))
            elif outdated(l):  # the gate stays shut: only the cards already in Anki are updated
                out.append(dict(path=l['path'], ungated=False))
        return out

    def availability(push, days_lessons):
        """Per course, how many new cards can have been shown by the end of each day: the new cards in
        Anki, what this run pushes, and each lesson read on day d from day d + 1."""
        pushed_now = {p['path'] for p in push if p['ungated'] or by_path[p['path']]['done']}  # not just updated
        start = dict(new_pool)
        for p in pushed_now:
            start[by_path[p]['course']] = start.get(by_path[p]['course'], 0) + unpushed(by_path[p])
        avail = {code: [start.get(code, 0)] * days_n for code in planned_codes}
        for d, todays in enumerate(days_lessons):
            for p in todays:
                l = by_path[p]
                if p not in pushed_now and l['course'] in avail:
                    for k in range(d + 1, days_n):
                        avail[l['course']][k] += unpushed(l)
        return avail, pushed_now

    def window(g):
        """The days a goal's cards may be shown: through the day before its deadline (place() keeps
        that last day for cards that don't fit earlier)."""
        return min(days_n, g['days_left'])  # days 0 .. days_left - 1; only goals with days_left > 0 get here

    def place(avail, soft):
        """Place every deadline's cards, then the casual floors. Returns the load, new cards per course
        per day, per goal [placed today, left over, went over the cap]."""
        load = Load(base, f, cap)
        shown = {code: [0] * days_n for code in planned_codes}

        def slack(code):  # suffix minimum of (available - shown so far), so x cards on day t fit when slack[t] >= x
            out, running, low = [0] * days_n, 0, 10 ** 9
            cum = []
            for k in range(days_n):
                running += shown[code][k]
                cum.append(running)
            for k in range(days_n - 1, -1, -1):
                low = min(low, avail[code][k] - cum[k])
                out[k] = low
            return out

        result = {}
        for g in deadline_goals:
            code = g['course']
            if code not in shown:
                continue
            need = max(0, g['cumulative'] - sum(shown[code]))
            days = [t for t in range(window(g)) if not (t == 0 and code in backlog)]
            # The day before the exam only takes what doesn't fit earlier: a card shown by two days
            # before comes back at least once before the exam.
            early = [t for t in days if t != g['days_left'] - 1] or days
            today_n, over, left = 0, False, need
            chunk = max(1, math.ceil(need / 60))
            while left > 0:
                x = min(chunk, left)
                room = slack(code)

                def fit(ts):
                    return [t for t in ts if room[t] >= x and load.fits(t, x)]

                fitting = fit(early)
                if not fitting and x > 1 and early != days:
                    chunk = 1  # fill the earlier days card by card before using the day before
                    continue
                fitting = fitting or fit(days)
                options = fitting or ([t for t in days if room[t] >= x] if soft else [])
                if not options:
                    if x > 1:
                        chunk = 1
                        continue
                    break
                t = min(options, key=lambda t: (round(load.score(t, x), 6), t))
                over = over or not fitting
                load.add(t, x)
                shown[code][t] += x
                today_n += x if t == 0 else 0
                left -= x
            result[(code, g['id'])] = [today_n, left, over]
        for k in range(days_n):  # casual and budget floors, room permitting
            exams = {g['course'] for g in exam_today + deadline_goals if g['days_left'] == k}
            for code in planned_codes:
                floor = int(cfg['casual_new_per_day'])
                if code in budget_courses:
                    floor = max(floor, int(cfg['budget_new_per_day']))
                if code in exams or (k == 0 and code in backlog):
                    floor = 0
                x = min(floor - shown[code][k], slack(code)[k], load.room(k))
                if x > 0:
                    load.add(k, x)
                    shown[code][k] += x
        return load, shown, result

    days_lessons, lesson_notes = schedule_lessons()
    push = push_list()
    avail, pushed_now = availability(push, days_lessons)
    _, _, trial = place(avail, soft=False)
    flipped = False
    for g in deadline_goals:  # cards that don't fit under the cap: cram mode
        if trial.get((g['course'], g['id']), [0, 0, False])[1] > 0 and not g['cram']:
            g['cram'] = flipped = True
    if flipped:
        days_lessons, lesson_notes = schedule_lessons()
        push = push_list()
        avail, pushed_now = availability(push, days_lessons)
    load, shown, result = place(avail, soft=True)
    warnings += lesson_notes
    for g in exam_today:  # the verdict of the day before: would its unseen cards have fit?
        if base[0] + g['unseen'] > cap:
            g['cram'] = True
    for g in deadline_goals:
        today_n, left, over = result.get((g['course'], g['id']), [0, 0, False])
        g['required_new_per_day'] = today_n
        if over:
            span = range(0, min(days_n, window(g) + 1))  # through the exam day
            busiest = max(span, key=lambda k: (load.load[k], -k))
            date = today + datetime.timedelta(days=busiest)
            peak = math.ceil(load.load[busiest] - 1e-6)
            warnings.append(f"{g['title']}: its cards don't fit in cards_per_day ({cap}), so they go over it on the "
                            f"least busy days; the busiest, {WEEKDAYS[date.weekday()]} {date.isoformat()}, comes to "
                            f"about {peak} cards. Raise cards_per_day to about {peak} or cut scope.")

    course_out = {}
    for code in codes:
        if code in fixed:
            course_out[code] = fixed[code]
            continue
        n = shown[code][0]
        day_before = any(g['course'] == code and g['days_left'] == 1 for g in deadline_goals)
        # On the day before an exam the limit is also set as "today only", so that big day's limit
        # lapses if the next run doesn't happen.
        course_out[code] = dict(new_per_day=n, new_today=n if day_before else None, overdue=overdue[code],
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

    for l in lessons:
        if not l['verified'] and l['cards']:
            inbox.append(f"{l['title']} isn't verified yet: run the verification pass.")

    deck_goals = [g for g in exam_today + deadline_goals if g['cram']]  # cram deck and calendar line
    cram = []
    for g in deck_goals:
        if g['days_left'] <= int(cfg['cram_days']):
            cram.append(dict(name=cram_name(g), limit=9999,
                             search=f'deck:"{g["course"]}" tag:{g["course"]}::exam::{g["id"]} prop:r<0.85'))
    built = {d['name'] for d in cram}  # a goal that left cram mode (caught up, or `cram` unset) loses its deck
    cram_delete += [cram_name(g) for g in goals_out if cram_name(g) not in built]

    # ---- the next days: card counts and calendar text ---------------------------------------------
    days = []
    for k in range(min(horizon, days_n)):
        date = today + datetime.timedelta(days=k)
        new = sum(shown[code][k] for code in planned_codes)
        cards_total = round(load.load[k] - new * f[0])  # new cards + reviews (same-day steps aren't cards)
        due = max(0, cards_total - new)
        todays = [by_path[p] for p in days_lessons[k]]
        title = f"[studious] Daily quota: {plural(cards_total, 'card')} · {plural(len(todays), 'lesson')}"
        lines = ['Today', f"• Anki: {plural(cards_total, 'card')}"]
        # Sub-bullets are indented with no-break spaces: Calendar's description view collapses plain ones.
        lines += ([f"• {'Lesson' if len(todays) == 1 else 'Lessons'}:"] + [f"{INDENT}- {l['title']}" for l in todays]
                  if todays else ['• Lessons: none'])
        for g in deck_goals:
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
        g.pop('lessons_per_week', None)
    load_info = dict(source='anki' if from_anki else 'default', cards_per_day=cap,
                     reviews_per_new_card_30_days=round(sum(f[:30]), 1))
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
    return dict(today=today.isoformat(), total_new_per_day=sum(c['new_per_day'] for c in course_out.values()),
                load=load_info,
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
