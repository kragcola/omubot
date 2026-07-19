# Bot 记忆、措辞与图片回复行为审计（2026-07-18）

> 状态：审计完成，待按优先级修复
> 基线：`/Volumes/OmubotDisk/omubot` 当前工作树、2026-07-18 09:27 创建的 `qq-bot` 运行容器及其只读日志/SQLite 数据
> 范围：无法记忆、内部“置信度”提示泄漏、“被你发现”词族重复、独立省略号消息、图片描述式回复
> 本轮边界：只读取证与文档落盘；未修改生产行为、配置、数据库、容器或 NapCat，未部署
> 隐私：报告不记录 QQ/群 ID、完整私人消息、请求 ID 或凭据；数据库均以 SQLite `mode=ro` 打开并先执行 `PRAGMA quick_check`

## 1. 执行结论

五类用户可见问题都能在日志、运行数据或当前代码中找到明确原因，不应归结为“模型偶尔发挥不好”。其中三类是确定性的管线或数据契约缺陷：

1. 群聊记忆存在“写入成功但下一轮不可见”的 scope 断链，图片身份纠正还被错误抽取成用户偏好。
2. 人格漂移修复发生在可见回复兜底之后，修复模型返回 `...` 时没有重新执行可见性下限，导致纯省略号被直接发送。
3. 视觉系统把成品图片说明伪装成用户正文，同时 Style 数据库又把系统生成的视觉注释误标成人类表达并批准，形成描述性回复的反馈环。

其余两类是确定的防线缺口：视觉置信诊断词直接进入主模型可复述文本；重复检测只看紧邻上一条完整回复，识别不了跨多轮的短语词族。

| 优先级 | 问题 | 审计判断 | 证据强度 |
| --- | --- | --- | --- |
| P1 | 群聊无法记忆图片人物/用户纠正 | 写入数据模型、可见 scope 和视觉对象身份契约同时不成立 | 日志 + 生产 DB + 代码确认 |
| P1 | 独立发送 `...` / `…` | 已观察的 `...` 来自 persona repair 后缺少二次 finalize；Unicode `…` 另有可复现分段风险 | 日志 + BlockTrace + 代码确认 |
| P1 | 图片总是描述性说明 | 视觉 prose 被当用户正文；Style 又学习并注入“先描述图片再说明用途” | 生产 DB + 代码 + 排名复现确认 |
| P2 | 说出“置信度/阈值” | 内部视觉诊断直接进入模型文本，现有测试反向保护泄漏格式 | 日志 + 代码 + 测试确认 |
| P2 | “被你发现/看穿/抓到”反复出现 | 不是已批准口癖，而是短语级、跨多轮重复检测缺失，并被反复认错场景放大 | 日志 + 数据排除 + 代码确认 |

## 2. 样本、方法与限制

### 2.1 日志样本

主症状统计从 2026-07-10 至 2026-07-18 的 48 个可解析滚动日志切片中提取了 114 条 `services:chat` 最终回复。启发式命中如下：

- 独立 ASCII `...`：1 条。
- 用户可见“置信度/低置信”措辞：1 条。
- “被你发现/看穿/抓到”词族：5 条。
- “记住/记性/不会忘”承诺：18 条。
- “没记住/认错/搞混/翻车/认不出来”：12 条。
- 图片描述式回复：13 条。

这些分类用于定位问题，不是严格的产品发生率估计。日志只保留截断请求预览和最终分段回复，没有完整主模型原始文本，因此不能严谨证明“所有图片都被描述”或把纯标点精确归因到模型原文、分段器还是修复模型。代码与生产数据用于补足边界证据。

另对容器滚动日志做了更宽的纯标点扫描：135 个日志文件、约 1.33 GB、200 条可解析最终输出中未命中独立 Unicode `…`，但命中 4 条其他纯标点输出。由此可知：用户报告的实际样本是 ASCII `...`；Unicode `…` 是当前代码可稳定复现的相邻风险，二者不能混写成同一个根因。

### 2.2 运行态与数据

- `qq-bot` 容器创建于 2026-07-18 09:27（Asia/Shanghai）。
- 关键宿主源码与运行容器 SHA-256 一致：`services/llm/client.py`、`plugins/memo/plugin.py`、`services/media/visual_evidence.py`、`services/media/vision.py`、`services/style/store.py`。
- 生产 `memory_cards.db`、`style.db`、`block_trace.db` 均为 `quick_check=ok`。
- 宿主 `storage/` 与容器 `/app/storage/` 不是同一份数据；本文生产数据结论来自容器内只读数据库，不以宿主旧副本代替运行态。

