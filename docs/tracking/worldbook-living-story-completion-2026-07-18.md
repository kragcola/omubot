# Worldbook Living Story Completion

> 状态：complete · 2026-07-19 · Living Story 内容、因果闭环、观测、开发启用与回滚证明均完成

## Objective

把 Worldbook 从“默认关闭、空 registry 的安全底座”推进为真实可运行的 Living Story：

1. 无群聊刺激时，角色仍有自己的生活状态、目标和每日推进；
2. main/side/ambient 长期故事可跨天、跨重启保持因果与未解决线程；
3. 虚构伙伴有 pinned profile 与动态状态，不是工具人；
4. 当前群/当前用户的公开共同经历可有界影响角色故事，但绝不虚构真人线下行为；
5. 原生世界 Canon 与当前故事按触发器交融，不全量灌入；
6. 冲突具备条件、代价、后果、升级上限、cooldown 与恢复；
7. Dream 只提出 fiction proposal，经确定性验证后才能提交 Arc/Life State；
8. 整个闭环可 shadow replay、可观测、可回滚，并在开发 bot 上实际启用验收。

## Recovered baseline

- Stage 0 tracker：`docs/tracking/worldbook-living-story-runtime-v1-2026-07-18.md`。
- `services/worldbook/` 与 `plugins/worldbook/` 已完成 0C/11I 终审修复。
- 最终 Stage 0 全仓基线：4900 passed / 17 skipped / 186 warnings。
- 六项 gate 默认 false；未部署。
- `config/worldbook/canon/`、`config/worldbook/storylets/` 仅 `.gitkeep`，无可用内容。
- 现有 Schedule/StoryArc/Dream 已具备角色驱动、跨天、伙伴状态和 event replan 的 legacy 能力；本轮复用，不平行重造。

## Initial completion audit

| Requirement | Current evidence | Status |
| --- | --- | --- |
| 自主日常 | Schedule 每日生成与 legacy StoryArc 已存在，但 Worldbook Life State 无 producer | partial |
| 长期 main/side/ambient | ledger/store/projection 已有；无 Stage 1 authoring fixture 与 restart replay | partial |
| 虚构伙伴生活 | FictionPartnerStateStore 已有；Worldbook event/recovery 未形成端到端 fixture | partial |
| 群友交融 | Social Evidence 只读投影已严格 scoped；没有 factual evidence → bounded story consequence bridge | partial |
| 原生世界交融 | trigger/projection 已有；Canon registry 为空 | missing |
| 冲突与恢复 | Storylet/Drama/reducer 已有；Storylet registry 为空，无 7 天语义验收 | missing |
| Dream 闭环 | proposal-only bridge 已有；没有 validated/rejected/committed fiction lifecycle | missing |
| Shadow/eval | 无 replay runner、指标、报告或 identity comparison | missing |
| 观测/管理 | 无 Worldbook status/registry/proposal/shadow API 或 Admin 入口 | missing |
| 开发运行启用 | 所有 gate false，未部署 | missing |

## Fixed decisions

- 单 bot 活社会世界模型；不做多 agent society。
- Persona/Native Canon runtime read-only。
- 真人只允许当前 scope 的共同经历/主观印象；不生成真人线下行为。
- Schedule 永不注入 Social Evidence；social 影响只能来自真实聊天路径并绑定 evidence ref。
- 不新建第二个 StoryArc 真值源；复用 `StoryArcStore`。
- 自主推进优先 on-schedule/on-read，不增加高频常驻 tick。
- QZone live、NapCat、凭据、远端发布不在本 tracker 范围。
- 允许最终 bot-only 开发部署；部署前必须全绿、备份、可回滚。

## Post-audit architecture decisions

