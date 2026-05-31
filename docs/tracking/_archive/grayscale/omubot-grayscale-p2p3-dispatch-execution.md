# P2/P3 派单——推荐方案执行追踪

> 状态：2026-05-27 待启动。本文是 [P2/P3 决策文档](omubot-grayscale-p2p3-decision-pending.md) 的执行版。
>
> 前置：P0 五件已部署；P1 八件执行中。P2/P3 复用 P0 A 簇 sentinel_registry + P1 D 簇 overshare detector 骨架。
>
> 约束：**不允许修改人设文件**（source.md / instruction.md）。所有方案纯运行时 / 代码层。
>
> 执行原则：
> 1. **按簇分 PR**——同簇共骨架的 issue 合 1 个 PR；跨簇独立 PR。
> 2. **每条 Wave 自带 D1 grep 证据 + D2 cancel-path 测试 + 回滚开关**。
> 3. **默认 OFF**——所有新模块 config 段 `enabled: false`，灰度群先开观察。
> 4. **Issue 17 已独立立项**——不在本文范围，详见 [issue17 文档](omubot-grayscale-issue17-at-burst-humanization.md)。

---

## 簇划分

| 簇 | PR | 包含 Issue | 共骨架 | 预估行数 |
|---|---|---|---|---|
| H（plugin humanizer 修正） | 1 个 | F15 | humanizer runtime 参数补齐 | ~60-90 |
| I（self-mute 生命周期） | 1 个 | F16 | scheduler mute gate + admin SPA + reconcile | ~100-180 |
| J（符号监测） | 1 个 | F9 | sentinel_registry warn 规则 | ~15-25 |

执行顺序：H → I → J（H 簇最小且是纯 bug fix；I 簇涉及 admin SPA 需 D6 流程；J 簇 5-10 行可随时挂载）。

---

## 派单 H — plugin humanizer 修正（F15）

> 共骨架：复用 `services/scheduler.py:493-498` 的 `_humanizer_runtime(group_id)` 模式。
>
> 核心思路：echo plugin 和 element_detector plugin 调用 `humanizer.delay()` 时缺少 runtime 参数（mood/slot/register），且 echo plugin 传入的文本含长 marker 导致 delay 过长。修正输入 + 补齐参数。

### 前置知识（执行者必读）

**humanizer.delay() 正确调用模式**：

```python
# services/scheduler.py:696-697 — 这是正确的调用方式
if self._humanizer is not None and humanize != "skip":
    await self._humanizer.delay(text, **self._humanizer_runtime(group_id))
```

`_humanizer_runtime(group_id)` 返回：

```python
{
    "group_id": group_id,
    "register": self._current_register(group_id),   # 当前语域标签
    "slot": self._current_slot_payload(group_id),    # 当前时段信息
    "mood": self._get_current_mood(group_id),        # 当前心情 profile
}
```

**humanizer.delay() 的 runtime_multiplier 逻辑**（`services/humanizer.py:82-96`）：

- `register="playful"` → ×0.7（活泼时回复更快）
- `register="quiet"` + low energy → ×1.5（安静时回复更慢）
- mood factor 额外调节（`services/humanizer.py:127-134`）

**缺少 runtime 参数时的行为**：所有参数默认 None → `_runtime_multiplier` 返回 1.0 → delay 只由 `base + char_delay × len(text)` 决定。对 echo plugin 来说，`echo_key` 含 `[image:sub:hash]`（~40 字符）等 marker，`char_delay=0.02` × 40 = 额外 0.8s，多个 marker 叠加后 delay 远超正常回复。

**受影响的 plugin 列表**（D1 grep 锁结果）：

| 文件 | 行号 | 问题 |
|---|---|---|
| `plugins/echo/plugin.py` | 190 | 传 `echo_reply`（打断分支），无 runtime 参数 |
| `plugins/echo/plugin.py` | 194 | 传 `echo_key`（含长 marker），无 runtime 参数 |
| `plugins/element_detector/plugin.py` | 143 | 传 `reply_text`，无 runtime 参数 |
| `plugins/element_detector/plugin.py` | 154 | 传 `reply_text`，无 runtime 参数 |

`plugins/chat/plugin.py:380,391` 是 debug 路径，不影响正常用户体验，本轮不修。

---

### Wave H0 — 前置验证（零代码）

| 步骤 | 命令 / 操作 | 预期 | 目的 |
|---|---|---|---|
| 1 | `grep -n "_humanizer_runtime" services/scheduler.py` | 确认 helper 签名和返回值 | 确定 plugin 内复刻还是抽公共 |
| 2 | `grep -n "self._humanizer\|ctx.humanizer\|ctx.scheduler" plugins/echo/plugin.py` | 确认 echo plugin 能访问 scheduler 实例 | 判断能否直接调 `scheduler._humanizer_runtime` |
| 3 | `grep -n "self._humanizer\|ctx.humanizer\|ctx.scheduler" plugins/element_detector/plugin.py` | 同上 | 同上 |
| 4 | `grep -rn "class MessageContext\|class.*Context" plugins/` &#124; `head -10` | 确认 plugin context 结构 | 判断 runtime 参数获取路径 |

**关键判断**：如果 plugin context 已持有 `scheduler` 引用，直接调 `ctx.scheduler._humanizer_runtime(group_id)` 最简单（0 新代码）。否则需要在 plugin 内复刻一份轻量 helper 或抽到 `services/humanizer.py`。

