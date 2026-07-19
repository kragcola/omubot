# QZone Journal 基础版实现

> 状态：v0.8.2 authenticity / approval-scope 修复已完成代码验收，schema v6；真实发布常驻锁定，等待本轮备份与 bot-only 部署 · 2026-07-18

> 关联 charter：`docs/tracking/qzone-journal-plugin-charter-2026-06-16.md`
> 离线 runbook：`docs/runbooks/qzone-sanitized-fixture-conformance.md`
> v0.2 migration：`docs/migrations/qzone-journal-advanced-fiction-review-provenance-2026-07-16.md`
> v0.3 migration：`docs/migrations/qzone-journal-review-console-v0.3-2026-07-16.md`
> v0.4 migration：`docs/migrations/qzone-journal-selection-decision-v0.4-2026-07-16.md`
> v0.5 migration：`docs/migrations/qzone-journal-selection-observability-v0.5-2026-07-16.md`
> v0.6 migration：`docs/migrations/qzone-journal-producer-candidate-contract-v0.6-2026-07-16.md`
> v0.7 migration：`docs/migrations/qzone-journal-public-projection-v0.7-2026-07-16.md`
> v0.8 migration：`docs/migrations/qzone-journal-draft-revisions-v0.8-2026-07-16.md`
> v0.8.2 migration：`docs/migrations/qzone-journal-authenticity-approval-scope-v0.8.2-2026-07-18.md`

## 目标

把 `qzone_journal` 推进到可审计、可 dry-run、默认关闭的预发布形态：从 Living Persona 已有事实材料中挑选“值得发的事”，进阶开关可为既有 fiction 事件补世界书背景，生成带来源证据的待审草稿，并在专用 Admin SPA 中完成显式审核、dry-run 与 unknown 人工处置。

v0.4 在保持硬门禁与 fail-closed 发布合同不变的前提下，补齐**离线选择质量**：闭集 selection reason、确定性 publish-worth 排名、按 `event_date` 的日草稿预算，以及 secret-free 过程计数与 durable runtime metrics。

v0.5 将这些计数变成可操作的**离线选材可观测性**：health 提供明确的进程级摘要，Admin SPA 展示非零闭集 reason 与草稿预算；同时把 `PluginConfig.allowed_sources` 与 JSON Schema 收敛为同一三源闭集，阻止程序化配置绕过 source 边界。

v0.6 将候选公开性责任前移到**事件生产者**：Dream、Schedule 与 Replan 在写入 StoryArc 时显式提供 `subject_kind / privacy / salience`；QZone adapter 不再按 source 或 arc scope 推断，只做 fail-closed 结构适配。

v0.7 将 factual Part C 公开投影改为 **by-construction**：闭集 `public_template_id` + generic label allowlist 渲染；raw 仅 hash-bound；共享严格 `validate_public_projection_metadata`；裸 factual → `reject_public_projection`；禁止 CJK/自由文本去标识猜测。

v0.8 解决审核流的结构性死端：同 dedupe 草稿被拒绝后，`enqueue()` 与自动 tick 会继续命中旧草稿，而旧正文不可修改，导致该逻辑事件无法修订再审。新版本采用 append-only revision：旧正文永远保留，新 revision 获得新 `draft_id` 并显式记录 lineage；recompose 只能由操作员触发，且必须重新进入 `pending_review`。

## v0.8 冻结实施合同

- additive migration v5 为每条草稿补齐 `revision_root_id`、`revision`、`supersedes_draft_id`；既有行迁移为 revision 1，root 指向自身。
- 新 revision 使用独立的 revision dedupe key，但保留原逻辑事件 lineage；自动 tick 继续按原逻辑 dedupe，不得把 rejected 行当成重新自动生成许可。
- recompose 只允许来源状态 `rejected` 或 `pending_review`，且必须是 **lineage tip**；`approved/dispatching/unknown/published/failed` 与非 tip 行均拒绝。
- 每次 recompose 创建新 `draft_id`，新行恒为 `pending_review`，不继承 approval/review decision/publish claim；版本历史 append-only；**不**持久化 `superseded` 状态。
- 旧版本与并发请求使用事务内 CAS：同一 source revision 最多成功产生一个直接后继；取消或失败不得留下半成品 revision、预算占用或审计污染。
- 日草稿预算按 **lineage tip 占用态** 计（`NOT EXISTS` 后继 + occupied statuses）；历史 immutable 行不占；tip rejected 后 occupied=0 可再 recompose；同 lineage pending→pending 替换不占第二格。
- 运营 `list`/`count`/`stats`/`list_recent_source_summaries` 默认 tip-only；`include_superseded` 仅物理行 escape；`list_revisions`/`get` 全历史不变。
- operator note 只作为 bounded、scrubbed 的措辞指导，不是事实来源；旧正文只作为 untrusted wording reference，不能提升为 evidence；recompose 要求非空 verified `source_summary`。
- factual revision 的可发布事实只来自既有、已验证的 `source_summary/public_projection/provenance`。store `create_revision` **无 override 参数**；factual body 必须等于 inherited verified summary。
- Admin 继续作为审核队列：详情抽屉内展示版本历史；**allowed status + tip** 才显示「修订并重新入队」；`revisions=[]` fail-closed；成功后父组件切换到新 tip；不新增真实 `/publish` 入口。
- plugin preflight（status/tip/source_summary）在 LLM 前；store 事务内再次校验。
- 版本升到 `0.8.0`；保持 `BUILTIN_WIRE_PROFILE.validated=false`，不部署、不读凭据、不真实 HTTP、不触 live DB/Docker/NapCat。

