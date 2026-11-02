#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""Which source files are new, changed or gone since they were last ingested.

    uv run manifest.py diff [COURSE...] [--vault DIR]
    uv run manifest.py accept COURSE PATH... [--lesson LESSON_PATH] [--vault DIR]
    uv run manifest.py ignore COURSE PATH... [--vault DIR]

Courses and their source folders come from studious/config.md:

    courses:
      CS3214: {folders: [cs3214]}            # relative to the home folder

`diff` hashes every file under those folders (skipping build outputs, archives, caches and anything
matched by a `.studiousignore` or `.studyignore` file, gitignore-style globs, one per line) and
compares with studious/manifests/<COURSE>.json. Each file gets a guessed class (course-material,
exam-blueprint, my-work, artifact) for the agent to confirm. `accept` records files as ingested
(optionally with the lesson they went into); `ignore` records them as deliberately skipped.
"""
import argparse
import fnmatch
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import yaml

SKIP_DIRS = {'.git', '__pycache__', 'node_modules', '__MACOSX', '.venv', 'venv', 'build', 'dist', 'target',
             '.ipynb_checkpoints', '.pytest_cache', '.cache', '.idea', '.vscode', 'anki', 'anki-backup'}
ARTIFACT_EXT = {'.o', '.a', '.so', '.exe', '.out', '.class', '.pyc', '.zip', '.tar', '.gz', '.tgz', '.7z', '.log',
                '.DS_Store', '.apkg', '.anki2', '.dSYM', '.d', '.lock', '.mp4', '.mov', '.mkv'}
TEXT_CODE = {'.c', '.h', '.cc', '.cpp', '.rs', '.hs', '.py', '.java', '.rkt', '.pas', '.scala', '.js', '.ts',
             '.go', '.sh', '.s', '.S', '.ml', '.sql', '.r', '.R', '.m'}
BLUEPRINT_DIR = re.compile(r'^(tests?\d+|exams?\d*|midterms?[-_ ]?\d*|finals?([-_ ]review)?|quiz(zes)?\d*|review)$', re.I)
BLUEPRINT_FILE = re.compile(r'(?<![a-z])(review|exams?|midterms?|finals?|quiz(zes)?|prep|solutions?)(?![a-z])', re.I)
MY_WORK = re.compile(r'(^|/)(hw\d*|homework|p\d+[-_]|project|ex\d+|submission|cheat ?sheet|my[-_])', re.I)


def read_config(vault):
    path = vault / 'studious/config.md'
    m = re.match(r'---\n(.*?)\n---', path.read_text(encoding='utf-8'), re.S) if path.exists() else None
    return (yaml.safe_load(m.group(1)) or {}) if m else {}


def ignore_patterns(folder):
    patterns = []
    for name in ('.studiousignore', '.studyignore'):
        f = folder / name
        if f.exists():
            patterns += [l.strip() for l in f.read_text().splitlines() if l.strip() and not l.startswith('#')]
    return patterns


def ignored(rel, patterns):
    return any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(rel, p.rstrip('/') + '/*')
               or fnmatch.fnmatch(Path(rel).name, p) for p in patterns)


def classify(rel):
    ext = Path(rel).suffix
    if ext in ARTIFACT_EXT or Path(rel).name.startswith('.'):
        return 'artifact'
    parts = Path(rel).parts
    if any(BLUEPRINT_DIR.match(d) for d in parts[1:-1]) or (ext not in TEXT_CODE and BLUEPRINT_FILE.search(parts[-1])):
        return 'exam-blueprint'
    if MY_WORK.search(rel) and (ext in TEXT_CODE or ext in {'.tex', '.html', '.md', '.txt', '.ipynb'}):
        return 'my-work'
    return 'course-material'


def scan(folders):
    files = {}
    for folder in folders:
        root = Path.home() / folder
        patterns = ignore_patterns(root)
        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = os.path.relpath(dirpath, Path.home())
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith(('.', 'anki-backup'))
                                 and not ignored(os.path.relpath(os.path.join(dirpath, d), root), patterns))
            for name in sorted(filenames):
                path = Path(dirpath) / name
                rel = os.path.join(rel_dir, name)
                if path.is_symlink() or ignored(os.path.relpath(path, root), patterns):
                    continue
                kind = classify(rel)
                if kind == 'artifact':
                    continue
                files[rel] = dict(hash=hashlib.sha256(path.read_bytes()).hexdigest()[:16], kind=kind,
                                  size=path.stat().st_size)
    return files


def manifest_path(vault, course):
    return vault / 'studious/manifests' / f'{course}.json'


def load(vault, course):
    path = manifest_path(vault, course)
    return json.loads(path.read_text()) if path.exists() else {}


def save(vault, course, data):
    path = manifest_path(vault, course)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, sort_keys=True) + '\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('cmd', choices=['diff', 'accept', 'ignore'])
    parser.add_argument('args', nargs='*')
    parser.add_argument('--lesson')
    parser.add_argument('--under', help='only files under this path (relative to home)')
    parser.add_argument('--list', action='store_true', help='list every file instead of a per-folder summary')
    parser.add_argument('--vault', default=os.environ.get('STUDIOUS_VAULT', str(Path.home() / 'studious')))
    a = parser.parse_args(argv)
    vault = Path(a.vault)
    courses = read_config(vault).get('courses') or {}
    if a.cmd == 'diff':
        out = {}
        for course in a.args or sorted(courses):
            if course not in courses:
                out[course] = dict(error='not in studious/config.md courses')
                continue
            known, now = load(vault, course), scan(courses[course].get('folders') or [])
            if a.under:
                now = {p: f for p, f in now.items() if p.startswith(a.under.rstrip('/') + '/')}
                known = {p: f for p, f in known.items() if p.startswith(a.under.rstrip('/') + '/')}
            out[course] = dict(
                new=[dict(path=p, kind=f['kind'], size=f['size']) for p, f in now.items() if p not in known],
                changed=[dict(path=p, kind=f['kind'], lesson=known[p].get('lesson')) for p, f in now.items()
                         if p in known and known[p]['hash'] != f['hash'] and known[p].get('status') != 'ignored'],
                gone=[dict(path=p, lesson=known[p].get('lesson')) for p in known if p not in now
                      and known[p].get('status') != 'ignored'])
            if not a.list and len(out[course]['new']) > 40:  # summarize: folder -> counts by kind
                folders = {}
                for f in out[course]['new']:
                    key = str(Path(f['path']).parent)
                    folders.setdefault(key, {}).setdefault(f['kind'], 0)
                    folders[key][f['kind']] += 1
                out[course]['new'] = dict(total=len(out[course]['new']), by_folder=folders,
                                          hint='use --under FOLDER or --list for the files')
        print(json.dumps(out, indent=2))
        return 0
    course, paths = a.args[0], a.args[1:]
    if course not in courses or not paths:
        print(json.dumps(dict(error='usage: accept|ignore COURSE PATH...')))
        return 1
    data, now = load(vault, course), scan(courses[course].get('folders') or [])
    for p in paths:
        rel = os.path.relpath(Path(p).expanduser().resolve(), Path.home()) if os.path.isabs(os.path.expanduser(p)) else p
        if rel not in now:
            print(json.dumps(dict(error=f'{rel} is not a source file of {course}')))
            return 1
        data[rel] = dict(hash=now[rel]['hash'], kind=now[rel]['kind'],
                         status='ignored' if a.cmd == 'ignore' else 'ingested',
                         **({'lesson': a.lesson} if a.lesson and a.cmd == 'accept' else {}))
    save(vault, course, data)
    print(json.dumps(dict(recorded=len(paths))))
    return 0


if __name__ == '__main__':
    sys.exit(main())
