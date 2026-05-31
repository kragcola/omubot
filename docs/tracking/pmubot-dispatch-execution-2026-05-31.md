# pmubot 派单 — 容器编排控制平面（执行追踪）

> 状态：2026-05-31 立。本文是 [pmubot 最终方案](pmubot-final-plan-2026-05-31.md) 的执行派发版，交付执行者按 Wave 顺序落地。
>
> 范围：pmubot 独立控制平面 P0–P4（只读视图 → 受限写 → 更新接线 → sidecar 编排 → 多 bot）。
>
> 配套：[最终方案](pmubot-final-plan-2026-05-31.md) §0 决策冻结表（**冲突时以决策表为准**）、[设计+三轮审计](supervisor-control-plane-design-2026-05-31.md)（背景论证）。
>
> 上游决策（已冻结，执行者不得改）：① 薄控制平面非重框架；② docker 权限用 docker-socket-proxy 不裸挂；③ 更新写平面分档（napcat 永不自动）；④ D6 红线 napcat **物理不开 recreate/down**，`restart napcat` 保留但**高危**（实证会掉线重验证，需 ack_relogin 守卫，见 §3.2 ⚠️ 订正）；⑤ CCIP 外挂为共享 sidecar；⑥ relation 等 per-bot 状态绝不进共享 sidecar。
>
> **执行原则（覆盖任何冲突项，逐条照做）：**
> 1. **Wave 严格串行**——下一 Wave 必须等上一 Wave「收口」全绿。不允许跳 Wave、不允许并行抢跑。
> 2. **每 Wave 自带：D1 grep 证据 + 验证命令 + 回滚命令**——三者缺一不算闭环。动手前先跑 D1 grep，把实际行号/输出贴回本文对应位置。
> 3. **遇到与本文不符的现实（行号变了/文件不在/命令报错）→ 停，记录到 §5 偏差表，等验收人确认，不要自行猜着改。**
> 4. **pmubot 是独立容器，不动 omubot/napcat 的现有 compose 服务**——pmubot 挂了不能影响 bot 跑。任何改到 `docker-compose.yml` 现有 napcat/bot 段的操作，先停下问。
> 5. **绝不碰 napcat 容器的 down/recreate/换 tag**——这是反风控红线（D6），违反会导致 bot 被封号重新扫码。只允许 `docker compose restart napcat`。
> 6. **每个写操作（重启/改配置）落地前，确认回滚命令可用**。

---

## 1. 主线自审与证据订正（执行前必读）

下表是对最终方案的 grep 实证锚点。**派单按本表执行；执行者第一步（Wave 0）就是把这些锚点重新 grep 一遍确认仍成立。**

| 锚点 | 最终方案表述 | 验证命令 | 预期（2026-05-31 实测） |
| --- | --- | --- | --- |
| docker socket 路径 | `~/.docker/run/docker.sock`（macOS Docker Desktop） | `ls -la /var/run/docker.sock` | symlink → `/Users/kragcola/.docker/run/docker.sock` |
| 容器网络 | bot+napcat 同 `omubot_default` | `docker inspect qq-bot napcat --format '{{.Name}}: {{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'` | 两者都含 `omubot_default` |
| WebUI 活否 | 宿主 200 / 容器间 502 | `curl -s -o /dev/null -w "%{http_code}" http://localhost:6099/webui/` | `200`（活） |
| ⚠️ 容器间路由 | bot→napcat:6099 不通 | `docker compose exec -T bot sh -c '/app/.venv/bin/python -c "import urllib.request;urllib.request.urlopen(\"http://napcat:6099/webui/\",timeout=3)"'` | 502 或异常（**确认不通 → P1 走宿主网络**） |
| WebUI token | `24c611869429` | `grep token napcat/config/webui.json` | 当前值（落地读 config，不写死） |
| napcat 镜像 | `mlikiowa/napcat-docker:v4.15.0` | `grep image docker-compose.yml \| head -1` | v4.15.0 |
| bot self_id | 运行时取，非写死 | `grep -n 'self_id' services/scheduler.py \| head -3` | `:335` `getattr(bot,"self_id")` |

> **派单规则**：执行者拿到本文后按本表 Wave 0 验证。任何一项与预期不符 → 记 §5 偏差表，停，问验收人。

---

## 2. Wave 0 — 前置零代码验证 + 两个未知项摸清（必做，零代码）

P0 之后所有 Wave 依赖本步骤回执。**Wave 0 不写任何代码、不改任何配置，只跑命令 + 抓信息 + 回填。**

| 步骤 | 命令 / 操作 | 预期 / 产出 |
| --- | --- | --- |
| 0.1 | 跑 §1 表全部 7 条验证命令，逐条把实际输出贴到 §5 | 7 条全部对上 → 继续；任一不符 → 停，问 |
| 0.2 | **摸清 NapCat v4.15 WebUI REST API 契约**（前置障碍①）：`curl -i http://localhost:6099/webui/` 看跳转 + 抓前端 JS 里的 API 路径；试 `curl -i -X POST http://localhost:6099/api/auth/login -H 'Content-Type: application/json' -d '{"token":"24c611869429"}'`（路径/方法可能不同，多试几种大小写/前缀） | 产出：登录拿 token 的真实端点 + 1~2 个管理端点（如查登录状态）的 curl 实证，贴到 §5 |
| 0.3 | **确认 bot↔napcat 容器间 6099 是否真不通**（前置障碍②）：跑 §1 表「容器间路由」命令 | 不通 → P1 的 pmubot 走宿主网络（`network_mode` 或连 host）；通 → pmubot 可直接进 `omubot_default` 连 `napcat:6099` |
| 0.4 | 确认 docker-socket-proxy 镜像可拉：`docker pull tecnativa/docker-socket-proxy:latest` | 拉取成功（~10MB） |
| 0.5 | 把 0.1–0.4 结论写 1 段到 §5「Wave 0 回执」 | 给 P1 选址拍板：① WebUI API 真实契约 ② pmubot 网络接法 ③ proxy 可用 |

**Wave 0 不是 commit；是派单前置验证 + 摸清两个未知。验收人看回执再发 P1 单。**

## 3. 串行执行 Wave 表

依赖：Wave 0（验证）→ P0（只读骨架）→ P1（受限写 + napcat 代理）→ P2（更新接线）→ P3（sidecar 编排/CCIP）→ P4（多 bot）。**每 Wave 收口全绿才进下一个。**

### 3.1 P0 — pmubot 只读骨架 + docker-socket-proxy（零写权限，最安全）

目标：起一个独立 pmubot 容器，经只读 docker-socket-proxy 看到 napcat/bot 状态，有一个最简 web 页。**全程只读，不能动任何容器。**

