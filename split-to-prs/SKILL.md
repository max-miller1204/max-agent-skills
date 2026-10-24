---
name: split-to-prs
description: >-
  Split current work into small reviewable pull requests. Use when the user asks
  to split a conversation, set of changes, branch, or pull request.
disable-model-invocation: true
---
# Split to PRs

Use this skill only when the user explicitly invokes it. Do not activate it from task relevance alone.

Split related work into small pull requests with clear review boundaries.

## Required rules

- Get approval for the split plan before you create branches, commit, push, or open PRs. Use existing approval when it covers the plan.
- Never discard user work. Get explicit approval before destructive Git commands, branch deletion, force-push, or history changes.
- Save a recoverable snapshot before you move work. Do not assume the starting branch is safe.
- Stage named files or selected hunks. Do not use `git add .` or `git add -A`.
- Use the Git and PR tools available in the environment. If a required tool or permission is missing, report the blocker. Do not substitute another service or claim completion.
- Follow repository instructions and the user's attribution rules. Do not add AI attribution trailers unless explicitly requested.
- Include captured visual evidence for changes to visible UI or interaction. Text descriptions alone do not satisfy this requirement. Follow the evidence guidance in [references/pr-body.md](references/pr-body.md).

## 1. Check the state

Identify the repository's default branch. Compare committed and uncommitted changes to it. Use the conversation to recover the intent of each change.

Read ownership rules for the affected paths. Check `CODEOWNERS`, nested ownership files, and repository equivalents. Use ownership boundaries to identify reviewers.

## 2. Propose the split

List the proposed PR titles. Add a short scope note when a title is unclear. Show dependencies with Mermaid when the response format supports it. Otherwise state the dependency order in text.

Split independent owners or concerns. Keep tightly coupled changes together. Use independent PRs from the default branch when possible. Stack PRs only for real dependencies. Put foundations before their consumers.

For each slice with a visual change, identify the screen or interaction to capture. Plan matching before and after screenshots, or a recording when motion matters. Capture the before state from the slice's base before applying its changes.

Get approval for the plan before execution unless the user has already approved it.

## 3. Execute the split

Save recoverable refs for committed work. For tracked uncommitted work, create a snapshot without changing the working tree:

```bash
snapshot_sha=$(git stash create "pre-split")
if [ -n "$snapshot_sha" ]; then
  git update-ref "refs/backup/pre-split-$(date +%s)" "$snapshot_sha"
fi
```

`git stash create` does not include untracked files. Copy relevant untracked files to a separate backup before moving them. Record the backup location. Do not expose secrets in logs or PRs.

Read [references/opening-a-pr.md](references/opening-a-pr.md) before preparing commits and PRs. Read [references/pr-body.md](references/pr-body.md) before writing PR bodies.

For each approved slice:

1. Create a branch from its required base in a separate worktree when needed to preserve the starting work.
2. Apply only the planned changes.
3. Stage named files or selected hunks.
4. Review the staged diff.
5. Run the checks required for that slice. Capture and inspect its visual evidence before opening the PR.
6. Commit the slice.
7. Push the branch.
8. Open the PR with the correct base and requested readiness.
9. Confirm its URL, base branch, and status.
10. Attach it to the current chat when the environment provides a PR attachment tool.

Stop on an error. Report the failed operation and the remaining work. Do not hide failures or change the approved split to bypass them.

## 4. Report the result

List the PR titles, URLs, and dependency order. State what remains on the starting branch or in the working tree. Include the backup refs or locations. Keep the backup and original branch unless the user asks to delete them.
