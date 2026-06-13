# 分句器:专名 / 省略号 / 叠标点保护 + LLM 换行硬边界(2026-06-11)

## 背景

烤群日志实证两类不拟人分句缺陷:

1. 省略号 `……`(两个 U+2026)被切成 `…` / `…` 两条气泡。
2. 乐队专名 `BanG Dream! MyGO!!!!!` 被叹号切碎成 `BanG Dream!` / `MyGO!` / `!` / `!` / `!!那个乐队…` 一串孤立气泡。

## 根因(三路调研收敛同一结论)

- **社会语言学**:连续标点 `！！！` / `……` 是一个韵律/情感原子(数字时代的语调音量),按单字符切=把一个音节切碎;专名是 lexical TCU,内部绝不切;真人"发送动作=句末标点"。
- **成熟项目(pySBD / HarvestText)**:正确范式是 mask→split→restore——不该断的标点先占位,切完还原;终止符按贪婪连续块整体匹配,不逐字符。
- **前沿论文**:MWE 立场论文(Savary et al. 2019)背书"专名靠词典而非模型";Stephanie2(2026)点名机械等间隔切=unnatural pacing。

代码根因:[services/llm/segmentation.py](../../services/llm/segmentation.py) 的 `_natural_boundary_indices` 逐字符遇分隔符就切,`_protected_spans` 只保护 CQ / URL / ASCII token / 颜文字,**不保护连续标点 run、省略号、含内部标点的专名**。`_natural_merge_segments` 还会随机吞掉 LLM 的换行边界(LLM 辅助分段从未生效)。流式分句器 [services/llm/streaming_segmenter.py](../../services/llm/streaming_segmenter.py) 同模式缺陷(默认 OFF)。

## 用户决策(2026-06-11)

| 维度 | 决策 |
|------|------|
| 分句架构 | 规则为主 + LLM 辅助(换行硬边界) |
| 专名保护 | 启发式(标点紧贴 ASCII 词尾视为词内) + 小词典(从 charpack context_label 抽取) |
| 叠标点 | 整体保留不折叠(`！！！` 原样,保留情感强度) |

## 改动清单(旧 → 新)

| 关注点 | 旧行为 | 新行为 | 位置 |
|--------|--------|--------|------|
| 省略号 `……` | 切成 `…`/`…` | 整体保护,run 内部不切、run 后可切 | `segmentation._PUNCT_RUN_RE` → `_protected_spans` |
| 叠标点 `！！！` | 切成孤立 `！` | 整体保留为一个气泡 | 同上 |
| 专名 `MyGO!!!!!` | 切碎 | ASCII 词尾标点黏附 + 专名词典 mask | `_ASCII_TRAILING_PUNCT_RE` / `_PROPER_NOUN_PHRASES` / `_proper_noun_re()` |
| LLM 换行 | 被随机合并吞掉 | 硬边界,逐行独立处理,合并不跨行 | `natural_split` split("\n") 重构 |
| ASCII 专名间空格 | 合并时 `BanG Dream!MyGO!!!!!` 粘连 | 两 latin/digit run 之间保留单空格 | `_natural_merge_segments` glue 逻辑 |
| 流式分句(默认 OFF) | 同上全部缺陷 | 镜像修复 + run 跨 delta 推迟切分 | `streaming_segmenter._protected_spans` / `_find_boundary` |
| persona 引导 | 无分段指引 | 加"换行分气泡 + 专名/省略号/叠标点整体不拆"规则 | `config/persona/fengxiaomeng-v2/source.md` |

## D1 同模式扫描

grep `_protected_spans` / `_safe_boundary` / 分隔符集,确认全仓只有两处分句器:`segmentation.py`(默认路径,生产 100% 走)、`streaming_segmenter.py`(`streaming_segment_enabled=False` 默认 OFF)。两处均已修复并各带回归测试。legacy `segment_reply` / `/debug split` 路径有独立的 `_adjust_cut_for_repeated_punctuation`,不受影响。

## 测试映射

| 缺陷 | 回归测试 |
|------|----------|
| `……` 不拆 | `test_natural_split.py::test_ellipsis_run_is_never_split_inside` / `test_streaming_segmenter.py::test_ellipsis_run_not_split_in_stream` |
| `！！！` 整体 | `test_repeated_enders_stay_one_unit` / `test_repeated_ender_run_split_across_deltas_stays_whole` |
| 专名不拆 | `test_proper_noun_with_internal_punctuation_not_split` / `test_proper_noun_with_punctuation_protected_in_stream` |
| ASCII 词尾强调 | `test_ascii_token_trailing_emphasis_is_kept_whole` |
| LLM 换行硬边界 | `test_llm_newlines_are_hard_bubble_boundaries` / `test_within_line_still_merges_under_high_strength` |
| 合并保留空格 | `test_merge_keeps_space_between_ascii_proper_nouns` |

## 验证证据

- `uv run pytest`(全量):2642 passed, 17 skipped, 0 failed。
- ruff / pyright:改动文件全绿(`segmentation.py` / `streaming_segmenter.py` / 两个测试文件)。
- (待补)线上实测:部署后烤群发含省略号 / 专名 / 叠标点的回复,确认气泡不再被拆。

## 回滚路径

`git revert` 本次 commit 即可。改动纯粹在两个分句器 + 一个 persona source + 测试,无 schema / 无依赖 / 无 NapCat 变更。persona 改动需重新 import + hot-reload(见部署步骤);代码改动需 rebuild bot。

## 已知遗留

- streaming_segmenter 的 run-deferral 对"单字符逐个到达的 SSE"(每个 delta 仅 1 char)无法保护首个 lone ender(无法预知它会被重复)——但真实 SSE token 多字符,且该路径默认 OFF,影响可忽略。
- 专名词典 `_PROPER_NOUN_PHRASES` 是静态常量,新增乐队/角色/曲名需手动补;空格分隔的多词专名(`Ave Mujica`)无需录入(现有 ASCII-token 空格逻辑已保护)。
