# 情感动力学作为人类 vs AI 文本的判别签名 —— 研究立项

> Affective Divergence: Intra-/Inter-Utterance Sentiment Dynamics and Compensatory Balancing as a Human-vs-LLM Signature
>
> 状态：**研究立项 · 2026-06-16**（P0 首轮深读后修正，见 §2.5 + [LITERATURE-NOTES.md](LITERATURE-NOTES.md)）｜ 性质：**前沿研究**（核心命题经深读收窄为"情感的**时间动力学结构**作判别签名"，仍无直接先例）
> 定位：独立研究，**不接入运行态 bot**；若结论成立，未来或可反哺 `services/humanization`、`services/dialogue_climate`（仅设想，见 §8）。
> 方法纪律：本文档凡涉及具体论文方法处均标注置信度。检索仅及摘要/标题层，深读核实项见 §9 TODO。

---

## 0. 一句话命题

人类对话文本的**情感（效价）在句内、句间存在更高的波动**，并伴随**补偿性平衡**（一个偏离关系基线的局部情感会被追加成分——称呼语、波浪号、括号、削弱型 emoji/表情图——抵消）；而 LLM 生成文本情感**全程趋于一致、缺乏这种自平衡**。本研究检验「情感动力学特征」能否作为区分人类与 AI 文本的判别签名。

## 1. 引入与直觉来源（用户设想，原样登记）

提出者的三个观察（未经文献过滤的原始直觉，作为待检验素材）：

1. **句内/句间情感不一致**：AI 生成句子与段落情感基本全程一致；真人前一句与后一句、甚至一句前半句与后半句都会产生差异。
   - 例：关系很好的两人，一方在被化妆时说「你会不会嫌我烦啊，亲爱的」。两人心情平静、关系良好，整句情感推断应偏好；但**前半句假定了一个坏场景**（情绪趋恶劣），**后半句不自主添加「亲爱的」这类增进关系词来平衡整句情感值**。
2. **表情包的削弱配图**：话语尖锐的表达往往配可爱的图来削弱整体意思，使其默认更通用。例：见到擦边内容时用「臭婊子」+「萌萌二次元瞎想图」的概率高于直接骂出来。
3. **句末括号削弱语气**：新时代 QQ 群二次元会在句末加括号或半个括号来削弱语气，使情感不尖锐。

> 这三条分别对应 §3 的 H2（补偿性）、H3（多模态削弱）、H2 的标点子类。它们是**假设来源**，不是结论。

## 2. 这个命题能挂到哪些现成"情感指数"范畴上

> 结论：**支柱构念都有成熟文献背书；但把它们整合成「情感动力学 → 人/AI 文本判别签名」据初检无直接先例**——因此定为前沿研究而非套用现成指数。

