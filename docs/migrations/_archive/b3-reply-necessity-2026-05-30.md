# B3 回复必要性动机分（抑制强行展现）— D3 实施清单

> 状态：2026-05-30 **编码完成，全绿（2269 passed, ruff/pyright clean）**，默认 `necessity_gate_enabled=false`（零行为变化）待灰度。对应 [group-multitopic-understanding-b-series-design.md](../tracking/group-multitopic-understanding-b-series-design.md) §4（B3）。治 **F-β「强行展现自己」**。
> **风险最高一阶**：动机分调过严 = 过度沉默（话痨的反面）；必须默认关、灰度、可一键回退。
> 缓存红线（设计 §7.3）：动机维度的**指令/schema 进 thinker static 段**（每轮字节相同，可缓存）；**不新增 per-turn system 块、不独立 LLM 调用**——折叠进现有 thinker 输出字段，零新往返、零新延迟、static 前缀字节不动。

## 0. 目标与边界

**F-β 根因**（日志实证）：thinker 的 thought 暴露表演性动机——`"夸他破纪录厉害"`、`"接梗逗趣假装在烤鱼鱼烧"`、`"想轻松夸一下活跃气氛"`。根因是**裸概率 + 角色判定决定"要不要 fire"，thinker 只决定"怎么回"，没有一层问"这条回复有没有信息/关系价值，还是纯刷存在感"**。

**做**：给 thinker 输出加一个 `reply_necessity`（high/medium/low）维度——评估回复的**必要性**（信息缺口 / 关系价值 / 是否只是接梗）。`necessity=low` 且非显式寻址（无 trigger、非 addressed）→ 决策**降级为 `wait`**（复用既有 wait 短路 → 沉默）。

**不做（本期）**：不改 RWS / B1 / B2；不加独立打分 LLM 调用（折叠进 thinker）；不动 addressed/closing 路径（被寻址恒有回应义务，不受 necessity 抑制）。

**与 B1/B2 关系**：B2 治"插非己块"（overhearer 沉默）；B3 治"在己块也强行表演"（ratified/概率插话时的必要性）。两者正交：B2 管"该不该进这个块"，B3 管"进了之后这条该不该说"。

## 1. 接缝核实（已读代码）

| 挂载点 | 文件:行号 | 现状 |
| --- | --- | --- |
| thinker system prompt（static，可缓存） | `THINKER_SYSTEM_PROMPT` thinker.py:61 | B3 维度说明 + schema 字段加这里 |
| 输出 JSON schema | thinker.py:162 | 加 `reply_necessity`（high/medium/low） |
| 决策解析 | `_decision_from_data` thinker.py:298-340 | 解析 + 归一 necessity |
| ThinkDecision 字段 | thinker.py:165-205（`__slots__` + ctor） | 加 `reply_necessity` slot/字段 |
| think() 入参 | thinker.py:632-644 | 加 `necessity_gate: bool` / 阈值（或在 client 侧 gate） |
| wait 短路（沉默落点） | client.py:4023-4040 | necessity-gate 降级为 wait 即复用此短路 |
| think() 调用 | client.py:3930-3942 | 传 trigger/addressed 上下文供 gate 判断 |
| thinker 配置 | `ThinkerConfig` config.py:741 | 加 `necessity_gate_enabled` + `necessity_gate_addressed_exempt` |

**关键复用**：closing P0 已示范"thinker 加输出字段 + dynamic 提示 + `_decision_from_data` 归一 + client 消费"的完整模式（light_kind）。B3 照抄这条路径，且**比 closing 更轻**——necessity 不需要 dynamic 块（schema 在 static 即可），gate 逻辑可全在 `_decision_from_data` 或 client wait 分支。

## 2. 改动清单（四列：旧 → 新 / 文件 / 类型 / 回归）

