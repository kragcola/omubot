# P0 文献深读笔记 —— Affective Divergence

> 配套 [PROPOSAL.md](PROPOSAL.md) / [READING-LIST.md](READING-LIST.md)。状态：**第一轮深读完成 · 2026-06-16**
>
> 本轮深读了**决策关键**的 5 篇全文（A1/A2/Muñoz-Ortiz/D2/部分 B），足以对核心「空白」命题下修正判断。其余按 READING-LIST 优先级续读。
> 全文获取方式：anysearch extract 抓 HTML 版（arXiv /html/、PLOS、期刊页）；PDF-only 与超大页面（Frontiers PDF、Song2022 PDF、Reagan PDF）抓取失败，标记为"待手动取全文"，本笔记仅据其摘要/检索元数据，**不臆造内容**。

---

## 🔑 核心结论：空白命题的修正（最重要）

深读后，原 proposal「情感动力学作判别特征是空白」的主张**需要分层修正**——有一部分已被做过，真正的空白更窄也更精确，但**信号大概率存在**（这反而是利好）：

| 子主张 | 修正后状态 | 证据 |
|---|---|---|
| "比较人/AI 文本的**情感均值**" | ❌ **已做，且是弱判别特征** | Sandler 2024：positive/negative affect 均值人/AI **无显著差异**（δ=.02, ns）。Muñoz-Ortiz 2024：均值差异存在但依领域/情绪类别而定。→ **均值不是好特征，别押注它** |
| "人类文本**整体方差/变异性**更高" | ⚠️ **已有强证据（部分被占）** | Sandler 2024：Levene 检验，**全部 118 个 LIWC 类目**人类方差 > ChatGPT（p<.001）。→ H1 的"静态方差"层面已被证实，**单纯比方差不再新颖** |
| "**句内/句间情感时间动力学**（轨迹、AR(1) 惯性、MSSD、rise/recovery）作人/AI 判别" | ✅ **仍是空白** | Sandler 在**会话/嵌入聚合层**测情感，**未做会话内时间序列动力学**；Hipson&Mohammad 2021 给了 UED 工具但用于**电影角色**非人/AI 判别。→ **真正的前沿点在这里** |
| "**补偿性平衡 / 局部正负共存**（co-activation）作人类签名" | ✅ **空白，但有强提示** | Sandler 2024 发现**人类对话独有一个 UMAP 簇**：正价情绪(caring/trusting/proud)与负价(afraid/terrified/lonely/devastated)**共存**，明确援引 co-activation 理论(Larsen 2001)；ChatGPT 簇分离更干净(Dunn 0.222 vs 0.153)。→ H2 未被操作化，但**已有人/AI 差异的直接苗头** |
| "情感动力学作**增量检测特征**(H4)" | ✅ **空白** | 检测综述(Tang 2024/Wu 2023)特征以统计/水印为主；无情感动力学特征条目 |

**一句话修正**：命题从"无人比较过人/AI 情感"（错，会被 Sandler 一篇推翻）收窄为——**"情感的时间动力学结构（轨迹形状、惯性、补偿性回弹）作为人/AI 判别签名，尚未被操作化或用作检测特征；且已有间接证据（人类方差更高 + 人类独有情绪共存簇）表明该信号很可能存在。"** 这个修正版**更稳、更可辩护，且仍然新颖**。

---

## A1 — Sandler et al. 2024（**枢轴文献，必引**）

