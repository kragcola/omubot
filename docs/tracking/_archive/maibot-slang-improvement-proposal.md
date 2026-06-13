# MaiBot 黑话系统改进方案

> 基于论文和成熟项目的架构改进提案，供 AI agent 阅读和执行。

---

## 一、背景

MaiBot（麦麦）的黑话学习系统（原 `bw_learner/`，现 `src/learners/`）实现了从群聊中自动提取和学习俚语/表达式的完整流程。本方案识别出 8 个可改进方向，按优先级排列，每项标注参考文献来源。

### 现有架构概览

```
消息输入 → learn_style.prompt (LLM提取表达式+候选黑话)
  ├── expression_learner.py → difflib去重 → 表达式库
  └── jargon_miner.py → 三阶段LLM推理 → 黑话库
       ├── 阶段1: jargon_inference_with_context (上下文推理)
       ├── 阶段2: jargon_inference_content_only (纯词推理)
       └── 阶段3: jargon_compare_inference (对比判断)
       └── count阈值重推: 24/60/100次 → 最终释义
```

**现有模块清单**：

| 模块 | 职责 |
|------|------|
| `jargon_miner.py` | 黑话提取 + 三阶段推理 + 阈值重推 |
| `expression_learner.py` | 表达式提取 + difflib去重 + 频率统计 |
| `jargon_explainer.py` | 黑话检索/摘要 |
| `expression_utils.py` | 解析和评估工具 |
| `jargon_data_model.py` | 数据模型：content, meaning, raw_content, count, is_jargon, is_complete, is_global |
| `query_jargon.py` | Dream Agent 工具：运行时查询黑话 |
| `typo_generator.py` | 拼音+字频+声调错误生成器 |

---

## 二、改进方案

### 改进项 1：NER 预过滤层

**参考文献**：
- Lee et al. (2025). "Deep Learning-Based Context-Aware NER for Neologism Detection Across Generations." DOI:10.33851/jmis.2025.12.4.141
- Tomaszewska et al. (2025). "NeoN: A Tool for Automated Detection, Linguistic and LLM-Driven Analysis of Neologisms." arXiv:2505.15426

**现有问题**：`jargon_miner.py` 的 `learn_style.prompt` 将所有非常用词列为候选黑话，人名、地名、品牌名等专有名词也被送入三阶段 LLM 推理，浪费 token 且增加误报。

**改进方案**：在 LLM 提取前增加 NER 预过滤层。

```python
# jargon_miner.py — 在 learn_style.prompt 调用前

import jieba.posseg as pseg

# 已知实体白名单（从群聊历史中积累）
KNOWN_ENTITIES: set[str] = set()

def _ner_filter(candidates: list[str]) -> list[str]:
    """过滤掉已知实体，只保留候选黑话。"""
    filtered = []
    for term in candidates:
        # 词性标注：nr=人名, ns=地名, nt=机构名
        words = list(pseg.cut(term))
        if all(w.flag not in ('nr', 'ns', 'nt') for w in words):
            filtered.append(term)
        # 人工白名单
        elif term in KNOWN_ENTITIES:
            continue
        else:
            filtered.append(term)
    return filtered
```

**实施步骤**：
1. 在 `learn_style.prompt` 调用前，对 LLM 返回的候选列表执行 `_ner_filter`
2. 被 NER 识别为专有名词的条目跳过黑话推理，直接进入表达式库（作为普通表达式记录）
3. 维护 `KNOWN_ENTITIES` 集合，从群聊中高频出现但无黑话特征的实体自动积累

**预期收益**：减少 30-50% 的无效 LLM 调用，降低 token 成本和误报率。

---

### 改进项 2：置信度评分系统

**参考文献**：
- malaya.ai（马来西亚 AI 副驾驶）将俚语归一化与置信度评分结合，Qwen 2.5 7B 马来语性能从 60% 提升到 95%。GitHub: `github.com/aminkhalili96/malaya.ai`
- Davidson et al. (2017). "Automated Hate Speech Detection and the Problem of Offensive Language." ICWSM 2017. URL: aaai.org/ocs/index.php/ICWSM/ICWSM17/paper/view/15665

**现有问题**：`jargon_data_model.py` 的 `is_jargon` 是二值 bool，没有置信度概念。低质量黑话和高确定性黑话被同等对待，都会注入 prompt。

