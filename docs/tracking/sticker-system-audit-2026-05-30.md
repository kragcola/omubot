# 表情包系统审计报告（为「表情包语义检索」立项）

> 状态：审计完成 | 日期：2026-05-30 | 类型：架构审计（Workflow B）
> 触发：弱回复机制设计中发现 STICKER_ONLY 无法实现——当前表情包不支持按语义检索。立项前先审计现状。
> 关联：[weak-reply-mechanism-design.md §2.6](weak-reply-mechanism-design.md)、[omubot-grayscale-issue17-pre-part0-sticker-identity.md](omubot-grayscale-issue17-pre-part0-sticker-identity.md)

## 0. 审计范围与方法

审计表情包全链路六层：存储 → 捕获 → 描述生成 → 检索/选择 → 发送 → 维护。证据来自直接读源码，不臆测。

| 层 | 文件 | 行数 |
|---|---|---|
| 存储 | `services/media/sticker_store.py` | 236 |
| 捕获 | `services/media/sticker_capture.py` | 136 |
| 描述生成 | `services/media/vision.py` | 97 |
| 选择决策 | `services/sticker/decision_provider.py` | 204 |
| 重排 | `services/sticker/fairmatch.py` | 41 |
| 工具 | `services/tools/sticker_tools.py` | 298 |
| 维护 | `plugins/dream/plugin.py`（sticker 段） | — |
| Prompt 注入 | `sticker_store.format_prompt_view()` | — |

## 1. 现状架构全景

```text
【收集侧】
群友发图 → is_sticker_like_segment() 判定（sub_type 1/7 或 summary 含"动画表情/表情/mface/sticker"）
    │
    ├─ 自动捕获：sticker_description_from_segment() 生成保守描述（"群友发送的动画表情"）
    │            → emit_emotion_tag() 调 VL 生成 usage_hint（一句话情绪场景）
    │
    └─ 管理员 save_sticker 工具：LLM 填 description + usage_hint，手动收录
                                         │
                                         ▼
【存储】StickerStore：目录 + index.json（非 SQLite）
    stk_{sha256[:8]} → {file, description, usage_hint, source, send_count, last_sent, created_at}
    SHA-256[:8] 去重；max_count=200；拒绝 GIF/未知格式

【发送侧 — 两条路径】
路径 A（工具）：LLM 主动调 send_sticker(sticker_id) → 按 ID 取文件 base64 发送
路径 B（humanization 自动）：_maybe_attach_sticker
    → StickerDecisionContext（4 类 ID 候选池：tool_call/kaomoji/frequent/thinker）
    → StickerDecisionProvider.decide()
        → fairmatch_rerank（按使用频次惩罚过曝，纯重排不筛选）
        → _send_probability（mood/affection 调概率）
        → 取 candidate_pool[0] 发送

【LLM 可见性】format_prompt_view() 把全库 description+usage_hint 注入 system prompt
    → LLM 靠"读全库文本"自己选 sticker_id

【维护】Dream agent 周期跑：list_stickers 看 send_count → delete_sticker 删 LRU/低质
```

<!-- SECTION-FINDINGS -->
## 2. 审计发现（按严重度排序）

### F1（核心缺口）：无任何语义检索能力——"按意图找表情"在架构上不存在

**结论**：从候选池构建到发送，全程是 **ID 集合操作 + 概率门**，没有一处把"当前要表达的意图/情绪"映射到"该发哪张表情"。

**证据**：

