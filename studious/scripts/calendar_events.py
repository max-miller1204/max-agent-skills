#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""studious all-day events in Google Calendar, through google-calendar-mcp (spawned over stdio).

    uv run calendar_events.py calendars
    uv run calendar_events.py sync PLAN.json [--calendar ID] [--vault DIR] [--dry-run]
    uv run calendar_events.py clear [--calendar ID] [--vault DIR]
    uv run calendar_events.py deadlines [--days 120] [--vault DIR]

`deadlines` reads (never writes) the calendars named in config `deadline_calendars` (all calendars
when unset) for events that look like exams, tests, quizzes, midterms, finals or interviews in the
next `--days` days, and leaves out those already adopted (a goal note whose `calendar_event_id`
matches). The agent asks the user before adopting any.

`sync` writes one all-day event per day of plan.json's `days` to the configured calendar. It keeps
`<vault>/studious/calendar.json` (date -> event id, Google's `updated` stamp, content hash):
- a recorded event whose `updated` changed was edited by hand: left alone from then on;
- a recorded event that's gone was deleted by hand: not recreated for that date;
- otherwise the event is created, updated when the text changed, or left as is.
A `[studious]` event already on a date but missing from the record (record lost, or another
machine wrote it) is adopted instead of duplicated. Only the configured calendar is ever touched.
`clear` deletes the future events this record manages that weren't hand-edited.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

CONFIG_DIR = Path.home() / '.config/studious'
TOOLS = 'list-calendars,list-events,get-event,create-event,update-event,delete-event'
PREFIX = '[studious]'


class CalendarError(Exception):
    pass


def server_env():
    env = {k: os.environ[k] for k in ('PATH', 'HOME', 'LANG', 'TMPDIR') if k in os.environ}
    env_file = CONFIG_DIR / 'google-calendar-mcp.env'
    if not env_file.exists():
        raise CalendarError(f'{env_file} is missing: see studious-connectors calendar/README.md')
    for line in env_file.read_text().splitlines():
        m = re.match(r'\s*(?:export\s+)?([A-Z_]+)=(.*)$', line)
        if m:
            env[m.group(1)] = os.path.expandvars(m.group(2).strip().strip('"\'')).replace('$HOME', str(Path.home()))
    return env


class Calendar:
    """A minimal MCP stdio client for google-calendar-mcp."""

    def __init__(self):
        self.proc = subprocess.Popen(['google-calendar-mcp', 'start', '--enable-tools', TOOLS], env=server_env(),
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, bufsize=1)
        self.next_id = 0
        self.request('initialize', dict(protocolVersion='2025-06-18', capabilities={},
                                        clientInfo=dict(name='studious', version='1')))
        self.send(dict(jsonrpc='2.0', method='notifications/initialized'))

    def send(self, message):
        self.proc.stdin.write(json.dumps(message) + '\n')
        self.proc.stdin.flush()

    def request(self, method, params):
        self.next_id += 1
        self.send(dict(jsonrpc='2.0', id=self.next_id, method=method, params=params))
        for line in self.proc.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get('id') == self.next_id:
                if 'error' in message:
                    raise CalendarError(message['error'].get('message', str(message['error'])))
                return message['result']
        raise CalendarError('google-calendar-mcp exited')

    def tool(self, name, **arguments):
        result = self.request('tools/call', dict(name=name, arguments=arguments))
        text = ''.join(c.get('text', '') for c in result.get('content') or [])
        if result.get('isError'):
            raise CalendarError(text)
        if result.get('structuredContent') is not None:
            return result['structuredContent']
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


def events_of(result):
    """The event list from a list-events result, whatever wrapper the server uses."""
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ('events', 'items'):
            if isinstance(result.get(key), list):
                return result[key]
    return []


def event_of(result):
    return result.get('event', result) if isinstance(result, dict) else {}


def digest(title, description):
    return hashlib.sha256(f'{title}\n{description}'.encode()).hexdigest()[:16]


def load_record(vault, calendar_id):
    path = Path(vault) / 'studious/calendar.json'
    record = json.loads(path.read_text()) if path.exists() else {}
    if record.get('calendarId') not in (None, calendar_id):
        record = {}  # a different calendar: start over rather than touch events elsewhere
    record.setdefault('calendarId', calendar_id)
    record.setdefault('events', {})
    return path, record


def sync(cal, plan, calendar_id, vault, dry_run=False):
    path, record = load_record(vault, calendar_id)
    today = plan['today']
    record['events'] = {d: e for d, e in record['events'].items() if d >= today}
    days = plan['days']
    end = (datetime.date.fromisoformat(days[-1]['date']) + datetime.timedelta(days=1)).isoformat()
    listed = events_of(cal.tool('list-events', calendarId=calendar_id, timeMin=f'{today}T00:00:00',
                                timeMax=f'{end}T00:00:00', fields=['description', 'updated']))
    by_id = {e['id']: e for e in listed}
    on_date = {}
    for e in listed:
        start = (e.get('start') or {}).get('date')
        if start and str(e.get('summary', '')).startswith(PREFIX):
            on_date.setdefault(start, e)
    report = []
    for d in days:
        date, title, description = d['date'], d['title'], d['description']
        rec = record['events'].get(date)
        want = digest(title, description)
        if rec and rec['state'] in ('edited', 'deleted'):
            report.append(dict(date=date, action=f"skipped ({rec['state']} by hand)"))
            continue
        if rec:
            current = by_id.get(rec['id'])
            if current is None:
                rec['state'] = 'deleted'
                report.append(dict(date=date, action='deleted by hand: not recreated'))
                continue
            if current.get('updated') != rec['updated']:
                rec['state'] = 'edited'
                report.append(dict(date=date, action='edited by hand: left alone'))
                continue
            if rec['hash'] == want:
                report.append(dict(date=date, action='unchanged'))
                continue
            if not dry_run:
                event_of(cal.tool('update-event', calendarId=calendar_id, eventId=rec['id'], summary=title,
                                  description=description))
                rec.update(updated=updated_of(cal, calendar_id, rec['id']), hash=want)
            report.append(dict(date=date, action='updated'))
            continue
        adopt = on_date.get(date)
        if dry_run:
            report.append(dict(date=date, action='adopt and update' if adopt else 'create'))
            continue
        if adopt:
            cal.tool('update-event', calendarId=calendar_id, eventId=adopt['id'], summary=title, description=description)
            event_id = adopt['id']
        else:
            next_day = (datetime.date.fromisoformat(date) + datetime.timedelta(days=1)).isoformat()
            event_id = event_of(cal.tool('create-event', calendarId=calendar_id, summary=title,
                                         description=description, start=date, end=next_day,
                                         transparency='transparent'))['id']
        record['events'][date] = dict(id=event_id, updated=updated_of(cal, calendar_id, event_id), hash=want,
                                      state='managed')
        report.append(dict(date=date, action='adopted' if adopt else 'created'))
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=1, sort_keys=True) + '\n')
    return report


