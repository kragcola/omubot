# Issue 15 — 指令门禁落地设计 / InstructionAuthorityGate landing design

> 状态：**已实现（2026-05-29），默认 enabled=false**。代码与测试已落地、全绿；下面的设计章节保留为实现依据。
>
> 实现摘要：`services/llm/instruction_gate.py`（gate + AuthorityStore）；`kernel/config.py` InstructionGateConfig；thinker `instruction_signal` 字段（9 处穿透）；`client.py` `_apply_instruction_gate`（插入点 thinker reply 之后、prompt build 之前）；`/authority` slash 命令；`tests/test_instruction_gate.py` + `tests/test_instruction_gate_client.py`（42 测试全绿）。
>
> 来源：基于 [omubot-grayscale-issue15-instruction-gate.md](omubot-grayscale-issue15-instruction-gate.md) 的方案 15A/15C，于 2026-05-29 对齐当前代码现状后产出。
>
> 约束：纯运行时 / 代码层，**不修改 persona 文件**（v2 `source.md` / `_pending_freeze/`）。
>
> 方案选型（2026-05-29 用户确认）：
> - severity 判定 = **regex 兜底 + thinker 增强**（fast-path 硬规则保底，thinker 字段作辅助信号）
> - 落地节奏 = **先出设计文档**（本文），评审通过后再实现

---

## 一、与原方案的架构漂移修正

原方案文档写于 2026-05-27，此后经历 C 系列 persona v2 切换。2026-05-29 用 Explore 全仓核验，4 处假设已漂移，落地必须按现状修正：

| # | 原方案假设 | 当前现状（已核验） | 落地修正 |
|---|---|---|---|
| 1 | `MoodEngine.evaluate()` 在 `services/` | 在 `plugins/schedule/mood.py:112`；`MoodProfile` 在 `plugins/schedule/types.py:64` | gate **不直接 import plugin**——复用 client 已有的注入式回调 `self._mood_getter`（`client.py:3551`），保持解耦 |
| 2 | MoodProfile = {openness, valence, energy} | 实际还有 `tension`、`label`、`anomaly_reason` 三字段 | `energy_floor` 逻辑可用；可顺带用 `tension` 作"烦躁度"信号 |
| 3 | persona guard 有 `hard_check`/`soft_judge` 运行时可挂载 | **未实现**：`guard.yaml` 标 `2.1.0-proposal`，全仓零运行时引用；编译器只读 `constitution.hard_rules`/`guard.behavior_instructions` 烘进静态 prompt | 不挂 soft_judge；改挂活的 `sentinel_registry` 链（见三）或独立 gate 模块 |
| 4 | persona 目录是 `source.md + freeze/` | 实际是 `source.md + _pending_freeze/`；v1 `config/soul/*.md` 已退役 | 仅命名差异；"不改人设"约束依然成立 |

---

## 二、关键发现：hint 注入通道（修正原方案的隐含错误）

原方案 15A 的 Layer 3 设计「往主 LLM 注入 compliance/refusal hint」。核验后发现 **thinker 的 `thought`/`tone`/`topic_intent_label` 当前都没有直接进主生成 prompt** 的通道：

- `thinker_thought` 在 client 里只作函数间穿透，唯一实际用途是 **output-side 剥离**（防 bot 把内心独白说出来，`client.py:1759`）。
- `topic_intent_label` 只流向 `on_thinker_decision` 钩子做 trace（`kernel/types.py:361`），不进 prompt builder。

但进一步核验发现**已有等价管道**可直接复用，无需新建：

> `addressee_hint`（`client.py:3987-3993`）把一段文本 `append` 进 `plugin_dynamic` 列表，经 `build_blocks` 落在主 prompt 末尾（every-turn 段，靠近 messages，`prompt_builder.py:140`）。

**结论**：COMPLY/REFUSE_SOFT 的 hint 注入 = 仿照 `addressee_hint`，构造一条 directive-hint 文本 append 进 `plugin_dynamic` 即可。成本远低于原方案设想的"新建 thinker→prompt 通道"。这是本设计相对原文档的核心增量。

---

## 三、最终架构：regex 兜底 + thinker 增强

severity 判定采用双信号融合（用户 2026-05-29 确认）：

```text
信号 A（确定性 fast-path）：regex 扫 user_message
  → severity_regex ∈ {none, low, medium, high}
信号 B（语义增强，可选）：thinker 新增字段 instruction_signal
  → severity_thinker ∈ {none, low, medium, high}

融合规则（取较严，most-restrictive）：
  severity = max(severity_regex, severity_thinker)  # none<low<medium<high
  命中其一即触发，避免单信号漏判
```

