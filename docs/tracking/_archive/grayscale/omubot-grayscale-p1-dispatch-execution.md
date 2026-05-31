# P1 派单——8 件推荐方案执行追踪

> 状态：2026-05-27 执行中。本文是 [P1 决策文档](omubot-grayscale-p1-decision-pending.md) 的执行版。
>
> 前置：P0 五件（F1/F3/F7/F10/F13）已合并 main 并部署。P1 复用 P0 A 簇 sentinel_registry 骨架。
>
> 约束：**不允许修改人设文件**（source.md / instruction.md）。所有方案纯运行时 / 代码层。
>
> 执行原则：
> 1. **按簇分 PR**——同簇共骨架的 issue 合 1 个 PR；跨簇独立 PR。
> 2. **每条 Wave 自带 D1 grep 证据 + D2 cancel-path 测试 + 回滚开关**。
> 3. **不动 persona 文件**——anchor / baseline 从 `freeze/` artifacts 自动派生。
> 4. **默认 OFF**——所有新模块 config 段 `enabled: false`，灰度群先开观察。

---

## 簇划分

| 簇 | PR | 包含 Issue | 共骨架 | 预估行数 |
|---|---|---|---|---|
| D（drift/oversharing 防御） | 1 个 | F4 + F8 | sentinel_registry 出口侧 + drift detector | ~900-1100 |
| E（输入侧 binding + filter） | 1 个 | F11 + F12 + F14 | nickname_registry + input/output processor | ~600-800 |
| F（OOV + sticker 行为增强） | 1 个 | F5 + F6 | gate 扩展 + speculative executor | ~500-650 |
| G（低信号前置） | 1 个 | F2 | text_preflight 独立模块 | ~150-200 |

执行顺序：D → E → F → G（D 簇与 P0 A 簇共骨架最紧密，优先落地；G 簇紧迫性最低排末）。

---

## 派单 D — drift/oversharing 防御（F4 + F8）

> 共骨架：复用 P0 A 簇 `services/llm/sentinel_registry.py` 的 `register_rule()` + `apply_guardrails()` 出口管线。
>
> 核心思路：F4 在 LLM 输出侧检测 persona drift 并修复/拦截；F8 在同一出口检测 unsolicited schedule oversharing 并 dampen。两者共享 sentinel_registry 注册机制和 `services/block_trace/store.py` metric 通道。

### 前置知识（执行者必读）

**现有 sentinel_registry 工作方式**：

- 位于 `services/llm/sentinel_registry.py`，已在 P0 A 簇落地
- `register_rule(name, pattern, action, severity)` 注册规则
- `apply_guardrails(text, *, thinker_thought, last_assistant_text)` 在 `services/llm/client.py:1209` 被调用，位于 LLM 返回 final text 之后、segmentation 之前
- action 类型：`strip` / `redact` / `block` / `warn` / `rewrite`
- 已有规则：F1 sentinel strip（`«图片»` 等标记）、F10 dedup gate、F13 phrase detector

**persona v2 freeze artifacts 位置**：

- `config/persona/<persona_id>/freeze/` 目录
- `freeze/core.identity` — 身份锚点（名字、身份、人格首行）
- `freeze/voice_exemplars` — 语音范例（典型说话风格样本）
- `PersonaRuntime` 在 `services/persona/runtime.py` 管理 freeze 加载

**mood_block 注入位置**：

- `plugins/schedule/mood.py:293` `MoodEngine.build_mood_block()` 生成 mood 系统块
- 该块已包含 `注意：你知道自己今天做了什么，但不要主动说出来` 指令（line 336-340）
- mood_block 通过 `services/llm/client.py` 的 `dynamic_blocks` 注入 system prompt

---

### Wave D0 — 前置验证（零代码）

| 步骤 | 命令 / 操作 | 预期 | 目的 |
|---|---|---|---|
| 1 | `ls config/persona/fengxiaomeng-v2/freeze/` | 确认 `core.identity` / `voice_exemplars` 存在 | F4 Layer 1/2 依赖 freeze artifacts |
| 2 | `grep -n "register_rule\|register(" services/llm/sentinel_registry.py` | 确认注册 API 签名 | F4 Layer 5 / F8 detector 需注册到 registry |
| 3 | `grep -n "apply_guardrails" services/llm/client.py` | 确认 hook 位点（应在 ~line 1209 和 ~3645 和 ~3937） | 确认 F4/F8 新规则自动被现有 hook 调用 |
| 4 | `grep -n "build_mood_block\|mood_block\|dynamic_blocks" services/llm/client.py plugins/schedule/mood.py` | 确认 mood_block 注入路径 | F4 Layer 2 anchor 需在同一 dynamic_blocks 通道注入 |
| 5 | `grep -rn "unsolicited\|overshare\|schedule.*detector" services/` | 确认当前无同名模块冲突 | F8 新模块命名空间干净 |

---

### Wave D1 — F8 unsolicited schedule detector（简单件先行）

F8 比 F4 简单得多（~80-120 行 vs ~800-950 行），且与 F4 无代码依赖。先落 F8 验证 registry 扩展路径通畅。

| 编号 | 一句话 | 关键文件 | 详细指导 |
|---|---|---|---|
| **D1.1** | 新建 `services/llm/schedule_overshare_detector.py` | 新文件 ~80-120 行 | 见下方详细规格 |
| **D1.2** | 注册到 sentinel_registry | `services/llm/sentinel_registry.py` 末尾 import-time 注册 | 同 F10/F13 模式 |
| **D1.3** | config 段 + metric | `kernel/config.py` + `services/block_trace/store.py` | 加 `schedule_overshare` 配置段 |
| **D1.4** | 单元测试 + cancel-path | `tests/test_schedule_overshare_detector.py` | 覆盖 5 个场景 |

**D1.1 详细规格**：

```python
# services/llm/schedule_overshare_detector.py

@dataclass
class OvershareDetectResult:
    hit: bool
    reason: str  # "unsolicited_time_mention" / "cumulative_threshold"
    dampened_text: str  # 移除时段信息后的文本（hit=True 时）

def detect(
    bot_reply: str,
    user_message: str,
    *,
    session_count: int = 0,  # 本 session 已主动报日程次数
    cumulative_threshold: int = 2,
) -> OvershareDetectResult:
    """
    判定 bot 是否在用户未询问日程时主动报出时段信息。

    逻辑：
    1. 先检查 user_message 是否含时段询问词（bypass 条件）：
       - 匹配：几点|什么时候|日程|安排|忙不忙|在干嘛|在做什么|干啥呢
       - 命中任一 → hit=False（用户主动问了，bot 回答合理）

    2. 再检查 bot_reply 是否含时段泄露 pattern：
       - 匹配：\d{1,2}[：:]\d{2}|上午|下午|晚上|排练|吃饭|休息|上课|午饭|晚饭
       - 未命中 → hit=False

    3. 两条都命中 → hit=True
       - session_count >= cumulative_threshold → reason="cumulative_threshold"
       - else → reason="unsolicited_time_mention"

    4. dampened_text：从 bot_reply 中移除含时段 pattern 的子句
       - 策略：按句号/感叹号/换行分句，移除含 pattern 的句子
       - 如果移除后为空 → dampened_text = ""（由 registry action 决定是 block 还是 fallback）
    """
```

**D1.2 注册方式**（参照 `services/llm/dedup_gate.py` 末尾的 import-time 注册模式）：

```python
# 在 sentinel_registry.py 的 _register_defaults() 或在 schedule_overshare_detector.py 末尾
from services.llm.sentinel_registry import register_rule

register_rule(
    name="schedule_overshare",
    check=_check_schedule_overshare,  # 包装 detect() 为 registry 接口
    action="rewrite",  # 默认 rewrite（移除时段句子），不 block 整条
    severity="low",
)
```

**D1.3 config 段**：

```python
# kernel/config.py — 新增 BaseModel
class ScheduleOvershareConfig(BaseModel):
    enabled: bool = False  # 默认 OFF
    cumulative_threshold: int = 2
    bypass_patterns: list[str] = ["几点", "什么时候", "日程", "安排", "忙不忙", "在干嘛", "在做什么", "干啥呢"]
    leak_patterns: list[str] = [r"\d{1,2}[：:]\d{2}", "上午", "下午", "晚上", "排练", "吃饭", "休息", "上课"]
```

**D1.4 测试场景**：

| 场景 | user_message | bot_reply | 预期 |
|---|---|---|---|
| 用户问日程 → bypass | "你今天忙不忙" | "下午有排练" | hit=False |
| 用户闲聊 → bot 泄露 | "哈哈好搞笑" | "对吧哈哈，我下午3:00还要排练呢" | hit=True, dampened 移除"我下午3:00还要排练呢" |
| 无时段词 → 安全 | "你喜欢什么" | "我喜欢画画" | hit=False |
| 累积超限 | "嗯嗯" | "晚上要休息了" (session_count=2) | hit=True, reason="cumulative_threshold" |
| cancel-path | — | — | detect() 中途被取消，session_count 不递增 |

**D1 回滚**：`git restore services/llm/schedule_overshare_detector.py tests/test_schedule_overshare_detector.py`

**Wave D1 回填（2026-05-27）**：

- **D1.1 已落地**：新增 `services/llm/schedule_overshare_detector.py`，提供 `OvershareDetectResult`、纯函数 `detect()` 与 `schedule_overshare_rule()`。实际 dampen 口径按“句号/换行分句 + 整句移除含时段信息的子句”实现；因此示例 `"对吧哈哈，我下午3:00还要排练呢。先聊这个。"` 会清洗成 `"先聊这个。"`，不会保留前半句寒暄尾巴。
- **D1.2 已接入真实 registry 骨架**：由于当前 `sentinel_registry` 真实 API 是 `register_rule(handler)`，本轮按 import-time `RuleHandler` 模式注册，而不是执行单旧稿里的 `name/check/action/severity` 形态。接线文件：
  - `services/llm/sentinel_registry.py`：`GuardrailContext` 新增 `user_message` / `session_count`；`apply_guardrails()` 透传这两个字段；末尾补 `schedule_overshare_detector` import 注册。
  - `services/llm/dedup_gate.py`、`services/llm/thinker_phrase_detector.py`：补总开关判定，避免只开 `schedule_overshare.enabled=true` 时误触发 A 簇 dedup / thinker phrase 规则。
- **D1.3 已补 config + metric + client wiring**：
  - `kernel/config.py`：新增独立根配置 `ScheduleOvershareConfig`
  - `config/config.json`：新增默认 OFF 的 `"schedule_overshare"` 配置块
  - `plugins/chat/plugin.py`：`LLMClient(...)` 新增 `schedule_overshare_config=config.schedule_overshare`
  - `services/llm/client.py`：新增 guardrail 组合配置拼装、按 `session_id/group_id` 维度的 session 内 overshare 计数、`user_message` / `session_count` 透传、`schedule_overshare_*` metrics 汇总，以及“回复真正提交后才累计命中次数”的 cancel-safe 计数边界
  - `services/block_trace/store.py`：新增 `schedule_overshare_hits` / `schedule_overshare_rewritten`
- **实现口径修正**：执行单旧稿把 F8 写成“注册到 sentinel_registry 即可”，但按当前仓内真实调用链，若不扩 `GuardrailContext` 与 `LLMClient._apply_visible_reply_guardrails()`，无法拿到 `user_message` 和 `session_count`。本轮已做最小扩展，不触及外部持久化状态。
- **D1.4 测试已补齐**：
  - 新增 `tests/test_schedule_overshare_detector.py`：覆盖用户问日程 bypass、用户闲聊时命中、无时段词安全、累计阈值、cancel-path 五个场景
  - 扩 `tests/test_humanization_metrics_persist.py`：覆盖 `schedule_overshare_hits` / `schedule_overshare_rewritten` 聚合
  - 扩 `tests/test_sentinel_pipeline_e2e.py`：新增“只开 overshare、关闭 sentinel_guardrail 时仍可 rewrite 并落 metrics”的 e2e
- **验证结果**：
  - `source ./scripts/dev/env.sh && uv run pytest tests/test_schedule_overshare_detector.py tests/test_humanization_metrics_persist.py tests/test_sentinel_pipeline_e2e.py tests/test_humanization_config.py -q`
    - `24 passed`
  - `source ./scripts/dev/env.sh && uv run ruff check services/llm/schedule_overshare_detector.py services/llm/sentinel_registry.py services/llm/dedup_gate.py services/llm/thinker_phrase_detector.py services/llm/client.py kernel/config.py plugins/chat/plugin.py tests/test_schedule_overshare_detector.py tests/test_sentinel_pipeline_e2e.py tests/test_humanization_metrics_persist.py tests/test_humanization_config.py`
    - `All checks passed`
  - `source ./scripts/dev/env.sh && uv run pyright services/llm/schedule_overshare_detector.py services/llm/sentinel_registry.py services/llm/dedup_gate.py services/llm/thinker_phrase_detector.py services/llm/client.py kernel/config.py tests/test_schedule_overshare_detector.py tests/test_sentinel_pipeline_e2e.py tests/test_humanization_metrics_persist.py tests/test_humanization_config.py`
    - `0 errors, 0 warnings`
