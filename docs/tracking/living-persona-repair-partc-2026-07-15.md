# Living Persona 全链修复与 Part C 实装

> 状态：complete
> mode: none
> 最后更新：2026-07-15（post-deploy docs closure）
> 当前下一步：none
> 阻塞：无
> 验证证据：代码/review/验证 + 生产备份/迁移/bot-only 部署 + 180s 公开 silent 观察 + NapCat 不变 均已完成。
> 回滚入口：tag `omubot-bot:pre-living-persona-98887a5-20260715` → image `sha256:56f51b2ce8d58c268085ad3bcdf1a31335e730e31734826ea1b6636bafac1a44`（runtime `d51a7d41…`）；备份 `pre-change-20260715-201745`；仅 recreate bot；NapCat 不动。

## Resume Capsule

- objective: 按审计优先级修复 Living Persona 全链，随后实装 Part C 真人 Social Narrative 并部署 — **已完成**。
- next_step: none
- current_files: n/a（tracker closed）
- last_verified: 2026-07-15 production deploy + independent post-deploy comparison + 180s observation。
- do_not_redo: 不再重复审计/实装/部署/迁移 apply；不要把 post-start Dream 增长误读为 migration drift。
- rollback: 关 `social_narrative.enabled` 与相关 flags；bot 回滚到 `omubot-bot:pre-living-persona-98887a5-20260715`；数据侧 expired 标记可审计保留；NapCat 不动。
- implementation_sha: `98887a548eb574f5ab0d068b1529ad06e53f88aa`

## Section Progress

| Section | Status | Evidence / Note | Next Update |
| --- | --- | --- | --- |
| Context | done | 生产、SQLite、代码、测试、文档全链审计完成 | 无 |
| Plan | done | 修复顺序、Part C 存储与证据契约已冻结 | 无 |
| Reliability fixes | done | Dream 同日幂等、StoryArc 生命周期、Climate post-reply、persona fail-fast、affection/climate 接线、backup/catalog、health 权限可观测 | 无 |
| Part C | done | `plugins/social_narrative` v0.1.0 + `services/social_narrative`；生产仅两测试群 enable | 无 |
| Verification | done | full pytest / Ruff / Pyright / manifest / review + 部署/runtime/观察 | 无 |
| Migration apply | done | freeze matrix + apply；`invalid_active` 56→0；无 delete/reassignment | 无 |
| Deploy | done | bot-only rebuild/recreate；NapCat invariant | 无 |
| Handoff | done | 本 docs-closure packet 收口 ACTIVE + maintenance-log | 无 |

## Scope And Acceptance

1. Dream 同一自然日幂等；卡片与 Arc 更新可重试且不半写；reflection group/scope 只能使用真实 group ID 或 `global/global`。
2. StoryArc 支持未开始/active/terminal/expired 生命周期、归档、并发更新保护和干净存储启动策略。
3. Climate post-reply 经真实 manifest + PluginBus 派发；多人 mood 不错归；M2/M3/M4 合法组合不吞旧信号；snapshot 按 session+user 隔离。
4. Persona 初次加载失败必须中止；hot reload 失败继续持有旧 bundle。
5. Affection/Climate/Willingness/旧 Coupling 收敛为有明确 producer/consumer 的状态；Health/Admin 能揭示权限断链和 Living Persona 运行状态。
6. StoryArc/partner/social factual 状态进入 daily/migration/pre-change 备份。
7. Part C 真人只允许 `factual`：来源为当前群可追溯共同经历或既有 factual memory；禁止虚构线下行为，禁止私聊进群，禁止 fiction/factual 混写。
8. Part C 至少接入：事实采集/派生状态、日程/反思叙事输入、普通对话上下文、Admin/health 可观测与全局开关；默认关，部署后仅测试开发群启用。
9. 上线前完成 focused + full pytest、ruff、pyright、JSON/schema、备份清单；容器运行态与公开 silent 群零出站负向窗口已在部署 packet 完成。

## Part C Frozen Contract