---

### Wave H1 — echo plugin 输入修正 + runtime 参数补齐

| 编号 | 一句话 | 关键文件 | 详细指导 |
|---|---|---|---|
| **H1.1** | 新建 `_visible_text_for_humanizer()` helper | `plugins/echo/plugin.py` 内新增 ~20 行 | 见下方规格 |
| **H1.2** | 修正 delay 调用 + 补 runtime 参数 | `plugins/echo/plugin.py:190,194` | 两处调用点 |
| **H1.3** | 单元测试 | `tests/test_echo_humanizer_input.py` | 覆盖 4 个场景 |

**H1.1 详细规格**：

```python
# plugins/echo/plugin.py — 新增 module-level helper

import re

_MARKER_RE = re.compile(
    r"\[(?:image|face|json|mface|record|video|forward):[^\]]*\]"
)
_AT_RE = re.compile(r"\[at:(\d+)\]")
_MARKER_VISUAL_LEN = 2  # 每个 marker 视觉等价字符数

def _visible_text_for_humanizer(echo_key: str) -> str:
    """
    从 echo_key 还原"可见字符长度"的等价文本。

    规则：
    - [image:sub:hash] / [face:id] / [json:...] → 替换为 2 个占位字符
    - [at:qq] → 保留（at 目标在群里可见）
    - 其余纯文本保留原样

    返回的文本仅用于 humanizer.delay() 计算 typing_extra，
    不用于实际发送。
    """
    result = _MARKER_RE.sub("__", echo_key)
    return result
```

**H1.2 修改**：

```python
# 修改前（line 189-195）：
if echo_reply.startswith("打断"):
    await self._humanizer.delay(echo_reply)
    await ctx.bot.send_group_msg(...)
else:
    segments = ctx.raw_message.get("segments")
    await self._humanizer.delay(echo_key)
    await ctx.bot.send_group_msg(...)

# 修改后：
if echo_reply.startswith("打断"):
    visible = _visible_text_for_humanizer(echo_reply)
    await self._humanizer.delay(visible, **self._get_humanizer_runtime(group_id))
    await ctx.bot.send_group_msg(...)
else:
    segments = ctx.raw_message.get("segments")
    visible = _visible_text_for_humanizer(echo_key)
    await self._humanizer.delay(visible, **self._get_humanizer_runtime(group_id))
    await ctx.bot.send_group_msg(...)
```

`_get_humanizer_runtime(group_id)` 的实现取决于 H0 判断结果：

- **方案 A**（推荐，如果 ctx 有 scheduler）：`return self._scheduler._humanizer_runtime(group_id)`
- **方案 B**（如果 scheduler 方法是 private 不宜外部调用）：在 plugin 内复刻：

```python
def _get_humanizer_runtime(self, group_id: str) -> dict:
    return {
        "group_id": group_id,
        "register": None,   # echo 没有独立语域，用 None 走默认 ×1.0
        "slot": None,       # 同上
        "mood": self._scheduler._get_current_mood(group_id) if hasattr(self._scheduler, "_get_current_mood") else None,
        "thinking_elapsed_s": 0,  # echo 没有 thinker 阶段
    }
```

**H1.3 测试场景**：

| 场景 | echo_key 输入 | 预期 visible_text | 预期 delay 行为 |
|---|---|---|---|
| 纯文本 | "哈哈哈好搞笑" | "哈哈哈好搞笑"（7 字符） | base + 7×0.02 |
| 含 image marker | "看这个[image:sub:abc123def]" | "看这个__"（5 字符） | base + 5×0.02 |
| 多 marker | "[face:178][image:x:y]好的" | "____好的"（6 字符） | base + 6×0.02 |
| 打断分支 | "打断复读！" | "打断复读！"（5 字符） | base + 5×0.02 + runtime mult |

**H1 回滚**：`git restore plugins/echo/plugin.py && rm -f tests/test_echo_humanizer_input.py`

---

### Wave H2 — element_detector plugin runtime 参数补齐

| 编号 | 一句话 | 关键文件 | 详细指导 |
|---|---|---|---|
| **H2.1** | 补 runtime 参数到 delay 调用 | `plugins/element_detector/plugin.py:143,154` | 同 H1.2 模式 |
| **H2.2** | 单元测试 | 扩展 `tests/test_echo_humanizer_input.py` 或新建 | 覆盖 2 个场景 |

**H2.1 修改**：

element_detector 的 `reply_text` 是 LLM 生成的纯文本（无 marker），所以不需要 `_visible_text_for_humanizer()`。只需补 runtime 参数：

```python
# 修改前（line 143）：
await self._humanizer.delay(reply_text)

# 修改后：
await self._humanizer.delay(reply_text, **self._get_humanizer_runtime(group_id))
```

`_get_humanizer_runtime` 同 H1.2 的方案 A 或 B。

**H2.2 测试场景**：

| 场景 | 预期 |
|---|---|
| 正常 reply_text + mood=playful | delay 乘 0.7（更快） |
| 正常 reply_text + mood=None | delay 乘 1.0（默认） |

**H2 回滚**：`git restore plugins/element_detector/plugin.py`

---

### Wave H3 — 集成验证

