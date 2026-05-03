# Changelog

All notable changes to Omubot are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.1] — 2026-05-03

### Fixed

- **文本分段算法重写**：替换三层函数叠加（`_split_on_sentence_end` + `_split_long_on_comma` + hard slice）为单一 `_smart_chunk` 回溯式标点优先级切分。扫描窗口从右向左找最佳断点：句末标点（。！？～…）→ 从句标点（，；：、）→ 字符边界（保护英文单词完整性）→ 硬切。解决 "AI修复" 被撕成 "AI"+"修复" 的孤儿碎片问题
- **段首标点修复**：`_smart_chunk` 将标点留在前段末尾（`t[:best]`），不再推到下一段开头
- **句尾从句标点剥离**：独立 QQ 消息末尾无意义连接符（"虽然我主要玩烤和邦邦，"）剥离从句标点，句末标点保留
- **`～` 升级为句末标点**：从仅用于 `\n` 合并判断升级为一级切分点，与 `。！？` 同级
- **`/debug split` 误输入保护**：纯 ASCII 小写首词检测，非已知子命令时提示可用命令而非送 LLM

### Added

- **`/debug split <文本>`** 子命令：实时测试 `_split_naturally()` 分段效果，别名 `/debug 分段`/`/debug 分割`
- 新增 5 个测试：段首无标点、英文完整性、尾段合并、精确回归、用户 case v2（共 13 个 split 测试）

### Changed

- `_MIN_CHUNK` 3 → 6，避免短片段逃脱合并逻辑
- `_MAX_CHUNK` 45 → 20（配合新算法更精确的断点选择）
- Bot 版本 1.2.0 → 1.2.1
- ChatPlugin 1.1.3 → 1.1.4

## [1.2.0] — 2026-05-03

### Fixed

- **句中断行合并**：`\n` 从硬分段边界降级为软提示。仅当上一行末尾有句末标点（。！？～…）」』））时才切分，句内换行直接合并
- **超长句语义切分**：硬字符切分替换为逗号层级语义切分，避免合并后完整句子被重新撕碎
- **指令更新**：分段指导从"换行即分段"改为"一个完整想法写完后再换行"

### Added

- `_SENTENCE_ENDING` 字符集用于 `\n` 合并判断
- `TestSplitNaturally` 测试类：9 个测试覆盖句中断行合并、句末切分、`---cut---`、长句语义切分、`_MIN_CHUNK` 合并

### Changed

- Bot 版本 1.1.1 → 1.2.0

## [1.1.0] — 2026-05-03

### Added

- **多级命令支持**：`Command.sub_commands` 字段，`CommandDispatcher` 递归匹配子命令，未命中回退父 handler
- **`/debug` 子命令**：`save`（别名: 保存/收录/添加表情）、`send`（别名: 发/发送）
- **B站视频链接识别插件**：识别 BV号/av号/b23.tv/番剧链接，注入视频摘要（含 Qwen VL 封面描述），本地缓存去重
- **B站回复模式**：4 种模式（mood/always/dedicated/autonomous），兴趣评估函数根据视频标题匹配 bot 人设关键词计算 0-1 兴趣分
- **心情系统 × 概率调度联动**：心情三维度（valence/energy/openness）计算 talk_value 乘数 [0.25, 2.0]
- **群聊延迟优化**：概率调度替代固定 debounce、移除独立 Thinker（~55 行）、合并 Sticker 强制执行（~23 行），延迟从 17-22s 降至 ~3-5s
- **调度器日志可见性**：5 处 skip 决策日志从 DEBUG 提升至 INFO
- **心情缓存修复**：`mood_getter` lambda 改为主动调用 `mood_engine.evaluate()`，修复重启后首次聊天心情乘数始终 1.0 的 bug
- **要素察觉启用**：`[element_detection]` 配置补全，修复 `identity_mgr`→`identity` 引用错误
- **NoneBot NICKNAME 配置**：补全 `config/.env` 的 `NICKNAME`，修复适配器层昵称剥离和 `to_me` 标记

### Changed

- **插件配置迁移**：6 个插件配置从中央 `config.toml` 迁移至 `plugins/<name>.toml`，Config 模型从 kernel/config.py 搬至各插件 .py 文件
- Bot 版本 1.0.7 → 1.1.0
- ChatPlugin 1.1.1 → 1.1.2

## [1.0.6] — 2026-05-02

### Fixed

- **mface（QQ 商城表情）检测**：`/debug 保存表情` 无法识别第四张 QQ 商城表情（如 `[星星眼]`）。新增 `market_face` 类型检测 + 从 `event.raw_message` 解析 `[mface:...]` CQ 码兜底，NoneBot 将其转为纯文本时仍可捕获
- **Qwen VL 连续调用限流**：多图保存时连续 vision API 调用触发硅基流动限流。图片间增加 1.5s 延迟，timeout 10s → 15s
- **历史加载触发正常回复**：Bot 重启后历史加载回填 `/debug` 消息进入 timeline，重新触发 thinker/chat 流程。`HistoryLoaderPlugin` 现跳过含 `/debug` 的消息

### Changed

- ChatPlugin 1.0.3 → 1.0.4
- HistoryLoaderPlugin 1.0.0 → 1.0.1
- VisionClient 错误日志增加异常类型

## [1.0.5] — 2026-05-02

### Changed

