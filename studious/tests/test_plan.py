"""Golden tests for scripts/plan.py.  uv run --with pytest pytest tests/test_plan.py"""
import copy
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('plan', Path(__file__).resolve().parent.parent / 'scripts/plan.py')
planner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(planner)

CONFIG = dict(cards_per_day=150, max_lessons_per_day=2,
              lesson_buffer_days=3, prep_days=2, exam_recall_target=0.9, casual_new_per_day=5, budget_new_per_day=10,
              backlog_overdue_threshold=0, calendar_days=7, vault_name='vault')
TODAY_NUM = 1000
NEXT_DAY = [0.0, 1.0] + [0.0] * 88  # test review curve: each new card comes back once, the next day


def card(cid, course='CS3214', deck=None, goals=(), type_=0, due=None, queue=None, guid=None):
    return dict(cardId=cid, noteId=cid, guid=guid or f'g{cid}', deck=deck or f'{course}::L01',
                type=type_, queue=queue if queue is not None else type_, due=due if due is not None else cid,
                reps=0, lapses=0, flag=0, suspended=False, retrievability=None,
                tags=[f'{course}::exam::{g}' for g in goals])


def reviewed(cid, r, s, goals=('Test2',), course='CS3214', **kw):
    """A card with a memory state: recall r today, stability s days."""
    c = card(cid, course=course, goals=goals, type_=2, queue=2, due=TODAY_NUM + 5, **kw)
    return dict(c, retrievability=r, stability=s, reps=3)


def lesson(n, course='CS3214', goals=('Test2',), done=False, verified=True, cards=3, pushed=False):
    return dict(path=f'courses/{course}/lessons/{n:02d}-l{n}.md', title=f'lesson{n}', course=course,
                deck=f'{course}::L{n:02d}', order=n, goals=list(goals), done=done, verified=verified,
                cards=[dict(guid=f'{course}-{n}-{i}' if pushed else None, count=1) for i in range(cards)])


def goal(gid='Test2', course='CS3214', date='2026-11-11', **kw):
    return {**dict(id=gid, course=course, title=f'{course} {gid}', mode='deadline', date=date, status='active',
                   cram=False, path=f'goals/{course}-{gid}.md'), **kw}


def course(cards, counts=None, code='CS3214'):
    return dict(root=code, today=TODAY_NUM, counts=counts or dict(new=0, learning=0, review=0),
                preset=None, revlog=[], cards=cards)


def state(goals=(), lessons=(), courses=None, today='2026-11-01', forecast=None, **cfg):
    return dict(today=today, config={**CONFIG, **cfg}, goals=list(goals), lessons=list(lessons),
                courses=courses or {}, errors=[],
                forecast=forecast or dict(source='anki', per_new_card=NEXT_DAY, load={}))


def test_casual_cards_fill_only_the_room_under_the_cap():
    cards = [card(i) for i in range(30)]
    p = planner.plan(state(courses={'CS3214': course(cards, counts=dict(new=0, learning=0, review=6))},
                           cards_per_day=8))
    assert p['courses']['CS3214']['new_per_day'] == 2  # 6 reviews + 2 new = 8; casual would be 5
    assert p['days'][0]['cards'] == 8 and p['total_new_per_day'] == 2


def test_a_deadline_is_spread_over_the_days_before_it():
    cards = [card(i, goals=['Test2']) for i in range(90)]
    p = planner.plan(state([goal(date='2026-11-11')], courses={'CS3214': course(cards)}))
    new = [d['new'] for d in p['days']]
    assert 10 <= new[0] <= 15  # about 90 / 9, a little more today, before any reviews come back
    assert all(6 <= n <= 15 for n in new) and 60 <= sum(new) < 90  # the rest on days 7 and 8, past the calendar
    assert p['goals'][0]['required_new_per_day'] == new[0] == p['courses']['CS3214']['new_per_day']
    assert p['courses']['CS3214']['new_today'] is None


def test_last_day_puts_everything_in_today():
    cards = [card(i, goals=['Test2']) for i in range(40)]
    p = planner.plan(state([goal(date='2026-11-02')], courses={'CS3214': course(cards)}))
    assert p['courses']['CS3214']['new_today'] == 40
    assert p['days'][0]['new'] == 40


def test_lessons_paced_before_the_buffer():
    ls = [lesson(n) for n in range(1, 5)]
    p = planner.plan(state([goal(date='2026-11-11')], ls))
    per_day = [len(d['lessons']) for d in p['days']]
    assert per_day == [1, 1, 1, 1, 0, 0, 0]  # 4 lessons in the 7 days before the 3-day buffer
    assert not p['goals'][0]['cram']
    assert p['push'] == []  # nothing done yet: cards wait for the lessons


def test_done_lesson_releases_its_cards():
    ls = [lesson(1, done=True), lesson(2)]
    p = planner.plan(state([goal()], ls))
    assert p['push'] == [dict(path='courses/CS3214/lessons/01-l1.md', ungated=False)]


def test_unverified_lesson_is_not_pushed_and_goes_to_inbox():
    p = planner.plan(state([goal()], [lesson(1, done=True, verified=False)]))
    assert p['push'] == []
    assert any('verification' in i for i in p['inbox'])


def test_too_many_lessons_means_cram():
    ls = [lesson(n) for n in range(1, 7)]
    p = planner.plan(state([goal(date='2026-11-05')], ls))  # 4 days, buffer 3: no normal window
    g = p['goals'][0]
    assert g['cram']
    assert [len(d['lessons']) for d in p['days']][:4] == [2, 2, 1, 1]  # packed until the day before
    assert all(x['ungated'] for x in p['push']) and len(p['push']) == 6
    assert '• Cram: CS3214 Test2 (4 days left)' in p['days'][0]['description']
    assert p['prep'] == []  # the prep deck only for the final 2 days


def test_forced_cram():
    p = planner.plan(state([goal(date='2026-12-20', cram=True)], [lesson(1)]))
    assert p['goals'][0]['cram'] and p['push'][0]['ungated']


def test_backlog_stops_new_cards_unless_urgent():
    cards = [card(i, goals=['Test2']) for i in range(30)] + [card(100, type_=2, queue=2, due=TODAY_NUM - 3)]
    calm = planner.plan(state([goal(date='2026-11-20')], courses={'CS3214': course(cards)}))
    assert calm['courses']['CS3214']['new_per_day'] == 0 and calm['courses']['CS3214']['overdue'] == 1
    assert any('overdue' in w for w in calm['warnings'])
    urgent = planner.plan(state([goal(date='2026-11-03')], courses={'CS3214': course(cards)}))
    assert urgent['courses']['CS3214']['new_per_day'] > 0