| 步骤 | 命令 | 预期 |
|---|---|---|
| 1 | `grep -rn "humanizer.delay\|humanizer\.delay" plugins/ services/` | 所有 plugin 调用点都有 runtime 参数（debug 路径除外） |
| 2 | `uv run pytest tests/test_echo_humanizer_input.py -v` | 全部通过 |
| 3 | `uv run ruff check plugins/echo/plugin.py plugins/element_detector/plugin.py` | 无 lint 错误 |
| 4 | `uv run pyright plugins/echo/plugin.py plugins/element_detector/plugin.py` | 0 errors |

**Wave H0 回填（2026-05-27）**：

- D1 实证结果：
  - `services/scheduler.py:493-499` 确认 `_humanizer_runtime(group_id)` 真实存在，且返回 `group_id / register / slot / mood`
  - `plugins/echo/plugin.py:151-152`、`plugins/element_detector/plugin.py:94-95` 均已在 `on_startup()` 持有 `ctx.scheduler`
  - `kernel/types.py:154-205` 的 `PluginContext` 真实含 `scheduler` / `humanizer`，无需改 context 结构
- 关键判断：本簇采用“plugin 内薄封装 `_get_humanizer_runtime()` → 委托 `scheduler._humanizer_runtime(group_id)`”的实现，不新增公共 helper，不改 scheduler API。
- 旧稿修正：
  - 旧稿提到 `plugins/chat/plugin.py:380,391` debug 路径不修，本轮 grep 复核后仍成立；H 簇只碰 `echo` / `element_detector`
  - `grep -rn "humanizer.delay\\|humanizer\\.delay" plugins/ services/` 现状表明 `services/send_queue.py` 仍有无 runtime 的 send_queue 路径，但它不在 F15 plugin humanizer 修正范围，本簇不扩圈

**Wave H1 回填（2026-05-27）**：

- `plugins/echo/plugin.py` 已落地：
  - 新增 module-level `_MARKER_RE` + `_visible_text_for_humanizer()`
  - `[image|face|json|mface|record|video|forward:...]` 统一折算为 `"__"`，纯文本保持原样
  - 两处 `self._humanizer.delay(...)` 改为传 `visible_text + **self._get_humanizer_runtime(group_id)`
- 实现口径修正：
  - 执行单里单列了 `_AT_RE`，但当前仓 `build_echo_key()` 本来就把 `[at:qq]` 视为普通可见 marker 文本，且 H1 测试目标是“修正超长 marker 对 typing_extra 的放大”；因此本轮未单独引入 `_AT_RE` 分支，保留现有 `[at:qq]` 可见长度
  - `_get_humanizer_runtime()` 做成 plugin 内薄封装，优先调 `scheduler._humanizer_runtime(group_id)`；只有极端场景下才 fallback 到最小 dict，避免把 private helper 直接散落到调用点
- 测试：
  - 新增 `tests/test_echo_humanizer_input.py`
  - 已覆盖：纯文本、单 image marker、多 marker、打断文案、echo plugin runtime 透传

**Wave H2 回填（2026-05-27）**：

- `plugins/element_detector/plugin.py` 已落地：
  - 新增同模式 `_get_humanizer_runtime()`
  - 两处 `delay(reply_text)` 均改为 `delay(reply_text, **runtime)`
- 测试：
  - `tests/test_echo_humanizer_input.py` 同文件补 element_detector 两条路径
  - 覆盖 template reply 和 LLM reply 两个发送分支；断言 runtime 参数确实透传到 humanizer
- 口径说明：
  - 执行单 H2.2 原文写“mood=playful delay 乘 0.7”，但当前仓最稳的专项测试锚点是“plugin 是否把 runtime 参数透传给 humanizer”，因为乘数逻辑已由 `tests/test_humanizer_register.py` 单独覆盖；本轮沿仓内测试分层做参数透传断言，避免重复测 Humanizer 自身

**Wave H3 回填（2026-05-27）**：

- 验证结果：
  - `grep -rn "humanizer.delay\\|humanizer\\.delay" plugins/ services/`
    - `plugins/echo/plugin.py:213,218`、`plugins/element_detector/plugin.py:158,169` 均已带 runtime 参数
    - 其余无 runtime 的点仅剩 `plugins/chat/plugin.py` debug 路径和 `services/send_queue.py`，均不在 F15 修复范围
  - `source ./scripts/dev/env.sh && uv run pytest tests/test_echo_humanizer_input.py tests/test_echo.py tests/test_element_detector.py -q`
    - `40 passed`
  - `source ./scripts/dev/env.sh && uv run ruff check plugins/echo/plugin.py plugins/element_detector/plugin.py tests/test_echo_humanizer_input.py`
    - `All checks passed`
  - `source ./scripts/dev/env.sh && uv run pyright plugins/echo/plugin.py plugins/element_detector/plugin.py tests/test_echo_humanizer_input.py`
    - `0 errors, 0 warnings`
- H 簇结论：
  - F15 的两层根因已同时修正：echo 输入从原始 marker-heavy key 改为 visible text；echo / element_detector 都补齐 runtime 参数
  - kill-switch 不涉及新 config；回滚仍按文档 `git restore plugins/echo/plugin.py plugins/element_detector/plugin.py && rm -f tests/test_echo_humanizer_input.py`

---
<!-- PLACEHOLDER_SECTION_I -->

## 派单 I — self-mute 生命周期（F16）

> 共骨架：复用 `services/scheduler.py` 的 `_muted_groups` / `is_muted()` / `_handle_group_ban()` 已有基础设施。
>
> 核心思路：三段并行——① echo/element_detector/food plugin 加 mute gate（bug fix）；② admin SPA 增加 self-mute 状态可见卡片；③ scheduler 增加周期 reconcile + ActionFailed 反向标记。

