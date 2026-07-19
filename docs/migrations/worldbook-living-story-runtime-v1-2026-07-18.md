# Worldbook Living Story Runtime v1 migration checklist

> 日期：2026-07-18
> 状态：Living Story v1 completed and deployed for the development allowlist on 2026-07-19

## Old -> New

| Surface | Old | New v1 | Compatibility / rollback |
| --- | --- | --- | --- |
| Persona | Persona v2 identity prompt | Persona Canon remains Persona v2 read-only truth | No duplicate identity store |
| Native world | Reserved state.world / ad-hoc prompt text | Triggered immutable Canon registry | Empty registry degrades to no block |
| Daily life | Schedule + mood scattered state | Typed Life State with source/confidence/scope/TTL | Gate off keeps existing state path |
| Story continuity | Latest-mtime active StoryArc | Stable main + side + ambient arc stack | Existing Arc JSON loads with defaults；多 legacy arc 无显式 main 时 fail-closed |
| Variables | Schedule heuristics / fixed increments | Committed-event reducer | Legacy update retained only when gate off if required |
| Events | Free-form LLM continuation | Storylet registry + deterministic conditions + durable budget commit | Registry empty/persist failure means no candidate |
| Conflict | Prompt instruction and one-setback convention | Drama budget, severity, cost, recovery | Selection gate independently reversible |
| Social | Dream-only gated factual evidence | Current-scope read-only chat projection | Never injected into background Schedule |
| Dream | Reflection may update Arc | Proposal -> validation -> commit lifecycle | Proposal gate off preserves current behavior |
| Prompt | Schedule-specific Arc block, no chat World Info | Shared bounded projection + PromptProvider | Chat/schedule gates independent |
| Trace | Partial prompt/provider traces | source/scope/hit reason/evidence/budget trace | No trace means candidate rejected or omitted |

## Actual files/modules

- services/worldbook/: domain, loaders/store, trigger, storylets, drama, projection, provider, adapters.
- plugins/worldbook/: plugin lifecycle, production Arc seed importer, shadow runner and runtime ownership.
- plugins/schedule/: minimal optional projection/ledger adapter；Worldbook Schedule gate 可独立注入共享 StoryArcStore，不依赖 legacy story-arc gate。
- plugins/dream/: optional proposal bridge only; no factual/Canon writes.
- bootstrap/chat_runtime.py: unchanged. ChatPlugin priority 0 already constructs PromptProviderBus before WorldbookPlugin priority 45; plugin lifecycle performs late registration without duplicate wiring.
- kernel/types.py: additive `worldbook_config/worldbook_runtime/worldbook_dream_bridge` lifecycle handles.
- services/tools/memo_tools.py: query-mode scope privacy hardening found by full-suite acceptance.
- services/system_module/: unchanged in Stage 0; `state.world/runtime.planner/self.reflection` remain reserved until an explicitly authorized Stage 1 runtime-state-bus migration.
- admin/routes/api/worldbook.py and admin/frontend/src/views/worldbook/: sanitized GET-only runtime snapshot and Calm Ops console.
- config/worldbook/: versioned Canon, Storylet and main/side/ambient Arc seed content.
- tests/: domain, persistence, trigger, projection, privacy, Storylet, Dream/Social lifecycle, seed, Admin and runtime regression tests.

No SQLite schema or destructive production data migration was required.

## Data compatibility

- Existing StoryArc files must load without migration; new fields use explicit defaults.
- 单个 legacy Arc 可兼容视为 main；多个 legacy Arc 必须由 authoring/migration 显式指定 main，运行时不会用 mtime 猜测。
- `event_budget.committed_event_ids` 是 exact idempotency state，不随可读 history 淘汰；Stage 0 接受其随 committed event 数量增长。
- No bulk rewrite of existing Arc or memory data.
- No production database schema change is required for v1 unless implementation evidence proves JSON persistence insufficient; any SQLite addition requires a separate versioned migration and backup gate.
- Canon registry and Storylet registry are version-controlled configuration, not learned factual memory.
- Life State and proposal stores use separate namespaces and atomic writes.

## Production Arc seed

- `plugins/worldbook/arc_seed.py` validates the entire configured Arc bundle before any write.
- It only creates missing `living_story_v1.main`, `living_story_v1.side_study`, and `living_story_v1.ambient_park` records. Existing Arc bytes are never overwritten.
- Invalid, terminal, duplicate, or foreign-main bundles fail closed before seeding.
- The legacy `weekly_life_20260716` Arc remains byte-identical and is retained as an additional side Arc.
- Startup and replay are idempotent. Production restart proof returned `existing=3`, `seeded=0`, with Arc/Life/partner/event hashes unchanged.

## Route/menu/API checklist

| Type | Old | New | v1 decision |
| --- | --- | --- | --- |
| Admin route | none | `/api/admin/worldbook/snapshot` | GET-only, sanitized, disabled/unmounted returns explicit unavailable reason |
| Admin menu | none | `/worldbook` | Visible only when canonical plugin state says `worldbook.enabled=true`; fetch failure hides it |
| Runtime config | no worldbook gate | explicit worldbook gates | Defaults remain false; development runtime override enables six gates for one allowlisted group |
| Prompt provider | none | worldbook provider | Registered only when enabled |
| Schedule adapter | StoryArc-only | optional shared projection | No Social Evidence |
| Dream adapter | direct reflection/Arc logic | optional proposal bridge | No factual/Canon commit |

## Rollout stages

1. Stage 0: source/tests only; all gates false. Completed.
2. Stage 1: deterministic seven-day fixture and shadow projection. Completed with essential hash `12bffcb74edc0832ff51d4e468d5638edbfd57ea71171cc29a0c46f6ad7feb35`.
3. Stage 2: Storylet/Life/partner, Dream commit, and bounded Social-to-story closure. Completed.
4. Stage 3: GET-only Admin/API observability and fail-closed plugin menu. Completed.
5. Stage 4: backup, bot-only deployment, restart continuity, off/on rollback drill and final enablement. Completed.

Defaults remain false in version-controlled plugin config. The development runtime override has all six gates true, with Worldbook and Social allowlists exactly `984198159`. QZone live/approval/credentials and NapCat were not changed.

## Rollback

- Preferred rollback is state-preserving: set the six Worldbook gates false and plugin state disabled, then restart only `bot`.
- Verified backup: `/app/storage/backups/worldbook-wave-e-rollback-20260719T021136Z` with manifest SHA-256 `cd0394e5c78752dcff4c3507bc8b5ba23b19497329ca27e80a12060a1d1dd473`.
- Restore `worldbook.json` and `plugin-state.json` from that backup, then run `docker compose restart bot`.
- Do not delete Arc/Life/partner/proposal state for routine rollback. Never run `docker compose down` or restart/recreate NapCat.
