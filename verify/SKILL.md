---
name: verify
description: "Verify code changes through real user paths and maintain project verification skills with proven recipes. Use to check new code, verify all features, or set up and repair verification tooling."
disable-model-invocation: true
---

# Verify behavior and maintain verification skills

Use this skill only when the user explicitly invokes it. Do not activate it from task relevance alone.

Observe behavior in the running system. Tests, typechecks, and builds support this work but do not replace runtime observation. Keep project verification skills and feature maps accurate through proven instructions.

## Select the mode and scope

Read repository instructions. State the selected mode, target, and scope before running.

- **Change:** Verify the requested behavior or code change. Update the affected project skill and feature recipes when needed. This is the default for new code and before committing nontrivial runtime changes. Do not expand it into a full audit.
- **All:** Use for an explicit request to verify everything or audit verification coverage. Review source coverage and exercise every mapped feature. No code change is required. Read [references/tooling.md](references/tooling.md) for the audit procedure.
- **Setup:** Use for an explicit setup request or when a required project skill does not exist. Prove the control path and create the project skill. Read [references/tooling.md](references/tooling.md) for the creation procedure. During a change check, keep setup scoped to the affected system and recipes.

For a branch or pull request, identify the actual base and inspect the full committed range. Include relevant staged and unstaged changes. Do not assume the upstream branch is the review base or the last commit is the whole change. State the range and commit count. Report material differences between the diff and stated claim. Without a diff, use the behavior the user named. Ask only when neither establishes a scope.

If the change has no runtime behavior, report SKIP with a reason. Run separately required repository checks. Do not launch the app or change verification tooling without a relevant reason.

## Determine the blast radius

For Change mode, trace changed code to its callers, downstream consumers, shared state, and user entry points. Inspect affected contracts, configuration, and data formats. Include cross-component flows when they depend on the changed behavior. A local screen change may need a narrow check. Changes to authentication, shared libraries, schemas, or common launch code may need checks across several features.

Select runtime paths and repository checks from these relationships. State what is included, what is excluded, and why. Do not select scope from changed filenames alone. Broaden checks when the impact is uncertain. If the impact cannot be established, report that limit instead of claiming complete coverage.

Load the project skill and feature index first. Read only recipes in the blast radius for Change mode. Reuse setup across checks when the project permits it. Stop after selected paths, required checks, and recipe validation pass. Repeat checks only for new changes, failures, or unresolved concerns. Required repository checks still apply.

Keep All mode deliberate. Do not run a full audit for each PR. An explicit full audit must finish its stated coverage before PASS. If it cannot finish, retain completed evidence and report incomplete coverage. Do not silently reduce its scope to meet a time limit.

## Select the project control path

Inspect configured project skill locations, including `.agents/skills/verify-*/` at the root and in affected app directories. Match descriptions and instructions. Resolve discovery links to the canonical project skill. Read the matching skill and affected feature recipes.

Identify the user interface that reaches the behavior: browser, terminal, public API, desktop or mobile app, library export, agent session, or workflow. Use a library's public package boundary. Drive a GUI through its controls. Internal setters and test-only endpoints do not prove user behavior.

Use the project's launch, doctor, drive, evidence, and cleanup instructions. A cross-component flow needs a harness that runs every participant. Independent systems may need separate project skills.

If no matching skill exists, inspect source, README, manifests, and existing harnesses. Establish the tools, isolation, readiness check, evidence location, and teardown before launch. Use Setup to capture the proven path. Do not invent commands or selectors. Report BLOCKED when requirements cannot be established.

Do not add fallbacks or switch tools, backends, or launch paths after a failure. Capture and diagnose it. Correct proven instruction or harness drift within the selected project skill. Read the repair guidance in [references/tooling.md](references/tooling.md). Retry only after a concrete correction. Missing tools or credentials are unmet requirements.

## Run and observe

Run checks required by the repository and appropriate to the change. Honor project checks required before launch. Report check results separately from runtime observations.

Use isolated instances and scratch state. Follow the user's global package policy. Do not drive a shared user session without authorization. Respect authorization for destructive and external actions. Use documented fixtures or dry-run modes when they prove the behavior. State their limits. Report any required action that remains outside authorization.