def test_two_deadlines_that_fit_get_no_warning():
    a = [card(i, goals=['Test2']) for i in range(180)]
    b = [card(1000 + i, course='CS4804', goals=['Exam2']) for i in range(190)]
    p = planner.plan(state([goal(date='2026-11-11'), goal('Exam2', 'CS4804', '2026-11-21')],
                           courses={'CS3214': course(a), 'CS4804': course(b, code='CS4804')}))
    assert p['warnings'] == [] and not any(g['cram'] for g in p['goals'])
    assert max(d['cards'] for d in p['days']) <= 150


def test_a_deadline_is_spread_instead_of_piling_up_at_the_end():
    """The CMDA3654 case: 131 cards, quiz in 3 days. Not 27 today and 104 tomorrow."""
    cards = [card(i, course='CMDA3654', goals=['Quiz3']) for i in range(131)]
    p = planner.plan(state([goal('Quiz3', 'CMDA3654', '2026-11-04')],
                           courses={'CMDA3654': course(cards, code='CMDA3654')}))
    new = [d['new'] for d in p['days'][:3]]
    assert sum(new[:2]) == 131 and abs(new[0] - new[1]) <= 3 and new[2] == 0  # it fits: the day before stays free
    assert p['warnings'] == []


def test_new_cards_go_around_a_busy_day():
    due = [card(500 + i, type_=2, queue=2, due=TODAY_NUM + 1) for i in range(60)]  # 60 reviews tomorrow
    cards = [card(i, goals=['Test2']) for i in range(60)] + due
    p = planner.plan(state([goal(date='2026-11-05')], courses={'CS3214': course(cards)}))
    new = [d['new'] for d in p['days'][:3]]
    assert sum(new) == 60 and new[1] < new[0] and new[1] < new[2]


def test_cards_that_dont_fit_go_over_the_cap_with_a_warning():
    cards = [card(i, goals=['Test2']) for i in range(180)]
    p = planner.plan(state([goal(date='2026-11-11')], courses={'CS3214': course(cards)}, cards_per_day=20))
    g = p['goals'][0]
    assert g['cram']  # they don't fit at the normal pace
    w = next(w for w in p['warnings'] if w.startswith('CS3214 Test2: its cards'))
    assert "don't fit in cards_per_day (20)" in w and 'Raise cards_per_day to about' in w
    assert all(d['cards'] > 20 for d in p['days'])  # over the cap every day, since 180 in 9 days can't fit


def test_simulated_review_load_shapes_the_placement():
    cards = [card(i, goals=['Test2']) for i in range(40)]
    load = {'CS3214': [0, 0, 140] + [0] * 87}  # the simulator expects 140 reviews in 2 days
    p = planner.plan(state([goal(date='2026-11-04')], courses={'CS3214': course(cards)},
                           forecast=dict(source='anki', per_new_card=NEXT_DAY, load=load)))
    new = [d['new'] for d in p['days'][:3]]
    assert new[0] + new[1] == 40 and new[1] <= 10  # tomorrow's cards come back that day: 150 - 140
    assert max(d['cards'] for d in p['days']) <= 150 and p['warnings'] == [] and p['load']['source'] == 'anki'


def test_without_the_simulator_the_default_curve_is_used_and_noted():
    p = planner.plan(state(forecast=dict(source='unavailable', reason='unsupported action')))
    assert p['load']['source'] == 'default' and 3.5 < p['load']['reviews_per_new_card_30_days'] < 5
    assert any('anki/apply.py' in w for w in p['warnings'])


def test_old_budget_keys_are_reported():
    s = state()
    s['config_set'] = ['anki_minutes_per_day']
    assert any(w.startswith('studious/config.md sets anki_minutes_per_day') for w in planner.plan(s)['warnings'])


def test_overlapping_goals_count_the_union_once():
    shared = [card(i, goals=['Test2', 'Final']) for i in range(40)]
    later = [card(100 + i, goals=['Final']) for i in range(60)]
    p = planner.plan(state([goal(date='2026-11-06'), goal('Final', date='2026-11-21')],
                           courses={'CS3214': course(shared + later)}))
    by_day = [d['new'] for d in p['days']]
    assert sum(by_day[:4]) >= 40  # Test2's 40 by two days before it, since they fit
    assert sum(by_day) < 100  # the Final's 60 more are spread over its 19 days, not all this week
    assert sum(g['unseen'] for g in p['goals']) == 140 and p['courses']['CS3214']['new_per_day'] <= 15


def test_reposition_puts_scope_first_interleaving_lessons():
    cards = [card(1, deck='CS3214::L02'), card(2, deck='CS3214::L01', goals=['Test2']),
             card(3, deck='CS3214::L01', goals=['Test2']), card(4, deck='CS3214::L02', goals=['Test2'])]
    ls = [lesson(1, pushed=True), lesson(2, pushed=True)]
    p = planner.plan(state([goal()], ls, courses={'CS3214': course(cards)}))
    assert p['courses']['CS3214']['reposition'] == [2, 4, 3, 1]


def test_a_zero_cap_warns_instead_of_failing():
    cards = [card(i, goals=['Test2']) for i in range(40)]
    p = planner.plan(state([goal(date='2026-11-11')], courses={'CS3214': course(cards)}, cards_per_day=0))
    assert sum(d['new'] for d in p['days']) > 0  # the exam's cards still go in
    assert any('Raise cards_per_day to about' in w for w in p['warnings'])


def test_event_text_format():
    cards = [card(i, goals=['Test2']) for i in range(90)]
    ls = [lesson(7, cards=0)]
    ls[0]['title'] = 'conditionvariables'
    p = planner.plan(state([goal(date='2026-11-11')], ls,
                           courses={'CS3214': course(cards, counts=dict(new=0, learning=1, review=4))}))
    d = p['days'][0]
    n = d['new'] + 5  # new + 5 due
    assert d['title'] == f'[studious] Daily quota: {n} cards · 1 lesson' and d['cards'] == n
    assert d['description'].split('\n')[:4] == ['Today', f'• Anki: {n} cards', '• Lesson:',
                                                '\u00a0' * 4 + '- conditionvariables']
    assert 'obsidian://' not in d['description']
    empty = p['days'][-1]
    assert '• Lessons: none' in empty['description'] and empty['title'].endswith('· 0 lessons')