- **全引**：Sandler, M., Choung, H., Ross, A., David, P. (2024). *A Linguistic Comparison between Human and ChatGPT-Generated Conversations.* Proc. ICPRAI 2024. arXiv:2401.16587v3. CC-BY.
- **数据**：人类 = EmpathicDialogues(Rashkin 2019，25K 双人对话，32 情绪，MTurk)；AI = 作者新建 **2GPTEmpathicDialogues**（两个 ChatGPT-3.5-turbo 互聊，19.5K，temp=0.5）。**数据+代码公开**：github.com/morganlee123/2GPTEmpathicDialogues（LIWC 需自备授权）。
- **方法**：LIWC-22，118 类目；t 检验(均值) + Levene(方差) + Bonferroni(p<.001)；再用 text-embedding-ada-002 嵌入 + RF/SVM/MLP 做正负价二分类 + UMAP 可视化。
- **关键发现**（逐条，含对本研究的用途）：
  1. **人类方差更高**：全部 118 类目 Levene p<.001，ChatGPT 方差更低（作者归因 temp=0.5 + 训练求"普适讨喜"）。→ **H1 静态层的现成佐证**。
  2. **情感均值无差异**：Emotion Positive δ=.02 ns、Emotion Negative δ=.02 ns；但 Emotional Tone(更正向) ChatGPT 略高(δ=.30)。→ **均值是弱特征**。
  3. **ChatGPT "比人更像人"**：social/prosocial/politeness/cognition/attentional-focus/analytical 全部更高。**Politeness ChatGPT 显著更高(δ=.46)** → 注意：这对 H2"礼貌缓和"是反直觉信号——AI 礼貌词更多，所以 H2 不能只数礼貌词频，必须抓"**局部负价后紧跟缓和**"的时序共现，否则会被 AI 的高礼貌基线淹没。
  4. **唯一人类胜出**：Authenticity（人 63.99 vs AI 52.49, δ=.42）。
  5. **co-activation 簇**（**H2 的金线索**）：人类对话嵌入 UMAP 有一个独特簇，正负价情绪共存；ChatGPT 簇分离更干净。作者原话：人类"seemingly contradictory emotions can coexist within the same context"，AI"more predictable and uniform patterns of emotional expression"。
- **局限（直接影响本研究设计）**：① 仅 ChatGPT-3.5（R3 模型进化风险实锤——须用新模型复核）；② 词数严重不等（AI 300 vs 人 58 词，p<.001）→ **长度是头号混杂**，本研究必须控制；③ 价分类只用了 valence 二分，作者自己建议加 arousal 维。
- **对本研究**：既是最强的相关工作（必须正面持有，否则命题显得无知），也提供了**现成对照语料**(2GPTEmpathicDialogues + EmpathicDialogues)和**方法对照点**（我们做时间动力学，正好补它的聚合层空白）。

## A2 — Guo et al. 2023（HC3，语料确认）

- **全引**：Guo, B., Zhang, X., Wang, Z., Jiang, M., Nie, J., Ding, Y., Yue, J., Wu, Y. (2023). *How Close is ChatGPT to Human Experts? Comparison Corpus, Evaluation, and Detection.* arXiv:2301.07597.
- **确认**：HC3 ~数万人/ChatGPT 对照问答，覆盖 open-domain/金融/医学/法律/心理，**含中英文**（HC3 有中文子集，须核版本）；代码/模型公开 github.com/Hello-SimpleAI/chatgpt-comparison-detection；HuggingFace `Hello-SimpleAI/HC3`。
- **§9 TODO① 部分回答**：摘要明言做了"comprehensive human evaluations and **linguistic analyses**"。**全文 PDF 抓取超限未拿到细节**——是否已含**情感**维度分析仍需手动取 PDF 第 3-4 节核实。**这条 TODO 不关闭，降级为"高优先手动核"**。但即便 HC3 做过静态情感对比，也属"均值/计数层"，不触及本研究的时间动力学，空白判断不受推翻。
- **用途**：QA 型人/AI 平行语料（与 Sandler 的对话型互补，覆盖不同体裁，利于 H4 跨域）；中文子集是中文实验的关键。

## Muñoz-Ortiz et al. 2024（人/AI 新闻文本对比，强相关）

- **全引**：Muñoz-Ortiz, A., Gómez-Rodríguez, C., Vilares, D. (2024). *Contrasting Linguistic Patterns in Human and LLM-Generated News Text.* Artificial Intelligence Review 57:265. arXiv:2308.09067v3.
- **规模**：6 个 LLM（3 家族 × 4 尺寸）vs 人类英文新闻；维度涵盖形态/句法/心理计量/社会语言学。
- **情感相关发现**：① 人类**句长分布更分散**、词汇更多样（= 变异性更高，呼应 Sandler）；② **人类负面情绪更强（fear, disgust）、joy 更少**；LLM 更正向，且**模型越大 toxicity 越高**；③ LLM 更多数字/符号/助动词（客观腔）+ 更多代词；④ **LLM 与人的差异 > LLM 之间的差异**（利好：判别信号稳定跨模型）。
- **对本研究**：第二个独立证据链表明"均值情感有方向性差异 + 人类变异性更高"，但同样是**静态聚合**，无时间动力学。强化"静态层已被覆盖、动力学层是空白"的分层判断。

