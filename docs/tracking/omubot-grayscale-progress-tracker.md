# Omubot 灰度进度追踪表（人设 / 拟人）

> 编制：2026-05-27
> 性质：跨执行文档的索引视图，**不重复**各 part 的 wave 细节，仅汇总当前阶段 / 当前内容 / 阻塞
> 维护节奏：每次 ① 灰度档位变更 ② Wave 推进 ③ Phase 完成 时同步更新顶部时间戳与对应行
> 状态语义：✅ 完成并已验收 ／ 🟡 已落地待最终验收 ／ ⏳ 待执行 ／ ⏸ 阻塞中 ／ ❌ 证据未建立 ／ 🔥 生产故障中

---

## 0. 速览（最近 7 天关键事件）

| 时间 | 事件 | 关联文档 |
|---|---|---|
| 2026-05-27 | Persona parity audit 假阳性修复 + source.md 补 `bot_self_id_hint` / `admins`，6 axes 全 aligned（待 bot 镜像 rebuild 后由 connect-time shadow log 复核） | [importer §9](../migrations/persona-v2-importer.md) / [parity_audit.py](../../services/persona/parity_audit.py) |
| 2026-05-27 | Part 6 balanced profile 上线后单段死锁，审查 + 派单文档已落地，Phase 0 待用户授权回滚 | [bugfix-part1](omubot-humanization-part6-bugfix-part1.md) / [-execution](omubot-humanization-part6-bugfix-part1-execution.md) |
| 2026-05-27 | Persona v2 runtime_consume / shadow_compare 上线，灰度群双双吃 v2 | [B3-execution](persona-runtime-cutover-B3-execution.md) |
| 2026-05-26 | humanization profile=balanced 切换 + 灰度群扩到 2 个 | config.json:382 |
| 2026-05-26 | Part 4 v1 → v2 → v2-修订版 三轮迭代收敛 | [part4](omubot-humanization-part4-memory-relationship.md) |

---

## 1. 当前生产灰度配置快照（runtime source: `config/config.json`）

