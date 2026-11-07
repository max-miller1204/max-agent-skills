"""Unit tests for scripts/anki.py (no Anki needed).

    uv run --with pytest --with markdown-it-py --with pygments --with pyyaml pytest tests/
"""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / 'scripts'
spec = importlib.util.spec_from_file_location('anki', SCRIPTS / 'anki.py')
anki = importlib.util.module_from_spec(spec)
spec.loader.exec_module(anki)

LESSON = """---
course: CS3214
deck: CS3214::L_CondVars
goals: [Test2]
verified: true
done: false
---
# Condition variables

Some teaching.

> [!card]- Why recheck the predicate?
> %% type: Basic+ · tags: cheatsheet %%
> #### Front
> Two consumers wait. Why must each **recheck** the queue?
> #### Back
> Waking does not make the predicate true.
> #### Source
> L_CondVars slide 12

> [!card]- Exit status
> %% id: cs3214_aaaaaaaaaaaa · type: Basic+ (type in) %%
> #### Front
> Exit status of `true`? (number)
> #### Back
>  0
> #### Source
> L_Shell slide 3
"""

FIELDS = {'Basic+': ['Front', 'Back', 'Extra', 'Source', 'Add Reverse'],
          'Basic+ (type in)': ['Front', 'Back', 'Extra', 'Source'],
          'Cloze+': ['Text', 'Back Extra', 'Source'],
          'Code+': ['Prompt', 'Code', 'Answer', 'Extra', 'Source'],
          'Image Occlusion': anki.IO_FIELDS}


def test_parse_cards_reads_meta_and_fields():
    cards = anki.parse_cards(LESSON)
    assert [c['title'] for c in cards] == ['Why recheck the predicate?', 'Exit status']
    assert cards[0]['meta'] == {'type': 'Basic+', 'tags': 'cheatsheet'}
    assert cards[0]['fields']['Front'] == 'Two consumers wait. Why must each **recheck** the queue?'
    assert cards[1]['meta']['id'] == 'cs3214_aaaaaaaaaaaa'
    assert cards[1]['fields']['Back'] == '0'


def test_with_id_replaces_meta_line_and_keeps_the_rest():
    card = anki.parse_cards(LESSON)[0]
    text = anki.with_id(LESSON, card, 'cs3214_bbbbbbbbbbbb')
    again = anki.parse_cards(text)[0]
    assert again['meta'] == {'id': 'cs3214_bbbbbbbbbbbb', 'type': 'Basic+', 'tags': 'cheatsheet'}
    assert again['fields'] == card['fields']
    assert len(text.split('\n')) == len(LESSON.split('\n'))


def test_with_id_adds_a_meta_line_when_missing():
    text = '> [!card]- T\n> #### Front\n> q\n> #### Source\n> s\n'
    out = anki.with_id(text, anki.parse_cards(text)[0], 'x_0123456789ab')
    assert out.split('\n')[1] == '> %% id: x_0123456789ab %%'
    assert anki.parse_cards(out)[0]['fields']['Front'] == 'q'


def test_guid_shape():
    import re
    assert re.fullmatch(r'cs3214_[0-9a-f]{12}', anki.new_guid('cs3214'))


@pytest.fixture
def convert(tmp_path):
    (tmp_path / 'attachments').mkdir()
    (tmp_path / 'attachments' / 'slide 12.png').write_bytes(b'png')
    c = anki.Converter(tmp_path, 'cs3214')
    return c


def test_math_becomes_mathjax_and_is_html_escaped(convert):
    out = convert.convert(r'If $a<b$ and $x_i^2$ then $$\sum_{i} x_i$$')
    assert r'\(a&lt;b\)' in out
    assert r'\(x_i^2\)' in out  # underscores in math are not emphasis
    assert r'\[\sum_{i} x_i\]' in out
    assert '$' not in out


def test_dollar_in_code_is_not_math(convert):
    out = convert.convert('Use `echo $? $HOME` to see it')
    assert '<code>echo $? $HOME</code>' in out
    assert r'\(' not in out


def test_code_fence_is_highlighted_with_br_lines(convert):
    out = convert.convert('```c\nint main() {\n  return 0;\n}\n```')
    assert out.startswith('<pre class="hl"><code>') and out.endswith('</code></pre>')
    assert '<br>' in out and '\n' not in out
    assert '<span class="kt">int</span>' in out


def test_mermaid_is_refused(convert):
    with pytest.raises(anki.Refused):
        convert.convert('```mermaid\ngraph TD; A-->B\n```')


def test_embeds_become_prefixed_media(convert):
    out = convert.convert('See ![[slide 12.png|400]]')
    assert out == 'See <img src="cs3214_slide-12.png">'
    assert list(convert.media) == ['cs3214_slide-12.png']


def test_missing_embed_is_refused(convert):
    with pytest.raises(anki.Refused):
        convert.convert('![[nope.png]]')


def test_wikilinks_tables_and_paragraphs(convert):
    out = convert.convert('See [[Locks|locks]] and [[CS3214/Semaphores]].\n\n| a | b |\n|---|---|\n| 1 | 2 |')
    assert out.startswith('See locks and Semaphores.<table>')
    assert '<td>1</td>' in out
    assert convert.convert('one\n\ntwo') == 'one<br><br>two'


def test_single_paragraph_has_no_p_wrapper(convert):
    assert convert.convert('just **bold**') == 'just <strong>bold</strong>'


def test_cloze_brace_collision_is_refused():
    with pytest.raises(anki.Refused):
        anki.check_cloze(r'\(\mu_{ {{c1::c^{(i)}}} }\)')
    anki.check_cloze(r'\(\mu = {{c1::c^{(i)} }}\)')
    anki.check_cloze('A {{c1::mutex}} and {{c2::semaphore}}')


