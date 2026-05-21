# 黑话治理方案 — 跨项目调研与改造提案（2026-05-16）

## 一、背景

omubot 黑话模块当前状态：候选池 998 条 `candidate`、AI 已批 50 条待人审、人工已确认 158 条、AI 否决 134 条；backlog reviewer 一轮跑下来 90% 进 kept 桶（confidence 落 [0.5, 0.82) 区间，既不批也不否），池子无法收敛。

为定方向，调研了同类竞品 MaiBot、四篇 ACL/EMNLP 论文（NeoN / SLANG / CHEER+RESS / NEO-BENCH）、两个中文新词发现开源项目（统计法）。本文档输出：横向对比 → 我们和业界的差距 → 排序后的改造清单。

不是大重构方案，是"现有架构上贴几个补丁"的增量优化清单。

---

## 二、业界四种主流路线

| 路线 | 抽取信号 | 审核机制 | 注入方式 | 状态机 | 代表 |
|---|---|---|---|---|---|
| **统计先验** | PMI 凝固度 + 左右熵自由度 + 频次 | 词典对照 + 人审 | jieba 用户词典 | 一次性，无生命周期 | colinwke / JunyuMao |
| **LLM 全自动** | LLM 直接抽 | 双 prompt 自我对照（带上下文 vs 裸词） | substring 命中 → LLM 概括 | `is_jargon ∈ {None, True, False}` + `is_complete` | **MaiBot** |
| **LLM + UGC 检索增强** | UGC 召回 → LLM 起草 → 留出 UGC 自验 | LLM 用 holdout UGC 验证定义一致性 | RAG 注入候选定义 | dictionary 字段 | **CHEER + RESS（ACL 2025）** |
| **混合管线** | 参考语料对比剔除已知词 + 频率峰 + 形态规则 | LLM 解释 + 人工 spot-check | 直接出词典，不上 chat | 候选 → 验证 → 入典 三段 | **NeoN（2025）** |

四条路线的根本分歧不是"用不用 LLM"，是 **"信号从哪里来 / 怎么验证一个候选不是 LLM 编的"**。

omubot 当前接近 MaiBot 一路：LLM 抽 → LLM 复核 → 三态 + 人审兜底。

---

## 三、关键差异（按 ROI 排序）

### 差异 1：MaiBot 的"双 prompt 自我对照"（反幻觉）

