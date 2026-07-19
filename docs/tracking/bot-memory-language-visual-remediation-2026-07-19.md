# Bot 记忆、措辞与图片回复行为修复（2026-07-19）

> 状态：completed
> mode: task-bug
> 最后更新：2026-07-19 CST
> 当前下一步：无；任务已完成并从 ACTIVE 收口，部署与生产 Style 数据清理均需另行授权。
> 阻塞：无。
> 验证证据：最终全仓 5093 passed / 17 skipped / 189 warnings；scoped Ruff/Pyright 0；required Grok 终审 0 Critical。
> 回滚入口：仅回退本 tracker 所列代码/测试/文档增量；不改生产 DB，不触 QQ/QZone/NapCat。

## Resume Capsule

- objective: 修复 `docs/audits/bot-memory-language-visual-behavior-audit-2026-07-18.md` 确认的五条行为链：记忆 scope 断链、repair 后纯标点泄漏、视觉 prose 污染用户正文与 Style、置信度诊断泄漏、跨轮角色化短语重复。
- next_step: none；若未来部署，先可信备份并按迁移清单做 bot-only 维护窗口；历史 Style 污染清理需独立授权。
- current_files: `kernel/types.py`, `services/llm/client.py`, `plugins/memo/plugin.py`, `services/memory/visual_identity.py`, `services/tools/memo_tools.py`, `bootstrap/chat_runtime.py`, `kernel/router.py`, `services/media/visual_evidence.py`, `services/storage/catalog.py`, 对应 tests/docs。
- last_verified: HEAD `dcc75aaeb7f08d2e8b02f8cf0522bb48f204b97a`；protected review baseline 118 tracked / 146 untracked；最终 status surface 118 tracked / 147 untracked，porcelain-path SHA-256 `a52024eb997c526d27f953e4417368aefa55463d3db2295fcba91d626c08a5f6`。全部既有 dirty/untracked WIP 保留。
- do_not_redo: 不重做生产源码/容器 SHA 对齐、SQLite quick_check、历史 BlockTrace 与 Style 只读统计；不把历史启发式发生率当精确比例。
- rollback: 保留用户既有 WIP；通过本任务 scoped patch 逐文件反向回退，不 reset/clean/stash/commit。

## Codex-owned contract freeze

1. Memory visibility is not storage scope. Every extracted fact must preserve source conversation (`group_id` when present), subject (`user_id` or explicit entity ref), visibility (`private`, `same_group`, `global`), provenance, and confidence. Group auto-recall may include the current speaker's same-group-visible user facts, but must exclude another user's facts, facts from another group, and private-chat facts.
2. Visual evidence is structured side-channel context, not user-authored text. Router/model context may carry observations and opaque image refs, but Style/manual extraction must fail closed for visual/system/bot-derived provenance.
3. Model-visible visual text contains observations needed for the current intent only. Numeric thresholds, embedding distances, candidate diagnostics, and internal confidence policy never enter user-visible prompt text or final reply.
4. Every repair/rewrite path converges on one final visible-reply gate. The gate rejects blank, ellipsis-only, and punctuation-only replies/segments after every transformation.
5. Repetition control uses a bounded multi-turn phrase-family history for role-specific stock phrases, while quoted/repeated user text is excluded from bot self-repetition evidence.

## Parallel run ledger

- run_id: `bot-behavior-remediation-20260719`
- workspace: `/Volumes/OmubotDisk/omubot`
- accepted_base: `dcc75aaeb7f08d2e8b02f8cf0522bb48f204b97a`
- dirty_baseline: 100 tracked / 134 untracked; status digest above; all pre-existing changes are user-owned.
- user_scope: code + tests + tracker/migration/maintenance documentation only; no deploy or external send.
- hard_prohibitions: no reset/clean/stash/commit/push/deploy; no writable production DB; no QQ/QZone send; no NapCat recreate/restart; never set `BUILTIN_WIRE_PROFILE.validated=true`.
- global_budget: implementation 阶段 Codex main + Grok top-level + one real child；当前 review 阶段复用相同 3-slot 预算，ordinary Codex subagents = 0。
- integration_order: shared tests/contracts -> memory scope -> visual/style boundary -> finalization/dedup -> combined verification.
- shared_conflicts: `services/llm/client.py` belongs only to language/output owner; `kernel/router.py` belongs only to visual/style owner; generated files, lockfiles, DBs, ports and runtime containers have no delegated writer.