### 前置知识（执行者必读）

**现有 mute 基础设施**：

```python
# services/scheduler.py — 已有
self._muted_groups: set[str] = set()          # line 124

def mute_group(self, group_id: str):          # line 139-140
    self._muted_groups.add(group_id)

def unmute_group(self, group_id: str):        # line 151-152
    self._muted_groups.discard(group_id)

def is_muted(self, group_id: str) -> bool:    # line 155-156
    return group_id in self._muted_groups
```

**当前 mute 触发来源**：

- `kernel/router.py` 的 `_handle_group_ban` 事件处理器（NapCat 推送 `group_ban` 事件时调用 `scheduler.mute_group()`）
- 启动时 `_poll_self_mute_status()` 主动查询一次

**缺口**：

1. plugin 直接调 `bot.send_group_msg()` 绕过 scheduler 的 mute 检查
2. 无周期 reconcile——如果 NapCat 漏推 `group_ban` 事件，bot 不知道自己被禁言
3. admin SPA 无法看到当前 mute 状态

**绕过 mute gate 的 plugin 列表**（D1 grep 锁结果）：

| 文件 | 行号 | 说明 |
|---|---|---|
| `plugins/echo/plugin.py` | 191, 195 | 复读回复 |
| `plugins/element_detector/plugin.py` | 144, 155 | 元素检测回复 |
| `plugins/food/plugin.py` | 442, 448 | 食物推荐回复 |

---

### Wave I0 — 前置验证（零代码）

| 步骤 | 命令 / 操作 | 预期 | 目的 |
|---|---|---|---|
| 1 | `grep -n "is_muted\|_muted_groups" services/scheduler.py` | 确认 API 签名 | plugin 调用方式 |
| 2 | `grep -rn "send_group_msg" plugins/ --include="*.py"` | 列出所有绕过 scheduler 的发送点 | 确认修复范围 |
| 3 | `grep -n "_handle_group_ban\|group_ban\|_poll_self_mute" kernel/router.py services/scheduler.py` | 确认现有 ban 事件处理 | reconcile 需要对齐的数据源 |
| 4 | `ls admin/routes/api/` | 确认 API 路由目录结构 | 新增 mute_state 端点的位置 |
| 5 | `grep -rn "get_group_member_info\|shut_up_timestamp" services/ kernel/` | 确认 NapCat API 调用模式 | reconcile loop 的查询方式 |

---

### Wave I1 — plugin mute gate（bug fix，必须做）

| 编号 | 一句话 | 关键文件 | 详细指导 |
|---|---|---|---|
| **I1.1** | echo plugin 加 mute 检查 | `plugins/echo/plugin.py:189` 前 | 见下方规格 |
| **I1.2** | element_detector plugin 加 mute 检查 | `plugins/element_detector/plugin.py:143` 前 | 同模式 |
| **I1.3** | food plugin 加 mute 检查 | `plugins/food/plugin.py:442` 前 | 同模式 |
| **I1.4** | 单元测试 | `tests/test_plugin_mute_gate.py` | 覆盖 3 个 plugin |

**I1.1 详细规格**：

在 `plugins/echo/plugin.py` 的 `on_message` 方法中，`send_group_msg` 调用前加 mute 检查：

```python
# 在 line 189 之前（即 "if echo_reply.startswith("打断"):" 之前）插入：
if self._scheduler.is_muted(group_id):
    _log.info("echo | group={} muted, skip send", group_id)
    return True  # 消息已被 echo 识别，但因禁言不发送
```

**注意**：返回 `True` 而非 `False`——echo 已经识别了复读，只是不发送。返回 `False` 会让消息继续流入 scheduler 触发 LLM 回复。

**I1.2/I1.3 同模式**：

```python
# element_detector — 在 send_group_msg 前
if self._scheduler.is_muted(group_id):
    _log.info("element_detector | group={} muted, skip send", group_id)
    return True

# food — 在 send_group_msg 前
if self._scheduler.is_muted(group_id):
    _log.info("food | group={} muted, skip send", group_id)
    return  # food plugin 的返回值语义不同，需确认
```

**I1.4 测试场景**：

| 场景 | 预期 |
|---|---|
| echo plugin + group muted | 不调用 send_group_msg，返回 True |
| echo plugin + group not muted | 正常发送 |
| element_detector + group muted | 不调用 send_group_msg |
| food + group muted | 不调用 send_group_msg |

**I1 回滚**：`git restore plugins/echo/plugin.py plugins/element_detector/plugin.py plugins/food/plugin.py`

---

### Wave I2 — 周期 reconcile + ActionFailed 反向标记

| 编号 | 一句话 | 关键文件 | 详细指导 |
|---|---|---|---|
| **I2.1** | 新增 `_reconcile_self_mute_loop` | `services/scheduler.py` 新增 ~50-80 行 | 见下方规格 |
| **I2.2** | ActionFailed 反向标记 | `services/scheduler.py:_send_to_group` 的 except 分支 | 见下方规格 |
| **I2.3** | config 段 | `kernel/config.py` 新增 `SelfMuteConfig` | 见下方规格 |
| **I2.4** | 单元测试 + cancel-path | `tests/test_self_mute_reconcile.py` | 覆盖 4 个场景 |

