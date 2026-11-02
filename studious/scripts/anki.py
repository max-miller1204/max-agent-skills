#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["markdown-it-py>=3", "pygments>=2.17", "pyyaml>=6"]
# ///
"""studious <-> Anki, through the patched AnkiConnect in studious-connectors.

    uv run anki.py [--vault DIR] [--profile NAME] [--port N] COMMAND ...

Commands (all print JSON):
  doctor                      clone the connectors if missing, run their doctor
  status                      patch/Anki version, open profile, missing note types
  ensure-note-types           create/update Basic+, Basic+ (type in), Cloze+, Code+
  push PATH... [--ungated] [--dry-run]
                              push the card blocks of lesson notes (files or folders)
  cards PATH...               list card blocks: path, id, type, title, line
  observe ROOT [--since-days N]
                              card states, FSRS estimates, revlog, today's counts for a course root deck
  flagged ROOT                flagged cards of ROOT, matched to their lesson card blocks
  limits ROOT [--new N] [--today N] [--by-position]
  reposition CARD_ID...       put these new cards first, in order
  set-flag FLAG CARD_ID...    0 clears
  suspend CARD_ID... | unsuspend CARD_ID...
  cram NAME --search QUERY [--limit N]   |   cram-delete NAME
  sync                        normal AnkiWeb sync; refuses when a full sync is needed
  call ACTION [JSON_PARAMS]   any AnkiConnect action (profile added for studious* actions)

Card blocks live in lesson notes (see references/cards.md):

    > [!card]- Why recheck the predicate after waking?
    > %% id: cs3214_3f9a0c1d2e4b · type: Basic+ · tags: cheatsheet %%
    > #### Front
    > Two consumers wait ... Why must each recheck the queue?
    > #### Back
    > Only one may take the item ...
    > #### Source
    > L_CondVars slide 12

`push` gives every card without an id a GUID (`<course>_<12 hex>`) and writes it back into the
note. It creates a card in Anki only when the lesson has `verified: true` and `done: true`
(or `--ungated`, for cram mode). Cards already in Anki are updated whenever the lesson is verified.
"""
import argparse
import base64
import html
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import yaml
from markdown_it import MarkdownIt
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.util import ClassNotFound

CONNECTORS_REPO = 'max-miller1204/studious-connectors'
IO_FIELDS = ['Occlusion', 'Image', 'Header', 'Back Extra', 'Comments']

EXTRA_FIELD = {'Cloze+': 'Back Extra', 'Image Occlusion': 'Back Extra'}


class Refused(Exception):
    pass


# ---- configuration -------------------------------------------------------------------------------

def connectors_dir(clone=False):
    path = Path(os.environ.get('STUDIOUS_CONNECTORS', Path.home() / '.local/share/studious/connectors'))
    if not path.exists() and clone:
        path.parent.mkdir(parents=True, exist_ok=True)
        if shutil.which('gh'):
            cmd = ['gh', 'repo', 'clone', CONNECTORS_REPO, str(path)]
        else:
            cmd = ['git', 'clone', f'git@github.com:{CONNECTORS_REPO}.git', str(path)]
        subprocess.run(cmd, check=True)
    return path


def read_frontmatter(text):
    """(frontmatter dict, body start index). No frontmatter gives ({}, 0)."""
    m = re.match(r'---\n(.*?)\n---\n?', text, re.S)
    if not m:
        return {}, 0
    return (yaml.safe_load(m.group(1)) or {}), m.end()


def vault_config(vault):
    path = Path(vault) / 'studious/config.md'
    return read_frontmatter(path.read_text(encoding='utf-8'))[0] if path.exists() else {}


# ---- AnkiConnect ------------------------------------------------------------------------------