**改进方案**：引入 `confidence: float` 字段，基于多信号叠加计算。

```python
# jargon_data_model.py — JargonDataModel 新增字段

@dataclass
class JargonDataModel:
    content: str
    meaning: str
    raw_content: str
    count: int
    is_jargon: bool
    is_complete: bool
    is_global: bool
    inference_with_context: str
    inference_with_content_only: str
    # 新增
    confidence: float = 0.0          # 0.0-1.0 综合置信度
    confidence_signals: dict = field(default_factory=dict)  # 各信号分项
```

**置信度计算规则**：

```python
def compute_confidence(jargon: JargonDataModel) -> float:
    """基于多信号叠加计算置信度。"""
    score = 0.0
    signals = {}

    # 信号1: 出现次数（0.0-0.3）
    count_score = min(jargon.count / 30, 0.3)
    score += count_score
    signals['count'] = count_score

    # 信号2: 上下文推理与纯词推理的差异度（0.0-0.4）
    # 差异越大 → 越可能是真黑话
    if jargon.inference_with_context and jargon.inference_with_content_only:
        diff = _compute_inference_difference(
            jargon.inference_with_context,
            jargon.inference_with_content_only
        )
        diff_score = min(diff * 0.4, 0.4)
        score += diff_score
        signals['inference_diff'] = diff_score

    # 信号3: 群组内使用人数（0.0-0.2）
    # 多人使用 → 更可能是群体黑话
    unique_users = len(jargon.get('unique_users', []))
    user_score = min(unique_users / 5, 0.2)
    score += user_score
    signals['unique_users'] = user_score

    # 信号4: NER 未命中（0.0-0.1）
    # 不是专有名词 → 加分
    if not jargon.get('is_entity', False):
        score += 0.1
        signals['not_entity'] = 0.1

    jargon.confidence = round(score, 3)
    jargon.confidence_signals = signals
    return score


def _compute_inference_difference(ctx_inference: str, content_inference: str) -> float:
    """计算两次推理结果的差异度（0.0-1.0）。"""
    # 用 embedding 余弦距离，退化为 difflib
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer('shibing624/text2vec-base-chinese')
        embeddings = model.encode([ctx_inference, content_inference])
        similarity = cosine_similarity([embeddings[0]], [embeddings[1]])[0][0]
        return 1.0 - similarity
    except ImportError:
        import difflib
        return 1.0 - difflib.SequenceMatcher(None, ctx_inference, content_inference).ratio()
```

**置信度阈值**：

| 阈值 | 行为 |
|------|------|
| >= 0.6 | 高置信度：注入 prompt，标记 `is_jargon=True` |
| 0.3 - 0.6 | 中置信度：注入 prompt，标记为"待验证" |
| < 0.3 | 低置信度：不注入 prompt，保留在观察区继续累积 |

**实施步骤**：
1. `jargon_data_model.py` 增加 `confidence` 和 `confidence_signals` 字段
2. `jargon_miner.py` 在三阶段推理完成后调用 `compute_confidence()`
3. 修改 prompt 注入逻辑，只注入 `confidence >= 0.3` 的黑话
4. 新增 SQLite 列 `confidence REAL DEFAULT 0`

**预期收益**：减少低质量黑话污染 prompt，提升回复质量。

---

### 改进项 3：嵌入向量替代 difflib 去重

**参考文献**：
- Abbas et al. (2023). "SemDeDup: Data-efficient learning at web-scale through semantic deduplication." arXiv:2303.09540. Meta AI.
- TextDedup 库. GitHub: `github.com/ChenghaoMou/textdedup`. 支持语义去重（sentence embeddings + FAISS）。
- Sentence-Transformers 库. `all-MiniLM-L6-v2`（英文）/ `shibing624/text2vec-base-chinese`（中文）

**现有问题**：`expression_learner.py:_find_similar_expression()` 使用 `difflib.SequenceMatcher.ratio()` 阈值 0.75，纯字符级匹配。"社死" vs "社会性死亡"、"yyds" vs "永远的神" 会被判为不同条目。

**改进方案**：用 embedding 余弦相似度替代 difflib。