## D2 — Hipson & Mohammad 2021（**H1 的现成测量工具箱，必引必用**）

- **全引**：Hipson, W.E., Mohammad, S.M. (2021). *Emotion dynamics in movie dialogues.* PLOS ONE 16(9):e0256153. CC-BY。代码(R 包 `edyn`)：github.com/whipson/edyn。
- **贡献**：把心理学 emotion dynamics 落到**话语序列**，提出 **Utterance Emotion Dynamics (UED)** 一套指标，词级、用 **NRC VAD + NRC Emotion Lexicon**，对每个角色按时间排词 + 滚动平均（valence 用 10 词、arousal、正负密度用 30 词窗）。
- **UED 指标定义（可直接实现）**：
  | 指标 | 定义 | 捕捉 |
  |---|---|---|
  | Emotion Word Density (EWD) | 情感词占比；VAD 维下=词的平均 valence/arousal | 典型情感水平 |
  | **Home Base** | 高概率情感子空间；1D=均值±t 分布 CI(68%)；2D=v-a 协方差椭圆(χ² 临界) | 个体情感"驻地" |
  | **Variability** | 状态 SD（2D=v、a 的 SD 平均） | 不可预测性 = **H1 核心** |
  | Displacement count/length | 离开 home base 的次数 / 离开到返回所用词数 | 偏离频率与持续 |
  | Peak distance | 单次 displacement 离 home base 最远点的欧氏距离 | 偏离幅度 |
  | **Rise / Recovery rate** | peak 距离 ÷ 上升(或恢复)期词数 | 情感**反应性 / 调节**（= 补偿回弹的时序代理，**对 H2 极关键**） |
- **可移植性 caveat（必须正视）**：他们只取**≥50 turns** 的角色算指标，且明说"narrative 须有足够词数才能得可靠指标"。→ **QQ 群短消息/几轮对话可能词数不足**，UED 在短对话上的最小长度阈值是 P1 必须先验证的可行性问题。可能的对策：聚合同一用户多条、或退化到更鲁棒的少量指标(EWD/variability)。
- **现成发现可类比**：变异性最低的角色是 *Star Trek 的 Data（机器人，"experiences minimal emotion"）*，最高的是大反派 Ginger。→ 一个文学化但生动的旁证：低情感变异 ↔ 机器感。
- **对本研究**：**rise/recovery rate 是 H2"补偿性回弹"最接近的现成操作化**——人类局部偏离后快速拉回(高 recovery)可作为补偿平衡的时序指纹。直接采用 NRC VAD + UED 作为 H1/H2 测量底座，省去自造。

---

## 待手动取全文（抓取受限，不臆造）

| 文献 | 受限原因 | 降级处理 |
|---|---|---|
| A2 HC3 全文 §3-4 | PDF 超大 | §9 TODO① 保留为高优先手动核——HC3 是否已含情感维度 |
| E1 Berríos 2015 混合情绪元分析 | Frontiers PDF content-type | 手动取；H2 测量法基石 |
| D1 Reagan 2016 情感弧 | UVM PDF 超大 | 手动取；摘要已知=滑窗+labMT 词典+SVD/聚类六弧型 |
| G1 Song 2022 句末符号/emoji | 作者站 PDF 超大 | **最对口"括号削弱语气"**，手动取优先 |
| B1 Tang 2024 CACM 全文 | 未抓 | 取特征清单，精确定位情感动力学空位（TODO④） |

## 下一步（P0 续）

1. ~~手动取 5 篇受限全文~~ → **第二轮已取 4/5**（见下 §第二轮）。仅 D1 Reagan / D3 MDPI 仍卡，方法已从二手确认。
2. ~~续读 C1-C3 / H1-H2~~ → **已完成**（见 §第二轮 + 指标表）。
3. ~~回写 PROPOSAL~~ → **已回写**（§2.5 + §3 假设 + §4 方法 + §9 TODO）。

