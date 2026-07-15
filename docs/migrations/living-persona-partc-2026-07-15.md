# Living Persona Part C + Reliability Migration Checklist

> 状态：**deployed / complete**，2026-07-15。
> 范围：Living Persona 全链 reliability 修复 + Part C 真人 Social Narrative（`plugins/social_narrative` v0.1.0）+ 生产污染迁移。
> Implementation commit：**`98887a548eb574f5ab0d068b1529ad06e53f88aa`**。
> 生产 runtime GIT_COMMIT：exact `98887a548eb574f5ab0d068b1529ad06e53f88aa`。
> Pre-commit HEAD base：`0e6827762aaaa28336d0e8c6481993452dfd7565`。

## 1. Dream Reliability

- [x] 旧：重启可同日多次 Dream 批处理放大写卡；新：同一自然日幂等，重复触发不追加同日批。
- [x] 旧：reflection scope 可接受模型生成的伪 scope（`global/self`、地点名等）；新：仅真实 group ID 或 `global/global`。
- [x] 旧：cancel / shutdown 可能半写；新：cancel-path 可观测，外部状态不污染下一次执行。
- [x] 旧：卡片与 Arc 更新重试边界不清；新：可重试且不半写。

## 2. StoryArc Lifecycle

- [x] 旧：过期/已结束 arc 仍可推进（如 `generated_days` 与日期继续增长）；新：生命周期 `not_started` / `active` / `terminal` / `expired`。
- [x] 旧：无正式归档；新：过期 arc 归档，停止 arc 与 fiction partner 状态继续传播。
- [x] 旧：并发更新保护不足；新：并发更新保护 + 干净存储启动策略。

## 3. Climate Post-Reply

- [x] 旧：Schedule/Climate post-reply 权限缺失导致 PluginBus 不 fire（callback=0）；新：真实 manifest permissions + `PluginBus.fire_on_post_reply`。
- [x] 旧：同 session 多用户 snapshot 互相覆盖；新：per-user snapshot 隔离（session+user）。
- [x] 旧：permission denial 难观测；新：health/可观测路径暴露权限拒绝。

## 4. Persona Fail-Fast / Hot-Reload

- [x] 旧：初次加载失败可能带病启动；新：initial load 失败 fail-fast 中止。
- [x] 旧：hot-reload 失败可能清空/替换为坏 bundle；新：失败时继续持有旧 bundle。

## 5. Affection / Climate / Willingness

- [x] 旧：affection / climate / willingness / 旧 coupling 边界模糊；新：明确 producer/consumer，合法 M2/M3/M4 组合不吞旧信号。
- [x] 接线与 focused tests 覆盖关系温度与 climate 指导不串写、不错归。

## 6. Backup / Catalog

- [x] 旧：catalog 未登记 social narrative / 部分 Living Persona 状态；新：`services.social_narrative` 登记为 client。
- [x] 旧：story_arc / partner / social factual 可能落在备份集外；新：纳入 daily/migration/pre-change 备份边界（`memory_cards.db` 统一 critical 边界 + 独立表）。

## 7. Health Observability

- [x] 旧：PluginBus 权限拒绝静默；新：permission denial 可观测（health/诊断路径）。
- [x] Living Persona 运行状态在 admin/health 可揭示（开关与关键断链）。

## 8. Part C Social Narrative

- [x] 旧：Part C 未实装；新：`plugins/social_narrative` v0.1.0（kind=user，`restart_required`，memory）+ `services/social_narrative`。
- [x] 旧：无独立 factual 存储；新：`memory_cards.db` 独立表，**不**复用 `memory_cards.scope` 单维度。
- [x] 旧：无证据契约；新：仅 `factual`；必须 `group_id`/`user_id`/evidence message id/time/source；无证据不写。
- [x] 旧：可能私聊→群、fiction 混写、昵称/猜测升格；新：禁止 private→group；禁止 fiction/factual 混写；昵称/猜测/情绪分类/LLM 补全不得升格为事实。
- [x] 旧：无默认开关；新：fail-closed 默认 `enabled=false`，`allowed_group_ids=[]`；跨群默认不共享。
- [x] 幂等 key：`(group_id,user_id,evidence_message_id,evidence_source)`；撤回/删除后派生可失效。
- [x] 生产配置：`/app/storage/plugins/config/social_narrative.json` — `schema_version=1`，`enabled=true`，allowlist exact `["984198159","993065015"]`；观察时 store empty healthy（0 active / 0 entities）。

## 9. Production Data Migration

- [x] **已 apply**（bot stopped freeze → trusted backup → remediation apply）。
- [x] 审计 / freeze 结论（**历史 scope 总量**，非 active-only）：
  - total **224**
  - valid `global/global` **15** = **4 active + 11 superseded**（合法保留）
  - valid test-group rows **0**
  - invalid total **209** = **56 active + 29 expired + 124 superseded**
  - 均缺 evidence 时间/消息，**禁止改归属**
- [x] pre-change **named-volume 备份**（apply 使用）：
  - ID `pre-change-20260715-201745`
  - path `/app/storage/backups/pre-change/2026-07-15-201745`
  - created `2026-07-15T20:18:15.407203+08:00`
  - `schema_version=2`，`complete=true`，`trusted=true`
  - memory_cards SHA256 `081c43e0a408f2ada0c7b323445ab0078f156025d88848c7973cdf19404aee3a`
  - `quick_check=ok`；`build_restore_plan` memory_cards one item，`can_apply=true`，`compatibility=upgrade_on_start`
  - extra recovery（未用于 apply）：`pre-change-20260715-200450`
