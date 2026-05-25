# Omubot 拟人回复与语句切割研究追踪

> 状态：完成
> 启动时间：2026-05-24
> 执行人：Codex
> 审计对象：Omubot 当前回复生成、回复分段发送、语句切割、人设语气约束与相关配置
> 方法约束：外部成熟项目必须拉取到 `.research/` 本地后以源码为证据；不以 README/简介作为结论依据；论文必须读取正文/抽取文本，不只看摘要。

---

## 0. 执行规则

1. 每个步骤开始前写清楚：细分动作、风险、回滚方式、验收证据。
2. 每个步骤完成后立刻回填：实际读取/拉取内容、代码证据、初步发现、自审遗漏。
3. 外部项目只作为对照样本；缺陷判断必须回到 Omubot 当前代码证据。
4. 本轮默认只产出研究和审计文档，不直接改回复运行时代码。
5. 核心问题聚焦“拟人回复”：包括说话节奏、语气自然度、分段边界、上下文承接、角色一致性、少机械说明。

---

## 1. 步骤总览

| 步骤 | 名称 | 状态 | 完成证据 |
|---|---|---|---|
| R0 | 建立追踪文档与当前 Omubot 回复链路索引 | ✅ 完成 | 本文 + 当前代码入口清单 |
| R1 | 拉取/更新成熟项目与论文素材 | ✅ 完成 | 本地 repos/papers HEAD 与文本抽取 |
| R2 | Omubot 当前回复/分段源码解析 | ✅ 完成 | `LLMClient` / `segmentation.py` / `send_queue.py` / persona prompt 事实表 |
| R3 | 外部项目源码拆解：拟人回复生成 | ✅ 完成 | 角色卡、prompt assembly、chat template、reply pipeline 证据表 |
| R4 | 外部项目源码拆解：语句切割/句界检测 | ✅ 完成 | rule/model sentence splitter 证据表 |
| R5 | 论文正文解析：拟人回复与句界切分原则 | ✅ 完成 | 正文证据表 |
| R6 | 对比审计与改进方向 | ✅ 完成 | 缺陷/风险/改造建议矩阵 |
| R7 | 自审、验证与收口 | ✅ 完成 | `git diff --check` + 证据链检查 |

---

## 2. 执行日志

### R0 建立追踪文档与当前 Omubot 回复链路索引

**开始前拆分**

1. 检查工作树状态，确认本轮只新增/修改追踪文档，不接管无关脏文件。
2. 搜索 Omubot 当前与回复生成、分段、发送节奏、人设指令相关的入口。
3. 读取最小代码切片：
   - `services/llm/segmentation.py`
   - `services/llm/client.py`
   - `kernel/router.py`
   - `services/send_queue.py`
   - `kernel/config.py`
   - `config/soul/SKILL.md`
4. 建立后续 R1-R7 的证据口径：先事实，后对比，再建议。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 把“回复自然”写成主观感受 | 高 | 每条判断必须绑定代码机制、配置参数或论文/项目源码证据 |
| 只看 prompt 文本而忽略发送链路 | 高 | 同时覆盖模型输出、分段器、send callback、队列 humanize |
| 外部项目偏角色扮演，不能直接迁移群聊 bot | 中 | A5/R6 只抽象机制，不照搬产品形态 |
| 当前 repo 有并行改动 | 中 | 不回滚、不格式化无关文件；只编辑本文 |

**回滚方式**

- 删除本文即可回滚本轮文档产物；`.research/` 是 ignored 本地素材，不影响 git。

**验收证据**

- `rg` 能定位当前回复生成和分段入口。
- 本节完成后回填当前代码入口事实表。

**完成后回填**

- 已检查工作树：本轮新增 `docs/tracking/reply-segmentation-persona-response-research.md`；另有 `services/persona/__init__.py`、`services/persona/shadow.py`、`tests/test_persona_shadow.py` 等并行变更，均不纳入本轮修改。
- 当前运行入口索引：
  - `kernel/router.py` 私聊路径将 `send_segment()` 传入 `LLMClient.chat(..., on_segment=send_segment)`，前 N-1 段由 callback 直接 `bot.send()`，最后一段由 caller `finish()`。
  - `services/scheduler.py` 群聊路径将 `on_segment()` 传给 `LLMClient.chat()`；首段可附加 `[CQ:reply]`，首段 `humanize="skip"`，后续段 `humanize="normal"`。
  - `services/llm/client.py` 当前运行时 `_reply_segments()` 使用本文件内旧 `_split_naturally()`，再由 `_coalesce_segments(..., _MAX_SEND_SEGMENTS=4)` 合并超额段；发送间隔使用模块常量 `_SEGMENT_DELAY = 0.8`。
  - `services/llm/segmentation.py` 存在新版 `segment_reply()`：支持 `ReplySegmentationConfig`、`pysbd_hybrid`、URL/CQ/ASCII token 保护、括号未闭合保护、软/硬段数限制与 `---cut---`。
  - `kernel/config.py` 和 `config/config.toml` 已暴露 `reply_segmentation` 配置，但 `services/llm/client.py` 未引用该配置，也未调用新版 `segment_reply()`。
  - `services/send_queue.py` 已定义 `ReplySegmentBatch`，能按 `first_segment_humanize` / `later_segment_humanize` / `inter_segment_delay_s` 发送批次；但当前 `services/scheduler.py` 仍直接 `_send_to_group()`，未使用 `ReplySegmentBatch`。
  - `config/soul/SKILL.md` 在 prompt 侧要求 QQ 聊天“默认一句话”、不用 Markdown、不要括号动作描述，并提示模型可用换行和 `---cut---` 控制多条消息。
- 当前测试事实：
  - `tests/test_segmentation.py` 覆盖新版 `services/llm/segmentation.segment_reply()` 的 token 保护、显式切分、语义换行、短尾合并、软/硬段数限制、破折号/引号/书名号不拆等。
  - 这些测试未直接证明生产运行路径使用了新版分段器；生产路径证据仍指向 `services/llm/client.py` 内旧 `_split_naturally()`。
- 初步发现：
  - Omubot 已有较完整的“自然句界 + 聊天节奏”分段器，但运行时可能尚未接线，这是本轮后续审计的最高优先级事实。
  - prompt 层要求每条 40 字以内/默认一句话，运行旧分段硬合并为 4 段；新版配置默认 `max_segment_chars=20` 且不限制段数。三处策略口径不完全一致。
  - 拟人回复目前由“人设 prompt + 清洗器 + 分段器 + humanizer 延迟”共同塑形；缺口不是单点 prompt，而是生成、切分、发送节奏、记忆回写是否一致。
- 自审遗漏：
  - R0 只确认入口和明显接线状态，尚未完整审查 prompt assembly、工具回合后回复、群聊 timeline 回写细节。
  - 外部成熟项目和论文尚未进入证据链，不能据此下最终方案。

### R1 拉取/更新成熟项目与论文素材

**开始前拆分**

