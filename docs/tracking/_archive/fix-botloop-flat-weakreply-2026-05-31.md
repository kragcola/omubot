# 烤群三问题排查（bot↔bot 循环 / 扁平化用语 / 弱回复失效）

> 2026-05-31 03:50 线上日志排查（`bot_2026-05-31.log`，群 993065015）。**只探查根因，未改代码。** 三个现象同源高度关联——根子是 **bot↔bot 互@走 force_reply、绕过所有弱回复/节制层**，叠加 **pair_guard 未启用**。

## 现象与时间线（实证）

03:46–03:48，bot `384801062`(emu/凤笑梦) 与另一个 bot `2708815230`(🍟薯条) **每 8–10 秒互@一次，连续 15+ 轮**，内容全是"晚安/去剪视频/薯条管够/快睡"的空转客套（reply+@ 对方，谁也停不下来）。其间 bot 两次（03:44:28、03:46:39）发出几乎相同的 **"我先缓一下\n马上接你"** 占位语。

## 问题①：bot↔bot 循环互@（P0 复发）

**根因（两层）**：
1. **pair_guard 线上未启用**：`load_config()` 实测 `bot_pair_guard.enabled=False`、`known_other_bots={}`。此前为防循环加的 P0 机制**根本没开**，对方 bot `2708815230` 也未登记。
2. **故每条对方@都被当正常 @bot 必答**：shadow gate 实测 `is_addressed=True, current_trigger=at_mention, action=force_reply confidence=1.00`。两 bot 互为"被寻址"→ 互相 force_reply → 无限对@。

**证据**：`bot_pair_guard` 全仓日志 0 命中；03:46–03:48 互@序列（≥15 条 `from 2708815230 [reply][at:qq=384801062]`）。

**修复方向（待定，未改）**：开 `bot_pair_guard.enabled=true` + 把 `2708815230` 等登记进 `known_other_bots`；并复核 pair_guard 的 `max_per_minute=3 / cooldown_seconds=60` 是否真能掐断（它在 router 的接入点是否先于 force_reply 生效——需确认 `_maybe_drop_pair_guard` 在 at_mention bypass 之前）。

## 问题②："缓一下/接住你"扁平化 AI 套话

**根因**：这不是话术配置问题，是 **LLM 在"被 force_reply 逼着必须回、但对方在说车轱辘客套、无实质可接"时的逃避占位模板**。

- 03:44:22 对方 bot 发 `🍟`（一个表情），bot 被 @ → `force_reply` → **跳过 thinker**（client.py:3884 `if not force_reply`）→ 主 LLM 直接生成 → 憋出 "我先缓一下\n马上接你"。
- 这类"缓一下/接住你/接住"是模型在**信息量为零的输入 + 强制回复义务**下的标准敷衍，与 bot↔bot 空转互刷强相关（对方全是 `(´▽`ʃ♡)🍟` 这种无内容客套）。
- **加重因素**：force_reply 跳过 thinker，意味着连"要不要回/这条值不值得实质回"的判断都没有 → 模型只能硬挤一句。

**修复方向（待定）**：根治依赖①（断了 bot↔bot 空转，这类处境就消失大半）；其次可考虑 force_reply 路径也过一道"无实质内容→light_reply/短应"的轻判，而非硬走主 LLM 长生成。

## 问题③：弱回复机制未生效

**根因（两层，均确凿）**：
1. **closing 注入要求 `not is_addressed`**（router.py:1369-1375）：closing 弱回复只在**非寻址**的告别场景注入 `mode=closing`。但 bot↔bot 的"晚安/快睡"客套都是 **@ 对方（is_addressed=True）**，**永远进不了 closing 分支**。全天 `closing` 注入 **0 次**。
2. **force_reply 跳过整个 thinker**（client.py:3884）：@ 路径 force_reply=True → thinker 不跑 → thinker 的 `light_reply`/`light_kind=closing` 判定（client.py:4094 `is_closing_turn`）依赖 `thinker_decision`，根本没机会触发。

**证据**：全天 thinker 只跑 **5 次**（action=reply×3 / wait×1 / light_reply×1），`closing` 注入 0 次，`light_kind=closing` 0 次。海量 @ 流量全走 force_reply 绕过了弱回复层。

**定性**：弱回复（closing/companion）**在 @ 密集场景结构性失效**——它被设计在"非寻址 + thinker 跑"的路径上，而 @（尤其 bot↔bot 互@）恰恰是"寻址 + force_reply 跳过 thinker"，两者不相交。

## 三点的关联（一句话）

**bot↔bot 互@（①，因 pair_guard 没开）制造了海量 force_reply 流量；force_reply 跳过 thinker，既让弱回复/closing 无从触发（③），又让模型在无实质输入下硬挤敷衍套话（②）。** 断①是上游总闸。

## 建议优先级（待用户定，未改代码）

1. **① 最高**：开 pair_guard + 登记对方 bot，并核实其接入点在 at_mention bypass 之前能真正掐断循环。这是 P0 复发，且是②③的上游。
2. **③ 次之**：评估"force_reply 路径是否也应让 closing/light 判定有机会跑"——当前弱回复与 @ 路径不相交是设计盲区。
3. **② 顺带**：①断了之后复看；必要时给 force_reply 的"零信息输入"加轻应短路，避免敷衍长句。

**待确认**：pair_guard 为何线上是关的（配置遗漏 / 曾回退 / 从未在 config.json 开）？这决定①是配置修复还是需补码。

## 补充：① 是纯配置修复，无需补码（已核实接入顺序）

`_maybe_drop_pair_guard` 在 `kernel/router.py:1028`，**先于** `is_addressed=event.is_tome()`（:1060）、trigger 构造（:1123+）、`_notify_group_scheduler`（:1183/:1457）。命中即 `return`（:1033）——**pair_guard 在 @ 判定与 fire 之前拦截**。故只要 `bot_pair_guard.enabled=true` + 把对方 bot QQ 登记进 `known_other_bots`，循环就在 router 入口被掐断，**无需改代码**。`max_per_minute=3 / cooldown_seconds=60` 的限频/冷却逻辑随之生效。

→ ① 修复 = 改 `config/config.json`：`bot_pair_guard.enabled=true` + `known_other_bots` 加 `2708815230`(🍟薯条) 及其它已知 bot；`docker compose restart bot`（config 是 bind mount，无需 rebuild）。**待用户确认对方 bot QQ 清单后即可执行。**