- **不新增高频 autonomous tick**：复用现有每日 Schedule loop 作为“无群聊刺激仍推进”的权威时钟；Worldbook 在 schedule commit 后更新 Arc/Life。只允许可选的 on-read TTL decay，不再造第二个 scheduler。
- **Storylet 必须先提交再投影**：选择、repeat budget、typed effect 与 EventRecord 在同一个 StoryArcStore 原子 update 内完成；commit 失败则不进入 Prompt。
- **typed effect contract**：Storylet `cost/consequence` 只允许闭集 `variable_deltas/open_threads/resolve_threads/stage/partner_updates/life_updates`；未知键 fail-closed。
- **Dream 不放宽 EventProposal**：保持 proposal-only immutable；新增独立 ProposalDecision/CommitRecord 记录 validated/rejected/committed，fiction-only validator 通过后才生成 EventRecord。
- **群友因果不保存原始叙事到主线**：SocialNarrative factual record 成功后，只允许生成绑定 evidence ref 的通用“角色受到互动影响”事件及有界 self-state delta；不得写真人行为、原文或私密内容。
- **Chat rollout fail-closed**：新增显式 group allowlist；空列表不投影。开发验收先使用项目既有测试群，QZone 与其他群不自动联动。
- **观测复用既有基建**：Worldbook candidate 使用 BlockTraceStore；补只读 status/proposal/shadow API，不新建重复 telemetry DB。

## Workstreams

### Stage 1 — Content + fixture + shadow

- 从本地 Persona freeze/source 提取最小 Native Canon registry，记录 source metadata。
- author fiction-only Storylet registry，覆盖日常推进、考试/排练冲突、伙伴受限、恢复与收束。
- 建立 7 天确定性 fixture replay：主线 + 副线 + 伙伴状态 + 单次 setback + 2–3 天恢复。
- Shadow evaluator 输出结构、语义、连续性、隐私、冲突预算与 trace 指标；不得改变 Prompt/生产状态。

### Stage 2 — Causal runtime closure

- Life State producer：从已提交 Schedule/Arc event 派生带 TTL 的 location/activity/mood/energy/open constraints。
- Dream fiction proposal validator/committer：proposal → validated/rejected → committed event；禁止 factual/Canon。
- Social-to-story bridge：当前 group/user evidence 只能产生“角色受到群聊影响”的 bounded event，不得产生真人线下行为。
- committed event 同步 Arc/Life/partner state，跨重启幂等。

### Stage 3 — Observability + authoring operations

- 提供只读 Worldbook status/registry/ledger/proposal/shadow report API。
- Admin 只读控制台展示 gate、registry counts、active stack、Life State TTL、proposal 状态、最近 trace 与 shadow verdict。
- 配置/内容继续走版本控制与 plugin config；不在 Web 里直接改 Canon。

### Stage 4 — Development enablement

- 全量 fixture/focused/full/static/frontend/runtime/collision 验收。
- 备份相关配置与独立 Worldbook/StoryArc state。
- bot-only build/recreate；永不触 NapCat。
- 在真实开发运行态确认 provider、registry、active arc、Life State、proposal/commit、Admin observability；不触 QZone live。

## Complete implementation plan (frozen 2026-07-18)

### Non-negotiable truth and write order

1. `StoryArcStore` remains the only fiction-ledger truth. Worldbook may add deterministic `EventRecord` entries, but must not create a second Arc database or shadow-only success path.
2. `SocialNarrativeStore` remains the factual evidence truth. The story bridge runs only after `record_shared_experience()` returns a persisted active record; a story failure never rewrites or deletes that factual record.
3. Dream truth is a three-record chain: immutable `EventProposal(status=proposal)` → immutable `ProposalDecision(validated|rejected, proposal_fingerprint)` → `FictionCommitRecord`. A commit record is valid only when the validated decision and Arc event both still exist and agree.
4. Arc mutation commits first. Life/fiction-partner effects are idempotent catch-up projections keyed by the same event ID. No prompt block may claim an event that failed to commit.
5. Canon stays versioned and runtime read-only. No Dream, Social, Admin or recovery path may write Persona/Native Canon.