```python
# expression_learner.py — 替换 _find_similar_expression

from sentence_transformers import SentenceTransformer
import numpy as np

# 全局模型实例（进程级复用）
_embed_model: SentenceTransformer | None = None

def _get_embed_model() -> SentenceTransformer:
    global _embed_model
    if _embed_model is None:
        _embed_model = SentenceTransformer('shibing624/text2vec-base-chinese')
    return _embed_model


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))


def _find_similar_expression(
    content: str,
    existing: list[ExpressionDataModel],
    threshold: float = 0.85,
) -> ExpressionDataModel | None:
    """用 embedding 余弦相似度查找相似表达式。"""
    model = _get_embed_model()
    content_emb = model.encode(content)

    best_match = None
    best_sim = 0.0

    for expr in existing:
        # 优先用缓存的 embedding
        if hasattr(expr, '_embedding') and expr._embedding is not None:
            sim = _cosine_similarity(content_emb, expr._embedding)
        else:
            expr_emb = model.encode(expr.content)
            expr._embedding = expr_emb  # 缓存
            sim = _cosine_similarity(content_emb, expr_emb)

        if sim > best_sim:
            best_sim = sim
            best_match = expr

    if best_sim >= threshold:
        return best_match
    return None
```

**向量索引优化（表达式量大时）**：

```python
# expression_learner.py — 批量索引版本

import faiss

class ExpressionIndex:
    """FAISS 索引，用于大规模表达式检索。"""

    def __init__(self, dim: int = 768):
        self.dim = dim
        self.index = faiss.IndexFlatIP(dim)  # 内积（归一化后=余弦相似度）
        self.expressions: list[ExpressionDataModel] = []

    def add(self, expr: ExpressionDataModel):
        model = _get_embed_model()
        emb = model.encode(expr.content)
        emb = emb / np.linalg.norm(emb)  # L2 归一化
        self.index.add(emb.reshape(1, -1))
        self.expressions.append(expr)

    def search(self, query: str, threshold: float = 0.85) -> ExpressionDataModel | None:
        model = _get_embed_model()
        q_emb = model.encode(query)
        q_emb = q_emb / np.linalg.norm(q_emb)
        scores, indices = self.index.search(q_emb.reshape(1, -1), 1)
        if scores[0][0] >= threshold:
            return self.expressions[indices[0][0]]
        return None
```

**实施步骤**：
1. `pyproject.toml` 添加依赖：`sentence-transformers`, `numpy`（已有）
2. 替换 `_find_similar_expression()` 实现
3. 在 `ExpressionDataModel` 增加 `_embedding` 缓存字段（不持久化）
4. 表达式量 > 100 时启用 FAISS 索引
5. 保留 difflib 作为 fallback（embedding 模型加载失败时）

**预期收益**：语义去重率从 ~75% 提升到 ~95%+，覆盖拼音缩写、谐音、同义表达。

---

### 改进项 4：表达式生命周期管理

**参考文献**：
- My-Neuro 项目的情绪状态持久化机制（情绪有生命周期，会随时间衰减）。GitHub: `github.com/morettt/my-neuro`
- Hamilton et al. (2016). "Diachronic Word Embeddings Reveal Statistical Laws of Semantic Change." ACL 2016. 频率和多义性预测语义变化速度。

**现有问题**：表达式和黑话一旦学到就永久存在，没有过期机制。旧的、不再使用的表达式持续占用 prompt 空间。

**改进方案**：增加时间戳和生命周期状态。

```python
# jargon_data_model.py — 新增生命周期字段

@dataclass
class JargonDataModel:
    # ... 现有字段 ...
    created_at: str = ""           # ISO 时间戳
    last_seen_at: str = ""         # 最后一次在群聊中出现的时间
    lifecycle: str = "active"      # active / dormant / expired
```

**生命周期规则**：

```python
from datetime import datetime, timedelta

def update_lifecycle(jargon: JargonDataModel) -> str:
    """更新黑话生命周期状态。"""
    if not jargon.last_seen_at:
        return jargon.lifecycle

    last_seen = datetime.fromisoformat(jargon.last_seen_at)
    now = datetime.now()
    days_since = (now - last_seen).days

    if days_since <= 14:
        jargon.lifecycle = "active"       # 活跃期：注入 prompt
    elif days_since <= 60:
        jargon.lifecycle = "dormant"      # 休眠期：低优先级注入
    else:
        jargon.lifecycle = "expired"      # 过期：不注入 prompt

    return jargon.lifecycle
```