- [x] **209 invalid historical scope rows total**；其中 **56 still active at freeze** 改为 `expired`；**153 already non-active retained**（29 expired + 124 superseded 原 status 不变）。**不是**「209 rows all changed」。
- [x] apply：`expired_count=56`；`before.invalid_active=56`；`after.invalid_active=0`；`quick_check=ok`；无 delete / 无 reassignment；224 行保持 semantic identity/content/scope。
- [x] 15 `global/global` historical total **keep**（frozen 4 active / 11 superseded）。
- [x] 过期 StoryArc `stage_play_competition_week` **archived**（active ledger empty；archive 含该 JSON；**no seed**）。
- [x] post-deploy independent compare：backup SHA match；restore plan usable；224 semantic equal；candidate 56 all expired；invalid noncandidate statuses equal；live invalid total remains 209 且 `invalid_active=0`。
- [x] **count-gate / Dream 增长边界**：freeze/apply 数字是 migration 真值。正常 post-start Dream 可写新合法卡（抽样曾见 live total 226 / valid 17）；不得把动态 valid 总量误读为 migration drift。

## 10. Delivery Checklist

- [x] 代码实装 + focused RED→GREEN tests + same-pattern scan（Dream cancel、Climate reply perm、persona fail-fast、backup clients、evidence 边界、private 排除）。
- [x] full pytest：**3686 passed / 17 skipped / 202 warnings / 0 failed in 63.78s**。
- [x] scoped Ruff clean；scoped Pyright 0。
- [x] 24 manifests valid；plugin layout clean；72/72 plugin JSON parse；`git diff --check` clean。
- [x] independent review：**0 Critical / 0 open Important**。
- [x] 本 packet 文档：tracker + ACTIVE + 本迁移清单 + `docs/wiki/Plugins.md`（包数 24/22/20 与 `social_narrative` 真值）。
- [x] implementation commit 精确 SHA：`98887a548eb574f5ab0d068b1529ad06e53f88aa`。
- [x] 生产 named-volume 备份（trusted `pre-change-20260715-201745`）。
- [x] 污染迁移 dry-run → apply（见 §9）。
- [x] bot-only 部署：`docker compose build bot` 因 Buildx refs EPERM 失败；成功用 `DOCKER_BUILDKIT=1 docker build --build-arg GIT_COMMIT=98887a548eb574f5ab0d068b1529ad06e53f88aa -t omubot-bot:latest .`；再 `docker compose up -d --no-deps --force-recreate bot`（非 compose build 成功）。
  - 旧：container `0f7f47c3ffae…`；image `sha256:56f51b2ce8d5…`；runtime `d51a7d41…`
  - 新：container `4199a39340f0…`；image `sha256:01c68ae819b2…`；StartedAt `2026-07-15T12:20:22.459948294Z`；runtime exact `98887a548…`；restart=0；OOM=false；running
  - rollback tag：`omubot-bot:pre-living-persona-98887a5-20260715` → 旧 image
- [x] **NapCat 全程不 restart / 不 recreate / 不改存储布局**。
  - container `19f6cf13607c…`；image `sha256:cde89d766604…`；StartedAt `2026-07-09T22:51:47.963549084Z`；restart=0；OOM=false；running
- [x] 部署后观察：
  - startup banner exact commit；Application startup complete；OneBot `384801062` connected；group outbound guard installed；Bot ready
  - `/admin/` 200；authenticated Admin health：`social_narrative` ok，0 active / 0 entities，allowlist exact two
  - no ERROR/CRITICAL/Traceback since start
  - 被动窗 UTC `2026-07-15T12:25:18Z`–`12:28:18Z`：92 lines；46 `message.group`；46 `silent_learn`；public inbound groups `805836168`/`860324414`/`477640404`/`953023811`/`963085812`；authorized inbound 0；bot outbound send/poke 0；window errors none；no QQ probes
- [x] maintenance-log 证据；post-deploy 记录 implementation SHA 与 rollback image/tag。
- [x] 回滚路径入口：关 `social_narrative`/Living Persona flags；recreate bot from pre-change image/tag；NapCat 不动。

## 回滚（部署后）

1. 配置：`social_narrative.enabled=false`，必要时关 Dream/Climate/StoryArc 相关 flags。
2. 镜像：用 tag `omubot-bot:pre-living-persona-98887a5-20260715`（image `sha256:56f51b2ce8d58c268085ad3bcdf1a31335e730e31734826ea1b6636bafac1a44`，runtime `d51a7d41…`）`--force-recreate bot`（仅 bot）。
3. 数据：expired 标记可审计保留；必要时从 trusted backup `pre-change-20260715-201745`（path `/app/storage/backups/pre-change/2026-07-15-201745`，SHA `081c43e0…`，restore plan `can_apply=true`）按 restore plan 恢复 memory_cards；禁止用 NapCat 重启“试修复”。
4. **永不** `docker compose down`/`up` NapCat，**永不** recreate NapCat。