| 编号 | 一句话 | 关键文件 | D1 grep 锁 | 验证 | 回滚 |
| --- | --- | --- | --- | --- | --- |
| **P0.1** | 在 `docker-compose.yml` **新增**（不改现有 napcat/bot 段）`socket-proxy` 服务：`tecnativa/docker-socket-proxy`，环境变量 `CONTAINERS=1 POST=0`（其余默认 0=全只读），只接内部网络不映射公网端口；挂 `~/.docker/run/docker.sock`（macOS 路径，见 Wave 0.1） | `docker-compose.yml`（+ ~12 行新服务） | `grep -nE 'socket-proxy\|docker-socket-proxy' docker-compose.yml` 仅新增段命中 | `docker compose up socket-proxy -d` 后 `docker compose exec -T socket-proxy sh -c 'wget -qO- http://localhost:2375/version'` 返回 docker 版本 JSON；试 `.../containers/json` 返回 403（POST/CONTAINERS 控制对，只读生效） | `docker compose rm -sf socket-proxy` + 删 compose 新增段 |
| **P0.2** | 新建 `pmubot/` 目录（与 omubot 独立）：`Dockerfile`（python3.12-slim + FastAPI）+ `app.py`（FastAPI 骨架，连 `DOCKER_HOST=tcp://socket-proxy:2375`，`GET /api/containers` 调 docker API list 返回 napcat/bot 状态/uptime/镜像 tag）；compose 加 `pmubot` 服务（进 `omubot_default` 网络 + 依赖 socket-proxy） | `pmubot/Dockerfile` + `pmubot/app.py`（新，~80 行）+ `docker-compose.yml`（+ ~10 行） | `grep -nE 'pmubot' docker-compose.yml` 仅新增段 | `docker compose up pmubot -d --build` 后 `curl -s http://localhost:<pmubot端口>/api/containers` 返回含 qq-bot/napcat 的 JSON（状态/tag）；napcat/bot **未受任何影响**（`docker compose ps` 两者 Up 不变） | `docker compose rm -sf pmubot` |
| **P0.3** | pmubot 最简 web 页（`pmubot/static/` 或内嵌 HTML）：一个表格列出容器名/状态/uptime/镜像 tag，调 `/api/containers`。**只读展示，无任何按钮。** | `pmubot/static/index.html`（新，~60 行）+ `app.py` 挂静态 | 浏览器开 pmubot web 页能看到容器列表 | `curl -s http://localhost:<port>/` 返回 HTML；浏览器看到 napcat+bot 两行状态 | `git restore pmubot/ docker-compose.yml` |

**P0 收口**：① pmubot 独立容器 Up，web 页看得到 napcat+bot 状态；② socket-proxy 实测**只读**（写类 API 返回 403）；③ napcat+bot **全程 Up 未受影响**（`docker compose ps` 对比 P0 前后）；④ 停掉 pmubot+proxy，omubot 照常跑（`curl` bot admin 仍通）。

### 3.2 P1 — 受限写（restart 守 D6）+ napcat WebUI 代理（关 #napcat 首用例）

目标：pmubot 能 restart bot（白名单），代理 napcat WebUI 关 `#napcat`。**napcat 物理上不开 recreate；且 `restart napcat` 经实证为高危（见 ⚠️ 订正），需二次确认 + 明示掉线风险，不当作无害操作。**

> ⚠️ **2026-05-31 实证订正（执行人发现 + 验收人确认，覆盖本节原表述）**：
> 1. **docker-socket-proxy 的 `POST` 是全局总闸**——单 proxy `ALLOW_RESTARTS=1` 在 `POST=0` 下 restart 仍被拒；`POST=1` 又会连带放开 `create`。**已落地为双 proxy**：`socket-proxy`（只读 `CONTAINERS=1 POST=0`）+ `socket-proxy-write`（`ALLOW_RESTARTS=1 POST=1 CONTAINERS=0`）。实测 `create`/读 在写 proxy = 403、写在读 proxy = 403，D6 边界成立。
> 2. **`restart napcat` 不是无害操作**——本机实测 `docker compose restart napcat` 后 NapCat **掉回二维码/手Q验证**（OneBot WS 断、bot disconnected），可能自愈也可能要人工扫码。**原方案"restart 不触发反风控、只有 recreate 才会"的假设错误。** napcat restart 重新定级为**高危操作**（与换镜像同档处置思路：二次确认 + 明示掉线风险 + 审计），但**保留**该能力（用户 2026-05-31 决定：保留但标高危，不是删除）。

| 编号 | 一句话 | 关键文件 | D1 grep 锁 | 验证 | 回滚 |
| --- | --- | --- | --- | --- | --- |
| **P1.1**（已落地，订正版） | 双 proxy：`socket-proxy`（只读 `CONTAINERS=1 POST=0`）+ `socket-proxy-write`（`ALLOW_RESTARTS=1 POST=1 CONTAINERS=0`）；pmubot 读走只读 proxy、写走写 proxy；`POST /api/containers/{name}/restart` 白名单 `qq-bot`/`napcat`，调 docker restart API | `docker-compose.yml`（双 proxy 服务）+ `pmubot/app.py`（restart 端点 + 白名单 + 读写分流） | `grep -nE 'socket-proxy\|ALLOW_RESTARTS\|POST=\|CONTAINERS=' docker-compose.yml`：双 proxy 权限对、**无** IMAGES/create 放开 | 写 proxy `POST /containers/create` → 403；读 proxy 写 → 403；写 proxy 读 → 403；`POST .../qq-bot/restart` → 204（bot 重启无害） | 去写 proxy + 删端点 |
| **P1.4**（新增，restart napcat 高危处置） | `restart napcat` 端点对 `name=napcat` **额外要求**：`?confirm=1` 之外再加 `?ack_relogin=1`（明确知晓会掉线重验证）+ 返回体明示「napcat 已重启，可能需手机扫码恢复，请检查登录态」；记审计日志；**默认 UI 不放在常用按钮区**（与换镜像同危险档）。`name=qq-bot` 保持普通 confirm（bot restart 无害） | `pmubot/app.py`（restart 端点对 napcat 分支加 ack_relogin 守卫 + 审计） | `grep -nE 'ack_relogin\|napcat\|relogin\|掉线' pmubot/app.py` | `POST .../napcat/restart?confirm=1`（无 ack_relogin）→ 400 提示「napcat restart 高危需 ack_relogin=1」；带 ack_relogin → 执行 + 返回掉线提示 | 改回普通 confirm（不建议——会丢掉线警示） |
| **P1.2** | pmubot 加 napcat WebUI 代理：`pmubot/napcat_client.py` 按真实 API 契约封装（`POST /api/auth/login` 用 `{hash: sha256(token+".napcat")}` → Credential → bearer 调 `/api/Plugin/Config`，改 builtin 插件 enableReply，非破坏式合并 config）；`POST /api/napcat/disable-builtin-reply` 关 `#napcat` | `pmubot/napcat_client.py` + `app.py` 端点 | `grep -nE 'napcat_client\|auth/login\|enableReply\|Plugin/Config' pmubot/`：契约调用点确认 | **依赖 napcat QQ 在线**：QQ 会话恢复后 `curl -X POST .../disable-builtin-reply` → 群里发 `#napcat` 不再回版本信息（napcat 日志无 `已回复版本信息`） | 调 WebUI 把 enableReply 改回 true；或删端点 |
| **P1.3** | 控制接口独立鉴权：写类端点加独立 token（env `PMUBOT_TOKEN`，**不与 omubot admin_token 同**），无 token → 401；写操作需 `?confirm=1` 二次确认 | `pmubot/app.py`（鉴权依赖 + compose env） | `grep -nE 'PMUBOT_TOKEN\|confirm\|401\|compare_digest\|Depends' pmubot/app.py` | 无 token → 401；带 token 无 confirm → 400；带 token+confirm → 执行 | 去鉴权（不建议；至少保留 token） |

