# Omubot 黑话模块修复跟踪

本文用于实时跟踪 `docs/audits/slang-module-audit-2026-05-10.md` 与 `docs/audits/slang-module-remediation-plan-2026-05-10.md` 的落地状态。每次实现、测试、复盘或发现风险后都应更新。

## 当前状态

- 状态：Phase 0 已完成；Phase 1 已完成；Phase 2 已完成；Phase 3 已完成；Phase 4 已完成；Phase 5 已完成；Phase 6 已完成；Phase 10 已完成；Phase 11 已完成；Phase 12 已完成；Phase 13 P0 / P1-2 / P2-2 已落地，P1-1（反向重申）经数据决断后**搁置不实施**
- 当前阶段：Phase 13 — backlog reviewer 治理与 AI review 契约重构
- 当前目标：观察 `backlog_auto_approve_min_confidence=0.70` 阈值下一轮清池效果（预期通过率 3% → 25%）；P1-1 反向重申暂搁置（数据反证：当前 LLM 通过率 33%、否决率 4%，瓶颈是阈值不是 LLM 一致性，反向重申会反伤 `is_public_meme=true` 的合理梗）；P2-1 jieba 仍在"待评估"，需积累 ~200 条人工驳回库后再启动
- 暂不改动：NapCat、Admin 页面样式、embedding 路线、重型 RAG

## 与 ConversationArchive 的关系

黑话模块 daily review 与手动抽取已优先使用 `ConversationArchive` cursor，不新增自己的原始消息表：

- `slang_manual_extract` 作为独立 scanner 记录手动抽取进度，避免重复扫描最近窗口。
- `slang_daily_review` 使用独立 scanner，避免手动抽取推进 daily review 进度。
- 如果 MessageLog 对象没有 archive 能力、archive 读取失败或 cursor 需要人工重扫，会自动退回旧 `query_recent()` 最近窗口。
- 黑话 observation/evidence 写入或可重建 `conversation_message_refs`，未来 dry-run 清理时作为证据保护来源。
- stoplist、pending key 索引、global scan、semantic/drift review 等黑话业务语义仍留在 `SlangStore`。
- 缺少 required cursor 时，ConversationArchive 清理 dry-run 应阻塞，不影响现有黑话运行。

详细方案见 `docs/conversation-archive-implementation-tracker.md`。

## 决策记录

| 日期 | 决策 | 原因 |
| --- | --- | --- |
| 2026-05-10 | 先修 daily review 状态机，再修 collision | 当前线上“AI 自动通过没触发”比重复词条更直接阻断功能 |
| 2026-05-10 | stale recovery 以 `slang_extraction_runs` 为准 | meta 只能表示最后一次状态，不能可靠表达 dangling run |
| 2026-05-10 | `daily_ai_review_time` 改为分钟数比较 | 当前字符串比较可工作，但数值比较更稳，更利于后续维护 |
| 2026-05-10 | `last_daily_ai_review_date` 只在当天存在成功复核时才当作已执行 | 兼容旧版本遗留的“日期已写入但 run 仍 running”脏状态 |
| 2026-05-10 | daily review 长历史改为只看最近一个受控窗口 | 200 条一口气喂给 extractor 会把候选抽成空，分批又容易拖超时，最终收敛到最近窗口策略 |
| 2026-05-10 | collision helper 只做 normalize 后精确碰撞 | 语义去重不应进入热写入路径，避免把 drift / daily review 语义职责揉进去 |
| 2026-05-10 | 日志增强前移到 Phase 2 之前 | 后续 collision 和缓存改动需要先有可对账日志 |
| 2026-05-10 | daily review 复核 pending 队列，search 退为辅助证据 | 只扫最近消息会让待处理一直堆着；AI 明确判定“不通过”的 pending 现在会被消耗掉 |
| 2026-05-11 | `last_semantic_inference_count` 只按整型阈值比较 | 语义复核阈值是 count gate，不应受 0..1 浮点归一化影响 |
| 2026-05-11 | prompt block 只注入当前上下文直接命中的已批准黑话 | 避免把未命中的 approved 词条整块塞给主 LLM |
| 2026-05-11 | 同阈值重复复核在调用层先短路 | 降低重复 LLM 烧模型风险，便于 pending meta 稳定复盘 |
| 2026-05-11 | web search 仅保留为辅助证据 | 语义 compare 不依赖搜索结果，避免搜索噪声覆盖群内语义 |
| 2026-05-11 | 冲突返回结构保持兼容接口并归档 | 当前链路已闭环，结构化返回留作下一轮维护重构项 |
| 2026-05-11 | stale run 回收误伤风险已用回归验证 | 现有 stale recovery 单测与 tick 回收测试已覆盖该路径 |
| 2026-05-11 | 手动“全量 AI 复核”必须同时复核 pending 与 candidate | 用户点击全量复核时预期是“把待处理都扫一遍”，不能继续受日常阈值 gate 限制 |
| 2026-05-11 | 黑话库使用 `journal_mode=DELETE` 而非 WAL | Docker Desktop bind mount 下出现过 deleted WAL 句柄导致 Admin API 与宿主 SQLite 视图分裂；黑话库写入量低，优先选一致性 |
| 2026-05-11 | candidate AI 复核结果必须分栏可见 | AI 未通过/保留的候选仍需要人工二次判断，不能只显示“通过”队列 |
| 2026-05-11 | 普通全量复核默认不覆盖已复核 candidate | live 观察到建议通过数从 14/82 波动到 8/83，说明重复 LLM 判定会造成审核口径漂移 |
| 2026-05-11 | AI 明确未通过的 candidate 自动归档为 muted | 人不应承担逐条清理普通词/非黑话；AI 高置信否决必须进入可恢复的“AI 未通过”队列 |
| 2026-05-11 | candidate 复核协议改为 `decision + decision_confidence` | 旧 `confidence` 被模型理解成“像黑话的概率”，会把明确普通词低分误归到观察不足 |
| 2026-05-11 | 退回候选会清空 AI 复核痕迹并重新计入未审候选 | 恢复动作应让词条回到 `candidate_ai_unreviewed`，避免旧 `ai_rejected` 状态污染人工队列 |
| 2026-05-11 | drift 创建必须先过专用语义门控 | n-gram/子串相似度只能做轻量守卫，不能证明语义漂移；`same_meaning/alias_candidate/unclear` 一律不开 open drift |
| 2026-05-11 | drift gate LLM 不可用时 fail closed | 宁可漏开弱漂移，也不能把同义改写继续塞进人工 open 队列 |
| 2026-05-12 | pending alias 预过滤改用 normalized key 索引表 | `aliases_json LIKE` 无法可靠匹配归一化 alias，容易漏合并 `P J S K` / `pjsk` 这类候选 |
| 2026-05-12 | stoplist 按 term + aliases 彻底停用 | Wiki 口径是“永不学习词”，alias 命中也应阻止新入库，并从 match / 注入 / lookup 隐身 |
| 2026-05-12 | `message_id=None` 的 hit buffer 使用内部 event key | 无消息 ID 时仍可能连续收到多条同词消息，不能在 2 秒缓冲窗口内互相覆盖 |
| 2026-05-16 | "AI 是否审过"与"AI 结论"用两个独立字段表达，不复用 `ai_approved` 布尔 | 原方案 A 把"是否审过"和"审核结论"塞进同一个 bool 是 1075 条候选不收敛的根因；deepseek H2 + gpt High #2 联合识别后改为 `ai_reviewed_at` + `ai_review_decision` 两字段彻底解耦，`ai_approved` 仅留 daily 兼容 |
| 2026-05-16 | 历史 meta 迁移 CASE 直读 `backlog_review.approved` 源字段，不依赖最终 status | "backlog 通过→人工驳回"的 row（status='muted' 但 `approved=1`）若按 status 反推会被误标 decision='rejected'，污染 AI 通过率统计；CASE 应表达"AI 当时的结论"而非"最终状态" |
| 2026-05-16 | 第二段 daily 迁移 WHERE 用 `NOT LIKE ai_review_source` 而非 `NOT LIKE ai_reviewed_at` | `upsert_ai_approved_term` 已经写过 `ai_reviewed_at`（store.py:1063），按它过滤会 0 行命中，daily 50 条永远拿不到 source/decision；改用 source 判断"是否已被新契约迁移过"语义更准 |
| 2026-05-16 | LIKE 双格式纪律明文化 | Python `json.dumps(ensure_ascii=False)` 输出带空格 `"k": "v"`，SQLite `json_set` 输出紧凑 `"k":"v"`；每条 LIKE / NOT LIKE 必须双格式同时写，与现有 `_ai_review_sql_condition` 范式统一，否则单格式会让其中一种来源永远不命中 |
| 2026-05-16 | backlog mute 与人工 mute 在 CASE 里用 revision 表 EXISTS 子查询区分 | 用户对 backlog kept candidate 直接点 admin `/mute` 走 `set_status` 不写 meta，CASE 仅靠 `approved=0 AND status='muted'` 会把人工 mute 误标 AI 否决；用 `EXISTS (action='backlog_review:mute')` 子查询区分，剩余的人工 mute 落 ELSE `'kept'`（保留 AI 当时是 kept 的真实结论） |
| 2026-05-16 | 大规模 meta 契约迁移按"自审 2 + 外部审计 2 + 修订后再审计 2 = 六轮"工序进入实施 | 1075 条候选 meta_json 永久重写、上线后回滚成本极高；前五轮已识别 N1-N7 + gpt 5 + deepseek 8 共 20 项缺陷，第六轮 claude 终审又发现 8 项 net-new（含 1 致命 N5 死代码、2 重要 O2/O3、5 实施级歧义），证明该工序对此规模的迁移是必要质量保障 |
| 2026-05-17 | P1-2 群内 UGC 取证落地，不依赖外部 search | 用户在 settings 主动关闭 `backlog_review_search_enabled`；本轮 200 条决策中 group_evidence=0 占 80% 是 `_collect_context` 取"最近 8 条 user 消息"对存量审核错位（存量词条几天前讨论的，最近聊天里不出现）；改为 `MessageLog.query_term_hits` 按 term/alias `LIKE` 全文索回历史命中行，新增 `backlog_local_evidence_count: int = 5`；先跑数据再决定是否启动 search |
| 2026-05-17 | P2-2 频次阶梯重判与 P0-1 共进，避免重复审同批 kept | `[3, 8, 30, 100]` 阈值 + `meta.last_inference_count` 跨档门；用 `json_extract(meta_json, '$.last_inference_count')` SQL 过滤，candidate 池 ~800 → 阶梯门后 ~290 实际可审；`backlog_threshold_gating_enabled` 设置项可一键关 |
| 2026-05-17 | 手动 AI 清池循环改为无总 timeout，`per_batch_timeout_s=600s` 单批兜底 | 旧 `timeout_s=510s` 是从 tick job 的 `_TICK_JOB_TIMEOUT_S * 0.85` 抄来的；tick 必须给下一次留余量，手动触发不应套这个限制；本轮线上证据：301 条池子跑 11 分 30 秒就 deadline，processed=150 / completed=False；改为 `for _ in range(max_batches=200)` 配合每批 wait_for(600s)，能一次清空任意规模池子 |
| 2026-05-17 | kept 路径不写 `last_inference_count`，仅 approved/rejected/streak_mute 三条定谈分支写 | 之前 P2-2 把 stamp 写在 meta_patch 顶部，所有路径都被打上跨档门，导致 LLM approved 但 conf 不到自动通过阈值的 73 条词条被锁住下一轮也不能再审；定谈才 stamp，疑义未决（kept）等下一轮再看，符合"等更多证据"的语义 |
| 2026-05-17 | `backlog_auto_approve_min_confidence` 0.82 → 0.70 | 本轮 308 条决策证据：LLM `approved=true` 102 条但 conf 集中 0.5-0.8，0.82 阈值砍掉 43 条 0.7-0.82 区间合理通过（含"圆神""老祖""凹暴击""杰专"等明显合理黑话）；LLM 自评置信度精度不到 0.05，0.82 这条线把"明显该过的"切两半，纯属阈值精度高于 LLM 自评精度；先降到 0.7 看实测 |
| 2026-05-17 | P1-1 反向重申暂搁置，不立即实施 | 数据反证 P1-1 不解决当前问题：① 当前 LLM 通过率 33%、否决率 4%、`approved=true is_public_meme=true` 46 条，瓶颈是阈值不是 LLM 一致性；② 反向重申"脱离上下文能解释 → 否决"会反伤 `is_public_meme=true` 的合理公网梗（脱离上下文也能解释，这是优点不是缺点）；③ `backlog_review.is_public_meme` 字段已隐含一次反向自检，主 prompt 里 LLM 自己已经做了"这是公网梗 vs 群内黑话"的判断；④ 真正适用场景是"同一个 LLM 在不同批次对同个词矛盾判断"，当前数据看到的是保守一致中等置信，不是矛盾 — 是阈值精度问题不是稳定性问题；何时改主意：等 0.70 阈值跑完，若出现"明显错误的高置信通过"（如把"扫码"判成 approved 0.85）才需要反向重申给真黑话二次验证 |