## 已冻结且已实现的安全合同

- 默认 `enabled=false`、`dry_run=true`、`allow_live_publish=false`、`allowed_live_uins=[]`。
- `manual_review` 为类型与 JSON schema 双重常量 `true`；生成草稿不会外发。
- 默认每日最多发布 1 条，按 Asia/Shanghai 日期统计；`dispatching/published/unknown` 均占额度。
- v0.4 另有日草稿预算 `max_drafts_per_day`（默认 1）：按 `event_date` 计 `pending_review/approved/dispatching/unknown/published` 为占用；`rejected/failed` 不占用；**在 LLM 之前**检查。
- 基础版公开候选仍以 `self` / `fiction` 为主；`factual` 仅在通过闭集模板投影后可接受，裸 factual 与无效投影均为 `reject_public_projection`；私聊、未知来源、隐私/主体缺失均 fail-closed。
- Cookie、`p_skey`、token 仅临时读取，永不落盘、永不写日志；未验证 profile 的 dry-run 不读取凭据。
- live 顺序固定为：批准 → enabled → 非 dry-run → allow_live_publish → validated profile → 临时凭据 → UIN allowlist → claim → HTTP。
- 内置 `qzone-text-v1-unverified` / `BUILTIN_WIRE_PROFILE` 保持 `validated=false`，因此当前真实发布成功路径刻意不可用；**禁止**把内置 profile 改成 `validated=true`。
- 本轮不部署、不真实发布、不重启或重建 NapCat。
- `advanced_enabled` **不新增日常伙伴候选**、不改变“有事才发”的触发频率；只给已经通过 selector 的 fiction 事件补充 bounded worldbook context。
- Part C factual 真人数据的 raw 文本不得进入 CandidateEvent/composer/provenance/UI；仅允许经 `project_factual_event` 的模板投影正文与安全 metadata。advanced 开启时仍不得把未投影 raw 放行。
- prompt、LLM 成稿、Store 正文、review provenance 与人工恢复 note 共用公开文本护栏；Unicode Cf/全角/ZWSP/Bearer/QQ-like 编号均 fail-closed 或 redaction。
- Admin SPA 是审核队列，不是发布控制台：源码不包含 `/publish` 调用；approved 仅 dry-run，真实发布仍只能通过原有后端 fail-closed gate。
- v0.4 selection health / runtime metrics **仅**含闭集 reason 整数计数与 source 元数据；永不含 summary / stable_id / QQ-like / secrets。
- v0.5 `selection_summary.scope=process_lifetime`，重启归零；UI 不把它表述成持久历史。typed `allowed_sources` 只允许三种官方来源，和 JSON Schema 一致。
- v0.6 producer 必须显式提供 `subject_kind / privacy / salience`；adapter 缺 subject/privacy → `reject_missing_subject_privacy`，缺失/bool/非数值/非有限 salience → `reject_adapter_unparseable`，有限越界继续由 selector 返回 `reject_salience_out_of_range`。
- `JournalEventRecord.event_id` 可选；一旦提供必须匹配 `^[A-Za-z0-9][A-Za-z0-9._:-]{0,179}$`。v0.8.2 起 Dream / Schedule 在 fiction arc 分别输出 `fiction/public/0.82`、`fiction/public/0.35`，非-fiction arc 输出 `self/unknown`；Replan=`fiction/0.95`，且仅 fiction arc 为 public，其他 arc 为 unknown。任何 synthetic source + `self` 的 stale/forged 记录由 selector 与 live delivery 双重拒绝。
- v0.7 闭集模板与 generic label 合同见 `docs/migrations/qzone-journal-public-projection-v0.7-2026-07-16.md`；`source_event_hash` 绑定 template + alias；store 与 projector 共用严格 validator。

## 已实现组件

