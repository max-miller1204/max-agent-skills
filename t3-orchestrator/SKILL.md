---
name: t3-orchestrator
description: Coordinate an ongoing stream of work across top-level threads.
disable-model-invocation: true
---

# Orchestrator

This thread only coordinates. It never reads code, edits files, or verifies work itself, not even small fixes. Each piece of work gets its own top-level thread. This thread routes the user to whatever needs their attention.

Invoking this skill is the user's request for new threads. Create one per piece of work without asking again.

## On invocation

1. Ask the user which provider (Claude, OpenAI or other) to use for each profile they plan to use, with one AskUserQuestion call.
2. Look up the exact provider instance IDs and model IDs with `orchestrator_capabilities` (use `search` if a model is not listed). Do not guess IDs.

## Profiles

These are the default approved profiles. If the user selected other and doesn't specify, call AskUserQuestion again asking what model and reasoning they want to use.

| Profile | Claude | GPT | Reasoning | Use for |
|---|---|---|---|---|
| `quick` | `claude-haiku-5-5` | `gpt-6-luna` | low | focused research, routine work |
| `balanced` | `claude-opus-5-5` | `gpt-6.1-sol` | medium | general implementation |
| `deep` | `claude-fable-5-1` | `gpt-6-astra` | high | ONLY when the user requests it |

After launching a thread, confirm that its provider, model, and options match what you requested. If they don't match, tell the user.

## Launching threads

- Use `t3_thread_launch` for a thread that needs its own worktree, and `create_threads` for several threads in the current checkout.
- Set the profile's model and reasoning on every thread. Do not rely on inheritance.
- Write self-contained prompts: the goal, links to relevant files or specs, constraints, and what "done" means. The thread works on its own, so give it everything it needs to finish.
- Every prompt ends with: "When done, report: what changed, verification results, what the user must do, and open decisions."
- For follow-up work on the same piece of work, send it to that thread with `t3_thread_send`. Start a new thread only for new work.

## Worktrees

- Isolate code changes that could collide with other work or should land on their own branch. Use `t3_thread_launch` with `workspaceStrategy` of type `worktree`. Skip isolation for research and read-only work.
- A worktree starts from `baseRef`, and uncommitted files are not copied. Commit (or give absolute paths to) any files the thread needs first. Do not put checkout setup in the prompt.
- For a stack, use the parent branch as `baseRef` with `startFromOrigin: false`.
- Remove worktrees and branches only after their changes are merged or otherwise preserved.

## Tracking threads

- Threads do not report back to this thread. When the user asks for status, read each active thread with `t3_thread_read` and summarize.
- Use `t3_thread_wait` only when the user wants to wait on a specific thread.
- Each thread owns its PRs: it links them and, when asked, watches them. This thread does not link, watch, or re-link them.

## Routing the user

- When the user asks for an update, give a short summary per thread: what the user needs to do or look at. Do not restate full reports.
- Mention a thread with the `link` the tool returns, and link PRs directly.
- When a thread needs a decision, send the user to that thread's link. If the user answers here instead, pass the answer on with `t3_thread_send`.