**I2.1 详细规格**：

```python
# services/scheduler.py — 新增方法

async def _reconcile_self_mute_loop(self) -> None:
    """
    每 reconcile_interval_seconds 秒查询一次所有活跃群的 bot 自身禁言状态。
    信任 server-side shut_up_timestamp 为准。
    """
    interval = self._config.self_mute.reconcile_interval_seconds  # 默认 300
    while True:
        await asyncio.sleep(interval)
        if not self._bot:
            continue
        for group_id in list(self._active_groups()):
            try:
                info = await self._bot.get_group_member_info(
                    group_id=int(group_id),
                    user_id=self._self_id,
                    no_cache=True,
                )
                shut_up_ts = info.get("shut_up_timestamp", 0)
                is_muted_server = shut_up_ts > time.time()
                is_muted_local = group_id in self._muted_groups

                if is_muted_server and not is_muted_local:
                    self._muted_groups.add(group_id)
                    _L.warning(
                        "reconcile | group={} server says muted (until {}), marking",
                        group_id, shut_up_ts,
                    )
                elif not is_muted_server and is_muted_local:
                    self._muted_groups.discard(group_id)
                    _L.info(
                        "reconcile | group={} server says unmuted, clearing",
                        group_id,
                    )
            except Exception:
                _L.debug("reconcile | group={} query failed, skip", group_id)
```

**启动注册**：在 scheduler 的 `start()` 或 `_on_connect()` 中：

```python
if self._config.self_mute.reconcile_enabled:
    self._reconcile_task = asyncio.create_task(self._reconcile_self_mute_loop())
```

**I2.2 ActionFailed 反向标记**：

在 `_send_to_group` 的 `except ActionFailed` 分支中：

```python
except ActionFailed as e:
    retcode = getattr(e, "retcode", None) or 0
    # retcode 1200/1300 系列通常表示禁言/权限不足
    if retcode in {1200, 1300} and self._config.self_mute.action_failed_reverse_mark:
        if group_id not in self._muted_groups:
            self._muted_groups.add(group_id)
            _L.warning(
                "scheduler | group={} ActionFailed retcode={} → reverse-mark muted",
                group_id, retcode,
            )
    # 现有重试逻辑继续...
```

**I2.3 config 段**：

```python
# kernel/config.py — 新增
class SelfMuteConfig(BaseModel):
    reconcile_enabled: bool = False  # 默认 OFF
    reconcile_interval_seconds: int = 300
    action_failed_reverse_mark: bool = False  # 默认 OFF
    action_failed_retcodes: list[int] = [1200, 1300]
```

**I2.4 测试场景**：

| 场景 | 预期 |
|---|---|
| server says muted + local not muted | reconcile 标记 mute |
| server says unmuted + local muted | reconcile 清除 mute |
| ActionFailed retcode=1200 | 反向标记 mute |
| cancel-path：reconcile loop 被 cancel | 无 dirty state，下次启动重新开始 |

**I2 回滚**：`git restore services/scheduler.py kernel/config.py`

---

### Wave I3 — admin SPA self-mute 状态可见

| 编号 | 一句话 | 关键文件 | 详细指导 |
|---|---|---|---|
| **I3.1** | 后端 API 端点 | `admin/routes/api/scheduler.py` 新增 `GET /api/scheduler/mute_state` | 见下方规格 |
| **I3.2** | 前端状态卡 | `admin/frontend/src/views/dashboard.vue` | 复用 MetricCard/AppCard |
| **I3.3** | 前端构建验证 | `cd admin/frontend && npm run build` | D6 流程 |

**I3.1 后端 API 规格**：

```python
# admin/routes/api/scheduler.py — 新增路由

@router.get("/api/scheduler/mute_state")
async def get_mute_state():
    """返回所有群的 bot 自身禁言状态。"""
    scheduler = get_scheduler()  # 获取 scheduler 单例
    result = {}
    for group_id in scheduler._muted_groups:
        result[group_id] = {
            "muted": True,
            "source": "event",  # 或 "reconcile" / "action_failed"
        }
    return {"groups": result}
```

**注意**：需要在 scheduler 中记录 mute 来源（event / reconcile / action_failed）。可在 `_muted_groups` 从 `set[str]` 升级为 `dict[str, MuteRecord]`，或保持 set 但另开一个 `_mute_sources: dict[str, str]`。

**I3.2 前端卡片**：

- 位置：dashboard.vue 顶部状态区
- 样式：复用 `AppCard.vue`，标题"Bot 禁言状态"
- 内容：列出被禁言的群 ID + 来源标签
- 空状态：`EmptyState` 组件，文案"当前无禁言"
- 刷新：页面加载时 fetch 一次，不需要轮询

**I3 回滚**：`git restore admin/routes/api/ admin/frontend/src/views/dashboard.vue`

**D6 提醒**：I3 涉及前端改动，完成后需 `cd admin/frontend && npm run build`。不需要 docker rebuild bot。

---

### Wave I4 — 集成验证

| 步骤 | 命令 | 预期 |
|---|---|---|
| 1 | `grep -rn "send_group_msg" plugins/ --include="*.py"` | 所有 send 点前都有 mute 检查 |
| 2 | `uv run pytest tests/test_plugin_mute_gate.py tests/test_self_mute_reconcile.py -v` | 全部通过 |
| 3 | `uv run ruff check plugins/ services/scheduler.py kernel/config.py admin/routes/` | 无 lint 错误 |
| 4 | `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit && npm run build` | 前端编译通过 |
| 5 | 手动验证：在测试群禁言 bot → 观察 echo plugin 是否静默 | 不发送消息，日志显示 "muted, skip send" |