1. 盘点 `.research/` 中已有角色/机器人项目，记录本地路径与 HEAD，不重新依赖 README。
2. 新增或更新与本轮直接相关的样本：
   - 拟人回复/角色聊天项目：SillyTavern、MaiBot、LangBot、AstrBot、Letta、text-generation-webui、generative_agents。
   - 语句切割项目：pySBD、pragmatic_segmenter、wtpsplit / Segment Any Text。
   - 对话/人格论文源码或正文：Generative Agents、MemGPT、RoleLLM、Character-LLM、PersonaGym、Punkt、WtP/SaT。
3. 每个外部仓库只记录代码证据入口和 commit HEAD；禁止把 README/简介当结论。
4. 论文若已有正文文本则复用；缺失时下载 PDF 并用 `pdftotext` 抽取。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 外部仓库体量过大，全文阅读失控 | 中 | 只进入 reply pipeline、prompt assembly、message rendering、sentence splitting 模块 |
| GitHub 仓库迁移或网络失败 | 中 | 先复用本地已有素材；失败项写明不可验证，不拿来下结论 |
| README 污染判断 | 高 | 搜索时允许定位仓库，分析时只引用源码/论文正文路径 |
| 论文 PDF 抽取失败 | 中 | 记录失败，换 arXiv HTML 或本地已有文本 |

**回滚方式**

- 删除 `.research/reply-systems/` 新增素材即可；该目录受 ignore 约束，不影响 git。

**验收证据**

- 外部样本清单包含本地路径、commit 或文件哈希/大小、代码入口。
- 追踪文档回填哪些素材进入后续 R3/R4/R5，哪些失败或跳过。

**完成后回填**

- 已复用本地已有成熟项目：
  - `.research/persona-systems/repos/SillyTavern` HEAD `51ad27fb86d3`
  - `.research/bot-systems/repos/MaiBot` HEAD `8b8118e49928`
  - `.research/bot-systems/repos/LangBot` HEAD `f0061817eaa6`
  - `.research/bot-systems/repos/AstrBot` HEAD `ff28eca9ca17`
  - `.research/persona-systems/repos/letta` HEAD `1131535716e8`
  - `.research/persona-systems/repos/generative_agents` HEAD `fe05a71d3e4e`
  - `.research/persona-systems/repos/PersonaGym` HEAD `536f705e6102`
  - `.research/persona-systems/repos/RoleLLM-public` HEAD `131a157c9962`
- 已新增本轮“回复/语句切割”样本：
  - `.research/reply-systems/repos/RisuAI` HEAD `fc7811d54878`
  - `.research/reply-systems/repos/text-generation-webui` HEAD `f9df9be98267`
  - `.research/reply-systems/repos/pySBD` HEAD `5905f13be4fc`
  - `.research/reply-systems/repos/pragmatic_segmenter` HEAD `358fe97e6290`
  - `.research/reply-systems/repos/wtpsplit` HEAD `e48922c0ad24`
- 已准备论文正文：
  - `.research/persona-systems/papers/text/generative_agents_2304.03442.txt`
  - `.research/persona-systems/papers/text/memgpt_2310.08560.txt`
  - `.research/persona-systems/papers/text/character_llm_2310.10158.txt`
  - `.research/persona-systems/papers/text/rolellm_2310.00746.txt`
  - `.research/persona-systems/papers/text/personagym_2407.18416.txt`
  - `.research/reply-systems/papers/text/punkt_kiss_strunk_2006.txt`，抽取约 126 KB。
  - `.research/reply-systems/papers/text/pysbd_2010_09657.txt`，抽取约 18 KB。
  - `.research/reply-systems/papers/text/wtp_2305_18893.txt`，抽取约 75 KB。
  - `.research/reply-systems/papers/text/segment_any_text_2406_16678.txt`，抽取约 118 KB。
- 自审：
  - 本阶段只完成“证据入库”，还没有开始从外部代码得出机制判断。
  - 后续 R3/R4/R5 禁止引用 README、changelog、marketing 文案；只允许引用源码、测试、配置 schema、论文正文。
  - 对 Omubot 的改进判断必须回扣 R0/R2 的生产代码证据，不能因为外部项目“看起来成熟”就直接迁移。

### R2 Omubot 当前回复/分段源码解析

**开始前拆分**

1. 解析回复生成前处理：prompt block、人设指令、群聊 pending/timeline、私聊 short-term 的输入组装。
2. 解析回复后处理：Markdown 清洗、括号动作清洗、贴纸叙述清洗、控制 token 清洗。
3. 解析生产分段路径：`LLMClient._reply_segments()`、`_split_naturally()`、`_coalesce_segments()`、`_SEGMENT_DELAY`、私聊/群聊 `on_segment`。
4. 对照新版分段器：`services/llm/segmentation.py` 的算法、配置、测试覆盖与生产接线状态。
5. 解析发送节奏：`services/scheduler.py` humanizer、`services/send_queue.py` 批次状态机、当前调用方是否接入。
6. 输出“代码证据 -> 当前机制 -> 拟人回复影响 -> 风险/机会”的事实表。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 把测试通过误判成生产启用 | 高 | 生产路径必须看 import/call site |
| 忽略私聊和群聊差异 | 中 | 分别记录 router 私聊与 scheduler 群聊 |
| 将 prompt 规则等同 runtime 保障 | 高 | prompt、清洗、分段、发送分别列证据 |
| 误伤并行 persona 文件 | 中 | 只读相关文件，不编辑运行代码 |

**回滚方式**

- 本阶段只编辑本文；若回滚，删除 R2 回填即可。

**验收证据**

- 至少覆盖 `services/llm/client.py`、`services/llm/segmentation.py`、`services/scheduler.py`、`kernel/router.py`、`services/send_queue.py`、`config/soul/SKILL.md`、`tests/test_segmentation.py`。
- 明确指出生产路径与新版分段器是否一致。

**完成后回填**

- 源码事实表：

