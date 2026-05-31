# pmubot 框架最终方案（定稿）— 容器编排控制平面

> 状态：**最终方案（定稿）· 2026-05-31**
> 收口来源：[supervisor-control-plane-design](supervisor-control-plane-design-2026-05-31.md)（立项 + §A.1–A.7 三轮审计：MCDReforged 对标 → 四战线深审 → 多 bot 共享）。本文是该设计的**定稿浓缩 + 决策冻结**，执行派发见 [pmubot-dispatch-execution-2026-05-31.md](pmubot-dispatch-execution-2026-05-31.md)。
> 定位：pmubot = omubot 之上的**薄控制平面**（不是新应用框架）。带独立 web 界面，统一观测 + 编排 napcat / bot / ML sidecar，做 napcat 管理权的中间商，支持多 bot + 共享 sidecar。

## 0. 决策冻结表（三轮审计后的最终定论）

| # | 决策 | 定论 | 依据 |
| --- | --- | --- | --- |
| F1 | 形态 | **薄控制平面，非重框架**；首版形态 A（独立服务/容器，复用 FastAPI+Vue 栈），接口保持 control-plane 抽象以便升 fleet | §10 必要性论证；MCDReforged 式重框架被四战线否定 |
| F2 | docker 权限 | **docker-socket-proxy（Tecnativa,2.5k★）**，不裸挂 sock、不自写白名单 | §A.3 |
| F3 | 更新策略 | **写平面分档、绝不统一**：napcat=Diun 式只通知（人工值守）、bot=现有 CI、sidecar=Watchtower 可自动 | §10.1 + §12 + §A.4 |
| F4 | D6 红线 | napcat **物理上不开 recreate/down/换 tag**；`restart napcat` 保留但定级**高危**（实证会掉线重验证，需二次确认 + `ack_relogin` 守卫 + 提示查登录态）；换 tag 进「危险操作」区 | §4.2（CLAUDE.md D6 编码化 + 2026-05-31 实证订正） |
| F5 | napcat 中间商 | pmubot 代理 NapCat WebUI API（关 `#napcat`/查登录态/改网络），对 omubot 统一开放 | §5 + §A.2 |
| F6 | ML sidecar | CCIP 作首个外挂 sidecar（~80 行 FastAPI `/identify`，不上 Triton/BentoML）；推理无状态共享、relation 等 per-bot 状态留各 bot | §11 + §A.5 + §A.7 |
| F7 | 多 bot | 支持 N×{napcat+bot+独立storage} + 共享 sidecar 队列；napcat 必须 per-bot（设备指纹），无状态 sidecar 多 bot 共享 | §A.7 |
| F8 | 安全 | 控制接口独立鉴权（不与 admin token 同级）；写操作二次确认；凭证走 secret 不入 git | §4.1 + §4.3 |

## 1. 自研面（极小）vs 现成件（组装）

**pmubot 真正自研的只有 4 件**（领域特定，通用工具不提供）：
1. **读平面聚合视图**：一个 web 界面看 N×napcat + N×bot + sidecar 队列的状态/日志/版本/健康。
2. **napcat WebUI 配置代理**：把 NapCat 6099 的 API 封装成 pmubot 统一接口（关 `#napcat` 等）。
3. **D6 护栏**：napcat 物理不提供 recreate 入口；`restart napcat` 保留但高危（实证掉线重验证，二次确认 + ack_relogin）。
4. **bot 自愈编排**：感知无状态 sidecar 死 → 自动拉起（recreate 无害）；感知 napcat 掉线 → **告警人工**（restart napcat 会掉线重验证，不自动 restart）。

**其余全部组装现成件**：docker 权限→docker-socket-proxy；容器更新→Watchtower(sidecar)+Diun(napcat 通知)；ML serving→FastAPI；多 host fleet（未来）→迁 Komodo（Core+Periphery）而非自造。

## 2. 已核实的基础设施现实（落地前提）

| 项 | 实测（2026-05-31） | 落地含义 |
| --- | --- | --- |
| docker socket | `/var/run/docker.sock` → `~/.docker/run/docker.sock`，存在可挂 | docker-socket-proxy 挂得上 |
| 网络 | bot+napcat 同在 `omubot_default` bridge | pmubot 加入该网络 |
| NapCat WebUI | 宿主 `GET /webui/`→200（**活**）；token `24c611869429` | 中间商代理可行 |
| ⚠️ 容器间路由 | **bot→napcat:6099 = 502，宿主→napcat:6099 = 200** | **前置障碍**：pmubot 代理 WebUI 要走宿主网络或先修容器间路由 |
| ⚠️ WebUI API 契约 | WebUI 活但 `/api/auth/login` 返回 Cannot GET（路径/方法未摸清） | **前置**：落地前抓 NapCat v4.15 WebUI 实际 REST schema |
| 单例假设 | `container_name: qq-bot` 硬编码、`omubot-storage` 单 volume、`persona_id` 单值、napcat 配置按 QQ | 多 bot 要参数化部署层；`self_id` 已运行时化（无障碍） |
| 依赖 | numpy/onnxruntime/imgutils 全未装 | CCIP 外挂 → 重依赖锁在 sidecar，不污染 bot |

