# 概率插话锚定缺失 — 话题终止后表情触发回旧话题

> 状态：2026-05-30 日志排查 + 根因定位完成，**仅观察结论，未改代码**
> 触发：群 993065015，17:33，用户连发两个动画表情，bot 既没回应表情、也没沉默，而是把此前已停下的"鱼鱼烧/肯德基"旧话题捞起来续上
> 报告人：运行时巡检（扒容器内 `/app/storage/logs/bot_2026-05-30.log`）

## 1. 现象

话题自然终止后，群里出现两条纯表情消息。bot 被概率门触发开口，但回复内容与"刚到的表情"无关，而是接续了上一段已经结束的具体话题。用户观感：bot 像是"没在看现在发生什么，硬把前面没聊完的话题翻出来续"。

## 2. 复现时间线（群 993065015）

| 时间 | 事件 | 关键字段 |
|------|------|----------|
| 17:33:27 | Religion(3286443160) 发动画表情① | `Message 1391104898` `[image:summary=[动画表情]]` |
| 17:33:29 | Religion 发动画表情② | `Message 1845267186` 同上 |
| 17:33:34 | 表情① 计分 | `scheduler_rws score=0.309` → `prob skip (threshold=0.31 ... mode=none)` |
| 17:33:36 | 表情② 计分顶过阈值 | `scheduler_rws score=0.618` → **`prob fire (threshold=0.62 ... mode=none)`** |
| 17:33:36 | 进入 chat | `chat \| session=group_993065015 ... text=''`（**触发正文为空**） |
| 17:33:37 | thinker 首轮结构化解析失败 | `thinker structured parse failed, retrying once \| raw=鱼鱼烧！` |
| 17:33:38 | 启发式兜底 | `thinker_parse_heuristic \| action=reply thought='看到你说肯德基欸—— 我也想吃那个鱼鱼烧'` |
| 17:33:42–47 | 三段发出 | `'鱼鱼烧！\n是那种小鱼形状的鲷鱼烧吗？\n好想吃喔~' \| sticker=none segments=3`，**无 `[CQ:reply]` 锚定** |

此前群内残留的话题是一段"吃什么 / 肯德基 / 还没想好有什么推荐"的觅食闲聊，已经停下（`context memory source` query 里仍能看到 `还没想好呢~ 有什么推荐的吗？` 等残句）。

## 3. 排除项

- **不是弱回复 P0（closing）。** 全天 grep `closing|收尾|light_reply|light_kind` 在 `bot_2026-05-30.log` **零命中**；本次 `mode=none`，`trigger=None`。closing 通道未参与。
- **不是 @ / directed_followup / correction / video。** fire 日志明确 `mode=none`，走的是纯概率插话分支。
- **不是单次偶发。** 这是结构性缺陷（见 §4），任何"话题停顿后一条低语义消息恰好把概率顶过阈值"都会复现。

## 4. 根因 — 三因素叠加

### R1（主因）概率插话路径不告诉 LLM "在回哪条"
`services/scheduler.py:1434` 概率 fire 调用 `self._llm.chat(..., user_content="", trigger=None)`：
- 没有 transient `user_content`（正文空 → 日志 `text=''`）
- `trigger is None`，因此 `services/scheduler.py:1331` 的 `add_pending_trigger(reason=...)` 不执行，pending 里没有"该回应哪条/为什么回"的锚点

结果：`services/llm/client.py` chat 把"当前要回应什么"完全交给 timeline，LLM 自己挑一条"最值得接"的。`@mention` 才会注入 `[CQ:reply]` 前缀（`scheduler.py:1352`），概率插话没有任何等价锚定，所以回复也没有引用框。

### R2 表情在 timeline 里是低权重文本，被旧话题盖过
`services/memory/timeline.py:305 get_recent_text` 把表情渲染为 `«动画表情: <描述>»`。当"当前消息"是纯表情（语义弱），而上文残留一段信息量更大的未收尾话题（鱼鱼烧）时，模型自然锚定到旧文本——这正是"抓上最近上下文回复"的来源。conversation_text 取 `recent_text(last_n=3) + pending_text`（`client.py:3841`），把刚停的旧话题和新表情混在一起喂给 thinker，没有"新消息优先"的权重。

### R3 顶过阈值的信号与被回应的对象解耦
把 RWS 顶过 0.5 的是"表情② + consecutive_skip 压力"（`compute_rws` 的 `skip_pressure` 项，`services/scheduler_rws/rws.py:91`），但 chat 阶段回应的对象是旧话题。"为什么开口"和"回什么"用了两条互不相关的信号链，中间没有"把触发消息绑定为回应目标"的桥。

**一句话：概率插话缺一个"把回应锚定到触发消息"的机制；纯表情/极低语义消息触发时，这个缺陷最刺眼——既不回应表情本身，又把上一段没说完的话题硬接上。**

## 5. 证据清单（D4）

- 触发与决策：`scheduler_rws score=0.309/0.618`、`prob skip/fire ... mode=none`（log 行 15042–15074）
- 空正文入口：`chat | session=group_993065015 ... text=''`（log 行 15101 附近）
- 跑偏产出：`thinker_parse_heuristic ... thought='...鱼鱼烧'`、最终 `'鱼鱼烧！...'` segments=3（log 行 15247–15268）
- closing 未触发：`grep -nE "closing|light_reply|light_kind" bot_2026-05-30.log` → 空
- 代码位点：`scheduler.py:1434`（空 user_content）、`scheduler.py:1331`（trigger=None 跳过 pending reason）、`scheduler.py:1352`（仅 @mention 注入 CQ:reply）、`timeline.py:305`（表情低权重渲染）、`client.py:3841`（recent+pending 混合）

## 6. 修复方向（未实施，待定）

- **方向 A — 给概率插话注入轻量 anchor。** prob-fire 时把"当前要回应的是 user 刚发的这条（可能是表情）"显式喂给 thinker/LLM（复用 `add_pending_trigger` 或 anchor 机制），治"回错对象"。
- **方向 B — 提高纯表情/极低语义消息单独顶过阈值的门槛。** 让这类消息更倾向"跟着已有话题或沉默"，而不是凭一个表情就开口，治"不该开口"。

A 治锚定、B 治触发，二者正交，可分别评估。**需用户确认是否进入修复，以及优先 A / B / 两者。**

## 7. 风险与开放问题

- thinker 首轮结构化解析失败（`raw=鱼鱼烧！`）单独看也是一处隐患：解析失败 → 启发式兜底，兜底更容易顺着上下文跑偏。是否与 R1 叠加放大，待修复时一并核。
- 本报告仅覆盖一次实例；若要量化"概率插话回旧话题"的发生率，需在 fire 路径加一条"触发消息 vs 回应锚定"对照日志后再统计。

---

## 第二部分：群聊多话题并行的理论与同类项目调研

> 状态：2026-05-30 文献 + 同类项目调研完成。四条战线（会话分析/社会语言学、计算对话解缠、多方对话 addressee/response、生产系统与前沿 LLM），引用均经 web 检索核实标题/年份/出处。
> 动机：§1–§7 复现的"回旧话题"只是表象的一个切片。真实问题不是"串行话题终止位置判断错"，而是**群聊里同时存在多个并行话题块（multi-floor），消息在屏幕上线性排列却逻辑上属于不同话题线**。机器人用"上一句=待回应对象"的朴素模型，会系统性接错话题块。本部分调研成熟领域如何理解与处理这个问题。

## 8. 问题重定义：从"话题终止位置"到"多 floor 并行"

经典轮替模型（Sacks/Schegloff/Jefferson 1974）默认**一个 floor、一次一人说话**。群聊（尤其文本异步 CMC）系统性违反这个假设。Herring (1999, *Interactional Coherence in CMC*, JCMC 4(4)) 命名了核心现象：**disrupted turn adjacency（相邻位错乱）**——相关消息被无关消息隔开，N 不接 N−1。Elsner & Charniak (2008) 在 IRC 实测：**平均同时有 2.75 个会话并行，36% 的消息带 @提及**（人类主动用点名来降低解缠难度）。