- **D1 结论**：F8 现已形成独立 kill-switch：`schedule_overshare.enabled=false` 时无行为；即便 `sentinel_guardrail.enabled=false`，也可单独开启 overshare rewrite，不会连带开启 dedup / thinker phrase。

**Wave D0 回填（2026-05-27）**：

- 步骤 1 实证：`config/persona/fengxiaomeng-v2/freeze/` 在当前仓库**不存在**；现有可读 runtime 产物路径是 `config/persona/fengxiaomeng-v2/_pending_freeze/`，其中包含 `source.frozen.md`、`voice.yaml`、`persona.yaml`、`_persona_runtime.json`。`services/persona/runtime.py:84-150` 也明确只从 `_pending_freeze/` 读取。后续 D3/D4/D5 的 freeze 依赖需按 `_pending_freeze/` 对齐，不能照单里旧路径硬写。
- 步骤 2 实证：`services/llm/sentinel_registry.py:116-117` 的注册 API 是 `register_rule(handler: RuleHandler)`，不是文档前置知识里写的 `register_rule(name, pattern, action, severity)` 形态；默认 sentinel 仍通过 `SentinelEntry` 注入，非 sentinel 规则（如 dedup / thinker phrase）走 handler 模式。
- 步骤 3 实证：`apply_guardrails()` 的真实调用位点当前只有 `services/llm/client.py:1209-1214` 一处，在 `_apply_visible_reply_guardrails()` 内；单据里提到的 `~3645 / ~3937` 旧位点已漂移或已被重构移除。
- 步骤 4 实证：mood_block 由 `plugins/schedule/mood.py:293-343` 生成，并在 `plugins/schedule/plugin.py:72-92` 的 `on_pre_prompt()` 中以 `ctx.add_block(... position="dynamic" ...)` 注入；当前不再直接 grep 到 `client.py` 的 `dynamic_blocks` 路径。另一个现实差异是 `plugins/schedule/mood.py:318` 已优先使用 `slot.description or slot.activity`，和派单撰写时不同。
- 步骤 5 实证：`grep -rn "unsolicited|overshare|schedule.*detector" services/` 当前无命中，F8 命名空间干净。

**Wave D0 结论**：D 簇后续实现需按当前仓内现实做 3 处口径修正：① freeze artifacts 读取根改为 `_pending_freeze/`；② sentinel_registry 新规则按 `RuleHandler` 注册；③ mood anchor 注入需走 plugin pre-prompt block 通道，而不是假设直接接到 `client.py dynamic_blocks`。

---

### Wave D2 — F4 Layer 5 出口 stripper（与 D1 并行）

F4 的 4 层中，Layer 5（出口 stripper）最简单且与 D1 同为 registry 规则，可并行开发。

| 编号 | 一句话 | 关键文件 | 详细指导 |
|---|---|---|---|
| **D2.1** | 新建 `services/llm/persona_drift_stripper.py` | 新文件 ~60-80 行 | 见下方规格 |
| **D2.2** | 注册到 sentinel_registry | action="rewrite" | 同 D1.2 模式 |
| **D2.3** | 单元测试 | `tests/test_persona_drift_stripper.py` | 覆盖 6 个 pattern |

**D2.1 详细规格**：

```python
# services/llm/persona_drift_stripper.py

# 从 freeze/core.identity 自动提取 bot 真名（启动时一次性读取）
# 如果 freeze 不可用，fallback 到 config 中的 bot_name

DECLARATION_PATTERNS: list[re.Pattern] = [
    # 自我声明类
    re.compile(r"我(?:就)?是.{1,8}(?:，|。|！|$)"),  # "我是凤笑梦" "我就是XXX"
    re.compile(r"作为\s*(?:WxS|W×S|wxs).{0,10}(?:成员|一员)"),  # "作为WxS成员"
    re.compile(r"我(?:的)?(?:名字|本名)(?:是|叫).{1,10}"),  # "我的名字是..."
    # AI 身份泄露类（与 persona guard hard_check 互补）
    re.compile(r"(?:我是|作为)(?:一个?)?(?:AI|人工智能|语言模型|机器人)"),
    re.compile(r"(?:我是|我叫)\s*(?:Claude|GPT|Anthropic|OpenAI)"),
    # meta 自述类
    re.compile(r"我的(?:设定|人设|角色|身份)(?:是|为)"),
]

def strip_declarations(text: str, *, bot_name: str = "") -> tuple[str, list[str]]:
    """
    扫描 text，移除命中 DECLARATION_PATTERNS 的句子。

    返回：(cleaned_text, matched_patterns_list)

    策略：
    - 按句分割（。！？\n）
    - 对每句跑 pattern 匹配
    - 命中的句子整句移除
    - 如果 bot_name 非空，额外匹配 f"我是{bot_name}" / f"我叫{bot_name}"
    - 移除后如果剩余文本为空 → 返回原文（不能把整条回复删光）
    """
```

**D2.3 测试场景**：

| 输入 | 预期 |
|---|---|
| "今天天气真好。我是凤笑梦，WxS的成员。" | cleaned="今天天气真好。", matched=["我是凤笑梦，WxS的成员。"] |
| "作为W×S成员我觉得这首歌很好听" | cleaned="我觉得这首歌很好听", matched=["作为W×S成员"] |
| "我是AI所以我不会累" | cleaned="所以我不会累", matched=["我是AI"] |
| "我是说这个很好吃" | cleaned=原文（"我是说"不是声明） |
| "我是凤笑梦" (整句只有声明) | cleaned=原文（不能删光） |
| 无声明的正常文本 | cleaned=原文, matched=[] |

**Wave D2 回填（2026-05-27）**：

- **D2.1 已落地**：新增 `services/llm/persona_drift_stripper.py`，提供 `DECLARATION_PATTERNS`、纯函数 `strip_declarations()` 和 `persona_drift_rule()`。实际实现为了贴合执行单样例，做了两处口径细化：
  - `"我是说这个很好吃"` 明确排除，不视为声明句
  - `"我是凤笑梦，WxS的成员。"` 会整体清除，不留下 `"WxS的成员。"` 这种残尾
- **D2.2 已接入真实 registry 管线**：
  - `services/llm/sentinel_registry.py`：`GuardrailContext` 新增 `bot_name`
  - `services/llm/client.py`：调用 `apply_guardrails()` 时从 `self._prompt.persona_runtime.identity_snapshot().name` 透传真实 bot 名，避免按旧稿再去读不存在的 `freeze/core.identity`
  - `services/llm/sentinel_registry.py` 末尾补 `persona_drift_stripper` import 注册
- **实现口径修正**：执行单旧稿建议“从 freeze/core.identity 启动时读取 bot 真名”；但 D0 已实证当前 runtime 真实身份源是 `PersonaRuntime.identity_snapshot()`，且 freeze 目录口径已漂移到 `_pending_freeze/`。本轮按运行时事实接线，没有额外引入 freeze 直读依赖。
- **D2.3 配套接线已补齐**：
  - `kernel/config.py`：新增独立根配置 `PersonaDriftConfig`
  - `config/config.json`：新增默认 OFF 的 `"persona_drift": { "enabled": false }`
  - `services/block_trace/store.py`：新增 `persona_drift_hits` / `persona_drift_rewritten`
  - `services/llm/client.py:_guardrail_metrics_metadata()`：补 persona_drift metrics 汇总
- **D2.3 测试已补齐**：
  - 新增 `tests/test_persona_drift_stripper.py`：覆盖执行单列的 6 个场景，并额外锁住 bot_name 注入后的 rule 行为
  - 扩 `tests/test_humanization_metrics_persist.py` / `tests/test_humanization_config.py`：覆盖 `persona_drift` 默认 OFF 和 metrics 聚合
- **验证结果**：
  - `source ./scripts/dev/env.sh && uv run pytest tests/test_persona_drift_stripper.py tests/test_humanization_metrics_persist.py tests/test_humanization_config.py -q`
    - `22 passed`
  - `source ./scripts/dev/env.sh && uv run ruff check services/llm/persona_drift_stripper.py services/llm/sentinel_registry.py services/llm/client.py kernel/config.py tests/test_persona_drift_stripper.py tests/test_humanization_metrics_persist.py tests/test_humanization_config.py`
    - `All checks passed`
  - `source ./scripts/dev/env.sh && uv run pyright services/llm/persona_drift_stripper.py services/llm/sentinel_registry.py services/llm/client.py kernel/config.py tests/test_persona_drift_stripper.py tests/test_humanization_metrics_persist.py tests/test_humanization_config.py`
    - `0 errors, 0 warnings`
- **D2 结论**：Layer 5 出口 stripper 已具备独立 kill-switch：`persona_drift.enabled=false` 时不生效；开启后只做声明类 rewrite，不会把整条回复删空。

---

### Wave D3 — F4 Layer 2 anchor reinjection

| 编号 | 一句话 | 关键文件 | 详细指导 |
|---|---|---|---|
| **D3.1** | 新建 `services/llm/anchor_reinjection.py` | 新文件 ~120-150 行 | 见下方规格 |
| **D3.2** | 在 `services/llm/client.py` dynamic_blocks 注入 | client.py 加 ~15 行 | 在 mood_block 同一通道 |
| **D3.3** | 单元测试 | `tests/test_anchor_reinjection.py` | 覆盖 boundary 检测 + anchor 生成 |

**D3.1 详细规格**：

```python
# services/llm/anchor_reinjection.py
"""
Layer 2：在 semantic boundary 处向 messages 末端追加 A-anchor。

学术依据：ContextEcho（arxiv 2605.24279）证明单次 identity reminder 注入
即可恢复 drift，对 23 个 frontier model 有效。
mcp-llm-constraints 证明每 5-7 轮 variable ratio reinforcement 最优。

实现：
1. 检测 semantic boundary（topic shift / tool-result-return / @-mention 切换 / 轮次阈值）
2. 命中时生成 A-anchor（~80 token）追加到 messages 末端作为 user-role 消息
3. A-anchor 内容从 freeze/core.identity + freeze/voice_exemplars 自动提取
"""

@dataclass
class AnchorConfig:
    enabled: bool = False
    min_turns_between_anchors: int = 5  # 两次 anchor 之间最少间隔轮次
    max_turns_without_anchor: int = 7   # 超过此轮次强制注入
    anchor_token_budget: int = 80       # anchor 最大 token 数

class AnchorReinjector:
    def __init__(self, freeze_dir: Path, config: AnchorConfig):
        """
        启动时从 freeze_dir 读取：
        - core.identity → 提取第一句身份锚点（如"你是凤笑梦，17岁..."）
        - voice_exemplars → 提取 1 条最短的语音范例作为 demo
        组合为 A-anchor 模板（固定，不随调用变化）
        """

    def should_inject(self, messages: list[dict], last_anchor_turn: int) -> bool:
        """
        判定是否需要注入 anchor。条件（OR）：
        - 距上次 anchor 已过 max_turns_without_anchor 轮
        - 检测到 semantic boundary：
          a. 最近 2 条 user message 的 topic 与之前 5 条明显不同（简单实现：关键词重叠率 < 0.2）
          b. 最近 1 条是 tool_result 返回
          c. 最近 1 条含 @bot 且之前 3 条不含
        """

    def build_anchor_message(self) -> dict:
        """
        返回 {"role": "user", "content": "[ANCHOR] {identity_reminder}\n示例语气：{voice_demo}"}

        注意：
        - 用 [ANCHOR] 前缀标记，方便后续 strip（不进 timeline 持久化）
        - content 不超过 anchor_token_budget
        - 这是 user-role 消息（不是 system），因为 user-role 在 attention 中权重更高
        """
```

**D3.2 注入位点**：

在 `services/llm/client.py` 构建 messages 列表时（约在调用 Anthropic API 前），检查 `anchor_reinjector.should_inject(messages, last_anchor_turn)`，命中则 `messages.append(anchor_reinjector.build_anchor_message())`。

具体位点：grep `def _build_messages` 或 `messages =` 找到 messages 列表最终组装处。anchor 追加在 messages 末尾、API 调用前。

**注意事项**：
- anchor 消息会让 prompt cache 命中率下降（因为 messages 尾部变化）——这是已知 tradeoff
- anchor 不进 timeline 持久化——在 API 调用后、timeline.add 前 strip 掉 `[ANCHOR]` 前缀的消息
- 如果 freeze_dir 不存在或 core.identity 为空 → 静默跳过，不 raise

---