## 3. 目标架构

```text
        ┌────────────────────────────────────────────────────┐
        │  pmubot 控制平面（独立容器，FastAPI + Vue web 界面）  │
        │  读平面聚合 / napcat WebUI 代理 / D6 护栏 / 自愈编排  │
        │  控制接口独立鉴权 + 写操作二次确认                     │
        └──┬───────────────┬──────────────────┬───────────────┘
           │ docker API     │ NapCat WebUI      │ sidecar HTTP
           ▼（经 proxy）     ▼（6099+token）     ▼（/identify 等）
   ┌──────────────┐  ┌──────────────┐   ┌────────────────────────┐
   │docker-socket │  │ napcat_i WebUI│   │ 共享 ML sidecar 队列      │
   │-proxy(白名单) │  │ (per-bot)     │   │ ├ ccip（无状态,多bot共享） │
   └──────┬───────┘  └──────────────┘   │ └ stt（后续）             │
          │ 仅 CONTAINERS=1 + ALLOW_RESTARTS=1        └────────────┘
          ▼
   N×{ napcat_i(restart-only,D6) + bot_i(omubot) + storage_i(独立volume) }
                                  │ relation 等 per-bot 状态留各 storage_i
```

更新分档：napcat→Diun 只通知（exclude label）；bot→现有 CI；sidecar→Watchtower（enable label，接口版本化防一次更新打挂全部 bot）。

## 4. 分期（与派发文档 Wave 对应）

| Phase | 内容 | 价值 | 安全 |
| --- | --- | --- | --- |
| **P0 只读** | docker-socket-proxy(`POST=0` 全只读) + pmubot 骨架 + 读平面：容器状态/日志/版本 web 视图 | 看得见全栈 | 零写权限 |
| **P1 受限写** | 双 proxy（读 `CONTAINERS=1 POST=0` / 写 `ALLOW_RESTARTS=1 POST=1 CONTAINERS=0`）；restart bot（无害）+ restart napcat（**高危,ack_relogin 守卫**）+ napcat WebUI 代理（关 `#napcat`） | 能动手 + 中间商雏形 | 白名单 + 二次确认 + napcat restart 高危守卫 |
| **P2 更新接线** | sidecar 挂 Watchtower（label）；napcat 挂 Diun（只通知）；bot 更新封装（backup+回滚） | 更新可管（分档） | napcat 永不自动 |
| **P3 sidecar 编排** | pmubot 定义/拉起/健康检查 ML sidecar；CCIP 首发（FastAPI `/identify` + HF 缓存 volume）；omubot `character_recognizer` 切 HTTP client | ML 能力外挂 | 依赖隔离 |
| **P4 多 bot** | 部署层参数化（N×{napcat+bot+storage}）；共享 sidecar 接入多 bot；relation per-bot 隔离校验 | 多实例 + 共享推理 | napcat per-bot |

P0 可独立交付（纯只读、零风险）。P3 是 CCIP 推理落地的前置（pre-part0 v2 走外挂路线依赖此）。

## 5. 不做什么（范围护栏，冻结）

- ❌ 不重写应用框架（omubot 已有 kernel/services/plugins 三层）。
- ❌ 不给 napcat 提供 recreate/down 日常入口（D6）。
- ❌ 不做 napcat 自动镜像更新（反风控）。
- ❌ 不裸挂 docker.sock 给业务逻辑。
- ❌ 不自写 docker 白名单 / 不自写更新器 / 不上重型 serving 框架——全用现成件。
- ❌ 不把 relation 等 per-bot 状态焊进共享 sidecar。
- ❌ fleet 长大不自造 Komodo——直接迁移。

## 6. 风险与回滚

| 风险 | 缓解 |
| --- | --- |
| docker.sock = 宿主 root | docker-socket-proxy 白名单 + 内部网络 + 独立鉴权 |
| napcat 误 recreate 触发反风控 | D6 物理护栏：proxy 不开 recreate 相关 API |
| 共享 sidecar 一次更新打挂所有 bot | `/identify` 接口版本化 + sidecar 更新前契约兼容校验 |
| 多 bot 撞库 | 每 bot 独立 storage volume；relation per-bot |
| WebUI 容器间不通 | 走宿主网络 or 修路由（前置项，P1 前解决） |

**全局回滚**：pmubot 是独立容器，停掉即回到现状（omubot/napcat compose 不依赖 pmubot 运行）。docker-socket-proxy 可摘。各 Phase 路由 additive、可下线。

## 7. 落地前必须先解决的前置（派发 Wave 0 验证）

1. **NapCat v4.15 WebUI REST API 契约**——抓实际路径/方法/鉴权（`/api/auth/login` 当前 Cannot GET）。
2. **bot↔napcat 容器间 6099 路由**——实测 502，确认 pmubot 走宿主网络还是修 napcat 监听。
3. **docker-socket-proxy 在本机 Docker Desktop（macOS）的 socket 路径**适配（`~/.docker/run/docker.sock`）。