1. `selector.py`：来源 allowlist、显著度、主体/隐私门禁、稳定 dedupe key；v0.4 闭集 `SELECTION_REASON_CODES`、`evaluate()`、Unicode 分词 Jaccard、确定性 `compute_publish_worth` / `rank_accepted`。
2. `composer.py`：typed `LLMRequest(task="qzone_journal_compose")`、第一人称约束、失败时事实摘要降级；advanced fiction context 只作为“非指令引用材料”。
3. `store.py`：SQLite migration v1-v6；v6 additive `approval_scope` 默认 `dry_run`，live 必须显式批准；中央 catalog target 同步为 6。其余 outbox、lineage、预算、取消安全与审计合同保持不变。
4. `transport.py`：NapCat 临时凭据、hash33 `g_tk`、固定 HTTPS host/path、无 redirect/代理、secret-free descriptor；集成 response parser。
5. `response_parser.py`：**已完成**严格 JSON/JSONP 分类（success / failed / ambiguous），BOM/空白、安全 callback、非 2xx/redirect/超大 body/畸形 HTML fail-closed，secret-free 结果；仅合成 envelope 覆盖，**不是**真实脱敏 CGI fixture 符合性验证。
6. `fixture_conformance.py` + `tools/verify_qzone_fixture.py`：**已完成**版本化、secret-safe、纯离线 fixture 合同与符合性工具链（schema_version=1、origin 分类、names-only fingerprint、parser 对照、advisory `profile_creation_eligible`）。**永不** mutate WireProfile、**永不**创建/设置 `validated=True`。仓内示例仅为 `origin=synthetic` 模板。
7. `delivery.py`：发布总闸、claim-before-HTTP、模糊/取消结果转 unknown、UIN 白名单；v0.8.2 在凭证前重验 live 草稿真实性，拒绝缺失元数据、synthetic-self、无前缀 fiction 与被改写 factual。
8. `plugin.py`：Manifest V3 **`0.8.2`**；普通 Admin approve 显式为 `dry_run`，`live` 必须由请求明确指定且完整 live gate ready；既有 tick、review、revision、health 与状态机合同保持不变。
9. 通用接线：插件 AdminRoute 聚合、`qzone_journal.db` catalog/backup、运行指标（含 `qzone_draft_rejected` / `qzone_selection_decision`）、LLM task 与 Admin provider task。
10. `advanced_fiction_context.py`：仅在 advanced + fiction arc + fiction candidate 三门同时满足时提取 bounded StoryArc/fiction partner context；不新增候选源；factual provenance schema v2 仅挂安全 `public_projection`。
11. `public_safety.py`：公开文本统一 Unicode/Cf/编号/secret-assignment scrub，worldbook 保留换行结构；单个 provenance 拒绝不再阻断整个 tick。
12. `public_projection.py`（v0.7）：闭集 `PUBLIC_TEMPLATES` / `GENERIC_PUBLIC_LABELS`、`render_public_summary`、`project_factual_event`、`validate_public_projection_metadata`、hash 绑定 template+alias；无 CJK 启发式。
13. `composer.py`（v0.8）：`recompose()` — factual 固定 verified projected summary；fiction/self LLM 措辞；operator_guidance scrubbed wording-only。
14. `admin/frontend/src/views/qzone-journal/`：Calm Ops 审核队列、四指标卡、health gate banner、分页列表与状态动作抽屉；v0.5 新增进程级“选材诊断”；v0.7 详情展示安全 `public_template_id` 等投影字段；**v0.8 版本历史 +「修订并重新入队」**；列表/health/详情/action 有陈旧请求保护，dry-run 固定字段白名单；**无** `/publish` UI。
15. `plugins/schedule/story_arc.py` + producers：v0.6 新增冻结 `JournalEventRecord`；v0.8.2 修正 Dream/Schedule 为 fiction arc=`fiction/public`、其他 arc=`self/unknown`；Replan 仅 fiction arc 写 `fiction/public/0.95`，其他 arc 写 `fiction/unknown/0.95`；Dream parser 不能自我授权公开性。

## Grok 审查与 Codex 收口

- Grok 写入包补齐 `allowed_live_uins`、unknown 的 `confirm_published` / `confirm_not_published`、v2 人工审计表及 Admin API。
- Codex 独立复核发现并修复：
  1. 人工恢复幂等不能只看最终状态，必须存在 note/external id 匹配的人工审计记录；普通 approved/published 不得伪装成恢复重放。
  2. `/publish` 必须尊重插件级 `dry_run` 总闸。
  3. late success 不能把已恢复成 unknown 的草稿静默标为成功；`mark_published` 仅允许 `dispatching → published`。
  4. post-dispatch `RuntimeError` 必须走 502/unknown 分类，不能混入 409 前置门禁。
