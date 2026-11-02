#!/usr/bin/env python3
"""Create or repair the studious vault layout. Safe to run any number of times; never overwrites notes.

    python3 setup_vault.py [--vault DIR]

Creates the folders, a config.md template and the Dashboard.base lesson and goal views (only if missing), the Obsidian CSS snippet for card and
supplementary callouts (enabled in appearance.json, other settings kept), .gitignore entries, and a
local git repository (no remote) used as an undo log. Prints what it did as JSON.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

FOLDERS = ['studious/runs', 'studious/manifests', 'goals', 'courses', 'timetable', 'attachments',
           '.obsidian/snippets']
SNIPPET = """.callout[data-callout="card"] { --callout-color: 59, 110, 165; --callout-icon: lucide-layers; }
.callout[data-callout="supplementary"] { --callout-color: 130, 130, 130; --callout-icon: lucide-book-plus; }
"""
IGNORES = ['.obsidian/workspace*.json', '.trash/', '.DS_Store']
CONFIG = """---
# studious settings. See references/onboarding.md in the skill for every key.
anki_profile: testing
anki_port: 8765
timezone: America/New_York
calendar_id:
deadline_calendars: []
courses: {}
anki_minutes_per_day: 45
max_lessons_per_day: 2
lesson_buffer_days: 3
cram_days: 2
---
# studious config

The settings are this note's properties (above). `/studious setup` fills them in, and you can edit
them here: click **Add property** and type the key, or switch to Source mode (`courses` and
`anki_settings` are nested, so edit those in Source mode). A key that isn't set uses its default.
The next daily run picks up any change.

## Anki connection

| Key | Default | What it does |
|---|---|---|
| `anki_profile` | none (required) | The Anki profile studious writes to. Every write is refused while any other profile is open. |
| `anki_port` | `8765` | AnkiConnect's port. |
| `anki_launch_command` | `anki -p {profile}` (Linux), `open -g -a Anki --args -p {profile}` (Mac) | How the daily run starts Anki when it's closed. `{profile}` is replaced with `anki_profile`. |

## Courses

| Key | Default | What it does |
|---|---|---|
| `courses` | `{}` | One entry per course or track: `CODE: {folders: [path, ...]}`. The code names the Anki deck and tags (`CS3214`, `CS3214::exam::Test2`); the folders (relative to your home folder) are where its sources are. |
| `courses.CODE.done` | `false` | Set `done: true` when the course is over: no more new cards, and its decks move to the done preset (lower retention, fewer reviews). |

```yaml
courses:
  CS3214: {folders: [cs3214]}
  CS2505: {folders: [cs2505], done: true}
```

## Daily workload

| Key | Default | What it does |
|---|---|---|
| `anki_minutes_per_day` | `45` | Minutes of Anki a day for all courses together. Sets the new-card budget: minutes x 60 / `seconds_per_review` / `reviews_per_new_card`. |
| `seconds_per_review` | measured, else `10` | Average seconds per answer. Measured from the last 28 days once there are 100 answers. Setting it turns measuring off. |
| `reviews_per_new_card` | measured, else `10` | How many reviews a day each new card a day turns into. Measured once the review log spans 56 days and 30 cards were new in the last 28 (kept between 4 and 20). Setting it turns measuring off. |
| `max_lessons_per_day` | `2` | Most lessons scheduled on one day, across all goals. Cram mode may go over it (with a warning). |
| `casual_new_per_day` | `5` | New cards a day for a course with cards but no deadline pressure, and for casual goals. |
| `budget_new_per_day` | `10` | Minimum new cards a day for a course with a self-paced budgeted goal. |
| `backlog_overdue_threshold` | `0` | A course with more overdue reviews than this gets no new cards until they're done, unless one of its exams is within `cram_days`. |

## Deadlines and cram

| Key | Default | What it does |
|---|---|---|
| `lesson_buffer_days` | `3` | Lessons are scheduled to finish this many days before a deadline, leaving those days for reviews. |
| `cram_days` | `2` | In cram mode, the last this many days before an exam get a filtered deck of your weakest cards (`CS3214 Test2 cram`). |

A goal switches to cram mode by itself when its lessons or cards can't fit at the normal pace, or
when you set `cram: true` in the goal note.

## Calendar

| Key | Default | What it does |
|---|---|---|
| `calendar_id` | none | The calendar the daily `[studious]` events go to. Not set: no calendar writes. |
| `deadline_calendars` | all calendars | Calendar names or ids scanned for exams and deadlines to adopt as goals. |
| `calendar_days` | `7` | How many days of events are written ahead. |
| `timezone` | `America/New_York` | Used for dates in the plan, the timetables and the review log. |

## Vault and sync

| Key | Default | What it does |
|---|---|---|
| `vault_name` | the vault folder's name | The vault's name in Obsidian, used in `obsidian://` links in the timetables. |
| `obsidian_sync_service` | none | The systemd user service that syncs this vault with Obsidian Headless. The daily run stops (with an inbox note) when it isn't running, rather than plan from a stale vault. Skipped on machines without the service. |
| `rag_green`, `rag_amber` | `0.90`, `0.75` | Timetable colors: a day's share of reviews not pressed Again at or above `rag_green` is green, at or above `rag_amber` amber, below that red. |