| 代码证据 | 当前机制 | 对拟人回复的影响 | 风险/机会 |
|---|---|---|---|
| `services/llm/prompt_builder.py` | 静态人格、人设指令、管理员、主动发言规则组成首个 system block；插件块按 static/stable/dynamic 拼接 | 人设不只是一份文本，动态状态会参与每轮回复 | 动态块过多时需要预算/排序，否则角色基调被工具/知识块稀释 |
| `plugins/schedule/plugin.py` | 每轮注入当前时间/心情动态块 | 回复可随心情变短、变慢或更活跃 | 心情只进入 prompt，最终节奏仍要靠分段/发送层配合 |
| `services/llm/thinker.py` | 回复前先决策 `reply/wait`、检索模式、表情包、`tone` 四档 | 能减少机械插话，给主回复方向 | `tone` 只作为 system hint，不是结构化风格控制器；不会约束分段边界 |
| `plugins/style/plugin.py` / `services/block_trace/style_provider.py` | 注入“动态风格档案”和“表达习惯参考”，并收集 bot reply 反馈 | 已有学习表达习惯的入口，适合承载真人化语气样本 | 需要确认样本质量和负反馈，否则可能固化坏口癖 |
| `plugins/memo/plugin.py` | 注入全局索引、实体记忆、对话后抽取记忆 | 能让回复有关系连续性，而不只是套人设 | 如果记忆块和人设冲突，需要排序/预算/审计 |
| `plugins/sticker/plugin.py` | 表情包规则和库进入 prompt；发送后由后处理清除“已发送表情包”等叙述 | 表情包是拟人交流的一部分 | 文字与贴纸顺序依赖发送队列一致性，当前普通回复批次未统一走 `ReplySegmentBatch` |
| `services/llm/client.py::_clean_reply()` | 清 Markdown、括号动作、表情包叙述、控制 token | 防止舞台剧/客服/AI 输出直接暴露 | 清洗是兜底，不能替代生成策略；过度清洗可能吞掉合法颜文字边界 |
| `services/llm/client.py::_reply_segments()` | 生产路径使用旧 `_split_naturally()`，再 `_coalesce_segments(..., _MAX_SEND_SEGMENTS=4)` | 能实现连续多条发送，但超 4 段会合并 | 与新版配置/测试不一致；长回复会在第 4 段塞入多段内容，像“被压缩的长消息” |
| `services/llm/segmentation.py` | 新版 `segment_reply()` 支持 `pysbd_hybrid`、token 保护、软上限、硬上限、断点原因 | 更适合解释“为什么这样切”，也更适合调参和 debug | 当前未被 `LLMClient` 调用，主要用于 debug/test/benchmark |
| `kernel/config.py` / `config/config.toml` | 暴露 `reply_segmentation` 配置，默认 `max_segment_chars=20`，段数上限 0 | 配置面已准备好更细的回复节奏 | 生产路径没有消费该配置，用户调配置可能无效 |
| `services/scheduler.py` | 群聊首段 `humanize=skip`，后续 `normal`；段间延迟来自 `LLMClient._SEGMENT_DELAY` | 首段快、后续慢，接近真人连发 | 配置中的 `inter_segment_delay_s` 未接线；私聊 callback 也没有 humanizer |
| `services/send_queue.py` | `ReplySegmentBatch` 支持批次发送、首段 future、段间让位、固定间隔、人性化策略 | 这是更完整的拟人发送层 | 当前 scheduler 普通 LLM 回复仍未入批次路径，能力闲置 |
| `tests/test_client.py` | 覆盖旧 `_split_naturally()` 的换行、短碎片、显式 cut、长句切分 | 旧生产路径仍有测试保护 | 测试目标和新版 `tests/test_segmentation.py` 并存，说明迁移未收口 |
| `tests/test_segmentation.py` | 覆盖新版分段器的 CQ/URL/ASCII token、破折号、引号、书名号、软/硬限制 | 新分段器更接近本轮要研究的“自然切句” | 需要把生产入口迁移后再用这些测试证明真实生效 |

- 关键结论：
  1. 当前 Omubot 的拟人回复已经有“决策前 thinker + 动态 prompt + 表情包 + 记忆 + 风格学习 + 输出清洗”的骨架，不是纯 prompt bot。
  2. 最主要的链路缺口是分段系统双轨：生产 `client.py` 旧分段器与新版 `segmentation.py`/配置/测试脱节。
  3. 发送层也有双轨：`send_queue.py` 已具备批次化、人性化、段间让位能力，但普通 LLM 回复当前仍经 `on_segment` callback 逐段发送。
  4. prompt 中“40 字以内”和新版配置 `max_segment_chars=20`、旧生产 `_MAX_CHUNK/_MAX_SEND_SEGMENTS=4` 的口径不统一，会让模型生成策略、切分策略、可见发送形态互相拉扯。
  5. 时间线/短期记忆回写使用 `full_reply = "\n".join(segments)`，后续模型看到的是“一条 assistant 消息内含换行”，而用户实际看到的是多条消息；这会影响模型学习“我刚才是怎么连续发消息的”。
- 自审：
  - 已区分生产路径与测试/预备路径，避免把新版分段器误判为线上生效。
  - 未修改运行代码；本阶段只形成证据。
  - 后续 R3/R4 对照时，优先找外部项目如何解决三件事：角色信息如何编排、回复历史如何呈现、多条消息/句界如何由生成层与发送层共同决定。

### R3 外部项目源码拆解：拟人回复生成

**开始前拆分**

1. SillyTavern：定位 prompt assembly、character/persona/world-info、message examples、extension prompt、chat history template、send/regenerate 的源码。
2. RisuAI：定位角色卡/会话组装/模板替换/输出处理源码，重点看如何把角色设定变成可变而稳定的回复。
3. text-generation-webui：定位 chat prompt template、history formatting、instruction/chat 模式、stopping/continuation 逻辑。
4. MaiBot/AstrBot/LangBot：定位 bot 运行时回复 pipeline、人格/上下文/事件分发、发送阶段处理，重点对照 Omubot 的群聊 bot 形态。
5. Letta/generative_agents：只抽取 memory/persona/state 影响 dialogue 的核心代码，避免按 README 描述下结论。
6. 每个样本输出：代码入口、机制、可借鉴点、不可直接迁移点。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 前端项目源码入口多，容易迷路 | 中 | 用 `rg` 搜 `prompt`, `persona`, `character`, `history`, `template`, `generate` |
| 不同产品形态差异大 | 中 | 只抽象机制，不比较产品优劣 |
| 角色卡项目偏单聊，Omubot 偏 QQ 群聊 | 高 | 迁移建议必须加“群聊适配条件” |
| 只看配置样例而非源码 | 高 | 结论必须绑定 `.ts/.js/.py` 实现文件 |

**回滚方式**

- 本阶段只编辑本文；外部源码不改动。

**验收证据**

- 每个纳入样本都有至少一个源码路径。
- 结论围绕“拟人回复稳定但不僵硬”的机制，而不是功能介绍。

**完成后回填**

- 已读取外部样本源码入口，未引用 README/简介作为结论：

