# QZone published content authenticity audit

> 状态：completed · 2026-07-17 · 只读事故审计已完成，不修复、不删除、不重发

## Objective

1. 区分本次发布是测试/dry-run 记录还是真实远端 QZone 记录。
2. 对已发布正文逐句追溯来源，判断哪些是直接事实、推断、润色或无证据生成。
3. 审计 selector → projection → composer → review → delivery → manual resolution 全链路，找出真实性门失效的根因。
4. 由 Codex 主审、Grok 独立只读交叉验证，最终按严重度给出结论；本轮不实施修复。

## Published draft under audit

- draft: `qzd_9e0753f900cd837b46d6d3bd`
- status: `published`
- content SHA-256: `cae01edff8ec7db05407cd58e14d18ff2d3b12cecf52cd1f22c79b6654f5b4c2`
- text: `今天把“慢”当成关键词,下午用粉笔重新标了落地角度,司走位后节奏干净了不少。傍晚去乐园边吃烤串,听宁宁讲她小时候上台紧张的事,觉得心里松快了一些。`

## Boundaries

- 不执行 QZone POST/publish，不重发该 draft，不自动删除远端内容。
- 不读取、复制或输出 cookie、`p_skey`、UIN、token、attestation secret。
- live SQLite 仅 `mode=ro`；不 restart/recreate NapCat，不改运行配置。
- 本轮只调查根因和影响，不提交修复。

## Initial evidence

- 单次授权 POST 只执行一次；严格 parser 返回 ambiguous，未重试、未生成 fixture。
- 后续只读认证 feed 返回 HTTP 200 / code 0，精确命中正文和远端 post ID；因此“远端记录存在”已建立。
- 远端存在不等于正文中的事件真实；此前验收没有逐句回溯 source evidence。

## Executive conclusion

- **发布性质：真实远端发布。** 单次真实 POST 的响应虽然被严格 parser 判为 ambiguous，但后续只读认证 QZone feed 返回 HTTP 200 / code 0，精确命中正文和远端 post ID；live DB 的 `external_post_id` 哈希与 feed 证据一致。它不是 test fixture、不是仅 dry-run descriptor，也不是只存在本地 DB 的假记录。
- **内容性质：真实发布的合成内容。** 正文中的具体经历全部来自 20:08:23 才由 Schedule LLM 生成的沉浸式 fiction 日程，再由 Dream LLM 在 20:08:50 总结为 `dream_reflection`，最后由 QZone composer 改写成第一人称短日志。
- **事实证据：零。** 没有一句绑定真实观察、群消息 ID、factual projection 或原始 evidence ref。当前 `source_summary` 是第二层 LLM 总结，不是行为证据。
- **事故等级：Critical。** `scope=fiction` 的世界书事件被 producer 洗成 `subject_kind=self / privacy=public`，随后以未标注虚构性质的第一人称真实经历发布到真实 QZone。

## Runtime evidence

- draft：`qzd_9e0753f900cd837b46d6d3bd`
- DB schema：v5；`PRAGMA quick_check=ok`
- status：`published`；source：`dream_reflection`；`subject_kind=self`；`privacy=public`；salience `0.82`
- created：`2026-07-17T20:09:27+08:00`
- content SHA-256：`cae01edff8ec7db05407cd58e14d18ff2d3b12cecf52cd1f22c79b6654f5b4c2`
- external post ID SHA-256：`ab9355123c5bbf63322e97d0ab0a2b555b53468a2b30f7a4f02f1b846e2508e8`
- approval note：`2026-07-17 授权 dry-run：public/self,secret scan clean`
- manual resolution：ambiguous publish response 后，以只读 QZone feed 证据从 `unknown` 确认成 `published`；没有第二次 POST。
- 当前常驻运行态已重新锁定：plugin enabled；`dry_run=true`；`allow_live_publish=false`；内置 profile `validated=false`；live UIN allowlist 为空。

## Clause-level evidence matrix