> ⚠️ `config/config.toml` 已废为过期文档，运行时只读 JSON（见 [kernel/config.py:1232](../../kernel/config.py#L1232) `_resolve_config_file`）。修改灰度旗标必走 JSON，否则不生效。

### 1.1 humanization 段（[config/config.json:374-399](../../config/config.json#L374-L399)）

| 字段 | 值 | 含义 / 备注 |
|---|---|---|
| `profile` | `"balanced"` | 🔥 当前生产中，触发单段死锁；待 Phase 0 回滚 `custom` |
| `runtime_groups` | `["993065015","984198159"]` | plan_then_utter 等子模块的额外白名单（balanced 对其它群也生效） |
| `context_providers` | `true` | Part 1 V0 ✅ |
| `register_classifier` | `true` | Part 1 V0 ✅ |
| `sticker_register_provider` | `true` | Part 1 V0 ✅ |
| `thinker_provider` | `true` | Part 1 V0 ✅ |
| `semantic_gate_dynamic` | `true` | Part 1 V0 ✅ |
| `kaomoji_enforce_strict` | `true` | Part 1 V0 ✅ |
| `rewrite_threshold` | `0.4` | Part 1 V0 ✅ |
| `rws_shadow` | `true` | Part 1 灰度-1 影子比对 🟡 |
| `pause_then_extend.enabled` | `true` | Part 6 Wave 4 🟡 |
| `streaming_segment.enabled` | `false` | Part 6 Wave 3（balanced 经 profile 转译为 True，但 [_streaming_segment_enabled](../../services/llm/client.py#L1758-L1771) 门禁强关）🔥 |
| `plan_then_utter.enabled` | `false` | Part 6 Wave 5 灰度白名单空 |
| `state_board.layout` | `"head"` | Part 6 Wave 0 默认 |
| `state_board.granularity` | `"fine"` | Part 6 Wave 0 默认 |

### 1.2 persona_v2 段（[config/config.json:405-411](../../config/config.json#L405-L411)）

| 字段 | 值 | 含义 / 备注 |
|---|---|---|
| `persona_id` | `"fengxiaomeng-v2"` | 凤笑梦 v2 freeze artifact |
| `runtime_consume` | `true` | B3 切流真启用 |
| `shadow_compare` | `true` | B2 影子对比真启用 |
| `runtime_groups` | `["993065015","984198159"]` | v2 切流的灰度群范围 |
| `fallback_on_compile_error` | `true` | compile 失败回 v1 静态块 |

---

## 2. 灰度群清单与启用能力

| 群号 | 别名 | persona | humanization | 备注 |
|---|---|---|---|---|
| `993065015` | 灰度群 1 | v2 切流（B3）+ shadow | balanced（受 §1.1 全部旗标影响）| 主灰度，6 条以上 v2 回复在线 |
| `984198159` | 灰度群 2 | v2 切流（B3）+ shadow | balanced（同上） | 第二灰度，扩量观察 |
| 其它群 | — | v1 静态块（runtime_groups 不包含） | balanced 仍生效（profile 不受 runtime_groups 限制） | 注意：拟人 profile **对所有群生效**，仅 plan_then_utter 等子模块受 runtime_groups 收口 |

> ⚠️ 易错点：`humanization.runtime_groups` 不是 humanization 整体白名单，仅是「需要群级白名单的子能力」（例如 plan_then_utter）才参考它。`profile` 字段直接对全局生效。

---

## 3. Persona（人设）模块灰度进行表

> 主线：source.md → import → freeze → runtime ⇒ B1 协议 / B2 影子 / B3 切流 / B4 多群灰度 / B5 全量 / B6 后台面板
> 详见：[persona-v2-importer.md](../migrations/persona-v2-importer.md) §12 已迁移项一览

| 阶段 | 状态 | 当前内容 | 阻塞 / 下一步 | 关联文档 |
|---|---|---|---|---|
| **B1** 协议 + 配置 + runtime entry skeleton | ✅ | `PersonaV2Config` BaseModel、`PersonaRuntimeBundle` 协议、`load_pending_freeze()` 入口已落地 | — | [persona-runtime-cutover-B1](persona-runtime-cutover-B1-execution.md) |
| **B2** 影子比对引擎 | ✅ | `ShadowCompareEngine.run_once()` + `ShadowDiffReport` + `ShadowCounter`，`/storage/persona_shadow_diff.log` 写盘正常 | parity 6 axes 全 `aligned`（2026-05-27 修复 substring anchor 假阳性 + 补 source.md front matter `bot_self_id_hint` / `admins` 之后；shadow log 验证由下次 connect 走过新镜像后落定） | [persona-runtime-cutover-B2](persona-runtime-cutover-B2-execution.md) |
| **B3** runtime selector + PromptBuilder 集成 | ✅ | `PersonaRuntimeSelector` + `_on_connect` 装配 + `bundle.ok=True / v2_text 10285 字节`，`runtime_consume=true` 生效中 | B3.4 用户最终人工验收 ⏳（生产已 live，待用户主动签收） | [persona-runtime-cutover-B3-execution](persona-runtime-cutover-B3-execution.md) |
| **B4** 多群灰度扩量 | 🟡 进行中 | runtime_groups 已扩到 2 个群（993065015 / 984198159），shadow 双群在线 | 观察窗口：≥ 7 天无 divergence 升级再推 B5 | — |
| **B5** 全量切流 | ⏳ | 计划：移除 runtime_groups 收口，全部群吃 v2 | 前置：B4 灰度无 regression + parity 6 axes 全 aligned（已达成 2026-05-27，待 bot 镜像 rebuild 后由下次 connect-time shadow log 复核）+ B4 观察窗口 ≥ 7 天 | — |
| **B6** 后台 SPA runtime 切档面板 | ⏳ | 计划：admin/frontend 提供 persona_v2 旗标可视化切档（取代手编 JSON） | 前置：B5 完成 + admin SPA 风格统一 | — |

**parity audit 现状**（[importer §9](../migrations/persona-v2-importer.md)，2026-05-27 修正）：6 axes 全 `aligned`。修正路径：

- `admins` / `bot_self_id`：source.md front matter 之前缺 `admins:` / `bot_self_id_hint:`；2026-05-27 已补，importer/freeze 重跑后 `adapter.yaml` 正确落到 prompt block。
- `identity_personality` / `behavior_instruction` / `proactive_rules`：之前 parity 用 `_first_line` 取 v1 第一行做 substring 锚点，碰到 markdown 标题（`# 1. 是谁` / `## 8.4 行为指令` / `## 插话方式`）会假阳性；2026-05-27 改为 `_meaningful_anchors`（跳过 markdown 标题与列表前缀，多取前 5 条非空业务行 any-match），新增 4 条回归（`tests/test_persona_parity_audit.py`）。
- 容器 fallback：bot 镜像里 `services/persona/parity_audit.py` 不是 bind mount，需要 `dot_clean . && docker compose up bot -d --build`；rebuild 完成前 shadow log 仍按旧首行锚点判，最新一行（2026-05-26T19:10Z `divergent_axes: identity_personality, behavior_instruction`）即为旧算法残影。

---

## 4. Humanization（拟人）模块灰度进行表

> 主线：6 个 part + 1 个 bugfix。Part 1 是基础设施 V 系列；Part 2-3 是输入感知 + 群上下文；Part 3.5 是概率调度修订；Part 4 是关系/记忆；Part 5 是分段；Part 6 是三档 profile + 源生侧；Bugfix Part 1 是 Part 6 上线后的紧急审查。

### 4.1 Part 1（语言质感 V0-V17 + 灰度）

| 段位 | 状态 | 当前内容 / 阻塞 | 关联 |
|---|---|---|---|
| P0.0 - P0.8 | ✅ | 全部 V 系列基础设施已落地 | [part1-execution](omubot-humanization-part1-execution.md) |
| U1 - U13 | ✅ | 所有 utterance 工具链已收敛 | 同上 |
| V0 - V17 | ✅ | context_providers / register_classifier 等 7 旗标已配置启用 | 同上 |
| 灰度-1 | 🟡 | rws_shadow=true 影子比对在线，待最终验收 | 同上 |
| 灰度-2 / 灰度-3 | ⏸ | 阻塞于 Part 6 bugfix（balanced 故障未结案前不推进） | — |

### 4.2 Part 2-3（输入感知 + 群上下文，多 wave 合并执行）

| 段位 | 状态 | 当前内容 / 阻塞 | 关联 |
|---|---|---|---|
| Wave 1-5 全部 | ✅ | P2.1/P3.1/P3.6/P3.4/P2.4/P3.2/P3.7/P2.2/P2.6/P2.8/P3.8/P3.9/P2.5/P3.3/P2.9/P2.10/P2.14/P2.11/P3.10/P2.12/P2.13 全 ✅ | [part2-3-execution](omubot-humanization-part2-3-execution.md) |
| Wave 6（P2.7+P3.5+v2-灰度）| 🟡 | 2026-05-26 03:04 CST 进入灰度，993065015 启全部 7 个已接线旗标；984198159 自动走 v1 对照（注：此处 v1/v2 指 humanization Wave 6 灰度版本，非 persona v1/v2） | 同上 |
| Wave 7（P2/3-DOC 收口） | ⏳ | 待 Wave 6 验收后归档 | 同上 |

### 4.3 Part 3.5（概率调度修订 P3.11-P3.18）

| 段位 | 状态 | 当前内容 | 关联 |
|---|---|---|---|
| P3.11.0 - P3.11.x | ✅ | 概率调度全链路重写 | [part3.5-execution](omubot-humanization-part3.5-execution.md) |
| P3.12 - P3.18 | ✅ | 验收闭环 | 同上 |

### 4.4 Part 4（关系 / 记忆）

| 段位 | 状态 | 当前内容 | 关联 |
|---|---|---|---|
| v1（已废） | ❌ | 2026-05-24 草案，过度耦合 | [part4](omubot-humanization-part4-memory-relationship.md) |
| v2（已废） | ❌ | 2026-05-26 重写，过度工程化 | 同上 |
| v2-修订版（当前设计） | ⏳ | 设计落地中，未进入实现 wave | 同上 |

### 4.5 Part 5（分段策略）

| 段位 | 状态 | 当前内容 / 阻塞 | 关联 |
|---|---|---|---|
| P5.0 - P5.4 | ✅ | 分段决策器与字数 / 标点 / 子句切分主路径已收敛（P5.4 用户授权代验收）| [part5-execution](omubot-humanization-part5-execution.md) |
| P5.5 | 🟡 ⚠️ | 代码已落地（default 翻 True + `_legacy_segment_path` 删除 + 1980 passed），但 2026-05-27 误判验收已撤回 — 与 Part 6 bugfix §1.2 故障强耦合（bugfix Phase 1A 改名 `disable_natural_split`→`streaming_already_emitted` 会重构 P5 fallback 路径），且 balanced 实测「单段死锁」证伪 P5 默认 True 在 profile 干预下真正分段 | 同上 |
| P5.6 | ⏳ | 阻塞于 bugfix Phase 1 全绿后 P5.5 重新判定 | 同上 |

### 4.6 Part 6（三档 profile + 源生侧）

| 段位 | 状态 | 当前内容 / 阻塞 | 关联 |
|---|---|---|---|
| P6.-1 | ✅ | 前置审查 | [part6-execution](omubot-humanization-part6-execution.md) |
| P6.0.a - P6.0.c | 🟡 | state_board layout/granularity 默认未翻（head/fine）| 同上 |
| P6.0.x1 - P6.0.x5 | 🟡 | profile 注入链路 | 同上 |
| P6.0.y1 - P6.0.y4 | 🟡 | QQInteractionsConfig 11 字段 ResolvedHumanization 已就位（5/27 重 build 镜像后） | 同上 |
| P6.1 - P6.13 | 🟡 | Wave 1-6 全部落地，pytest 45 全 pass | 同上 |
| 最终验收 | 🔥 阻塞 | balanced 上线后单段死锁，审查 + 派单已出 | [bugfix-part1](omubot-humanization-part6-bugfix-part1.md) |

### 4.7 Part 6 Bugfix Part 1（balanced 故障紧急审查）

| Phase | 状态 | 当前内容 | 阻塞 / 下一步 |
|---|---|---|---|
| 审查文档 | ✅ | 9 条 finding（A-J）+ 5 条契约缺陷 + 4 条必加回归 + 4 条选加 | — |
| 派单文档 | ✅ | Wave 0/1/2/3 + 5 个执行点 + 测试金字塔 | — |
| **Phase 0** 紧急回滚（config.json balanced→custom + restart） | ⏳ 等用户授权 | 30 秒 0 行代码，纯 config 切换 | 用户书面同意后立即执行 |
| **Phase 1A** 改名 disable_natural_split→streaming_already_emitted | ⏳ | 4 处 client.py 调用点 + 5 处 kernel/config.py setter 同步改名 | 前置：Phase 0 完成 |
| **Phase 1B** _should_force_reply 加 qq_interaction | ⏳ | scheduler.py:35-42 一行修改 | 同上 |
| **Phase 1C** _streaming_segment_enabled 门禁松绑（推荐选项 B：始终允许 streaming） | ⏳ 待选项确认 | 选 A 维护白名单 / 选 B 运行时降级 | 用户确认 A/B |
| 4 条 must-have 回归（T1-T4） | ⏳ | balanced 多段 / qq_interaction force / streaming fallback / quote kill-switch | Phase 1 同步落地 |
| Phase 2 选加 4 条（T5-T8） | ⏳ | T8 invariant 优先 | Phase 1 完成后 |
| Phase 3 余项收尾（C/F/G/E/J finding） | ⏳ | quote_reply CQ-strip / interaction_tools wire / health_guard turn 边界 / dead branch / pause_extend 时机 | Phase 1 完成后 |

---

## 5. 当前阻塞 & 下一步建议执行序

```
┌─ 5.1 紧急（用户授权后立即）
│   Phase 0 回滚 config.json:382 "balanced" → "custom" + docker compose restart bot
│
├─ 5.2 本日（Phase 0 完成后）
│   Phase 1A 改名 + T1 回归（balanced 长回复 ≥2 段）
│
├─ 5.3 本周
│   Phase 1B + Phase 1C(B) + T2/T3/T4 → 全绿后再切 balanced 二次灰度
│
├─ 5.4 灰度二次切回成功 → Part 6 最终验收解锁 → Part 1 灰度-2 / 灰度-3 解阻
│
├─ 5.5 并行（不阻塞 bugfix）
│   ├─ Persona B3.4 用户主动签收
│   ├─ Persona B4 观察窗口（≥ 7 天）
│   ├─ Humanization Part 5 P5.5 → P5.6 收口
│   ├─ Humanization Part 2-3 Wave 7 P2/3-DOC 收口
│   └─ Humanization Part 4 v2-修订版 进入实现 wave
│
└─ 5.6 中期
    ├─ Persona B5 全量切流 → B6 后台面板
    └─ Humanization Part 4 实装
```

---

## 6. 文档关系图（避免重复维护）

| 类别 | 文档 | 维护责任 |
|---|---|---|
| **本表** | [omubot-grayscale-progress-tracker.md](omubot-grayscale-progress-tracker.md) | 索引 / 速览 / 跨 part 状态 |
| **Persona 主线** | [persona-runtime-cutover-B1/B2/B3-execution.md](persona-runtime-cutover-B3-execution.md) | 每 B 段细节 wave |
| Persona 迁移 | [persona-v2-importer.md](../migrations/persona-v2-importer.md) | parity audit / 已迁移项 |
| **Humanization 各 part** | `omubot-humanization-part{1,2-3,3.5,4,5,6}-execution.md` | 每 part wave 细节 |
| Humanization bugfix | [omubot-humanization-part6-bugfix-part1.md](omubot-humanization-part6-bugfix-part1.md) + [-execution](omubot-humanization-part6-bugfix-part1-execution.md) | 故障审查 + 派单 |
| **运行台账** | [maintenance-log.md](../../maintenance-log.md) | 每次部署 / 配置 / 事件追加条目 |

**维护规则**：

- 本表只放「当前阶段 / 当前内容 / 阻塞」三栏，**不复制 wave 级细节**
- Wave 推进或档位变更，**先改本表顶部时间戳与对应行**，再写各执行文档细节
- 与各执行文档冲突时，以执行文档为准；本表过期时立即修正
- 新增 part 或新增灰度档时，需追加 §3 或 §4 对应行 + §1.1 / §1.2 旗标行

---

## 7. 自审记录（2026-05-27）

| 自审项 | 验证手段 | 结论 |
|---|---|---|
| config.json humanization 全字段 | Read [config/config.json:374-399](../../config/config.json#L374-L399) | 与 §1.1 表一致 |
| config.json persona_v2 全字段 | Read [config/config.json:405-411](../../config/config.json#L405-L411) | 与 §1.2 表一致；runtime_consume=true / runtime_groups 双群 confirmed |
| Part 6 bugfix Phase 0 仍未执行 | config.json:382 仍 `"balanced"` | 与 §4.7 / §5.1 一致 |
| persona-runtime-cutover-B3 §7 状态 | Read [B3-execution §7](persona-runtime-cutover-B3-execution.md) | B3.4 ⏳ 待手动验收，本表 §3 已对齐 |
| humanization-part2-3 P2.7+P3.5 灰度时间 | Read [part2-3-execution](omubot-humanization-part2-3-execution.md) | 2026-05-26 03:04 CST 启灰度，与 §4.2 一致 |
| Part 6 wave 状态 🟡 | Read [part6-execution §6](omubot-humanization-part6-execution.md) | 全 wave 🟡 待最终审查，与 §4.6 一致 |