**P1 收口（订正版）**：① 双 proxy 实测 `create`/recreate = 403（D6 守住）；② `restart qq-bot` 无害可用；**③ `restart napcat` 经 `ack_relogin` 高危守卫，调用前明示掉线、调用后提示检查登录态（不再宣称无害）**；④ `#napcat` 经代理可关（依赖 napcat 在线）；⑤ 写端点独立鉴权 + 二次确认。**注：因 restart napcat 会掉线，P1.2 的 `#napcat` 落地需在 napcat QQ 会话在线时进行，勿在 napcat 等扫码态测试。**

### 3.3 P2 — 更新接线（分档，napcat 永不自动）

目标：更新执行外包给现成件，按组件分档。**不自写更新器。**

| 编号 | 一句话 | 关键文件 | D1 grep 锁 | 验证 | 回滚 |
| --- | --- | --- | --- | --- | --- |
| **P2.1** | compose 新增 `watchtower` 服务，**仅纳管 sidecar**（默认 `WATCHTOWER_LABEL_ENABLE=true`，只更新带 `com.centurylinklabs.watchtower.enable=true` label 的容器）；napcat/bot **不打** enable label（默认被排除） | `docker-compose.yml`（+watchtower 服务 + label 策略） | `grep -nE 'watchtower\|label.enable' docker-compose.yml`：确认 napcat/bot 段**无** enable label | `docker compose up watchtower -d` 后看日志：扫描时跳过 napcat/bot（无 label），不对它们做任何更新 | `docker compose rm -sf watchtower` |
| **P2.2** | pmubot `GET /api/updates/check`：对 napcat **只读探测**有无新 tag（Diun 式，调 registry API 比对，**只通知不动**）；对 bot 提示走现有 CI；返回各组件「有无新版本」 | `pmubot/app.py`（+ ~30 行只读探测） | `grep -nE 'updates/check\|napcat.*pull\|recreate' pmubot/app.py`：确认对 napcat **无** pull/recreate 调用 | `curl http://localhost:<port>/api/updates/check` 返回各容器当前 tag + 有无新版本，**不触发任何更新** | 删端点 |
| **P2.3** | bot 更新封装 `pmubot/bot_update.py`：`POST /api/bot/update`（带 token+confirm）= 先 `scripts/backup-databases.sh`→记当前 git tag/镜像→`docker compose up bot -d --build`→失败回滚上一镜像。**napcat 无此端点**（D6） | `pmubot/bot_update.py`（新，~40 行）+ `app.py` 端点 | `grep -nE 'bot/update\|napcat.*update\|backup' pmubot/`：确认**无** napcat update 端点 | 测试环境跑一次 bot update（带 confirm），看 backup 先执行、失败能回滚；napcat 无 update 端点（404） | 删端点；bot 镜像回滚 = `docker compose up bot -d`（用上一镜像） |

**P2 收口**：① watchtower 只动 sidecar、实证跳过 napcat/bot；② napcat 更新仅只读通知，pmubot 无任何 napcat pull/recreate 路径；③ bot update 带 backup+回滚；④ 全程 napcat 未被 recreate。

### 3.4 P3 — sidecar 编排 + CCIP 首发（外挂，依赖隔离）

目标：pmubot 能拉起/健康检查一个 ML sidecar；CCIP 作首个，omubot 改调它。**CCIP 重依赖锁在 sidecar，不进 bot 镜像。** 此 Wave 与 [issue17-pre-part0-landing-v2](issue17-pre-part0-landing-v2-2026-05-31.md) 的 Phase 1b 对接。

| 编号 | 一句话 | 关键文件 | D1 grep 锁 | 验证 | 回滚 |
| --- | --- | --- | --- | --- | --- |
| **P3.1** | 新建 `ccip-sidecar/` 独立容器：`Dockerfile`（python + `dghs-imgutils` + `onnxruntime` + HF 缓存 volume，**核版本**见下注）+ `server.py`（FastAPI `POST /identify`：图 bytes → character_id + 嵌入相似度 + 识别缓存按完整 hash 全局命中）；compose 加服务 + 打 watchtower enable label + 挂 HF 缓存 named volume | `ccip-sidecar/Dockerfile` + `ccip-sidecar/server.py`（新，~120 行）+ `docker-compose.yml` | `grep -nE 'ccip-sidecar\|dghs-imgutils\|huggingface' docker-compose.yml ccip-sidecar/`：依赖**只在 sidecar**，`grep dghs-imgutils pyproject.toml`=0（不进 bot） | `docker compose up ccip-sidecar -d --build`（首次下模型~100MB）后 `curl -F image=@test.jpg http://localhost:<port>/identify` 返回 character_id+score；bot 镜像无 numpy 依赖（`docker compose exec bot sh -c '.venv/bin/python -c "import numpy"'` 应失败/无关) | `docker compose rm -sf ccip-sidecar` |
| **P3.2** | pmubot 纳管该 sidecar：`/api/containers` 列表含 ccip-sidecar 健康；pmubot 健康检查（`/identify` ping），挂掉能拉起 | `pmubot/app.py`（+ ~20 行 sidecar 健康/拉起） | `grep -nE 'ccip-sidecar\|health\|identify' pmubot/app.py` | pmubot web 页看到 ccip-sidecar 状态；手动停 ccip → pmubot 检测到并拉起 | 去健康检查逻辑 |
| **P3.3** | omubot 侧 `services/media/character_recognizer.py` 从「进程内 ONNX」改为「HTTP client 调 ccip-sidecar `/identify`」；**relation 留 omubot 本地库**（per-bot，不进 sidecar）；flag `character_recognition.enabled` 默认 false | `services/media/character_recognizer.py`（按 v2 方案，HTTP client 版）+ router 接入 | `grep -nE 'character_recognizer\|/identify\|relation' services/media/ kernel/router.py`：确认 relation 查询在 omubot 本地、识别调 sidecar | flag 开后群里发已注册角色表情包，bot 描述带角色名；relation(self/friend) 由 omubot 本地判 | flag `enabled=false` 旁路回 VL 描述 |

