---
name: github-issues-setup
description: >-
  Set up a GitHub repo for a Linear-like workflow on plain GitHub Issues (no
  Projects board): type and priority labels, a triage label, issue forms, and
  a generated project-local skill for triage, create, next, start and done,
  blocking, projects as parent issues, stale issues, and a done log. Use when
  the user wants to set up GitHub issues "like Linear" for a repo.
disable-model-invocation: true
---
# GitHub Issues setup (Linear-style)

This skill sets up one repo per run on plain GitHub Issues. There is no Projects board, no Actions, and no token. Then it generates a project-local skill in the repo with `gh` recipes for the Linear-like workflow.

Paths below are relative to this skill's directory (`$S`).

## Model

Status comes from native issue state, so nothing has to keep it in sync:

| Status | Native state |
|---|---|
| Triage | open, labeled `triage` |
| Backlog | open, not assigned |
| In Progress | open, assigned |
| In Review | open, and an open PR closes it |
| Done | closed as completed |
| Canceled | closed as not planned or duplicate |

- Type: labels `bug`, `feature`, `chore`.
- Priority: labels `urgent`, `high`, `medium`, `low`. At most one per issue.
- Projects: a parent issue labeled `project` with native sub-issues.
- Blocked by: native issue dependencies.
- No cycles and no estimates.

## Inputs

Ask for these in one message:

1. The repo (`OWNER/REPO`) and a local checkout (or clone one).
2. Whether to push the setup commit straight to the default branch (the default) or open a PR.

## Steps

1. Make sure `gh` is logged in with the `repo` scope (`gh auth status`).
2. Run `$S/scripts/setup.sh OWNER/REPO --dir CHECKOUT`. It is safe to re-run. It:
   - creates or updates the labels: `triage`, `bug`, `feature`, `chore`, `urgent`, `high`, `medium`, `low`, `project`
   - installs `.github/ISSUE_TEMPLATE/` (bug and feature forms that add `triage` and their type, and a `config.yml` that turns off blank issues)
   - writes the local skill at `.agents/skills/<repo>-issues/SKILL.md` and links it at `.claude/skills/<repo>-issues`
   - lists GitHub's default labels that no issue uses
3. If it listed unused default labels, ask the user whether to delete them. On yes, re-run with `--delete-unused-defaults`. Labels that are on any issue are never deleted.
4. In the checkout, commit the changed files with a message like `Set up Linear-style issues`. Show `git status` and ask before you push. Then push to the default branch, or open a PR, as the user chose.
5. Tell the user the local skill's name and what it covers (triage, create, next, start and done, block, projects, stale, log).

## Rules

- Existing issues are left as they are. Do not backfill labels.
- Ask before you push, open PRs, or delete labels.
- Do not add agent co-author lines to commits or PRs.

## Files

- `scripts/setup.sh`: labels, issue forms, and local skill rendering.
- `templates/ISSUE_TEMPLATE/`: the issue forms.
- `templates/local-skill/SKILL.md.tmpl`: the generated skill. `__SKILL_NAME__` and `__REPO__` are filled in.