| 样本 | 源码证据 | 机制拆解 | 对 Omubot 的启发 | 不可直接迁移点 |
|---|---|---|---|---|
| SillyTavern | `.research/persona-systems/repos/SillyTavern/public/script.js`、`public/scripts/openai.js`、`PromptManager.js`、`world-info.js`、`power-user.js` | `Generate()` 收集 character description/personality/scenario/persona/examples/system/world-info/extension prompts；`preparePromptsForChatCompletion()` 与 `populateChatCompletion()` 再按 token budget 组装 prompt、chat history、dialogue examples 和 depth injection | 人设不是单块硬塞，而是稳定字段、示例、世界书、历史、扩展注入分层；动态块可放在 in-chat depth，避免长期人设被每轮状态污染 | 偏角色单聊/前端产品；swipes/regenerate 可借鉴为候选审查，不等于 QQ 群聊发送策略 |
| RisuAI | `.research/reply-systems/repos/RisuAI/src/ts/process/index.svelte.ts`、`prompt.ts`、`stringlize.ts`、`exampleMessages.ts`、`request/request.ts`、`prereroll.ts` | `sendChat()` 先构建 main/jailbreak/chats/lorebook/globalNote/authorNote/lastChat/description/personaPrompt 等分区；`exampleMessage()` 把 `{{char}}:`/`{{user}}:` 解析成示例；group 模式强制 “只写当前角色下一句”；输出后有 editoutput、reroll 与 incomplete tail 修剪 | 分区式 prompt template 适合规范人设文档；group 下必须有“只以当前 bot 回复”的硬约束；尾句完整性和候选重生成能降低僵硬断句 | 主要是角色聊天前端，多数结果是一条可见消息，不解决 QQ 连续多条发送节奏 |
| text-generation-webui | `.research/reply-systems/repos/text-generation-webui/modules/chat.py`、`modules/training.py` | `generate_chat_prompt()` 用 chat/instruction template 渲染 message list，历史从后往前塞；`get_stopping_strings()` 用模板推断 stop strings；streaming wrapper 分离 visible/internal，并清理 `name:` 前缀；训练只监督 assistant token | Omubot 需要基于当前模板/群名片生成 forbidden prefix/stop strings，防止模型续写用户；visible/internal 双轨能修复“用户看到多条，记忆里只有一条带换行”的不一致 | WebUI 不处理 QQ 发送队列、CQ 码或群聊触发概率 |
| MaiBot | `.research/bot-systems/repos/MaiBot/src/services/generator_service.py`、`src/chat/replyer/maisaka_generator_base.py`、`src/chat/replyer/maisaka_expression_selector.py`、`src/chat/utils/utils.py`、`src/services/send_service.py`、`src/config/official_configs.py` | `generate_reply()` 由 replyer 生成后调用 `process_llm_response()` 转成 `MessageSequence`；replyer 组装 system/history/final user message，包含人格、群聊注意事项、目标消息、推理/参考信息、关键词反应、表达习惯；表达习惯由子代理从 DB 候选中选 0-3 条；after_response hook 可改写或要求最多 3 次重生成；发送侧有 typing delay、引用回复、写库、同步历史 | “稳定人格 + 本轮目标 + 表达习惯样本 + after_response 自检重生成”比单纯 prompt 更接近拟人；随机备用风格和表达习惯能带来自主发挥，但必须被场景选择器约束 | 分句用逗号/句号/空格/换行加随机合并，并可造错别字；这类随机拟人可能伤害稳定人设，Omubot 应优先做结构化节奏计划，再决定是否引入轻微随机 |
| AstrBot | `.research/bot-systems/repos/AstrBot/astrbot/builtin_stars/astrbot/main.py`、`long_term_memory.py`、`core/pipeline/process_stage/*`、`core/pipeline/result_decorate/stage.py`、`core/pipeline/respond/stage.py` | long_term_memory 将群聊消息记录为 `[昵称/时间]: 内容`，主动回复时把群聊历史写进 prompt；pipeline 先 request LLM，再 result_decorate 做前缀、安全、分段，再 respond 阶段按组件发送；分段可按 regex/words，发送间隔可随机或按字数 log 计算 | Omubot 可借鉴“生成结果装饰”和“发送阶段”分离：先把文本切成 message components，再由发送层按组件间隔发送；引用/At 作为 header 只挂首段也很适合 QQ | AstrBot 的切分规则较通用，未处理人设语气学习；主动回复 prompt 是单块英文模板，不能直接替代 Omubot 的动态 persona 管线 |
| LangBot | `.research/bot-systems/repos/LangBot/src/langbot/pkg/pipeline/preproc/preproc.py`、`process/handlers/chat.py`、`provider/runners/localagent.py`、`provider/session/sessionmgr.py`、`pipeline/msgtrun/*`、`pipeline/respback/respback.py`、`pipeline/longtext/longtext.py` | preproc 建立 session/conversation/prompt/messages/user_message，并触发 PromptPreProcessing 插件；local-agent 用 `prompt + history + user` 请求模型，RAG 改写 user text；handler 把 user 和 assistant 写回 conversation；msgtrun 从后向前按轮数截断；respback 有全局随机强制延迟；longtext 超阈值转图片/转发 | Pipeline stage 化清晰：prompt 预处理、历史截断、生成、长文本处理、回发是不同阶段；Omubot 目前分段与发送混在 LLMClient callback 中，后续应把 visible reply plan 独立成 stage/service | LangBot 强调通用平台和流水线，不解决角色口吻/OOC；延迟是全局随机，不是基于情绪/关系/内容的回复节奏 |
| Letta | `.research/persona-systems/repos/letta/letta/prompts/system_prompts/memgpt_chat.py`、`functions/function_sets/base.py`、`functions/interface.py`、`agents/agent_loop.py`、`constants.py` | system prompt 明确 inner monologue 与 visible `send_message` 分离；persona/human 是 core memory 常驻块；`send_message()` 是唯一可见输出；core/archival memory 通过工具写入；MultiAgentMessagingInterface 只捕获 `send_message` 工具参数作为可见消息 | 对 Omubot 最重要的是 visible action 边界：模型可以思考、检索、更新记忆，但可见回复必须是显式 message/action；这能让“多条消息”成为结构化输出，而不是事后从一段文本硬切 | Letta 强依赖工具调用模型和 agent loop，不能在当前 LLMClient 中直接套用；中文 QQ 口吻仍要由本地 persona/示例/历史塑形 |
| Generative Agents | `.research/persona-systems/repos/generative_agents/reverie/backend_server/persona/cognitive_modules/converse.py`、`retrieve.py`、`plan.py`、`memory_structures/associative_memory.py`、`scratch.py` | scratch 持有 innate/learned/currently/lifestyle/action/chatting_with/chat buffer；`new_retrieve()` 用 recency/relevance/importance 选记忆；`agent_chat_v2()` 逐轮生成双方 utterance 并判断 end；`plan()` 先决定是否 talk/react/wait，再把 chat 写入行动状态和 associative memory | 拟人不只靠回复文字，而是“当前正在做什么、是否该回应、和谁刚聊过、关系记忆”共同决定；Omubot 的 thinker/timeline 可以升级为回复节奏计划的输入 | 仿真世界代码偏研究原型，单次聊天生成不适合作为 QQ 实时架构；但“先决定是否说话/说多久/何时停”可迁移 |

- R3 核心结论：
  1. 成熟样本很少只靠一段人设文本维持拟人感；普遍把“人格核心、用户/关系记忆、场景状态、示例、近期历史、工具/知识、输出约束”拆成不同层。
  2. 稳定不 OOC 的关键不是把人设重复塞得更重，而是把“不可偏离核心”和“可自由发挥空间”分离：核心常驻、习惯样本可检索、场景节奏每轮计算。
  3. 自然回复需要生成前和发送后两层协作：生成前给出当前可见回复目标、禁用续写他人、长度/段数倾向；发送后用分段组件、typing/间隔、引用首段、历史写回保持用户可见形态一致。
  4. MaiBot、AstrBot、LangBot 都把“回复内容生成”和“回发流水线”拆开；Omubot 当前 `LLMClient.chat(on_segment=...)` 把旧分段和发送 callback 耦合较深，这是后续改造要切开的点。
  5. Letta/text-generation-webui 的 visible/internal 分离说明：多条可见消息最好成为结构化结果，而不是把一段 assistant 文本换行后既当记忆又当发送内容。
