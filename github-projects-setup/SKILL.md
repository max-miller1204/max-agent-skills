---
name: github-projects-setup
description: >-
  Turn a fresh GitHub Projects (v2) board into a Linear-like workspace, with a
  pace (Timeboxed cycles or Self-paced) and a preset (Full, Solo, Hackathon):
  Status, Priority, Estimate, Cycle, and project fields; views; labels and
  issue templates; Actions for git sync, cycle rollover, stale and SLA flags,
  and a weekly digest. Then generate a project-local board skill that can also
  upgrade the board later. Use when the user wants to set up a new GitHub
  project board "like Linear".
---
# GitHub Projects setup (Linear-style)

This skill sets up a **fresh** GitHub Projects board as a Linear-style workspace. Then it generates a project-local skill in the primary repo for the Linear features that GitHub does not have (triage, Linear-style issue creation, cycle planning or "what's next", projects, add-only upgrades).

Scripts are in `scripts/` (gh CLI + GraphQL + jq). Templates are in `templates/`. Paths below are relative to this skill's directory (`$S`).

## Pace and preset

Two independent choices. Always ask both. There is no default.

| Pace | What it means |
|---|---|
| **Timeboxed** | Cycles with rollover, Target date and Health on projects, Due date, Urgent SLA, `overdue` label, Overdue / SLA view. Weekly digest with on-track / at-risk status. |
| **Self-paced** | No cycles, Target date, Health, SLA, or overdue. Projects show sub-issue progress only. Due date is optional (off by default). A **WIP limit** (max In Progress items) warns but never blocks. A Now / Next / Later board replaces Active cycle. The digest is a "what got done" log. The local skill has `next` (top items by priority, then age) instead of cycle plan and review. |

| Preset | What it means |
|---|---|
| **Full** | Everything in the mapping below. 1 or more repos. |
| **Solo** | Exactly one repo. No Triage status: new issues land in Backlog. Type labels bug, feature, chore. No My issues view and no assign-on-branch. 4 views: Backlog, Active cycle (or Now / Next / Later), All issues, Projects. |
| **Hackathon** | Statuses Todo, In Progress, Done only. Priority but no Estimate, no projects or Health. 2 views: one board and All issues. Only the git-sync Action (it still needs `PROJECT_TOKEN`), no scheduled Actions. Optional end date shown as a countdown in the project README. |

The full matrix is in `scripts/defaults.json`: each status, field, label, view, workflow, and issue form lists the presets, paces, and flags it needs. The config stores pace, preset, WIP limit, end date, and that catalog, and `board.sh resolve` computes what is enabled. The Actions and the local skill read only the config.

Upgrades are add-only and run from the generated local skill (`board.sh upgrade`): preset Hackathon -> Solo -> Full, pace Self-paced -> Timeboxed. They add statuses, fields, views, labels, workflows, and issue forms, and never delete anything.

## Concept mapping (Full + Timeboxed)

| Linear | GitHub |
|---|---|
| Team | One project. Linked repos are its issue sources. |
| Issue ID | Native `repo#number` |
| Workflow states | `Status`: Triage, Backlog, Todo, In Progress, In Review, Done, Canceled |
| Duplicate | Closed as duplicate + Status Canceled |
| Priority | `Priority`: Urgent, High, Medium, Low, No priority |
| Estimate | `Estimate` number: 1, 2, 3, 5, 8 |
| Cycles | `Cycle` iteration field. Rollover by Action |
| Projects | Parent issue labeled `project` with sub-issues. `Target date`, `Health`, Lead = assignee |
| Labels / type | `bug`, `feature`, `improvement`, `chore`, `project` in every linked repo |
| Due date | `Due date` |
| Project updates | Native project status updates |

## Rules

- Fresh boards only. If `preflight.sh` reports that the board has items or custom fields, stop and show its message. Do not offer a migration.
- Never ask for the Actions token in chat, and never echo, log, or store it. Give the user the `gh secret set` command and wait for them to confirm.
- Ask before you push, open PRs, or delete anything.
- Do not add agent co-author lines to commits or PRs.
- When the API cannot do a step, say so and show the manual step from `summary.sh`. Do not skip it silently.

## Inputs