- `fiction`: 继续由 `FictionPartnerStateStore`/StoryArc 管理，可演绎虚构事件。
- `factual`: 真人实体只保存 bot 的主观印象、群聊中可验证的共同经历和关系温度；每条必须带 `group_id`、`user_id`、evidence message id/time/source。
- 不把昵称、推测、情绪分类或 LLM 补全提升为事实；没有证据就不写。
- 私聊证据不得进入任何 group-scoped social narrative；跨群默认不共享。
- 生成器只消费结构化、已筛选的 factual 摘要，并附硬提示“不可补写该真人未在证据中出现的行为”。
- 数据写入需幂等 key；删除/撤回原消息后对应派生叙事可失效。
- Part C factual 数据落在现有 `memory_cards.db` 的独立表，不复用 `memory_cards.scope`：后者只有 user/group 单一维度，无法同时表达 `(group_id,user_id)`，并会经全局索引或群卡注入造成跨 scope 暴露。
- `memory_cards.db` 继续是统一 critical backup 边界；catalog 将 `services.social_narrative` 登记为 client，独立表以 `(group_id,user_id,evidence_message_id,evidence_source)` 作为幂等边界。
- 插件：`plugins/social_narrative` v0.1.0，kind=user，`restart_required`，memory 类；默认 `enabled=false`，`allowed_group_ids=[]`（fail-closed）。

## Production Migration (ground truth at freeze / apply)

**Count-gate 纠正**：早期表述若把「15/209」读成「15 active valid / 209 active invalid」是错误的。冻结矩阵是 **历史 scope 总量** 分解，不是 active-only。

| 集合 | 总数 | 分解 | 备注 |
| --- | --- | --- | --- |
| dream_reflection 历史总量 | **224** | — | freeze 时 bot stopped |
| valid `global/global` 总量 | **15** | **4 active + 11 superseded** | 合法保留；不改写 |
| valid 测试群行 | **0** | — | freeze 时 |
| invalid 总量 | **209** | **56 active + 29 expired + 124 superseded** | 禁止 reassignment |
| apply 候选 N | **56** | 全部 invalid **active** | 仅这 56 行改 status→`expired` |
| valid active M | **4** | freeze 时 | 合法 keep |

**Apply 结果（bot stopped）**：

- `expired_count=56`；`before.invalid_active=56`；`after.invalid_active=0`；`quick_check=ok`
- 全部候选变为 `expired`；全部 invalid 非候选 status 不变
- 224 备份行保持语义 identity/content/scope；**无 delete、无 reassignment**
- 之后 live invalid total 仍为 **209** 且 `invalid_active=0`（209 = 56 新 expired + 153 原本已非 active）

**动态 valid 总量 ≠ migration drift**：freeze/apply 后 bot 正常启动，Dream 可继续写新的合法卡。独立 post-deploy 抽样曾见 live total **226** / valid total **17**（相对 freeze 的 224/15 多 2 张新 valid），后续 valid status 计数还会演化。**冻结矩阵与 apply 数字是 migration 真值**；部署后 valid 增长属正常 post-start Dream 行为，不得回写成「迁移漂移」。

### Trusted backup used for apply

| Field | Value |
| --- | --- |
| ID | `pre-change-20260715-201745` |
| path | `/app/storage/backups/pre-change/2026-07-15-201745` |
| created | `2026-07-15T20:18:15.407203+08:00` |
| schema_version | 2 |
| complete | true |
| trusted | true |
| memory_cards SHA256 | `081c43e0a408f2ada0c7b323445ab0078f156025d88848c7973cdf19404aee3a` |
| quick_check | ok |
| build_restore_plan | memory_cards: one item, `can_apply=true`, `compatibility=upgrade_on_start` |

Earlier safe-abort backup `pre-change-20260715-200450` exists as **extra recovery evidence** but was **not** used for apply.

### Independent post-deploy comparison

- backup SHA exact match；restore plan still usable
- all 224 backed semantic rows equal
- `candidate_count` 56 all expired
- invalid noncandidate statuses equal
- live invalid total remains 209 and `invalid_active=0`
- normal post-start Dream growth observed (see above) — not migration drift

## Production Deploy Evidence

### Implementation

- commit：`98887a548eb574f5ab0d068b1529ad06e53f88aa`（`feat(living-persona): add factual social narrative`）

### Old production (rollback target)