| 用户设想支柱 | 对应学术范畴 | 代表文献（命中，置信度） |
|---|---|---|
| 真人情感会变、AI 全程一致 | **Emotion dynamics**：情感变异性(variability)、不稳定性(instability)、惯性(inertia) | Kuppens et al. 2010《Emotional Inertia & Psychological Maladjustment》(Psych Science, 736 cit)；Hamaker et al. 2015《Modeling Affect Dynamics》(Emotion Review, 226)；Koval/Kuppens 2013 affective instability。**高置信**（与本仓 dialogue_climate 已引同支） |
| 前半句负、后半句加词平衡整句 | **混合情绪/情感矛盾**(mixed emotions / ambivalence / co-activation) + **语用缓和**(mitigation/hedging)、**礼貌与面子**(politeness/face)、**称呼语**(address terms) | Dewaele & MacIntyre 2014「two faces of Janus」；Brown & Levinson 礼貌理论（领域基石，待补正引）；称呼语社会指示文献多篇。**中-高置信**。注：「compensatory balancing / 句内自平衡」**无干净现成术语**，是本研究新构念 |
| 尖锐文字 + 可爱图削弱 | **情感不一致 / 反讽**(emoji-text incongruity, sarcasm softening)、**多模态情感** | 《Behind Kind Words: Sarcasm》；Frenda et al. 2022《unbearable hurtfulness of sarcasm》；Al Rashdi 2015 emoji 功能。**中置信** |
| 句末括号/半括号削弱语气 | **CMC 语气标记 / 语用软化**(tone-softening punctuation) | 最近命中 Xu & Xia 2023《Digital tildes (∼) may convey more》(WeChat 波浪号)。**括号专文未检到——小空白，本研究可顺带补一手中文语料证据** |
| 情感值数能判文本是人是 AI | **LLM 文本检测**（现有特征：perplexity / 水印 / 词汇统计） | Tang et al. 2024《The Science of Detecting LLM-Generated Text》(CACM, 109 cit)；Wu et al. 2023 检测综述(arXiv 2310.14724)。**情感动力学作判别特征几乎空白 = 前沿点** |
| （工具）把对话情感画成时间序列 | **情感弧 / 情感轨迹**(emotional arc / sentiment trajectory) | Reagan et al. 2016《emotional arcs of stories dominated by six basic shapes》(EPJ Data Sci, 258)；Gallagher et al. 2021 word-shift graphs；Gâză et al. 2023 用 DFA 做文学情感序列。**高置信，提供 H1 的现成量化方法** |
| （侧证）AI 的情感表达确与人不同 | **LLM 情感能力评估** | Zhao et al. 2023《Is ChatGPT Equipped with Emotional Dialogue Capabilities?》(arXiv 2304.09582)；Abercrombie et al. 2023《Mirages: On Anthropomorphism in Dialogue Systems》(EMNLP) |

## 2.5 P0 深读后的命题修正（重要，决定 §3 假设）

首轮深读 Sandler 2024 / Muñoz-Ortiz 2024 / Hipson&Mohammad 2021 后，原命题分层修正（详 [LITERATURE-NOTES.md](LITERATURE-NOTES.md) §🔑）：

- **已被占，弃守**：① 人/AI **情感均值**差异——Sandler 实测 positive/negative affect 均值**无显著差异**(δ=.02 ns)，是弱特征；② 人类文本**整体方差更高**——Sandler 用 Levene 检验在全部 118 LIWC 类目证实，**单纯比静态方差不再新颖**。
- **真正空白，主攻**：情感的**时间动力学结构**（会话内轨迹形状、惯性 AR(1)、回弹 rise/recovery、补偿性微结构）作人/AI 判别——Sandler 只在**聚合/嵌入层**测情感，未做会话内时间序列；Hipson 的 UED 工具只用于电影角色非人/AI 判别。
- **空白且有强提示**：**co-activation（局部正负共存）**作人类签名——Sandler 发现**人类对话独有一个正负价情绪共存的 UMAP 簇**，AI 簇分离更干净(Dunn 0.222 vs 0.153)，明确援引 co-activation 理论。H2 未被操作化，但已有人/AI 差异苗头。
- **⚠️ 空白边界再收窄（P2 前针对性证伪检索结果，2026-06-16）**：**Teodorescu et al. 2023 (EMNLP) 已把 UED 指标当判别特征**用于**心理健康诊断**分组——即"文本情感动力学→判别器"这条方法范式**已发表，非本研究首创**。故本研究新颖点须改述为：**① 判别对象 = 人 vs AI（非人内部临床分组）；② 机制 = 补偿性平衡/co-activation 作人类签名（Teodorescu 无此构念）。不能再宣称"方法新颖"。** 针对性反向检索（约 10 路 + 引用追踪）仍未发现占「会话内情感动力学 × 人/AI 判别」交集者，空白**成立但应表述为"已尽合理检索未发现"，非"绝对不存在"**（preprint/中文/2025-26 最新稿覆盖有限）。详见 [LITERATURE-NOTES §第四轮](LITERATURE-NOTES.md)。

## 3. 形式化假设（修正版）