def test_event_text_lists_several_lessons_under_one_header():
    ls = [lesson(n, cards=0) for n in (7, 8, 9, 10)]
    p = planner.plan(state([goal(date='2026-11-04')], ls, max_lessons_per_day=2, lesson_buffer_days=0))
    d = p['days'][0]
    assert d['title'].endswith('· 2 lessons')
    assert d['description'].split('\n')[2:5] == ['• Lessons:', '\u00a0' * 4 + '- lesson7', '\u00a0' * 4 + '- lesson8']
    assert '• Lesson:' not in d['description']


def test_future_days_count_due_reviews():
    cards = [card(1, type_=2, queue=2, due=TODAY_NUM + 2), card(2, type_=2, queue=2, due=TODAY_NUM + 2)]
    p = planner.plan(state(courses={'CS3214': course(cards)}))
    assert [d['due'] for d in p['days'][:3]] == [0, 0, 2]


def test_no_new_cards_for_a_goal_on_its_exam_day():
    cards = [card(i, goals=['Test2']) for i in range(40)]
    p = planner.plan(state([goal(date='2026-11-01', cram=True)], courses={'CS3214': course(cards)}))
    c = p['courses']['CS3214']
    assert p['goals'][0]['required_new_per_day'] == 0
    assert c['new_per_day'] == 0 and c['new_today'] is None  # not even the casual 5: they'd be Test2 cards
    assert [d['name'] for d in p['prep']] == ['CS3214::Test2 prep']  # its unseen cards, that morning
    assert '• Prep: CS3214 Test2, 40 cards (today, cram mode)' in p['days'][0]['description']


def test_exam_day_course_keeps_its_other_goals_pace_in_their_order():
    cards = [card(i, goals=['Test2']) for i in range(20)] + [card(100 + i, goals=['Final']) for i in range(30)]
    p = planner.plan(state([goal(date='2026-11-01'), goal('Final', date='2026-11-11')],
                           courses={'CS3214': course(cards)}))
    c = p['courses']['CS3214']
    assert 2 <= c['new_per_day'] <= 5 and c['new_today'] is None  # about 30 / 9; no today-only limit
    assert c['reposition'][:30] == [100 + i for i in range(30)]  # the Final's cards first, not Test2's


def test_exam_day_does_not_skip_the_backlog_or_the_budget():
    overdue = [card(500 + i, type_=2, queue=2, due=TODAY_NUM - 1) for i in range(50)]
    cards = [card(i, goals=['Test2']) for i in range(40)] + [card(100 + i, goals=['Final']) for i in range(90)]
    p = planner.plan(state([goal(date='2026-11-01'), goal('Final', date='2026-11-11')],
                           courses={'CS3214': course(cards + overdue)}))
    assert p['courses']['CS3214']['new_per_day'] == 0  # overdue reviews first, exam or not
    assert any('50 overdue reviews' in w for w in p['warnings'])
    busy = course(cards, counts=dict(new=0, learning=0, review=25))
    p = planner.plan(state([goal(date='2026-11-01'), goal('Final', date='2026-11-11')],
                           courses={'CS3214': busy}, cards_per_day=30))
    assert p['courses']['CS3214']['new_per_day'] <= 5  # 25 reviews today leave room for 5
    assert not p['goals'][1]['cram']  # the Final's 90 still fit over its other days


def test_forecast_rests_a_course_on_its_exam_day():
    cards = [card(i, goals=['Test2']) for i in range(10)] + [card(100 + i) for i in range(90)]
    p = planner.plan(state([goal(date='2026-11-03')], courses={'CS3214': course(cards)}))
    assert [d['new'] for d in p['days'][:4]] == [10, 5, 0, 5]  # countdown, casual, exam day, casual


def test_new_cards_come_back_the_next_day():
    cards = [card(i, goals=['Test2']) for i in range(40)]
    p = planner.plan(state([goal(date='2026-11-02')], courses={'CS3214': course(cards)}))
    assert p['days'][0]['new'] == 40 and p['days'][1]['due'] == 40


def test_deterministic():
    cards = [card(i, goals=['Test2']) for i in range(50)]
    s = state([goal()], [lesson(n) for n in range(1, 5)], courses={'CS3214': course(cards)})
    assert json.dumps(planner.plan(s)) == json.dumps(planner.plan(copy.deepcopy(s)))


# ---- timetable ---------------------------------------------------------------------------------
tspec = importlib.util.spec_from_file_location('timetable', Path(__file__).resolve().parent.parent / 'scripts/timetable.py')
timetable = importlib.util.module_from_spec(tspec)
tspec.loader.exec_module(timetable)


def test_timetable_rows_now_and_sessions():
    import datetime as dt
    from zoneinfo import ZoneInfo
    noon = int(dt.datetime(2026, 10, 30, 12, tzinfo=ZoneInfo('America/New_York')).timestamp() * 1000)
    seen = card(1, deck='CS3214::L01', goals=['Test2'], type_=2)
    seen['retrievability'] = 0.8
    cards = [seen, card(2, deck='CS3214::L01', goals=['Test2']), card(3, deck='CS3214::L02', goals=['Test2'])]
    c = course(cards)
    c['revlog'] = [dict(id=noon, cardId=1, ease=3, kind=1, ms=4000), dict(id=noon + 1, cardId=1, ease=1, kind=1, ms=4000)]
    s = state([goal()], [lesson(1, pushed=True, done=True), lesson(2, pushed=True)], courses={'CS3214': c},
              timezone='America/New_York')
    md = timetable.render(s, s['goals'][0])
    assert md.count('<tr>') == 3  # header + 2 topics
    assert '80% · 1/2' in md and 'rgba(210,153,34,.40)' in md  # amber now for L01
    assert 'unseen · 0/1' in md
    assert '<th>Oct 30</th>' in md and '>2 reviews<' in md and '>50%<' not in md  # too few to color
    c['revlog'].append(dict(id=noon + 2, cardId=1, ease=3, kind=1, ms=4000))
    assert '>67%<' in timetable.render(s, s['goals'][0])  # three reviews: colored, one Again of three
    assert '✓' in md
    assert 'cram:' not in md  # no plan, no verdict
    assert timetable.filename(s['goals'][0]) == 'CS3214-Test2 timetable.md'  # not the goal note's name
    p = planner.plan(s)
    md = timetable.render(s, s['goals'][0], p['goals'][0])
    assert 'cram: false' in md and 'lessons_left: 1' in md and 'date: 2026-11-11' in md
    assert f"cards_left: {p['goals'][0]['unseen']}" in md
    assert 'new_cards_today:' in md and 'minutes' not in md
    assert '"[[courses/CS3214/lessons/01-l1|lesson1]]"' in md and 'goal_note: "[[goals/CS3214-Test2|' in md
    assert 'group:' not in md  # no group, no property
    grouped = dict(s['goals'][0], group='Fall-2026', path='goals/Fall-2026/CS3214-Test2.md')
    md = timetable.render(s, grouped)
    assert 'group: "Fall-2026"' in md and 'goal_note: "[[goals/Fall-2026/CS3214-Test2|' in md
    assert timetable.filename(grouped) == 'CS3214-Test2 timetable.md'  # timetables stay flat


