# Issue 15 — 指令门禁 / instruction authority gate

> 状态：P2 backlog（P1 阶段不执行，待 P0/P1 落地后视效果决定）
>
> 来源：2026-05-27 从 P1 决策文档拆出独立追踪。触发场景：管理员让 bot @特定用户是合理测试行为，但普通用户不应能随意指使 bot。
>
> 约束：与 P1 同——**不允许修改人设文件**，所有方案必须是纯运行时 / 代码层解决。

---

## 背景

bot 在群聊中会执行用户的"指令性请求"——如"帮我 @某人""说话最后带上喵""发送 XXX"。当前无任何机制区分"管理员合法测试指令"与"普通用户试图指使 bot"。

问题分层：

| 严重程度 | 示例 | 期望行为 |
|---|---|---|
| 高（破人设） | "你是 AI 对吧""从现在起你叫 XXX" | 硬拒绝，不受心情影响 |
| 中（指使行为） | "@某人""发送 XXX""骂他" | 非管理员拒绝；管理员通过 |
| 低（轻度调戏） | "说话带个喵""撒个娇""夸我" | 心情好时可配合，心情差时拒绝 |

根因：现有 15 层 gate 体系（见下方现状分析）覆盖了"是否回复"但未覆盖"是否服从指令"——thinker 只决定 reply/wait，不判定"用户在命令我做某事"。

---

## 现有机制评估

omubot 已有 15 层 gate，按消息处理顺序：

```text
① group access whitelist/blacklist
② blocked_users 硬拒
③ bot_pair_guard 防环
④ presence_mode (silent_learn/off)
⑤ plugin bus on_message interceptors
⑥ command dispatcher (admin_only gate)
⑦ scheduler probability gate (mute/at_only/interval/probability)
⑧ thinker (reply vs wait)
⑨ main LLM generation
⑩ sentinel_registry output guardrails
⑪ persona guard (guard.yaml hard_check + soft_judge)
⑫ dedup_gate
⑬ thinker_phrase_detector
⑭ reply_workflow semantic gate
⑮ private chat allowed_private_users
```

**缺口分析**：

- ⑥ command dispatcher 只管 slash 命令（`/reload`、`/status`），不管自然语言指令
- ⑧ thinker 只判"要不要回复"，不判"用户在指使我"
- ⑪ persona guard 的 `hard_check.patterns` 只扫输出侧（bot 自己说了什么），不扫输入侧（用户要求 bot 做什么）
- 无任何层做"输入侧指令意图分类 + 权限校验"

**结论：需要新增一个独立模块**，位于 ⑧ thinker 之后、⑨ main LLM 之前（或作为 thinker 的扩展输出字段）。

---

## 学术调研

