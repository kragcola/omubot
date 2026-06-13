---
name: omubot-continuity
description: Omubot context-continuity workflow for resuming long-running work without rediscovery. Use when the user says continue/接着/恢复/别重新开始/防止失忆, after context compaction or a new session, before ending or handing off a long task, when a task spans multiple files or sessions, during bug investigations that need a test ledger, or when changing agent prompts, skills, hooks, workflow docs, deployment, runtime, character packs, or production-facing behavior.
---

# Omubot Continuity

Use this skill to resume, checkpoint, and hand off Omubot work without losing context after compaction.

## Recovery Protocol

1. Read `AGENTS.md`.
2. Read `.workspace/agent-session-state.md` if present.
3. Read `docs/tracking/ACTIVE.md`.
4. Read the active tracker named in ACTIVE.
5. If mode is `bug` or `task-bug`, read the Test Ledger before running commands.
6. Run `git status --short`.
7. State the recovered objective, next step, risks, and what will not be re-investigated.

Do not restart from wiki or architecture docs unless ACTIVE/tracker is missing, stale, or contradicted by git state.

## Tracker Threshold

Create or update a tracker when any condition holds:

- task likely exceeds 15 minutes
- task touches 3+ files
- task spans sessions or user says not to lose context
- task involves production runtime, deployment, character packs, skills, hooks, prompts, workflow rules
- bug investigation requires multiple experiments

For small one-turn tasks, use `update_plan` only.

## Update Protocol

- Keep `docs/tracking/ACTIVE.md` short.
- Put live todo, decisions, verification, rollback, and per-section progress in the active tracker.
- Use `.workspace/agent-session-state.md` for temporary session state.
- Append every meaningful bug experiment to Test Ledger with exact command, actual result, and conclusion.
- Update `maintenance-log.md` only when durable project behavior or future-agent workflow changes.

## Resume Output

After recovery, report:

- objective
- current status
- next concrete action
- files likely involved
- evidence already established
- dead ends not to retry

## Stop / Handoff Protocol

Before final response on long tasks:

1. Update tracker next step and verification.
2. If done, set ACTIVE mode to `none` or point to remaining work.
3. If durable change, update `maintenance-log.md`.
4. Report what was verified and what remains.

## Related Files

- `docs/tracking/ACTIVE.md`
- `docs/tracking/agent-continuity-template.md`
- `docs/tracking/agent-continuity-omubot-plan-2026-06-06.md`
- `scripts/dev/codex-session-start.py`
- `.codex/hooks.json`
