#!/usr/bin/env python3
"""Create or repair the studious vault layout. Safe to run any number of times; never overwrites notes.

    python3 setup_vault.py [--vault DIR]

Creates the folders, a config.md template (only if missing), the Obsidian CSS snippet for card and
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

The settings are in this note's properties (above). `/studious setup` fills them in; you can also
edit them here. Every key and its default is in the skill's `references/onboarding.md`.
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
