# 表情包语义检索 — 全阶段实施方案（OCR 入库 → 检索 → 弱回复接入）

> 状态：阶段 1 已落地（2026-05-30，待 rebuild）｜阶段 2A 已落地代码（2026-05-30，待 rebuild）｜阶段 2B/3 待办 | 类型：实施规划（全阶段）
> 上游：[sticker-system-audit-2026-05-30.md](sticker-system-audit-2026-05-30.md)（F1-F5 + §5 前沿 + §6 pre-part0 兼容）
> 三阶段：阶段 1 OCR 入库（解 F2）→ 阶段 2 语义检索（解 F1/F3/F4）→ 阶段 3 弱回复 STICKER_ONLY 接入。

## 0. 目标与全局约束

**总目标**：让 bot 能"按意图找表情"——`search_by_intent("挥手告别")` 返回合适的 sticker，最终服务弱回复 STICKER_ONLY。

**决定性约束（已核准现状）**：

- **全仓零向量依赖**：`pyproject.toml` 无 faiss/sqlite-vec/sentence-transformers/torch；`EmbeddingSimilarityProvider` 直接 `raise "not installed/enabled"`（[similarity.py:62](../../services/similarity.py#L62)）。
- **现有检索栈**：知识库用 `KeywordBM25Retriever`（[knowledge/retrievers.py:95](../../services/knowledge/retrievers.py#L95)，纯 BM25 倒排）；context 用 RRF 融合（[context/service.py:262](../../services/context/service.py#L262)）；相似度只有 ngram（`NgramSimilarityProvider`）。
- **含义**：sticker 语义检索若上 embedding = **全仓第一次引入 embedding 栈**（重）。故阶段 2 分两步走——**先复用现成 BM25 验证检索范式（零新依赖），再按需升级 embedding**。这是本方案相对审计 §5.4 的修正：不一上来就 embedding，先 BM25 兜底。

**阶段依赖链**：阶段 1 产出富描述（含 OCR）→ 阶段 2 拿富描述做检索 → 阶段 3 拿检索接弱回复。**每阶段独立可上线、独立有价值**。

---

## 阶段 1 — OCR 入库（解 F2）

### 1.0 为什么先行

- 解 F2（无 OCR），且 OCR 是 F1 检索的上游特征——审计 §5 StickerCLIP 证 OCR 是 sticker 检索标配。
- 最轻：改 VL prompt + 加一个字段，无新依赖、无新服务、无 DB 迁移。
- 独立可上线：自身就改善"LLM 读 prompt 选表情"质量（F1 伪语义路径受益），且为 pre-part0 补 OCR（审计 §6.4）。

### 1.1 现状接缝（已核准）

| 接缝 | 位置 | 现状 |
|---|---|---|
| VL 描述 prompt | [vision.py:16](../../services/media/vision.py#L16) `_STICKER_DESCRIBE_PROMPT` | 要情绪/内容/用法，**不要 OCR**；`max_tokens=128` 硬编码 |
| emotion_tag prompt | [sticker_capture.py:12](../../services/media/sticker_capture.py#L12) `_EMOTION_TAG_PROMPT` | 只要情绪场景一句话；`normalize_emotion_tag` 截 32 字 |
| 入库 | [sticker_store.py:126](../../services/media/sticker_store.py#L126) `add()` | 写 `{description, usage_hint, ...}`，**无 ocr 字段** |
| 更新 | [sticker_store.py:182](../../services/media/sticker_store.py#L182) `update()` | 只改 description/usage_hint |
| prompt 注入 | [sticker_store.py:215](../../services/media/sticker_store.py#L215) `format_prompt_view()` | 注入 description+usage_hint，**无 OCR** |
| emit_emotion_tag 调用 | sticker plugin ×2、history_loader ×1 | 自动捕获时补 usage_hint |
| 回填入口 | [dream/plugin.py:291](../../plugins/dream/plugin.py#L291) | Dream 周期整理，无 OCR 回填 |
| 测试 | `test_sticker_store.py` / `test_sticker_capture_emotion.py` | 可扩展，非新建 |

<!-- IMPL-SECTIONS -->

### 1.2 改动清单（按依赖顺序）

#### 1.2.1 存储层：sticker_store 加 `ocr_text` 字段（additive）

[services/media/sticker_store.py](../../services/media/sticker_store.py)

- `add()`：entry dict 增 `"ocr_text": ocr_text`（新增可选入参 `ocr_text: str = ""`）。旧调用不传 → 默认空，向后兼容。
- `update()`：增可选入参 `ocr_text: str | None = None`，与 description/usage_hint 同款条件更新。
- `format_prompt_view()`：若 `ocr_text` 非空，渲染进 prompt 行——`«表情包:{id}» [{fmt}] {description} | {usage_hint} | 图上文字：{ocr_text}`。空则不加该段（不污染无文字表情）。
- **缓存稳定性**：`format_prompt_view` 注释已声明只含稳定字段（[sticker_store.py:218](../../services/media/sticker_store.py#L218)）。`ocr_text` 是稳定字段（不随发送变），可安全加入，不破 prompt cache。

#### 1.2.2 VL 层：富描述一次产出 `{内容/情绪, OCR}`（对齐 pre-part0 §6.2）

[services/media/vision.py](../../services/media/vision.py)

- 改 `_STICKER_DESCRIBE_PROMPT`：增一句"如果图上有文字，请在描述末尾用固定格式附上：`图上文字：xxx`（无文字则省略）"。**一次 VL 调用同时拿描述 + OCR**，不新增调用（审计 §6.3 约定①）。
- `max_tokens` 128 → 提到 192-256（给 OCR 文字留空间）。建议提为 `VisionConfig` 可配（`vision.describe_max_tokens`，默认 200），而非硬编码。
- 新增解析助手 `split_desc_and_ocr(raw: str) -> tuple[str, str]`：从 VL 返回里按"图上文字："分隔符切出 description 与 ocr_text。放 `sticker_capture.py`（与其他解析助手同处）。

> **pre-part0 兼容**：这个"富描述 prompt"就是 §6.2 的共用产出。pre-part0 落地时复用同一 prompt + `split_*`，再叠加角色名，不另写 VL 调用。

#### 1.2.3 捕获层：emotion_tag 流程纳入 OCR

[services/media/sticker_capture.py](../../services/media/sticker_capture.py)

- `_EMOTION_TAG_PROMPT`：保持"情绪场景"职责不变（usage_hint 仍是情绪），**但 OCR 走 2.2 的主描述 prompt**，不在 emotion_tag 里重复要文字。
- `emit_emotion_tag()`：调 VL 后用 `split_desc_and_ocr` 拆出 ocr，若非空则 `sticker_store.update(sticker_id, ocr_text=ocr)`。`normalize_emotion_tag` 的 32 字截断**只作用于 usage_hint**，OCR 单独存（可放宽到 64 字，梗图文字可能较长）。
- `dry_run` 路径同步返回 ocr 供测试断言。

#### 1.2.4 入库工具：save_sticker 透传 OCR（可选增强）

[services/tools/sticker_tools.py](../../services/tools/sticker_tools.py)

- `SaveStickerTool`：管理员手动收录时，LLM 已能从 prompt 看到图，可选让 LLM 填 `ocr_text`（parameters 加可选字段）。**非必须**——自动捕获已覆盖 OCR，手动收录缺省走 VL 富描述补。首期可不改，标注为可选。

#### 1.2.5 回填：Dream agent 给存量表情补 OCR（一次性 + 持续）

[plugins/dream/plugin.py:291](../../plugins/dream/plugin.py#L291)

- Dream 周期整理段增一句：对 `ocr_text` 缺失（旧条目）的表情，调 VL 富描述补 OCR（复用 `emit_emotion_tag` 的 overwrite 路径，或新增 `backfill_ocr`）。
- 限流：每轮 Dream 只补 N 张（如 10），避免一次性 VL 调用量爆炸。存量 200 张上限，几轮 Dream 补完。
- 这是 pre-part0 §四"持久化缓存回填"的同类操作——若 pre-part0 先落地建了回填骨架，本项复用。

### 1.3 数据结构变更

```python
# services/media/sticker_store.py — index entry 增字段（additive）
{
    "file": ..., "description": ..., "usage_hint": ...,
    "ocr_text": "",          # ← 新增：图上文字，旧条目默认空
    "source": ..., "send_count": ..., "last_sent": ..., "created_at": ...,
}

# kernel/config.py — VisionConfig 增（可选，给 OCR 留 token 空间）
class VisionConfig(BaseModel):
    ...
    describe_max_tokens: int = 200   # ← 新增，替 vision.py 硬编码的 128
```

### 1.4 测试清单（D2/D4，扩展现有测试）

| 用例 | 文件 | 断言 |
|---|---|---|
| `split_desc_and_ocr("...图上文字：晚安")` | test_sticker_capture_emotion | 切出 desc + "晚安" |
| `split_desc_and_ocr("无文字描述")` | 同 | ocr 为 "" |
| `add(ocr_text="晚安")` 后 `get` 含该字段 | test_sticker_store | ✓ |
| `add()` 不传 ocr_text（旧调用） | 同 | 默认 ""，不报错（向后兼容） |
| `update(ocr_text="x")` 单独更新 | 同 | description 不变，ocr 变 |
| `format_prompt_view` 含 ocr 行 | 同 | 有 ocr 时渲染"图上文字："；空时不渲染 |
| `emit_emotion_tag` VL 返回带 OCR → 写入 | test_sticker_capture_emotion | sticker_store.ocr_text 被更新 |
| `emit_emotion_tag` VL 无 OCR | 同 | ocr_text 保持空，不误写 |
| prompt cache 稳定性 | — | format_prompt_view 对同一库两次调用输出一致 |
| 回填：Dream 对缺 ocr 的旧条目补 | test_dream | 限流 N 张，补后字段非空 |

### 1.5 验证 & 影响

- `uv run pytest tests/test_sticker_store.py tests/test_sticker_capture_emotion.py tests/test_dream.py -v`（先确认改前后）→ 全量 `pytest`（D5 先 pkill）→ ruff + pyright。
- **D1 同模式扫描**：grep 所有读 `entry.get("description")` / `format_prompt_view` 的位点，确认加 ocr 字段不破其他消费方（router vision 管线、prompt_builder）。
- **影响面**：改 .py（vision/sticker_store/sticker_capture/config/dream）→ 需 rebuild bot。index.json 旧条目无 ocr_text 字段，`entry.get("ocr_text", "")` 兜底，**无需迁移**。VL 调用量：新图不变（同一次调用多拿 OCR），回填阶段每轮 Dream +N 张。
- **回滚**：字段 additive，`format_prompt_view` 不渲染空 ocr → 回滚只需 `git checkout` 这几个文件，旧 index.json 自然兼容（多出的 ocr_text 字段被忽略）。

### 1.6 落地顺序

1. 1.2.1 存储字段 + 1.2.2 VL 富描述 + `split_desc_and_ocr`（核心，一批）。
2. 1.2.3 捕获层接通 + 测试（验证新图能入 OCR）。
3. 1.2.5 Dream 回填（存量补齐，限流）。
4. 1.2.4 save_sticker 透传（可选，可延后）。
5. rebuild + 灰度观察：看新表情的 OCR 是否准、prompt 里"图上文字"是否改善 LLM 选表情。

---

## 阶段 2 — 语义检索（解 F1 / F3 / F4）

### 2.0 前提与决策

阶段 1 落地后，每个 sticker 记录已含富描述 `{description, usage_hint, ocr_text}`。阶段 2 在此之上建"按意图检索"。

**核心决策（基于零向量依赖现状）——两步走，先 BM25 后 embedding**：

omubot 全仓无 embedding 栈，知识库现成 `KeywordBM25Retriever` 可直接复用。故阶段 2 拆两子步，**2A 零新依赖先上线验证范式，2B 按需升级**：

| | 2A：BM25 检索（先） | 2B：embedding 检索（按需升级） |
|---|---|---|
| 依赖 | 零新增（复用 `KeywordBM25Retriever`） | 引入 embedding 模型（全仓首次） |
| 检索 | 富描述分词倒排，query 关键词匹配 | 富描述向量 + query 向量余弦 |
| 召回质量 | 中（同义词/语义泛化弱） | 高（语义泛化） |
| 适用 | "晚安"→含"晚安"字样/描述的表情 | "告别"→召回"拜拜/再见/挥手"近义 |
| 成本 | 即时 | 模型加载 ~150-300MB + 索引 |
| 上线门槛 | 低，立即可验证 | 高，需调研模型+部署 |

**判据**：先上 2A，用真实弱回复场景看 BM25 召回够不够；若"告别↔挥手"这类近义召回明显不足，再上 2B。审计 §5.4 收敛的"text-to-text embedding"在 2B 落地，**不在 2A 跳过验证直接上**。

### 2.1 子步 2A — BM25 富描述检索（零新依赖）

#### 2A.1 存储升级：index.json → SQLite（解 F3）

[services/media/sticker_store.py](../../services/media/sticker_store.py)

- 现状 `index.json` 全量重写无法承载检索索引（审计 F3）。迁移到 SQLite——与 pre-part0 `character_recognition.db` **同库或关联表**（审计 §6.2 缓存合一约定）。
- 表 `stickers(sticker_id PK, file, description, usage_hint, ocr_text, source, send_count, last_sent, created_at)`。
- **迁移兼容**：启动时若检测到旧 `index.json` 且 SQLite 空 → 一次性导入；保留 index.json 一个版本周期作回退，之后删。
- `StickerStore` 所有读写方法（`add/get/update/remove/record_send/list_all`）改走 SQLite，对外接口签名不变（调用方零改动）。

#### 2A.2 检索接口：search_by_intent（复用 BM25）

[services/media/sticker_store.py](../../services/media/sticker_store.py) + 复用 [knowledge/retrievers.py:95](../../services/knowledge/retrievers.py#L95)

- 新增 `StickerStore.search_by_intent(query: str, top_k: int = 5) -> list[str]`：把每个 sticker 的 `description + usage_hint + ocr_text` 拼成"文档"，喂 `KeywordBM25Retriever`（或其轻量复刻），query 分词打分返回 top-k sticker_id。
- 索引在 `add/update/remove` 时增量维护（或检索时惰性重建——库仅 200 张，重建成本可忽略）。

#### 2A.3 发送入口：send_sticker 加 by_intent（解 F1 入口）

[services/tools/sticker_tools.py:203](../../services/tools/sticker_tools.py#L203)

- `SendStickerTool.parameters` 加可选 `intent: str`：传 `sticker_id` 则按 ID 发（现状）；传 `intent` 则 `search_by_intent(intent)` 取 top-1 发。
- `StickerDecisionContext` 加 `semantic_candidates` 候选源（[decision_provider.py:25](../../services/sticker/decision_provider.py#L25)）；`_trigger_source` 增 `"semantic"` 优先级。**接通 F4** 的 `rerank_strategy` 占位——`intent` 候选走 `intent` 策略真正消费，不再空转。

#### 2A.4 落地记录（2026-05-30，待 rebuild）

实际实现与计划的差异 / 落地证据：

- **存储层重写**：[sticker_store.py](../../services/media/sticker_store.py) 由 `index.json` 全量重写改为 SQLite（`storage/stickers/stickers.db`，WAL + `close_with_checkpoint_sync`，对齐 [knowledge/store.py](../../services/knowledge/store.py) 的 slang.db 防腐范式）。`add/get/update/remove/record_send/list_all` 接口签名不变；内存镜像服务读（库 ≤200）。`close()` 接入 [chat/plugin.py:on_shutdown](../../plugins/chat/plugin.py)。
- **迁移**：以「SQLite 表为空」而非「index.json 存在」为触发键 → 幂等。旧 `index.json` 迁移后**冻结**作回退快照，不再回写（新增表情只进 SQLite）。
- **检索**：`search_by_intent(query, top_k=5)` 复用 `KeywordBM25Retriever`（desc+usage_hint+ocr_text 拼文档），dirty-flag 惰性重建（mutate 时置脏，search 时重建）。零新依赖。
- **发送入口**：[sticker_tools.py](../../services/tools/sticker_tools.py) `SendStickerTool` 去掉 `required`，加可选 `intent`；`sticker_id` 优先，否则 `intent → search_by_intent top-1`，两路并存。这是 F1 的**真实 LLM 可调用入口**。
- **F4 / decision_provider「semantic」触发：本期未做（修正 2A.3）**。`StickerDecision.rerank_strategy` 全仓**零下游消费者**（[client.py:1520](../../services/llm/client.py#L1520) 仅发 `candidate_pool[0]`，不读 strategy），现在加 `"semantic"` 触发 + strategy 消费纯属推测性死码。F4 真正落地点是阶段 3 弱回复接 `search_by_intent`，届时再补 decision_provider。
- **D1 同模式扫描发现两处**：
  - **跨线程崩溃（本次新引入并修复）**：silent sticker learning 经 `asyncio.to_thread` 调 `store.add`（[sticker/plugin.py:189](../../plugins/sticker/plugin.py#L189)）。JSON 版无线程亲和，SQLite 默认 `check_same_thread=True` 会抛 `ProgrammingError`。修：`check_same_thread=False` + `threading.RLock` 串行化所有 DB+镜像访问。回归测试 `test_add_works_from_worker_thread`。
  - **OCR 回填三态保留（阶段 1 联动）**：Dream OCR 回填以 `"ocr_text" not in entry` 判「未做过 OCR」。SQLite 列默认 `''` 会让 key 恒存在 → 整个存量库永不回填。修：`ocr_text` 列设 NULL-able，迁移时**缺 key→NULL（key 缺席）**、有值（含 `''`）→保留；`_row_to_entry` NULL→省略 key。三态：NULL/缺席=未尝试、`''`=尝试无字、有字=OCR 文本。
  - **预存 admin bug（顺手修）**：[stickers.py:91](../../admin/routes/api/stickers.py#L91) `store.update(desc=, hint=)` 关键字与 `StickerStore.update(description=, usage_hint=)` 不符，每次后台编辑表情必 `TypeError`。改对齐签名。同时 list/detail 响应补 `ocr_text` 字段（后台可见 OCR）。
- **待办（前端）**：`StickersView.vue` 可加 OCR 列展示（后端已出字段），属阶段 2A 之外的 UI 增强，未做。

### 2.2 子步 2B — embedding 检索（按需升级，2A 不足时）

#### 2B.1 引入 embedding provider（全仓首次）

[services/similarity.py:58](../../services/similarity.py#L58) `EmbeddingSimilarityProvider`（现 raise 占位）

- 接通占位：选本地中文 embedding（bge-small-zh / m3e-small，ONNX，~100-200MB，复用 pre-part0 的 onnxruntime 部署模式）或 API（siliconflow 已有 key，零部署但有延迟/成本）。**调研后定**。
- 新增 `services/media/sticker_embeddings.py`：富描述 → 向量，存 SQLite BLOB（同 2A.1 的库，加 `embedding` 列）。

#### 2B.2 检索升级：search_by_intent 切向量

- `search_by_intent` 内部：BM25 召回 top-N → embedding 重排 top-k（hybrid，仿 context 的 RRF 融合 [context/service.py:262](../../services/context/service.py#L262)），或纯向量余弦。
- 对外接口不变（2A 已定 `search_by_intent` 签名），**2B 是内部实现升级，调用方零感知**。
- StickerCLIP 图 embedding（审计 §5.1）作为更后续增强——需 fine-tune 或本地大模型，2B 不含，留观察。

### 2.3 阶段 2 测试

| 用例 | 断言 |
|---|---|
| index.json → SQLite 迁移：旧库导入后条目数/字段一致 | ✓ |
| 迁移幂等：二次启动不重复导入 | ✓ |
| `search_by_intent("晚安")` 命中含晚安 OCR/描述的 sticker | ✓（2A BM25） |
| `search_by_intent` 空库 / 无匹配 → 空列表 | ✓ |
| `send_sticker(intent=...)` 走检索；`send_sticker(sticker_id=...)` 走 ID | 两路径并存 |
| StickerStore 接口签名不变（add/get/update 旧调用） | 回归全绿 |
| 2B：embedding "告别"召回"拜拜/挥手"近义（BM25 召不回的） | ✓（2B 才测） |

### 2.4 阶段 2 影响 & 回滚

- 2A：SQLite 迁移有数据动作（一次性导入）——**这是阶段 2 唯一的存储迁移点**，需备份 index.json、迁移幂等、保留回退周期。改 .py 需 rebuild。
- 2B：引入 embedding 依赖 → pyproject 改动 + 镜像增量（~150-300MB，同 pre-part0 量级）。回滚 = `search_by_intent` 切回 2A 的 BM25 分支（保留），停用 embedding provider。
- 回滚总则：`send_sticker` 的 `intent` 是可选入参，去掉即回到纯 ID 发送；SQLite 迁移保留 index.json 回退周期。

---

## 阶段 3 — 弱回复 STICKER_ONLY 接入

### 3.0 前提

阶段 2 提供 `search_by_intent`。阶段 3 把它接到弱回复机制（见 [weak-reply-mechanism-design.md](weak-reply-mechanism-design.md) §2c，原标注"依赖前置项"的部分）。

### 3.1 接入点

- 弱回复 thinker 判 `light_reply` 且 `light_kind ∈ {closing, companion}` 且语境适合纯表情 → 走 `ResponseClass.STICKER_ONLY`。
- 由 `light_kind` 映射检索意图：closing → `search_by_intent("告别/晚安/挥手")`；companion → `search_by_intent(当前情绪/话题词)`。
- 复用弱回复的 mood 染色：低落 → 检索结果再按 mood 过滤（安静表情），承接 [mood→how 改造](weak-reply-mechanism-design.md)。
- 命中则 STICKER_ONLY 发表情、不发字；未命中（检索空）→ 回退纯文字 token（弱回复本来的载体），**不静默**。

### 3.2 节制（复用弱回复第 3 层）

- STICKER_ONLY 与文字弱回复**共用弱回复冷却**（不能既发字又发图、不能连发表情）。
- 表情包发送频率受 `decision_provider` 现有 cooldown + fairmatch 过曝惩罚共同约束。

### 3.3 阶段 3 测试

| 用例 | 断言 |
|---|---|
| closing + 适合纯表情 → STICKER_ONLY 走 search_by_intent | ✓ |
| 检索空 → 回退文字 token，不静默 | ✓ |
| STICKER_ONLY 与文字弱回复共用冷却 | ✓ |
| mood 低落 → 检索结果偏安静表情 | ✓ |

---

## 4. 全阶段落地顺序与依赖

```text
阶段 1（OCR 入库）        ── 独立可上线，解 F2，为下游喂富描述
   │
   ▼
阶段 2A（BM25 检索+SQLite）── 零新依赖，解 F1 入口/F3/F4，先验证范式
   │
   ├─（够用则止步）
   ▼
阶段 2B（embedding 升级） ── 按需，BM25 近义召回不足时才上，引入 embedding 栈
   │
   ▼
阶段 3（弱回复接入）       ── 接 search_by_intent，STICKER_ONLY 落地
```

每阶段独立 maintenance-log 条目 + 回归测试 + rebuild。阶段间不强耦合：阶段 1 可单独上线观察；阶段 2A 充分则可不上 2B；阶段 3 等弱回复主体（文字 token）先行后再接表情。

## 5. 与 pre-part0 协调checklist

- [ ] 阶段 1 的富描述 prompt 与 pre-part0 的角色识别描述**合并为一次 VL 调用**（审计 §6.3 约定①）。
- [ ] 阶段 2A 的 SQLite 与 pre-part0 `character_recognition.db` **同库/关联**，不起第二个 DB（审计 §6.2）。
- [ ] OCR 字段在两项目共用富描述里（审计 §6.4 反向提示已记）。
- [ ] 谁先落地谁搭统一管线骨架（VL 调用 + 缓存），后者复用。

## 6. 开放问题

- Qwen-VL 的 OCR 准确率未实测——阶段 1 上线前用真实群表情样本抽验（中文梗图、艺术字、动图首帧）。
- 动图（GIF 被 store 拒收，但 mface 动画表情走捕获）OCR 取哪一帧？建议首帧。
- 阶段 2B embedding 模型选型（本地 bge/m3e ONNX vs siliconflow API）——成本/延迟/质量权衡，2A 验证后定。
- `describe_max_tokens` 提到 200 是否够长梗图——观察后调。
- 阶段 2A 的 BM25 对短 query（"晚安"二字）召回是否够——决定是否需要尽早上 2B。