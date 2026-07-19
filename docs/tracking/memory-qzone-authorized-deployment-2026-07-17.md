# Memory Stage-0 + QZone v0.8.1 authorized deployment

> 状态：completed_without_live_canary · 2026-07-17 · Stage-0 已部署；QZone 鉴权与 dry-run 完成；live gate 未满足，未发布

## Objective

将已离线终验的记忆系统与 QZone Journal v0.8.1 从当前 dirty worktree 受控部署到 bot，先应用 memory `stage0_dark`，再验证 bot/runtime/Admin 健康；QZone 先做 fixture/profile/credential/dry-run，只有前置门全绿时才执行单条最小 canary。NapCat 不重启、不重建、不改存储。

## Scope and authority

- Authorized: bot image build/recreate、Stage-0/main/plugin config、只读/必要迁移验证、QZone dry-run、单条 QZone canary、运行日志与 Admin/health 检查。
- Forbidden: `docker compose down`、NapCat restart/recreate、批量 QZone 发布、`git add -A`、清理/回退无关 dirty files、把 synthetic fixture 当真实 profile、把 `BUILTIN_WIRE_PROFILE.validated` 设为 true。
- Production baseline before this task: documented `98887a5`; exact running image/container state must be captured in preflight.

## Execution plan

1. Preflight: doctor、git/stash/untracked audit、Docker/bot/NapCat identity、disk、current config owners、QZone gates and rollback image.
2. Re-run deployment gates: rollout contract/QZone focused/static/frontend build as required.
3. Materialize `stage0_dark`: context/memo canonical plugin overrides + main knowledge_graph/block_trace values, preserving unrelated operator settings; backup config payloads.
4. Build/recreate bot only; verify startup, migrations/catalog, OneBot connection, Admin/health, Stage-0 disabled behavior; verify NapCat identity unchanged.
5. QZone authorized test: sanitized fixture/profile validation, credential availability, dry-run, tip/action guards; only then one canary publish and post-write verification.
6. Record exact evidence, rollback, maintenance-log; close ACTIVE only when deployment and authorized test have a truthful terminal state.

## Test Ledger