The vault's location isn't a config key: it's `~/studious`, or the `STUDIOUS_VAULT` environment
variable when set.

## Anki settings

| Key | Default | What it does |
|---|---|---|
| `anki_preset_name` | `studious` | The name given to Anki's Default preset, which every course uses. Done courses use `<name>::done`. |
| `fsrs_optimize_days` | `30` | The daily run runs Anki's "Optimize all presets" once this many days have passed since the last optimization. |
| `anki_settings` | below | Anki settings studious keeps set. Each daily run applies them, so a change made in Anki's deck options is set back (and the run notes it): change them here instead. |

`anki_settings` has four parts. Only list what you want to differ from the defaults; it's merged
over them.

```yaml
anki_settings:
  preset:                    # the studious preset (any deck option, nested or dotted)
    desiredRetention: 0.9    # FSRS target recall: higher means more reviews
    new.delays: [15]         # learning steps in minutes: one short same-day step suits FSRS
    lapse.delays: [15]       # relearning steps after pressing Again
    rev.perDay: 9999         # maximum reviews a day: uncapped so due reviews never pile up
    newGatherPriority: 1     # new cards in deck position order, which studious sets
    new.bury: true           # bury new siblings until tomorrow
    rev.bury: true           # bury review siblings
    buryInterdayLearning: true
    lapse.leechFails: 8      # lapses before a card counts as a leech
    lapse.leechAction: 1     # 1 = tag it only, 0 = suspend it
    maxTaken: 360            # seconds an answer can count toward review time
  done_preset:               # overrides for done courses, on top of preset
    desiredRetention: 0.85
  collection:
    config: {fsrs: true, applyAllParentLimits: true}   # FSRS on; "Limits start from top" on
    preferences: {}          # e.g. {scheduling: {rollover: 4, learnAheadSecs: 1200}}
  desktop: {}                # e.g. {theme: 2} (0 system, 1 light, 2 dark), autoSync, numBackups
```

New cards a day (`new.perDay`) isn't a setting: the planner sets it every run. To see every
setting Anki has, in this shape: `uv run ~/.agents/skills/studious/scripts/anki.py settings show`.
Unknown fields, wrong types and out-of-range values are refused before anything is written.

## Goal notes

Goals (`goals/<COURSE>-<id>.md`) have their own properties: `id`, `course`, `title`, `mode`
(`deadline`, `self-paced-budget` with `weekly_hours` of lessons (default 3), or `self-paced-casual`), `date`,
`calendar_event_id`, `status` (`active`, `done`, `archived`) and `cram`.
"""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--vault', default=os.environ.get('STUDIOUS_VAULT', str(Path.home() / 'studious')))
    args = parser.parse_args(argv)
    vault = Path(args.vault).expanduser()
    done = []
    for folder in FOLDERS:
        path = vault / folder
        if not path.is_dir():
            path.mkdir(parents=True)
            done.append(f'created {folder}/')
    config = vault / 'studious/config.md'
    if not config.exists():
        config.write_text(CONFIG, encoding='utf-8')
        done.append('created studious/config.md (template)')
    dashboard = vault / 'Dashboard.base'
    if not dashboard.exists():
        dashboard.write_text((Path(__file__).resolve().parent.parent / 'assets/Dashboard.base').read_text(), encoding='utf-8')
        done.append('created Dashboard.base')
    inbox = vault / 'studious/inbox.md'
    if not inbox.exists():
        inbox.write_text('# Inbox\n\nNothing needs you.\n', encoding='utf-8')
        done.append('created studious/inbox.md')
    snippet = vault / '.obsidian/snippets/studious.css'
    if not snippet.exists() or snippet.read_text() != SNIPPET:
        snippet.write_text(SNIPPET, encoding='utf-8')
        done.append('wrote the studious CSS snippet')
    appearance = vault / '.obsidian/appearance.json'
    settings = json.loads(appearance.read_text()) if appearance.exists() else {}
    enabled = settings.get('enabledCssSnippets') or []
    if 'studious' not in enabled:
        settings['enabledCssSnippets'] = enabled + ['studious']
        appearance.write_text(json.dumps(settings, indent=2) + '\n')
        done.append('enabled the snippet in .obsidian/appearance.json')
    gitignore = vault / '.gitignore'
    lines = gitignore.read_text().splitlines() if gitignore.exists() else []
    missing = [l for l in IGNORES if l not in lines]
    if missing:
        gitignore.write_text('\n'.join(lines + missing) + '\n')
        done.append(f'added {missing} to .gitignore')
    if not (vault / '.git').exists():
        subprocess.run(['git', 'init', '-q', '-b', 'main', str(vault)], check=True)
        subprocess.run(['git', '-C', str(vault), 'add', '-A'], check=True)
        subprocess.run(['git', '-C', str(vault), 'commit', '-q', '-m', 'studious: vault setup'], check=True)
        done.append('started the local git undo log (no remote)')
    print(json.dumps(dict(vault=str(vault), changes=done or ['nothing to do']), indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
