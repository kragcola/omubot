# Active Omubot Work

> This is the compaction/new-session recovery entrypoint. Keep it short.

## Current

- mode: none
- tracker: none
- objective: none
- status: complete
- checkpoint: 插件两轮 33 项完成矩阵、三轮 closure review、D1 同根项及运行期 Style tick 预算缺口全部关闭；最终 full 3372 passed / 17 skipped / 161 warnings，Ruff/Pyright/typed/manifests/layout/frontend 与最终运行固定窗全绿。
- completed_at: 2026-07-15 CST
- next_step: none
- last_completed: `docs/tracking/existing-plugin-remediation-completion-audit-2026-07-14.md`
- implementation_commit: `715445a`（本地 `main`，尚未 push）
- deployment: bot image `e31c2a630cd...`（tag `omubot-bot:plugin-closure-style-tick-final-20260715`）/ container `41a5346c3278...` / restart=0 / OOM=false。
- rollback: 精确上一版 `omubot-bot:pre-style-tick-fix-20260715`=`671078e6bf6c...`；只允许 bot-only recreate；NapCat `19f6cf...` 不得 restart/recreate/down。

## Recovery Order

1. Read `.workspace/agent-session-state.md` if present.
2. Read this file.
3. If `tracker` is not `none`, read the tracker above.
4. Run `git status --short`.
5. Continue from `next_step`.

## Notes

- 全局 OneBot 群出站守卫已部署：最终 `GroupConfig.allows_active_group()` fail-closed，覆盖插件直发、生日 tick、管理员工具、scheduler，以及所有经已包装 OneBot `bot.call_api` 的发送路径；2026-07-14 固定窗已验证公开受限群只收不发。
- Worktree retains character-pack tracker notes, conflicting deep-delivery skill drafts, local coursework/tool outputs, NapCat data and temp artifacts; never use `git add -A`.
- Pytest baseline on this host uses `PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-}`.

## Pending (authoritative; not the Current task)

- **进阶话题块 Phase 2（未启动）**：Phase 1 raw research event layer 已部署且当前 capture health 为 healthy；新容器本轮 metrics 为 0/0/0。Phase 2 仍需实现 versioned `topic_assignment`、stable `block_uuid`、assignment evidence 与 `utterance_membership`，不得改写 raw event。
- **Character pack gap filling（active）**：sidecar healthy，4 packs / 136 characters；剩 BangDream 10 个 `chibi`、`lily:expression`、`haru:chibi`。无技术阻塞，主要约束是可信单角色来源不足。
- **Dialogue Climate 后续增量（实现已部署并 active）**：A-M2/M3/M4 已分别提交于 `30f0f23` / `f547344` / `f5ac299`，当前 schedule effective config 的 `m1/m2/m3/m4` 四 flag 均为 true。仍未做 provider-bus 让位、affection+climate 单 block 合并、Humanizer/Thinker adapter、MessageSensor classifier 运行馈入、baseline durable persistence、M1 tension 完全退役。
- **QZone Journal（仅立项）**：可行性已实证，仓库只有 charter，无 `plugins/qzone_journal` 实现。基础版与进阶版均未启动。
- **关闭中的两项 LLM 行为开关**：`schedule_overshare.enabled=false`（正则误伤需重做边界）与 `addressee_hint.enabled=false`（缺置信阈值/歧义门）。
- **低优先级已知瑕疵**：persona drift 对 `我是凤笑梦呀` 清理后可能残留 `呀`，主路径影响低。

## Historical Pending Snapshot (superseded; do not use for status)

- **进阶话题块 Phase 2（未启动）**：Phase 1 首批生产 raw rows 与运行期 metrics 已闭环，中期架构 M3-M6 也已收口；仍等待用户显式启动，不改写 raw event。

- **Character pack gap filling / 人物角色识别训练包缺口补齐**：原 tracker `docs/tracking/character-pack-batch-fill-2026-06-06.md` 的状态信息保留；当前剩 BangDream 10 个 `chibi`、日V `lily:expression` 与 `haru:chibi`，等待用户在它与话题块 Phase 2 之间选择优先级。

- **Dialogue Climate M3/M4 全量已落地（2026-06-16，休眠默认关，待部署）**：承接 A-M2。M3（commit `f547344`）= ClimateEngine 升 per-(group,user) + clear_stale + `sensors.py`(6 sensor) + `m2_metrics.py`(ClimateMetricsRecorder→m2_climate.db) + 运行态接线 + on_post_reply 反馈。M4（本轮 commit）= `policy.py` ClimatePolicy 纯函数 + schedule 注入"对话气候"block + 让位 M1 tension block。三 flag `m2_enabled`/`m3_sensors_enabled`/`m4_policy_enabled` 默认关，零行为变更。全量 2743 passed。偏离 plan 处（provider-bus 让位/adapter 消费/MessageSensor 馈入/block 合并/tension M1 退役）如实记录在 [Part A §10.2/§10.4](living-persona-partA-dialogue-climate.md)，留增量。上线需 shadow→active。**M4 commit 待提交、未部署**。