- **图像描述提升至系统层**：`VisionPlugin` 删除，`VisionClient` 移至 `services/media/vision.py`。由 `bot.py` 根据 `api_key` 是否填写自动启用，无需额外 `enabled` 开关
- **接入硅基流动 VLM**：配置 `Qwen/Qwen3-VL-30B-A3B-Instruct` 通过 SiliconFlow API 提供多模态描述，DeepSeek V4 不支持多模态
- **`/debug 保存表情` 图像描述**：保存时调用 Qwen VL 生成中文描述，替代硬编码占位符。支持用户自定义描述前缀
- **表情包评估日志**：回复日志新增 `sticker=sent`/`sticker=none` 字段
- **指令调度兼容前置文本**：`CommandDispatcher` 匹配 `/` 命令不再要求以斜杠开头，支持 `[表情] /debug` 等前置内容

### Added

- `config.example.toml` 新增 `[vision.qwen]` 示例段落

### Removed

- `QwenVLConfig.enabled` 字段：api_key 非空即启用，留空即关闭

### Changed (plugins)

- StickerPlugin 1.0.1 → 1.0.2

## [1.0.4] — 2026-05-02

### Fixed

- **表情包发送全线修复（critical）**：`SendStickerTool` 中 `subType`（驼峰）→ `sub_type`（蛇形），OneBot v11 协议要求蛇形命名，NapCat 静默忽略驼峰 key 导致所有表情包以普通图片发送
- **Docker 容器文件隔离**：bot 与 napcat 在不同容器，改为 base64 编码内联传输图片
- **`/debug` 指令触发 thinker**：命令匹配后调用 `scheduler.cancel_debounce()` 取消待处理的 thinker 触发
- **指令被复读插件检测**：`EchoPlugin` 跳过以 `/` 开头的消息

### Changed

- **`/debug` 直接调度**：新增 `_debug_direct_dispatch()` 绕过 LLM 直接执行已知命令（发表情等），避免 DeepSeek V4 幻觉不调用工具
- **表情包发送**：新增 `summary=[动画表情]` 字段使 QQ 正确渲染贴图样式
- ChatPlugin 1.0.2 → 1.0.3

## [1.0.3] — 2026-05-02

### Added

- **`/debug` 工具循环**：从单轮 `_call()` 改为完整 5 轮工具循环，LLM 可调用所有已注册工具
- **好感度插件 INFO 日志**：on_pre_prompt 记录好感度层级和分数，on_post_reply 记录互动后变化

### Fixed

- **分段逻辑根本修复**：`force_reply=True` 不再绕过 `_split_naturally()`，所有回复统一分段
- **消息分段增强**：硬字符上限强制切分、`---cut---` 逐行精确匹配、尾段合并仅对纯标点生效
- **日志频道默认值**：`LogChannelConfig.system` 默认改为 `True`
- **记忆卡片提取日志**：从 DEBUG 提升到 INFO

### Changed

- ChatPlugin 1.0.1 → 1.0.2
- AffectionPlugin 1.0.0 → 1.0.1
- MemoPlugin 1.0.0 → 1.0.1
- SchedulePlugin 1.0.0 → 1.0.1
- StickerPlugin 1.0.0 → 1.0.1

## [1.0.2] — 2026-05-02

### Added

- Command alias system: `Command.aliases` field, `CommandDispatcher` indexes by alias
- `/plugins` now accessible via `/p`, `/plg`, `/插件` (Chinese alias)
- All plugins signed with author "kragcola" (default in `AmadeusPlugin` base class)

### Fixed

- Scheduler: `proactive is None` no longer blocks @-mention responses — @ mentions always trigger a reply
- `force_reply` no longer strips mood/affection blocks for @ mentions (was conflating debug mode with mention-reply)
- Thinker now correctly fires for non-@ group messages (was blocked by scheduler proactive guard)

### Changed

- DebugCommands plugin upgraded to v1.1.0

## [1.0.0] — 2026-05-01

### Added

- Omubot 三层框架：Kernel（PluginBus）→ Services（LLC/Scheduler）→ Plugins（14 个）
- 核心聊天：消息路由、LLM 调用、Tool loop、Thinker 决策
- 14 个插件：Chat、DateTime、WebSearch、WebFetch、HttpApi、GroupAdmin、Vision、Sticker、Memo、Affection、Schedule、HistoryLoader、Dream、Echo、ElementDetector
- CommandDispatcher 服务层指令系统：/debug、/plugins、/version
- PluginBus 钩子驱动架构：on_startup、on_message、on_pre_prompt、on_post_reply、on_tick
- 插件自动发现：单文件 + 目录插件，plugin.json 侧车清单
- plugin_data_dir：插件私有数据目录（storage/plugins/），gitignored
- 上下文压缩（compact）：LLM 摘要 + 熔断器
- 记忆卡片系统（CardStore）：7 类 3 作用域，SQLite 持久化
- 多模态视觉：图片下载 → pyvips 缩放 → Anthropic API
- 表情包系统：SHA256 去重、LLM 可发送/保存、Dream Agent 整理
- 好感度系统：分数累加、昵称系统、Prompt 态度调节
- 模拟日程：每日 LLM 生成，结合真实日期与季节
- Dream Agent：定期记忆整理 + 表情包清理
- Admin 面板：用量统计、配置查看、Soul 在线编辑、日志查看
- LLM 用量追踪：SQLite 记录、TUI 查看、API 端点
- 群聊调度器：debounce + batch 双模式、@消息优先
- 单群配置覆盖：at_only、debounce、batch_size、blocked_users
- 群聊隐私遮掩：QQ 号脱敏
- 复读检测：5 分钟内同消息 3 次触发
- 管理员系统：超级管理员 + 群管理工具
- Docker Compose 部署：NapCat + Bot 双容器
- config/ gitignore 隔离：API key 不进入版本控制

[1.0.0]: https://github.com/kragcola/omubot/releases/tag/v1.0.0