所以 §1 那次"鱼鱼烧"不是孤例，而是一个通用缺陷的实例：**bot 缺少"把消息归入正确并行话题块 + 判断自己在该块里是什么角色 + 据此决定是否开口"的三段式能力**。下面的调研全部围绕这三段。

## 9. 会话分析与社会语言学（理论层：定义信号）

| 理论（作者, 年份, 出处） | 核心概念 | 对 bot 的信号/启示 |
|---|---|---|
| **Egbert 1997**（*Schisming*, RLSI 30(1):1–51） | 对话**协作裂变**成多个并行对话；裂变诱发轮(SIT)有三属性 | 话题块切分应看**三信号联合偏移**：① recipiency（被 @/reply 的对象集合变化）② sequence（上一问答对是否已闭合）③ topic（与当前块语义骤降）。且裂变需"有人 uptake"才成立——可用"是否有人接了这条脱节消息"判断新块是否真生成 |
| **Goffman 1981**（*Forms of Talk*, "Footing"） | participation framework：production(animator/author/principal) + reception(addressed / unaddressed-ratified / overhearer / bystander) | bot 在群里默认是 ratified participant，但每条消息它可能是 addressed / unaddressed / overhearer 中任一种。**只有 addressed 才有强回应义务**；"我是 overhearer → 理解但不发声"是合法且常见状态。转述场景下 principal≠animator，勿把转述内容当转述者立场 |
| **Edelsky 1981**（*Who's Got the Floor?*, Lang.in Society 10(3):383–421） | F1 单一 floor vs F2 协作 floor（多人同时刷屏共建，重叠是常态非违规） | 群聊常是 **F2 + 多个 F1 并存**。bot 应先分类：F2（高频+短消息+表情/附和/多发送者）里沉默/轻附和才像真人，别试图"持有 floor"；F1（问答对+较长消息+稳定 2–3 人）里若被寻址应给实质回应。**§1 那次正是 F2 刷表情场景，bot 却用 F1 的"实质接话"模式响应——错配** |
| **Lerner 2003**（*Selecting Next Speaker*, Lang.in Society 32(2):177–201） | SSJ 选下一说话人规则 (a)当前选定>(b)自选>(c)当前继续；显式 vs tacit addressing | 文本群聊无 gaze，**显式寻址权重最高**（@bot/点名/reply-to bot → 必答）。最难是 **tacit addressing**（没点名但"这问题该我答"）——需一个"我是否是合格/预期回答者"判断。可编码为决策层级：显式寻址→必答；隐性→概率自选；都无→沉默 |
| **Sacks/Schegloff 1973 + Sacks Lectures** | topic shift（硬转换，旧话题闭合）vs shading（渐变滑入）；**skip-connecting / tying**（跳过紧邻句，用指称/重复接回几轮前的话） | **skip-connecting 是并行话题在线性流里得以维持的核心机制**，也是切块的最强证据：一条消息显式跳接到 K 轮前（reply 引用/重述原词/回应早前未答的问题）→ 它属于那条老话题线，不属于紧邻的当前 floor。shading 可顺接，shift 表示旧块已闭合勿接 |
| **Goffman + SSJ 汇合** | unaddressed/overhearer **无回应义务**；沉默是结构常态；noticeable absence | **这是 bot"何时不该开口"的理论根基**：默认状态 = 沉默合法，回应才需触发条件。只有 bot 是 addressed 时，它的沉默才会被注意到（=失礼）。回应紧迫度应与"我的沉默会不会成为 noticeable absence"挂钩，而非对每条消息都找理由回 |

**理论层一句话**：把 bot 决策拆成三串联判断——①这条属于哪个并行块（skip-connecting/tying 链 + Egbert 三信号 + 时间窗）→ ②我在该块是什么接收角色（Goffman + Lerner 寻址强度）→ ③该开口吗（仅 addressed/高隐性寻址有义务；F2 默认沉默，概率随 floor+寻址调制）。这三步直接对应现有的 reply-to 链、@ 解析、scheduler 调度。

## 10. 计算对话解缠（方法层：把混合流切成线程）

**任务定义**：把单条消息流里交织的多个对话拆开，判定每条消息属于哪个 thread。两种等价表述——**reply-to 重建**（预测每条回复哪条，连通分量=会话）vs **thread clustering**（直接聚簇）。主流共识（Kummerfeld 起）：标 reply-to，聚类作副产品。

**经典数据集 / 方法演进**：
- Elsner & Charniak (2008, ACL；2010, *Comput. Linguistics* 36(3)) — 首个 IRC 解缠语料 + pairwise 分类→图聚类范式。**时间间隔是最强单特征**。
- Kummerfeld et al. (2019, ACL, *A Large-Scale Corpus for Conversation Disentanglement*) — Ubuntu IRC 77,563 条人工 reply 图，DSTC8 Track2 基础，至今金标准。
- 神经路线：Mehri & Carenini (2017, IJCNLP, 直接预测 reply 关系)；Jiang et al. (2018, NAACL, Siamese 层次网络 + **时间窗限定把 O(M²)→O(kM)**，对实时友好)；Liu et al. (2020, IJCAI, 首个 end-to-end transition-based 在线解缠)；Yu & Joty (2020, EMNLP, 指针网络指 parent)；**Ma et al. (2022, ACL, "Struct"，显式建模 speaker property + reference dependency(@谁)，Ubuntu SOTA)** — 直接背书"@提及"信号价值。
- 弱/无监督与冷启动：Chi & Rudnicky (2021, EMNLP, **zero-shot**：零标注 cluster F1≈25，10% 标注即达全量 92%)。
- LLM 时代：Li et al. (2024, TOIS) 与 ROCLING 2025 横评均显示 **LLM 纯 zero-shot 解缠弱于微调小模型**（缺对话语篇结构预训练曝光）；Takada & Mori (2026) 的 iterative-greedy 让 LLM 按序为每条 utterance 分配会话归属，是 LLM 原生路线代表。

**评测指标**（衡量什么——报告时三类都要给，否则被高 VI 误导）：
- 整体切分像不像：**1-1 overlap**（Elsner 08）、**VI / 1−VI**、**NMI**、**Shen-F**（Shen 06）。
- 精确还原：**cluster exact-match F1**（Kummerfeld 19，最严格，常只 40+ 而 VI 已 90+）、link exact-match F1。
- 在线局部：**Local-k**（判"当前消息是否与前 k 条同簇"，对流式友好）。

**给 bot 的可借鉴清单**：
- **轻量实时（首选，零/低训练）**：① 吃群聊**原生强信号**——QQ 的 `@提及`、引用 reply 直接当 parent 边（IRC 没有、QQ 白拿的 ground-truth，质量远高于推断）；② 无显式回复时回退 **time gap + same-speaker + 词/embedding 重叠** 加权打分指向时间窗内最高分前文（Elsner 特征 + Jiang 时间窗），连通分量即 thread；③ 若上 LLM，按 Takada&Mori iterative-greedy，但**别让 LLM 裸做全局解缠**——用廉价信号先给候选/约束，LLM 只做歧义裁决。
- **重型离线（不入实时回路）**：指针网络/层次 BERT（Yu&Joty、DialBERT）、结构图 + easy-first 非时序解码（Ma 22、Li 24，违背在线因果性）——适合离线扒历史日志做话题分析。

## 11. 多方对话 addressee / response（决策层：对谁说、该不该接）

把领域凝练为一句（Gu et al. 2022 IJCAI Survey）：**"Who says What to Whom"**。三个纠缠子任务：disentanglement（哪个线程）/ addressee detection（说给谁）/ response decision（该不该回、回谁）。