| Field | Value |
| --- | --- |
| container | `0f7f47c3ffaea547aa2d6b2138f55731a9fcce27272f603961edafb72efd1669` |
| image | `sha256:56f51b2ce8d58c268085ad3bcdf1a31335e730e31734826ea1b6636bafac1a44` |
| runtime GIT_COMMIT | `d51a7d41bed5b031659e09dcfd148c10e6cd4e0a` |
| rollback tag | `omubot-bot:pre-living-persona-98887a5-20260715` → 上述 image |

### New production

| Field | Value |
| --- | --- |
| container | `4199a39340f0d5a9ac398aee293bb50586cf3d0d7ac689c39bbcf7a863e382a7` |
| image | `sha256:01c68ae819b2b364c2d1bd721eddf13e9ce5e4d83ea171720836db2ed4b6f502` |
| StartedAt | `2026-07-15T12:20:22.459948294Z` |
| runtime GIT_COMMIT | `98887a548eb574f5ab0d068b1529ad06e53f88aa`（exact） |
| restart | 0 |
| OOM | false |
| state | running |

### Social Narrative (production config)

- path：`/app/storage/plugins/config/social_narrative.json`
- `schema_version=1`；`enabled=true`
- allowlist **exact**：`["984198159","993065015"]`
- authenticated Admin services health：`social_narrative` status=`ok`，**0 active / 0 entities**，allowed exact two（empty healthy store at observation）

### StoryArc

- active ledger empty
- archive contains `stage_play_competition_week.json`
- **no seed** of new arcs

### Startup / Admin / errors

- banner exact commit `98887a548…`
- Application startup complete
- OneBot bot `384801062` connected
- group outbound access guard installed
- Bot ready
- `/admin/` **200**；authenticated Admin health reachable
- no ERROR / CRITICAL / Traceback since start

### Passive observation window

| Field | Value |
| --- | --- |
| window (UTC) | `2026-07-15T12:25:18Z` → `2026-07-15T12:28:18Z`（180s） |
| log lines | 92 |
| `message.group` | 46 |
| `silent_learn` | 46 |
| public inbound groups | `805836168`, `860324414`, `477640404`, `953023811`, `963085812` |
| authorized-group inbound | 0 in window |
| bot outbound send/poke lines | 0 |
| successful non-authorized send/poke | 0 |
| window errors | none |
| QQ probes | none |

### NapCat exact invariance

| Field | Value |
| --- | --- |
| container | `19f6cf13607cb7e9097dd0c5484e375e7abf73110c90d1e703721cfc0471278e` |
| image | `sha256:cde89d766604e570517e9ce66304d4222210479d2209ec04e5d58890dea087f7` |
| StartedAt | `2026-07-09T22:51:47.963549084Z` |
| restart | 0 |
| OOM | false |
| state | running |
| ops | **never** stopped / restarted / recreated / exec'd into / config-changed |

## Todo

### 已完成（代码 / 测试 / 验证）

- [x] Dream/StoryArc reliability 修复（同日幂等、真实 group scope、cancel-safe、生命周期/归档/并发保护）
- [x] Climate/Persona/关系接线修复（post-reply PluginBus、snapshot 隔离、persona fail-fast / hot-reload hold-old、affection-climate producer-consumer）
- [x] backup/catalog 纳入 `services.social_narrative` 与 story_arc/partner/social factual
- [x] health 权限拒绝可观测
- [x] 生产污染数据迁移方案与 remediation 工具（`tools/living_persona_remediation.py` dry-run/apply 能力）
- [x] Part C 数据模型与 RED→GREEN tests
- [x] Part C service/runtime/prompt/schedule/dream/admin 接线（`plugins/social_narrative` v0.1.0）
- [x] 全量验证（full pytest / scoped Ruff / Pyright / manifest / layout / JSON parse / diff-check）
- [x] independent review：0 Critical / 0 open Important

### 已完成（部署 packet）

- [x] 生产 named-volume 备份（trusted `pre-change-20260715-201745`；extra abort backup `…-200450` 未用于 apply）
- [x] 污染迁移 dry-run → apply（56 invalid **active** → expired；209 invalid 历史总量不变语义；15 valid historical total keep；StoryArc archive）
- [x] bot-only 部署（`compose build` 因 Buildx EPERM 失败 → 直接 `DOCKER_BUILDKIT=1 docker build ... -t omubot-bot:latest .` → `up -d --no-deps --force-recreate bot`；**永不 touch NapCat**）
- [x] 部署后观察（startup/OneBot、silent 群零出站、测试群 allowlist 启用 social_narrative）
- [x] maintenance-log 证据条目
- [x] post-deploy docs checkpoint 记录精确 implementation commit SHA `98887a548eb574f5ab0d068b1529ad06e53f88aa`
- [x] implementation commit 已落盘（相对 pre-commit base `0e6827762aaaa28336d0e8c6481993452dfd7565`）

