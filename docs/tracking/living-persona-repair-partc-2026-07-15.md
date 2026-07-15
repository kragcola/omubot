# Living Persona 全链修复与 Part C 实装

> 状态：active
> mode: task-bug
> 最后更新：2026-07-15
> 当前下一步：named-volume 备份 → 污染迁移 dry-run/apply → bot-only 部署（仅 build+recreate bot；禁止 touch NapCat）。**部署尚未执行。**
> 阻塞：无。实现 commit SHA 尚未落盘（commit 在本 packet 文档之后）；精确 SHA 记入 post-deploy docs checkpoint，禁止编造。
> 验证证据：代码/review/验证已完成 — 0 Critical / 0 open Important；full pytest 3686 passed / 17 skipped / 202 warnings / 0 failed in 63.78s；scoped Ruff clean；scoped Pyright 0；24 manifests valid；plugin layout clean；72/72 plugin JSON parse；`git diff --check` clean。
> 回滚入口：关 `social_narrative` / Living Persona flags；用变更前 bot image recreate bot；NapCat 不 restart/recreate。

## Resume Capsule

- objective: 按审计优先级修复 Living Persona 全链，随后实装 Part C 真人 Social Narrative 并部署。
- next_step: named-volume 备份 → 污染迁移 dry-run/apply（209 invalid Dream scopes → expired；15 global/global 保留；过期 StoryArc 归档）→ bot-only 部署（build+recreate bot only；永不 touch NapCat）。
- current_files: `plugins/dream/`、`plugins/schedule/`、`plugins/social_narrative/`、`services/social_narrative/`、`services/dialogue_climate/`、`services/persona/`、`bootstrap/chat_runtime.py`、backup/health、`tools/living_persona_remediation.py`、本 packet 四份文档。
- last_verified: 代码侧 static + full pytest + independent review 已通过（见 Verification）；**部署/运行态容器检查本 packet 未跑**。
- do_not_redo: 不再重复审计结论；不再重做 reliability / Part C 实装与验证；不碰角色包、课程、NapCat 数据。
- rollback: 先关 `social_narrative.enabled` 与相关 Living Persona flags；bot 回滚到 pre-change image；数据保留只停消费；NapCat 不动。
- implementation_sha: **TBD** — 精确实现 commit SHA 在 post-deploy docs checkpoint 记录，本 tracker 不编造。

## Section Progress

| Section | Status | Evidence / Note | Next Update |
| --- | --- | --- | --- |
| Context | done | 生产、SQLite、代码、测试、文档全链审计完成 | 无 |
| Plan | done | 修复顺序、Part C 存储与证据契约已冻结 | 无 |
| Reliability fixes | done | Dream 同日幂等、StoryArc 生命周期、Climate post-reply、persona fail-fast、affection/climate 接线、backup/catalog、health 权限可观测 | 部署后观察 |
| Part C | done | `plugins/social_narrative` v0.1.0 + `services/social_narrative`；独立表；fail-closed 默认；代码+测试完成 | 测试群启用 |
| Verification | done（代码侧） | full pytest / Ruff / Pyright / manifest / review 全绿；部署/runtime 未跑 | 部署 packet |
| Handoff | partial | 本 packet：tracker + ACTIVE + migration 清单；commit+deploy 在后续 packet | 部署后收口 |

## Scope And Acceptance

1. Dream 同一自然日幂等；卡片与 Arc 更新可重试且不半写；reflection group/scope 只能使用真实 group ID 或 `global/global`。
2. StoryArc 支持未开始/active/terminal/expired 生命周期、归档、并发更新保护和干净存储启动策略。
3. Climate post-reply 经真实 manifest + PluginBus 派发；多人 mood 不错归；M2/M3/M4 合法组合不吞旧信号；snapshot 按 session+user 隔离。
4. Persona 初次加载失败必须中止；hot reload 失败继续持有旧 bundle。
5. Affection/Climate/Willingness/旧 Coupling 收敛为有明确 producer/consumer 的状态；Health/Admin 能揭示权限断链和 Living Persona 运行状态。
6. StoryArc/partner/social factual 状态进入 daily/migration/pre-change 备份。
7. Part C 真人只允许 `factual`：来源为当前群可追溯共同经历或既有 factual memory；禁止虚构线下行为，禁止私聊进群，禁止 fiction/factual 混写。
8. Part C 至少接入：事实采集/派生状态、日程/反思叙事输入、普通对话上下文、Admin/health 可观测与全局开关；默认关，部署后仅测试开发群启用。
9. 上线前完成 focused + full pytest、ruff、pyright、JSON/schema、备份清单；**容器运行态与公开 silent 群零出站负向窗口留部署 packet**。

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

