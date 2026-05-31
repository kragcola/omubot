# 灰度 P2/P3 问题——方案候选（待审查）

> 状态：2026-05-27 从 [解决方案候选文档](omubot-grayscale-issues-2026-05-26-solutions.md) 抽出 P2/P3 子集。
>
> P0 五件已部署；P1 八件已进入派单执行。本文列 **P2 两件 + P3 一件**，暂不排期。Issue 17 已独立立项。
>
> 约束：与 P0/P1 同——**不允许修改人设文件**。所有方案纯运行时 / 代码层。

---

## 总览

| # | 标题 | P 级 | 紧迫性 | 性质 | 推荐方案 |
|---|---|---|---|---|---|
| 15 | 复读插件回复异常慢 | P2 | 中（节奏倒挂破沉浸） | plugin 输入选错 + runtime 参数缺失 | 15A |
| 16 | bot 自身禁言状态可见性 + 自动恢复 | P2 | 中（noise + UX） | 架构层缺 reconcile + admin SPA + echo gate | 16A |
| 9 | ☆/✨ 符号存疑 | P3 | 低（用户存疑） | watcher 监测即可 | 9A |

> **Issue 17 已独立立项**：经日志验证"丢 @ 目标"不成立，实际问题重新定义为"连续 @ 压测下拟人行为异常"（回复零延迟 + 重复内容 + 情绪不变）。详见 [omubot-grayscale-issue17-at-burst-humanization.md](omubot-grayscale-issue17-at-burst-humanization.md)。

---

## Issue 15 — 复读插件回复比正常 LLM 回复更慢（P2）

### 背景

echo plugin 的 humanizer.delay() 调用传入 `echo_key` 原始文本（含 `[image:sub:hash]` 等长 marker），导致 `char_delay * len(echo_key)` 远大于用户视觉感知字符长度。同时缺少 runtime 参数（mood/slot），humanizer 无法按心情调节节奏。

### 方案 15A — 输入修正 + 段感知 delay + runtime 参数补齐（推荐）

**修改位点**：`plugins/echo/plugin.py:189-195`

**改动内容**：

1. **输入修正**：新建 `_visible_text_for_humanizer(echo_key: str) -> str`
   - 从 echo_key 还原"可见字符长度"
   - `[image:sub:hash]` / `[face:id]` / `[json:prompt]` → 当 2-3 个字符长度
   - `[at:qq]` → 当 `len(at_target_nickname)` 长度
   - 剥离所有 marker 后的纯文本保留原长

2. **runtime 参数补齐**：加入 `**self._humanizer_runtime(group_id)`
   - 同 `services/scheduler.py:649-650` 已有模式
   - 包括 `register / slot / mood / thinking_elapsed_s=0`（echo 没有 thinker 阶段）
   - 在 plugin 内复刻一份轻量 helper，或抽到 `services/humanizer.py` 的 `runtime_params_for_group()`

3. **错误分支**：`echo_reply.startswith("打断")` 路径同样改为 visible_text 输入

**成本**：30-50 行 + 3-4 用例测试

**优势**：
- 针对根因——echo_key 含长 marker 是 delay 过长的直接原因
- 同时修两层缺陷——输入选错 + runtime 参数缺失
- 不动 humanizer 自身——风险局限于 echo plugin
- 与 scheduler 已有的 humanizer 调用模式一致

**风险**：
- visible_text 估算近似——image/face 取 2-3 字符是经验值，需灰度调参 1 周
- echo 是异步 task，`_humanizer_runtime(group_id)` 需要 ctx 传入（ctx 已有 group_id，不增加签名）

**D1 grep 锁**：`grep -rn "humanizer.delay\|humanizer\.delay" plugins/ services/` — 自审发现 `plugins/element_detector/plugin.py:143,154` 也缺 runtime 参数，属同模式位点，需一并修复

### 方案 15B — 只补 runtime 参数（不修输入选错）

仅在 `delay()` 调用加 `**self._humanizer_runtime(group_id)`，输入仍是 `echo_key`。

**成本**：10-15 行

**缺点**：不治本——`echo_key` 长度问题不解决，长复读仍慢。次选。

### 方案 15C — 所有 humanizer.delay 路径走 send_queue 统一处理

移除 echo plugin 内 humanizer 调用，改为 reply 直接进 send_queue 统一处理。

**成本**：300-450 行（涉及 send_queue 改造 + 所有 plugin 出口路径迁移）

**缺点**：变更面巨大，破坏 humanization part 1-5 已有的 plugin-level 灵活性。不推荐。

---

## Issue 16 — bot 自身被禁言时的状态可见性 + 自动恢复（P2）

### 背景

bot 被群管禁言后，echo plugin 仍尝试发消息导致 NapCat ActionFailed 累积日志噪声。admin SPA 无法看到 bot 当前禁言状态。NapCat 协议层 `shut_up_timestamp` 偶有不准，缺乏 reconcile 机制。

### 方案 16A — echo gate + admin SPA 自我状态卡 + 周期 reconcile 三段并行（推荐）

**第 1 段：echo plugin 加 mute gate**（必须做，bug fix）

- `plugins/echo/plugin.py:189` 调用 send_group_msg 前加 `if ctx.scheduler.is_muted(group_id): return`
- D1 同模式扫描：`grep -rn "bot.send_group_msg\|bot.call_api.*send_group_msg" plugins/` 找其他绕开 scheduler.is_muted gate 的 plugin

