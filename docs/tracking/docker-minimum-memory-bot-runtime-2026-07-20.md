# Docker 最低内存 Bot 运行恢复（2026-07-20）

> 状态：completed
> mode: task
> 授权：用户明确要求“不管 NapCat，尽可能降低 Docker 到最低；Docker 只保证 Bot 流畅”。
> 当前下一步：无；本任务完成后立即终止。
> 阻塞：无。
> 回滚：将 `settings-store.json` 的 `MemoryMiB` 恢复为 5120，执行 `docker desktop restart`，再按需启动原容器。

## Objective / Acceptance

- Docker Desktop VM 上限降到保证 Omubot 核心消息链平稳的最低合理值：3072 MiB。
- 只运行 `napcat` 与 `qq-bot`；`ccip-sidecar`、PMUbot、socket proxies、watchtower、测试 NapCat 全部停止。
- 允许 Docker Desktop/NapCat 重启；不要求保留本轮 NapCat 会话连续性，但最终 Bot 应重新连接消息适配层。
- 复用现有 image/container/volume，不 build、不 recreate、不清理数据。
- Admin health 200、Bot running、connected_bots=1、PluginBus 无启动失败；无 OOM/restart loop。
- 记录 Docker/容器实际内存、宿主 memory pressure 与回滚路径。

## Baseline

- Host: 16 GiB；memory available 62%；swap 0。
- Docker Desktop: `MemoryMiB=5120`；VM process 约 3.56 GiB。
- Running before change: `napcat` 约 582 MiB、`ccip-sidecar` 约 322 MiB、`qq-bot` 约 166 MiB。
- Bot container hard limit: 2 GiB；NapCat/CCIP 无单容器 limit。
- Existing identities/images: `napcat=19f6cf13…/cde89d76…`，`qq-bot=e95c0b9b…/d89121d9…`，storage=`omubot-storage`。

## Decisions

- 3 GiB 而非 2 GiB：Bot hard limit 已为 2 GiB，仍需 NapCat 与 Linux VM/kernel/file-cache 余量；2 GiB 无法同时声称“流畅”。
- 停 CCIP：角色识别属于可降级视觉能力，不是核心消息链；本轮以最低内存优先。
- 保留 NapCat：用户不要求保护其会话，但 Bot 实际收发仍依赖 NapCat；因此允许其随 Docker 重启，最终仍恢复连接。
- 不改 Compose/source memory limit：本轮只改宿主 Docker VM cap 与运行服务集，避免将本机临时资源策略扩散到仓库默认部署。

## Plan

- [x] Freeze current identities/status and stop running containers.
- [x] Set Docker Desktop `MemoryMiB=3072` and restart via official CLI.
- [x] Start only `napcat` and `qq-bot`; keep all others stopped.
- [x] Verify Docker cap, memory footprint, Bot/Admin/OneBot, logs and restart/OOM state.
- [x] Update maintenance/ACTIVE and commit evidence.

## Test Ledger

| ID | Command / Evidence | Actual Result | Conclusion | Date |
| --- | --- | --- | --- | --- |
| M00 | Host/Docker baseline | 16 GiB；Docker 5120 MiB cap；VM 3.56 GiB；containers约 1.07 GiB；swap 0 | 5 GiB cap can collide with upcoming 8+ GiB host workloads | 2026-07-20 |
| M01 | Graceful container stop | `qq-bot`/CCIP exit 0；NapCat exit 137；all IDs/images/restart=0 preserved | Runtime quiesced before VM restart；user explicitly waived NapCat continuity | 2026-07-20 |
| M02 | `plutil -lint settings-store.json` | failed before write: `Unexpected character { at line 1`；`jq`/`file` prove valid JSON；MemoryMiB remained 5120 | Root cause is this `plutil` JSON compatibility path；do not retry | 2026-07-20 |
| M03 | Settings update + official restart | backup `.workspace/docker-settings-store.pre-3072-20260720.json` records 5120；one-line JSON patch sets 3072；`docker desktop restart` success；engine MemTotal 2.845 GiB | 3 GiB cap applied and rollback frozen | 2026-07-20 |
| M04 | Minimal running set | only `napcat` + `qq-bot` running；CCIP/PMUbot/proxies/watchtower/test NapCat exited；IDs/images unchanged；NapCat login retcode=0 | Core message chain preserved with no optional containers | 2026-07-20 |
| M05 | First Admin latency script | polled protected health before login and ended `health not ready`；startup logs already showed connected | Test harness ordering bug, not product failure | 2026-07-20 |
| M06 | Corrected runtime/latency/memory acceptance | login 200；health 20x median/p95/max 0.43/0.73/1.23 ms；connected_bots=1；services 11 ok / 2 warning / 0 error；plugins 0 error；Worldbook available；QZone locked；NapCat/Bot memory约 621.7/181.8 MiB；VM约 2.23 GiB；host 65% available、swap 0；OOM=false/restart=0 | 3 GiB is stable and is the lowest reasonable cap that still reserves Bot+NapCat peak headroom | 2026-07-20 |

## Final Outcome

- Docker Desktop `MemoryMiB`: `5120 → 3072`; engine usable memory约 2.845 GiB。
- Docker VM process:约 `3.56 → 2.23 GiB`，释放约 1.33 GiB 宿主内存。
- Running set precisely `napcat + qq-bot`; all optional/auxiliary/test containers stopped。
- Bot commit/image/container unchanged (`40a8e32` / `d89121d9…` / `e95c0b9b…`)；Admin/OneBot/PluginBus/Worldbook 正常。
- CCIP 角色识别端口关闭，图片角色识别为本轮明确接受的降级；核心视觉 LLM 与其他 Bot 功能仍由各自路径运行。
- Rollback: restore `.workspace/docker-settings-store.pre-3072-20260720.json` or set `MemoryMiB=5120`, then `docker desktop restart` and start required containers。
