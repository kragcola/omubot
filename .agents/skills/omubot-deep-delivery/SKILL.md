---
name: omubot-deep-delivery
description: Omubot deep-delivery workflow for tasks where shallow execution would be harmful. Use when the user criticizes reasoning depth or verification quality, asks to search the web, asks to enroll/build datasets or character packs, touches agent prompts/skills/hooks, or requests a production-facing change that needs research, collision checks, dry-runs, runtime validation, and a rollback story.
---

# Omubot Deep Delivery

Use this skill when the task can fail silently: internet-sourced facts, character
pack enrollment, prompt/skill/hook changes, production runtime changes, or any
turn where the user says the previous work lacked depth, initiative, or
verification.

## Delivery Executor Mode

When you are implementing an already-scoped, already-dispatched task (the
planning/architecture happened upstream), these rules are not optional — they
exist to stop the five failure modes that recur on this repo:

1. **Read before you ask.** You have read access (`read_file`, `grep`, `glob`,
   `ls`). If the answer is in the repo, find it yourself. Only ask the user for
   information that genuinely cannot be obtained from the codebase, logs, DB, or
   docs — never to save yourself a lookup you have permission to do.
2. **Confirm understanding before editing.** Restate the objective and the
   concrete acceptance criteria in your own words. If your restatement could be
   wrong, surface it before touching files, not after.
3. **Never claim done without re-reading your own work.** Before saying a task is
   complete, re-open the files you changed and confirm the edit actually does
   what was asked — not just that the tool returned success.
4. **Verify with the self-check toolbox; do not offload testing to the user.**
   Most claims on this repo are machine-verifiable, so prove them yourself:
   - logic / behavior → `uv run pytest` (run the relevant tests, paste output)
   - types / lint → `uv run pyright`, `uv run ruff check`
   - live DB / runtime state → `sqlite3 'file:storage/<db>.db?mode=ro' '<query>'`
     so committed WAL state remains visible; use `immutable=1` only for a known
     offline backup that cannot require WAL replay
   - QQ, QZone, NapCat, webhook, or other external sends are never routine
     self-checks. They require current-task authorization for the exact action and
     target; otherwise use offline fixtures, dry-runs, and read-only status.
   If a required check needs new authority, credentials, or an external mutation,
   report that boundary explicitly. Never broaden scope or invent verification.
5. **Attempt before escalating.** If a first approach fails, diagnose and try a
   different one. Escalate to the user only after a real attempt, with evidence
   of what you tried and why it failed — do not dispatch the problem back
   unattempted.

## Operating Mode

1. Restate the real objective and acceptance criteria in your own words before
   editing when ambiguity could change the outcome.
2. Gather context before deciding:
   - local: `AGENTS.md`, `maintenance-log.md`, relevant docs/tests/code
   - external: web search whenever the user says web/search/latest, the facts
     are current or source-dependent, or the task depends on outside datasets
3. Prefer primary sources. For technical/agent workflow claims, use official
   docs first; cite sources in the final answer when web was used.
4. Keep an assumption ledger. Convert risky assumptions into checks, explicit
   IDs, allowlists, dry-runs, or warnings.
5. Build deterministic scripts for repeatable collection/enrollment. Avoid
   manually curated one-off state unless the user explicitly wants that.

## Research Floor

Before implementation, answer these internally:

- What existing project behavior or prior maintenance log entry constrains this?
- What could collide with existing IDs, DB primary keys, runtime caches, or
  admin display semantics?
- Which facts must come from the internet or upstream docs instead of memory?
- Which search result could be wrong due to homonyms, stale pages, redirects, or
  scraped summaries?
- What is the smallest reproducible input that proves the implementation works?

## Verification Matrix

Do not declare done until the relevant rows have evidence.

- **Static**: `ruff`, `pyright`, `vue-tsc`, build, JSON/schema checks as needed.
- **Structural**: generated files contain expected counts, keys, dimensions,
  manifests, samples, routes, or config fields.
- **Semantic**: prove the core meaning, not only shape. Example: character packs
  need no duplicate `character_id`, correct `work/relation`, and collision
  checks against existing packs.
- **Runtime**: hit the actual read-only API/sidecar/admin endpoint when available.
  Confirm registry counts, health output, cache behavior, or UI visibility.
  External writes or messages require explicit current-task authorization.
- **Negative/collision**: test the case most likely to be confused, such as PJSK
  初音 vs 本家初音, same-title wiki results, or duplicate pack IDs.
- **Idempotency/rollback**: note whether rerun is safe and how to revert runtime
  data without deletion when possible.

## Character Pack Enrollment Rules

When enrolling CCIP packs:

- Use globally unique `character_id`; aliases may repeat, IDs may not.
- Do not use blind search-first-title for short or ambiguous names. Use exact
  source allowlists or verified upstream IDs.
- Record image source families in the script output and maintenance log.
- Dry-run first. Abort on missing images or duplicate IDs.
- Verify manifest counts, npz keys/dims, sample directories, sidecar health,
  registry sync, and representative `/identify` matches.
- Include a collision test for any character with multiple works/forms.

## Final Answer Contract

Close with:

- what changed or concluded
- evidence and commands/API checks that support it
- sources used when web was used
- residual risks or follow-up checks

Keep it concise, but never omit failed checks or unverified assumptions.
