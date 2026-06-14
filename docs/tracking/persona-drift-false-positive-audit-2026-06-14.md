# persona_drift 误伤/漏拦审计 — 2026-06-14

**状态**：审计完成，**未改代码**。观察项① 闭环为「发现确定性缺陷，待立项修复」。

## 背景

E/F 簇全开后观察项① 要验「persona_drift 不误伤自我介绍」。persona_drift 是确定性正则（`services/llm/persona_drift_stripper.py` + `services/llm/persona_patterns.py` 的 `DECLARATION_PATTERNS`），所以静态喂样本比靠群聊随机采样更彻底。运行态实测了 bot_name（`identity.name=凤笑梦`，persona=`fengxiaomeng-v2`；注意不是 napcat 昵称「emu不吃小杯面」），用真名跑矩阵。

## 两套机制

1. **drift_detector**（`drift_detector.py`）：对 **emu 自己的回复** 打 EWMA 分。分层：`DECLARATION_PATTERNS`→0.72；`我是/叫{name}`→0.74；`{name}`+成员/设定/角色→0.76；AI 标记（我是ai/语言模型/claude/gpt）→0.92。theta_repair=0.6→repair，theta_block=0.85→block。
2. **persona_drift sentinel**（`persona_drift_stripper.py`，guardrail rule，action=rewrite）：剥声明性句子。`_matches_declaration` 命中即记 hit；`_rewrite_sentence` 实际剥除。

## 缺陷矩阵（bot_name=凤笑梦，runtime 真实值）

| 输入回复 | hit | 改写 | passed | 输出 | 判定 |
|---|---|---|---|---|---|
| `我叫凤笑梦，很高兴认识你～` | ✓ | ✓ | T | `很高兴认识你～` | **误伤**：名字被抹 |
| `我是凤笑梦呀` | ✓ | ✓ | T | `呀` | **误伤**：几乎全删 |
| `我是凤笑梦，WxS的一员` | ✓ | ✗ | T | 原样 | 误标 hit（透传） |
| `我是个吃货` / `我是来帮忙的` | ✓ | ✗ | T | 原样 | 误标 hit（普通"我是X"） |
| `我是一个AI` | ✓ | ✗ | T | **原样透传** | **漏拦**：最该拦的放行 |
| `我是凤笑梦，我是一个AI` | ✓ | ✓ | T | `我是一个AI` | **漏拦+误伤**：删真名留 AI 声明 |
| `我的人设是温柔` | ✓ | ✗ | T | 原样 | **漏拦**：设定泄露透传 |
| `我是说你别多想` | ✗ | ✗ | T | 原样 | ok（豁免正确） |

## 三个根因

1. **误伤自我介绍**：合法自报真名（`我叫凤笑梦…`/`我是凤笑梦呀`）命中 `DECLARATION_PATTERNS[0]` 的 `我(?:就)?是(?!说\b).{1,8}` 与 name_pattern，被当漂移剥除。被问"你叫什么"时正常作答会被抹名。
2. **漏拦纯 AI 声明**：`strip_declarations` 末尾 `if matched and not cleaned: return text, matched`——当声明剥完整句为空（如 `我是一个AI` 单句），为避免返回空串而**原样放行**，导致最该拦的内容透传（`passed=True`）。只有声明后还跟别的话时才正确剥除。
3. **剥错方向**：`我是凤笑梦，我是一个AI` 被剥成 `我是一个AI`——删掉了合法真名，保留了违规 AI 声明，与目标完全相反。

## 实测缓解：light_reply 短路

运行态压测（984198159，让 emu 自我介绍）实测：emu 走 **light_reply greeting 短路径**（thinker `action=light_reply kind=greeting`，直接 emit token `我叫凤笑梦，很高兴认识你～`），**完全绕过 `_apply_visible_reply_guardrails`**，persona_drift stripper 没跑，所以日常打招呼式自我介绍**天然不被误伤**。误伤只在自我介绍走主 LLM 路径（多轮上下文、非纯打招呼触发）时发生。这解释了为何线上 `persona_drift_hits` 长期≈0：高频自我介绍场景恰好走了不过 guardrail 的短路。

## 建议修复路径（未实施）

1. **保护自报真名**：`_matches_declaration` 对"仅自报 name 无 AI/设定标记"的句子豁免（类似 `我是说` 豁免），或 `_rewrite_sentence` 对 name-only 声明不剥（自报名字是合法人设行为，非漂移）。
2. **修漏拦**：`if matched and not cleaned` 分支不应原样返回违规原文——纯 AI/设定声明剥空时应返回 guardrail fallback（让上层 `passed=False` 走「我重新整理一下再接」），而非透传。
3. **修剥除方向**：混合句应优先保留 name、剥除 AI/设定片段，而非按出现顺序贪婪剥首句。
4. 加回归测试覆盖上述 8 个样本（D2）。

## 风险评估

当前**误伤实际影响低**（light_reply 短路兜底了高频场景），但**漏拦风险中**（`我是一个AI` 单句透传——若 emu 在主路径吐出纯 AI 声明会原样发群）。修复优先级：漏拦 > 剥错方向 > 误伤。