### 2.3 排除项

- 未发现 memo/context 异常导致写入服务整体失效。
- 日志中的 schedule 依赖阻塞与本次记忆问题无关。
- DEBUG 级 plugin permission denied 是总线探测不匹配；memo/context 的有效入口仍执行，不能据此判断插件失效。
- 配置中存在内联敏感值，已脱敏，与本次五类根因无直接关系。

## 3. Findings

### P1-1 群聊记忆是“写了但读不到”，且图片身份被写成了错误事实

#### 现象与数据证据

问题会话中用户反复教机器人识别图片人物，Bot 多次承诺记住，随后仍认错或无法确认。生产 `memory_cards.db` 证明 extractor 并非没有运行：

- extractor active 卡 36 张，全部是 `user` scope；不存在 extractor 生成的 group 卡。
- 目标会话确实写入了相关卡，但语义发生误归因，例如：
  - “这是高松灯”被写成“用户提到高松灯这个名称”。
  - “这是守岸人/安洁莉娜”被写成“用户偏好被称呼为该名字”。
  - “你是凑企鹅”被写成“用户希望助手扮演凑企鹅而非 emu”。
  - “弗洛洛”只勉强写成用户对某个表情或概念的命名。

同期 context 日志在群聊中使用 `scope=group`；多数轮次只有 `minimal_hint` 且 0 hit。数据库里有卡、检索服务也运行，但写入与读取的 scope 不相交。

#### 根因链

1. Legacy extractor 的系统提示只允许提取“关于当前用户”的事实，无法表达“图片指纹/视觉对象 → 角色身份”关系：`plugins/memo/plugin.py:61`。
2. Legacy 和 write-policy 两条路径都显式丢弃 `group_id`：`plugins/memo/plugin.py:187`、`plugins/memo/plugin.py:263`。
3. 两条写入路径都固定创建 `scope="user"` 卡：`plugins/memo/plugin.py:229`、`plugins/memo/plugin.py:553`、`plugins/memo/plugin.py:581`。
4. 群聊检索有 `group_id` 时只解析为 group pools：`services/memory/retrieval.py:204`；加载该 scope 后仅追加 global：`services/memory/retrieval.py:457`。
5. Context takeover 开启后，MemoPlugin 不再自行注入实体卡：`plugins/memo/plugin.py:658`。

最终链路为：

```text
群聊图片纠正
  -> extractor 只能表达“关于用户”的事实
  -> 固定写入 user scope
  -> Context 接管 prompt
  -> 群聊只查 group pools + global
  -> 刚写入的 user 卡下一轮不可见
```

当前生产 `memo.write_policy_enabled=false`，因此实际走 legacy add-only；但 write-policy 路径同样丢弃 `group_id` 并固定 user scope，所以打开开关也不会修复这个断链。

#### 架构判断

这是数据模型与隐私契约问题，不是简单把 user 卡加入群聊查询即可解决：

- 当前卡没有足够的 `origin_group_id` / visibility 元数据，不能安全判断某张 user 卡是否可在某个群使用。
- 私聊事实、群 A 事实和同群其他用户的私有事实必须保持隔离。
- 图片身份学习需要稳定视觉对象键，例如图片哈希、感知哈希或角色识别样本引用，而不是把“这是某角色”降格成用户偏好。
- Character Pack 是独立管理能力，目前没有“用户纠正图片身份后持久学习到识别系统”的正式链路。

#### 测试缺口

- `tests/test_context_service.py:753` 明确断言群聊能看到 group 卡、看不到当前用户 user 卡；测试保护了粗粒度隔离，但也锁定当前断链。
- MemoExtractor 测试基本使用 `group_id=None`，没有“群聊提取 → 同一发言人在同群安全召回”的端到端测试。
- 缺少三类隐私负例：其他群员不可见、私聊卡不可直接进群、群 A 来源事实不可进入群 B。
- 缺少“图片纠正 → 视觉对象身份存储 → 下次同图/近似图识别”的契约测试。

#### 修复方向

先定义来源与可见性契约，再改检索：卡或 observation 需要来源 scope、来源群、主体、可见性和视觉对象引用；群聊检索应组合“当前群 + 当前发言人且允许在本群使用的事实 + global”。禁止直接把所有 user 卡并入群聊。

### P1-2 独立 `...` 来自 persona repair 后缺少二次可见性校验

#### 已观察事件

2026-07-18 18:13:56 的最终回复是独立 ASCII `...`。前置对话正在进行助手身份诱导。对应 `block_trace.db` 在 18:13:55 记录：

