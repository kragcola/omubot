# Changelog

All notable changes to Omubot are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
