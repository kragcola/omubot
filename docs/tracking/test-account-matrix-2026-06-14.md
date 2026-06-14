# 测试账号矩阵 runbook（4× 纯 NapCat + HTTP，不接 LLM）

> 范围：管理员号 + 3 普通号，仅做收发/行为测试，统一由 pmubot 容器编排纳管。
> 不接 omubot 大脑（无 LLM、无人设）。生产 napcat（emu 384801062）/ bot / pmubot 全程不动。
> 创建日期：2026-06-14

## 架构

```
QQ  ←→  napcat-test-admin  (HTTP 127.0.0.1:29320, WebUI 6101)   管理员号
QQ  ←→  napcat-test-1      (HTTP 127.0.0.1:29321, WebUI 6102)   普通号 1
QQ  ←→  napcat-test-2      (HTTP 127.0.0.1:29322, WebUI 6103)   普通号 2
QQ  ←→  napcat-test-3      (HTTP 127.0.0.1:29323, WebUI 6104)   普通号 3
                  │
                  └─ websocketClients = []  →  不连任何 bot，纯 HTTP 收发
```

- 每实例独立 `napcat/bots/test-<role>/{config,data}`，登录态/设备指纹与生产 emu 完全隔离。
- HTTP 口 `token=""`、bind `127.0.0.1`：**仅本机可达、无鉴权——只准 dev 用，严禁公网/生产**。
- 由 `docker-compose.test-matrix.yml` 独立编排，起停不触碰主栈（emu/bot/pmubot）。
- pmubot 靠容器名正则 `^(?:napcat|qq-bot)(?:-.+)?$` 自动纳管，控制台可统一看起停/日志/stats。

## 端口表

| 实例 | 角色 | WebUI（宿主） | OneBot HTTP（宿主） | 容器内口 |
|------|------|------|------|------|
| napcat-test-admin | 管理员 | 6101 | 29320 | 6099 / 29300 |
| napcat-test-1 | 普通1 | 6102 | 29321 | 6099 / 29300 |
| napcat-test-2 | 普通2 | 6103 | 29322 | 6099 / 29300 |
| napcat-test-3 | 普通3 | 6104 | 29323 | 6099 / 29300 |

避开：emu(6099/29300/29301)、bot2(6100/29310/29311)。

## 起停

```bash
# 起全部 4 实例（不影响主栈）
docker compose -f docker-compose.test-matrix.yml up -d

# 只起单个
docker compose -f docker-compose.test-matrix.yml up -d napcat-test-admin

# 看状态 / 日志
docker compose -f docker-compose.test-matrix.yml ps
docker logs --tail 50 napcat-test-admin

# 停全部（保留登录态 data，下次起免重扫）
docker compose -f docker-compose.test-matrix.yml stop

# 彻底拆除（保留 data 目录，仅删容器）
docker compose -f docker-compose.test-matrix.yml down
```

> ⚠️ `down` / `stop` 只作用于 test-matrix 这 4 个容器，**不会**碰 emu/bot/pmubot。
> 重启已登录的测试实例可能掉登录态（同 napcat 风控特性），非必要别 restart；要重连优先 stop→start。

## 首次扫码登录（各实例 WebUI）

4 个实例各开独立 WebUI，浏览器逐个扫码：

| 实例 | WebUI 地址 | token |
|------|------|------|
| admin | http://127.0.0.1:6101/webui | pmubot-test-admin-webui |
| 普通1 | http://127.0.0.1:6102/webui | pmubot-test-t1-webui |
| 普通2 | http://127.0.0.1:6103/webui | pmubot-test-t2-webui |
| 普通3 | http://127.0.0.1:6104/webui | pmubot-test-t3-webui |

步骤：打开 WebUI → 输 token 登录 → 「网络配置」确认 HTTP server 已启用 → 扫码登录对应 QQ 号。
扫码后 `data/` 落登录态，后续 stop/start 免重扫。

## HTTP 收发自测

每实例 OneBot 11 HTTP API（无 token）。示例打 admin（29320），其余换端口即可。