为什么双信号：Control Illusion（arxiv 2502.15851）证明纯 prompt/LLM 方案 compliance 仅 9.6-45.8%，不能只靠 thinker 自觉；regex fast-path 提供确定性保底。thinker 字段补语义（regex 抓不到的隐式指使），但**不作唯一 gate**。

### 授权等级模型（0-4，2026-05-29 用户拍板）

放弃原方案的二元 admin 判断，改用 Koishi.js 式数值授权等级（原研究文档 §工程项目代码分析 B）。每个用户有一个授权等级 0-4，每类指令有一个所需等级阈值，`user_authority >= required` 才放行。

**用户授权等级（per-user，可运行时调配）：**

| 等级 | 名称 | 含义 |
|---|---|---|
| 0 | 受限 | 边缘/不信任，几乎不响应任何指令性请求 |
| 1 | 低信任 | 陌生/新成员 |
| 2 | **默认** | 普通群成员（`default_authority`，所有未配置用户的初值） |
| 3 | 受信 | 熟人/常客，可下达指使类指令 |
| 4 | 最高 | 管理员等级；`config.admins` 成员**自动映射至 4** |

- 普通用户可被 admin **调配到 0-4** 任意值（运行时，见 4.6）。
- admin（`config.admins` keys）恒等于 4，不入 overrides 表。

**指令类 → 所需等级（`required_authority`，可配置）：**

| 指令类 | severity | 默认所需等级 | 行为 |
|---|---|---|---|
| 闲聊 / 无指令 | none | 0 | gate 不介入，正常生成 |
| 轻度调戏（撒娇/喵） | low | 2 | `≥2` 通过 → 进 mood 层（comply/refuse_soft）；`<2` → DENY |
| 指使行为（@/帮发/骂） | medium | 3 | `≥3` 通过 → ALLOW（注入指令 context）；`<3` → DENY |
| 破人设（你是AI/改设定） | high | 4 | 仅 level-4/admin 通过；**设为 5 则全拒（含 admin）= 绝对人设锁** |

> 设计点：high 默认 4（admin 可通过，呼应"管理最高"）。若要让破人设对所有人（含 admin）硬拒，把 `required_authority.high` 配成 `5`（超过 max=4，无人可达）。这样 admin-是否可破人设 完全由 config 决定，无需改代码。

### 处理流水线（插入点：`client.py:3876-3902`，thinker 已决策、wait 已早退、prompt build 之前）

```text
[InstructionAuthorityGate.evaluate]
  ├── Layer 0: 命中检测
  │   severity = max(regex_scan(user_message), thinker_signal)   # none<low<medium<high
  │   if severity == none → PASS（无指令意图，gate 不介入）
  │
  ├── Layer 1: Authority Check（确定性，等级比较）
  │   user_authority = 4 if str(user_id) in admins else overrides.get(user_id, default=2)
  │   required = required_authority[severity]          # {none:0, low:2, medium:3, high:4}
  │   if user_authority < required → DENY
  │   else（通过等级门）:
  │       if severity == low  → 进 Layer 2（mood 调节）
  │       else                → ALLOW（注入指令 context hint）
  │
  └── Layer 2: Mood Modulation（仅 severity==low 且过等级门时到达；mood via self._mood_getter）
      ├── openness > openness_min 且 valence > valence_min → COMPLY
      ├── energy < energy_floor （或 tension 过高）        → REFUSE_SOFT
      └── else: random() < openness ? COMPLY : REFUSE_SOFT

[Layer 3: Response Strategy]
  ├── PASS         → 不介入，原流程继续
  ├── ALLOW        → append directive hint 进 plugin_dynamic，继续主 LLM
  ├── COMPLY       → append compliance hint 进 plugin_dynamic，继续主 LLM
  ├── REFUSE_SOFT  → append refusal hint 进 plugin_dynamic，继续主 LLM
  └── DENY         → 不调主 LLM，quote 原消息 + 经 on_segment 发固定 in-character 拒绝话术 + return None
```

DENY 走 `on_segment` 直发（参考 `client.py:2523` `_emit_segment`、`2529` quote 逻辑），**quote 原消息**（用户拍板），不进主生成：零额外 token、话术可控、不破沉浸（"我又不是你的工具人"而非"抱歉我无法执行"）。

---

## 四、实现清单（落地时逐项执行）