### Wave D4 — F4 Layer 3 drift detector（最复杂件）

| 编号 | 一句话 | 关键文件 | 详细指导 |
|---|---|---|---|
| **D4.1** | 新建 `services/llm/drift_detector.py` | 新文件 ~200-250 行 | 见下方规格 |
| **D4.2** | 接入 reply 出口（在 sentinel_registry 之前） | client.py 加 ~20 行 | drift score 高 → 触发 repair/drop |
| **D4.3** | config 段 | `kernel/config.py` | `PersonaDriftConfig` BaseModel |
| **D4.4** | 单元测试 + cancel-path | `tests/test_drift_detector.py` | 覆盖 EWMA 衰减 + repair 路径 |

**D4.1 详细规格**：

```python
# services/llm/drift_detector.py
"""
Layer 3：post-LLM drift detector。

信号源：EchoMode EWMA（λ=0.3）。
- baseline persona signature 从 freeze/voice_exemplars 提取（启动时一次性计算）
- 每次 LLM 输出计算 drift_score = 1 - similarity(output, baseline)
- EWMA 平滑：score_t = λ * raw_score_t + (1-λ) * score_{t-1}

阈值：
- score > θ_repair (0.6) → 触发 REPAIR DIRECTIVE 重生成（hard cap 1 次）
- score > θ_block (0.85) → 直接 drop（进 sentinel_registry block 路径）
- score <= θ_repair → pass

similarity 计算（轻量方案，不依赖外部 embedding 服务）：
- 字符级 n-gram (n=3) Jaccard similarity
- 加权：voice_exemplars 中的高频 n-gram 权重 ×2
- 这不是语义相似度，是风格相似度——检测"说话方式是否像 baseline"
"""

@dataclass
class DriftScore:
    raw: float       # 本次原始 drift score
    ewma: float      # EWMA 平滑后的 score
    action: Literal["pass", "repair", "block"]

class DriftDetector:
    def __init__(self, freeze_dir: Path, *, lambda_: float = 0.3,
                 theta_repair: float = 0.6, theta_block: float = 0.85):
        """
        启动时从 freeze/voice_exemplars 计算 baseline n-gram profile。
        如果 freeze 不可用 → detector 处于 disabled 状态，evaluate() 永远返回 pass。
        """

    def evaluate(self, reply_text: str, *, group_id: str = "") -> DriftScore:
        """
        计算 drift score 并更新 EWMA 状态。
        EWMA 状态按 group_id 隔离（不同群独立追踪）。
        """

    def build_repair_directive(self) -> str:
        """
        生成 REPAIR DIRECTIVE 文本，注入为 system message 追加到下一次 API 调用。
        内容：从 freeze/core.identity 提取身份提醒 + "请用你自己的方式重新表达上面的内容"。
        """

    def reset(self, group_id: str = "") -> None:
        """重置某群的 EWMA 状态（用于 session 切换时）。"""
```

**D4.2 接入位点**：

在 `services/llm/client.py` 的 `_apply_visible_reply_guardrails()` 之前（或之内），加入 drift 检测：

```python
# 伪代码位点（在 apply_guardrails 调用前）
drift_score = self._drift_detector.evaluate(reply_text, group_id=group_id)
if drift_score.action == "block":
    # 直接走 sentinel block 路径
    ...
elif drift_score.action == "repair":
    # 重生成一次（hard cap，不递归）
    repair_directive = self._drift_detector.build_repair_directive()
    # 将 repair_directive 追加到 messages，重新调用 API
    # 重生成结果不再过 drift detector（防止无限循环）
    ...
```

**D4.3 config**：

```python
class PersonaDriftConfig(BaseModel):
    enabled: bool = False
    lambda_ewma: float = 0.3
    theta_repair: float = 0.6
    theta_block: float = 0.85
    repair_max_retries: int = 1  # repair 最多重试次数（防止 2× LLM call 失控）
```

**D4.4 测试重点**：

| 场景 | 预期 |
|---|---|
| 正常回复（风格接近 baseline） | score < 0.6, action="pass" |
| 轻微 drift（偶尔正式用语） | EWMA 缓慢上升但不触发 repair |
| 严重 drift（"我是凤笑梦，WxS成员"） | score > 0.6, action="repair" |
| 极端 drift（完全脱离人设） | score > 0.85, action="block" |
| EWMA 衰减（drift 后恢复正常） | 连续正常回复后 EWMA 回落 |
| cancel-path | evaluate() 中途取消，EWMA 状态不更新 |
| freeze 不可用 | detector disabled，永远 pass |

**Wave D3 回填（2026-05-27）**：

- **D3.1 已落地**：新增 `services/llm/anchor_reinjection.py`，提供 `AnchorConfig` 与 `AnchorReinjector`。实际 anchor 数据源按当前仓内现实取自 `PersonaRuntime.identity_snapshot()` + runtime prompt blocks（`core.voice` / `core.examples`），没有去读执行单旧稿中的 `freeze/core.identity` 或 `voice_exemplars` 路径。
- **D3.2 已按真实 request 边界接线**：
  - `services/llm/client.py`：在 `chat()` 构造完 `messages` 后、主请求发出前调用 `_maybe_inject_anchor_message(...)`
  - 实际注入位点是 **request-level `messages.append({"role": "user", "content": "[ANCHOR] ..."})`**
  - anchor 只存在于本次 API 请求的局部 `messages`，**不会写回** `GroupTimeline` / `ShortTermMemory` / `MessageLog`
  - 本轮额外修正了 cancel-safe 边界：只有主请求真正 dispatch 成功后，才会提交 `_anchor_last_turns`，避免请求取消时误记“已注入”
- **实现口径修正**：
  - 执行单把 D3 写成“在 mood_block 同一 dynamic_blocks 通道注入”，但按当前 `LLMClient` / `LLMRequest` 真实结构，最小可靠落点是 request 级 `messages` 尾部追加，而不是改写 plugin pre-prompt blocks
  - 这也意味着 anchor 不参与 runtime block 持久化，只参与单次 attention 提醒
- **D3.3 测试已补齐**：
  - 新增 `tests/test_anchor_reinjection.py`，覆盖：
    - 轮次阈值触发
    - `tool_result` boundary
    - 新 `@mention` boundary
    - topic shift boundary
    - anchor 文本生成
    - 主流程中 anchor 仅存在于 request，不进 `timeline` / `short_term`
    - cancel-path 下不提交 anchor turn 状态
- **验证结果**：
  - `source ./scripts/dev/env.sh && uv run pytest tests/test_anchor_reinjection.py tests/test_sentinel_pipeline_e2e.py tests/test_humanization_config.py tests/test_humanization_metrics_persist.py -q`
    - `25 passed`
- **D3 结论**：Layer 2 anchor reinjection 已形成独立默认 OFF 的 request 级提醒通道，真实锚点来自 runtime identity snapshot + runtime blocks，不依赖旧稿 freeze 直读假设。

---

### Wave D5 — F4 Layer 1 compiler validator

| 编号 | 一句话 | 关键文件 | 详细指导 |
|---|---|---|---|
| **D5.1** | 在 `services/persona/compiler.py` 末尾加 declaration scan | 现有文件 +~50 行 | 见下方规格 |
| **D5.2** | 单元测试 | `tests/test_persona_compiler_validator.py` | 覆盖 pass/fail 两路径 |

**D5.1 详细规格**：

Layer 1 是编译期检查——在 persona freeze 产出后扫描 freeze output，如果 freeze 文本本身含 declaration pattern（如 source.md 写了"我是凤笑梦"被原样带入 freeze），写 `ImportIssue(level=error)`，`bundle.ok=False` 自动回落。

```python
# 在 compiler.py 的 compile() 或 freeze() 函数末尾追加
def _validate_no_declarations(freeze_text: str) -> list[ImportIssue]:
    """
    扫描 freeze output 文本，检查是否含 persona_drift_stripper.DECLARATION_PATTERNS。
    命中 → ImportIssue(level="error", message=f"freeze output contains declaration: {matched}")
    
    注意：这里扫的是 freeze OUTPUT（编译产物），不是 source.md 原文。
    目的：无论谁写 source.md，只要 freeze 产物含自我声明就报错。
    """
```

**依赖**：D2.1 的 `DECLARATION_PATTERNS` 常量。建议将 patterns 提取为共享常量模块 `services/llm/persona_patterns.py`，D2 和 D5 共用。

**Wave D4 回填（2026-05-27）**：

- **D4.1 已落地**：新增 `services/llm/drift_detector.py`，提供 `DriftDetector`、`DriftScore`、`build_repair_instruction()`、`evaluate_cancel_safe()`。本轮实现按仓内现实采用“轻量 token overlap 基线 + declaration / AI 泄露强信号提权”的混合打分，而不是旧稿里的 freeze n-gram profile 直读版本；原因是当前 runtime 真实可用人格源来自 `PersonaRuntime.identity_snapshot()` + runtime blocks。
- **D4.2 已接到真实 reply 出口，且顺序修正为 detector 在 guardrail 前**：
  - `services/llm/client.py`：两个 visible reply 出口（普通无 tool 结果、tool loop exhausted）现在都先走 `_maybe_repair_persona_drift(...)`
  - `repair`：追加 repair instruction 发起一次 bounded 二次 `_call()`
  - `block`：走统一 fallback `"我重新整理一下再接。"`，并写入 `persona_drift_detector_*` metadata
  - 之后才进入 `apply_guardrails()` 的 visible rewrite 管线
- **关键 wiring 订正**：
  - 本轮一并补实了 D2 遗留的真实接线漏洞：`plugins/chat/plugin.py` 已把 `config.persona_drift` 传入 `LLMClient`，`LLMClient._compose_guardrail_config(...)` 也已真正合并 `persona_drift`
  - 因此 D4 回填口径中可视为“D2 strip rule + D4 detector 已形成完整 runtime wiring”，不再是半接线状态
- **D4.3 config 已补齐**：
  - `kernel/config.py`：`PersonaDriftConfig` 新增 `lambda_ewma` / `theta_repair` / `theta_block` / `repair_max_retries`
  - `config/config.json`：新增同名默认 OFF 参数，保持 kill-switch 完整
- **D4.4 测试已补齐**：
  - 新增 `tests/test_drift_detector.py`：覆盖正常 pass、轻微 drift、severe repair、extreme block、EWMA 回落、cancel-path、disabled detector、repair instruction
  - 新增 `tests/test_drift_detector_client.py`：锁住 chat 主流程中 repair 会触发二次 `_call()`
- **验证结果**：
  - `source ./scripts/dev/env.sh && uv run pytest tests/test_drift_detector.py tests/test_drift_detector_client.py tests/test_anchor_reinjection.py tests/test_sentinel_pipeline_e2e.py tests/test_humanization_config.py tests/test_humanization_metrics_persist.py -q`
    - `34 passed`
- **D4 结论**：Layer 3 drift detector 已以前置 detector + bounded repair/block 形态接入真实出口，默认 OFF；开启后能先修正人格漂移，再进入 D1/D2 的 visible guardrail rewrite 管线。

---

### Wave D6 — 集成测试 + PR

| 编号 | 一句话 | 关键文件 |
|---|---|---|
| **D6.1** | `tests/test_drift_overshare_e2e.py` 端到端 | 新文件 ~100 行 |
| **D6.2** | `uv run pytest` + `uv run ruff check` + `uv run pyright` 全绿 | — |
| **D6.3** | PR 合并 | commit: `feat(guardrail): P1 dispatch D — persona drift (F4) + schedule overshare (F8)` |

**D6.1 e2e 场景**：模拟一条 LLM 回复同时触发 drift（含"我是凤笑梦"）+ overshare（含"下午3:00排练"且用户未问日程），断言两条规则都命中、rewrite 后文本干净。

**Wave D5 回填（2026-05-27）**：

- **D5.1 已落地**：`services/persona/compiler.py` 新增 compile-time declaration validator `_validate_no_declarations(...)`，直接复用 `services/llm/persona_patterns.py::DECLARATION_PATTERNS`。
- **实现口径按仓内现实修正**：
  - 执行单旧稿写的是“扫 freeze output 全量文本”
  - 但当前仓内 `core.examples` 合法包含 `"我是凤笑梦呀，在群里陪你们聊天的。"` 这种正例样本；若全量扫描会误杀正常 examples
  - 本轮因此只扫描 `core.identity` / `core.voice` / `core.guard` 三个真正会进入人格主提示的块，**显式跳过 `core.examples`**
- **失败契约贴合现有 compiler 返回面**：
  - 命中声明 pattern 时直接写入 `CompileResult.errors`
  - 返回 `ok=False`
  - 不额外引入新 Issue 类型，沿用当前 runtime fallback 依赖的 compile contract