- **联合任务奠基**：Ouchi & Tsuboi (2016, EMNLP) 首提 addressee+response 联合任务 + Ubuntu 多方语料；Zhang et al. (2018, AAAI, **SI-RNN**) 角色敏感（sender/addressee/observer 随轮变化），在"回复远距离消息"场景显著优于基线；Le et al. (2019, EMNLP, **W2W**) 补全整 session 缺失的 addressee（贴合真实群聊 addressee 稀疏）。
- **结构感知建模**（动机：序列模型假设线性，但群聊消息会并行——正是本问题）：Hu et al. (2019, IJCAI, **GSN** 图结构编码器，按"谁回谁"拓扑而非顺序编码)；**Gu et al. (2021, ACL, MPC-BERT)** 五个自监督任务统一学 who-what-whom，消融证明 **reply-to recognition 对 addressee 最关键，结构类任务贡献>语义类**；HeterMPC (2022)、GIFT (2023) 把结构信息以低成本注入生成。
- **关键实证（对本问题最直接）**：**Penzo et al. (2024, EMNLP, "Do LLMs Suffer from Multi-Party Hangover?")** — **Response Selection（回哪条）主要靠文本内容；Addressee Recognition（说给谁/谁该接）必须喂结构（谁回复谁的图），只给纯文本最差**；且参与人越多（degree centrality 越高）addressee 准确率越低。
- **何时开口 / 沉默**：**MUCA (2024, arXiv:2401.04883)** 文本群聊 3W 框架（What/When/Who），chime-in 概率 = silence factor（连续沉默轮数↑→倾向插话）+ semantic factor（对话卡住/重复/有未解决问题），阈值≈0.45，且有显式 **Keep Silent** 默认态防过度回复；MM-When2Speak (2025) 把"何时说"做成**三档输出：沉默 / 短反应(backchannel) / 完整回复**——与本仓"四档 ResponseClass"同构；Umair et al. (2024, EMNLP Findings, *LLMs Know What To Say But Not When To Speak*) 证明 **LLM 不擅长判断 within-turn 插话时机**——印证"时机交规则、内容交 LLM"的分工。

**给 bot 的可借鉴清单**：
- **零成本**：把 timeline 喂给 LLM 时，**显式结构化标注 reply-to 边 + @mention（含 @他人）**——这是 addressee 判断收益最高/成本最低的一步，有 Multi-Party Hangover 直接背书。@了别人的消息大概率不是对 bot 说的，应调低回复概率；**reply-to bot 必须等价于 @bot**（多个开源 bot 的已知漏点）。
- **需专门模型**：无 @ 无 reply 时的高精度隐式 addressee（W2W/MPC-BERT）、reply 元数据缺失时的大规模解缠——仅当成为实际痛点才上。

## 12. 生产系统与前沿 LLM 工程（落地层：同类项目怎么做）

**贯穿性结论（改变架构选型）**：Inner Thoughts (Liu et al., **CHI 2025**, arXiv:2501.00383) 实测——群聊里 **95% 发言是 self-selection（对所有人开放），仅 5% 明确点名**；GPT-4o 在 self-selection 下预测"下一个说话人"准确率仅 **0.121 ≈ 随机(0.127)**，点名时才升到 0.697。**结论：「该不该 bot 开口」本质无法从历史消息可靠预测**（人类靠内部动机而非可观察线索）。正确方向 = **强信号走确定性规则（被@/点名/问句指向→必答）；弱信号走"动机/相关性打分 + 概率/阈值"而非硬预测**——与本仓"强/弱回复二分 + 四档 ResponseClass"方向一致。

**同类项目可直接抄的工程清单（均有据可查）**：
| 模式 | 出处 | 内容 |
|---|---|---|
| debounce 3s + 累积 batch 分析 | sgnt.ai (killable LLM responses) | 静默 3s 合并；对 M1 / M1+M2 / 整批分别判意图取最强（不止看最后一条） |
| cancel-token 自毁工作单元 | sgnt.ai | 新消息到来标记 Redis 取消位，生成 run 轮询自毁；是本仓 D2 cancel-path 测试的天然回归点 |
| **版本号丢弃陈旧任务** | basecase.vc/blog/group-chat | 话题飘走后丢弃过期待发回复——**直接解 §1"话题飘走 bot 还回旧的"** |
| 连续发言上限 + 收尾 flag | basecase（MAX=5） | 连续 K 条 AI 消息后强制收尾，防自我刷屏 |
| 群 idle 才主动 + 随机区间 + quiet hours + 连续无应答闭嘴 | AstrBot proactive_chat（开源，QQ） | 群静默 30min 才考虑开口；区间随机；连续 2 次没人理就暂停 |
| Listen→Score→Respond | Corzo (Medium) | 便宜打分器先打 relevance，过阈才进昂贵 LLM，省 token |
| 候选两级过滤（规则筛候选→LLM 在小集选）+ 默认禁连续发言 | AutoGen SelectorGroupChat | `candidate_func` + `selector`；`allow_repeated_speaker=False` |
| 检索记忆判是否发起/回应（relevance+recency+importance 三因子） | Generative Agents (Park 2023, UIST) | "该不该开口"= 一次基于检索记忆的 LLM 判断，非分类器 |
| adjacency-pair 控轮（被点名必答 / 否则 importance 竞争 / 平手随机 / 无人想说原话题继续） | Who Speaks Next?（arXiv:2412.04937） | 把"何时开口"形式化为两条规则，与"强信号确定/弱信号竞争"二分吻合 |

**Inner Thoughts 三旋钮（最值得对标，可直接进 per-group config）**：`system1_prob`（话痨度）、`im_threshold`（开口动机门槛）、`interrupt_threshold`（抢话门槛）；"沉默越久越想说"用 `motivation *= λ^(沉默轮数)`（λ≈1.02）一行实现。8 维启发式可裁剪成 2–3 维（相关性/信息缺口/会否刷屏）做一次轻量 LLM 打分。

**话题分割（判"现在有几个并行话题"）**：经典 TextTiling（相似度低谷=边界）最便宜；对话专用 Xing & Carenini (2021, SIGDIAL，连贯性打分>纯相似度)、DialSTART (2023)；**LLM 时代最实用 = 直接 prompt LLM 把当前窗口消息按话题分簇返回 id+标签**，一次调用同时给出"几个并行话题 + 每条归属"。

**明确不要做**：① 预测"下一个具体说话人"（self-selection 下 ≈ 随机）；② 指望 LLM 判 within-turn 插话时机；③ 纯文本场景上声学 turn-taking/VAP。Character.AI 群聊"何时回复"的官方工程实现**未检索到可靠来源**（多为社区二手讨论），不作采纳。

## 13. 调研 → 本仓的具体映射

把 §9–§12 收敛回 §1–§7 暴露的缺陷，落到本仓现有组件（`GroupTimeline` / `GroupChatScheduler` / `thinker` / RWS / 四档 ResponseClass）：

| 调研结论 | 对应 §1 缺陷 | 本仓落点 |
|---|---|---|
| 多 floor 并行 + skip-connecting，N 不接 N−1（Herring/Sacks） | R1 概率插话无锚定，LLM 自挑旧话题 | 给 prob-fire 注入"当前要回应的是 user 刚发的这条"锚点（已在 §6 方向 A），并把消息归入正确话题块再选回应对象 |
| 结构信号比纯文本更能定位 addressee（Multi-Party Hangover/MPC-BERT） | 喂给 thinker 的是混合纯文本（`recent_text+pending_text`） | timeline 喂 LLM 时**显式标注 reply-to 边 + @关系**；@他人的消息调低回复概率；reply-to bot 等价 @bot |
| F2 协作 floor 里沉默/轻附和才像真人（Edelsky） | R3 表情刷屏(F2)被用 F1 实质接话模式响应 | scheduler 增"F1/F2 floor 分类"，F2 下倾向沉默或弱回复（接本仓四档 ResponseClass） |
| 该不该开口无法硬预测，强信号规则+弱信号动机打分（Inner Thoughts/CHI25） | R2 一个表情顶过阈值就开口 | RWS 当前已是打分+阈值思路，方向对；可补"纯表情/极低语义消息门槛"（§6 方向 B）+ Inner Thoughts 式相关性维度 |
| 版本号丢弃陈旧任务（basecase） | "话题飘走 bot 还回旧的"的通用解 | scheduler 的 `pending_during_generation` / 版本计数，丢弃话题已切换后的过期回复 |
| 只有 addressed 沉默才被注意；unaddressed 沉默是常态（Goffman/SSJ） | bot 倾向"为每条消息找理由回" | 决策默认态 = 沉默合法；回应需触发（寻址/高隐性指向/自选阈值），与弱回复设计一致 |

