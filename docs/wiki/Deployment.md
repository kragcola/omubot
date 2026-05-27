# 部署

## Docker Compose（推荐）

```bash
# 首次启动
cp .env.example config/.env
cp config.example.toml config/config.toml
# 人设走 v2：admin SPA「人设管理」上传 source.md → import → freeze → hot-reload
docker compose up -d

# 日常运维
docker compose restart bot       # 重启 bot（配置变更）
docker compose up -d --build --no-deps bot # 只重建 bot（代码变更，不碰 napcat）
docker compose restart napcat    # 重启 NapCat（断线重连）
docker compose logs bot --tail=50
```

## 关键规则

- **永远不要 `docker compose down` + `up` 重启 napcat**：device fingerprint 变化 → 腾讯反欺诈。始终用 `restart`
- **改 Bot 代码或 Admin Web 后只重建 `bot`**：使用 `docker compose up -d --build --no-deps bot`，不要顺手重建 `napcat`
- **NapCat WebUI**：`http://localhost:6099`，扫码登录

## 端口

| 端口 | 服务 | 用途 |
|------|------|------|
| 6099 | NapCat WebUI | 扫码登录、QQ 管理 |
| 8081 | NoneBot FastAPI | Bot API + Admin Dashboard |
| 29300 | NapCat HTTP | OneBot HTTP API |
| 29301 | NapCat WS | OneBot WebSocket（本地开发用） |

## 本地开发

```bash
source ./scripts/dev/env.sh
uv sync
docker compose up napcat -d     # 仅启动 NapCat
uv run python bot.py            # 直接运行 bot
```

本机日常开发目录是 `$HOME/OmubotWorkspace/omubot`。原外置盘 checkout `/Volumes/我的电脑/omubot` 只作为 staging/挂载来源，不用于普通测试运行。

## 容器结构

```
docker compose
├── napcat (mlikiowa/napcat-docker)
│   └── QQ NT 协议 → WebSocket 29301 / HTTP 29300
└── bot (omubot-bot)
    ├── NoneBot2 → OneBot V11 Adapter（NapCat WS 反连）
    ├── FastAPI → :8080 (→ 宿主机 :8081)
    └── storage/ (volume mount)
```

## 存储路径

```
storage/
├── usage.db          # LLM 用量
├── messages.db       # 群消息持久化
├── memory_cards.db   # 记忆卡片
├── knowledge_index.db # 文档知识库持久索引
├── knowledge_graph.db # 知识图谱事实与证据
├── slang.db          # 群内黑话、候选、AI 复核、语义漂移、修订历史
├── style.db          # 表达样本、反馈、动态风格档案
├── plugins/config/   # 插件 runtime override JSON
├── config/           # 配置审计与快照
├── logs/             # 日志（10MB 切割，30 天保留）
├── stickers/         # 表情包
├── affection/        # 好感度
└── schedule/         # 日程
```

## 故障排查

1. `docker compose ps` — 确认两个容器都在运行
2. `docker compose logs bot --tail=50` — bot 错误日志
3. NapCat WebUI (`:6099`) — QQ 是否在线
4. `config/.env` — `SUPERUSERS` JSON 格式正确
5. `config/config.json` / `config/config.toml` — `api_key` 有效、余额充足；JSON 优先，TOML 兼容读取
