# Omubot 表达学习与动态风格档案改造跟踪

本文用于长期跟踪 Omubot "学习真实表述方式" 的设计、实现、验收与风险。目标是让 bot 在不破坏凤笑梦核心人设的前提下，逐步学习群聊里的自然接话方式、表达节奏和场景化语言习惯。

## 当前状态

- 状态：Phase 0-6 初版已完成；等待人工端到端验收后再考虑 Phase 7
- 当前阶段：Phase 6 人工端到端验收卡点
- 核心目标：新增独立的表达学习层，不把风格学习塞进黑话模块
- 默认路线：激进自演化，但写入可回滚的动态风格档案，不直接修改 `config/soul/*.md`
- 学习来源：人类群聊表达 + 经过质量信号筛选的 bot 回复
- 暂不改动：NapCat、黑话词义治理、发送队列、核心 soul 文件自动写入、模型微调

## 与 ConversationArchive 的关系

表达学习手动抽取已优先使用 `ConversationArchive` cursor，不新增自己的历史消息表：

- `style_manual_extract` 作为独立 scanner 记录 `conversation_scan_cursors`，用 `message_pk` 范围增量扫描。
- 如果 MessageLog 对象没有 archive 能力、archive 读取失败或 cursor 需要人工重扫，会自动退回旧 `query_recent()` 最近窗口。
- 表达 evidence 写入或可重建 `conversation_message_refs`，防止未来清理原始消息时丢失审计来源。
- 默认群隔离和 global 表达池语义保持在 `StyleStore`，`ConversationArchive` 只提供 source chat 的消息与游标。
- 动态风格档案仍由 `StyleStore` 管理，不写入归档底座，不自动改 core soul。

详细方案见 `docs/conversation-archive-implementation-tracker.md`。

## 参考资料

| 类型 | 名称 | 启发 |
| --- | --- | --- |
| 本地项目 | MaiBot `/private/tmp/maibot-analysis.uoedgQ` | 将 expression learner 与 jargon miner 分离，表达项使用 `situation + style + count` |
| 论文 | Generative Agents | observation -> reflection -> retrieval，适合周期性表达反思 |
| 论文 | MemGPT / Letta | 使用可更新 memory block 承载动态人格/记忆 |
| 论文 | Reflexion | 将成功/失败经验写成语言反思，不训练模型也能改进行为 |
| 论文 | Character-LLM | 长期可参考角色经历重建，但当前不走训练路线 |
| 成熟项目 | LangMem | 将表达习惯视为 procedural memory，可从反馈优化 prompt |
| 成熟项目 | SillyTavern | example dialogue 对角色口吻很关键，但要控制数量避免复读 |

参考链接：

- Generative Agents: https://arxiv.org/abs/2304.03442
- MemGPT: https://arxiv.org/abs/2310.08560
- Reflexion: https://arxiv.org/abs/2303.11366
- Character-LLM: https://arxiv.org/abs/2310.10158
- Letta memory blocks: https://docs.letta.com/guides/core-concepts/memory/memory-blocks/
- LangMem: https://langchain-ai.github.io/langmem/concepts/conceptual_guide/
- SillyTavern character design: https://docs.sillytavern.app/usage/core-concepts/characterdesign/

## 核心设计决策

| 日期 | 决策 | 原因 |
| --- | --- | --- |
| 2026-05-12 | 表达学习独立于黑话模块 | 黑话负责词义，表达学习负责说法和节奏，混在一起会污染职责 |
| 2026-05-12 | 核心 soul 文件不自动改 | `identity.md` / `instruction.md` 是人设宪法，不能被短期群聊噪声覆盖 |
| 2026-05-12 | 自演化写入动态风格档案 | 满足快速拟人化，同时保留禁用、回滚和审计能力 |
| 2026-05-12 | 默认按群隔离；全局表达开启后共享学习池 | 默认避免串群；开启全局表达的群视为一体，像黑话 global 一样共同学习和使用 |
| 2026-05-12 | 学习 bot 回复必须有质量信号 | 避免 bot 把自己的坏回复反复强化 |
| 2026-05-12 | Prompt 注入少量表达参考 | 目标是借鉴表达方式，不是复制群友原话 |
| 2026-05-12 | 表达学习不因语气风险拒学 | 骂人、阴阳怪气、过度幼态、客服腔等都是真实语言生态；学习层记录，输出层按人设和心情矫正 |