- `persona_drift_detector_action=repair`
- `persona_drift_detector_repaired=true`
- humanization score 为 1.0，issues 为空
- near-duplicate、sentinel strip/redact/block 均未命中

这表明纯省略号不是发送层随机丢字，而是 drift repair 生成后被整条后处理链视为合法回复。

#### 根因链

1. 主模型回复先经过 `_finalize_visible_reply()`；它会把空串、ASCII `...`、`☆`、`~` 视为不可见回复并 fallback/suppress：`services/llm/client.py:3449`。
2. 随后才调用 `_maybe_repair_persona_drift()`：正常无工具路径见 `services/llm/client.py:5671` 与 `services/llm/client.py:5709`；tool-exhausted 路径见 `services/llm/client.py:6086` 与 `services/llm/client.py:6120`。
3. 修复结果只执行 `_clean_reply()`、control token 清理和：

   ```python
   candidate = candidate.strip() or reply
   ```

   见 `services/llm/client.py:2283`。`"..."` 是 truthy，因此被接受为合法修复结果。
4. repair、guardrail 与 humanization 之后没有再次调用 `_finalize_visible_reply()`，回复直接进入 segmentation 和发送。

这是确定性顺序 bug：第一次 finalize 只保护主模型原始输出，没有保护后续 LLM 重写输出。

#### Unicode `…` 的相邻风险

当前 `_finalize_visible_reply()` 也未覆盖 Unicode `…` / `……` 或一般纯标点回复。分段器把 `…` 视为自然边界，合成复现可得到：

```text
natural_split("…然后呢")   -> ["…", "然后呢"]
natural_split("……然后呢") -> ["……", "然后呢"]
```

`services/llm/segmentation.py:318`、`services/llm/segmentation.py:483` 能说明边界行为。宽日志样本未发现独立 Unicode `…`，所以它应记录为代码级风险，而不是伪称已在线复现。

#### 测试缺口

- `tests/test_drift_detector_client.py:50` 只覆盖合法自然语言修复，没有 repair 返回 `...`、`…`、空白或纯标点。
- `tests/test_natural_split.py:159` 只保证 `……` 不从 run 中间拆成单个 `…`，没有句首省略号测试。
- `tests/test_streaming_segmenter.py:98` 同样只覆盖前面已有正文的双省略号。
- 发送层没有拒绝纯标点 segment 的最后一道防线。

#### 修复方向

所有会改变可见文本的 LLM 后处理必须统一回到一个最终出口：repair/rewrite 后重新执行 visible floor、control-token 清理、纯标点判定和 segmentation 前校验。发送层可再加防御，但不能替代上游顺序修复。

### P1-3 图片描述形成“视觉 prose → 用户正文 → Style 学习 → 再注入”的反馈环

#### 现象与频率边界

主样本启发式识别出 13 条图片描述式回复，问题高度集中在 2026-07-18 17:47—18:18 的图片人物问答。日志不足以证明“每一张图都描述”，但代码证明 VLM 成功时，描述文本会稳定进入主模型上下文；生产 Style 数据又证明这类描述已经被学习并批准。

#### 第一层：VLM 被要求生成可直接复述的成品说明

`services/media/vision.py:16` 明确要求描述：

- 图片展示什么。
- 传达什么情绪或态度。
- 适合在什么聊天场景使用。
- OCR 文字。

这不是紧凑的结构化视觉事实，而是一段主模型很容易照抄的“表情包使用说明”。

#### 第二层：Router 把内部视觉证据伪装成用户正文

- 直接图片被拼成 `«图片N: 描述»`：`kernel/router.py:1480`、`kernel/router.py:1501`。
- 引用图片被拼成 `[图片: 描述]`：`kernel/router.py:1397`。
- 同时消息仍附带 `image_ref`：`kernel/router.py:1517`。

纯图片消息因此在主模型眼中接近“用户发送了一段图片说明，同时附图”，而不是“系统提供了不可复述的内部视觉证据”。现有 visual grounding 只约束“这是谁”的人物指代，见 `services/llm/client.py:708`，没有处理纯图片的社交意图、是否需要描述或 sticker-only。

#### 第三层：Style 数据把系统视觉注释误标为人类语料

生产 `style.db` 有 12 条 approved expression，其中目标群的两条视觉表达习惯为：

- confidence 0.8：推荐或解释表情包时，先描述角色表情、动作和配字，再说明适用情境。
- confidence 0.7：表达疑惑时，用“咦…”加描述表情动作的句子。