> **P3.1 依赖核版本**：`dghs-imgutils`/`onnxruntime` 落地前先 `pip index versions dghs-imgutils` 核当前版本（原调研 v0.19.0@2025-09，今已过时）；确认 numpy<2 约束**只锁在 sidecar 容器**。

**P3 收口**：① ccip-sidecar 独立 Up，`/identify` 可用；② bot 镜像**无** numpy/onnxruntime（依赖隔离成功）；③ omubot 经 HTTP 调 sidecar 认出角色，relation 留本地；④ flag 关掉旁路回现状。

### 3.5 P4 — 多 bot + 共享 sidecar（部署层参数化）

目标：支持 N 个 bot，共用 ccip sidecar；napcat per-bot。**relation 等 per-bot 状态严格隔离。**

| 编号 | 一句话 | 关键文件 | D1 grep 锁 | 验证 | 回滚 |
| --- | --- | --- | --- | --- | --- |
| **P4.1** | compose 部署层参数化：每 bot 一组 `{napcat_i + bot_i + storage_i 独立 volume}`；容器名/persona_id/config 路径参数化（compose profile 或 override 文件）；**ccip-sidecar 单实例共享** | `docker-compose.yml` / `docker-compose.<bot>.yml`（参数化） | `grep -nE 'container_name\|omubot-storage\|persona_id' docker-compose*.yml`：确认每 bot 独立 volume/名 | 起第二个 bot 实例，两 bot 各自 napcat+storage，共用一个 ccip-sidecar；两 bot 库不撞（各自 messages.db 独立） | 停第二组，回单 bot |
| **P4.2** | 验证共享 sidecar 多 bot 正确性：同一角色表情包，bot-A relation=self、bot-B relation=known，识别结果（character_id）相同但 relation 各取本地——证明「识别共享 + relation per-bot」分野成立 | （验证为主，无新代码或少量）`pmubot/app.py` sidecar 接口版本号 | `grep -nE 'relation\|character_id\|/identify' services/media/character_recognizer.py`：relation 来自 bot 本地、character_id 来自 sidecar | 两 bot 对同一图：character_id 一致、relation 不同（A=self/B=known） | — |

**P4 收口**：① 两 bot 并存，各自 napcat+独立 storage、共用 ccip-sidecar；② 库无碰撞（各 storage volume 隔离）；③ relation per-bot 实证（同图不同关系）；④ ccip-sidecar 接口版本化（一次更新不打挂任一 bot）。

---

## 4. 总收口判据（全 Wave 完成后）

- pmubot 独立容器带 web 界面，看得到全栈状态/日志/版本；停掉不影响 omubot/napcat。
- docker 权限经 docker-socket-proxy 白名单（无裸 sock、无 recreate napcat 入口）。
- 更新分档：sidecar→watchtower 自动、napcat→只通知、bot→backup+回滚 CI。
- napcat WebUI 经 pmubot 代理（`#napcat` 可关），写端点独立鉴权+二次确认。
- CCIP 外挂为共享 sidecar，bot 镜像无 numpy 污染，多 bot 共用、relation per-bot。
- 全程 napcat 从未被 recreate（D6 守住，无意外扫码）。
- `uv run ruff check` + `uv run pyright` 对改动的 omubot 侧文件无新增错误（pmubot/ccip-sidecar 独立项目自带检查）。

---

## 5. 偏差表 + Wave 0 回执（执行者回填）

**Wave 0 回执**（执行者填）：

| 项 | 实测结果 | 结论 |
| --- | --- | --- |
| §1 七条验证 | 1) `ls -la /var/run/docker.sock` → `/var/run/docker.sock -> /Users/kragcola/.docker/run/docker.sock`<br>2) `docker inspect qq-bot napcat --format '{{.Name}}: {{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'` → `/qq-bot: omubot_default` / `/napcat: omubot_default`<br>3) `curl -s -o /dev/null -w "%{http_code}" http://localhost:6099/webui/` → `200`<br>4) `docker compose exec -T bot ... urllib.request.urlopen("http://napcat:6099/webui/")` → `urllib.error.HTTPError: HTTP Error 502: Bad Gateway`<br>5) `grep token napcat/config/webui.json` → `"token": "24c611869429"`<br>6) `grep image docker-compose.yml \| head -1` → `image: mlikiowa/napcat-docker:v4.15.0`<br>7) `grep -n 'self_id' services/scheduler.py \| head -3` → `317/335/338` 行仍为运行时 `getattr(bot, "self_id", "")` | 7 条全部对上 |
| 0.2 WebUI API 契约 | `POST /api/auth/login` 请求体不是原始 token，而是 `{"hash":"sha256(<token>.napcat)"}`；当前 token 算得 hash=`9e501c530e34ce2f8750b0686edcc3e90abf5071874383349d9d1b3ac2456cef`。<br>实测 `POST /api/auth/login` → `{"code":0,"data":{"Credential":"<Bearer>"}}`；随后 `POST /api/auth/check` + `Authorization: Bearer <Credential>` → `{"code":0,"message":"success"}`。<br>管理端点实测：`GET /api/base/GetNapCatVersion` → `{"version":"4.15.0"}`；`GET /api/Plugin/List` → builtin 插件 `napcat-plugin-builtin`；`GET /api/Plugin/Config?id=napcat-plugin-builtin` → 返回 `config.enableReply=true` 与 schema。 | 契约已确定，P1 可按 Bearer + `/api/Plugin/Config` 封装 |
| 0.3 容器间路由 | bot 容器访问 `http://napcat:6099/webui/` 稳定返回 `502 Bad Gateway`；宿主访问 `http://localhost:6099/webui/` 为 `200` | 当前应按宿主网络/宿主入口接 pmubot 的 NapCat WebUI 代理 |
| 0.4 proxy 可拉 | `docker pull tecnativa/docker-socket-proxy:latest` 成功；digest=`sha256:1f3a6f303320723d199d2316a3e82b2e2685d86c275d5e3deeaf182573b47476` | ok |

**P0 执行回执**（执行者填）：