- 严格 response parser 与 transport 集成已落地并通过合成用例；**真实发布阻塞**收敛为：真实脱敏 fixture 符合性、独立 validated profile、另行授权单条 canary。
- v0.2.0 首轮 Grok TDD 后，Codex 复核补正文/Store 最终边界；独立 adversarial review 发现 1 Critical + 5 Important（候选隔离、Unicode/ZWSP/Bearer、worldbook 结构、manual note），均以精确 RED 修复并复审为 FIXED。
- v0.3.0 由 Grok 分后端/前端两包实现；Codex 增加 SQLite trigger 原子回滚故障注入，并修复独立 review 的 2 Important：approved 普通 reject 状态矩阵过宽、前端陈旧请求覆盖。
- v0.5 范围审计前两次遭遇 Grok 上游 `524` / `503`；第三次从同一 scope 重建成功，首轮 final review **0 Critical / 0 Important**。唯一 Minor 指出 health load 失败时选材区会误显“暂无决策”，已以错误态/加载态 RED→GREEN 修复；factual/social public projection 继续后置的判断获复审确认。
- 上线前第二轮独立复审在当前文件上发现 **0 Critical / 2 Important**：Drawer dry-run 迟到响应可串到新草稿、`mark_published` 未复用 external post id 消毒合同；另有 health 首载/失败门禁真值问题。三项均以精确 RED 修复。Codex 验收再发现 response parser 允许 128 字符而 Store 只允许 120 字符，会造成判成功后落库失败；回派同一 Grok scope 后以 120/121 边界 RED→GREEN 收口。

## Test Ledger