**注**：本仓已落地的 closing 弱回复（P0）正是 §9 "opening up closings / 对称告别"理论的一个点状实现；本调研说明它只是"多 floor + 角色 + 时机"这张更大图里的一小块——closing 是"旧 floor 闭合"的特例。

## 14. 结论与建议路径（待定，未实施）

**结论**：§1 的"回旧话题"不是修一个判断位置就能根治的局部 bug，而是 bot 缺少**三段式群聊理解**（话题块归属 → 接收角色 → 开口决策）的表征。学术界（解缠 + 多方对话）和生产界（Inner Thoughts/basecase/AstrBot）已就关键取舍形成共识：**时机交确定性规则，对谁/属于哪个话题交结构信号，该不该回交动机打分；放弃预测"下一个具体说话人"**。

**建议分层落地（成本递增，每层独立可验证）**：
1. **L0 零成本立刻可做**：timeline 喂 LLM 时结构化标注 reply-to + @ 边（addressee 收益最高/成本最低，Multi-Party Hangover 背书）；reply-to bot 等价 @bot 自查。
2. **L1 小改造（治 §1 直接症状）**：prob-fire 注入触发消息锚点（方向 A）+ 纯表情/极低语义门槛（方向 B）+ 版本号丢弃陈旧回复。
3. **L2 中等（结构化群聊理解）**：scheduler 增 F1/F2 floor 分类 + 一次 LLM 话题分簇（"现在几个并行话题、每条归属"）；据此选回应对象、调回应概率。
4. **L3 进阶（按需）**：仅当隐式 addressee / 跨线程交叉成为实测痛点，才引入训练型 addressee 判别器或解缠模型；否则用 prompt 结构化已能拿到大部分收益。

**开放问题**：L2 的"LLM 话题分簇"每次群消息都跑成本不低，需评估缓存/增量；F1/F2 分类阈值需用本仓真实群活跃度调参（文献数值均为他人经验值，非本场景验证）。**是否进入 L0–L3 任一层、以及优先级，待用户决策。**

## 15. 改进方案（具体设计，待批准后实施）

> 设计原则（承 §12–§14 共识）：**时机交确定性规则，归属/对谁交结构信号，该不该回交打分；放弃预测下一说话人**。全程**复用既有管线**（`add_pending_trigger` / RWS 特征 / 四档 ResponseClass），不新建平行链路——与 closing P0 同构。下面 P0/P1/P2 与 §14 的 L0/L1/L2 对应，按"成本递增、各自独立可上线、节制同批"组织。

### 15.0 决策骨架：把"开口"拆成确定性层级

当前 `notify` 已是"强信号 bypass（at/followup/correction/closing）+ 弱信号走 RWS 概率"的二分（`scheduler.py:425-642`），方向正确。本方案**不推翻它**，只补两个缺口：① 概率 fire 后缺"锚定到触发消息"（R1）；② 表情/极低语义消息不应与正常消息同权顶阈值（R2/R3）。决策层级（高优先级先短路，复用现有顺序）：

```text
@bot / reply-to-bot / 点名        → 必答（既有 at bypass，确认 reply-to-bot 等价 @）
directed_followup / correction    → 既有 bypass
closing                           → 既有 P0 bypass
─────────────────────────────────  以上为「显式寻址」确定性层，不动
低语义消息(纯表情/单字/语气词)     → P1：进语义门，单独的更高阈值
其余非寻址消息                     → RWS 概率 + P0 锚点
默认                              → 沉默（合法，Goffman/SSJ）
```

### 15.1 P0 — 概率插话锚点（治 R1，对应 L1/方向 A，**最高优先级**）

**问题**：prob fire 时 `_do_chat` 拿到的 `trigger` 多为 `None`（`mode=none`），不调 `add_pending_trigger`，`chat(user_content="")` → LLM 无"回哪条"锚点，自由挑了旧话题。

**方案**：在 `notify` 的 prob-fire 分支（`scheduler.py:626` `if decision:` 内、`self._fire(group_id)` 之前），当 `slot.trigger is None` 时，用本函数已有的 `message_text` / `user_id` / 当前 `event.message_id`（notify 需补传 `message_id` 入参，或从 timeline 取最近一条 pending 的 id）构造一个轻量 trigger：

```python
# prob-fire 命中但无显式 trigger：锚定到刚触发的这条消息
if slot.trigger is None and message_text:
    slot.trigger = TriggerContext(
        reason=f"群里刚有人说「{message_text[:40]}」，你接这条的话题",
        mode="ambient_anchor",      # 新增自由 str mode，additive
        target_message_id=message_id,
        target_user_id=user_id,
    )
```

`_do_chat` 已有的 `if trigger is not None: add_pending_trigger(...)`（`scheduler.py:1331-1339`）会把它写进 pending，`_pending_conversation_text`（`client.py:502`）随即把"该回应这条"喂给 thinker。**复用既有锚点机制，零新管线。** `ambient_anchor` 在 RWS / force_reply 里不享受任何 bypass 加成（它在概率已命中后才构造），仅作"回应目标"用。

**纯表情场景的锚点增强**：当 `message_text` 是表情占位（`«动画表情: …»`），reason 改为"群里刚有人发了个表情（…描述…），你可以回应这个表情或这条消息所在的话题，也可以只回个表情/短一句"——给 LLM "回应表情本身"的合法出口，而非强行接旧话题。

**验证**：① 单测——构造 prob-fire 命中 + trigger=None + message_text=表情，断言 `add_pending_trigger` 被调用且 reason 含锚点；② D2 cancel-path——锚点 trigger 在 `_fire` 失败/取消后不残留污染下条（`slot.trigger` 在 skip 分支已 `=None`，fire 分支需确认 `_do_chat` 末尾清理）；③ 烤群验"回旧话题"是否消失。

**回滚**：删 prob-fire 分支那段 `if slot.trigger is None` 注入即回原行为；`ambient_anchor` mode 是自由 str，additive。

### 15.2 P1 — 低语义消息门槛 + 结构标注（治 R2/R3，对应 L0+L1/方向 B）

分两件，可同批：

**(a) 低语义消息单独阈值（治 R3：表情不该与正常消息同权顶阈值）。**
新增一个轻量分类 `classify_low_signal(message_text) -> bool`（规则层，照 `classify_closing_intent` 的写法）：纯表情占位（`«动画表情…»` / `«图片…»`）、单字、纯语气词（嗯/哦/啊/草/笑死…封闭集）、纯标点/颜文字。命中后**不直接 skip**（仍可能是 F2 里该附和的场景），而是在 RWS 阶段对这类消息施加一个**更高的有效阈值**或一个负向特征项：

- 接入点：`compute_rws` 的 terms（`rws.py:77-94`）加一项 `low_signal`（权重负），由 `RWSFeatures.low_signal: bool` 驱动；`_maybe_compute_rws` 填充该特征。
- 效果：一个表情单独把分顶过 0.5 的概率被压低，但"连续多条表情刷屏（F2 热度高）"或"刚 @ 过 bot 的余温"仍能通过——交给 RWS 既有的 `skip_pressure` / `hawkes` / heat 综合，不一刀切。
- **不做硬过滤**：Goffman/Edelsky——F2 里附和表情是合法社交，硬 skip 会让 bot 显得冷漠。压低而非封死。

**(b) 结构标注（治 R2，零成本，L0，addressee 收益最高）。**
timeline 喂 LLM 时，把 `«动画表情»` 之外的**结构边显式标注**：每条消息若有 reply-to，渲染成 `[回复 @某人「被回的话…」]`；@他人渲染成 `[对 @某人说]`。落点 `kernel/router.py:_render_message`（已解析 reply/at 段，`router.py:571/646`）+ `timeline.get_recent_text`。依据 Multi-Party Hangover：**判 addressee 必须喂结构，纯文本最差**。附带收益：bot 看到"这条 @的是别人"，`ambient_anchor` 锚点能识别"这条不是对我说的"，回应概率自然下调。