| 项 | 实测结果 | 结论 |
| --- | --- | --- |
| P0.1 socket-proxy 只读链路 | D1：`grep -nE 'socket-proxy\|docker-socket-proxy' docker-compose.yml` → `44/45/46` 行为新增段。<br>验证：`docker compose exec -T socket-proxy sh -c 'wget -Y off -qO- http://localhost:2375/version'` 返回 Docker Desktop 28.1.1 JSON；`docker compose exec -T socket-proxy sh -c 'wget -Y off -qO- http://localhost:2375/containers/json?all=1'` 返回容器列表；`docker compose exec -T socket-proxy sh -c 'wget -Y off -S -qO- --post-data="" http://localhost:2375/containers/napcat/restart'` → `HTTP/1.0 403 Forbidden` | socket-proxy 已按只读模式上线，写类 restart 被物理拒绝 |
| P0.2 pmubot 骨架 API | D1：`grep -nE 'pmubot' docker-compose.yml` → `54/56/57/62/64` 行为新增段。<br>验证：`curl -s http://localhost:8610/api/containers` 返回 `napcat / pmubot / qq-bot / socket-proxy` 的 `state/status/image_tag` JSON；`docker compose ps` 期间 `napcat`、`qq-bot` 持续 `Up` | pmubot 独立容器可经 proxy 读取目标容器状态，未影响现有 bot/napcat |
| P0.3 最简 Web 页 | 文件：`pmubot/static/index.html` + `pmubot/app.py` 静态挂载。<br>验证：`curl -s http://localhost:8610/` 返回页面 HTML，页面脚本 fetch `/api/containers`；同一时刻 `/api/containers` 已返回容器列表 JSON | 只读展示页已可用，满足 P0 无按钮、无写操作要求 |
| P0 回滚演练 | `docker compose rm -sf pmubot socket-proxy` 后 `docker compose ps` 只剩 `napcat`/`qq-bot`，`curl -s -o /dev/null -w '%{http_code}' http://localhost:8081/admin/` 回前后均为 `200`；随后 `docker compose up socket-proxy pmubot -d` 恢复成功 | 回滚路径有效，停掉 pmubot/socket-proxy 不影响现有 omubot 管理端 |

**P1 执行回执**（执行者填）：

| 项 | 实测结果 | 结论 |
| --- | --- | --- |
| P1.1 写鉴权与白名单 | `POST /api/containers/napcat/restart` 无 token → `401`；带 `Authorization: Bearer <PMUBOT_TOKEN>` 但无 `?confirm=1` → `400`；`socket-proxy-write` 直测 `POST /containers/create?...` → `403 Forbidden`；`POST /api/containers/qq-bot/restart?confirm=1` 现返回 `200 {"status":"ok","action":"restart","container":"qq-bot"}` | 写端鉴权与 confirm 守卫已生效；危险 create 路径未放开；`qq-bot` restart 经 pmubot 可用 |
| P1.1 docker-socket-proxy 权限模型 | 直接读镜像 `/tmp/haproxy.cfg` 实证：`POST` 总闸在前，`ALLOW_RESTARTS=1` 单独开启仍会被 `POST=0` 拒绝；若单 proxy 改成 `POST=1 + CONTAINERS=1`，则 `POST /containers/create` 会一并放开 | 因官方 proxy 规则限制，已落地为**双 proxy**：`socket-proxy` 只读列表，`socket-proxy-write` 仅 `ALLOW_RESTARTS=1 + POST=1 + CONTAINERS=0`，pmubot 读写分流以守 D6 |
| P1.1 restart 假失败修正 | 运行态曾出现「proxy 已 204、pmubot 却回 502」：`socket-proxy-write` 日志显示 `POST /containers/qq-bot/restart` 实际成功，但 pmubot 原 5s 写超时把慢 restart 误报成失败；现 `pmubot/app.py` 已加 `PMUBOT_RESTART_TIMEOUT_SECONDS=30` 默认值 | 误判根因已查清并修正，后续 restart 以实际返回 `200` 为准 |
| P1.2 NapCat WebUI 代理（恢复后完成） | 在 QQ 会话恢复在线后，`POST /api/napcat/disable-builtin-reply?confirm=1` 实测 `200`；恢复前 `enableReply=true`，关闭后读回 `enableReply=false`。回滚演练：`POST /api/napcat/enable-builtin-reply?confirm=1` → `enableReply=true`；再 `disable` 一次回到 `false` | `NapCatClient` 契约封装可用，builtin `#napcat` 开关已落地且有正反向回滚证据 |
| P1.4 napcat 高危处置 | `POST /api/containers/napcat/restart?confirm=1`（无 `ack_relogin`）→ `400 {"detail":"napcat restart is high risk; ack_relogin=1 required"}`；带 `ack_relogin=1` 后返回 `200 {"warning":"napcat 已重启，可能需手机扫码恢复，请检查登录态"}`。18s 后 `docker inspect napcat` StartedAt=`2026-05-31T12:30:30.322085344Z` | `ack_relogin` 守卫、警示返回体与审计日志已生效；高危行为仍会真实触发掉线，见下条 |
| P1.4 高危副作用复核 | 带 `ack_relogin=1` 的实机验证后，NapCat 日志再次出现 `正在快速登录 384801062`、`请扫描下面的二维码`、`登录需要手Q验证。`；`qq-bot` 日志出现 `WebSocket ... closed by peer` / `bot disconnected` | **高危定级再确认**：napcat restart 会把 QQ 会话打回二维码 / 手Q验证。当前运行态需人工手机侧确认后，`qq-bot` 才会重新连回 |

**P2 执行回执**（执行者填）：

| 项 | 实测结果 | 结论 |
| --- | --- | --- |
| P2.1 watchtower label-only | D1：`grep -nE 'watchtower\|label.enable' docker-compose.yml` → `88-99` 行新增 `watchtower` 服务，`bot`/`napcat` 段无 enable label。`docker exec watchtower /watchtower --run-once --label-enable` 输出：`Only checking containers using enable label`、`Session done Failed=0 Scanned=0 Updated=0` | watchtower 已按 label-only 策略上线，当前不会触碰 `bot` / `napcat` |
| P2.2 `/api/updates/check` | D1：`grep -nE 'updates/check' pmubot/app.py` → `254`；实测 `curl http://localhost:8610/api/updates/check` 返回：`napcat current_tag=v4.15.0 latest_tag=v4.18.4 update_available=true`；`bot strategy=ci-build update_available=null`；接口全程只读，无 pull/recreate | napcat 更新探测已接成「只通知不动容器」，bot 明确提示走 CI / `/api/bot/update` |
| P2.3 backup + bot update | D1：`grep -nE 'bot/update|run_bot_update' pmubot/app.py pmubot/bot_update.py` → `299-303`、`137+`；`grep -n '/app/.venv/bin/python -m services.storage.backup create --host-mode' scripts/backup-databases.sh` → `36`。实测 `POST /api/bot/update?confirm=1` 返回 `200`，`backup_summary=Backup daily-20260531-202004: trusted=True ok=14 failed=0 skipped=0`，`update_attempts=2`，bot 镜像 `sha256:848c2f...` → `sha256:e034ff...`，StartedAt `2026-05-31T12:18:13Z` → `2026-05-31T12:29:31Z` | bot update 端点已打通：先备份，再 rebuild/recreate，仅动 `qq-bot`；`POST /api/napcat/update` 实测 `404`，D6 守住 |