### Wave A — Dream lifecycle closure (serial dependency)

- Bind the validation fingerprint to the complete persisted proposal, including its original `created_at`; semantically equivalent resubmission may ignore the caller's replacement timestamp but must retain the stored record byte-for-byte.
- Fail closed when a commit record has no matching validated decision, has a rejected decision, has a decision/proposal fingerprint mismatch, or has no matching committed Arc event.
- Preserve crash recovery order: validate persisted truth → verify/commit Arc event → catch up Life/partner → persist/return commit truth. Replays must not duplicate any effect.
- Exit gate: two new REDs green; five focused Worldbook/Dream suites green; Ruff/Pyright clean; Codex re-reads diff and runs independent adversarial probes.

### Wave B — Social-to-story bounded causal bridge

- Add an explicit Worldbook chat group allowlist. `enabled=true` with an empty list remains fail-closed. Runtime eligibility is the intersection of Worldbook's allowlist, the factual adapter's successful record path, current `group_id`, and current `user_id`.
- Accept only a freshly persisted active `SocialExperience` whose `entity_kind=factual`, evidence message ID/time/source are non-empty, and returned group/user exactly match the current reply context.
- Generate one deterministic main-Arc fiction event per `(experience_id, group_id, user_id, evidence_message_id, target_arc_id)`. If there is no explicit main Arc, do not seed or select one from social input; return a traceable no-op.
- The committed event contains only a generic bot-self consequence, for example “这次交流让角色更愿意整理自己的想法”, plus closed/bounded self-state deltas. It may persist the opaque evidence ref `social:<group>:<message>` and IDs required for verification, but must never copy `user_text`, `bot_reply`, names, private content, or assert any real-person offline action.
- Reuse the normal Arc reducer and Life/partner idempotency ledgers. Social input cannot create setbacks/crises, cannot mutate Canon, cannot target side/ambient arcs implicitly, and is never available to Schedule projection.
- Add BlockTrace observations for accepted, duplicate, no-main, disabled, empty-allowlist, scope mismatch, missing evidence, invalidated evidence, and forbidden payload outcomes without logging raw chat content.
- Exit gate: current-scope success; duplicate/restart idempotency; cross-group, cross-user, private, missing/invalidated evidence, empty allowlist and raw-text leakage all rejected; unchanged behavior when either plugin/gate is off.

### Wave C — Read-only observability API and Admin console

- Add `/api/admin/worldbook/*` read-only endpoints for status/gates, registry metadata/counts, active main/side/ambient ledger, Life State TTL/expiry, proposals/decisions/commits, recent Worldbook BlockTrace rows, and latest shadow report.
- API must tolerate the plugin being disabled or runtime unavailable and return explicit availability/reason fields. It must not create registries/state, mutate Canon/Arc/Life, validate/commit proposals, run shadow, change gates, or expose raw Social Narrative text.
- Proposal views expose IDs, statuses, reason codes, fingerprints, timestamps, Arc/event bindings and sanitized effect summaries; never secret/config values or unrestricted payload dumps.
- Build a detail-heavy Calm Ops page using existing `AppPage`, `AppCard`, `MetricCard`, `EmptyState` and `PageToolbar` components. The page prioritizes: health/gates → active story stack → Life TTL → Dream lifecycle → traces/shadow → registries.
- Add the Worldbook route/menu entry through the existing plugin-menu visibility helper. It appears only when the canonical plugin API reports `worldbook.enabled=true`; disabled, missing or fetch-failed state hides the first-level sidebar item while direct-route compatibility may show an unavailable empty state.
- Exit gate: backend tests, frontend contract tests, `vue-tsc`, production build, keyboard/focus/contrast/empty/loading/error states, and real API-to-render verification with no fake data.

### Wave D — Codex-owned independent acceptance