**验证**：① `classify_low_signal` 单测（表情/单字/语气词判真，正常句判假，含边界"草（植物）"类）；② RWS `low_signal` 特征单测（同输入下命中项使 score 下降，但 skip_pressure 高时仍可 fire）；③ 渲染单测（reply/at 边出现在 `get_recent_text` 输出）；④ D1 同模式扫描——`«动画表情»`/`«图片»` 占位的产生点（`router.py:660/710`）全部覆盖。

**回滚**：`low_signal` 权重置 0 即旁路；结构标注是 additive 渲染，去掉即回原文本。

### 15.3 P2 — 节制（防回潮，与 P1 同批，对应"节制必须同批"纪律）

P1 让 bot 在 F2 表情场景"可能开口"，必须同批加节制，否则变刷屏机器：

- **复用 closing P0 已上线的冷却**：`slot.last_light_time`（30s）已存在；`ambient_anchor` / 低语义触发的回应也纳入同一冷却，连发表情时 bot 最多 30s 应一次。
- **连续无应答闭嘴**（AstrBot/basecase 模式）：`slot` 加 `consecutive_unanswered`，bot 主动/ambient 回应后若 N 条内无人接（无 @bot、无 reply-to-bot、无话题延续），计数 ++，达阈值（如 2）临时压低该群 ambient 回应概率，直到有人理。
- **版本号丢弃陈旧回复**（basecase 模式，治"话题已飘走 bot 还在回"）：`_do_chat` 生成期间记录 `slot` 的话题快照/pending 版本；发送前若版本已变（话题切走），降级为更短回应或丢弃。本仓已有 `pending_during_generation` 基础设施（`scheduler.py:1349`），扩展即可。

**验证**：D2 cancel-path——`consecutive_unanswered` / 版本号在生成被取消时不污染下条；冷却/计数的 sqlite 或 in-flight 旗标断言。

### 15.4 落地顺序与边界

| 阶段 | 治 | 成本 | 依赖 | 可独立上线 |
| --- | --- | --- | --- | --- |
| P0 锚点 | R1 | 低（复用 add_pending_trigger） | 无 | ✅ 单独上即可消除"回旧话题"主症状 |
| P1(a) 低语义阈值 | R3 | 低（RWS 加一特征） | 无 | ✅ |
| P1(b) 结构标注 | R2 | 极低（渲染层） | 无 | ✅ addressee 收益最高，可最先做 |
| P2 节制 | 防回潮 | 中 | **必须与 P1 同批** | ❌ 不可晚于 P1 |

**不在本方案**（§14 的 L2/L3，按需）：F1/F2 floor 显式分类、LLM 话题分簇、训练型 addressee 判别器/解缠模型——仅当 P0/P1 上线后实测"跨线程接错话题"仍频发才评估。

**建议**：先上 **P1(b) 结构标注 + P0 锚点**（都低成本、正交、共同治 §1 主症状），观察烤群几天；再决定 P1(a)+P2 是否需要。**待用户批准后进入实施（届时出 D3 四列迁移清单 + 测试 + 回滚路径）。**

## 16. 复用审查（D1）：§15 方案与现有设施的去重修正

> 对 §15 做了一次全仓 D1 同模式扫描（services/kernel/plugins），结论：**§15 大幅高估了"新增"，多处与现有设施重复**。下表逐条标注，并给出修正后的方案。原 §15 保留作思路记录，**以本节为准实施**。

### 16.1 审查发现：已有什么

| §15 设想 | 现状判定 | 现有设施（文件:行号） | 它已经做了什么 |
| --- | --- | --- | --- |
| P0 "回应目标锚点" | **已有三层，高度重复** | `add_pending_trigger`（timeline.py:256）、`AddresseeHintDetector`（addressee_hint.py，client.py:1321 装配）、quote anchor（client.py:438-452） | ① `«触发原因…消息ID=mid»` 写进 pending 喂 LLM；② `[当前你在回复：昵称(QQ)]` 注入 plugin_dynamic；③ LLM 输出里的 `[CQ:reply,id]` 抽取/回贴 |
| P0 人格/语义边界锚点 | **已有** | `AnchorReinjector`（anchor_reinjection.py） | 已实现 topic-shift 检测（词重叠<0.2）+ mention 检测，但注入的是**人格**锚点，非回应目标 |
| P1(a) 低语义判定 | **部分有** | `text_preflight.preflight()`（text_preflight.py:38） | 已判纯标点/单字/单 emoji/纯重复；命中**直接 return None 沉默**（client.py:3864） |
| P1(a) RWS 低信号特征 | **不重复，且有空壳可复用** | `RWSFeatures.info_gain`（rws.py:32）是死字段（权重 0、构造处不传） | RWS 特征填充集中在 scheduler.py:1063-1078 一处，易加 |
| P1(b) 结构标注 reply-to | **已有结构，未抹平** | `_render_message`（router.py:672-676）渲染 `[QUOTED_MSG sender_id=… ]…[/QUOTED_MSG]`；merge 加 `«msg:id» 昵称(QQ)` 前缀 | 喂给主 LLM 的正式上下文已带 reply 结构 + speaker/msg-id |
| P1(b) @他人标注 | **部分有，可改进** | router.py:683-685 渲染裸 `@<qq号>` | 保留 @ + QQ 号，但未解析为可读昵称、未做 "@昵称" 归一 |
| P2 冷却 | **已有** | `_LIGHT_COOLDOWN_S=30s`+`last_light_time`（scheduler.py:43,517）、`planner_smooth`+`last_fire_time`（:561） | 轻量回复冷却 + 最小触发间隔 |
| P2 连续 skip 升压 | **已有** | `consecutive_skip` + double/force 阈值（scheduler.py:577-583） | 连续 skip 后阈值翻倍/强制 fire |
| P2 版本号丢弃陈旧回复 | **概念无，但功能等价物已有** | `_EmissionGate`+`_arbiter_b_monitor`（scheduler.py:920-982,1394-1412） | 生成期间轮询新 pending，arbiter 判 interruption，verdict 非 continue 则中止后续分段 |
| P2 连续无应答闭嘴 | **没有** | — | 无 `consecutive_unanswered` 计数器（`closing_done` 语义是"收尾后闭嘴"，不等价） |
| L2 floor/话题解缠 | **基本没有** | `topic_drift.py` 仅"最新 vs 前两条"相似度标量；`floor` 全是误命中（概率下限，非话语权） | 单线程漂移标量，非分段/聚类/解缠 |

### 16.2 根因再校正（基于审查）

审查暴露了一个比 §4 更精确的事实链：

- **R2 校正**：`text_preflight` 命中即沉默——但它的正则**不识别 `«动画表情»`/`«图片»` 占位**（只认裸 emoji/单字/标点）。所以那两个表情**根本没被 preflight 拦**，直接走到 prob fire。"表情低权重被旧话题盖过"的真实机制是：**preflight 漏了占位符 + RWS 无信息量特征**，二者叠加。
- **R1 校正**：`AddresseeHintDetector` 其实**已经在回答"对谁说"**——prob-fire 无 trigger 时它走 `last_speaker`(0.7) fallback，注入 `[当前你在回复：Religion(QQ)]`。但它**只解决 addressee(对谁)，不解决 message/topic anchor(哪条/哪个话题)**。这正是 Multi-Party Hangover 的 "addressee 靠结构 / content 靠文本" 区分在本仓的真实落点：**对谁有了，回什么没锚**。所以 bot 知道在跟 Religion 说话，却仍从 timeline 自由挑了旧话题"鱼鱼烧"。

### 16.3 修正后的方案（复用优先，最小新增）

**P0′ — 不新建锚点，扩 `add_pending_trigger` 的调用条件（治 R1，最高优先级）。**
不造新 trigger 类型、不碰 AddresseeHint。改动收敛为：prob-fire 命中且 `slot.trigger is None` 时，**直接调一次现有的 `add_pending_trigger`**，reason 写"群里刚有人发了 X（表情/话），你接这条所在的话题，可只回表情或短一句"，`message_id`/`target_user_id` 用 notify 已有的入参。这是 closing P0 已验证的同一行 API，**纯复用**。新增量 ≈ prob-fire 分支里 5 行 + reason 文案。
- 与现有 AddresseeHint 的关系：AddresseeHint 给"对谁"，这一步补"哪条/什么话题"，正交互补，不冲突。