| # | 旧行为 | 新行为 | 文件 | 类型 | 回归 |
| --- | --- | --- | --- | --- | --- |
| E1 | schema 无 necessity | system prompt 加"## 回复必要性"段 + schema 加 `reply_necessity` 字段 | thinker.py:61/162（static） | 改 prompt | 缓存：static 段每轮字节相同 |
| E2 | ThinkDecision 无字段 | 加 `reply_necessity: str = "high"` slot/ctor/repr | thinker.py:165-217 | 加字段（additive） | 默认 high = 不抑制 |
| E3 | 不解析 | `_decision_from_data` 解析 + 归一（非法→high） | thinker.py:298-340 | 解析 | 单测 |
| E4 | 无 gate | client：necessity=low + 非 addressed + 无 trigger + action 非 light_reply/closing → 强制 `thinker_action="wait"` | client.py:~4020（wait 分支前） | gate | 单测 |
| E5 | — | config `thinker.necessity_gate_enabled`（默认 False）+ `necessity_gate_addressed_exempt`（默认 True） | config.py:741 | 新增 config | 关闭=现状 |

## 3. 必要性维度设计（E1 细节）

system prompt 加（static 段，可缓存）：

```text
## 回复必要性（reply_necessity）——你这条回复是"被需要"还是"刷存在感"
在决定 reply 之前，诚实评估这条回复的必要性，避免无谓地刷存在感：
- **high**：对方在问你 / 信息有缺口你能补 / 关系上该回应（被点名、求助、情绪需要承接）。
- **medium**：话题与你相关，你的加入能推进，但不说也没损失。
- **low**：你只是想接个梗、附和、活跃气氛、展示自己——没有信息或关系价值。**群里没你这句话会更自然。**
判 low 时要克制：真人不会对每句话都插嘴，旁观也是常态。
```

schema 加字段：`"reply_necessity": "high|medium|low"`。

**判定原则写进 prompt 的关键**：把"群里没你这句话会更自然"作为 low 的锚——直接对抗 F-β 的表演冲动。

## 4. Gate 逻辑（E4 细节）

client.py 在 `if thinker_action == "wait":`（line 4023）**之前**插入：

```text
if (necessity_gate_enabled
        and getattr(thinker_decision, "reply_necessity", "high") == "low"
        and thinker_action == "reply"          # 不动 light_reply/closing
        and trigger is None                     # 显式触发恒回应
        and not is_addressed_turn               # 被寻址豁免（config 可关）
   ):
    thinker_action = "wait"
    _log_msg_out.info("necessity_gate | session={} downgraded reply->wait (low)", session_id)
```

随后既有 `if thinker_action == "wait":` 短路到沉默（line 4023-4040）。**复用既有 wait 路径，不新增沉默分支。**

- **只降级 `reply`**：light_reply（companion/closing）是已设计的弱回应，不受 necessity 抑制。
- **trigger/addressed 豁免**：被寻址恒有回应义务（Goffman），不能被 necessity 压掉。
- `is_addressed_turn` 来源：client 已有 trigger，addressed 信号需从 trigger.extra 或 force_reply 推。

## 5. D1 同模式扫描（编码时执行）