- Scope audit: inspect actual changed-file list and per-file diff; reject unrelated rewrites, weakened tests, secrets, QZone/NapCat/Docker mutations, duplicate stores and fail-open defaults.
- Static/structural: JSON/schema validation, Ruff, Pyright, `vue-tsc`, frontend build, migration/config file presence and `git diff --check`.
- Behavioral: focused Worldbook/Dream/Social/Admin suites, all relevant integration suites, then full pytest after D5 worker cleanup if needed.
- Semantic: deterministic 7-day shadow twice from clean temp roots; compare reports; require main/side/ambient evolution, at least two partner changes, one bounded setback, 2–3 day recovery, final active stage, Life TTL evidence, complete commit observation and no social data in Schedule.
- Privacy/collision: scan committed Arc/Life/trace/API/static assets for fixture raw `user_text`/`bot_reply`, cross-scope IDs, private-message markers, QZone hooks and accidental credentials. Verify deterministic IDs cannot collide across Arc/group/user/evidence dimensions.
- Identity/off checks: every Worldbook gate false and plugin disabled must preserve no-I/O/no-state/no-prompt behavior; menu hidden; existing Schedule/Dream baselines unchanged.

### Wave E — Backup, bot-only enablement and rollback proof

- Before mutation, record `git stash list`, `git status -uno`, all untracked build inputs, current bot/NapCat container IDs/timestamps, effective plugin states and current Worldbook/StoryArc/config hashes.
- Create timestamped copies of Worldbook config, plugin-state, `storage/worldbook/`, and `storage/living_persona/story_arcs/`; preserve live SQLite WAL semantics where applicable. No destructive cleanup or migration.
- Enable the Worldbook plugin and required gates only after Wave D passes. Enable Social-to-story only for the existing development test group allowlist; no grey percentage path and no other group. Keep QZone dry-run/live/approval state unchanged.
- Build frontend, then build and force-recreate only the bot container. Never restart/recreate NapCat and never run `docker compose down`.
- Runtime acceptance: plugin effective-enabled, provider registered, Canon/Storylet registry non-empty, explicit main/side/ambient stack visible, Life State readable, Dream decision/commit stores readable, Admin page visible, latest trace/shadow truthful, bot healthy across one restart, and QZone/NapCat container/config evidence unchanged.
- Rollback drill: restore gate/plugin config or set Worldbook disabled, recreate only bot, confirm provider/menu disappear and no further Worldbook mutation occurs. Preserve state by default; rollback must not require deleting Arc/Life/proposal data.

### Grok acceleration and ownership

- Codex owns architecture, sequence, acceptance, tracker and deployment decisions.
- One top-level write-capable Grok process may allocate adaptive background workers only to independent backend, test and frontend lanes, with one writer per full conflict domain and no duplicate live workstream. Conflict domains include APIs/types/schemas, generated outputs, fixtures, databases/caches and ordering dependencies, not only files/modules.
- Dream remains a focused serial correction. After it passes, Social backend/tests and observability frontend work may overlap only when every mutable conflict domain is disjoint; API integration and route wiring remain a named single-writer boundary.
- Grok may edit and run local tests only inside the delegated packet. It may not edit tracker/ACTIVE/maintenance-log, commit/push/publish/deploy, touch credentials, QZone live, NapCat or unrelated dirty files. Every Grok result is provisional until Codex independently accepts the diff and evidence.

## Acceptance matrix

| Layer | Required proof |
| --- | --- |
| Content | Canon/Storylet schema valid；无真人事实写入；source 可追溯 |
| 7-day semantics | 同一主线跨 7 天推进；主题不随机漂移；至少 2 名伙伴状态变化；最多 1 个重大 setback；恢复持续 2–3 天 |
| Social privacy | current group+user+evidence only；cross-group/user/private/missing evidence 全拒绝；不出现真人线下行为 |
| Dream lifecycle | proposal 可 validated/rejected；仅 fiction committed；重放幂等；Canon/factual 永拒绝 |
| Restart | 重新组装 runtime 后 Arc/Life/Storylet budget/proposal 状态连续 |
| Shadow | disabled/identity 不改 Prompt/状态；shadow report 可复现且 verdict 明确 |
| Observability | API/Admin 展示真实 registry/state/trace，不使用假数据 |
| Runtime | 开发 bot 实际加载并启用；QZone live/NapCat/凭据不变 |
| Rollback | gate off + bot-only rollback；独立 state 可保留，不需 destructive delete |