| 正文片段 | 最近来源 | 原始来源 | 分类 | 结论 |
| --- | --- | --- | --- | --- |
| `今天把“慢”当成关键词` | Dream `source_summary` / composer | Schedule LLM `day_narrative` | synthetic schedule | 无观察证据 |
| `下午用粉笔重新标了落地角度` | Dream `source_summary` | Schedule LLM 15:30 slot | synthetic scene | 无观察证据 |
| `司走位后节奏干净了不少` | Dream `source_summary` | Schedule LLM 15:30 slot；司为 `kind=fiction` partner | fiction-derived synthetic scene | 被错误呈现为 self 真实经历 |
| `傍晚去乐园边吃烤串` | Dream `source_summary` | Schedule LLM 17:00 slot | fiction-derived synthetic scene | 无观察证据 |
| `听宁宁讲她小时候上台紧张的事` | Dream `source_summary` | Schedule LLM 17:00 slot；宁宁为 `kind=fiction` partner | fiction-derived synthetic dialogue/background | 无消息或原始资料证据 |
| `觉得心里松快了一些` | composer wording | Dream 推断 `情绪恢复效果好` | secondary inference / embellishment | 不是直接事实 |

## Root-cause chain

1. `plugins/schedule/generator.py` 在当天 20:08 启动补生成整日 schedule。它明确调用 LLM 生成“具体、有画面感”的场景，并把生成结果写入存储；这不是观察日志。
2. `update_story_arc_after_schedule()` 对 synthetic schedule 硬编码 `subject_kind="self" / privacy="public"`。本条未直接入选只因 salience `0.35` 低于门槛，但同类真实性缺陷存在。
3. `plugins/dream/plugin.py` 的 reflection 上下文读取 schedule 的 theme / day narrative / 前 6 个 slots；本次正好覆盖到 17:00，因此包含正文全部主要场景。全局 reflection 没有群聊消息或 social factual context。
4. `_apply_life_reflection_to_arc()` 无视 active arc 的 `scope=fiction`，硬编码 `subject_kind="self"`；global reflection 同时获得 `privacy="public"` 和 salience `0.82`。
5. `plugins/qzone_journal/plugin.py::_adapt_raw_event()` 信任 producer 自带的 subject/privacy；`selector.py` 仅对 `factual` 要求 validated public projection，`self/fiction` 不要求 observation evidence。
6. review provenance schema v1 记录了 arc id/stage/revision 和 fiction partner IDs，但缺少 `arc_scope`、schedule generation provenance、message/evidence refs；操作员看不到“这是 20:08 才生成的 fiction 日程”。
7. composer 把 `source_summary` 称作“已验证事件”，而本次 `advanced_enabled=false`、candidate 已是 `self`，所以未携带 fiction 标识，输出自然第一人称真实经历口吻。
8. approval 只做状态迁移；实际批准备注明确仅写“授权 dry-run”，没有 clause-level truth check，也没有 live-scope approval binding。
9. `publish_qzone_capture.py::publish_once()` 只校验 approved 状态、正文 SHA、唯一 approved 队列、目标账号哈希和确认口令；不会重新核验审批作用域或内容事实性，因此 dry-run approval 被 live capture 复用。

## Additional impact

Dream 同一轮还把合成事件写成两张 active/global memory cards：

- `降难度站位标记不仅让司的扭伤得到保护，反而让他的走位节奏更干净……`
- `排练后的小吃摊烤串和宁宁主动讲童年上台紧张的事，让W×S四人之间的信任感……`

两张卡均 `source=dream_reflection`、无原始 message ID（只有 deterministic synthetic reflection source ID），会被未来 schedule continuity 再读取，形成 **fiction schedule → self memory → future schedule** 的自强化污染。

## Severity findings

### Critical

- fiction/synthetic schedule 经 Dream 被错误标成 `self/public`，并以未标注虚构性质的第一人称内容真实发布。

### Important