**Wave I0 回填（2026-05-27）**：

- D1 实证结果：
  - `services/scheduler.py:124-156` 现有 mute 基础设施仍是 `_muted_groups + mute()/unmute()/is_muted()`
  - `grep -rn "send_group_msg" plugins/ --include="*.py"` 真实绕过点锁定为：
    - `plugins/echo/plugin.py:217,222`
    - `plugins/element_detector/plugin.py:162,173`
    - `plugins/food/plugin.py:450,459`
  - `kernel/router.py:897-900` 启动时已有一次 `get_group_member_info + shut_up_timestamp` 查询；`kernel/router.py:1327-1336` 已有 `group_ban / lift_ban` 事件接线
  - admin 路由目录现成存在 `admin/routes/api/scheduler.py` 与 `admin/routes/api/dashboard.py`，I3 可直接接在这两处，不必新建模块
- 口径修正：
  - 执行单旧稿写 `_poll_self_mute_status()`，当前仓真实实现是 `kernel/router.py` connect 时的内联启动查询，并没有独立 helper 名称
  - `food` 插件的风险点不在 `on_message()` 返回值本身，而在 `_feedback_recommend()` 背景任务里的两次 `send_group_msg()`

**Wave I1 回填（2026-05-27）**：

- plugin mute gate 已落地：
  - `plugins/echo/plugin.py`：timeline 记录后、实际发送前加 `is_muted(group_id)` 检查；命中时 `return True`
  - `plugins/element_detector/plugin.py`：同模式加 gate；命中时 `return True`
  - `plugins/food/plugin.py`：在 `_feedback_recommend()` 背景任务里，推荐前和发送前各补一次 mute 检查；命中时直接 `return`
- 语义说明：
  - `echo` / `element_detector` 继续按“已识别但静默跳过发送”口径消费消息，避免消息继续流入 scheduler 触发 LLM
  - `food` 的反馈推荐本来就是后台任务，不需要额外向主消息管线返回值对齐；因此在后台任务内短路是最小侵入
- 测试：
  - 新增 `tests/test_plugin_mute_gate.py`
  - 覆盖 echo muted、element_detector muted、food feedback muted 三条路径

**Wave I2 回填（2026-05-27）**：

- `services/scheduler.py` 已落地：
  - 新增 `_MuteRecord`，在原有 `_muted_groups` 之上记录 `source / since_unix / until_unix`
  - `mute()` 升级支持 `source / since_unix / until_unix`
  - 新增 `get_mute_state()` 供 admin API / dashboard 读取
  - 新增 `_active_groups()`、`_reconcile_self_mute_once()`、`_reconcile_self_mute_loop()`
  - `set_bot()` 在 `self_mute.reconcile_enabled=true` 时自动起 reconcile task；`close()` 会一并 cancel
  - `_send_to_group()` 的 `except ActionFailed` 分支新增 retcode 解析 + reverse-mark 逻辑
- config + wiring：
  - `kernel/config.py`：新增 `SelfMuteConfig`
  - `config/config.json`：新增默认 OFF 的 `"self_mute"` 段
  - `plugins/chat/plugin.py`：`GroupChatScheduler(...)` 新增 `self_mute_config` 和 `group_inventory_getter`
  - `kernel/router.py`：
    - connect-time 启动查询命中后改记 `source="reconcile"`
    - `group_ban` 事件改记 `source="event"` 与 `until_unix`
- 实现口径修正：
  - 真实 `ActionFailed` 结构在当前 nonebot/onebot 版本里是 `e.info == {"info": {...}}`，不是平铺 dict；因此 `_action_failed_retcode()` 做了兼容解析，按运行时事实收口
- 测试：
  - 新增 `tests/test_self_mute_reconcile.py`
  - 覆盖：server says muted、server says unmuted、ActionFailed reverse-mark、reconcile cancel-path

**Wave I3 回填（2026-05-27）**：

- admin API / dashboard 可见性已落地：
  - `admin/routes/api/scheduler.py`：新增 `GET /api/admin/scheduler/mute_state`
  - `admin/routes/api/dashboard.py`：dashboard payload 新增 `self_mute`
  - `admin/frontend/src/views/dashboard/DashboardView.vue`：
    - 新增 `DashboardSelfMute` 类型
    - 页面中部新增 “Bot 禁言状态” 区块
    - 复用 `AppPanelSection + AppCard + StateBadge + EmptyState`
    - 不引入新配色和奇异布局，保持现有 Calm Ops 仪表盘节奏
- 测试：
  - `tests/test_admin_api.py`：补 `/api/admin/scheduler/mute_state`
  - `tests/test_dashboard_cache_pipelines.py`：补 dashboard `self_mute` 字段暴露
- 前端验证：
  - `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit && npm run build`
    - 编译通过

**Wave I4 回填（2026-05-27）**：