## Decisions

| Decision | Choice | Why | Date |
| --- | --- | --- | --- |
| `PC` 解释 | Living Persona Part C | 上下文唯一对应未实施项 | 2026-07-15 |
| Part C 形态 | 单 bot 世界模型，不做多 bot society | 延续 Part C 已冻结形态 | 2026-07-15 |
| 真人事实边界 | 当前群证据 + 主观印象，禁止线下脑补 | Part C 红线与可验证性 | 2026-07-15 |
| 上线范围 | 仅现有 active 测试群，公开群继续全局零出站 | 用户既有上线裁定 | 2026-07-15 |
| Part C 存储 | `memory_cards.db` 独立 factual tables | 保留统一备份，同时避免 CardStore 单 scope 的跨群/跨用户暴露 | 2026-07-15 |
| Dream 污染迁移 | **56** 张 invalid **active** 标 `expired`（invalid 历史总量 209）；不删除/不改归属 | 无 evidence 时不能推定真实群；仅 active invalid 需熄火 | 2026-07-15 |
| 默认 fail-closed | `social_narrative.enabled=false`，`allowed_group_ids=[]` | 部署前零行为变更；仅测试群显式放开 | 2026-07-15 |
| 生产 allowlist | `984198159` + `993065015` only | 用户裁定的两个 active 测试群 | 2026-07-15 |
| 部署完成 | bot-only recreate；NapCat 不变 | D6 NapCat 红线 | 2026-07-15 |
| count-gate | freeze 224/15/209 为历史 scope 总量；非 15/209 active | 防止把 superseded/expired 计入「待改 active」 | 2026-07-15 |
| post-start Dream | valid 总量可在 15 之上增长 | 正常运行态写卡，非 migration drift | 2026-07-15 |

## Files Touched

| File / Category | Change | Status |
| --- | --- | --- |
| `plugins/dream/`、`plugins/schedule/` | Dream 幂等、StoryArc 生命周期、Climate post-reply 接线 | deployed |
| `services/dialogue_climate/`、`services/persona/`、affection/willingness 相关 | post-reply、snapshot 隔离、fail-fast、producer-consumer | deployed |
| `plugins/social_narrative/`、`services/social_narrative/` | Part C v0.1.0；独立表；factual-only | deployed |
| `bootstrap/chat_runtime.py` 等接线 | persona fail-fast、social_narrative 装配 | deployed |
| backup/catalog、health | social_narrative client；权限拒绝可观测 | deployed |
| `tools/living_persona_remediation.py` | 污染迁移 dry-run/apply | applied in prod |
| tests：`test_social_narrative*`、`test_living_persona_remediation*`、`test_chat_runtime_persona_failfast*`、`test_schedule_plugin_*` 等 | RED→GREEN + cancel-path | done |
| `docs/tracking/living-persona-repair-partc-2026-07-15.md` | 本任务追踪器 | complete |
| `docs/tracking/ACTIVE.md` | Current closed；Pending 保留 | idle |
| `docs/migrations/living-persona-partc-2026-07-15.md` | 迁移清单 deployed/complete | complete |
| `docs/wiki/Plugins.md` | 本地包 24；runtime 22；用户 20；登记 `social_narrative` 0.1.0 | docs done |

## Verification