def test_build_note_tags_plain_typein_and_source(tmp_path):
    cards = anki.parse_cards(LESSON)
    cards[0]['meta']['id'] = 'cs3214_000000000001'
    lesson, _ = anki.read_frontmatter(LESSON)
    conv = anki.Converter(tmp_path, 'cs3214')
    first = anki.build_note(cards[0], lesson, tmp_path / 'l.md', conv, FIELDS)
    assert first['deck'] == 'CS3214::L_CondVars'
    assert first['tags'] == ['CS3214::exam::Test2', 'CS3214::cheatsheet::Test2']
    assert first['fields']['Source'] == 'L_CondVars slide 12'
    second = anki.build_note(cards[1], lesson, tmp_path / 'l.md', conv, FIELDS)
    assert second['fields']['Back'] == '0'
    assert second['tags'] == ['CS3214::exam::Test2']


def test_build_note_requires_source_and_known_fields(tmp_path):
    lesson = {'course': 'CS3214', 'deck': 'CS3214::X'}
    conv = anki.Converter(tmp_path, 'cs3214')
    no_source = dict(meta={'id': 'cs3214_1', 'type': 'Basic+'}, fields={'Front': 'q', 'Back': 'a'}, title='t', line=0)
    with pytest.raises(anki.Refused):
        anki.build_note(no_source, lesson, tmp_path / 'l.md', conv, FIELDS)
    bad_field = dict(no_source, fields={'Front': 'q', 'Answer': 'a', 'Source': 's'})
    with pytest.raises(anki.Refused):
        anki.build_note(bad_field, lesson, tmp_path / 'l.md', conv, FIELDS)


def test_image_occlusion_puts_source_under_back_extra(tmp_path):
    (tmp_path / 'fig.png').write_bytes(b'png')
    conv = anki.Converter(tmp_path, 'cs4804')
    card = dict(meta={'id': 'cs4804_1', 'type': 'Image Occlusion'}, title='t', line=0, fields={
        'Occlusion': '{{c1::image-occlusion:rect:left=.1:top=.1:width=.2:height=.1:oi=1}}\n'
                     '{{c2::image-occlusion:rect:left=.5:top=.1:width=.2:height=.1:oi=1}}',
        'Image': '![[fig.png]]', 'Header': 'Bayes net', 'Source': 'L08 slide 4'})
    note = anki.build_note(card, {'course': 'CS4804', 'deck': 'CS4804::L08'}, tmp_path / 'l.md', conv, FIELDS)
    assert note['fields']['Image'] == '<img src="cs4804_fig.png">'
    assert note['fields']['Occlusion'].count('<br>') == 1
    assert note['fields']['Back Extra'].endswith('<div class=source>Source: L08 slide 4</div>')
    assert 'Source' not in note['fields']


def test_note_type_fields_match_connectors():
    fields = anki.note_fields(anki.connectors_dir())
    for name, expected in FIELDS.items():
        assert fields[name] == expected


def test_settings_payload_merges_config_over_defaults():
    config = {'anki_preset_name': 'study', 'anki_settings': {
        'preset': {'new': {'delays': [10, 60]}, 'desiredRetention': 0.85},
        'collection': {'preferences': {'scheduling': {'rollover': 5}}},
        'desktop': {'global': {'theme': 2}}}}
    payload = anki.settings_payload(config, new_per_day=27)
    preset = payload['presets'][0]
    assert preset['id'] == 1 and preset['name'] == 'study'
    assert preset['options']['new.delays'] == [10, 60]
    assert preset['options']['desiredRetention'] == 0.85
    assert preset['options']['rev.perDay'] == 9999 and preset['options']['new.perDay'] == 27
    assert payload['collection'] == {'config': {'fsrs': True, 'applyAllParentLimits': True},
                                     'preferences': {'scheduling': {'rollover': 5}}}
    assert payload['desktop'] == {'global': {'theme': 2}}


def test_settings_payload_defaults():
    preset = anki.settings_payload({})['presets'][0]
    assert preset['name'] == 'studious' and 'new.perDay' not in preset['options']
    assert preset['options']['newGatherPriority'] == 1


def test_settings_payload_done_preset():
    payload = anki.settings_payload({'anki_settings': {'done_preset': {'desiredRetention': 0.8}}}, 27, done=True)
    main, done = payload['presets']
    assert main['options']['new.perDay'] == 27 and 'id' not in done
    assert done['name'] == 'studious::done' and done['options']['desiredRetention'] == 0.8
    assert done['options']['new.perDay'] == 0 and done['options']['rev.perDay'] == 9999


def test_goals_written_as_one_value_are_a_list():
    assert anki.as_list('Test2') == ['Test2']
    assert anki.as_list(['Test2', 'Final']) == ['Test2', 'Final']
    assert anki.as_list(None) == []


class FakeAnki:
    """studiousNotes and studiousUpsertNotes against an in-memory set of guids."""

    def __init__(self):
        self.guids = set()

    def call(self, action, **params):
        if action == 'studiousNotes':
            return [dict(guid=g) for g in params['guids'] if g in self.guids]
        assert action == 'studiousUpsertNotes'
        out = []
        self.sent = getattr(self, 'sent', []) + params['notes']
        for n in params['notes']:
            out.append(dict(guid=n['guid'], action='updated' if n['guid'] in self.guids else 'created'))
            self.guids.add(n['guid'])
        return out


def write_lesson(vault, text):
    path = vault / 'courses/CS3214/lessons/07-condvars.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


def test_push_refuses_a_deck_outside_the_course(tmp_path):
    path = write_lesson(tmp_path, LESSON.replace('deck: CS3214::L_CondVars', 'deck: CS9999::L_CondVars'))
    report = anki.push(FakeAnki(), str(tmp_path), [str(path)])
    assert 'must be a topic' in report[0]['errors'][0]


def test_push_puts_a_bare_topic_under_the_course_deck(tmp_path):
    path = write_lesson(tmp_path, LESSON.replace('deck: CS3214::L_CondVars', 'deck: L_CondVars').replace('done: false', 'done: true'))
    fake = FakeAnki()
    report = anki.push(fake, str(tmp_path), [str(path)])
    assert report[0]['errors'] == [] and {n['deck'] for n in fake.sent} == {'CS3214::L_CondVars'}