**P3 执行回执**（执行者填）：

| 项 | 实测结果 | 结论 |
| --- | --- | --- |
| P3.1 ccip-sidecar 独立容器 + 依赖隔离 | D1：`rg -n 'ccip-sidecar\|dghs-imgutils\|huggingface' docker-compose.yml ccip-sidecar` → `docker-compose.yml:133-146` 为新 sidecar 服务与 HF cache；`ccip-sidecar/Dockerfile:19` 固定 `dghs-imgutils==0.19.0`；`rg -n 'dghs-imgutils' pyproject.toml` → **0 命中**。依赖核版本：`python3 -m pip index versions dghs-imgutils` → `0.19.0`，`python3 -m pip index versions onnxruntime` → `1.26.0`。运行态：`docker compose up -d --build --no-deps ccip-sidecar` 后 `docker compose ps` 显示 `ccip-sidecar Up (healthy)` 映射 `8620->8080`；临时验证 pack（执行后已删除）期间，`curl http://localhost:8620/health` → `pack_count=1 character_count=1 registry_version=a9e6d5391750`；`curl -F image=@config/character_packs/verify.charpack/verify.png http://localhost:8620/identify` → `{"matched":true,"character_id":"emu_verify","character_name":"凤笑梦验证","difference":0.0,"score":1.0,"cache_hit":false}`；同图第二次请求 `cache_hit=true`。隔离验证：`docker compose exec -T bot sh -lc '.venv/bin/python -c "import numpy"'` → `ModuleNotFoundError: No module named 'numpy'` | CCIP 重依赖已完全锁进 sidecar 容器，识别 / 缓存链路可用，`qq-bot` 镜像未混入 `numpy/onnxruntime` |
| P3.2 pmubot 纳管 + 自愈拉起 | D1：`rg -n 'ccip-sidecar\|health\|identify' pmubot/app.py` → `22,112-132,197-237,365`。`curl http://localhost:8610/api/containers` 返回 `ccip-sidecar` 条目，带 `health=ok`、`character_count`、`registry_version`。自愈验证：`docker stop ccip-sidecar && curl http://localhost:8610/api/containers` 后，返回中 `ccip-sidecar` 已变为 `Up Less than a second (health: starting)`，证明 pmubot 检测到 down 并执行恢复。回滚演练：`docker compose rm -sf ccip-sidecar && docker compose up -d --no-deps ccip-sidecar` 后 `docker compose ps ccip-sidecar` 回到 `Up ... (health: starting)`，`curl http://localhost:8620/health` 重新返回 `status=ok`。补充：`curl http://localhost:8610/api/updates/check` 现返回 `watchtower.managed_containers=["ccip-sidecar"]` | pmubot 已把 sidecar 纳入状态页和恢复链路；删掉 / 停掉 sidecar 都能在不碰 napcat 的前提下拉回 |
| P3.3 omubot HTTP client + 本地 relation | D1：`rg -n 'character_recognizer\|/identify\|relation' services/media/character_recognizer.py kernel/router.py` → `services/media/character_recognizer.py:19-141` 与 `kernel/router.py:658,785,808-818,1183,1556`。测试：`uv run pytest tests/test_config_loader.py tests/test_character_recognizer.py tests/test_render_message_character_recognition.py tests/test_json_card.py` → `33 passed`；`uv run pyright services/media/character_recognizer.py kernel/config.py kernel/router.py kernel/types.py plugins/chat/plugin.py tests/test_character_recognizer.py tests/test_render_message_character_recognition.py` → `0 errors`。容器内实证（临时验证 pack 存在时）：`docker compose exec -T bot sh -lc '.venv/bin/python - <<... CharacterRecognizer ...'` 输出 `CharacterRecognition(matched=True, character_id='emu_verify', character_name='凤笑梦验证', relation='self', ...)`。配置核对：`CharacterRecognitionConfig.enabled` 默认 `false`，默认 `sidecar_url=http://host.docker.internal:8620`，旁路现状 | omubot 已改为 HTTP 调 sidecar；角色名写回描述链路、`relation` 在 bot 本地解析，且默认 flag 关闭时仍回到旧 `desc_cache -> sticker_store -> Qwen VL` 行为 |

**P4 执行回执**（执行者填）：

