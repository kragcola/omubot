# P0 批量阅读清单 —— Affective Divergence 研究

> 配套 [PROPOSAL.md](PROPOSAL.md)。状态：**待批量阅读 · 2026-06-16 汇编**
>
> **来源说明**：经多轮学术库检索（arXiv preprint / OpenAlex-S2 search / 按相关性与引用排序），未设硬年份下限——奠基工作与最新进展并收。
>
> **新旧辨别原则**（应用户要求，防过时数据）：每条标 `状态` 列：
> - 🟢 奠基 = 领域基石，方法/构念仍立得住，时间久≠过时
> - 🔵 最新 = 2024–2026 前沿，反映当前 LLM 现状（LLM 迭代快，检测/情感能力类**必须**用新的）
> - 🟡 待核 = 元数据或相关性存疑，读时先验证
> - ⚠️ 噪声 = 检索误命中（标题/年份对不上主题），**不读**，仅登记避免重复检索
>
> **关键纪律**：凡涉及「LLM 当前能力/检测效果」的结论，**以 🔵 最新为准**，🟢 奠基仅用于方法与构念奠基，不用其年代久远的 LLM 性能数字。引用前一律核对 URL 可达 + 元数据（年份/作者/venue）。

---

## A. 核心命题：人 vs AI 文本差异（最高优先级）

| # | 标题 | 年/出处/引用 | 状态 | URL | 为何读 |
|---|------|------|------|-----|--------|
| A1 | **A Linguistic Comparison between Human and ChatGPT-Generated Conversations** | 2024 arXiv 2401.16587 · 10 cit | 🔵最新·**最对口** | https://arxiv.org/abs/2401.16587 | 直接做人/ChatGPT 对话语言学对比；**先读它定"情感动力学作判别特征"的空白边界** |
| A2 | **How Close is ChatGPT to Human Experts? (HC3)** | 2023 arXiv 2301.07597 | 🔵最新 | https://arxiv.org/abs/2301.07597 | HC3 语料原文 + 语言学分析；核 §9 TODO①「是否已含情感维度」 |
| A3 | HC3 Plus: A Semantic-Invariant Human ChatGPT Comparison Corpus | 2023 arXiv 2309.02731 | 🔵最新 | https://arxiv.org/abs/2309.02731 | HC3 升级版语料，扩数据源 |
| A4 | **Artificial Intelligence and the Psychology of Human Connection** | 2026 Perspectives on Psych Science · 8 cit | 🔵最新 | https://doi.org/10.1177/17456916251404394 | Boyd & Markowitz；人机连接的心理学视角，可能含情感表达差异 |
| A5 | Is ChatGPT Equipped with Emotional Dialogue Capabilities? | 2023 arXiv 2304.09582 · 39 cit | 🔵最新 | https://arxiv.org/abs/2304.09582 | AI 情感对话能力评估，侧证"AI 情感与人不同" |
| A6 | Mirages: On Anthropomorphism in Dialogue Systems | 2023 EMNLP · 48 cit | 🔵最新 | https://aclanthology.org/2023.emnlp-main.290.pdf | 对话系统拟人化批判，框定"像人"的边界 |
| A7 | Humans versus AI: whether and why we prefer human-created artwork | 2023 Cognitive Research · 193 cit | 🟡待核 | https://cognitiveresearchjournal.springeropen.com/counter/pdf/10.1186/s41235-023-00499-6 | 人/AI 产物偏好，类比迁移；非文本但机制相关 |

## B. LLM 文本检测（定位"情感动力学是空白"）

