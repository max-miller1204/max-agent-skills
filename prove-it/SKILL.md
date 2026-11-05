---
name: prove-it
description: Set up, maintain and audit a repo's verification skill, prove-<app> (or a temporary prove-<migration>), which an agent runs after each change to prove it in the running app against an answer key the builder didn't write. Use for /prove-it, "set up verification", "make a verify skill for this repo", adding a surface such as an MCP server, "verify this migration", "the agent keeps saying done without testing", "the verifier is out of date", "prove missed this bug", "can the verifier be fooled", or after a big merge or migration milestone.
---

# Prove It

Prove It gives a repo a project-local skill that makes every future agent prove its change in the running app, instead of saying "done" because the code looks right or the tests pass. This skill builds that project skill, keeps it current and tries to fool it. It never proves product changes itself: the project skill does.

## Modes

| Invocation | Goal | Done when | Read |
|---|---|---|---|
| `/prove-it` or `/prove-it create` | Give the repo `prove-<app>`, add a surface to it, or give a migration its temporary `prove-<migration>` | A clean run of the generated skill passes, and a fresh agent runs it unprompted after a small change | [references/create.md](references/create.md) |
| `/prove-it maintain` | Fold what past runs and slipped bugs learned back into the skill | Every edit is proved live, and the report says `current`, `updated` or `blocked` | [references/maintain.md](references/maintain.md) |
| `/prove-it audit` | Find out whether the skill can be fooled, by planting bugs one at a time | Every planted bug is reverted exactly, and every miss has a fix proposed | [references/audit.md](references/audit.md) |

Free text picks the mode: "set up verification" or "I'm adding an MCP server" is create, "the verifier is out of date" or "prove missed this bug" is maintain, "test the verify skill" is audit. If none exists yet, maintain and audit stop and suggest create.

## The project skills

- **`prove-<app>`**: permanent, one per app, with a section per surface (web, API, MCP, CLI) and a recipe per user feature. A new surface adds a section, never a second skill.
- **`prove-<migration>`**: temporary, one per migration (Python to Rust, Next.js to React Router). It runs old and new side by side, and its end step moves its cases into `prove-<app>` as snapshots before it deletes itself. The rule: if the old thing is deleted at the end, it is a migration; if it stays, it is the app.

Find them under `.claude/skills/` or `.agents/skills/`, at the repo root and in package folders, by the `prove-it:` marker comment, which also says which kind it is. If there are several, ask which.

## Ideas every mode relies on

**A check has three parts**: a surface (where a user or program meets the change), an answer key (what right looks like) and a comparison between them.

**Answer keys come from someone other than the builder.** Ladder, best first: 1 an old system that does the same job, 2 a snapshot from a version the user trusts, 3 an independent test suite, 4 the user's spec, 5 nothing (flag it). No mode ever writes the feature spec or edits an answer key to make a check pass; only the user changes one.

**Check what the change touches, not the whole app.** Each flow the diff reaches is new (answer key: the spec, or parity with what it copies) or existing (answer key: the app before the change). Each is hard (near the edge of what agents get right: concurrency, sync, money, auth, migrations, shared code) or routine. Hard flows get the full proof; routine ones get their scripted checks.

**Split agents by context, not by job.** The builder does Scope and Sort, then a fresh checker gets only the flows, their recipes and their answer keys, never the builder's plan or reasoning, so it can't inherit its blind spots. Independent flows can get one checker each, in parallel, when they can run on separate instances. Findings go back to the same builder, which already knows why it built things that way, for up to 3 rounds.

**Recipes say how; answer keys say what right is.** A feature recipe (how to reach and drive a feature) may be updated by the builder in the same change, once it ran. What right looks like stays in the answer key.

**Prove every edit live.** A verification skill nobody has run is a guess. Each mode runs what it wrote or changed before it reports.

**Every step encodes a guess about what agents get wrong.** Audit tests those guesses with planted bugs; maintain lightens the ones newer models have made stale. Expect the skill to get simpler over time, not longer.

## Files

- `.prove-it/runs/<UTC timestamp>-<slug>/`: receipts and evidence from each run. Gitignored.
- `.prove-it/audits/<UTC timestamp>/`: audit backups and reports. Gitignored.
- `.prove-it/snapshots/`: answer keys. Committed.

## References

- [references/skill-template.md](references/skill-template.md): the two templates for the generated skill, with the core blocks copied word for word.
- [references/methods.md](references/methods.md): verification methods to offer during create.
- [references/unreachable-surfaces.md](references/unreachable-surfaces.md): common blockers and their unlocks.
- [references/mutation-catalogue.md](references/mutation-catalogue.md): bug types by layer, for audit.

<!-- Adapted from create-, maintain- and audit-prove-it in github.com/ray-amjad/skills. -->