---

## 第二轮深读（2026-06-16，换 HTML/PMC/期刊页取全文）

## B1 — Tang et al. 2024 CACM（**检测特征清单，定空位，必引**）

- **全引**：Tang, R., Chuang, Y.-N., Hu, X. (2024). *The Science of Detecting LLM-Generated Text.* Commun. ACM 67(4):50–59.（全文经 ar5iv 2303.07205 取得）
- **检测特征三大类（§3.2，逐条核实）**：① **统计**：perplexity（人类文本困惑度更高——表达更多样）、GLTR 词排名、Zipf 系数；② **语言模式**：词汇(多样性/长度)、词性、依存、**sentiment analysis**、stylometry；③ **fact verification**（幻觉）。白盒侧=水印(post-hoc / inference-time green-list)。
- **🔑 对空白命题的决定性证据**：sentiment **确实**作为"语言模式"特征**被列入**，但原文对它的定性是**静态**——"LLMs tend to be **neutral by default** and lack emotional expression… ChatGPT expresses significantly **less negative emotion**"。**全篇无"情感随时间变化/轨迹/惯性/波动"任何条目**。→ **空白判断成立且更精确**：情感被用作*静态均值/极性*特征，**时间动力学维度在检测领域是空位**。
- **部分回答 TODO①**（HC3 是否含情感）：§3.1.3 转述 Guo 2023(HC3) 的人评发现——"human authors frequently use exclamation marks, question marks, ellipsis to express emotions, while LLMs more formal/structured"；§3.2.2 转述 HC3 词汇发现。→ **HC3 触碰的是静态情感 + 标点情感，非动力学**，空白不被推翻。HC3 规模确认：37,175 题，英+中，RoBERTa F1 99.79%(段)/98.43%(句)。
- **必须正视的混杂（§7.1）**：HC3 的"ChatGPT 情感中性"**部分是采集 artifact**——未给风格指令导致 ChatGPT 默认中性腔。→ 本研究比情感动力学时**必须控制 prompt 风格**，否则差异可能来自采集设置而非模型本性（与 Sandler 长度混杂并列为两大 confound）。
- **方法借鉴**：检测评估须报 **低 FPR 下的 TPR**（非仅 AUC/acc），尤其防误伤非母语者——H4 实验须遵此。改写攻击(Sadasivan 2023)可破多数检测器→ H4 须测情感动力学特征的抗改写性(R3)。

## E1 — Berríos et al. 2015（**H2"共存"操作化，必引**）

- **全引**：Berríos, R., Totterdell, P., Kellett, S. (2015). *Eliciting mixed emotions: a meta-analysis comparing models, types, and measures.* Front. Psychol. 6:428.（63 实验，random-effects，d=0.77 中-高，混合情绪可稳健诱发）
- **🔑 H2 的现成测量法**：混合情绪 = **co-activation of two oppositely-valenced emotions**（Larsen et al. 2001）。两种机制：(a) 同一刺激正负特征同时被知觉；(b) 正负刺激**快速交替**到足以维持双激活。→ 对应本研究 H2 两路：**同窗共存** + **快速负→正交替（=补偿回弹）**。
- **指标**：**Minimum Index (MIN)** = 一对相反价情感取**较小值**（min(pos,neg)）——co-activation 的标准强度度量；元分析发现用 MIN 的研究效应更小（保守），主观量表更大。→ 本研究可在效价时间序列上算 MIN 类指标度量"局部正负并存强度"。SIM(同时)/SEQ(序列) 之分须在标注中区分。

## G1 — Song 2022（**H3/H2"削弱包裹"机制，最对口中文括号直觉**）

