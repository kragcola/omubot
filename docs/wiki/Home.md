# Omubot — QQ 机器人框架

基于 NoneBot2 + Anthropic Claude API 的三层 QQ 机器人框架。NapCat 处理 QQ 协议，DeepSeek API 驱动 LLM 对话。

## 快速开始

```bash
git clone https://github.com/kragcola/omubot.git
cd omubot
cp config/.env.example config/.env
cp config/config.toml.example config/config.toml
cp config/soul/identity.example.md config/soul/identity.md
docker compose up -d
```

## 核心特性

- **三层架构**：Kernel (PluginBus) → Services (LLM/Scheduler) → Plugins (14个)
- **多轮工具调用**：LLM 可调用 web_search、send_sticker、lookup_cards 等工具
- **群聊智能调度**：debounce + batch 双模式，@消息优先
- **表情包系统**：SHA256 去重存储，LLM 可按场景匹配发送
- **好感度与记忆**：用户好感度分数 + SQLite 记忆卡片
- **上下文压缩**：LLM 摘要历史消息，突破 token 限制
- **多模态视觉**：图片理解 (Qwen VL)，粘贴即描述
- **复读打断**：5 分钟内同消息 3 次触发
- **Admin 面板**：用量统计、Soul 编辑、日志查看

## 技术栈

| 层 | 技术 |
|----|------|
| 框架 | NoneBot2 + OneBot V11 |
| LLM | DeepSeek API (Anthropic 兼容) |
| QQ 协议 | NapCat (NTQQ) Docker |
| 数据库 | SQLite (用量、消息、记忆卡片) |
| 部署 | Docker Compose |

## 版本

当前版本：**v1.0.4** | [Release Notes](https://github.com/kragcola/omubot/releases/tag/v1.0.4)