| # | 标题 | 年/出处/引用 | 状态 | URL | 为何读 |
|---|------|------|------|-----|--------|
| B1 | **The Science of Detecting LLM-Generated Text** | 2024 CACM · 109 cit（arXiv 2303.07205 同源） | 🔵最新·综述 | https://dl.acm.org/doi/pdf/10.1145/3624725 | 权威综述，**用于定位现有特征清单中情感的空位**（§9 TODO④） |
| B2 | A Survey on LLM-Generated Text Detection: Necessity, Methods, Future | 2023 arXiv 2310.14724 · 30 cit | 🔵最新·综述 | https://arxiv.org/pdf/2310.14724 | 第二份检测综述，交叉验证特征清单 |
| B3 | A Survey on Detection of LLMs-Generated Content | 2024 EMNLP Findings · 12 cit | 🔵最新·综述 | https://aclanthology.org/2024.findings-emnlp.572.pdf | 更新的检测综述（2024），覆盖更晚模型 |
| B4 | **Detecting AI-Generated Text: Factors Influencing Detectability** | 2025 JAIR · 37 cit | 🔵最新 | https://www.jair.org/index.php/jair/article/download/16665/27172 | Fraser et al.，2025 最新，哪些因素影响可检测性 |
| B5 | DetectGPT 系 / Fast-DetectGPT (Conditional Probability Curvature) | 2023 arXiv 2310.05130 · 19 cit | 🟢奠基·方法 | https://arxiv.org/pdf/2310.05130 | zero-shot perplexity-curvature baseline，H4 的对照检测器 |
| B6 | DetectLLM: Log Rank Information for Zero-Shot Detection | 2023 EMNLP Findings · 47 cit | 🟢奠基·方法 | https://aclanthology.org/2023.findings-emnlp.827.pdf | 另一类 zero-shot baseline |
| B7 | Spotting LLMs With Binoculars | 2024 arXiv 2401.12070 · 16 cit | 🔵最新·方法 | https://arxiv.org/pdf/2401.12070 | 2024 强 baseline，H4 增量须超过它才有意义 |
| B8 | **MAGE: Machine-generated Text Detection in the Wild** | 2024 ACL · 48 cit | 🔵最新·语料 | https://aclanthology.org/2024.acl-long.3.pdf | 多模型多领域语料，§9 TODO⑥可得性核查目标 |
| B9 | MGTBench: Benchmarking Machine-Generated Text Detection | 2024 ACM · 31 cit | 🔵最新·benchmark | https://dl.acm.org/doi/pdf/10.1145/3658644.3670344 | 检测 benchmark 框架，实验对齐用 |
| B10 | Paraphrasing evades detectors, but retrieval is effective defense | 2023 arXiv 2303.13408 · 89 cit | 🔵最新·鲁棒性 | https://arxiv.org/pdf/2303.13408 | 改写攻击——情感动力学特征是否抗改写？R3 相关 |
| B11 | Simple techniques to bypass GenAI text detectors (education) | 2024 IJETHE · 69 cit | 🔵最新 | https://link.springer.com/content/pdf/10.1186/s41239-024-00487-w.pdf | 规避手段实证，鲁棒性边界 |
| B12 | Linguistic features of AI mis/disinformation & detection limits of LLMs | 2025 Nature Communications · 3 cit | 🔵最新 | https://www.nature.com/articles/s41467-025-67145-1_reference.pdf | 2025 Nature Comms，AI 文本语言特征 + 检测极限 |
| B13 | On the Possibilities of AI-Generated Text Detection | 2023 arXiv 2304.04736 · 51 cit | 🔵最新·理论 | https://arxiv.org/pdf/2304.04736 | 可检测性理论上界，框定 H4 天花板 |

## C. 情感动力学 / 变异性 / 惯性（H1 方法基石）

| # | 标题 | 年/出处/引用 | 状态 | URL | 为何读 |
|---|------|------|------|-----|--------|
| C1 | **Emotional Inertia and Psychological Maladjustment** | 2010 Psych Science · 736 cit | 🟢奠基 | https://lirias.kuleuven.be/handle/123456789/257518 | Kuppens；inertia=AR(1) 构念源头（本仓 climate 已引同支） |
| C2 | **Modeling Affect Dynamics: State of the Art & Future Challenges** | 2015 Emotion Review · 226 cit | 🟢奠基·综述 | （UvA pure 记录，需找 PDF） https://handle.uba.uva.nl/personal/pure/en/publications/modeling-affect-dynamics(469cd314-69cc-4021-927b-6c3b5300d7ba).html | Hamaker；affect dynamics 指标全景（§9 TODO③公式来源） |
| C3 | Affective variability in depression: inertia–instability paradox | 2018 Br J Psychology · 128 cit | 🟢奠基 | https://onlinelibrary.wiley.com/doi/pdfdirect/10.1111/bjop.12372 | 区分 inertia vs instability vs variability，定义辨析关键 |
| C4 | Neuroticism may not reflect emotional variability | 2020 PNAS · 101 cit | 🔵最新 | https://www.pnas.org/content/pnas/117/17/9270.full.pdf | variability 指标的混杂警示，方法严谨性 |
| C5 | Changing dynamics: Time-varying autoregressive models (GAM) | 2016 Psych Methods · 210 cit | 🟢奠基·方法 | （Bringmann et al.）https://pure.rug.nl/ws/files/77012803/ | 时变 AR 模型，对话内情感动态可借 |
| C6 | Predicting Depression From Language-Based Emotion Dynamics (FB/Twitter) | 2018 JMIR · 148 cit | 🔵参考 | https://www.jmir.org/2018/5/e168/PDF | **把 emotion dynamics 用到文本语言**的先例——方法直接可迁移 |
| C7 | Discrete- vs Continuous-Time Modeling of Unequally Spaced ESM Data | 2017 Front Psych · 149 cit | 🟢奠基·方法 | https://www.frontiersin.org/articles/10.3389/fpsyg.2017.01849/pdf | 不等间隔时序建模（对话回合间隔不均，必读） |