- 自审：
  - 已覆盖角色聊天、群聊 bot、agent memory、通用 pipeline 四类样本；所有结论绑定源码入口。
  - 未把外部项目的 README、官网描述、配置示例当作依据。
  - R3 仍只说明“回复生成与人格约束”机制；句界切割算法本体放到 R4，论文原则放到 R5。

### R4 外部项目源码拆解：语句切割/句界检测

**开始前拆分**

1. pySBD：读取 `pysbd/segmenter.py`、`processor.py`、语言配置、缩写/标点替换器和 tests，确认它如何保护缩写、URL、数字、引号、括号，再恢复边界。
2. pragmatic_segmenter：读取 Ruby `lib/pragmatic_segmenter` 与 specs，确认规则管线和 pySBD 的血缘/差异，重点看 “清洗 -> 保护 -> 分割 -> 恢复”。
3. wtpsplit / SaT：读取 Python inference/model split 入口，确认神经分割如何从字符/子词概率产生边界，以及哪些配置影响阈值、stride、语言。
4. 对照 Omubot 新旧分段器：将外部规则型/模型型切分思想映射到 QQ 回复场景，判断哪些用于“句界准确”，哪些用于“拟人节奏”。
5. 输出证据表：项目、源码入口、切分机制、可借鉴点、对 Omubot 当前缺口的指向。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 把句界检测等同于聊天分段 | 高 | 明确区分“语言学句界”与“QQ 多条消息节奏” |
| pySBD/pragmatic 主要面向英文 | 中 | 只迁移 token/缩写/边界保护思想，中文标点继续回到 Omubot 测试验证 |
| 神经分割模型成本高 | 中 | R6 只作为可选增强，不作为短期必要改造 |
| 只读 tests 不读实现 | 中 | 每个项目至少绑定实现文件；tests 只作为行为证据补充 |

**回滚方式**

- 本阶段只编辑本文；外部源码不改动。

**验收证据**

- 至少覆盖 pySBD、pragmatic_segmenter、wtpsplit 三类实现。
- 能明确回答：Omubot 该先修生产接线，还是替换句界算法。

**完成后回填**

- 已读取实现与测试，未使用 README/简介作为结论：

| 样本 | 源码证据 | 切分机制 | 对 Omubot 的启发 | 不直接迁移点 |
|---|---|---|---|---|
| pySBD | `.research/reply-systems/repos/pySBD/pysbd/segmenter.py`、`processor.py`、`punctuation_replacer.py`、`lang/chinese.py`、`tests/lang/test_chinese.py` | `Segmenter.segment()` 默认保持 non-destructive spans；`Processor.process()` 先把换行转为 `\r`，再走 list item、缩写、数字、连续标点、numeric reference、email/geo/file 保护，最后 `split_into_segments()`；`punctuation_replacer.py` 将内部 `.` / `。` / `！` / `?` 等换成占位符；中文 `BetweenPunctuation` 专门保护 `《...》`、`「...」` 内部标点；测试确认 `《摔跤吧！爸爸》` 内 `！` 不切 | 句界规则的核心不是一个正则，而是“保护 -> 切分 -> 恢复 -> 对齐原文”。Omubot 新版 `segmentation.py` 已经在 CQ/URL/ASCII token、括号、中文标点上走类似方向，短期应优先接生产路径和补中文回归 | pySBD 面向文本句子边界，不知道 QQ 连续消息节奏、首段引用、typing delay、表情包组件 |
| pragmatic_segmenter | `.research/reply-systems/repos/pragmatic_segmenter/lib/pragmatic_segmenter/processor.rb`、`cleaner.rb`、`punctuation_replacer.rb`、`languages/chinese.rb`、`spec/pragmatic_segmenter/languages/chinese_spec.rb` | `Processor#process` 先 `List.add_line_break`，再 replace abbreviations/numbers/continuous punctuation/numeric references/email/geo/file，`split_into_segments` 内处理括号、单换行、省略号、标点、恢复符号；`Cleaner#clean` 处理 HTML、新行、PDF、括号内标点、无空格连句；中文模块同样保护书名号和直角引号内标点 | 与 pySBD 相互印证：成熟规则库把“清洗”和“句界”分层，而且大量边界是负例保护。Omubot 不应把 `---cut---`、换行、中文标点、CQ 码混在一个硬正则里长期维护 | Ruby 实现和英文规则资产不可直接搬；聊天分段还要考虑语气、长度、段数、发送间隔 |
| wtpsplit / SaT | `.research/reply-systems/repos/wtpsplit/wtpsplit/__init__.py`、`extract.py`、`extract_batched.py`、`utils/constraints.py`、`models.py` | `predict_proba()` 通过 `extract()` 对文本做 stride/block chunking，模型输出每字符或 token 对齐 logits，再经 sigmoid 得到边界概率；`split()` 用 threshold 或 paragraph threshold 转为 indices；当设置 `min_length/max_length` 时走 `constrained_segmentation()` + `_enforce_segment_constraints()`，支持 viterbi/greedy、prior、严格长度约束；SaT 将 token probability 映射回 char probability，并可按输入 newline 再拆 | 神经分割适合解决无标点、多语言、文本体裁不同导致的句界不稳；长度约束与 prior 思路可借鉴为“回复段长计划”，但应放在可解释配置后面 | 引入模型会增加依赖、延迟、缓存和可观测成本；Omubot 当前最大缺口不是句界模型能力，而是新版分段器和发送批次未接生产链路 |

- R4 关键结论：
  1. Omubot 现阶段不应先替换句界算法；应先把已有新版 `segment_reply()` 接入 `LLMClient`/配置，并让测试覆盖生产入口。
  2. “句界检测”只回答哪里可以断句，不回答一个拟人 bot 此刻该发几条、先快后慢还是只回一句。聊天节奏需要独立的 `ReplyStylePlan` / `VisibleReplyPlan` 输入心情、关系、触发强度、目标消息、场景状态。
  3. 规则型项目证明 CQ/URL/ASCII token/书名号/括号保护应作为分段器硬能力；模型型项目证明长度约束可以和边界概率并存，但这属于中后期增强。
  4. 对 QQ 场景，最佳中间形态是：生成层产出可见回复文本或结构化段落意图，分段器只给候选边界，发送层根据首段引用、humanizer、段间延迟和消息组件做最终发送。
- 自审：
  - 已区分规则型和模型型切分，避免把 wtpsplit/SaT 当短期必需依赖。
  - 已补中文书名号/直角引号保护证据；还缺的是 Omubot 生产接线后的中文回归测试，而不是外部算法材料。
  - R4 只处理“怎么切”；“为什么这样说、怎么保持人设不僵硬”继续放到 R5/R6。

### R5 论文正文解析：拟人回复与句界切分原则

**开始前拆分**

