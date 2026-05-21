# MaiBot 黑话系统代码逻辑表

审计日期：2026-05-11

范围：本机 `/Users/kragcola/MaiM-with-u/MaiBot` 的黑话相关代码。本文只记录代码事实和 Omubot 可移植边界，不记录未验证猜测。它补充而不是替代 `docs/maibot-slang-improvement-proposal.md`，后者更偏改进设想。

## 核心结论

MaiBot 的黑话链路不是单独一个 `jargon_miner.py`，而是：

`message_recorder` 定时窗口采样 -> `expression_learner` 同时抽表达与黑话候选 -> `jargon_miner` 聚合证据与按阈值推断 -> `jargon_explainer` 回复前解释已知黑话 -> `webui/jargon_routes` 提供轻量管理。

Omubot 不应整套照搬。真正值得借的是“上下文推断 vs 词条裸推断 vs 对比判定”和“按出现次数分阶段推断”。MaiBot 的表结构、后台、作用域和治理能力弱于 Omubot 现有 Slang v3。

## 代码入口表

| 模块 | 文件 | 主要职责 | 关键代码 | 备注 |
|---|---|---|---|---|
| 消息窗口调度 | `/Users/kragcola/MaiM-with-u/MaiBot/src/bw_learner/message_recorder.py` | 按 chat 维护提取窗口，达到时间与消息量阈值后触发学习 | `min_messages_for_extraction = 30`、`min_extraction_interval = 60`，见 `message_recorder.py:37-45`；触发 `expression_learner.learn_and_store()`，见 `message_recorder.py:119-145` | 只触发表达学习；黑话候选通过 expression learner 转发 |
| 候选提取 | `/Users/kragcola/MaiM-with-u/MaiBot/src/bw_learner/expression_learner.py` | 一个 LLM prompt 同时抽“表达方式”和“黑话候选” | prompt 要求 JSON 数组，黑话字段为 `content/source_id`，见 `expression_learner.py:35-86` | 源行号是关键证据定位机制 |
| 候选转发 | 同上 | 校验 source_id，过滤 bot 自己、SELF、机器人名，再构造上下文 | `build_context_paragraph(messages, line_index)`，见 `expression_learner.py:559-621` | 上下文是中心消息前 3 条 + 后 3 条 |
| 候选入库与计数 | `/Users/kragcola/MaiM-with-u/MaiBot/src/bw_learner/jargon_miner.py` | 按 `content` 合并 raw_context；存在则 count+1，不存在则创建 | 见 `jargon_miner.py:451-599` | 没有 alias/collision 体系；content 精确相同才合并 |
| 语义推断 | 同上 | 在阈值点执行三段 LLM 推断，判断是否真黑话 | 见 `jargon_miner.py:142-178` 和 `jargon_miner.py:241-443` | 这是最值得移植的部分 |
| 回复前解释 | `/Users/kragcola/MaiM-with-u/MaiBot/src/bw_learner/jargon_explainer.py` | 在上下文中匹配已知 jargon，查释义，再用 LLM 概括为回复参考 | 见 `jargon_explainer.py:52-151` 和 `jargon_explainer.py:153-240` | 只解释有 meaning 的记录 |
| 后台管理 | `/Users/kragcola/MaiM-with-u/MaiBot/src/webui/jargon_routes.py` | list/detail/create/update/delete/batch status/stats | 见 `jargon_routes.py:211-268`、`jargon_routes.py:322-368`、`jargon_routes.py:392-526` | 管理能力轻，Omubot 已经更完整 |

## 端到端流程表