## 实施清单

| 阶段 | 项目 | 状态 | 验收 |
| --- | --- | --- | --- |
| Phase 0 | 追踪文档与边界归档 | 已完成 | 文档说明目标、来源、阶段、风险，不产生代码行为变化；黑话 wiki 明确职责边界 |
| Phase 1 | `StyleStore / ExpressionStore` 最小存储 | 已实现 | 可保存 `situation/style/scope/status/confidence/count/evidence/risk_tags/output_policy`；SQLite 使用 DELETE journal 便于人工审计 |
| Phase 1 | 表达样本 CRUD 与去重 | 已实现 | 同群相似表达累加 count；`scope=global` 时跨来源群合并为同一表达池 |
| Phase 1 | 基础 Admin/API 只读查看 | 已实现 | 能查看摘要、表达样本、证据和修订记录；API 不提供写入入口 |
| Phase 2 | 人类消息表达抽取 | 已实现 | `StyleExtractor` 可从近期群聊抽取 `当 X 时，可以 Y` 形式表达候选；仅手动 Admin 触发，不自动监听；低信号泛化候选会被入库前过滤 |
| Phase 2 | 黑话优先边界过滤 | 已实现 | 候选 situation/style 命中当前群黑话 term 或 alias 时不保存为表达习惯 |
| Phase 2 | 手动抽取结果可观测 | 已实现 | 手动抽取返回并展示每群 `scanned/extracted/saved`，0 候选群不会在 Web 上消失 |
| Phase 2 | 人设一致性标注器 | 已实现 | 不因骂人、阴阳怪气、过度幼态、客服腔等语气风险拒学；标注 `risk_tags/persona_fit/mood_fit/output_policy` |
| Phase 2 | 候选进入 pending/approved | 已实现 | 手动抽取默认进入 pending；显式 `auto_approve=true` 时高置信、非 observe_only 候选可 approved |
| Phase 3 | Prompt 注入表达习惯参考 | 已实现 | `StylePlugin.on_pre_prompt()` 每轮最多注入 1-5 条相关 approved 表达；不相关时不注入 |
| Phase 3 | 与黑话块并存 | 已实现 | 表达块 label 为 `表达习惯参考`，黑话块仍解释词义；二者职责分离 |
| Phase 3 | 注入上限与降级 | 已实现 | `max_items/max_chars/min_confidence` 可配置；`observe_only` 不注入，风险标签表达强制转译提示 |
| Phase 4 | bot 回复质量信号采集 | 已实现 | `StylePlugin.on_post_reply()` 记录 bot 回复弱信号为中性 feedback，不自动学习或改权重 |
| Phase 4 | 成功/失败反思记录 | 已实现 | Admin/API 可对表达标记 positive/negative，记录 feedback 和 revision，小幅调整置信度 |
| Phase 4 | 管理员反馈入口 | 已实现 | API 和 `/admin/style` 可通过、拒绝、静音、好/坏反馈 |
| Phase 5 | 动态风格档案生成 | 已实现 | 可从 approved 表达生成短风格档案，并按请求启用 |
| Phase 5 | revision 与回滚 | 已实现 | profile 有 version/status，可禁用、启用、回滚上一版；操作写 feedback 审计 |
| Phase 5 | 人设守门规则 | 已实现 | 档案 Prompt 明确不得改变核心人设；风险表达在档案中转成“理解但不照搬” |
| Phase 6 | Admin 表达学习控制台 | 已实现 | 新增 `/admin/style`，展示样本、档案、反馈，支持手动抽取、生成档案、审核、启用旧版、回滚与禁用 |
| Phase 6 | 指标与观测日志 | 已实现 | summary 暴露 feedback/profile 指标；feedback 表记录人工和弱信号 |
| Phase 7 | 长期优化与可选训练集导出 | 暂缓 | 积累足够干净样本后，可导出 few-shot/评测集；暂不微调 |

## 各阶段详细方案

### Phase 0：文档与边界

建立 `docs/style-learning-implementation-tracker.md`。同时在相关 wiki 中说明：表达学习不是黑话，不是记忆卡片，不是 soul 自动改写器。