Launch the system and run its health check. Use a fresh isolated session when required by the project. Drive the smallest real user path that reaches the changed behavior. For All mode, follow the full audit coverage procedure. Capture actions and resulting state. Check stored side effects and a second view when persistence matters. A success message alone does not prove a durable result.

Probe at least one relevant error or adjacent path. Select it from the scope: invalid input, repeated actions, conflicting options, interruption, or stale state. Report its result. Do not introduce unauthorized effects to satisfy a probe.

After a failed drive, retain evidence and run the health check. Distinguish product failure, harness drift, and environment failure. Recheck affected behavior after a correction within scope. Do not substitute another entry point as proof of the failed path. Do not change product code solely to make verification pass.

## Maintain proven recipes

A change check includes maintenance of the affected project skill and feature map. Read [references/tooling.md](references/tooling.md) when creating or changing their structure.

Compare the verified behavior with existing recipes. Add a feature for new user behavior. Update changed entry points, expected results, selectors, launch requirements, or harness commands. Preserve accurate recipes. A bug fix that restores an existing expectation may need no map change. State when no update is needed.

Maintain the current product by feature, not by PR, commit, or issue. Update an existing recipe first. Create a new feature file only for a distinct user capability. Keep a bug regression case only when it adds a distinct check of input, state, an entry point, or a failure condition. Combine duplicate cases. Remove obsolete paths only with source evidence that the behavior was removed or replaced. Never remove coverage because a check fails.

Keep shared launch and harness instructions in the project skill. Keep feature actions in feature files. Keep run history and evidence outside maintained instructions. Read the compactness and concurrent-edit rules in [references/tooling.md](references/tooling.md) before editing recipes.

Require a concrete source path before adding a feature. Write commands and selectors that ran successfully. Add only successfully exercised recipes as proven coverage. Record unexecuted entry points as coverage gaps. Do not invent working instructions for blocked paths.

Validate each changed harness path live. Validate a new project skill from a fresh session using its written instructions. An unexecuted skill remains a draft. Do not rewrite expected behavior to conceal a product regression. Retain failed evidence even when a later check succeeds.

Edit only selected project verification skills, feature maps, and required harness scripts for maintenance. Report product defects separately. Product fixes require authorization from the wider task. Verification does not itself authorize product changes.

## Retain evidence and clean up

Use the project's evidence location or the declared scratch location for Setup. Record stdout, stderr, and exit codes for commands. Capture response bodies for APIs and screenshots or snapshots for UIs. Inspect UI captures before using them as evidence. Use mocks only at an existing production boundary and state what they prove. Identify temporary scaffolding and its removal steps.

Clean up owned processes, sessions, and temporary state after success and failure. Do not kill by process name or port alone. Confirm evidence survives cleanup. Leave an instance running only when requested.

Make evidence available through accessible links. Include decisive output when a local path is inaccessible. Do not claim a screenshot or recording exists unless captured. Keep run notes in scratch space.

## Report

Report the mode, claim, scope or change range, method, actions, observations, adjacent probe, evidence, and findings. For All, list exercised features and entry points. State friction, unverified paths, and harness or environment limits. Keep repository check results distinct from observed behavior.

For Change, report the blast radius and the reason for selected and excluded checks. A PASS applies to that scope. It does not claim that every project feature was tested.

List project skill and feature-map changes. Distinguish proven recipes from draft coverage. Read every changed file again. If no maintenance was needed, say why.

Choose a verdict for the whole stated scope:

- **PASS:** Required behavior and the relevant probe succeeded. Required checks passed. No required path or required recipe validation remains incomplete.
- **FAIL:** Behavior contradicts the claim, a required adjacent path breaks, or a required check fails. Distinguish pre-existing failures when established. Successful subsets do not cancel a failure.
- **BLOCKED:** A missing requirement, failed launch, authorization limit, or inconclusive evidence prevents a verdict. Name where observation stopped and what remains incomplete.
- **SKIP:** The scoped change has no runtime behavior. State the reason and report separately required checks.

For All, also report maintenance as clean, changed, or blocked. For Setup, distinguish executed instructions from draft coverage. Do not create a branch or pull request for a clean or blocked audit. Include only proven corrections in a pull request. Create at most one maintenance pull request when authorized. Otherwise leave changes ready for review. Suggest scheduled audits only when requested.