| 步骤 | 触发条件 | 输入 | 处理 | 输出/副作用 | Omubot 借鉴 |
|---|---|---|---|---|---|
| 1. 采样窗口 | 同一 chat 距离上次提取 >= 60s，且窗口内消息 >= 30 条 | 原始消息流 | 取 `last_extraction_time` 到当前时间的消息，按时间排序 | 异步触发 `ExpressionLearner.learn_and_store()` | 可借“窗口阈值”，但 Omubot 已有 `extract_interval_minutes` 和批量 recent query，不必照搬 |
| 2. LLM 候选抽取 | expression learning 开启 | 带行号的匿名聊天文本 | 单 prompt 同时抽表达方式和黑话候选 | `expressions` 与 `jargon_entries` | 可借 `source_id` 强约束；Omubot 当前 `evidence` 可继续保留，但要强化来源定位 |
| 3. 缓存召回 | 每次 learn 后 | JargonMiner LRU cache 里的最近候选 | 在本批消息里再扫一遍缓存词 | 补充遗漏候选 | 可借“已知候选二次召回”；Omubot 已有 term snapshot，可对 pending 做轻量召回 |
| 4. 候选清洗 | 转发到 miner 前 | `content/source_id/messages` | 过滤 SELF、机器人名、无效 source_id、bot 自己消息、空上下文 | `{"content": term, "raw_content": [context]}` | 必须借，尤其是 bot 自己消息过滤和上下文证据 |
| 5. 候选聚合 | miner 收到 entries | content + raw_content list | 按 content 去重；已有记录 count+1 并合并 raw_content；否则创建 | `Jargon.count`、`raw_content`、`chat_id` 更新 | Omubot 已有 term/alias/pending/revision，不能降级成 content-only |
| 6. 阈值推断 | count 达到下个阈值且大于 `last_inference_count` | Jargon record | 异步 `create_task(_infer_meaning_by_id)` | 后台更新 meaning/is_jargon/last_inference_count | 借阈值节奏，但 Omubot 要有 run log、timeout 和失败状态 |
| 7. 上下文推断 | 阈值任务启动 | `content` + raw_content list + 可选 previous meaning | LLM 根据上下文推断含义；若 no_info 则本轮停止 | `inference1.meaning` | 直接借；这是解决“AI 审核完全没操作 pending”的关键方向 |
| 8. 词条裸推断 | 上下文推断成功 | 仅 `content` | LLM 推断常识/公网/字面含义 | `inference2.meaning` | 直接借，但要走 Omubot `slang_review` profile |
| 9. 语义比较 | 两次推断都成功 | inference1 + inference2 | LLM 判断二者是否相同或类似 | `is_similar` | 直接借，`not similar` 才是“群内语义偏移”的强信号 |
| 10. 状态落库 | 比较完成 | `is_similar` | `is_jargon = not is_similar`；真黑话用上下文释义，否则清空 meaning | 更新 Jargon | Omubot 不能直接清空；应进入 `approved/candidate/muted/drift` 等可审计状态 |
| 11. 回复前解释 | 生成回复前 | 当前上下文消息 | 匹配有 meaning 的 jargon，查精确释义，LLM 概括 | 一段黑话解释文本 | Omubot 已有 prompt block 和 `slang_lookup`，可增补“命中才解释”的小块 |

## 数据模型对照

| MaiBot 字段 | 代码位置 | 含义 | Omubot 当前对应 | 迁移建议 |
|---|---|---|---|---|
| `content` | `database_model.py:342` | 主词条文本 | `slang_terms.term` + `term_key` | 保留 Omubot 结构 |
| `raw_content` | `database_model.py:343` | JSON list，候选出现上下文 | `slang_observations.context/raw_text`，pending evidence | 不新增同名字段，继续用 observations |
| `meaning` | `database_model.py:344` | 推断出的释义 | `slang_terms.meaning` | 保留 |
| `chat_id` | `database_model.py:345` | 可为 JSON list：`[[chat_id,count]]` | `scope/group_id`，global 另有字段 | 不照搬 JSON chat_id，Omubot 的作用域更清楚 |
| `is_global` | `database_model.py:346` | 是否全局 | `scope = global` | 保留 Omubot 作用域 |
| `count` | `database_model.py:347` | 出现次数和阈值推断依据 | `usage_count`、pending `count` | 复用，不新增重复计数字段 |
| `is_jargon` | `database_model.py:348` | `None` 未判定，`True` 是黑话，`False` 非黑话 | `status` + `meta.ai_reviewed` + drift queue | 不照搬布尔状态；Omubot 需要更细状态 |
| `last_inference_count` | `database_model.py:349` | 上次推断时的 count，防重复 | 可放入 `meta.last_semantic_inference_count` | 建议新增 meta 字段，不必迁移 schema |
| `is_complete` | `database_model.py:350` | count>=100 后停止推断 | 可放入 `meta.semantic_inference_complete` | 建议 meta 化 |
| `inference_with_context` / `inference_content_only` | `database_model.py:351-352` | 预留存两次推断 JSON | 当前无直接字段 | 建议存入 revision/meta，方便 admin 复查 |