- **A-M2 全维 ClimateState 引擎已落地（2026-06-16，实现侧，休眠默认关，待部署）**：`services/dialogue_climate/state.py`（ClimateState 6 维 + ClimateSignal）+ `dynamics.py`（ClimateDynamics on-read 闭式 + ClimateEngine，按 R5 不用 tick/不做动量项）+ 4 配置文件加 `dialogue_climate.m2_enabled`（默认关）+ `tests/test_climate_dynamics.py` 15 例。**休眠态**：无 reply-path 消费者（grep 仅命中 plugin.py 配置字段），flag 关零行为变更。调参用公开实证锚定（Verduyn 2015 情绪时长 + emotional inertia AR(1) ESM），因 M1 live 校准样本仍空（6-14 采集口修复后 0 条自然 tension 事件）。验证：ruff/pyright 0、test_climate_dynamics 15 passed、-k schedule 186 passed 无回归。未做 M3/M4（sensor/policy/adapter/持久化/接 prompt）。详见 [Part A §9](living-persona-partA-dialogue-climate.md)。**未提交、未部署**（提交待用户确认）。

- **空间日志插件（qzone_journal）已立项（2026-06-16，仅文档）**：`docs/tracking/qzone-journal-plugin-charter-2026-06-16.md`。基础版发 bot 一天「值得发的事」到 QQ 空间，进阶版联动 story_arc + 群友共造故事。QZone 发布可行性已实证（NapCat 4.15.0 无 OneBot 发说说 action，但 `get_cookies(user.qzone.qq.com)` 能拿 p_skey + `get_csrf_token` ok → 走 g_tk + emotion_cgi 经典路径，纯 HTTP 不碰 NapCat 重启）。用户裁定：事件触发选材、真人化名+不虚构（严守 Part C）、**living 系列先行（下一步做 A-M2）**、本轮不写代码。进阶版依赖 Part C 主体（搁置待调研），后置。

- **运行态灰度全开观察（2026-06-14，已部署，开发阶段盯日志）**：取消 arbiter 灰度（`runtime_groups []`）+ 开 4 项（slang_lookup / memory.semantic[ngram] / persona_drift / anchor_reinjection）+ coalesce 开并收窗 5→2.5s；保持关 2 项（schedule_overshare 正则误伤、addressee_hint 缺阈值，须改代码非改 flag）。爆炸半径仅 2 个 active 测试群（993065015/984198159），8 个 silent_learn 大群上游 return 不受影响。详见 maintenance-log 2026-06-14 顶部「取消 arbiter 灰度 + E/F 簇分批启用」。config 备份 `config.json.bak-20260614-150723`。**观察项**：① ✅ `persona_drift` 误伤/漏拦审计**已完成 + 已修复（2026-06-14，代码已改，全量 pytest 2689 passed）**：静态喂样本（bot_name=凤笑梦 runtime 真值）测出三根因，均已修——(a) 误伤自我介绍：现按「自报真名算 drift」策略剥名（用户裁定），`我叫凤笑梦，很高兴认识你～`→`很高兴认识你～`；(b) **漏拦（最高优先级安全缺陷）已修**：纯 AI 声明 `我是一个AI` / 设定泄露 `我的人设是温柔` 剥空时改为返回空串→`passed=False`→上层 `_guardrail_fallback`「我重新整理一下再接」，不再原样透传；(c) **剥错方向已修**：`我是凤笑梦，我是一个AI` 改写后残留若仍含硬声明则丢弃并 fail-closed（不再留 AI 句）。区分「硬声明」（AI/型号/人设/真名/WxS，可 fail-closed）与「软匹配」（泛化 `我是X`，剥空仍透传，不误伤 `我是个吃货`/`我是来帮忙的`）。**附带根治一个预先存在的 import-顺序 bug**：guardrail 规则注册顺序原依赖模块 import 到达顺序（循环 import），特定顺序下 schedule_overshare 抢在 persona_drift 前把含「真名+时间」的整句剥掉、饿死 persona_drift（`test_persona_drift_stripper + test_drift_overshare_e2e` 组合跑暴露，纯 baseline 全量 suite 因 collection 顺序恰好不触发）。改为给规则加显式 `order`（RULE_ORDER_*），`apply` 时稳定排序，彻底脱离 import 时序。改动：services/llm/sentinel_registry.py（order 机制）+ persona_drift_stripper.py（三缺陷修复）+ dedup/schedule_overshare/thinker_phrase（传 order）+ 9 新测试（6 fail-closed + 3 ordering）。**残留次要瑕疵（未扩大范围）**：`我是凤笑梦呀` 剥名后留 `呀` 残渣（既有 `_rewrite_sentence` 标点尾问题，线上自我介绍走 light_reply greeting 短路不过 guardrail，影响极低）。详见 `docs/tracking/persona-drift-false-positive-audit-2026-06-14.md`；② ✅ `anchor_reinject_count` **已可观测并捕获**（2026-06-14 补埋点：原 anchor 注入/commit 路径无任何 metric/日志，现两个 commit 点[streaming+非 streaming]接 `_record_runtime_metric` 写 `runtime_metric_events`；压测拿到 2 条 `anchor_turn=7` 样本，group=984198159，正是 `max_turns_without_anchor=7` 首注入；首注后需再积 5 轮+boundary 才二注，符合设计）；③ ✅ coalesce 有数据（984198159 实测 enqueued/flushed/bypassed≈1:1，收窗 2.5s 起作用，无全 bypass/全卡顿极端）；④ ✅ `arbiter_b_abort` 首个正向打断样本**已捕获**（2026-06-14 多账号测试矩阵压测：同号同 block 生成中 @ 强打断「别讲故事了/改讲笑话」→ `arbiter_b_abort action=revise` → remerge → `arbiter_a_fire pending=2 confidence=0.90` → emu 输出从长故事改为笑话，群内可见。dc0c0f3 修复后首次端到端验证 B 正向路径，详见测试矩阵 runbook）；⑤ ✅ slang_lookup **已闭环**（补埋点 `slang_lookup_resolved`[带 source 分布]/`slang_lookup_unresolved`；压测拿到 resolved amount=2 `sources={local_db:2}`，emu 回复真用上库释义「搬史=搬低质量内容，史=屎谐音」；**正向发现**：LLM 实测会主动调 `slang_lookup` 工具[搬史/薯片侠]，原"prompt 未引导"疑问可闭环；注意全库仅 17 个 global term，多数 scope=group 绑生产群对测试群不可见）。**埋点改动**：services/llm/client.py 新增 `_record_runtime_metric`(复用 `_budget_manager._store`) + anchor/slang 三处调用；services/block_trace/store.py 登记 3 新 key 进 `_RUNTIME_METRIC_KEYS`；全量 pytest 2680 passed。已 rebuild bot 部署，debug 通道压测后已复原 false。**回滚**：恢复备份后 `docker compose restart bot`，不碰 NapCat。