def test_done_course_gets_no_new_cards():
    cards = [card(i) for i in range(30)]
    s = state(courses={'CS3214': course(cards)})
    s['config']['courses'] = {'CS3214': {'folders': ['cs3214'], 'done': True}}
    p = planner.plan(s)
    assert p['courses']['CS3214']['new_per_day'] == 0 and p['courses']['CS3214']['done']


def test_retired_course_is_suspended_and_left_out():
    cards = [card(1), card(2, type_=2), card(3)]
    cards[2]['suspended'] = True  # suspended by hand before retiring: not studious's to undo later
    s = state([goal()], [lesson(1, done=True), lesson(2)], courses={'CS3214': course(cards)})
    s['config']['courses'] = {'CS3214': {'folders': ['cs3214'], 'retired': True}}
    p = planner.plan(s)
    assert p['suspend'] == [1, 2] and p['unsuspend'] == [] and p['retired'] == {'CS3214': [1, 2]}
    assert p['courses']['CS3214']['retired'] and p['courses']['CS3214']['new_per_day'] == 0
    assert p['push'] == [] and p['goals'] == [] and not any(d['lessons'] for d in p['days'])
    assert p['prep_delete'] == ['CS3214::Test2 cram', 'CS3214::Test2 prep'] and not p['inbox']


def test_unretiring_unsuspends_only_what_studious_suspended():
    cards = [card(1), card(2), card(3)]
    for c in cards:
        c['suspended'] = True
    s = state(courses={'CS3214': course(cards)})
    s['retired'] = {'CS3214': [1, 2]}
    p = planner.plan(s)
    assert p['unsuspend'] == [1, 2] and p['suspend'] == [] and p['retired'] == {}


def test_inbox_suggests_done_after_the_final():
    g = goal('Final', date='2026-10-20', title='CS3214 Final Exam')
    p = planner.plan(state([g]))
    assert any('mark it `done: true`' in item for item in p['inbox'])


def test_cram_schedules_every_lesson_over_the_cap():
    ls = [lesson(n, cards=2) for n in range(1, 10)]
    p = planner.plan(state([goal(date='2026-11-03')], lessons=ls))
    days = [len(d['lessons']) for d in p['days'][:3]]
    assert days == [5, 4, 0]
    assert any('all are scheduled' in w for w in p['warnings'])


def carried(pushed=3, tags=('Test2',)):
    """Lesson 1 was pushed during the Test2 cram, then carried into the Final; Test2 is over."""
    l = lesson(1, goals=('Test2', 'Final'), pushed=True)
    for c in l['cards'][pushed:]:
        c['guid'] = None
    cards = [card(i + 1, guid=c['guid'], goals=tags, type_=2, due=TODAY_NUM + 30)
             for i, c in enumerate(l['cards']) if c['guid']]
    return state([goal(date='2026-10-30'), goal('Final', date='2026-12-10')], [l], {'CS3214': course(cards)})


def test_carried_lesson_is_pushed_again_to_retag_its_cards():
    p = planner.plan(carried())
    assert p['push'] == [dict(path='courses/CS3214/lessons/01-l1.md', ungated=False)]


def test_lesson_with_matching_tags_is_not_pushed_again():
    p = planner.plan(carried(tags=('Test2', 'Final')))
    assert p['push'] == []


def test_retagging_an_unread_lesson_does_not_release_its_held_cards():
    p = planner.plan(carried(pushed=2))
    assert p['push'] == [dict(path='courses/CS3214/lessons/01-l1.md', ungated=False)]
    assert p['days'][0]['new'] == 0


def test_edited_card_in_anki_is_pushed_again():
    l = lesson(1, done=True, pushed=True)
    for c in l['cards']:
        c['hash'] = 'h1'
    cards = [card(i + 1, guid=c['guid'], goals=['Test2'], type_=2, due=TODAY_NUM + 30) for i, c in enumerate(l['cards'])]
    s = state([goal()], [l], {'CS3214': course(cards)})
    s['pushed'] = {c['guid']: 'h1' for c in l['cards']}
    assert planner.plan(s)['push'] == []
    s['pushed'][l['cards'][0]['guid']] = 'h0'  # the block changed since its last push
    assert planner.plan(s)['push'] == [dict(path='courses/CS3214/lessons/01-l1.md', ungated=False)]
    del s['pushed'][l['cards'][0]['guid']]  # never recorded (pushed before the record existed)
    assert planner.plan(s)['push'] == [dict(path='courses/CS3214/lessons/01-l1.md', ungated=False)]


def test_today_only_limit_is_never_below_the_daily_limit():
    cards = [card(i, goals=['Quiz']) for i in range(4)] + [card(100 + i, goals=['Exam']) for i in range(40)]
    p = planner.plan(state([goal('Quiz', date='2026-11-02'), goal('Exam', date='2026-11-06')],
                           courses={'CS3214': course(cards)}))
    c = p['courses']['CS3214']
    assert c['new_per_day'] > 4 and c['new_today'] == c['new_per_day']  # the quiz's 4 plus some of the exam's