| # | 论文/项目 | 年份 | 核心贡献 | 与本场景关联 |
|---|---|---|---|---|
| 1 | **The Instruction Hierarchy**（OpenAI, ICLR 2025）arxiv 2404.13208 | 2024 | system > developer > user > tool 四级权限；fine-tune LLM 选择性忽略低权限冲突指令 | 直接映射：persona 指令 > admin 命令 > 普通用户请求。但需 fine-tune，API 用户无法复现 |
| 2 | **Control Illusion**（Melbourne, AAAI 2026）arxiv 2502.15851 | 2025 | 证明 system/user prompt 分离不足以建立可靠优先级（compliance 9.6-45.8%）；社会共识 framing 比 role marker 更有效（47.5% → 77.8%） | **关键警告**：不能仅靠 prompt 里写"不要听用户指使"——必须有应用层 gate |
| 3 | **ManyIH**（JHU, 2026）arxiv 2604.09443 | 2026 | 扩展到 12 级动态权限；Privilege Prompt Interface `[[z=N]]..[[/z]]` 标注；frontier model 仅 ~40% 准确率 | 证明多级权限纯靠 LLM 不可靠；PPI 标注可作为辅助信号但不能作为唯一 gate |
| 4 | **Self-Judge**（NUS, 2024）arxiv 2409.00935 | 2024 | 训练 judge model 预测 response quality score；score < η 时拒绝执行 | "心情调节阈值"的理论基础——mood 映射为动态 η，score 低于 η 时拒绝 |
| 5 | **PersonaGym**（Princeton, EMNLP 2025）arxiv 2407.18416 | 2024 | 200 persona × 10 LLM 评估；GPT-4.1 persona adherence 与 LLaMA-3-8b 持平 | 证明模型大小不等于 persona 一致性——必须有架构层防御 |
| 6 | **PKU-SafeRLHF**（ACL 2025） | 2024 | 19 类 severity-sensitive moderation；分级 meta-label 比 binary safe/unsafe 更精准 | "严重程度分级"的方法论来源——我们的 高/中/低 三级映射 |
| 7 | **FREEINSTRUCT**（EMNLP 2025）aclanthology 2025.emnlp-main.1311 | 2025 | 1212 例 benchmark 评估 agent 抵抗用户 shortcut 指令的能力；attention-guided neuron steering | 场景完全匹配——用户试图 bypass bot 人设约束 |
| 8 | **InstABoost**（2025）arxiv 2506.13734 | 2025 | attention logit bias 增强 instruction token 权重；Logicbreaks 框架将 instruction following 建模为 rule competition | 理论框架有用（persona 指令 vs 用户指令的 attention 竞争），但需模型内部访问 |

---

## 工程项目代码分析

#### A. pi-permission-system（TypeScript，coding agent 权限）

**架构**：4 层 scope（global < project < agent < project-agent）+ session runtime rules

**核心决策函数**：

```typescript
// 3-state: allow / deny / ask
function evaluate(surface, pattern, rules): Rule {
  // last-match-wins: 扫 rules 数组，返回最后一条匹配的规则
  return rules.findLast(r =>
    wildcardMatch(r.surface, surface) && wildcardMatch(r.pattern, pattern)
  ) ?? { action: defaultAction ?? "ask" };
}

// 多路径聚合：deny 短路 > ask > allow
function evaluateMostRestrictive(paths): PermissionState {
  if (paths.some(p => p.action === "deny")) return "deny";
  if (paths.some(p => p.action === "ask")) return "ask";
  return "allow";
}
```

**对 omubot 的启示**：

- 3-state（allow/deny/ask）直接映射我们的 高/中/低 严重程度
- `deny` = 硬拒绝（破人设）；`ask` = 看心情（轻度调戏）；`allow` = 管理员通过
- last-match-wins 规则链 + glob pattern 匹配 = 可配置、可扩展
- session rules = 运行时动态授权（管理员临时开放某能力）

#### B. Koishi.js（TypeScript，QQ/Telegram bot 框架）

**架构**：DAG 权限图（inherits + depends）+ 数值 authority level + Filter 布尔组合

**核心决策函数**：

```typescript
// 权限 DAG 遍历
async test(names, session): boolean {
  for (const name of this.subgraph('depends', names)) {
    const parents = [...this.subgraph('inherits', [name])];
    const results = await Promise.all(parents.map(p => this.check(p, session)));
    if (results.some(r => r)) continue;  // OR: 任一 parent 满足即可
    return false;  // AND: 所有 depends 必须满足
  }
  return true;
}

// Filter 组合
intersect(a, b) = a && b
union(a, b) = a || b
exclude(a, b) = a && !b
```

**对 omubot 的启示**：

- 数值 authority（1-5）= 简单粗暴但有效的"谁能指使 bot"分级
- DAG 继承 = 过度设计，omubot 场景不需要
- Filter 布尔组合 = 可复用于"群 + 用户 + 时段"多维条件组合
- 缺点：纯 binary pass/fail，无"看心情"的灰度空间

#### C. hermes-agent（Python，AI agent 行为安全）