**P1(a)′ — 扩 `text_preflight` 识别占位符 + 接 RWS（治 R2/R3）。**
- 让 `preflight` 的正则**识别 `«动画表情»`/`«图片»`/`«表情»` 占位**（当前漏了，是 R2 直接成因）。但**不要让它命中即沉默**——表情在 F2 里是合法附和（Goffman/Edelsky）。改为：preflight 输出的 `density` 不再是硬编码常量，而是流入 RWS。
- 复用 `info_gain` 空壳字段（rws.py:32，已有但死）：把它在 scheduler.py:1063-1078 填上 `= density`，weights.py 给正权重（信息量低→分低）。**零新字段，激活一个预留接口**。
- 效果：一个表情单独顶阈值的概率被压低，但连发刷屏(skip_pressure/hawkes 高)仍可通过。

**P1(b)′ — 仅改进 @他人渲染（治 R2 残留）。**
reply-to 结构（`[QUOTED_MSG]`）**已有，不动**。唯一改进点：router.py:683-685 的裸 `@<qq号>` 解析为 `@昵称`（用已有的 `NameVariationRegistry`/name_registry）。让 bot 看懂"这条 @ 的是别人不是我"→ 配合 P0′ 降低接错话题概率。小改一处渲染。

**P2′ — 只补"连续无应答闭嘴"，其余复用。**
- 冷却、skip 升压、陈旧回复中断(`_EmissionGate`/arbiter) **全部已有，复用，不重写**。
- 唯一新增：`slot.consecutive_unanswered` 计数器——ambient/主动回应后 N 条内无人接（无 @bot、无 reply-to-bot）则 ++，达阈值临时压低该群 ambient 概率。这是审查确认仓库**没有**的唯一节制项。

### 16.4 修正后的工作量对比

| 项 | 原 §15 设想 | 审查后 | 实际新增 |
| --- | --- | --- | --- |
| 回应锚点 | 新 trigger 类型 + 注入机制 | 复用 `add_pending_trigger` | ~5 行 + 文案 |
| 低语义判定 | 新 `classify_low_signal` | 扩 `text_preflight` 正则 | 加 2-3 条正则 |
| RWS 信息量特征 | 新特征字段 | 激活 `info_gain` 空壳 | 0 新字段，填 1 处 + 调权重 |
| reply-to 结构标注 | 新渲染 | 已有，不动 | 0 |
| @他人标注 | （未细想） | 裸 QQ→昵称 | 1 处渲染 |
| 冷却/中断/skip | 部分新增 | 全复用 | 0 |
| 连续无应答闭嘴 | 新增 | 确认无，新增 | 1 计数器 |

**结论**：原 §15 估的"三档新功能"实际**大半是复用现有设施**。真正的新增只有：① `info_gain` 空壳激活；② preflight 占位符正则；③ @昵称渲染；④ `consecutive_unanswered` 计数器。其余（锚点、reply 结构、冷却、陈旧回复中断）**全部复用**。L2 话题解缠/floor 确属全新能力，但按 §14 留作按需，本期不碰。

**修正后建议**：**先做 P0′（复用 `add_pending_trigger`，5 行）+ P1(a)′（激活 `info_gain` + preflight 认占位符）**——两者直击 R1/R2、几乎零新代码、共同消除"回旧话题"主症状。P1(b)′/P2′ 视烤群表现再定。**待批准后出 D3 清单实施。**

## 17. 自我审查：P0′/P1′ 是否治标不治本？调研先进内容落实了吗？

> 诚实结论先行：**§16.3 的 P0′/P1′ 是治标，不是治本。** 它们让"鱼鱼烧"那一类具体症状消失，但没有触及调研（§9–§12）指出的结构性缺陷。下面把"调研说该有什么"和"方案实际给了什么"逐条对照，不为方案辩护。

### 17.1 治标 vs 治本：缺口在哪

调研的核心论断是：群聊决策应是**三段式串联**——①消息属于哪个并行话题块 → ②我在该块是什么接收角色 → ③该不该开口。§1 的 bug 只是"③开口了但①②错了"的一个可见切片。

| 调研要求的能力 | P0′/P1′ 给了吗 | 性质 |
| --- | --- | --- |
| ① 话题块归属（disentanglement / skip-connecting） | **没有**。P0′ 只是把"回应锚点"指向**最后一条**消息，仍假设"当前=最新"，没重建 tying 链、没区分并行块 | 治标：换了个锚点对象，没建归属能力 |
| ② 接收角色（addressed / overhearer，Goffman） | **部分，且是已有的**。`AddresseeHintDetector` 给"对谁"，但 fallback 到 last_speaker 时并不判断"我到底是不是被寻址者" | 未新增 |
| ③ 开口决策（动机打分，Inner Thoughts） | **没有**。P1(a)′ 只在 RWS 加一个"信息量低→分低"的标量，仍是"概率顶阈值就开口"，没有相关性/信息缺口/会否刷屏的多维动机 | 治标：压低了表情的触发概率，没换决策范式 |

**一句话**：P0′/P1′ 是在现有"概率插话 + 单点锚定"框架内做**参数与锚点修补**，治的是 R1/R2/R3 这三个**实现层**根因；但调研指出的**架构层**根因——"bot 没有话题块归属与角色判断，用'最新消息=回应对象'的朴素模型"——P0′/P1′ **没碰**。所以下次只要并行话题交叉得更复杂（不是简单的"旧话题残留"，而是两三个话题块真正同时活跃），P0′ 把锚点指向"最新一条"照样会接错块。

### 17.2 调研先进内容，落实了多少

逐项核对四条战线的关键结论是否进了方案：

| 调研结论（出处） | 是否落实 | 说明 |
| --- | --- | --- |
| **结构信号决定 addressee，纯文本不够**（Multi-Party Hangover EMNLP24） | **部分**。reply-to 的 `[QUOTED_MSG]` 已有；P1(b)′ 补 @昵称。但"谁回复谁"的**图结构**没有显式喂给开口决策 | 已有设施承接了一半 |
| **放弃预测下一说话人，强信号规则+弱信号动机打分**（Inner Thoughts CHI25） | **未落实动机打分**。RWS 是概率回归，不是"相关性/信息缺口/影响/会否刷屏"多维动机分；P1(a)′ 只加了一个负向信息量项 | 治本的核心，未做 |
| **三段式：归属→角色→开口** | **未落实①归属**。这是最关键的缺口 | 治本的地基，未做 |
| **F1/F2 floor 分类**（Edelsky）：F2 刷屏里默认沉默/轻附和 | **未落实**。§1 正是 F2 表情刷屏被当 F1 实质接话；P1(a)′ 压低概率算近似，但没有显式 floor 分类 | §14 已列 L2，本期未碰 |
| **沉默是默认合法态**（Goffman/SSJ） | **理念落实，机制未变**。仍是"概率门决定"，没有"unaddressed→默认沉默"的角色驱动 | 部分 |
| **轻量实时解缠：@/reply 当 parent 边 + 时间窗聚类**（Elsner/Jiang） | **未落实**。topic_drift.py 只有单线程标量，没有话题块聚类 | 治本路径，未做 |
| **话题分割可用一次 LLM 分簇近似**（§12） | **未落实** | L2，本期未碰 |

**结论**：调研里真正"先进"的两点——**(A) 三段式中的"话题块归属"，(B) Inner-Thoughts 式动机打分**——P0′/P1′ **都没落实**。落实的多是"已有设施复用 + 概率参数修补"。

### 17.3 治本方案（对齐调研，挂现有骨架，分阶段）

审查发现仓库**已有可挂载治本的骨架**，治本不等于推倒重来：