## Final delivered state

- **自主生活**：Schedule 是唯一日推进时钟；正式 Storylet commit 生成有限 TTL Life 条目。生产现有 `activity=舞台热身走位` 与 `open_constraint=伙伴各自推进，稍后同步`，均来自 `story_ledger`。
- **长期故事**：生产 main=`living_story_v1.main`、side=`living_story_v1.side_study`、ambient=`living_story_v1.ambient_park`；legacy `weekly_life_20260716` 字节不变并作为额外 side 保留。
- **伙伴生活**：partner state 保留 pinned profile，动态状态使用 event-id 幂等账本；生产伙伴树包含 Mafuyu/Rui/Nene/Tsukasa，重启及 off/on drill 前后 hash 不变。
- **Dream / Social**：Dream proposal→decision→commit 真值链与 Social factual→generic bounded story consequence 已完成；Social 仅允许群 `984198159`，不复制聊天原文、不写真人线下行为、不进入 Schedule。
- **冲突与恢复**：七日 shadow 强制一个正式 setback、两步后 recovery、最终 main `active` 且 `setback_flag=0`；essential hash=`12bffcb74edc0832ff51d4e468d5638edbfd57ea71171cc29a0c46f6ad7feb35`。
- **生产 seed**：三个 Arc seed 先全包验证、仅补缺失、不覆盖现有、非法/terminal/foreign-main fail-closed；restart probe=`existing=3/seeded=0`。
- **Admin**：`/worldbook` 与 GET-only snapshot 展示真实 gate/ledger/Life/Dream/trace/shadow；插件关闭、缺失或菜单状态拉取失败均 fail-closed。最终浅色最低对比度 `4.64:1`，深色 `5.88:1`，刷新目标 `80x44`，900/1440/1920 无水平溢出。
- **最终运行范围**：`worldbook=true`、`social_narrative=true`，Worldbook 六 gate 全 true，两个 allowlist 均仅 `984198159`。QZone live/approval/credentials 未触；NapCat 未 restart/recreate。

## Test Ledger