至少一条 evidence 原文以 `«图片1: ...适合在聊天中...»` 开头，明显是系统视觉注释，却被记录为 `source_type=human`。这证明视觉注释与人类表达样本的来源边界已经失效。`services/style/store.py:870` 会选取 approved expression，`_expression_relevance()` 在 `services/style/store.py:1873` 根据当前会话文本排名；`plugins/style/plugin.py:169` 再把命中项作为“表达习惯参考”注入 prompt。

对问题会话的实际视觉描述 query 做只读排名复现，两条污染 expression 都被命中，相关度约为 0.0989 和 0.0686。由此形成确定反馈环：

```text
Qwen VL 生成详细图片说明
  -> Router 把说明当成用户文字
  -> Style 学习把视觉注释标成人类表达并批准
  -> 图片会话再次命中并注入“先描述图片”的风格
  -> 主模型继续详细说明图片
```

StylePlugin 还会把 Bot 回复作为 `weak_signal/neutral` 记录：`plugins/style/plugin.py:180`。这不是上述两条 `source_type=human` 误标的唯一必要条件，但会继续扩大“系统输出成为学习输入”的闭环风险。

#### 测试缺口

- 视觉测试主要断言描述、角色名和低置信候选必须进入 prompt，实际固化了 prose 注入方式：`tests/test_render_message_character_recognition.py:275`。
- `tests/test_build_group_messages.py:165` 只验证人物 grounding，没有纯图片接梗、情绪表情、梗图、明确求描述等意图分类。
- Style 缺少来源负例：`«图片...》`、`[图片: ...]`、系统/模型注释不得成为 `human` evidence。
- 缺少端到端测试：用户未要求描述时，最终回复不得复述画面说明；用户明确问“图里是什么”时才允许简洁描述。

#### 修复方向

1. 将 VLM 输出改为结构化内部 evidence，例如对象、动作、情绪、OCR、可信角色和用途标签，不生成可直接照抄的成品 prose。
2. Router 不再把 evidence 拼入用户 utterance；主模型 prompt 使用明确的系统证据块和“不可复述内部注释”契约。
3. 增加视觉意图分流：求描述/识人、图片附带问题、纯图接梗/情绪、sticker-only。
4. Style ingestion 按 provenance fail-closed；系统视觉标记、Bot 输出和派生文本不得标为 `human`。
5. 在新规则上线前禁用或清理上述两条污染 expression；数据操作需另行授权和备份，本轮未执行。

### P2-1 “置信度/阈值”是内部视觉诊断直接泄漏到可复述文本

#### 日志证据

2026-07-18 17:51:03 有一条实际发送回复包含“识别结果说可能是若叶睦但置信度……”的片段。其他日志中的 confidence 大多是内部 workflow/arbiter 遥测，未计为用户可见泄漏。

#### 根因链

1. `render_visual_evidence()` 直接渲染“未达到置信阈值”“低置信候选”和三位小数距离：`services/media/visual_evidence.py:69`。
2. `_ground_visual_content()` 又在主模型文本中写“标记为低置信候选”“低置信候选不得当作人物答案”：`services/llm/client.py:708`。
3. 主模型需要向用户解释不确定性时，自然会复述“识别结果、置信度、阈值”等元术语。
4. 可见回复清理和 guardrail 没有禁止这些内部诊断词。

现有测试反向保护了泄漏格式：

- `tests/test_render_message_character_recognition.py:289`、`:593` 要求 prompt 出现“置信阈值”“低置信候选”和具体小数。
- `tests/test_build_group_messages.py:179` 要求出现“低置信候选不得当作人物答案”。

#### 严重度判断

近期只确认 1 条用户可见命中，因此按 P2 排序；但上游文本边界是确定性的，应与 P1-3 视觉协议修复一起处理，而不是只在输出层替换“置信度”三个字。

#### 修复方向

保留内部结构化 confidence/distance 供决策，但模型可见约束只表达自然行为，例如“证据不足时说不太确定，不要猜”。Prompt 和最终回复都不应包含阈值、距离、confidence/置信度等实现术语；输出过滤仅作为第二道保险。

### P2-2 “被你发现/看穿/抓到”不是已批准口癖，而是跨轮短语防线缺失

#### 日志与数据证据

主样本命中 5 条变体：

- “被你看穿了”
- “被发现了嘛”
- “被你发现我收藏了这个表情呀”
- “被你抓到啦”
- “被你发现啦”

历史 Style 弱信号中还可见“被你发现”类 8 条、“被你看穿”类 3 条。排除结果：