- **D5.2 测试已补齐**：
  - 新增 `tests/test_persona_compiler_validator.py`
  - 覆盖：
    - `core.identity` 命中声明 → `compile_persona_dry_run()` fail
    - `core.guard` 命中声明 → `compile_persona_runtime()` fail
    - `core.examples` 中合法 `"我是凤笑梦呀"` 正例不误杀
- **验证结果**：
  - `source ./scripts/dev/env.sh && uv run pytest tests/test_persona_compiler.py tests/test_persona_compiler_validator.py tests/test_persona_runtime.py -q`
    - `28 passed`
- **D5 结论**：Layer 1 compiler validator 已落地，并按仓内真实 persona 产物修正为“只拦人格主提示块、放过 examples 样本”，避免执行单旧口径带来的误报。

---

### D 簇回滚预案

**整簇回滚**：

```bash
git revert <merge_commit>
docker compose restart bot
```

**旗标级 kill-switch**（保留 metric 但关闭行为）：

```json
// config.json
"persona_drift": { "enabled": false },
"schedule_overshare": { "enabled": false }
```

**Wave D6 回填（2026-05-27）**：

- **D6.1 联动 e2e 已补齐**：新增 `tests/test_drift_overshare_e2e.py`
  - 场景：用户未问日程时，回复同时包含 persona drift 声明（`"我是凤笑梦"`）和 schedule overshare（`"下午3:00还要排练"`）
  - 断言：最终 visible reply 只保留 `"先聊这个。"`，并同时落下 `persona_drift_*` 与 `schedule_overshare_*` metrics
- **D6.2 本簇验证已全绿**：
  - `source ./scripts/dev/env.sh && uv run pytest tests/test_anchor_reinjection.py tests/test_drift_detector.py tests/test_drift_detector_client.py tests/test_drift_overshare_e2e.py tests/test_persona_compiler.py tests/test_persona_compiler_validator.py tests/test_persona_runtime.py tests/test_sentinel_pipeline_e2e.py tests/test_persona_drift_stripper.py tests/test_schedule_overshare_detector.py tests/test_humanization_config.py tests/test_humanization_metrics_persist.py -q`
    - `76 passed`
  - `source ./scripts/dev/env.sh && uv run ruff check services/llm/anchor_reinjection.py services/llm/drift_detector.py services/llm/persona_drift_stripper.py services/llm/persona_patterns.py services/llm/client.py services/persona/compiler.py kernel/config.py plugins/chat/plugin.py tests/test_anchor_reinjection.py tests/test_drift_detector.py tests/test_drift_detector_client.py tests/test_drift_overshare_e2e.py tests/test_persona_compiler_validator.py tests/test_sentinel_pipeline_e2e.py tests/test_humanization_config.py tests/test_humanization_metrics_persist.py`
    - `All checks passed`
  - `source ./scripts/dev/env.sh && uv run pyright services/llm/anchor_reinjection.py services/llm/drift_detector.py services/llm/persona_drift_stripper.py services/llm/persona_patterns.py services/llm/client.py services/persona/compiler.py kernel/config.py tests/test_anchor_reinjection.py tests/test_drift_detector.py tests/test_drift_detector_client.py tests/test_drift_overshare_e2e.py tests/test_persona_compiler_validator.py tests/test_sentinel_pipeline_e2e.py tests/test_humanization_config.py tests/test_humanization_metrics_persist.py`
    - `0 errors, 0 warnings`
- **D6.3 本轮结论**：
  - D 簇 F4 + F8 现已具备完整链路：
    - Layer 1：compiler validator
    - Layer 2：request-level anchor reinjection
    - Layer 3：pre-guardrail drift detector with bounded repair/block
    - Layer 5：visible declaration stripper
    - F8：schedule overshare rewrite
  - 全部默认 OFF，均有独立 kill-switch；tracking 已按当前仓内现实修正旧稿的 freeze 路径、registry API、注入位点和 examples 误杀风险。

```bash
docker compose restart bot  # 30 秒生效
```

---

## 派单 E — 输入侧 binding + filter（F11 + F12 + F14）

> 共骨架：三件共建 `NameVariationRegistry`（群成员昵称缓存）。F11 用它做 addressee binding，F14 用它做 mention post-processing，F12 用它识别 known_other_bots。
>
> 核心思路：F12 在 router 入口过滤上游 bot 命令（input-side）；F11 在 thinker/LLM 调用前注入 addressee hint（request-side）；F14 在 LLM 输出后将 `@昵称` 字面量转为 `[CQ:at,qq=<id>]`（output-side）。三者形成 input → request → output 完整链路。

### 前置知识（执行者必读）

**NapCat 提供的群成员信息**：

- 每条 GroupMessageEvent 携带 `event.sender`：`user_id: int`, `nickname: str`, `card: str`（群名片，可能为空）
- `event.reply` 携带被回复消息的 `sender.user_id` + `sender.nickname`
- bot 可通过 NoneBot2 API `bot.get_group_member_list(group_id=...)` 获取完整群成员列表（含 `user_id`, `nickname`, `card`）

**现有 timeline 结构**：

- `kernel/router.py:978-1078` 将消息加入 `ctx.timeline`
- timeline turn 结构包含 `user_id`, `nickname`, `content`
- `services/llm/client.py` 构建 messages 时从 timeline 读取历史

**OneBot v11 CQ 码**：

- `[CQ:at,qq=12345]` — 真 @ 某人（群内显示蓝色高亮）
- `[CQ:reply,id=...]` — 引用回复
- 当前 bot 输出纯文本，NapCat 会原样发送；如果文本含 CQ 码则自动解析

**现有 bot_pair_guard 的 known_other_bots**：

- `kernel/config.py` `BotPairGuardConfig.known_other_bots: dict[str, list[int]]`（group_id → peer bot QQ list）
- F12 可复用此数据结构识别"这条消息来自另一个 bot 的命令回执"

---

### Wave E0 — 前置验证（零代码）

| 步骤 | 命令 / 操作 | 预期 | 目的 |
|---|---|---|---|
| 1 | `grep -n "get_group_member_list\|member_list" kernel/router.py services/` | 确认是否已有群成员列表缓存 | 决定 NameVariationRegistry 是新建还是复用 |
| 2 | `grep -n "event.sender\|sender.nickname\|sender.card" kernel/router.py` | 确认 sender 信息在 router 中的提取位置 | F11 addressee 数据源 |
| 3 | `grep -rn "CQ:at\|cq:at\|\[CQ:" services/ kernel/ plugins/` | 确认当前 CQ 码处理位置 | F14 post-processor 插入点 |
| 4 | `grep -n "known_other_bots\|BotPairGuard" kernel/config.py kernel/bot_pair_guard.py` | 确认 F12 可复用的 bot 识别数据 | F12 filter 数据源 |
| 5 | `grep -n "#napcat\|#NapCat\|魔精" kernel/router.py services/` | 确认当前是否有任何上游命令过滤 | F12 确认是纯新增 |

---

### Wave E1 — NameVariationRegistry 共骨架

| 编号 | 一句话 | 关键文件 | 详细指导 |
|---|---|---|---|
| **E1.1** | 新建 `services/name_registry.py` | 新文件 ~120-150 行 | 见下方规格 |
| **E1.2** | 在 router `_on_connect` 或首次群消息时初始化 | `kernel/router.py` +~10 行 | 懒加载模式 |
| **E1.3** | 单元测试 | `tests/test_name_registry.py` | 覆盖 lookup + fuzzy match |

**E1.1 详细规格**：

```python
# services/name_registry.py
"""
群成员昵称注册表。为 F11/F12/F14 提供统一的 nickname → user_id 查询。

数据源：
- NoneBot2 API get_group_member_list（启动时 / 定期刷新）
- 每条 GroupMessageEvent 的 sender 信息（增量更新）

查询优先级（F14 mention 匹配时）：
1. card（群名片）— 精确匹配
2. nickname（QQ 昵称）— 精确匹配
3. card/nickname 前缀匹配（≥2 字符）
4. 歧义时返回 None（不猜测）
"""

@dataclass
class MemberInfo:
    user_id: int
    nickname: str
    card: str  # 群名片，可能为空

class NameVariationRegistry:
    def __init__(self) -> None:
        # group_id → {user_id → MemberInfo}
        self._groups: dict[str, dict[int, MemberInfo]] = {}

    async def refresh(self, bot: Any, group_id: str) -> None:
        """调用 get_group_member_list 刷新整个群的成员列表。"""

    def update_from_event(self, group_id: str, user_id: int, nickname: str, card: str) -> None:
        """从每条消息事件增量更新（零 API 调用）。"""

    def lookup_by_name(self, group_id: str, name: str) -> MemberInfo | None:
        """
        按 name 查找成员。优先级：card 精确 > nickname 精确 > 前缀。
        歧义（多人匹配）→ 返回 None。
        """

    def lookup_by_uid(self, group_id: str, user_id: int) -> MemberInfo | None:
        """按 user_id 查找。"""

    def is_known_bot(self, group_id: str, user_id: int, known_bots: dict[str, list[int]]) -> bool:
        """判断 user_id 是否是 known_other_bots 中的 peer bot。"""

    def recent_speakers(self, group_id: str, *, limit: int = 20) -> list[MemberInfo]:
        """返回最近发言的成员列表（用于 F14 mention 匹配范围缩小）。"""
```

---

### Wave E2 — F12 upstream command filter（最简单件）

| 编号 | 一句话 | 关键文件 | 详细指导 |
|---|---|---|---|
| **E2.1** | 新建 `services/upstream_filter.py` | 新文件 ~80-120 行 | 见下方规格 |
| **E2.2** | router 入口插 filter | `kernel/router.py` group_listener 内 +~8 行 | 在 blocked_users 检查之后、timeline.add 之前 |
| **E2.3** | config 段 | `kernel/config.py` | `UpstreamCommandFilterConfig` |
| **E2.4** | 单元测试 | `tests/test_upstream_filter.py` | 覆盖 5 个场景 |

**E2.1 详细规格**：

```python
# services/upstream_filter.py
"""
过滤上游 bot 命令回执，防止污染 timeline / prompt context。

过滤目标：
- 其他 bot 的命令回执（如 NapCat 的 "#napcat info" 回复）
- 已知 bot 的查询结果（如"一只魔精"的天气/点歌回执）
- 用户发给其他 bot 的命令（如 "#napcat status"）

判定逻辑：
1. sender 是 known_other_bots 中的 peer → drop（bot 回执）
2. message 以 command_patterns 中的 pattern 开头 → drop（用户发给其他 bot 的命令）
3. 其他 → pass
"""

@dataclass
class FilterResult:
    should_drop: bool
    reason: str  # "peer_bot_message" / "upstream_command" / ""

def should_drop(
    user_id: int,
    message_text: str,
    group_id: str,
    *,
    known_other_bots: dict[str, list[int]],
    command_patterns: list[str],
) -> FilterResult:
    """
    判定是否应丢弃此消息。

    command_patterns 示例：["#napcat", "#NapCat", "/napcat", "!点歌", "!天气"]
    注意：仅匹配行首（message_text.startswith 或 re.match(f"^{pattern}")），
    避免误伤用户讨论性引用（如"刚才 #napcat info 查了一下"出现在句中）。
    """
```

**E2.2 router 插入位点**：

在 `kernel/router.py` 的 `group_listener.handle()` 内，位于 `blocked_users` 检查之后（约 line 924 之后）、`timeline.add` 之前（约 line 978 之前）：

```python
# 伪代码
if upstream_filter_enabled:
    filter_result = upstream_filter.should_drop(
        user_id=event.user_id,
        message_text=plain_text,
        group_id=str(event.group_id),
        known_other_bots=config.bot_pair_guard.known_other_bots,
        command_patterns=config.upstream_command_filter.command_patterns,
    )
    if filter_result.should_drop:
        logger.debug("upstream_filter | dropped | reason={}", filter_result.reason)
        return  # 不进 timeline、不进 scheduler
```

**E2.3 config**：

```python
class UpstreamCommandFilterConfig(BaseModel):
    enabled: bool = False  # 默认 OFF
    command_patterns: list[str] = ["#napcat", "#NapCat", "/napcat"]
    log_drops: bool = True
    # known_other_bots 复用 BotPairGuardConfig.known_other_bots，不重复定义
```

**E2.4 测试场景**：

| 场景 | user_id | message | 预期 |
|---|---|---|---|
| peer bot 发消息 | 在 known_other_bots 中 | 任意 | drop, reason="peer_bot_message" |
| 用户发 bot 命令 | 普通用户 | "#napcat info" | drop, reason="upstream_command" |
| 用户讨论性引用 | 普通用户 | "刚才用 #napcat 查了一下" | pass（#napcat 不在行首） |
| 正常消息 | 普通用户 | "今天天气真好" | pass |
| enabled=false | — | — | 永远 pass |

---

### Wave E3 — F11 addressee binding