Ask for these at the start, in one message if you can. Ask for pace and preset every time, even when the user seems to want the full setup:

1. **Project URL**, for example `https://github.com/users/NAME/projects/3` or `https://github.com/orgs/ORG/projects/3`.
2. **Board name** (optional). This renames the project and sets the local skill name `<slug>-board`. The default is the current title.
3. **Pace**: Timeboxed or Self-paced. **Preset**: Full, Solo, or Hackathon. Show the two tables above so the user can choose.
4. **Linked repos** (`owner/repo`), and which one is the **primary repo**. Solo takes exactly one. The primary repo gets the local skill and the primary-only workflows (cycle-rollover, weekly-digest).
5. Timeboxed: **cycle length** in days (Linear's default is 14) and the **start**: a date (`YYYY-MM-DD`) or a weekday (`monday` means the most recent Monday, so a cycle is active immediately).
6. Self-paced: the **WIP limit** (max In Progress items; required) and whether to add a **Due date** field (default no).
7. Hackathon: the **end date** (optional).
8. Whether you can push the setup commit straight to each repo's default branch, or whether it must go through a PR. Scheduled workflows run only from the default branch.

## Steps

Keep the config at a scratch path until it is committed, for example `CFG=$(mktemp -d)/linear-board.json`.

1. **Preflight.** Run `$S/scripts/preflight.sh URL`.
   - Exit 2: scopes are missing. Tell the user to run `gh auth refresh -s project,repo,workflow`, then run preflight again.
   - Exit 3: the board is not fresh. Stop.
2. **Fields.** Run `$S/scripts/create-fields.sh --project-url URL --pace timeboxed|self-paced --preset full|solo|hackathon --out "$CFG" [--name NAME]`, plus `--cycle-days N --cycle-start START` (Timeboxed), `--wip N [--due-date on]` (Self-paced), or `--end-date YYYY-MM-DD` (Hackathon). It writes the config, then `board.sh schema-sync` sets the Status options for the preset (it keeps the IDs of Todo, In Progress, and Done) and creates the fields this combination uses (Priority, Estimate, Cycle with 4 iterations named "Cycle 1".."Cycle 4", Target date, Due date, Health), and writes every ID to `$CFG`.
3. **Link repos.** Run `$S/scripts/link-repos.sh --config "$CFG" --primary OWNER/PRIMARY OWNER/OTHER...`. Solo refuses more than one repo.
4. **Per repo.**
   - Labels: `$S/scripts/setup-repo.sh --config "$CFG" OWNER/REPO`. It creates only the labels this combination uses.
   - Files: clone the repo (or use the user's checkout), create a branch `board-setup`, then run `$S/scripts/install-files.sh --config "$CFG" --dir CHECKOUT [--primary]`. Pass `--primary` only for the primary repo. That run also generates the local skill (step 7). The script installs:
     - `.github/linear-board.json` (config and IDs), `.github/linear-board/board.sh` (the shared CLI), and `.github/linear-board/templates/` (every workflow, issue form, and the local-skill template, so a later upgrade can add them)
     - the enabled workflows: `linear-git-sync.yml` always; `linear-stale-flag.yml` (Full, Solo); `linear-urgent-sla.yml` (Full, Solo, Timeboxed); in the primary repo `linear-cycle-rollover.yml` (Full, Solo, Timeboxed) and `linear-weekly-digest.yml` (Full, Solo). Hackathon gets git-sync only.
     - `.github/ISSUE_TEMPLATE/`: bug and feature forms (and improvement on Full). These issue forms add the issue to the board. git-sync sets it to the inbox status (Triage, Backlog on Solo, Todo on Hackathon).
   - Commit with a message like `Set up Linear-style board automation`. Show `git status` and ask before you push. Then push to the default branch, or open a PR, as the user chose.
   - Secret: give the user the token instructions from `references/manual-steps.md` section 3 and the exact commands, for example `gh secret set PROJECT_TOKEN -R OWNER/REPO`. Wait for the user to confirm. Then check it with `$S/scripts/setup-repo.sh --config "$CFG" OWNER/REPO --check-secret`.
5. **Built-in workflows.** The API can only read and delete these. It cannot turn them on or configure them. Show `references/manual-steps.md` section 1 (`summary.sh` renders it for this board). The board still works before the user finishes this step. The git-sync Action adds new issues and moves them to the inbox status, and it also replaces a Todo that the default "Item added to project" workflow set. On boards with Canceled, issues closed as not planned or duplicate end as Canceled, even though "Item closed" first sets them to Done.
6. **Views.** Run `$S/scripts/create-views.sh --config "$CFG"`. It creates the views for this combination (the first one reuses the default View 1), each with its layout, filter, and visible fields. Full + Timeboxed gets Triage, Backlog, Active cycle, Upcoming cycle, My issues, All issues, Projects, Roadmap, Cycles, and Overdue / SLA. See "Pace and preset" for the others. The API cannot set group by, sort, field sums, or roadmap dates. Those are in manual-steps section 2.
   - Project filters cannot OR across different fields (due date and label). So the urgent-sla Action keeps an `overdue` label current, and the "Overdue / SLA" view filters on `label:overdue,sla-breach`.
7. **Local skill.** `install-files.sh --primary` writes `.agents/skills/<slug>-board/` (SKILL.md rendered for this pace and preset, and config.json, a link to `.github/linear-board.json`) and links it at `.claude/skills/<slug>-board`. It is committed with the rest of the primary repo's setup commit. The local skill calls `.github/linear-board/board.sh`, so the Actions and the skill share one implementation.
   - Hackathon with an end date: run `bash CHECKOUT/.github/linear-board/board.sh --config "$CFG" readme` to write the countdown into the project README.
8. **Summary.** Run `$S/scripts/summary.sh --config "$CFG"`. It shows fields, views, repos, the state of the built-in workflows, secret status, and every manual step with URLs. Give the user that output, and list what is still open.

## Smoke test after setup

Run this only when the user agrees. Use a test issue that can be deleted.

1. Create a test issue in the primary repo: `bash .github/linear-board/board.sh create --title "Smoke test" --type chore --priority Low`, plus `--estimate 1` when the board has Estimate and `--cycle @current` when it is Timeboxed.
2. Create a branch named `<N>-smoke-test` and push it. The issue should move to In Progress (and be assigned to you, except on Solo). Open a PR that says `Fixes #N`: In Review (In Progress on Hackathon). Merge it: Done, issue closed.
3. Run each installed scheduled workflow once: `gh workflow run "board: cycle-rollover" -R PRIMARY`. Then do the same for `"board: stale-flag" -f days=0`, `"board: urgent-sla" -f hours=0`, and `"board: weekly-digest" -f dry_run=true`. Skip the ones this combination does not install. Check the results with `gh run list` and `gh run view --log`.

## Files

- `scripts/defaults.json`: the catalog. Statuses, fields, labels, views, workflows, and issue forms with the presets, paces, and flags each needs; field options, colors, estimate scale, and thresholds (stale 60 days, SLA 24h, archive 14 days, 3 upcoming cycles).
- `templates/github/linear-board/board.sh`: the shared CLI, including the schema code (`schema-sync`, `sync-files`, `render`, `upgrade`) that setup and upgrades both use. Run `board.sh help` to list its commands.
- `templates/local-skill/SKILL.md.tmpl`: the template for the generated skill. `<!-- when: ... -->` blocks and inline `<!-- x -->...<!-- /x -->` keep text only for boards that have that feature.
- `references/manual-steps.md`: the steps that the API cannot do (rendered per board).

## Limits

- Board and repos only: no custom issue IDs, no migration of existing boards, no initiatives, no GitHub App auth.
- Upgrades only add. Downgrades (Full -> Solo, Timeboxed -> Self-paced) are not supported. Boards set up before pace and presets existed have no catalog in their config, so `board.sh upgrade` refuses them.
- An upgrade changes files only in the primary repo checkout. For other linked repos, copy `.github/linear-board.json` into each repo and run `board.sh sync-files` there.
- The Hackathon countdown refreshes only when git-sync runs or someone runs `board.sh readme`, because Hackathon has no scheduled Actions.
- Scheduled workflows that use `PROJECT_TOKEN` stop working when the token expires. Tell the user the token's expiry date and suggest a calendar reminder.
