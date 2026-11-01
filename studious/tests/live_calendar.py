"""Live test of calendar_events.py against a real calendar, on dates far in the future, then cleanup.

    python3 tests/live_calendar.py CALENDAR_ID

Uses a throwaway vault in a temp folder, so the real studious/calendar.json is never touched.
"""
import datetime
import importlib.util
import sys
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location('cal', Path(__file__).resolve().parent.parent / 'scripts/calendar_events.py')
cal_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cal_mod)

DATES = ['2027-01-04', '2027-01-05', '2027-01-06']


def plan(title_suffix=''):
    return dict(today=DATES[0], days=[dict(date=d, title=f'[studious] Daily quota: {i} cards · 0 lessons{title_suffix}',
                                           description=f'Today\n• Anki: {i} cards\n• Lesson: none') for i, d in enumerate(DATES)])


def main():
    calendar_id = sys.argv[1]
    failures = []

    def ok(label, cond, detail=''):
        print(('PASS ' if cond else 'FAIL ') + label + ('' if cond else f'  {detail}'))
        if not cond:
            failures.append(label)

    cal = cal_mod.Calendar()
    with tempfile.TemporaryDirectory() as vault:
        (Path(vault) / 'studious').mkdir()
        try:
            r = cal_mod.sync(cal, plan(), calendar_id, vault)
            ok('creates one event per day', [x['action'] for x in r] == ['created'] * 3, r)
            r = cal_mod.sync(cal, plan(), calendar_id, vault)
            ok('second run changes nothing', [x['action'] for x in r] == ['unchanged'] * 3, r)
            r = cal_mod.sync(cal, plan(' (new)'), calendar_id, vault)
            ok('changed text updates in place', [x['action'] for x in r] == ['updated'] * 3, r)

            _, record = cal_mod.load_record(vault, calendar_id)
            edited, deleted = record['events'][DATES[1]]['id'], record['events'][DATES[2]]['id']
            cal.tool('update-event', calendarId=calendar_id, eventId=edited, summary='[studious] my own note')
            cal.tool('delete-event', calendarId=calendar_id, eventId=deleted, sendUpdates='none')
            r = cal_mod.sync(cal, plan(' (again)'), calendar_id, vault)
            ok('hand edit and delete detected', [x['action'] for x in r] ==
               ['updated', 'edited by hand: left alone', 'deleted by hand: not recreated'], r)
            got = cal_mod.event_of(cal.tool('get-event', calendarId=calendar_id, eventId=edited))
            ok('edited event kept the user text', got.get('summary') == '[studious] my own note', got.get('summary'))

            fresh = Path(vault) / 'studious/calendar.json'
            fresh.unlink()  # record lost: existing events must be adopted, not duplicated
            r = cal_mod.sync(cal, plan(), calendar_id, vault)
            ok('lost record adopts instead of duplicating', [x['action'] for x in r][:2] == ['adopted', 'adopted'], r)
            listed = cal_mod.events_of(cal.tool('list-events', calendarId=calendar_id, timeMin=f'{DATES[0]}T00:00:00',
                                                timeMax='2027-01-07T00:00:00'))
            ok('still one event per day', len(listed) == 3, [(e['start'], e['summary']) for e in listed])
        finally:
            listed = cal_mod.events_of(cal.tool('list-events', calendarId=calendar_id, timeMin=f'{DATES[0]}T00:00:00',
                                                timeMax='2027-01-07T00:00:00'))
            for e in listed:
                if str(e.get('summary', '')).startswith('[studious]'):
                    cal.tool('delete-event', calendarId=calendar_id, eventId=e['id'], sendUpdates='none')
            left = cal_mod.events_of(cal.tool('list-events', calendarId=calendar_id, timeMin=f'{DATES[0]}T00:00:00',
                                              timeMax='2027-01-07T00:00:00'))
            ok('cleanup', not left, left)
            cal.close()
    print(f'\n{len(failures)} failed' if failures else '\nall passed')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