## Production Pollution Dry-run / Migration Plan

- 只读容器卷检查（审计时）：`memory_cards.db` `PRAGMA quick_check=ok`，共有 `dream_reflection=224`。
- 新 scope 契约下仅 `global/global=15` 合法；其余 `209` 张均为模型生成的伪 scope（如 `global/self`、`group/wxs`、地点名或剧情组名）。
- 224 张历史卡全部缺 `source_msg_id/captured_at`，无法证明应归属哪个真实群，禁止自动改写为测试群 ID。
- **迁移策略（部署前执行，尚未 apply）**：
  1. pre-change **named-volume 备份**（必须先做）。
  2. 用 `tools/living_persona_remediation.py` dry-run 再 apply：209 张 invalid Dream scopes → `expired`，保留原内容/ID 作审计，**不物理删除、不伪造归属**。
  3. 15 张 `global/global` **保留**。
  4. 过期 StoryArc（如 `stage_play_competition_week`）按新生命周期 **归档**，禁止继续推进。
- 部署后：仅在测试群启用 `social_narrative`；公开/silent 群保持 fail-closed。
- **回滚**：关 flag + 切回 pre-change bot image；NapCat 全程不动。数据侧 expired 标记可审计保留，不要求物理还原。

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

### 待完成（部署 packet）

- [ ] 生产 named-volume 备份
- [ ] 污染迁移 dry-run → apply（209 expired；15 global/global keep；StoryArc archive）
- [ ] bot-only 部署（`build` + `up -d --no-deps --force-recreate bot`；**永不 touch NapCat**）
- [ ] 部署后观察（startup/OneBot、silent 群零出站、测试群按需启用 social_narrative）
- [ ] maintenance-log 证据条目
- [ ] 在 post-deploy docs checkpoint 记录精确 implementation commit SHA（当前 **TBD，勿编造**）
- [ ] 本 packet 后的 implementation commit（pre-commit HEAD base：`0e6827762aaaa28336d0e8c6481993452dfd7565`）

## Decisions

| Decision | Choice | Why | Date |
| --- | --- | --- | --- |
| `PC` 解释 | Living Persona Part C | 上下文唯一对应未实施项 | 2026-07-15 |
| Part C 形态 | 单 bot 世界模型，不做多 bot society | 延续 Part C 已冻结形态 | 2026-07-15 |
| 真人事实边界 | 当前群证据 + 主观印象，禁止线下脑补 | Part C 红线与可验证性 | 2026-07-15 |
| 上线范围 | 仅现有 active 测试群，公开群继续全局零出站 | 用户既有上线裁定 | 2026-07-15 |
| Part C 存储 | `memory_cards.db` 独立 factual tables | 保留统一备份，同时避免 CardStore 单 scope 的跨群/跨用户暴露 | 2026-07-15 |
| Dream 污染迁移 | 209 张 invalid scope 标 `expired`，不删除/不改归属 | 无 evidence 时不能推定真实群 | 2026-07-15 |
| 默认 fail-closed | `social_narrative.enabled=false`，`allowed_group_ids=[]` | 部署前零行为变更；仅测试群显式放开 | 2026-07-15 |
| 部署延后 | 代码/review/验证完成；deploy 与 maintenance-log 证据另开 packet | 本 packet 仅文档与后续 commit 准备 | 2026-07-15 |
| 实现 SHA 延后 | 精确 implementation commit SHA 记入 post-deploy docs checkpoint | commit 尚未发生；禁止编造 SHA | 2026-07-15 |
| 生产 runtime 不变 | 仍为 `d51a7d41bed5b031659e09dcfd148c10e6cd4e0a` | 本任务未部署 | 2026-07-15 |

## Files Touched

