# 部署

## Docker Compose（推荐）

```bash
# 首次启动
cp .env.example config/.env
cp config.example.toml config/config.toml
# 人设走 v2：admin SPA「人设管理」上传 source.md -> import -> freeze -> hot-reload
docker compose up napcat -d
docker compose up -d --build bot ccip-sidecar
```

日常运维常用命令：

```bash
docker compose restart bot                          # 仅配置变更
docker compose up -d --build --no-deps bot         # bot 代码/依赖变更
docker compose up -d --build --no-deps ccip-sidecar # 角色识别 sidecar 变更
docker compose restart napcat                      # 断线重连，唯一安全方式
docker compose logs bot --tail=50
docker compose logs ccip-sidecar --tail=50
```

前端仅改 `admin/frontend` 时，不需要 rebuild bot；执行 `npm run build` 让 `admin/static` 更新即可。

## 关键规则

- **永远不要 `docker compose down` + `up` 重启 napcat**：device fingerprint 变化会触发腾讯反欺诈，始终使用 `docker compose restart napcat`。
- **Bot / Sidecar 改动分开重建**：`bot` 与 `ccip-sidecar` 各自按需 `--no-deps --build`，不要顺手重建 `napcat`。
- **`admin/static` 是 bind mount**：前端 build 产物会直接生效；后端 API 改动仍需要 rebuild `bot`。
- **NapCat WebUI**：`http://localhost:6099/webui`。

## 端口

| 端口 | 服务 | 用途 |
|------|------|------|
| 6099 | NapCat WebUI | 扫码登录、QQ 管理 |
| 8081 | bot / FastAPI | Admin Dashboard + Bot API |
| 8620 | `ccip-sidecar` | 角色识别与角色包构建 |
| 8610 | `pmubot` | 可选控制平面 |
| 29300 | NapCat HTTP | OneBot HTTP API |
| 29301 | NapCat WS | OneBot WebSocket（本地开发用） |

## 本地开发

当前机器的活跃工作区是：

```bash
cd /Volumes/OmubotDisk/omubot
source ./scripts/dev/env.sh
bash ./scripts/dev/doctor.sh
uv sync
docker compose up napcat -d
uv run python bot.py
```

旧路径 `$HOME/OmubotWorkspace/omubot` 与 `/Volumes/我的电脑/omubot` 不再作为正常开发工作区使用。

## 容器结构

```text
docker compose
├── napcat
│   └── QQ NT 协议 -> WebSocket 29301 / HTTP 29300
├── bot
│   ├── NoneBot2 -> OneBot V11 Adapter（NapCat 反连）
│   ├── FastAPI -> :8080（映射到宿主机 :8081）
│   └── omubot-storage + ./config + ./admin/static
├── ccip-sidecar
│   ├── /identify /identify-multi
│   └── /build-pack /build-series-pack /health
└── pmubot（可选）
    └── 多 bot / 运维控制平面
```

## 存储路径

```text
storage/
├── usage.db                   # LLM 用量
├── messages.db                # 群消息持久化
├── memory_cards.db            # 记忆卡片
├── knowledge_index.db         # 文档知识库持久索引
├── knowledge_graph.db         # 知识图谱事实与证据
├── character_recognition.db   # 角色注册表 + 识别缓存
├── slang.db                   # 群内黑话、候选、AI 复核、语义漂移、修订历史
├── style.db                   # 表达样本、反馈、动态风格档案
├── plugins/config/            # 插件 runtime override JSON
├── config/                    # 配置审计与快照
├── logs/                      # 日志（10 MB 切割，30 天保留）
├── stickers/
│   ├── stickers.db            # 表情包 SQLite 索引
│   └── stk_*.jpg|png|webp|gif # 表情包文件
├── affection/                 # 好感度
└── schedule/                  # 日程
```

角色样本包位于 `config/character_packs/*.charpack/`，属于 gitignored 运行时数据。

## 故障排查

1. `docker compose ps`：确认 `napcat`、`bot`、`ccip-sidecar` 处于运行状态。
2. `docker compose logs bot --tail=50`：查看 bot 错误日志。
3. `docker compose logs ccip-sidecar --tail=50`：查看角色识别 sidecar 是否健康。
4. NapCat WebUI（`:6099/webui`）：确认 QQ 是否在线。
5. `/admin/system` 与 `/admin/characters`：确认 Provider、sidecar、角色包与缓存状态。
6. `config/config.json` / `config/config.toml`：确认 LLM key、角色识别配置与群访问策略正确；JSON 优先，TOML 兼容读取。