1. 拟人回复论文组：
   - Generative Agents：读取 memory stream、retrieval、reflection、planning、dialogue 相关正文，确认“拟人”是否来自状态/记忆/计划，而不是单轮 prompt。
   - MemGPT/Letta：读取 core memory、persona/human memory、visible message/tool call 边界，确认可见回复和内部状态如何分离。
   - Character-LLM、RoleLLM、PersonaGym：读取角色一致性、角色资料构造、语言风格、评估维度，确认“保持设定”和“自然发挥”的可验证目标。
2. 句界切分论文组：
   - Punkt：读取缩写、collocation、sentence starter、无监督句界判别，确认规则库为什么需要负例保护。
   - pySBD：读取正文中 rule-based SBD、offset、cleaning、benchmark 相关内容，确认 pySBD 设计意图与代码一致。
   - WtP / Segment Any Text：读取模型目标、边界概率、无标点/多语言/长度约束相关内容，确认神经切分的适用边界。
3. 论文结论必须落回 Omubot：每条原则都对应当前代码缺口或保留项。
4. 不引用 abstract/README；只用本地 `.research/**/papers/text/*.txt` 正文抽取文本。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 论文概念过大，落不到当前工程 | 高 | 每条原则后加“对应 Omubot 证据/动作” |
| 把角色评估论文当运行时方案 | 中 | 区分评估维度、数据构造、线上架构 |
| 句界论文偏通用 NLP，忽略聊天节奏 | 高 | 保持“语言句界”和“拟人多条消息”双层结论 |
| 抽取文本可能丢图表 | 中 | 只使用可检索正文；图表缺失处标记不作为证据 |

**回滚方式**

- 删除 R5 章节回填即可；不改运行代码和外部素材。

**验收证据**

- 每篇纳入论文至少有本地正文路径。
- 输出“论文事实 -> 对 Omubot 的可执行原则 -> 风险”的表。
- 明确哪些原则支持短期改造，哪些只作为中长期增强。

**完成后回填**

- 已读取本地论文正文抽取文本，未使用 abstract/README 作为结论：

| 论文 | 正文证据 | 论文事实 | 对 Omubot 的可执行原则 | 风险/边界 |
|---|---|---|---|---|
| Generative Agents | `.research/persona-systems/papers/text/generative_agents_2304.03442.txt` | 第 4 节把可信行为拆为 memory stream、retrieval、reflection、planning；检索按 recency/relevance/importance 选入 prompt；对话生成先有观察、关系摘要、当前意图，再生成 utterance；论文也承认对话风格可能偏正式 | 拟人回复不应只靠固定人设。Omubot 的 thinker、timeline、memo、style blocks 应进入一个回复计划：先决定是否说、对谁说、说多长、是否追问，再生成短句 | 研究场景是模拟世界，不是 QQ 实时 bot；迁移的是“状态/记忆/计划分层”，不是完整仿真架构 |
| MemGPT | `.research/persona-systems/papers/text/memgpt_2310.08560.txt` | main context 被拆成 system instructions、working context、FIFO queue；working context 保存 user/persona 关键事实；queue manager 将消息写入 recall storage；函数调用让模型自主管理记忆；长期对话评估关注 consistency 与 engagement | Omubot 需要把“用户实际看到的多条消息”和“模型内部记忆”显式建模。多段 visible reply 应有 metadata 或多消息记录，避免只以一条换行文本写回 | MemGPT 强依赖工具调用/函数解析；短期只迁移可见/内部边界和记忆层级，不强推 agent loop |
| Character-LLM | `.research/persona-systems/papers/text/character_llm_2310.10158.txt` | 角色不是只靠口癖，而是 profile、scene、interaction、target character reflection；提出 protective experiences，让角色面对超出时代/身份的问题时表现“不知道/困惑”，避免 Character Hallucination；评估含 memorization、values、personality、hallucination、stability | 人设文档应区分核心资料、经历/关系、表达习惯、禁区/不知道策略。OOC 防护不应只写“不要 OOC”，要给角色面对越界问题的响应模式 | 论文使用微调角色模型；Omubot 当前以 prompt/runtime 为主，应先通过模板和样例做 protective scenes |
| RoleLLM | `.research/persona-systems/papers/text/rolellm_2310.00746.txt` | 设计原则包含 lexical consistency 与 dialogic fidelity；role-specific knowledge 分 script-based 与 script-agnostic；RoleGPT 用角色描述、catchphrases、top-5 relevant dialogue pairs 做 few-shot；Context-Instruct 先分段 role profile 再生成 QA 数据 | Omubot 的 persona source importer 不应只产一块设定文本；应产稳定核心、catchphrase/禁用口癖、可检索 dialogue examples、角色知识 QA、场景指令 | RoleLLM 面向角色基准和训练数据，不等于线上发送节奏方案；“catchphrase”要防过拟合，不能让 bot 生搬硬套 |
| PersonaGym | `.research/persona-systems/papers/text/personagym_2407.18416.txt` | PersonaScore 拆成 Expected Action、Action Justification、Linguistic Habits、Persona Consistency、Toxicity Control；动态环境选择和 persona-specific questions 用于评估；结果指出模型规模不保证 persona 能力，Linguistic Habits 是常见弱项 | Omubot 验收拟人回复时至少要拆五类 eval：是否该回、行为/建议是否符合设定、语言习惯、直接问设定时一致性、越界/毒性控制。不能只靠“像不像”主观验收 | PersonaGym 是评估框架，不提供生产切分算法；可用于后续 eval.yaml 设计 |
| Punkt | `.research/reply-systems/papers/text/punkt_kiss_strunk_2006.txt` | 句界歧义大量来自 period 的多重用途；先做 type-based abbreviation detection，再做 token-based reclassification；缩写证据包括强 collocation、短、内部 periods；token 阶段用 orthographic、collocation、frequent sentence starter | Omubot 分段应优先保护负例：URL、CQ、数字、英文缩写、连续标点、书名号/引号内部标点。不要用单个“看到标点就切”的规则长期承载 | Punkt 主攻 period 语言；中文 QQ 短句还需要本地样例和测试，不应照搬英文缩写模型 |
| pySBD paper | `.research/reply-systems/papers/text/pysbd_2010_09657.txt` | Processor 以 Common/Standard/ListItem/Abbreviation/Exclamation/BetweenPunctuation 分组；先加 unicode placeholder 表示非句界标点，再用简单 regex 切分，最后恢复原文；支持 non-destructive char span；Cleaner 处理 OCR/PDF/URL/HTML/无空格连句 | Omubot 新版 `segmentation.py` 的“保护 token + break reason + 原文保留”方向正确；短期要做的是生产接线、配置统一、中文/QQ 噪声回归 | pySBD 强调句界准确，不解决一条回复切成几条更像真人 |
| WtP | `.research/reply-systems/papers/text/wtp_2305_18893.txt` | 把句子定义为“可合理跟随 newline 的字符序列”；训练双向字符模型预测每个字符后是否有 newline；threshold 决定边界；辅助标点预测用于域适配；不依赖标点，适合无标点/低资源/噪声文本 | 如果 Omubot 后续要处理无标点长回复或 ASR/转写文本，可考虑边界概率模型；但当前 QQ 回复更需要短句生成和发送计划 | 神经切分引入模型依赖，短期收益低于接好已有分段器 |
| Segment Any Text | `.research/reply-systems/papers/text/segment_any_text_2406_16678.txt` | SaT 使用 subword encoder 提升效率，不依赖语言码；通过标点/大小写/空格 corruptions 提升噪声鲁棒；limited lookahead 处理短序列；LoRA 做域适配；论文指出 prompt LLM 做句界容易改写输入，不适合需要字符保持的切分任务 | Omubot 不应把“切句”交给 LLM 再让它重写文本；切分应是 deterministic/runtime 层。若以后做模型切分，优先要求可复原原文和域适配样本 | SaT 适合 NLP 预处理；聊天节奏仍需业务层决定 |