- 20:08 才补生成当天整日“已发生经历”，生成时间晚于正文描述的大部分事件。
- review provenance 不含 `arc_scope` 与 clause-level evidence refs，无法让操作员验证真实性。
- dry-run approval 没有作用域约束，被一次性 live capture 直接复用。
- 两张虚构事件卡已进入 active/global memory，后续会污染日程和反思。
- schedule producer 同样将 synthetic event 硬编码为 `self/public`，只是本次未因 salience 入选。

### Minor

- composer 的“已验证事件”措辞把 schema/字段完整性误称为事实真实性，容易误导开发和审核者。

## Grok independent cross-check

- Grok 独立确认了相同设计链：Schedule LLM synthetic event → Dream `self/public` → selector 信任 producer → approval/capture 只检查状态与哈希。
- Grok 特别指出 `test_dream_global_reflection_emits_self_public_salience` 正在把该行为固化为正确合同。
- Grok 因只读范围内未进入容器 DB、也未做远端 feed 请求，独立报告中将“真实远端发布”列为未验证；它明确说明这不是反证。Codex 的 live DB、manual resolution 与此前认证 feed 证据补齐了远端层。
- Grok 最终未发现与“真实远端发布的 fiction-derived synthetic content”假说相冲突的代码证据。

## Hypotheses

- H1：发布目标是真实 QZone，但 draft 是开发/测试候选，未满足事实真实性门。
- H2：`dream_reflection` 或高级 fiction/self composer 将弱证据扩写为具体行为、场景和对话，形成无证据细节。
- H3：review/approval 只校验安全与状态，不要求每个 factual clause 绑定可验证 provenance。
- H4：public projection 或 source summary 已经包含模型生成内容，后续“factual equality”只能保证复制，不保证原始事实。

Disposition：H1 confirmed；H2 partially confirmed（主要生成发生在 Schedule 与 Dream，composer 主要做第一人称改写和情绪润色）；H3 confirmed；H4 confirmed（本条没有 factual projection，`source_summary` 已是合成摘要）。

## Test Ledger

| Time | Experiment | Actual result | Conclusion |
| --- | --- | --- | --- |
| 2026-07-17 | recovery + scope freeze | 远端存在证据与内容真实性证据被明确拆分；无新外部写操作 | 从内容 provenance 逆向追踪，不重复发布测试 |
| 2026-07-17 | live DB read-only audit | schema v5；quick_check ok；draft/source/content/source_summary/provenance/review/manual resolution 与上文一致 | 该记录是实际 published row；approval 仅注明 dry-run |
| 2026-07-17 | runtime schedule + arc read-only audit | schedule generated_at `20:08:23+08:00`；active arc `scope=fiction`；4 个 partner 全为 `kind=fiction`；Dream event `self/public/0.82` | 全部场景来自晚间合成 fiction schedule，不是观察日志 |
| 2026-07-17 | memory DB read-only audit | 两张相关 `dream_reflection` 卡为 active/global，created `20:08:50`，无原始消息 evidence | 已发生跨模块记忆污染 |
| 2026-07-17 | focused producer contract regression | `3 passed in 0.56s`：schedule self/public、dream global self/public、global dream reaches pending review | 缺陷由当前测试合同明确允许，属设计级问题 |
| 2026-07-17 | Grok independent audit | 两次上游 HTTP 524 后同一 session 聚焦重连成功；确认 synthetic→self/public→selector→capture 根因链；无冲突证据 | 交叉验证支持 Codex 内容真实性结论；远端层由 Codex 运行态证据独立补齐 |

## Out of scope / not executed

- 未执行任何 QZone POST、重发、删除或隐藏。
- 未读取/输出 cookie、UIN、`p_skey`、token 或 attestation secret。
- 未 restart/recreate NapCat，未修改插件配置或运行态。
- 未修复 producer、selector、review、capture 或 memory；本轮仅完成事故审计。

## Recommended next step (requires new authorization)

在任何下一次 live 发布授权前，先设计并实施真实性修复：阻断 `arc.scope=fiction` → `self`，为 self 内容建立 clause-level observation evidence，给 approval 绑定 `dry-run/live` 作用域，隔离/处置已污染 memory cards，并新增负向回归。远端现有日志是否删除或保留应由用户单独决定。