- **H1（时间动力学，非静态方差）**：同等语境下，人类对话文本的**情感时间序列动力学**——句间效价的 AR(1) 反惯性、MSSD/过零率、UED 的 variability/rise-recovery rate——与 LLM 文本可区分。**注意**：弃守"静态方差"（已被 Sandler 占），主张落在**序列结构**层。
- **H2（补偿性 + 共存）**：人类话语存在 (a) **局部负效价后接缓和成分**（称呼语/波浪号/括号/削弱 emoji）的**时序共现**（情感轨迹"负峰→快速回正"，对应 UED 高 recovery rate）；(b) 同一语境内**正负价共存**（co-activation）频率高于 LLM。**关键**：不能只数礼貌/缓和词频——Sandler 实测 ChatGPT **礼貌词反而更多(δ=.46)**，必须抓"局部负价触发的缓和"时序结构，否则被 AI 高礼貌基线淹没。
- **H3（多模态削弱）**：人类在尖锐文本上**配削弱型图/标点**的概率高于直接输出；图文情感符号相反（incongruity）是人类特征。
- **H4（判别力）**：H1–H3 的情感动力学特征，**加入现有检测器后能提升人/AI 判别 AUC**（增量价值，而非取代 perplexity；与 Tang 2024 / Wu 2023 既有特征互补）。

> 反事实/证伪条件：若在控制话题、**长度**（Sandler 实测 AI 词数 5× 于人，长度是头号混杂）、情绪基调后 H1–H3 的人/AI 差异消失，或 H4 的增量 AUC 不显著，则命题不成立——文档须如实记录否定结果。

## 4. 方法