| Check | Command / Evidence | Result |
| --- | --- | --- |
| Pre-audit focused tests | Dream/Climate/StoryArc/PluginBus/关系相关 suites | 186 passed（审计期） |
| Production DB health | read-only `PRAGMA quick_check` | 4 DB ok（审计期） |
| Runtime identity | container PersonaRuntime load | 凤笑梦 / 4273 chars / 6 blocks（审计期） |
| Full pytest | `uv run pytest` | **3686 passed / 17 skipped / 202 warnings / 0 failed in 63.78s** |
| Scoped Ruff | `uv run ruff check`（改动范围） | clean |
| Scoped Pyright | `uv run pyright`（改动范围） | 0 errors |
| Plugin manifests | 24 manifests 校验 | valid |
| Plugin layout | layout 检查 | clean |
| Plugin JSON parse | 72/72 plugin JSON | parse ok |
| Diff hygiene | `git diff --check` | clean |
| Independent review | Living Persona / Part C | **0 Critical / 0 open Important** |
| Freeze matrix | bot stopped totals | total 224；valid 15=4a+11s；invalid 209=56a+29e+124s；N=56 M=4；quick_check=ok |
| Trusted backup | `pre-change-20260715-201745` | trusted complete schema_version=2；SHA `081c43e0…`；restore plan can_apply |
| Migration apply | remediation apply | expired_count=56；invalid_active 56→0；no delete/reassignment |
| Post-deploy compare | backup vs live | 224 semantic equal；SHA match；invalid_active=0 |
| Bot runtime | docker inspect / logs | container `4199a393…`；image `01c68ae8…`；GIT_COMMIT exact `98887a548…`；restart=0 OOM=false |
| Admin/health | `/admin/` + services health | 200；social_narrative ok 0/0；allowlist two |
| StoryArc | ledger/archive | active empty；`stage_play_competition_week` archived；no seed |
| Observation 180s | UTC 12:25:18–12:28:18 | 46 silent_learn；public inbounds only；outbound 0；errors none |
| NapCat | inspect invariant | container `19f6cf13…`；StartedAt 2026-07-09；restart=0；never touched |

### Same-pattern scan（D1 摘要）

已扫并在实现中对齐的模式位点：

- Dream / StoryArc **cancel-path**（wait_for / shutdown 取消不污染半写）
- Climate **post-reply 权限**（manifest permissions + PluginBus `fire_on_post_reply`）
- Persona **初次加载 fail-fast** vs **hot-reload 失败 hold 旧 bundle**
- Backup/catalog **clients** 登记（含 `services.social_narrative`）
- Social narrative **证据边界**（必须 evidence；昵称/猜测不升格事实）
- **私聊排除**（private → group-scoped narrative 禁止）

## Test Ledger

Append-only. Every meaningful bug experiment records exact command, actual result, and conclusion.

