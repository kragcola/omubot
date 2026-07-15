# 谐音理解辅助上线

> 状态：done
> mode: task
> 最后更新：2026-07-15 CST
> 当前下一步：无；后续仅按真实日志扩充人工审核规则，不做自动拼音枚举。
> 阻塞：无。
> 验证证据：review 0C/0I；3507 full passed；image/runtime smoke 与 2m27s 零群出站窗口通过。
> 回滚入口：移除 HomophoneProvider 注册及 Thinker hint 构造；原始消息与存储合同从未改变。

## Resume Capsule

- objective: 让 Thinker 与主回复模型理解用户的高置信谐音表达，例如 `窝讨厌泥` 可能表达 `我讨厌你`。
- next_step: 无。
- current_files: `services/homophone/`、`services/block_trace/`、`services/llm/client.py`、`bootstrap/chat_runtime.py`、对应 tests。
- last_verified: 现有 `SlangProvider` 只消费已审核群黑话；Thinker 已支持动态 `slang_hint`；主模型已有 active PromptProviderBus。
- do_not_redo: 不引入全局文本替换，不改 `conversation_text`、timeline、message log、memory、research DB 或学习归一化证据。
- rollback: 切 `omubot-bot:pre-homophone-20260715-c959054` 后只 recreate bot；不碰 NapCat。

## Section Progress

| Section | Status | Evidence / Note | Next Update |
| --- | --- | --- | --- |
| Context | done | 只读链路审计完成；确认 ProviderBus 是唯一主生成注入边界 | 不再重查 ingest/timeline |
| Plan | done | 整短语白名单、最长匹配、载体保护、slang 冲突抑制 | 按 RED 逐层实现 |
| Implementation | done | 解释器、slang guard、Thinker、ProviderBus、atomic budget、bootstrap 已接线 | 独立复审 |
| Verification | done | 118 focused、224 expanded、3507 full；Ruff/Pyright clean；review 0C/0I；runtime pass | 无 |
| Handoff | done | commit/image/container/rollback/window 已记录 | ACTIVE 归零 |

## Next Session Starts Here

- Direction: 从纯函数内核向外接入，任何候选都只形成动态提示。
- First action: 精确 add/commit 本 tracker 列出的文件，不纳入其他脏改动。
- Open questions: 首批可审计词表的覆盖范围应以保守低误伤为先，后续根据真实日志增量扩充。
- Do not redo: 不用 `rapidfuzz` 做字符相似度；不做逐字无声调拼音全局替换；不让提示参与原始证据持久化。

## Todo

- [x] 定位当前输入、Thinker 与主 prompt 注入路径
- [x] 冻结解释结果模型、置信度与正负例
- [x] RED-GREEN 实现纯函数解释器
- [x] 接入 Thinker 动态提示
- [x] 新增并注册主模型 HomophoneProvider
- [x] focused/full pytest、Ruff、Pyright、diff-check
- [x] 独立 code review
- [x] 精确提交、bot-only rebuild/recreate
- [x] 运行态 prompt/静默群零出站验证

## Decisions

| Decision | Choice | Why | Date |
| --- | --- | --- | --- |
| Evidence ownership | 原始文本为唯一事实，候选释义只做 prompt hint | 防止污染引用、日志、记忆、研究采集与后续训练 | 2026-07-15 |
| Detection strategy | 保守的 phrase/context 规则与显式负例，不做盲目逐字替换 | 中文同音歧义高，`泥土`/`蜂窝` 等普通词不能误改 | 2026-07-15 |
| Prompt reach | Thinker 与主 PromptProviderBus 各消费同一纯函数结果 | 决策阶段和回答阶段都需要理解，且共用单一语义 owner | 2026-07-15 |
| Persistence | 无持久化、无 schema | 解释是请求期派生信息，可随代码回滚 | 2026-07-15 |
| Carrier protection | 引用、代码、URL、CQ/meta-language 按 span 保护 | 不误解载荷片段，也不吞掉同一消息其他真实谐音 | 2026-07-15 |
| Slang collision | 同 surface 命中本群 approved slang 时抑制通用谐音 hint | 群内已审核语义优先，且不跨群泄漏 | 2026-07-15 |
| Downstream isolation | classifier、arbiter、topic attribution、memory/research 均保持原文 | 谐音候选不能改变出站决策或成为持久事实 | 2026-07-15 |

## Files Touched

| File | Change | Status |
| --- | --- | --- |
| `docs/tracking/ACTIVE.md` | 指向本任务 | in_progress |
| `docs/tracking/homophone-understanding-2026-07-15.md` | 连续性、合同与验证台账 | in_progress |
| `services/homophone/*` | 解释器、提示格式与群 slang evidence 过滤 | done |
| `services/block_trace/homophone_provider.py` | 主模型动态 candidate | done |
| `services/block_trace/budget_manager.py` | atomic candidate 整块拒绝 | done |
| `services/llm/client.py` / `thinker.py` | Thinker hint 与主 Provider context 接线 | done |
| `bootstrap/chat_runtime.py` | composition root 注册 | done |
| `tests/test_homophone_*` 及相关既有 tests | RED/GREEN 与集成不变量 | done |