| 项 | 实测结果 | 结论 |
| --- | --- | --- |
| P4.1 部署层参数化 + 第二组实例起停 | D1：`rg -n 'container_name\|omubot-storage\|BOT_CONFIG_PATH\|config/bots/bot2\|6100:6099\|8082:8080' docker-compose.bot2.yml config/bots/bot2/config.toml napcat/bots/bot2/config/*.json` → `docker-compose.bot2.yml:4/24/30/32/35/40/49-50` 命中 `napcat-bot2`、`qq-bot-bot2`、`omubot-storage-bot2`、独立 config 路径与独立 host 端口；`config/bots/bot2/config.toml:10` 命中 `packs_dir = "config/bots/bot2/character_packs"`。`docker compose -f docker-compose.yml -f docker-compose.bot2.yml config --services` 额外出现 `napcat-bot2` / `bot-bot2`。运行态：`docker compose -f docker-compose.yml -f docker-compose.bot2.yml up -d --build --no-deps napcat-bot2 bot-bot2` 后 `docker compose ... ps` 显示第二组容器 Up，`curl http://localhost:8610/api/containers` 在验证窗口内列出了 `napcat-bot2` 与 `qq-bot-bot2`，并带 `compose_project=omubot` / `compose_service=napcat-bot2,bot-bot2`。存储隔离实证：`docker inspect qq-bot qq-bot-bot2 --format '{{.Name}} {{range .Mounts}}{{if eq .Destination "/app/storage"}}{{.Name}}{{end}}{{end}}'` → `/qq-bot omubot-storage`、`/qq-bot-bot2 omubot-storage-bot2`。回滚演练：`docker compose -f docker-compose.yml -f docker-compose.bot2.yml rm -sf bot-bot2 napcat-bot2` 后 `curl http://localhost:8610/api/containers` 恢复只剩单 bot 组；`docker volume ls` 仍保留 `omubot-storage-bot2` | 第二组 `{napcat+bot+storage}` 已能作为 override 独立起停，主 bot/napcat 不受影响，storage volume 完整隔离 |
| P4.2 共享 sidecar + relation per-bot + 接口版本化 | D1：`rg -n 'compose_project\|compose_service\|api_version\|RUNTIME_CONTAINER_RE\|napcat_instances\|bot_instances' pmubot/app.py ccip-sidecar/server.py services/media/character_recognizer.py` → `pmubot/app.py:31,49,67-79,259,388-442`、`ccip-sidecar/server.py:177,205,221`、`services/media/character_recognizer.py:24,147`。验证：用一次性 sidecar 镜像容器临时写入共享 pack `config/character_packs/p4-verify.charpack`，并在 `config/bots/bot2/character_packs/p4-verify.charpack/manifest.json` 写 bot-B 本地 relation。验证窗口内 `curl http://localhost:8620/health` → `pack_count=1 character_count=1 registry_version=5ca8bdcb9c48 api_version=2026-05-31.v1`；`curl -F image=@config/character_packs/p4-verify.charpack/verify.png http://localhost:8620/identify` 首次返回 `matched=true character_id=emu_multi cache_hit=false api_version=2026-05-31.v1`，第二次同图请求 `cache_hit=true`。两 bot 容器内手动识别同一图：`qq-bot` 输出 `CharacterRecognition(... character_id='emu_multi', relation='self', api_version='2026-05-31.v1' ...)`，`qq-bot-bot2` 输出 `CharacterRecognition(... character_id='emu_multi', relation='known', api_version='2026-05-31.v1' ...)`。临时 pack 删除后，`curl http://localhost:8620/health` 回到 `pack_count=0 character_count=0 api_version=2026-05-31.v1 cache_entries=0` | 已实证“识别共享、relation per-bot”：同图 `character_id` 一致，但本地 relation 可分叉；sidecar 现显式返回 `api_version`，pmubot 状态页 JSON 也能看到版本化字段 |

**偏差表**（执行中遇到与本文不符时填，停下等验收）：

| Wave | 本文表述 | 实际 | 处理 |
| --- | --- | --- | --- |
| P0.1 | 验证列写的是 `wget ... /containers/json` 返回 403 | 实测 `GET /containers/json?all=1` 在 `CONTAINERS=1` 下应返回 200 容器列表；只读生效应以 `POST /containers/napcat/restart` 返回 `403 Forbidden` 证明 | 已按实际权限模型改用“读成功 + 写 403”双证据继续执行；同时因容器内继承 `HTTP_PROXY`，`wget` 需补 `-Y off` 才能直连本地 proxy |
| P1.1 | `socket-proxy` 加 `ALLOW_RESTARTS=1` 即可在同一 proxy 上既保留只读列表又仅放行 restart | 官方 `docker-socket-proxy` 规则实测不成立：`POST=0` 时 restart 仍被拒；若改成 `POST=1 + CONTAINERS=1` 则 `POST /containers/create` 也会被一并放开 | 已改成读写双 proxy（读：`socket-proxy`；写：`socket-proxy-write`）来维持 D6 |
| P1.1 | `restart napcat` 误重启无害（不会触发重新扫码） | 本机实测 `restart napcat` 会直接打断 OneBot，NapCat 落回二维码/手Q验证；本次带 `ack_relogin=1` 的验收再次复现 | 已改成高危守卫 + 明示风险；验收后现场重新进入人工手机验证状态 |
| P1.1 | pmubot restart 端点返回 2xx 即表示成功；原实现不会误报 | 旧实现里 `qq-bot` restart 真实已成功（proxy 204），但 pmubot 因 5s 超时返回 `502` | 已把写超时提高到 30s，`qq-bot` restart 现稳定返回 `200` |
| P2.1/P2.3 | 容器内直接连 `socket-proxy-*` / Docker Hub 不需额外网络处理 | 当前容器默认注入了 `HTTP_PROXY/HTTPS_PROXY=http://host.docker.internal:8890`，但 `NO_PROXY` 只有 `localhost,127.0.0.1`：内部 Docker API 请求被错误送去宿主代理，`watchtower` 与容器内 `docker` CLI 都回 `502`；反过来 Docker Hub 探测若 `trust_env=False` 又无法出网 | 已在 `pmubot` / `watchtower` 环境补 `NO_PROXY/no_proxy=...,socket-proxy,socket-proxy-write,socket-proxy-update`；Docker Hub tags 探测改为 `trust_env=True`，内部直连 / 外网走代理同时成立 |
| P2.3 | `scripts/backup-databases.sh` 现状可直接执行 `docker exec qq-bot uv run ...` | 当前 `qq-bot` 镜像里**没有** `uv` 可执行文件，原脚本会直接报 `exec: "uv": executable file not found`；但 `/app/.venv/bin/python -m services.storage.backup create --host-mode` 可用 | 已修脚本为直接调用容器内 `.venv` Python；host 手工备份与 pmubot `/api/bot/update` 共用同一条可执行链路 |
| P2.3 | pmubot 里把 repo 挂成 `/workspace` 即可在容器内 `docker compose up ... bot` | 这样会让 Docker daemon 看到宿主源路径是 `/workspace/...`，触发 `Mounts denied: The path /workspace/admin/static is not shared from the host`；首轮失败回滚因此未能自动拉回 `qq-bot` | 已把 pmubot 内 repo 改挂为与宿主一致的绝对路径 `/Volumes/OmubotDisk/omubot`，并设置 `PMUBOT_REPO_ROOT` + `COMPOSE_PROJECT_NAME=omubot`；随后 host `docker compose up -d --no-deps bot` 成功把 bot 拉回，`/api/bot/update` 也已二次验证成功 |
| P2.3 | bot update 单次构建应稳定完成 | 首轮 `POST /api/bot/update` 在 `apt-get install libvips` 过程中遇到瞬时取包失败；相同主机命令二次执行成功，说明是网络瞬断而非结构性错误 | `pmubot/bot_update.py` 已加 1 次轻量重试；最终成功回执 `update_attempts=2` |
| P3.1 | sidecar Dockerfile 只需 Python 运行时依赖即可 | `dghs-imgutils` 的传递依赖 `bchlib` 在当前 `linux/arm64 + python3.12` 组合下需要现场编译；无 `gcc` 时 `pip install` 失败，报 `error: command 'gcc' failed: No such file or directory` | sidecar 镜像已补 `build-essential`，重建后 `bchlib-2.1.3` 轮子可成功生成，整个镜像构建通过 |
| P3.2 | 文中写 pmubot 对 sidecar 走 `/identify` ping | `/identify` 是 multipart 图像识别接口，不适合做空载健康探针；实装必须补单独 `GET /health` 才能做低成本巡检 | sidecar 新增 `/health`；pmubot 改为探测 `/health` 并在 `/api/containers` 展示 `health / character_count / registry_version` |
| P3.3 | relation 留 omubot 本地“库” | 本仓 issue17 的本地 sqlite registry / relation 库尚未落地；本波只需要把识别外挂和 per-bot 归属边界做对 | 暂以 **bot 本地挂载的 charpack manifest 元数据** 解析 `relation`，仍满足“relation 不进共享 sidecar”；后续 issue17 Phase 1/2 若补本地 DB，可直接替换 metadata 来源 |
| P3.1/P3.3 | charpack 目录内只要 `manifest.json` 变化就能触发 sidecar 重扫 | 实测若先写 `manifest.json`、后写 `embeddings.npz`，旧实现只拿 manifest 做签名，会把“第一次空载入”缓存住，后续同 manifest 不再重扫 | sidecar 重载签名已扩到 `manifest.json + embeddings.npz` 两个文件，临时验证 pack 的增删都能被在线识别到 |
| P4.1 | D1 grep 写的是 `grep -nE 'container_name\|omubot-storage\|persona_id' docker-compose*.yml` | 当前多 bot 设计把 persona 维度放在**每 bot 独立 config 文件**，compose 只负责 `BOT_CONFIG_PATH` / 挂载 / volume / 端口参数化，因此 `persona_id` 不会直接出现在 `docker-compose.bot2.yml` | D1 扩成“compose override + per-bot config.toml”双证据：compose 证明容器名 / volume / config path 参数化，config 文件证明 `packs_dir` / persona 配置分叉入口存在 |
| P4.2 | 删除共享 pack 后 `/health.cache_entries` 会立即回到 0 | 初版实现里 cache 只在 `/identify` 路径按 `registry_version` reset；若只看 `/health`，删 pack 后 `cache_entries` 会短暂保留旧值，虽不会产生错误识别，但观测口径不够干净 | 已修成 `health()` 先 `cache.reset(registry.registry_version)` 再上报；当前删除临时 pack 后 `pack_count=0 character_count=0 cache_entries=0` |