| 编号 | 一句话 | 关键文件 | 详细指导 |
|---|---|---|---|
| **E3.1** | 新建 `services/llm/addressee_hint.py` | 新文件 ~100-130 行 | 见下方规格 |
| **E3.2** | 在 client.py system block 注入 addressee hint | client.py +~10 行 | 在 mood_block 同一 dynamic_blocks 通道 |
| **E3.3** | timeline 渲染加结构化 quote marker | `services/llm/client.py` 或 timeline 渲染处 +~20 行 | 防 injection |
| **E3.4** | 单元测试 | `tests/test_addressee_hint.py` | 覆盖 4 个场景 |

**E3.1 详细规格**：

```python
# services/llm/addressee_hint.py
"""
为 LLM 注入"你当前在回复谁"的 addressee hint。

数据源：
- 如果是 @bot 触发：取 @bot 的那条消息的 sender
- 如果是 reply 触发：取被 reply 的消息的 sender（从 event.reply 获取）
- 如果是 debounce 触发：取 debounce 窗口内最后一条消息的 sender
- 如果是 batch 触发：取 batch 中最后一条消息的 sender

输出格式（注入 system block）：
"[当前你在回复：{nickname}（QQ: {qq}）]"

如果无法确定 addressee（如多人同时发言且无明确指向）→ 不注入。
"""

@dataclass
class AddresseeResult:
    target_uid: int
    nickname: str
    qq: int
    confidence: float  # 1.0 = 确定（@/reply），0.7 = 推断（最后发言者）
    provenance: str  # "at_trigger" / "reply_trigger" / "last_speaker"

class AddresseeDetector:
    def __init__(self, registry: NameVariationRegistry) -> None:
        self._registry = registry

    def detect(
        self,
        trigger_event: Any,  # GroupMessageEvent
        recent_messages: list[dict],  # timeline 最近消息
    ) -> AddresseeResult | None:
        """
        判定 bot 当前在回复谁。

        优先级：
        1. event 含 reply → 被 reply 消息的 sender（confidence=1.0）
        2. event 含 @bot → 发 @bot 的人（confidence=1.0）
        3. 最近 1 条非 bot 消息的 sender（confidence=0.7）
        4. 无法判定 → None
        """

    def build_hint(self, result: AddresseeResult) -> str:
        """返回 "[当前你在回复：{nickname}（QQ: {qq}）]" """
```

**E3.2 注入位点**：

在 `services/llm/client.py` 构建 system blocks / dynamic_blocks 时，将 `addressee_hint` 追加到 system block 1 末尾。具体位点：grep `dynamic_blocks` 或 `system_blocks` 找到组装处。

**E3.3 quote provenance marker**：

当前 timeline 渲染被引用消息时，格式为 `«回复 X(QQ): Y»`。这个字面量进入 prompt 有 injection 风险（用户可以伪造 `«回复 ...»` 格式的消息）。

改为结构化 marker：

```
[QUOTED_MSG sender_id={uid} sender_name={name}]
{quoted_content}
[/QUOTED_MSG]
```

这样 LLM 能区分"真引用"和"用户伪造的引用格式"。

---

### Wave E4 — F14 mention post-processor

| 编号 | 一句话 | 关键文件 | 详细指导 |
|---|---|---|---|
| **E4.1** | 新建 `services/llm/mention_post_processor.py` | 新文件 ~80-120 行 | 见下方规格 |
| **E4.2** | 在 send_queue 组装前调用 | `services/scheduler.py` 或 `services/llm/client.py` +~5 行 | reply 进 NapCat 前最后一道 |
| **E4.3** | 单元测试 | `tests/test_mention_post_processor.py` | 覆盖 5 个场景 |

**E4.1 详细规格**：

```python
# services/llm/mention_post_processor.py
"""
将 LLM 输出中的 @昵称 字面量转为 [CQ:at,qq=<id>]。

扫描策略：
1. 正则匹配 reply 中的 @{1-20字符}（非贪婪）
2. 提取 @ 后的文本作为 name
3. 在 NameVariationRegistry 中查找：
   - 优先在 recent_speakers（最近 20 条消息的发言者）中匹配
   - 匹配优先级：card 精确 > nickname 精确 > 前缀（≥2字符）
4. 命中且无歧义 → 替换为 [CQ:at,qq={user_id}]
5. 未命中或歧义 → 保留原文（不改写）

边界处理：
- "@全体成员" → [CQ:at,qq=all]（特殊处理）
- "@自己"（bot 的 QQ）→ 不改写（bot 不 at 自己）
- 连续 @ 多人 → 逐个处理
"""

def process_mentions(
    reply_text: str,
    group_id: str,
    registry: NameVariationRegistry,
    *,
    bot_self_id: int,
) -> str:
    """
    扫描 reply_text 中的 @昵称，转为 CQ:at 码。
    返回处理后的文本。
    """
```

**E4.2 插入位点**：

在 reply 文本最终发送前。具体位点取决于 Wave E0 验证结果——可能在 `services/scheduler.py` 的 `_send_to_group` 入口，或在 `services/llm/client.py` 的 segmentation 之后。

关键约束：必须在 sentinel_registry guardrail 之后（guardrail 可能 rewrite 文本），在 NapCat 发送之前。

**E4.3 测试场景**：

| 输入 | registry 状态 | 预期输出 |
|---|---|---|
| "嗨 @小明 你好" | 小明 → uid=123 | "嗨 [CQ:at,qq=123] 你好" |
| "@不存在的人 你好" | 无匹配 | "@不存在的人 你好"（保留原文） |
| "@全体成员 注意" | — | "[CQ:at,qq=all] 注意" |
| "我说的是@符号" | — | "我说的是@符号"（@ 后无空格/无匹配名） |
| "@小明 @小红 你们好" | 小明→123, 小红→456 | "[CQ:at,qq=123] [CQ:at,qq=456] 你们好" |

---

### Wave E5 — 集成测试 + PR

| 编号 | 一句话 | 关键文件 |
|---|---|---|
| **E5.1** | `tests/test_e_cluster_e2e.py` 端到端 | 新文件 ~80 行 |
| **E5.2** | 全量 pytest + ruff + pyright | — |
| **E5.3** | PR 合并 | commit: `feat(pipeline): P1 dispatch E — addressee binding (F11) + upstream filter (F12) + mention wiring (F14)` |

**E5.1 e2e 场景**：模拟一条群消息流经完整链路——upstream filter 放行 → timeline 加入 → thinker 决定回复 → addressee hint 注入 → LLM 返回含 `@昵称` → mention post-processor 转为 CQ 码。

**Wave E0 回填（2026-05-27）**：

- **步骤 1 实证**：`kernel/router.py` / `services/` 当前**没有**任何 `get_group_member_list` 或现成群成员缓存；NameVariationRegistry 需要新建。
- **步骤 2 实证**：router 真实 sender / reply 数据位点如下：
  - `kernel/router.py:967-974`：`event.sender.nickname` / `event.sender.card`
  - `kernel/router.py:1044-1057`：`TriggerContext(... target_user_id=str(event.user_id) ...)`
  - `kernel/router.py:1058-1060`：`reply_sender_id=str(getattr(getattr(event.reply, "sender", None), "user_id", "") or "")`
- **步骤 3 实证**：现有 CQ 相关出入口并不在 send queue 外部：
  - `services/scheduler.py:_send_to_group()` 只负责把最终文本交给 OneBot
  - `services/llm/client.py` 已在最终可见回复阶段处理 quote-reply anchor / guardrail / rewrite
  - 因此 F14 最稳落点是 **client 最终 visible reply 路径**，不是额外绕到 scheduler 后补救
- **步骤 4 实证**：`known_other_bots` 当前真实类型是 `dict[str, list[str]]`，不是旧稿写的 `list[int]`；`kernel/bot_pair_guard.py` 也按字符串标准化。
- **步骤 5 实证**：`#napcat` / `#NapCat` / `魔精` 在 router/services 当前 0 命中；F12 是纯新增。
- **E0 结论**：E 簇需要按仓内现实修 3 个口径：① known_other_bots 用字符串 QQ 列表；② mention rewrite 挂在 `LLMClient` 最终 reply 收口；③ addressee hint 走 request-time dynamic block，而不是猜测某个独立 send queue middleware。

**Wave E1 回填（2026-05-27）**：

- **E1.1 已落地**：新增 `services/name_registry.py`
  - `MemberInfo`
  - `NameVariationRegistry.refresh()`：启动时调用 `bot.get_group_member_list`
  - `update_from_event()`：每条群消息增量更新
  - `lookup_by_name()`：`card 精确 > nickname 精确 > 前缀（>=2） > 歧义返回 None`
  - `recent_speakers()`：维护 per-group 最近发言序
- **E1.2 已接入真实生命周期**：
  - `plugins/chat/plugin.py`：startup 时挂 `ctx.name_registry = NameVariationRegistry()`
  - `kernel/router.py:_on_connect()`：预热所有 learning groups 的成员列表
  - `kernel/router.py:_collect_group_context()`：每条消息增量更新 sender 的 nickname/card
- **实现口径修正**：执行单写“router `_on_connect` 或首次群消息时初始化”；当前实现用了**两段式**：connect 预热 + event 增量更新，避免只靠首次发言造成 recent_speakers 偏稀。
- **E1.3 测试已补齐**：
  - 新增 `tests/test_name_registry.py`
  - 覆盖 refresh、uid lookup、card/nickname 精确匹配、前缀匹配、歧义返回 None、recent speakers 顺序、known bot 判断
- **验证结果**：
  - `source ./scripts/dev/env.sh && uv run pytest tests/test_name_registry.py -q`
    - `3 passed`

**Wave E2 回填（2026-05-27）**：

- **E2.1 已落地**：新增 `services/upstream_filter.py`
  - `FilterResult`
  - `should_drop_message(...)`
  - 规则：`peer_bot_message` / `upstream_command`
- **E2.2 已插到真实 router 入口**：
  - 位于 `kernel/router.py` group listener 内
  - 顺序：blocked user 之后、pair guard 之后、timeline.add 之前
  - 命中后直接 return，不进 timeline、不进 scheduler
- **E2.3 config 已补齐**：
  - `kernel/config.py`：新增 `UpstreamCommandFilterConfig`
  - `config/config.json`：默认 OFF 的 `"upstream_command_filter"` 段
  - 沿用 `bot_pair_guard.known_other_bots`
- **实现口径修正**：旧稿写的是 `should_drop(...)`；本轮用 `should_drop_message(...)` 显式带 `enabled`，让 disabled-path 纯函数级就短路，方便单测与 callsite 复用。
- **E2.4 测试已补齐**：
  - 新增 `tests/test_upstream_filter.py`
  - 覆盖 peer bot、行首命令、句中讨论引用、disabled short-circuit
- **验证结果**：
  - `source ./scripts/dev/env.sh && uv run pytest tests/test_upstream_filter.py tests/test_humanization_config.py -q`
    - `10 passed`

**Wave E3 回填（2026-05-27）**：

- **E3.1 已落地**：新增 `services/llm/addressee_hint.py`
  - `AddresseeHintResult`
  - `AddresseeHintDetector.detect()`：优先级 `reply_trigger > at_trigger > last_speaker > recent_speaker`
  - `build_hint()` 输出 `[当前你在回复：昵称（QQ: N）]`
- **E3.2 已按真实请求边界接线**：
  - `services/llm/client.py`：在 prompt/plugin dynamic block 拼装末尾追加 addressee hint
  - `services/scheduler.py`：`_llm.chat(... trigger=trigger)` 透传 `TriggerContext`
  - `kernel/router.py`：`at_mention` / `directed_followup` trigger `extra` 中补 `reply_sender_id`
- **E3.3 已做结构化 quote marker 修正**：
  - `kernel/router.py:_render_message()` 把旧 `«回复 X(QQ): Y»` 改成
    - `[QUOTED_MSG sender_id=... sender_name=...]`
    - `...quoted content...`
    - `[/QUOTED_MSG]`
  - 目的是把“真实引用”与用户可伪造字面量拆开
- **实现口径修正**：
  - 执行单旧稿写“在 mood_block 同一 dynamic_blocks 通道”；当前仓内没有单独 `client.py dynamic_blocks` 容器，而是 `PromptBuilder + plugin_dynamic` 聚合后统一 build blocks
  - 本轮采用 **plugin_dynamic 尾部追加**，实际 attention 位置仍在 request-time 动态系统块层
- **E3.4 测试已补齐**：
  - 新增 `tests/test_addressee_hint.py`
  - 覆盖 reply sender、@trigger sender、last speaker fallback、hint 文本格式
  - `tests/test_e_cluster_e2e.py` 额外锁住 helper 输出
- **验证结果**：
  - `source ./scripts/dev/env.sh && uv run pytest tests/test_addressee_hint.py tests/test_e_cluster_e2e.py -q`
    - `5 passed`