def test_push_records_what_snapshot_fingerprints(tmp_path):
    sspec = importlib.util.spec_from_file_location('snapshot', SCRIPTS / 'snapshot.py')
    snapshot = importlib.util.module_from_spec(sspec)
    sspec.loader.exec_module(snapshot)
    path = write_lesson(tmp_path, LESSON.replace('done: false', 'done: true'))
    anki.push(FakeAnki(), str(tmp_path), [str(path)])
    record = json.loads((tmp_path / 'studious/pushed.json').read_text())
    cards = snapshot.lessons(tmp_path)[0]['cards']
    assert len(record) == 2 and {c['guid']: c['hash'] for c in cards} == record
    path.write_text(path.read_text().replace('Waking does not', 'Waking alone does not'))
    changed = {c['guid']: c['hash'] for c in snapshot.lessons(tmp_path)[0]['cards']}
    assert sum(record[g] != h for g, h in changed.items()) == 1  # only the edited card


def test_push_reports_a_refusal_and_goes_on_to_the_next_lesson(tmp_path):
    class Refusing(FakeAnki):
        def call(self, action, **params):
            if action == 'studiousNotes' and any(g.startswith('cs3214_aaaa') for g in params['guids']):
                raise anki.Refused('collection is busy')
            return super().call(action, **params)

    first = write_lesson(tmp_path, LESSON.replace('done: false', 'done: true'))
    second = first.with_name('08-semaphores.md')
    second.write_text(LESSON.replace('done: false', 'done: true').replace('cs3214_aaaaaaaaaaaa', 'cs3214_bbbbbbbbbbbb'))
    report = anki.push(Refusing(), str(tmp_path), [str(first.parent)])
    assert report[0]['errors'] == ['collection is busy'] and report[0]['created'] == 0
    assert report[1]['errors'] == [] and report[1]['created'] == 2


def test_push_drops_the_leech_tag_only_from_fixed_cards(tmp_path):
    path = write_lesson(tmp_path, LESSON.replace('done: false', 'done: true'))
    fake = FakeAnki()
    anki.push(fake, str(tmp_path), [str(path)])
    assert not any('dropTags' in n for n in fake.sent)  # new notes: nothing to drop
    fake.sent = []
    anki.push(fake, str(tmp_path), [str(path)])
    assert not any('dropTags' in n for n in fake.sent)  # unchanged blocks keep a leech tag
    path.write_text(path.read_text().replace('Waking does not', 'Waking alone does not'))
    fake.sent = []
    anki.push(fake, str(tmp_path), [str(path)])
    assert [n.get('dropTags') for n in fake.sent].count(['leech']) == 1  # only the edited card


def test_snapshot_lists_new_and_changed_sources(tmp_path, monkeypatch):
    sspec = importlib.util.spec_from_file_location('snapshot', SCRIPTS / 'snapshot.py')
    snapshot = importlib.util.module_from_spec(sspec)
    sspec.loader.exec_module(snapshot)
    home = tmp_path / 'home'
    monkeypatch.setenv('HOME', str(home))
    for rel, text in (('cs4824/notes/01.pdf', 'a'), ('cs4824/notes/deep/02.pdf', 'b'), ('cs4824/test2/review.pdf', 'c'),
                      ('cs4824/hw1/solution.py', 'd'), ('cs4824/notes/03.pdf', 'e'), ('done/notes/x.pdf', 'f')):
        (home / rel).parent.mkdir(parents=True, exist_ok=True)
        (home / rel).write_text(text)
    vault = tmp_path / 'vault'
    (vault / 'studious/manifests').mkdir(parents=True)
    known = snapshot.manifest.scan(['cs4824'])
    (vault / 'studious/manifests/CS4824.json').write_text(json.dumps(
        {'cs4824/notes/03.pdf': dict(known['cs4824/notes/03.pdf'], hash='old', status='ingested')}))
    config = {'courses': {'CS4824': {'folders': ['cs4824']}, 'Old': {'folders': ['done'], 'done': True}}}
    assert snapshot.sources(vault, config) == {'CS4824': dict(
        new={'cs4824/notes': {'course-material': 2}, 'cs4824/test2': {'exam-blueprint': 1}}, changed=1,
        base=['cs4824'])}  # homework (my-work) and done courses left out


def test_snapshot_spells_course_codes_as_the_config_does():
    sspec = importlib.util.spec_from_file_location('snapshot', SCRIPTS / 'snapshot.py')
    snapshot = importlib.util.module_from_spec(sspec)
    sspec.loader.exec_module(snapshot)
    items = [dict(course='cs3214'), dict(course='Other')]
    snapshot.spell_courses({'courses': {'CS3214': {}}}, items)
    assert [i['course'] for i in items] == ['CS3214', 'Other']


def test_daily_run_stopped_before_anki_leaves_anki_alone(tmp_path, monkeypatch, capsys):
    dspec = importlib.util.spec_from_file_location('daily', SCRIPTS / 'daily.py')
    daily = importlib.util.module_from_spec(dspec)
    dspec.loader.exec_module(daily)
    (tmp_path / 'studious').mkdir()
    (tmp_path / 'studious/config.md').write_text('---\nanki_profile: Max\n---\n')

    def stale(self):
        raise RuntimeError('studious-obsidian-sync is inactive')

    def no_anki(*args, **kwargs):
        raise AssertionError('Anki was called after the run stopped')

    monkeypatch.setattr(daily.Run, 'obsidian_sync', stale)
    monkeypatch.setattr(daily.anki.Anki, 'call', no_anki)
    assert daily.main(['--vault', str(tmp_path), '--dry-run']) == 1
    summary = json.loads(capsys.readouterr().out)
    assert summary['errors'] == ['obsidian sync: studious-obsidian-sync is inactive']


