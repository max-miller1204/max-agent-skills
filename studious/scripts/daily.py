#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["markdown-it-py>=3", "pygments>=2.17", "pyyaml>=6"]
# ///
"""The hands-off daily run. Asks nothing; anything that needs the user goes to studious/inbox.md.

    uv run daily.py [--vault DIR] [--dry-run] [--no-calendar]

1. Preflight: Obsidian sync service (if configured), Anki running (launched if needed).
2. AnkiWeb sync (never a full sync).
3. Snapshot and plan; push the cards of lessons marked done (or all in-scope cards in cram mode),
   and edits and retags of cards already in Anki; re-snapshot and re-plan if anything was pushed.
4. Apply the plan to Anki: new cards/day per course, today-only overrides, reposition, cram decks.
5. Regenerate the timetables, write the calendar events.
6. AnkiWeb sync again, quit Anki if this run started it, write the inbox, commit the vault (local git).
7. Write a run log outside the vault, to ~/.local/state/studious/runs/<vault>/ (the last 60 are kept).

Prints a JSON summary; exits 1 if any step failed (the failure is in the inbox too, and so are notes
that need the user, such as a skipped AnkiWeb sync).
"""
import argparse
import datetime
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
KEEP_LOGS = 60


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


anki = load('studious_anki', 'anki.py')
snapshot = load('studious_snapshot', 'snapshot.py')
planner = load('studious_plan', 'plan.py')
timetable = load('studious_timetable', 'timetable.py')
calendar = load('studious_calendar', 'calendar_events.py')