验收标准：文档能回答 "为什么做、做什么、不做什么、怎么验收"。

### Phase 1：表达存储层

新增独立服务，建议命名为 `services/style/` 或 `services/expression/`。第一版使用 SQLite，表结构只服务表达学习：

- 表达样本：场景、表达方式、作用域、状态、置信度、计数、来源、最后活跃时间。
- 证据表：来源消息、speaker、上下文片段、bot/user 来源类型。
- revision 表：人工审核、自动反思、档案生成都留痕。
- 风险与输出策略字段：`risk_tags`、`persona_fit`、`mood_fit`、`output_policy`，用于区分 "学到了" 和 "能不能原样说"。

作用域语义：

- 默认 `scope=group`，按 `group_id` 隔离学习、去重和注入。
- 开启全局表达后，相关群写入 `scope=global`，存储层用同一 global 表达池去重和计数；证据表继续记录真实来源群。
- 后续运行时设置需要提供和黑话类似的全局表达开关/排除群能力：未开启全局表达的群只看本群表达，已开启的群可参与并使用 global 表达池。

不复用 `SlangStore`，避免词义和风格混账；不复用 `CardStore`，避免把表达习惯当事实记忆。

### Phase 2：表达抽取与审查

从近期群消息窗口中抽取表达候选，格式固定为：

```text
当 <使用情境> 时，可以 <表达习惯>
```

标注规则必须先于入库，但标注不是拒学。表达学习层要尽量记录真实语言生态；输出层再按凤笑梦人设、当前心情和场景做转译或压制。

- 具体人名、私事、一次性事件不作为可复用表达样本；可作为证据上下文保留。
- 用户让 bot 改人设的命令不转成风格规则；可标注为 `prompt_control` 风险样本。
- 骂人、阴阳怪气、过度幼态、客服腔、AI 模板腔等都可以学习，但必须打 `risk_tags`。
- 违反 `instruction.md` 输出禁区的表达也可以学习为负面/转译样本，但 `output_policy` 必须是 `transform` 或 `observe_only`，不能原样注入为可照用风格。
- 与凤笑梦人设不一致的表达进入 pending 或 approved-with-guard，运行时只提供 "理解/转译参考"，不要求模型模仿原话。

### Phase 3：运行时注入

新增 `StylePlugin.on_pre_prompt()`。构建动态 PromptBlock：

```text
【表达习惯参考】
以下只用于调整本轮说话方式，不要照抄，不要改变核心人设。
- 当大家轻松吐槽时，可以用短促附和 + 一点明亮反应。
- 当有人分享成果时，先表达开心，再说一个具体喜欢的点。
- 当群里使用尖锐吐槽或脏话时，只理解其情绪强度；输出时按凤笑梦当前心情改写成符合人设的表达。
```

只注入与当前上下文相关的少量表达。默认 3 条，最多 5 条。表达块排在记忆/黑话附近，但不能覆盖核心人设规则。

### Phase 4：反馈学习

通过 `on_post_reply` 采集 bot 回复效果。第一版不要求显式点赞，也可以用弱信号：

- 正向：群友继续自然接话、同一话题延续、没有纠正 bot。
- 负向：用户纠正、冷场、触发禁区、管理员标记不好。
- 中性：只记录，不强化。

bot 自己的回复只有在正向信号足够时才进入表达候选，避免坏口吻自我循环。

### Phase 5：动态风格档案

周期性生成 "当前群动态风格档案"，类似可更新 memory block：

```text
【当前群动态风格档案】
- 日常闲聊更适合短句、快反应，不要长篇解释。
- 轻松吐槽时可以接一点夸张感；如果样本带脏话或阴阳怪气，输出时转成凤笑梦式的明亮/调皮表达。
- 安慰场景保持凤笑梦的温度，降低兴奋度，少用口头禅。
```

档案自动启用，但必须：

- 保留 revision。
- 可回滚。
- 可禁用。
- 不写入 `identity.md` / `instruction.md`。
- 每条规则都能追溯到表达样本或反思依据。

### Phase 6：Admin 控制台

新增表达学习页面或在现有 memory/slang 邻近入口增加 "表达学习"。

页面包含：