def test_a_group_nests_the_course_deck_and_folder():
    config = {'courses': {'CS3214': {'folders': ['Fall-2026/cs3214'], 'group': 'Fall-2026'},
                          'K8s': {'group': 'Work/Infra'}, 'CS1114': {}}}
    assert anki.course_root(config, 'CS3214') == 'Fall-2026::CS3214'
    assert anki.course_dir(config, 'CS3214') == 'courses/Fall-2026/CS3214'
    assert anki.course_root(config, 'K8s') == 'Work::Infra::K8s'
    assert anki.course_dir(config, 'K8s') == 'courses/Work/Infra/K8s'
    assert anki.goal_dir(config, 'K8s') == 'goals/Work/Infra' and anki.goal_dir(config, 'CS1114') == 'goals'
    assert anki.course_root(config, 'cs3214') == 'Fall-2026::CS3214'  # matched without case, spelled as the config does
    assert anki.course_root(config, 'CS1114') == 'CS1114' and anki.course_dir(config, 'CS1114') == 'courses/CS1114'
    assert anki.resolve_root(config, 'cs3214') == 'Fall-2026::CS3214'
    assert anki.resolve_root(config, 'CS1114') == 'CS1114'
    assert anki.resolve_root(config, 'Fall-2026::CS3214') == 'Fall-2026::CS3214'
    for bad in ('Fall::2026', 'Work//Infra', 'a"b', '../Archive', 'Work/./Infra', 'a\\b'):
        with pytest.raises(anki.Refused):
            anki.course_root({'courses': {'X': {'group': bad}}}, 'X')


def test_lesson_deck_follows_the_group_whatever_the_lesson_says():
    config = {'courses': {'CS3214': {'group': 'Fall-2026'}}}
    for written in ('L_CondVars', 'CS3214::L_CondVars', 'Fall-2026::CS3214::L_CondVars',
                    'Spring-2026::CS3214::L_CondVars', 'cs3214::L_CondVars'):
        assert anki.lesson_deck(config, 'CS3214', written) == 'Fall-2026::CS3214::L_CondVars', written
    assert anki.lesson_deck(config, 'CS3214', 'CS3214::L1::Part2') == 'Fall-2026::CS3214::L1::Part2'
    assert anki.lesson_deck({}, 'CS3214', 'L_CondVars') == 'CS3214::L_CondVars'
    for bad in ('Other::L_CondVars', 'CS3214::', '::L1'):
        with pytest.raises(anki.Refused):
            anki.lesson_deck(config, 'CS3214', bad)


def write_group_vault(vault, lesson_text):
    (vault / 'studious').mkdir(parents=True, exist_ok=True)
    (vault / 'studious/config.md').write_text(
        '---\ncourses:\n  CS3214: {folders: [Fall-2026/cs3214], group: Fall-2026}\n---\n')
    path = vault / 'courses/Fall-2026/CS3214/lessons/07-condvars.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(lesson_text, encoding='utf-8')
    return path


def test_push_with_a_group_wants_the_lesson_deck_under_the_group(tmp_path):
    grouped_lesson = LESSON.replace('deck: CS3214::L_CondVars', 'deck: Fall-2026::CS3214::L_CondVars')
    path = write_group_vault(tmp_path, grouped_lesson.replace('done: false', 'done: true'))
    fake = FakeAnki()
    report = anki.push(fake, str(tmp_path), [str(path)])
    assert report[0]['errors'] == [] and report[0]['created'] == 2
    path.write_text(LESSON.replace('done: false', 'done: true'))  # written before the course had a group
    fake = FakeAnki()
    report = anki.push(fake, str(tmp_path), [str(path)])
    assert report[0]['errors'] == [] and {n['deck'] for n in fake.sent} == {'Fall-2026::CS3214::L_CondVars'}
    (tmp_path / 'studious/config.md').write_text('---\ncourses:\n  CS3214: {group: "Fall::2026"}\n---\n')
    report = anki.push(FakeAnki(), str(tmp_path), [str(path)])
    assert 'use names separated by /' in report[0]['errors'][0]  # reported, not a crash


def test_snapshot_finds_grouped_lessons_and_maps_the_forecast_to_course_codes(tmp_path):
    sspec = importlib.util.spec_from_file_location('snapshot', SCRIPTS / 'snapshot.py')
    snapshot = importlib.util.module_from_spec(sspec)
    sspec.loader.exec_module(snapshot)
    write_group_vault(tmp_path, LESSON.replace('deck: CS3214::L_CondVars', 'deck: L_CondVars'))
    noise = tmp_path / 'courses/Fall-2026/CS3214/practice/07-x/lessons/readme.md'  # practice code isn't walked
    noise.parent.mkdir(parents=True)
    noise.write_text(LESSON)
    found = snapshot.lessons(tmp_path)
    assert [l['path'] for l in found] == ['courses/Fall-2026/CS3214/lessons/07-condvars.md']
    assert found[0]['deck'] == 'Fall-2026::CS3214::L_CondVars'  # matches the cards' decks in Anki

    class Forecasting:
        def call(self, action, **params):
            assert action == 'studiousForecast' and params['roots'] == ['Fall-2026::CS3214']
            return dict(preset='studious', params=[], perNewCard=[1.0], load={'Fall-2026::CS3214': [3.0]})

    fc = snapshot.forecast(Forecasting(), {'CS3214': 'Fall-2026::CS3214'})
    assert fc['load'] == {'CS3214': [3.0]}


def test_snapshot_reads_goals_in_group_folders(tmp_path):
    sspec = importlib.util.spec_from_file_location('snapshot', SCRIPTS / 'snapshot.py')
    snapshot = importlib.util.module_from_spec(sspec)
    sspec.loader.exec_module(snapshot)
    for rel in ('goals/Fall-2026/CS3214-Test2.md', 'goals/Other-Quiz1.md'):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        code = path.stem.split('-')[0]
        path.write_text(f'---\ntype: goal\nid: {path.stem.split("-")[1]}\ncourse: {code}\n---\n')
    found = {g['course']: g['path'] for g in snapshot.goals(tmp_path)}
    assert found == {'CS3214': 'goals/Fall-2026/CS3214-Test2.md', 'Other': 'goals/Other-Quiz1.md'}