def test_unverified_lessons_are_not_put_on_the_calendar():
    p = planner.plan(state([goal()], [lesson(1, verified=False), lesson(2)]))
    assert [l['path'] for d in p['days'] for l in d['lessons']] == ['courses/CS3214/lessons/02-l2.md']
    assert p['goals'][0]['lessons_left'] == ['courses/CS3214/lessons/01-l1.md', 'courses/CS3214/lessons/02-l2.md']


def test_a_later_deadline_waits_out_a_nearer_one():
    urgent = [card(i, goals=['Test2']) for i in range(40)]
    later = [card(100 + i, course='MATH', goals=['Final']) for i in range(60)]
    p = planner.plan(state([goal(date='2026-11-02'), goal('Final', course='MATH', date='2026-11-11')],
                           courses={'CS3214': course(urgent), 'MATH': course(later, code='MATH')}))
    new = [d['new'] for d in p['days'][:4]]
    assert p['courses']['CS3214']['new_today'] == 40  # all of Test2's today, the day before
    assert p['courses']['MATH']['new_per_day'] < new[2]  # MATH steps around today's 40 and their reviews


def test_cram_warning_names_nearer_deadlines_when_they_fill_the_day():
    ls = [lesson(n) for n in range(1, 4)] + [lesson(n, course='MATH', goals=('Q',)) for n in range(1, 5)]
    p = planner.plan(state([goal(date='2026-11-02'), goal('Q', course='MATH', date='2026-11-05')], ls))
    lesson_warnings = [w for w in p['warnings'] if 'lesson' in w]
    assert any(w.startswith('CS3214 Test2: 3 lessons left in 1 day') for w in lesson_warnings)
    assert any(w.startswith('MATH Q:') and 'nearer deadlines' in w for w in lesson_warnings)


def test_timetable_of_a_past_goal_says_days_ago():
    s = state([goal(date='2026-10-29')], [lesson(1)])
    md = timetable.render(s, s['goals'][0])
    assert '3 days ago' in md and 'date: 2026-10-29' in md


def test_the_day_before_counts_toward_fitting_under_the_cap():
    """90 cards, exam in 3 days, 60 a day: two days can't hold them, so the day before takes the rest."""
    cards = [card(i, goals=['Test2']) for i in range(90)]
    p = planner.plan(state([goal(date='2026-11-04')], courses={'CS3214': course(cards)}, cards_per_day=60))
    assert sum(d['new'] for d in p['days'][:3]) == 90 and p['days'][2]['new'] > 0
    assert p['warnings'] == [] and not p['goals'][0]['cram']


# ---- orphans: notes whose card block is gone from the vault ----------------------------------------

def owned(c, yes=True):
    c['owned'] = yes  # snapshot marks the cards studious made
    return c


def orphan_state(cards, lessons=None, vault_guids=(), **kw):
    s = state(lessons=lessons if lessons is not None else [lesson(1)],
              courses={'CS3214': dict(course(cards), root='Fall-2026::CS3214')})
    s.update({**dict(orphaned={}, orphaned_cards={}), **kw, 'vault_guids': list(vault_guids)})
    return s


def restored_lesson(guid, verified=True):
    l = lesson(1, verified=verified)
    l['cards'][0]['guid'] = guid
    return l


def test_a_note_without_a_block_is_suspended_and_recorded():
    gone, kept = 'cs3214_aaaaaaaaaaaa', 'cs3214_bbbbbbbbbbbb'
    cards = [owned(card(1, guid=gone, deck='Fall-2026::CS3214::L01')), owned(card(2, guid=gone)),
             owned(card(3, guid=kept)), owned(card(4, guid='Xy$hand-made'), False),
             owned(card(5, guid='cs3214_cccccccccccc')), owned(card(6, guid='old-guid'))]
    cards[4]['suspended'] = True  # suspended by hand: left alone, never recorded
    p = planner.plan(orphan_state(cards, vault_guids=[kept]))
    assert p['orphans']['suspend'] == [1, 2, 6] and p['orphans']['unsuspend'] == []
    assert p['orphaned'] == {gone: dict(course='CS3214', cardIds=[1, 2]), 'old-guid': dict(course='CS3214', cardIds=[6])}
    assert p['orphans']['new'][0] == dict(guid=gone, course='CS3214', deck='Fall-2026::CS3214::L01', cardIds=[1, 2])
    assert p['courses']['CS3214']['new_per_day'] == 2  # planned as suspended: only cards 3 and 4 are new


def test_a_card_moved_to_another_lesson_or_course_is_not_an_orphan():
    moved = 'cs3214_aaaaaaaaaaaa'
    s = orphan_state([owned(card(1, guid=moved))], vault_guids=[moved])  # its block now sits in a CS4824 lesson
    p = planner.plan(s)
    assert p['orphans'] == dict(suspend=[], unsuspend=[], new=[], restored={}) and p['orphaned'] == {}


def test_a_block_back_in_a_verified_lesson_unsuspends_what_studious_suspended():
    back = 'cs3214_aaaaaaaaaaaa'
    entry = dict(course='CS3214', cardIds=[1])
    now = {back: [dict(cardId=1, deck='Fall-2026::CS3214::L01', suspended=True),
                  dict(cardId=2, deck='Fall-2026::CS3214::L01', suspended=True)]}  # card 2 suspended by hand
    s = orphan_state([], lessons=[restored_lesson(back)], vault_guids=[back], orphaned={back: entry}, orphaned_cards=now)
    p = planner.plan(s)
    assert p['orphans']['unsuspend'] == [1] and p['orphaned'] == {} and p['orphans']['restored'] == {back: entry}
    s['lessons'] = [restored_lesson(back, verified=False)]  # back, but in a lesson being rewritten: waits
    p = planner.plan(s)
    assert p['orphans']['unsuspend'] == [] and back in p['orphaned']


def test_a_restored_card_moved_out_of_the_course_decks_by_hand_still_comes_back():
    back = 'cs3214_aaaaaaaaaaaa'
    s = orphan_state([], lessons=[restored_lesson(back)], vault_guids=[back],
                     orphaned={back: dict(course='CS3214', cardIds=[1])},
                     orphaned_cards={back: [dict(cardId=1, deck='Parking', suspended=True)]})
    assert planner.plan(s)['orphans']['unsuspend'] == [1]


def test_a_recorded_note_is_dropped_only_once_anki_says_it_is_gone():
    gone = {'cs3214_aaaaaaaaaaaa': dict(course='CS3214', cardIds=[1])}
    assert planner.plan(orphan_state([], orphaned=gone, orphaned_cards={}))['orphaned'] == {}
    assert planner.plan(orphan_state([], orphaned=gone, orphaned_cards=None))['orphaned'] == gone  # Anki couldn't say


