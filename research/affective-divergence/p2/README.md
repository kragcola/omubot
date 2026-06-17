# P2 离线中文情感动力学标注管线

> 状态：**已写好待数据 · 2026-06-17** ｜ 配套 [PROPOSAL.md](../PROPOSAL.md)、[LITERATURE-NOTES.md](../LITERATURE-NOTES.md) 指标合表
> 代码：[annotate_corpus.py](annotate_corpus.py) ｜ 环境：复用 [../p1/.venv](../p1/)（+snownlp/cnsenti/jieba）
> 性质：**纯离线分析**，读 `storage/topic_corpus.db`（话题块采集产物），不碰 bot、不联网。

## 它做什么

读话题块采集库 → 按 **(话题块, 说话人)** 重建中文效价时间序列 → 算 P0 笔记里那套 H1/H2 指标 → 人 vs AI 对比（效应量 + null 检验）。

**H1 时间动力学**（每个 unit 的效价序列）：SD / MSSD / RMSSD / AR1 / ZCR。
**H2 补偿性平衡**：
- **MIN co-activation**：每条消息 `min(正词数, 负词数)`（cnsenti / DUTIR 大连本体），度量"同一句正负共存"。
- **softener-on-negative rate**：负价消息中带**语气削弱**（句末括号/半括号、`~`、称呼语"亲爱的/呀/啦"、削弱 emoji `(小声/(bushi/(狗头`）的比例——Song 2022 包裹算子 + Li&Lin 2023 QQ 括号专文背书。

## 情感工具（均离线词典法）

- **snownlp**：连续效价 0–1 → 映射 [−1,1]，做主效价序列。
- **cnsenti（DUTIR 大连情感本体）**：正/负词计数 → MIN 共存。
- 中文分句用正则（`。！？!?…\n`），NLTK punkt 在本机被 SSL 墙、且本就只管英文。

## 怎么跑

```bash
# 前提：bot 侧已开 topic_block.corpus_capture_enabled 并攒了数据
cd research/affective-divergence/p2
../p1/.venv/bin/python annotate_corpus.py \
    --db ../../../storage/topic_corpus.db \
    --min-msgs 4 \
    --report results_p2.json
```

数据不足（人/AI unit 各 <5）时打印 `NOT ENOUGH DATA` 并写 `status: insufficient_data`，不报错。

## 与 P1 的关系（务必延续 P1 的教训）

- **null 检验是标配**：管线自带"打乱句序"对照。P1 已证 SD/MSSD/ZCR 的人/AI 差异多为**静态分布**（打乱不变），唯 AR1 测到时间顺序结构。读结果时**先看 null**：若某指标打乱后差异不变，它就不是"时间动力学"信号。
- **核心赌注在 H2**：P1 显示纯 H1 偏弱，命题更独特处是 H2（co-activation + 缓和回弹）。本管线把 H2 一等公民化（MIN + softener rate）。
- **体裁/模型已对齐**：语料是中文真实群聊 + emu（deepseek-v4，当前模型），补上了 P1 用 HC3(GPT-3.5)/RAID(英文摘要) 的两个硬伤。

## 已验证（合成数据 smoke）

构造 8 human + 6 ai unit 跑通全路径：H1 五指标 + null + H2（MIN δ=−0.625 p=.028；human 负价消息 45% 带削弱、AI 负价 0 条故 rate=null）；称呼语/括号/`~` 检测器命中；同一说话人哈希稳定可链。ruff `All checks passed!`。**合成数据仅验管线，非真实结论。**

## 局限 / 待办

- 词典法句级噪声大（Reagan 2016 警告）；真实数据上若 H1 仍弱，P2 可换 ERC 模型或 NRC-VAD 中文版。
- softener 检测是**规则**，需在真实数据上抽样核准确率/召回（误报如正常句末"啦/呀"）。
- DFA/UED rise-recovery 未纳入（需更长序列，本管线先做短对话可算的 5 指标 + H2）。
- 群消息短、单 unit 句数可能不足 4 → `--min-msgs` 可调，或聚合同一人跨块消息（待真实数据定）。
