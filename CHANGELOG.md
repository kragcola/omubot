# Changelog

All notable changes to Omubot are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.1] — 2026-05-02

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