## 实施清单

| 阶段 | 项目 | 状态 | 验收 |
| --- | --- | --- | --- |
| Phase 0 | 审计与修复方案归档 | 已完成 | 审计报告与修复方案文档存在且边界清晰 |
| Phase 1 | `run_daily_ai_review_if_due()` 不再提前写 `last_daily_ai_review_date` | 已实现 | 只有成功完成后才写当天日期 |
| Phase 1 | stale `daily_ai_review` run 检测与回收 | 已实现最小版 | `started_at` 超阈值的 `running` run 可回收为 `abandoned` |
| Phase 1 | daily review 时间判断改为分钟数比较 | 已实现 | 不再依赖字符串字典序 |
| Phase 1 | daily review / manual extract 关键日志增强 | 已实现 | daily review 与 manual extract 都有 start/success/fail/timeout/stale_recovered 关键日志 |
| Phase 1 | daily review 相关单测补齐 | 已实现 | 覆盖失败不消耗当天机会、stale run 回收、tick timeout 回收、store 回收接口 |
| Phase 1 | tick 内 daily review / manual extract 超时拆分 | 已实现 | 两条后台任务分别 timeout，并在 timeout 后回收 `running` run |
| Phase 1 | admin / 前端对 `abandoned` run 状态兼容 | 已实现最小版 | API 返回 `abandoned`，SlangView 类型与状态显示已容忍 |
| Phase 2 | term + alias 精确碰撞 helper | 已实现 | `find_existing()` 改为 term+alias normalize 精确碰撞 |
| Phase 2 | 冲突返回结构替代 `str \| None` | 已归档 | 保留兼容返回接口，作为下一轮维护重构项，不纳入当前实施范围 |
| Phase 2 | `_promote_pending_candidate()` 保留 pending aliases | 已实现 | existing 命中时 alias / observation / pending meta 会并回 existing |
| Phase 2 | existing 命中时吸收同 key pending | 已实现 | 避免 alias 冲突后留下幽灵 pending |
| Phase 2 | collision 路径测试补齐 | 已实现核心回归 | alias 撞 term/alias、pending promote 命中 existing、admin abandoned run 均有测试 |
| Phase 3 | settings 内存缓存与失效机制 | 已实现 | admin 保存后 plugin 可见最新 settings，跨 store 实例会刷新 |
| Phase 3 | group term snapshot 缓存 | 已实现 | 高频群走 group snapshot cache，写入后会失效刷新 |
| Phase 3 | `record_hit()` 写放大评估/收敛 | 已实现 | `record_hits()` 支持批量命中写入，插件热路径已改为批量调用 |
| Phase 4 | 短词/alias 匹配门槛收紧 | 已实现 | 两字、三字短词不再异常膨胀，短 ASCII 词需更强边界 |
| Phase 4 | `_pick_source_row()` 重写 | 已实现 | 短 evidence 优先 exact line，再保守回退，不再优先误绑 |
| Phase 5 | slang extract/review task 入口统一 | 已实现 | `slang` / `slang_review` 任务入口已拆分，review 不再走裸 `_call`，并补了结构化运行日志 |
| Phase 5 | daily review 同步复核 pending 队列 | 已实现 | pending 候选会被按群限量复核；明确拒绝会转为 muted，避免待处理长期堆积 |
| Phase 6 | 黑话语义复核三段推断 | 已实现 | pending 达阈值后先做上下文、裸义、对比三段语义判定 |
| Phase 6 | 语义复核 meta/revision 可审计 | 已实现 | `last_semantic_inference_count` 与 semantic_* meta 已落库，拒绝/候选/通过路径可回溯 |
| Phase 6 | prompt block 仅注入直命中已批准黑话 | 已实现 | 只注入当前上下文命中的 approved/global-or-current-group 词条 |
| Phase 6 | 语义复核与回归测试 | 已实现 | 新增 reviewer/store/plugin 回归，覆盖 no_info、parse fail、timeout、重复阈值、prompt 注入边界 |
| Phase 6 | `PluginContext` 黑话字段显式声明 | 已完成 | `slang_store` / `slang_plugin` 已纳入内核上下文字段 |
| Phase 6 | `SlangStore` 拆分预案 | 已归档 | 先保留为后续维护计划，不纳入当前实施范围 |
| Phase 7 | 手动全量 AI 复核穿透 pending 阈值 | 已实现 | `review_candidates=true` 时 pending 全量语义复核，并记录 `semantic_force_review` |
| Phase 7 | Admin 观察中候选显示 AI 复核状态 | 已实现 | pending 仍保留时显示“全量已审：信息不足”等状态，避免误判为未处理 |
| Phase 7 | 黑话库 WAL 可见性分裂修复 | 已实现 | `journal_mode=DELETE` 生效，bot 不再持有 `slang.db-wal (deleted)` 句柄 |
| Phase 8 | Admin candidate AI 复核分栏 | 已实现 | 候选池新增“AI 建议通过 / AI 未通过 / 未复核”队列，AI 理由在列表卡片可见 |
| Phase 8 | candidate AI 复核稳定性保护 | 已实现 | 普通全量复核只审未复核 candidate；`rerun_reviewed_candidates=true` 才覆盖旧结果 |
| Phase 9 | AI 未通过自动归档 | 已实现 | candidate 复核中 `approved=false` 且高置信时转 `muted + ai_rejected`，不再挤在候选待处理里 |
| Phase 9 | 观察不足分流 | 已实现 | `no_info`/低置信保留为观察不足；超时/解析失败只保留诊断 meta，并继续归入待 AI 复核 |
| Phase 9 | Admin 运营队列口径 | 已实现 | 首页指标和队列改为“待 AI 复核 / AI 建议通过 / AI 未通过 / 观察不足”，失败不再单独成栏 |
| Phase 9 | 重跑历史已审候选入口 | 已实现 | Admin “重跑已审”显式传 `rerun_reviewed_candidates=true`，用于把旧 kept 历史按新规则重新归档 |
| Phase 9 | candidate 复核三态决策协议 | 已实现 | LLM 输出 `decision=approve/reject/observe` 与 `decision_confidence`；旧 `confidence` 只保留为黑话概率/兼容字段 |
| Phase 9 | 复核失败回待审 | 已实现 | 超时/解析失败不会标成已审；下一轮普通 AI 复核会按待审候选重试 |
| Phase 10 | 专用 drift 语义判定器 | 已实现 | `SlangDriftReviewer` 输出 `same_meaning / alias_candidate / real_drift / unclear`，低置信/超时/解析失败 fail closed |
| Phase 10 | store drift 创建门控 | 已实现 | `_maybe_create_drift_review()` 只有高置信 `real_drift` 才创建/刷新 open drift；同义改写不改 approved meaning |
| Phase 10 | alias 型漂移分流 | 已实现 | `alias_candidate` 只允许合并 alias，不改 meaning、不进入 drift |
| Phase 10 | open drift 回放修复 | 已实现 | 新增 `replay_open_drift_reviews()` 与 Admin API `/slang/drift/replay`，可 dry-run 或 apply 关闭历史误报 |
| Phase 10 | Admin drift 可见性 | 已实现 | drift 卡片展示语义门控 verdict / reason，revision 展示 `drift_suppressed` / `drift_alias_candidate` |
| Phase 10 | drift gate 回归测试 | 已实现 | 覆盖 `没米` 同义改写、真漂移、alias candidate、unclear/timeout/parse fail、历史 open drift 回放 |
| Phase 11 | `message_id=None` 命中缓冲事件隔离 | 已实现 | 无消息 ID 的多条同词消息使用内部 event key，不再在缓冲或 flush 分组中互相覆盖 |
| Phase 11 | pending normalized key 索引表 | 已实现 | `slang_pending_candidate_keys` 在 init 时创建并 backfill，pending insert/update/delete/promote/merge 同步维护 |
| Phase 11 | pending merge 走 key 索引预过滤 | 已实现 | `_merge_pending_candidates_into_existing()` 先按 `(group_id, term_key)` 查 pending key 索引，再保留 Python 二次确认 |
| Phase 11 | stoplist alias 新入库拦截 | 已实现 | extractor、candidate upsert、AI approved upsert、manual create 均拒绝 term 或 alias 命中 stoplist |
| Phase 11 | stoplist 运行时隐藏保持一致 | 已实现 | 已存在词条不删除；match、Prompt 注入、lookup 继续通过 term + alias key 过滤 |
| Phase 11 | correctness 回归测试 | 已实现 | 覆盖无 message_id 双消息、pending alias key merge/backfill、stoplist alias intake/runtime、extractor alias stoplist |
| Phase 12 | daily review 去硬超时 | 已实现 | tick 后台任务不再用 90 秒 `wait_for` 取消长复核；运行中时下一轮 tick 跳过，避免反复 start/abandoned |
| Phase 12 | candidate 与 pending 全量复核解耦 | 已实现 | `review_candidates` 只审 candidate；`review_all_pending` 才穿透 pending 阈值；日常复核默认不全扫 pending |
| Phase 12 | pending 语义复核并发与降耗 | 已实现 | pending 语义复核按 3 并发执行；pending 阶段不再做不参与判定的 web search |
| Phase 12 | Admin/API 显式全量 pending 开关 | 已实现 | `/slang/review/run` 支持 `review_all_pending`；Admin“全量 AI 复核”保留手动全量语义复核 |
| Phase 12 | 性能回归测试 | 已实现 | 覆盖日常复核不穿透 pending 阈值、手动全量仍可穿透、tick 不再硬取消 daily review |
| Phase 13 | backlog reviewer 频次门槛（P0-1） | 待实施 | `list_backlog_candidates` / `count_backlog_candidates` 同步加 `min_usage_count` 参数；默认 3；admin summary 新增 `eligible_backlog_count`；前端 settings 4 件文件同步 |
| Phase 13 | AI review meta 契约 + SQL helper 拆分（P0-2，Day 1） | 待实施 | 三路径统一写 `ai_reviewed_at` / `ai_review_source` / `ai_review_decision` 顶层字段；`_ai_review_sql_condition` 改名退役，新增 `_ai_reviewed_sql_condition` / `_ai_approved_sql_condition` / `_ai_rejected_sql_condition` / `_ai_kept_sql_condition` 四个双格式 LIKE helper；老调用点（`list_terms.review_filter` / `summary`）按语义切到新 helper |
| Phase 13 | 历史 backlog/daily meta 一次性迁移（P0-2，Day 1） | 待实施 | 两段 UPDATE：① `LIKE backlog_review AND NOT LIKE ai_reviewed_at` 用直读 `backlog_review.approved` 的 CASE 补 source/decision；② `LIKE ai_approved=true AND NOT LIKE ai_review_source` 给 daily 50 条补 source/decision；先在备份 DB dry-run 输出三类（approved / rejected / kept）数量再上生产；fallback 时间链 `COALESCE(reviewed_at, updated_at, created_at)` 全用历史时间不写 `datetime('now')` |
| Phase 13 | `meta.ai_approved` 全仓读取点盘点（P0-2 N7） | 待实施 | grep `meta\.ai_approved` / `term\.meta\.ai_approved` / `\.ai_approved` 在 admin 前后端、reviewer、其他业务路径的所有读取，逐个改读 `ai_review_decision='approved'`；仅留 SQL 层 `_ai_approved_sql_condition` 兼容 daily 历史；已知点：`admin/frontend/src/views/slang/helpers/badges.ts:80` `isAiApproved` |
| Phase 13 | kept streak 自动降级（P0-3） | 待实施 | `meta.backlog_kept_streak` 累加，连续 ≥ 2 自动 mute 并改写 `ai_review_decision='rejected'`；`record_revision=True` 写审计行；`meta.backlog_kept_history` 保留最多 3 条（run_id / confidence / reason / at）；types/formatters/SettingsForm/SettingsDrawer 四件前端同步 |
| Phase 13 | 前端 tab 重排 + 砍作用域（P0-4，Day 2） | 待实施 | 7 tab：待审核 / AI 审核 / 待观察 / AI 否决 / 已批准 / 语义漂移 / 全部；`review_filter` 新增 `under_observation` / `ai_rejected_only` / `human_reviewed_only`；admin summary 新增 4 个 count 字段；删除 `scopeFilter` 相关 9 处；1280 / 1366 / 1920 三档宽度截图验布局 |
| Phase 13 | P0-4 历史 human_reviewed 迁移 | 待实施 | 一次性 UPDATE 给 `status='approved' AND source!='ai_auto_review' AND NOT 已 AI approved` 的人工新增条目补 `human_reviewed=true`；先 dry-run 对照预期 ≈ 158 - 43 = 115 条；六条 NOT LIKE 全双格式覆盖 |
| Phase 13 | P0-4 5 tab 互斥覆盖证明落档 | 待实施 | 验收信号下补一段 (status, decision 三态, human_reviewed 二态) 真值表，证明 5 个 term tab 两两不交集；剩余 (status='muted' AND human_reviewed=true) 等组合走"全部"兜底 |
| Phase 13 | O1 N5 死代码清理 | 待实施 | 第六轮审计 O1：删除 P0-2 第一段 CASE 的最高优先级 `WHEN ai_approved=true` 分支，避免下次审计误以为它在工作；保留三分支（approved=1 / approved=0+muted / ELSE kept） |
| Phase 13 | O2 用户手动 mute 与 backlog mute 区分 | 待实施 | 第六轮审计 O2：第一段 CASE 第三分支加 `EXISTS (SELECT 1 FROM slang_revisions WHERE action='backlog_review:mute')` 子查询，剩余的"approved=0 + muted + 无 backlog mute revision"行落 ELSE `'kept'`，避免 AI 否决统计虚高 |
| Phase 13 | O5 `upsert_ai_approved_term` 内部统一写新字段 | 待实施 | 第六轮审计 O5：`ai_meta` 拼装处直接写 `ai_review_source='daily'` + `ai_review_decision='approved'`，调用方不需要传——与 `ai_approved=true` / `ai_reviewed_at` 同源，避免未来新增调用方漏写 |
| Phase 13 | O6 手工新增 approved 在 store 层补 human_reviewed | 待实施 | 第六轮审计 O6：`create_term(source='manual')` 内部 `meta_json` 同步写 `human_reviewed=true` / `reviewed_at` / `reviewed_by`，避免每个调用点（创建 + 编辑）都补 |
| Phase 13 | P1-1 反向重申 prompt（双 reviewer） | **暂搁置（2026-05-17）** | 数据反证当前不需要：本轮 308 条决策 LLM 通过率 33%、否决率 4%，瓶颈是阈值精度不是 LLM 一致性；反向重申"脱离上下文能解释→否决"会反伤 46 条 `is_public_meme=true` 合理公网梗；`backlog_review.is_public_meme` 字段已隐含一次反向自检；触发再做的条件：出现"明显错误的高置信通过"（如把普通词判成 approved 0.85） |
| Phase 13 | P1-2 群内 UGC 自验代替 web_search 默认 | **已落地（2026-05-17）** | `MessageLog.query_term_hits(group_id, terms, limit)` 按 term/alias `LIKE` 全文索回该群历史命中行；`backlog_reviewer._collect_context` 优先 term-hits，<2 条退化到最近群聊；`SlangSettings.backlog_local_evidence_count: int = 5`（0 表示禁用）；前端 settings drawer 加"群内取证条数"输入项；本轮线上证据：`is_public_meme=true` 占比 5/200 → 31/150（6 倍），`approved=true` 占比 5% → 49% |
| Phase 13 | P2-1 jieba 分词辅助（不做 reference corpus） | 待评估 | jieba 仅辅助分词（单 token 才考虑通用词剔除），真正的"已知词义"参考库用项目内 `human_reviewed=true AND ai_approved=false` 的人工驳回库；冷启动期效果有限（需 ~200 条积累） |
| Phase 13 | P2-2 频次阶梯重判 | **已落地（2026-05-17）** | `[3, 8, 30, 100]` 阈值 + `meta.last_inference_count`；store.py 新增 `_BACKLOG_THRESHOLD_GATE_SQL` 用 `json_extract` SQL 过滤；`count/list_backlog_candidates` 加 `gated_by_threshold` 参数；`SlangSettings.backlog_threshold_gating_enabled: bool = True`（一键关）；reviewer 仅在 approved/rejected/streak_mute 三条定谈分支写 stamp，kept 不写避免误锁；线上证据：candidate 池 ~800 → 阶梯门后 290 实际可审 |
| Phase 13 | 手动 AI 清池连续清空（2026-05-17） | **已落地** | `run_backlog_review_continuous` 去掉总 timeout，改为 `for _ in range(max_batches=200)` + 每批 `asyncio.wait_for(per_batch_timeout_s=600s)` 兜底防卡死；旧 `timeout_s=510` 是从 tick job 抄来的有误用；`/slang/ai-review/run` 后台任务现可清空任意规模池子 |
| Phase 13 | `backlog_auto_approve_min_confidence` 阈值实测调整 | 已操作 0.82→0.70（2026-05-17，DB 直改） | 本轮 308 条 LLM 通过 102 条、其中 conf ≥ 0.82 仅 29 条、conf 0.7-0.82 区间 43 条被卡 kept；样本含"圆神""老祖""凹暴击""杰专""蓝色矮子色图"等明显合理黑话；下一轮观察 0.70 是否产生"明显错误高置信通过"，若有则需 P1-1 反向重申，若无则维持 |
| Phase 13 | toolbar 待清池分桶 + 语义漂移 tab 加回 | 已落地（2026-05-17） | "待清池"分两色子计数：绿色"可审核"= `eligible_backlog_count`（usage_count >= 3 的可审子集，AI 清池跑完会清零给闭环感），灰色"采集中"= 总待清池 - 可审核（usage_count < 3 + 观察中 + drift）；toolbar 5 tabs：待清池 / 语义漂移 / 已批准 / 已否决 / 全部；解决"点完还剩几百条"的心理落差 |