- 验证结果：
  - `grep -rn "send_group_msg" plugins/ --include="*.py"`
    - 本簇覆盖的三处真实发送点已全部补 gate；`group_admin` 仍是管理工具路径，不属于 self-mute 生命周期修复范围
  - `source ./scripts/dev/env.sh && uv run pytest tests/test_plugin_mute_gate.py tests/test_self_mute_reconcile.py tests/test_dashboard_cache_pipelines.py tests/test_admin_api.py tests/test_humanization_config.py tests/test_scheduler.py -q`
    - 已包含在本轮组合验证中，整体 `130 passed`
  - `source ./scripts/dev/env.sh && uv run ruff check ...`
    - `All checks passed`
  - `source ./scripts/dev/env.sh && uv run pyright plugins/echo/plugin.py plugins/element_detector/plugin.py plugins/food/plugin.py services/scheduler.py kernel/config.py admin/routes/api/scheduler.py admin/routes/api/dashboard.py services/llm/sentinel_registry.py tests/test_plugin_mute_gate.py tests/test_self_mute_reconcile.py tests/test_dashboard_cache_pipelines.py tests/test_humanization_config.py tests/test_sparkle_watcher.py`
    - `0 errors, 0 warnings`
  - `cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit && npm run build`
    - 编译通过
- I 簇结论：
  - self-mute 生命周期三段都已接上：plugin mute gate、reconcile + reverse-mark、admin 可见状态
  - 新能力默认 OFF：`self_mute.reconcile_enabled=false`、`action_failed_reverse_mark=false`

---

## 派单 J — 符号监测（F9）

> 共骨架：复用 P0 A 簇 `services/llm/sentinel_registry.py` 的 `SentinelEntry` + `action="warn"` 模式。
>
> 核心思路：不做判定，只做观测。将 ✨ 注册为 `warn_only` sentinel，30 天收集数据后再决定是 strip 还是白名单。

### 前置知识（执行者必读）

**sentinel_registry 的 warn 模式**：

```python
# services/llm/sentinel_registry.py:115-116
if entry.action == "warn":
    return GuardrailResult(passed=True, text=text, hits=hits)
```

`action="warn"` 时：
- `passed=True` → 文本不被修改，正常发送
- `hits` 非空 → 命中记录被保留，可用于 metric 统计
- 对用户完全透明——bot 行为不变，只是后台多了一条 hit 记录

**已有 sentinel 注册模式**（`_DEFAULT_SENTINELS` tuple，line 55-61）：

```python
_DEFAULT_SENTINELS: tuple[SentinelEntry, ...] = (
    SentinelEntry("sentinel_image", re.compile(r"«图片(?::[^»]*)?[^»]*»"), action="strip"),
    SentinelEntry("sentinel_face", re.compile(r"«表情»"), action="strip"),
    # ...
)
```

新增 ✨ watcher 只需在 tuple 末尾追加一条 `SentinelEntry`。

**metric 通道**：

- `services/block_trace/store.py` 的 `record_runtime_metric()` 已在 P0 落地
- sentinel hit 通过 `services/llm/client.py` 的 `_apply_visible_reply_guardrails()` 返回的 `GuardrailResult.hits` 自动记录
- admin SPA 的 metrics 面板已能展示 sentinel hit 统计

---

### Wave J0 — 前置验证（零代码）

| 步骤 | 命令 / 操作 | 预期 | 目的 |
|---|---|---|---|
| 1 | `grep -n "_DEFAULT_SENTINELS" services/llm/sentinel_registry.py` | 确认 tuple 位置 | 追加位点 |
| 2 | `grep -n "hits.*metric\|record.*hit\|guardrail.*hit" services/llm/client.py` | 确认 hit 自动记录路径 | 确保 warn hit 也被记录 |
| 3 | `grep -rn "✨\|☆" services/ plugins/ config/` | 确认当前无同名规则冲突 | 命名空间干净 |

---

### Wave J1 — ✨ watcher 注册

| 编号 | 一句话 | 关键文件 | 详细指导 |
|---|---|---|---|
| **J1.1** | 追加 SentinelEntry | `services/llm/sentinel_registry.py:61` 后 | 见下方规格 |
| **J1.2** | 频率回归测试 | `tests/test_sparkle_watcher.py` | 覆盖 3 个场景 |

**J1.1 详细规格**：

在 `_DEFAULT_SENTINELS` tuple 末尾追加：

```python
SentinelEntry(
    "sparkle_symbol_watcher",
    re.compile(r"[✨☆★✧⭐]"),
    severity="low",
    action="warn",  # 不修改文本，仅记录
    replacement="",  # warn 模式不使用 replacement
),
```

**pattern 说明**：

- `✨`（U+2728 SPARKLES）— 用户报告的主要符号
- `☆`（U+2606 WHITE STAR）— 人设中"哇嚯☆"的合法用法，但需要监测频率
- `★✧⭐` — 同族符号，一并监测以建立 baseline

**为什么用 character class 而非单字符**：建立完整的"星号族"baseline，30 天后可以区分"哪些是人设合法用法（☆）、哪些是 LLM 自由发挥（✨）、哪些是 sticker description 回流"。

**J1.2 测试场景**：

| 场景 | 输入 | 预期 |
|---|---|---|
| 含 ✨ 的回复 | "今天心情好✨" | hit=True, passed=True, text 不变 |
| 含 ☆ 的回复 | "哇嚯☆好厉害" | hit=True, passed=True, text 不变 |
| 无星号族的回复 | "今天天气真好" | hit=False, passed=True |

**J1 回滚**：`git restore services/llm/sentinel_registry.py && rm -f tests/test_sparkle_watcher.py`

---

### Wave J2 — 集成验证