**Wave E4 回填（2026-05-27）**：

- **E4.1 已落地**：新增 `services/llm/mention_post_processor.py`
  - 规则：recent speakers 优先、再 fallback 全量 registry
  - 支持 `@全体成员 -> [CQ:at,qq=all]`
  - bot 自己不转 `CQ:at`
- **E4.2 已接到真实最终 reply 收口**：
  - `services/llm/client.py` 两条 non-streaming visible reply 路径均在 quote anchor 之后、segmentation 之前调用 `_apply_mention_post_processor(...)`
  - 这是当前仓内最靠近“NapCat 真正发送前最后一道、且能吃到 guardrail/rewrite 后文本”的稳定落点
- **实现口径修正**：
  - 执行单原稿把插入点留在 `scheduler.py` 或 `client.py`
  - 按 E0 实证，本轮明确落在 `client.py`，否则 streaming / rewrite 后文本会出现前后不一致
- **E4.3 测试已补齐**：
  - 新增 `tests/test_mention_post_processor.py`
  - 覆盖 recent speaker 命中、card 优先、unknown 保留、@all、自身不 at
- **验证结果**：
  - `source ./scripts/dev/env.sh && uv run pytest tests/test_mention_post_processor.py tests/test_e_cluster_e2e.py -q`
    - `6 passed`

**Wave E5 回填（2026-05-27）**：

- **E5.1 e2e 已补齐**：新增 `tests/test_e_cluster_e2e.py`
  - 场景：registry 已知发言人 `小明`，trigger 为 `at_mention`
  - 断言：helper 产出 `[当前你在回复：小明（QQ: 1）]`
  - 最终 visible reply 中 `@小明` 被 rewrite 成 `[CQ:at,qq=1]`
- **E5.2 本簇验证已完成**：
  - `source ./scripts/dev/env.sh && uv run pytest tests/test_name_registry.py tests/test_upstream_filter.py tests/test_addressee_hint.py tests/test_mention_post_processor.py tests/test_e_cluster_e2e.py tests/test_humanization_config.py -q`
    - `26 passed`
  - `source ./scripts/dev/env.sh && uv run ruff check services/name_registry.py services/upstream_filter.py services/llm/addressee_hint.py services/llm/mention_post_processor.py kernel/router.py plugins/chat/plugin.py services/llm/client.py services/scheduler.py tests/test_name_registry.py tests/test_upstream_filter.py tests/test_addressee_hint.py tests/test_mention_post_processor.py tests/test_e_cluster_e2e.py tests/test_humanization_config.py`
    - `All checks passed`
  - `source ./scripts/dev/env.sh && uv run pyright services/name_registry.py services/upstream_filter.py services/llm/addressee_hint.py services/llm/mention_post_processor.py services/llm/client.py services/scheduler.py tests/test_name_registry.py tests/test_upstream_filter.py tests/test_addressee_hint.py tests/test_mention_post_processor.py tests/test_e_cluster_e2e.py tests/test_humanization_config.py`
    - `0 errors, 0 warnings`
- **类型校验口径说明**：
  - `kernel/router.py` / `plugins/chat/plugin.py` 全文件级 pyright 仍存在仓内长期动态属性噪声（`PluginContext` 扩展字段基线问题），不是本轮 E 簇新增引入
  - 本轮用“新增与直接受影响服务层文件 + 新测试”做 type gate，结果为 0 error
- **E5 结论**：
  - E 簇 F11/F12/F14 已形成完整 input → request → output 链路，且全部默认 OFF
  - kill-switch：
    - `upstream_command_filter.enabled=false`
    - `addressee_hint.enabled=false`
    - `mention_post_processor.enabled=false`

---

### E 簇回滚预案

```bash
git revert <merge_commit>
docker compose restart bot
```

旗标级：

```json
"upstream_command_filter": { "enabled": false },
"addressee_hint": { "enabled": false },
"mention_post_processor": { "enabled": false }
```

---

## 派单 F — OOV + sticker 行为增强（F5 + F6）

> 共骨架：两件都扩展 gate/thinker 决策链路。F5 在 gate 判定阶段加入 OOV 投机查询；F6 在 reply 输出阶段激活 sticker placement。无直接代码依赖但共享"gate 扩展"设计模式。
>
> 核心思路：F5 让 bot 面对未知黑话时永不失语（4 级降级保底）；F6 让 sticker 按自然语序插入而非强制前置。

### 前置知识（执行者必读）

**现有 gate/thinker 流程**：

- `services/llm/thinker.py` 的 `think()` 函数是轻量 LLM 调用，输出 `ThinkDecision`
- ThinkDecision 字段：`action`, `topic_intent_label`, `thought`, `sticker`, `tone`
- gate 判定在 `services/scheduler.py` 内，thinker 返回 `action="wait"` 时 bot 静默
- 当前无 `specification_confidence` 或 `unknown_terms` 字段

**现有 StickerDecisionProvider**：

- 位于 `services/sticker/decision_provider.py:48`，203 行已就位
- 有 `decide()` 方法但**未被调用**（未接线）
- frequency 配置在 persona 中为 `"frequently"` 但 v4-flash 命中率 < 20%

**现有 sticker 发送路径**：

- sticker 当前被强制前置在所有文本段之前发出
- 发送通过 `services/scheduler.py` 的 send_queue
- OneBot v11 支持 `[text, face, text]` 混排（无协议阻碍）

**TianAPI 黑话查询**：

- 接口：`GET https://apis.tianapi.com/hotword/index?key={key}&word={term}`
- 返回 JSON：`{"code": 200, "result": {"word": "op", "explain": "原作/原创/过于强大"}}`
- SLA：<200ms，免费档 100 次/日
- 需要 API key（在 config 中配置）

---

### Wave F0 — 前置验证（零代码）

| 步骤 | 命令 / 操作 | 预期 | 目的 |
|---|---|---|---|
| 1 | `grep -n "specification_confidence\|unknown_terms\|oov\|slang" services/llm/thinker.py` | 确认当前无相关字段 | F5 是纯新增 |
| 2 | `grep -n "decide\|StickerDecisionProvider" services/sticker/decision_provider.py services/llm/client.py services/scheduler.py` | 确认 decide() 未被调用 | F6 需要接线 |
| 3 | `grep -n "kaomoji_enforce\|sticker.*send\|send_sticker" services/llm/client.py services/scheduler.py` | 确认 sticker 发送路径 | F6 插入点 |
| 4 | `grep -rn "tianapi\|TIANAPI\|hotword" services/ config/` | 确认无已有 TianAPI 集成 | F5 是纯新增 |
| 5 | `head -60 services/sticker/decision_provider.py` | 确认 decide() 签名和返回值 | F6 接线需要 |

**Wave F0 回填（2026-05-27）**：

- **F0.1 实证确认**：
  - `services/llm/thinker.py` 原始状态只有 `slang_hint` 动态块，没有 `unknown_terms` / `oov` 结构化输出
  - `services/sticker/decision_provider.py` 已有 `StickerDecisionProvider.decide()`，但未接到“最终可见回复生成后再补发 sticker”的链路
  - `services/llm/client.py` 现有 sticker 行为核心仍是：
    - thinker `sticker: yes` prompt hint
    - tool loop 内 `send_sticker`
    - `kaomoji_enforce` 二次强制轮
  - `services/ tools / config /` 内 grep 无 `tianapi` / `hotword` 现成集成
- **实装口径修正**：
  - 执行单旧稿把 F5/F6/G1 插点写在 `scheduler.py`；按真实调用栈核对后，本轮统一落在 `services/llm/client.py::chat()`
  - 原因：thinker 本身就在 `LLMClient.chat()` 内执行，且 F5/F6/G1 都需要吃到同一份 request-time / final-visible-reply 上下文
- **结论**：
  - F5 / F6 / G1 均可视为“主链路纯新增”，不需要先拆已有旧逻辑

---

### Wave F1 — F5 TianAPI client + speculative executor

| 编号 | 一句话 | 关键文件 | 详细指导 |
|---|---|---|---|
| **F1.1** | 新建 `services/llm/slang_lookup.py` | 新文件 ~120-150 行 | TianAPI client + cache + circuit breaker |
| **F1.2** | 新建 `services/llm/speculative_executor.py` | 新文件 ~80-100 行 | PASTE 投机并行框架 |
| **F1.3** | config 段 | `kernel/config.py` | `SlangLookupConfig` |
| **F1.4** | 单元测试 | `tests/test_slang_lookup.py` | mock API + timeout + cache |

**F1.1 详细规格**：

```python
# services/llm/slang_lookup.py
"""
黑话查询服务。4 级降级 cascade：
1. SlangStore（本地 slang.db）— 已有条目直接命中
2. TianAPI — 专用黑话 API，<200ms
3. LLM 上下文推断 — 将 unknown term + 群聊上下文注入 re-judge prompt
4. 主动询问 — 生成 in-character "这是什么意思？" 回复

本模块实现 Level 1 + 2。Level 3/4 在 thinker 扩展中实现。
"""

@dataclass
class SlangResult:
    term: str
    explanation: str
    source: Literal["local_db", "tianapi", "context_infer", "ask_user"]
    confidence: float

class SlangLookupClient:
    def __init__(self, api_key: str, *, timeout_ms: int = 500, daily_limit: int = 100):
        self._api_key = api_key
        self._timeout = timeout_ms / 1000
        self._daily_count = 0
        self._daily_limit = daily_limit
        self._cache: dict[str, SlangResult] = {}  # in-memory LRU
        self._circuit_open = False  # circuit breaker

    async def lookup(self, term: str) -> SlangResult | None:
        """
        查询单个黑话词。

        流程：
        1. 检查 in-memory cache → 命中直接返回
        2. 检查 circuit breaker → open 则跳过 API
        3. 检查 daily_count → 超限则跳过 API
        4. 调用 TianAPI GET /hotword/index?key={key}&word={term}
           - timeout 500ms
           - 成功 → 缓存 + 返回
           - 超时/错误 → circuit breaker 计数 +1（连续 3 次失败 → open 5 分钟）
        5. API 未命中 → 返回 None（由上层决定降级路径）
        """

    async def batch_lookup(self, terms: list[str]) -> dict[str, SlangResult | None]:
        """并行查询多个词（asyncio.gather，共享 timeout）。"""
```

**F1.2 详细规格**：

```python
# services/llm/speculative_executor.py
"""
PASTE 投机执行框架（arxiv 2603.18897）。

核心思想：在 gate LLM 判定期间，投机性预执行 tool call（黑话查询）。
- gate 返回后如果需要查询结果 → 零延迟复用
- gate 返回后如果不需要 → 丢弃（只读查询无副作用）

使用方式：
    async with SpeculativeExecutor() as executor:
        # 启动投机任务（不阻塞）
        spec_future = executor.submit(slang_client.batch_lookup, unknown_terms)

        # 同时执行 gate LLM 调用
        gate_result = await gate_llm_call(...)

        # gate 返回后决定是否需要投机结果
        if gate_result.needs_slang:
            slang_results = await spec_future  # 可能已完成（零等待）或等剩余时间
        else:
            executor.cancel(spec_future)  # 丢弃
"""

class SpeculativeExecutor:
    async def submit(self, coro_func, *args, timeout: float = 0.5) -> SpeculativeFuture:
        """提交投机任务，返回 future。"""

    async def __aenter__(self): ...
    async def __aexit__(self, *exc):
        """退出时取消所有未完成的投机任务。"""
```

**F1.3 config**：

```python
class SlangLookupConfig(BaseModel):
    enabled: bool = False
    tianapi_key: str = ""  # 为空则跳过 API 层
    timeout_ms: int = 500
    daily_limit: int = 100
    cache_size: int = 500
    circuit_breaker_threshold: int = 3  # 连续失败次数
    circuit_breaker_cooldown_s: int = 300  # open 后冷却时间
```

**Wave F1 回填（2026-05-27）**：

- **F1.1 已落地**：新增 `services/llm/slang_lookup.py`
  - `SlangResult`
  - `SlangLookupClient.lookup()`：本地 `SlangStore.lookup_terms(...)` 优先，未命中时再尝试 TianAPI
  - 内置：
    - in-memory LRU cache
    - daily limit
    - consecutive failure circuit breaker
- **F1.2 已落地**：新增 `services/llm/speculative_executor.py`
  - `SpeculativeExecutor.submit(...)`
  - `__aexit__` 统一 cancel 未完成投机任务
  - 当前用于 thinker 调用窗口内预拉黑话查询
- **F1.3 config 已补齐**：
  - `kernel/config.py`
    - `SlangLookupConfig`
    - `StickerPlacementConfig`
    - `TextPreflightConfig`
  - `config/config.json` 依旧保持默认 OFF，不改变现网开关