## 后续维护预案（非当前实施项）

这部分只作为未来重构参考，不再算当前待办：

- `upsert_result.py` / result dataclass：把 `upsert_candidate()`、`upsert_ai_approved_term()` 等兼容返回改成结构化结果
- `store_terms.py`：term CRUD / collision / alias 归并
- `store_runs.py`：run / meta / 状态机
- `prompt_adapter.py`：prompt block / lookup formatting

等下一轮人工评审后，再决定是否拆分。

## 风险跟踪

| 风险 | 等级 | 状态 | 缓解 |
| --- | --- | --- | --- |
| stale run 回收误伤正在执行的 review | 高 | 已验证 | 仅回收超阈值 `started_at` 且 `kind=daily_ai_review` 的 `running` 记录；已有 stale recovery 回归覆盖 |
| 修复 `last_daily_ai_review_date` 后同一天重复执行 | 中 | 已缓解 | 成功后才写当天日期；旧脏状态需同时验证当天成功 run 才会挡住重试 |
| daily review 日志与 task 口径仍可再细化 | 低 | 已归档 | 已拆分 `slang_review` 入口并补 start/finish/skip/latency；usage/cost 作为后续观测增强 |
| 长历史一口气喂给 extractor 导致 review 空跑 | 中 | 已缓解 | daily review 改为只取最近一个受控窗口，避免超时和空跑 |
| pending 队列长期堆积 | 中 | 已缓解 | review 现在会按群限量消费 pending，明确否决会转成 muted |
| collision 改动影响现有 candidate 提升语义 | 中 | 已缓解 | 通过 store 层定向回归覆盖 alias 碰撞与 pending promote |
| settings 缓存引入脏读 | 中 | 已缓解 | settings cache 按 DB path 缓存，`save_settings()` 会刷新缓存并让 admin 保存立即可见 |
| timeout 回收误伤真实运行中的后台任务 | 中 | 已缓解 | 仅回收当前 `kind` 的 `running` run，并保留 `abandoned` 原因字段 |
| 语义阈值重复复核仍烧模型 | 中 | 已缓解 | pending meta 写入 `last_semantic_inference_count`，调用层先短路 |
| prompt block 误注入非直命中词条 | 中 | 已缓解 | `build_prompt_block()` 只取当前上下文 direct hits |
| 手动全量复核后 pending 仍显示“未处理” | 中 | 已缓解 | 手动全量复核穿透 pending 阈值；Admin pending 行显示 semantic meta 状态 |
| Docker bind mount 下 WAL 句柄被 unlink 后造成视图分裂 | 高 | 已缓解 | 黑话库切到 DELETE journal；已验证无 `slang.db-wal (deleted)` fd，宿主 SQLite 能看到最新 run |
| candidate 复核结果被重复全量复核覆盖 | 高 | 已缓解 | `_review_existing_candidates()` 默认只取 `candidate_ai_unreviewed`；已复核项计入 `candidate_skipped_reviewed` |
| AI 通过率偏低/标准偏窄 | 中 | 待人工确认 | 不靠降阈值硬拉高；先用 Phase 9 队列把 rejected/observe/failed 分清，再抽样调 prompt 和准入边界 |
| `confidence` 二义性导致普通词进观察不足 | 高 | 已修复 | candidate review prompt 改为显式三态决策；代码按 `decision_confidence` 归档 reject，新增低黑话概率但高拒绝把握回归 |
| 批量 LLM 响应缺项导致失败项堆积 | 中 | 已修复 | 批量响应接受 decision-only；缺项时自动 fallback 单条复核；真正失败项回到待 AI 复核，不再形成独立队列 |
| 同义改写被误判为语义漂移 | 高 | 已修复 | drift 创建改为专用语义门控；`same_meaning/unclear` 不开单，历史 open drift 可回放关闭 |
| drift gate LLM 不可用导致真漂移暂不开单 | 中 | 已接受 | 这是有意 fail closed；后续通过 Admin 回放或再次观察补开，避免误报优先 |
| pending key 索引与 pending 主表不同步 | 中 | 已缓解 | `init()` 每次 backfill；常规 pending 写/删/晋升路径同步维护；Admin debug 直写后 rebuild 并提交 |
| 无 `message_id` 缓冲仍可能在异常退出时丢最近 observation | 中 | 已接受 | 只影响命中 observation，term 不丢；保留 2 秒/30 条 flush 与 shutdown flush |
| stoplist alias 拦截影响人工新增词条 | 中 | 已接受 | 这是“永不学习词”的预期语义；需要恢复时先从 stoplist 移除，再人工新增或等待重新学习 |
| daily review 长任务被 90 秒超时反复重开 | 高 | 已修复 | tick 不再硬取消 daily review；后台任务运行中直接跳过下一轮 tick，stale recovery 仅处理真正遗留的 running run |
| 手动全量复核拖慢日常复核 | 高 | 已修复 | `review_all_pending` 从 `review_candidates` 中拆出，日常只审达到阈值的 pending；人工全量才穿透阈值 |
| pending 语义复核搜索成本无收益 | 中 | 已修复 | pending semantic review 不再调用 web search；公网搜索仍保留在新抽取候选的辅助准入路径 |
| 并发复核打爆 LLM 或导致数据库写竞争 | 中 | 已缓解 | 只并发 LLM 语义判断，落库仍按结果顺序串行；并发数固定为 3，先不做可配置 |
| Phase 13 历史 meta 迁移误标污染 1075 条 candidate | 高 | 待落地 | 迁移脚本必须在备份 DB dry-run 输出三类（approved / rejected / kept）数量，对照预期（DB 实测当前 backlog approved=43 / rejected=3 / kept=409；daily approved=50）匹配后再上生产；CASE 直读源字段不依赖 status；六轮审计已识别 N1-N7 + O1-O8 共 28 项缺陷并落档 |
| Phase 13 LIKE 单格式让一类写入永远漏过过滤 | 高 | 待落地 | Python `json.dumps` 输出带空格、SQLite `json_set` 输出紧凑，必须双格式同时写；新 helper 与迁移 NOT LIKE 已对齐既有 `_ai_review_sql_condition` 范式 |
| Phase 13 backlog approved 43 条被 P0-4 迁移误打 human_reviewed | 高 | 待落地 | P0-4 迁移 NOT LIKE 同时排除 `ai_review_decision='approved'` 和 `ai_approved=true`，先 dry-run 对照预期 ≈ 158 - 43 = 115 条再上 |
| Phase 13 用户手动 mute 与 backlog mute 混入 AI 否决 tab | 中 | 待落地 | 第六轮审计 O2：第一段 CASE 第三分支加 `EXISTS (revision action='backlog_review:mute')` 子查询区分，剩余人工 mute 落 ELSE `'kept'` |
| Phase 13 N5 高优先级分支死代码 | 中 | 待落地 | 第六轮审计 O1：第一段 WHERE `NOT LIKE ai_reviewed_at` 已排除所有含 `ai_approved=true` 的行，N5 永远不可达；删除以避免下次审计误以为它在工作 |
| Phase 13 P1-1 集成点死引用 backlog_reviewer `_assess` | 中 | 待落地 | 第六轮审计 O3：backlog_reviewer 没有 `_assess` 方法，文档死引用；先抽 helper 再集成反向重申 prompt |
| Phase 13 反向重申 prompt 误判"群里有特殊用法的普通词" | 中 | 待落地 | P1-1 仅在置信度 > 0.7 时否决；边界保留观察期不直接 mute，避免"超舟"等真黑话被误降级 |
| Phase 13 P1-1 在当前数据下会反伤 `is_public_meme=true` 合理梗 | 中 | 已识别（2026-05-17） | 数据反证：本轮 46 条 `is_public_meme=true` 是合理公网梗（圆神、凹暴击等），脱离上下文也能解释 → 反向重申会把它们都打到否决；P1-1 暂搁置；触发再做的条件：出现"明显错误的高置信通过" |
| Phase 13 自动通过阈值精度高于 LLM 自评精度 | 中 | 已识别（2026-05-17） | LLM 给的 confidence 是粗粒度自评（精度 ~0.05-0.1），0.82 这条线把 LLM 通过的"圆神""老祖""杰专"等切到 0.7-0.8 落 kept；阈值已下调到 0.70；下一轮观察是否引入噪声，若有再上调到 0.75 |
| Phase 13 kept 路径误打 `last_inference_count` 锁住次轮重审 | 高 | 已修复（2026-05-17） | 之前 P2-2 把 stamp 写在 meta_patch 顶部所有路径都打，本轮 146 条 LLM 通过但 conf<0.82 的词条被锁死；修复：仅 approved/rejected/streak_mute 三条定谈分支写；线上同步释放了被错锁的 146 条 kept 词条 meta |
| Phase 13 手动 AI 清池被 510s 总 timeout 截断 | 高 | 已修复（2026-05-17） | 旧 `timeout_s=510` 抄自 tick job 但手动触发不该套；改为无总上限 + per-batch 600s wait_for 兜底；测试覆盖 max_batches=200 安全帽；线上验证 301 条池子可一次清空 |
| Phase 13 用户在设置里关闭 search 后 LLM 信号严重不足 | 高 | 已缓解（2026-05-17 P1-2 落地） | 用户主动关 `backlog_review_search_enabled` 是产品决策；P1-2 改 `_collect_context` 用群内历史命中替代 search，验证 `is_public_meme=true` 占比从 5/200 跳到 31/150（6 倍），`approved=true` 从 5% 跳到 49%；search 仍可在 settings 重开作为补充 |
| Phase 13 jieba 通用词剔除误杀网络黑话 | 中 | 待落地 | "摆烂""内卷""超舟"已在 jieba 默认词典；P2-1 改为 jieba 仅辅助分词，真正的 reference corpus 用项目内人工驳回库（需 ~200 条积累），冷启动期不强行启用 |
| Phase 13 kept streak 误 mute 上下文不足的真黑话 | 低 | 待落地 | P0-3 streak 触发 mute 时写 `reason='backlog_kept_streak_exceeded'` + `record_revision=True` + `meta.backlog_kept_history` 最多 3 条；事后翻案路径明确 |