def test_placement_warns_about_misplaced_notes_and_keeps_one_copy_of_a_goal():
    sspec = importlib.util.spec_from_file_location('snapshot', SCRIPTS / 'snapshot.py')
    snapshot = importlib.util.module_from_spec(sspec)
    sspec.loader.exec_module(snapshot)
    config = {'courses': {'CS3214': {'group': 'Fall-2026'}}}
    state = dict(
        lessons=[dict(course='CS3214', path='courses/Fall-2026/CS3214/lessons/01-a.md'),
                 dict(course='CS3214', path='courses/CS3214/lessons/02-b.md'),
                 dict(course='Other', path='courses/Other/lessons/01-a.md')],
        goals=[dict(course='CS3214', id='Final', path='goals/CS3214-Final.md'),
               dict(course='CS3214', id='Final', path='goals/Fall-2026/CS3214-Final.md'),
               dict(course='CS3214', id='Test2', path='goals/CS3214-Test2.md'),
               dict(course='Other', id='Quiz', path='goals/Other-Quiz.md')])
    warnings = snapshot.placement(config, state)
    assert [g['path'] for g in state['goals']] == ['goals/Fall-2026/CS3214-Final.md', 'goals/CS3214-Test2.md',
                                                   'goals/Other-Quiz.md']
    assert any('courses/CS3214/lessons/02-b.md is outside' in w for w in warnings)
    assert any('CS3214 Final is in 2 goal notes' in w and 'uses goals/Fall-2026/CS3214-Final.md' in w for w in warnings)
    assert any('goals/CS3214-Test2.md is outside' in w for w in warnings)
    assert len(warnings) == 3  # nothing about Other, which has no group


def test_daily_moves_a_deck_whose_group_changed(tmp_path):
    dspec = importlib.util.spec_from_file_location('daily', SCRIPTS / 'daily.py')
    daily = importlib.util.module_from_spec(dspec)
    dspec.loader.exec_module(daily)
    (tmp_path / 'studious').mkdir()
    (tmp_path / 'studious/config.md').write_text(
        '---\nanki_profile: Max\ncourses:\n  CS3214: {group: Fall-2026}\n  CS4824: {group: Fall-2026}\n'
        '  Rust: {}\n  Two: {group: G}\n---\n')

    class Client:
        renamed = []

        def call(self, action, **params):
            if action == 'deckNames':
                return ['CS3214', 'CS3214::L1', 'Fall-2026', 'Fall-2026::CS4824', 'Rust',
                        'A::Two', 'B::Two', 'Fall-2026::CS4824::CS3214']
            assert action == 'studiousRenameDeck'
            self.renamed.append((params['old'], params['new']))

    run = daily.Run(str(tmp_path), False)
    run.client = Client()
    moved = run.move_decks()
    assert Client.renamed == [('CS3214', 'Fall-2026::CS3214')]  # not the CS4824 subdeck named CS3214
    assert moved == [dict(course='CS3214', old='CS3214', new='Fall-2026::CS3214')]
    assert any('Two: its deck G::Two' in n and '2 decks could be it' in n for n in run.notes)


def apply_run(tmp_path, patch, fail_cram=None):
    """A daily Run whose Anki records every call; `fail_cram` is the refusal studiousCram raises."""
    dspec = importlib.util.spec_from_file_location('daily', SCRIPTS / 'daily.py')
    daily = importlib.util.module_from_spec(dspec)
    dspec.loader.exec_module(daily)
    (tmp_path / 'studious').mkdir(exist_ok=True)
    (tmp_path / 'studious/config.md').write_text('---\nanki_profile: Max\n---\n')
    calls = []

    class Fake:
        def call(self, action, **params):
            calls.append((action, params))
            if action == 'studiousApplySettings':
                return dict(changes=[])
            if action == 'studiousCram' and fail_cram:
                raise daily.anki.Refused(fail_cram)
            return {}

    run = daily.Run(str(tmp_path), False)
    run.client, run.patch = Fake(), patch
    return run, calls


PREP = dict(name='Fall-2026::CS3214::Test2 prep', goal='Test2', course='CS3214', search='cid:7,3', limit=2,
            order=8, search2='deck:"Fall-2026::CS3214" tag:CS3214::exam::Test2 is:new -is:suspended', limit2=9999,
            order2=6, weak=2, unseen=40, cards=42)


def plan_with(prep, delete=(), **kw):
    return dict(courses={}, total_new_per_day=0, prep=prep, prep_delete=list(delete), suspend=[1], unsuspend=[],
                retired={}, reading=kw.pop('reading', {}), notes=kw.pop('notes', []), **kw)


def test_a_refused_prep_deck_does_not_stop_the_rest_of_the_apply(tmp_path):
    run, calls = apply_run(tmp_path, 11, fail_cram='studious refused: something odd')
    assert run.apply(plan_with([PREP], ['Fall-2026::CS3214::Old prep'])) == 'applied'
    assert [a for a, _ in calls[-2:]] == ['studiousCram', 'studiousSuspend']
    assert any('Test2 prep not built' in n for n in run.attention)


def test_prep_deck_on_patch_v11_sends_both_filters(tmp_path):
    run, calls = apply_run(tmp_path, 11)
    run.apply(plan_with([PREP], ['Fall-2026::CS3214::Test2 cram']))
    cram = [p for a, p in calls if a == 'studiousCram']
    assert cram == [{k: PREP[k] for k in ('name', 'search', 'limit', 'order', 'search2', 'limit2', 'order2')}]
    assert ('studiousCramDelete', dict(name='Fall-2026::CS3214::Test2 cram')) in calls
    assert not run.attention