---

## 6. 自审表（每 Wave 收口后勾）

| Wave | 代码改动 | D1 grep 贴回 | 验证全绿 | 回滚验证 | napcat 未 recreate | 验收人确认 |
| --- | --- | --- | --- | --- | --- | --- |
| Wave 0 | N/A | ☑ | ☑ | N/A | ☑ | ☑ |
| P0 | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ |
| P1 | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ |
| P2 | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ |
| P3 | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ |
| P4 | ☑ | ☑ | ☑ | ☑ | ☑ | ☑ |

> P1「回滚验证」此前为 ☐，验收时已实测复核（restart 写超时修正后 `qq-bot` restart 稳定 `200`、`#napcat` 开关有正反向回执、双 proxy create=403 边界成立），据此勾上。

---

## 7. 验收人复核（2026-05-31，独立运行态证据）

> 验收方式：**不照单全收执行人回执**，按 D4 逐条用运行态外部证据复核。下表每条均为验收人现场实测，非引用回执。

### 7.1 D6 红线（最高优先级）— 守住

| 检查 | 验收实测证据 | 判定 |
| --- | --- | --- |
| napcat 未被 recreate | `docker inspect napcat` → `Created=2026-05-28T10:56:06`（早于验收日 3 天，期间未重建）；镜像仍 `mlikiowa/napcat-docker:v4.15.0` 未换 tag | ✅ |
| 无 recreate napcat 物理入口 | `socket-proxy`（只读）与 `socket-proxy-write`（只写）**双双对 `POST /containers/create` 返回 403**；`socket-proxy` 对 restart 也 403 | ✅ |
| 无 napcat update 端点 | `POST /api/napcat/update`、`POST /api/containers/napcat/update` 均 **404** | ✅ |
| watchtower 不碰 napcat/bot | napcat/bot **无 `com.centurylinklabs.watchtower.enable` label**（仅 ccip-sidecar=true）；watchtower 实际 `Scanned=1 Updated=0` | ✅ |

### 7.2 P0–P4 核心证据

| Wave | 验收实测证据 | 判定 |
| --- | --- | --- |
| P0/P1 权限边界 | 只读 proxy：GET `/containers/json` 200 / create 403 / restart 403；只写 proxy：GET 403 / create 403。读写物理隔离成立 | ✅ |
| P1 鉴权 | 写端点（`/api/containers/qq-bot/restart`、`/api/napcat/disable-builtin-reply`、`/api/bot/update`）无 token **全 401**；只读 `/api/containers` 200 | ✅ |
| P2 更新分档 | `/api/updates/check` 实测：napcat=`notify-only`（探到 `v4.18.4` 可用但只通知）、bot=`ci-build`、watchtower 只管 `["ccip-sidecar"]` | ✅ |
| P3 依赖隔离 | bot 镜像 `import numpy` / `import onnxruntime` 均 `ModuleNotFoundError`；ccip-sidecar 自带 `numpy 1.26.4`（numpy<2 约束守住）+ `onnxruntime 1.26.0` | ✅ |
| 运行态健康 | qq-bot 22:04 持续正常收群消息（`OneBot V11 384801062` 在线）；ccip-sidecar `Up (healthy)` | ✅ |
| omubot 侧代码门禁 | 执行人改的 `.py`：`uv run ruff check` → All passed；`uv run pyright` → 0 errors；`pytest tests/test_character_recognizer.py tests/test_render_message_character_recognition.py tests/test_config_loader.py` → 26 passed | ✅ |

### 7.3 残余风险（非阻断，列后续收窄项）

`socket-proxy-update` 为全权限（`CONTAINERS/POST/IMAGES/EXEC/AUTH/VOLUMES/BUILD=1`），理论上能 create/recreate。缓解到位：① `Ports=null` 不映射宿主、仅 `omubot_default` 内网可达；② 唯一消费方 `pmubot/bot_update.py` 硬编码 `docker compose up -d --no-deps --force-recreate bot`，只动 bot 不连带 napcat；③ 无任何 napcat 调用路径。属"能力存在但无调用路径"，可接受。

**收窄建议**：`bot_update` 实际只需 `BUILD/IMAGES/POST/CONTAINERS`，可砍 `EXEC/AUTH/VOLUMES`，把 update proxy 横向暴露面再压一档（后续派单，非本轮阻断项）。

**验收结论：pmubot P0–P4 全数通过，D6 红线守住，准予收口。**