| Stream | Objective | Canonical target | Ownership / conflict domain | Dependencies | Isolation | Status | Required evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| G-MEM | Preserve memory subject/source/visibility and safe same-speaker group recall | recovery replacement `2518a21c-373b-4217-8149-2ff3d001280a` | `plugins/memo/`, `services/memory/`, scoped `services/tools/memo_tools.py`, memory tests | frozen memory contract | artifacts reviewed; `memo_tools.py` WIP preserved | completed | 188 Memory + 244 Context passed；显式 scope lookup fail-closed |
| G-VIS-LANG | Separate visual provenance, hide diagnostics, unify finalization, and bound phrase-family repetition | corrective child `019f7a68-03a0-7630-904a-05ab89764b50` | media/style/llm/humanization + related tests | frozen visual/output contracts | `/tmp/g-vis-lang-impl` integrated by scoped diff | completed | 111 Visual/语言 passed；scoped Ruff/Pyright 0 |
| G-ROUTER-IDENTITY | Exact visual identity lookup + privacy negatives | top-level `bf004f17-572b-4127-a5d1-8f3ee2e5e2ad`; child `019f7a96-0ffa-7f23-801e-2d5736dba707` | top-level `kernel/router.py`; child new router tests | `VisualIdentityStore.lookup_for_context` frozen API | shared workspace, disjoint writers | completed | 10 RED→GREEN；stash violation audited，path-only pop 成功，stash list empty |
| G-READONLY-REVIEW | Independent integration + privacy/security review | session `d329e4f7-c07d-433e-a4ec-271bfa92d33a`; completed child `019f7abd-c8e9-7fd1-8107-44ab395892c4` | read-only all scoped files; no doc writes | completed implementation | shared workspace read-only | completed | 0 Critical；stale CardUpdate I1 rejected；store/tool/diagnostic defense-in-depth findings delivered；zero edits |
| C-INTEGRATE | Contracts, TDD, integration, verification and docs | Codex main | main worktree; scoped code/tests/tracker/migration/maintenance | all deliveries | main worktree | completed | post-review hardening accepted；full pytest 5093 passed；docs/ACTIVE closed |

Status values: `pending`, `running`, `reconnecting`, `rebuilding`, `replacement_running`, `resumed`, `completed`, `failed_after_30m`, `blocked_nontransient`.

## Section Progress

| Section | Status | Evidence / Note | Next Update |
| --- | --- | --- | --- |
| Context | done | Gate/skills/session/ACTIVE/audit handoff restored; current dirty baseline captured | only update on contradiction |
| Plan | done | contract/conflict graph frozen；required Grok implementation + review packets recorded | only update on review contradiction |
| Implementation | done | memory visibility、视觉身份、side-channel、visible floor、Style/dedup 与 composition root 已接线 | no deploy |
| Verification | done | post-review RED→GREEN、full pytest 5093、scoped Ruff/Pyright 0、same-pattern/global namespace negatives | preserve full-lint baseline caveat |
| Handoff | done | migration、maintenance、tracker 与 ACTIVE 已对齐；未部署 | production Style cleanup remains separately authorized |

## Todo

- [x] Recover continuity and load current parallel/Grok/TDD/debugging rules.
- [x] Capture base and protected dirty baseline.
- [x] Write and confirm failing behavioral tests.
- [x] Create isolated worktrees and scoped WIP snapshots.
- [x] Dispatch first required Grok packet; session and real child evidence recorded.
- [x] Dispatch corrective required Grok packet for rejected acceptance gaps.
- [x] Integrate implementation delivery manifests and independently reject/fix remaining gaps.
- [x] Run collision/negative/runtime-safe verification.
- [x] Complete independent read-only Grok review.
- [x] Close confirmed defense-in-depth findings and add negative tests.
- [x] Add migration/rollback notes and maintenance log entry; close ACTIVE.

## Decisions

| Decision | Choice | Why | Date |
| --- | --- | --- | --- |
| Deployment | not authorized | User asked for remediation; prior live-publish authority does not automatically carry into this task | 2026-07-19 |
| Production data cleanup | deferred, separately authorized only | Style evidence cleanup requires backup and migration semantics | 2026-07-19 |
| Parallel requirement | required | User explicitly requested Grok parallel agents and two independent conflict domains exist | 2026-07-19 |
| Cost mode | normal | User requested speed, not token/cost reduction | 2026-07-19 |

## Verification