class Anki:
    def __init__(self, port, profile):
        self.port, self.profile = port, profile

    def call(self, action, **params):
        if action.startswith('studious') and action != 'studiousVersion':
            if not self.profile:
                raise Refused('no Anki profile: pass --profile or set anki_profile in studious/config.md')
            params.setdefault('profile', self.profile)
        body = json.dumps({'action': action, 'version': 6, 'params': params}).encode()
        request = urllib.request.Request(f'http://127.0.0.1:{self.port}', body, {'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                reply = json.load(response)
        except OSError as exc:
            raise Refused(f'Anki is not answering on port {self.port} ({exc}); start Anki, then run `anki.py doctor`')
        if reply.get('error'):
            raise Refused(reply['error'])
        return reply['result']


# ---- card blocks ---------------------------------------------------------------------------------

CARD_START = re.compile(r'^>\s*\[!card\][-+]?\s*(.*)$')
META = re.compile(r'^>\s*%%\s*(.*?)\s*%%\s*$')
FIELD = re.compile(r'^>\s*####\s+(.+?)\s*$')


def parse_meta(text):
    meta = {}
    for part in re.split(r'\s+·\s+|\s*;\s*', text):
        if ':' in part:
            key, value = part.split(':', 1)
            meta[key.strip().lower()] = value.strip()
    return meta


def parse_cards(text):
    """Card blocks in a note: [{line, end, title, meta, meta_line, fields}] (0-based line numbers)."""
    lines = text.split('\n')
    cards, i = [], 0
    while i < len(lines):
        m = CARD_START.match(lines[i])
        if not m:
            i += 1
            continue
        start, title = i, m.group(1).strip()
        i += 1
        meta, meta_line, fields, current = {}, None, {}, None
        while i < len(lines) and lines[i].startswith('>'):
            line = lines[i]
            mm, fm = META.match(line), FIELD.match(line)
            if mm and current is None:
                meta, meta_line = parse_meta(mm.group(1)), i
            elif fm:
                current = fm.group(1)
                fields[current] = []
            elif current is not None:
                fields[current].append(re.sub(r'^> ?', '', line))
            i += 1
        cards.append(dict(line=start, end=i, title=title, meta=meta, meta_line=meta_line,
                          fields={k: '\n'.join(v).strip() for k, v in fields.items()}))
    return cards


def with_id(text, card, guid):
    """The note text with `id: guid` set in the card's %% meta %% line (added if missing)."""
    lines = text.split('\n')
    meta = dict(card['meta'], id=guid)
    order = ['id', 'type'] + [k for k in meta if k not in ('id', 'type')]
    line = '> %% ' + ' · '.join(f'{k}: {meta[k]}' for k in order if k in meta) + ' %%'
    if card['meta_line'] is not None:
        lines[card['meta_line']] = line
    else:
        lines.insert(card['line'] + 1, line)
    return '\n'.join(lines)


def new_guid(prefix):
    return f'{prefix}_{secrets.token_hex(6)}'


# ---- markdown -> Anki HTML -------------------------------------------------------------------------

MD = MarkdownIt('commonmark', {'html': True, 'breaks': True}).enable(['table', 'strikethrough'])
FENCE = re.compile(r'^```[ \t]*([\w+#.-]*)[^\n]*\n(.*?)^```[ \t]*$', re.M | re.S)
INLINE_CODE = re.compile(r'(`+)(.+?)\1', re.S)
DISPLAY_MATH = re.compile(r'\$\$(.+?)\$\$', re.S)
INLINE_MATH = re.compile(r'(?<![\\$])\$(?=\S)(.+?)(?<=\S)\$(?!\$)')
EMBED = re.compile(r'!\[\[([^\]|#]+?)(?:\|[^\]]*)?\]\]')
MD_IMAGE = re.compile(r'!\[([^\]]*)\]\((?!https?:)([^)\s]+)\)')
WIKILINK = re.compile(r'\[\[([^\]|]+?)(?:\|([^\]]+))?\]\]')


class Converter:
    """Markdown with LaTeX and Obsidian embeds -> Anki field HTML. Collects the media it references."""

    def __init__(self, vault, prefix):
        self.vault, self.prefix = Path(vault) if vault else None, prefix
        self.media = {}  # anki filename -> local path

    def media_name(self, ref):
        name = Path(ref).name
        if not re.fullmatch(r'[A-Za-z0-9_\-.]+', name):
            name = re.sub(r'[^A-Za-z0-9_\-.]+', '-', name)
        return name if name.startswith(self.prefix + '_') else f'{self.prefix}_{name}'

    def find_file(self, ref, base):
        candidates = [base / ref] if base else []
        if self.vault:
            candidates += [self.vault / ref, self.vault / 'attachments' / Path(ref).name]
        for c in candidates:
            if c.is_file():
                return c
        if self.vault:
            for c in self.vault.rglob(Path(ref).name):
                if c.is_file() and '.git' not in c.parts:
                    return c
        raise Refused(f'embedded file {ref!r} not found in the vault')

    def image(self, ref, base, alt=''):
        path = self.find_file(ref, base)
        name = self.media_name(ref)
        self.media[name] = path
        alt = f' alt="{html.escape(alt)}"' if alt else ''
        return f'<img src="{name}"{alt}>'

    def code_block(self, lang, code):
        lang = (lang or '').lower()
        if lang == 'mermaid':
            raise Refused('mermaid blocks do not render in Anki: render the diagram to an SVG/PNG in attachments '
                          'and embed the image instead')
        try:
            lexer = get_lexer_by_name(lang) if lang else guess_lexer(code)
        except ClassNotFound:
            lexer = get_lexer_by_name('text')
        body = highlight(code.rstrip('\n'), lexer, HtmlFormatter(nowrap=True)).rstrip('\n')
        return '<pre class="hl"><code>' + body.replace('\n', '<br>') + '</code></pre>'

    def convert(self, text, base=None):
        held = []

        def hold(value):
            held.append(value)
            return f'@@STUDIOUSHOLD{len(held) - 1}@@'

        text = FENCE.sub(lambda m: hold(self.code_block(m.group(1), m.group(2))), text)
        text = INLINE_CODE.sub(lambda m: hold('<code>' + html.escape(m.group(2).strip()) + '</code>'), text)
        text = DISPLAY_MATH.sub(lambda m: hold(r'\[' + html.escape(m.group(1).strip(), quote=False) + r'\]'), text)
        text = INLINE_MATH.sub(lambda m: hold(r'\(' + html.escape(m.group(1), quote=False) + r'\)'), text)
        text = EMBED.sub(lambda m: hold(self.image(m.group(1).strip(), base)), text)
        text = MD_IMAGE.sub(lambda m: hold(self.image(m.group(2), base, m.group(1))), text)
        text = WIKILINK.sub(lambda m: (m.group(2) or m.group(1).split('/')[-1].split('#')[0]), text)
        out = MD.render(text).replace('<br />', '<br>').replace('\n', '')
        # Paragraph margins look odd on cards: separate paragraphs with a blank line instead,
        # and drop the break before a block element, which starts on its own line anyway.
        out = re.sub(r'<p>(.*?)</p>', r'\1<br><br>', out, flags=re.S)
        out = re.sub(r'(?:<br>)+(?=<(?:table|ul|ol|pre|blockquote|h\d|hr|div)\b)', '', out)
        out = re.sub(r'(?:<br>)+$', '', out)
        return re.sub(r'@@STUDIOUSHOLD(\d+)@@', lambda m: held[int(m.group(1))], out)


def check_cloze(value):
    """Anki ends a deletion at the first `}}`: refuse text where that cuts a LaTeX group short."""
    for m in re.finditer(r'\{\{c\d+::', value):
        close = value.find('}}', m.end())
        if close < 0:
            raise Refused('cloze deletion is never closed')
        inner = value[m.end():close]
        if inner.count('{') != inner.count('}'):
            raise Refused(f'cloze deletion closes early inside {inner[:40]!r}: add a space before the final }} '
                          '(e.g. `\\frac{N}{M} }}`)')


# ---- building notes ------------------------------------------------------------------------------

def note_fields(connectors):
    specs = json.loads((connectors / 'anki/note_types/note_types.json').read_text(encoding='utf-8'))
    fields = {s['name']: s['fields'] for s in specs}
    fields['Image Occlusion'] = IO_FIELDS
    return fields


def build_note(card, lesson, path, converter, fields_by_type):
    kind = card['meta'].get('type')
    if kind not in fields_by_type:
        raise Refused(f'unknown card type {kind!r} (use one of {sorted(fields_by_type)})')
    allowed = fields_by_type[kind]
    source = card['fields'].get('Source', '').strip()
    if not source:
        raise Refused('every card needs a #### Source')
    out = {}
    for name, value in card['fields'].items():
        if name == 'Source' and 'Source' not in allowed:
            continue
        if name not in allowed:
            raise Refused(f'{kind} has no field {name!r} (fields: {allowed})')
        if (kind, name) == ('Basic+ (type in)', 'Back'):  # typed answers compare exactly: keep plain text
            out[name] = html.escape(' '.join(value.split()), quote=False)
        elif (kind, name) == ('Image Occlusion', 'Occlusion'):  # shape specs pass through, one per line
            out[name] = '<br>'.join(line.strip() for line in value.split('\n') if line.strip())
        else:
            out[name] = converter.convert(value, path.parent)
    if 'Source' not in allowed:  # Image Occlusion: the source goes under Back Extra
        extra = EXTRA_FIELD[kind]
        out[extra] = (out.get(extra, '') + f'<div class=source>Source: {html.escape(source)}</div>').strip()
    first = allowed[0]
    if not out.get(first):
        raise Refused(f'{kind} needs a #### {first}')
    for value in out.values():
        check_cloze(value)
    course = lesson['course']
    goals = lesson.get('goals') or []
    tags = [f'{course}::exam::{g}' for g in goals]
    card_tags = [t.strip() for t in re.split(r'[,\s]+', card['meta'].get('tags', '')) if t.strip()]
    if 'cheatsheet' in card_tags:
        tags += [f'{course}::cheatsheet::{g}' for g in goals]
    tags += [t for t in card_tags if t != 'cheatsheet']
    return dict(guid=card['meta']['id'], modelName=kind, deck=lesson['deck'], fields=out, tags=tags)


def lesson_files(paths):
    for p in map(Path, paths):
        if p.is_dir():
            yield from sorted(f for f in p.rglob('*.md') if '.git' not in f.parts)
        elif p.suffix == '.md':
            yield p


def push(anki, vault, paths, ungated=False, dry_run=False):
    connectors = connectors_dir()
    fields_by_type = note_fields(connectors)
    report = []
    for path in lesson_files(paths):
        text = path.read_text(encoding='utf-8')
        lesson, _ = read_frontmatter(text)
        cards = parse_cards(text)
        if not cards:
            continue
        entry = dict(lesson=str(path), created=0, updated=0, unchanged=0, held=0, errors=[])
        report.append(entry)
        missing = [k for k in ('course', 'deck') if not lesson.get(k)]
        if missing:
            entry['errors'].append(f'frontmatter needs {missing}')
            continue
        prefix = re.sub(r'[^a-z0-9]+', '', str(lesson['course']).lower())
        changed = False
        for card in reversed(cards):  # bottom-up so earlier line numbers stay valid
            if not card['meta'].get('id'):
                card['meta']['id'] = new_guid(prefix)
                text = with_id(text, card, card['meta']['id'])
                changed = True
        if changed and not dry_run:
            path.write_text(text, encoding='utf-8')
            cards = parse_cards(text)
        if not lesson.get('verified'):
            entry['held'] = len(cards)
            entry['errors'].append('lesson is not verified: run the verification pass first')
            continue
        converter = Converter(vault, prefix)
        notes = []
        for card in cards:
            try:
                notes.append(build_note(card, lesson, path, converter, fields_by_type))
            except Refused as exc:
                entry['errors'].append(f"line {card['line'] + 1} ({card['title'][:50]}): {exc}")
        if entry['errors']:
            continue
        existing = {n['guid'] for n in anki.call('studiousNotes', guids=[n['guid'] for n in notes])} if notes else set()
        gate_open = ungated or bool(lesson.get('done'))
        send = [n for n in notes if n['guid'] in existing or gate_open]
        entry['held'] = len(notes) - len(send)
        if dry_run or not send:
            entry['wouldSend'] = len(send) if dry_run else 0
            continue
        if converter.media:
            files = [dict(filename=name, data=base64.b64encode(p.read_bytes()).decode())
                     for name, p in converter.media.items()]
            stored = {s['filename']: s['stored'] for s in anki.call('studiousStoreMedia', files=files)}
            for note in send:
                for k, v in note['fields'].items():
                    for name, actual in stored.items():
                        if actual != name:
                            v = v.replace(f'src="{name}"', f'src="{actual}"')
                    note['fields'][k] = v
        course = lesson['course']
        results = anki.call('studiousUpsertNotes', notes=send,
                            ownedTagPrefixes=[f'{course}::exam::', f'{course}::cheatsheet::'])
        for r in results:
            entry[r['action']] += 1
    return report


def find_card(vault, guid):
    for path in lesson_files([Path(vault)]):
        text = path.read_text(encoding='utf-8')
        if guid not in text:
            continue
        for card in parse_cards(text):
            if card['meta'].get('id') == guid:
                return dict(path=str(path), line=card['line'] + 1, title=card['title'], type=card['meta'].get('type'))
    return None


# ---- CLI -----------------------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description='studious <-> Anki')
    parser.add_argument('--vault', default=os.environ.get('STUDIOUS_VAULT', str(Path.home() / 'studious')))
    parser.add_argument('--profile')
    parser.add_argument('--port', type=int)
    sub = parser.add_subparsers(dest='cmd', required=True)
    sub.add_parser('doctor')
    sub.add_parser('status')
    sub.add_parser('ensure-note-types')
    p = sub.add_parser('push')
    p.add_argument('paths', nargs='+')
    p.add_argument('--ungated', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    p = sub.add_parser('cards')
    p.add_argument('paths', nargs='+')
    p = sub.add_parser('observe')
    p.add_argument('root')
    p.add_argument('--since-days', type=int, default=120)
    p = sub.add_parser('flagged')
    p.add_argument('root')
    p = sub.add_parser('limits')
    p.add_argument('root')
    p.add_argument('--new', type=int)
    p.add_argument('--today', type=int)
    p.add_argument('--by-position', action='store_true')
    p = sub.add_parser('reposition')
    p.add_argument('cards', nargs='+', type=int)
    p = sub.add_parser('set-flag')
    p.add_argument('flag', type=int)
    p.add_argument('cards', nargs='+', type=int)
    for name in ('suspend', 'unsuspend'):
        sub.add_parser(name).add_argument('cards', nargs='+', type=int)
    p = sub.add_parser('cram')
    p.add_argument('name')
    p.add_argument('--search', required=True)
    p.add_argument('--limit', type=int, default=9999)
    sub.add_parser('cram-delete').add_argument('name')
    sub.add_parser('sync')
    p = sub.add_parser('call')
    p.add_argument('action')
    p.add_argument('params', nargs='?', default='{}')
    args = parser.parse_args(argv)

    config = vault_config(args.vault)
    profile = args.profile or os.environ.get('STUDIOUS_ANKI_PROFILE') or config.get('anki_profile')
    port = args.port or int(config.get('anki_port') or 8765)
    anki = Anki(port, profile)
    try:
        if args.cmd == 'doctor':
            path = connectors_dir(clone=True)
            subprocess.run(['git', '-C', str(path), 'pull', '--ff-only', '-q'], check=False)
            result = subprocess.run([sys.executable, str(path / 'doctor.py'), '--json', '--port', str(port)],
                                    capture_output=True, text=True)
            out = json.loads(result.stdout)
        elif args.cmd == 'status':
            out = anki.call('studiousVersion')
        elif args.cmd == 'ensure-note-types':
            base = connectors_dir(clone=True) / 'anki/note_types'
            specs = json.loads((base / 'note_types.json').read_text(encoding='utf-8'))
            css = (base / 'shared.css').read_text(encoding='utf-8')
            out = anki.call('studiousEnsureNoteTypes', noteTypes=[dict(s, css=css) for s in specs])
        elif args.cmd == 'push':
            out = push(anki, args.vault, args.paths, args.ungated, args.dry_run)
        elif args.cmd == 'cards':
            out = [dict(path=str(f), line=c['line'] + 1, id=c['meta'].get('id'), type=c['meta'].get('type'),
                        title=c['title']) for f in lesson_files(args.paths)
                   for c in parse_cards(f.read_text(encoding='utf-8'))]
        elif args.cmd == 'observe':
            import time
            since = int((time.time() - args.since_days * 86400) * 1000)
            out = anki.call('studiousObserve', root=args.root, sinceMs=since)
        elif args.cmd == 'flagged':
            cards = anki.call('studiousObserve', root=args.root, sinceMs=0)['cards']
            out = [dict(cardId=c['cardId'], guid=c['guid'], flag=c['flag'], lapses=c['lapses'],
                        card=find_card(args.vault, c['guid'])) for c in cards if c['flag']]
        elif args.cmd == 'limits':
            out = anki.call('studiousSetLimits', root=args.root, newPerDay=args.new, newToday=args.today,
                            gatherByPosition=True if args.by_position else None)
        elif args.cmd == 'reposition':
            out = anki.call('studiousReposition', cardIds=args.cards)
        elif args.cmd == 'set-flag':
            out = anki.call('studiousSetFlag', cardIds=args.cards, flag=args.flag)
        elif args.cmd in ('suspend', 'unsuspend'):
            out = anki.call('studiousSuspend', cardIds=args.cards, suspended=args.cmd == 'suspend')
        elif args.cmd == 'cram':
            out = anki.call('studiousCram', name=args.name, search=args.search, limit=args.limit)
        elif args.cmd == 'cram-delete':
            out = anki.call('studiousCramDelete', name=args.name)
        elif args.cmd == 'sync':
            out = anki.call('studiousSync')
        else:
            out = anki.call(args.action, **json.loads(args.params))
    except Refused as exc:
        print(json.dumps(dict(error=str(exc))))
        return 1
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 1 if isinstance(out, list) and any(e.get('errors') for e in out if isinstance(e, dict)) else 0


if __name__ == '__main__':
    sys.exit(main())