**第 2 段：admin SPA self-mute 状态可见**

- 后端：`admin/routes/api/` 增加 `GET /api/scheduler/mute_state`
  - 返回 `{group_id: {muted: bool, since_unix: int|null, source: "manual"|"event"|"reconcile"}}`
- 前端：`admin/frontend/src/views/dashboard.vue` 顶部状态卡新增"bot 自身禁言状态"区块
  - 列出当前被禁言的群、起始时间、来源
  - 复用 `MetricCard.vue` / `AppCard.vue`

**第 3 段：周期 reconcile + ActionFailed 反向标记**

- scheduler 注册 `_reconcile_self_mute_loop`——每 5 分钟跑一次 `bot.get_group_member_info(group_id, user_id=self_id, no_cache=True)`
- 对比 `shut_up_timestamp` 与 `_muted_groups`，不一致时 reconcile（信任 server-side 为准）
- send_queue ActionFailed 路径：`retcode in {1200, ...}` 时反向标记 mute

**config**：

```json
"self_mute_lifecycle": {
  "reconcile_interval_seconds": 300,
  "action_failed_reverse_mark": true,
  "admin_state_visible": true
}
```

**成本**：100-180 行（echo gate 5-10 / admin API 30-50 / admin SPA 卡片 40-60 / reconcile loop 50-80）+ 3-4 用例测试

**优势**：
- 三段并行——echo gate 是 bug fix（必须）；admin SPA 是 UX 必修；reconcile 是 robustness
- 复用已有基础设施——`scheduler.is_muted()` / `_handle_group_ban` / startup poll 都已落地
- 与 F7/F12 admin SPA 编辑面板同次 PR 一起做最经济（D6 一次 npm build）

**风险**：
- reconcile interval 300s 是平衡值——太密对 NapCat API rate limit 不友好
- ActionFailed retcode 集合需灰度采样确认——OneBot 协议 retcode 不完全标准化
- admin SPA 改动需走 D6 流程（`npm run build`，不 rebuild bot）

### 方案 16B — 只补 echo plugin mute gate

仅做 16A 第 1 段。

**成本**：10-15 行

**缺点**：仍存在 admin SPA 不可见的 UX 问题 + stale state 风险。次选。

### 方案 16C — ActionFailed-only 反向标记（不主动 reconcile）

16A 第 1 段 + 第 2 段保留，去掉周期 reconcile。

**成本**：60-100 行

**缺点**：错过 server 端主动 lift_ban 的恢复——bot 一直以为自己被禁言。可选但不推荐。

---

---

## Issue 9 — ☆/✨ 异常符号（P3，用户存疑）

### 背景

bot 回复中偶尔出现 ✨ 符号，用户不确定是 bug（sticker description 回流）还是 LLM 自由发挥（装饰性表达）。

### 方案 9A — soft-watch 30 天，挂 sentinel registry（推荐）

- 复用 P0 A 簇 `sentinel_registry`，加 watcher：`✨` action=`warn_only`（不 strip 不 redact，仅记 metric）
- 30 天数据收集：① 出现频率 ② 是否伴随 sticker description tag 残留 ③ 是否和 F1 sentinel 同窗口爆发
- 数据收集后判定升级路径：
  - 若证据指向 sticker description 回流 → 升 `strip` / 入黑名单
  - 若证据指向 LLM 自由发挥 → 加入 `_DECOR_RE` 白名单
- ☆ 频率回归保险丝：`tests/test_persona_marker_frequency.py`——sample 100 条历史 reply，验证「哇嚯☆」落在 1/4 - 1/8 区间

**成本**：~5-10 行附加到 sentinel registry + 一份频率测试

**优势**：
- 不下结论，符合用户"存疑"判定
- 0 风险（warn_only，不改写）
- 顺带建立"将来发现新泄漏字符直接加 config"的可扩展模式

**风险**：
- 30 天 baseline 期间用户可能再次复读戏仿——若数据越发支持"是 bug"，需快速升级

### 方案 9B — 立即 strip ✨

直接放入 sentinel registry 的 `strip` 集合。

**缺点**：代用户做决定（用户判定是"存疑"）；✨ 可能是合法自由发挥。不推荐。

### 方案 9C — 把 ✨ 加入 `_DECOR_RE` 白名单

`services/humanization/scorer.py:18` 的 `_DECOR_RE` 加入 ✨。

**缺点**：同样代用户做决定；若实际是 bug 则把 bug 合法化。不推荐。

---

## 决策模板

```text
Issue 15 / echo humanizer：    [ ] 15A 推荐  [ ] 15B  [ ] 15C  [ ] 暂不做
Issue 16 / self-mute lifecycle：[ ] 16A 推荐  [ ] 16B  [ ] 16C  [ ] 暂不做
Issue 9 / ☆/✨ symbols：        [ ] 9A 推荐  [ ] 9B  [ ] 9C  [ ] 暂不做

执行批次偏好（可选）：
[ ] F15 单独做（单点 plugin 修复，30-50 行）
[ ] F16 与 P1 E 簇 admin SPA 改动批一起（D6 一次 npm build）
[ ] F9 挂 P0 A 簇 sentinel registry 顺手加（5-10 行）
[ ] 全部暂不做，观察 P0/P1 落地效果再定
[ ] 其他：___
```
