---
name: github-projects-setup
description: >-
  Turn a fresh GitHub Projects (v2) board into a Linear-like workspace: Status,
  Priority, Estimate, Cycle, and project fields; views; labels and issue
  templates; Actions for git sync, cycle rollover, stale and SLA flags, and a
  weekly digest. Then generate a project-local board skill. Use when the user
  wants to set up a new GitHub project board "like Linear".
---
# GitHub Projects setup (Linear-style)

This skill sets up a **fresh** GitHub Projects board as a Linear-style workspace. Then it generates a project-local skill in the primary repo for the Linear features that GitHub does not have (triage, Linear-style issue creation, cycle planning, project health).

Scripts are in `scripts/` (gh CLI + GraphQL + jq). Templates are in `templates/`. Paths below are relative to this skill's directory (`$S`).

## Concept mapping

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

Ask for these at the start, in one message if you can:

1. **Project URL**, for example `https://github.com/users/NAME/projects/3` or `https://github.com/orgs/ORG/projects/3`.
2. **Board name** (optional). This renames the project and sets the local skill name `<slug>-board`. The default is the current title.
3. **Linked repos** (`owner/repo`, one or more), and which one is the **primary repo**. The primary repo gets the local skill and the primary-only workflows (cycle-rollover, weekly-digest).
4. **Cycle length** in days (Linear's default is 14) and the **start**: a date (`YYYY-MM-DD`) or a weekday (`monday` means the most recent Monday, so a cycle is active immediately).
5. Whether you can push the setup commit straight to each repo's default branch, or whether it must go through a PR. Scheduled workflows run only from the default branch.

## Steps

Keep the config at a scratch path until it is committed, for example `CFG=$(mktemp -d)/linear-board.json`.

1. **Preflight.** Run `$S/scripts/preflight.sh URL`.
   - Exit 2: scopes are missing. Tell the user to run `gh auth refresh -s project,repo,workflow`, then run preflight again.
   - Exit 3: the board is not fresh. Stop.
2. **Fields.** Run `$S/scripts/create-fields.sh --project-url URL --cycle-days N --cycle-start START --out "$CFG" [--name NAME]`. This replaces the Status options (it keeps the IDs of Todo, In Progress, and Done). It also creates Priority, Estimate, Cycle (4 iterations named "Cycle 1".."Cycle 4"), Target date, Due date, and Health, and writes every ID to `$CFG`.
3. **Link repos.** Run `$S/scripts/link-repos.sh --config "$CFG" --primary OWNER/PRIMARY OWNER/OTHER...`.
4. **Per repo.**
   - Labels: `$S/scripts/setup-repo.sh --config "$CFG" OWNER/REPO`.
   - Files: clone the repo (or use the user's checkout), create a branch `board-setup`, then run `$S/scripts/install-files.sh --config "$CFG" --dir CHECKOUT [--primary]`. Pass `--primary` only for the primary repo. That run also generates the local skill (step 7). The script installs:
     - `.github/linear-board.json` (IDs) and `.github/linear-board/board.sh` (the shared CLI)
     - `.github/workflows/linear-git-sync.yml`, `linear-stale-flag.yml`, `linear-urgent-sla.yml`, and in the primary repo also `linear-cycle-rollover.yml` and `linear-weekly-digest.yml`
     - `.github/ISSUE_TEMPLATE/{bug,feature,improvement}.yml`. These are issue forms that add the issue to the board. git-sync sets it to Triage.
   - Commit with a message like `Set up Linear-style board automation`. Show `git status` and ask before you push. Then push to the default branch, or open a PR, as the user chose.
   - Secret: give the user the token instructions from `references/manual-steps.md` section 3 and the exact commands, for example `gh secret set PROJECT_TOKEN -R OWNER/REPO`. Wait for the user to confirm. Then check it with `$S/scripts/setup-repo.sh --config "$CFG" OWNER/REPO --check-secret`.
5. **Built-in workflows.** The API can only read and delete these. It cannot turn them on or configure them. Show `references/manual-steps.md` section 1 with the project URL. The board still works before the user finishes this step. The git-sync Action adds new issues and moves them to Triage, and it also replaces a Todo that the default "Item added to project" workflow set. Issues closed as not planned or duplicate end as Canceled, even though "Item closed" first sets them to Done.
6. **Views.** Run `$S/scripts/create-views.sh --config "$CFG"`. It creates Triage (from the default View 1), Backlog, Active cycle, Upcoming cycle, My issues, All issues, Projects, Roadmap, Cycles, Overdue, and SLA breach, each with its layout, filter, and visible fields. The API cannot set group by, sort, field sums, or roadmap dates. Those are in manual-steps section 2.
   - "Overdue / SLA" is two views: Overdue (`due-date:<@today`) and SLA breach (`label:sla-breach`). The project filter syntax has no OR across different fields.
7. **Local skill.** `install-files.sh --primary` writes `.agents/skills/<slug>-board/` (SKILL.md + config.json) and links it at `.claude/skills/<slug>-board`. It is committed with the rest of the primary repo's setup commit. The local skill calls `.github/linear-board/board.sh`, so the Actions and the skill share one implementation.
8. **Summary.** Run `$S/scripts/summary.sh --config "$CFG"`. It shows fields, views, repos, the state of the built-in workflows, secret status, and every manual step with URLs. Give the user that output, and list what is still open.

## Smoke test after setup

Run this only when the user agrees. Use a test issue that can be deleted.

1. Create a test issue in the primary repo: `bash .github/linear-board/board.sh create --title "Smoke test" --type chore --priority Low --estimate 1 --cycle @current`.
2. Create a branch named `<N>-smoke-test` and push it. The issue should move to In Progress and be assigned to you. Open a PR that says `Fixes #N`: In Review. Merge it: Done, issue closed.
3. Run each scheduled workflow once: `gh workflow run "board: cycle-rollover" -R PRIMARY`. Then do the same for `"board: stale-flag" -f days=0`, `"board: urgent-sla" -f hours=0`, and `"board: weekly-digest" -f dry_run=true`. Check the results with `gh run list` and `gh run view --log`.

## Files

- `scripts/defaults.json`: field options, colors, labels, estimate scale, and thresholds (stale 60 days, SLA 24h, archive 14 days, 3 upcoming cycles).
- `templates/github/linear-board/board.sh`: the shared CLI. Run `board.sh help` to list its commands.
- `templates/local-skill/SKILL.md.tmpl`: the template for the generated skill.
- `references/manual-steps.md`: the steps that the API cannot do.

## Limits

- Board and repos only: no custom issue IDs, no migration of existing boards, no initiatives, no GitHub App auth.
- Scheduled workflows that use `PROJECT_TOKEN` stop working when the token expires. Tell the user the token's expiry date and suggest a calendar reminder.