def test_a_restored_card_in_a_retired_course_deck_stays_suspended():
    back = 'cs3214_aaaaaaaaaaaa'
    s = orphan_state([], lessons=[restored_lesson(back, ), lesson(2, course='CS2505')], vault_guids=[back],
                     orphaned={back: dict(course='CS2505', cardIds=[1])},
                     orphaned_cards={back: [dict(cardId=1, deck='Fall-2025::CS2505::L01', suspended=True)]})
    s['courses']['CS2505'] = dict(course([], code='CS2505'), root='Fall-2025::CS2505')
    s['config']['courses'] = {'CS2505': {'retired': True}}
    p = planner.plan(s)
    assert p['orphans']['unsuspend'] == [] and back in p['orphaned']


def test_a_course_with_no_lessons_at_all_is_held_with_a_warning():
    p = planner.plan(orphan_state([owned(card(1, guid='cs3214_aaaaaaaaaaaa'))], lessons=[]))
    assert p['orphans']['suspend'] == [] and p['orphaned'] == {}
    assert any('no lesson of CS3214 was found' in w for w in p['warnings'])


def test_orphans_of_a_retired_course_are_left_to_the_retired_rule():
    s = orphan_state([owned(card(1, guid='cs3214_aaaaaaaaaaaa'))])
    s['config']['courses'] = {'CS3214': {'retired': True}}
    p = planner.plan(s)
    assert p['orphans']['suspend'] == [] and p['suspend'] == [1]


def test_unretiring_keeps_an_orphan_suspended():
    gone = 'cs3214_aaaaaaaaaaaa'
    cards = [owned(card(1, guid=gone)), owned(card(2, guid='cs3214_bbbbbbbbbbbb'))]
    for c in cards:
        c['suspended'] = True
    s = orphan_state(cards, vault_guids=['cs3214_bbbbbbbbbbbb'], retired={'CS3214': [1, 2]},
                     orphaned={gone: dict(course='CS3214', cardIds=[1])},
                     orphaned_cards={gone: [dict(cardId=1, deck='CS3214::L01', suspended=True)]})
    p = planner.plan(s)
    assert p['unsuspend'] == [2] and p['orphaned'] == {gone: dict(course='CS3214', cardIds=[1])}


def test_a_state_without_vault_guids_detects_nothing():
    s = orphan_state([owned(card(1, guid='cs3214_aaaaaaaaaaaa'))])
    del s['vault_guids']
    assert planner.plan(s)['orphans']['suspend'] == []


# ---- prep decks ----------------------------------------------------------------------------------

UNSEEN = 'deck:"CS3214" tag:CS3214::exam::Test2 is:new -is:suspended'


def test_recall_follows_the_fsrs_forgetting_curve():
    assert abs(planner.recall_after(0.9, 10, 10, decay=0.5) - (1 + 19 / 81 * 2) ** -0.5) < 1e-9  # 0.825
    assert round(planner.recall_after(0.9, 10, 10, decay=0.5), 3) == 0.825
    assert round(planner.recall_after(0.9, 10, 0), 3) == 0.9  # today stays today
    assert planner.recall_after(0.95, 30, 3) < 0.95 and planner.recall_after(None, 30, 3) is None
    assert planner.recall_after(0.9, 0, 3) is None


def test_every_goal_gets_a_prep_deck_of_cards_below_target_on_exam_day():
    cards = [reviewed(1, 0.91, 2), reviewed(2, 0.99, 400), reviewed(3, 0.80, 5), reviewed(4, 0.85, 3, goals=['Other']),
             card(5, goals=['Test2']), dict(reviewed(6, 0.5, 1), suspended=True), dict(reviewed(7, 0.6, 1), type=1)]
    p = planner.plan(state([goal(date='2026-11-03')], courses={'CS3214': course(cards)}))
    assert not p['goals'][0]['cram']
    assert p['prep'] == [dict(name='CS3214::Test2 prep', search='cid:3,1', limit=2, order=8, goal='Test2',
                              course='CS3214', weak=2, unseen=0, cards=2)]  # weakest first; no new, suspended or learning
    assert 'CS3214::Test2 prep' not in p['prep_delete'] and 'CS3214::Test2 cram' in p['prep_delete']
    assert '• Prep: CS3214 Test2, 2 cards (2 days left)' in p['days'][0]['description']
    assert '• Prep: CS3214 Test2 (1 day left)' in p['days'][1]['description']


def test_an_empty_prep_window_promises_no_deck_on_later_days():
    p = planner.plan(state([goal(date='2026-11-03')], courses={'CS3214': course([reviewed(1, 0.99, 300)])}))
    assert not any('Prep:' in d['description'] for d in p['days'])


def test_prep_deck_waits_for_the_last_prep_days():
    p = planner.plan(state([goal(date='2026-11-04')], courses={'CS3214': course([reviewed(1, 0.8, 2)])}))
    assert p['prep'] == [] and 'CS3214::Test2 prep' in p['prep_delete']
    assert 'Prep:' not in p['days'][0]['description'] and '• Prep: CS3214 Test2 (2 days left)' in p['days'][1]['description']


def test_cram_mode_adds_the_unseen_cards_after_the_weak_ones():
    cards = [reviewed(1, 0.8, 5), reviewed(2, 0.99, 100)] + [card(10 + i, goals=['Test2']) for i in range(5)]
    p = planner.plan(state([goal(date='2026-11-03', cram=True)], courses={'CS3214': course(cards)}))
    assert p['prep'] == [dict(name='CS3214::Test2 prep', search='cid:1', limit=1, order=8, search2=UNSEEN,
                              limit2=9999, order2=6, goal='Test2', course='CS3214', weak=1, unseen=5, cards=6)]
    assert '• Prep: CS3214 Test2, 6 cards (2 days left, cram mode)' in p['days'][0]['description']
    only_new = planner.plan(state([goal(date='2026-11-03', cram=True)], courses={'CS3214': course(cards[2:])}))
    assert only_new['prep'][0]['search'] == UNSEEN and only_new['prep'][0]['order'] == 6
    calm = planner.plan(state([goal(date='2026-11-03')], courses={'CS3214': course(cards)}))
    assert 'search2' not in calm['prep'][0]  # out of cram mode: the weak cards only