**Prompt 注入逻辑**：
- `active`：正常注入
- `dormant`：仅在相关上下文匹配时注入
- `expired`：不注入，保留在数据库供未来参考

**实施步骤**：
1. 数据模型增加 `created_at`, `last_seen_at`, `lifecycle` 字段
2. SQLite schema ALTER TABLE 增加三列
3. 每次群聊中出现已知黑话时更新 `last_seen_at`
4. DreamAgent 合并周期中批量更新 `lifecycle`
5. 修改 prompt 注入逻辑，按 lifecycle 过滤

**预期收益**：prompt 空间利用率提升，减少过时内容干扰。

---

### 改进项 5：多信号检索融合

**参考文献**：
- Mem0 项目（YC S24）的多信号检索架构：语义搜索 + BM25 关键词匹配 + 实体匹配，三路并行打分融合。GitHub: `github.com/mem0ai/mem0`
- LoCoMo 基准：Mem0 得分 91.6；LongMemEval 基准：93.4。

**现有问题**：`jargon_explainer.py` 的检索是精确匹配或前缀匹配，无法处理语义相关但表面不同的查询。

**改进方案**：三路并行检索 + 分数融合。

```python
# jargon_explainer.py — 新增多信号检索

from rank_bm25 import BM25Okapi
import numpy as np

class JargonRetriever:
    """多信号黑话检索器。"""

    def __init__(self, jargons: list[JargonDataModel]):
        self.jargons = jargons
        self._build_index()

    def _build_index(self):
        # 语义索引
        model = _get_embed_model()
        contents = [j.content for j in self.jargons]
        self.embeddings = model.encode(contents)

        # BM25 索引（基于 content + meaning 分词）
        tokenized = [list(jieba.cut(j.content + ' ' + j.meaning)) for j in self.jargons]
        self.bm25 = BM25Okapi(tokenized)

    def retrieve(self, query: str, top_k: int = 5) -> list[tuple[JargonDataModel, float]]:
        """三路检索 + 分数融合。"""
        scores = {}

        # 路径1: 语义检索（权重 0.6）
        model = _get_embed_model()
        q_emb = model.encode(query)
        semantic_scores = cosine_similarity([q_emb], self.embeddings)[0]
        for i, score in enumerate(semantic_scores):
            scores.setdefault(i, 0.0)
            scores[i] += score * 0.6

        # 路径2: BM25 关键词匹配（权重 0.3）
        q_tokens = list(jieba.cut(query))
        bm25_scores = self.bm25.get_scores(q_tokens)
        bm25_norm = (bm25_scores - bm25_scores.min()) / (bm25_scores.max() - bm25_scores.min() + 1e-8)
        for i, score in enumerate(bm25_norm):
            scores.setdefault(i, 0.0)
            scores[i] += score * 0.3

        # 路径3: 精确/前缀匹配（权重 0.1）
        for i, jargon in enumerate(self.jargons):
            if query in jargon.content or jargon.content in query:
                scores.setdefault(i, 0.0)
                scores[i] += 0.1

        # 按融合分数排序
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
        return [(self.jargons[i], score) for i, score in ranked]
```

**实施步骤**：
1. `pyproject.toml` 添加 `rank-bm25` 依赖
2. 新建 `JargonRetriever` 类，替代 `jargon_explainer.py` 的精确匹配
3. DreamAgent 查询时使用 `JargonRetriever.retrieve()`
4. 缓存 embedding 索引，黑话库更新时增量重建

**预期收益**：黑话召回率从精确匹配提升到语义级召回，释义检索更准确。

---

### 改进项 6：跨群组知识隔离

**参考文献**：
- Mem0 的多级记忆架构：User / Session / Agent 三级隔离。GitHub: `github.com/mem0ai/mem0`
- Letta/MemGPT 的 memory blocks 机制：命名段（`human`, `persona`），agent 可读写。GitHub: `github.com/letta-ai/letta`

**现有问题**：`is_global` 字段只有 true/false，缺乏系统化的群组隔离。A 群学到的黑话可能在 B 群中乱用。

**改进方案**：增加来源追溯和作用域控制。

```python
# jargon_data_model.py — 新增作用域字段

@dataclass
class JargonDataModel:
    # ... 现有字段 ...
    origin_group: str = ""              # 来源群组 ID
    scope: str = "group"                # group / global
    shared_to: list[str] = field(default_factory=list)  # 已共享到的群组列表
```

