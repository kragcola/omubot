# QZone Journal v0.7 - Closed-Template Public Projection

> 日期：2026-07-16
> 状态：implemented offline, not deployed, not committed.
> 范围：factual Part C 公开投影改为 **by-construction** 闭集模板渲染；共享严格 metadata 校验；selector/store/provenance/Admin 接线。无 SQLite schema migration、无真实 QZone HTTP、无凭据读取、无 validated profile、无 Docker/NapCat 操作。

## Old -> New

| 面 | v0.6 / 启发式草稿 | v0.7.0 |
| --- | --- | --- |
| 公开正文来源 | free-form exact-name 替换 / CJK unbound-name 猜测，可能把 raw narrative 片段带入 `projected_summary` | 仅由闭集 `public_template_id` + 校验后的 generic `public_label` 渲染；raw 永不贡献公开正文 |
| 生产者输入 | 可选投影；裸 factual 可能被不同 reason 拒绝 | 生产者选择与 claim class 兼容的 `public_template_id`，并完整提供 alias 映射；裸 factual 可构造但 selector → `reject_public_projection` |
| 标签策略 | 任意中文昵称可作 `public_label` | 仅 `GENERIC_PUBLIC_LABELS` 闭集（如 `一位朋友` / `伙伴甲`） |
| claim 类 | 宽松 / 可塞 `private_relationship` | 仅 `social_public_event` / `attendance` / `milestone` |
| 源绑定 | 弱或仅摘要 hash | `compute_source_event_hash` v1 绑定 raw + **ordered** `(position, entity_key, surface, alias_id, public_label)` + claims + `public_template_id`；reorder/reassign 改 hash |
| Producer 载体 | 无 / 浅层 dict | `JournalEventRecord.public_projection` **deep-freeze**（`MappingProxyType` + nested tuples）；`to_dict` deep thaw，禁止构造后嵌套静默改写 |
| Store 消毒 | `int(True)` 等宽松类型；空 claim 列表、任意 policy/label、重复 alias_id 可能通过 | `validate_public_projection_metadata` 严格类型（bool≠int）、闭集 policy/schema/template/claim、非空有界列表、标签 allowlist、唯一 alias_id/label、未知键/内部 ref 拒绝；store 复用同一函数 |
| 插件版本 | `0.6.0` | `0.7.0` |
| Provenance | schema v1 为主 | factual 投影走 schema v2 + `public_projection`（含 `public_template_id`）；Admin 只展示安全字段 |

## Closed Templates (minimal)

| `public_template_id` | Claims | Arity | Pattern (labels only) |
| --- | --- | --- | --- |
| `social_public_event_solo_v1` | social_public_event | 1 | 今天和{0}一起参加了公开活动 |
| `social_public_event_duo_v1` | social_public_event | 2 | {0}和{1}一起参加了公开活动 |
| `attendance_solo_v1` | attendance | 1 | {0}到场了 |
| `attendance_duo_v1` | attendance | 2 | {0}和{1}到场了 |
| `milestone_solo_v1` | milestone | 1 | 和{0}一起达成了一个里程碑 |
| `milestone_duo_v1` | milestone | 2 | 和{0}、{1}一起达成了一个里程碑 |

## Frozen Contract

- 公开输出**只能**由代码模板 + 闭集 generic label 渲染；禁止从 raw summary 复制任意叙事。
- `public_label` 必须在 `GENERIC_PUBLIC_LABELS`；真实中文昵称（如「阿花」）一律拒绝。
- raw summary 仅作 hash-bound 源输入与 surface 完整性检查；不得进入 CandidateEvent 正文、composer、provenance 公开字段或 Admin UI。
- `InternalIdentityRef` 仅 transformation-only，不可 public-serialize。
- bare factual `CandidateEvent` 可构造，但 selector 必须 `reject_public_projection`；同 tick 硬门禁拒绝不得毒化 dedupe key。
- Store / projector 共用 `validate_public_projection_metadata`：精确类型、无静默截断、无未知键。
- `JournalEventRecord` 对 `public_projection` deep-freeze；外部/post-construction 嵌套 mutation 不得改写冻结载体。
- 已删除 CJK unbound-name 启发式及“任意 raw 可安全去标识”的主张。
- `BUILTIN_WIRE_PROFILE.validated` 必须保持 `false`。
- 真实 QZone HTTP、凭据读取、Docker/NapCat、commit/deploy 仍为 NO-GO。