def test_prep_deck_sits_inside_a_grouped_course_deck():
    courses = {'CS3214': dict(course([reviewed(1, 0.8, 5)] + [card(9, goals=['Test2'])]), root='Fall-2026::CS3214')}
    p = planner.plan(state([goal(date='2026-11-03', cram=True)], courses=courses))
    assert p['prep'][0]['name'] == 'Fall-2026::CS3214::Test2 prep'
    assert p['prep'][0]['search2'] == 'deck:"Fall-2026::CS3214" tag:CS3214::exam::Test2 is:new -is:suspended'
    p = planner.plan(state([goal(date='2026-11-03', status='archived')], courses=courses))
    assert p['prep'] == [] and p['prep_delete'] == ['Fall-2026::CS3214::Test2 cram', 'Fall-2026::CS3214::Test2 prep']


def test_nothing_to_prep_is_a_note_not_a_deck():
    p = planner.plan(state([goal(date='2026-11-01')], [lesson(1, done=True, pushed=True)],
                           courses={'CS3214': course([reviewed(1, 0.99, 300)])}))
    assert p['prep'] == [] and p['prep_delete'] == ['CS3214::Test2 cram', 'CS3214::Test2 prep']
    assert p['notes'] == ['CS3214 Test2 prep: nothing below 0.90 on exam day.']
    assert 'Prep:' not in p['days'][0]['description']


def test_archived_done_or_past_goals_lose_their_decks():
    cards = [reviewed(1, 0.5, 2)]
    for kw in (dict(status='archived'), dict(status='done'), dict(date='2026-10-30')):
        p = planner.plan(state([goal(**{'date': '2026-11-01', 'cram': True, **kw})], courses={'CS3214': course(cards)}))
        assert p['prep'] == [] and p['prep_delete'] == ['CS3214::Test2 cram', 'CS3214::Test2 prep']


def test_cram_days_still_works_with_a_warning():
    s = state([goal(date='2026-11-04')], courses={'CS3214': course([reviewed(1, 0.8, 2)])})
    s['config'] = {k: v for k, v in s['config'].items() if k != 'prep_days'} | dict(cram_days=3)
    s['config_set'] = ['cram_days']
    p = planner.plan(s)
    assert [d['name'] for d in p['prep']] == ['CS3214::Test2 prep']  # 3 days out, inside cram_days 3
    assert any('cram_days, which is now prep_days' in w for w in p['warnings'])
    s['config']['prep_days'], s['config_set'] = 2, ['cram_days', 'prep_days']
    p = planner.plan(s)
    assert p['prep'] == [] and any('cram_days is ignored' in w for w in p['warnings'])


def test_exam_recall_target_sets_the_bar():
    cards = [reviewed(1, 0.95, 30)]
    p = planner.plan(state([goal(date='2026-11-03')], courses={'CS3214': course(cards)}))
    assert p['prep'] == []  # about 0.94 on exam day
    p = planner.plan(state([goal(date='2026-11-03')], courses={'CS3214': course(cards)}, exam_recall_target=0.97))
    assert p['prep'][0]['search'] == 'cid:1'


# ---- inbox nudges ------------------------------------------------------------------------------------

def test_new_material_lists_folders_blueprints_first():
    sources = {'CS4824': dict(new={'Fall-2026/cs4824/notes': {'course-material': 10},
                                   'Fall-2026/cs4824/test2': {'exam-blueprint': 4},
                                   'Fall-2026/cs4824/tmp/pdfs': {'course-material': 5, 'exam-blueprint': 1},
                                   'Fall-2026/cs4824/assignment2': {'course-material': 9},
                                   'Fall-2026/cs4824': {'course-material': 1}},
                              changed=2, base=['Fall-2026/cs4824']),
               'CS3214': dict(new={}, changed=1, base=['Fall-2026/cs3214']),
               'CMDA3654': dict(new={'Fall-2026/cmda3654': {'course-material': 2}}, changed=0, base=['Fall-2026/cmda3654'])}
    s = state()
    s['sources'] = sources
    assert planner.plan(s)['inbox'] == [
        'CMDA3654: new material in cmda3654: ingest it?',
        'CS3214: 1 changed source file: update their lessons?',
        'CS4824: new material in tmp/pdfs, test2, notes and 2 more folders, and 2 changed files: ingest it?']


def test_no_material_is_flagged_as_soon_as_the_goal_exists():
    far = planner.plan(state([goal(date='2027-01-15')]))
    assert 'CS3214 Test2 is in 75 days and has no material yet: ingest its notes.' in far['inbox']
    undated = planner.plan(state([goal(mode='self-paced-casual', date=None)]))
    assert 'CS3214 Test2 has no material yet: ingest its notes.' in undated['inbox']


def test_unseen_cards_with_no_upcoming_goal():
    cards = [card(i, goals=['Test2']) for i in range(4)]
    ls = [lesson(1, cards=3), lesson(2, verified=False, cards=5)]
    p = planner.plan(state([goal(date='2026-10-30')], ls, courses={'CS3214': course(cards)}))
    assert 'CS3214: 7 unseen cards and no upcoming goal: add one?' in p['inbox']  # 4 in Anki + 3 verified unpushed
    later = planner.plan(state([goal(date='2026-10-30'), goal('Final', date='2026-12-15')], ls,
                               courses={'CS3214': course(cards)}))
    assert not any('no upcoming goal' in i for i in later['inbox'])
    casual = planner.plan(state([goal(date='2026-10-30'), goal('Self', mode='self-paced-casual', date=None)], ls,
                                courses={'CS3214': course(cards)}))
    assert not any('no upcoming goal' in i for i in casual['inbox'])
    done = state([goal(date='2026-10-30')], ls, courses={'CS3214': course(cards)})
    done['config']['courses'] = {'CS3214': {'done': True}}
    assert not any('no upcoming goal' in i for i in planner.plan(done)['inbox'])


def test_past_goal_counts_the_cards_never_shown():
    cards = [card(i, goals=['Test2']) for i in range(5)] + [reviewed(9, 0.9, 5)]
    p = planner.plan(state([goal(date='2026-10-30')], [lesson(1, cards=2)], courses={'CS3214': course(cards)}))
    assert ('CS3214 Test2 was on 2026-10-30, 7 cards never shown: carry its lessons into a later goal, '
            'or archive it?') in p['inbox']
    seen = planner.plan(state([goal(date='2026-10-30')], courses={'CS3214': course([reviewed(9, 0.9, 5)])}))
    assert ('CS3214 Test2 was on 2026-10-30: archive it, or carry its unread lessons into a later goal?'
            in seen['inbox'])


