# Supervisor 控制平面设计 — 统一编排 napcat / bot 基建层

> 状态：**立项设计（纯文档，未编码，待审）** · 2026-05-31
>
> 2026-05-31 更新：经「CCIP 内置 vs 外挂 + 更新管理」二轮评估，用户判断 **CCIP 不会是最后一个 ML sidecar**（后续语音 STT 等），bot 走向「主进程 + ML sidecar 队列」形态 → supervisor 的存在必要性成立，本文转为**前置准备**。新增 §10（必要性论证：为何「统一更新器」是伪命题）、§11（sidecar fleet 演进 + CCIP 作首个纳管 sidecar）、§12（多组件更新分档矩阵）。
>
> 来源：用户提出「omubot 只主管 bot 本身，够不到 napcat 等自治层，也管不了它们的更新；能否建一个 pmubot 框架层（类比 MCDReforged）统一统筹、并为 omubot 开放所有接口」。
>
> 本文是**架构设计 + 安全边界 + 接口清单**，不含实现。决策点用 ⚠️ 标注，待用户拍板后才进入编码立项（届时每条出 D3 四列迁移清单）。

---

## 0. 一句话结论（先给判断）

不建议新写一个「NoneBot 式应用框架」。要的是一个**编排/监督层（supervisor / control plane）**——它不替代 omubot，而是站在 napcat + bot **之上**，持有 omubot 自己够不到的两类能力：① 容器生命周期（docker），② napcat 客户端自治配置（NapCat WebUI）。然后把这两类能力**以受限、白名单化的接口**开放给 omubot admin。

MCDReforged 能统筹 MC server 是因为它是 server 的**父进程**（持有 stdin/stdout）。我们的 napcat/bot 是**对等容器**，omubot 不是 napcat 的父进程——所以「统筹」的实现路径不是写框架，而是**拿到 docker 编排权 + napcat WebUI API 权**。

---

## 1. 问题定义：omubot 当前的能力边界

### 1.1 能触及（走 OneBot v11 协议，已验证）

| 方向 | 能力 | 证据 |
| --- | --- | --- |
| 出站 bot→napcat | 发/撤消息、群管理（禁言/头衔/查成员/查群）、查消息 | `send_group_msg`/`set_group_ban`/`get_group_member_info`/`delete_msg` 等 12+ API 在用 |
| 入站 napcat→bot | message / notice(戳一戳/撤回) / request(加好友/进群) 事件 | router `on_message`/`on_notice`/`on_request` |

### 1.2 触及不到（协议之外的 napcat 自治层）

| 不可触及项 | 为什么 | 本轮实例 |
| --- | --- | --- |
| NapCat 内置插件行为 | 在 napcat 进程内、OneBot 协议之上自治 | `#napcat` 由 `napcat-plugin-builtin` 直接回版本信息，bot 看得到消息但拦不住它抢发 |
| NapCat WebUI 配置 | 6099 端口独立服务，非 OneBot 通道 | 关 `#napcat`、查登录态、改 OneBot 网络配置 |
| 容器生命周期 | docker 编排在容器之外 | 重启 napcat、换镜像 tag、查容器状态/日志 |
| 镜像更新 | docker 镜像管理 | napcat/bot 升级 = `docker compose` 人工操作 |

**核心缺口**：omubot 是「挂在 napcat OneBot 接口上的一个应用」，管不到宿主客户端的内部，也管不到自己和 napcat 的容器/镜像生命周期。

---

## 2. 对标：MCDReforged 给的启示（与差异）

| 维度 | MCDReforged | 本场景 |
| --- | --- | --- |
| 被管对象 | MC server（子进程） | napcat + bot（对等容器） |
| 控制手段 | 持有 server stdin/stdout，父进程直管 | 需借 docker API + napcat WebUI API |
| 插件热管理 | MCDR 自带插件系统 | omubot 已有 manifest v3 插件系统，不需要再造 |
| 我们要什么 | 「监督者 / control plane」之意 | 「编排 + 自治配置代理」，**不是**再来一个应用框架 |

**结论**：取 MCDReforged 的「监督者 / control plane」之意，**舍**其「应用框架 / 插件系统」之形（omubot 已有）。

---

## 3. 四种形态对比（2026-05-31 横向审计后修订）

| 形态 | 描述 | 适用规模 | 运维成本 | 评价 |
| --- | --- | --- | --- | --- |
| **A. admin 内嵌 supervisor 模块** | 现有 admin 后台加一个基建控制层，复用 SPA | 单 bot+单 napcat（当前） | 最低 | **推荐起步**，对标 Dockge 的「轻」 |
| **B. 独立 pmubot 容器** | 专职编排容器，bot/napcat/sidecar 为被管对象，对 omubot 暴露 REST | 多 bot / 多 sidecar | 中（多一进程） | sidecar 队列长大时升级 |
| **C. 纯现成工具** | Portainer / Watchtower / Dozzle 直接管 | 任意 | 零开发 | 不与 omubot 打通，只「人来管」，不满足「为 omubot 开放接口」 |
| **D. 用 Komodo/Dockge 托管 + omubot 调其 API**（审计新增） | 容器编排外包给成熟件，supervisor 退为薄领域适配 | 单宿主(Dockge)→多宿主(Komodo) | 低-中 | **fleet 阶段的目标范本**——不自造编排，迁移到 Komodo Core+Periphery |