- **全引**：Song, C. (2022). *Sentence-Final Particle vs. Sentence-Final Emoji: The Syntax-Pragmatics Interface in the Era of CMC.* In Proc. Grapholinguistics in the 21st Century 2022, Grapholinguistics and Its Applications Vol. 9, Brest: Fluxus Editions, 157–192. DOI 10.36824/2022-graf-song.
- **🔑 对核心直觉的形式化背书**：affective emoji（加语气的 emoji）在 CMC 中**功能上"wrap around"语言文本、从而 set its tone**；可推广到 meme、背景音乐等 CMC 特有 affective 元素。→ 这正是你"尖锐文字 + 可爱图削弱整体意思""句末括号削弱语气"的**句法-语用机制**：削弱成分作为**包裹算子**作用于核心文本的情感。生成句法 root-based 分析，把 CMC 数据拆成 非-CMC（文本）+ CMC（emoji 包裹）两部分。
- **缺口收窄**：原 proposal 标"中文括号削弱语气无专文"。Song 2022 虽以 emoji 为主例，但其"affective 包裹算子"框架**直接覆盖括号类削弱成分**（同属设定语气的 CMC 后缀）。→ 缺口从"无理论"降为"括号专项实证待补"；CNKI/万方仍建议补一手中文括号语料证据。

## C 簇 — 情感动力学指标精确公式（H1 心理学正引，补 UED 之外）

来源：Thompson et al. 2012《Everyday Emotional Experience of Adults with MDD》(J Abnorm Psychol 121(4):819–829, via PMC3624976) + Bos et al. 2018《Affective variability in depression: revisiting the inertia–instability paradox》(Br J Psychol, PubMed 30588616 摘要)。

- **Variability / Dispersion = SD**：情感时序的标准差。Bos 2018 关键结论：抑郁与负情感的 **SD(dispersion)** 关联，**而非** instability 或 inertia——三者高度重叠，**必须互相控制**才知谁在起作用。→ 本研究比人/AI 时同理：三指标须同时报并去重叠，不能只报一个。
- **Instability = MSSD**（mean square successive difference）= **相邻观测差的平方的均值** = mean[(x_{t+1} − x_t)²]。同时含**变异性 + 时间依赖**，故优于纯 SD（Ebner-Priemer 2009 / Jahng 2008）。RMSSD = √MSSD。
- **Inertia = AR(1)**：变量对自身滞后一阶的**自相关系数**；越高=情感越"黏"、越抗变（Suls 1998；Box-Jenkins）。**LLM 文本预期高 inertia / 低 MSSD**（情感黏、不波动）= H1 的核心可证伪预测。
- **Reactivity**：对情感事件的反应速度/强度（≈ UED 的 rise rate）；**Regulation/recovery** ≈ UED recovery rate。

## H 簇 — 效价测量底座（句内翻转处理）

- **H1 VADER**（Hutto & Gilbert 2014, ICWSM 8:216–225）：规则+词典，**5 条语法/句法规则**调情感强度——否定、程度副词、标点(!)、大写强调、**对比连词"but"**（"but"后情感**加权占主导**）。→ **"but"规则直接是"前半句负、后半句翻转"的现成处理**；社媒短文本之选，含 emoji/俚语强度。F1=0.96(>单个人类 0.84)。
- **H2 Taboada et al. 2011 SO-CAL**（Comp. Ling. 37(2):267–307, 3252 cit）：词典法 + **valence shifters**（否定、强化词 intensifiers）+ **contrastive conjunction 重加权**（"but/however"压低从句、抬高主句情感）。→ 与 VADER 互为印证，提供**子句级**情感翻转的成熟处理。中文须换中文情感词典(大连本体/HowNet) + 中文转折词("但是/不过/虽然")。

---

## 📊 H1/H2 可实现指标合表（供 P1 直接落地）