### 4.1 新建 `services/llm/instruction_gate.py`（~120-160 行）

与 `dedup_gate.py` 同目录、同命名风格。核心契约：

```python
from dataclasses import dataclass
from typing import Literal

GateAction = Literal["pass", "allow", "deny", "comply", "refuse_soft"]

@dataclass(frozen=True)
class InstructionGateResult:
    action: GateAction
    severity: str            # none | low | medium | high
    user_authority: int      # 命中时解析出的用户等级 0-4（trace 用）
    required_authority: int   # 该 severity 的所需等级（trace 用）
    reason: str              # 决策依据（trace 用）
    response_hint: str = ""  # ALLOW/COMPLY/REFUSE_SOFT 注入 plugin_dynamic 的文本
    deny_text: str = ""      # DENY 直发的固定话术

class InstructionAuthorityGate:
    def __init__(self, config) -> None: ...

    def scan_severity(self, user_message: str) -> str:
        """regex fast-path，返回 none/low/medium/high。"""

    def resolve_authority(self, user_id: str, admins: dict[str, str]) -> int:
        """admin→4；否则 overrides.get(user_id) ?? default_authority(=2)。"""

    def evaluate(
        self,
        *,
        user_message: str,
        user_id: str,
        admins: dict[str, str],
        authority_overrides: dict[str, int],  # per-user 调级表，运行时可变
        mood,                      # MoodProfile | None，via _mood_getter，鸭子类型不 import plugin
        thinker_signal: str = "none",  # thinker 增强信号
    ) -> InstructionGateResult: ...
```

设计要点：
- 授权解析独立成 `resolve_authority`：`admin→4`，其余查 `authority_overrides`，缺省 `default_authority`（=2）。等级比较 `user_authority >= required_authority[severity]` 是纯整数比较，确定性强、易测。
- `mood` 用鸭子类型（读 `.openness/.valence/.energy/.tension`），**不 import `plugins.schedule`**，保持 services↔plugins 解耦（修正漂移 #1）。
- `evaluate` 是纯函数（无 I/O）→ 易单测。随机部分用注入的 rng 或 `random.random()`，测试时 monkeypatch。
- `authority_overrides` 由调用方（client）从持久化层读出后传入，gate 本身不负责存储（见 4.6）。

### 4.2 thinker 增强字段 `instruction_signal`（复用 topic_intent_label 模式）

按 `topic_intent_label` 的「enum + normalize fallback」模板（`thinker.py`），新增字段需同步改 9 处穿透点：

| 序 | 文件:位置 | 改动 |
|---|---|---|
| 1 | `thinker.py:33` 附近 | 加 `_ALLOWED_INSTRUCTION_SIGNALS = frozenset({"none","low","medium","high"})` |
| 2 | `thinker.py:239` 附近 | 加 `_normalize_instruction_signal(value) -> str`，fallback `"none"` |
| 3 | `thinker.py:149` `ThinkDecision.__slots__` | 加 `instruction_signal` |
| 4 | `thinker.py:167` 附近 ctor | 加 `instruction_signal: str = "none"` |
| 5 | `thinker.py:264` `_decision_from_data` | 加 `instruction_signal = _normalize_instruction_signal(data.get(...))` |
| 6 | `thinker.py:59` `THINKER_SYSTEM_PROMPT` | 输出 spec 加字段 + 一段说明（识别"用户在指使我做某事"） |
| 7 | `thinker.py:337` `_heuristic_decision` | 加 `instruction_signal="none"` |
| 8 | `thinker.py:432` `write_thinker_decision_state` | 写入 RuntimeStateBus slot（可选，trace） |
| 9 | `kernel/types.py:361` + `client.py:3821` 附近 | `ThinkerContext` 加字段 + client 取 `thinker_decision.instruction_signal` |

⚠️ 风险：thinker prompt +~30 token；v4-flash 对"指使意图"识别准确率需灰度验证。**因此 regex 是保底，thinker 字段仅增强**——即使 thinker 全判 `none`，regex 仍能拦住硬 pattern。

### 4.3 wire 进 `client.py`（插入点 3876-3902 之间）

