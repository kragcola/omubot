# Omubot — QQ 机器人框架

基于 NoneBot2 + OneBot V11 + NapCat 的三层 QQ 机器人框架。当前运行重点已经从“单体迁移”进入“可治理的本地插件生态、统一上下文、表达学习与管理端运维控制台”阶段。

## 快速开始

```bash
git clone https://github.com/kragcola/omubot.git
cd omubot
cp .env.example config/.env
cp config.example.toml config/config.toml
# 人设走 v2：admin SPA「人设管理」上传 source.md → import → freeze → hot-reload
docker compose up -d
```

> 当前配置加载器优先读取 `config/config.json`，并兼容已有 `config/config.toml`。Admin 配置页保存时会写出 JSON 主配置。此机器的活跃开发工作区是 `/Volumes/OmubotDisk/omubot`（旧路径 `$HOME/OmubotWorkspace/omubot` / `/Volumes/我的电脑/omubot` 已废弃，磁盘格式化迁移后请勿再使用）。

## 当前状态

| 项目 | 当前事实 |
| --- | --- |
| 版本 | `v1.4.0`，来源为 `pyproject.toml` |
| 插件形态 | manifest v3 + 目录插件 + JSON 配置契约 |
| 本地插件包 | 23 个本地包/能力包，其中 19 个用户运行时插件，4 个系统锁定能力包 |
| 配置主路径 | `config/config.json`，`config/config.toml` 仅作为 legacy 兼容源 |
| 管理端 | Vue 3 + Naive UI，Calm Ops / 雾青控制台风格 |
| 部署 | Docker Compose；NapCat 反连 Bot，Bot 暴露 Admin Dashboard |

## 核心特性

- **三层架构**：Kernel (`PluginBus`) → Services → Plugins，内核保持调度与类型契约。
- **本地插件治理**：插件目录化、manifest v3、JSON 配置、系统锁定能力、本地包索引、签名/来源校验预留。
- **多 Provider LLM**：`llm.profiles`、`task_profiles`、DeepSeek 原生/Anthropic 兼容/OpenAI 风格 profile，支持任务级热切换。
- **统一上下文**：`ContextPlugin` 系统锁定，统一打包记忆卡片、文档知识库和知识图谱事实，避免重复注入。
- **知识库**：生产扫描 `docs/knowledge`，SQLite 持久索引，本地 BM25/ngram 检索，提供上下文调试和评测指标。
- **群内黑话**：候选学习、人工审核、每日 AI 复核、存量候选池 backlog reviewer、语义漂移治理、修订历史与 `slang_lookup` 工具。
- **表达学习**：独立于人设的 `style` 插件与 `/admin/style` 控制台，学习“怎么说”，不自动改 persona source。
- **对话归档底座**：`ConversationArchive` 提供消息事件流、scanner cursor、运行审计、证据引用和留存 dry-run 原语。
- **群画像与访问控制**：按群 profile 覆盖参与模式、工具 allow/block、回复风格、表情模式、黑话学习与 `silent_learn`。
- **系统运维**：Admin Dashboard、配置 diff/审计/快照回滚、日志、协议连接/trace、健康阈值告警、运行态错误存储。

## 技术栈

| 层 | 技术 |
|----|------|
| 框架 | NoneBot2 + OneBot V11 |
| QQ 协议 | NapCat Docker |
| LLM | 多 Provider profiles；默认兼容 Anthropic/OpenAI/DeepSeek 接入 |
| 数据库 | SQLite（用量、消息、记忆、知识索引、知识图谱、黑话、表达、好感度等） |
| 管理端 | FastAPI + Vue 3 + Naive UI |
| 部署 | Docker Compose |

## 版本

当前版本：**v1.4.0**