- **表情包配图 · 心情二轴改造**：**已实施 2026-06-10**（decision_provider + client.py + 3 测试，全量 pytest 2588 passed，待下次部署 rebuild 随车）。方案 + 落地差异：`docs/tracking/sticker-mood-two-axis-plan-2026-06-09.md`；维护日志见 maintenance-log 2026-06-10 顶部条目。
  - 落地要点：删 `_blocked_by_mood`/英文 mood 集合死代码 → 二轴乘子（energy 缩放概率地板 0.5、valence 偏置选图 query）；thinker:false 改 ×0.6 降权非否决（D1=a）；affection_stage 真实接线（B3）；base_frequency 接 `GroupStickerMode`（off→熄火）；D1 同模式顺修 `_should_force_kaomoji_sticker_round` 同源死代码。
  - F1 已满足：`config/config.json` 的 `sticker_placement.enabled` 已为 true，部署即生效。回滚：config 关该开关秒级熄火，或 `git checkout` 两源文件。
  - NapCat 不得 recreate（D6）。

- **话题块系统缺陷审计 + 重构评审 + 搜索审计**（待立项修复，**仅审计/评审未动代码**）：复杂多话题多人物极端模拟，发现 7 项缺陷（3 高 3 中/低）。报告 `docs/tracking/topic-block-multitopic-defects-audit-2026-06-11.md`（§1-5 缺陷，§6 用户"注册/合并/降级"提案评审，§7 学术搜索审计，§8 风险B再审）。根因：B 系列把"并发话题"建模成"参与者无序 set 并集"+无 message_id→block 反查+deque 插入序淘汰。**搜索审计关键转向**：该问题=NLP 成熟领域 conversation disentanglement（Kummerfeld 2019 IRC 语料），标准解=维护"消息→前驱边"、块=连通分量、单消息 attribute 为基本操作（**§6.3 澄清：用户"引用归入被引用块"=attribute 单条消息，正确，非 merge 块**）。**§8 风险B再审**：用户论点"引用回复复活低活跃块、绕开相似度门"结构正确（WeChat Q&R sequence-jumping 文献支持跨时复活），把风险B 收窄到"无引用接话"子集（保守~半数仍需相似度兜底）。**三条护栏**：①L2 不可"只审高活跃块"省算力（伤无引用复活）；②引用是强先验非硬真值（防 broadcast/reframe 误并）；③低活跃块衰减≠物理删除（否则 message_id 反查失败）。推荐路径 L0 边模型→L1 线性打分→L2 活跃度衰减（守护栏①）→L3 嵌入（可选）。缺陷6+B2 角色门 omubot 特有、文献不覆盖。**本轮未写任何修复代码。**
