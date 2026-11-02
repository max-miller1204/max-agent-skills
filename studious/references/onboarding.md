# Setup

`/studious setup` (or any run without `studious/config.md`). Work through the sections in order;
skip what's already done (each step checks first). Installs, sign-ins and account changes are the
user's to do: give them the exact command and wait.

## 1. Machine check

```bash
uv run scripts/anki.py doctor
```

It clones `max-miller1204/studious-connectors` to `~/.local/share/studious/connectors` if missing and
checks Anki, the patched AnkiConnect, the calendar server and its credentials. Each failure comes with
its fix; the connectors README and `calendar/README.md` have the details (Google Cloud project, OAuth
client in production, sign-in, the Studious calendar).

## 2. Vault

The vault is `~/vault` unless the user wants it elsewhere (then they set `STUDIOUS_VAULT` in their
shell profile and in the scheduler). Create or repair it:

```bash
python3 scripts/setup_vault.py
```

Then sync, one method per device (never the desktop app's Sync and Headless on the same machine):

| Device | How |
|---|---|
| The scheduled machine (Linux desktop) | Obsidian Headless as a user service, below. If the Obsidian app is also used there, keep its Sync plugin off. |
| Mac, phone | The Obsidian app with the core Sync plugin, connected to the same remote vault. |

Headless setup (needs an Obsidian Sync subscription and Node 22+):

```bash
dots pkg add npm:obsidian-headless       # or: npm install -g obsidian-headless
ob login
ob sync-create-remote --name vault --encryption standard   # first device only; others: ob sync-list-remote
ob sync-setup --vault <id from the line above> --path ~/vault --device-name "$(hostname)"
ob sync-config --path ~/vault --file-types image,pdf,unsupported --excluded-folders studious/cache \
  --configs appearance,appearance-data
ob sync --path ~/vault                    # first sync, watch it finish
```

`unsupported` makes JSON and code files sync too (manifests, the calendar record, practice code).
`appearance-data` carries the callout CSS snippet to the other devices. Dot-folders such as `.git`
never sync, so each machine keeps its own undo log.

If `ob sync-config` fails with "Could not locate the bindings file" (mise installs skip native
builds), fetch the prebuilt SQLite module and retry:

```bash
cd "$(dirname "$(find ~/.local/share/mise/installs/npm-obsidian-headless -path '*node_modules/better-sqlite3/package.json' | head -1)")"
npx --yes prebuild-install@7
```

An upgrade of `obsidian-headless` needs this again.

Run it continuously:

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/studious-obsidian-sync.service <<'EOF'
[Unit]
Description=Obsidian Headless sync for the studious vault
After=network-online.target

[Service]
Environment=PATH=%h/.local/share/mise/shims:/usr/local/bin:/usr/bin:/bin
ExecStart=%h/.local/share/mise/shims/ob sync --path %h/vault --continuous
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload && systemctl --user enable --now studious-obsidian-sync
```

Without mise, drop the `Environment` line and use the path from `command -v ob`.
Then set `obsidian_sync_service: studious-obsidian-sync` in the config, and the daily run stops
(with an inbox note) whenever the service isn't running, rather than planning from a stale vault.

## 3. Interview

Ask these together, with the defaults shown, then write the answers into `studious/config.md`'s
frontmatter. Never invent values the user didn't choose; leave a key out to get its default.

1. **Courses and tracks**: a code for each (as used in decks and tags: `CS3214`, `Rust`) and its
   source folders, relative to the home folder.
2. **Calendar**: `uv run scripts/calendar_events.py calendars`, then pick the Studious calendar
   for `calendar_id`, and the calendars that hold exams and deadlines for `deadline_calendars`.
3. **Daily budget**: minutes of Anki per day (sets the new-card budget), most lessons per day,
   how many days before a deadline lessons should be done, how many days of cram deck.
4. **Anki profile**: keep `testing` while trying studious out; switch later (section 6).
5. **Timezone**, if not America/New_York.

Config keys and defaults:

| Key | Default | Meaning |
|---|---|---|
| `anki_profile` | none | Profile the scripts may write to; every write is refused for any other profile. |
| `anki_port` | 8765 | AnkiConnect port. |
| `anki_launch_command` | `anki -p {profile}` (Linux), `open -g -a Anki --args -p {profile}` (Mac) | How the daily run starts Anki. |
| `timezone` | America/New_York | For review dates in the timetable. |
| `vault_name` | vault | The vault's name in Obsidian, for `obsidian://` links. |
| `calendar_id` | none | The calendar the daily events go to. Unset: no calendar writes. |
| `deadline_calendars` | all | Calendar names or ids scanned for exams and deadlines. |
| `courses` | `{}` | `CODE: {folders: [path, ...]}` |
| `anki_minutes_per_day` | 45 | Review time for all courses; new cards/day = minutes × 60 ÷ `seconds_per_review` ÷ `reviews_per_new_card`. |
| `seconds_per_review` | 10 | |
| `reviews_per_new_card` | 10 | Each new card a day turns into about 10 reviews a day. |
| `max_lessons_per_day` | 2 | Across all goals. |
| `lesson_buffer_days` | 3 | Lessons finish this many days before a deadline (cram mode uses the day before). |
| `cram_days` | 2 | Final days with a cram deck, for goals in cram mode. |
| `casual_new_per_day` | 5 | New cards/day for courses with no deadline pressure, and casual goals. |
| `budget_new_per_day` | 10 | For self-paced budgeted goals. |
| `backlog_overdue_threshold` | 0 | More overdue reviews than this: no new cards for that course (unless a deadline is within `cram_days`). |
| `calendar_days` | 7 | Days of events written ahead. |
| `rag_green`, `rag_amber` | 0.90, 0.75 | Timetable color thresholds. |
| `obsidian_sync_service` | none | systemd user service the daily run checks first. |

Show the user the final config, then commit the vault.

## 4. Goals

```bash
uv run scripts/calendar_events.py deadlines
```

lists exam-like events in `deadline_calendars` for the next 120 days that have no goal yet. For each
one the user wants: create `goals/<COURSE>-<id>.md` (SKILL.md, Goals) with `calendar_event_id` set to
the event's id, so it isn't offered again. Tentative events can wait. Then ingest the nearest goal's
material first; everything else fills in over later runs.

## 5. Schedule

The daily run happens on one machine only (the one with the Headless sync service), at 6:00 AM.
In t3: New task, runs on that machine, project the vault (or none), schedule "At a time" 06:00
every day, any model, and this prompt:

```
Use the studious skill in daily mode: run `uv run ~/.agents/skills/studious/scripts/daily.py` and reply with today's event title, any errors or notes, and the number of inbox items. Don't ask questions or fix anything.
```

Another scheduler works the same way: it only needs to run that command (with `STUDIOUS_VAULT` if
the vault isn't `~/vault`) in the user's desktop session, so Anki can start if it's closed. Never
publish the vault's git repository from the scheduler's UI.

## 6. Switching to the real Anki profile

When the user is happy with the lessons and cards from the `testing` profile:

1. On the machine with the real collection, sync to AnkiWeb. On the scheduled machine, open Anki on
   that profile and sync. If Anki asks for a one-way full sync, the user picks the direction.
2. Set `anki_profile` to that profile's name.
3. `uv run scripts/anki.py ensure-note-types` (creates Code+ and updates the shared styling).
4. Push the lessons already marked done into the real collection:
   `uv run scripts/anki.py push ~/vault/courses` (lessons not done stay held, as always).
5. `uv run scripts/daily.py`, then check the event and the deck options in Anki.

The test profile can stay as it is; nothing writes to it once the config points elsewhere.

## New machine (for example the Mac)

1. The skill comes with `~/.agents/skills` (link it into `~/.claude/skills/studious` for Claude Code).
2. `uv run ~/.agents/skills/studious/scripts/anki.py doctor` and follow its fixes: AnkiConnect
   (add-on `2055492159`), `python3 ~/.local/share/studious/connectors/anki/apply.py`, restart Anki,
   then `scp -r max-desktop:.config/studious ~/.config/studious` and `google-calendar-mcp` installed.
3. Obsidian app with Sync, connected to the remote vault at `~/vault`.
4. No schedule here: interactive `/studious` only. Its closing daily run still updates the calendar;
   the calendar record syncs with the vault and existing events are adopted, so nothing is duplicated.