def test_an_older_patch_gets_the_weak_cards_as_a_cram_deck(tmp_path):
    run, calls = apply_run(tmp_path, 10)
    weak_only = {k: v for k, v in PREP.items() if not k.endswith('2')}  # the planner leaves filter 2 out
    run.apply(plan_with([dict(weak_only, unseen=0, cards=2)], ['Fall-2026::CS3214::Exam3 cram']))
    decks = [(a, p['name']) for a, p in calls if a in ('studiousCram', 'studiousCramDelete')]
    assert decks == [('studiousCramDelete', 'Fall-2026::CS3214::Exam3 cram'),
                     ('studiousCramDelete', 'Fall-2026::CS3214::Test2 cram'),  # emptied, then built again
                     ('studiousCram', 'Fall-2026::CS3214::Test2 cram')]
    assert [p for a, p in calls if a == 'studiousCram'] == [dict(name='Fall-2026::CS3214::Test2 cram',
                                                                   search='cid:7,3', limit=2)]
    assert any('patch v11' in n and 'v10' in n for n in run.attention)


def test_old_decks_are_emptied_before_reposition_and_the_build(tmp_path):
    run, calls = apply_run(tmp_path, 11)
    plan = plan_with([PREP], ['Fall-2026::CS3214::Test2 cram'])
    plan['courses'] = {'CS3214': dict(new_per_day=3, new_today=None, reposition=[5, 6])}
    run.apply(plan)
    order = [a for a, _ in calls if a in ('studiousCramDelete', 'studiousReposition', 'studiousCram')]
    assert order == ['studiousCramDelete', 'studiousCramDelete', 'studiousReposition', 'studiousCram']


def test_apply_writes_the_reading_record_and_the_plan_notes(tmp_path):
    run, _ = apply_run(tmp_path, 11)
    run.apply(plan_with([], reading={'courses/CS3214/lessons/08-semaphores.md': '2026-11-02'},
                        notes=['CS3214 Test2 prep: nothing below 0.90 on exam day.']))
    import json as _json
    assert _json.loads((tmp_path / 'studious/reading.json').read_text()) == {
        'courses/CS3214/lessons/08-semaphores.md': '2026-11-02'}
    assert 'CS3214 Test2 prep: nothing below 0.90 on exam day.' in run.notes


# ---- orphans: a card block removed from the vault ----------------------------------------------------

class FakeCollection:
    """Enough of the patched AnkiConnect for whole daily runs: notes keyed by guid, cards with decks
    and suspension, and the read and write actions the run uses."""

    def __init__(self):
        self.notes, self.next_id, self.calls = {}, 1, []
        self.Refused = anki.Refused  # the daily run loads its own copies of anki.py

    def cards(self):
        return [(g, n, c) for g, n in self.notes.items() for c in n['cards']]

    def call(self, action, **params):
        self.calls.append((action, params))
        if action == 'studiousVersion':
            return dict(profile='Max')
        if action == 'deckNames':
            return sorted({c['deck'] for _, _, c in self.cards()} | {d.rsplit('::', 1)[0] for _, _, c in self.cards()
                                                                     for d in [c['deck']]})
        if action == 'studiousObserve':
            root = params['root']
            cards = [dict(cardId=c['id'], noteId=c['id'], guid=g, deck=c['deck'], type=0, queue=-1 if c['suspended'] else 0,
                          due=c['id'], reps=0, lapses=0, flag=0, suspended=c['suspended'], tags=n['tags'], retrievability=None)
                     for g, n, c in self.cards() if c['deck'] == root or c['deck'].startswith(root + '::')]
            if not cards:
                raise self.Refused(f'deck {root} does not exist')
            return dict(root=root, today=1000, dayCutoff=0, cards=cards, revlog=[], counts=dict(new=0, learning=0, review=0),
                        preset=dict(name='studious', newPerDay=5, reviewsPerDay=9999))
        if action == 'studiousNotes':
            return [dict(guid=g, fields=self.notes[g]['fields'], tags=self.notes[g]['tags'],
                         cards=[dict(cardId=c['id'], deck=c['deck'], queue=-1 if c['suspended'] else 0)
                                for c in self.notes[g]['cards']]) for g in params['guids'] if g in self.notes]
        if action == 'studiousUpsertNotes':
            out = []
            for n in params['notes']:
                if n['guid'] in self.notes:
                    self.notes[n['guid']].update(fields=n['fields'], tags=n['tags'])
                    out.append(dict(guid=n['guid'], action='updated'))
                else:
                    self.notes[n['guid']] = dict(fields=n['fields'], tags=n['tags'],
                                                 cards=[dict(id=self.next_id, deck=n['deck'], suspended=False)])
                    self.next_id += 1
                    out.append(dict(guid=n['guid'], action='created'))
            return out
        if action == 'studiousSuspend':
            for _, _, c in self.cards():
                if c['id'] in params['cardIds']:
                    c['suspended'] = params['suspended']
            return dict(cards=len(params['cardIds']), suspended=params['suspended'])
        if action == 'studiousForecast':
            raise self.Refused('patch older than v8')
        if action == 'studiousApplySettings':
            return dict(changes=[])
        if action == 'studiousOptimize':
            return dict(ran=False)
        return {}

    def suspended(self, guid):
        return [c['suspended'] for c in self.notes[guid]['cards']]


CARD = """> [!card]- {title}
> %% id: {guid} · type: Basic+ %%
> #### Front
> {title}?
> #### Back
> Because.
> #### Source
> slide {n}
"""


def grouped_lesson(name, deck, *cards):
    return (f'---\ncourse: CS3214\ndeck: {deck}\ngoals: [Test2]\nverified: true\ndone: true\n---\n# {name}\n\n'
            + '\n'.join(CARD.format(title=t, guid=g, n=i) for i, (t, g) in enumerate(cards)))


def daily_run(daily, vault, fake, monkeypatch, capsys):
    fake.Refused = daily.snapshot.anki.Refused
    monkeypatch.setattr(daily.anki.Anki, 'call', lambda self, action, **p: fake.call(action, **p))
    monkeypatch.setattr(daily.snapshot.anki.Anki, 'call', lambda self, action, **p: fake.call(action, **p))
    code = daily.main(['--vault', str(vault), '--no-calendar'])
    summary = json.loads(capsys.readouterr().out)
    assert code == 0, summary['errors']
    return (vault / 'studious/inbox.md').read_text()


