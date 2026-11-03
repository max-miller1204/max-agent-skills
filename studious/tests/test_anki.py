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
    path = write_lesson(tmp_path, LESSON.replace('deck: CS3214::L_CondVars', 'deck: L_CondVars'))
    report = anki.push(FakeAnki(), str(tmp_path), [str(path)])
    assert 'must be under the course deck' in report[0]['errors'][0]


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


def test_snapshot_spells_course_codes_as_the_config_does():
    sspec = importlib.util.spec_from_file_location('snapshot', SCRIPTS / 'snapshot.py')
    snapshot = importlib.util.module_from_spec(sspec)
    sspec.loader.exec_module(snapshot)
    items = [dict(course='cs3214'), dict(course='Other')]
    snapshot.spell_courses({'courses': {'CS3214': {}}}, items)
    assert [i['course'] for i in items] == ['CS3214', 'Other']
