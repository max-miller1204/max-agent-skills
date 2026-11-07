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

The vault is `~/studious` unless the user wants it elsewhere (then they set `STUDIOUS_VAULT` in their
shell profile and in the scheduler). Create or repair it:

```bash
python3 scripts/setup_vault.py
```

Then sync, one method per device (never the desktop app's Sync and Headless on the same machine):

| Device | How |
|---|---|
| The scheduled machine (Linux desktop) | Obsidian Headless as a user service, below. If the Obsidian app is also used there, keep its Sync plugin off. |
| Mac, phone | The Obsidian app with the core Sync plugin, connected to the same remote vault. In Settings > Sync, turn on **Sync all other types** (practice code, manifests and the calendar record are not notes, and interactive runs on that device need them), and in Settings > Files and links turn on **Detect all file extensions** so code files show in the file list. |

Headless setup (needs an Obsidian Sync subscription and Node 22+):

`ob` needs its SQLite module built, and mise's npm installs skip build scripts unless allowed. With
mise (the dots setup), put this line under `[tools]` in `~/.config/mise/config.toml`, run
`mise install`, and persist it with `dots config import mise/config.toml`:

```toml
"npm:obsidian-headless" = { version = "latest", allow_builds = ["better-sqlite3"] }
```

Without mise: `npm install -g obsidian-headless`. Then:

```bash
ob login
ob sync-create-remote --name studious --encryption standard   # first device only; others: ob sync-list-remote
ob sync-setup --vault <id from the line above> --path ~/studious --device-name "$(hostname)"
ob sync-config --path ~/studious --file-types image,pdf,unsupported \
  --configs appearance,appearance-data
ob sync --path ~/studious                    # first sync, watch it finish
```

`unsupported` makes JSON and code files sync too (manifests, the calendar record, practice code).
`appearance-data` carries the callout CSS snippet to the other devices. Dot-folders such as `.git`
never sync, so each machine keeps its own undo log.

A remote vault can't be renamed, and Obsidian Sync Standard allows one remote: to rename, delete
the old remote in the Obsidian app (Settings, Sync, Manage), then `ob sync-unlink`, create the new
remote, and `sync-setup` again.

Run it continuously:

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/studious-obsidian-sync.service <<'EOF'
[Unit]
Description=Obsidian Headless sync for the studious vault
After=network-online.target

[Service]
Environment=PATH=%h/.local/share/mise/shims:/usr/local/bin:/usr/bin:/bin
ExecStart=%h/.local/share/mise/shims/ob sync --path %h/studious --continuous
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

1. **Courses and tracks**: a code for each (as used in decks and tags: `CS3214`, `Rust`), its
   source folders, relative to the home folder, and the group it belongs to, if any: a semester
   (`Fall-2026`), `Work`, a project, nested with `/` (`Work/Infra`).
2. **Calendar**: `uv run scripts/calendar_events.py calendars`, then pick the Studious calendar
   for `calendar_id`, and the calendars that hold exams and deadlines for `deadline_calendars`.
3. **Daily budget**: Anki cards per day (new cards plus reviews), most lessons per day,
   how many days before a deadline lessons should be done, how many days of prep deck before an exam.
4. **Anki profile**: keep `testing` while trying studious out; switch later (section 6).
5. **Timezone**, if not America/New_York.

Config keys and defaults:

| Key | Default | Meaning |
|---|---|---|
| `anki_profile` | none | Profile the scripts may write to; every write is refused for any other profile. |
| `anki_port` | 8765 | AnkiConnect port. |
| `anki_launch_command` | `anki -p {profile}` (Linux), `open -g -a Anki --args -p {profile}` (Mac) | How the daily run starts Anki. |
| `timezone` | America/New_York | For today's date in the plan and review dates in the timetable. |
| `vault_name` | the vault folder's name | The vault's name in Obsidian, for `obsidian://` links. |
| `calendar_id` | none | The calendar the daily events go to. Unset: no calendar writes. |
| `deadline_calendars` | all | Calendar names or ids scanned for exams and deadlines. |
| `courses` | `{}` | `CODE: {folders: [path, ...], group: Fall-2026}`; `group` (optional: a semester, `Work`, a project, nested as `Work/Infra`) puts the course under a parent deck (`Fall-2026::CODE`, `Work::Infra::CODE`) and folders (`courses/Fall-2026/CODE`, `goals/Fall-2026/`), so studying the group's deck interleaves all its courses. Add `done: true` when the course is over (no new cards, done preset), or `retired: true` to suspend all its cards and leave it out of planning (remove it to unsuspend). |
| `cards_per_day` | 150 | Anki cards a day for all courses together: new cards plus reviews (the number on the calendar event). The reviews each new card brings come from Anki's FSRS simulator, so new cards are placed where they keep every day under this; a deadline that can't fit goes over it, with a warning naming the busiest day. |
| `max_lessons_per_day` | 2 | Across all goals. |
| `lesson_buffer_days` | 3 | Lessons finish this many days before a deadline (cram mode uses the day before). |
| `prep_days` | 2 | Final days, through the exam day, with a prep deck (`<root>::<goal> prep`) for every deadline goal: the in-scope cards whose expected recall on exam day is below `exam_recall_target`, weakest first, plus the goal's unseen cards in cram mode. Replaces `cram_days`, still read for now with a warning. |
| `exam_recall_target` | 0.90 | The prep deck's bar: recall on exam day, projected on the FSRS forgetting curve from each card's recall and stability today. |
| `casual_new_per_day` | 5 | New cards/day for courses with no deadline pressure, and casual goals. |
| `budget_new_per_day` | 10 | For self-paced budgeted goals. |
| `backlog_overdue_threshold` | 10 | More overdue reviews than this: no new cards for that course today (unless a deadline is within `prep_days`). |
| `calendar_days` | 7 | Days of events written ahead. |
| `rag_green`, `rag_amber` | 0.90, 0.75 | Timetable color thresholds. |
| `obsidian_sync_service` | none | systemd user service the daily run checks first. |
| `anki_preset_name` | studious | Name given to Anki's Default preset, which every course uses. |
| `anki_settings` | see below | Anki settings studious keeps set: `preset`, `done_preset`, `collection`, `desktop`. |
| `fsrs_optimize_days` | 30 | The daily run runs Anki's "Optimize all presets" once this many days have passed since the last optimization. |

### Anki settings

Every course uses one preset: Anki's Default preset (id 1, so new decks get it too), renamed
`anki_preset_name`. Each course deck gets its own new cards/day on the deck's "This deck" tab, set by
the planner, and the preset's new cards/day is the sum of today's course limits. "Limits start from top" is on,
so a lesson subdeck never gives more than its course allows.

A course marked `done: true` moves to a second preset, `<anki_preset_name>::done` (created the first
time): the same settings with `done_preset` on top, 85% retention by default, so its reviews thin
out. Exam prep stays on filtered decks; retention isn't raised before exams, since workload climbs
steeply above 90%. The prep deck brings back only the cards that would be below
`exam_recall_target` on exam day. Once `fsrs_optimize_days` have passed (Anki keeps the count), the daily run
runs "Optimize all presets", so FSRS fits your own memory as reviews accumulate.

The defaults follow the Anki manual's FSRS advice: one same-day learning step, 90% retention, and an
uncapped review limit so workload is set by new cards only. Each daily run applies `anki_settings`
(deep-merged over these defaults) and notes anything it had to change back, so settings live in
the config, not in Anki's deck options:

```yaml
anki_settings:
  preset:                    # any preset field, nested or dotted; must already exist in Anki
    desiredRetention: 0.9
    new.delays: [15]         # one short same-day step with FSRS
    lapse.delays: [15]
    rev.perDay: 9999         # never hide due reviews; new cards set the workload
    newGatherPriority: 1     # by position: studious decides which new cards come first
    new.bury: true
    rev.bury: true
    buryInterdayLearning: true
    lapse.leechFails: 8
    lapse.leechAction: 1     # tag only
    maxTaken: 360
  collection:
    config: {fsrs: true, applyAllParentLimits: true}   # also fsrsHealthCheck, newCardsIgnoreReviewLimit, ...
    preferences: {}          # e.g. {scheduling: {rollover: 4, learnAheadSecs: 1200}, reviewing: {...}, backups: {...}}
  done_preset:               # courses marked done: the preset's settings with these overrides
    desiredRetention: 0.85
  desktop: {}                # global: theme (0 system, 1 light, 2 dark), answer_keys, minimalist_mode, defaultLang
                             # profile: autoSync, syncMedia, autoSyncMediaMinutes, networkTimeout, numBackups, ...
```

`new.perDay` comes from the planner. The patch refuses unknown fields, wrong types and out-of-range
values before writing anything.

```bash
uv run scripts/anki.py settings show                 # everything, in the shape `apply` takes
uv run scripts/anki.py settings apply --dry-run      # what the config would change
uv run scripts/anki.py settings apply --file F.json  # a whole settings file (presets with their decks
                                                     # and limits, collection, desktop), e.g. one saved
                                                     # earlier with `settings show`
```

During setup: run `settings show`, walk the user through what differs from the defaults, write their
choices into `anki_settings`, then `settings apply`.

Anki's `Default` deck can't be deleted (it hides once it's empty and other decks exist), and neither
can the Default preset. Deleting any other preset forces a one-way full sync:
`anki.py settings remove-preset NAME --allow-full-sync` only after the user agrees, and the user
picks the sync direction.

Show the user the final config, then commit the vault.

## 4. Goals

```bash
uv run scripts/calendar_events.py deadlines
```

lists exam-like events in `deadline_calendars` for the next 120 days that have no goal yet. For each
one the user wants: create the goal note in the course's goal folder (`goals/[<group>/]<COURSE>-<id>.md`, SKILL.md, Goals) with `calendar_event_id` set to
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
the vault isn't `~/studious`) on that machine while the user is logged in to its desktop (a locked
or blank screen is fine). The job doesn't need the desktop's display itself: when Anki is closed and
the job has no `DISPLAY` or `WAYLAND_DISPLAY` (t3, cron), the run borrows them from the user's
systemd manager (`systemctl --user show-environment`) to start Anki. Never publish the vault's git
repository from the scheduler's UI.

## 6. Switching to the real Anki profile

When the user is happy with the lessons and cards from the `testing` profile:

1. On the machine with the real collection, sync to AnkiWeb. On the scheduled machine, open Anki on
   that profile and sync. If Anki asks for a one-way full sync, the user picks the direction.
2. Set `anki_profile` to that profile's name.
3. `uv run scripts/anki.py ensure-note-types` (creates Code+ and updates the shared styling).
4. Push the lessons already marked done into the real collection:
   `uv run scripts/anki.py push ~/studious/courses` (lessons not done stay held, as always).
5. `uv run scripts/daily.py`, then check the event and the deck options in Anki.

The test profile can stay as it is; nothing writes to it once the config points elsewhere.

## Moving a course into a group

A course that already has lessons and cards moves without losing review history, from no group
into one or from one group to another:

1. Set (or change) `group:` on the course in `studious/config.md`.
2. Move its folder to `courses/<group>/CS3214/` and its goal notes to `goals/<group>/`. Lessons need
   no edit: their `deck` is the topic, and the group comes from the config.
3. Rewrite links to the old folders: `[[courses/…/CS3214/…` in `studious/reports/`, `"lesson"` paths
   in `studious/manifests/CS3214.json`, and any links you made yourself to the goal notes.
4. Run `uv run scripts/daily.py`. Its "move decks" step renames the course's deck (`CS3214` or
   `<old group>::CS3214`) to the new root, with its lesson and prep decks, keeping every card's review
   history (patch v9 or later; with an older patch the run stops there and says so). It also rewrites
   the timetables, and the inbox lists any lesson or goal note still outside the new folders.

To rename a deck by hand instead: `uv run scripts/anki.py rename-deck CS3214 Fall-2026::CS3214`.

## New machine (for example the Mac)

1. The skill comes with `~/.agents/skills` (link it into `~/.claude/skills/studious` for Claude Code).
2. `uv run ~/.agents/skills/studious/scripts/anki.py doctor` and follow its fixes: AnkiConnect
   (add-on `2055492159`), `python3 ~/.local/share/studious/connectors/anki/apply.py`, restart Anki,
   then `scp -r max-desktop:.config/studious ~/.config/studious` and `google-calendar-mcp` installed.
3. Obsidian app with Sync, connected to the remote vault at `~/studious`.
4. No schedule here: interactive `/studious` only. Its closing daily run still updates the calendar;
   the calendar record syncs with the vault and existing events are adopted, so nothing is duplicated.