## 下一轮待观测（2026-05-17 阈值实验）

`backlog_auto_approve_min_confidence` 已下调到 0.70，状态切到等用户触发下一轮 AI 清池后看实测结果。观测项：

| 信号 | 期待值 | 决策路径 |
| --- | --- | --- |
| **decision=approved 占比** | 3% → ~25% | 不及预期（<15%）则继续下调到 0.65；超过 30% 且包含明显误判则上调到 0.75 |
| **明显错误的高置信通过** | 0 条 | 出现 ≥3 条则启用 P1-1 反向重申给真黑话二次验证（条件：脱离上下文 LLM 仍能解释 → 不算群内黑话） |
| **`is_public_meme=true` 通过率** | ≥ 60% | 维持 P1-2 不再做调整 |
| **`is_public_meme=false` 通过率** | 30~50% | < 20% 说明群内取证仍不足，考虑提升 `backlog_local_evidence_count` 到 8 或重开 search |
| **kept 词条次轮重审命中** | ≥ 80%（kept 不再写 stamp 后下一轮应都能再审） | 验证 kept 路径阶梯门 bug 修复有效 |
| **池子清空所需时间** | 一次清空 290 条 ≈ 20 分钟 | 超过 30 分钟说明 LLM 调用慢或 batch_size 50 过大，考虑降到 30 |