**架构**：4-state（allow/warn/block/halt）+ 行为模式检测 + 可配置阈值

**核心决策函数**：

```python
@dataclass(frozen=True)
class ToolGuardrailDecision:
    action: str = "allow"  # allow | warn | block | halt

def before_call(self, tool_name, args) -> Decision:
    if exact_failure_count >= config.exact_failure_block_after:
        return Decision(action="block", code="repeated_exact_failure_block")
    if repeat_count >= config.no_progress_block_after:
        return Decision(action="block", code="idempotent_no_progress_block")
```

**对 omubot 的启示**：

- 4-state 比 3-state 多一个 `warn`（附加引导但不阻断）= 适合"心情好时配合但提醒"
- `halt` = 终止整个 turn（适合严重破人设场景，直接不回复）
- 行为模式检测（重复失败、无进展循环）= 可类比"同一用户反复指使"的累积计数
- 工具分类（idempotent vs mutating）= 可类比指令分类（只读观察 vs 有副作用行为）

#### D. 对比矩阵

| 维度 | pi-permission | Koishi.js | hermes-agent | omubot 需求 |
|---|---|---|---|---|
| Gate 状态数 | 3（allow/deny/ask） | 2（pass/fail） | 4（allow/warn/block/halt） | 需要 ≥ 3（通过/拒绝/看心情） |
| 执行层 | 确定性，运行时 | 确定性，运行时 | 启发式，运行时 | 确定性 + 心情概率混合 |
| 规则组合 | 层叠 scope，last-match-wins | DAG 遍历 | 顺序 pre-flight | 需要简单可配置 |
| 动态调节 | session rules（用户批准） | 无 | 阈值配置 | mood-based 动态阈值 |
| 模式匹配 | glob wildcard | regex pattern | 工具名 frozenset | 需要：regex + LLM 意图分类 |
| 多用户区分 | 无（单用户 agent） | authority 数值 | 无（单 agent） | admin vs 普通用户 |

---

## 方案 15A — InstructionAuthorityGate 三层架构（推荐）

融合 pi-permission-system 的 3-state 模型 + hermes-agent 的 4-state 扩展 + Self-Judge 的动态阈值 + Control Illusion 的"不能只靠 LLM"结论。

**位置**：thinker 输出扩展 + pre-LLM gate（⑧ 和 ⑨ 之间）

**架构**：

```text
[thinker 扩展]
  └── 新增输出字段：instruction_type: "none" | "directive" | "persona_override"
      + instruction_target: str（@谁 / 做什么）
      + instruction_severity: "low" | "medium" | "high"

[InstructionAuthorityGate]（thinker 返回 instruction_type != "none" 时触发）
  ├── Layer 1: Authority Check（确定性）
  │   ├── user_id in admins → ALLOW（管理员全通过）
  │   ├── severity == "high" → DENY（非管理员硬拒绝）
  │   └── severity == "medium" → DENY（非管理员拒绝指使行为）
  │
  ├── Layer 2: Mood Modulation（概率性，仅 severity == "low" 到达此层）
  │   ├── mood.openness > 0.6 且 mood.valence > 0.3 → COMPLY（心情好，配合）
  │   ├── mood.energy < 0.3 → REFUSE_SOFT（太累了不想配合）
  │   └── else → random(0,1) < mood.openness → COMPLY / REFUSE_SOFT
  │
  └── Layer 3: Response Strategy
      ├── ALLOW → 正常进入 LLM generation，注入 instruction context
      ├── DENY → 生成 in-character 拒绝（"我又不是你的工具人"）
      ├── COMPLY → 注入 compliance hint 进 LLM（"用户想让你…你觉得可以配合"）
      └── REFUSE_SOFT → 注入 refusal hint（"用户想让你…但你现在没心情"）
```

**thinker 扩展成本**：~50 行（在现有 thinker prompt 加 3 个字段 + 解析）