## Verification

| Check | Command / Evidence | Result |
| --- | --- | --- |
| Repository gate | README 首标题 `# Omubot`，canonical ACTIVE 存在 | pass |
| Worktree boundary | `git status --short` 已记录无关修改与 untracked | pass |
| Read-only path audit | `/root/homophone_path_audit`：ProviderBus 唯一注入边界；ingest/timeline 禁止改 | pass |
| Slice 1 RED | interpreter/provider/Thinker 定向集合 | 13 failed / 50 passed；均为预期缺实现 |
| Slice 1 GREEN | `pytest tests/test_homophone_interpreter.py tests/test_homophone_provider.py tests/test_thinker.py -q` | 63 passed |
| Integrated focused | homophone + budget + client + wiring + thinker runtime | 97 passed / 22 warnings |
| Expanded regression | ProviderBus/LLMClient/bootstrap/typed boundary 聚合 | 207 passed / 62 warnings |
| Static | scoped Ruff + Pyright | clean / 0 errors, 0 warnings |
| Full regression | `uv run pytest -q` | 3486 passed / 17 skipped / 175 warnings |
| Review closure | 初轮 0C/4I/1M；两轮 RED 修复后 closure | 0 Critical / 0 Important / 1 accepted Minor |
| Final focused | 解释器/provider/Thinker/budget/wiring/runtime | 118 passed / 22 warnings |
| Final expanded | LLMClient/ProviderBus/bootstrap/typed boundary | 224 passed / 62 warnings |
| Final full | 最后一次代码变更后 `uv run pytest -q` | 3507 passed / 17 skipped / 168 warnings |
| Commit | `c9590543aa90698cf679a542a280ada31aaa3433` | pass |
| Image integrity | 6 个关键 source host/image SHA | 0 mismatch |
| Runtime semantic | live-image Provider + Thinker mock request | candidate 完整；hint in dynamic blocks；普通词不命中 |
| Runtime health | Application complete / OneBot connected / Admin 200 | pass |
| Silent outbound window | UTC 04:32:58-04:35:25，78 group inbound / 55 silent_learn | bot/NapCat group outbound 0 |
| Deployment | image `f5aa4c590b1f...` / container `aadfe15b6bc6...` | restart=0 / OOM=false |
| NapCat invariant | `19f6cf13607c...` / StartedAt `2026-07-09T22:51:47.963549084Z` | unchanged / restart=0 |

## Test Ledger

| ID | Command / Input | Actual Result | Conclusion | Date |
| --- | --- | --- | --- | --- |
| H-001 | `窝讨厌泥`、常见整短语、Provider、Thinker 参数 | 13 个正向行为失败；负例通过 | RED 有效，进入最小实现 | 2026-07-15 |
| H-002 | `  窝讨厌泥！` | 首次 GREEN 得到 `  我讨厌你!`，1 failed / 62 passed | NFKC 不得改变未命中全角标点；改为规范化检测视图 + 原文替换 | 2026-07-15 |
| H-003 | 同 H-001/H-002 focused 集合 | 63 passed | 纯函数、basic provider、Thinker 参数层转绿 | 2026-07-15 |
| H-004 | slang conflict / atomic budget / client wiring / bootstrap | 9 failed / 1 passed | 第二轮 RED 有效 | 2026-07-15 |
| H-005 | H-004 + Slice 1 | 94 passed，后续不变量扩为 97 passed | 集成路径与原文不变量转绿 | 2026-07-15 |
| H-006 | composition root store getter 所有权 | 1 failed：无参 Provider 缺 getter | RED 后移除 QueryContext 扩张，改具体 Provider 注入 | 2026-07-15 |
| H-007 | expanded + full pytest | 207 passed；3486 passed / 17 skipped | 未发现跨模块行为回归 | 2026-07-15 |
| H-008 | review 4I 复现 | 普通词误伤、carrier 整条短路、slang unavailable、partial conflict 均复现 | 反馈成立，逐项补 RED | 2026-07-15 |
| H-009 | review 修复 focused | 106 passed 后 closure 尚余普通词系统性 1I | 后缀 blacklist 不充分，改 fail-closed continuation allowlist | 2026-07-15 |
| H-010 | 动作续接召回 | 4 failed -> 118 passed；7 普通词负例保持 | 多字符续接前缀兼顾 precision/recall | 2026-07-15 |
| H-011 | final closure/full | review 0C/0I；3507 passed / 17 skipped | 实现侧可提交上线 | 2026-07-15 |

## Handoff

已完成。实现 commit `c959054`；部署 image `f5aa4c590b1f...`、container `aadfe15b6bc6...`；rollback tag `omubot-bot:pre-homophone-20260715-c959054`；NapCat 未操作。