def test_daily_suspends_a_removed_card_and_restores_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    dspec = importlib.util.spec_from_file_location('daily', SCRIPTS / 'daily.py')
    daily = importlib.util.module_from_spec(dspec)
    dspec.loader.exec_module(daily)
    vault = tmp_path / 'vault'
    (vault / 'studious').mkdir(parents=True)
    (vault / 'studious/config.md').write_text('---\nanki_profile: Max\ncourses:\n  CS3214:\n    group: Fall-2026\n---\n')
    lessons = vault / 'courses/Fall-2026/CS3214/lessons'
    lessons.mkdir(parents=True)
    a, b, c = 'cs3214_aaaaaaaaaaaa', 'cs3214_bbbbbbbbbbbb', 'cs3214_cccccccccccc'
    first, second = lessons / '01-locks.md', lessons / '02-condvars.md'
    first.write_text(grouped_lesson('Locks', 'L01_Locks', ('Why <b>lock</b>', a), ('Spin or block', b)))
    second.write_text(grouped_lesson('Condvars', 'L02_CondVars', ('Why recheck', c)))
    fake = FakeCollection()
    assert 'Suspended' not in daily_run(daily, vault, fake, monkeypatch, capsys)
    assert fake.notes[a]['cards'][0]['deck'] == 'Fall-2026::CS3214::L01_Locks'

    # a dropped from the vault, b moved to the other lesson, c untouched
    first.write_text(grouped_lesson('Locks', 'L01_Locks'))
    second.write_text(grouped_lesson('Condvars', 'L02_CondVars', ('Why recheck', c), ('Spin or block', b)))
    inbox = daily_run(daily, vault, fake, monkeypatch, capsys)
    assert fake.suspended(a) == [True] and fake.suspended(b) == [False] and fake.suspended(c) == [False]
    assert '## Suspended (card block gone)' in inbox
    assert f'- CS3214: `{a}` "Why lock?" in Fall-2026::CS3214::L01_Locks' in inbox and b not in inbox
    assert json.loads((vault / 'studious/orphaned.json').read_text()) == {a: dict(course='CS3214', cardIds=[1])}

    # the next run leaves it suspended and says nothing more
    assert 'Suspended' not in daily_run(daily, vault, fake, monkeypatch, capsys) and fake.suspended(a) == [True]

    # the block comes back unchanged: the daily run unsuspends it
    first.write_text(grouped_lesson('Locks', 'L01_Locks', ('Why <b>lock</b>', a)))
    daily_run(daily, vault, fake, monkeypatch, capsys)
    assert fake.suspended(a) == [False] and json.loads((vault / 'studious/orphaned.json').read_text()) == {}


def test_push_unsuspends_a_restored_card(tmp_path):
    path = write_lesson(tmp_path, LESSON.replace('done: false', 'done: true'))
    fake = FakeCollection()
    anki.push(fake, str(tmp_path), [str(path)])
    gone = 'cs3214_aaaaaaaaaaaa'
    fake.call('studiousSuspend', cardIds=[c['id'] for c in fake.notes[gone]['cards']], suspended=True)
    (tmp_path / 'studious/orphaned.json').write_text(json.dumps({gone: dict(course='CS3214', cardIds=[2])}))
    report = anki.push(fake, str(tmp_path), [str(path)])
    assert report[0]['restored'] == 1 and fake.suspended(gone) == [False]
    assert json.loads((tmp_path / 'studious/orphaned.json').read_text()) == {}


def test_vault_guids_finds_blocks_anywhere_but_dot_folders_reports_and_practice(tmp_path):
    def note(rel, guid):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(CARD.format(title='x', guid=guid, n=1))
    note('misplaced.md', 'cs3214_aaaaaaaaaaaa')
    note('.trash/old.md', 'cs3214_bbbbbbbbbbbb')
    note('studious/reports/2026-11-06-verify-07.md', 'cs3214_cccccccccccc')  # a report quoting a dropped card
    note('courses/CS3214/practice/07-x/notes.md', 'cs3214_dddddddddddd')
    note('courses/CS3214/lessons/studious/odd.md', 'cs3214_eeeeeeeeeeee')  # only the top studious/ is skipped
    assert anki.vault_guids(tmp_path) == {'cs3214_aaaaaaaaaaaa', 'cs3214_eeeeeeeeeeee'}
    assert anki.vault_guids(tmp_path, skip={tmp_path / 'misplaced.md'}) == {'cs3214_eeeeeeeeeeee'}
    assert anki.find_card(tmp_path, 'cs3214_bbbbbbbbbbbb') is None  # flagged agrees: a trashed block is gone
    assert anki.find_card(tmp_path, 'cs3214_aaaaaaaaaaaa')['path'].endswith('misplaced.md')


def orphan_run(tmp_path, client, dry_run=False):
    dspec = importlib.util.spec_from_file_location('daily', SCRIPTS / 'daily.py')
    daily = importlib.util.module_from_spec(dspec)
    dspec.loader.exec_module(daily)
    (tmp_path / 'studious').mkdir(exist_ok=True)
    (tmp_path / 'studious/config.md').write_text('---\nanki_profile: Max\n---\n')
    run = daily.Run(str(tmp_path), dry_run)
    run.client = client
    client.Refused = daily.anki.Refused
    return daily, run


def test_a_failed_unsuspend_keeps_the_restored_note_recorded(tmp_path):
    fake = FakeCollection()
    fake.notes = {'cs3214_aaaaaaaaaaaa': dict(fields={}, tags=[], cards=[dict(id=1, deck='CS3214::L01', suspended=True)]),
                  'cs3214_bbbbbbbbbbbb': dict(fields={'Front': 'Gone'}, tags=[], cards=[dict(id=2, deck='CS3214::L01', suspended=False)])}

    class Flaky(FakeCollection):
        def call(self, action, **params):
            if action == 'studiousSuspend' and params['suspended'] is False:
                raise self.Refused('collection is busy')
            return fake.call(action, **params)

    daily, run = orphan_run(tmp_path, Flaky())
    restored = {'cs3214_aaaaaaaaaaaa': dict(course='CS3214', cardIds=[1])}
    gone = {'cs3214_bbbbbbbbbbbb': dict(course='CS3214', cardIds=[2])}
    plan = dict(orphans=dict(suspend=[2], unsuspend=[1], restored=restored,
                             new=[dict(guid='cs3214_bbbbbbbbbbbb', course='CS3214', deck='CS3214::L01', cardIds=[2])]),
                orphaned=gone)
    assert run.step('orphans', run.apply_orphans, plan) is None and run.errors
    assert fake.suspended('cs3214_bbbbbbbbbbbb') == [True]  # suspended, and recorded before the call
    assert json.loads((tmp_path / 'studious/orphaned.json').read_text()) == {**gone, **restored}  # retried next run


