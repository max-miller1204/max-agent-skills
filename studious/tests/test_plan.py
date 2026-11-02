"""Golden tests for scripts/plan.py.  uv run --with pytest pytest tests/test_plan.py"""
import copy
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('plan', Path(__file__).resolve().parent.parent / 'scripts/plan.py')
planner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(planner)

CONFIG = dict(anki_minutes_per_day=45, seconds_per_review=10, reviews_per_new_card=10, max_lessons_per_day=2,
              lesson_buffer_days=3, cram_days=2, casual_new_per_day=5, budget_new_per_day=10,
              backlog_overdue_threshold=0, calendar_days=7, vault_name='vault')
TODAY_NUM = 1000


def card(cid, course='CS3214', deck=None, goals=(), type_=0, due=None, queue=None, guid=None):
    return dict(cardId=cid, noteId=cid, guid=guid or f'g{cid}', deck=deck or f'{course}::L01',
                type=type_, queue=queue if queue is not None else type_, due=due if due is not None else cid,
                reps=0, lapses=0, flag=0, suspended=False, retrievability=None,
                tags=[f'{course}::exam::{g}' for g in goals])


def lesson(n, course='CS3214', goals=('Test2',), done=False, verified=True, cards=3, pushed=False):
    return dict(path=f'courses/{course}/lessons/{n:02d}-l{n}.md', title=f'lesson{n}', course=course,
                deck=f'{course}::L{n:02d}', order=n, goals=list(goals), done=done, verified=verified, minutes=40,
                cards=[dict(guid=f'{course}-{n}-{i}' if pushed else None, count=1) for i in range(cards)])


def goal(gid='Test2', course='CS3214', date='2026-11-11', **kw):
    return {**dict(id=gid, course=course, title=f'{course} {gid}', mode='deadline', date=date, status='active',
                   cram=False, path=f'goals/{course}-{gid}.md'), **kw}


def course(cards, counts=None, code='CS3214'):
    return dict(root=code, today=TODAY_NUM, counts=counts or dict(new=0, learning=0, review=0),
                preset=None, revlog=[], cards=cards)


def state(goals=(), lessons=(), courses=None, today='2026-11-01', **cfg):
    return dict(today=today, config={**CONFIG, **cfg}, goals=list(goals), lessons=list(lessons),
                courses=courses or {}, errors=[])


def test_budget_is_derived_from_minutes():
    assert planner.plan(state())['total_new_per_day'] == 27


