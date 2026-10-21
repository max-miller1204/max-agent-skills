# Project verification tooling

Read this reference for Setup, All, or structural changes to a project skill. Use the shared execution, evidence, cleanup, and verdict rules in `../SKILL.md`.

## Project skill structure

Honor repository locations and explicit user choices. Otherwise use `.agents/skills/verify-<system>/`. Keep one canonical copy. Create discovery links only within task authorization. Keep instructions portable across agents.

Use one skill for components that launch together and share state. Keep a web app, API, and worker together when one user flow needs all three. Split independently operated systems with separate setup and state. Each cross-component flow needs instructions that launch every participant.

Write `SKILL.md` with a matching directory name and YAML `name: verify-<system>`. Describe the system, surfaces, and task. Include these sections with observed commands and selectors:

- **Launch:** Exact start command, readiness check, and teardown. Build or install once for a short-lived CLI or TUI. Start each drive in its own isolated session when required.
- **Doctor:** A health check that does not change app state. Check process, build, owned port, and authentication as applicable. Name evidence files it writes.
- **Drive:** Required tools, harness commands, and stable selectors. Prefer accessible names, data attributes, prompt strings, and routes over coordinates or tab order. Missing tools are explicit errors.
- **Evidence:** Artifact location, actions, expected observations, stored side effects, and proof limits.
- **Cleanup:** Remove only owned runtime state after success and failure. Preserve evidence.
- **Helpers:** Ship required scripts with the skill. Make executable helpers executable. Document each invocation.

## Feature map

Keep `features/README.md` and one file per user-facing feature inside the project skill. The README indexes the feature files and names baseline preconditions. The map is the maintained coverage source. Do not create a separate generated inventory.

Use [feature-map-example/README.md](feature-map-example/README.md) for the format. Its commands are examples for Notes, not commands to run for another app. Each feature file starts with a title and user behavior. Use these H2 sections in order:

1. `Sub-features`
2. `How to get to it (user POV)`
3. `Driving it with <harness>`
4. `Gotchas`

List all known user entry points. Pair actions with exact harness commands and observable proof. Keep implementation details out of recipes. Record source references in review evidence instead. Verification through one entry point does not verify another.

Preserve accurate content. Add features only with concrete source evidence. Document unmet requirements and coverage gaps. Do not present an unexecuted recipe as proven.

## Keep maintenance compact

Use stable feature names and IDs. Do not create files for individual PRs, commits, or issues. Update the existing feature recipe before adding a new one. Add a regression case only when existing checks do not cover its distinct behavior.

Keep each instruction in one maintained location. Link to shared preconditions and harness commands instead of copying them across features. Combine equivalent cases and remove obsolete instructions with source evidence. Keep useful error paths and entry points when consolidating. Do not erase a product failure or an unverified path to shorten the map.

Keep the project skill as the launch and control guide. If a feature file becomes hard to scan, split it by distinct user capability and update the index. Do not split by development history. Keep evidence, review notes, commit lists, and run results in scratch artifacts rather than the feature map.

Use existing repository regression tests and CI for repeatable checks. Reference maintained test commands instead of duplicating test bodies in recipes. Automated checks support runtime observation. They do not replace the required user-path proof. Recommend a repository regression test for a useful missing case. Add it only when product or test edits are authorized by the wider task.

During Change mode, consolidate only the recipes being edited. Do not turn a small check into a map-wide cleanup. During All mode, check the whole map for duplicate and obsolete coverage.

## Concurrent edits

Work in the task's checkout or isolated worktree. Do not use a shared runtime or overwrite another contributor's uncommitted recipe edits. Keep updates limited to affected feature files and required harness changes. Change the index only when its links or feature descriptions need an update. Avoid unrelated formatting and generated inventories.

Before writing a file, check whether it changed since inspection. Read concurrent changes and preserve them. Do not replace the whole file from a stale copy. Resolve overlapping edits by reconciling behavior and coverage. Recheck affected instructions after a merge changes commands, selectors, preconditions, or expected results.

Keep evidence scoped to the run and identify the tested revision and relevant local changes. A result from one revision does not prove a later merge. If verification runs before integration, disclose that limit. Follow repository requirements for verification after integration.

## Setup

Inspect source and documentation for user surfaces, dependencies, launch commands, ports, environment variables, seed data, and authentication. Inspect existing browser tests, terminal helpers, HTTP clients, and harnesses. Choose a tool that supports the actual surface.

Build or start the checkout. Run the health check. Drive a real user flow and retain evidence. Fix failures only within task scope. A broken launch cannot produce a completed skill.

Write the project skill using successful commands and observed selectors. Start with the main three to five features when practical. For setup during a change check, cover the changed behavior and relevant adjacent paths first. State remaining gaps.

Follow the written instructions from a fresh session. Launch, run Doctor, drive the new recipes, capture evidence, and clean up. Confirm evidence remains available. Validate each new project skill this way. Correct failures within scope and repeat affected checks. An unexecuted path remains unverified. An unexecuted skill remains a draft.

## All: source coverage and live audit

Accept a named system or an explicit request to audit all project verification skills. Ask only if the target remains ambiguous. Honor an explicit audit target. If it does not exist, report BLOCKED. Do not silently replace the audit with Setup.

Edit only selected project skill directories, feature maps, and harness scripts. Do not edit product code as part of an audit.

Check each feature index against its sibling files. Correct missing, duplicate, extra, or dead entries. Review source for every feature. Record user behavior, source entry points with file and line references, likely drift or none found, and a concise live recipe. Require completed source review for every feature.

Use direct review unless delegation is available and authorized for this task. If authorized, source reviewers remain read-only. One coordinator drives the app and edits the selected tooling.

Inspect recent code changes for missing user-facing features. Require a concrete source path before adding each feature. Combine overlapping live recipes into as few app states as practical. Check suspected drift without repeating completed clean source reviews.

Run live verification even when source review finds no drift. Exercise every mapped feature at least once. Include source-discovered missing features in the audit scope. Track entry points used and report untested ones. An explicit request for every entry point requires exercising each one. Do not claim full entry-point coverage from one path per feature.

Follow the project's launch model. Run Doctor before the first drive, in each fresh session, and after a failed drive. If a healthy process has a stuck UI, reset or relaunch the same documented instance. Remove failed-drive residue without ending an instance still needed for later checks.

Classify findings as documentation drift, harness gaps, or product regressions. Correct proven documentation and harness problems. Run each corrected path live before marking it proven. Report product regressions without changing the expected behavior to conceal them.

Report unreachable features with the attempted route and unmet requirement. Correct omitted requirements in the map. Unreachable features have not passed verification. Finish teardown and retain evidence.

Report maintenance as:

- **clean:** Source and live coverage completed for all mapped and source-discovered features. No corrections are needed. Disclose entry-point coverage limits.
- **changed:** Proven corrections are ready. Disclose untested entry points.
- **blocked:** Required coverage or a correction could not finish. Name the blocker and incomplete coverage.

A maintenance result does not override the runtime verdict. An audit can have accurate tooling and still report a product failure.

## Repair during a change check

Capture the failed instruction and evidence. Diagnose whether the instruction, harness, product, or environment caused the failure. Correct proven tooling drift only within the affected project skill. Do not add a fallback or substitute another entry point as proof.

If the correction invalidates launch state, restart only that state. Retry the affected path once after the correction. If that retry fails, retain evidence and report FAIL or BLOCKED according to the cause. Do not keep changing the method until something passes.

After a successful repair, update the affected feature recipe and run its written instructions. Record any remaining gaps. Keep product defects separate from tooling corrections.
