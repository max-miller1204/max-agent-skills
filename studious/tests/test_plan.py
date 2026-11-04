"""Golden tests for scripts/plan.py.  uv run --with pytest pytest tests/test_plan.py"""
import copy
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('plan', Path(__file__).resolve().parent.parent / 'scripts/plan.py')
planner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(planner)

CONFIG = dict(cards_per_day=150, max_lessons_per_day=2,
              lesson_buffer_days=3, cram_days=2, casual_new_per_day=5, budget_new_per_day=10,
              backlog_overdue_threshold=0, calendar_days=7, vault_name='vault')
TODAY_NUM = 1000
NEXT_DAY = [0.0, 1.0] + [0.0] * 88  # test review curve: each new card comes back once, the next day


def card(cid, course='CS3214', deck=None, goals=(), type_=0, due=None, queue=None, guid=None):
    return dict(cardId=cid, noteId=cid, guid=guid or f'g{cid}', deck=deck or f'{course}::L01',
                type=type_, queue=queue if queue is not None else type_, due=due if due is not None else cid,
                reps=0, lapses=0, flag=0, suspended=False, retrievability=None,
                tags=[f'{course}::exam::{g}' for g in goals])


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
    assert p['cram'] == []  # the cram deck only for the final 2 days


def test_cram_deck_in_the_final_days():
    ls = [lesson(n) for n in range(1, 4)]
    p = planner.plan(state([goal(date='2026-11-03')], ls))
    assert p['cram'] == [dict(name='CS3214::Test2 cram', limit=9999,
                              search='deck:"CS3214" tag:CS3214::exam::Test2 prop:r<0.85')]


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


def test_past_goal_asks_and_removes_cram_deck():
    p = planner.plan(state([goal(date='2026-10-30')]))
    assert any('archive it, or carry its unread lessons' in i for i in p['inbox'])
    assert p['cram_delete'] == ['CS3214::Test2 cram']


def test_archived_or_done_goal_loses_its_cram_deck():
    cards = [card(i, goals=['Test2']) for i in range(10)]
    for status in ('archived', 'done'):  # e.g. archived on its exam day, before the next run
        p = planner.plan(state([goal(date='2026-11-01', cram=True, status=status)],
                               courses={'CS3214': course(cards)}))
        assert p['cram'] == [] and p['cram_delete'] == ['CS3214::Test2 cram']


def test_a_zero_cap_warns_instead_of_failing():
    cards = [card(i, goals=['Test2']) for i in range(40)]
    p = planner.plan(state([goal(date='2026-11-11')], courses={'CS3214': course(cards)}, cards_per_day=0))
    assert sum(d['new'] for d in p['days']) > 0  # the exam's cards still go in
    assert any('Raise cards_per_day to about' in w for w in p['warnings'])


def test_goal_without_material_reminds_within_21_days():
    p = planner.plan(state([goal(date='2026-11-15')]))
    assert any('no material yet' in i for i in p['inbox'])
    far = planner.plan(state([goal(date='2027-01-15')]))
    assert not any('no material' in i for i in far['inbox'])


def test_event_text_format():
    cards = [card(i, goals=['Test2']) for i in range(90)]
    ls = [lesson(7, cards=0)]
    ls[0]['title'] = 'conditionvariables'
    p = planner.plan(state([goal(date='2026-11-11')], ls,
                           courses={'CS3214': course(cards, counts=dict(new=0, learning=1, review=4))}))
    d = p['days'][0]
    n = d['new'] + 5  # new + 5 due
    assert d['title'] == f'[studious] Daily quota: {n} cards · 1 lesson' and d['cards'] == n
    assert d['description'].split('\n')[:3] == ['Today', f'• Anki: {n} cards', '• Lesson: conditionvariables']
    assert 'obsidian://' not in d['description']
    empty = p['days'][-1]
    assert '• Lesson: none' in empty['description'] and empty['title'].endswith('· 0 lessons')


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
    assert [d['name'] for d in p['cram']] == ['CS3214::Test2 cram']  # weak cards to look over that morning
    assert '• Cram: CS3214 Test2 (today)' in p['days'][0]['description']


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


def test_on_pace_goal_gets_no_cram_deck_on_its_exam_day():
    p = planner.plan(state([goal(date='2026-11-01')], [lesson(1, done=True, pushed=True)]))
    assert p['cram'] == [] and p['cram_delete'] == ['CS3214::Test2 cram']


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
    assert '<th>Oct 30</th>' in md and '>50%<' in md  # one Again out of two reviews
    assert '✓' in md
    assert 'cram:' not in md  # no plan, no verdict
    assert timetable.filename(s['goals'][0]) == 'CS3214-Test2 timetable.md'  # not the goal note's name
    p = planner.plan(s)
    md = timetable.render(s, s['goals'][0], p['goals'][0])
    assert 'cram: false' in md and 'lessons_left: 1' in md and 'date: 2026-11-11' in md
    assert f"cards_left: {p['goals'][0]['unseen']}" in md
    assert 'new_cards_today:' in md and 'minutes' not in md
    assert '"[[courses/CS3214/lessons/01-l1|lesson1]]"' in md and 'goal_note: "[[goals/CS3214-Test2|' in md


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
    assert p['cram_delete'] == ['CS3214::Test2 cram'] and not p['inbox']


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


def test_cram_deck_is_deleted_once_cram_mode_ends():
    s = state([goal(date='2026-11-02', cram=True)], [lesson(1, done=True, pushed=True)])
    assert [d['name'] for d in planner.plan(s)['cram']] == ['CS3214::Test2 cram']
    s['goals'][0]['cram'] = False
    p = planner.plan(s)
    assert p['cram'] == [] and p['cram_delete'] == ['CS3214::Test2 cram']


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
