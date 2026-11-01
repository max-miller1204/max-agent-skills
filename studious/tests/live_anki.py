"""Push a sample lesson through anki.py into a running Anki on a TEST profile, check, clean up.

    uv run --with pyyaml tests/live_anki.py [--profile testing]

Uses a throwaway vault in a temp folder and the deck tree `StudiousSelfTest`. Refuses the Max profile.
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ANKI = Path(__file__).resolve().parent.parent / 'scripts/anki.py'
PNG = bytes.fromhex('89504e470d0a1a0a0000000d4948445200000001000000010806000000'
                    '1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082')
LESSON = r"""---
course: StudiousSelfTest
deck: StudiousSelfTest::L01_Sample
goals: [Exam1]
verified: true
done: false
---
# Sample lesson

> [!card]- Mechanism
> %% type: Basic+ · tags: cheatsheet %%
> #### Front
> Why does $P(A \mid B) = \frac{P(A \land B)}{P(B)}$ divide by $P(B)$?
> #### Back
> It renormalizes to the worlds where $B$ holds.
> #### Source
> L01 slide 3

> [!card]- Cloze
> %% type: Cloze+ %%
> #### Text
> A {{c1::mutex}} gives {{c2::mutual exclusion}}.
> #### Source
> L01 slide 4

> [!card]- Predict
> %% type: Code+ %%
> #### Prompt
> What does this print?
> #### Code
> ```python
> print(sum(range(4)))
> ```
> #### Answer
> `6`
> #### Source
> L01 slide 5

> [!card]- Typed
> %% type: Basic+ (type in) %%
> #### Front
> Exit status of `true`? (number)
> ![[selftest-fig.png]]
> #### Back
> 0
> #### Source
> L01 slide 6

> [!card]- Figure
> %% type: Image Occlusion %%
> #### Image
> ![[selftest-fig.png]]
> #### Occlusion
> {{c1::image-occlusion:rect:left=0.1000:top=0.1000:width=0.3000:height=0.2000:oi=1}}
> #### Header
> Sample figure
> #### Source
> L01 slide 7
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', default='testing')
    args = parser.parse_args()
    if args.profile == 'Max':
        sys.exit('refusing to test on the Max profile')
    failures = []

    def ok(label, cond, detail=''):
        print(('PASS ' if cond else 'FAIL ') + label + ('' if cond else f'  {detail}'))
        if not cond:
            failures.append(label)

    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp)
        (vault / 'attachments').mkdir()
        (vault / 'attachments/selftest-fig.png').write_bytes(PNG)
        lesson = vault / 'courses/StudiousSelfTest/lessons/01-sample.md'
        lesson.parent.mkdir(parents=True)
        lesson.write_text(LESSON)

        def run(*cmd):
            out = subprocess.run(['uv', 'run', '-q', str(ANKI), '--vault', str(vault), '--profile', args.profile, *cmd],
                                 capture_output=True, text=True)
            try:
                return json.loads(out.stdout)
            except json.JSONDecodeError:
                raise SystemExit(f'{cmd}: {out.stdout}\n{out.stderr}')

        if run('status').get('profile') != args.profile:
            sys.exit(f'open profile is not {args.profile!r}')
        run('ensure-note-types')

        held = run('push', str(lesson))[0]
        ok('not-done lesson holds every new card', held['held'] == 5 and held['created'] == 0, held)
        text = lesson.read_text()
        cards = run('cards', str(lesson))
        ok('ids written back', all(c['id'] and c['id'].startswith('studiousselftest_') for c in cards), cards)
        ok('write-back kept the rest of the note', text.count('#### ') == LESSON.count('#### '))

        lesson.write_text(text.replace('done: false', 'done: true'))
        made = run('push', str(lesson))[0]
        ok('done lesson creates every card', made['created'] == 5 and not made['errors'], made)
        again = run('push', str(lesson))[0]
        ok('second push changes nothing', again['unchanged'] == 5, again)

        guid = cards[0]['id']
        notes = run('call', 'studiousNotes', json.dumps({'profile': args.profile, 'guids': [c['id'] for c in cards]}))
        by_type = {n['modelName']: n for n in notes}
        basic = by_type['Basic+']['fields']
        ok('math became MathJax', r'\(P(A \mid B) = \frac{P(A \land B)}{P(B)}\)' in basic['Front'], basic['Front'])
        ok('cheatsheet tags', sorted(by_type['Basic+']['tags']) == ['StudiousSelfTest::cheatsheet::Exam1',
                                                                   'StudiousSelfTest::exam::Exam1'], by_type['Basic+']['tags'])
        code = by_type['Code+']['fields']['Code']
        ok('code highlighted', code.startswith('<pre class="hl"><code>') and 'class="nb"' in code, code)
        typed = by_type['Basic+ (type in)']['fields']
        ok('media uploaded and referenced', 'src="studiousselftest_selftest-fig.png"' in typed['Front'], typed['Front'])
        io = by_type['Image Occlusion']['fields']
        ok('occlusion source under Back Extra', 'Source: L01 slide 7' in io['Back Extra'], io)
        decks = {c['deck'] for n in notes for c in n['cards']}
        ok('cards in the lesson subdeck', decks == {'StudiousSelfTest::L01_Sample'}, decks)

        edited = lesson.read_text().replace('It renormalizes', 'It rescales').replace('goals: [Exam1]', 'goals: [Exam2]')
        lesson.write_text(edited)
        upd = run('push', str(lesson))[0]
        ok('edit updates in place', upd['updated'] == 5 and upd['created'] == 0, upd)
        tags = run('call', 'studiousNotes', json.dumps({'profile': args.profile, 'guids': [guid]}))[0]['tags']
        ok('goal change moves the exam tag', 'StudiousSelfTest::exam::Exam2' in tags
           and 'StudiousSelfTest::exam::Exam1' not in tags, tags)

        seen = run('observe', 'StudiousSelfTest')
        ok('observe sees the cards', len(seen['cards']) >= 6, len(seen['cards']))  # cloze has 2 cards
        card_id = next(c['cardId'] for c in seen['cards'] if c['guid'] == guid)
        run('set-flag', '1', str(card_id))
        flagged = run('flagged', 'StudiousSelfTest')
        ok('flagged card maps back to its lesson block', len(flagged) == 1 and flagged[0]['card']
           and flagged[0]['card']['path'] == str(lesson), flagged)

        limits = run('limits', 'StudiousSelfTest', '--new', '5', '--by-position')
        ok('limits applied', limits.get('newPerDay') == 5, limits)

        names = run('call', 'deckNames')
        run('call', 'deleteDecks', json.dumps({'decks': [n for n in names if n.startswith('StudiousSelfTest')],
                                               'cardsToo': True}))
        run('call', 'removeDeckConfigId', json.dumps({'configId': limits['presetId']}))
        run('call', 'deleteMediaFile', json.dumps({'filename': 'studiousselftest_selftest-fig.png'}))
        ok('cleanup', not [n for n in run('call', 'deckNames') if n.startswith('StudiousSelfTest')])

    print(f'\n{len(failures)} failed' if failures else '\nall passed')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