| Time | Experiment | Actual result | Conclusion |
| --- | --- | --- | --- |
| 2026-07-18 | recovery | Stage 0 complete；registries empty；all gates false；ACTIVE mode none | Stage 0 不是完整产品终点，建立 completion tracker |
| 2026-07-18 | original design comparison | Part B 要求经历→反思→规划，Part C 要求虚构伙伴动态状态与真人 factual 红线；现有 legacy Schedule/Dream 已覆盖部分，Worldbook 缺内容、commit、shadow、observability | 复用 legacy 能力，补 Worldbook 因果闭环而非重造 |
| 2026-07-18 | Grok completion gap audit | 目标 1/2/4/5/6/7 partial，目标 3 代码层 achieved 但 gate off；P0 为 content、shadow、storylet→event、Life writer、proposal commit、自主 continuity | 冻结 Stage 1 W1 content + W2 shadow/eval；不重开 Stage 0 缺陷 |
| 2026-07-18 | `uv run pytest -q tests/test_worldbook_shadow.py::test_storylet_budget_matches_committed_major_setbacks` | RED：`budget_setback_count=2`，`major_setback_count=1` | Storylet 选择预算与 EventReducer 双计 setback；shadow 当前 `overall_verdict=pass` 为假绿，必须由正式原子提交 API 消除双计并新增强制 invariant |
| 2026-07-18 | Stage 2 formal `commit_storylet` + shadow formal path | GREEN：mandatory RED 1 passed；focused suite 82 passed；ruff clean；pyright 0 errors | setback 只经 EventReducer 计一次；`budget_setback_count == major_setback_count == 1`；recovery_observed；未知 effect fail-closed；reject 无 life/partner；replay 幂等 |
| 2026-07-18 | Codex Stage 2 final acceptance | **95 passed**；Ruff clean；Pyright 0；7-day shadow `overall=pass`，9 formal commits complete，main/side/ambient 均变化，major=budget=1，final main `stage=active` / `setback_flag=0` | 接受 Storylet 原子提交、target Arc 路由、typed nested fail-closed、Schedule 全局时钟、per-Arc step cap、Life/partner 永不过期幂等账本与 shadow 完整观测；进入 Dream lifecycle |
| 2026-07-18 | Dream lifecycle implementation + first Codex hardening | 首版及 immutability/tamper/Arc-truth/non-Mapping 修正后，五个 focused 文件 **140 passed**；Ruff clean；Pyright 0 | proposal/decision/commit 主链已形成，但仍需绑定 persisted `created_at` 并拒绝无 validated decision 的孤儿 commit record，暂不接受 Dream 完成 |
| 2026-07-18 | `pytest` 两项 Dream integrity probes | **2 failed as expected**：仅篡改 persisted `created_at` 未被 fingerprint 捕获；孤儿 commit record 被误解释为 missing proposal/rejected，而非 `commit_record_without_validated_decision` | RED 有效；派发同一 Grok 工作流做定点根因修正，完成前不进入 Social 写入 |
| 2026-07-18 | Codex Dream final acceptance | 两项 RED **2 passed**；五个 focused 文件 **142 passed**；Ruff（含新增测试）clean；实现文件 Pyright **0 errors**；diff 复核确认 fingerprint 绑定原 persisted `created_at`，CommitStore 可发现非确定性 orphan record，existing-commit 在 decision/Arc truth 前不做 catch-up | **Dream lifecycle accepted**；Grok 报告未覆盖的新测试 RUF043 已由 Codex 机械修正；进入 Social-to-story + observability 并行实现 |
| 2026-07-19 | Social-to-story Codex acceptance | 真实 `ReplyContext` late-bound 回调、persist-success 顺序、failure 不回滚 factual、generic/raw-free event、Life TTL、clamped self state、duplicate/restart idempotency、privacy/scope/status/evidence/allowlist/no-main rejection 与 ID collision probes 全绿；新增关键测试 **14 passed**，六个 focused 文件 **163 passed**；Ruff clean；生产实现 Pyright 0；JSON/diff check 通过 | **Wave B accepted**；Social 仅提交 explicit main 的 generic `social_influence`，Schedule 无 social，生产 gate 仍 false；进入 Wave C 只读 API/Admin |
| 2026-07-19 | Worldbook observability API/Admin Codex acceptance | API 14 tests；Worldbook/Dream/Social/Admin 组合 **177 passed**；前端 node tests **10 passed**；Ruff clean；生产 Pyright 0；`vue-tsc --noEmit` 通过；Vite production build 通过；菜单 exhaustive contract 与 GET-only/no-style-literal checks 通过 | **Wave C accepted**；Codex 拒收并修正 Life GET 创建 lock 目录、proposal summary/key 泄漏、Storylet `severity` DTO mismatch、validated/pass/fail 状态色；登录态视觉被现有 Admin Token 挡住，留到部署后继续 |
| 2026-07-19 | Wave D full/static/shadow acceptance | 全仓 **5004 passed / 17 skipped**；仅两项因沙箱禁止向 repo fixture 写临时文件失败，原样复制到 `/tmp` 后 **2 passed**，可执行等价 **5006 passed**；Worldbook focused **228 passed**；双跑 shadow essential hash 稳定；Ruff/Pyright/vue-tsc/JSON/diff checks 全绿 | **Wave D accepted**；架构反向依赖清零，shadow 只统计本轮 committed event，历史状态不能垫高指标 |
| 2026-07-19 | Production Arc seed + bot-only deployment | 三个 seed Arc 全包验证后仅补缺失；legacy Arc 字节不变；bot image `sha256:9aa7e39aae78…`，container `29444b2626ac…`；Worldbook/Social 仅 allowlist `984198159` | 正式运行态 main/side/ambient、Life、partner 均已产生；QZone/NapCat 未动 |
| 2026-07-19 | Grok Wave E restart + rollback drill, Codex independent hash acceptance | bot-only restart；seed `existing=3/seeded=0`；关闭后 snapshot `available=false/reason=runtime_not_mounted` 且 state 不变；恢复后六 gate/plugin/allowlist 全绿。QZone config=`7a07a817…2753`、QZone plugin subtree=`32c602c3…8160`、NapCat aggregate=`0842ed44…bfe0` 前后不变 | **Wave E accepted**；最终保持启用。备份 `/app/storage/backups/worldbook-wave-e-rollback-20260719T021136Z` |
| 2026-07-19 | Admin visual/readability final acceptance | TDD 两轮 RED→GREEN；fresh focused Python **245 passed**；前端 Node **11 passed**；vue-tsc/build 通过；浅色最低 `4.64:1`、深色最低 `5.88:1`；刷新 `80x44`；900/1440/1920 overflow=0；浏览器 console 0 warning/error | **Admin accepted and deployed through bind-mounted `admin/static`**；业务/API 行为未改，bot/NapCat 无需重启 |
| 2026-07-19 | Codex/Grok parallel workflow acceptance | Codex 独立只读 review 发现并修正全局默认预算、`fork_turns`、30 分钟恢复语义、静默接管与 conflict-domain 表述；同步修正 deep-delivery 的 live DB `immutable=1` 和未授权 QQ/NapCat 外部发送规则。required Grok 审计会话 `cd6f3847-cee5-4da0-83b6-d45db50aeb8c` 明确 `auth_unavailable`，结构化 session 无 `subagents/`，按终止型认证故障停止；ledger：`$CODEX_HOME/state/parallel-runs/omubot-workflow-20260719.md` | **Codex workflow accepted；Grok required-parallel contract unmet due non-transient auth blocker**。不降级 optional、不伪造 child、不重试；无 QZone/NapCat/Docker/外部消息动作 |