class Run:
    def __init__(self, vault, dry_run):
        self.vault, self.dry_run = Path(vault), dry_run
        self.config = {**snapshot.DEFAULTS, **anki.vault_config(self.vault)}
        self.client = anki.Anki(int(self.config.get('anki_port') or 8765),
                                os.environ.get('STUDIOUS_ANKI_PROFILE') or self.config.get('anki_profile'))
        self.steps, self.errors, self.notes = [], [], []
        self.attention = []  # notes the user has to act on: they go to the inbox too
        self.launched_anki = False
        self.pushed = False  # whether a push changed Anki, even if another lesson failed

    def step(self, name, fn, *args, required=False):
        try:
            result = fn(*args)
            self.steps.append(dict(step=name, ok=True))
            return result
        except Exception as exc:  # every failure ends up in the inbox, never silently
            self.steps.append(dict(step=name, ok=False, error=str(exc)))
            self.errors.append(f'{name}: {exc}')
            if required:
                raise StopRun(name)
            return None

    def note(self, text, attention=False):
        if text not in self.notes:  # the after-run sync repeats the first one's notes
            self.notes.append(text)
            if attention:
                self.attention.append(text)

    # ---- preflight ----
    def obsidian_sync(self):
        service = self.config.get('obsidian_sync_service')
        if not service:
            return 'not configured'
        # The service lives on the scheduled machine only; elsewhere the Obsidian app syncs the vault.
        if not shutil.which('systemctl') or subprocess.run(['systemctl', '--user', 'cat', service],
                                                            capture_output=True).returncode != 0:
            return f'{service} is not on this machine (the Obsidian app syncs here)'
        state = subprocess.run(['systemctl', '--user', 'is-active', service], capture_output=True, text=True).stdout.strip()
        if state != 'active':
            raise RuntimeError(f'{service} is {state or "not running"}: the vault may be stale, so nothing was planned')
        return state

    def ensure_anki(self):
        try:
            version = self.client.call('studiousVersion')
        except anki.Refused:
            command = self.config.get('anki_launch_command') or (
                ['open', '-g', '-a', 'Anki', '--args', '-p', self.client.profile] if platform.system() == 'Darwin'
                else ['anki', '-p', self.client.profile])
            if isinstance(command, str):
                command = command.replace('{profile}', self.client.profile).split()
            subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            self.launched_anki = True
            for _ in range(60):
                time.sleep(1)
                try:
                    version = self.client.call('studiousVersion')
                    break
                except anki.Refused:
                    continue
            else:
                raise RuntimeError('Anki did not start (is a desktop session available to this job?)')
        if version['profile'] != self.client.profile:
            raise RuntimeError(f"Anki is open on profile {version['profile']!r}, not {self.client.profile!r}")
        return version

    def anki_sync(self):
        try:
            return self.client.call('studiousSync')
        except anki.Refused as exc:
            if 'not logged in' in str(exc):
                self.note('AnkiWeb sync skipped: this profile is not logged in.', attention=True)
                return None
            raise

    # ---- plan and apply ----
    def make_plan(self):
        cache = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'studious'
        state_path = cache / f'state-{self.vault.name}.json'  # scratch, kept out of the synced vault
        state_path.parent.mkdir(parents=True, exist_ok=True)
        code = snapshot.main(['--vault', str(self.vault), '--out', str(state_path)])
        state = json.loads(state_path.read_text())
        if code:
            raise RuntimeError('; '.join(state['errors']))
        return state, planner.plan(state)

    def push(self, items):
        report = []
        for item in items:
            if self.dry_run:
                report.append(dict(lesson=item['path'], dryRun=True))
                continue
            try:
                report += anki.push(self.client, str(self.vault), [str(self.vault / item['path'])],
                                    ungated=item['ungated'])
            except Exception as exc:  # one lesson's failure doesn't stop the others
                report.append(dict(lesson=item['path'], errors=[str(exc)]))
        self.pushed = any(r.get('created') or r.get('updated') for r in report)
        problems = [f"{r['lesson']}: {e}" for r in report for e in r.get('errors') or []]
        if problems:
            raise RuntimeError('; '.join(problems))
        return report

    def apply(self, plan):
        if self.dry_run:
            return 'dry run'
        done = any(c.get('done') for c in plan['courses'].values())
        # The preset's new cards/day also caps each lesson subdeck, so it must cover the largest course
        # limit (a cram day can go over the all-course budget).
        cap = max([plan['total_new_per_day']] + [max(c['new_per_day'], c['new_today'] or 0)
                                                for c in plan['courses'].values()])
        payload = anki.settings_payload(self.config, cap, done=done)
        result = self.client.call('studiousApplySettings', settings=payload)
        reset = [c['field'] for c in result['changes'] if c['field'] not in ('new.perDay', 'name', None)]
        if reset:
            self.note(f"Anki settings changed in Anki were set back from studious/config.md: "
                      f"{', '.join(sorted(set(reset)))}. Change them in the config instead.", attention=True)
        names = [p['name'] for p in payload['presets']]
        for code, c in plan['courses'].items():
            try:
                self.client.call('studiousSetLimits', root=code, newPerDay=c['new_per_day'], newToday=c['new_today'],
                                 preset=names[1] if c.get('done') else names[0])
            except anki.Refused as exc:
                if 'does not exist' in str(exc):
                    continue  # no cards for this course yet
                raise
            if c['reposition']:
                self.client.call('studiousReposition', cardIds=c['reposition'])
        for deck in plan['cram']:
            try:
                self.client.call('studiousCram', name=deck['name'], search=deck['search'], limit=deck['limit'])
            except anki.Refused as exc:
                if 'No cards matched' not in str(exc):
                    raise
                self.notes.append(f"{deck['name']}: no weak cards to cram yet (none reviewed below 85%).")
        for name in plan['cram_delete']:
            try:
                self.client.call('studiousCramDelete', name=name)
            except anki.Refused:
                pass  # never built
        if plan['suspend']:
            self.client.call('studiousSuspend', cardIds=plan['suspend'], suspended=True)
        if plan['unsuspend']:
            self.client.call('studiousSuspend', cardIds=plan['unsuspend'], suspended=False)
        record = self.vault / 'studious/retired.json'
        if plan['retired'] or record.exists():
            record.write_text(json.dumps(plan['retired'], indent=1, sort_keys=True) + '\n')
        if plan['suspend'] or plan['unsuspend']:
            self.notes.append(f"Retired courses: {len(plan['suspend'])} cards suspended, "
                              f"{len(plan['unsuspend'])} unsuspended.")
        return 'applied'

    def optimize(self):
        """FSRS optimization once `fsrs_optimize_days` have passed since the last one (Anki counts)."""
        if self.dry_run:
            return 'dry run'
        result = self.client.call('studiousOptimize', minDays=int(self.config.get('fsrs_optimize_days') or 30))
        if result['ran']:
            changed = [p['name'] for p in result['presets'] if p['changed']]
            self.notes.append('FSRS optimized' + (f": new parameters for {', '.join(changed)}." if changed
                                                  else '; parameters unchanged (not enough reviews yet).'))
        return result

    def timetables(self, state, plan):
        out = self.vault / 'timetable'
        out.mkdir(parents=True, exist_ok=True)
        status = {(g['course'], g['id']): g for g in plan['goals']}
        written = []
        for goal in state['goals']:
            path = out / timetable.filename(goal)
            if plan['courses'].get(goal['course'], {}).get('retired') or goal.get('status', 'active') != 'active':
                if path.exists() and not self.dry_run:
                    path.unlink()  # archived and done goals, and a retired course's, leave the Goals view
                continue
            if not self.dry_run:
                path.write_text(timetable.render(state, goal, status.get((goal['course'], goal['id']))), encoding='utf-8')
            written.append(path.name)
        return written

    def calendar_sync(self, plan):
        calendar_id = self.config.get('calendar_id')
        if not calendar_id:
            self.note('Calendar skipped: no calendar_id in studious/config.md.', attention=True)
            return None
        cal = calendar.Calendar()
        try:
            return calendar.sync(cal, plan, str(calendar_id), str(self.vault), self.dry_run)
        finally:
            cal.close()

    # ---- close ----
    def write_inbox(self, plan):
        lines = ['# Inbox', '', f'Updated by the daily run on {datetime.date.today().isoformat()}. '
                 'The next interactive `/studious` run works through it.', '']
        if plan:
            if plan['inbox']:
                lines += ['## Decisions', ''] + [f'- [ ] {i}' for i in plan['inbox']] + ['']
            if plan['warnings']:
                lines += ['## Warnings', ''] + [f'- {w}' for w in plan['warnings']] + ['']
        if self.attention:
            lines += ['## Needs you', ''] + [f'- {n}' for n in self.attention] + ['']
        if self.errors:
            lines += ['## Problems in the last run', ''] + [f'- {e}' for e in self.errors] + ['']
        if len(lines) == 4:
            lines.append('Nothing needs you.')
        if not self.dry_run:
            (self.vault / 'studious/inbox.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        return len(lines) > 5

    def write_log(self, summary):
        if self.dry_run:
            return
        state = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'studious'
        runs = state / 'runs' / self.vault.name  # machine-local; the inbox carries anything the user needs
        runs.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime('%Y-%m-%d-%H%M')
        (runs / f'{stamp}-daily.json').write_text(json.dumps(summary, indent=1) + '\n')
        for old in sorted(runs.glob('*-daily.json'))[:-KEEP_LOGS]:
            old.unlink()

    def commit(self):
        if self.dry_run or not (self.vault / '.git').exists():
            return 'skipped'
        subprocess.run(['git', '-C', str(self.vault), 'add', '-A'], check=True)
        if subprocess.run(['git', '-C', str(self.vault), 'diff', '--cached', '--quiet']).returncode == 0:
            return 'nothing to commit'
        result = subprocess.run(['git', '-C', str(self.vault), 'commit', '-q', '-m', 'studious: daily run'],
                                capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'git commit failed')
        return 'committed'

    def quit_anki(self):
        if self.launched_anki:
            self.client.call('guiExitAnki')
        return self.launched_anki


class StopRun(Exception):
    pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--vault', default=os.environ.get('STUDIOUS_VAULT', str(Path.home() / 'studious')))
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--no-calendar', action='store_true')
    args = parser.parse_args(argv)
    run = Run(args.vault, args.dry_run)
    plan = state = None
    try:
        run.step('obsidian sync', run.obsidian_sync, required=True)
        run.step('anki', run.ensure_anki, required=True)
        run.step('anki sync', run.anki_sync, required=True)
        state, plan = run.step('plan', run.make_plan, required=True)
        if plan['push']:
            run.step('push cards', run.push, plan['push'])
            if run.pushed and not args.dry_run:  # also when another lesson failed: Anki changed either way
                state, plan = run.step('re-plan', run.make_plan, required=True)
        run.step('apply to anki', run.apply, plan)
        run.step('optimize fsrs', run.optimize)
        run.step('timetables', run.timetables, state, plan)
        if not args.no_calendar:
            run.step('calendar', run.calendar_sync, plan)
    except StopRun:
        pass
    if not any(s['step'] == 'anki' and not s['ok'] for s in run.steps):
        run.step('anki sync (after)', run.anki_sync)
        run.step('quit anki', run.quit_anki)
    run.write_inbox(plan)  # after every step that talks to Anki, so their failures are in it
    if run.step('commit vault', run.commit) is None:
        run.write_inbox(plan)  # left uncommitted, so it can say the commit failed
    summary = dict(date=datetime.date.today().isoformat(), dryRun=args.dry_run, steps=run.steps, notes=run.notes,
                   errors=run.errors, today=(plan or {}).get('days', [{}])[0].get('title'))
    run.write_log(summary)
    print(json.dumps(summary, indent=2))
    return 1 if run.errors else 0


if __name__ == '__main__':
    sys.exit(main())