## D. 情感弧 / 情感轨迹 / 分形（H1 测量工具）

| # | 标题 | 年/出处/引用 | 状态 | URL | 为何读 |
|---|------|------|------|-----|--------|
| D1 | **The emotional arcs of stories are dominated by six basic shapes** | 2016 EPJ Data Sci · 258 cit | 🟢奠基 | https://cdanfort.w3.uvm.edu/research/2016-reagan-epj.pdf | Reagan；情感弧测量法（§9 TODO②能否移植到短对话） |
| D2 | **Emotion dynamics in movie dialogues** | PLOS One · — | 🔵最新·**对口** | https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0256153 | 把 emotion dynamics 用到**对话**，最接近本研究单位 |
| D3 | Correlations and Fractality in Sentence-Level Sentiment Analysis (DFA/HFD) | 2024 MDPI Information 15(11):698 | 🔵最新·方法 | https://www.mdpi.com/2078-2489/15/11/698 | **句级**情感序列 DFA/HFD——直接是 H1 工具 |
| D4 | MultiSentimentArcs: coherence in multimodal sentiment for long narratives | 2024 Front Comp Sci | 🔵最新·方法 | https://www.frontiersin.org/journals/computer-science/articles/10.3389/fcomp.2024.1444549/full | 多模态情感弧 + 跨模态距离度量（H3 可借） |
| D5 | Sentiment Dynamics of Success: Fractal Scaling of Story Arcs | 2021 NLP4DH · — | 🔵参考 | https://aclanthology.org/2021.nlp4dh-1.1.pdf | Hurst 指数度量情感弧自相似/连贯 |
| D6 | The Fractality of Sentiment Arcs for Literary Quality (Nobel) | jdmdh · — | 🔵参考 | https://jdmdh.episciences.org/11677/pdf | 自适应滤波 + 分形，情感序列稳健测量 |
| D7 | Generalized word shift graphs | 2021 EPJ Data Sci · 73 cit | 🟢奠基·方法 | https://epjdatascience.springeropen.com/track/pdf/10.1140/epjds/s13688-021-00260-3 | 解释两文本情感差异来源的可视化/归因法 |
| D8 | An Evaluation Framework for Emotional Support in Language Models | 2025 arXiv 2511.09003 | 🔵最新·**前沿** | https://arxiv.org/html/2511.09003v1 | **dynamic trajectory** 评估 LLM 情感——直接邻接命题 |

## E. 补偿性平衡：混合情绪 / 语用缓和 / 礼貌面子（H2 核心）

| # | 标题 | 年/出处/引用 | 状态 | URL | 为何读 |
|---|------|------|------|-----|--------|
| E1 | **Eliciting mixed emotions: a meta-analysis comparing models/types/measures** | 2015 Front Psych · 182 cit | 🟢奠基·综述 | https://www.frontiersin.org/articles/10.3389/fpsyg.2015.00428/pdf | 混合情绪测量法元分析——H2"局部正负共存"的测量基石 |
| E2 | The two faces of Janus: Anxiety and enjoyment (mixed emotions) | 2014 SSLLT · 1569 cit | 🟢奠基 | https://pressto.amu.edu.pl/index.php/ssllt/article/download/3941/3990 | 同一表达正负情绪共存的代表实证 |
| E3 | Levels of Valence | 2013 Front Psych · 134 cit | 🟢奠基·理论 | https://www.frontiersin.org/articles/10.3389/fpsyg.2013.00261/pdf | Scherer 组；valence 的层次，效价测量概念前提 |
| E4 | The Distancing-Embracing model (enjoyment of negative emotions) | 2017 Behav Brain Sci · 387 cit | 🟡参考 | （Cambridge core PDF） | 负情绪被"包裹"消费的机制，类比 H2 缓和 |
| E5 | **Brown & Levinson《Politeness: Some universals in language use》** | 1987 专著 | 🟢奠基·必引 | （需另找，检索未直出 PDF；经典书） | 面子/FTA/缓和理论基石，H2 语用补偿的理论框架 |
| E6 | Sociolinguistic Study of Pet Names among Couples (Nsukka) | 2020 JLTR · 5 cit | 🔵参考 | https://doi.org/10.17507/jltr.1105.09 | 宠称/称呼语的关系维护功能（"亲爱的"例的直接对应） |
| E7 | Overaccommodation in eldercare (address terms/accommodation) | 2016 J Multiling Multicult Dev · 11 cit | 🟡参考 | https://dr.ntu.edu.sg/bitstream/10356/80739/1/ | 言语顺应理论中的称呼语，关系调节 |