```python
# after _fire_thinker_decision (3876), before `if thinker_action == "wait"` (3878)
if thinker_action == "reply" and self._instruction_gate is not None:
    gate_result = self._instruction_gate.evaluate(
        user_message=current_user_text,   # 见 §七-1：当前用户那条，非整段 timeline
        user_id=user_id,
        admins=dict(getattr(self._config, "admins", {})),
        authority_overrides=self._authority_store.snapshot(),  # 见 4.6
        mood=self._mood_getter(group_id=group_id, session_id=session_id) if self._mood_getter else None,
        thinker_signal=getattr(thinker_decision, "instruction_signal", "none"),
    )
    if gate_result.action == "deny":
        if on_segment:
            # quote 原消息（用户拍板）：复用 _emit_segment 的 quote 路径（client.py:2529）
            await self._emit_deny_segment(gate_result.deny_text, quote_msg_id=msg_id, on_segment=on_segment)
        # record usage + trace（含 user_authority/required_authority），then:
        return None
    elif gate_result.response_hint:
        # 暂存，3993 附近 append 进 plugin_dynamic
        instruction_hint = gate_result.response_hint
```

注入点（3993 附近，紧邻 addressee_hint）：

```python
if addressee_hint:
    plugin_dynamic.append({"type": "text", "text": addressee_hint})
if instruction_hint:                       # 新增
    plugin_dynamic.append({"type": "text", "text": instruction_hint})
```

DENY quote 细节：`_emit_segment`（`client.py:2523`）已有 `quote_reply_enabled && msg_id` → 设 `quote_msg_id` 的逻辑。DENY 直发若想复用,要么把 `msg_id` 透传给一个轻量 `_emit_deny_segment`,要么在 `on_segment` 前手动构造 quote 段。落地时对齐 `quote_reply_enabled` 开关(群配置)——若该群关了 quote,DENY 退化为不带引用直发。

### 4.4 配置（`kernel/config.py`，与 `admins` 同区，~1963 附近）

新增 `instruction_gate` 配置块（Pydantic 子模型）。在原方案 15A 字段基础上，加入授权等级模型字段：

```python
class InstructionGateConfig(BaseModel):
    enabled: bool = False                    # 灰度开关
    mode: Literal["shadow", "active"] = "shadow"   # shadow 只记录不拦
    default_authority: int = 2               # 普通用户初值
    authority_overrides: dict[str, int] = {} # qq_id → 0..4（持久化初值，运行时可覆盖，见 4.6）
    required_authority: dict[str, int] = {   # severity → 所需等级
        "low": 2, "medium": 3, "high": 4,    # high=5 则含 admin 全拒（绝对人设锁）
    }
    severity_patterns: dict[str, list[str]] = {  # regex fast-path
        "high": [...], "medium": [...], "low": [...],
    }
    mood_threshold: dict[str, float] = {"openness_min": 0.6, "valence_min": 0.3, "energy_floor": 0.3}
    deny_responses: list[str] = [...]
    refuse_soft_responses: list[str] = [...]
```

`admins`（`config.admins`，等级恒 4）与本块解耦——admin 不入 `authority_overrides`。

### 4.5 gate 实例化与依赖注入

在 `LLMClient.__init__` 加 `self._instruction_gate = InstructionAuthorityGate(config.instruction_gate) if config.instruction_gate.enabled else None`。`_mood_getter` 已存在，无需新增依赖。

### 4.6 运行时调级（per-user authority 持久化 + 调配入口）

`authority_overrides` 需运行时可变（admin 把某用户调到 0-4），不能只读 config。两层：

- **持久化**：config 里的 `authority_overrides` 是初值/种子。运行时变更落 SQLite（新表 `instruction_authority`，列 `user_id TEXT PK, authority INT, updated_at`）或复用现有 KV/state 存储。`self._authority_store.snapshot()` 返回 `dict[str,int]`（config 种子 ∪ DB 覆盖，DB 优先）。
- **调配入口**（二选一，首期建议 A）：
  - **A. slash 命令**（复用 `services/command.py` admin_only gate，`command.py:127`）：`/authority <user_id> <0-4>`、`/authority <user_id>`（查询）。最小改动、与现有命令体系一致。
  - **B. admin SPA 页面**：群成员列表加等级列 + 调级控件，走新 API `POST /api/admin/authority`。体验好但工作量大，列为后续。

> 首期：命令式调级（A）+ config 种子。SPA（B）作为 roadmap，不阻塞 gate 主体上线。

---

## 五、测试计划（D2 cancel-path + 回归）

