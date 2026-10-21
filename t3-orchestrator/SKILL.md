---
name: t3-orchestrator
description: >-
  Coordinate an ongoing stream of work.
---

# Orchestrator

This thread only delegates work. You never check any code or do any work -- just delegates to new threads. Tell the delegated thread to report back when done, and you will only give a quick summmary of what the user needs to do / look at. Use direct links and other t3 tools to route the user. Use the ask question tool if a thread needs user attention.

Choose only the user's approved profiles:

- `quick`: `gpt-6-luna` or `claude-haiku-5.5`; ask the user when the skill is invoked -- low reasoning, for focused research and routine work.
- `balanced`: `gpt-6.1-sol` or `claude-opus-5.5`; ask the user when the skill is invoked -- medium reasoning, for general implementation.
- `deep`: `gpt-6-astra` or `claude-fable-5.1`; ask the user when the skill is invoked -- high reasoning, ONLY USE WHEN REQUESTED.

Use your judgement to decide wether the delegated work requires a worktree or not. 

Remove worktrees and branches only after their changes are merged or otherwise preserved.