**作用域规则**：
1. 默认 `scope="group"`，仅在来源群组中注入 prompt
2. `scope="global"` 需要显式提升（管理员操作或 DreamAgent 自动判断）
3. DreamAgent 自动提升条件：同一黑话在 3 个以上群组中独立出现
4. 查询时按 `scope` + `origin_group` 过滤

```python
def get_injectable_jargons(group_id: str, all_jargons: list[JargonDataModel]) -> list[JargonDataModel]:
    """获取可注入当前群组 prompt 的黑话列表。"""
    result = []
    for j in all_jargons:
        if j.scope == "global":
            result.append(j)
        elif j.scope == "group" and j.origin_group == group_id:
            result.append(j)
    return result
```

**实施步骤**：
1. 数据模型增加 `origin_group`, `scope`, `shared_to` 字段
2. SQLite schema 增加列
3. 提取黑话时记录 `origin_group`
4. DreamAgent 周期中检测跨群组重复，自动提升 `scope`
5. prompt 注入时按 `group_id` 过滤

**预期收益**：避免跨群组梗乱用，保持各群组文化独立性。

---

### 改进项 7：语义漂移追踪

**参考文献**：
- Hamilton et al. (2016). "Diachronic Word Embeddings Reveal Statistical Laws of Semantic Change." ACL 2016. 使用 Procrustes 对齐追踪词义变化。
- SemEval-2020 Task 1: "Unsupervised Lexical Semantic Change Detection." URL: ims.uni-stuttgart.de/en/research/ressourcen/korpora/wic/
- Giulianelli (2020). "Analysing Lexical Semantic Change with Contextualised Word Representations." ACL 2020. 使用 BERT 上下文 embedding 检测词义变化。
- Kutuzov et al. (2018). "Diachronic word embeddings and semantic change: a survey." arXiv:1806.03537.

**现有问题**：MaiBot 的 `count` 字段在 24/60/100 次触发重新推理，但只替换释义，没有检测语义是否真的发生了漂移。

**改进方案**：存储历史推理结果，用 embedding 距离检测语义变化。

```python
# jargon_data_model.py — 新增历史推理字段

@dataclass
class JargonDataModel:
    # ... 现有字段 ...
    inference_history: list[dict] = field(default_factory=list)
    # 格式: [{"count": 24, "inference": "...", "embedding": [...], "timestamp": "..."}]
    semantic_drift: float = 0.0  # 语义漂移程度 (0.0-1.0)
```

**漂移检测逻辑**：

```python
def detect_semantic_drift(jargon: JargonDataModel, new_inference: str) -> float:
    """检测新推理与历史推理的语义漂移程度。"""
    if not jargon.inference_history:
        return 0.0

    model = _get_embed_model()
    new_emb = model.encode(new_inference)

    # 与最近 3 次历史推理比较
    recent = jargon.inference_history[-3:]
    distances = []
    for record in recent:
        old_emb = np.array(record['embedding'])
        dist = 1.0 - _cosine_similarity(new_emb, old_emb)
        distances.append(dist)

    avg_distance = np.mean(distances)
    jargon.semantic_drift = round(float(avg_distance), 3)

    # 漂移阈值判断
    if avg_distance > 0.4:
        # 显著漂移：标记为需要审核
        jargon.lifecycle = "drifted"
        logger.warning(f"语义漂移检测 | content={jargon.content} drift={avg_distance:.3f}")

    return avg_distance


def on_count_threshold_reached(jargon: JargonDataModel, new_inference: str):
    """count 达到阈值时的处理逻辑。"""
    # 存储历史
    model = _get_embed_model()
    jargon.inference_history.append({
        'count': jargon.count,
        'inference': new_inference,
        'embedding': model.encode(new_inference).tolist(),
        'timestamp': datetime.now().isoformat(),
    })
    # 只保留最近 5 条
    jargon.inference_history = jargon.inference_history[-5:]

    # 检测漂移
    drift = detect_semantic_drift(jargon, new_inference)

    if drift < 0.2:
        # 无显著变化，更新释义
        jargon.meaning = new_inference
        jargon.is_complete = True
    elif drift < 0.4:
        # 轻微变化，保留新旧释义供参考
        jargon.meaning = new_inference
    else:
        # 显著漂移，需要人工审核或自动合并
        jargon.meaning = f"[待审核] {new_inference}（原义: {jargon.meaning}）"
```