| 指标 | 公式（纯文本） | 捕捉 | 序列最小长度 | 主源 |
| --- | --- | --- | --- | --- |
| **SD / dispersion** | std(效价序列) | 整体离散（**已被 Sandler 占静态层，慎用单独**） | 短可算，~5+ | Bos 2018 |
| **MSSD** | mean[(x_{t+1}−x_t)²] | 不稳定性=变异+时间依赖 | ~5+ 点 | Thompson 2012 / Jahng 2008 |
| **RMSSD** | √MSSD | 同上，量纲还原 | ~5+ | 同上 |
| **AR(1) inertia** | corr(x_t, x_{t−1}) | 情感黏性（**LLM 预期偏高**） | ~10+ 点更稳 | Suls 1998 / Box-Jenkins |
| **过零率 ZCR** | 效价符号翻转次数 / 长度 | 句间正负切换密度 | 短可算 | 本研究自定义 |
| **UED variability** | v、a 的 SD 平均 | 话语级变异 | UED 原文 ≥50 turns（短对话待验证） | Hipson&Mohammad 2021 |
| **UED rise/recovery rate** | peak 距离 ÷ 上升(恢复)期词数 | **补偿回弹（H2 核心）** | 需 ≥1 完整 displacement | Hipson&Mohammad 2021 |
| **Minimum Index (MIN)** | min(pos_t, neg_t) 的窗口均值 | **局部正负共存强度（H2）** | 窗口级 | Larsen 2001 / Berríos 2015 |
| **缓和包裹共现率** | P(负价 span 后窗口内出现 称呼语/~/()/削弱emoji) | **补偿性平衡（H2）** | 窗口级 | 本研究（Song 2022 包裹框架背书） |
| **DFA 标度指数** | 去趋势波动分析标度 α | 情感序列长程相关/分形 | 较长序列 | MDPI 2024 D3（方法待手动核） |
| **困惑度 perplexity** | LM 下负平均对数似然 | 既有检测 baseline（H4 对照） | 文档级 | Tang 2024 |

> **落地优先**：MSSD / AR(1) / ZCR / MIN / 缓和包裹共现率 这 5 个在**短对话可算**，是 P1 首选；UED rise/recovery 与 DFA 需足够长度，作长文本或聚合后补充。

## 第三轮深读（2026-06-16，换 Springer 镜像/PLOS/John Benjamins 取全文 + 填中文缺口）

> 上一轮"仍未取全文"的 5 项**已全部解决**：D1 Reagan、D3 MDPI 同源方法、A2 HC3（经 Tang 二手）、E1/G1（二轮已取）。本轮另**填平中文括号缺口**（曾是 proposal 最弱子假设）。

### D1 — Reagan et al. 2016（情感弧完整方法，必引）

- **全引**：Reagan, A.J., Mitchell, L., Kiley, D., Danforth, C.M., Dodds, P.S. (2016). *The emotional arcs of stories are dominated by six basic shapes.* EPJ Data Science 5:31.（经 Springer HTML 镜像取全文）
- **方法精确**：**10,000 词滑窗**滑过全文 → 每窗用 **Hedonometer + labMT 词典**打效价分 → 得情感弧时间序列；三独立法交叉验证（**SVD** 找正交基模态、**Ward 层次聚类**、**SOM** 自组织映射）→ 六弧型（Rags-to-riches 升 / Tragedy 降 / Man-in-a-hole 降升 / Icarus 升降 / Cinderella 升降升 / Oedipus 降升降）。
- **🔑 短对话移植的硬限制（直接影响 H1 设计）**：原文明言"dictionary-based methods 在**单句上表现差于随机**，须用 rolling average 救"，且窗口设 **10,000 词为可靠下限**。→ **Reagan 法不能直接用于 QQ 短消息**；本研究 H1 在短对话上**应优先 UED（词级 + 短窗）+ MSSD/AR(1)/ZCR**，Reagan 六弧型留给长文本/聚合语料。
- **方法亮点可借**：**null-model 检验**——把书打乱成"word salad"/2-gram"nonsense"重跑，确认真实情感弧不是统计 artifact。→ **本研究比人/AI 时应照做 null 对照**（打乱句序后差异应消失），证明信号来自时序结构而非词频。

### D3 同源 — 句级情感 DFA/分形（H1 的长程相关工具）

两篇同源（MDPI 原文 403，取到同方法的 PLOS 全文 + 元数据）：

