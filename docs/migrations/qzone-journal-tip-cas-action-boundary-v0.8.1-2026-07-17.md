# QZone Journal v0.8.1 — Tip CAS / Action Boundary Hardening

> 日期：2026-07-17 · 版本：`0.8.1` · **无 schema migration** · 未部署

## 范围

Patch hardening on accepted offline v0.8. Not v0.9. Not a redesign.

| # | Contract | Owner |
|---|----------|--------|
| M1 | `create_revision` maps **only** `qzone_journal_drafts.supersedes_draft_id` UNIQUE (`SQLITE_CONSTRAINT_UNIQUE` / 2067 + constraint identity) → `InvalidDraftTransitionError` (Admin HTTP 409). Other IntegrityError / BUSY / cancel / unknown propagate. | `JournalStore` |
| M2 | Public read-only `JournalStore.is_lineage_tip(draft_id)` → `bool`; unknown id → `KeyError`. | `JournalStore` |
| M3 | `JournalDelivery.deliver` requires approved **and** lineage tip before describe / credentials / transport. Non-tip → domain transition error. | `JournalDelivery` |
| M4 | `confirm_published` / `confirm_not_published` revalidate tip inside the same `BEGIN IMMEDIATE` txn **before** idempotent shortcuts, mutation, audit insert. | `JournalStore` |
| M5 | Admin drawer: `canDryRun = isApproved && isLineageTip`, `canResolve = isUnknown && isLineageTip`; handlers + buttons share truth; `revisions=[]` non-actionable. | `DraftDetailDrawer.vue` |
| M6 | Plugin class + manifest version `0.8.1`. | plugin / plugin.json |

## Schema

**None.** No new tables, columns, indexes, or MigrationRunner entries.

## Rollback

1. Revert plugin/store/delivery/Vue/tests to v0.8.0 tip-gate surface (or prior commit of this slice).
2. Set manifest + `QZoneJournalPlugin.version` back to `0.8.0` if needed for ownership tests.
3. No DB migrate/down step. Existing v5 revision schema unchanged.
4. Recreate **bot only** if ever deployed; **never** touch NapCat / live QZone HTTP / credentials.

## Verification boundaries (offline)

- Focused: `tests/test_qzone_journal*.py`, `tests/test_qzone_producer_candidate_contract.py`
- Ruff / Pyright on changed Python
- `vue-tsc --noEmit` + `npm --prefix admin/frontend run build` (do not retain `admin/static` as commit inputs unless separately intended)
- Assert `BUILTIN_WIRE_PROFILE.validated is False`; no `validated=True` assignment
- No live DB, Docker, NapCat, credential reads, or real QZone HTTP

## Codex acceptance (2026-07-17)

- Host SQLite probe returned extended code `2067`, name
  `SQLITE_CONSTRAINT_UNIQUE`, and the exact
  `qzone_journal_drafts.supersedes_draft_id` identity; the classifier matched.
- Both manual-resolution Admin routes returned HTTP 409 for a historical
  non-tip `unknown` row without status, audit, or budget mutation.
- QZone focused suite: **273 passed**.
- Full repository: **4775 passed, 17 skipped, 186 warnings**.
- Scoped Ruff clean; Pyright **0 errors**; `vue-tsc --noEmit` and JSON parsing
  passed; `admin/static` contains no retained build change.
- Codex direct review found no Critical or Important defect in the frozen
  contracts. Independent Grok review synthesis was attempted in the original
  session, a resumed session, and compact rebuilds, but the upstream
  `grok-4.5` stream repeatedly ended at HTTP 524 before returning findings.
  This document therefore makes no independent-Grok `0C/0I` claim.

## Explicit non-claims

Does **not** claim deployment, real sanitized CGI fixture conformance, live publish, canary, or enabling `validated` wire profiles.