- R5 核心结论：
  1. 拟人回复的稳定性来自分层：核心人设/价值观/边界不可漂移，经历/关系/表达习惯可检索，当前情绪/场景/触发强度每轮计算。
  2. 自主发挥不是让模型随便扩写，而是给它“可发挥空间”：本轮目标、可用记忆、可选表达习惯、允许的段数/语气、越界时怎么不知道。
  3. 不 OOC 需要两种约束：生成前的角色边界与 protective examples，生成后的审查/重生成或降级策略。
  4. 句界算法应保持原文、保护负例、可解释；聊天多条发送则应由 runtime plan 决定，不能把 `max_segment_chars` 当成拟人回复的唯一旋钮。
  5. LLM prompt 切句不适合作为核心分段器，因为它可能改写文本；Omubot 当前的 deterministic `segment_reply()` 方向比“让模型帮忙切”更可靠。
- 自审：
  - 已覆盖 persona/runtime 论文与 sentence boundary 论文两组证据。
  - 论文事实已回扣到 Omubot 的生成、切分、发送、记忆回写四层。
  - R5 未直接提出代码改动，具体缺陷和阶段方案放入 R6。

### R6 对比审计与改进方向

**开始前拆分**

1. 汇总 R2-R5 证据，按 Omubot 当前链路拆成四层审计：
   - 生成前：人设、记忆、风格、thinker、prompt blocks。
   - 生成后：清洗、stop/forbidden prefix、重生成/降级。
   - 切分：旧 `_split_naturally()` 与新版 `segment_reply()`/配置/测试。
   - 发送与回写：`on_segment` callback、`ReplySegmentBatch`、timeline/short-term 记录。
2. 对每个缺陷评估：严重度、证据、拟人影响、短期修复、长期方案。
3. 输出阶段化路线：
   - P0 接线和配置统一。
   - P1 可见回复计划与批次发送。
   - P2 人设文档/导入产物分层。
   - P3 eval 与后处理自审。
   - P4 可选模型切分/域适配。
4. 自审是否存在“外部项目好所以照搬”的推理跳跃；所有建议必须回到 Omubot 当前代码缺口。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 建议过大，无法落地 | 高 | 每条建议标短期/中期/长期，并给出最小入口 |
| 忽略当前已有能力 | 高 | 先列保留项：thinker、style、memo、sticker、segment_reply、ReplySegmentBatch |
| 把研究文档写成实现承诺 | 中 | 明确本轮只审计，不改 runtime |
| 只关注分段忽略人设自然度 | 高 | R6 矩阵必须覆盖拟人回复生成与 OOC 防护 |

**回滚方式**

- 删除 R6 回填即可；不影响代码。

**验收证据**

- 缺陷矩阵能直接对应代码文件/配置/测试。
- 阶段路线能回答“先做什么、为什么不是先换算法”。
- 明确剩余未做项与下一阶段入口。

**完成后回填**

- 对比审计矩阵：

| 优先级 | 缺陷/风险 | Omubot 代码证据 | 拟人回复影响 | 建议动作 |
|---|---|---|---|---|
| P0 | 新版分段器未进入普通生产路径 | `services/llm/client.py` 中 `_reply_segments()` 仍调用本文件旧 `_split_naturally()` 与 `_coalesce_segments(..., _MAX_SEND_SEGMENTS=4)`；`services/llm/segmentation.py` 的 `segment_reply()` 未被 `LLMClient` 调用 | 用户调 `reply_segmentation` 配置可能无效；新版保护/软限制/break reasons 只在 debug/test 有效，真实回复仍可能 4 段硬合并 | 将 `ReplySegmentationConfig` 注入 `LLMClient`，用 `segment_reply()` 替换旧 `_reply_segments()`；保留旧函数一轮兼容测试后删除 |
| P0 | 配置、prompt、生产常量三套口径不统一 | `config/soul/SKILL.md` 写“默认一句话”“每条 40 字以内”；`config/config.toml` 写 `max_segment_chars=20`、`max_send_segments=0`；`client.py` 旧常量 `_MAX_SEND_SEGMENTS=4`、`_SEGMENT_DELAY=0.8` | 模型被要求短，但 runtime 又可能把多段合并成第 4 段；“像人连发”会变成“最后一条很长” | 统一为配置驱动：prompt 只说高层意图，runtime 以 `reply_segmentation` 决定段长/段数/间隔；文档标注实际默认 |
| P0 | 普通群聊回复未使用 `ReplySegmentBatch` | `services/send_queue.py` 已有 `ReplySegmentBatch`，但 `services/scheduler.py` 普通 LLM 回复仍通过 `on_segment` + `_send_to_group()` 逐段发送 | 首段引用、humanizer、段间让位、工具输出排序不能在统一队列中表达；未来表情包/文字顺序更容易分叉 | 群聊回复改为 `segment_reply()` 生成 segments 后交给 `GroupSendQueue.enqueue_reply_batch()`；首段可加 reply prefix |
| P1 | visible 多条消息与 memory 一条换行文本不一致 | `client.py` 对外发送前 N-1 段，最后返回 caller；但 timeline/short-term 写 `full_reply = "\n".join(segments)` | 用户看到多条 QQ 消息，模型后续看到一条 assistant 消息内换行；长期学习不到真实“我发了几条” | 在 timeline/short_term 增加 `visible_segments` metadata，或写回多条 assistant event；至少保留 send shape |
| P1 | 生成前缺少结构化 `ReplyStylePlan` / `VisibleReplyPlan` | 现有 thinker 输出 `tone`、prompt 注入 style/mood/memory，但没有统一对象决定“回几条、是否追问、是否短停、是否只发贴纸/文字” | 自主发挥容易落到模型自由扩写；人设可能稳定但回复节奏僵硬或每次都像执行规则 | 新增轻量 plan：输入 mood、关系、触发类型、目标消息、上下文热度，输出 `target_segments`、`max_chars`、`ask_followup`、`energy`、`ooc_guard` |
| P1 | forbidden prefix / stop string 仍主要靠 prompt 与清洗 | `config/soul/SKILL.md` 禁止 `昵称(QQ号):` 等前缀；`client.py` 清 Markdown/括号动作/控制 token，但未见基于当前群成员/模板的 stop strings 或 prefix 拦截重生成 | 模型可能续写他人、输出内部标记或角色前缀；清洗能删一部分，但不能保证语义没串台 | 生成前构造 forbidden prefixes；生成后检测 `昵称:`、`«msg:»`、assistant/user 前缀，必要时重生成一次或只保留合法尾段 |
| P2 | 人设文档缺少“不可偏离核心”和“可发挥空间”的运行时边界 | 当前 `config/soul/SKILL.md`/persona source 已有规则和口癖，但导入产物仍容易变成大块说明 | 模型可能生搬人设原句，或在缺少 protective examples 时遇到越界问题 OOC | persona source importer 输出分层 schema：core identity、values、relationship stance、speech habits、avoid/ooc boundaries、protective responses、few-shot examples |
| P2 | 缺少 persona/回复节奏 eval 闭环 | `tests/test_segmentation.py` 覆盖切分算法；未看到针对“是否该回/语言习惯/OOC/越界不知道/多条可见消息”的统一 eval | 只测“怎么切”，不测“像不像这个人、该不该这么说” | 按 PersonaGym/Character-LLM 维度扩展 `eval.yaml`：Expected Action、Linguistic Habits、Persona Consistency、OOC Hallucination、Visible Shape |
| P3 | 模型切分不是短期瓶颈 | 新版 deterministic 分段器已有 CQ/URL/ASCII/引号/书名号/软硬限制；外部 SaT/WtP 引入模型依赖 | 过早接神经切分会增加延迟和维护成本，却不能解决回复僵硬/OOC | 保持 `pysbd_hybrid/local`；等生产接线和 eval 完成后，再用 SaT/WtP 做离线 benchmark |