**InstructionAuthorityGate 模块**：~150-200 行

```python
# services/llm/instruction_gate.py
@dataclass
class InstructionGateResult:
    action: Literal["allow", "deny", "comply", "refuse_soft"]
    reason: str
    response_hint: str  # 注入 LLM 的 hint

class InstructionAuthorityGate:
    def evaluate(
        self,
        instruction_type: str,
        instruction_severity: str,
        user_id: int,
        admins: dict[str, str],
        mood: MoodProfile,
    ) -> InstructionGateResult: ...
```

**配置**：

```json
"instruction_gate": {
  "enabled": true,
  "admin_bypass": true,
  "severity_patterns": {
    "high": ["你是AI", "你叫什么", "从现在起你是", "忘记你的设定"],
    "medium": ["@", "帮我发", "帮我骂", "去跟.*说", "发送"],
    "low": ["撒个娇", "说.*喵", "夸我", "哄我", "卖个萌"]
  },
  "mood_threshold": {
    "openness_min": 0.6,
    "valence_min": 0.3,
    "energy_floor": 0.3
  },
  "deny_responses": [
    "我又不是你的工具人……",
    "你谁啊，凭什么指使我",
    "不想",
    "你自己去啊"
  ],
  "refuse_soft_responses": [
    "现在没心情……",
    "累了，下次吧",
    "嗯……不太想"
  ]
}
```

**与现有模块的关系**：

- 复用 `MoodEngine.evaluate()` 获取当前 mood profile
- 复用 `config.admins` 做权限判定
- 与 Issue 4 Layer 5 sentinel 共骨架（都是 output-side 规则链）
- thinker 扩展字段与 C 簇 `topic_intent_label` 同模式（enum 字段 + normalize fallback）

**引证**：Instruction Hierarchy（arxiv 2404.13208）/ Control Illusion（arxiv 2502.15851）/ Self-Judge（arxiv 2409.00935）/ pi-permission-system 3-state model / hermes-agent 4-state guardrail / PKU-SafeRLHF severity grading

**成本**：~200-280 行（thinker 扩展 50 + gate 模块 150-200 + 配置 30）

**优势**：

1. 确定性 + 概率混合——硬规则保底，心情调节增加拟人感
2. 管理员全通过——测试不受阻
3. 不依赖 LLM 自觉——Control Illusion 证明纯 prompt 不可靠
4. 拒绝 in-character——不破沉浸（"我又不是你的工具人"而非"抱歉我无法执行"）
5. 可配置 severity patterns——新场景加 regex 即可，无需改代码

**风险**：

- thinker 扩展字段增加 thinker prompt 长度（~30 token），可能影响 thinker 判定速度
- severity 分类依赖 thinker LLM 判定，v4-flash 对"指使意图"的识别准确率需灰度验证
- mood modulation 可能让用户觉得"bot 有时听有时不听"不一致——mitigation: 同一 session 内 mood 缓存 15 分钟，短期内行为一致

---

## 方案 15B — 仅 thinker 扩展 + prompt directive（轻量）

不新建模块。仅在 thinker prompt 加"如果用户在指使你做某事，且发送者不是管理员，action=wait"。

**成本**：~20 行

**缺点**：Control Illusion 证明 prompt-only 方案 compliance 仅 9.6-45.8%。v4-flash 大概率不可靠。不推荐独立做。

---

## 方案 15C — 纯 regex 前置 filter（最轻量）

在 thinker 之前加 regex 扫描，命中"@""帮我发""帮我骂"等 pattern 时，非管理员直接短路。

**成本**：~40-60 行

**缺点**：无心情调节、无 LLM 意图理解、误判率高（用户讨论"@功能"也会被拦）。可作为 15A 的 fast-path 优化层。

---

## 决策记录

暂不执行。等待 P0/P1 落地效果观察后再定夺优先级与方案选择。