**实施步骤**：
1. 数据模型增加 `inference_history` 和 `semantic_drift` 字段
2. JSON 字段存储在 SQLite 的 `meta_json` 列中
3. 修改 `jargon_miner.py` 的阈值重推逻辑，调用 `detect_semantic_drift()`
4. DreamAgent 合并周期中对 `lifecycle="drifted"` 的黑话进行审核

**预期收益**：及时发现已过时的黑话含义，避免用旧义回复。

---

### 改进项 8：自进化 Persona 块

**参考文献**：
- Letta/MemGPT 的 self-editing memory：agent 可修改自己的 persona 和 human 描述块。GitHub: `github.com/letta-ai/letta`
- LangMem 的 prompt refinement：根据累积交互优化系统 prompt。GitHub: `github.com/langchain-ai/langmem`
- Character-LLM (EMNLP 2023). "Character-LLM: A Trainable Agent for Role-Playing." arXiv:2310.10158. Experience Reconstruction 方法。
- SillyTavern 的 character card spec：`personality` + `scenario` + `mes_example` + `system_prompt` 四段式定义。GitHub: `github.com/SillyTavern/SillyTavern`
- PersonaGPT (2025). "A Context-Aware Personalization Engine Using Dynamic Learner Personas and Reflexive Dialogue." DOI:10.1109/SMAP66932.2025.00028.

**现有问题**：MaiBot 的人格定义在 prompt 中是静态的，学到的黑话只影响回复内容，不影响"人格"本身。bot 的说话风格不会随社群文化演化。

**改进方案**：在 DreamAgent 合并周期中，让 LLM 审视学到的表达风格，提炼为 persona 指令。

```python
# expression_reflector.py — 扩展自进化 persona

PERSONA_EVOLUTION_PROMPT = """
你是一个人格演化分析器。根据以下信息，判断这个角色的说话风格是否发生了变化。

## 当前 persona 描述
{current_persona}

## 最近学到的表达式（Top 20）
{recent_expressions}

## 最近学到的黑话（Top 10）
{recent_jargons}

## 任务
1. 判断说话风格是否发生了显著变化（是/否）
2. 如果是，生成更新后的 persona 风格描述（仅修改风格部分，不修改核心人格）
3. 输出 JSON：
   {
     "changed": true/false,
     "style_update": "新的风格描述（如果 changed=true）",
     "reason": "变化原因"
   }

## 约束
- 只修改说话风格，不修改核心人格特征
- 风格描述不超过 50 字
- 如果表达式和黑话没有明显的风格倾向，返回 changed=false
"""

def evolve_persona(
    current_persona: str,
    expressions: list[ExpressionDataModel],
    jargons: list[JargonDataModel],
) -> dict:
    """DreamAgent 周期中调用，评估 persona 是否需要演化。"""
    recent_expr = sorted(expressions, key=lambda e: e.count, reverse=True)[:20]
    recent_jargon = sorted(jargons, key=lambda j: j.confidence, reverse=True)[:10]

    prompt = PERSONA_EVOLUTION_PROMPT.format(
        current_persona=current_persona,
        recent_expressions='\n'.join(f"- {e.content} (使用{e.count}次)" for e in recent_expr),
        recent_jargons='\n'.join(f"- {j.content}: {j.meaning}" for j in recent_jargon),
    )

    result = llm_call(prompt, response_format='json')
    return json.loads(result)
```

**Guardrails**：
1. 核心人格特征（名字、身份、核心价值观）标记为 `immutable`
2. 只允许修改 `## 说话风格` 段落
3. 每次演化需要 DreamAgent 确认（不自动写入，而是生成候选）
4. 演化频率限制：最多每周一次
5. 保留演化历史，支持回滚

**实施步骤**：
1. `config/soul/identity.md` 增加 `## 说话风格` 段落（如果不存在）
2. DreamAgent 周期中调用 `evolve_persona()`
3. 如果 `changed=true`，将更新写入 `storage/memories/persona_evolution.md`
4. Prompt builder 读取演化记录，注入最新风格描述

**预期收益**：bot 的说话风格随社群文化自然演化，增强拟人感。

---

## 三、实施优先级