def updated_of(cal, calendar_id, event_id):
    return event_of(cal.tool('get-event', calendarId=calendar_id, eventId=event_id, fields=['updated'])).get('updated')


def clear(cal, calendar_id, vault):
    path, record = load_record(vault, calendar_id)
    today = datetime.date.today().isoformat()
    done = []
    for date, rec in sorted(record['events'].items()):
        if date < today or rec['state'] != 'managed':
            continue
        try:
            cal.tool('delete-event', calendarId=calendar_id, eventId=rec['id'], sendUpdates='none')
        except CalendarError as exc:
            if 'not found' not in str(exc).lower() and '410' not in str(exc):
                raise
        del record['events'][date]
        done.append(date)
    path.write_text(json.dumps(record, indent=1, sort_keys=True) + '\n')
    return done


DEADLINE = re.compile(r'\b(exams?|tests?|quiz(zes)?|midterms?|finals?|interviews?)\b|\btest\s*\d|\bquiz\s*\d', re.I)


def deadlines(cal, vault, days):
    import yaml
    path = Path(vault) / 'studious/config.md'
    m = re.match(r'---\n(.*?)\n---', path.read_text(), re.S) if path.exists() else None
    config = (yaml.safe_load(m.group(1)) or {}) if m else {}
    calendars = cal.tool('list-calendars')
    calendars = calendars.get('calendars', calendars) if isinstance(calendars, dict) else calendars
    wanted = config.get('deadline_calendars')
    chosen = [c for c in calendars if not wanted or c.get('summary') in wanted or c.get('id') in wanted]
    adopted = set()
    for goal in (Path(vault) / 'goals').glob('*.md'):
        hit = re.search(r'^calendar_event_id:\s*(\S+)', goal.read_text(), re.M)
        if hit:
            adopted.add(hit.group(1).strip('"\''))
    today = datetime.date.today()
    end = today + datetime.timedelta(days=days)
    found = []
    for c in chosen:
        if c.get('id') == config.get('calendar_id'):
            continue
        events = events_of(cal.tool('list-events', calendarId=c['id'], timeMin=f'{today}T00:00:00',
                                    timeMax=f'{end}T00:00:00'))
        for e in events:
            summary = str(e.get('summary', ''))
            if e.get('id') in adopted or summary.startswith(PREFIX) or not DEADLINE.search(summary):
                continue
            start = e.get('start') or {}
            found.append(dict(calendar=c.get('summary'), eventId=e.get('id'), summary=summary,
                              date=(start.get('date') or start.get('dateTime', ''))[:10]))
    return sorted(found, key=lambda f: (f['date'], f['summary']))


def configured_calendar(vault):
    path = Path(vault) / 'studious/config.md'
    if path.exists():
        m = re.search(r'^calendar_id:\s*(\S+)\s*$', path.read_text(), re.M)
        if m:
            return m.group(1).strip('"\'')
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('cmd', choices=['calendars', 'sync', 'clear', 'deadlines'])
    parser.add_argument('plan', nargs='?')
    parser.add_argument('--calendar')
    parser.add_argument('--days', type=int, default=120)
    parser.add_argument('--vault', default=os.environ.get('STUDIOUS_VAULT', str(Path.home() / 'vault')))
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    try:
        cal = Calendar()
    except (CalendarError, OSError) as exc:
        print(json.dumps(dict(error=str(exc))))
        return 1
    try:
        if args.cmd == 'calendars':
            result = cal.tool('list-calendars')
            items = result.get('calendars', result) if isinstance(result, dict) else result
            out = [dict(id=c.get('id'), name=c.get('summary') or c.get('name'), primary=c.get('primary', False))
                   for c in items] if isinstance(items, list) else items
        elif args.cmd == 'deadlines':
            out = deadlines(cal, args.vault, args.days)
        else:
            calendar_id = args.calendar or configured_calendar(args.vault)
            if not calendar_id:
                raise CalendarError('no calendar: pass --calendar or set calendar_id in studious/config.md')
            if args.cmd == 'sync':
                with open(args.plan, encoding='utf-8') as f:
                    out = sync(cal, json.load(f), calendar_id, args.vault, args.dry_run)
            else:
                out = clear(cal, calendar_id, args.vault)
    except CalendarError as exc:
        print(json.dumps(dict(error=str(exc))))
        return 1
    finally:
        cal.close()
    print(json.dumps(out, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