```bash
# 查登录态（确认账号在线 + uin）
curl -sX POST http://127.0.0.1:29320/get_login_info -d '{}'

# 在线状态
curl -sX POST http://127.0.0.1:29320/get_status -d '{}'

# 群列表 / 好友列表
curl -sX POST http://127.0.0.1:29320/get_group_list -d '{}'
curl -sX POST http://127.0.0.1:29320/get_friend_list -d '{}'

# 发群消息（文本）
curl -sX POST http://127.0.0.1:29320/send_group_msg \
  -H 'Content-Type: application/json' \
  -d '{"group_id":<GID>,"message":[{"type":"text","data":{"text":"test from admin"}}]}'

# 发私聊
curl -sX POST http://127.0.0.1:29320/send_private_msg \
  -H 'Content-Type: application/json' \
  -d '{"user_id":<UID>,"message":[{"type":"text","data":{"text":"hi"}}]}'

# 撤回（best-effort，超时限会 timeout）
curl -sX POST http://127.0.0.1:29320/delete_msg -d '{"message_id":<MID>}'
```

普通号端口：t1=29321 / t2=29322 / t3=29323。

## pmubot 控制台纳管验证

```bash
# 4 实例应出现在纳管列表，compose_project=omubot 之外按名字正则纳管
curl -s http://localhost:8610/api/containers | python3 -m json.tool | grep -E 'napcat-test'
```

## 安全红线

- HTTP/WebUI 全 bind `127.0.0.1`，仅本机。**严禁**改 `0.0.0.0` 或映射到公网。
- HTTP token 为空 = 无鉴权，仅因纯本机 dev 才可接受；任何对外暴露前必须加 token。
- 测试号与生产 emu 物理隔离（独立 data/config/容器/端口/storage），互不串号。

## 回滚

```bash
docker compose -f docker-compose.test-matrix.yml down   # 删 4 容器
rm -rf napcat/bots/test-{admin,t1,t2,t3}                 # 删配置+登录态（彻底清场）
rm docker-compose.test-matrix.yml docs/tracking/test-account-matrix-2026-06-14.md
```

主栈（emu/bot/pmubot）零影响，无需回滚。

## 压测记录 · 2026-06-14 首轮（三普通号 → 984198159「测试」群）

三号在线：Kucycx(2192849458)/七喜bot(1368720427)/爱笑的紫毛奶奶(3808827879)。
admin(1416930401) 与电脑端 QQ 互斥，按决定不接入。
984198159 配置前提全绿：`access.whitelist` 含该群、`presence_mode=active`、`arbiter.enabled=true runtime_groups=[] interruption_enabled=true`。

**第一轮 — @bot + Arbiter A 完整性**：三号快速 burst（演唱会话题）末条 @emu。
- `arbiter_a_fire confidence=0.95 fallback=False` 正向样本 ✅
- emu 完整回复 + 表情包二轴：`post_reply_sticker_send sent=True prob=0.932 thinker=True energy=0.64 valence=+0.42`
- burst 后续 `busy,skip` 正确合并

**第二轮 — 跨号生成中插入**：29322 @emu 触发，3s 后 29323/29321 插入异话题。
- Arbiter A 再 fire（confidence=0.95）；插入消息进 `pending_during_generation`
- monitor arm 后 `judge_interruption` 判 `continue`（静默路径，无日志）——换话题不构成中断强信号，**符合预期**
- 诊断：`log.channels.debug=false` 吞掉了 monitor 的 `queued/same-addressee` debug 行（非 bug）

**第三轮 — 同号同 block 强打断（成功撞 Arbiter B）**：29322 @emu「讲超长故事」触发，t+1.2s/t+1.8s 同号连发 @「别讲故事了」「改讲笑话」。
- **`arbiter_b_abort action=revise reason=用户新消息对 bot 即将展开的故事进行了否定和打断，需要调整回复方向`** ✅ **dc0c0f3 修复后首个正向 B 样本**
- 链路：abort → remerge → `arbiter_a_fire pending=2 confidence=0.90` → emu 输出**从长故事改为笑话**（群内可见），证明 abort_unsent+revise 真实改变输出

**结论**：@bot 立即触发、Arbiter A 完整性、Arbiter B 中断重判（revise）三条路径端到端验证通过；表情包二轴（energy/valence/thinker veto）、reply_workflow 群门控影子模式均正常。Arbiter B 触发要害 = **同号同 block + 抢在已发段前/强否定语义**；跨号换话题判 continue 是正确保守行为。