## Codex Counterexamples (must remain rejected / non-leaking)

1. raw `小明、阿花一起彩排` 仅声明小明：不得输出含「阿花」的公开正文（模板路径永不复制 raw）。
2. `public_label=阿花`：拒绝。
3. store/sanitize：`schema_version=True`、任意 policy、`private_relationship`、空 claim 列表、标签「阿花」、重复 `alias_id`：全部拒绝。
4. same-tick hard-gate：裸 factual → 精确 `reject_public_projection`，合法 twin 仍可 accept 且 dedupe 不中毒。
5. carrier nested mutation：构造后改 `identities[0].public_label` / `claim_classes` / hash 必须 TypeError 或对 record 无影响；`to_dict` 返回独立深拷贝。

## Independent final review (2026-07-16)

- **Important (fixed)**：`JournalEventRecord` 原仅浅拷贝 `public_projection`，嵌套 list/dict 与调用方共享；外部 mutation 可在 frozen dataclass 后静默改写 projection（hash/label 再赋值后可改变 adapter 输出）。已 deep-freeze + RED 回归。
- **Minor (已修)**：顶层 provenance `schema_version` 显式值现在必须是精确 `int`；`True`、`2.0`、字符串均 fail-closed。缺少该字段时仍兼容默认 v1。
- 本 pass 的 Important 与 Minor 均已通过 RED→GREEN 关闭；不宣称未经证据的“零问题”，而是保留上述审计记录。

## Verification (offline, post independent review)

- Focused QZone 九文件（含 producer contract）：**236 passed**。
- Dream/Schedule/producer 相关：**136 passed**。
- producer candidate contract：**50 passed**。
- public_projection 对抗：**37 passed**。
- 全仓：**4318 passed / 17 skipped / 187 warnings**。
- Ruff clean（`story_arc.py` + focused tests）。
- Pyright `plugins/schedule/story_arc.py`：**0 errors**。
- `git diff --check` 通过（本修复相关路径）。
- `BUILTIN_WIRE_PROFILE.validated is False`。
- 无真实 QZone HTTP / 凭据 / Docker / NapCat。
- Admin `vue-tsc` / Vite build：本 pass 未重跑（未改前端）；先前实现会话已通过。

## Explicit Non-goals / NO-GO

- 不扩展为任意自由文本模板或用户自定义模板。
- 不把真实昵称写入 public_label 白名单。
- 不启用 live publish；不把内置 profile 标为 validated。
- 不自动迁移历史草稿中的旧启发式投影（若存在，须人工 reject 或重建）。

## Rollback

1. 回退 `plugins/qzone_journal/public_projection.py` 与 store/selector/plugin/advanced_fiction_context 接线。
2. 回退 Admin `types.ts` / `DraftDetailDrawer.vue` 中 `public_template_id` 展示。
3. 回退相关测试、本文档、manifest `0.7.0` → `0.6.0`。
4. 无 SQLite 反向 migration；开发库可删后重建。
5. 回滚前后均保持 `validated=false`。

## D1 Same-pattern Scan

| 位点 | 结果 |
| --- | --- |
| `public_projection.py` free-form replace / CJK helpers | 已移除；仅模板渲染 |
| `store._sanitize_public_projection_meta` | 委托 `validate_public_projection_metadata` |
| `selector` factual 门禁 | `reject_public_projection`（非 `reject_subject_not_allowed`） |
| `plugin._adapt_raw_event` | factual 强制 `project_factual_event` |
| `advanced_fiction_context.build_review_provenance` | 仅 validated + `public_metadata()` |
| Admin provenance flatten | 不含 raw_summary；含 `public_template_id` |
| 其他插件 public text scrub | 未改；仍走 `public_safety` |