判断 P1-1 必要性的硬指标：

- 触发启用：明显误判通过 ≥ 3/100，或 `approved=true is_public_meme=true` 通过率超过 90%（说明对公网梗误纵）
- 维持搁置：通过率从 3% 拉到 20-30%、误判 < 1%、`is_public_meme=true/false` 比例自然 — 当前数据走向支持搁置

## Phase 1 设计边界

Phase 1 只处理 daily review 状态机与观测，不处理：

- term / alias collision
- on_message 热路径缓存
- 短词匹配规则
- `_pick_source_row()` source attribution

Phase 1 的目标是让我们先把“今天到底有没有跑 daily review、为什么没跑、是不是卡住了”这件事讲清楚。

## 验证计划

当前阶段最小验证命令：

```bash
source ./scripts/dev/env.sh
uv run pytest tests/test_slang_semantic_reviewer.py tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py -q
uv run pytest tests/test_slang_drift_reviewer.py tests/test_slang_store.py tests/test_admin_api.py tests/test_client.py tests/test_config_loader.py -q
uv run ruff check services/slang/drift_reviewer.py services/slang/store.py services/llm/client.py kernel/config.py plugins/slang/plugin.py admin/routes/api/slang.py admin/routes/api/providers.py tests/test_slang_drift_reviewer.py tests/test_slang_store.py tests/test_admin_api.py tests/test_client.py tests/test_config_loader.py
uv run pytest tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py tests/test_slang_semantic_reviewer.py -q
uv run ruff check services/slang plugins/slang admin/routes/api/slang.py tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py
```