> **E 区缺口**：Brown & Levinson（E5）与 Lakoff/Leech 礼貌原则、hedging（Hyland）等经典须在 P0 手动补全 PDF/正引——学术库 preprint 检索对老牌专著命中弱（见 §C 检索局限）。

## F. emoji-文本不一致 / 反讽削弱 / 多模态（H3 核心）

| # | 标题 | 年/出处/引用 | 状态 | URL | 为何读 |
|---|------|------|------|-----|--------|
| F1 | Reasoning with Multimodal Sarcastic Tweets (Cross-Modality Contrast) | 2020 ACL · 157 cit | 🟢奠基 | https://www.aclweb.org/anthology/2020.acl-main.349.pdf | 图文情感对比建模——H3"尖锐文字+可爱图"的方法原型 |
| F2 | Towards Multi-Modal Sarcasm Detection via Hierarchical Congruity | 2022 EMNLP · 94 cit | 🔵参考 | https://aclanthology.org/2022.emnlp-main.333.pdf | 图文一致性/不一致建模 |
| F3 | The unbearable hurtfulness of sarcasm | 2022 Expert Sys Appl · 76 cit | 🔵参考 | https://www.sciencedirect.com/science/article/pii/S0957417421016870 | 反讽的伤害性——尖锐被削弱前后的对比 |
| F4 | Affective and Contextual Embedding for Sarcasm Detection | 2020 COLING · 108 cit | 🟢参考 | https://www.aclweb.org/anthology/2020.coling-main.20.pdf | 情感嵌入做反讽，特征工程参考 |
| F5 | A Multi-View Interactive Approach for Multimodal Sarcasm (knowledge) | 2024 Appl Sci · 22 cit | 🔵最新 | https://www.mdpi.com/2076-3417/14/5/2146/pdf | 2024 多模态反讽最新法 |
| F6 | Communicative functions of emoji sequences on Sina Weibo | First Monday · — | 🔵最新·**中文** | https://firstmonday.org/ojs/index.php/fm/article/download/9413/7610 | 中文 emoji 序列的言语行为功能，H3 中文语境 |

## G. 中文 CMC 语气软化 / 句末符号（H2 标点子类，括号削弱语气）

| # | 标题 | 年/出处/引用 | 状态 | URL | 为何读 |
|---|------|------|------|-----|--------|
| G1 | **Sentence-final particle vs. sentence-final emoji: syntax-pragmatics in CMC** | 2022 Song · — | 🔵最新·**最对口** | https://www.juliosong.com/doc/Song2022Grafematik.pdf | 句末符号/emoji 的语气功能（Weibo）——**最接近"句末括号削弱语气"** |
| G2 | The Pragmatic Functions of the tilde "~" in China's Social Media | IJLLT · — | 🔵最新·中文 | https://al-kindipublishers.org/index.php/ijllt/article/view/4548 | 波浪号语气软化（已在 proposal 引 Xu&Xia，此为补充） |
| G3 | Digital tildes ("∼") may convey more (WeChat) | 2023 Language and Semiotic Studies | 🔵最新·中文 | https://www.degruyter.com/document/doi/10.1515/lass-2023-0009/pdf | 波浪号创新用法，proposal 已引 |
| G4 | "Write oneself into being" – Ha (哈) as pragmatic marker on WeChat | 2022 Pragmatics · — | 🔵最新·中文 | https://www.degruyterbrill.com/document/doi/10.1515/pr-2022-0035/html | 语气词"哈"的人际软化功能 |
| G5 | Communicative functions of emoji sequences on Sina Weibo | （= F6） | 🔵中文 | （见 F6） | 跨列引用 |

