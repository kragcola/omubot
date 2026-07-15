# Living Persona Part C + Reliability Migration Checklist

> 状态：**code complete / not deployed**，2026-07-15。
> 范围：Living Persona 全链 reliability 修复 + Part C 真人 Social Narrative（`plugins/social_narrative` v0.1.0）+ 生产污染迁移方案。
> 生产 runtime 仍为 `d51a7d41bed5b031659e09dcfd148c10e6cd4e0a`。精确 implementation commit SHA **TBD**（commit 在文档 packet 之后；记入 post-deploy docs checkpoint，禁止编造）。
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

## 9. Production Data Migration

- [ ] **尚未 apply**。仅方案与工具就绪（`tools/living_persona_remediation.py` dry-run/apply）。
- [x] 审计 dry-run 结论：224 dream_reflection；15 合法 `global/global`；209 invalid scopes；均缺 evidence 时间/消息，**禁止改归属**。
- [ ] pre-change **named-volume 备份**（必须先于 apply）。
- [ ] 209 invalid scopes → `expired`（保留 content/ID；不物理删除、不 reassignment）。
- [ ] 15 `global/global` **keep**。
- [ ] 过期 StoryArc（如 `stage_play_competition_week`）按新生命周期 **archive**。

## 10. Delivery Checklist

- [x] 代码实装 + focused RED→GREEN tests + same-pattern scan（Dream cancel、Climate reply perm、persona fail-fast、backup clients、evidence 边界、private 排除）。
- [x] full pytest：**3686 passed / 17 skipped / 202 warnings / 0 failed in 63.78s**。
- [x] scoped Ruff clean；scoped Pyright 0。
- [x] 24 manifests valid；plugin layout clean；72/72 plugin JSON parse；`git diff --check` clean。
- [x] independent review：**0 Critical / 0 open Important**。
- [x] 本 packet 文档：tracker + ACTIVE + 本迁移清单 + `docs/wiki/Plugins.md`（包数 24/22/20 与 `social_narrative` 真值）。
- [ ] implementation commit 落盘后，精确 SHA 记入 **post-deploy docs checkpoint**（pre-commit base `0e68277…`；本清单不预写 SHA）。
- [ ] 生产 named-volume 备份。
- [ ] 污染迁移 dry-run → apply。
- [ ] bot-only 部署：`docker compose build bot` + `docker compose up -d --no-deps --force-recreate bot`。
- [ ] **NapCat 全程不 restart / 不 recreate / 不改存储布局**。
- [ ] 部署后观察：startup/OneBot；测试群按需启用 social_narrative；公开 silent 群固定窗零成功出站。
- [ ] maintenance-log 证据；post-deploy 记录 implementation SHA 与 rollback image。
- [ ] 回滚路径验证入口：关 `social_narrative`/Living Persona flags；recreate bot from pre-change image；NapCat 不动。

## 回滚（未部署前 / 部署后通用）

1. 配置：`social_narrative.enabled=false`，必要时关 Dream/Climate/StoryArc 相关 flags。
2. 镜像：用 pre-change bot image `--force-recreate bot`（仅 bot）。
3. 数据：expired 标记可审计保留；不要求物理还原；禁止用 NapCat 重启“试修复”。
4. **永不** `docker compose down`/`up` NapCat，**永不** recreate NapCat。