- 已有能力应保留：
  - `services/llm/thinker.py` 的 reply/wait、tone、贴纸决策是回复计划的上游基础。
  - `plugins/style`、`services/block_trace/style_provider.py`、`plugins/memo`、`plugins/sticker` 已经能提供表达习惯、关系记忆和非文字表达。
  - `services/llm/segmentation.py` 的新版分段器方向正确，适合先接线而不是重写。
  - `services/send_queue.py` 的 `ReplySegmentBatch` 已具备批次发送状态机，适合承接普通回复。
- 阶段路线：
  1. P0 接线收口：让 `LLMClient` 使用 `segment_reply()` 与 `reply_segmentation` 配置，删除/降级旧 `_split_naturally()` 生产职责；测试从 `tests/test_client.py` 迁移到生产入口。
  2. P0 发送收口：普通群聊回复走 `ReplySegmentBatch`，把首段引用、首段 skip、后续 normal、`inter_segment_delay_s` 放到统一队列。
  3. P1 可见形态收口：timeline/short-term 记录 visible segments 或 metadata，保证模型看到的历史接近用户看到的历史。
  4. P1 回复计划：新增 `ReplyStylePlan`/`VisibleReplyPlan`，由 thinker/mood/style/memory/trigger 共同决定段数倾向、短句程度、追问/停顿、OOC guard。
  5. P2 人设格式：persona source importer 产出 core、history、speech、forbidden、protective examples、few-shot dialogue、eval cases，避免大块人设硬塞。
  6. P2 eval：建立“拟人回复验收集”，不仅测切分，还测该不该回、语言习惯、角色一致性、越界不知道、可见多条消息。
  7. P3 模型切分研究：仅在 deterministic 分段器遇到无标点/多语言噪声瓶颈后，离线评测 SaT/WtP，不作为当前主战场。
- 结论：
  - 短期第一刀是生产接线和配置统一，不是换算法。
  - 中期关键是把“句界候选”和“聊天节奏决策”拆开：句界分段器只负责哪里能断，回复计划负责为什么断成这些条。
  - 人设自然度的核心不是加更多人设文字，而是让核心设定、关系记忆、场景目标、表达习惯、越界策略分别有位置，并在发送形态上保持一致。
- 自审：
  - 已明确保留现有能力，没有把外部项目当成推倒重来理由。
  - 每个缺陷都绑定当前文件或测试证据。
  - 本轮仍是研究/审计文档，未修改 runtime；下一阶段应按 P0 拆执行文档再动代码。

### R7 自审、验证与收口

**开始前拆分**

1. 更新步骤总览状态，确认 R0-R7 都有开始前拆分、风险、回滚、验收、完成后回填。
2. 运行文档级验证：
   - `git diff --check -- docs/tracking/reply-segmentation-persona-response-research.md`
   - `git check-ignore -v .research/`
   - `git status --short`
3. 自审证据链：
   - 外部项目结论是否都来自源码/测试。
   - 论文结论是否都来自本地正文文本。
   - Omubot 缺陷是否都能回到当前代码。
4. 标记最终状态并写收口说明。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 文档过长但缺收口 | 中 | R7 只写最终结论、验证结果、下一步入口 |
| 忽略 git 脏文件 | 中 | `git status --short` 明确区分本轮文档与并行改动 |
| 把未执行代码改动写成已完成 | 高 | 明确本轮只完成研究审计，不声称 runtime 已修 |

**回滚方式**

- 回滚本文档修改即可；`.research/` 是 ignored 素材。

**验收证据**

- diff check 通过。
- `.research/` 确认 ignored。
- 最终状态更新为完成。

**完成后回填**

- 验证结果：
  - `git diff --check -- docs/tracking/reply-segmentation-persona-response-research.md`：通过，无输出。
  - `git check-ignore -v .research/`：`.gitignore:54:.research/`，确认研究素材目录不入库。
  - `git status --short`：本轮新增 `docs/tracking/reply-segmentation-persona-response-research.md`；同时工作树存在并行改动 `kernel/router.py`、`kernel/types.py`、`services/llm/client.py`、`services/llm/prompt_builder.py`，本轮未修改这些代码文件。
- 证据链自审：
  1. 外部项目分析均绑定本地源码或测试路径：SillyTavern、RisuAI、text-generation-webui、MaiBot、AstrBot、LangBot、Letta、Generative Agents、pySBD、pragmatic_segmenter、wtpsplit/SaT。
  2. 论文分析均绑定本地正文抽取文本：Generative Agents、MemGPT、Character-LLM、RoleLLM、PersonaGym、Punkt、pySBD、WtP、Segment Any Text。
  3. Omubot 缺陷均回扣当前代码：`services/llm/client.py` 旧分段生产路径、`services/llm/segmentation.py` 新分段器、`kernel/config.py`/`config/config.toml` 配置、`services/send_queue.py` 批次能力、`services/scheduler.py` 发送路径、`config/soul/SKILL.md` prompt 规则。
  4. 已明确本轮只完成研究审计，不声明 runtime 已修复。
- 最终结论：
  - 当前最主要缺口是“已有新分段器/配置/发送批次未统一接入普通回复生产链路”，不是外部句界算法不足。
  - 拟人回复要拆成“角色稳定层 + 场景计划层 + 可见消息层 + 发送节奏层 + 记忆回写层”；单靠增加人设文字会让回复更僵硬。
  - 下一阶段应从 P0 开始：先接 `segment_reply()` 到 `LLMClient`，再让群聊普通回复走 `ReplySegmentBatch`，同时统一 prompt/config/runtime 的段长和段数口径。