> **G 区缺口**：中文"**括号/半括号削弱语气**"专文**未直接检到**（proposal §9 已登记）。G1（句末符号）最接近。P0 须用 CNKI/中文库专门补检"括号 语气 网络语言 弹幕"。

## H. 情感测量工具 / 词典 / ERC（贯穿 H1–H3 的实现底座）

| # | 标题 | 年/出处/引用 | 状态 | URL | 为何读 |
|---|------|------|------|-----|--------|
| H1 | **VADER: Parsimonious Rule-Based Sentiment for Social Media** | 2014 ICWSM · 5732 cit | 🟢奠基·工具 | https://ojs.aaai.org/index.php/ICWSM/article/download/14550/14399 | 社媒情感词典基线，含**强度+否定+程度副词**处理 |
| H2 | **Lexicon-Based Methods for Sentiment Analysis** | 2011 Comp Ling · 3252 cit | 🟢奠基·**关键** | http://www.mitpressjournals.org/doi/pdf/10.1162/COLI_a_00049 | Taboada；含 **contrast/valence shifters（"但是"翻转）**——直接对应"句内情感翻转" |
| H3 | Sentiment Analysis: An Overview from Linguistics | 2015 Annu Rev Ling · 402 cit | 🟢奠基·综述 | https://www.annualreviews.org/doi/pdf/10.1146/annurev-linguistics-011415-040518 | 语言学视角情感综述 |
| H4 | DialogueRNN: Attentive RNN for Emotion Detection in Conversations | 2019 AAAI · 811 cit | 🟢奠基·ERC | https://ojs.aaai.org/index.php/AAAI/article/view/4657 | 对话情感识别代表模型，模型法效价底座 |
| H5 | SemEval-2019 Task 3: EmoContext Contextual Emotion Detection | 2019 · 288 cit | 🟢奠基·任务 | https://www.aclweb.org/anthology/S19-2005.pdf | 上下文情感检测任务/数据 |
| H6 | A survey on sentiment analysis methods, applications, challenges | 2022 AI Review · 1421 cit | 🔵综述 | https://link.springer.com/content/pdf/10.1007/s10462-022-10144-1.pdf | 情感分析方法总览，选型参考 |
| H7 | MELD / IEMOCAP（多模态对话情感语料） | — | 🟢奠基·语料 | （P0 补正引：MELD ACL2019 / IEMOCAP 2008） | 带情感标注的人类对话语料候选 |

---

## 检索局限与新旧辨别备注（诚实记录）

1. **preprint 库对老牌专著命中弱**：Brown & Levinson 1987、Leech、Hyland hedging 等礼貌/语用经典没从 arXiv 直出，需手动补 PDF/正引（E5 已标）。**不能因检索没命中就当它不存在**。
2. **2026/2027 标注的若干结果疑似噪声**：末轮检索出现一批标 2026–2027、主题为护理/外汇/城市规划的条目，与本题无关，**判为 ⚠️ 噪声未纳入**——提示按时间排序时会混入元数据错误项，引用前必核。
3. **LLM 能力类用最新、方法/构念用奠基**：B 区检测效果、A 区 LLM 情感能力 → 以 2024–2026 为准（模型迭代快，2023 数字可能已过时）；C/D/E/H 的测量方法与心理构念 → 奠基工作仍有效，时间久不等于过时。
4. **URL 状态**：表中 URL 取自检索返回，P0 阅读前**逐条验证可达**；C2（UvA pure 记录页非直链 PDF）、E4/E5（需另找 PDF）已标注待补。
5. **中文括号削弱语气**：核心直觉之一仍缺专文支撑（G 区缺口），P0 必须走中文库（CNKI/万方）专门补检，否则该子假设证据偏弱。

## P0 阅读优先级（建议顺序）
1. **先读定空白**：A1 → A2 → B1 → B3（确认"情感动力学作判别特征"到底是不是空白，避免命题被既有工作覆盖）。
2. **再读拿方法**：C1/C2/C3（dynamics 指标定义）→ D1/D2/D3（情感弧 + 句级 DFA 落到对话）→ H2/H1（效价测量底座 + 句内翻转）。
3. **补 H2/H3 构念**：E1/E5 → F1 → G1。
4. **核语料可得**：A2(HC3)、B8(MAGE)、B9(MGTBench)、H7。