| 优先级 | 改进项 | 难度 | 收益 | 依赖 | 预计工时 |
|:---:|--------|:---:|:---:|------|:---:|
| **P0** | 1. NER 预过滤 | 低 | 高 | jieba | 2h |
| **P0** | 2. 置信度评分 | 低 | 高 | 无 | 3h |
| **P1** | 3. 嵌入向量去重 | 中 | 高 | sentence-transformers | 4h |
| **P1** | 4. 生命周期管理 | 中 | 中 | 无 | 3h |
| **P2** | 5. 多信号检索 | 中 | 高 | rank-bm25, embedding | 5h |
| **P2** | 6. 跨群组隔离 | 低 | 中 | 无 | 2h |
| **P3** | 7. 语义漂移追踪 | 高 | 中 | embedding, 历史存储 | 6h |
| **P3** | 8. 自进化 Persona | 高 | 高 | DreamAgent 扩展 | 8h |

**推荐实施路径**：P0（1周）→ P1（2周）→ P2（3周）→ P3（按需）

---

## 四、依赖清单

```toml
# pyproject.toml — 新增依赖
[project.optional-dependencies]
slang-learning = [
    "sentence-transformers>=2.2.0",  # 嵌入向量
    "jieba>=0.42.1",                 # 中文分词 + NER
    "rank-bm25>=0.2.2",             # BM25 检索
    "faiss-cpu>=1.7.4",             # 向量索引（可选，量大时启用）
]
```

---

## 五、参考文献汇总

### 论文

| 编号 | 论文 | 年份 | 领域 | 链接 |
|------|------|------|------|------|
| [1] | SemDeDup (Meta AI) | 2023 | 语义去重 | arXiv:2303.09540 |
| [2] | Hamilton et al. "Diachronic Word Embeddings" | 2016 | 语义漂移 | ACL 2016 |
| [3] | SemEval-2020 Task 1 | 2020 | 词义变化检测 | ims.uni-stuttgart.de |
| [4] | Giulianelli "Lexical Semantic Change" | 2020 | 语义漂移 | ACL 2020 |
| [5] | Jin et al. "Text Style Transfer Survey" | 2020 | 风格迁移 | arXiv:2011.00416 |
| [6] | CAT-LLM (Tao et al.) | 2025 | 中文风格迁移 | DOI:10.1145/3744250 |
| [7] | Character-LLM | 2023 | 角色扮演训练 | arXiv:2310.10158 |
| [8] | NeoN (Tomaszewska et al.) | 2025 | 新词检测 | arXiv:2505.15426 |
| [9] | Lee et al. "NER for Neologism Detection" | 2025 | NER新词检测 | DOI:10.33851/jmis.2025.12.4.141 |
| [10] | CSCIS (Ma et al.) | 2025 | 中文俚语纠错 | DOI:10.1007/978-981-95-3349-7_29 |
| [11] | Li & Wang "Douyin Slang" | 2024 | 中文网络俚语 | DOI:10.1515/applirev-2023-0094 |
| [12] | Davidson et al. "Hate Speech Detection" | 2017 | 误报分析 | ICWSM 2017 |
| [13] | PersonaGPT | 2025 | 动态人格 | DOI:10.1109/SMAP66932.2025.00028 |
| [14] | Kutuzov et al. "Diachronic Survey" | 2018 | 语义变化综述 | arXiv:1806.03537 |

### 开源项目

| 编号 | 项目 | 领域 | 链接 |
|------|------|------|------|
| [P1] | Mem0 | 记忆系统 | github.com/mem0ai/mem0 |
| [P2] | LangMem | 记忆管理 | github.com/langchain-ai/langmem |
| [P3] | Letta/MemGPT | 自编辑记忆 | github.com/letta-ai/letta |
| [P4] | SillyTavern | 角色卡系统 | github.com/SillyTavern/SillyTavern |
| [P5] | TextDedup | 语义去重 | github.com/ChenghaoMou/textdedup |
| [P6] | malaya.ai | 俚语归一化 | github.com/aminkhalili96/malaya.ai |
| [P7] | My-Neuro | 情绪状态 | github.com/morettt/my-neuro |
| [P8] | Character-LLM | 角色训练 | github.com/choosewhatulike/trainable-agents |
| [P9] | neologism-pipeline | 新词检测 | github.com/DiegoRossini/neologism-pipeline |
| [P10] | chinese-internet-slang-data | 中文俚语数据 | github.com/YongX/chinese-internet-slang-data |
