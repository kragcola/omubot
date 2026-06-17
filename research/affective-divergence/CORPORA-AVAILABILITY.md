# 人/AI 平行语料可得性与许可核查 —— MAGE / M4GT-Bench / RAID

> 配套 [PROPOSAL.md](PROPOSAL.md) §4.2、[READING-LIST.md](READING-LIST.md) B8/B9。状态：**核查完成 · 2026-06-16**
> 用途：为 P1/P2 实验选 baseline 语料；本研究做**人 vs AI 情感时间动力学**对比，需人/AI 平行、多模型多领域、许可允许研究使用。
> 核查方式：官方 GitHub / HuggingFace dataset card / PyPI / ACL Anthology 全文，逐条取证。

---

## 结论速览

| 语料 | 规模 | 许可 | 可得性 | 对本研究适配度 |
|---|---|---|---|---|
| **MAGE** | 436,606 条（train 319k / val 56.8k / test 60.7k） | **CC BY 4.0** | ✅ HuggingFace `yaful/MAGE`（554 MB，Parquet，免登录）+ GitHub `yafuly/MAGE` | ⭐⭐⭐ **首选**：规模适中、纯文本带 label+src、易加载 |
| **M4GT-Bench** | 多语言多领域多生成器（MGT），6 任务 | **CC BY 4.0** | ✅ GitHub `mbzuai-nlp/m4gt-bench` + arXiv 2402.11175 | ⭐⭐⭐ **多语言关键**：含**中文**等多语，补 MAGE 英文为主之短 |
| **RAID** | **>10M 文档**（HF：raid ~7.42M + raid_test 672k） | **MIT**（代码/基准）；构成语料含各源 | ✅ HuggingFace `liamdugan/raid` + GitHub `liamdugan/raid` + PyPI `raid-bench` | ⭐⭐ **鲁棒性/对抗专用**：超大、带 11 类对抗攻击，P2 抗改写(R3)用；**test split 标签隐藏**（防泄漏） |

> 三者**均 ACL 2024**、均开放可得、许可均允许研究使用（CC BY 4.0 / MIT，需署名引用）。**没有可得性或许可障碍**。

---

## MAGE（首选基线语料）

- **全引**：Li, Y., Li, Q., Cui, L., Bi, W., Wang, Z., Wang, L., Yang, L., Shi, S., Zhang, Y. (2024). *MAGE: Machine-generated Text Detection in the Wild.* ACL 2024. arXiv:2305.13242（原名 "Deepfake Text Detection in the Wild"）。
- **可得**：HuggingFace `yaful/MAGE`（436,606 行 / 554 MB，自动转 Parquet，dataset viewer 可直接浏览，免登录下载）；GitHub `yafuly/MAGE`（224★）。
- **许可**：**CC BY 4.0**（GitHub README license badge 实证）——允许研究/商用，需署名。
- **结构**：三列 `text` / `label`（0/1）/ `src`（322 个来源类，如 `cmv_human`）。train 319k、val 56.8k、test 60.7k。人类写作来自多领域（CMV/Reddit、新闻、故事、科学写作等），机器文本来自多个 LLM。
- **对本研究**：规模适中、加载简单、人/AI 标注清晰，**P1 可行性实验首选**。`src` 列可控领域协变量（呼应 Sandler 的长度/话题混杂警告）。**注意**：以**英文**为主。

## M4GT-Bench（多语言补充，含中文）

- **全引**：Wang, Y., Mansurov, J., Ivanov, P., Su, J., Shelmanov, A., Tsvigun, A., et al. (2024). *M4GT-Bench: Evaluation Benchmark for Black-Box Machine-Generated Text Detection.* ACL 2024. arXiv:2402.11175。
- **可得**：GitHub `mbzuai-nlp/m4gt-bench`；OpenReview / HF papers 页齐全。
- **许可**：**CC BY 4.0**（arXiv HTML 版页首明确标注）。
- **结构**：**多语言 × 多领域 × 多生成器**的 MGT 语料；三类任务——(1) 单语/多语二分类人vsMGT；(2) multi-way 判定由哪个生成器所生；(3) 边界检测（human→machine 切换点）。
- **对本研究**：**中文实验的关键来源**（MAGE 偏英文，本仓 bot 是中文场景，中文人/AI 对比须靠它 + HC3 中文子集）。multi-generator 设计利于 H4 跨模型稳健性验证。

## RAID（鲁棒性 / 对抗专用，P2 用）

- **全引**：Dugan, L., Hwang, A., Trhlík, F., Ludan, J.M., Zhu, A., Xu, H., Ippolito, D., Callison-Burch, C. (2024). *RAID: A Shared Benchmark for Robust Evaluation of Machine-Generated Text Detectors.* ACL 2024. arXiv:2405.07940。
- **可得**：HuggingFace `liamdugan/raid`（HF viewer：`raid` ~7.42M 行 + `raid_test` 672k 行；首 5GB 可在线浏览）；GitHub `liamdugan/raid`（171★）；PyPI `raid-bench`（`pip install raid-bench`）。
- **许可**：**MIT License**（PyPI 包元数据实证，Copyright 2024- Liam Dugan）——最宽松。
- **结构**：**>10M 文档**，列含 `model`（多 LLM）/ `decoding` / `repetition_penalty` / `attack`（11 类对抗攻击：改写/同义替换/拼写扰动等）/ `domain`（含 abstracts/news 等）/ `generation`。**test split 标签隐藏**（走 leaderboard 提交，防过拟合泄漏）。
- **对本研究**：体量最大、唯一系统带**对抗攻击维度**。**P2 验证情感动力学特征的抗改写性（直接打 proposal R3 风险 + Tang 2024 的 paraphrase-attack 威胁）**——可看改写攻击是否抹掉人/AI 的情感动力学差异。P1 不必用（太大），P2/鲁棒性阶段用。

---

## 选型建议（写入 P1/P2 计划）

1. **P1 可行性**：用 **MAGE**（英文，规模适中，易加载）先验证 H1——人/AI 的 MSSD/AR(1)/ZCR 是否有可见差异 + 控长度。
2. **中文线**：用 **M4GT-Bench 中文子集 + HC3 中文**做中文人/AI 对比（本仓 bot 中文场景的对应）。
3. **P2 鲁棒性**：用 **RAID** 的 `attack` 维度测情感动力学特征抗改写性（R3）。
4. 三者许可（CC BY 4.0 / MIT）均允许研究使用，**唯一义务=署名引用**，无障碍。

## 仍待实验期确认（非可得性障碍）
- 各语料的**人类侧文本是否带足够长的对话/多句结构**支持时间动力学指标（MAGE/RAID 多为单文档段落，UED 的话语序列需足够句数——与 Reagan 10k 词、DFA 3×10⁴ 门槛同源顾虑，P1 先验证）。
- M4GT-Bench 中文子集的**具体生成模型与年份**（影响 R3 模型时效性）。