## 更新日志

- 2026-05-17：Phase 13 进阶项落地与阈值实测，**P1-1 反向重申经数据决断后搁置**：
  - **P1-2 群内 UGC 取证落地**：`MessageLog.query_term_hits(group_id, terms, limit)` 按 term/alias `LIKE` 全文索回该群历史命中行；`backlog_reviewer._collect_context` 优先调 term-hits，<2 条退化到最近群聊取风格；`SlangSettings.backlog_local_evidence_count: int = 5`（0 禁用）；`backlog_review_search_enabled` 是用户产品决策（已设 false），P1-2 不强制开 search。**线上效果**：`is_public_meme=true` 占比 5/200 → 31/150（6 倍），LLM `approved=true` 占比 5% → 49%（10 倍）。
  - **P2-2 频次阶梯重判落地**：`[3, 8, 30, 100]` 阈值 + `meta.last_inference_count`；store.py 新增 `_BACKLOG_THRESHOLD_GATE_SQL` 用 `json_extract(meta_json, '$.last_inference_count')` SQL 过滤；`count/list_backlog_candidates` 加 `gated_by_threshold` 参数；`SlangSettings.backlog_threshold_gating_enabled: bool = True`；reviewer 仅在 approved/rejected/streak_mute 三条**定谈**分支写 stamp，kept 不写避免误锁。**线上效果**：candidate 池 ~800 → 阶梯门后 290 实际可审。
  - **手动清池循环修复**：`run_backlog_review_continuous` 旧 `timeout_s=510` 抄自 tick job（`_TICK_JOB_TIMEOUT_S * 0.85`），手动触发不该套这个限制；本轮线上证据：301 条池子跑 11 分 30 秒 deadline、processed=150、completed=False；改为 `for _ in range(max_batches=200)` + 每批 `asyncio.wait_for(per_batch_timeout_s=600s)` 兜底防卡死，能一次清空任意规模池子。
  - **kept 路径阶梯门 bug 修复**：之前 P2-2 把 `last_inference_count` stamp 写在 meta_patch 顶部所有路径都打，导致本轮 146 条 LLM `approved=true` 但 conf<0.82 的词条全部被锁住下一轮也不能再审；修复：仅 approved / rejected / streak_mute 三条定谈分支写 stamp；同步释放 DB 中被错锁的 146 条 kept 词条 meta。
  - **toolbar 待清池分桶 + 语义漂移 tab 加回**：`SlangQueueToolbar.vue` 5 tabs（待清池 / 语义漂移 / 已批准 / 已否决 / 全部）；"待清池"分两色子计数：绿色"可审核"= `eligible_backlog_count`（usage_count >= 3 子集，AI 清池跑完会清零给闭环感），灰色"采集中"= 总待清池 - 可审核（usage_count<3 + 观察中 + drift）；解决"点完还剩几百条"的心理落差。
  - **`backlog_auto_approve_min_confidence` 0.82 → 0.70**（DB 直改）：本轮 308 条决策证据 LLM `approved=true` 102 条但 conf 集中 0.5-0.8，0.82 砍掉 43 条 0.7-0.82 区间合理通过（含"圆神""老祖""凹暴击""杰专""蓝色矮子色图"等明显合理黑话）；LLM 自评置信度精度 ~0.05-0.1，0.82 这条线纯属阈值精度高于 LLM 自评精度。下一轮观察 0.70 是否产生"明显错误高置信通过"。
  - **268 条历史 backlog rejected 词条手动恢复**：`status='muted' AND ai_review_decision='rejected' AND ai_review_source='backlog'` 全部回 `candidate`，meta 清掉 `ai_review_decision/source/at`、`backlog_review`、`backlog_kept_streak/history`、`last_inference_count`；每条写 `slang_term_revisions` action=`manual_revert` 留痕。
  - **P1-1 反向重申搁置（数据决断）**：① 当前 LLM 通过率 33%、否决率 4%、`is_public_meme=true` 46 条，瓶颈是阈值不是 LLM 一致性；② 反向重申"脱离上下文能解释→否决"会反伤 `is_public_meme=true` 的合理公网梗（脱离上下文也能解释，这是优点不是缺点）；③ `backlog_review.is_public_meme` 字段已隐含一次反向自检；④ 真正适用场景是"同一个 LLM 对同个词矛盾判断"，当前数据看到的是保守一致中等置信，不是矛盾。何时改主意：等 0.70 阈值跑完，若出现"明显错误的高置信通过"（如把"扫码"判成 approved 0.85）才需要反向重申给真黑话二次验证。
  - 验证：`uv run pytest tests/test_slang_backlog_reviewer.py tests/test_slang_plugin.py tests/test_slang_store.py -q`（29 passed），`uv run ruff check services/slang/store.py services/slang/types.py services/slang/backlog_reviewer.py services/memory/message_log.py admin/routes/api/slang.py plugins/slang/plugin.py`（all checks passed），`vue-tsc --noEmit` 与 `npm run build` 通过，Docker bot 已重建启动。
