---
name: omubot-deep-delivery
description: Omubot deep-delivery workflow for tasks where shallow execution would be harmful. Use when the user criticizes reasoning depth or verification quality, asks to search the web, asks to enroll/build datasets or character packs, touches agent prompts/skills/hooks, or requests a production-facing change that needs research, collision checks, dry-runs, runtime validation, and a rollback story.
---

# Omubot Deep Delivery

Use this skill when the task can fail silently: internet-sourced facts, character
pack enrollment, prompt/skill/hook changes, production runtime changes, or any
turn where the user says the previous work lacked depth, initiative, or
verification.

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
- **Runtime**: hit the actual API/sidecar/admin endpoint when available. Confirm
  registry counts, health output, cache behavior, or UI visibility.
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
