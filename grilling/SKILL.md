---
name: grilling
description: Grill the user relentlessly about a plan, decision, or idea, and when the plan is something to build, end by deciding how to cut it (one spec, or an MVP and branches) and writing the spec. Use when the user wants to stress-test their thinking, plan a feature, or uses any 'grill' trigger phrases.
---

Interview the user relentlessly until you reach a shared understanding. Map this as a **design tree**: every decision branches into the decisions that hang off it.

Work the tree in **rounds**. The **frontier** is every decision whose prerequisites are already settled: the questions you can ask _now_ without guessing at answers you haven't heard yet. Ask the whole frontier in one round: number each question and give your recommended answer. Then wait for the user's answers before the next round.

Format a round like so:

```
❓ **Q1** - **<question title>**: <question body, might be multiple paragraphs, including multiple choices>

➡️ <your recommended answer>

---

❓ **Q2** - **<question title>**: <question body, might be multiple paragraphs, including multiple choices>

➡️ <your recommended answer>
```

Word each question so "yes" accepts your recommended answer.

Each round the user answers reshapes the tree: settled decisions push the frontier outward and unblock questions that depended on them. Recompute the frontier and ask the next round. A question whose answer depends on another question still open in this round belongs to a _later_ round, not this one.

Finding _facts_ is your job, never the user's. When a frontier question needs a fact from the environment (filesystem, tools, etc.), look it up yourself; don't ask the user for anything you could look up. Most lookups are a command or two, and what you find shapes later questions. Use a sub-agent with a fresh context window only for a broad search whose details you won't need, and don't block on it: a running exploration is an unsettled prerequisite, so only the questions downstream of it wait for the sub-agent to report; ask the rest of the frontier now. The _decisions_ are the user's: put each to them and wait.

The session is done when the frontier is empty: every branch of the design tree visited, nothing left silently assumed. Do not act on it until the user confirms you have reached a shared understanding.

## When the plan is something to build

Before the frontier closes, settle how the work is cut, then offer to write the spec.

**One spec, or an MVP and branches.** Default to one spec, built by one builder from start to finish. Split only into an MVP and branches:

- The MVP is the smallest version a user can actually use, not "the backend part".
- A branch is a feature added on top of the MVP ("bulk invites", "invite expiry"). Never a layer (backend, frontend) and never leftovers (tests, error handling, polish): those belong inside the MVP or the branch.
- Branches are built after the MVP merges, so their builders read the MVP's decisions from the code instead of a handoff.
- Two branches can be built at the same time only if they don't touch the same files, data or decisions. Otherwise one waits for the other to merge.
- If the MVP and the branches are all small, it is one spec.

**Write the spec**, with the user's OK. Write the single spec, or the MVP, in full:

```
# <title>
Goal: <the outcome, measured where you can ("the invite page loads in under 200ms"), not the steps to get there>
Decisions: <one line per decision this session settled>
Acceptance flows: <each way a user or program uses it, and what done looks like for each. This is the answer key the work is checked against>
Out of scope: <what it doesn't do>
Branches: <one line per branch goal, if split>
Progress:
- [ ] <checklist the builder ticks as it goes>
- [ ] Fresh code review (spec + diff only) if the change is hard (auth, money, concurrency, data, shared code) or large; findings fixed in this session
- [ ] prove-<app> passes, if the repo has one
```

Always end Progress with those two lines: the builder does what its own checklist says.

Write each branch as its one-line goal only. It gets its own spec in a short grilling round after the MVP merges, against the real code: a detailed branch spec written now goes stale while the MVP is built.

Put the spec where the repo's checks look for it: an issue body (one issue per spec; an MVP and branches go as sub-issues of a parent `project` issue), the PR body, or a spec file the repo already uses (such as `docs/specs/<slug>.md`). If the repo has no convention, ask which.