def test_an_apply_failure_does_not_hold_back_the_orphans(tmp_path):
    fake = FakeCollection()
    calls = []

    class Broken(FakeCollection):
        def call(self, action, **params):
            calls.append(action)
            if action == 'studiousApplySettings':
                raise self.Refused('settings refused')
            return fake.call(action, **params)

    daily, run = orphan_run(tmp_path, Broken())
    plan = dict(courses={}, total_new_per_day=0, prep=[], prep_delete=[], suspend=[], unsuspend=[], retired={},
                orphans=dict(suspend=[7], unsuspend=[], restored={}, new=[]), orphaned={'g': dict(course='X', cardIds=[7])})
    run.step('apply to anki', run.apply, plan)  # two steps, as main runs them
    run.step('orphans', run.apply_orphans, plan)
    assert calls == ['studiousApplySettings', 'studiousSuspend'] and len(run.errors) == 1


def test_a_dry_run_says_what_it_would_suspend(tmp_path):
    fake = FakeCollection()
    daily, run = orphan_run(tmp_path, fake, dry_run=True)
    plan = dict(orphans=dict(suspend=[2], unsuspend=[], restored={},
                             new=[dict(guid='cs3214_bbbbbbbbbbbb', course='CS3214', deck='CS3214::L01', cardIds=[2])]),
                orphaned={'cs3214_bbbbbbbbbbbb': dict(course='CS3214', cardIds=[2])})
    assert run.apply_orphans(plan) == 'dry run' and fake.calls == []
    assert run.notes == ['Dry run: would suspend 1 note whose card block is gone (cs3214_bbbbbbbbbbbb) and '
                         'unsuspend 0 cards of restored blocks.']
    assert not (tmp_path / 'studious/orphaned.json').exists()


def test_push_leaves_a_restored_card_of_a_retired_course_suspended(tmp_path):
    path = write_lesson(tmp_path, LESSON.replace('done: false', 'done: true'))
    (tmp_path / 'studious').mkdir(exist_ok=True)
    (tmp_path / 'studious/config.md').write_text('---\ncourses:\n  CS2505:\n    retired: true\n---\n')
    fake = FakeCollection()
    anki.push(fake, str(tmp_path), [str(path)])
    gone = 'cs3214_aaaaaaaaaaaa'
    fake.call('studiousSuspend', cardIds=[2], suspended=True)
    (tmp_path / 'studious/orphaned.json').write_text(json.dumps({gone: dict(course='CS2505', cardIds=[2])}))
    report = anki.push(fake, str(tmp_path), [str(path)])
    assert 'restored' not in report[0] and fake.suspended(gone) == [True]


class DeletingAnki:
    """studiousNotes and deleteNotes against in-memory notes keyed by guid."""

    def __init__(self, notes):
        self.notes = dict(notes)  # guid -> noteId
        self.deleted = []

    def call(self, action, **params):
        if action == 'studiousNotes':
            return [dict(guid=g, noteId=self.notes[g], cards=[dict(reps=2)]) for g in params['guids'] if g in self.notes]
        assert action == 'studiousDeleteNotes'
        self.deleted += [self.notes[g] for g in params['guids'] if g in self.notes]
        self.notes = {g: i for g, i in self.notes.items() if g not in params['guids']}
        return dict(deleted=len(params['guids']), cards=0, missing=[])


def test_delete_notes_deletes_and_cleans_the_records(tmp_path):
    write_lesson(tmp_path, LESSON)  # still holds cs3214_aaaaaaaaaaaa
    (tmp_path / 'studious').mkdir()
    (tmp_path / 'studious/pushed.json').write_text(json.dumps({'cs3214_bbbbbbbbbbbb': 'h1', 'cs3214_aaaaaaaaaaaa': 'h2'}))
    (tmp_path / 'studious/orphaned.json').write_text(json.dumps({'cs3214_bbbbbbbbbbbb': {'course': 'CS3214', 'cardIds': [7]}}))
    fake = DeletingAnki({'cs3214_bbbbbbbbbbbb': 11})
    dry = anki.delete_notes(fake, str(tmp_path), ['cs3214_bbbbbbbbbbbb', 'cs3214_cccccccccccc'], dry_run=True)
    assert dry['notes'] == 1 and dry['reviews'] == 2 and dry['missing'] == ['cs3214_cccccccccccc'] and not fake.deleted
    out = anki.delete_notes(fake, str(tmp_path), ['cs3214_bbbbbbbbbbbb', 'cs3214_cccccccccccc'])
    assert fake.deleted == [11] and out['notes'] == 1
    assert json.loads((tmp_path / 'studious/pushed.json').read_text()) == {'cs3214_aaaaaaaaaaaa': 'h2'}
    assert json.loads((tmp_path / 'studious/orphaned.json').read_text()) == {}


def test_delete_notes_refuses_a_guid_still_in_the_vault_or_not_studious(tmp_path):
    write_lesson(tmp_path, LESSON)
    fake = DeletingAnki({'cs3214_aaaaaaaaaaaa': 1, 'handmade-guid': 2})
    for guids in (['cs3214_aaaaaaaaaaaa'], ['handmade-guid']):
        with pytest.raises(anki.Refused):
            anki.delete_notes(fake, str(tmp_path), guids)
    assert not fake.deleted