| 测试 | 断言 |
|---|---|
| `test_authority_admin_is_4` | `config.admins` 成员 → resolve_authority 返回 4 |
| `test_authority_default_2` | 未配置用户 → 返回 default_authority(2) |
| `test_authority_override` | overrides 表里 user→3 → 返回 3（DB 覆盖 config 种子） |
| `test_gate_high_deny_level2` | level-2 用户 + "你是AI吧"(high,req=4) → deny（2<4） |
| `test_gate_high_admin_pass` | admin(4) + high(req=4) → 通过等级门（4>=4），ALLOW |
| `test_gate_high_lock_all` | required_authority.high=5 → 连 admin(4) 也 deny（绝对人设锁） |
| `test_gate_medium_deny_level2` | level-2 + "帮我@他"(medium,req=3) → deny（2<3） |
| `test_gate_medium_pass_level3` | level-3 + medium(req=3) → ALLOW（3>=3） |
| `test_gate_low_comply_good_mood` | level-2 + low(req=2) 过门 + openness=0.8/valence=0.5 → comply |
| `test_gate_low_refuse_tired` | level-2 + low 过门 + energy=0.1 → refuse_soft |
| `test_gate_low_deny_level0` | level-0 + low(req=2) → deny（0<2，连调戏都不响应） |
| `test_gate_none_passthrough` | 普通闲聊 → action=pass（gate 不介入） |
| `test_regex_fastpath_independent` | thinker_signal=none 但 regex 命中 high → 仍 deny（保底验证） |
| `test_thinker_signal_escalates` | regex=none 但 thinker_signal=high → 仍按 high 处理（max 融合） |
| `test_mood_duck_typing` | 传入无 tension 字段的 mock mood → 不抛异常（getattr fallback） |
| **D2** `test_deny_no_main_llm_pollution` | DENY 分支 → 主 LLM 不被调用（mock `_call` 断言 0 次），timeline/usage 不污染下一轮 |
| `test_deny_quotes_original` | DENY → on_segment 收到的段带 quote_msg_id（quote 原消息） |
| `test_误判_discussion` | "@功能怎么用" 这类讨论 → 不应被 high/medium 拦（regex pattern 需带否定边界，灰度调参） |

---

## 六、灰度与回滚

- **灰度**：`enabled=false` 默认关；开启后 `mode=shadow`（只记录 gate_result 不真拦），观察误判率 + 各等级命中分布，再切 `mode=active`。
- **回滚**：纯运行时新增，回滚 = `instruction_gate.enabled=false` + `docker compose restart bot`（config 改动，无需 rebuild）。thinker 字段是 additive enum（fallback none），回滚不影响旧逻辑。`instruction_authority` 表回滚后变孤儿表，无副作用。
- **D6**：本特性是后端（services/.py + kernel/config.py + 新 SQLite 表），改 .py 需 rebuild bot；config.toml 改动只需 restart。

---

## 七、未决问题（评审需拍板）

1. **gate 取 user_message 用哪个变量**：群聊里 `conversation_text` 是整段 timeline，`user_content` 才是当前用户那条。regex 应只扫当前用户消息，避免扫到历史。落地时确认变量来源（4.3 用 `current_user_text` 占位）。
2. **medium 默认所需等级 3 是否合适**：medium（@/帮我发）默认 req=3，意味 level-2 默认用户被拒。但群里正常社交也说"帮我看下"——medium pattern 需精调（带否定边界），或个别群把 `default_authority` 提到 3。建议 shadow 期收集真实 medium 命中样本再定阈值。
3. **运行时调级入口**：4.6-A（slash 命令）首期上，4.6-B（SPA 页面）列 roadmap——确认这个先后顺序。
4. **持久化选型**：`instruction_authority` 用新建 SQLite 表，还是复用现有 state/KV 存储？落地时对齐项目现有持久化惯例。

### 已拍板（2026-05-29 用户确认）

- ✅ **授权模型**：分层 0-4，user 默认 2，可调配 0-4，admin=最高(4)；指令按所需等级分（§三授权等级模型）。
- ✅ **thinker 增强首期就上**：4.2 的 9 处穿透与 regex fast-path 同一首期 PR，不拆第二步。
- ✅ **DENY quote 原消息**：拒绝话术引用触发消息（4.3）。

---

## 八、引证

Instruction Hierarchy（arxiv 2404.13208）/ Control Illusion（arxiv 2502.15851，"prompt-only 不可靠"）/ Self-Judge（arxiv 2409.00935，mood→动态阈值）/ **Koishi.js 数值 authority 等级模型（本设计 0-4 分层主轴）** / pi-permission-system 3-state / hermes-agent 4-state / PKU-SafeRLHF severity grading。详见原方案文档 §学术调研 / §工程项目代码分析。