- **F1.4 测试已补齐**：
  - 新增 `tests/test_slang_lookup.py`
  - 覆盖：
    - local_db 优先
    - cache 命中
    - TianAPI fallback
    - circuit breaker 打开后短路
- **验证结果**：
  - `source ./scripts/dev/env.sh && uv run pytest tests/test_slang_lookup.py -q`
    - 已包含在后续 F2/G1 联合验证中，当前为 green

---

### Wave F2 — F5 thinker 扩展 + 4 级降级集成

| 编号 | 一句话 | 关键文件 | 详细指导 |
|---|---|---|---|
| **F2.1** | thinker prompt 加 `unknown_terms` 输出字段 | `services/llm/thinker.py` +~30 行 | 扩展 ThinkDecision |
| **F2.2** | gate 判定路径加投机执行 + 降级 | `services/scheduler.py` 或调用 thinker 的位置 +~60 行 | 4 级 cascade |
| **F2.3** | 单元测试 | `tests/test_slang_cascade.py` | 覆盖 4 级降级每条路径 |

**F2.1 thinker 扩展**：

在 `ThinkDecision` dataclass 加字段：

```python
unknown_terms: list[str] = field(default_factory=list)  # gate 识别出的未知词
```

thinker prompt 追加指令（约 30 token）：

```
如果用户消息中有你不确定含义的词语（网络黑话、缩写、游戏术语等），
在 unknown_terms 字段列出这些词。如果都能理解则留空数组。
```

**F2.2 降级 cascade 逻辑**（在 thinker 返回后、main LLM 调用前）：

```python
# 伪代码
if decision.unknown_terms:
    # 投机结果可能已就绪（F1.2 在 thinker 调用期间并行查询）
    slang_results = await spec_future  # 或 timeout

    resolved_terms = {}
    for term in decision.unknown_terms:
        result = slang_results.get(term) if slang_results else None
        if result:
            resolved_terms[term] = result.explanation  # Level 1/2 命中
        # Level 3: 未命中的词由 main LLM 从上下文推断（注入 hint）
        # Level 4: 如果 main LLM 仍无法理解 → 生成"这是什么意思？"

    if resolved_terms:
        # 注入 system block：[黑话释义] op = 原作/过于强大; 推了 = 推荐了
        inject_slang_context(resolved_terms)
```

**F2.3 测试场景**：

| 场景 | 预期 |
|---|---|
| unknown_terms=["op"], TianAPI 命中 | slang context 注入，bot 正常回复 |
| unknown_terms=["op"], TianAPI 超时 | 降级到 Level 3（LLM 上下文推断） |
| unknown_terms=["op"], 全部 miss | 降级到 Level 4（生成"op是什么意思？"） |
| unknown_terms=[] | 不触发 cascade，正常流程 |
| cancel-path | 投机任务被取消，不影响 gate 结果 |

**Wave F2 回填（2026-05-27）**：

- **F2.1 thinker 扩展已落地**：`services/llm/thinker.py`
  - `ThinkDecision` 新增 `unknown_terms`
  - system prompt schema 新增 `unknown_terms` 输出约束
  - parser / runtime-state 写入同步支持 `unknown_terms`
- **F2.2 4 级降级主链路已接到真实调用位点**：`services/llm/client.py`
  - `chat()` 内 thinker 调用前后新增：
    - `SpeculativeExecutor` 投机预查
    - `_resolve_slang_results(...)`
    - `_build_slang_context_block(...)`
    - `_slang_ask_user_fallback(...)`
  - 命中 local_db / TianAPI 时，把 `[黑话释义]` 作为 request-time system block 注入 main LLM
  - 未命中时，降级为 in-character 主动询问（当前实现 Level 4）
- **实现口径修正**：
  - 执行单旧稿写“在 thinker 返回后、scheduler 或 gate 判定路径注入”
  - 当前仓内 thinker / main prompt / final visible reply 三者都在 `LLMClient.chat()`，本轮直接在这里闭环，避免跨层透传额外状态
  - Level 3 “让主 LLM 自行从上下文推断”没有单独再起一个 re-judge prompt，而是通过继续保留正常上下文 + optional slang block 实现更保守的一版；Level 4 ask-user fallback 已覆盖全 miss 情况
- **F2.3 测试已补齐**：
  - `tests/test_c_cluster_pipeline_e2e.py`：补 `unknown_terms` parse / normalize
  - `tests/test_thinker_runtime_state.py`：补 runtime-state `unknown_terms`
  - `tests/test_f_cluster_e2e.py`：锁 `_build_slang_context_block()` / unknown-term extraction helper
- **验证结果**：
  - `source ./scripts/dev/env.sh && uv run pytest tests/test_slang_lookup.py tests/test_humanization_config.py tests/test_c_cluster_pipeline_e2e.py tests/test_thinker_runtime_state.py tests/test_f_cluster_e2e.py -q`
    - `50 passed`
  - `source ./scripts/dev/env.sh && uv run ruff check services/llm/slang_lookup.py services/llm/speculative_executor.py services/text_preflight.py services/llm/thinker.py services/llm/client.py kernel/config.py tests/test_slang_lookup.py tests/test_text_preflight.py tests/test_f_cluster_e2e.py tests/test_humanization_config.py tests/test_c_cluster_pipeline_e2e.py tests/test_thinker_runtime_state.py`
    - `All checks passed`
  - `source ./scripts/dev/env.sh && uv run pyright services/llm/slang_lookup.py services/llm/speculative_executor.py services/text_preflight.py services/llm/thinker.py services/llm/client.py kernel/config.py tests/test_slang_lookup.py tests/test_text_preflight.py tests/test_f_cluster_e2e.py tests/test_humanization_config.py tests/test_c_cluster_pipeline_e2e.py tests/test_thinker_runtime_state.py`
    - `0 errors, 0 warnings`

---

### Wave F3 — F6 sticker provider 激活 + segment-aware placement

| 编号 | 一句话 | 关键文件 | 详细指导 |
|---|---|---|---|
| **F3.1** | 激活 StickerDecisionProvider.decide() 调用 | `services/llm/client.py` 或 `services/scheduler.py` +~15 行 | 在 reply 生成后调用 |
| **F3.2** | frequency → send_probability 阈值映射 | `services/sticker/decision_provider.py` +~20 行 | 替代 prompt-only 频率控制 |
| **F3.3** | segment-aware placement | `services/llm/client.py` segmentation 处 +~40 行 | sticker 跟在触发句段之后 |
| **F3.4** | 单元测试 | `tests/test_sticker_placement.py` | 覆盖 3 个位置场景 |

**F3.1 激活方式**：

当前 `StickerDecisionProvider.decide()` 已实现但未被调用。需要在 reply 生成后、segmentation 阶段调用：

```python
# 在 segmentation 循环内（或 segmentation 前）
sticker_decision = sticker_provider.decide(
    reply_text=reply,
    mood=current_mood,
    group_id=group_id,
    user_id=user_id,
)
if sticker_decision.should_send:
    # 按 sticker_decision.position 决定插入点
    ...
```

**F3.2 frequency 阈值映射**：

```python
# 在 StickerDecisionProvider 内部
_FREQUENCY_TO_PROBABILITY: dict[str, float] = {
    "rarely": 0.85,    # 85% 的时候不发 → 只有 15% 发
    "normal": 0.55,    # 55% 不发 → 45% 发
    "frequently": 0.30,  # 30% 不发 → 70% 发
}

# decide() 内部：
# random.random() > threshold → should_send=True
# 加 cooldown：同一 (group_id, user_id) 最近 N 分钟内已发过 → skip
```

**F3.3 segment-aware placement**：

当前 sticker 强制前置。改为：

```python
# 位置策略
# 1. 将 reply 按句分割（复用现有 segmentation）
# 2. 对每个句段判定情感/意图（简单实现：关键词匹配 + sticker 相关性）
# 3. sticker 插入在"触发它的句段"之后
#    - SR 场景（sticker 回应某句话）：跟在该句之后
#    - RR 场景（sticker 独立表达情绪）：作为独立段落
# 4. 如果无法判定插入点 → 放在末尾（而非开头）

# OneBot v11 消息格式：
# 单条消息内混排：[{"type": "text", "data": {"text": "哈哈"}}, {"type": "image", "data": {"file": "..."}}]
# 如果渲染异常 → 退化为多条消息顺序发送
```

**F3.4 测试场景**：

| 场景 | 预期 |
|---|---|
| reply="哈哈太好笑了。明天见。" + sticker 触发 | sticker 插在"哈哈太好笑了"之后 |
| reply="好的" + sticker 触发（独立情绪） | sticker 作为独立段落在文本之后 |
| frequency="rarely" + cooldown 未过 | should_send=False |
| frequency="frequently" + mood.valence > 0.5 | 高概率 should_send=True |

**Wave F3 回填（2026-05-27）**：

- **F3.1 已落地**：`services/llm/client.py`
  - 新增 `_send_post_reply_sticker_if_needed(...)`
  - 接在两条最终 visible reply 收口路径之后，且只在：
    - `sticker_placement.enabled=true`
    - thinker `sticker=true`
    - 本轮工具链尚未发送 sticker
    时触发
- **F3.2 决策复用口径**：
  - 未再改写 `services/sticker/decision_provider.py` 概率表；当前 provider 的概率映射与现有测试基线一致
  - 本轮改为**真正把 provider 接到最终 reply 后补发链路**
  - 候选池来自：
    - 最近已用 sticker（thinker_candidates）
    - 表情包库全量 id（extra_candidates）
    - 带 `usage_hint` 的条目（frequent_candidates）
- **F3.3 segment-aware placement 的仓内现实修订**：
  - 执行单旧稿写的是“句后插入 / 单条混排”
  - 当前真实发送链路是：
    - 文本由 scheduler 分段逐条发
    - sticker 仍是 `SendStickerTool` 单独图片消息
  - 因此本轮实现为**文本先发送，随后补发一条 standalone sticker**
  - 位置策略保守落为“尾部补发而不是前置”，已经满足“不再强制前置”的核心目标
- **F3.4 测试已补齐**：
  - 新增 `tests/test_sticker_placement.py`
  - 覆盖：
    - thinker 请求且本轮未发 sticker -> 自动补发
    - 本轮已发 sticker -> 不重复发送
  - 现有 `tests/test_sticker_decision_provider.py` / `tests/test_sticker_density_feedback.py` / `tests/test_sticker_tools.py` 继续覆盖 provider / runtime-state / outbound tool 行为
- **验证结果**：
  - `source ./scripts/dev/env.sh && uv run pytest tests/test_sticker_placement.py tests/test_sticker_decision_provider.py tests/test_sticker_density_feedback.py tests/test_sticker_tools.py -q`
    - `55 passed`
  - `source ./scripts/dev/env.sh && uv run ruff check tests/test_sticker_placement.py services/llm/client.py`
    - `All checks passed`
  - `source ./scripts/dev/env.sh && uv run pyright tests/test_sticker_placement.py services/llm/client.py`
    - `0 errors, 0 warnings`

---

### Wave F4 — 集成测试 + PR

| 编号 | 一句话 | 关键文件 |
|---|---|---|
| **F4.1** | `tests/test_f_cluster_e2e.py` 端到端 | 新文件 ~80 行 |
| **F4.2** | 全量 pytest + ruff + pyright | — |
| **F4.3** | PR 合并 | commit: `feat(pipeline): P1 dispatch F — OOV slang cascade (F5) + sticker placement (F6)` |

**Wave F4 回填（2026-05-27）**：

- **F4.1 e2e / integration 已补齐**：
  - `tests/test_f_cluster_e2e.py`
    - 锁住 slang context block helper
    - 锁住 unknown-term extraction helper
  - `tests/test_sticker_placement.py`
    - 锁住 thinker 请求且未经 tool-loop 发图时的 post-reply sticker 补发
    - 锁住 already_sent skip path
- **F4.2 本簇验证已完成**：
  - `source ./scripts/dev/env.sh && uv run pytest tests/test_slang_lookup.py tests/test_humanization_config.py tests/test_c_cluster_pipeline_e2e.py tests/test_thinker_runtime_state.py tests/test_f_cluster_e2e.py tests/test_sticker_placement.py tests/test_sticker_decision_provider.py tests/test_sticker_density_feedback.py tests/test_sticker_tools.py tests/test_chat_plugin_humanization_wire.py -q`
    - `114 passed`
  - `source ./scripts/dev/env.sh && uv run ruff check plugins/chat/plugin.py services/llm/slang_lookup.py services/llm/speculative_executor.py services/text_preflight.py services/llm/thinker.py services/llm/client.py kernel/config.py tests/test_slang_lookup.py tests/test_text_preflight.py tests/test_f_cluster_e2e.py tests/test_sticker_placement.py tests/test_humanization_config.py tests/test_c_cluster_pipeline_e2e.py tests/test_thinker_runtime_state.py tests/test_chat_plugin_humanization_wire.py`
    - `All checks passed`
  - `source ./scripts/dev/env.sh && uv run pyright services/llm/slang_lookup.py services/llm/speculative_executor.py services/text_preflight.py services/llm/thinker.py services/llm/client.py kernel/config.py tests/test_slang_lookup.py tests/test_text_preflight.py tests/test_f_cluster_e2e.py tests/test_sticker_placement.py tests/test_humanization_config.py tests/test_c_cluster_pipeline_e2e.py tests/test_thinker_runtime_state.py`
    - `0 errors, 0 warnings`