| 时间 | 命令/场景 | 实际结果 | 结论 |
| --- | --- | --- | --- |
| 2026-07-15 | 首轮完整全量 | `3728 passed, 17 skipped` | 新增插件后的 5 个精确清单旧失败已归零 |
| 2026-07-15 | Grok unknown/UIN focused | `45 passed`，Ruff clean，Pyright 0 | 协议无关预发布护栏完成 |
| 2026-07-15 | 人工恢复 provenance RED | `1 failed`：普通 published 未抛冲突 | 复现状态即幂等的根因 |
| 2026-07-15 | provenance GREEN | `3 passed` | 幂等改为匹配人工审计记录 |
| 2026-07-15 | dry-run + late-success RED | `2 failed` | 复现 `/publish` 忽略 dry-run 与 mark no-op |
| 2026-07-15 | post-dispatch 分类 RED | `1 failed`：期望 502、实际 409 | 复现 RuntimeError 边界过宽 |
| 2026-07-15 | 三项 GREEN | `3 passed` | dry-run、late success、502 分类关闭 |
| 2026-07-15 | QZone focused 四文件 | `49 passed` | selector/store/composer/transport/delivery/plugin/admin 全绿 |
| 2026-07-15 | scoped Ruff / Pyright | Ruff clean；`0 errors, 0 warnings` | 静态通过 |
| 2026-07-15 | JSON + `vue-tsc --noEmit` | 通过 | manifest/config/provider task 合同通过 |
| 2026-07-15 | 最终完整全量 | `3736 passed, 17 skipped, 186 warnings` | 0 fail；warnings 为既有第三方弃用项 |
| 2026-07-15 | 当前混合工作树复验 | `49 passed`；Ruff clean；Pyright `0 errors, 0 warnings`；三份 JSON 合同通过 | 记忆重构中的并行脏改未破坏 QZone |
| 2026-07-16 | QZone focused 四文件（parser 集成后） | `76 passed`（`test_qzone_journal` + `runtime` + `transport` + `delivery`） | 含严格 JSON/JSONP parser 与 transport 合成覆盖 |
| 2026-07-16 | 最终混合工作树全量 | `3785 passed, 17 skipped, 186 warnings`；相关源码 scoped Ruff/Pyright 通过 | QZone + 记忆并存回归通过；仍无真实 QZone HTTP / 凭据读取 / 部署 |
| 2026-07-16 | fixture harness RED | `tests/test_qzone_journal_fixture_conformance.py`：`19 failed, 1 passed`（缺 `fixture_conformance` 模块） | 先落 RED：synthetic 不可资格、secret 拒绝、request drift、parser mismatch、JSON/JSONP 机制等合同 |
| 2026-07-16 | fixture harness GREEN | 同文件 `20 passed`；QZone 五文件 `96 passed`；scoped Ruff clean；Pyright `0 errors` | 离线 harness 完成 |
| 2026-07-16 | CLI smoke synthetic | `uv run python tools/verify_qzone_fixture.py tests/fixtures/qzone_journal/synthetic_publish_json_v1.json` → `ok=true`, `profile_creation_eligible=false`, 无 body/secrets | 合成模板不可作 live-release 证据 |
| 2026-07-16 | validated 门禁 | `rg`：`plugins/qzone_journal` 无 `validated=True` 赋值；`BUILTIN_WIRE_PROFILE.validated is False` | 未创建 validated profile |
| 2026-07-16 | Codex 主审加强 | fixture tests **28 passed**；QZone 五文件 **104 passed** | 新增 JSON 键式 secret 检测、重复对象键/大小写重复 header 拒绝、安全 remote_id 与 CLI 不回显回归；移除恒真断言 |
| 2026-07-16 | 最终全量 | **3819 passed, 17 skipped, 186 warnings** | QZone harness 与记忆 residual 并存全量通过；无真网/凭据/部署 |
| 2026-07-16 | v0.2 advanced/provenance 初始 RED | `tests/test_qzone_journal_advanced_context.py`：**13 failed, 2 passed** | advanced no-op、无 v3 provenance、Admin/health 缺合同 |
| 2026-07-16 | Grok GREEN + Codex正文边界补强 | QZone 六文件 **120 passed** | fiction worldbook、v3、review bundle、prompt/output/store/public safety |
| 2026-07-16 | 独立 adversarial review | 1 Critical + 5 Important | tick poison 隔离、全角/ZWSP/Bearer、换行结构、manual note 均建立 RED |
| 2026-07-16 | review remediation + 复审 | QZone 六文件 **123 passed**；复审 **0 Critical / 0 Important** | 原六项均 FIXED；额外 fullwidth Latin secret key 覆盖 |
| 2026-07-16 | QZone + manifest/catalog/ownership | **159 passed**；JSON parse clean | v0.2.0 manifest/catalog/插件所有权合同通过 |
| 2026-07-16 | 当前混合工作树全量 | **3940 passed, 17 skipped, 186 warnings** | QZone v0.2 与当前记忆分支并存无全仓回归 |
| 2026-07-16 | v0.3 review-console 后端 RED→GREEN | 新增 migration v4、分页/详情/双审计/health gate；Codex 故障注入后 QZone 七文件 **133 passed** | approve/reject audit 同事务；approved 普通 reject 返回 409 |
| 2026-07-16 | v0.3 Admin SPA 验收 | `vue-tsc --noEmit`、production build、UI compliance、无 `/publish`/raw color/`!important`/inline style 扫描均通过 | Calm Ops 审核队列完成；未部署 |
| 2026-07-16 | v0.3 独立 review + remediation | 0 Critical；2 Important（状态矩阵、陈旧响应）均修复；复验 QZone **133 passed**、前端 type/build 通过 | v0.3.0 可作为离线预发布审核面收口 |
| 2026-07-16 | v0.3 最终全量 pytest | **3950 passed, 17 skipped, 186 warnings** | 相对 v0.2 全量基线净增 10 个 QZone v0.3 回归；无全仓失败 |
| 2026-07-16 | v0.4 selection RED 切片（先于实现的合同） | 新契约：闭集 reason / 双候选排名 / stable_id 并列 / 日预算 / dedupe reason / health counters / metrics 聚合 | 合同先于代码固定；实现后同批 GREEN |
| 2026-07-16 | v0.4 focused GREEN | 上述 8 个 v0.4 精确用例 **8 passed** | R1–R6、R9 核心路径通过 |
| 2026-07-16 | v0.4 QZone 七文件 + metrics | **148 passed**（含 poison-candidate fallthrough 修复） | 排名+预算+闭集 reason 与既有 advanced/review/fixture 共存 |
| 2026-07-16 | scoped Ruff / Pyright | Ruff clean；Pyright `0 errors, 0 warnings` on selector/plugin/store + block_trace store | 静态通过 |
| 2026-07-16 | validated 门禁复验 | `BUILTIN_WIRE_PROFILE.validated is False`；无 `validated=True` 赋值 | 未创建 validated profile；live 仍阻塞 |
| 2026-07-16 | v0.4 审查 6 冻结用例 | **2 GREEN / 4 RED**（selector 排名 2 绿；store 日预算原子 / 同 tick dedupe / 毒 review 字段 / metric source 闭集 4 红） | 合同冻结后发现实现缺口 |
| 2026-07-16 | store.py 恢复事故 | 外部 worker 误截断 untracked `store.py`；Codex 终止并完整恢复权威文件（48045 bytes / 1406 行 / AST valid） | 禁止再整文件替换/截断恢复 |
| 2026-07-16 | v0.4 四合同修正 GREEN | 6 frozen **6 passed**；七 QZone + metrics **153 passed**；scoped ruff/pyright 通过 | store `max_drafts_per_day`+`DayDraftBudgetExceededError`；同 tick seen-dedupe；`validate_review_fields_for_compose`；metric source 闭集 `unknown` |
| 2026-07-16 | 修正后 integrity | `store.py` 1475 行 AST valid；`BUILTIN_WIRE_PROFILE.validated is False`；本 worker **未改** tests | 未部署、未 commit |
| 2026-07-16 | same-tick hard-gate vs dedupe 排序 RED | 同 tick：先 factual 后 fiction/public 同 key → 误 `reject_duplicate_dedupe`、零草稿/LLM | 冻结顺序要求 hard gate 先于 seen-dedupe 登记 |
| 2026-07-16 | same-tick hard-gate vs dedupe GREEN | 新回归 + 既有 same-tick duplicate **2 passed**；七 QZone + metrics **154 passed**；scoped ruff/pyright 通过 | `seen_dedupe_keys` 仅在 `selector.evaluate` accept 后检查/登记；6 prior frozen 保留 |
| 2026-07-16 | Codex 最终全仓验收 | **4127 passed, 17 skipped, 189 warnings** | QZone v0.4 corrections 与当前 memory slices 全仓并存；无真实 HTTP / 凭据 / 部署；warnings 为既有依赖弃用与 aiosqlite 退出告警 |
| 2026-07-16 | v0.5 selection summary RED→GREEN | health 缺 `selection_summary` 精确 RED；摘要 + manifest `0.5.0` GREEN | 保留完整 `selection_decisions`，新增明确 process-lifetime 汇总；无 schema migration |
| 2026-07-16 | v0.5 Admin observability + visual | 前端合同 RED→GREEN；`vue-tsc` / production build 通过；1440 light/dark + 390 collapsed DOM/截图通过 | 非零 reason、预算、零状态可见；合成 API 隔离于 live `8081` |
| 2026-07-16 | v0.5 typed source boundary | 任意 source 未拒绝 RED；typed validator GREEN；对抗 metric fixture 改为未知 source + 官方 source 毒 review 字段 | JSON Schema / Pydantic 同为三官方源闭集；错误不回显恶意原值 |
| 2026-07-16 | v0.5 第一轮 focused/static | QZone 七文件 + metrics **156 passed**；Ruff clean；Pyright **0 errors** | 最终 full / post-review 尚待下方收口 |
| 2026-07-16 | v0.5 Grok final review + Minor remediation | **0 Critical / 0 Important**；1 Minor health-error empty-state 已 RED→GREEN | 真实发布无新增路径；factual/social projection 继续后置 |
| 2026-07-16 | v0.5 最终验收 | QZone **156 passed**；全仓 **4192 passed / 17 skipped / 189 warnings**；Ruff clean；Pyright **0 errors**；前端 type/build 通过 | 未部署、未真网、未读凭据；无 orphan pytest |
| 2026-07-16 | v0.5 上线前第二轮独立复审 | **0 Critical / 2 Important**：Drawer action 迟到串写、`mark_published` ID 未消毒；另有 gate loading/error 真值缺口 | 默认 live publish 仍不可能；问题均进入精确 RED |
| 2026-07-16 | v0.5 review remediation | 3 条回归 RED→GREEN；QZone 七文件 + metrics **159 passed**；Ruff/Pyright/type/build 通过 | action generation、gate phase、Store 120 字符 ID 合同完成 |
| 2026-07-16 | Codex 集成验收 + parser 修正 | 发现 parser 128 / Store 120 mismatch；120/121 边界 RED→GREEN；最终 focused **4 passed**、QZone 七文件 + metrics **160 passed** | 121..128 字符 CGI id 在 delivery 前即 ambiguous；`validated=false` |
| 2026-07-16 | v0.5 post-fix final review + 全仓验收 | Grok **0 Critical / 0 Important**；5 个关键回归通过；全仓 **4196 passed / 17 skipped / 186 warnings**；Ruff clean；Pyright **0 errors**；前端 type/build 通过 | 上线前补强接受；仍未部署、未真网、未读凭据，live release 三门禁不变 |
| 2026-07-16 | v0.6 producer candidate contract | QZone **201 passed**；Dream/Schedule **130 passed**；contract **41 passed**；Ruff clean；Pyright **0 errors** | producer-owned metadata、adapter no-inference、event_id 边界与负向隐私路径通过；未部署、未真实 QZone HTTP，`validated=false` |
| 2026-07-16 | v0.7 closed-template public projection | 实现会话：public_projection **35** / focused **225** / Dream-Schedule **125**；Admin type/build 通过 | Codex 四反例关闭；`validated=false` |
| 2026-07-16 | v0.7 independent final review + schema strictness remediation | public_projection **37 passed**；focused 九文件 **236 passed**；Dream/Schedule/producer **136 passed**；producer contract **50 passed**；全仓 **4318 passed, 17 skipped, 187 warnings**；Ruff clean；Pyright **0 errors** | **1 Important + 1 Minor fixed**：`JournalEventRecord` deep-freeze nested `public_projection`；provenance 顶层 schema 显式值严格 int；`validated=false` |
| 2026-07-16 | v0.8 revisions RED（实现前） | `tests/test_qzone_journal_revisions.py` 初始缺 API/列/路由 → 多例失败；fixture 修正前仍见 budget/CAS/async/factual 失败 | 合同先于实现；slice：migration backfill、lineage、状态矩阵、并发 CAS、日预算、D2 cancel、tick suppress、factual 投影、API、Admin 源码合同 |
| 2026-07-16 | v0.8 implementation GREEN（Grok 实现会话） | revisions **14 passed**；QZone `test_qzone_journal*.py` + producer contract **250 passed**；Admin API/types/drawer 已接线；migration 文档已写 | **未**宣称 Codex 全仓验收 / 独立 adversarial review；`validated=false`；无真网/凭据/部署 |
| 2026-07-16 | v0.8 Codex Important 复现 → RED gaps A–I | focused gap 例 **8 failed / 1 passed**（budget row-count、非 tip actionable、factual override、plugin 信任顺序、Admin tip 选择、`mark_failed`） | 根因：预算按行计、缺 tip 门禁、create_revision 可 override、preflight 在 LLM 后、drawer 仍刷新旧 id |
| 2026-07-16 | v0.8 state/safety remediation GREEN | revisions **23 passed**；QZone + producer **259 passed**；Ruff/Pyright/vue-tsc/build 通过 | lineage 预算、tip-only 动作、factual 严格 body、plugin preflight、Admin select tip、移除 `mark_failed`；**仍非** Codex 全仓验收 |
| 2026-07-16 | v0.8 tip-only operational state RED→GREEN | RED 4 fail（reject tip budget、list 历史 pending、summaries 重复、drawer 空历史 open）；GREEN revisions **28** + QZone/producer **264** | 预算 tip-only NOT EXISTS；list/count/stats tip-only + `include_superseded`；summaries tip-only；`isLineageTip` fail-closed |
| 2026-07-16 | v0.8 independent review I1 (c78ba9b3) pre-fix + factual max_chars remediation | Pre-fix: **0C / 1I / 4M**；I1 only blocker. RED: composer/E2E long factual (~400) truncated at 280 → store equality fail. GREEN: factual recompose returns full scrubbed summary；revisions **31**；QZone+producer **267** | Minimal `composer.py` factual branch only；store equality/self-fiction caps unchanged；**未**宣称 post-fix independent closure / 全仓 pytest |
| 2026-07-16 | v0.8 Codex final acceptance + post-fix closure | post-fix Grok review **0C / 0I / 4M residual，ACCEPT offline**；全仓 **4473 passed, 17 skipped, 186 warnings**；QZone **267**；Ruff clean；Pyright 0；vue-tsc/build 通过；浏览器 desktop light/dark + recompose r1→r2→r3 + 390px drawer width=390/无横向溢出/console 0 | `BUILTIN_WIRE_PROFILE.validated=false`；无真网/凭据/live DB/部署；4 Minor 为 multi-process 409 映射、defense-in-depth tip gate、Pending 文案、commit hygiene，不阻塞离线验收 |
| 2026-07-17 | v0.8.1 Grok TDD + Codex final acceptance | actual SQLite `2067/SQLITE_CONSTRAINT_UNIQUE` probe；manual 两路 Admin 409；QZone **273 passed**；全仓 **4775 passed, 17 skipped, 186 warnings**；Ruff clean；Pyright 0；vue-tsc/JSON 通过 | Codex direct review 无 C/I；独立 Grok review 多次 524 未返回 final，故不冒充 `0C/0I`；`validated=false`，无真网/凭据/live DB/部署 |