def test_countdown_sees_every_card_by_the_day_before():
    cards = [card(i, goals=['Test2']) for i in range(90)]
    p = planner.plan(state([goal(date='2026-11-11')], courses={'CS3214': course(cards)}))
    assert p['goals'][0]['required_new_per_day'] == 10  # ceil(90 / (10 - 1))
    assert p['courses']['CS3214']['new_per_day'] == 10
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
    assert p['cram'] == [dict(name='CS3214 Test2 cram', limit=9999,
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


def test_shared_budget_goes_to_the_nearest_deadline():
    a = [card(i, goals=['Test2']) for i in range(180)]                      # 180 / 9 = 20 per day
    b = [card(1000 + i, course='CS4804', goals=['Exam2']) for i in range(190)]  # 190 / 19 = 10 per day
    p = planner.plan(state([goal(date='2026-11-11'), goal('Exam2', 'CS4804', '2026-11-21')],
                           courses={'CS3214': course(a), 'CS4804': course(b, code='CS4804')}))
    assert p['courses']['CS3214']['new_per_day'] == 20
    assert p['courses']['CS4804']['new_per_day'] == 7  # 27 - 20
    assert any('CS4804' in w and 'budget' in w for w in p['warnings'])


def test_overlapping_goals_count_the_union_once():
    shared = [card(i, goals=['Test2', 'Final']) for i in range(40)]
    later = [card(100 + i, goals=['Final']) for i in range(60)]
    p = planner.plan(state([goal(date='2026-11-06'), goal('Final', date='2026-11-21')],
                           courses={'CS3214': course(shared + later)}))
    rates = {g['id']: g['required_new_per_day'] for g in p['goals']}
    assert rates == {'Test2': 10, 'Final': 6}  # 40/4 and (40+60)/19, not 160
    assert p['courses']['CS3214']['new_per_day'] == 10


def test_reposition_puts_scope_first_interleaving_lessons():
    cards = [card(1, deck='CS3214::L02'), card(2, deck='CS3214::L01', goals=['Test2']),
             card(3, deck='CS3214::L01', goals=['Test2']), card(4, deck='CS3214::L02', goals=['Test2'])]
    ls = [lesson(1, pushed=True), lesson(2, pushed=True)]
    p = planner.plan(state([goal()], ls, courses={'CS3214': course(cards)}))
    assert p['courses']['CS3214']['reposition'] == [2, 4, 3, 1]


def test_past_goal_asks_and_removes_cram_deck():
    p = planner.plan(state([goal(date='2026-10-30')]))
    assert any('keep, archive or carry' in i for i in p['inbox'])
    assert p['cram_delete'] == ['CS3214 Test2 cram']


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
    assert d['title'] == '[studious] Daily quota: 15 cards · 1 lesson'  # 10 new + 5 due
    assert d['description'].split('\n')[:3] == ['Today', '• Anki: 15 cards', '• Lesson: conditionvariables']
    assert 'obsidian://' not in d['description']
    empty = p['days'][-1]
    assert '• Lesson: none' in empty['description'] and empty['title'].endswith('· 0 lessons')


def test_future_days_count_due_reviews():
    cards = [card(1, type_=2, queue=2, due=TODAY_NUM + 2), card(2, type_=2, queue=2, due=TODAY_NUM + 2)]
    p = planner.plan(state(courses={'CS3214': course(cards)}))
    assert [d['due'] for d in p['days'][:3]] == [0, 0, 2]


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


def revlog(n_cards, per_card, days, ms=6000, start='2026-11-01'):
    """Each of n_cards first answered `days` ago (spread), then answered per_card times."""
    import datetime as dt
    from zoneinfo import ZoneInfo
    base = dt.datetime.fromisoformat(start).replace(hour=12, tzinfo=ZoneInfo('America/New_York'))
    out = []
    for c in range(n_cards):
        first = base - dt.timedelta(days=days - (c % days))
        for k in range(per_card):
            when = min(first + dt.timedelta(days=k), base)
            out.append(dict(id=int(when.timestamp() * 1000) + k, cardId=c, ease=3, kind=0 if k == 0 else 1, ms=ms))
    return out


def test_calibration_defaults_without_history():
    p = planner.plan(state())
    assert p['calibration']['seconds_per_review']['source'] == 'default'
    assert p['calibration']['reviews_per_new_card']['source'] == 'default'
    assert p['total_new_per_day'] == 27


def test_calibration_measures_answer_time():
    c = course([], code='CS3214')
    c['revlog'] = revlog(60, 3, 20, ms=5000)  # 180 answers in the window, 5 s each
    p = planner.plan(state(courses={'CS3214': c}))
    assert p['calibration']['seconds_per_review'] == dict(value=5.0, source='measured', answers=180)
    assert p['calibration']['reviews_per_new_card']['source'] == 'default'  # history too short
    assert p['total_new_per_day'] == 54  # 45 * 60 / 5 / 10


def test_calibration_measures_reviews_per_new_card_and_config_wins():
    c = course([], code='CS3214')
    c['revlog'] = revlog(120, 8, 60)
    p = planner.plan(state(courses={'CS3214': c}))
    measured = p['calibration']['reviews_per_new_card']
    assert measured['source'] == 'measured' and 4 <= measured['value'] <= 20
    s = state(courses={'CS3214': c})
    s['config_set'] = ['seconds_per_review', 'reviews_per_new_card']
    fixed = planner.plan(s)['calibration']
    assert fixed['seconds_per_review']['source'] == 'config' and fixed['reviews_per_new_card']['source'] == 'config'


def test_done_course_gets_no_new_cards():
    cards = [card(i) for i in range(30)]
    s = state(courses={'CS3214': course(cards)})
    s['config']['courses'] = {'CS3214': {'folders': ['cs3214'], 'done': True}}
    p = planner.plan(s)
    assert p['courses']['CS3214']['new_per_day'] == 0 and p['courses']['CS3214']['done']


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