| File / Category | Change | Status |
| --- | --- | --- |
| `plugins/dream/`、`plugins/schedule/` | Dream 幂等、StoryArc 生命周期、Climate post-reply 接线 | code done |
| `services/dialogue_climate/`、`services/persona/`、affection/willingness 相关 | post-reply、snapshot 隔离、fail-fast、producer-consumer | code done |
| `plugins/social_narrative/`、`services/social_narrative/` | Part C v0.1.0；独立表；factual-only | code done |
| `bootstrap/chat_runtime.py` 等接线 | persona fail-fast、social_narrative 装配 | code done |
| backup/catalog、health | social_narrative client；权限拒绝可观测 | code done |
| `tools/living_persona_remediation.py` | 污染迁移 dry-run/apply | code done |
| tests：`test_social_narrative*`、`test_living_persona_remediation*`、`test_chat_runtime_persona_failfast*`、`test_schedule_plugin_*` 等 | RED→GREEN + cancel-path | code done |
| `docs/tracking/living-persona-repair-partc-2026-07-15.md` | 本任务追踪器 | active |
| `docs/tracking/ACTIVE.md` | 指向本任务 / next_step 更新 | active |
| `docs/migrations/living-persona-partc-2026-07-15.md` | 旧→新迁移清单（本 packet 新建） | code complete / not deployed |
| `docs/wiki/Plugins.md` | 本地包 24；runtime 22；用户 20；登记 `social_narrative` 0.1.0 | docs done |

## Verification

| Check | Command / Evidence | Result |
| --- | --- | --- |
| Pre-audit focused tests | Dream/Climate/StoryArc/PluginBus/关系相关 suites | 186 passed（审计期） |
| Production DB health | read-only `PRAGMA quick_check` | 4 DB ok（审计期） |
| Runtime identity | container PersonaRuntime load | 凤笑梦 / 4273 chars / 6 blocks（审计期；生产仍 d51a7d4） |
| Full pytest | `uv run pytest` | **3686 passed / 17 skipped / 202 warnings / 0 failed in 63.78s** |
| Scoped Ruff | `uv run ruff check`（改动范围） | clean |
| Scoped Pyright | `uv run pyright`（改动范围） | 0 errors |
| Plugin manifests | 24 manifests 校验 | valid |
| Plugin layout | layout 检查 | clean |
| Plugin JSON parse | 72/72 plugin JSON | parse ok |
| Diff hygiene | `git diff --check` | clean |
| Independent review | Living Persona / Part C | **0 Critical / 0 open Important** |
| Deployment / runtime container | build+recreate / silent 群窗 / NapCat 未变 | **本 packet 未跑** |

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
| LP-011 | `tests/test_living_persona_remediation*.py` dry-run/apply | GREEN：209 invalid → expired 路径；global/global 保留；不伪造归属 | 迁移工具可上线前 dry-run | 2026-07-15 |
| LP-012 | backup/catalog + health permission denial suites | GREEN：social_narrative client 入 catalog；permission denial 可观测 | backup/health 接线验收 | 2026-07-15 |
| LP-013 | same-pattern scan（Dream cancel、Climate reply perm、persona fail-fast、backup clients、evidence 边界、private 排除） | 相关位点已对齐或有覆盖测试 | D1 同模式扫描通过 | 2026-07-15 |
| LP-014 | plugin manifest/layout/JSON：24 manifests + layout clean + 72/72 JSON parse | all valid / clean | 插件包装交付干净 | 2026-07-15 |
| LP-015 | scoped Ruff + scoped Pyright on changed paths | Ruff clean；Pyright 0 | static 干净 | 2026-07-15 |
| LP-016 | full `uv run pytest` | **3686 passed / 17 skipped / 202 warnings / 0 failed in 63.78s** | 全量回归通过 | 2026-07-15 |
| LP-017 | independent review | **0 Critical / 0 open Important** | 实现可进入部署 packet | 2026-07-15 |
| LP-018 | `git diff --check` | clean | 无 whitespace 脏 diff | 2026-07-15 |

## Next Session Starts Here

- Direction: **部署 packet** — 代码/review/验证已完成，尚未部署。
- First action: 生产 **named-volume 备份** → `living_persona_remediation` dry-run → apply → bot-only deploy。
- Open questions: 无产品阻塞；实现 commit SHA 在 commit 后、post-deploy docs checkpoint 写入。
- Do not redo: 审计、reliability/Part C 实装、full pytest/review。
- Do not: restart/recreate NapCat；编造 implementation SHA；宣称部署或 production backup 已完成。

## Handoff

**Ready for deployment packet；not deployed.**

- 代码 + 测试 + 静态检查 + independent review：完成。
- 本 packet：tracker / ACTIVE / migration 清单 / `docs/wiki/Plugins.md` 文档更新 + implementation commit。
- 后续：named-volume backup → migration apply → bot-only deploy → 观察 → maintenance-log + post-deploy SHA 记录。
- 生产 runtime 仍为 `d51a7d41bed5b031659e09dcfd148c10e6cd4e0a`。