## 已完成运行态 smoke（无真实发布）

- 同一事件连续两个 tick：只生成 1 个草稿、LLM 只调用 1 次。
- 批准后保持 `approved`；未验证 profile dry-run 返回 sanitized descriptor，`credential_reads=[]`。
- 临时 SQLite `quick_check=ok`；仓库 `storage/qzone_journal.db` 未生成。
- NapCat 容器 ID/StartedAt/restart 保持不变，未 restart/recreate。

## 真实发布剩余阻塞

1. 获取并脱敏保存一次**真实** CGI request/response fixture（`origin=real_sanitized`），并用 `tools/verify_qzone_fixture.py` / harness 做符合性验证（成功/失败/模糊路径）。离线 harness 与 synthetic 模板**已就绪**，但**不能**替代真实捕获。
2. 在真实 fixture 符合性通过且 `profile_creation_eligible` 仅为 advisory 通过后，**另行**新增独立的 `validated=true` wire profile；**绝不**修改内置 `BUILTIN_WIRE_PROFILE` / `qzone-text-v1-unverified` 冒充已验证。Harness **不会**创建或设置 validated profile。
3. 由用户另行授权测试账号单条人工 canary；需保持 UIN allowlist、每日限额和 unknown 人工确认。

在这三项完成前，`allow_live_publish=true` 也不能成功记为 published，这是预期 fail-closed，不是待绕过缺陷。Parser 与**离线符合性 harness** 已完成；**真实脱敏 fixture 证据**仍缺。