| Check | Command / Evidence | Result |
| --- | --- | --- |
| Repository gate | `git rev-parse --show-toplevel`; README first heading; ACTIVE exists | pass |
| Baseline | `git status --porcelain=v1`; SHA-256 digest | 100 tracked / 134 untracked; protected |
| Memory focused | `pytest test_card_store test_retrieval test_memory_visibility_scope test_memo_extractor_write_policy test_memo_tools test_visual_identity_store test_visual_identity_reply_pipeline` | pre-review 188 passed；post-review changed files 47 passed |
| Visual/language focused | visual/router/style/finalize/repair/rewrite/dedup/segmentation suite | 111 passed |
| Lifecycle/catalog | chat runtime/application/catalog/health/kernel/plugin bus suite | 149 passed |
| Context related | context service/plugin + retrieval/card/memo visibility | 244 passed |
| Full pytest | `uv run pytest -q` | final 5093 passed / 17 skipped / 189 warnings |
| Scoped Ruff | all files touched by this remediation | pass |
| Scoped Pyright | production + focused test files | 0 errors / 0 warnings |
| Full Ruff/Pyright baseline | whole dirty workspace | Ruff 179 / Pyright 391, unrelated coursework/research/ipv6/legacy WIP; not modified |

## Test Ledger

Append-only. Every meaningful experiment records exact command, actual result, and conclusion.

| ID | Command / Input | Actual Result | Conclusion | Date |
| --- | --- | --- | --- | --- |
| T00 | Prior audit focused suite | 6 passed | Root-cause evidence reproduced before this implementation turn; not completion evidence | 2026-07-19 |
| T01 | Structured Grok session metadata/events | child `019f7a50-24c2-7990-853d-897e100f17cb`, assigned G-VIS-LANG, `child_cwd=.../g-vis-lang`, status running | required parallel contract satisfied at first natural checkpoint after one corrected spawn attempt | 2026-07-19 |
| T02 | `source ./scripts/dev/env.sh && PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest tests/test_drift_detector_client.py tests/test_natural_split.py tests/test_streaming_segmenter.py tests/test_render_message_character_recognition.py tests/test_build_group_messages.py tests/test_style_extractor.py tests/test_dedup_gate.py -q` | 68 passed, 1 warning | Existing tests are green but protect several wrong contracts; this is the integration-before baseline | 2026-07-19 |
| T03 | G-VIS-LANG write-isolation recovery | prepared worktree write tool returned `Operation not permitted`; same child verified `/tmp` writable and resumed in `/tmp/g-vis-lang-impl` | path-specific sandbox mismatch recovered without duplicate child or scope takeover | 2026-07-19 |
| T04 | First Grok delivery manifests | G-MEM: 9 visibility + 186 related pass; G-VIS: 15 contract + 81 related claimed; child id/activity valid | artifacts preserved, but worker success text is not acceptance | 2026-07-19 |
| T05 | Codex independent diff inspection | router still writes desc prose into user text; phrase history not passed by production guardrail; repair test calls finalizer directly; punctuation filter drops emoji; Style marker includes generic `检测到`; G-MEM lacks visual identity store | first candidates rejected; corrective Grok run required before integration | 2026-07-19 |
| T06 | Corrective structured Grok metadata/events | session `fe177e28-7fa2-4b7a-bf38-cd764c463be3`; child `019f7a68-03a0-7630-904a-05ab89764b50`; cwd `/tmp/g-vis-lang-impl`; status running | corrective required-parallel contract satisfied at first natural checkpoint | 2026-07-19 |
| T07 | G-VIS corrective transport event | child hit HTTP 524 after identifying three remaining failures; `/tmp/g-vis-lang-impl` retained production edits and tests | transient outage window started; reconnect same canonical child/parent before replacement | 2026-07-19 |
| T08 | Same-target recovery checkpoint | corrective child resumed and reported 37 focused tests green | concrete progress reset outage window; no replacement spawned | 2026-07-19 |
| T09 | Corrective child completion / parent interruption | G-VIS child SUCCESS with 73 focused; parent Grok exited 1 while collecting final manifest because stored 524 propagated | child scope complete; G-MEM artifacts preserved; rebuild one top-level finalization target only | 2026-07-19 |
| T10 | `pytest tests/test_visual_identity_store.py::test_same_user_same_image_corrections_coexist_across_groups` RED→GREEN | old key overwrote group A；三列 `scope_key` 后 10 store tests passed | same-user cross-group collision closed | 2026-07-19 |
| T11 | Production post-reply RED→GREEN ledger | ReplyContext fields、`_fire_post_reply` trigger/sanitization、real chat propagation、Memo correction routing、composition-root init/close逐片 RED→GREEN | real correction ABI closed without path/base64 leakage | 2026-07-19 |
| T12 | Legacy visual schema migration RED→GREEN | old two-column PK migrated atomically；legacy row recalled in original group | pre-scope dev schema no longer blocks startup or cross-group coexistence | 2026-07-19 |
| T13 | Router required Grok run | session `bf004f17-572b-4127-a5d1-8f3ee2e5e2ad`; child `019f7a96-0ffa-7f23-801e-2d5736dba707`; 10 RED then GREEN | required child contract met；exact lookup integrated | 2026-07-19 |
| T14 | Grok stash incident audit | structured event proves `git stash push ... -- kernel/router.py` only, then `git stash pop` exit 0；stash list empty；helpers/tests present | red-line violation recorded；no evidence of lost unrelated files；future packets remain read-only for review | 2026-07-19 |
| T15 | Human-vs-machine correction conflict RED→GREEN | prior output `('高松灯','错误角色')`；after fix only `('高松灯',)` | exact human correction is authoritative, not additive | 2026-07-19 |
| T16 | Explicit memo lookup scope RED→GREEN | other user card body was returned；after `_scope_allowed` current user/group/global only | same-pattern explicit-scope leakage closed while preserving query WIP | 2026-07-19 |
| T17 | Focused integration | 87 + 188 + 111 + 149 + 244 passed | five audit chains and lifecycle/catalog/context neighbors green | 2026-07-19 |
| T18 | Full pytest attempt 1 | 5076 passed / 5 failed；all failures were old visual-prose expectations or quoted enrichment ref timeout | updated wrong tests；fixed real timeout regression | 2026-07-19 |
| T19 | Full pytest attempt 2 | 5080 passed / 1 failed；backup disk fixture read host at 90% | made health test deterministic at 50%; product behavior unchanged | 2026-07-19 |
| T20 | Full pytest final | 5081 passed / 17 skipped / 189 warnings | full behavioral acceptance green | 2026-07-19 |
| T21 | Static checks | scoped Ruff pass；scoped Pyright 0；whole dirty workspace Ruff 179 / Pyright 391 outside scope | remediation clean；unrelated coursework/research/ipv6/legacy WIP preserved | 2026-07-19 |
| T22 | `tests/test_memo_tools.py` CardUpdate authorization RED→GREEN | foreign add/update/supersede/expire 与 global write 均拒绝；15→17 focused passed | chat LLM tool cannot poison another entity/global；CardStore direct admin/background boundary preserved | 2026-07-19 |
| T23 | Required Grok final read-only review | session `d329e4f7-c07d-433e-a4ec-271bfa92d33a`; completed child `019f7abd-c8e9-7fd1-8107-44ab395892c4`; 0 Critical；zero edits | old CardUpdate Important finding stale；store authorization、tool prompt/schema、diagnostic scrub residual require Codex decision | 2026-07-19 |
| T24 | Store authorization RED→GREEN + parser negatives | missing auth initially DID NOT RAISE；写入点校验后 invalid trigger/count/provenance/hash/missing context 全拒绝；visual store/pipeline 30 passed | production parser and persistence boundary now both enforce explicit single-image correction contract | 2026-07-19 |
| T25 | Tool contract/global namespace RED→GREEN | schema originally advertised global write；query returned `global/legacy-other-global`；修复后 memo tools 17 passed | prompt contract matches enforcement；canonical global recall strictly `global/global` | 2026-07-19 |
| T26 | Final static + full regression | scoped Ruff pass；scoped Pyright 0；`uv run pytest -q` = 5093 passed / 17 skipped / 189 warnings | final code/test state accepted；no deploy or external action | 2026-07-19 |