MaiBot 的 [`infer_meaning`](file:///Users/kragcola/MaiM-with-u/MaiBot/src/bw_learner/jargon_miner.py) 不直接问"这是不是黑话"，而是：

1. **Prompt₁**：给词 + 群聊上下文 → 推断含义 A
2. **Prompt₂**：**只给词、不给上下文** → 推断含义 B
3. **Prompt₃**：A vs B 是否一致？
   - 一致 ⇒ 普通词（LLM 凭训练就懂），**否决**
   - 不一致 ⇒ 真黑话（含义只能从上下文里学到），**通过**

这是最便宜的反幻觉 trick。omubot 现在 [services/slang/daily_reviewer.py:406-414](services/slang/daily_reviewer.py#L406-L414) `_assess` 是 `assess_with_llm` 的薄封装，单 prompt + 可选 web_search，**没这一刀** — 所以"已经在通用语境里存在但 LLM 心情好就抽出来"的词会被错误判为黑话。

### 差异 2：CHEER-RESS 的"群内 UGC 自验"

CHEER 数据集（ACL 2025）的 RESS 方法：

1. 用候选词召回包含它的 UGC（社媒原帖）
2. LLM 看 N 条 UGC 起草定义
3. **拿起草的定义 + 留出来的另一组 UGC 给 LLM**：定义能不能解释这些 UGC？不能 → 重写

对应我们的场景：现在 `_assess` 用最近 8 条群消息 + 可选 web_search。问题是 web_search 召回的是"互联网普遍用法"，**和这个群里的具体用法可能不是一回事**。RESS 思路 = 拿同一个群另外几条命中消息验证定义的语境一致性，比 web_search 更精准。

### 差异 3：NeoN 的"参考语料对照"

NeoN 的强项是 **reference corpus filtering**：建一个"已知词集合"（普通中文词典 + 历史聊天里 LLM 已经认过的词），新词必须**在新语料里出现且不在参考集合里**才进入候选。

我们彻底没做这一步 — 1020 条候选里很多其实是普通词，只因为 LLM 抽取阶段心情好就抽出来了。**注意**：照搬 NeoN 用通用词典的做法在中文场景会误杀真黑话（"摆烂""内卷""超舟"已经在 jieba 默认词典里）—— 真正可行的 reference corpus 是项目内部累积的"人工驳回库"，详见 P2-1。

### 差异 4：MaiBot 的频次阶梯重判

MaiBot 在 `count = [2, 4, 8, 12, 24, 60, 100]` 每跨过一档触发**一次重判**，并在 24/60 时**随机砍一半 raw_content**（防早期噪声污染）。

类比到我们的死池：998 条候选里其实分两种 — "出现 1 次的偶然词" vs "出现 N 次的真候选"。MaiBot 不会浪费 token 复审低频词，只在频次过门槛时复审。我们 backlog reviewer 是不分高低频一锅端（拉取实现在 [services/slang/store.py:1413+](services/slang/store.py#L1413) `list_backlog_candidates`，仅按 confidence 排序、无频次门槛），所以会在 409 条 kept 上反复烧 token。

---

## 四、改造清单

> 编号 = 优先级（P0 立刻做 / P1 一次小重构 / P2 长期工程 / P3 业界踩过坑不要做）

### P0-1：backlog reviewer 加频次门槛 ★★★

**改动**：[services/slang/store.py](services/slang/store.py) 的 `list_backlog_candidates` 和 `count_backlog_candidates` 同步加 `min_usage_count` 过滤；N 可配置，默认 3。两个方法必须同时改 —— 否则进度状态机里的 `total_at_start` 和实际拉取池数量对不上，UI 进度条永远凑不齐 100%。

**预期**：998 候选里大部分是偶然出现 1 次的噪声 — 加门槛后估计降到 200 左右进入 backlog reviewer 复核池。低频词不烧 LLM token，留给"出现多了再说"。

**触及文件 — 后端**：
- [services/slang/store.py](services/slang/store.py) `list_backlog_candidates` + `count_backlog_candidates` 同时加 `min_usage_count` 参数
- [services/slang/types.py](services/slang/types.py) `SlangSettings` 加 `backlog_review_min_usage_count: int = Field(default=3, ge=1, le=20)`
- [services/slang/backlog_reviewer.py](services/slang/backlog_reviewer.py) 透传配置到 store 调用
- [admin/routes/api/slang.py](admin/routes/api/slang.py) `summary` 端点新增 `eligible_backlog_count` 字段（按当前 `min_usage_count` 阈值统计 `slang_terms.status='candidate' AND usage_count >= N`），用于验收

**触及文件 — 前端配置**（gpt 审计 Medium #5：手写表单不会随 schema 自动出来）：
- [admin/frontend/src/views/slang/helpers/types.ts](admin/frontend/src/views/slang/helpers/types.ts) `SlangSettings` 接口加字段
- [admin/frontend/src/views/slang/helpers/formatters.ts](admin/frontend/src/views/slang/helpers/formatters.ts) `DEFAULT_SLANG_SETTINGS` 加默认值
- [admin/frontend/src/views/slang/components/SlangSettingsForm.vue](admin/frontend/src/views/slang/components/SlangSettingsForm.vue) 加输入项
- [admin/frontend/src/views/slang/components/SlangSettingsDrawer.vue](admin/frontend/src/views/slang/components/SlangSettingsDrawer.vue) 同步加输入项

**风险**：第一次清池会留下一批"低频但真的是黑话"的词暂时进不了 review。**缓释不是 P0-3**（P0-3 只对已进入 review 的 kept 词有效）—— 而是依赖词条自然累积 `usage_count`，一旦跨过门槛就自动进入下一轮 review。门槛不要设太高（默认 3 已足够过滤"昙花一现"噪声）。

### P0-2：定义 AI review 契约 + 三路径统一 + SQL helper 拆分 ★★★

**根因（重新认定）**：[services/slang/backlog_reviewer.py:353-409](services/slang/backlog_reviewer.py#L353-L409) 三个分支（approve / mute / kept）共用同一个 `meta_patch`，里面只有 `backlog_review` 子键。但 SQL 条件 [services/slang/store.py:354-358](services/slang/store.py#L354-L358) `_ai_review_sql_condition()` 只查 `source='ai_auto_review'` 和 `meta_json LIKE '%"ai_approved": true%'`，**完全不认 `backlog_review` 键、也不认 `ai_approved=false`**。

**后果**（DB 实测，不是推测）：

| 路径 | status 变化 | 当前 SQL 可见性 | 实际现象 |
|---|---|---|---|
| approve | candidate → approved | **看不见** | backlog 已 approve 的 43 条全部混进"已批准 158"和人工新增的混在一起 |
| mute | candidate → muted | **看不见** | backlog 否决落入 muted 桶但用户分不清是 AI 否决还是 extractor 噪声 |
| kept | candidate 不变 | **看不见** | kept 词条回到"待审核"和纯候选混在一起 |

**daily_reviewer 的 50 条 "AI 审核" 项不受影响**：它走 [services/slang/store.py:1025+](services/slang/store.py#L1025) `upsert_ai_approved_term`，在 [store.py:1063](services/slang/store.py#L1063) 显式写 `meta.ai_approved=true`，所以被 SQL 条件正确捕获。问题完全在 backlog_reviewer 这条新加路径。

**修复（统一契约方案）**：

deepseek 审计 H2 指出原方案 A（"三路径都写 ai_approved=true/false"）会让"AI 审过"和"AI 通过了"两个语义共用一个字段、且需要追加 `LIKE '%"ai_approved": false%'`。改为**新增独立字段表达"AI 已审过"**，与"AI 是否通过"解耦：

| meta 字段 | 类型 | 含义 | 写入位置 |
|---|---|---|---|
| `ai_reviewed_at` | iso 时间戳 | **AI 是否审过**（不论结论）。是"AI 审核"概念的唯一信号 | daily_reviewer + backlog_reviewer 三路径，全部写 |
| `ai_review_source` | `daily` \| `backlog` | 来源标记 | 同上 |
| `ai_review_decision` | `approved` \| `rejected` \| `kept` | AI 的结论 | 同上 |
| `ai_approved` | bool | 兼容字段：daily_reviewer 双写（与 `ai_review_decision='approved'` 同步），backlog_reviewer 不写。新代码读取一律用 `ai_review_decision`，`ai_approved` 仅供老 SQL 兜底 | daily_reviewer 单向写 |

SQL helper 同步拆分。**关键纪律**：项目 Python 端 `json.dumps(..., ensure_ascii=False)` 输出**带空格**（`"k": "v"`），SQLite `json_set()` 输出**紧凑无空格**（`"k":"v"`），所以每条 LIKE 都必须**双格式同时写**——既有 `_ai_review_sql_condition()`（[store.py:354-358](services/slang/store.py#L354-L358)）就是这个范式：

```python
# services/slang/store.py:354-372
def _ai_reviewed_sql_condition() -> str:
    """AI 是否审过（不论结论）。匹配 ai_reviewed_at 时间戳存在即可。"""
    return (
        "(source = 'ai_auto_review' "
        "OR meta_json LIKE '%\"ai_reviewed_at\": \"%' "
        "OR meta_json LIKE '%\"ai_reviewed_at\":\"%')"
    )

def _ai_approved_sql_condition() -> str:
    """AI 的结论是 approved。原 _ai_review_sql_condition 改名为这个，语义更准。"""
    return (
        "(source = 'ai_auto_review' "
        "OR meta_json LIKE '%\"ai_approved\": true%' "
        "OR meta_json LIKE '%\"ai_approved\":true%' "
        "OR meta_json LIKE '%\"ai_review_decision\": \"approved\"%' "
        "OR meta_json LIKE '%\"ai_review_decision\":\"approved\"%')"
    )

def _ai_rejected_sql_condition() -> str:
    """AI 的结论是 rejected（明确否决，不含 kept 模糊态）。"""
    return (
        "(meta_json LIKE '%\"ai_review_decision\": \"rejected\"%' "
        "OR meta_json LIKE '%\"ai_review_decision\":\"rejected\"%')"
    )

def _ai_kept_sql_condition() -> str:
    """AI 的结论是 kept（模糊态，留候）。P0-4 待观察 tab 用，避免 inline LIKE 漂移。"""
    return (
        "(meta_json LIKE '%\"ai_review_decision\": \"kept\"%' "
        "OR meta_json LIKE '%\"ai_review_decision\":\"kept\"%')"
    )

def _human_reviewed_sql_condition() -> str:
    # 现有，不动（已经是双格式：line 362-366）
    ...
```

**为什么这样拆**：

1. "AI 审过"和"AI 结论"两件事——原 `_ai_review_sql_condition()` 名字误导（叫 review 但只查 approved 一种结论）。拆开后命名能反映语义。
2. P0-4 "待观察"和"AI 否决"两 tab 都需要"AI 审过 + 不同结论"组合，新 helper 直接组合即可，无需在 SQL LIKE 里堆字符串。
3. 不需要 `LIKE '%"ai_approved": false%'`——这种写法对 mute/kept 写 false 时才生效，对 kept 路径"什么都不写"或写其他字段时立刻失效，脆弱。
4. **每个 LIKE 必须双格式**：Python 写 `": "` / SQLite `json_set` 写 `":"`，单格式 LIKE 会让其中一种来源的写入永远不命中——这是修订过程中差点漏掉的一致性纪律，与 [store.py:354-358](services/slang/store.py#L354-L358) 现有风格统一。

**触及文件**：

- [services/slang/backlog_reviewer.py:353-409](services/slang/backlog_reviewer.py#L353-L409) `meta_patch` 构造处，三路径统一加 `ai_reviewed_at` / `ai_review_source='backlog'` / `ai_review_decision`
- [services/slang/daily_reviewer.py](services/slang/daily_reviewer.py) `upsert_ai_approved_term` 的调用方在 meta 里同步加新字段（保留 `ai_approved=true` 兼容），让两个 reviewer 的 meta schema 对齐
- [services/slang/store.py:354-366](services/slang/store.py#L354-L366) `_ai_review_sql_condition` 改名 + 新增两个 helper；老调用点（[store.py:1382-1387](services/slang/store.py#L1382-L1387) `list_terms` 的 `review_filter`、[store.py:2333-2340](services/slang/store.py#L2333-L2340) summary）按语义换成新 helper
- **盘点 `meta.ai_approved` 全部读取点**（第五轮审计 N7）：grep `meta\.ai_approved` / `meta\["ai_approved"\]` / `term\.meta\.ai_approved` / `\.ai_approved` 在 admin 前后端、reviewer、其他业务路径的所有读取，逐个改读 `ai_review_decision='approved'`。仅留 SQL 层的 `_ai_approved_sql_condition()` 兼容 daily 历史数据。backlog_reviewer 在新契约下不再写 `ai_approved`，未盘点漏改会让 backlog approved 在该处"看不见"
- 一次性数据迁移（在 backup DB 上 dry-run 后再跑生产）：

```sql
-- 历史 backlog approve / mute / kept（写过 meta.backlog_review 但缺顶层 ai_reviewed_at）
-- decision 字段直接读 backlog_review.approved，不依赖最终 status——避免"backlog 通过→人工驳回"
-- 的项（status='muted' 但 backlog_review.approved=1）被误标 decision=rejected，污染 AI decision 语义。
UPDATE slang_terms
SET meta_json = json_set(
    meta_json,
    '$.ai_reviewed_at',
        COALESCE(json_extract(meta_json, '$.backlog_review.reviewed_at'), updated_at, created_at),
    '$.ai_review_source', 'backlog',
    '$.ai_review_decision',
        CASE
            -- 最高优先级（第五轮审计 N5）：daily_reviewer 已升级为 approved（写过 ai_approved=true）。
            -- 场景：先 backlog kept（approved=0）→ 后 daily 升 approved，
            -- 此时 backlog_review.approved=0 但 AI 最终结论是 approved，不是 kept。
            -- 双格式 LIKE 覆盖 Python 带空格 / SQLite json_set 紧凑两种序列化。
            WHEN meta_json LIKE '%"ai_approved": true%'
              OR meta_json LIKE '%"ai_approved":true%' THEN 'approved'
            -- AI 当时的决定就是 approved；后续被人工驳回也不改 AI 决定
            WHEN json_extract(meta_json, '$.backlog_review.approved') = 1 THEN 'approved'
            -- AI 明确否决：approved=0 且 status 已是 muted（且不是被人工 mute——P0-2 不区分人工/AI mute，
            -- 但 backlog_reviewer mute 路径 status 必为 muted；人工对 candidate 的 mute 不会写 backlog_review）
            WHEN json_extract(meta_json, '$.backlog_review.approved') = 0
              AND status = 'muted' THEN 'rejected'
            -- AI kept（approved=0 且 status 仍是 candidate）
            ELSE 'kept'
        END
)
WHERE meta_json LIKE '%"backlog_review":%'
  AND meta_json NOT LIKE '%"ai_reviewed_at": "%'
  AND meta_json NOT LIKE '%"ai_reviewed_at":"%';

-- 历史 daily_reviewer approve（已有 ai_approved=true、ai_reviewed_at 但缺 source/decision）
-- 注意：upsert_ai_approved_term (store.py:1063) 已经写过 ai_reviewed_at，所以不能用
-- "NOT LIKE ai_reviewed_at" 做 WHERE——会 0 行命中，daily 50 条永远拿不到 source/decision，
-- 后续基于 ai_review_decision 的统计（如 AI 通过率）会漏数据。改用 "NOT LIKE ai_review_source"。
UPDATE slang_terms
SET meta_json = json_set(
    meta_json,
    '$.ai_review_source', 'daily',
    '$.ai_review_decision', 'approved'
)
WHERE (meta_json LIKE '%"ai_approved": true%' OR meta_json LIKE '%"ai_approved":true%')
  AND meta_json NOT LIKE '%"ai_review_source": "%'
  AND meta_json NOT LIKE '%"ai_review_source":"%';
```

迁移脚本必须先在备份 DB dry-run 输出三类（approved / rejected / kept）数量，确认与预期（DB 实测当前 backlog approved=43、rejected=3、kept=409；daily approved=50）匹配后再上生产。

**fallback 时间链语义**（第五轮审计 N6）：第一段 `COALESCE(backlog_review.reviewed_at, updated_at, created_at)` 的优先级是"首选 → 失效兜底"——`reviewed_at` 是 backlog_reviewer 写入时的真实 review 时间，是首选；`updated_at` 因为 `record_hit` 等路径会刷新而可能晚于真实 review，是次选；`created_at` 是 term 首次入库时间，比 review 时间早是常态，是末选。三层都是历史时间（绝不写 `datetime('now')`），但前两层失效时仍能落到一个有意义的历史锚点上，这条链是退化但不污染。事后审计 `reviewed_at` 字段时应优先信任 `meta.backlog_review.reviewed_at`，顶层 `ai_reviewed_at` 仅在 SQL 条件里用于"是否审过"判断。

**LIKE 双格式纪律**：所有 `LIKE '%"key": "%'` 都必须配对一条 `LIKE '%"key":"%'`（无空格版），覆盖 Python `json.dumps` 和 SQLite `json_set` 两种序列化输出格式。`NOT LIKE` 同样要双格式——只写一种会让另一种格式的 row 漏过过滤。`ai_approved`/`human_reviewed` 是布尔字段（`%": true%` / `%":true%` 形式），其余字符串字段都是 `%": "%` / `%":"%` 形式。

**修后效果**：

- 待审核 = `status='candidate' AND NOT _ai_reviewed_sql_condition()` → 真正的"AI 没看过"
- 待观察 = `status='candidate' AND _ai_reviewed_sql_condition() AND ai_review_decision='kept'` → AI 看过、判模糊
- AI 否决 = `status='muted' AND _ai_rejected_sql_condition() AND NOT _human_reviewed_sql_condition()` → 仅 AI 否决（gpt 审计 High #2 修复）
- AI 审核 / 已批准 三 tab 详见 P0-4

**这是 P0-3、P0-4 的前置。**

### P0-3：kept 累计计数 + 自动降级 ★★★

**改动**：在 `meta_json` 加 `backlog_kept_streak: int`，每次 kept 时 `+= 1`；连续 ≥ 2 自动 mute。

**预期**：彻底解决"409 条永远 kept"。LLM 第一次模糊就给一次机会，第二次仍然模糊 → 默认无价值。这是 MaiBot 没做、但用户实际反馈"AI 一千多条只有几条否决"驱动的。

**触及文件 — 后端**：

- [services/slang/backlog_reviewer.py:398-409](services/slang/backlog_reviewer.py#L398-L409) kept 分支加计数
- 同分支 streak ≥ 2 时调 `set_status(..., "muted", reason="backlog_kept_streak_exceeded")`，并在 `meta_patch` 把 `ai_review_decision` 从 `'kept'` 改写为 `'rejected'`（与 P0-2 契约一致——streak mute 的语义就是 AI 最终否决，不再是模糊态）
- [services/slang/types.py](services/slang/types.py) `SlangSettings` 加 `backlog_kept_streak_limit: int = 2`

**触及文件 — 前端配置**（gpt 审计 Medium #5）：

- [admin/frontend/src/views/slang/helpers/types.ts](admin/frontend/src/views/slang/helpers/types.ts) `SlangSettings` 接口加字段
- [admin/frontend/src/views/slang/helpers/formatters.ts](admin/frontend/src/views/slang/helpers/formatters.ts) `DEFAULT_SLANG_SETTINGS` 加默认值
- [admin/frontend/src/views/slang/components/SlangSettingsForm.vue](admin/frontend/src/views/slang/components/SlangSettingsForm.vue) 加输入项
- [admin/frontend/src/views/slang/components/SlangSettingsDrawer.vue](admin/frontend/src/views/slang/components/SlangSettingsDrawer.vue) 同步加输入项

**审计可见性修正**：当前 kept 分支 [services/slang/backlog_reviewer.py:407](services/slang/backlog_reviewer.py#L407) 用 `record_revision=False` —— 不写 revision 行。streak 触发的 mute 必须 `record_revision=True`，否则用户事后 grep `reason='backlog_kept_streak_exceeded'` 翻案时查不到 LLM 的连续两次评分上下文。同时建议在 meta 里追加 `backlog_kept_history: list[{run_id, confidence, reason, at}]`（最多保留 3 条）便于翻案。

**风险**：业务侧 — 极小概率把"上下文不足、过几天才能判清楚"的真黑话误 mute。`reason="backlog_kept_streak_exceeded"` 留痕便于事后翻案。

### P0-4：前端 tab 重排 + 砍作用域过滤 ★★

**目标 tab 序列**（7 个）：

```
待审核 N₁  AI 审核 50  待观察 X  AI 否决 Y  已批准 Z  语义漂移 6  全部
```

| Tab | SQL 条件（基于 P0-2 新契约） | 含义 |
|---|---|---|
| 待审核 | `status='candidate' AND NOT _ai_reviewed_sql_condition()` | 纯人工待批，AI 没看过（含被 P0-1 频次门槛挡住的低频词） |
| AI 审核 | `status='approved' AND _ai_approved_sql_condition() AND NOT _human_reviewed_sql_condition()` | AI 已升 approved、人没二审过；含 daily_reviewer + backlog_reviewer 两条来源 |
| **新增** 待观察 | `status='candidate' AND _ai_reviewed_sql_condition() AND _ai_kept_sql_condition()` | AI 看过、判模糊、留候。鼠标悬停："AI 审核未通过，观察中" |
| **新增** AI 否决 | `status='muted' AND _ai_rejected_sql_condition() AND NOT _human_reviewed_sql_condition()` | **仅** AI 判定否决；不含人工通过 `deny_ai_reviewed_term()` 改写的"人审驳回 AI 通过项"（gpt 审计 High #2） |
| 已批准（改义） | `status='approved' AND _human_reviewed_sql_condition()` | **仅**人工已二审确认 + 人工新增。**注意**：手工新增的 approved 当前不写 `human_reviewed=true`，需要在 [admin/routes/api/slang.py](admin/routes/api/slang.py) 创建/编辑路径补写该字段，否则手工新增的会落入"全部"但不在"已批准"中 |
| 语义漂移 | `slang_drift_reviews` 待处理（独立表，现状不动） | drift |
| 全部 | 无过滤（`slang_terms` 全表，**不含** drift） | 全部 |

**触及文件 — 后端**：

- [services/slang/store.py:1382-1387](services/slang/store.py#L1382-L1387) `review_filter` 新增 `under_observation` / `ai_rejected_only` / `human_reviewed_only` 三个枚举（`ai_approved` / `needs_human_review` / `human_reviewed` 已有）。`under_observation` 内部对应 `_ai_reviewed_sql_condition() AND _ai_kept_sql_condition()`；`ai_rejected_only` 内部对应 `_ai_rejected_sql_condition() AND NOT _human_reviewed_sql_condition()`
- [admin/routes/api/slang.py](admin/routes/api/slang.py) summary 端点回传 4 个新 count 字段（`under_observation_count`、`ai_rejected_count`、`human_reviewed_count`、`needs_human_review_count`）；同时在 term 创建/编辑路径补写 `meta.human_reviewed=true`
- 一次性数据迁移脚本（**P0-2 跑完后再跑这个，先 dry-run**）：

```sql
-- 修复 H1：补齐历史人工新增 approved 的 human_reviewed 标记
-- 关键：必须先排除 P0-2 已补齐 ai_review_decision='approved' 的 backlog 已 approved 项，
-- 否则 43 条 backlog approved 会被误标人工审核。
-- 每条 NOT LIKE 都用紧凑/带空格双格式，否则 SQLite json_set 输出（紧凑）会逃过过滤。
UPDATE slang_terms
SET meta_json = json_set(meta_json, '$.human_reviewed', json('true'))
WHERE status = 'approved'
  AND meta_json NOT LIKE '%"human_reviewed": true%'
  AND meta_json NOT LIKE '%"human_reviewed":true%'
  AND source != 'ai_auto_review'
  AND meta_json NOT LIKE '%"ai_review_decision": "approved"%'
  AND meta_json NOT LIKE '%"ai_review_decision":"approved"%'
  AND meta_json NOT LIKE '%"ai_approved": true%'
  AND meta_json NOT LIKE '%"ai_approved":true%';
```

**先 dry-run**（备份 DB 上跑）输出受影响行数，对照预期"≈ 158 - 43 = 115 条"，确认无误再上生产。

**触及文件 — 前端**：

- [admin/frontend/src/views/slang/SlangView.vue:79](admin/frontend/src/views/slang/SlangView.vue#L79) 删 `scopeFilter` ref
- [admin/frontend/src/views/slang/SlangView.vue:118](admin/frontend/src/views/slang/SlangView.vue#L118) detail label 里 `term.scope === 'global'` 简化（保留显示作用域，但不依赖 filter ref）
- [admin/frontend/src/views/slang/SlangView.vue:126](admin/frontend/src/views/slang/SlangView.vue#L126) `watch` 数组里删 `scopeFilter`
- [admin/frontend/src/views/slang/SlangView.vue:148](admin/frontend/src/views/slang/SlangView.vue#L148) `buildParams` 删 scope 注入
- [admin/frontend/src/views/slang/SlangView.vue:142-157](admin/frontend/src/views/slang/SlangView.vue#L142-L157) `buildParams` 新增 5 个 review_filter 分支
- [admin/frontend/src/views/slang/SlangView.vue:358](admin/frontend/src/views/slang/SlangView.vue#L358) / :422 / :646 删 `scopeFilter.value = ...` 三处赋值
- [admin/frontend/src/views/slang/SlangView.vue:758](admin/frontend/src/views/slang/SlangView.vue#L758) 删 `v-model:scope-filter` 绑定
- [admin/frontend/src/views/slang/components/SlangQueueToolbar.vue:21](admin/frontend/src/views/slang/components/SlangQueueToolbar.vue#L21) 删 `defineModel('scopeFilter')`
- [admin/frontend/src/views/slang/components/SlangQueueToolbar.vue:96](admin/frontend/src/views/slang/components/SlangQueueToolbar.vue#L96) 删 NSelect 和 `SCOPE_OPTIONS` 常量
- [admin/frontend/src/views/slang/components/SlangQueueToolbar.vue:36-50](admin/frontend/src/views/slang/components/SlangQueueToolbar.vue#L36-L50) NTag 添加；待观察那一项包 NTooltip
- [admin/frontend/src/views/slang/helpers/types.ts:10](admin/frontend/src/views/slang/helpers/types.ts#L10) `SlangQueueMode` 加新枚举

**布局检查**：当前 SlangQueueToolbar 是 5 个 tab + 一个右侧 NSelect。砍 NSelect 后多空间能容纳 7 tab。但低分辨率可能换行——验收时在 1280 / 1366 / 1920 三档宽度下截图确认；若挤占则把"语义漂移"折进右侧 NDropdown 或 sidebar。

**数据迁移说明（写入维护日志）**：

落地后变化：

- "AI 否决"tab 数字会从 0 跳到几条到几十（DB 实测当前 `backlog_rejected=3`，若 P0-3 跑一周后约几十~一百），用户看到要预期。
- "已批准"tab 数字会从 158 降到 ~115（剩余的 43 条 backlog approved 移到"AI 审核"），不是删数据，是重分桶。
- 维护日志要明确写"这是迁移现象、不是新增否决/丢失批准"。

**关于"7 tab 合计 ≠ 全部"**（gpt 审计 Medium #3）：

- 语义漂移走 `slang_drift_reviews` 独立表，不与 term 桶互斥。
- 普通 muted（extractor 噪声 / 人工静音）和 expired 不在 7 个 tab 互斥分桶里，只能在"全部"看到（deepseek L2 同一现象）。
- **不要以"7 tab 合计 = 全部"作为验收**——改为"5 个 term tab（待审核 / AI 审核 / 待观察 / AI 否决 / 已批准）互斥覆盖 `status ∈ {candidate, approved, muted}` 中的非孤儿条目；drift 独立计数；剩余 muted/expired 通过'全部'兜底"。

**前提**：必须先做 P0-2，否则 SQL 命中失败。

### P1-1：加"反向重申"prompt 校验（MaiBot 双 prompt 同思路）

**改动**：[services/slang/daily_reviewer.py:406-414](services/slang/daily_reviewer.py#L406-L414) `_assess` 之后多一次轻 prompt — "只给词、不给上下文，LLM 能解释吗？能且解释和有上下文那次基本一致 → 不算群内黑话，降级为否决"。

**触及文件 — 后端**：

- [services/slang/daily_reviewer.py](services/slang/daily_reviewer.py) 加 `_naked_assess` helper + 在主流程后调用
- 同样集成到 [services/slang/backlog_reviewer.py](services/slang/backlog_reviewer.py) `_assess` 之后
- 共享 prompt 提到模块级常量

**预期 token 影响**：每条多一次 LLM 调用，但 prompt 极短（仅词条本身 + 一行 system）— 输入 token < 100，比一次 web_search 便宜得多。

**风险**：可能把"普通词但群里有特殊用法"的边界案例误判（如"超舟" — 字面意思有，但群内有专属上下文）。可加置信度门槛：只有"两次解释非常相似且 LLM 自信度 > 0.7"才否决，否则维持原判。

### P1-2 群内 UGC 自验代替 web_search 默认值

**改动**：把现在 `daily_ai_review_search_enabled=True` 默认值改成 `False`；新增 `daily_ai_review_local_evidence_count: int = 5` 和 `backlog_review_search_enabled: bool = False`（双开关分离，详见下文）。

**算法**：在 `_assess` 之前，针对候选词去 `MessageLog` 拉**该群另外 5 条命中过该词的消息**（按 `usage_count` 排序，去重），加进 prompt 上下文。LLM 起草定义后再多一个轻 check："这 5 条消息能用你给的定义解释吗？"

**前提**：候选词必须有 `usage_count >= 3`（P0-1 的门槛刚好），否则没足够 UGC 自验。

**触及文件 — 后端**：

- [services/slang/daily_reviewer.py](services/slang/daily_reviewer.py) `_assess` 输入 context 改造
- [services/slang/types.py](services/slang/types.py) 加 `daily_ai_review_local_evidence_count` 和 `backlog_review_search_enabled`

**触及文件 — 前端配置**（gpt 审计 Medium #5）：

- [admin/frontend/src/views/slang/helpers/types.ts](admin/frontend/src/views/slang/helpers/types.ts) `SlangSettings` 接口加字段
- [admin/frontend/src/views/slang/helpers/formatters.ts](admin/frontend/src/views/slang/helpers/formatters.ts) `DEFAULT_SLANG_SETTINGS` 加默认值
- [admin/frontend/src/views/slang/components/SlangSettingsForm.vue](admin/frontend/src/views/slang/components/SlangSettingsForm.vue) 加输入项
- [admin/frontend/src/views/slang/components/SlangSettingsDrawer.vue](admin/frontend/src/views/slang/components/SlangSettingsDrawer.vue) 同步加输入项

**预期**：比 web_search 更准（针对本群语境），省 web_search 配额；但不彻底替代 — web_search 留作"群内无证据时降级用"。

**新词复核退化风险**：daily_reviewer 现在主要靠 web_search 给"刚出现 1 次"的新词补语境。默认改 False 后，新词复核首批的 LLM 会失去外部信息源、可能更保守。**缓释**：daily_reviewer 走"新词单次出现"路径时仍默认开 web_search（局部 override），只在 backlog reviewer 走"已多次出现的存量"路径时默认关 —— 因为存量必有 usage_count ≥ 3，能拉 UGC 自验。两条路径分别配置 `daily_ai_review_search_enabled` / `backlog_review_search_enabled` 两个独立开关，避免一刀切影响新词。

### P2-1：jieba 已知词词典剔除（NeoN 思路，需改造）

**改动**：抽取阶段 [services/slang/extractor.py](services/slang/extractor.py) 拒绝 LLM 抽出的"完全在通用词典里"的词，除非有特殊用法证据。

**核心约束（NeoN 用 jieba 不直接work）**：jieba 默认词典是**分词词典**，不是"已知词义词典"。很多网络黑话（"摆烂""内卷""超舟"）已经在 jieba 词典里——直接按"在 jieba 词典里就剔除"会误杀真黑话。

**修正实施**：
- jieba 词典只用于**分词验证**：候选词 jieba 切分后是单一 token → 才考虑通用词剔除；如果切成多 token → 是新组合词，不算通用词
- 真正的"已知词义"参考词库另建：项目内累积的 `human_reviewed=true` 且 `ai_approved=false` 的人工驳回词条 → 进白名单"通用词"；社区维护的"网络梗白名单"用作豁免
- 仍可加 `jieba` 但定位是辅助分词，不是 reference corpus 主源

**预期**：从源头减少 30-50% 的噪声候选——但前提是先积累 ~200 条人工驳回词条作 reference。冷启动期效果有限。

**风险**：jieba 词典体积约 5MB；首次启动加载耗时秒级。可选 lazy load。误杀风险通过单一 token + 人工驳回库双校验降低。

### P2-2：频次阶梯重判（MaiBot 思路）

**改动**：把"全量轮询"backlog reviewer 改成"按 count 阶梯触发"。

**算法**：候选词每次新增使用都更新 `usage_count`；只有跨过 `[3, 8, 30, 100]` 任一档时才进入"待复审"队列。已经判过的词不再复审，除非 count 又跨档。

**触及文件**：
- [services/slang/store.py](services/slang/store.py) 加 `last_inference_count` 字段（meta 里）
- [services/slang/backlog_reviewer.py](services/slang/backlog_reviewer.py) `list_backlog_candidates` 加 `count_threshold_crossed` 过滤
- [services/slang/extractor.py](services/slang/extractor.py) usage 写入路径增加阶梯标记

**预期**：彻底告别"反复跑同一批 kept 词烧 token"，token 消耗从 O(总候选数) 降到 O(频次跨档事件数)。

**前提**：P0-1 已落地（频次门槛是这个的特例）。

---

## 五、不要做（业界踩过坑）

### P3-1：纯统计 PMI/熵替换 LLM

群聊语料量太小，PMI/熵不稳定（colinwke / JunyuMao 实验结论）。MaiBot 早期试过，放弃。

### P3-2：纯 web_search 决定

通用互联网的"梗"和"群内黑话"是两个概念（CHEER 论文专门讨论）。web_search 应是辅助、不是主路径。

### P3-3：直接 disable backlog reviewer

会导致 50 条 AI 已批进不了人审视野（已批 tab 现在和 AI 审核 tab 视觉重叠，用户分不清哪些是 AI 给的）。修方向是 P0-4 的 tab 分桶，不是关功能。

---

## 六、落地顺序与验收

```
Day 1（P0 契约 + 数据迁移）：
└── P0-2 AI review 契约定义 + 三路径统一 + SQL helper 拆分 + 历史 backlog/daily meta 迁移（在备份 DB dry-run）

Day 2（P0 主体）：
├── P0-1 频次门槛（store + types + backlog_reviewer + admin api summary 四处 + 前端 settings 4 件）
├── P0-3 kept streak 自动降级（backlog_reviewer 一处 + types + 前端 settings 4 件 + 测试）
└── P0-4 前端 tab 重排 + 砍作用域（store 1 处 + admin api 1 处 + 5 个 vue 文件 + 一次性补 human_reviewed 迁移）

Day 3-5（P1，按需）：
├── P1-1 反向重申 prompt（双 reviewer 改造）
└── P1-2 群内 UGC 自验（默认值翻转 + 主路径改造 + 前端 settings 4 件）

Future（P2，看效果再说）：
├── P2-1 jieba 词典剔除
└── P2-2 频次阶梯重判
```

### 验收信号

每个 P0 项都要满足 D4：完成声明含证据。

| 项 | 通过证据 |
|---|---|
| P0-1 | 验收用 `eligible_backlog_count` 跑前后对比（候选池总数 `candidate_count` 不会因频次门槛改变，gpt 审计 Medium #4）；新加配置项默认值 = 3，可在 settings 调；前端 4 件 settings 文件已同步 |
| P0-2 | 跑一轮 backlog reviewer + 迁移脚本后，`SELECT COUNT(*) FROM slang_terms WHERE _ai_reviewed_sql_condition()` 应明显增长（包含 backlog 三路径处理过的所有词条）。**验收用同一个 `_ai_reviewed_sql_condition()`**，不要用 `LIKE '%ai_reviewed_at%'` 这种与生产 SQL 不一致的近似条件。`ai_review_decision` 三类（approved / rejected / kept）数量与 dry-run 输出一致 |
| P0-3 | 一周后 muted 桶里 `reason='backlog_kept_streak_exceeded'` 的条目应有几十条；同步 candidate 桶应明显收缩；翻案路径：`SELECT meta_json FROM slang_terms WHERE term_id=?` 看 `backlog_kept_history` 数组；revision 表能查到对应行（`record_revision=True` 已生效） |
| P0-4 | 前端 7 个 tab 都能切换，5 个 term tab（待审核 / AI 审核 / 待观察 / AI 否决 / 已批准）count 加起来 ≈ `candidate + approved + muted_with_ai + non_orphan_else`，**不要要求 7 tab 合计 = 全部**（drift 独立、extractor 噪声 muted 不在分桶内，gpt 审计 Medium #3 + deepseek L2）；待观察 tag 鼠标悬停有提示文字；1280 / 1366 / 1920 三档宽度截图无换行/截断 |

---

## 七、参考资料

### 同类项目源码
- [MaiBot bw_learner（本地）](file:///Users/kragcola/MaiM-with-u/MaiBot/src/bw_learner/) — 双 prompt 自比 + 频次阶梯
- [colinwke/new_words_discovery](https://github.com/colinwke/new_words_discovery) — PMI + 熵
- [JunyuMao/New-word-discovery](https://github.com/JunyuMao/New-word-discovery) — PMI + 左右熵 + 词位概率

### 论文
- [SLANG: New Concept Comprehension of LLMs (EMNLP 2024)](https://arxiv.org/abs/2401.12585) — 因果推理增强
- [Can LLMs Understand Internet Buzzwords Through UGC (ACL 2025)](https://arxiv.org/abs/2505.15071) — CHEER 数据集 + RESS 方法
- [NeoN: Detection and LLM-Driven Analysis of Polish Neologisms (2025)](https://arxiv.org/abs/2505.15426) — 参考语料对照管线
- [NEO-BENCH: Evaluating LLM Robustness with Neologisms (ACL 2024)](https://acl.ldc.upenn.edu/2024.acl-long.749/) — 评测基准
- [Knowledge of Slang in LLMs](https://arxiv.org/html/2404.02323v1) — slang 评估
- [腾讯云开发者社区：NLP 新词发现](https://cloud.tencent.com.cn/developer/article/2416088)

### 项目内交叉引用
- [docs/maibot-slang-improvement-proposal.md](maibot-slang-improvement-proposal.md) — 早期 MaiBot 视角的改进笔记，本提案是其延续与收束
- [docs/slang-module-implementation-tracker.md](slang-module-implementation-tracker.md) — 实施跟踪
- [maintenance-log.md](../maintenance-log.md) — 维护日志（含 5/16 死循环修复条目）

---

## 八、自审记录（2026-05-16）

文档初稿写完后做了两轮内部自审。每轮发现的问题已在正文修正，此处保留过程便于追溯。

### 第一轮自审（5 项）

| # | 类别 | 发现 | 修正位置 |
|---|---|---|---|
| 1 | 致命 | P0-2/P0-4 SQL 断开：`_ai_review_sql_condition()` 不查 `ai_reviewed_at`，单修 meta 不能让 tab 工作 | 第二轮升级，见 P0-2 + P0-4 |
| 2 | 逻辑 | P0-1 风险缓释错指 P0-3：两者解决不同问题（进不了 review vs 进了反复 kept） | 已修 P0-1 风险段 |
| 3 | 行号错误 | `_assess` 在 [daily_reviewer.py:406](services/slang/daily_reviewer.py#L406)，文档原写 289-352 | 已修 |
| 4 | 行号错误 | `list_backlog_candidates` 在 [store.py:1413+](services/slang/store.py#L1413)，文档原指 list_terms 区间 | 已修差异 4 段 |
| 5 | 表述模糊 | "approve 路径已经在 store.py:1062-1063 隐式写过"——实际只在 daily_reviewer，backlog approve 也漏 | 第二轮重写时一并修正 |

### 第二轮自审（重读后发现强度不够）

第一轮把"approve 路径漏写"评为低严重度——重读后真相是：**backlog_reviewer 三条路径（approve / mute / kept）全部只写 `meta.backlog_review.*`，全部不写 `ai_approved=true`**。SQL 条件完全不认这个键。后果级联：

- backlog 已 approve 的 43 条混进"已批准"和人工新增的混在一起
- backlog 已 mute 的混进"muted"和 extractor 噪声混在一起
- backlog kept 的回到"待审核"和纯候选混在一起

第一轮还漏了：

1. P0-3 kept 路径用 `record_revision=False`——streak 触发 mute 后无审计行可翻案 → 已修 P0-3 加 `record_revision=True` 和 `backlog_kept_history` meta
2. P0-2 验收信号 `LIKE '%ai_reviewed_at%'` 与生产 SQL 不一致 → 已修验收信号统一用 `_ai_review_sql_condition()`
3. P0-4 触及文件清单只列了 SlangView，遗漏 SlangQueueToolbar 的 `defineModel` 和 NSelect → 已补全 9 处前端清理点
4. P0-4 "已批准" filter `human_reviewed` 排除手工新增 → 已加一次性数据迁移脚本
5. P1-2 默认值翻转影响 daily_reviewer 新词复核质量 → 已加双开关分离方案
6. P2-1 jieba 词典做 reference corpus 的精度高估（"摆烂""内卷"已在 jieba） → 已修为"jieba 仅辅助分词 + 人工驳回库作 reference"

### 元教训

第一轮自审"找到方向但深度不够"——SQL 条件这一致命点找到了，但没意识到它的真正破坏面是 backlog_reviewer 三路径全部对齐失败。**多模型/多轮自审在文档落地前是必要质量保障**，单轮容易把"修了一个点"和"修了根因"混淆。

---

## 九、外部审计记录（2026-05-16）

**审计人**：gpt

**审计范围**：
- 本文档中的 P0/P1/P2 改造提案、落地顺序和验收信号。
- 当前实现里的 `services/slang/store.py`、`services/slang/backlog_reviewer.py`、`services/slang/daily_reviewer.py`、`admin/routes/api/slang.py` 和 `admin/frontend/src/views/slang/`。
- 重点核对 SQL 分桶、AI review meta 契约、人工复核语义、前端 tab/filter 参数和设置页配置项。

**总体结论**：
方案方向成立：先修 AI 印记和分桶，再做频次门槛与 kept streak，P1 再推进双 prompt 与本群 UGC 自验。但 P0 落地前需要先把“AI reviewed”定义收敛成一个明确、可复用、可验收的契约，否则 tab、验收 SQL 和迁移脚本会继续互相打架。

### 审计发现

| 严重度 | 问题 | 证据 | 建议 |
|---|---|---|---|
| High | P0-2 与 P0-4 的 SQL 契约拆错阶段，Day 1 验收会失败。P0-2 写 `ai_approved=false` 后，当前 `_ai_review_sql_condition()` 仍只认 `source='ai_auto_review'` 或 `ai_approved=true`，mute/kept 不会被生产 SQL 命中。 | `services/slang/store.py::_ai_review_sql_condition()` 只匹配 true；本文 P0-2 写“SQL 条件无需改动”，P0-4 才补 false。 | 把“AI reviewed 条件识别 false / ai_reviewed_at / review decision”前移到 P0-2。P0-2 的验收必须在生产同款条件下覆盖 approve/mute/kept 三路径。 |
| High | “AI 否决”tab 条件会混入“人工否决 AI 通过项”。现有 `deny_ai_reviewed_term()` 会把 AI 通过项改成 `muted`，同时写 `human_reviewed=true`，并保留 AI 印记。 | `services/slang/store.py::deny_ai_reviewed_term()` 写 `human_reviewed=true` + `status='muted'`；本文 P0-4 定义 `AI 否决 = status='muted' AND _ai_review_sql_condition()`。 | `AI 否决` 条件改为 `status='muted' AND ai_reviewed AND NOT human_reviewed`，或引入明确字段 `ai_review_decision='rejected|kept|approved'`，避免把人审结果误标成 AI 否决。 |
| Medium | P0-4 “7 个 tab count 加起来 = 全部”验收不可成立。普通 muted、expired、人工静音、extractor 噪声不在 7 个 tab 的互斥分桶内；`语义漂移` 还来自 `slang_drift_reviews`，不是 `slang_terms` 同一集合。 | 当前前端 `totalQueueCount = candidate + approved + muted + expired`；summary 中 `drift_count` 单独查 drift 表。 | 改验收为“term 分桶互斥覆盖 + drift 单独计数”，或新增“静音/过期”分桶。不要要求 drift 与 term tab 合计等于全部。 |
| Medium | P0-1 的验收指标写成 candidate 总数会误导。频次门槛只过滤 backlog reviewer 可复核池，不会改变 `status='candidate'` 总量。低频词仍会留在候选池和待审核视图里。 | `summary()` 的 `candidate_count` 是 `slang_terms.status='candidate'` 总数；P0-1 只计划给 `list_backlog_candidates()` 加 `usage_count >= N`。 | 验收指标改成 `eligible_backlog_count` 跑前/跑后对比；必要时在 summary/API 里单独暴露“清池可复核数 / 被频次门槛暂缓数”。 |
| Medium | 新配置项的前端触及范围漏列。设置页不是 schema 自动渲染，而是手写表单；只改 `SlangSettings` 后，用户无法在页面调新字段。 | `SlangSettingsDrawer.vue`、`SlangSettingsForm.vue` 手写 `backlog_review_batch_size` / `backlog_review_min_confidence` 等字段；`helpers/types.ts` 和 `helpers/formatters.ts` 手写类型与默认值。 | P0/P1 新增设置时同步改 `helpers/types.ts`、`helpers/formatters.ts`、`SlangSettingsDrawer.vue` 和 `SlangSettingsForm.vue`。新增 `backlog_review_min_usage_count`、`backlog_kept_streak_limit`、`daily_ai_review_local_evidence_count`、`backlog_review_search_enabled` 都要进这一组。 |

### 建议调整后的 P0 顺序

1. **P0-A：定义 AI review 契约**
   先统一 meta 字段与 SQL helper，例如：
   - `ai_reviewed_at`
   - `ai_review_source = daily|backlog`
   - `ai_review_decision = approved|rejected|kept`
   - `ai_approved = true|false` 仅作为兼容字段

2. **P0-B：迁移历史 backlog_review meta**
   对已有 `meta.backlog_review` 补齐顶层 AI review 字段。迁移脚本需要先在备份 DB 上 dry-run，并输出 approve/rejected/kept 数量。

3. **P0-C：backlog 频次门槛 + eligible count**
   `count_backlog_candidates()` 和 `list_backlog_candidates()` 同步接收 `min_usage_count`，避免状态进度和实际拉取池不一致。

4. **P0-D：kept streak 自动降级**
   kept 分支记录 `backlog_kept_history`，streak 触发 mute 时必须写 revision，便于翻案。

5. **P0-E：前端分桶改造**
   在后端 review filters 和 summary counts 稳定后，再改 7 tab。验收时把 drift 当独立治理队列，不并入 term 分桶合计。

### 审计限制

本次尝试用本地 `storage/slang.db` 做统计复验时，`sqlite3` 返回 `database disk image is malformed`。因此本节没有采用当前 DB 实测数字作为证据，只基于文档和代码契约审计。后续落地迁移前，应先按项目既有恢复流程修复或替换可读 DB，再重新跑统计 SQL。

---

## 十、外部审计记录（2026-05-16，deepseek）

**审计人**：deepseek

**审计范围**：同第九节——文档全量 + [services/slang/store.py](services/slang/store.py)、[services/slang/backlog_reviewer.py](services/slang/backlog_reviewer.py)、[services/slang/daily_reviewer.py](services/slang/daily_reviewer.py)、[admin/frontend/src/views/slang/](admin/frontend/src/views/slang/)。重点核对：meta 写入路径与 SQL 条件的契约一致性、迁移脚本覆盖范围、行号引用准确性。

**总体结论**：gpt 的 5 条审计全部成立，本文不再重复。补充 8 条 gpt 未覆盖或深度不同的发现。

### 致命（2 条，不改会炸）

| # | 严重度 | 问题 | 位置 | 建议 |
|---|---|---|---|---|
| H1 | 致命 | **P0-4 迁移脚本污染 43 条 backlog approved**：行 154 的 `UPDATE SET human_reviewed=true WHERE status='approved' AND source != 'ai_auto_review'` 会命中 backlog_reviewer 已 approve 的条目（它们 status='approved'、无 human_reviewed、source 非 ai_auto_review）。43 条被误打 human_reviewed，从"AI 审核"tab 消失、在"已批准"tab 冒充人工审核 | [P0-4 数据迁移段](docs/slang-governance-research-2026-05-16.md) 行 154 | 追加 `AND meta_json NOT LIKE '%"ai_approved": true%'`。P0-2 运行后所有 AI-approved 的 meta 都有 `ai_approved: true` |
| H2 | 致命 | **P0-2 声称"SQL 无需改动"与 P0-4 行 150-151 矛盾**：行 99 写"方案 A — SQL 条件无需改动"，但行 150-151 明确需追加 `OR LIKE '%"ai_approved": false%'`。如果 mute/kept 写 `ai_approved: false`，只 LIKE true 的 SQL 不命中 | [P0-2 修复段](docs/slang-governance-research-2026-05-16.md) 行 99 vs [P0-4 触及文件](docs/slang-governance-research-2026-05-16.md) 行 150-151 | 方案 A 改成：三路径统一写 `ai_reviewed_at` 标记"AI 是否审过"，不依赖 `ai_approved` 判断审核状态。SQL 条件单加 `OR meta_json LIKE '%"ai_reviewed_at"%'`。比 LIKE false 干净，语义和实现不耦合 |

### 重要（3 条，不改有后果）

| # | 严重度 | 问题 | 位置 |
|---|---|---|---|
| M1 | 重要 | **P1-1 行号未同步**：行 178 仍写 `daily_reviewer.py:289-352`，但 `_assess` 已确认在 406-414。第三章第 40 行修过了，P1-1 段漏修——同文档一处对一处错 | [P1-1 改动段](docs/slang-governance-research-2026-05-16.md) 行 178 |
| M2 | 重要 | **P0-4 布局检查尺寸重复**：行 169 "1280 / 1024 / 1280"——1280 写了两遍，应为 "1280 / 1366 / 1920" | [P0-4 布局检查](docs/slang-governance-research-2026-05-16.md) 行 169 |
| M3 | 重要 | **落地顺序 Day 1 描述脱节**：行 258 仍写"P0-2 kept 写 ai_reviewed_at（10 行 .py）"，但 P0-2 已扩为三路径 + 迁移脚本，不止 10 行也不止 kept | [落地顺序](docs/slang-governance-research-2026-05-16.md) 行 258 |

### 轻微（3 条）

| # | 严重度 | 问题 | 位置 |
|---|---|---|---|
| L1 | 轻微 | **P0-2 迁移脚本只给目标没给 SQL**：P0-4 的迁移给了完整 SQL，P0-2 行 111 只说"写一次性 migration 脚本扫一遍...补上"——同文档同一风格应给齐 | [P0-2 触及文件](docs/slang-governance-research-2026-05-16.md) 行 111 |
| L2 | 轻微 | **"AI 否决" tab 覆盖不到 extractor 噪声 muted**：`status='muted' AND ai_reviewed` 只覆盖 AI 否决的，extractor 噪声 muted（未被 AI 看过）只出现在"全部"——没有独立 tab | [P0-4 tab 定义](docs/slang-governance-research-2026-05-16.md) 行 143 |
| L3 | 轻微 | **差异 3 提 jieba 未同步 P2-1 修正**：行 56 仍正面建议"jieba 默认词典"，但 P2-1（行 210）已明确否定 jieba 做 reference corpus。读者看完第三章觉得 jieba 好用，翻到第四章发现踩坑 | [差异 3](docs/slang-governance-research-2026-05-16.md) 行 56 |

### 与 gpt 审计的交叉覆盖

- H1、H2 为 net-new 发现，gpt 未覆盖。
- M3 与 gpt 的"Day 1 验收会失败"(High #1) 互补——gpt 关注的是 SQL 契约阶段错位，M3 关注的是 Day 1 描述文字的过时。
- gpt 的"AI 否决混入人工否决"(High #2) 不重复——本文 L2 是 muted 覆盖缺口，gpt 指出的是 `deny_ai_reviewed_term()` 写入 `human_reviewed=true` 后仍在 `ai_reviewed` 过滤中可见。两条都是对的但指向不同问题。
- gpt 的"7 tab 合计 = 全部不可验收"(Medium #3) 和"频次门槛验收指标误导"(Medium #4) 不重复。

### 审计限制

本次审计未跑 DB 验证——[gpt 审计已报告 storage/slang.db 损坏](docs/slang-governance-research-2026-05-16.md#审计限制)，所有代码引用以源文件为准。

---

## 十一、根据 gpt + deepseek 审计的方案修订（2026-05-16）

按"对方案自审 → 外部审计两轮 → 落实修订"流程做完最后一轮整合。本节记录两份审计 13 条发现各自如何被吸收。

### gpt 审计（5 条）落实情况

| 严重度 | 发现 | 落实位置 |
|---|---|---|
| High #1 | P0-2/P0-4 SQL 契约阶段错位 | 已在 P0-2 引入 `_ai_reviewed_sql_condition()` / `_ai_approved_sql_condition()` / `_ai_rejected_sql_condition()` 三个 helper，Day 1 一次定义；旧 `_ai_review_sql_condition()` 改名退役 |
| High #2 | "AI 否决"混入人工驳回 AI 通过项 | P0-4 "AI 否决"tab SQL 改为 `status='muted' AND _ai_rejected_sql_condition() AND NOT _human_reviewed_sql_condition()` |
| Medium #3 | 7 tab 合计 ≠ 全部 | P0-4 增"关于 7 tab 合计"段落，验收信号改为 5 term tab 互斥覆盖、drift 独立、extractor 噪声 muted 通过"全部"兜底 |
| Medium #4 | P0-1 验收指标误导 | P0-1 + 验收信号都改成 `eligible_backlog_count`；admin api summary 端点新增该字段 |
| Medium #5 | 新配置项前端触及范围漏列 | P0-1 / P0-3 / P1-2 三处都补全前端 4 件清单（types.ts / formatters.ts / SettingsForm.vue / SettingsDrawer.vue） |

### deepseek 审计（8 条）落实情况

| 严重度 | 发现 | 落实位置 |
|---|---|---|
| H1 | P0-4 迁移脚本污染 43 条 backlog approved | P0-4 迁移 SQL 加 `AND meta_json NOT LIKE '%"ai_review_decision": "approved"%' AND meta_json NOT LIKE '%"ai_approved": true%'`；并要求 dry-run 后再上生产 |
| H2 | P0-2 "SQL 无需改动"与 P0-4 矛盾 | P0-2 完全重写：用 `ai_reviewed_at` 表达"AI 审过"、用 `ai_review_decision` 表达结论，不再依赖 `ai_approved=false`；SQL helper 拆三个，无矛盾 |
| M1 | P1-1 行号未同步 | 已修为 `daily_reviewer.py:406-414` |
| M2 | 布局尺寸重复 | 已修为 1280 / 1366 / 1920 |
| M3 | Day 1 描述脱节 | 落地顺序整段重排：Day 1 = P0-2 契约 + 迁移；Day 2 = P0-1/P0-3/P0-4 主体 |
| L1 | P0-2 迁移脚本只给目标没给 SQL | P0-2 新增完整 SQL（两段 UPDATE，分别处理 backlog 三路径和 daily approve），含 dry-run 要求 |
| L2 | "AI 否决"tab 覆盖不到 extractor 噪声 muted | P0-4 "关于 7 tab 合计"段落明确说明：extractor 噪声 muted 通过"全部"兜底，不强行加单独 tab |
| L3 | 差异 3 提 jieba 与 P2-1 矛盾 | 差异 3 改为说"照搬 NeoN 用通用词典在中文场景会误杀真黑话，详见 P2-1" |

### 第三轮自审（修订后再读，2 项）

| # | 类别 | 发现 | 修正位置 |
|---|---|---|---|
| 1 | 设计一致 | P0-3 streak 触发 mute 时，原文档的 meta `ai_review_decision='kept'` 与 status='muted' 矛盾——streak mute 的语义是 AI 最终否决，应改写 decision 字段 | P0-3 加"streak ≥ 2 时改写 `ai_review_decision='rejected'`" |
| 2 | 字段语义 | `ai_approved` 字段在新契约下变成兼容字段（与 `ai_review_decision='approved'` 等价），daily_reviewer 仍写 `ai_approved=true`；backlog_reviewer 不再写——避免双源真相 | P0-2 表格已说明"仅 daily_reviewer 写（保留向后兼容）" |

### 第四轮审计（修订后第二次外部审计，4 项 net-new）

整合修订完后又跑一次审计，发现修订动作本身引入 4 条新缺陷。每条都已在正文落实。

| # | 严重度 | 发现 | 落实位置 |
|---|---|---|---|
| N1 | **致命** | 新 SQL helper 全部漏 LIKE 紧凑/带空格双格式（既有 `_ai_review_sql_condition()` 早就双格式，line 357-358）。Python `json.dumps` 输出带空格、SQLite `json_set` 输出紧凑，单格式会让一种来源的写入永远不命中。P0-4 迁移 NOT LIKE 同漏，43 条污染风险**未真消** | P0-2 helper 三个全部双格式 + 新增 `_ai_kept_sql_condition()`；P0-4 迁移 NOT LIKE 双格式；P0-2 迁移 NOT LIKE 双格式；正文加"LIKE 双格式纪律"明文段 |
| N2 | **致命** | P0-2 第一段迁移 CASE 用 `status` 推 `ai_review_decision`，但"backlog 通过→人工驳回"的项 status='muted' 而 `backlog_review.approved=1`，会被误标 decision=rejected，违反"AI decision = AI 当时的结论"语义，污染未来基于 `ai_review_decision` 的 AI 通过率/否决率统计 | P0-2 CASE 改为直接读 `backlog_review.approved` 字段，不依赖最终 status；注释说明语义 |
| N3 | 重要 | P0-2 第二段 `WHERE NOT LIKE '%"ai_reviewed_at":%'` 在当前生产 DB 上 0 行命中——`upsert_ai_approved_term` 已写过 `ai_reviewed_at`（[store.py:1063](services/slang/store.py#L1063)），daily 50 条永远拿不到 `ai_review_source`/`ai_review_decision`，统计指标漏数据 | 第二段 WHERE 改为 `NOT LIKE ai_review_source`（双格式），让 50 条真正补齐；不再写 `ai_reviewed_at`（已存在） |
| N4 | 轻微 | 第二段迁移 fallback 用 `datetime('now')` 把"迁移当时"误标成 AI review 时间，事后审计追溯失真 | 第二段不再写 `ai_reviewed_at`，问题自然消失；第一段 fallback 链拉长为 `COALESCE(backlog_review.reviewed_at, updated_at, created_at)` 用表自带列兜底 |

### 修订后的元教训

第二轮自审"找到了 SQL 契约根因但深度不够"——具体表现是把 backlog approve / mute / kept 三路径都写 `ai_approved=true/false` 当方案 A，结果引入了"`ai_approved=false` 既表达 AI 审过也表达 AI 否决，与 deny_ai_reviewed_term 的人工否决场景互相打架"的二次缺陷，需要 deepseek H2 + gpt High #2 两个独立审计联手才能识别。**真正的设计教训**：把"是否审过"和"审核结论"两件事混在一个布尔字段里，是这一切混乱的源头。本次修订用 `ai_reviewed_at` + `ai_review_decision` 两个独立字段彻底解耦，是方案稳定性的关键转折。

第四轮审计的新教训：**写新 helper 时必须把现有同类 helper 当强参照样本**。`_ai_review_sql_condition()` 早就为了规避 SQLite/Python JSON 序列化空格差异写了两遍 LIKE，第二轮修订却忘了这个模式——根因是动笔时只看新需求、没回头看现有代码做了什么、为什么这么做。同样的纪律也适用于迁移 SQL：`json_set()` 的输出格式、`COALESCE` 的 fallback 链、CASE 不依赖最终 status 而读源字段——这些都是写过一次后能形成的"该项目的 SQL 风格"，不能每次都重新踩一遍。

### 第五轮审计（修订动作之二，3 项 net-new）

**审计人**：claude

**审计范围**：第四轮 N1-N4 的修订动作落地后再读一遍方案 + 抽样核实 [services/slang/store.py:354-358](services/slang/store.py#L354-L358) 现有 helper、[store.py:1063](services/slang/store.py#L1063) `upsert_ai_approved_term` 的 `ai_reviewed_at` 写入、[store.py:1219+](services/slang/store.py#L1219) `deny_ai_reviewed_term` 的 meta 写入路径、[backlog_reviewer.py:340-409](services/slang/backlog_reviewer.py#L340-L409) 三路径 meta_patch 现状，验证修订动作本身没有引入新缺陷。

**总体结论**：第四轮 N1-N4 的修订动作均属实落地，但 N2 重写 CASE 时漏处理了一条边角 case（N5），N4 的 fallback 链表述偏简（N6），P0-2 触及文件清单遗漏读取点盘点（N7）。N5 致使统计失准，N6/N7 是收敛性的轻微改进。

| # | 严重度 | 发现 | 落实位置 |
| --- | --- | --- | --- |
| N5 | **致命** | 第四轮 N2 重写 CASE 直读 `backlog_review.approved`，但漏了边角 case：先 backlog kept（`backlog_review.approved=0`，仍是 candidate）→ 后 daily_reviewer 升 approved（`upsert_ai_approved_term` 写 `ai_approved=true`，status='approved'）。CASE 走向：`approved=1`? 否 → `approved=0 AND status='muted'`? 否 → ELSE → 误标 `decision='kept'`。AI 最终结论实际是 approved，未来基于 `ai_review_decision` 的 AI 通过率统计会少计这部分 | P0-2 第一段 CASE 顶部加最高优先级分支 `WHEN meta_json LIKE '%"ai_approved": true%' OR meta_json LIKE '%"ai_approved":true%' THEN 'approved'`，注释标"第五轮审计 N5"；双格式 LIKE 与正文纪律一致 |
| N6 | 轻微 | 第四轮 N4 把 fallback 拉成 `COALESCE(backlog_review.reviewed_at, updated_at, created_at)` 后没说明三层语义优先级。`updated_at` 因为 `record_hit` 等路径会被刷新，可能晚于真实 review 时间几小时到几天；`created_at` 是 term 首次入库时间，比 review 早是常态。读者直接复用迁移脚本时可能误以为三层等价 | P0-2 迁移段下方加"fallback 时间链语义"说明：`reviewed_at` 首选、`updated_at` 失效次选、`created_at` 末选；事后审计 `reviewed_at` 时优先信任 `meta.backlog_review.reviewed_at`；顶层 `ai_reviewed_at` 仅做"是否审过"判断 |
| N7 | 重要 | P0-2 表格写"新代码读取一律用 `ai_review_decision`"但触及文件清单只列了 reviewer / store helper / 迁移 SQL，**没要求实施时盘点 admin 前后端、其他业务路径里现存的 `meta.ai_approved` 读取点**。backlog_reviewer 在新契约下不再写 `ai_approved`，未盘点漏改会让 backlog approved 在该处"看不见"——属于实施级盲点 | P0-2 触及文件清单追加一行：grep `meta\.ai_approved` / `meta\["ai_approved"\]` / `term\.meta\.ai_approved` / `\.ai_approved` 的所有读取，逐个改读 `ai_review_decision='approved'`；仅留 SQL 层兼容 |

第五轮的元教训：**修订一次也会引入新 bug，所以审计→修订→再审计这条链至少要走两轮**。第四轮发现 N2 后的修订（CASE 直读 `backlog_review.approved`）本身是对的，但漏了"daily 二次升级"这条相对少见的路径——原因是当时只考虑了"backlog 的三路径 + 人工驳回"四个语义，没把 daily 与 backlog 的交叉路径列进矩阵。设计 CASE 时应当**先把所有可能进入这段 SQL 的 meta 状态组合穷举成矩阵**，再按矩阵设分支，而不是按"已知问题反推"。N7 的盲点也是同源：实施侧的扫描清单不能等审计来逐条补，必须在写文档时就把"全仓 grep"作为标准动作。

经此第五轮：单方案的稳定性需要**自审 2 轮 + 外部审计 2 轮 + 修订后再审计 2 轮**——共六轮才能进入实施。这套流程是大规模 meta 契约迁移这种"上线后回滚成本极高"决策的最低防线，对小修小补则不必。本次方案因为涉及 1075 条候选词的 meta_json 永久重写，值这个工序。

---

## 十二、第六轮审计（终审，8 项 net-new）

**审计人**：claude

**审计范围**：

- 第五轮 N5/N6/N7 修订动作落地后再读全文。
- 独立矩阵审计 P0-2 第一段 CASE 在所有可能 meta 状态组合下的命中分支。
- 核实 [services/slang/store.py:354-358](services/slang/store.py#L354-L358) `_ai_review_sql_condition`、[store.py:1025-1177](services/slang/store.py#L1025-L1177) `upsert_ai_approved_term`、[store.py:1195-1217](services/slang/store.py#L1195-L1217) `mark_human_reviewed`、[store.py:1219-1240](services/slang/store.py#L1219-L1240) `deny_ai_reviewed_term`、[store.py:1242-1259](services/slang/store.py#L1242-L1259) `return_ai_reviewed_term_to_candidate`、[store.py:1632-1701](services/slang/store.py#L1632-L1701) `update_term`、[store.py:1719-1738](services/slang/store.py#L1719-L1738) `set_status`、[store.py:2306-2357](services/slang/store.py#L2306-L2357) `summary`、[backlog_reviewer.py:309-411](services/slang/backlog_reviewer.py#L309-L411) `_review_one`、[daily_reviewer.py:406-414](services/slang/daily_reviewer.py#L406-L414) `_assess`、[admin/routes/api/slang.py:438-475](admin/routes/api/slang.py#L438-L475) 状态切换端点、[admin/frontend/src/views/slang/helpers/badges.ts:79-89](admin/frontend/src/views/slang/helpers/badges.ts#L79-L89) `isAiApproved` 等业务读取点。
- 全仓 grep `ai_approved` / `ai_reviewed_at` / `ai_review_decision` / `ai_review_source` 验证 N7 盘点边界。

**总体结论**：N1-N7 的修订动作经独立验证均属实落地（CASE 读源字段、双格式 LIKE、第二段 WHERE 改 ai_review_source、fallback 时间链语义），但本轮在矩阵审计层和实施细节层发现 8 项 net-new 缺陷，其中 1 项致命、2 项重要、5 项轻微。**致命项 O1 揭示了一个共识盲区：第五轮加的 N5 高优先级分支在当前 WHERE 条件下永远不可达，是死代码。**

### 致命（1 项，必须改）

| # | 严重度 | 发现 | 落实建议 |
| --- | --- | --- | --- |
| O1 | **致命** | **N5 高优先级分支在当前 WHERE 条件下永远不可达**。第一段 UPDATE 的 WHERE 是 `meta_json LIKE '%"backlog_review":%' AND meta_json NOT LIKE '%"ai_reviewed_at": "%' AND meta_json NOT LIKE '%"ai_reviewed_at":"%'`。但 `upsert_ai_approved_term` 在 [store.py:1063](services/slang/store.py#L1063) 把 `ai_reviewed_at: now` 写入 `ai_meta`，daily 升级走 [store.py:1095-1107](services/slang/store.py#L1095-L1107) `merged_meta = {**existing.meta, **ai_meta}` 也会把 `ai_reviewed_at` 写进去。换言之：**任何含 `ai_approved=true` 的 row 必含 `ai_reviewed_at`**——必被第一段 NOT LIKE 排除，永远进不到 N5 的 `WHEN ai_approved=true` 分支。N5 解的是不存在的命中场景。真正处理"backlog kept + daily 升级"双源行的是第二段（`LIKE ai_approved=true AND NOT LIKE ai_review_source`），它会把这种行正确标 `source='daily', decision='approved'`，**第一段根本碰不到这种行**。 | 二选一：① 删除 N5 高优先级分支与注释，保持第一段 CASE 三分支（approved=1 / approved=0+muted / ELSE kept）即可，因为这一段的输入域已被 WHERE 限定为"无 `ai_reviewed_at` 的 backlog 行"，daily 升级不属于这个域。② 保留 N5 但加注释"在当前 WHERE 下不可达，留作 WHERE 条件未来演化时的防御"，并把 N5 之上的注释从"AI 升 approved 的边角"改为"占位防御"。**推荐方案 ①**：死代码不如删，避免下次审计再误以为它在工作。 |

### 重要（2 项，不改有后果）

| # | 严重度 | 发现 | 落实建议 |
| --- | --- | --- | --- |
| O2 | 重要 | **"AI 否决"tab 会混入用户手动 mute 的 backlog kept candidate**。用户在 admin 对一个 `backlog_review.approved=0` 的 candidate 点 `/mute` 走 [admin/routes/api/slang.py:465](admin/routes/api/slang.py#L465) `set_status(term_id, "muted")`——[store.py:1719-1738](services/slang/store.py#L1719-L1738) `set_status` 不动 meta、不写 `human_reviewed`。这条 row 进 P0-2 第一段：`approved=0 AND status='muted'` 命中第三分支，被标 `decision='rejected'`。P0-4 "AI 否决" tab `status='muted' AND _ai_rejected_sql_condition() AND NOT _human_reviewed_sql_condition()` 命中——**显示成"AI 否决"，实际是用户手动 mute**。语义错位会让 AI 否决率统计虚高。 | 第一段 CASE 第三分支加 revision 表 EXISTS 子查询区分：`WHEN json_extract(meta_json, '$.backlog_review.approved') = 0 AND status = 'muted' AND EXISTS (SELECT 1 FROM slang_revisions r WHERE r.term_id = slang_terms.term_id AND r.action = 'backlog_review:mute') THEN 'rejected' ELSE 'kept' END`。backlog_reviewer 走 mute 路径会写 `revision_action='backlog_review:mute'`（[backlog_reviewer.py:387](services/slang/backlog_reviewer.py#L387)），人工 mute 不写。子查询成本可接受——一次性迁移。剩余的"approved=0 + status=muted + 无 backlog_review:mute revision"行落入 ELSE `'kept'`，这个语义比"误标 rejected"准确：AI 当时是 kept，用户后来手动 mute 不改 AI 决定。 |
| O3 | 重要 | **P1-1 集成点死引用 backlog_reviewer 不存在的 `_assess`**。文档 line 338 写"同样集成到 [services/slang/backlog_reviewer.py](services/slang/backlog_reviewer.py) `_assess` 之后"。但 backlog_reviewer **没有 `_assess` 方法**——它在 [backlog_reviewer.py:337-343](services/slang/backlog_reviewer.py#L337-L343) `_review_one` 内部直接调 `assess_with_llm()`（line 26 import）。daily_reviewer 才有 `_assess` 薄封装（[daily_reviewer.py:406-414](services/slang/daily_reviewer.py#L406-L414)）。两个 reviewer 集成方式不对称，照 line 338 实施会找不到挂点。 | line 338 改为：① 先抽 `assess_with_llm` 调用为 `_assess` helper（与 daily_reviewer 风格统一），再在 `_assess` 之后插反向重申 prompt；或 ② 直接说"集成到 [backlog_reviewer.py:337-343](services/slang/backlog_reviewer.py#L337-L343) `_review_one` 中 `assess_with_llm` 调用之后、`new_meaning` 计算之前"。**推荐 ①**：抽 helper 让两个 reviewer 集成路径对称，未来 prompt 演化只改一处。 |

### 轻微（5 项，可在实施期收敛）

| # | 严重度 | 发现 | 落实建议 |
| --- | --- | --- | --- |
| O4 | 轻微 | **`return_ai_reviewed_term_to_candidate` 产生 candidate 状态孤儿**。[store.py:1242-1259](services/slang/store.py#L1242-L1259) 把 status 改回 candidate 但保留 AI 写的 `ai_reviewed_at` + `ai_review_decision='approved'`（merged_meta 路径不删 AI 字段）。这种 row P0-4 落不进任何 candidate tab：待审核要求 `NOT _ai_reviewed_sql_condition()`（不命中——ai_reviewed_at 存在），待观察要求 `decision='kept'`（不命中——decision='approved'）。只能在"全部"看到。文档 line 327 只点名 muted/expired 是"全部"兜底，没说 candidate 也会有这种孤儿。 | P0-4 "关于 7 tab 合计"段落补一行：`status='candidate' AND _ai_reviewed_sql_condition() AND _ai_approved_sql_condition()`（即 returned-to-candidate 的旧 daily approved）也属"全部"兜底，不强行加 tab。或在 `return_ai_reviewed_term_to_candidate` 里清掉 AI 字段（语义更干净，但改动面更大）。**推荐前者**——孤儿数量极少。 |
| O5 | 轻微 | **`upsert_ai_approved_term` 加 `ai_review_source/decision` 应在 store 内部统一**。文档 line 166 说"`upsert_ai_approved_term` 的调用方在 meta 里同步加新字段"。当前唯一调用方是 [daily_reviewer.py:315](services/slang/daily_reviewer.py#L315)，但若未来新增调用方易漏。`ai_meta` 拼装在 [store.py:1059-1066](services/slang/store.py#L1059-L1066)——直接在这里写比依赖调用方稳。 | 改为：在 `upsert_ai_approved_term` 内部 `ai_meta` 拼装时同步写 `"ai_review_source": "daily"` + `"ai_review_decision": "approved"`。调用方不需要传 — 与 `ai_approved=True` / `ai_reviewed_at=now` 同源。文档 line 166 表述改为"`upsert_ai_approved_term` 内部 `ai_meta` 拼装处加新字段，无需改调用方"。 |
| O6 | 轻微 | **手工新增 approved 的 `human_reviewed` 写入位置歧义**。文档 P0-4 line 270 + 277 说"在 [admin/routes/api/slang.py](admin/routes/api/slang.py) 创建/编辑路径补写 `meta.human_reviewed=true`"。但 [store.py:946-1023](services/slang/store.py#L946-L1023) `create_term(source='manual')` 走 store 层、line 997 写 `meta_json` 时只写 `{"manual": True, **(meta or {})}`，不写 `human_reviewed`。如果在 admin 层补写，每个调用点（创建 + 编辑）都要补；store 层补写一次就管所有路径。 | 改在 store 层：`create_term` 当 `source='manual'` 时，`meta_json` 里同步写 `"human_reviewed": True`、`"reviewed_at": _now_iso()`、`"reviewed_by": <admin>`。文档 line 270 + 277 表述改为"store 层 `create_term(source='manual')` 内部补写"。 |
| O7 | 轻微 | **P0-4 "5 tab 互斥覆盖" 验收信号应给出 SQL 互斥证明**。line 446 写"5 个 term tab 互斥覆盖"，但没穷举证明。审计时按 (status, decision, human_reviewed) 三维状态空间确认了互斥（候选未审 / candidate+kept / approved+ai+!human / approved+human / muted+rejected+!human），但读者直接复用文档时缺一个能贴进迁移 PR 描述的证明。 | P0-4 验收信号下加一段三维真值表，证明 5 tab 在 (status, decision 三态, human_reviewed 二态) 状态空间里两两不交集，剩余的 (status='muted' AND human_reviewed=true) 等组合走"全部"兜底。 |
| O8 | 轻微 | **P0-2 老调用点改名清单不全 + summary 字段命名建议**。line 167 列了 [store.py:1382](services/slang/store.py#L1382) `list_terms.review_filter` 和 [store.py:2333-2340](services/slang/store.py#L2333-L2340) summary。前端 [admin/frontend/src/views/slang/helpers/badges.ts:80](admin/frontend/src/views/slang/helpers/badges.ts#L80) `isAiApproved` 含 `term.meta?.ai_approved === true`——N7 已要求 grep 但可点名落档。`summary()` 里 `ai_review_count` 字段名按新契约语义其实是 `ai_approved_count`，建议改名（兼容性：admin 前端有依赖此字段名的地方需同步）。 | 文档 P0-2 触及文件追加：① 前端 `helpers/badges.ts:80` `isAiApproved` 改为读 `term.meta?.ai_review_decision === 'approved'`；② summary 端点字段名 `ai_review_count` → `ai_approved_count`，前端依赖处同步改（grep 一次确认范围）；③ summary 端点同时新增 `ai_rejected_count` / `ai_kept_count` / `human_reviewed_count` 三个字段供 P0-4 tab badge 使用（line 277 已要求新增 4 个字段，但具体名字未对齐——这里统一）。 |

### 与前五轮的交叉覆盖

- O1 是对 N5 修订动作本身的反向证伪——独立 grep WHERE 条件后矩阵推演才能识别，前五轮（含修订自审）均未命中。
- O2 是对 N2 直读 `backlog_review.approved` 设计的边角扩展——第五轮 N2 矩阵只考虑了 backlog 三路径 + 人工驳回 + daily 升级共 5 种来源，**漏了"用户对 candidate 直接点 mute"这第 6 种路径**（不经过 deny_ai_reviewed_term）。
- O3 是对 P1-1 的代码事实核实——前五轮均未实读 backlog_reviewer 是否有 `_assess`，假设了对称结构。
- O4-O8 属于实施细节级，前五轮关注语义正确性，本轮关注实施时的代码挂点歧义。

### 第六轮元教训

第六轮在矩阵推演层面发现的 O1 揭示了一条重要的设计反思：**修订动作（N5）本身没经过"它真的会被命中吗"的可达性验证**。第五轮加 N5 时只想"如果这种 row 进了 CASE 怎么办"，没问"这种 row 真能进 CASE 吗"。WHERE 子句和 CASE 子句必须**联合矩阵推演**——WHERE 限定输入域，CASE 处理域内分支，两者割裂分析必然漏 case。

O2 的盲区源自类似问题：第五轮矩阵只列了 backlog reviewer 自己的三路径 + daily 升级 + 人工 deny，**漏了 admin 端零散的状态切换 API**（`/mute`、`/expire`、`/return-candidate`、`/human-approve`）。它们的写入路径不规则、有的写 meta（如 `mark_human_reviewed`、`deny_ai_reviewed_term`），有的不写（如 `set_status`），是矩阵的边界状态来源。**任何 meta 契约迁移的矩阵分析必须把所有"能改 meta 的入口"列全**——store 内部方法 + admin 端点 + reviewer 路径，三层联合。

O3 是最难发现也最重要的教训：**文档反复引用的代码符号必须在落档前被 grep 验证一次**。`_assess` 是同模块同语义命名（daily_reviewer 有 → 假设 backlog_reviewer 也有），是最容易越过审计的死引用类型。

第六轮经此 8 项调整后，方案的契约定义、迁移正确性、实施挂点清单可以视为收敛。**进入实施阶段前的最低工序：自审 2 轮 + 外部审计 2 轮 + 修订后再审计 2 轮 = 共 6 轮已完成**。后续若 N1-O8 任一被证伪或实施期发现新边角，应启动定向第七轮，不要无脑再扫全文——投入产出会快速衰减。本次方案的复杂度（1075 行 meta_json 永久重写 + 多源契约 + 前后端联动）在 omubot 项目里属于罕见量级，下次类似规模的迁移仍按此六轮工序走；常规 PR 不需要。