## 回滚

- 本轮未部署、未触生产库；v0.2 回滚见 `docs/migrations/qzone-journal-advanced-fiction-review-provenance-2026-07-16.md`。v3 六列为 nullable additive，可保留不删。
- 已生成的本地 v3 DB 可保留 nullable provenance 列与审计表；若整库回退则删除该开发 DB 后重建。
- 未来如部署，只允许 recreate bot，永不触碰 NapCat。

## 下一步

协议无关 MVP + 严格 parser + **离线 fixture harness** + advanced fiction/review provenance + Admin review console v0.3 + **v0.4 selection decision / publish-worth ranking** + **v0.5 selection observability** + **v0.6 producer candidate contract** + **v0.7 closed-template public projection** + **v0.8 draft recompose / immutable revisions** 已在实现会话落地（均未部署）。v0.8 详细合同与回滚见 `docs/migrations/qzone-journal-draft-revisions-v0.8-2026-07-16.md`。下一步仍仅当取得 `origin=real_sanitized` 的真实脱敏 CGI fixture 并跑通 verifier 后，才允许讨论独立 validated profile 与用户授权 canary。在此之前真实 QZone HTTP 保持 NO-GO。factual 投影已 by-construction 闭集；recompose 不得 free-form 改写 factual 投影正文。`BUILTIN_WIRE_PROFILE.validated` 继续为 `false`。Runbook：`docs/runbooks/qzone-sanitized-fixture-conformance.md`。