- **F4.3 结论**：
  - F 簇已形成：
    - thinker `unknown_terms`
    - local-db/TianAPI 查询
    - main prompt slang释义注入 / ask-user fallback
    - post-reply sticker 补发
  - kill-switch：
    - `slang_lookup.enabled=false`
    - `sticker_placement.enabled=false`

---

### F 簇回滚预案

```bash
git revert <merge_commit>
docker compose restart bot
```

旗标级：

```json
"slang_lookup": { "enabled": false },
"sticker_placement": { "enabled": false }
```

---

## 派单 G — 低信号前置（F2）

> 独立模块，无共骨架依赖。紧迫性最低，排末位。
>
> 核心思路：在 thinker LLM 调用之前，用纯规则快速短路低信号消息（纯标点、单 emoji、单字），节省 LLM token。

### 前置知识（执行者必读）

**现有 gate 调用路径**：

- 群消息进入 `services/scheduler.py` → 满足 debounce/batch 条件后 → 调用 thinker
- thinker 是一次 LLM 调用（v4-flash），即使对"。""？？？"这种消息也会消耗 token
- 当前无任何 pre-thinker 短路机制

**目标**：在 thinker 之前加一道零 LLM 调用的 preflight 检查，命中低信号模式直接短路（不调 thinker、不回复）。

---

### Wave G0 — 前置验证（零代码）

| 步骤 | 命令 / 操作 | 预期 | 目的 |
|---|---|---|---|
| 1 | `grep -n "think(\|await think\|thinker" services/scheduler.py` | 确认 thinker 调用位点 | preflight 插在此之前 |
| 2 | `grep -n "should_call_semantic_gate" services/reply_workflow.py` | 确认现有 pre-gate 逻辑 | 避免重复 |
| 3 | 统计最近 100 条群消息中纯标点/单字/单 emoji 占比 | 评估 preflight 节省量 | 确认值得做 |

**Wave G0 回填（2026-05-27）**：

- **G0.1 实证确认**：
  - scheduler 真实触发点是 `services/scheduler.py::_do_chat()`，但 thinker 调用本身发生在 `services/llm/client.py::chat()`
  - `services/reply_workflow.py::should_call_semantic_gate` 已存在，但语义是“上一轮 bot 回复承接判断”，不是本轮“低信号免打 LLM”
- **实现口径修正**：
  - 执行单写“插在 scheduler 调 thinker 前”
  - 本轮改为插在 `LLMClient.chat()` 最前段、组装 conversation_text 之后，这样：
    - 不重复复制 trigger / reply-to-bot 判定
    - 可统一覆盖 at / directed_followup / probability 等所有真实入口
  - “最近 100 条统计占比”本轮未做离线日志挖掘，改以规则模块 + targeted tests 先落地；后续若要做节省量评估，可另接 runtime metric

---

### Wave G1 — text_preflight 模块

| 编号 | 一句话 | 关键文件 | 详细指导 |
|---|---|---|---|
| **G1.1** | 新建 `services/text_preflight.py` | 新文件 ~100-150 行 | 见下方规格 |
| **G1.2** | 在 scheduler thinker 调用前插入 | `services/scheduler.py` +~8 行 | preflight 短路 |
| **G1.3** | config 段 | `kernel/config.py` | `TextPreflightConfig` |
| **G1.4** | 单元测试 | `tests/test_text_preflight.py` | 覆盖 8 个场景 |

**G1.1 详细规格**：

```python
# services/text_preflight.py
"""
零 LLM 调用的文本预检。在 thinker 之前短路低信号消息。

低信号模式（命中任一即短路）：
1. punctuation_only：全部由标点符号组成（中英文标点 + 空格）
   - 示例："。" "？？？" "..." "！！" "~"
2. single_emoji：单个 emoji 或颜文字（无其他文本）
   - 示例："😂" "🤔" "(╯°□°)╯"
3. single_char：单个汉字/字母（无上下文意义）
   - 示例："嗯" "哦" "啊" "1"
   - 例外：如果该字符是对 bot 上一条消息的直接回复 → 不短路（可能是确认）
4. repetition_only：同一字符重复 3 次以上
   - 示例："哈哈哈哈哈" "啊啊啊啊" "666666"
   - 例外："哈哈哈" 可能是对 bot 的回应 → 不短路（由 reply_workflow 处理）

不短路的情况（即使看起来低信号）：
- 消息是对 bot 的 reply（event.reply 指向 bot 消息）
- 消息含 @bot
- 消息在 at_only 群中（已有其他 gate 处理）
"""

@dataclass
class PreflightResult:
    should_skip: bool
    reason: str  # "punctuation_only" / "single_emoji" / "single_char" / "repetition" / ""
    density: float  # 信息密度 0-1（用于 metric）

def preflight(
    text: str,
    *,
    is_reply_to_bot: bool = False,
    is_at_bot: bool = False,
) -> PreflightResult:
    """
    判定消息是否为低信号。

    返回 should_skip=True 时，调用方应跳过 thinker 调用。
    """
```

**G1.2 插入位点**：

在 `services/scheduler.py` 调用 `think()` 之前：

```python
# 伪代码
if text_preflight_enabled:
    preflight_result = preflight(
        text=plain_text,
        is_reply_to_bot=is_reply_to_bot,
        is_at_bot=is_at_bot,
    )
    if preflight_result.should_skip:
        logger.debug("text_preflight | skipped | reason={}", preflight_result.reason)
        return  # 不调 thinker、不回复
```

**G1.3 config**：

```python
class TextPreflightConfig(BaseModel):
    enabled: bool = False
    skip_punctuation_only: bool = True
    skip_single_emoji: bool = True
    skip_single_char: bool = True
    skip_repetition: bool = True
    min_repetition_count: int = 3  # 重复 N 次以上才算
    bypass_on_reply_to_bot: bool = True  # reply bot 时不短路
    bypass_on_at_bot: bool = True  # @bot 时不短路
```

**G1.4 测试场景**：

| 输入 | is_reply_to_bot | is_at_bot | 预期 |
|---|---|---|---|
| "。" | False | False | skip, reason="punctuation_only" |
| "？？？" | False | False | skip, reason="punctuation_only" |
| "😂" | False | False | skip, reason="single_emoji" |
| "嗯" | False | False | skip, reason="single_char" |
| "嗯" | True | False | pass（reply to bot，不短路） |
| "哈哈哈哈哈" | False | False | skip, reason="repetition" |
| "今天天气好" | False | False | pass（正常文本） |
| "。" | False | True | pass（@bot，不短路） |

**Wave G1 回填（2026-05-27）**：

- **G1.1 已落地**：新增 `services/text_preflight.py`
  - `PreflightResult`
  - `preflight(...)`
  - 规则覆盖：
    - punctuation_only
    - single_emoji
    - single_char
    - repetition
    - reply/@ bypass
- **G1.2 已接到真实 thinker 前置位点**：`services/llm/client.py::chat()`
  - 在 `conversation_text` 构建后立即执行
  - 命中时直接 `return None`，不调 thinker、不进主 LLM
- **G1.3 config 已补齐**：
  - `kernel/config.py::TextPreflightConfig`
  - 默认 OFF，所有 skip 子开关默认按执行单建议值开启
- **G1.4 测试已补齐**：
  - 新增 `tests/test_text_preflight.py`
  - 覆盖标点、emoji、单字、重复、reply bypass、@ bypass、正常文本
- **验证结果**：
  - `source ./scripts/dev/env.sh && uv run pytest tests/test_text_preflight.py tests/test_humanization_config.py -q`
    - 已包含在联合验证中，当前为 green

---

### Wave G2 — 集成测试 + PR

| 编号 | 一句话 | 关键文件 |
|---|---|---|
| **G2.1** | 全量 pytest + ruff + pyright | — |
| **G2.2** | PR 合并 | commit: `feat(pipeline): P1 dispatch G — text preflight (F2)` |

**Wave G2 回填（2026-05-27）**：

- **G2.1 本簇验证已完成**：
  - `source ./scripts/dev/env.sh && uv run pytest tests/test_text_preflight.py tests/test_humanization_config.py tests/test_chat_plugin_humanization_wire.py -q`
    - 已包含在 F4 联合验证中，当前为 green
  - `source ./scripts/dev/env.sh && uv run ruff check services/text_preflight.py kernel/config.py tests/test_text_preflight.py`
    - 已包含在联合 lint 中，当前为 green
  - `source ./scripts/dev/env.sh && uv run pyright services/text_preflight.py kernel/config.py tests/test_text_preflight.py`
    - 已包含在联合 type gate 中，当前为 green
- **G2.2 结论**：
  - G 簇已形成 `chat()` 前置零 LLM 低信号短路
  - 默认 OFF，不影响现网
  - kill-switch：
    - `text_preflight.enabled=false`

---

### G 簇回滚预案

```json
"text_preflight": { "enabled": false }
```

```bash
docker compose restart bot
```

---

## 状态表

| 簇 | Wave | 编号 | 内容 | 状态 |
|---|---|---|---|---|
| D | D0 | — | 前置验证 | ✅ |
| D | D1 | D1.1-D1.4 | F8 schedule overshare detector | ✅ |
| D | D2 | D2.1-D2.3 | F4 Layer 5 出口 stripper | ✅ |
| D | D3 | D3.1-D3.3 | F4 Layer 2 anchor reinjection | ✅ |
| D | D4 | D4.1-D4.4 | F4 Layer 3 drift detector | ✅ |
| D | D5 | D5.1-D5.2 | F4 Layer 1 compiler validator | ✅ |
| D | D6 | D6.1-D6.3 | D 簇集成测试 + PR | ✅ |
| E | E0 | — | 前置验证 | ✅ |
| E | E1 | E1.1-E1.3 | NameVariationRegistry 共骨架 | ✅ |
| E | E2 | E2.1-E2.4 | F12 upstream command filter | ✅ |
| E | E3 | E3.1-E3.4 | F11 addressee binding | ✅ |
| E | E4 | E4.1-E4.3 | F14 mention post-processor | ✅ |
| E | E5 | E5.1-E5.3 | E 簇集成测试 + PR | ✅ |
| F | F0 | — | 前置验证 | ✅ |
| F | F1 | F1.1-F1.4 | F5 TianAPI client + speculative executor | ✅ |
| F | F2 | F2.1-F2.3 | F5 thinker 扩展 + 4 级降级 | ✅ |
| F | F3 | F3.1-F3.4 | F6 sticker placement | ✅ |
| F | F4 | F4.1-F4.3 | F 簇集成测试 + PR | ✅ |
| G | G0 | — | 前置验证 | ✅ |
| G | G1 | G1.1-G1.4 | F2 text preflight | ✅ |
| G | G2 | G2.1-G2.2 | G 簇集成测试 + PR | ✅ |

> 状态语义：✅ 完成并已验收 / 🟡 已落地待验收 / ⏳ 待执行 / ⏸ 阻塞中 / ❌ 证据未建立 / 🔥 生产故障

---

## 验收口径（整体收口标准）

4 个 PR 全部合并后，整体收口需满足：

1. 状态表所有行 ✅
2. `uv run pytest`（全量）+ `uv run ruff check` + `uv run pyright` 全绿
3. 4 个 PR 分别合并到 main
4. 灰度群 993065015 / 984198159 各 PR 观察 24-48 小时无异常
5. 所有新模块 config `enabled` 从 `false` 切为 `true` 后行为符合预期
6. `maintenance-log.md` 追加 P1 落地条目

---

## 回滚预案（整体）

**单簇回滚**：`git revert` 对应 PR 的 merge commit + `docker compose restart bot`

**全量回滚**（极端情况）：

```bash
git revert <G_merge> <F_merge> <E_merge> <D_merge>
docker compose restart bot
```

**旗标级 kill-switch**（保留代码但关闭行为）：

```json
{
  "persona_drift": { "enabled": false },
  "schedule_overshare": { "enabled": false },
  "upstream_command_filter": { "enabled": false },
  "addressee_hint": { "enabled": false },
  "mention_post_processor": { "enabled": false },
  "slang_lookup": { "enabled": false },
  "sticker_placement": { "enabled": false },
  "text_preflight": { "enabled": false }
}
```

每个模块独立开关，可逐个排查问题模块。
