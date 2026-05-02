# 部署

## Docker Compose（推荐）

```bash
# 首次启动
cp config/.env.example config/.env
cp config/config.toml.example config/config.toml
cp config/soul/identity.example.md config/soul/identity.md
docker compose up -d

# 日常运维
docker compose restart bot       # 重启 bot（配置变更）
docker compose up bot -d --build # 重建 bot（代码变更）
docker compose restart napcat    # 重启 NapCat（断线重连）
docker compose logs bot --tail=50
```

## 关键规则

- **永远不要 `docker compose down` + `up` 重启 napcat**：device fingerprint 变化 → 腾讯反欺诈。始终用 `restart`
- **NapCat WebUI**：`http://localhost:6099`，扫码登录

## 端口

| 端口 | 服务 | 用途 |
|------|------|------|
| 6099 | NapCat WebUI | 扫码登录、QQ 管理 |
| 8081 | NoneBot FastAPI | Bot API + Admin Dashboard |
| 29300 | NapCat HTTP | OneBot HTTP API |
| 3001 | NapCat WS | OneBot WebSocket |

## 本地开发

```bash
uv sync
docker compose up napcat -d     # 仅启动 NapCat
uv run python bot.py            # 直接运行 bot
```

## 容器结构

```
docker compose
├── napcat (mlikiowa/napcat-docker)
│   └── QQ NT 协议 → WebSocket 3001
└── bot (omubot-bot)
    ├── NoneBot2 → OneBot V11 Adapter → napcat:3001
    ├── FastAPI → :8080 (→ 宿主机 :8081)
    └── storage/ (volume mount)
```

## 存储路径

```
storage/
├── usage.db          # LLM 用量
├── messages.db       # 群消息持久化
├── memory_cards.db   # 记忆卡片
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
5. `config/config.toml` — `api_key` 有效、余额充足