## 2026-07-17 v0.8.1 Tip CAS / Action Boundary Hardening（Codex 离线接受）

### 为什么是 patch hardening，不是 v0.9 功能扩张

- v0.8 post-fix review 的 **0C / 0I / 4M** 中，M3 tracker 叙述已由当前 ACTIVE 修正；M4 是 dirty-tree commit hygiene，不是运行时功能。
- 剩余有代码价值的只有 M1/M2：multi-process successor unique conflict 的错误面，以及 dry-run/manual resolution 的 tip defense-in-depth。
- SQLite 官方合同：同一时刻仅一个 writer；`BEGIN IMMEDIATE` 可能以 `SQLITE_BUSY` 失败；UNIQUE 失败的扩展码为 `SQLITE_CONSTRAINT_UNIQUE (2067)`。因此现有事务/CAS 正确，不应改成第二套锁；只需把**精确 successor unique conflict** 映射成闭集 409，其他 integrity/busy 错误不可误吞。

### 冻结实现包（已落地）

1. **M1 精确冲突映射**：`JournalStore.create_revision()` 捕获且仅捕获 `qzone_journal_drafts.supersedes_draft_id` 的 UNIQUE constraint（`sqlite_errorcode` 2067 + 约束名），rollback 后转为 `InvalidDraftTransitionError`（Admin 409）。其他 IntegrityError / BUSY / 取消 / 未知 DB 错误原样传播。
2. **Store tip truth**：公共只读 `is_lineage_tip(draft_id)`；未知 id → `KeyError`；私有 `_is_lineage_tip(db, …)` 仍为事务内原语。
3. **Delivery dry-run gate**：`JournalDelivery.deliver()` 在描述符 / 凭据 / transport 前要求 approved + tip；non-tip → `InvalidDraftTransitionError`。
4. **Manual resolution gate**：`confirm_published` / `confirm_not_published` 在同一 `BEGIN IMMEDIATE` 内、幂等 shortcut 前 revalidate tip。
5. **Admin UI**：`canDryRun` / `canResolve` 与 handler、按钮共用 tip 真值；`revisions=[]` fail-closed。
6. **版本/文档**：`0.8.1`；migration checklist：`docs/migrations/qzone-journal-tip-cas-action-boundary-v0.8.1-2026-07-17.md`。

### Grok 派发状态（更新）

- 先前 ACTIVE/tracker 中「Grok 被旧 endpoint 阻塞」的叙述**已过时**：双方 workspace 使用 `/Users/kragcola/.grok`，模型 `ccapi_grok_45` / `grok-4.5`，endpoint `http://45.207.201.200:3040/v1`。
- Server-side `503 auth_unavailable` 视为上游 pool 瞬时抖动，应用有界退避重试；auxiliary title-channel 503 非致命。
- 本切片由 Grok write-capable worker 在 bounded packet 内离线 TDD 完成；**未** commit / push / deploy；**未**触 live DB / Docker / NapCat / 凭据 / 真实 QZone HTTP。

**Codex note**：v0.8.1 已完成实际 SQLite 扩展码探针、两路 manual API 409、QZone **273 passed**、全仓 **4775 passed / 17 skipped / 186 warnings** 与静态/前端类型验收。独立 Grok reviewer 在完成读取后以及紧凑重建时均遭上游 524，未返回 final；不声称额外 `0C/0I`。真实发布门禁与 validated profile 条件不变。