- `evaluate_group_gate_shadow`（reply_workflow.py:588）**已在 shadow 模式**计算 `is_addressed / has_other_at / reply_to_bot / followup_kind / last_assistant_to_user`——**这正是 Goffman 参与框架 + addressee 检测的信号维度**，只是还不决策（日志里 `mode=group_gate_shadow action=pass` 即它）。
- `topic_drift.py` 有相邻相似度标量，可作话题块切分的**输入信号**（非成品）。
- RWS 的 `info_gain` 空壳 + 集中填充点，可承接动机分的一个维度。

**治本三阶（B 系列，区别于治标的 P 系列）：**

**B1 — 话题块归属（治①，对齐 Elsner/Jiang 轻量解缠）。**
新增一个轻量 `TopicBlockTracker`：用**已有强信号**（QQ reply-to 边 = parent、@关系、时间窗 + topic_drift 相似度）把最近 N 条消息增量聚成 2–3 个话题块（连通分量），无需训练、无需大模型。prob-fire 时，P0′ 的锚点不再指向"最新一条"，而是指向**bot 最可能参与的那个块的代表消息**（块里有 @bot/reply-bot/上次 bot 发言 → 该块；否则最活跃块）。这才是"重建 tying 链、按块归属"而非"假设最新=对象"。

**B2 — 角色判断接管（治②，复用 shadow gate）。**
把 `evaluate_group_gate_shadow` 从 shadow **逐步转正**：输出 `is_addressed`（被寻址）vs `overhearer`（旁听）。overhearer 态 → 默认沉默（Goffman/SSJ），只更新理解不发声。这是把已有 shadow 设施"接线"，不是新建。

**B3 — 动机打分替换裸概率（治③，对齐 Inner Thoughts）。**
在 thinker 决策处（已有 `light_reply`/`light_kind` 三态基础）增一次轻量打分，维度裁剪为 **相关性 / 信息缺口 / 会否刷屏** 三项（Inner Thoughts 8 维的最小子集），配 `im_threshold`。RWS 保留作"时机/频率"层（时机交规则的精神不变），动机分管"该不该回这条"。"沉默越久越想说"用 `consecutive_skip` 已有计数接 λ 衰减。

### 17.4 取舍建议（治标先行，治本立项）

不是二选一，而是**分层、按证据推进**：

1. **立即（治标，止血）**：P0′ + P1(a)′。承认是治标，但**成本近零、直接消除当前可见症状**，且不与治本冲突（B1 会把 P0′ 的锚点对象从"最新"升级为"块代表"，是平滑演进非推翻）。
2. **立项（治本，对齐调研）**：B1 话题块归属为**第一优先**——它是三段式的地基，且 §1 的复杂版（多块并行）只有 B1 能治。B2 复用 shadow gate 成本低，可随 B1。B3 动机分最重、风险最高（回潮/话痨），放最后并需烤群数据支撑。
3. **不做（除非实测痛）**：训练型解缠/addressee 模型、F1/F2 显式分类器（§14 L3）——prompt + 轻量信号已能拿大部分收益。

**诚实定性**：本报告 §15/§16 解决的是"这次为什么回错"，§17 才对齐调研指出"要让 bot 真正理解群聊并行话题该建什么"。**P0′/P1′ 治标可先上止血；B1–B3 才是调研先进内容的落实，建议单独立项（B 系列设计文档），不要用治标的上线掩盖治本的缺位。是否立项 B 系列、以及 B1 是否先行，待用户决策。**

---

### 引用核实状态

会话分析/社会语言学：Egbert 1997 (RLSI 30(1))、Goffman 1981 (*Forms of Talk*)、Edelsky 1981 (Lang.in Society 10(3))、Sacks/Schegloff/Jefferson 1974 (*Language* 50(4))、Lerner 2003 (Lang.in Society 32(2))、Schegloff & Sacks 1973 (*Semiotica* 8(4))、Sacks 1992 (*Lectures on Conversation*)、Herring 1999 (JCMC 4(4))。
解缠：Elsner & Charniak 2008 (ACL)/2010 (CL 36(3))、Kummerfeld et al. 2019 (ACL)、Mehri & Carenini 2017 (IJCNLP)、Jiang et al. 2018 (NAACL)、Liu et al. 2020 (IJCAI)、Yu & Joty 2020 (EMNLP)、Ma et al. 2022 (ACL)、Chi & Rudnicky 2021 (EMNLP)、Li et al. 2024 (TOIS)、Takada & Mori 2026 (CEUR)。
多方对话：Ouchi & Tsuboi 2016 (EMNLP)、Zhang et al. 2018 (AAAI, SI-RNN)、Le et al. 2019 (EMNLP, W2W)、Hu et al. 2019 (IJCAI, GSN)、Gu et al. 2021 (ACL, MPC-BERT)/2022 (ACL, HeterMPC)/2023 (ACL, GIFT)/2022 (IJCAI Survey)、Penzo et al. 2024 (EMNLP, Multi-Party Hangover)、MUCA 2024 (arXiv:2401.04883)、MM-When2Speak 2025 (arXiv:2505.14654)、Umair et al. 2024 (EMNLP Findings)。
生产/前沿：Liu et al. 2025 (CHI, Inner Thoughts, arXiv:2501.00383)、Park et al. 2023 (UIST, Generative Agents)、Nonomura & Mori 2024 (arXiv:2412.04937)、AutoGen SelectorGroupChat（源码）、AstrBot proactive_chat（开源）、sgnt.ai、basecase.vc、Corzo (Medium)。话题分割：Hearst TextTiling、Xing & Carenini 2021 (SIGDIAL)、DialSTART 2023 (arXiv:2305.02747)。

> 所有引用经子代理 web 检索核实标题/年份/出处。一处更正记录：曾被记为 Zhu et al. "Who Says What to Whom (end-to-end)" 实为两篇——masked transformer 是 Zhu et al. 2020 AAAI *Who Did They Respond to?*，首个 end-to-end transition-based 是 Liu et al. 2020 IJCAI。

---

## 18. F-γ（深究记录，**暂不修复**）：@bot + 引用消息时，被引用内容未成为回应焦点

> 状态：2026-05-30 实战定位，**按用户指示先深究、不修复**。

### 18.1 实例（群 993065015，23:13:03）

用户（工 1416930401）**引用了一条 B 站视频消息 950864058**（`《摸摸小saki》`，丛非凡发的）**并 @bot**。预期：bot 回应这个被引用的视频。实际：

- 触发判定正确：`is_addressed=True`、`current_trigger=at_mention`、出站带 `[CQ:reply,id=1296409766]`、`_focused_trigger_reason` 聚焦指令也已注入（23:28 前的版本已带）。
- 但 bot 23:13:20 回的是 **"那雪人三项我申请改成：躺着喝牛奶、趴着看书、翻身睡觉…"**——完全是**前一个话题块（雪人三项的玩笑）**，与被引用的"摸摸小saki"视频无关。**话题偏移到了前一个块。**

### 18.2 关键证据

`context prompt pack` 日志显示当前 query 主体是：
> `query='这个封面上的粉发少女在舞台比心诶~ 好可爱！让我康康是什么节目！ [QUOTED_MSG sender_id=2459515872 ...]'`

那句"粉发少女在舞台比心"是 **bot 自己更早（22:51）对另一个视频的回复**——它和被引用视频的 `[QUOTED_MSG]` 标记一起进了 query，但 LLM 生成时被 timeline 里更"热"的近期话题（雪人三项）主导，**被引用视频的实际内容没有成为回应焦点**。

### 18.3 初步根因假设（待深究确认，未验证）

- **`[CQ:reply]` 引用只在出站层标记"回复哪条"，被引用消息的内容没有被强注入为 thinker/主 LLM 的焦点**。@ 路径喂的是整条 timeline（多话题堆积），引用的视频只是其中一条普通历史消息，没有比"正打得火热的雪人三项"更高的权重。
- `_focused_trigger_reason` 的聚焦指令是"只回应对方这条消息当下的话题"——但"对方这条消息"是个**空 @+引用**（正文只有 `[reply][at][at]`，无文字），其"当下话题"=被引用的视频，而**引用内容的解析/注入深度不足**，LLM 抓不到它，退回 timeline 里最显眼的块。
- 这与 §11 的 Multi-Party Hangover 结论一致：**addressee（@谁）靠结构信号可解，但"回应哪条引用的内容"需要把被引用消息的语义显式提升为焦点**，当前仅有 `[CQ:reply]` 的结构标记不足以对抗 timeline 的话题惯性。