## 三段推断逻辑表

| 阶段 | Prompt/函数 | 输入 | 成功条件 | 失败行为 | 状态更新 |
|---|---|---|---|---|---|
| 是否需要推断 | `_should_infer_meaning()` | `count`、`last_inference_count`、`is_complete` | count 达到 `[2,4,8,12,24,60,100]` 的下一个阈值 | 不触发 | 无 |
| 上下文推断 | `jargon_inference_with_context_prompt` | 词条、raw_context、可选上次 meaning | JSON dict 且 `no_info=false` 且 meaning 非空 | 设置 `last_inference_count = count` 后返回，等待下个阈值 | 防止同阈值重复烧模型 |
| 词条裸推断 | `jargon_inference_content_only_prompt` | 仅词条 | JSON dict 且 meaning 可取 | 返回，不更新判定 | 无 |
| 推断比较 | `jargon_compare_inference_prompt` | 上下文推断结果 + 裸推断结果 | JSON dict，读取 `is_similar` | 返回，不更新判定 | 无 |
| 判定落库 | `infer_meaning()` | `is_similar` | `is_jargon = not is_similar` | 外层 catch 只记录错误 | 真黑话写上下文 meaning；非黑话清空 meaning；更新 `last_inference_count`；count>=100 标记 complete |

## 质量守卫表

| 守卫点 | 代码位置 | 做了什么 | 盲区 |
|---|---|---|---|
| 单字黑话不进 LRU cache | `jargon_miner.py:25-45`、`jargon_miner.py:205-223` | 单个汉字、字母、数字不加入缓存 | 新建 Jargon 时没有同等拦截，主要只是缓存保护 |
| 机器人名过滤 | `learner_utils.py:258-275`、`expression_learner.py:586-589` | 候选含 bot nickname/alias 则跳过 | `name in target` 可能误伤包含昵称子串的正常词 |
| bot 自己消息过滤 | `learner_utils.py:313-352`、`expression_learner.py:603-607` | 通过平台账号判断消息是否来自 bot | 依赖平台账号配置正确 |
| 上下文构造 | `learner_utils.py:278-310` | 中心消息前 3 条 + 后 3 条，保留图片信息 | 上下文窗口固定，不考虑话题边界 |
| 候选数量熔断 | `expression_learner.py:156-164` | 表达方式 >20 或黑话候选 >30 时放弃对应结果 | 防爆量有效，但会丢掉真实候选 |
| 搜索/匹配边界 | `jargon_explainer.py:125-136` | 中文直接 regex 子串，英文数字加 `\b` | 中文短词容易误匹配 |

## 检索与解释逻辑表

| 功能 | 代码 | 查询范围 | 匹配方式 | 输出 |
|---|---|---|---|---|
| 上下文内命中已知黑话 | `JargonExplainer.match_jargon_from_messages()` | 有 meaning 的 Jargon；all_global 开启时只查 global，否则 Python 层过滤当前 chat/global | 中文子串 regex；英文数字 word boundary；跳过 bot 自名 | `[{content}]` |
| 查词条释义 | `search_jargon()` | 根据 `all_global_jargon` 和 chat_id 过滤；只返回有 meaning 的记录 | content 精确或 fuzzy contains，大小写可选 | `[{content, meaning}]` |
| 回复参考摘要 | `JargonExplainer.explain_jargon()` | 对命中词逐个精确查释义 | LLM 概括 explanations；失败则返回原始解释列表 | 一段自然语言解释 |
| 概念检索增强 | `retrieve_concepts_with_jargon()` | 先精确后模糊搜索 | 命中后拼出“你了解以下词语可能的含义” | 供记忆/回复上下文使用 |

## 后台能力表