### 4.1 测量管线
- **效价量化（双轨交叉验证）**：① 词典法 VAD / 中文大连情感本体；② 模型法（句级 sentiment + 对话情感识别 ERC，如 DialogueRNN/MELD 一脉）。双轨一致性本身作为稳健性检查。
- **情感轨迹特征（H1）**：把一轮对话的逐句（或逐子句）效价排成时间序列 → 提取方差、过零率、AR(1) 系数（情感惯性）、MSSD/RMSSD、DFA 标度指数。**首选现成底座 = Hipson&Mohammad 2021 的 UED 框架**（NRC VAD 词级 + home base/variability/displacement/**rise-recovery rate**，R 包 `edyn` 可复用），情感弧形状(Reagan 六弧型)作长文本补充。**注意**：UED 原文要求角色 ≥50 turns，短对话可靠性须 P1 先验证（见 §9）。
- **补偿性特征（H2）**：(a) 规则+统计检测「局部负效价 span 后窗口内出现缓和标记（称呼语词表 / ~ / ()/（）/ 削弱 emoji）」的**时序共现率**——等价于 UED 的高 recovery rate；(b) 同一窗口内**正负价共存**（co-activation，借 Larsen 2001 / Berríos 2015 测量法）。对人/AI 做配对检验。**不可只数缓和词频**（Sandler 实测 AI 礼貌词更多）。
- **多模态削弱（H3）**：需带图文的语料；图情感用现有 emoji/sticker 情感映射（可借 `services/humanization/emoji_sentiment.py` 思路）+ 视觉情感模型，算图文情感符号相反率。

### 4.2 数据源候选

- **人/AI 平行语料**：HC3（arXiv 2301.07597，HuggingFace `Hello-SimpleAI/HC3`，~40k 人/ChatGPT 问答，含中文子集，**确认公开**）；MAGE（`yaful/MAGE`，437k 条，**CC BY 4.0**）；M4GT-Bench（`mbzuai-nlp/m4gt-bench`，多语含中文，**CC BY 4.0**）；RAID（`liamdugan/raid`，>10M 带对抗攻击，**MIT**）。**三者许可/可得性已核查无障碍**，详见 [CORPORA-AVAILABILITY.md](CORPORA-AVAILABILITY.md)。
- **人类对话情感语料**：DailyDialog、EmpatheticDialogues、MELD、IEMOCAP（英文带情感标注）；中文 LCCC、社交媒体语料（**许可与隐私须先过审**）。
- **多模态（H3 专用）**：带 sticker/emoji 的中文群聊语料——公开可得性最弱，可能要退化为受控小规模标注集。

### 4.3 实验设计
1. 描述统计：人 vs AI 在 H1–H3 各特征上的分布与效应量（控制话题/长度/语言协变量）。
2. 判别实验（H4）：baseline 检测器（perplexity / 监督分类）vs +情感动力学特征，比较 AUC/F1 增量与特征重要度。
3. 消融与跨域：分语言（中/英）、分模型（ChatGPT / 开源 LLM）、分体裁（问答 vs 闲聊）验证稳健性。

## 5. 与已有工作的关键区分（即"新在哪"）
- 情感弧、emotion dynamics、礼貌缓和、emoji 反讽**各自成熟**，但都在各自领域内做描述/分类，**未被用作人/AI 判别特征**。
- LLM 检测领域的特征工程以统计/水印为主；**情感动力学维度基本空白**（待 §9 核 Guo 2023 HC3 的语言学分析是否已含情感，以确认空白边界）。
- 「**句内补偿性平衡**」作为可操作化构念是本研究的原创贡献点——现有最近构念（混合情绪、语用缓和）都不直接覆盖"局部情感自抵消"。

## 6. 风险与边界
- **R1 测量噪声**：句级/子句级情感标注误差会淹没微弱的句内波动信号——双轨交叉 + 人工抽检兜底。
- **R2 混杂**：人/AI 差异可能由话题/长度/体裁而非情感动力学造成——必须控制协变量，否则 H4 增量不可信。
- **R3 LLM 在进化**：带情感 prompt 或 RLHF 后的新模型可能"学会"波动——结论需标注模型版本与时间窗。
- **R4 中文多模态语料稀缺 + 隐私**：H3 最难落地；群聊语料涉真人隐私，须脱敏与合规审查（绝不外发原始对话）。
- **R5 术语风险**："compensatory balancing"无现成量表，操作化定义须自证信度（标注一致性 kappa）。
- **R6 omubot 实地采集合规约束**（`topic_corpus.db` 已于 2026-06-17 开启采集）：
  - **用途**：仅限本研究 Affective Divergence P2 离线情感动力学分析，不作其他用途。
  - **匿名化**：speaker QQ 号落盘即 SHA256+盐哈希（16 字符，不可逆还原），哈希值仅用于链接同一说话人的跨消息时间序列；原始 QQ 号不进数据库。
  - **文本范围**：仅采集参与话题块归属的群消息文本，不含私信、不含 NapCat 内部配置。
  - **数据留存**：研究完成后删除 `storage/topic_corpus.db`；任何情况下不推送到公开仓库（已在 `.gitignore` 的 `storage/*.db*` 物理护栏覆盖）。
  - **外发禁止**：原始或脱敏后的采集语料**绝不外发、不上传任何公开平台**；论文/报告引用仅以聚合统计量呈现，不含原文引述。
  - **被采集群**：仅 2 个 active 测试群（984198159/993065015），8 个 silent_learn 大群上游早返回，不进采集路径。

## 7. 阶段计划

- **P0 文献深读**（✅ 完成）：三轮深读，空白边界确认（情感**时间动力学**作判别签名=真空白）、H1/H2/H3 测量法 + 奠基正引齐备，见 [LITERATURE-NOTES.md](LITERATURE-NOTES.md)。
- **P1 可行性小实验**（✅ 完成 + ✅ 修订，见 [P1-RESULTS.md](P1-RESULTS.md)）：先在 HC3 跑，**经用户指正 HC3=GPT-3.5 太旧**，改用含 **GPT-4** 的 RAID(abstracts 单域)重跑 human/chatgpt-3.5/gpt4 三组。**修订结论：体裁对齐+含GPT-4后，"人类情感波动>AI"是真信号且效应可见(SD/MSSD δ≈−0.2，小→中)，方向与 HC3 上反向微弱信号相反——印证旧模型会误导；但 null 检验显示该信号多为静态分布差异，真·时间动力学仅 AR1 且效应小；代际上 gpt4 略比 chatgpt 靠近人类但幅度小、分维度。** 纯 H1 仍偏弱，核心赌注移 H2。
- **P2 全量实验**（据 P1 修正）：① 弃 VADER 句级，换 ERC/NRC-VAD+UED 滚动平均降噪；② 聚焦**顺序敏感指标**(AR1/UED rise-recovery/DFA)，每个过 null；③ 换**对话体裁**语料(EmpatheticDialogues + 中文)对齐命题；④ **核心赌注移向 H2**(co-activation + 缓和回弹)——命题更独特处；⑤ H4 判别增量 + RAID 抗改写。
- **P3 多模态扩展**（条件性）：视 H3 语料可得性决定是否纳入。

## 8. 未来或可反哺 bot（仅设想，不实现）

若 H1–H3 成立，可反向用于**让 bot 输出更像人**：在 `services/humanization` 注入"受控情感波动 + 补偿性缓和成分"，或让 `services/dialogue_climate` 的 ClimateState 驱动句内情感微结构。**本研究阶段不触碰任何运行态代码**，此节仅备忘。

## 9. 深读核实 TODO（P0 三轮深读后更新）

- [x] **A2 HC3 (2301.07597) 是否已含情感维度** —— 经 Tang 2024 CACM §3.1.3 二手确认：HC3 的情感发现属**静态层**（人类多用感叹号/问号/省略号表情绪，LLM 更正式中性），**非时间动力学**，空白判断不被推翻。**TODO 关闭**。
- [x] **Reagan 2016 情感弧测量法** —— 全文取得：10k 词滑窗 + labMT + SVD/Ward/SOM 六弧型。**短对话不适用**（原文明言词典法单句差于随机、需 10k 词窗），用 Hipson UED 替代；其 null-model 检验法已纳入本研究设计。
- [x] **Kuppens 2010 / Hamaker 2015 / Bos 2018 的 inertia/MSSD/SD 精确公式** —— 已取（MSSD=相邻差平方均值、inertia=AR(1)、SD=dispersion，三者须互控），见 [LITERATURE-NOTES.md](LITERATURE-NOTES.md) C 簇 + 指标合表。
- [x] **Tang 2024 / Wu 2023 检测特征清单** —— 全文取得：sentiment 仅作**静态**语言模式特征，**无时间动力学条目**，空位精确定位。
- [x] **中文"括号削弱语气"** —— **缺口填平**：Li & Lin 2023《Parentheses used as pragmatic strategies in Chinese online socialization》(Pragmatics & Society 14(3)，数据含 **QQ**) + Song 2022（句末 emoji 包裹）+ Luo 2018（中文括号专项）。曾是最弱子假设，现有 peer-reviewed 专文。
- [x] MAGE / M4GT-Bench / RAID 的可得性与许可 —— **核查完成**（见 [CORPORA-AVAILABILITY.md](CORPORA-AVAILABILITY.md)）：三者均 ACL 2024、均开放可得，MAGE/M4GT-Bench=**CC BY 4.0**、RAID=**MIT**，唯一义务署名引用，**无可得性/许可障碍**。选型：P1 用 MAGE（英文）、中文线用 M4GT-Bench 中文子集+HC3、P2 鲁棒性用 RAID 对抗维度。
- [ ] 用 **GPT-4/Claude/Gemini 等新模型**复核 Sandler 的 ChatGPT-3.5 结论（R3 实锤）。
- [x] UED 指标在**短对话**（3-20 turns）上的最小长度阈值——**P1 已部分验证**：≥4 句即可算 AR1/MSSD/ZCR（[P1-RESULTS.md](P1-RESULTS.md)），管线通；但 P1 发现这些指标在 HC3 上效应极小且多为静态分布伪装，**短文本可算 ≠ 有判别力**，P2 须换顺序敏感指标 + 对话语料。
- [+] **P1 新增待办**：用 GPT-4/Claude 等新模型语料复核（HC3 是 ChatGPT-3.5 时代）；换 ERC/NRC-VAD 降句级噪声；核心赌注移向 H2。

## 附：检索留痕（2026-06-16）

- 学术库 batch_search 三轮，命中见正文表格；关键确认：HC3 公开(HuggingFace)、Reagan 2016 情感弧、Tang 2024 检测综述、Zhao 2023 ChatGPT 情感能力。
- P0 首轮深读全文 5 篇（Sandler 2024 / HC3 / Muñoz-Ortiz 2024 / Hipson&Mohammad 2021 / 部分检测综述），抓取受限 5 篇待手动取，详见 [LITERATURE-NOTES.md](LITERATURE-NOTES.md)。
- 本轮检索 + 立项 + 首轮深读，未接触运行态。