| Time | Command / experiment | Actual result | Conclusion |
| --- | --- | --- | --- |
| 2026-07-17 | continuity gate + full git/stash/untracked audit | Real repo `/Volumes/OmubotDisk/omubot`; ACTIVE was none; worktree contains reviewed memory/QZone plus large unrelated dirty/untracked scope | New deployment tracker created; enumerate config/build inputs; never clean or bulk-stage |
| 2026-07-17 | production baseline + rollback freeze | Running bot reports `GIT_COMMIT=98887a548eb574f5ab0d068b1529ad06e53f88aa`; old image tagged `omubot-bot:rollback-98887a5-20260717`; NapCat image `sha256:cde89d766604...`, restart count `0`; storage volume `omubot-storage`, about 7.2 GiB | Code rollback is locally addressable; bot-only operations remain mandatory; NapCat identity must remain unchanged |
| 2026-07-17 | host config backup | Backed up `config.json` and `config.toml` under `.workspace/deploy-backups/memory-qzone-20260717-stage0/host-config/`; SHA256 `8e5de7a8...` / `a24e10f2...` | Main-config rollback payload exists before production mutation |
| 2026-07-17 | memory rollout + QZone focused test suites | `587 passed` total: memory rollout related `314`, QZone focused `273` | Combined offline deployment gate green |
| 2026-07-17 | static/build gates | Ruff clean; Pyright `0 errors`; strict plugin layout passed; `vue-tsc --noEmit` passed; admin production build passed with QZone page chunk | Candidate source tree passes static, structural, and frontend build gates |
| 2026-07-17 | NapCat QZone credential check (secret-safe) | Login API and cookie API passed; cookie header and `p_skey` present; cookie UIN matches login UIN; only UIN hash prefix `0e5e6e5315a6` retained | Authentication material is available without logging/persisting secrets; this does not validate the wire profile |
| 2026-07-17 | QZone profile/fixture gate | No local `real_sanitized` fixture; built-in `qzone-text-v1-unverified` remains permanently `validated=false` | Live publish remains blocked until an independently captured, secret-scanned, validated profile exists; never flip the built-in flag |
| 2026-07-17 | `GIT_COMMIT=worktree-20260717-memory-qzone-stage0 docker compose build bot` | Build passed, including strict plugin layout; candidate image `sha256:d17b4a7c8476d7b5aed8605c14e5a2a9c5cba0cb8321ba1bb54c4cda6fd6fda1`; tags `latest` and `stage0-20260717-authorized`; image env has expected `GIT_COMMIT` | Candidate is frozen and traceable; proceed to bot-only stop and live-storage backup |
| 2026-07-17 | `docker compose stop bot` + identity check | `qq-bot` exited cleanly with original container/image identity; NapCat remained container `19f6cf13607c...`, running, restart count `0`, image `sha256:cde89d766604...` | Bot-only maintenance window established; NapCat red line preserved |
| 2026-07-17 | stopped-volume rollback backup | Copied plugin storage plus messages/memory_cards/episodic/knowledge_graph/block_trace/consolidator_candidates/learning_normalizer DBs and present WAL/SHM into `.workspace/deploy-backups/memory-qzone-20260717-stage0/live-storage/`; checksum pass completed; size `439 MiB`; QZone DB absent | Exact pre-start data/config rollback payload exists before migrations or QZone DB creation |
| 2026-07-17 | Stage-0 config materialization via `BotConfig` + real `PluginConfigStore` | Main KG provenance `true`, KG observability `false`, JDT `false`; context temporal/query/card/pack/evidence flags all `false`; memo write policy `false`; QZone state/config enabled with `dry_run=true`, live publish false, empty allowlist, daily cap 1 | Dark rollout shape validates through real config owners while preserving existing operator values |
| 2026-07-17 | candidate image + host config + stopped volume read-only validation | All Stage-0 assertions passed; old bot mount inspection confirms host `config/`, `admin/static`, and `omubot-storage` are the runtime sources; built-in wire profile still `validated=false` | Safe to recreate only bot from the frozen candidate image |
| 2026-07-17 | `docker compose up -d --no-deps --force-recreate bot` | New `qq-bot` container `ca0155fadab8...` running image `sha256:d17b4a7c...`, commit `worktree-20260717-memory-qzone-stage0`, restart count `0`; NapCat container/Created/image/restart count unchanged | Bot-only deploy completed; no dependency was recreated |
| 2026-07-17 | startup/OneBot/runtime verification | Application startup completed after the expected large-storage initialization window; OneBot connected with one bot; outbound guard and protocol trace wrapper installed; Admin `/api/admin/health` 200; runtime Stage-0 values match the closed profile | Bot and core message path are live on Stage-0 |
| 2026-07-17 | QZone startup health + DB verification | QZone API 200; config is enabled/dry-run/live-disabled/profile-unvalidated; DB created at schema `user_version=5`, `PRAGMA quick_check=ok`; one automatic `pending_review` draft exists, no published/dispatching/unknown rows | QZone local path is active without external write; do not approve or deliver until remaining gates pass |
| 2026-07-17 | Admin services health | Overall `degraded`: only error is catalog reporting QZone target schema `1` while the plugin correctly migrated DB to `5`, producing a false `future` version status; all other 12 services OK, SQLite quick checks themselves OK | Reproducible catalog contract bug blocks canary; add RED regression, fix catalog target at root, rebuild/recreate bot only, then recheck |
| 2026-07-17 | Admin auth source check | Runtime `ADMIN_TOKEN` authenticates; checked-in config token does not match the runtime environment source | Record as existing operator-source mismatch; do not mutate auth during this deployment |
| 2026-07-17 | catalog regression RED | New `test_qzone_catalog_tracks_current_schema_version` failed exactly `1 != 5` | Confirms health error originates in catalog metadata, not QZone migration or DB corruption |
| 2026-07-17 | minimal catalog fix + GREEN | `_spec` now accepts an explicit target version with default 1; only QZone declares 5; regression passes | Catalog can represent the real QZone migration head without changing any other DB contract |
| 2026-07-17 | catalog/QZone focused regression and static checks | Catalog/status/backup `62 passed`; QZone core/runtime/revisions `79 passed`; Ruff clean; Pyright 0 errors; diff check clean | Root-cause fix is locally accepted; rebuild and bot-only recreate required for runtime health |
| 2026-07-17 | catalog-fix candidate build | Strict plugin layout passed; image `sha256:0c01368005b16ce31c328a87d376bc94fd8c814653cb696eff80f17f65cd7d4d`; tag `stage0-20260717-authorized-v2`; commit env `worktree-20260717-memory-qzone-stage0-catalog-v5` | Second candidate is frozen and traceable; safe for one bot-only recreate |
| 2026-07-17 | catalog-v5 bot-only recreate + runtime recheck | `qq-bot` container `2e5afddabaa3...` running second candidate, restart count 0; Admin bot/OneBot health 200; QZone catalog now `5/5 current`, quick_check ok, zero SQLite errors; global services health is warning only because two pre-existing optional DB files are absent | Catalog bug is fixed in production; no deployment-related health error remains, but the literal all-green condition is not met |
| 2026-07-17 | pending draft manual review | One draft only; `self/public`, salience 0.82, 73 chars, allowed provenance keys, secret/ID-assignment scan false; no real-person private material | Safe to use this same draft for the authorized dry-run; no additional draft created |
| 2026-07-17 | approve → dry-run → publish negative gate | Approve 200 with audit row; dry-run 200 returned only profile/host/path/field names/content length+SHA256 and no credentials; publish returned local 409 `configured for dry-run only`; final draft remains approved with no publish date/external ID/error | Dry-run/action boundary works; zero external QZone write; built-in profile remains unvalidated |
| 2026-07-17 | post-deploy NapCat QZone credential check (secret-safe) | login/cookie APIs OK; cookie header and `p_skey` present; cookie UIN matches login UIN; hash prefix `0e5e6e5315a6`; no credential persisted | QZone authorization material is available and internally consistent after deployment |
| 2026-07-17 | final state audit | QZone DB v5 quick_check ok; status counts `{approved: 1}`; review decisions 1; published/external-post rows 0; Stage-0 flags still exact; bot/NapCat restart counts 0 | Deployment and safe authorization test complete; live canary correctly withheld because profile validation/live allowlist/all-green gates are false |