def test_a_lesson_left_unread_for_two_days_goes_in_the_inbox():
    ls = [lesson(n) for n in range(1, 5)]
    day1 = planner.plan(state([goal(date='2026-11-11')], ls))
    assert day1['reading'] == {'courses/CS3214/lessons/01-l1.md': '2026-11-01'}
    assert not any('on your list' in i for i in day1['inbox'])
    s = state([goal(date='2026-11-11')], ls, today='2026-11-03')
    s['reading'] = day1['reading']
    day3 = planner.plan(s)
    assert 'lesson1 has been on your list since Nov 1: read it, or push it back?' in day3['inbox']
    assert day3['reading']['courses/CS3214/lessons/01-l1.md'] == '2026-11-01'
    ls[0]['done'] = True
    s = state([goal(date='2026-11-11')], ls, today='2026-11-03')
    s['reading'] = day1['reading']
    assert 'courses/CS3214/lessons/01-l1.md' not in planner.plan(s)['reading']  # read: off the record


def test_last_seven_days_under_half_the_cap_warn():
    import datetime as dt
    from zoneinfo import ZoneInfo
    def at(day_, i):
        return int(dt.datetime(2026, 10, day_, 12, tzinfo=ZoneInfo('America/New_York')).timestamp() * 1000) + i
    c = course([])
    c['revlog'] = [dict(id=at(d, i), cardId=d * 100 + i, ease=3, kind=1, ms=1000) for d in range(25, 32) for i in range(4)]
    s = state(courses={'CS3214': c}, cards_per_day=100, timezone='America/New_York')
    assert 'Last 7 days: about 4 cards a day, against cards_per_day 100.' in planner.plan(s)['warnings']
    assert not any(w.startswith('Last ') for w in planner.plan(dict(s, config={**s['config'], 'cards_per_day': 8}))['warnings'])
    c['revlog'] = [r for r in c['revlog'] if r['id'] >= at(30, 0)]  # a fresh setup: only 2 days of history
    assert 'Last 2 days: about 4 cards a day, against cards_per_day 100.' in planner.plan(s)['warnings']
    c['revlog'] = []
    assert not any(w.startswith('Last ') for w in planner.plan(s)['warnings'])


def test_a_small_backlog_no_longer_stops_new_cards():
    overdue = [card(500 + i, type_=2, queue=2, due=TODAY_NUM - 1) for i in range(5)]
    cards = [card(i) for i in range(30)]
    few = planner.plan(state(courses={'CS3214': course(cards + overdue)}, backlog_overdue_threshold=10))
    assert few['courses']['CS3214']['new_per_day'] == 5
    more = [card(600 + i, type_=2, queue=2, due=TODAY_NUM - 1) for i in range(6)]
    many = planner.plan(state(courses={'CS3214': course(cards + overdue + more)}, backlog_overdue_threshold=10))
    assert many['courses']['CS3214']['new_per_day'] == 0


def test_leeches_are_counted_in_the_inbox():
    cards = [dict(reviewed(1, 0.5, 1), tags=['CS3214::exam::Test2', 'leech']), dict(reviewed(2, 0.5, 1), tags=['Leech']),
             dict(reviewed(3, 0.5, 1), tags=['leech'], suspended=True), reviewed(4, 0.9, 9)]
    p = planner.plan(state([goal()], courses={'CS3214': course(cards)}))
    assert 'CS3214: 2 leeches (cards you keep forgetting): fix them?' in p['inbox']


def test_a_card_goes_to_the_nearest_goals_prep_deck_only():
    cards = [reviewed(1, 0.7, 3, goals=['Test2', 'Final']), reviewed(2, 0.7, 3, goals=['Final'])]
    p = planner.plan(state([goal(date='2026-11-02'), goal('Final', date='2026-11-03')],
                           courses={'CS3214': course(cards)}))
    assert [(d['name'], d['search']) for d in p['prep']] == [('CS3214::Test2 prep', 'cid:1'), ('CS3214::Final prep', 'cid:2')]


def test_an_older_patch_gets_the_weak_cards_as_a_cram_deck_and_a_warning():
    cards = [reviewed(1, 0.8, 5)] + [card(10 + i, goals=['Test2']) for i in range(5)]
    s = state([goal(date='2026-11-03', cram=True)], courses={'CS3214': course(cards)})
    s['patch'] = 10
    p = planner.plan(s)
    assert p['prep'] == [dict(name='CS3214::Test2 cram', search='cid:1', limit=1, goal='Test2', course='CS3214',
                              weak=1, unseen=0, cards=1)]  # no prep name, order or second filter before v11
    assert 'CS3214::Test2 cram' not in p['prep_delete'] and 'CS3214::Test2 prep' in p['prep_delete']
    assert '• Prep: CS3214 Test2, 1 card (2 days left, cram mode)' in p['days'][0]['description']
    assert [w for w in p['warnings'] if 'patch v11' in w] == [
        "Prep decks need the studious patch v11 (this machine's Anki runs v10): only the cards below the exam-day "
        "target went in, as `<goal> cram`, without a cram goal's unseen cards: CS3214 Test2 (5 unseen cards). "
        "In the connectors clone: git pull, python3 anki/apply.py, then restart Anki."]
    s['courses'] = {'CS3214': course(cards[1:])}  # unseen cards only: nothing an older patch can build
    p = planner.plan(s)
    assert p['prep'] == [] and p['notes'] == []  # no "nothing below target and no unseen cards"
    assert any('no prep deck could be built, without a cram goal' in w and '5 unseen cards' in w for w in p['warnings'])
    s['patch'] = 11
    assert not any('patch v11' in w for w in planner.plan(s)['warnings'])


def test_a_leech_note_counts_once():
    cloze = [dict(reviewed(i, 0.5, 1), noteId=50, tags=['leech']) for i in (1, 2, 3)]
    p = planner.plan(state([goal()], courses={'CS3214': course(cloze)}))
    assert 'CS3214: 1 leech (cards you keep forgetting): fix them?' in p['inbox']