- **Hernández-Pérez et al. (2024)**, *Correlations and Fractality in Sentence-Level Sentiment Analysis Based on VADER for Literary Texts*, Information 15(11):698。**句级 VADER 序列 + DFA + Higuchi 分形维**。（DOI 已确认，正文 403，方法见下同源篇）
- **Yang, Gu & Yang (2016)**, *Long-Range Correlations in Sentence Series from A Story of the Stone*, PLOS ONE 11(9):e0162423（**中文**《红楼梦》句长序列，全文取得）。
- **DFA 公式（可实现）**：① 序列 X={x₁…x_N} → 积分成 profile Y（累加去均值）；② profile 切窗口 w，每窗 q 阶多项式拟合去趋势（本文 q=2）；③ 残差标准差 F(w) ∝ w^H，**Hurst 指数 H** 由 log F(w)–log w 斜率估。H=0.5 随机、H>0.5 持续正相关、H<0.5 反持续。
- **🔑 两条方法警告（本研究必须遵守）**：① **结构稳定性检验必做**——该文实证"<1% 的噪声记录"就能把 H 从 0.575 抬到 0.87 得出"两作者不同"的**假结论**，清洗后差异消失；切成段分别算 H 才可信。→ 本研究算 DFA 前必清洗 + 分段验证。② **长度门槛高**：可靠 DFA 需 ~3×10⁴ 量级；短序列须用专门工具（文末引 Qi & Yang 2011《Hurst exponents for short time series》、Pan 2014 短序列标度——**短对话做分形的备选**）。

### G1 缺口填平 — Li & Lin 2023（**中文括号削弱语气专文，曾是最弱子假设**）

- **全引**：Li, J. (Jinan Univ.) & Lin, Y. (Uppsala Univ.) (2023). *Parentheses used as pragmatic strategies in Chinese online socialization.* Pragmatics and Society 14(3):442–460. DOI 10.1075/ps.20058.li.
- **🔑 直接命中核心直觉**：数据取自 **Weibo / WeChat / QQ**，Cyberpragmatics 框架。结论——括号作为**语用策略**，把括号当与 emoji/graphicon 一体的不可分单元，功能含：补充信息、隔离话题、**对比括号内外文本内容**、**间接传达意图**、**relieving communicative awkwardness（缓解交际尴尬）**、**调整对话者期待**。→ 你说的"句末括号削弱语气"**有了同平台(QQ!)、peer-reviewed 的专文背书**；缺口从"无专文"彻底关闭。
- **附带红利——它的参考文献补齐了我此前缺的奠基正引**：Brown & Levinson 1987《Politeness》(E5 待补已解)、Leech 1983《Principles of Pragmatics》、Goffman 1967《Interaction Ritual》(面子)、Yus 2011《Cyberpragmatics》(F/G 区理论框架)、Haugh 2016《'Just kidding': Teasing & non-serious intent》(直接对应"用削弱标记声明'我开玩笑的'")、Gu 1990《Politeness in modern Chinese》、**Luo 2018《Variations of Online Punctuation: Bracket+》(沈阳大学学报)**——中文括号的另一专项。

---

## ✅ 受限全文清理结果（全部闭环）

| 项 | 状态 | 结论 |
| --- | --- | --- |
| D1 Reagan 2016 | ✅ 取得全文 | 方法=10k 词窗+labMT+SVD/Ward/SOM；**短对话不适用**，用 UED 替代 |
| D3 MDPI 2024 + PLOS 同源 | ✅ 同方法全文 | DFA/Hurst 公式齐；**结构稳定性检验 + 长度门槛**两警告入库 |
| E1 Berríos 2015 | ✅（二轮） | MIN 指数 = co-activation 测量 |
| G1 Song 2022 + **Li&Lin 2023** | ✅ + **缺口填平** | 括号/emoji 削弱语气：QQ 平台 peer-reviewed 专文 |
| A2 HC3 §3-4 | ✅（经 Tang 二手） | 情感发现属静态层，不推翻动力学空白 |
| 奠基正引（B&L 1987 等） | ✅ 由 Li&Lin 参考文献补齐 | 见上 |

> **P0 文献深读阶段实质收尾**：核心命题（情感时间动力学作人/AI 判别签名 = 真空白）已坐实；H1/H2/H3 全部有现成测量法 + 奠基正引；三大方法警告（长度门槛、结构稳定性、null 对照）已登记。剩纯属可选增强：MAGE/M4GT 语料许可核查、新模型(GPT-4/Claude)复核 Sandler。

---

## 第四轮：P2 前针对性反向证伪检索（2026-06-16）