## Delivery manifests

- First run top-level session: `dbbcdf6f-8c70-4471-864c-d37eb2076652`, exit 0.
- Required child: `019f7a50-24c2-7990-853d-897e100f17cb`, 172 tool calls, actual artifact `/tmp/g-vis-lang-impl`.
- First-run outcome: preserved as candidate only. G-MEM visibility slice is structurally useful but incomplete for the audit; G-VIS explicitly reported caller/history and landing blockers, and Codex found additional real-side-channel/test-quality gaps.
- Corrective visual/language delivery: child `019f7a68-03a0-7630-904a-05ab89764b50` completed with focused evidence；parent manifest collection was interrupted by stored 524, so Codex accepted only after scoped artifact inspection and independent integration tests.
- Corrective recovery review: `2518a21c-373b-4217-8149-2ff3d001280a`; Memory 217 and Visual 73 reported before Codex integration; main `memo_tools.py` intentionally not overwritten.
- Router exact-lookup run: `bf004f17-572b-4127-a5d1-8f3ee2e5e2ad`; real child `019f7a96-0ffa-7f23-801e-2d5736dba707`; 10 RED→GREEN. Codex added authoritative-override fix and timeout integration after review.
- Read-only final review: session `d329e4f7-c07d-433e-a4ec-271bfa92d33a`, completed privacy child `019f7abd-c8e9-7fd1-8107-44ab395892c4`（compaction 前 metadata child `019f7aba-3b36-7de1-9907-08f5fa93f2c0` 亦 completed）；final verdict Conditional Accept / 0 Critical。Codex closed store authorization、tool description/schema 与 noncanonical global query，保留开放域 diagnostic wording 与 first-party plugin trust 为 documented residual risk。
