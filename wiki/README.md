# Omubot Wiki

基于 NoneBot2 的三层可扩展 QQ 机器人框架。设计灵感来自鸿蒙 OS（Kernel → 系统服务 → 应用）。

> 本目录是框架开发 Wiki，偏内核/API/迁移视角；面向运行、部署和管理端的当前 Wiki 见 `docs/wiki/`。当前项目版本为 `v1.4.0`。

## 快速导航

| 文档 | 内容 | 适合 |
|------|------|------|
| [01 - 架构概览](01-architecture.md) | 三层模型、数据流、设计原则 | 所有人 |
| [02 - 内核 API](02-kernel-api.md) | PluginBus 完整 API、插件发现、异常隔离 | 插件开发者 |
| [03 - Context 类型](03-context-types.md) | 6 种 Context、PromptBlock、Content 类型 | 插件开发者 |
| [04 - 插件开发指南](04-plugin-guide.md) | 写一个插件的完整流程、优先级规则、最佳实践 | 插件开发者 |
| [05 - 系统服务](05-services.md) | LLM、记忆、图片、表情等服务的接口说明 | 服务维护者 |
| [06 - 配置系统](06-config.md) | BotConfig、JSON/TOML 兼容、环境变量/CLI 合并 | 运维 |
| [07 - 工具系统](07-tools.md) | Tool ABC、ToolContext、register_tools | 工具开发者 |
| [08 - 迁移指南](08-migration.md) | 从旧 src/ 单体到 Omubot 插件的迁移步骤 | 迁移执行者 |
| [09 - 术语表](09-glossary.md) | 所有核心概念的简短定义 | 所有人 |
| [10 - FAQ](10-faq.md) | 常见问题与解答 | 所有人 |

## 项目结构

```
kernel/          # 内核层 —— 调度、类型契约、配置、manifest
├── types.py     #   类型系统（Context、Tool、Identity、PromptBlock）
├── bus.py       #   PluginBus 调度器
├── config.py    #   配置系统（JSON 优先，TOML 兼容）
├── router.py    #   NoneBot 消息路由
└── manifest.py  #   插件清单 + SemVer 解析
services/        # 系统服务层 —— LLM、记忆、知识、黑话、表达、归档等
plugins/         # 插件层 —— manifest v3 目录插件（plugin.py + plugin.json + JSON 配置）
admin/           # 管理面板（系统服务）
config.example.toml  # legacy TOML 配置模板
soul/            # 人设模板（已退役 — C 系列 v2 only 切换 2026-05-27 后改用 config/persona/<id>/source.md）
docs/            # 项目文档
wiki/            # 框架开发文档（本文档）
tests/           # 测试
config/          # 运行时配置（gitignored —— 从模板复制）
```

## 版本

当前版本：**v1.4.0**。单体迁移、`src/` 清理、目录插件、manifest v3、JSON 插件配置、本地插件索引、统一上下文、黑话治理、表达学习和对话归档底座均已进入当前主线。

## 贡献

修改 wiki 时同步更新维护日志。