### 18.4 与已修问题的区分

- F-α（插非己块）→ B2 overhearer silent 已修。
- ratified 延续被压死 → B2 continuation floor 已修（23:28 上线）。
- **F-γ（引用内容未成焦点）→ 本节，独立问题，未修**。它发生在**已正确触发的 @ 路径内部**，是"回什么"的焦点问题，不是"要不要回/回哪个块"的调度问题。

### 18.5 深究方向（下一步，不在本次）

1. 确认 `[CQ:reply]` 引用的消息内容在喂给主 LLM 时的实际形态——是否只有 `[QUOTED_MSG sender_id=...]` 头而**正文/视频简介被截断或未注入**。
2. 查 `_render_message` 对 reply 引用视频的渲染深度（是否含视频标题/简介，还是只剩 sender 标记）。
3. 评估：被引用消息应否作为一个**高权重 anchor block** 注入（B1 的扩展），让引用焦点压过 timeline 话题惯性。
4. 待确认后再决定修法，不在当前修复批次内。

## 19. F-γ 深度调研（治本，非治标）：被引用内容如何成为生成焦点

> 2026-05-30 深度调研（文献 + 同类项目 + 本地代码核实）。目标：找治本方案，不做"再加一句聚焦指令"式的治标。引用均经 web 核实。

### 19.1 决定性发现：被引用视频的正文**根本没进 prompt**（本地代码核实）

深究 `kernel/router.py:_render_message` 的 reply 渲染（router.py:675-717）：

```python
original = reply_msg.extract_plain_text().strip()   # 第 683 行
if not original:                                     # 进 fallback
    for seg in reply_msg:                            # 只认 image/face/text
        ...                                          # json 卡片三者都不是 → 跳过
    original = "".join(seg_descs)                    # → ""
```

B 站视频是 QQ **小程序 `[json:data={...}]` 卡片**：`extract_plain_text()` 返回空；fallback 只处理 image/face/text 三种 seg，**json 卡片不匹配任何分支 → `original=""`**。最终喂给 LLM 的被引用块是：

```text
[QUOTED_MSG sender_id=2459515872 sender_name=丛非凡]

[/QUOTED_MSG]
```

**正文是空的。** 这与 §18.2 日志里 `[QUOTED_MSG sender_id=... sender_name=丛非凡]` 后无内容完全吻合。**F-γ 的第一性根因不是"注意力被带跑"，而是被引用视频的语义零注入**——LLM 拿到一个空壳引用标记，无内容可回应，必然退回 timeline 里有实质文本的热门话题（雪人三项）。

**关键巧合**：`plugins/bilibili/plugin.py:205 _extract_json_card_text(raw)` **已经能**从任意 QQ 小程序 json 卡片抽出 `prompt + meta.detail_1.title/desc`（bilibili 自己的流程在用，日志 `《摸摸小saki》| UP: …` 就是它解析的）。但 `_render_message` 的 reply 路径**没调用它**——能力已存在，只是没接到引用渲染上。

### 19.2 调研结论（文献，两条战线交叉印证）

问题分三层机制叠加（Lost-in-the-Middle 团队 + MPC 生成 + 多模态三方共识）：

| 机制 | 本案表现 | 关键文献 |
| --- | --- | --- |
| **被引用内容是占位符/空壳** | json 卡片正文未注入（19.1） | Divter (Sun et al., ACL 2022, arXiv:2110.08515)：模型对多模态内容的处理**经由其文字描述**进行——卡片必须先转成结构化文字才"可回应" |
| **结构信号被语义信号压倒** | `[CQ:reply]` 只是弱结构标记，timeline 近期话题在 token 上又密又新 | *Is ChatGPT a Good MPC Solver?* (Tan/Gu/Ling, Findings EMNLP 2023, arXiv:2310.16301)：reply-to 结构若只当普通文本堆入，弱模型反而**性能下降**；编码方式决定帮助还是噪声 |
| **位置偏置（U 形 attention）** | 被引用消息在历史中段，drift 目标在末尾 | *Lost in the Middle* (Liu et al., TACL 2024, arXiv:2307.03172)：相关信息在中段时性能最差；*On the Emergence of Position Bias* (Wu et al., ICML 2025, arXiv:2502.01951)：causal mask→primacy + RoPE→recency，中段被两头夹击 |

**治本方向（两 agent 一致排序，纯 prompt、不微调、兼容 Anthropic 闭源 API）**：

1. **卡片去占位符化（最高 ROI）**：被引用 json 卡片→结构化文字（标题/简介/UP/分区）。依据 Divter；本仓 `_extract_json_card_text` 现成可复用。**这一条直接消除 19.1 的空壳根因。**
2. **被引用内容置于 user turn 末尾**：吃 recency、避开中段塌陷（Lost-in-the-Middle U 形；Anthropic 官方"query 放最后最高 +30%"；RE2 二次编码 Xu et al. EMNLP 2024）。
3. **焦点指令两端夹 + XML 标记**：`<reply_target>` 包裹 + "只回应这条、其余历史作背景"指令在历史块前后各一次（OpenAI GPT-4.1 指南 + Anthropic 长上下文 tips）。
4. **distractor 裁剪（按需）**：以被引用消息为 query，降权/截断不同话题串的近期热点（QFS；*The Distracting Effect* arXiv:2505.06914）。

**明确排除（与本仓约束冲突）**：PASTA 注意力重加权（ICLR 2024, arXiv:2311.02262）、attention sorting、Found-in-the-Middle 校准、IN2/FILM 微调（NeurIPS 2024）——全部需读写注意力或微调权重，Anthropic 闭源 SSE 做不到，仅作"将来自托管开源模型"备选。

### 19.3 治本方案（待批准，分两步，非治标）

**根因是"被引用内容没进 prompt"，所以治本第一步必须是补全内容注入，而非加指令。**

- **G1（治本核心，必做）**：`_render_message` 的 reply 分支，当 `extract_plain_text()` 为空且 seg 含 json 卡片时，调用（复用）`_extract_json_card_text` 把卡片标题/简介填进 `[QUOTED_MSG]` 正文。让被引用视频**有语义可回应**。这一步单独就能消除 19.1 的空壳根因，预计解决大部分 F-γ。
- **G2（加固，按需）**：把被引用块 + 焦点指令搬到喂 LLM 的**消息末尾**（recency 区），并用 `<reply_target>` 标记 + 两端夹聚焦指令。依据位置偏置文献，对抗 timeline 话题惯性。属 `services/llm/client.py` 的 group message 构建层，改动面比 G1 大。

**落地顺序建议**：先做 G1（小改一处渲染、复用现成解析、直击第一性根因），观察 F-γ 是否消失；仅当 G1 后仍偶发 drift（说明 19.2 的位置偏置在起作用）才上 G2。**先 G1 验证，不要一上来就堆 G2 的指令/位置工程**——那样无法分辨到底是哪层在起效，也容易变成治标。

### 19.4 引用核实

Divter (Sun et al., ACL 2022, arXiv:2110.08515)；Lost in the Middle (Liu et al., TACL 2024, arXiv:2307.03172)；On the Emergence of Position Bias (Wu et al., ICML 2025, arXiv:2502.01951)；Is ChatGPT a Good MPC Solver? (Tan/Gu/Ling, Findings EMNLP 2023, arXiv:2310.16301)；RE2 (Xu et al., EMNLP 2024, arXiv:2309.06275)；PASTA (Zhang et al., ICLR 2024, arXiv:2311.02262)；The Distracting Effect (Amiraz et al., 2025, arXiv:2505.06914)；HeterMPC (Gu et al., ACL 2022, arXiv:2203.08500)；Coreference-Aware Dialogue Summarization (Liu/Shi/Chen, SIGDIAL 2021, arXiv:2106.08556)；Anthropic Long-context tips；OpenAI GPT-4.1 Prompting Guide。均经子代理 web 检索核实标题/年份/出处。