- 总览：样本数、待审数、自动通过数、档案版本。
- 样本队列：待审、已通过、已拒绝、已静音。
- 风格档案：当前版本、历史版本、回滚按钮。
- 风险样本：人设冲突、疑似操控、负面语气。
- 手动动作：通过、拒绝、静音、置顶、降权、回滚。

### Phase 7：长期优化

当样本足够干净后，再考虑：

- 构造固定评测集，比较 "无表达学习 / 有表达学习" 的回复自然度。
- 导出 few-shot examples，辅助 prompt 或离线测试。
- 研究微调或偏好优化，但不作为近期目标。

## 风险跟踪

| 风险 | 等级 | 缓解 |
| --- | --- | --- |
| 学到群友坏口癖，凤笑梦人设变形 | 高 | 核心 soul 不自动改；风险表达照样学习但标注 `risk_tags`，输出时按人设和心情转译；档案可回滚 |
| bot 自己坏回复被反复强化 | 高 | bot 回复必须有正向信号才学习，负向反馈降权 |
| Prompt 被表达块挤爆 | 中 | 严格限制条数和字符；不相关不注入 |
| 不同群表达串味 | 中 | 默认按群隔离；只有开启全局表达的群进入 global 表达池，并保留来源群证据 |
| LLM 抽取成本增加 | 中 | 批量、低频、后台任务；热路径只做轻量记录 |
| 表达样本变成复读模板 | 中 | Prompt 明确 "不可照抄"，样本保存为抽象情境和风格；风险样本默认 `transform/observe_only` |
| 自动档案写坏 | 高 | revision、回滚、禁用、Admin 审计 |
| 和记忆/黑话职责重叠 | 中 | 文档和代码层明确：记忆=事实，黑话=词义，表达=说法 |

## 验证计划

当前阶段最小自动化验证：

```bash
source ./scripts/dev/env.sh
uv run pytest tests/test_style_store.py tests/test_style_extractor.py tests/test_style_api.py tests/test_style_plugin.py tests/test_admin_api.py -q
uv run ruff check services/style plugins/style admin/routes/api/style.py admin/routes/api/__init__.py tests/test_style_store.py tests/test_style_extractor.py tests/test_style_api.py tests/test_style_plugin.py
```

后续阶段预期验证：

```bash
source ./scripts/dev/env.sh
uv run pytest tests/test_style_store.py tests/test_style_plugin.py tests/test_style_reviewer.py -q
uv run pytest tests/test_slang_store.py tests/test_slang_plugin.py tests/test_client.py tests/test_prompt_builder.py -q
uv run ruff check services/style plugins/style tests/test_style_store.py tests/test_style_plugin.py tests/test_style_reviewer.py
```

前端阶段增加：

```bash
cd admin/frontend
./node_modules/.bin/vue-tsc --noEmit
npm run build
```

人工验收：

- 在测试群连续聊天后，表达样本能出现且不是黑话词条。
- bot 回复更短、更贴近当前群节奏，但仍像凤笑梦。
- 禁用动态风格档案后，回复回到纯 soul + memory + slang 行为。
- 回滚档案后，新 prompt 使用旧版本。
- 黑话审核、记忆卡片、发送队列行为不回退。

## 人工审计卡点

Phase 0-6 初版已完成。当前需要人工端到端验收：

- `/admin/style` 能看到表达样本、动态档案和反馈记录，并能完成通过/拒绝/静音/好/坏反馈。
- 手动抽取后生成的样本应是表达习惯，不是黑话词条、事实记忆或人设改写命令。
- 动态风格档案启用后，bot 回复更贴近群节奏，但仍像凤笑梦。
- 禁用档案后，回复应回到只有 soul + memory + slang + approved 表达参考的行为。
- 风险表达可以被学习，但输出必须转译，不能原样复刻。
- 默认群隔离符合预期；只有 `global_enabled_group_ids` 中的群读取 global 表达池。
- 通过人工群聊验收后，才考虑 Phase 7 的评测集/few-shot 导出；仍不做模型微调。

## 更新日志

