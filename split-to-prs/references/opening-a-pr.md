# Open each PR

Adapted from [Opening a PR](https://github.com/backnotprop/pstack/blob/main/skills/poteto-mode/playbooks/opening-a-pr.md).

Use isolated worktrees. Keep commits small, ordered, and independently reviewable. Amend or rebase only within the approved history-change scope.

Review for unnecessary code and comments before committing. Preserve useful explanations.

Use `type(scope): subject` titles unless repository rules differ. Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`. Use a short imperative subject without a final period.

Follow the repository's PR template. Otherwise read [pr-body.md](pr-body.md) and use its template.

Prefer a provided PR tool. Select one supported forge client for operations it cannot perform. Stop if unavailable.

Independent PRs target the default branch. Stack children target their parent branch and use its exact tip.

Open ready unless the user requests drafts. Confirm actual status before reporting.

Finish the approved stack before optional CI monitoring. Monitor only when requested.