## Rollback

1. `worldbook.enabled=false` 立即停止 Worldbook provider/selection/state mutation。
2. Shadow runner 默认使用临时目录，不写生产 state。
3. 新 Canon/Storylet 为版本化配置，可按文件精确回退。
4. 新 API/Admin 为只读；异常可单独回退路由与前端。
5. 运行态回滚备份：`/app/storage/backups/worldbook-wave-e-rollback-20260719T021136Z`；恢复其中 `plugins/config/worldbook.json` 与 `plugins/plugin-state.json` 后仅 `docker compose restart bot`。
6. 已实钻 gate/plugin off：provider/snapshot fail-closed、状态不再变化；恢复 true 后 Arc/Life/partner/event IDs 不重复不丢失。
7. NapCat 不 restart/recreate；QZone live/approval/credentials 保持不变。

## Next step

本 tracker 已完成。后续只需自然运行观测：等待真实聊天产生 scoped SocialExperience/Dream proposal 后检查 trace/decision/commit；不得为制造证据发送 QQ 消息或触碰 QZone live。独立安全债：生产未设置 `ADMIN_TOKEN`，当前 middleware 回退默认 `admin`；修改 secret 需要单独授权和明确交付方式。Admin snapshot 的 registry `runtime_loaded=false/count=0` 仍是只读快照可观测性缺口，provider 与正式 runtime 路径已验证，不影响本轮启用结论。