- active persona source 没有这些固定句式。
- approved style expression、enabled style profile、learning normalizer catchphrase cluster 均无对应命中。
- 因此没有证据表明 Style/Catchphrase provider 正主动注入这组套话。

#### 根因链

1. Persona 只用自然语言要求“不连续堆口癖”，没有词族级执行约束。
2. `services/llm/dedup_gate.py:39` 只比较当前回复和紧邻上一条助手回复。
3. 默认 `ngram=5`、阈值 0.4；“被你发现”只有 4 个汉字，完整回复正文差异又很大，即使相邻也难命中，跨十几轮更完全不可见。
4. `services/humanization/scorer.py:20` 的模板词表只覆盖“作为一个AI、根据你的要求、我将”等，不识别角色化短开场复用。
5. 目标回复对应 metrics 为 1.0、near-duplicate hits/rewrite 均为 0，说明现有质量门把套话重复视为正常。

该问题在“机器人反复认错—用户纠正—机器人卖萌认错”的上下文中被明显放大，因此先修 P1-1 会降低频率，但不能替代短语级防线。

#### 测试缺口与修复方向

- 增加最近 N 条/TTL 窗口内的实际 outbound 开场和 2—4 字 n-gram 统计。
- 对“被你发现/让你发现/被你看穿/被你看出来/被你抓到”等 phrase family 达到频率阈值时重写，不做永久硬禁。
- Humanization scorer 增加角色化模板短语和开场重复指标。
- Catchphrase recent state 应记录模型实际发出的短语，不只记录 provider 注入的 cluster ID。
- 测试覆盖跨多轮、不同正文共享短开场，以及引用/复述用户原话的误报负例。

## 4. 建议修复顺序

### 第一批：恢复正确性与可见性下限

1. 设计视觉纠正的持久化模型和群聊隐私 scope 契约；先补正负端到端测试，再改 extractor/retrieval。
2. 把 persona repair、humanization rewrite 等后处理纳入统一最终出口，禁止任何纯标点回复绕过 visible floor。

### 第二批：切断视觉数据污染

3. 将视觉 evidence 从用户正文中分离，改为结构化、不可复述的内部证据块，并增加视觉意图分流。
4. 禁止视觉注释进入 Style 人类语料；备份后禁用/清理两条已批准污染 expression。
5. 同步移除 prompt 中的置信度、阈值和距离数值，更新当前反向保护泄漏格式的测试。

### 第三批：改善跨轮语言多样性

6. 增加会话窗口级 phrase-family 检测、humanization 指标和可观测性，控制短套话复用。

## 5. 建议验收门

| 验收面 | 最小通过条件 |
| --- | --- |
| 记忆正确性 | 同一用户在同群纠正图片身份后可安全召回；其他用户、其他群和私聊来源负例全部隔离 |
| 视觉学习 | 同图或近似图再次出现时能引用已纠正身份；存储内容不是“用户偏好被称呼为角色名” |
| 可见回复 | 主模型、persona repair、humanization rewrite 分别返回空白/`...`/`…`/纯标点时均不直接发送 |
| 分段 | `…正文`、`……正文`、多标点前缀不会生成纯标点独立 segment |
| 图片社交回复 | 未明确求描述时不复述画面说明；明确求描述/识人时才简洁回答 |
| Style 来源 | 视觉 marker、系统注释、Bot 回复不能以 `source_type=human` 进入 approved expression |
| 内部诊断 | Prompt 与最终回复都不出现 confidence、置信度、阈值、距离分数 |
| 语言重复 | 同一 phrase family 在窗口内超过阈值会重写；引用用户原话不误杀 |

## 6. 风险与开放问题

- 记忆修复最大的风险是隐私边界。没有来源/可见性元数据前，禁止用“群聊也查 user scope”作为快速修复。
- Style 两条污染数据已经在生产 DB 中；清理属于生产数据变更，需先做可信备份并另行授权。
- 现有日志缺少 privacy-safe 的“原始生成形态 → 修复后形态 → 分段形态”观测。建议只记录形态标签、字符数、纯标点标志、视觉意图和分段数，不记录正文。
- “图片总是描述”的精确比例尚不能由当前日志证明；反馈环和 prompt 注入已足以确认根因，不应等待更大规模隐私日志再开始修复。

## 7. 本轮交付边界

本报告只完成审计和优先级排序。未改源码、测试、配置、数据库或运行容器；未重启/重建 bot；未触碰、重启或重建 NapCat；未执行任何 QZone 操作。