- 候选池来源（[decision_provider.py:59-65](../../services/sticker/decision_provider.py#L59)）是四个 **sticker_id 列表**拼接去重：`tool_call_candidates / kaomoji_candidates / frequent_candidates / thinker_candidates`。其中 `frequent_candidates` 是"有 usage_hint 的全部 sticker"（[client.py:1504](../../services/llm/client.py#L1504)），`thinker_candidates` 是"最近用过的"（`_recent_sticker_ids`）。**没有一个候选源是"语义匹配出来的"**。
- `fairmatch_rerank`（[fairmatch.py:29](../../services/sticker/fairmatch.py#L29)）只按使用频次给过曝 sticker 降权重排，**纯重排序，不做语义筛选**。
- `_rerank_strategy` 返回的 `emotion/intent/persona`（[decision_provider.py:146](../../services/sticker/decision_provider.py#L146)）**是死字符串标签，没有任何消费方**——grep 全仓 `rerank_strategy` 只被赋值/记录，无逻辑分支真正按它检索。是预留占位，未接通。
- `send_sticker` 工具签名只收 `sticker_id`（[sticker_tools.py:203-213](../../services/tools/sticker_tools.py#L203)），无 `by_intent`/`query` 入口。

**当前"选对表情"靠什么**：唯一的语义匹配发生在 **LLM 自己读 prompt**——`format_prompt_view()`（[sticker_store.py:215](../../services/media/sticker_store.py#L215)）把全库 description+usage_hint 文本塞进 system prompt，LLM 凭文本理解挑 ID 调 `send_sticker`。即：**语义检索被外包给主 LLM 的上下文阅读，没有独立的检索机制**。

**对 STICKER_ONLY 的影响**：弱回复要的"晚安场景→检索挥手类表情"，在路径 B（自动）里完全无法表达（它只会从频繁池取第一个）；在路径 A 里依赖主 LLM 在 prompt 里读到合适的，但弱回复恰恰要**绕过主 LLM**（instruction_gate 同款直发）——所以两条路都不通。这是立项的根本动因。

### F2（核心缺口）：无法识别表情包自带文字（无 OCR）

**结论**：表情包上的文字（梗图配字、"早安""绝绝子"等）**完全不进入描述**，VL 只描述画面情绪。

**证据**：

- 描述生成有三条来源，无一含 OCR：
  1. `sticker_description_from_segment`（[sticker_capture.py:58](../../services/media/sticker_capture.py#L58)）：纯靠 OneBot summary，输出"群友发送的动画表情"这类**零信息**占位。
  2. `_EMOTION_TAG_PROMPT`（[sticker_capture.py:12](../../services/media/sticker_capture.py#L12)）：明确要"概括情绪/场景"，**不要求读图上文字**。
  3. `_STICKER_DESCRIBE_PROMPT`（[vision.py:16](../../services/media/vision.py#L16)）：要"描述内容/情绪/用法"，同样**未要求 OCR**。
- VL 是 Qwen-VL（[vision.py:24](../../services/media/vision.py#L24)），理论上有 OCR 能力，但 **prompt 没让它读字**，且 `max_tokens=128`、描述被压成"一句话"，文字信息即便偶然带出也会被 `normalize_emotion_tag` 截断到 32 字（[sticker_capture.py:23](../../services/media/sticker_capture.py#L23)）。

**后果**：大量表情包的核心语义在文字上（"打工人""我emo了""晚安"字样）。当前 bot 对这类表情**只知道"一个人物表情"，不知道它在说什么字**——既影响接收理解，也使任何基于文字的检索不可能。

### F3（架构债）：存储是 index.json 全量内存 dict，无法承载向量检索

**结论**：`StickerStore` 用单个 `index.json`（[sticker_store.py:75-94](../../services/media/sticker_store.py#L75)）全量读写，与语义检索需要的向量索引不兼容。

**证据**：

- 每次 `add/update/remove/record_send` 都 `_save_index()` 全量重写整个 JSON（[sticker_store.py:86](../../services/media/sticker_store.py#L86)）。max_count=200 时尚可，但**加入 embedding 向量后单文件会膨胀**（200×768×4B≈600KB JSON base64，且每次 record_send 全量重写）。
- 无任何索引结构——`get` 按 dict key，`lookup_by_hash` 按 hash，**无相似度查询接口**。
- 对比：issue17 pre-part0 已规划 `character_recognition.db`（SQLite + embedding BLOB）。表情包语义检索若引入 embedding，应走同款 SQLite，而非塞进 index.json。

### F4（设计耦合）：rerank_strategy 占位符制造"已支持语义"的假象

**结论**：`StickerRerankStrategy = Literal["none","emotion","intent","persona"]`（[decision_provider.py:15](../../services/sticker/decision_provider.py#L15)）+ `_rerank_strategy()` 看起来像语义检索，实则空转。

**证据**：`_rerank_strategy` 根据 mood/affection 返回标签，但返回值只进 `StickerDecision.rerank_strategy` 字段被记录，**全仓无消费方按它改变检索行为**（grep 确认）。这是个**埋了一半的脚手架**——与 `ResponseClass` 四档同型（定义了未接通）。立项时应明确：要么接通它，要么移除以免误导。

### F5（观察）：两条发送路径语义能力不对等

**结论**：路径 A（LLM 工具）有"伪语义"（靠 prompt 文本），路径 B（humanization 自动）**纯频率驱动、零语义**。

**证据**：路径 B 的 `decide()` 从不读 description/usage_hint，只看 ID + 使用次数 + mood 概率。即 humanization 自动贴的表情**与当前话题语义无关**，纯粹"挑个最近/不过曝的"。这解释了为何自动表情有时"驴唇不对马嘴"。

## 3. 立项建议：「表情包语义检索」前置项

### 3.1 范围（解决 F1 + F2）

两个核心能力，可分两阶段：

#### 阶段 1 — OCR 入库（解决 F2，独立可上线）

- 改 `_STICKER_DESCRIBE_PROMPT` / `_EMOTION_TAG_PROMPT`：显式要求 VL 读出图上文字（Qwen-VL 已具 OCR 能力，仅 prompt 缺失）。
- `StickerStore` index 加 `ocr_text` 字段（additive，旧条目空）。
- 提升 `max_tokens` / 放宽 `normalize_emotion_tag` 截断，给文字留空间。
- 收益：表情包语义骤增（"晚安"字样的表情终于"知道"自己在说晚安），且**不依赖向量检索即可改善 LLM prompt 选择质量**（F1 的伪语义路径也受益）。

#### 阶段 2 — 向量语义检索（解决 F1）

- 给 description+usage_hint+ocr_text 生成 embedding，存 SQLite（解决 F3，复用 issue17 pre-part0 的 `character_recognition.db` 同款 SQLite+BLOB 模式，或独立 `sticker_embeddings.db`）。
- 新增 `StickerStore.search_by_intent(query, top_k)` → 相似度检索返回 sticker_id 列表。
- `send_sticker` 加 `by_intent` 可选入口；`StickerDecisionContext` 加 `semantic_candidates` 候选源；接通 F4 的 `rerank_strategy` 占位（或移除）。
- 弱回复 STICKER_ONLY 在此之后接入：closing/companion 意图 → `search_by_intent("挥手告别")` → 候选池。

### 3.2 与 issue17 pre-part0 的边界（避免混淆）

| | issue17 pre-part0 | 本前置项 |
|---|---|---|
| 方向 | 接收侧：识别收到的图里是谁（角色） | 发送侧：按意图检索该发哪张 |
| 技术 | CCIP/AnimeTrace 视觉角色嵌入 | 文本 embedding（描述+OCR）相似度 |
| 存储 | character_recognition.db | sticker_embeddings（可同库可独立） |
| 落地 | 未落地 | 未立项（本报告为立项依据） |

两者**正交**：pre-part0 让 bot 知道"收到的图是凤笑梦"，本项让 bot 知道"想说晚安该发哪张"。可独立推进，建议**阶段 1（OCR）优先**——成本最低、立竿见影、且为阶段 2 喂数据。

### 3.3 阶段 1 的最小改动面（不含实现）

| 文件 | 改动 | 风险 |
|---|---|---|
| `services/media/vision.py` | prompt 加 OCR 要求 | 低（纯 prompt） |
| `services/media/sticker_capture.py` | emotion_tag prompt 加文字、放宽截断 | 低 |
| `services/media/sticker_store.py` | index 加 `ocr_text` 字段（additive） | 低（旧条目兼容） |
| `plugins/dream/plugin.py` | 回填存量表情 OCR（一次性） | 中（VL 调用量） |

## 4. 审计结论

| # | 发现 | 严重度 | 立项归属 |
|---|---|---|---|
| F1 | 无语义检索能力，靠主 LLM 读 prompt 外包 | 核心缺口 | 阶段 2 |
| F2 | 无 OCR，图上文字完全丢失 | 核心缺口 | 阶段 1 |
| F3 | index.json 全量重写，无法承载向量 | 架构债 | 阶段 2（SQLite 迁移） |
| F4 | rerank_strategy 占位符空转，制造假象 | 设计债 | 阶段 2（接通或移除） |
| F5 | 两条发送路径语义能力不对等 | 观察 | 阶段 2 自然收敛 |

**核心结论**：用户指出的两个问题（无语义检索、无法识别自带文字）都**属实且互相关联**——F2（无 OCR）是 F1（无语义检索）的上游成因之一：连图上的字都没读进来，语义检索就缺了最关键的特征。建议**阶段 1（OCR 入库）先行**，低成本、独立可上线、且为阶段 2 的向量检索喂数据；阶段 2（向量检索 + SQLite 迁移）再解决 F1/F3/F4。弱回复 STICKER_ONLY 挂在阶段 2 之后。

### 不确定 / 待下一步核实

- VL（Qwen-VL）实际 OCR 准确率未实测——阶段 1 立项后需用真实群表情样本验证。
- 向量检索的 embedding 来源（本地模型 vs API）未定——影响成本与延迟，阶段 2 设计时调研（可参考 pre-part0 的 CCIP 本地 ONNX 思路）。
- index.json → SQLite 迁移是否需要保留 index.json 兼容期，待阶段 2 设计。

## 5. 前沿技术参考（论文 + 成熟项目，非臆想）

### 5.1 直接对口：StickerCLIP / Sticker820K（Tencent AI Lab，arXiv 2306.06870）

**最相关的工作**——专门做 sticker 文搜图检索的 benchmark，结论直接指导本立项：

- **关键数据**：zero-shot CLIP 在 sticker 上检索极差（ViT-B 文搜图 Mean Recall 仅 **16.7**），fine-tune 后（StickerCLIP）飙到 **82.7**。原因：sticker 与自然图差异大（手绘抽象、强情绪、**带文字**），通用 CLIP 不适配。
- **标注 schema**：每张 sticker 标注 `描述 + OCR光学字符 + 情绪标签(30类) + 风格分类`——**OCR 是其四大特征之一**，且论文明确"meme 上的文字携带重要含义，同图不同字含义迥异"。**直接印证审计 F2**：缺 OCR 是 sticker 理解的硬伤。
- **两种范式**：① StickerCLIP（fine-tune CLIP 双塔，文图对齐）；② StickerLLM（冻结 LLM + 加 `<ret>` 特殊 token，prompt tuning 让 LLM 按指令检索，**仅训新 token 的 embedding，不动 LLM 本体**，3M 可训参数）。后者思路与 omubot「主 LLM + 工具」架构高度契合。
- 项目页：[github.com/sijeh/Sticker820K](https://github.com/sijeh/Sticker820K)。

> **对本立项的启示**：① 通用 CLIP 零样本在 sticker 上不可靠，**纯视觉 embedding 检索效果存疑**——而 omubot 已有 VL 生成的**文字描述**，走"描述文本 embedding"（text-to-text）反而可能比"图 embedding"更稳，且复用现有管线；② OCR 是 sticker 检索的标配特征，验证阶段 1 优先级；③ StickerLLM 的 `<ret>` token 范式是阶段 2 的可选高级路线（但需训练，重）。

### 5.2 对话级 sticker 选择：STICKERCONV（arXiv 2402.01679）+ GIF reply（EMNLP 2021）

- **STICKERCONV / PEGS**：多模态共情对话框架，12.9K 对话 + 5.8K sticker，证明"按对话上下文情绪选 sticker"是成立的研究方向，且强调**共情/情绪匹配**（与 omubot 的 mood 系统天然契合）。
- **PEPE（EMNLP 2021）**：1.56M 文-gif 对话对，选 gif 回复，**真实用户 A/B 测试证明检索式 gif 回复显著更受欢迎**——为"弱回复用表情"提供了用户接受度证据。

### 5.3 成熟工程项目（检索基础设施，可直接复用）

| 项目 | 能力 | 对 omubot 的适配 |
|---|---|---|
| [jina-ai/clip-as-service](https://github.com/jina-ai/clip-as-service) | CLIP embedding/检索/排序服务 | 重，含独立服务，不符 omubot 单体架构 |
| [rom1504/clip-retrieval](https://github.com/rom1504/clip-retrieval) | 算 embedding + 建检索系统 | 参考其 embedding+索引流程 |
| ChineseCLIP（StickerCLIP 的基座） | 中文图文对齐 ONNX | 中文 sticker 适配，本地 ONNX（同 pre-part0 CCIP 部署模式） |
| sqlite-vec / FAISS | 向量索引 | sqlite-vec 与 omubot 现有 SQLite 栈最契合，无新服务 |

### 5.4 技术路线收敛建议

综合论文与现状，**阶段 2 推荐"描述文本 embedding + sqlite-vec"而非"图 embedding"**：

- omubot 已有 VL 生成的描述（加 OCR 后语义更全），文本 embedding 模型轻、中文成熟（bge/m3e 等），text-to-text 检索比 StickerCLIP 揭示的"通用图 embedding 零样本"更可靠。
- sqlite-vec 复用现有 SQLite 栈，不引入独立向量服务（符合 §6 与 pre-part0 共用存储的方向）。
- StickerCLIP/StickerLLM 的图 embedding 路线作为**后续增强备选**（需 fine-tune 或本地 ONNX，重），不在首期。

## 6. 与 issue17 pre-part0 的兼容规划（防两套工作流）

### 6.1 碰撞点：两个项目改的是同一段代码

issue17 pre-part0（接收侧角色识别）与本前置项（发送侧语义检索）**都要改 [kernel/router.py:733-768](../../kernel/router.py#L733) 这同一段 vision 管线**，且都涉及：调 VL、写缓存、组装图片描述文本。若各自独立落地，必然产生三处重复/冲突：

| 重复风险 | pre-part0 计划 | 本前置项需要 | 冲突 |
|---|---|---|---|
| **VL 调用** | describe_emotion（情绪）| describe + **OCR**（文字）| 同一张图被 VL 调两次 |
| **缓存** | 新建 `image_recognition_cache`（SQLite，替 desc_cache）| 入库描述需含 OCR 字段 | 两套缓存 schema，或 desc_cache 被改两次 |
| **描述组装** | `«角色名 + 情绪»` | 描述需进 sticker_store 供检索 | 两处各自拼描述，格式分叉 |

### 6.2 共用接缝设计（一次 VL、一份描述、分用途消费）

**核心原则：表情包这一个载体，VL 只调一次，产出一份"富描述"，接收侧与发送侧各取所需。**

```text
收到表情包/图片（router.py vision 管线，统一改造点）
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ 统一识别管线（pre-part0 与本项共用，VL 仅调一次）          │
│                                                          │
│  ① hash → 统一缓存查询（SQLite，合并 image_recognition_   │
│     cache + sticker 描述，不再各搭一套）                   │
│  ② miss → 并行：                                          │
│       ├ CCIP/AnimeTrace 角色识别（pre-part0）             │
│       └ VL 富描述：情绪 + 内容 + **OCR 文字**（合并 prompt）│
│  ③ 合并产出「富描述记录」：                                │
│       {character_name, emotion, content, ocr_text}        │
└────────────────────────┬─────────────────────────────────┘
                         │
          ┌──────────────┴──────────────┐
          ▼                             ▼
   【接收侧 pre-part0】            【发送侧 本前置项】
   组装 «角色名+情绪» 给 LLM      富描述（含 OCR）入 sticker_store
   理解收到的图                   → 阶段2 生成 embedding 供检索
```

### 6.3 落地约定（写给两个项目的共同约束）

1. **VL prompt 合并**：不要 pre-part0 调一次"情绪"、本项再调一次"OCR"。改造 VL prompt 一次产出 `{情绪, 内容, OCR文字}` 结构化富描述。`_STICKER_DESCRIBE_PROMPT` / `_EMOTION_TAG_PROMPT` 由两项目协商后**统一改一次**。
2. **缓存合一**：pre-part0 的 `image_recognition_cache` 与本项的 sticker 描述存储**共用同一 SQLite 库**（pre-part0 §四已规划 `character_recognition.db`，本项的 `ocr_text`/embedding 作为该库的扩展列或关联表，不另起 index.json 之外的第二个 DB）。`desc_cache` 内存层保留为两者共用热缓存。
3. **描述 schema 统一**：定义一个富描述结构 `{character_name, emotion, content_desc, ocr_text}`，接收侧取 `character_name+emotion` 拼 LLM 可见文本，发送侧取 `content_desc+ocr_text+emotion` 做检索特征。**单一数据源，两处消费**。
4. **落地顺序协调**：两项目都未落地。建议——先落本项**阶段 1（OCR 入库）**，因为它只改 VL prompt + 加字段，**最轻且为 pre-part0 的"富描述"也补上 OCR**；pre-part0 的角色识别（CCIP）与本项阶段 2（向量检索）可并行或先后，但**必须共用 §6.2 的统一管线骨架**，由先落地的一方搭骨架、后者复用。

### 6.4 给 pre-part0 文档的反向提示

pre-part0 文档（§九集成点）目前只规划了"角色识别 + 情绪描述"，**未含 OCR**。建议在其实施时**纳入 OCR 字段**（边际成本极低——VL 调用已在，prompt 加一句即可），避免本项再回头改一次它刚建好的缓存 schema。这条已在本报告记录，作为两项目协调依据。


