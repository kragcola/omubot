# Omubot Wiki

基于 NoneBot2 的三层可扩展 QQ 机器人框架。设计灵感来自鸿蒙 OS（Kernel → 系统服务 → 应用）。

## 快速导航

| 文档 | 内容 | 适合 |
|------|------|------|
| [01 - 架构概览](01-architecture.md) | 三层模型、数据流、设计原则 | 所有人 |
| [02 - 内核 API](02-kernel-api.md) | PluginBus 完整 API、插件发现、异常隔离 | 插件开发者 |
| [03 - Context 类型](03-context-types.md) | 6 种 Context、PromptBlock、Content 类型 | 插件开发者 |
| [04 - 插件开发指南](04-plugin-guide.md) | 写一个插件的完整流程、优先级规则、最佳实践 | 插件开发者 |
| [05 - 系统服务](05-services.md) | LLM、记忆、图片、表情等服务的接口说明 | 服务维护者 |
| [06 - 配置系统](06-config.md) | BotConfig、TOML/环境变量/CLI 三层合并 | 运维 |
| [07 - 工具系统](07-tools.md) | Tool ABC、ToolContext、register_tools | 工具开发者 |
| [08 - 迁移指南](08-migration.md) | 从旧 src/ 单体到 Omubot 插件的迁移步骤 | 迁移执行者 |
| [09 - 术语表](09-glossary.md) | 所有核心概念的简短定义 | 所有人 |
| [10 - FAQ](10-faq.md) | 常见问题与解答 | 所有人 |

## 项目结构

```
kernel/          # 内核层 —— 零 I/O，零外部依赖
├── types.py     #   类型系统（Context、Tool、Identity、PromptBlock）
├── bus.py       #   PluginBus 调度器
├── config.py    #   配置系统（23 个 Pydantic 模型 + load_config）
├── router.py    #   NoneBot 消息路由
└── manifest.py  #   插件清单 + SemVer 解析
services/        # 系统服务层 —— LLM、记忆、媒体、工具
plugins/         # 插件层 —— 单文件 .py + 侧车 .json，或目录（多文件）
admin/           # 管理面板（系统服务）
config.example.toml  # 配置模板
soul/            # 人设模板（identity.example.md + instruction.example.md）
docs/            # 项目文档
wiki/            # 框架开发文档（本文档）
tests/           # 测试
config/          # 运行时配置（gitignored —— 从模板复制）
```

## 版本

当前版本：**Phase 6b 完成**（src/ 清理 + 人格解耦 + config/ 隔离 + 开源推送），Phase 7 待实施。

## 贡献

修改 wiki 时同步更新维护日志。