- 2026-05-12：表达手动抽取迁到 ConversationArchive cursor：`/style/extract/run` 优先使用 `style_manual_extract` 增量扫描；archive 不可用、读取失败或 cursor 需要人工重扫时自动退回旧最近窗口。普通 Prompt 注入、动态风格档案、全局表达池语义不变。
- 2026-05-12：补充与 ConversationArchive 的关系：表达学习抽取规划迁到归档底座 scanner 和 `conversation_message_refs`，但当前 Phase 0-6 状态不变；动态风格档案仍由 `StyleStore` 管理，不写入归档底座。
- 2026-05-12：创建长期追踪文档；确定路线为 "激进自演化 + 动态风格档案 + 核心 soul 不自动改"；确定表达学习独立于黑话模块，学习来源为人类表达和高质量 bot 回复；在黑话 wiki 中补充职责边界说明。
- 2026-05-12：根据用户取舍调整 Phase 2：骂人、阴阳怪气、过度幼态、客服腔等风险表达也要学习，不能直接拒学；学习层记录真实表达并打风险标签，输出层再由人设、心情和场景矫正。
- 2026-05-12：Phase 1 落地：新增 `services/style` 独立表达存储，支持样本、证据、修订记录、风险标签、输出策略、同群去重与 global 表达池去重；新增只读 Admin API `/api/admin/style/*`，可查看 summary、expressions、evidence、revisions。表达库使用 DELETE journal，避免后续人工审计 DB 时出现 WAL 视图分裂。验证：`tests/test_style_store.py tests/test_style_api.py tests/test_admin_api.py -q` 通过（71 passed），相关 ruff 通过。Phase 2 前暂停，等待人工审计。
- 2026-05-12：根据用户取舍明确作用域语义：默认不串群；开启全局表达后，开启的群视为同一表达学习池，像黑话 global 一样共同学习和使用。存储层用 `scope=global` + 真实来源群 evidence 承载该语义。
- 2026-05-12：Phase 2 初版落地：新增 `StyleExtractor` 和手动 `/api/admin/style/extract/run`，可从近期人类群聊抽取表达候选并写入 pending/approved；风险表达不拒学，改为 `risk_tags` + `output_policy` 标注。默认不自动批准、不后台运行、不注入 Prompt，等待人工候选验收后再进入 Phase 3。验证：表达学习相关 pytest `77 passed`，ruff 通过。
- 2026-05-12：Phase 3 初版落地：新增 `plugins/style`，仅将相关 approved 表达注入动态 Prompt；默认不串群，`global_enabled_group_ids` 中的群才读取 global 表达池；`observe_only` 不注入，带 `risk_tags` 的表达强制提示转译。验证：表达学习相关 pytest `83 passed`，ruff 通过。等待人工回复风格验收后再进入 Phase 4。
- 2026-05-12：Phase 4-6 初版合并落地：新增 feedback 表、profile 表、动态风格档案生成/启用/禁用/回滚、bot 回复中性弱信号记录、表达好/坏反馈和 `/admin/style` 轻量控制台。验证：表达学习相关 pytest `88 passed`，ruff 通过；插件总线 `46 passed`；前端 `vue-tsc --noEmit` 和 `npm run build` 通过。当前等待人工端到端验收。
- 2026-05-12：采纳二次审计 P1 收口：`StyleExtractor` 增加入库前低信号质量过滤，拦截“有人说话/可以接话”这类泛化候选但继续保留风险语气样本；`/admin/style` 动态档案补充启用旧版与回滚按钮。验证：表达学习相关 pytest `91 passed`，ruff、`vue-tsc --noEmit`、`npm run build` 通过。
- 2026-05-12：补齐手动抽取可观测性：`POST /style/extract/run` 返回每群扫描、候选、保存、待审/通过明细，`/admin/style` 右侧显示“最近抽取”；大群被扫描但 0 候选时会明确显示。验证：表达学习相关 pytest `92 passed`，ruff、`vue-tsc --noEmit`、`npm run build` 通过。
- 2026-05-12：修复表达学习与黑话边界：手动表达抽取保存前会读取当前群黑话 term/alias，候选若在 situation/style 中直接围绕黑话 token 展开，则计入 `filtered` 并不入库；`/admin/style` 最近抽取面板显示过滤数。已将 `993065015` 中把 `emu/ymy` 误写成“无意义重复短词”的表达样本标记为 rejected，保留 revision。验证：表达学习相关 pytest 通过，ruff、`vue-tsc --noEmit`、`npm run build` 通过。
