# Persona v2 Only Cutover (C 系列)

> 状态：2026-05-27 完成。三段提交（C1 → C2 → C3）已落地，C4（本文）为收口。
> 目标：在「无需考虑 v1 兼容」的前提下，将 persona 全量切到 v2 runtime，删除 v1 静态块、影子比对、parity audit、IdentityManager、SoulConfig、Soul 编辑面板。
> 上游：[persona-source-importer](persona-source-importer.md) / [persona-v2-importer](../migrations/persona-v2-importer.md) / `_archive/persona-runtime-cutover-B[1|2|3]-execution.md`（B 系列已并入本切换，归档保留供历史参考）。

---

## 1. 设计决策（v2 only，无回滚）

| 决策 | 取舍 |
|---|---|
| **删 v1 而非保留 fallback** | 用户明示「无需考虑 v1 兼容」；保留 fallback 会导致 PromptBuilder / LLMClient / router 三处分支永远存在两套路径，长期维护成本高于一次性切换 |
| **PersonaRuntime 单例 + bind_bot_self_id 占位符替换** | `{bot_self_id}` 在 source.md 时未知（freeze 在容器外执行）；改成 connect 时由 `_on_connect` 注入 bot_self_id 后做字符串替换，比 freeze 时硬编码更灵活 |
| **LKG 保护 swap_bundle** | `swap_bundle()` 失败时保留 last-known-good bundle；避免热重载错误把 runtime 打成无 prompt 状态 |
| **删 SoulConfig 而非弃用** | 没有 v1 静态块就没有 `config/soul/*.md` 的存在意义；保留 SoulConfig 会让 admin SPA 出现「编辑 → 不生效」的死按钮 |
| **/api/admin/persona/hot-reload/{id} confirm-gated** | 热重载会原地替换 prompt 静态块；要求 `confirm=true` 防误触；失败保留 LKG |

## 2. 三段提交

| 提交 | 摘要 | 行数变化（净）|
|---|---|---|
| `208fd53` C1 — PersonaRuntime singleton (additive, v2 cutover prep) | 新增 PersonaRuntime（线程安全单例 / load / swap_bundle / bind_bot_self_id / static_text / group_profile_text / identity_snapshot / last_error）+ `IdentitySnapshot` v2 替换 + 接入 PluginContext + 单元测试。**完全 additive**，不动 v1 路径 | +约 750 |
| `0f58eae` C2 — full v2 cutover; remove v1 identity/shadow/selector | 删 IdentityManager / build_static / persona_runtime_selector / shadow.py / parity_audit.py；切换 PromptBuilder / LLMClient / router / scheduler 到 runtime；清掉 PersonaV2Config 中只剩 `persona_id` 一个字段；config.json/config.toml 删 `persona_v2.runtime_consume / shadow_compare / runtime_groups / fallback_on_compile_error`；conftest.py 加共享 fixtures；重写 16 个相关测试 | -约 3 500 / +约 1 200 |
| `629583d` C3 — retire legacy soul editor; expose v2 hot-reload | 删 admin/routes/api/soul.py（530 行）+ SoulView.vue / SoulPersonaGuideView.vue（共 1796 行）；删 SoulConfig + bot.py soul.dir 日志；BackupItem `soul` → `persona`；新增 POST /api/admin/persona/hot-reload/{id}（confirm-gated）；PersonaImporterView.vue 加热重载按钮（NPopconfirm 包裹）；/soul 与 /soul/persona-guide 重定向到 /persona-importer；侧边栏「人设编辑」条目删除，「人设导入」改名「人设管理」；test_admin_api.py 删 5 条旧 soul 测试 + 加 4 条 hot-reload 测试；test_backup_service fixture 从 config/soul/identity.md 改成 config/persona/fengxiaomeng-v2/source.md；test_config_loader 删 `cfg.soul.dir` 断言 | -2 579 / +146 |

## 3. 关键文件

| 文件 | 状态 | 说明 |
|---|---|---|
| [services/persona/runtime.py](../../services/persona/runtime.py) | NEW (C1) | PersonaRuntime 单例 + load_pending_freeze + bind_bot_self_id |
| [services/persona/runtime_selector.py](../../services/persona/runtime_selector.py) | DELETED (C2) | runtime selector 已并入 PersonaRuntime |
| [services/persona/shadow.py](../../services/persona/shadow.py) | DELETED (C2) | v2 only 不再需要影子比对 |
| [services/persona/parity_audit.py](../../services/persona/parity_audit.py) | DELETED (C2) | v2 only 不再需要 parity 审查 |
| [services/identity/__init__.py](../../services/identity/__init__.py) | DELETED (C2) | IdentityManager 退役；`Identity` 数据类由 `IdentitySnapshot` 替代 |
| [admin/routes/api/soul.py](../../admin/routes/api/soul.py) | DELETED (C3) | v1 soul 编辑面板退役 |
| [admin/frontend/src/views/soul/](../../admin/frontend/src/views/soul/) | DELETED (C3) | SoulView / SoulPersonaGuideView 退役 |
| [admin/routes/api/persona_importer.py](../../admin/routes/api/persona_importer.py) | UPDATED (C3) | 新增 `/hot-reload/{persona_id}` 端点 |
| [admin/frontend/src/views/persona/PersonaImporterView.vue](../../admin/frontend/src/views/persona/PersonaImporterView.vue) | UPDATED (C3) | 标题改「人设管理」+ 加热重载按钮 |
| [kernel/config.py](../../kernel/config.py) | UPDATED (C2/C3) | PersonaV2Config 字段缩到 `persona_id` 一个；删 SoulConfig |

## 4. 验证

| 项 | 结果 |
|---|---|
| ruff | All checks passed |
| pyright | 472 errors（基线，未引入新增；C1/C2/C3 触及文件 0 新增）|
| pytest 全量 | 1933 passed / 8 skipped |
| frontend vue-tsc | clean |
| frontend npm run build | PersonaImporterView 进 bundle；SoulView/SoulPersonaGuideView 不在 bundle |
| C3 后 admin 静态 bind mount | `admin/static/index.html` 已更新；deploy 端 restart bot 即可同步前端 |

## 5. 后续（非本次范围）

- bot 镜像 rebuild：`dot_clean . && docker compose up bot -d --build` 后 connect-time 验证 PersonaRuntime 静态块生效
- 用户最终验收：灰度群（993065015 / 984198159）观察 ≥ 1 轮对话后签收
- `docs/migrations/persona-v2-importer.md §12` 表格在 v2 only 环境下重新校对（旧 parity / shadow 行可标 ✅ retired）
- `docs/tracking/omubot-grayscale-progress-tracker.md §3` Persona 主线由 B1-B6 改为 v2-only 单行收口（B1/B2/B3 ✅ 归档；B4/B5/B6 演进路径取消，因 v2 已全量）

## 6. 回滚

不再支持。如需回到 v1 静态块，需 revert `629583d 0f58eae 208fd53` 三个提交并修复 conftest fixture / kernel/config 的 PersonaV2Config schema。这条回滚路径**未维护**，不在本次范围内。