- 2026-05-16：Phase 13 方案收口（六轮审计完成，待 PR 实施）：黑话 backlog reviewer 治理与 AI review 契约重构方案 [docs/slang-governance-research-2026-05-16.md](slang-governance-research-2026-05-16.md) 经"自审 2 轮 + 外部审计 2 轮（gpt 5 项 + deepseek 8 项）+ 修订后再审计 2 轮（claude N1-N7 + O1-O8 共 15 项）"共六轮审计，定稿四项 P0：① P0-1 backlog reviewer 加 `min_usage_count` 频次门槛（默认 3，预期把 998 候选降到 ~200 进 backlog 复核池）；② P0-2 用 `ai_reviewed_at` + `ai_review_source` + `ai_review_decision` 三字段独立表达"AI 审过/来源/结论"，废弃方案 A 的 `ai_approved=true/false` 双义；新增 `_ai_reviewed_sql_condition` / `_ai_approved_sql_condition` / `_ai_rejected_sql_condition` / `_ai_kept_sql_condition` 四个双格式 LIKE helper；历史 backlog/daily meta 一次性迁移（CASE 直读源字段、第二段 WHERE 用 `NOT LIKE ai_review_source`，备份 DB dry-run 后上生产）；③ P0-3 `meta.backlog_kept_streak` 累加，连续 ≥ 2 自动 mute 并改写 `ai_review_decision='rejected'`，写 `record_revision=True` + `backlog_kept_history` 最多 3 条便于翻案；④ P0-4 前端 7 tab 重排（待审核 / AI 审核 / 待观察 / AI 否决 / 已批准 / 语义漂移 / 全部）+ 砍 scopeFilter（9 处 vue 改动），admin summary 新增 4 个 count，"AI 否决"tab 排除 human_reviewed=true 避免混入人审驳回项。Day 1 = P0-2 契约 + 迁移；Day 2 = P0-1 / P0-3 / P0-4。第六轮独立审计（claude）发现并已落档的 8 项 net-new：O1（致命）N5 高优先级 CASE 分支在当前 WHERE 下永远不可达，建议删除；O2（重要）"AI 否决"tab 会混入用户手动 mute 的 backlog kept candidate，CASE 第三分支加 `EXISTS(action='backlog_review:mute')` 子查询区分；O3（重要）P1-1 集成点死引用 backlog_reviewer 不存在的 `_assess`，需先抽 helper；O4-O8 实施级歧义已逐项给出落实建议（returned-to-candidate 孤儿、`upsert_ai_approved_term` 内部统一写新字段、`create_term(source='manual')` store 层补 human_reviewed、5 tab 互斥真值表证明、前端 `isAiApproved` 改读 `ai_review_decision`）。P1-1 / P1-2 / P2-1 / P2-2 暂入"待评估"，P0 落地后看效果决定。无代码改动；本次只更新 [docs/slang-governance-research-2026-05-16.md](slang-governance-research-2026-05-16.md)（六轮审计章节落档）和本文档（Phase 13 实施清单 / 决策记录 / 风险跟踪）。
- 2026-05-13：Phase 12 性能收口：确认 2026-05-13 02:10 的 `daily_ai_review` 成功 run 耗时约 21 分钟，主因是 63 条 pending 三段语义复核串行执行，且 tick 侧 90 秒硬超时造成大量 abandoned 重跑。现 daily tick 不再硬取消复核；`review_candidates` 与 `review_all_pending` 解耦，日常复核不再全量穿透 pending 阈值，Admin 手动“全量 AI 复核”显式传 `review_all_pending=true`；pending 语义复核改为 3 并发，并跳过不参与判定的 web search。验证目标：黑话 plugin/admin 回归、ruff、前端 type/build。
- 2026-05-12：黑话抽取迁到 ConversationArchive cursor：手动抽取使用 `slang_manual_extract`，daily review 使用 `slang_daily_review`，二者进度互不推进；archive 不可用、读取失败或 cursor 需要人工重扫时自动退回旧最近窗口。热路径 message match、Prompt 注入、Admin 最近显示和黑话业务语义不变。
- 2026-05-12：补充与 ConversationArchive 的关系：黑话抽取规划迁到归档底座 scanner 和 `conversation_message_refs`，但当前 Phase 11 correctness 状态不变；stoplist、pending key、semantic/drift review 等业务语义仍由 `SlangStore` 管理。
- 2026-05-12：Phase 11 correctness 收口：`SlangPlugin` 对 `message_id=None` 的命中缓冲使用内部 event key，避免多条无 ID 消息在 2 秒窗口内覆盖；`SlangStore` 新增 `slang_pending_candidate_keys` 辅助表并在 init backfill / pending 写删晋升路径同步维护，pending merge 改为按 normalized key 预过滤后再 Python 精确确认；stoplist 扩展到 term + aliases，extractor、candidate upsert、AI approved upsert、manual create 均拒绝 alias 命中 stoplist 的新入库。验证：`uv run pytest tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py tests/test_slang_semantic_reviewer.py -q`（132 passed），`uv run ruff check services/slang plugins/slang admin/routes/api/slang.py tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py` 通过。
- 2026-05-11：落地语义漂移误报修复：新增专用 `SlangDriftReviewer`，`_maybe_create_drift_review()` 不再靠 n-gram 低相似度直接开 drift，只有高置信 `real_drift` 才创建/刷新 open drift；`same_meaning` / `unclear` fail closed，`alias_candidate` 只走 alias 合并。新增 `/api/admin/slang/drift/replay` 可 dry-run/apply 回放历史 open drift，Admin drift 卡片展示语义门控 verdict/reason。验证：`uv run pytest tests/test_slang_store.py tests/test_slang_drift_reviewer.py tests/test_slang_plugin.py tests/test_admin_api.py tests/test_client.py tests/test_config_loader.py tests/test_slang_semantic_reviewer.py -q`（208 passed），`ruff check` 通过，`vue-tsc --noEmit` 与 `npm run build` 通过。
- 2026-05-11：落地黑话 AI 审核自我审计后的低风险归档方案：candidate 复核结果现在稳定分成“AI 建议通过 / AI 未通过 / 观察不足 / 复核失败 / 待 AI 复核”。高置信 `approved=false` 会转 `muted + ai_rejected`，保留可恢复审计链，不再算人工未处理；`no_info`/低置信进入观察不足，失败进入可重试失败队列。Admin 首屏指标与队列同步调整，并保留“重跑已审”处理历史 kept 候选。聚焦验证目标：后端 slang 回归、Admin slang API、前端 `vue-tsc` 与 build、live 全量/重跑复核日志中的 `candidate_rejected/observe/failed` 分布。
- 2026-05-11：修正恢复候选回归口径：`return_ai_reviewed_term_to_candidate()` 会清空 `ai_rejected` / `candidate_review_state` 等旧复核痕迹，恢复后词条重新计入 `candidate_ai_unreviewed`。相应测试断言已校正，确认恢复动作不会再被人工队列误识别为已处理项。验证：`uv run pytest tests/test_slang_semantic_reviewer.py tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py -q`、`uv run ruff check tests/test_slang_store.py tests/test_admin_api.py`、`cd admin/frontend && ./node_modules/.bin/vue-tsc --noEmit`。
- 2026-05-11：修复 live 重跑后仍有大量“观察不足”的根因：模型把 `confidence` 当成“候选像黑话的概率”，例如普通问句会输出 `approved=false, confidence=0.1`，旧代码因此误判为“低置信，继续观察”。现 candidate 复核协议新增 `decision=approve/reject/observe` 与 `decision_confidence`，代码只用 `decision_confidence` 判断是否归档 AI 未通过；旧模型无 `decision` 时继续兼容原逻辑。新增回归覆盖“低黑话概率但高拒绝把握”转 muted，以及 observe 不算未复核。验证：`uv run pytest tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py tests/test_slang_semantic_reviewer.py -q`（105 passed），`uv run ruff check ...`、`vue-tsc --noEmit`、`npm run build` 均通过。
- 2026-05-11：继续收口 live 复核失败队列：发现批量 LLM 响应可能只返回 `decision` 而没有旧字段 `approved`，或漏掉部分 index，旧解析器会把这些项标成 `candidate_review_state=failed`。现已兼容 decision-only 响应，并在批量缺项时自动走单条 `_assess()` fallback；新增 `candidate_review_filter=review_failed` 只重试失败项，Admin 增加“重试失败”按钮。live 定向重试 `run_1cc2d62512ed54a3` 成功：32 条失败项重试后 23 条 AI 未通过、9 条观察、0 条失败。随后普通全量 `run_ebb732d91b086c9e` 消费新抽取的 2 条未审候选。最终 live 对账：`candidate_unreviewed_count=0`、`candidate_review_failed_count=0`、`candidate_review_rejected_count=88`、`candidate_review_kept_count=8`、`candidate_review_approved_count=14`、`pending_count=2`；清理 smoke 测试残留 pending 3 条。验证：聚焦回归 108 passed、ruff 通过、`vue-tsc --noEmit` 通过、`npm run build` 通过，Docker bot 已重建重启。
- 2026-05-11：补齐 candidate AI 复核可见性与稳定性：`SlangStore.summary()` 新增 candidate 总数、未复核、建议通过、未通过等拆分字段；Admin 黑话页新增“AI 建议通过 / AI 未通过 / 未复核”队列，并在候选卡片显示 AI 复核理由。发现重复全量复核会让建议通过数从 14/82 波动到 8/83，已修为普通全量复核默认只审未复核 candidate，已复核项计入 `candidate_skipped_reviewed`，只有显式 `rerun_reviewed_candidates=true` 才覆盖旧结果。最终 live 对账为 `candidate_total_count=83`、`candidate_review_approved_count=7`、`candidate_review_rejected_count=76`、`candidate_unreviewed_count=0`。
- 2026-05-11：修复“全量 AI 复核后 Web 仍像未处理”的真实根因链：手动 `review_candidates=true` 过去只扫 existing candidates，pending 仍受阈值 gate 限制；现改为手动全量时同步 `review_all_pending=true`，会穿透 pending 阈值并写入 `semantic_force_review`。Admin 观察中候选增加“全量已审：信息不足 / AI 复核失败”等标签，避免 pending 留存被误读成 AI 没操作。线上已触发 `run_5eb202bb5b78069c`，返回并落盘 `pending_reviewed=3 semantic_reviewed=3 semantic_no_info=3 review_all_pending=true`。
- 2026-05-11：修复黑话库可见性分裂：排查发现 bot 进程曾持有 `/storage/slang.db-wal (deleted)` / `slang.db-shm (deleted)` fd，导致 Admin API 能看到新 run，而宿主 `sqlite3 storage/slang.db` 一度看不到。`SlangStore.init()` 现在将黑话库 journal 切到 `DELETE` 并保持 `synchronous=FULL`；重建重启后验证 bot 只持有 `/app/storage/slang.db`，`PRAGMA journal_mode` 为 `delete`，`PRAGMA quick_check` 为 `ok`。
- 2026-05-11：追踪文档最终收口：`冲突返回结构替代 str | None` 明确归档为下一轮维护重构项，不再算当前实施待办；`stale run 回收误伤正在执行的 review` 根据现有 stale recovery / tick timeout 回归测试改为已验证。当前黑话模块只剩人工验收与线上观测回放。
- 2026-05-11：完成黑话库损坏修复与防复发闭环：`SlangStore.init()` 现在在坏库上抛 `SlangDatabaseCorruptError`，`connect_sqlite()` 失败时会关闭半开的 aiosqlite worker，`SlangPlugin` / Admin API / health 都改为结构化降级；新增 `scripts/dev/slang_db_repair.py` 负责备份、`.recover`、验证和替换，`scripts/dev/slang_semantic_smoke.py` 去掉直连 live SQLite 路径并把 admin 请求超时调到 60 秒。已实际恢复 `storage/slang.db`，保留坏库备份与 corrupt 副本。聚焦验证通过：`uv run pytest tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py tests/test_slang_semantic_reviewer.py tests/test_slang_db_integrity.py -q`（102 passed），`uv run ruff check services/storage/sqlite.py services/slang/store.py plugins/slang/plugin.py services/health.py admin/routes/api/slang.py scripts/dev/slang_db_repair.py scripts/dev/slang_semantic_smoke.py tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py tests/test_slang_semantic_reviewer.py tests/test_slang_db_integrity.py` 通过，`uv run python scripts/dev/slang_semantic_smoke.py` 通过。
- 2026-05-11：补充 MaiBot 黑话系统代码逻辑对照表 `docs/audits/maibot-jargon-code-logic-map-2026-05-11.md`，把消息采样、候选提取、阈值推断、解释和后台能力拆成可查表，后续重构以此为参考。
- 2026-05-11：落地黑话语义复核 + 解释方案：新增 `SlangSemanticReviewer` 三段推断（上下文/裸义/对比），`pending` 达到 [2,4,8,12,24,60,100] 阈值后再复核；`last_semantic_inference_count` 按整型阈值短路重复模型烧耗；`build_prompt_block()` 改为只注入当前上下文 direct hits 的 approved 词条；补齐 reviewer/store/plugin/admin 回归测试。聚焦验证通过：`uv run pytest tests/test_slang_semantic_reviewer.py tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py -q`（94 passed），`uv run ruff check services/slang/semantic_reviewer.py services/slang/daily_reviewer.py services/slang/store.py plugins/slang/plugin.py services/slang/__init__.py tests/test_slang_semantic_reviewer.py tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py` 通过。
- 2026-05-11：收尾黑话维护性欠账：`PluginContext` 现已显式声明 `slang_store` / `slang_plugin`，避免黑话插件继续依赖隐式动态挂载；`SlangStore` 拆分保留为后续维护预案，并从当前实施项移出，等下一轮人工评审后再决定是否拆分 `store_terms.py` / `store_runs.py` / `prompt_adapter.py`。
- 2026-05-10：创建黑话模块修复跟踪文档；根据专项审计与修复方案，确定先落 Phase 1（daily review 状态机、stale recovery、关键日志），再进入 Phase 2（term/alias collision）。
- 2026-05-10：Phase 1 最小实现落地：`SlangStore` 新增 stale extraction run 查询与 `abandon_extraction_run()`；`SlangPlugin.run_daily_ai_review_if_due()` 改为分钟数比较时间、入口先回收 stale `daily_ai_review` run、成功后才写 `last_daily_ai_review_date`，并补 `last_daily_ai_review_started_at/status/run_id` meta 与 start/success/fail/stale_recovered 关键日志。聚焦验证通过：`uv run pytest tests/test_slang_plugin.py tests/test_slang_store.py -q`（20 passed），`uv run ruff check plugins/slang services/slang tests/test_slang_plugin.py tests/test_slang_store.py` 通过。
- 2026-05-10：Phase 1 收尾完成：tick 内将 daily review 与 manual extract timeout 拆分；timeout 后按 `kind` 回收 `running` run 为 `abandoned`；manual extract 补 start / finished 结构化日志；admin API 与 SlangView 对 `abandoned` 状态兼容。聚焦验证通过：`uv run pytest tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py -q`（77 passed），`uv run ruff check services/slang/store.py plugins/slang/plugin.py tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py` 通过。
- 2026-05-10：Phase 2 核心修复落地：`find_existing()` 改为 term+alias normalize 精确碰撞；`upsert_candidate()` / `upsert_ai_approved_term()` / `create_term()` / `scan_global_candidates()` 统一走 alias-aware collision；`_promote_pending_candidate()` 与 existing 命中路径会并回 pending aliases、observations 与 meta，并消除幽灵 pending。新增 store/admin 回归覆盖 alias 撞 term / alias、pending promote 命中 existing、`abandoned` run 列表可见。
- 2026-05-10：SlangView 可见性调整：最近抽取记录从折叠“高级概览”中挪到首屏可见面板，避免人工验收时找不到 run 列表；`vue-tsc --noEmit` 通过。
- 2026-05-10：Phase 3 完成：`SlangStore` 增加 settings / group term snapshot 内存缓存与统一失效；所有词条写路径补齐快照失效；`record_hits()` 支持批量命中写入，`SlangPlugin.on_message()` 改为批量记录。回归通过：`uv run pytest tests/test_slang_store.py -q`（14 passed），`uv run pytest tests/test_slang_plugin.py -q`（12 passed），`uv run pytest tests/test_admin_api.py -q`（54 passed），`uv run ruff check services/slang/store.py tests/test_slang_store.py plugins/slang/plugin.py` 通过。
- 2026-05-10：Phase 4 完成：短词/alias 匹配切到共享 helper；`find_matching_terms()`、`get_injectable_terms()`、daily review 的 observed_count 都改用保守匹配；`_pick_source_row()` 改为优先 exact evidence line，再保守回退。新增回归覆盖短 ASCII 词边界、source row 精确命中。聚焦验证通过：`uv run pytest tests/test_slang_store.py tests/test_slang_plugin.py tests/test_admin_api.py -q`（82 passed），`uv run ruff check services/slang/quality.py services/slang/store.py services/slang/daily_reviewer.py services/llm/client.py plugins/slang/plugin.py kernel/config.py tests/test_slang_store.py tests/test_slang_plugin.py tests/test_client.py tests/test_config_loader.py` 通过。
- 2026-05-10：Phase 5 最小实现落地：`LLMClient` 新增 `slang_review` task wrapper；daily review 复核入口改为优先 `slang_review`、其次 `slang`、最后 `_call` 的统一调用链，避免 review 继续直接绑主聊天裸调用。随后补齐 `slang_extract` / `slang_review` 的结构化运行日志与 skip reason，后续仍需补 task 级 usage / cost 观测。
- 2026-05-10：补齐 daily review 的 pending 复核闭环：daily review 现在会对每个群按限额复核 `slang_pending_candidates`，AI 明确否决会把 pending 转成 muted，避免待处理长期堆积；同时把 web search 从唯一准入条件降级为辅助证据，群内重复证据足够时也可以直接通过。聚焦验证通过：`uv run pytest tests/test_slang_plugin.py -q`、`uv run pytest tests/test_slang_store.py tests/test_admin_api.py -q`，`uv run ruff check services/slang/daily_reviewer.py services/slang/store.py plugins/slang/plugin.py tests/test_slang_plugin.py tests/test_slang_store.py tests/test_admin_api.py`。
- 2026-05-10：补齐 legacy daily review 脏状态回退：若 `last_daily_ai_review_date` 已写成当天但当天并无成功 `daily_ai_review` run，则不再把它当作已执行；新增 `has_successful_extraction_run()` 与回归测试，确保旧的“日期已写入但 run 仍 running”状态可以自动重试。聚焦验证通过：`uv run pytest tests/test_slang_plugin.py tests/test_slang_store.py -q`（29 passed），`uv run ruff check services/slang/store.py plugins/slang/plugin.py tests/test_slang_plugin.py` 通过。
- 2026-05-10：补齐 daily review 长历史抽取问题：`daily_ai_review` 不再把最近 200 条用户消息一次性喂给 extractor，而是只保留最近一个受控窗口（窗口上限受 `extraction_batch_limit` 约束）再做候选抽取；这样既避免空跑，也避免多轮分批把 35 秒 timeout 吃满。新增回归覆盖“长历史不切窗会空跑，切到受控窗口后可正常 AI 通过”。聚焦验证通过：`uv run pytest tests/test_slang_plugin.py tests/test_slang_store.py -q`（30 passed），`uv run ruff check services/slang/daily_reviewer.py tests/test_slang_plugin.py` 通过。
