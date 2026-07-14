# 插件整改 D3 迁移清单（2026-07-13）

## Command / Entry

- [x] startup 前 command snapshot → startup 后 atomic command registry。
- [x] toggle 只刷新 tools → 同一事务刷新 tools + commands，失败回滚两者。
- [x] group hook-first → access/silent/mute guard 后 command-first，再进入 plugin hooks。
- [x] literal-space sub args → arbitrary-whitespace parsing。
- [x] partial segment scan → complete segment scan for slash command text。
- [x] unknown slash → LLM passthrough → command-owned response/consume。
- [x] root-only Admin metadata → aliases/subcommands/inherited permission/apply state。
- [x] split admin truth (`config.admins` vs `SUPERUSERS`) → one effective admin predicate。
- [x] command collisions/validation last-wins → canonical token + regex FSM fail-fast；多 registry 两阶段 commit/rollback。
- [x] tool provider failure partial commit → owner error + commands/tools/plugin state/persistence atomic rollback。

## Capability / Ownership

- [x] plugin config state + ChatRuntime private state → one effective capability state for affection/schedule。
- [x] split ScheduleGenerator start/stop → one lifecycle owner。
- [x] `plugins/schedule/calendar.py` consumers → canonical calendar service owned by `calendar_context` domain/service layer。
- [x] kernel/services imports from plugins → service/domain-neutral types/helpers。
- [x] Dream global cron host → Dream-only lifecycle + Calendar birthday owner + Memory consolidation owner。
- [x] Dream private field only → typed `ctx.dream` publish/unpublish。
- [x] HistoryLoader locked plugin hook → `RuntimeConnectionPipeline` core stage/status。
- [x] Chat `/debug`、`/authority` → `debug_commands` 唯一 command/handler owner。
- [x] Slang/Style independent periodic extraction + Style→Admin import → shared `LearningExtractCoordinator` + service extraction owner。
- [x] split kernel/service Tool ABC + plugin casts → one canonical Tool/ToolContext ABI。

## Business Correctness

- [x] affection zero increment division → bounded/no-op-safe scoring contract。
- [x] Bilibili detector-only ep/ss → real ep/ss resolution and trigger context path。
- [x] Food textual negative leaking into positive taste → structured exclusions before positive filter。
- [x] trusted arbitrary LLM food text → candidate/constraint validation + deterministic fallback。
- [x] Food search process-local flag → persistent effective override with immediate apply。
- [x] Echo expired/completed same-text state → new window reset。

## Surface / Compatibility

- [x] manifests/dependencies/toggle policy reflect new owners and restart semantics。
- [x] Admin API/UI reflect actual runtime command/capability state。
- [x] docs/wiki/comments no longer claim Chat/Dream/plugin lifecycle ownership that has moved。
- [x] old imports/legacy helper entrypoints removed or deliberately shimmed with tests。
- [x] blocked/off/silent_learn/mute group zero-outbound behavior unchanged。
- [x] rollback steps and bot-only deployment evidence recorded in tracker/maintenance log。
- [x] 第一轮 manifest 审计余项明确保留为独立未闭环平台合同，不随本轮 10I/4M/3D 自动清零。