| 路由 | 代码 | 能力 | Omubot 对照 |
|---|---|---|---|
| `GET /jargon/list` | `jargon_routes.py:211-268` | 搜索、chat_id、is_jargon、is_global、分页、按 count 排序 | Omubot `GET /api/admin/slang/terms` 已更强 |
| `GET /jargon/stats/summary` | `jargon_routes.py:322-368` | total、confirmed、pending、global、complete、top chats | Omubot summary/stats 已覆盖更多运行态 |
| `POST /jargon/` | `jargon_routes.py:392-419` | 手动创建，重复检查为 content + chat_id | Omubot create_term 有 alias/scope/status/confidence/revision |
| `PATCH /jargon/{id}` | `jargon_routes.py:428-450` | 增量改 content/raw/meaning/chat/global/is_jargon | Omubot update_term 有 revision |
| `DELETE /jargon/{id}` | `jargon_routes.py:459-476` | 删除单条 | Omubot 支持状态治理和批量观察删除 |
| `POST /jargon/batch/set-jargon` | `jargon_routes.py:509-526` | 批量设置 `is_jargon` 布尔值 | Omubot 应继续用 `approved/muted/expired/candidate` |

## 不能照搬的点

| MaiBot 做法 | 风险 | Omubot 应如何处理 |
|---|---|---|
| `asyncio.create_task()` 后没有 run 级追踪 | 推断失败只在日志里，后台无法看到 stuck/failed | 必须接入 `slang_extraction_runs` 或新增 semantic inference run meta |
| `content` 精确相同才合并 | `pjsk`、`PJSK`、`project sekai` 这类别名仍容易重复 | 必须保留 Omubot term/alias collision helper |
| `is_jargon` 布尔状态 | 无法区分候选、AI 通过、人工通过、静音、漂移 | 保留 Omubot 状态机 |
| 非黑话时直接清空 meaning | 丢失为什么否决的依据 | Omubot 应写 revision/meta，必要时转 muted |
| 中文匹配直接子串 | 短词误命中 | 继续使用 Omubot `matches_slang_candidate()` 的保守匹配，并补短词测试 |
| 只在代码日志输出推断结果 | admin 无法复核推断过程 | Omubot 要把 context inference、content-only inference、compare reason 暴露到详情或 revision |
| 表达学习和黑话抽取绑在一个 prompt | 单次失败会同时影响两类学习，prompt 输出结构也更脆 | Omubot 继续保持 slang extractor/reviewer 独立 task |

## Omubot 移植检查表

| 编号 | 要借的逻辑 | Omubot 落点 | 必须测试 |
|---|---|---|---|
| M1 | 阈值式语义复核：`2/4/8/12/24/60/100` 或配置化版本 | `services/slang/daily_reviewer.py` 或新增 `semantic_reviewer.py` | pending count 达阈值才调用 LLM；同阈值不会重复调用 |
| M2 | 上下文推断 JSON：`meaning/no_info` | `LLMClient._call_slang_review()` | no_info 时保留 pending，不批准也不静音 |
| M3 | 词条裸推断 JSON：`meaning` | 同上 | 普通词裸推断和上下文推断相似时不批准 |
| M4 | compare JSON：`is_similar/reason` | 同上 | `is_similar=true` -> fail closed 或 muted；`false` -> 可 candidate/approved |
| M5 | 上次推断结果参与高阈值重推 | `SlangTerm.meta` / `SlangPendingCandidate.meta` | count 24/60/100 时带旧释义；其余阈值不带 |
| M6 | 回复前命中才解释 | `SlangPlugin.on_pre_prompt()` 或 `slang_lookup` 增强 | 未命中词不注入解释；已命中 approved 词优先解释 |
| M7 | 缓存召回已知候选 | `SlangStore._load_group_term_snapshot()` / pending snapshot | pending/approved 命中时批量 record_hits，不每条 DB 乱扫 |
| M8 | 推断过程可见 | Admin slang detail / revisions / runs | 能看到 context inference、content-only inference、compare reason、latency/status |

## 下一步建议

先不要改主 store schema。第一步新增一个 Omubot 原生 `SlangSemanticAssessment` 与 semantic reviewer，把 MaiBot 三段推断结果写进 `meta` 和 revision；跑通后再决定是否把字段升级成正式列。这样能保留当前可回滚、可审计的 Slang v3，不会把 MaiBot 的弱治理结构搬回来。