> 目的：专门去**证伪**空白主张——找有没有人已做过「会话内情感时间动力学」×「人/AI 判别」的交集。5 路 preprint/search + 引用方向追踪 + 边缘命中核全文。**结论：核心交集仍未被命中，但发现一篇关键『方法范式先例』，需把空白主张再收窄一格。**

### 🔑 关键发现 —— Teodorescu et al. 2023（EMNLP，**方法范式先例，必须正面持有**）

- **全引**：Teodorescu, D., Cheng, T., Fyshe, A., Mohammad, S.M. (2023). *Language and Mental Health: Measures of Emotion Dynamics from Text as Linguistic Biosocial Markers.* EMNLP 2023. arXiv:2310.17369. CC-BY-NC-SA。（与 D2 Hipson&Mohammad 同组 Mohammad）
- **做了什么**：**首次**把 UED（话语情感动力学）指标从推文算出来，**作为判别标记**区分**心理健康诊断组**（control vs ADHD/MDD/PTSD/bipolar/OCD）。逐条发现：control 组 average valence 更高；**valence variability** control 显著低于多数诊断组；**rise/recovery rate** 也显著有别于 control。
- **🔑 对空白主张的精确冲击**：它证明「**文本 UED 指标 → 群体判别特征**」这条**方法范式已经存在并发表**——SD/variability/rise/recovery 当判别器，不是本研究首创。**但它的判别目标是『心理健康诊断』，不是『人 vs AI』。**
- **→ 空白再收窄一格（诚实修正）**：本研究的新颖点**不在"用 UED 当判别特征"**（Teodorescu 已做），**而在两处**：① **判别对象** = 人 vs AI 文本（Teodorescu 是人内部的临床分组）；② **机制假设** = 补偿性平衡/co-activation 作为人类签名（H2，Teodorescu 无此构念）。**"方法全新"的说法不能要了；"应用到人/AI 判别 + 补偿平衡机制"才是真新点。**

### 反向检索未命中交集（证伪未果＝空白成立的正面证据）

5 路检索 + 引用追踪所见，按类归档，**无一篇同时满足『会话内时间动力学』+『人/AI 判别』**：

- **纯检测（无情感动力学）**：RAID 2024、Petrillo 2024（可解释 LLM 分类人/AI 句）、Ghiurău&Popescu 2024（合成内容检测综述）、Sood 2025《The Disappearing Author: Linguistic & Cognitive Markers of AI-Generated Communication》——AI 语言/认知标记，但标记是静态词汇/认知，**非情感时间序列**。
- **纯情感动力学（判别目标≠人/AI）**：Teodorescu 2023（→心理健康）、Hipson&Mohammad 2021（→电影角色，D2）、CANDOR 语料 2023（自然对话多模态，无人/AI 对比）。
- **纯人/AI 情感（静态层，反复命中同几篇）**：Sandler 2024（第 1/5 路又各命中一次）、Moreno-Ortiz & García-Gámez 2025《Emotion in Context: Human and AI Perspectives...Severance》(Corpus Pragmatics，PDF 超大未取全文，据题/摘为**剧本情感语料**人/AI 视角对比，非时间动力学判别)。
- **LLM 情感分析的不稳定性（另一回事）**：Herrera-Poyatos 2025（LLM 做情感分析时自身输出的 variability，是"LLM 当工具的可靠性"，**不是**"AI 文本的情感动力学特征"）——易撞车的近义词，已排除。

### 本轮净结论（写入 PROPOSAL）

1. **核心交集空白成立**：针对性证伪后仍无人占「会话内情感时间动力学 × 人/AI 判别」交集。
2. **但空白边界从 P0 版再收窄**：「UED 当判别特征」已被 Teodorescu 2023 确立（临床域），故本研究**不能再宣称方法新颖**；真正新点 = **(判别对象=人/AI) + (机制=补偿性平衡/co-activation)**。
3. **检索局限（不夸大"空白"）**：本轮+P0 共约 10 路检索 + 2 跳引用追踪，但**非穷尽**；preprint 库对 2025-2026 最新稿、中文库、非 arXiv 渠道覆盖有限。Moreno-Ortiz 2025 全文未取（PDF 超限），其与本命题的距离仅据摘要判断。**空白主张应表述为"已尽合理检索未发现"，非"绝对不存在"。**