**关键修正（横向审计，详见附录 A.2）**：原方案在 A/B/C 三选一里**漏了形态 D**。社区三档（[Dockge/Portainer/Komodo](https://www.rafspiny.eu/blog/portainer-vs-dockge-vs-komodo)）显示：omubot 现状 = **Dockge 的适用场景**（单宿主 compose），首版 supervisor 应当对标 Dockge 的轻，而非 Portainer 的全。

**为何仍自建薄 supervisor、而非直接上 Komodo**：omubot 有 Dockge/Komodo 都不提供的 **3 条领域特定诉求**——① napcat `restart-only` 的 D6 反风控护栏；② napcat WebUI 配置代理（关 `#napcat` 等）；③ bot 自愈（感知 napcat/sidecar 掉线自动拉起）。这 3 条是自建的**唯一正当理由**（非 NIH）；其余通用编排能力不该重造。

**演进路径（修订）**：形态 A 起步（对标 Dockge）→ 接口保持 control-plane 抽象 → sidecar 队列长大时，**不要把 supervisor 自己写成 Komodo**，而是迁移到 Komodo（Core+Periphery 成熟范本），supervisor 退化为挂在其上的「omubot 业务编排 + D6 护栏」薄层。A 与 B 接口契约一致，差别只在进程边界。

---

## 4. 安全边界（本设计的重心 ⚠️）

这是把「能管」变「敢开」的前提。三条硬约束：

### 4.1 docker.sock = 宿主 root 等价权限 ⚠️

把 `/var/run/docker.sock` 挂进任何接公网/接 LLM 工具循环的进程，等于该进程被攻破即拿宿主 root。bot 进程**正是高暴露面**（吃 QQ 群消息 + 跑 LLM tool loop）。

**docker 权限给法（2026-05-31 审计后定向：用 docker-socket-proxy，非自写）**：

横向审计发现这是已被解决的问题，有 2.5k★ 生产验证件 [Tecnativa/docker-socket-proxy](https://github.com/Tecnativa/docker-socket-proxy)（详见附录 A.3）。**结论：bot/supervisor 不挂裸 `docker.sock`，改连 docker-socket-proxy 的内部端口。**

| 方案 | 机制 | 安全性 | 成本 | 取舍 |
| --- | --- | --- | --- | --- |
| **docker-socket-proxy（选定）** | proxy 容器按 per-API-section 环境变量授权，`HTTP 403` 拒越权；omubot 配 `CONTAINERS=1`+`ALLOW_RESTARTS=1`，`POST`/`EXEC`/`VOLUMES`/`SECRETS` 默认 `0` | 高 | **低**（配环境变量，非自写） | ✅ 站在生产验证件上，白名单不手搓 |
| 自写白名单封装 | supervisor 自己实现动作白名单 | 高 | 中（重复造轮子） | ✗ 被上一行取代 |
| 裸挂 docker.sock | 容器内直接 docker SDK，任意 API | 低（root 等价） | 低 | ✗ bot 是高暴露面，禁用 |
| 宿主 agent 中转 | docker 操作放宿主小 agent | 最高 | 高（多一宿主进程） | 仅极端隔离需求时 |

**docker-socket-proxy 关键能力**（README 抽取）：默认仅放行 `EVENTS/PING/VERSION`；`POST=0` 即全 API 只读；动作级 `ALLOW_RESTARTS=1` 只放行 restart/stop/kill。omubot 诉求（查状态 + restart napcat/bot）能精确表达，napcat 的 recreate 入口天然不暴露（`IMAGES`/recreate 不开）。proxy 端口只在内部 docker network、绝不公网。

无论哪种，**docker 控制接口必须独立鉴权**，不与普通 admin token 同级；写操作需二次确认。

### 4.2 D6 反风控红线 ⚠️（不可协商）

CLAUDE.md 写死：**napcat 永远 `restart`，绝不 `down`/recreate**（device fingerprint 反欺诈，recreate = 重新扫码登录 = 触发风控）。

> ⚠️ **2026-05-31 实证订正**：原以为 `restart napcat` 是「无害的安全替代」、只有 recreate 才掉线——**错误**。执行 pmubot P1 时实测：`docker compose restart napcat` 后 NapCat **掉回二维码/手Q验证**（OneBot WS 断、bot disconnected），可能自愈也可能要人工扫码。**即 `restart napcat` 本身就是高危操作，不是安全档**。CLAUDE.md「napcat 永远 restart 不 down」的本意是「restart 比 recreate 危害小」，但 restart 仍非无害——device fingerprint 虽不变（不像 recreate），但**会话需重新验证**。

supervisor 层必须把这条人肉纪律**固化成代码约束**：
- napcat **物理上不提供** `down`/`recreate`/`rm` 入口（recreate = 换设备指纹 = 必触发反风控，绝禁）。
- **`restart napcat` 保留但定级为高危**（用户 2026-05-31 决定）：需二次确认 + **额外 `ack_relogin` 守卫**（明示「会掉线、可能需手机扫码恢复」）+ 调用后提示检查登录态 + 记审计 + 默认 UI 不放常用区。**不再宣称 restart napcat 无害**。
- 换 napcat 镜像 tag（= 必然 recreate）= **最高危**：二次强确认 + 明示反风控 + 不在日常 UI 默认可达。
- bot 容器无此约束（restart/recreate/rebuild 均无害，可自由）。

### 4.3 凭证管理

NapCat WebUI token（现 `24c611869429`，明文在 `napcat/config/webui.json`）、docker 凭证——supervisor 读取这些必须走 env/secret，不硬编码、不入 git、不回显到日志/前端。

---

## 5. 接口清单（control-plane API，A/B 共用契约）

挂在现有 admin 体系下（`admin/routes/api/supervisor.py` 新增），前端复用「系统 / 协议连接」页（`SystemView` / `protocol.py` 已有挂靠点）。所有写操作独立鉴权 + 审计。

### 5.1 只读（最安全，第一优先）

| 端点 | 作用 | 数据源 |
| --- | --- | --- |
| `GET /supervisor/containers` | napcat/bot 运行状态、uptime、镜像 tag、健康 | docker inspect |
| `GET /supervisor/logs/{service}` | 容器近 N 行日志（脱敏） | docker logs |
| `GET /supervisor/napcat/status` | napcat 登录态、OneBot 连接、版本 | NapCat WebUI API |

### 5.2 受限写（白名单动作）

| 端点 | 作用 | 约束 |
| --- | --- | --- |
| `POST /supervisor/napcat/restart` | 重启 napcat | **高危**（实证会掉线重验证，见 §4.2）：仅 restart（禁 down/recreate）+ 二次确认 + `ack_relogin` 守卫 + 调用后提示检查登录态 |
| `POST /supervisor/bot/restart` | 重启 bot | 无 D6 约束（restart bot 无害） |
| `POST /supervisor/napcat/config` | 改 napcat 自治配置（如关 `#napcat` enableReply、改 OneBot 网络） | 经 NapCat WebUI API；改完按需 restart napcat |

### 5.3 高危写（镜像更新 / recreate，用户要求纳入设计 ⚠️）

镜像更新是「无法管理它们更新」的正面回应，但也是最高危的一类，设计上**强隔离**：

| 端点 | 作用 | 强约束 |
| --- | --- | --- |
| `POST /supervisor/image/check` | 查 napcat/bot 是否有新 tag（只读探测） | 安全，可常开 |
| `POST /supervisor/bot/update` | 拉新 bot 镜像 + recreate bot | bot 可 recreate；先备份、灰度可回滚 |
| `POST /supervisor/napcat/update` | 换 napcat 镜像 tag | **最高危**：二次强确认 + 明示「recreate=重新扫码+反风控」+ 审计 + 默认 UI 不可达（需显式进入「危险操作」区）。**首版建议不实现，留命令行人工**（见 §7） |

**镜像更新设计要点（2026-05-31 审计后：执行外包给现成件，supervisor 不自研更新器）**：
- **更新分两种哲学**（[Watchtower vs Diun](https://sumguy.com/watchtower-vs-diun/)，详见附录 A.4）：Watchtower 自动 pull+recreate；Diun 只检测+通知不动容器。两者都支持 per-container label 选择性纳管。
- **napcat = Diun 式只通知**：device fingerprint 风险，低频需人值守。打 exclude label，绝不自动 recreate。`/supervisor/image/check` 对 napcat 只做只读探测 + 通知管理员。
- **bot / ML sidecar = 可走 Watchtower**：sidecar 无状态无 D6，打 watchtower-enable label 可自动更新；bot 更新走现有 `git + docker compose up bot --build` 程序化封装（backup DB + 记 git tag + 失败回滚）。
- → **supervisor 不造更新器**：sidecar 自动更新挂 Watchtower（label 选择）、napcat 用 Diun 式只通知。supervisor 自身只做「读平面聚合 + napcat restart 护栏」。
- 版本对齐纪律（CLAUDE.md「Release」：镜像 tag 与 git tag 对齐）应由 supervisor 校验。

### 5.4 为 omubot 自身开放（「为 omubot 开放所有接口」的落点）

omubot 的 LLM tool loop / dream agent / admin 可调用上述 control-plane API（经内部受限通道），实现「检测到新版本 → 通知管理员」等自治闭环。**但**：写/高危操作即便对 omubot 内部也要走同一套白名单 + 审计，不因「自己人」放宽（bot 是高暴露面，见 §4.1）。**特别地，「bot 感知 napcat 掉线 → 自动 restart napcat」这条自愈不能做成全自动**——restart napcat 实证会掉线重验证（§4.2），自动重启可能把一次临时抖动放大成需人工扫码的中断；napcat 掉线应**告警通知人工**，不自动 restart。

## 6. 架构草图

```text
                ┌─────────────────────────────────────────────┐
                │  Supervisor 控制平面 (A: admin内嵌 / B: 独立容器) │
                │  ┌─────────────┐   ┌──────────────────────┐  │
                │  │ docker 适配  │   │ NapCat WebUI 适配      │  │
                │  │ (白名单动作)  │   │ (6099 + token)        │  │
                │  └──────┬──────┘   └──────────┬───────────┘  │
                │         │ 白名单+审计+独立鉴权      │             │
                └─────────┼─────────────────────┼─────────────┘
                          │                     │
              docker API  │                     │ HTTP (napcat:6099)
                          ▼                     ▼
        ┌──────────────────────┐     ┌──────────────────────┐
        │ napcat 容器           │     │ napcat WebUI / 内置插件 │
        │ (restart-only, D6)    │     │ (#napcat enableReply…) │
        └──────────┬───────────┘     └──────────────────────┘
                   │ OneBot v11 WS
                   ▼
        ┌──────────────────────┐
        │ bot 容器 (omubot)      │── admin SPA 调 supervisor 接口
        └──────────────────────┘
```

可达性已验证：bot 容器 → `napcat:6099` 网络通（探测得 HTTP 502，WebUI 在监听；实际 REST 路径/鉴权待实现阶段按 napcat 版本核实）。bot 容器当前**无 docker CLI、未挂 docker.sock**（§4.1 的权限给法是新增项）。

## 7. 待用户决策（编码立项前）

| # | 决策 | 选项 | 倾向 |
| --- | --- | --- | --- |
| D-1 | docker 权限给法 | 白名单封装 / 裸挂 sock / 宿主 agent | **docker-socket-proxy（附录 A.3 更新）**——白名单封装用现成件实现，非自写 |
| D-2 | 镜像更新/recreate 是否进首版 | 进 / 不进（留命令行人工） | 不进首版——napcat 换镜像高危低频、需人值守；首版只做「检查新版本（只读）」 |
| D-3 | 形态 A 还是 B | admin 内嵌 / 独立容器 | A 起步，接口契约保持 control-plane 抽象以便升 B |
| D-4 | NapCat WebUI 配置代理首个用例 | 关 `#napcat` / 查登录态 / 改网络 | 先做「关 `#napcat`」验证链路（直接回应本轮问题） |

## 8. 建议分期落地（待 §7 定后各自出 D3 清单）

- **Phase 0（只读）**：`GET containers/logs/napcat-status`，经 docker-socket-proxy（`POST=0` 全只读）。零写权限，先把「看得见 napcat/bot 状态」做出来，无安全风险。
- **Phase 1（受限写）**：`restart napcat`（docker-socket-proxy `ALLOW_RESTARTS=1`、守 D6）+ NapCat WebUI 配置代理（关 `#napcat`）。
- **Phase 2（按需）**：bot 镜像程序化更新（backup+回滚）；sidecar 自动更新挂 Watchtower（label 选择）。
- **Phase 3（暂不做）**：napcat 镜像更新——Diun 式只通知，更新永远人工 + 值守。
- **Phase 4（sidecar 编排，fleet 起点）**：supervisor 能定义/拉起/健康检查一个 ML sidecar（CCIP 首发）；CCIP 容器 + HF 缓存 volume + `/identify` 契约；omubot 侧 `character_recognizer` 从进程内 ONNX 切 HTTP client（详见 §11）。
- **Phase 5（按需）**：ptt2text 等后续 sidecar 复用 Phase 4 编排骨架。

**自研面极小（审计净判断）**：Phase 0-5 里真正要自研的只有「读平面聚合 + napcat restart 护栏 + WebUI 代理 + bot 自愈 + sidecar 编排薄层」；docker 权限(docker-socket-proxy)/容器更新(Watchtower+Diun)/ML serving(FastAPI)全是组装现成件。

## 9. 不做什么（范围护栏）

- 不重写应用框架（omubot 已有 kernel/services/plugins 三层 + manifest v3）。
- 不给 napcat 提供 down/recreate 的日常入口（D6）。
- 不做 napcat 自动镜像更新（反风控）。
- 不把裸 docker API 暴露给业务逻辑（§4.1）。

## 10. 必要性论证（2026-05-31 二轮评估结论）

立项前必须先答：supervisor 到底有没有存在必要，还是 docker compose + 脚本就够？结论：**重量级「框架」无必要；薄 supervisor 的必要性是条件性的，且驱动力不是「统一更新」——「统一更新」是伪命题。**

### 10.1 「统一更新器」是伪命题（关键洞察）

把三个组件的更新画像摊开，它们的**更新安全档根本不兼容**，无法统一对待：

| 组件 | 更新性质 | 频率 | 风险 | 能否自动化 |
| --- | --- | --- | --- | --- |
| **napcat** | 换镜像 tag = recreate = **重新扫码 + 触发反风控（D6）** | 极低 | 极高 | ❌ 永远人工值守 |
| **bot** | git + rebuild（已有 deploy 流程） | 高 | 中（可回滚） | ✅ 现有 CI |
| **CCIP（外挂后）** | 升 imgutils / 换模型，独立容器 recreate（无 D6） | imgutils 月更 | 低 | ✅ 可自动（Watchtower 式） |

一个把三者**统一对待**的「更新框架」必然错：要么用 napcat 的谨慎度去约束本可随便更新的 CCIP，要么给 napcat 开一个它**永远不该有**的更新按钮（把 D6 人肉纪律变成易误点的危险操作）。

**正解分平面：**
- **读平面（观测：状态 / 日志 / 版本）→ 应统一**：三者一套视图看，有价值、无害。
- **写平面（更新动作）→ 绝不统一**：napcat 永远人工 + 护栏、CCIP 可自动、bot 走现有 CI。各按自己的安全档（见 §12 矩阵）。

### 10.2 单论「更新」不构成框架必要性

- napcat 更新：低频 + 人工 + 高危 → **一份带护栏的 checklist 比框架更安全**；护栏 = 把 D6 写成「只暴露 restart」的 wrapper，不需要框架。
- CCIP 更新：外挂后 = `docker compose up ccip --build` 一行或挂 Watchtower → **用不上框架**。
- bot 更新：已有 deploy 流程 → **已解决**。

→ 三者各自的更新解都很轻（脚本 / checklist / 现有流程）。**更新轴本身 → 零框架必要性。**

### 10.3 真正构成必要性的三件事（非更新）

1. **D6 编码为护栏**：现在「napcat 只能 restart」全靠人记住，一次手滑 `down`/换 tag 就触发反风控。supervisor 能把它物理焊死（napcat 不暴露 recreate 入口）。价值真实但薄——一个受限 restart wrapper 即可。
2. **bot 自愈 / 够到自己的基建**：感知 sidecar 死→拉起（无状态 sidecar recreate 无害）、感知 napcat 掉线→**告警人工**（注意：restart napcat 实证会掉线重验证 §4.2，**不做自动 restart**，只告警）。**这条是 docker-compose 给不了的**（compose 声明式，无「运行时按条件触发动作」）——是 supervisor 不可替代的核心价值。
3. **组件数量轨迹（决定性变量）**：2 个组件（napcat+bot）compose + 手管足够；3 个（+CCIP）是临界点；**若后续还有语音 STT 等 ML sidecar → 走向 fleet，统一观测 + 自愈的 ROI 才显现。**

### 10.4 结论

- ❌ **重量级 MCDReforged 式应用框架**：为 3 个 docker 容器过度工程，compose 已是编排器，不建。
- ✅ **薄 supervisor（本文 control plane）**：**因用户判断 CCIP 非最后一个 sidecar、bot 走向「主进程 + ML sidecar 队列」**，必要性成立。但其价值定位是 **观测统一 + bot 自愈 + D6 护栏 + 多 sidecar 编排**，**不是**「统一更新器」。

## 11. sidecar fleet 演进 + CCIP 作首个纳管 sidecar

### 11.1 目标形态

```text
              ┌─────────────────────────────┐
              │  supervisor (control plane)  │  观测统一 / 自愈 / D6 护栏 / 编排
              └──┬──────────┬──────────┬─────┘
                 │          │          │
   restart-only  │   recreate-ok       │ recreate-ok（无 D6）
   + 人工护栏     │   现有 CI            │ 可自动更新
                 ▼          ▼          ▼
            ┌────────┐ ┌────────┐ ┌──────────────────────┐
            │ napcat │ │  bot   │ │ ML sidecar 队列         │
            │        │ │(omubot)│ │ ├ ccip（角色识别）首个   │
            └────────┘ └────────┘ │ ├ ptt2text（语音STT）后续│
                                  │ └ …                    │
                                  └──────────────────────┘
```

### 11.2 CCIP 作首个纳管 sidecar 的理由（最干净的试验田）

- **无 D6 约束**：CCIP 容器可随意 recreate / 换镜像 / 自动更新——不像 napcat 那样碰不得。是 supervisor「容器更新」能力的安全练手对象。
- **依赖隔离即收益**：CCIP 的 numpy<2 + onnxruntime + opencv（~300MB）锁在自己容器，**不污染 bot 镜像、不把 bot 钉死在 numpy<2**（这正是「内置难同步」的根因，见 [issue17-pre-part0-landing-v2](issue17-pre-part0-landing-v2-2026-05-31.md) §2/§8）。
- **更新同步从第一天就干净**：升 imgutils 只重建 CCIP 小容器，bot 不动；模型权重换 = 改 model_name + 拉新权重（需给 CCIP 容器挂 HF 缓存 named volume，避免每次 recreate 重下 ~100MB）。
- **接口契约**：CCIP 暴露 `POST /identify`（图 bytes → character + confidence + source）；omubot `services/media/character_recognizer.py` 从「进程内 ONNX」改为「HTTP client」，调用方（router 识别块）不变——v2 方案的 `character_recognizer` 已设计为独立 class，抽离是机械重构。

### 11.3 与 issue17 pre-part0 的时序关系（关键）

- pre-part0 v2 方案 §9 的 Phase 1a 是「纯地基（缓存层 + flag，识别返回 None）」——**可在 supervisor 落地前先做**（零行为变更、不依赖 sidecar）。
- CCIP 真正推理（Phase 1b）**外挂为 supervisor 纳管的 sidecar**——依赖 supervisor 至少能跑一个业务 sidecar（Phase 1 受限写 + 容器拉起能力）。
- **即 supervisor 是 CCIP 推理落地的前置**。pre-part0 v2 的「先内置后抽离」路线 A 作废，改走「直接外挂 + supervisor 纳管」路线 B——用户已认可「CCIP 等 supervisor」以换取无返工 + 无 numpy 污染 + 更新干净。

### 11.4 分期落地

sidecar 编排（Phase 4 CCIP 首发 / Phase 5 ptt2text 等）已合并进统一分期表 §8，不再单列。

## 12. 多组件更新分档矩阵（写平面绝不统一）

supervisor 实现「更新」相关能力时，**严格按组件分档**，不提供「一键更新全部」：

| 组件 | supervisor 暴露的更新能力 | 禁止 | 自动化（执行外包，非自研） |
| --- | --- | --- | --- |
| napcat | 仅 `restart`；「检查新版本」只读探测 | ❌ 无 recreate/换 tag 日常入口；换 tag 需显式进「危险操作」区 + 二次确认 + 明示反风控 | ❌ 禁自动；**Diun 式只通知**（exclude label） |
| bot | 走现有 deploy（git + rebuild）；supervisor 只读状态 | — | 现有 CI |
| CCIP / ML sidecar | `recreate` / 换镜像 / 重建——常规操作 | — | ✅ **Watchtower**（watchtower-enable label，sidecar 专属） |

**读平面统一**：`GET /supervisor/containers` 一视图列出三者状态 / uptime / 镜像 tag / 健康 / 有无新版本——观测统一，动作分档。

## 13. 影响 & 回滚

仅设计文档，无代码/配置改动。编码立项前不影响运行态。各 Phase 实现时：docker 权限是增量挂载（可摘）、supervisor 路由是 additive（可下线）、napcat WebUI 代理是只读优先。

---

## 附录 A — 横向同类项目深度审计（2026-05-31，超越单点 MCDReforged 对标）

> 前序评估只对标了 MCDReforged 一个点。本附录拉四条成熟/前沿生态横向对比，是正文 §3/§4.1/§5.3/§8/§11/§12 各项决策的**证据与推导来源**——那些章节已按本附录结论就地更新（不再是"待修正"，正文即新版）。方法：Web 检索 + 关键项目 README/文档抽取（docker-socket-proxy README、Komodo 文档、Watchtower/Diun 对比、LitServe/BentoML serving）。

### A.1 四条对标战线

| 战线 | 代表项目 | 对应 supervisor 的哪块 |
| --- | --- | --- |
| 自托管容器控制平面 | Portainer / Dockge / **Komodo** | §3 形态、§11 fleet 演进 |
| docker 权限最小化 | **Tecnativa/docker-socket-proxy**（2.5k★）/ wollomatic/socket-proxy | §4.1 docker 权限给法（D-1） |
| 容器自动更新哲学 | **Watchtower**（自动）vs **Diun**（只通知） | §12 更新分档矩阵 |
| ML 推理 sidecar 服务化 | **LitServe** / BentoML / Triton | §11 CCIP 外挂为 sidecar |

### A.2 战线一：容器控制平面三档（Portainer / Dockge / Komodo）

社区结论（[cavecreekcoffee 2026 评测](https://cavecreekcoffee.com/reviews/best-linux-container-management-tools-2026/) / [rafspiny 对比](https://www.rafspiny.eu/blog/portainer-vs-dockge-vs-komodo)）三档分明：

| 项目 | 架构 | 真相源 | 定位 | 对 omubot 的镜鉴 |
| --- | --- | --- | --- | --- |
| **Dockge** | 单进程、compose-native、轻 | compose 文件即真相源 | 单宿主 + compose 栈 | **最贴近 omubot 现状**——已经是 compose 单宿主，supervisor 不该比 Dockge 重 |
| **Portainer** | agent-based、DB 存配置、RBAC/SSO 付费墙 | 内部 DB | 多宿主/企业团队 | 过重；DB 存配置 = 与 compose 真相源割裂，omubot 不需要 |
| **Komodo** | **Core + 每宿主 Periphery agent**，TOML resource-sync（GitOps），细粒度 RBAC | TOML/git（声明式 diff，手动确认或 webhook 自动执行） | 多服务器 fleet，无付费墙 | **fleet 阶段的目标范本**——若 sidecar 队列长大，Komodo 的 Core/agent 分离 + 声明式 sync 是正解 |

**修正前文 §3**：原文档"形态 A(admin内嵌) / B(独立容器) / C(现成工具)"三选一里，**漏了"直接用 Komodo/Dockge 托管、omubot 只调它们的 API"这第四种**。重新判断：
- omubot 现状 = Dockge 的适用场景（单宿主 compose）。**首版 supervisor 应当对标 Dockge 的"轻"——而非 Portainer 的"全"**。前文倾向"形态 A 起步"与此一致，得到外部印证。
- **但 omubot 有 Dockge/Komodo 都没有的诉求**：`napcat 永远 restart-only`（D6 反风控）+ `napcat WebUI 配置代理`（关 #napcat）+ `bot 自愈`。这三条是**领域特定**的，通用工具不提供 → 这正是"不直接用 Komodo、要自建薄 supervisor"的**站得住的理由**（而非 NIH）。
- **fleet 长大后的退路**：若 sidecar 队列扩张到多宿主，不要自己把 supervisor 写成 Komodo，而应**直接迁移到 Komodo**（Core+Periphery 已是成熟范本），supervisor 退化为"omubot 业务编排 + D6 护栏"的薄适配层挂在 Komodo 之上。

### A.3 战线二：docker 权限最小化 — 直接解决 D-1（关键收获）

前文 §4.1 把 docker 权限给法列为「白名单封装 / 裸挂 sock / 宿主 agent」三选一待决。**横向审计发现这是个已被解决的问题，有成熟现成件**：[Tecnativa/docker-socket-proxy](https://github.com/Tecnativa/docker-socket-proxy)（2.5k★，HAProxy over docker socket，2025-12 仍活跃）。

机制（从 README 抽取）：在「服务 ↔ 真 docker socket」之间插一个 proxy 容器，按 **per-API-section 环境变量**授权，`HTTP 403` 拒绝越权请求：
- **默认拒绝**：除 `EVENTS`/`PING`/`VERSION` 外全部 `0`。
- **`POST=0` 即全 API 只读**（只放行 GET/HEAD）——这一条直接实现「读平面」。
- **细到动作级**：`ALLOW_RESTARTS=1` 只放行 `restart/stop/kill`，`ALLOW_START`/`ALLOW_STOP` 独立，`CONTAINERS=1` 放行查容器，`EXEC`/`VOLUMES`/`SECRETS`/`AUTH` 保持 `0`。

**对 D-1 的决定性影响——supervisor 的 docker 权限不用自己写白名单逻辑**：
- bot/supervisor **不挂裸 `docker.sock`**，改连 docker-socket-proxy 的 `2375`。
- omubot 这套诉求恰好能精确表达：`CONTAINERS=1`（查状态）+ `ALLOW_RESTARTS=1`（restart napcat/bot）+ `POST` 视需要 + `EXEC=0`/`VOLUMES=0`/`IMAGES` 仅 sidecar 更新阶段开。
- **§4.1「白名单封装」从"自己实现"降级为"配 docker-socket-proxy 环境变量 + 网络隔离"** —— 工作量骤降，且站在 2.5k★ 生产验证件上，不是手搓。
- 安全边界仍成立：proxy 端口只在内部 docker network、绝不公网（README 安全建议）。

→ **D-1 倾向更新为：用 docker-socket-proxy 做白名单层**（而非裸挂 / 自写 agent / 宿主 agent）。这是本次审计最实用的一条。

### A.4 战线三：自动更新哲学 — Watchtower vs Diun 印证「分档」

[Watchtower vs Diun 对比](https://sumguy.com/watchtower-vs-diun/) 揭示两种相反哲学，恰好对应 §12 矩阵的两端：
- **Watchtower**：检测到新镜像→自动 pull→recreate 容器。省心但**危险**（社区共识：auto-update 会无预警重建栈）。
- **Diun**：只检测 + 通知，**不动容器**。人决定何时更。

**印证 §12「写平面绝不统一」**：
- **napcat** = Diun 模式（**只通知有新版本，绝不自动 recreate**——D6 反风控，auto-update 会触发重新扫码）。
- **CCIP/ML sidecar** = Watchtower 模式可接受（无状态、无 D6，自动 recreate 无害）。
- 关键能力点：Watchtower/Diun 都支持 **per-container label 选择性纳管**（`com.centurylinklabs.watchtower.enable=false` 排除）。→ supervisor 不必自造更新器：**给 sidecar 打 watchtower-enable label 挂 Watchtower，napcat 打 exclude label**，更新分档用现成件 + label 实现，零自研。

→ **修正 §5.3/§12**：「镜像更新」能力对 sidecar 可直接复用 Watchtower（label 选择性），napcat 用 Diun 式只通知。supervisor 自身只需做"读平面聚合 + napcat restart 护栏"，更新执行外包给成熟件。

### A.5 战线四：ML 推理 sidecar 服务化 — 印证外挂方向 + 选型

[LitServe](https://github.com/Lightning-AI/LitServe)（Lightning AI，FastAPI-based 推理服务）/ BentoML / Triton 是 ML-model-as-sidecar 的成熟范式。[K8s AI 架构](https://markaicode.com/architecture/kubernetes-system-design-architecture-1136/) 给的经验法则：**推理服务典型拆成 inference-runner / preprocessor / postprocessor / cache / rate-limiter 五件；模型 < 7B 或轻量 CV 模型，进程内或单 sidecar 即可，不必过度拆分**。

对 CCIP（轻量 CV、ONNX、~150MB）的镜鉴：
- **印证 §11 外挂方向**：CCIP 作独立 sidecar 是业界标准做法，不是 omubot 异类。依赖隔离（numpy<2 锁在 sidecar）是 serving 框架存在的核心理由之一。
- **选型不必上 LitServe/BentoML/Triton**：那些是为 GPU 批处理/高吞吐 LLM 设计，CCIP 是低频 CV 推理 + 持久化缓存命中后几乎不调用。**一个 ~80 行 FastAPI `POST /identify` 足矣**（omubot 本就 FastAPI 栈，零新框架）。Triton/BentoML 对 CCIP 是过度工程。
- **保留的有用模式**：serving 框架的「缓存 + rate-limiter」前置——CCIP sidecar 应自带识别缓存（v2 方案的 recognition_cache 正好放 sidecar 内），与 omubot 主进程解耦。

### A.6 审计结论：四条修正（已落入正文）+ 净判断

**四条修正——均已就地覆盖进对应正文章节，此处列为索引**：
1. **§3 形态** ✅已更新——补「形态 D：直接用 Komodo/Dockge」第四选项；首版对标 Dockge 的轻，fleet 长大迁 Komodo（不自造）；自建薄 supervisor 的理由收敛为「D6 护栏 + napcat WebUI 代理 + bot 自愈」三条领域特定诉求。
2. **§4.1 docker 权限** ✅已更新——从"自写白名单"改为"docker-socket-proxy（2.5k★）配环境变量"，D-1 决策行同步。
3. **§5.3 + §12 镜像更新** ✅已更新——sidecar 复用 Watchtower（label 选择）、napcat 用 Diun 式只通知，更新执行外包不自研。
4. **§11 CCIP serving** ✅外挂方向被业界标准印证；选型保持轻（~80 行 FastAPI，不上 Triton/BentoML/LitServe）。

**净判断（横向对比后比单点 MCDReforged 更有底气）**：supervisor 的**自研面应当极小**——它真正独有的只有「D6 护栏 + napcat WebUI 配置代理 + bot 自愈编排 + 读平面聚合视图」。其余三块（docker 权限、容器更新、ML serving）**全有成熟现成件可组装**（docker-socket-proxy / Watchtower+Diun / FastAPI）。这把 supervisor 从"要不要造一个框架"彻底拉回到"组装现成件 + 写一层薄领域适配"——**MCDReforged 式重框架被四条战线一致否定**，前文 §11.4「薄 supervisor」结论得到横向加固。

### A.7 多 bot + 共享 sidecar 可行性审计（2026-05-31 追加）

诉求：supervisor 管多个 bot 实例，多 bot **同时共用** ccip/stt 等 sidecar。核查当前架构哪些是单例假设、哪些天然支持多实例。

**核查结论：架构基本支持，但有 3 层单例假设要参数化，且 sidecar 共享要分清「可共享 vs 必须 per-bot」两类状态。**

#### A.7.1 当前的单例假设（实测）

| 层 | 现状 | 多 bot 障碍 | 解法 |
| --- | --- | --- | --- |
| bot `self_id` | **运行时从 napcat 连接拿**（`scheduler.py:335` `getattr(bot,"self_id")`），非写死 | ✅ 无障碍——代码已按运行时 self_id 工作 | 天然支持 |
| `container_name: qq-bot` | 硬编码单名（compose） | ⚠️ 第二个 bot 撞名 | compose 参数化 / 每 bot 一个 service |
| `omubot-storage` 单 named volume | 所有 `.db` 平铺无 per-bot 前缀 | 🔴 多 bot 共用会**撞库**（messages/memory/affection 等全混） | 每 bot 独立 storage volume |
| `persona_v2.persona_id` 单值 | config.json 单一 `fengxiaomeng-v2` | ⚠️ 每 bot 不同人设 | 每 bot 独立 config |
| napcat 配置按 QQ keyed | `napcat_384801062.json` 设备指纹绑 QQ | 🔴 **每 bot 必须独立 napcat 实例**（D6：设备指纹不可共享） | 每 bot 一套 napcat 容器 + config |

→ **bot 是「软单例」**：核心代码（self_id 运行时化）已支持多实例，障碍全在**部署层**（容器名 / storage volume / config / napcat 各一套）。这正是 supervisor「编排多 bot」的用武之地——参数化部署 N 组 `{napcat_i + bot_i + 独立 storage_i}`。

#### A.7.2 sidecar 共享的关键分野：可共享状态 vs per-bot 状态

CCIP 的数据**分两层，多 bot 语义相反**——这是本审计最重要的发现：

| CCIP 数据 | 性质 | 多 bot | 证据 |
| --- | --- | --- | --- |
| **角色识别**（image hash → character_id / 嵌入库 character_registry） | **bot 无关**：一张图是谁，与哪个 bot 收到无关 | ✅ **可共享**——单一 ccip sidecar 服务所有 bot | v2 schema PK 是 `hash`/`character_id`，**无 bot_id**（[issue17-pre-part0-landing-v2](issue17-pre-part0-landing-v2-2026-05-31.md) §3） |
| **`relation`（self/friend/known）** | **per-bot 语义**：「凤笑梦」对 bot-A 是 self、对 bot-B 可能是 known | 🔴 **不可共享**——绝不能焊进共享嵌入库 | v2 §5.9 relation 驱动 per-bot 的 familiarity/openness 信号 |

**正确的共享架构**：
- **ccip sidecar 无状态共享**：只做 `POST /identify`(图 → character_id + 嵌入相似度 + 识别缓存)，识别缓存按 image hash 全局共享（同一张图所有 bot 命中同结果，**反而省算力**）。
- **`relation` 留在各 bot 自己的库**：bot 拿到 ccip 返回的 character_id 后，**在自己的 storage 里**查「这个角色对我是 self/friend/known」。relation 是 bot↔character 的关系表，per-bot。
- → 嵌入库（重、共享、只读）和 relation（轻、per-bot、可变）**分离**，正好对应「ccip sidecar 持有嵌入 + 识别」「omubot 持有关系」。这与 v2 §7「各管各」的职责分离一脉相承。

#### A.7.3 stt 等后续 sidecar 的通则

- **无状态推理 sidecar（ccip 识别 / stt 转写）→ 天然可多 bot 共享**：输入图/音→输出文本/嵌入，与 bot 身份无关。一个 sidecar 服务 N 个 bot，省内存省算力（模型只加载一份）。
- **任何「关系/个性化」状态 → 必须 per-bot**，留在各 bot storage，不进共享 sidecar。
- **共享 sidecar = supervisor 编排的核心价值之一**：N 个 bot 各自一套 napcat+storage，但**共用一队 ML sidecar**——这是「主进程队列 + 共享推理层」形态，比"每 bot 自带一份 CCIP"省 N×150MB。

#### A.7.4 对前文的影响

- §11「CCIP 作首个纳管 sidecar」**强化**：单例时外挂是「依赖隔离」，多 bot 时外挂是「共享推理层」——多 bot 把外挂从"可选优化"升为"必然选择"（绝不会让 N 个 bot 各内置一份 numpy<2 的 CCIP）。
- §12 更新矩阵**新增维度**：共享 sidecar 更新一次，N 个 bot 同时受影响——更新前要确认 `/identify` 契约兼容（接口版本化），避免一次 sidecar 更新打挂所有 bot。
- supervisor 编排职责**扩展**：从「管 napcat+bot+sidecar 三件」扩为「管 N×{napcat+bot+独立storage} + 共享 sidecar 队列」——但形态不变（仍是 control plane），只是被管对象成矩阵。

#### A.7.5 裁决

**当前能做到多 bot + 共享 sidecar，无架构硬墙。** 前置工作：① 部署层参数化（容器名/storage volume/config/napcat 各 per-bot）——supervisor 编排的本职；② sidecar 严守「无状态推理共享、relation 等 per-bot 状态留各 bot」分野（ccip v2 schema 已无 bot_id，符合）；③ 共享 sidecar 接口版本化（防一次更新打挂全部）。napcat **必须 per-bot**（设备指纹，D6），不可共享——这是唯一的"必须 N 份"组件。