| ID | Command / Input | Actual Result | Conclusion | Date |
| --- | --- | --- | --- | --- |
| LP-001 | real `SchedulePlugin` + local manifest + `PluginBus.fire_on_post_reply` probe | permissions omit reply; callback=0; permission_limited | post-reply dead path confirmed | 2026-07-15 |
| LP-002 | two Climate snapshots for u1/u2 in same `group_100` session | u2 write makes u1 unreadable | per_session key overwrites per-user snapshot | 2026-07-15 |
| LP-003 | production Dream log + memory_cards read-only query | 11 batches / 32 cards on 2026-07-15; 224 total | restart amplification confirmed | 2026-07-15 |
| LP-004 | production StoryArc JSON read | ended 2026-06-16 but generated_days=28 and July 15 events | missing lifecycle confirmed | 2026-07-15 |
| LP-005 | container `memory_cards.db` read-only scope dry-run | quick_check ok; 224 Dream cards; 15 valid `global/global`; 209 invalid scopes; all lack evidence time/message | pollution can only be expired, not reassigned | 2026-07-15 |
| LP-006 | container StoryArc + 4 partner JSON read | expired arc still has July 15 events and `generated_days=28`; fiction partners inherited stale state | archive must stop both arc and partner propagation | 2026-07-15 |
| LP-007 | focused `tests/test_schedule_plugin_*.py` + Dream/StoryArc cancel-path suites | GREEN：同日幂等、scope 校验、cancel 后无半写/无污染 | reliability Dream/StoryArc 验收 | 2026-07-15 |
| LP-008 | focused Climate post-reply + snapshot isolation suites | GREEN：PluginBus 带 reply 权限可 fire；同 session 多 user snapshot 不串 | Climate dead path 已修 | 2026-07-15 |
| LP-009 | `tests/test_chat_runtime_persona_failfast*.py`（及等价 suite） | GREEN：initial load fail 中止；hot-reload fail hold 旧 bundle | persona 契约验收 | 2026-07-15 |
| LP-010 | `tests/test_social_narrative*.py` RED→GREEN focused suites | GREEN：factual-only、evidence 必需、private 排除、fiction 不混写、fail-closed 默认 | Part C 核心契约验收 | 2026-07-15 |
| LP-011 | `tests/test_living_persona_remediation*.py` dry-run/apply | GREEN：invalid active → expired；valid keep；no reassignment | 迁移工具可上线前 dry-run | 2026-07-15 |
| LP-012 | backup/catalog + health permission denial suites | GREEN：social_narrative client 入 catalog；permission denial 可观测 | backup/health 接线验收 | 2026-07-15 |
| LP-013 | same-pattern scan（Dream cancel、Climate reply perm、persona fail-fast、backup clients、evidence 边界、private 排除） | 相关位点已对齐或有覆盖测试 | D1 同模式扫描通过 | 2026-07-15 |
| LP-014 | plugin manifest/layout/JSON：24 manifests + layout clean + 72/72 JSON parse | all valid / clean | 插件包装交付干净 | 2026-07-15 |
| LP-015 | scoped Ruff + scoped Pyright on changed paths | Ruff clean；Pyright 0 | static 干净 | 2026-07-15 |
| LP-016 | full `uv run pytest` | **3686 passed / 17 skipped / 202 warnings / 0 failed in 63.78s** | 全量回归通过 | 2026-07-15 |
| LP-017 | independent review | **0 Critical / 0 open Important** | 实现可进入部署 packet | 2026-07-15 |
| LP-018 | `git diff --check` | clean | 无 whitespace 脏 diff | 2026-07-15 |
| LP-019 | first safe abort / count reconciliation（bot stopped freeze matrix vs early 15/209 wording） | total 224；valid 15=4 active+11 superseded；invalid 209=56 active+29 expired+124 superseded；N=56 M=4；quick_check=ok；extra backup `pre-change-20260715-200450` | **count-gate 纠正**：15/209 是历史 scope 总量，不是 active-only；仅 56 active invalid 为 apply 候选 | 2026-07-15 |
| LP-020 | frozen trusted backup + remediation apply | backup `pre-change-20260715-201745` trusted/complete schema_v2 SHA `081c43e0…` restore can_apply；apply expired_count=56；invalid_active 56→0；224 rows keep identity/content/scope；no delete/reassignment；quick_check=ok | migration ground truth frozen；apply safe | 2026-07-15 |
| LP-021 | bot-only deploy + runtime inspect | new container `4199a393…` image `01c68ae8…` StartedAt `2026-07-15T12:20:22.459948294Z` GIT_COMMIT exact `98887a548…` restart=0 OOM=false；banner/startup/OneBot/guard/Admin 200；social_narrative allowlist exact two；StoryArc archived no seed；NapCat container/image/StartedAt unchanged | production runtime matches implementation SHA；NapCat invariant holds | 2026-07-15 |
| LP-022 | 180s passive public-group observation UTC 12:25:18–12:28:18 | 92 log lines；46 message.group；46 silent_learn；public groups 805836168/860324414/477640404/953023811/963085812；authorized inbound 0；bot send/poke 0；errors none；no QQ probes | public silent groups remain zero outbound | 2026-07-15 |

## Next Session Starts Here

- Direction: **none** — Living Persona Part C + reliability 已部署并文档收口。
- First action: 见 `docs/tracking/ACTIVE.md` Pending（character pack / QZone / LLM flags）；不要重做本 tracker。
- Open questions: 无。
- Do not redo: 审计、reliability/Part C 实装、full pytest/review、migration apply、bot-only deploy、180s 观察。
- Do not: restart/recreate NapCat；把 post-start Dream valid 增长误读为 migration drift。

## Handoff

**Deployed and closed.**

- 代码 + 测试 + 静态检查 + independent review：完成。
- 备份 + freeze matrix count-gate 纠正 + remediation apply：完成。
- bot-only 部署 + Admin/health + 180s 公开 silent 零出站 + NapCat 不变：完成。
- 文档收口：ACTIVE idle；本 tracker complete；migration checklist deployed；maintenance-log 证据条目。
- 生产 runtime：`98887a548eb574f5ab0d068b1529ad06e53f88aa`。
- 回滚：`omubot-bot:pre-living-persona-98887a5-20260715` / 备份 `pre-change-20260715-201745`。
