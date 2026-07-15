# Dialogue Climate Runtime Completion Migration Checklist

> 状态：complete and deployed，2026-07-15。
> 范围：把已部署 M2/M3/M4 的临时接线迁到单一 provider/policy/state owner，并彻底退役 legacy M1 tension。

## Prompt And Policy

- [x] 旧：schedule plugin 直接 resolve ClimateEngine 并注入“对话气候”block；新：ClimateProvider 从受管 snapshot 产出唯一 candidate。
- [x] 旧：affection plugin 独立注入“与当前用户的关系”block；新：关系信息合并进 ClimateProvider 的同一 candidate。
- [x] 旧：schedule 时间/心情 block、affection block、climate block 可并列；新：仅保留必要的时间事实，关系/气候指导只有一个 owner。
- [x] 旧：`PolicyOutput.reply_bias` 只生成不消费；新：Thinker 从同一 policy snapshot 消费。
- [x] 旧：`PolicyOutput.delay_multiplier` 只生成不消费；新：Humanizer 从同一 policy snapshot 消费。
- [x] 所有旧 block 只在新 provider 已启用且产出有效 candidate 时让位；关闭总闸可恢复既有 fallback。

## Sensor Feed

- [x] 旧：MessageSensor 在 default_sensors 中，但生产上下文无 `message_label`，实际 no-op；新：chat 用户消息路径调用 MoodClassifier 并把 label/confidence 直接传给 SensorHub。
- [x] classifier feed 不写、不覆盖 `MOOD_CURRENT_SLOT`，sticker/mood 既有语义保持不变。
- [x] 分类失败必须 fail-open 到“无 message signal”，不能阻断聊天主路径。

## Durable State

- [x] 旧：ClimateEngine `_states`/baseline 只在内存，重启丢失；新：专用 owner 保存 authoritative per-(group,user) slow baseline。
- [x] 启动恢复先校验 schema/version/fingerprint/时间戳，过期或损坏行不污染运行态。
- [x] 热路径不做无界同步 SQLite 写；批量/队列/明确 flush 策略有测试。
- [x] shutdown/cancellation 可观测：已接收状态不会因取消留下部分写或虚假成功。
- [x] metrics DB 继续只作观测，不伪装成 authoritative state store。

## Tension Ownership

- [x] 旧：`qq_interactions` 同时写 SensorHub 和 M1；新：只写 ClimateEngine/SensorHub。
- [x] 旧：poke 路径另写 M1；新：同一 IrritationSensor/ClimateEngine owner。
- [x] 旧：schedule prompt guidance 读取 `_m1_tension_state`；新：读取共享 climate policy snapshot。
- [x] 旧：schedule event replan 读取 M1 tension；新：读取 ClimateEngine resolved tension。
- [x] 旧：dream 插件读取 M1 tension；新：读取 ClimateEngine group summary。
- [x] 旧：M1 recorder/metrics/finalizer 独立存在；新：bootstrap 不再创建/关闭 M1 owner，历史 metrics 仅保留只读。
- [x] 删除 `_m1_tension_state`、M1 注入/guidance/metrics API 后，全仓生产同模式扫描无命中。

## Compatibility And Delivery

- [x] 配置面移除 legacy `m1_enabled`，现有生产 m2/m3/m4 配置保持兼容；回滚依赖旧 image/config 备份，不删除历史 DB。
- [x] focused tests 覆盖 classifier、provider merge、Thinker、Humanizer、store restore/flush/cancel、M1 absence。
- [x] Ruff、Pyright、full pytest 通过；两轮 reviewer Important 均关闭，最终独立 review 为 0 Critical / 0 Important。
- [x] build 前检查 stash、tracked dirty、untracked inputs，精确 stage，禁止 `git add -A`。
- [x] 标记 pre-change bot image，执行 `docker compose build bot` + `docker compose up -d --no-deps --force-recreate bot`。
- [x] 验证 bot startup/OneBot、climate provider/store 状态、公开 silent 群固定窗零成功出站。
- [x] 验证 NapCat container/image/StartedAt/restart_count 均未变化。
- [x] 记录 rollback image 与代码回退入口；不删除已有 metrics 数据。