## Final outcome

- Memory worktree deployed under the closed `stage0_dark` profile. Structural slices are present; flappable write/retrieval/packing/prompt observability features remain dark except the required graph provenance gate.
- QZone v0.8.1 is deployed and enabled in dry-run mode. One production-generated public/self draft was manually approved and dry-run verified; it was not published.
- A production-only health defect was found and fixed with RED/GREEN: QZone catalog target version now matches schema v5. Runtime health no longer has a QZone/SQLite error.
- A real canary was **not** attempted. The built-in profile is permanently unvalidated, no `real_sanitized` fixture exists, live publish remains disabled, the UIN allowlist is empty, and global health still has a pre-existing warning for two optional absent DBs.
- Admin auth uses runtime `ADMIN_TOKEN`; the checked-in config token differs. No authentication setting was changed in this deployment.

## Rollback

- Stage-0 flag rollback: restore backed-up main/plugin config, then bot-only recreate.
- Code/runtime rollback: restore documented previous bot image for `98887a5` if locally available; never recreate NapCat.
- QZone: no automatic delete/rewrite. If canary publish succeeds, record external post identity and leave an auditable trail; rollback means stop further delivery, not destructive remote deletion unless separately authorized and supported.

## Current next step

Task complete. A future live canary requires an independently captured and secret-scanned `real_sanitized` fixture/profile, an ephemeral validated profile path that does not alter `BUILTIN_WIRE_PROFILE`, an explicit single-UIN allowlist, live flags enabled for the one approved draft, and a fresh no-error/no-unexplained-warning gate review.