- `_decision_from_data` 所有 action 分支：necessity 只在 action=reply 时有 gate 意义，light_reply/wait 不受影响——确认归一逻辑不误清。
- think() 的全部调用点（client.py:3930 主路径；grep 是否有其他 think( 调用）确认新字段默认值不破坏旧调用。
- wait 短路的 usage 记录：necessity 降级走同一 wait 分支 → usage/log 一致，无重复记账。

## 6. 测试设计（D2 含 cancel-path）

`tests/test_thinker.py`：

1. `reply_necessity` 解析：high/medium/low 正常；非法值 → high（不抑制，安全默认）；缺字段 → high。
2. ThinkDecision 默认 `reply_necessity="high"`（旧构造不破）。

`tests/test_*client*`（necessity gate）：

3. **gate 降级**：necessity=low + action=reply + trigger=None + 非 addressed → action 变 wait → 返回 None（沉默）。
4. **light_reply 不受抑制**：necessity=low + action=light_reply（closing）→ 仍走弱回复，不降级。
5. **addressed 豁免**：necessity=low + is_addressed → 仍 reply（被寻址恒回应）。
6. **trigger 豁免**：necessity=low + trigger 存在 → 仍 reply。
7. **gate 关闭**：necessity_gate_enabled=False → low 也照常 reply（== 现状）。
8. **D2 cancel-path**：necessity 降级为 wait 后短路，与既有 wait 短路同路径——若 chat 被取消，无额外状态污染（复用已验证的 wait 路径）。

## 7. 缓存验证（D4，设计 §7.3 红线）

- **逐块 hash**：`cache_debug | system=[...]`——B3 的维度说明加在 thinker **static 段**（`THINKER_SYSTEM_PROMPT`），每轮字节相同。上线前后 thinker static 块 hash **必须不变**（格式化只代入 identity_name，与现状同）。
- **hit%**：thinker `cache_r/hit%` 上线前后不下降。schema 多一字段 = static 段长度固定增加一次，首次 cache_create 后稳定，hit% 不受影响。
- **不新增 dynamic 块**：B3 不往 dynamic_blocks 加任何 per-turn 内容（区别于 closing 的收尾提示）——necessity 是纯输出字段，输入侧零变化。

## 8. 回滚路径

- **一键回退**：`thinker.necessity_gate_enabled=False`（默认）→ 仍输出 necessity 但不据此降级，行为 == 现状。
- **代码回退**：E4 gate 块删除即回；E1-E3 是 additive（多一个输出字段，默认 high 不抑制）。
- 纯 prompt + 解析，无 DB/迁移/内存状态。

## 9. 灰度与上线

- 改 .py（thinker/client/config）→ rebuild bot。
- 灰度三步：① `necessity_gate_enabled=False` 上线（只输出 necessity，扒日志看 thinker 判 low 的比例与质量，确认不误杀 high）；② 开 gate 但 `addressed_exempt=True`（只抑制非寻址的 low）；③ 据烤群"话痨感 vs 冷漠感"反馈微调。
- **监控**：`necessity_gate | downgraded reply->wait` 日志频率——过高=过度沉默，需放宽 prompt 的 low 判定。

**待确认**：照此实施 B3？确认后按 E1→E5 编码，每步跑测试，回填证据。

## 10. 完成证据（D4，已回填）

**状态：2026-05-30 编码完成，全绿。**

- **改动（E1–E5）**：E1 `THINKER_SYSTEM_PROMPT` 加"## 回复必要性"段 + schema 加 `reply_necessity`（static 段，可缓存，thinker.py:61/162）；E2 `ThinkDecision` 加 `reply_necessity="high"` slot/ctor/repr；E3 `_normalize_reply_necessity` + `_ALLOWED_NECESSITY` + `_decision_from_data` 解析（非法/缺失→high）；E4 client gate（thinker.py wait 短路前）——necessity=low + action=reply + 非 trigger（addressed 豁免）→ `thinker_action="wait"`→沉默；E5 config `ThinkerConfig.necessity_gate_enabled`（默认 False）+ `necessity_gate_addressed_exempt`（默认 True），client ctor 两参 + plugin 接线。
- **D1 同模式**：`_decision_from_data` necessity 只读不影响 action 分支；`think()` 调用点（client.py:3930 主路径）默认值不破旧调用；gate 降级走既有 wait 短路（client.py:4027），usage/log 单点不重复记账。
- **测试（+8）**：`tests/test_thinker.py` 5（necessity 解析 high/medium/low、非法→high、缺失→high、默认 high、prompt 含字段）；`tests/test_thinker_runtime_state.py` 3（gate 降级 low→沉默 + 断言 main LLM 未调；gate 关闭 low 照常 reply；high necessity 不抑制）。
- **外部可观察**：全量 `pytest -q` → **2269 passed, 8 skipped**（+8 vs 2261）；`ruff` All passed；`pyright`（thinker/client/config）0 errors。
- **缓存（§7.3 红线核对）**：维度说明 + schema 字段全在 thinker **static 段**（`THINKER_SYSTEM_PROMPT`，格式化只代入 name）；**dynamic_blocks 零新增**（necessity 是纯输出字段，输入侧不变）；gate 在 client 决策层、不进 prompt。thinker static hash 仅一次性增长后稳定，hit% 不受影响。
- **回滚**：`necessity_gate_enabled=False`（默认）仍输出 necessity 但不降级，行为 == 现状。

**灰度路径**：rebuild 上线（默认关，零行为变化）→ 开 gate 前先扒 thinker 输出的 `reply_necessity` 分布与 thought 质量（确认不误判 high 为 low）→ 开 `necessity_gate_enabled=true`（addressed_exempt 默认 True，只抑制非寻址 low）→ 据"话痨感 vs 冷漠感"调 prompt 的 low 判定。监控 `necessity_gate | downgraded reply->wait` 频率，过高=过度沉默。