| 步骤 | 命令 | 预期 |
|---|---|---|
| 1 | `uv run pytest tests/test_sparkle_watcher.py -v` | 全部通过 |
| 2 | `uv run pytest tests/test_sentinel_pipeline_e2e.py -v` | 现有 sentinel 测试不受影响 |
| 3 | `uv run ruff check services/llm/sentinel_registry.py` | 无 lint 错误 |

**Wave J0 回填（2026-05-27）**：

- D1 实证结果：
  - `_DEFAULT_SENTINELS` 真实注册位点在 `services/llm/sentinel_registry.py` 顶部 tuple，而不是独立 `_register_defaults()` helper
  - sentinel hit 持久化仍走 `services/llm/client.py -> _apply_visible_reply_guardrails() -> block_trace` 现有链路，本簇不需要再扩 metric 通道
  - `grep -rn "✨\\|☆" services/ plugins/ config/` 当前无冲突规则

**Wave J1 回填（2026-05-27）**：

- `services/llm/sentinel_registry.py` 已在 `_DEFAULT_SENTINELS` 末尾追加：
  - `SentinelEntry("sparkle_symbol_watcher", re.compile(r"[✨☆★✧⭐]"), severity="low", action="warn")`
- 落地口径：
  - 保持 `warn`，不改文本，只打 hit
  - 监控范围按推荐方案使用完整星号族字符类，不只盯 `✨`
- 测试：
  - 新增 `tests/test_sparkle_watcher.py`
  - 覆盖：`✨`、`☆`、无星号族三种场景

**Wave J2 回填（2026-05-27）**：

- 验证结果：
  - `source ./scripts/dev/env.sh && uv run pytest tests/test_sparkle_watcher.py tests/test_sentinel_pipeline_e2e.py -q`
    - `6 passed`
  - `source ./scripts/dev/env.sh && uv run ruff check services/llm/sentinel_registry.py tests/test_sparkle_watcher.py`
    - 已包含在本轮组合 lint 中，`All checks passed`
  - `source ./scripts/dev/env.sh && uv run pyright ... services/llm/sentinel_registry.py tests/test_sparkle_watcher.py`
    - `0 errors, 0 warnings`
- J 簇结论：
  - watcher 已挂载到现有 sentinel_registry，行为透明，对用户可见回复无改写
  - 后续 30 天观测直接沿用本文 follow-up 指标

---

## 30 天观测计划（J 簇 follow-up）

J1 落地后，30 天内收集以下数据：

| 指标 | 查询方式 | 判定标准 |
|---|---|---|
| ✨ 出现频率 | admin metrics 面板 `sparkle_symbol_watcher` hit count | > 5 次/天 → 可能是 bug |
| ☆ 出现频率 | 同上，按字符分组 | 应稳定在 1/4 - 1/8 区间（人设"哇嚯☆"） |
| 是否伴随 sticker tag | 检查 hit 前后 context 是否含 `[CQ:image` / `mface` | 伴随 → sticker description 回流 |
| 是否与 F1 sentinel 同窗口 | 检查 hit 时间戳是否与其他 sentinel hit 聚集 | 聚集 → 系统性泄漏 |

**升级路径**：

- 证据指向 sticker description 回流 → 将 ✨ 的 action 从 `warn` 改为 `strip`
- 证据指向 LLM 自由发挥 → 不动（合法行为）
- ☆ 频率超出 1/4 - 1/8 区间 → 检查 persona drift 是否影响了"哇嚯☆"的使用频率

---

## 全局回滚

```bash
# H 簇回滚
git restore plugins/echo/plugin.py plugins/element_detector/plugin.py
rm -f tests/test_echo_humanizer_input.py

# I 簇回滚
git restore plugins/echo/plugin.py plugins/element_detector/plugin.py plugins/food/plugin.py services/scheduler.py kernel/config.py admin/routes/api/ admin/frontend/src/views/dashboard.vue
rm -f tests/test_plugin_mute_gate.py tests/test_self_mute_reconcile.py

# J 簇回滚
git restore services/llm/sentinel_registry.py
rm -f tests/test_sparkle_watcher.py
```

---

## 状态追踪

| Wave | 状态 | 完成日期 | 备注 |
|---|---|---|---|
| H0 | ✅ 完成 | 2026-05-27 | 前置验证完成，确认可直接复用 scheduler runtime |
| H1 | ✅ 完成 | 2026-05-27 | echo visible_text + runtime 参数补齐 |
| H2 | ✅ 完成 | 2026-05-27 | element_detector runtime 参数补齐 |
| H3 | ✅ 完成 | 2026-05-27 | pytest / ruff / pyright 全通过 |
| I0 | ✅ 完成 | 2026-05-27 | self-mute 真实位点和 API 入口实证锁定 |
| I1 | ✅ 完成 | 2026-05-27 | echo / element / food mute gate 已补 |
| I2 | ✅ 完成 | 2026-05-27 | reconcile + reverse-mark + config 已接线 |
| I3 | ✅ 完成 | 2026-05-27 | admin API + dashboard 可视状态已落地 |
| I4 | ✅ 完成 | 2026-05-27 | 后端验证 + 前端构建通过 |
| J0 | ✅ 完成 | 2026-05-27 | watcher 位点和 metric 路径实证完成 |
| J1 | ✅ 完成 | 2026-05-27 | sparkle watcher 已注册 |
| J2 | ✅ 完成 | 2026-05-27 | sentinel 相关验证通过 |
