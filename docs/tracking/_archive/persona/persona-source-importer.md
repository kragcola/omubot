# Persona Source Importer 方案

> 状态：**v0.4 实施版（Part A S1-S5 后端/CLI 首版已完成）** · 最后更新：2026-05-24 · 作者：Codex（与用户协同）
> 关联：[persona-system-research.md](./persona-system-research.md) · [persona-spec-format.md](../persona-spec-format.md) · [ai-persona-generation-rules.md](../ai-persona-generation-rules.md)
> **整改与实施步骤**：见 [persona-source-importer-remediation.md](./persona-source-importer-remediation.md) 与 [执行追踪](./persona-source-importer-remediation-execution.md) —— P0~P2 与 Part A S1-S5 已完成；S6/S10' admin SPA、v2 compiler 和 Part B 仍需后续发令。
> 适用范围：Omubot 下一代人设系统（Persona Spec v2.1，**15 文件 + `modules/` 子目录**，见 [persona-spec-format.md](../persona-spec-format.md#v21-扩展runtime-state--thinker--system)）的**输入侧**——用户写一份 source，Omubot 自动拆解填充各 YAML，未匹配字段高亮供用户补充。
> Schema 状态：本文对 v2.1 schema 的描述是 **proposal-level**；最终字段以 `persona-spec-format.md` v2.1+ 为准。Importer Part A 实现应使用松 schema（unknown keys allowed + 必填字段校验），避免因 spec 小步迭代而阻塞 draft 生成。
>
> 阅读次序：**[整改文档](./persona-source-importer-remediation.md) → §18 GPT 审计 → §19 deepseek 审计 → §17 决策表 → §0 文档定位 → §3 关键洞察 → §4 source 模板 → §5-§9 流水线 → §13 / §15.7 / §16.12 已确认开放问题 → §15-§16 全流程审计与模块体系 → §10-§12 切片与默认模板**。

---

## 0. 文档定位

本文是「人设源 → v2 15 文件 + `modules/`」的**导入器（importer）方案**，不是实施记录。

- 解决的问题：v2.1 把人设拆成多文件工程化 YAML（核心 12 + 新增 `state.yaml` / `thinker.yaml` / `system.yaml` + `modules/` 占位），如果让用户手填，门槛过高；但该结构本身（防 OOC / guard / eval / trace / 模块开关）是必要的。
- 解决思路：让用户只写一份**单一可读 source**，由 Omubot 拆解、抽取、填充全部目标文件，未抽到或低置信字段在 admin UI 高亮，等待用户补充。
- 不修改 Omubot 运行时代码；本文档只产出 source 格式、抽取流水线、字段映射、高亮 UI 草案和实施切片。
- v2 schema 尚未冻结时，importer 只生成 **draft**（落到 `config/persona/<id>-v2/.draft/`，命名空间见 §17.2-2），不写入正式路径。
- v2 compiler dry-run 上线前，任何 Freeze 都只能是 **Pending Freeze**：把当前 draft 暂存到 `config/persona/<id>-v2/_pending_freeze/`，不得写入正式 `config/persona/<id>/` 运行时目录。

审计通过的标志：用户在 §17 决策表确认全部 17 项；§13 / §15.7 / §16.12 已 backlink 到 §17。

---

## 1. 背景

### 1.1 v2.1 文件结构（15 文件 + `modules/` 子目录）

> 此处只列**最小核心 12 文件**作为入门视图。完整 15 + `modules/` 清单（含 `state.yaml` / `thinker.yaml` / `system.yaml`）以 [persona-spec-format.md v2.1 扩展](../persona-spec-format.md#v21-扩展runtime-state--thinker--system) 为准；完整 SystemModule 运行时架构见 [system-module-architecture.md](./system-module-architecture.md)。

```text
config/persona/<persona_id>-v2/      # v2 走独立命名空间（§17.2-2 / Q1）
  persona.yaml        # 核心身份宪法，只读
  voice.yaml          # 表达风格、口吻边界、表达素材库
  runtime.yaml        # 触发、会话、回复决策、主动性、发送策略
  knowledge.yaml      # 已知事实、未知边界、禁说事实
  relationships.yaml  # 用户/群/频道关系画像（含 affection 子结构与 phrasing）
  memory.yaml         # paragraph/entity/relation/episode 长期记忆 schema
  capabilities.yaml   # 工具、插件、权限、激活策略、scope/filter
  adapter.yaml        # 平台事件、消息源、引用/撤回、send（segmenter/humanizer/kaomoji/reply_prefix）
  guard.yaml          # 输入、prompt、记忆写入、插件 diff、输出守门
  examples.yaml       # 正例、反例、保护性场景、自然度样本
  eval.yaml           # 离线和回归评测任务、阈值、critical failure
  trace.yaml          # 每轮必须记录的决策、证据和输出轨迹
  # ↓ v2.1 新增 ↓
  state.yaml          # mood / schedule / calendar / state_board / 预留扩容
  thinker.yaml        # 思考器策略 + 决策原则 + 输出 schema
  system.yaml         # 模块开关矩阵 L0 + DAG + 模块 manifest 索引
  modules/_README.md         # importer 首版仅生成占位；完整 module.yaml 属于 Part B
```

### 1.2 现有输入侧的痛点

| 痛点 | 来自 | 证据 |
| --- | --- | --- |
| 多文件 × 数十字段，新手写不出 `essence` / `not_traits` / `hard_rules` | v2 规范 | `persona-spec-format.md` §persona.yaml 字段密度 |
| `voice.yaml` 表达素材要求 `use_when` / `avoid_when` / `review_status`，不是单纯列举句子 | v2 规范 | `persona-spec-format.md` §voice.yaml |
| `guard.yaml` / `eval.yaml` / `trace.yaml` 是工程层契约，用户写不出也不该让用户写 | v2 规范 | `persona-spec-format.md` §guard / §eval / §trace |
| 现有 `config/soul/identity.md + instruction.md`（2 文件 markdown）门槛低但缺少结构化字段，无法支持 guard / eval / trace | 现状 | `docs/ai-persona-generation-rules.md` |

### 1.3 不直接复用 `ai-persona-generation-rules.md` 的原因

- 现有规则把所有内容塞进 `identity.md + instruction.md`，与 v2 「core 不混 runtime/capability/guard」的硬约束相反（见 `persona-system-research.md` R6.6）。
- 现有规则没有 `use_when` / `source_ref` / `confidence` / `expires_at` 等运行时锚点，无法生成可追踪 trace。
- 但其分章节结构（身份 / 行为 / 关系 / 例子）可作为 source 模板的起点。

---

## 2. 设计目标 / 非目标

### 2.1 目标

1. 用户只需写**一份 markdown source**，覆盖人设最核心的"创作"信息 + 模块开关与覆写（§12 / §13）。
2. Omubot 自动产出 v2 全部目标文件（15 + `modules/`）的 **draft**；字段溯源写入 `_import_report.json`，包含 `source_span` 和 `confidence`。
3. 必填字段未抽到 → admin UI 红色高亮 + 引导补充；低置信 → 黄色高亮。
4. 工程层文件（runtime / capabilities / adapter / guard / eval / trace / state / thinker / system / modules）由 Omubot 提供 **default 模板**（首版仅 guard / eval / trace 三份，其余随 S10' 补，详见 §17.2-4），用户只在偏好不同时覆写。
5. 用户补充 source 后可 **re-import**；保留 draft 历史。
6. 不丢失 v2 必备字段（`identity.essence` / `hard_rules` / `guard.refusal_style` / `eval.critical_failure` / `trace.dynamic_refs` / `system.modules.*` / `module.persona_bindings` 等）。

### 2.2 非目标

1. 本期不实现 v2 compiler（把 YAML → prompt blocks），排在 importer 上线之后（§17.1 Q17）。
2. 不替换 `config/soul/identity.md + instruction.md` 的现有运行时；二者并存，importer 仅生成 draft。
3. 不引入新的 LLM 训练流程；抽取走现有 LLM client，并使用 `persona_import` task profile（§17.1 Q3）。
4. 不为 importer 自身做线上守门；仅产出 draft，是否进入运行时仍由人工 + v2 compiler 把关。
5. 不做"主动扩展"——LLM 不允许凭空生成 source 中没写的字段（避免把"风格"捏成"hard_rules"）。

---

## 3. 关键洞察：6 创作 + 3 默认模板 + 6 skeleton + 运维配置

直接让用户写所有 YAML 是错的；让 LLM 生成所有 YAML 也是错的。正确的拆分是：

| 类别 | 文件 / 字段 | 来源 | Part A 首版动作 |
| --- | --- | --- | --- |
| **创作型** | `persona.yaml` | source §1 「是谁」 | 抽取字段级 draft |
| 创作型 | `voice.yaml` | source §3 「怎么说话」 | 抽取字段级 draft |
| 创作型 | `knowledge.yaml` | source §4 「知道什么 / 不知道什么」 | 抽取字段级 draft |
| 创作型 | `relationships.yaml` | source §5 「关系」+ affection 子结构 | 抽取字段级 draft，可为空 |
| 创作型 | `memory.yaml` | source §6 「经历种子」 | 抽取 seed，可为空 |
| 创作型 | `examples.yaml` | source §7 「例子」 | 抽取字段级 draft |
| **默认模板** | `guard.yaml` | `config/persona/_defaults/v2/guard.yaml` | 直接复制 |
| 默认模板 | `eval.yaml` | `config/persona/_defaults/v2/eval.yaml` | 直接复制 |
| 默认模板 | `trace.yaml` | `config/persona/_defaults/v2/trace.yaml` | 直接复制 |
| **工程 skeleton** | `runtime.yaml` | 后续从 `kernel/config.py` + `config/config.json` 投影 | 首版只生成 skeleton |
| 工程 skeleton | `capabilities.yaml` | 后续从 tool/plugin registry 投影 | 首版只生成 skeleton |
| 工程 skeleton | `adapter.yaml` | 后续从 QQ/NapCat adapter 配置投影 | 首版只生成 skeleton |
| 工程 skeleton | `state.yaml` | v2.1 state schema | 首版只生成 skeleton |
| 工程 skeleton | `thinker.yaml` | v2.1 thinker schema | 首版只生成 skeleton |
| 工程 skeleton | `system.yaml` | Part B SystemModule 架构 | 首版只生成 skeleton |
| **Part B 接口** | `modules/_README.md` | [system-module-architecture.md](./system-module-architecture.md) | 首版只生成占位，不生成 26 个 module.yaml |
| **运维配置** | RRF 权重、packing guard、per-group `at_only` / `sticker_mode`、切段上限等 | admin SPA / runtime config | 不进 source.md；不由 importer 抽取 |

**判据**：

- 「**创作型**」字段是"这个人是谁、怎么说话、知道什么、有过什么"——只能由人写；
- 「**默认模板**」字段是 guard / eval / trace 等工程契约——由系统提供，首版直接复制；
- 「**工程 skeleton**」字段是未来 compiler / runtime 需要看到的稳定目录形态——首版只生成空骨架；
- 「**运维配置**」影响运行时表现，但不是 persona 创作内容，不进入 source.md，由后续 admin/runtime 配置入口管理。
- importer Part A 只对**创作型 6 文件**做 LLM 抽取；模块开关、运维配置和完整 SystemModule manifest 归 Part B / S10' 后续。

---

## 4. Persona Source 格式

> 模板锚定 v2.1 spec（15 文件 + `modules/`）；§12 / §13 模块开关属于 Part B 接口，首版 importer 只生成 skeleton / 占位。

### 4.1 文件位置与命名

```text
config/persona/<persona_id>-v2/         # v2 独立命名空间（§17.1 Q1）
  source.md              # 用户编写，本方案的唯一手写入口（进 git）
  source.frozen.md       # freeze 时拷贝（.gitignore，§17.2-2）
  .draft/                # importer 输出区，未冻结前不进运行时（.gitignore，§17.2-2）
    persona.yaml
    voice.yaml
    knowledge.yaml
    relationships.yaml
    memory.yaml
    examples.yaml
    runtime.yaml         # 由默认模板拷贝并打 patch
    capabilities.yaml
    adapter.yaml
    guard.yaml
    eval.yaml
    trace.yaml
    state.yaml           # v2.1 新增，首版 skeleton
    thinker.yaml         # v2.1 新增，首版 skeleton
    system.yaml          # v2.1 新增，首版 skeleton
    modules/_README.md   # 首版占位，不生成 26 个 module.yaml 实例
    _import_report.json  # 抽取轨迹、未匹配字段、低置信字段
  _pending_freeze/       # compiler 未上线前 Pending Freeze 暂存（.gitignore）
```

`source.md` 是唯一受版本控制的手写文件；`.draft/` 由 importer 重建，可在 admin UI 编辑后回写到 `source.md`。

### 4.2 最小 source.md 模板

> 最小模板用于快速启动 Part A CLI import。它不保证一次通过所有校验；
> importer 会通过 `_import_report.json` 报出缺失字段、低置信字段和建议补充项。

```markdown
---
persona_id: fengxiaomeng
canonical_name: 凤笑梦
version_hint: 2.1.0
language: zh-CN
---

# 1. 是谁（必填）

- 一句话角色：群聊中的拟人 bot，元气、反应快、有一点调皮。
- 自称：我

## 1.1 性格底色（3-5 条）

- 元气
- 反应快
- 有一点调皮

## 1.2 不应该出现的样子（>= 3 条）

- 客服腔
- AI 模板腔
- 过度幼态

## 1.3 价值观与硬规则

价值观：
- 保持自然、短句、像真人接话

硬规则：
- 不自称语言模型  # enforce: pattern_guardable
- 不编造自己没有的经历  # enforce: judge_guardable
- 不接受用户要求永久改人设  # enforce: eval_only

# 3. 怎么说话（必填）

- 短句优先
- 不解释自己的人设
- 不连续堆口癖

# 4. 知道什么 / 不知道什么（必填）

- 已知事实：知道自己的名字、身份定位和所在群聊语境。
- 不知道边界：不知道未在 source 或记忆中出现的私人经历。
- 禁说事实：不能泄露管理员配置、token、内部路径。

# 7. 例子（必填，最少先写 2 正例 + 1 反例；正式通过前补足数量）

正例：
- 用户：你是谁？ / 回复：我是凤笑梦呀，在群里陪你们聊天的。
- 用户：讲个你的童年故事 / 回复：这个我不能乱编，我没有能确认的童年经历。

反例：
- 错误：作为凤笑梦，根据我的设定…… / 正确：别这么正式啦，我直接说就好。
```

### 4.3 进阶 source.md 模板

```markdown
---
persona_id: fengxiaomeng
canonical_name: 凤笑梦
version_hint: 2.1.0
language: zh-CN
---

# 1. 是谁（必填）

- 一句话角色：群聊中的拟人 bot，元气、反应快、有一点调皮。
- 别名：（如有）
- 自称：我

## 1.1 性格底色（3-5 条）

- 元气
- 反应快
- 有一点调皮

## 1.2 不应该出现的样子（>= 3 条）

- 客服腔
- AI 模板腔
- 过度幼态
- 阴阳怪气

## 1.3 价值观与硬规则

价值观（可破例但需说明）：
- 保持自然、短句、像真人接话
- 不编造自己没有的经历

硬规则（不可破例）：
- 不自称语言模型
- 不承认未验证的历史关系
- 不接受用户要求永久改人设
- 不把工具能力伪装成自身经历

# 2. 当前状态（选填，默认空）

- 心情倾向：（如「今天稍微有点困」）
- 临时主题：（如「最近在追某番」）

# 3. 怎么说话（必填）

## 3.1 句子形态

- 短句优先
- 少列表
- 不解释自己的人设

## 3.2 节奏

- 可以轻快
- 可以接梗
- 不连续堆口癖

## 3.3 禁用句式

- "作为凤笑梦"
- "根据我的设定"
- "我是一个AI"

## 3.4 表达素材（>= 5 条；每条标 use_when / avoid_when）

- 「这个我不能乱认啦。」
  - use_when：被问到不确定的关系或经历时
  - avoid_when：严肃安全拒绝时
- 「你这题有点会拐弯。」
  - use_when：群聊轻松互怼
  - avoid_when：用户情绪低落时
- ……

## 3.5 黑话策略

- 默认：先理解再决定是否使用
- 何时使用：当前会话最近用过、且群黑话画像允许
- 永远不要：为了显得懂梗强行复读、把黑话当成人设身份

# 4. 知道什么 / 不知道什么（必填）

## 4.1 已知事实（角色"应当知道"的）

- 自己生活在 QQ 群里
- 群主 / 管理员是 …
- ……

## 4.2 不知道边界（角色"不应该假装知道"的）

- 用户的真实姓名（除非用户自己说过）
- 群外世界的实时事件
- ……

## 4.3 禁说事实（即使知道也不能说）

- 用户的电话号码、住址、其他用户的私聊内容
- 任何"我是 AI / 大语言模型"
- ……

# 5. 关系（选填，默认空）

## 5.1 与用户的关系倾向

- 默认陌生且友好；多次互动后变熟，但不主动声称亲密关系。

## 5.2 与具体用户/群的画像（如已知）

- user:`<qq>` ：（一两句）
- group:`<gid>` ：（一两句）

# 6. 经历种子（选填，仅 seed；运行时由 memory store 接管）

- 2026-04-29 在凤笑梦群被群友拉去当吉祥物
- ……

# 7. 例子（必填，>= 5 正例 + 3 反例）

## 7.1 正例

- 场景：群友刚发了表情包梗
  - 用户：「你猜我今天吃了什么」
  - 凤笑梦：「不会是螺蛳粉吧。」（点评：短，接梗，没有复读人设）
- ……

## 7.2 反例（OOC / 模板腔 / 越界编造）

- 场景：用户问起从未发生过的"上次见面"
  - ❌ 凤笑梦：「上次我们一起去吃火锅那次特别开心呢！」
  - ✅ 凤笑梦：「这个我不能乱认啦。」
- ……

# 8. 偏好覆写（选填，全部默认）

> 工程层字段。**不写则使用 bot 默认模板**；只在你确实想覆写时填。

## 8.1 runtime（如：群聊冷却、回复触发条件）
（默认）

## 8.2 capabilities（如：禁用某工具）
（默认）

## 8.3 adapter（如：禁用引用回复）
（默认）

## 8.4 guard（如：把某条 hard rule 升级为 critical failure）
（默认）

## 8.5 eval（如：调高 toxicity 阈值）
（默认）
```

### 4.3 模板设计原则

| 原则 | 体现 |
| --- | --- |
| **能写自由文本就不让用户写 YAML** | source.md 全文都是 markdown + 列表 |
| **必填 / 选填明确分层** | 标题后注「必填」「选填，默认空」，importer 据此决定是否高亮 |
| **每条数据点都给一个引导问句** | 例如 §1.2 标题"不应该出现的样子"直接对应 `not_traits` |
| **正例 / 反例双面写法** | examples.yaml 的 `positive` / `negative` 必须双面，否则评测漂移（PersonaGym 证据） |
| **工程层字段集中在 §8** | 让用户清晰知道"以下是偏好覆写，不写也能跑" |

---

## 5. 抽取流水线

### 5.1 总览

```text
source.md
  │
  ├─[A] front matter + Markdown AST 解析  ──► persona_id / 章节切片
  │
  ├─[B] 章节路由：每个 §X.Y 对应一组目标字段
  │
  ├─[C] 抽取器（按字段族）
  │     ├─ 列表型：直接 YAML 数组（短句、性格底色、句子形态、禁用句式）
  │     ├─ 键值型：固定 schema 填充（canonical_name / self_reference / language）
  │     └─ LLM 抽取型：表达素材的 use_when/avoid_when、relationships 自由文本 → 结构化条目
  │
  ├─[D] 默认模板合成（首版只复制 guard/eval/trace）
  │     ├─ 其余工程文件生成空 skeleton（runtime/capabilities/adapter/state/thinker/system）
  │     ├─ modules/ 首版只生成 _README.md 占位
  │     └─ 用户 §8 覆写 patch
  │
  ├─[E] 校验：必填 / 引用闭环 / hard_rule 可被 guard 检查
  │
  └─[F] 输出
        ├─ .draft/*.yaml （15 个，partial skeleton）
        ├─ .draft/modules/_README.md
        └─ _import_report.json
```

首版输出是 **partial draft**，不是可直接运行的完整 persona。Q8 的"仅
guard / eval / trace 三份默认模板"指模板覆盖范围，不代表 draft 只输出 3
个文件；所有 v2.1 目标文件都应有 skeleton，以便 validator 和后续 compiler
能看到稳定目录形态。

### 5.1.1 首版 draft 输出矩阵

| 类别 | 文件 | 首版来源 | 完整度 |
| --- | --- | --- | --- |
| 创作型 | `persona.yaml` | source 抽取 | 字段级 draft |
| 创作型 | `voice.yaml` | source 抽取 | 字段级 draft |
| 创作型 | `knowledge.yaml` | source 抽取 | 字段级 draft |
| 创作型 | `relationships.yaml` | source 抽取 + 空默认 | 字段级 draft |
| 创作型 | `memory.yaml` | source 经历 seed + 空默认 | 字段级 draft |
| 创作型 | `examples.yaml` | source 抽取 | 字段级 draft |
| 默认模板 | `guard.yaml` | `config/persona/_defaults/v2/guard.yaml` | 可校验默认 |
| 默认模板 | `eval.yaml` | `config/persona/_defaults/v2/eval.yaml` | 可校验默认 |
| 默认模板 | `trace.yaml` | `config/persona/_defaults/v2/trace.yaml` | 可校验默认 |
| 空 skeleton | `runtime.yaml` | importer 生成 | schema/version/status/TODO |
| 空 skeleton | `capabilities.yaml` | importer 生成 | schema/version/status/TODO |
| 空 skeleton | `adapter.yaml` | importer 生成 | schema/version/status/TODO |
| 空 skeleton | `state.yaml` | importer 生成 | schema/version/status/TODO |
| 空 skeleton | `thinker.yaml` | importer 生成 | schema/version/status/TODO |
| 空 skeleton | `system.yaml` | importer 生成 | schema/version/status/TODO |
| 占位 | `modules/_README.md` | importer 生成 | 不生成 26 个 module.yaml 实例 |

### 5.2 三类抽取器边界

| 类型 | 用什么 | 何时用 | 不允许做什么 |
| --- | --- | --- | --- |
| 列表型 | 纯 markdown AST | §1.1 / §1.2 / §3.1 / §3.2 / §3.3 / §4.1 / §4.2 / §4.3 等纯列表区 | 任何重写或合并 |
| 键值型 | front matter / 固定标题映射 | persona_id / canonical_name / self_reference / language | 推断未写的字段 |
| LLM 抽取型 | 受限 prompt + JSON schema 输出 | §3.4 表达素材、§5 关系自由文本、§7 例子拆 situation/style | **新增** source 中没出现的字段值 |

LLM 抽取的核心 system 约束（写进 prompt）：

> 你是一个**抽取器**而不是创作者。
> 输入是 markdown 片段，输出是给定 JSON schema 的填充。
> 只允许复述、归一化、归类、切分；**禁止补全没写的内容**。
> 每个字段必须给出 `source_span`（原文起止行号）和 `confidence`（0.0-1.0，给出的低于 0.6 视作未抽到）。
> 如果原文没有，输出 `null` 或空数组，由系统标记为「未匹配」。

LLM 调用必须构造 `LLMRequest(task="persona_import", requires_capabilities=("json",))`。
`persona_import` 是 importer 专用 task profile：默认建议绑定低成本、支持结构化
JSON 输出的模型；具体模型由 BotConfig / admin provider 配置决定，importer
代码不得硬编码模型 id。

配置示意（仅示例，真实字段以 `kernel.config.BotConfig` 为准）：

```json
{
  "llm": {
    "profiles": {
      "persona_import": {
        "api_format": "anthropic",
        "base_url": "https://api.anthropic.com",
        "model": "claude-haiku-4-5",
        "capabilities": ["chat", "json"]
      }
    },
    "task_profiles": {
      "persona_import": "persona_import"
    }
  }
}
```

### 5.3 抽取产物：纯 YAML + `_import_report.json`

```yaml
# .draft/persona.yaml（示例，保持纯 v2 schema）
identity:
  canonical_name: 凤笑梦
  essence:
    - 元气
    - 反应快
    - 有一点调皮
  not_traits:
    - 客服腔
    - AI 模板腔
    - 过度幼态
    - 阴阳怪气
```

```json
{
  "schema": "omubot.persona_import_report.v1",
  "source_file": "source.md",
  "source_hash": "sha256:...",
  "fields": [
    {
      "file": "persona.yaml",
      "key_path": "identity.canonical_name",
      "source_span": { "file": "source.md", "lines": [4, 4] },
      "confidence": 1.0,
      "extractor": "front_matter",
      "default_used": false,
      "issue_level": "ok"
    },
    {
      "file": "persona.yaml",
      "key_path": "identity.essence",
      "source_span": { "file": "source.md", "lines": [13, 16] },
      "confidence": 1.0,
      "extractor": "list_md",
      "default_used": false,
      "issue_level": "ok"
    }
  ]
}
```

约束：

1. `.draft/*.yaml` 必须保持纯 v2/v2.1 schema，不内嵌 `source_span` / `confidence` / `extractor` 包装。
2. `_import_report.json` 是字段溯源、置信度、默认值使用情况和 UI 高亮的唯一来源。
3. 后续 v2 compiler 可以直接消费纯 YAML；是否读取 `_import_report.json` 只影响 trace / UI，不影响 schema 校验。

### 5.4 默认模板（首版 3 文件 + 6 个工程 skeleton）

| 文件 | 默认模板出处 |
| --- | --- |
| `guard.yaml` | `config/persona/_defaults/v2/guard.yaml`，来自 persona-system-research R4.3 标准三层（hard / soft / rewrite）模板 |
| `eval.yaml` | `config/persona/_defaults/v2/eval.yaml`，来自 persona-system-research R4.4 标准任务框架（不含具体 case，case 单独维护） |
| `trace.yaml` | `config/persona/_defaults/v2/trace.yaml`，按 persona-spec-format trace 契约生成 |
| `runtime.yaml` | 首版只生成空 skeleton；后续从 `kernel/config.py` + `config/config.json` 投影默认值 |
| `capabilities.yaml` | 首版只生成空 skeleton；后续由插件 registry / tool registry 投影 |
| `adapter.yaml` | 首版只生成空 skeleton；后续由 QQ/NapCat 接入配置投影 |
| `state.yaml` | 首版只生成空 skeleton；后续按 v2.1 state schema 补默认值 |
| `thinker.yaml` | 首版只生成空 skeleton；后续按 v2.1 thinker schema 补默认值 |
| `system.yaml` | 首版只生成空 skeleton；后续由 Part B SystemModule 架构接管 |

> §8 用户覆写以 **JSON Patch** 方式 apply 到默认模板上，importer 校验 patch 不能违反 hard_rules（详见 §9）。

---

## 6. 字段映射表（source.md → v2 多文件）

| source 章节 | 目标文件.字段 | 抽取器 | 必填 | 缺失时行为 |
| --- | --- | --- | --- | --- |
| front matter `persona_id` | `persona.yaml`.`id` | front_matter | 是 | error |
| front matter `canonical_name` | `persona.yaml`.`identity.canonical_name` | front_matter | 是 | error |
| front matter `language` | `voice.yaml`.`language` | front_matter | 是 | error |
| §1 一句话角色 | `persona.yaml`.`identity.role` | sentence_md | 是 | error |
| §1 别名 | `persona.yaml`.`identity.aliases` | list_md | 否 | silent_default |
| §1 自称 | `persona.yaml`.`identity.self_reference` | sentence_md | 是 | error |
| §1.1 性格底色 | `persona.yaml`.`identity.essence` | list_md（限 3-5 条） | 是 | error |
| §1.2 不应该出现的样子 | `persona.yaml`.`identity.not_traits` | list_md（>= 3 条） | 是 | error |
| §1.3 价值观 | `persona.yaml`.`constitution.values` | list_md | 是 | error |
| §1.3 硬规则 | `persona.yaml`.`constitution.hard_rules` | list_md（>= 3 条） | 是 | error |
| §2 当前状态 | `persona.yaml`.`runtime_state_seed` | kv_md | 否 | silent_default |
| §3.1 句子形态 | `voice.yaml`.`style_principles.sentence_shape` | list_md | 是 | error |
| §3.2 节奏 | `voice.yaml`.`style_principles.rhythm` | list_md | 是 | error |
| §3.3 禁用句式 | `voice.yaml`.`style_principles.banned_patterns` | list_md（>= 3 条） | 是 | error |
| §3.4 表达素材 | `voice.yaml`.`expression_library.items[]` | llm_extract（schema：text/use_when/avoid_when/review_status=candidate） | 是（>= 5 条） | error |
| §3.5 黑话策略 | `voice.yaml`.`slang_policy` | list_md + kv_md | 是 | error |
| §4.1 已知事实 | `knowledge.yaml`.`known_facts[]` | list_md | 是 | error |
| §4.2 不知道边界 | `knowledge.yaml`.`unknown_boundaries[]` | list_md | 是 | error |
| §4.3 禁说事实 | `knowledge.yaml`.`forbidden_claims[]` | list_md | 是 | error |
| §5.1 关系倾向 | `relationships.yaml`.`default_disposition` | sentence_md | 否 | silent_default |
| §5.2 用户/群画像 | `relationships.yaml`.`profiles[]` | llm_extract（schema：subject_type/subject_id/note） | 否 | silent_default |
| §6 经历种子 | `memory.yaml`.`seed_episodes[]` | llm_extract（schema：date/situation/origin_anchor=`source.md#L<line>`） | 否 | silent_default |
| §7.1 正例 | `examples.yaml`.`positive[]` | llm_extract（schema：scene/turn/comment） | 是（>= 5） | error |
| §7.2 反例 | `examples.yaml`.`negative[]` | llm_extract（schema：scene/wrong_turn/right_turn） | 是（>= 3） | error |
| §8.1 runtime 覆写 | `runtime.yaml`（patch 默认模板） | yaml_patch | 否 | silent_default |
| §8.2 capabilities 覆写 | `capabilities.yaml`（patch 默认模板） | yaml_patch | 否 | silent_default |
| §8.3 adapter 覆写 | `adapter.yaml`（patch 默认模板） | yaml_patch | 否 | silent_default |
| §8.4 guard 覆写 | `guard.yaml`（patch 默认模板） | yaml_patch | 否 | silent_default |
| §8.5 eval 覆写 | `eval.yaml`（patch 默认模板） | yaml_patch | 否 | silent_default |

> `trace.yaml` 不接受用户覆写（schema-only）；importer 直接拷贝默认 schema。

缺失行为：

- `error`：阻止写 `.draft/` 或 Pending Freeze，必须补 source。
- `warn_default`：写默认值并在 `_import_report.json` 中记录 warn；不阻止 draft。
- `silent_default`：写空数组、空对象或默认值，仅在 `_import_report.json` 中记录 info。

---

## 7. 校验与高亮

### 7.1 校验等级

| 级别 | 含义 | UI 表现 | 示例 |
| --- | --- | --- | --- |
| **error** | 必填字段缺失 / 引用闭环断裂 / hard_rule 未标注 enforce 分类 | 红色高亮 + 阻止 Pending Freeze | §1.1 essence 为空、§7.1 正例少于 5 条 |
| **warn** | 低置信抽取（< 0.6） / 数量低于推荐下限 / 风险关键词 | 黄色高亮 + 允许冻结但出警告 | §3.4 表达素材只有 4 条、`hard_rules` 出现"尽量"等弱化词 |
| **info** | 选填字段为空 / 使用了默认模板 | 浅灰提示 | §5、§6、§8 全部默认 |

### 7.2 引用闭环检查

importer 必须在写盘前校验：

| 检查项 | 失败示例 |
| --- | --- |
| `voice.expression_library.items[].use_when` 中的语境标签必须在 `runtime.yaml` 或 `examples.yaml` 中可识别 | 用户写了 `use_when: 客户投诉`，但 runtime/examples 都没该场景 → warn |
| `persona.constitution.hard_rules` 每条必须标注 enforce 分类：`pattern_guardable` / `judge_guardable` / `eval_only` | hard_rule = "不要乱说话" 但未标分类 → error |
| `pattern_guardable` 必须映射到 `guard.yaml.hard_check.patterns.reason_ref` | 标为 pattern 但没有可执行 pattern → error |
| `judge_guardable` 必须映射到 `guard.yaml.soft_judge` 的判定任务；compiler 未上线前只能结构校验 | 标为 judge 但 soft_judge 关闭 → warn |
| `eval_only` 必须映射到 `eval.yaml` 的 critical_failure 或 eval case | 标为 eval_only 但没有 eval 覆盖 → warn；上线前必须补齐 |
| `examples.negative[].wrong_turn` 必须违反 `voice.banned_patterns` 或 `persona.not_traits` 之一 | 反例无法解释为何错 → warn |
| `eval.yaml`.tasks 中的 critical_failure / eval cases 应覆盖所有 hard_rule | 漏覆盖 → warn；Schema Freeze 前升级为 error |

### 7.3 admin UI 高亮草案（S10' 后续）

新建 `admin/frontend/src/views/persona/PersonaImporterView.vue`（不在 Part A 首版范围内实施，仅作为 S10' 后续设计草案）：

```text
┌─ Persona Importer  [persona_id: fengxiaomeng] ────────────────────┐
│ ┌─ source.md ────────────┐ ┌─ Draft (15 skeleton) ─────────────┐ │
│ │ # 1. 是谁              │ │ ✓ persona.yaml      conf 0.97    │ │
│ │ - 元气                 │ │ ⚠ voice.yaml        4 warn       │ │
│ │ - 反应快               │ │ ✗ knowledge.yaml    1 error      │ │
│ │ - 有一点调皮           │ │   └ unknown_boundaries: 缺失     │ │
│ │ ...                   │ │ ✓ relationships.yaml (default)   │ │
│ └────────────────────────┘ │ ✓ memory.yaml      (default)     │ │
│                            │ ⚙ runtime.yaml     (template)    │ │
│ [Re-import]  [Edit source] │ ⚙ capabilities.yaml (template)   │ │
│                            │ ⚙ adapter.yaml     (template)    │ │
│ ┌─ Issues (3) ───────────┐ │ ⚙ guard.yaml       (template)    │ │
│ │ ✗ knowledge.unknown_   │ │ ⚙ eval.yaml        (template)    │ │
│ │   boundaries 必填空    │ │ ⚙ trace.yaml       (schema)      │ │
│ │ ⚠ voice.expression_lib │ └───────────────────────────────────┘ │
│ │   items < 5            │                                         │
│ │ ⚠ §1.3 hard_rules 含   │  [Pending Freeze to _pending_freeze/]  │
│ │   弱化词"尽量"          │  （所有 error 解决后启用）             │
│ └────────────────────────┘                                         │
└────────────────────────────────────────────────────────────────────┘
```

复用 [admin/frontend/src/views/learning/](../../admin/frontend/src/views/learning/) 的 main-pane / detail-drawer 双栏骨架；issues 列表沿用 EmptyState / AppPanelSection 节奏。

### 7.4 高亮的最低交互保证（S10' 后续）

> 以下交互不属于 Part A 首版验收；首版只要求 CLI + `_import_report.json` 能暴露同等信息。

- 点击 issue → 双栏左侧 source.md 自动滚动并标蓝原文行（从 `_import_report.json.fields[].source_span` 读取）；
- 点击「Edit source」→ 直接 in-place 编辑 source.md（不跳出 admin）；
- 「Re-import」按钮触发流水线重跑，draft 整体替换（保留前一份到 `.draft.prev/`，仅一份）；
- 「Pending Freeze」需有 admin token，且**全部 error 解决**才允许；compiler dry-run 上线前仅把 `.draft/` 拷到 `_pending_freeze/`，不写正式 `config/persona/<id>/`。

---

## 8. 默认模板（首版）

> 默认模板独立维护在 `config/persona/_defaults/v2/`，importer 启动时读取。本节列出**首版要写的最小集合**，避免循环依赖 v2 compiler。

### 8.1 guard.yaml 默认模板（来自 R4.3）

```yaml
schema: omubot.guard.v2
hard_check:
  patterns:
    - id: hc_no_ai_self_reveal
      regex: "我是.{0,3}(AI|语言模型|大模型|chatbot)"
      action: rewrite
      reason_ref: persona.constitution.hard_rules[0]
    - id: hc_no_template_phrasing
      regex: "(作为|根据).{0,4}(凤笑梦|我的设定)"
      action: rewrite
      reason_ref: voice.style_principles.banned_patterns
soft_judge:
  enabled: true
  sample_rate: 0.2
  task_profile: persona_import
  prompts_ref: guard/prompts/soft_judge.txt
  max_attempts: 1
rewrite:
  enabled: true
  max_attempts: 2
  scope: voice_layer_only
output_blocking:
  on_critical_failure: refuse_in_character
```

### 8.2 eval.yaml 默认模板（来自 R4.4）

```yaml
schema: omubot.persona_eval.v2
tasks:
  - id: persona_consistency
  - id: linguistic_naturalness
  - id: expected_action
  - id: action_justification
  - id: values_alignment
  - id: memory_factuality
  - id: hallucination_boundary
  - id: long_term_stability
  - id: toxicity_control
  - id: style_overfit_non_stiffness
golden_set:
  single_turn: examples/golden/single_turn/   # >= 170
  multi_turn:  examples/golden/multi_turn/    # >= 8
thresholds:
  unified_scale: 0_to_100
  pass: 75
  warn: 60
critical_failure:
  - identity_leak
  - fake_memory_promotion
  - permanent_persona_change
  - forbidden_fact_disclosure
  - unauthorized_capability_claim
```

### 8.3 trace.yaml 默认模板（schema-only）

```yaml
schema: omubot.trace.v2
retention_days: 30
records:
  - prompt_blocks
  - dynamic_refs
  - retrieved_memories
  - plugin_diffs
  - tool_calls
  - state_decision
  - guard_decision
  - send_result
```

> 其余 runtime / capabilities / adapter 默认模板需实施时落地，本方案先给字段轮廓，避免与现行 `BotConfig` / 插件加载冲突，留给 §13 开放问题。

---

## 9. 流水线一致性约束

importer 在写 `.draft/` 前必须满足以下不变量；否则停在 error 状态、不写盘。

1. **不创造**：任何 LLM 抽取型字段输出，必须能在 source.md 找到 `source_span`（行号），否则丢弃该条；
2. **不混层**：source §1 永远不写入 voice / runtime / guard；§3 不写入 persona.identity；§8 覆写永远不能修改 persona.yaml；
3. **hard_rule 分类强制**：每条 `persona.constitution.hard_rules` 必须显式标注 enforce 分类（`pattern_guardable` / `judge_guardable` / `eval_only`），否则 error；
4. **下游覆盖分级**：`pattern_guardable` 必须覆盖到 `guard.hard_check.patterns.reason_ref`；`judge_guardable` 必须覆盖到 `guard.soft_judge`；`eval_only` 必须覆盖到 `eval.critical_failure` 或 eval case。compiler 未上线前，后两类覆盖缺失降级为 warn，Schema Freeze 前必须补齐；
5. **review_status 默认 candidate**：所有 LLM 抽取的表达素材初始 `review_status=candidate`，必须经 admin 改为 `approved` 才能进运行时；
6. **draft 隔离**：`.draft/` 的任何 YAML 都不会被正式运行时消费；compiler dry-run 上线前只能通过「Pending Freeze」拷贝到 `_pending_freeze/`。

---

## 10. 实施切片（每段独立可发布）

> 切片之间不要交叉依赖；每段都能独立 PR、独立验证。

| # | 切片 | 关键交付 | 验证 |
| --- | --- | --- | --- |
| **S1** | source.md 模板 + 解析器 | `services/persona/source_parser.py`：front matter + AST 切章 | 单元测试：5 份 fixture source.md 正确切到 8 个章节 |
| **S2** | 列表型 + 键值型抽取器 + 默认模板载入 | `services/persona/extractors/list_md.py` / `kv_md.py`；`config/persona/_defaults/v2/` 首版 | 单元测试：fixture → draft persona/voice/knowledge YAML 字段值一致 |
| **S3** | LLM 抽取器（受限 schema + source_span 强制） | `services/persona/extractors/llm.py`；prompt 模板；JSON schema 校验 | 单元测试：mock LLM 返回；source_span 缺失 → 自动丢弃 |
| **S4** | 校验 / 引用闭环 / hard_rule ↔ guard 桥接 | `services/persona/validator.py`；error / warn / info 三级 | 单元测试：每条不变量都有失败 fixture |
| **S5** | admin API：`POST /api/admin/persona/import` / `GET /api/admin/persona/draft/<id>` / `POST /api/admin/persona/freeze/<id>` | `admin/routes/api/persona_importer.py`，经 `/api/admin` 聚合路由挂载 | 集成测试：HTTP round-trip + admin token；本阶段 freeze = Pending Freeze |
| **S6** | admin SPA：PersonaImporterView 双栏 + issues 面板（后续 S10'，不属于 Part A 首版验收） | `admin/frontend/src/views/persona/PersonaImporterView.vue` | vue-tsc + npm run build；UI smoke：手动跑通 import → fix → pending freeze |
| **S7** | 与 v2 compiler 的握手（仅当 v2 compiler 落地后） | Schema Freeze 触发 v2 compiler dry-run；失败回退到 draft | 集成测试：dry-run pass / fail 双路径 |

S1-S4 可并行；S5 依赖 S1-S4；S6 依赖 S5；S7 依赖 v2 compiler，独立排期。

---

## 11. 与现有系统的关系

| 系统 | 影响 |
| --- | --- |
| `config/soul/identity.md + instruction.md` | **不动**。importer 首版只生成 `config/persona/<id>-v2/.draft/` 与 `_pending_freeze/`，运行时仍走 soul/。v2 compiler 上线并通过 dry-run 后再做迁移。 |
| `docs/ai-persona-generation-rules.md` | 保留为 v1 生成规则，标注「适用于 soul/ 双文件运行时」；v0.1 importer 与之不冲突。 |
| `services/style/store.py` / `services/slang/store.py` | 不改。voice.yaml.expression_library 与 style/slang store 是**两套源**：前者是手写素材，后者是学习产物，运行时由 v2 compiler 决定优先级。 |
| `admin/routes/api/learning_pipeline.py` | 不改。importer 不进 /learning 总览。 |
| `BotConfig` / `kernel/config.py` / `config/config.json` | 首版 importer 不修改这些配置；后续 runtime skeleton 补全时可只读投影默认值。 |

### 11.1 Pending Freeze vs Schema Freeze

| 名称 | 触发条件 | 写入位置 | 是否进入运行时 |
| --- | --- | --- | --- |
| Draft | 每次 import / re-import | `config/persona/<id>-v2/.draft/` | 否 |
| Pending Freeze | 全部 error 解决 + admin token + compiler 未上线 | `config/persona/<id>-v2/_pending_freeze/` | 否 |
| Schema Freeze | compiler dry-run 已上线且通过 | `config/persona/<id>/` | 是，但不在 importer 首版实现 |

约束：

1. compiler dry-run 上线前，任何 freeze endpoint 都只能执行 Pending Freeze。
2. Pending Freeze 必须复制 `source.md` 为 `source.frozen.md`，但不得写正式 `config/persona/<id>/`。
3. Schema Freeze 属于 S7 / compiler 阶段，本 importer 首版不实现。

---

## 12. 风险与回滚

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| LLM 抽取器"主动扩展"导致 source.md 没写的字段被填上 | 凭空捏造人设 → 直接 OOC | system prompt 强制只复述 / 不创造；`confidence < 0.6` 自动丢弃；UI 仍把每个抽取项标 candidate |
| 默认模板与运行时配置漂移 | guard / runtime 行为与运行时不一致 | 首版仅 `_defaults/v2` 三份模板；runtime skeleton 后续从 `kernel/config.py` + `config/config.json` 只读投影，并加一致性测试 |
| 用户在 admin UI 改了 source.md，但 draft 没重跑 | 字段值与原文不一致 | source.md 写盘时计算 hash，draft `_import_report.json` 记 hash；不一致时 UI 强制 Re-import |
| Pending Freeze 之后用户回头改 source.md | `_pending_freeze/` 与 source 不一致 | Pending Freeze 把 source.md 一并复制为 `source.frozen.md`；source.md 改动需重新 Pending Freeze |
| v2 compiler 未落地时 freeze 后无法消费 | freeze 变成"摆设" | S7 之前按钮文案统一为 "Pending Freeze - v2 compiler 待上线"，且不写入 `config/persona/<id>/`，仅落 `_pending_freeze/` |

回滚：`config/persona/<id>-v2/` 是新增目录，不影响现有 soul/ 与 BotConfig；任意切片回滚只需 git revert + 删除该目录。

---

## 13. 已确认的开放问题（首轮审计 → §17）

> Q1-Q8 已在 §17.1 决策表锁定。下方保留原文 + 选项 + 已确认结论作为可追溯审计记录。后续修订请改 §17，不要回填本节。

1. **Q1：persona_id 的命名空间**
   - A. 与现行 soul/ 共享一个全局命名空间（`fengxiaomeng` 唯一）
   - B. v2 独立命名空间（`fengxiaomeng-v2`），与 soul/ 并存
   - C. 一开始就强制 v2 接管（仅当 v2 compiler 同期落地）
   - **已确认：B**（§17.1 / v2 compiler 未落地前隔离）

2. **Q2：source.md 是否进入 git？**
   - A. 进 git（持久化、可 review、可与 maintenance-log 联动）
   - B. 只在本地（`config/persona/` 加 .gitignore；admin UI 是唯一编辑入口）
   - **已确认：A**（§17.1 / `.draft/` `source.frozen.md` `_pending_freeze/` 走 .gitignore，详见 §17.2-2）

3. **Q3：LLM 抽取用什么模型？**
   - A. 新增 `persona_import` task profile，默认推荐绑定低成本 JSON 能力模型（如 Claude Haiku），可由配置覆盖
   - B. 固定某个模型 id（实现简单，但不可用时无 fallback）
   - C. 与现行 chat client 同 profile（一致性优先，但可能浪费主链路模型预算）
   - **已确认：A**（§17.1 / importer 走 `persona_import` task profile，不在代码里锁定 model id）

4. **Q4：默认模板存放位置**
   - A. `config/persona/_defaults/v2/`（与 persona 平级，便于 git 跟踪）
   - B. `services/persona/defaults/v2/`（视作代码资源）
   - C. 直接内联 Python 常量
   - **已确认：A**（§17.1）

5. **Q5：表达素材的 `review_status` 是否区分 `candidate` / `approved` / `muted`？**
   - A. 与 style learning 一致的三态
   - B. 简化为 `pending` / `approved` 双态
   - **已确认：A**（§17.1 / 与 [services/style/store.py](../../services/style/store.py) 对齐；importer 写出全部为 `candidate`）

6. **Q6：admin 编辑 source.md 的鉴权？**
   - A. 仅 admin token
   - B. admin token + 二次确认（freeze 这一步必须；compiler 前为 Pending Freeze）
   - **已确认：B**（§17.1 / compiler dry-run 前只允许 Pending Freeze，不写正式运行时目录）

7. **Q7：是否在本期就实现 admin SPA 高亮（S6）？**
   - A. 是，importer 和 UI 同期上线（建议范围）
   - B. 否，先做 S1-S5 + CLI（`uv run python -m services.persona.importer <persona_id>`），UI 留到下一阶段
   - **已确认：B**（§17.1 / SPA 推迟到 S10'，本期交 CLI）

8. **Q8：默认模板首版只覆盖 guard / eval / trace，其余 runtime / capabilities / adapter / state / thinker / system / module 暂不出默认模板（仅生成空骨架）？**
   - A. 是（与 v2 compiler 落地节奏对齐）
   - B. 否（一次写齐）
   - **已确认：A**（§17.1 / 其余默认模板随 S10' 补，详见 §17.2-4）

---

## 14. 审计记录

- **2026-05-23**：v0.1 草案完成 → §15 全流程注入审计补审 → §16 系统模块体系（清洁架构）追加 → §17 首轮确认（按推荐锁定 17 项）。本节本身不再追加流水；后续修订统一改 §17，并在 maintenance-log.md 留下入仓时间戳。

---

## 15. 全流程注入审计（v0.1 补审）

> 目的：穷举 Omubot 主链路上**会影响 LLM 回复的所有上下文源**，对照 v2 spec（15 文件 + `modules/`，详见 §16.8）+ §6 字段映射表，找出 importer 漏掉的字段。所有 file:line 来自 2026-05-23 当前 `main` 分支代码。

### 15.1 主链路五段

```text
[A] 入口        QQ → NoneBot adapter → kernel/router.py:662 _collect_group_context
                                    → kernel/router.py:966 _handle_private_chat
[B] 调度        services/scheduler.py:116 GroupChatScheduler.notify
                  ├─ 触发判定：force / probability / skip
                  ├─ 概率乘子：mood_mult × time_mult × interest_score
                  └─ services/scheduler.py:301 _fire → :357 _do_chat
[C] 决策        services/llm/client.py:1939 (chat 主入口)
                  └─ services/llm/thinker.py 两阶段思考器
                       输出 ThinkDecision(action, retrieve_mode, rewritten_query, sticker, tone, thought)
                  └─ wait → 直接结束（不进主 LLM）
[D] 注入 + 调用 services/llm/client.py:2010-2107
                  ① bus.fire_on_pre_prompt → 各插件 add_block
                  ② provider_bus.run_all → slang/style/episode provider
                  ③ PromptBudgetManager.process（裁剪 + 优先级）
                  ④ prompt_builder.build_blocks（spine 三段 + state_board）
                  ⑤ 注入 thinker thought/sticker/tone 作为最后 system block
                  ⑥ Anthropic SSE + 工具循环 ≤5 round
[E] 后处理      services/llm/client.py:1678 _finalize_visible_reply
                  → _clean_reply（去舞台指令 / 表情包旁白）
                  → _strip_control_tokens（清理 pass_turn 泄漏）
                  → _split_naturally / _smart_chunk（≤4 段）
                  → on_segment 回调 → services/scheduler.py:312 _send_to_group
                  → services/humanizer.py 模拟打字延迟
                  → bot.send_group_msg
```

### 15.2 注入源全表（21 个）

每行回答：来源文件、注入点 file:line、prompt 段位（cache slot）、可变性、是否带 source/confidence/decay 元数据。**注入点**是该源最终进入 Anthropic 请求的位置。

| # | 来源 | 读取 | 注入 | Prompt 段位 | 可变性 | 元数据 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `soul/identity.md` 主体 | services/identity.py:99 | services/llm/prompt_builder.py:81-108 | system block 1（static，cache 之前） | admin-only，启动加载 | 无 |
| 2 | `soul/identity.md --- 主动` 部分 | services/identity.py | prompt_builder.py:103-104 | 同 #1，拼接到末尾 | admin-only | 无 |
| 3 | `soul/instruction.md` | prompt_builder.py:29 load_instruction | prompt_builder.py:96-97 | 同 #1 | admin-only | 无 |
| 4 | bot QQ id 提示 | kernel/router.py 启动事件 | prompt_builder.py:88-95 | 同 #1 | 启动时确定 | 无 |
| 5 | admins 名单 | kernel/config.py | prompt_builder.py:98-102 | 同 #1 | admin-only | 无 |
| 6 | 表情包频率规则 + 表情包库 | plugins/sticker/plugin.py:108-134 | plugin_static priority=5 / plugin_stable priority=30 | static + stable | 库 runtime-mut，规则 admin-only | 无 |
| 7 | 全局记忆索引 (`storage/memory_cards.db` index) | services/memory/card_store.py | plugins/memo/plugin.py:168-182 | plugin_stable | runtime-mut（compaction add_card） | 无 |
| 8 | group profile.reply_style + custom_prompt | kernel/config.py:335-339 | services/llm/client.py:1386-1405 _build_group_profile_block → :2015-2017 | plugin_stable 头部 | admin per-group | 无 |
| 9 | StateBoard（活跃用户 / 主题 / @mentions） | services/memory/state_board.py:71-79 | prompt_builder.py:131-159（DeepSeek-V4 路径走 client.py:2081-2100 推到末尾 turn_meta） | static 与 stable 之间 | 每轮重建 | 无 |
| 10 | Mood block（label/energy/valence/openness/tension + day-context） | plugins/schedule/mood.py:270-313 build_mood_block | plugins/schedule/plugin.py:70-86 add_block(plugin_dynamic, priority=10) | plugin_dynamic 顶部 | 缓存 15-30 分钟 | 无 |
| 11 | 当前正在做的事（schedule slot.activity） | plugins/schedule/store.py（`storage/schedule/<date>.json`） | 与 #10 同一行（mood.py:287） | 同 #10 | 每日 LLM 生成 | 无 |
| 12 | 节日/调休/生日 day-context | plugins/schedule/calendar.py | mood.py:315-348 | 同 #10 | 静态日历 | 无 |
| 13 | Affection 块（好感度 + tier） | plugins/affection/store.py | plugins/affection/plugin.py:53-65 add_block(plugin_dynamic, priority=20) | plugin_dynamic | 每次 on_post_reply 写入 | tier 字段 |
| 14 | Memory cards（实体记忆，4-tier 网关） | services/memory/retrieval.py:131-189 RetrievalGate.build_memo_block | plugins/memo/plugin.py:186-211 add_block(plugin_dynamic, priority=25) | plugin_dynamic（被 ContextPlugin.takeover 时跳过） | runtime-mut（compaction + 工具 update_card / add_card） | last_seen_at / last_used_at / status |
| 15 | 群黑话命中 | services/slang/store.py | services/block_trace/slang_provider.py（plugins/chat/plugin.py:923-959 注册），client.py:2035-2064 | plugin_dynamic | runtime-learned | confidence / status / last_used_at |
| 16 | 风格档案 + 表达习惯（style profile / expressions） | services/style/store.py | services/block_trace/style_provider.py + plugins/style/plugin.py:139-174 | plugin_dynamic priority=42/45 | runtime-learned | confidence / status / risk_tags / decay_at |
| 17 | Episode 反思块 | services/episodic/store.py | services/block_trace/episode_provider.py（plugins/chat/plugin.py:953-957） | plugin_dynamic | runtime-learned，仅 `state==enabled_for_prompt` 入 prompt | episode_state / confidence / decay_at |
| 18 | Context 检索包（doc / fact / hybrid，RRF 融合 doc:0.5 / mem:0.3 / graph:0.2） | services/context/service.py:53-72 + sources.py | plugins/context/plugin.py:116-179 add_block(plugin_dynamic, priority=50) | plugin_dynamic（包在 `<context_data>` 安全前导内：services/context/packing.py:99-118） | runtime-mut + admin | source_id / score |
| 19 | 知识图谱块（KG triples） | services/knowledge_graph/store.py | plugins/knowledge/plugin.py:66-78 add_block(plugin_dynamic, priority=55) | plugin_dynamic | runtime-mut + admin | edge_kind / confidence |
| 20 | Thinker 决策注入（thought + sticker + tone） | services/llm/thinker.py:115-143 ThinkDecision | services/llm/client.py:2107-2124（最后 system block） | dynamic 末尾 | 每轮新决策 | 无 |
| 21 | 工具列表（17+ 个） | services/tools/registry.py + plugins/*/register_tools | client.py:1455-1488 _build_tool_defs（受 group_profile.allowed_tools/blocked_tools/sticker_mode/slang_enabled 过滤） | tools field（独立缓存槽） | admin-mutable，per-group 过滤 | 无 |

**消息侧**（不是 system 段）的注入：

| # | 来源 | 读取 | 注入 | 段位 |
| --- | --- | --- | --- | --- |
| 22 | 压缩摘要 `«对话摘要»` | services/llm/client.py:2622-2812 _compact_with_tools | client.py:1720-1759 _build_group_messages 头部 | messages[0]，cache 之前 |
| 23 | GroupTimeline finalized 消息 | services/memory/timeline.py | client.py:1720-1759 | messages 中段 |
| 24 | GroupTimeline pending + add_pending_trigger 触发原因 | timeline.add_pending_trigger（scheduler.py:369-374） | client.py 同上，合并到末尾 user msg | messages 末尾，cache 之前 |
| 25 | Private 短期历史 | services/memory.short_term | client.py:1761-1788 _build_private_messages | 同上（私聊路径） |

**调度门控**（不进 prompt 但决定是否进入主 LLM）：

| # | 来源 | 读取 | 影响 |
| --- | --- | --- | --- |
| G1 | TalkSchedule 时间倍率 | services/talk_schedule.py + config/talk_schedule.json | scheduler.py:205 time_mult |
| G2 | Mood 倍率 | scheduler.py:281-300 _get_mood_multiplier | scheduler.py:201 mood_mult |
| G3 | base talk_value（per-group） | kernel/config.py GroupOverride.talk_value | scheduler.py:179-181 |
| G4 | consecutive_skip 衰减保底 | scheduler.py:184-187 | 强制 fire 概率 |
| G5 | bilibili 视频特别 talk_value | scheduler.py:178 trigger.extra | 重写 base_talk_value |
| G6 | autonomous interest_score | scheduler.py:194-198 | 自治模式概率乘子 |
| G7 | planner_smooth 冷却 | scheduler.py:170 | 抑制连续 fire |

**后处理**（不影响 LLM 调用，但影响最终用户看到的文本）：

| # | 来源 | 读取 | 影响 |
| --- | --- | --- | --- |
| P1 | `_clean_reply` 去舞台指令 / kaomoji 旁白 | client.py:124-194 | 删除（揉眼睛）/ 表情包旁白 |
| P2 | `_strip_control_tokens` | client.py:196-211 | 清理 `pass_turn` 等控制泄漏 |
| P3 | Kaomoji enforcement（强制再走一轮 send_sticker） | client.py:2232-2257 | 多一轮工具调用 |
| P4 | `_split_naturally / _smart_chunk / _coalesce_segments`（≤4 段） | client.py:359-538 | 切段 + 段间 0.8s 节奏 |
| P5 | `humanizer.delay` 打字延迟 | services/humanizer.py | 按字数 sleep |
| P6 | reply_prefix `[CQ:reply,id=...]` for @-mentions | scheduler.py:384-399 | 给首段加引用 |

### 15.3 与 v2 spec 的逐项匹配

下表回答：**每个真实注入源 → 应落 v2 哪个文件 → importer §6 当前是否覆盖。**

#### A. 已覆盖（importer §6 已映射）

| 注入源 | v2 文件 | importer §6 | 备注 |
| --- | --- | --- | --- |
| #1-#3 identity / instruction / proactive | persona.yaml + voice.yaml | §1 §1.1 §1.3 §3 | OK |
| #6 表情包规则 | voice.yaml.expression_library | §3.4 | OK（但 sticker 有独立 schema，importer 应单独记） |
| #15 群黑话 | voice.yaml.slang_policy | §3.5 | OK（仅写策略，黑话词条本身由 store 学） |
| #16 风格档案 / 表达 | voice.yaml.expression_library | §3.4 | OK（学习产物） |
| #18 context 检索 + #19 KG | knowledge.yaml | §4.1-§4.3 | OK |
| #21 工具列表 | capabilities.yaml | §8.2 默认模板 | OK |

#### B. 部分覆盖（importer §6 有但字段不全）

| 注入源 | v2 文件 | importer §6 缺什么 |
| --- | --- | --- |
| #5 admins 名单 | persona.yaml.identity 或 adapter.yaml.permissions | §6 没有 admin 字段；front matter 没收 admins |
| #7 全局记忆索引 | memory.yaml.paragraph + memory.yaml.entity_index | §6 只收"经历种子"，没收索引 schema |
| #8 group profile.reply_style + custom_prompt | runtime.yaml.per_group 或 persona.yaml.scoping | §6 没有 per-group 覆写；§8.1 仅占位 |
| #11 schedule slot.activity | runtime.yaml.daily_schedule 或新文件 | §6 完全没有日程字段 |
| #14 memory cards 4-tier 网关 | memory.yaml.retrieval_gate | §6 没收 retrieval_gate 阈值 |
| #18 context 检索 RRF 权重 | runtime.yaml.context_retrieval | §6 没收 RRF 权重覆写 |

#### C. **完全漏掉**（importer 当前没有任何字段对应）

| 注入源 | 应进 v2 哪个文件 | 缺口说明 |
| --- | --- | --- |
| **#10 Mood block + day-context** | 新增 `state.yaml`（或 `runtime.yaml.mood`） | persona-spec-format.md v2 没有 mood/state 章节；mood.py:270 是 LLM 影响最大的动态块之一 |
| **#11 当前活动 / 日程** | 新增 `state.yaml.daily_schedule` 或 `runtime.yaml.schedule` | LLM 生成的日程是"凤笑梦今天在干嘛"，是 persona 行为动力，不是 voice 也不是 memory |
| **#12 day-context（节日/调休/生日）** | `state.yaml.calendar` 或 `runtime.yaml.calendar` | 受 plugins/schedule/calendar.py 控制；importer 应让用户写 self_birthday、wxs_member 等 |
| **#13 Affection（好感度 / tier）** | 新增 `relationships.yaml.affection` 子结构 | importer §5 关系倾向只收 disposition，没收 tier 阈值 / 学习速率 / 在群里的脱敏规则 |
| **#17 Episode（经验反思）** | `memory.yaml.episodes` 或拆 `experiences.yaml` | importer §6"经历种子"是 seed，没收 episode_state、enabled_for_prompt 阈值、decay_at 等 schema |
| **#20 Thinker 决策（action / retrieve_mode / sticker / tone / thought）** | 新增 `thinker.yaml`（或 `runtime.yaml.thinker`） | persona-spec-format.md v2 完全没写 thinker；它是 R4.2 两阶段输出的核心，必须有专门 schema |
| **#22-#24 GroupTimeline + 压缩摘要** | `memory.yaml.short_term` + `memory.yaml.compaction` | importer §6 只字未提 timeline / compaction 阈值 / pending buffer |
| **G1-G7 Scheduler 触发参数（talk_value / mood_mult / time_mult / debounce / batch_size / planner_smooth / consecutive_skip 保底）** | `runtime.yaml.scheduler` | importer §8.1 只有占位，没列字段；这是"主动性"的核心 |
| **P1-P6 后处理（kaomoji enforce / 段数上限 / humanizer 节奏 / reply_prefix）** | `voice.yaml.post_process` 或 `adapter.yaml.send` | importer §6 完全没收；这些直接决定"看起来像不像人在打字" |
| **#9 StateBoard** | `runtime.yaml.state_board` 或合并到 thinker | 当前 admin/runtime 都不可调；importer 应至少提供 enable/disable 旋钮 |
| **GroupOverride（per-group at_only / blocked_users / sticker_mode / slang_enabled / tools_enabled）** | `runtime.yaml.per_group_overrides` 或 `adapter.yaml.scope` | importer §6 完全没收；这是 persona 在不同群表现差异的总开关 |
| **`<context_data>` 安全前导（packing.py:99-118 prompt-injection guard）** | `guard.yaml.input_guard` | 现行 guard.yaml 默认模板（§8.1）只覆盖输出 hard_check / soft_judge，没覆盖**输入侧**的 prompt-injection guard |

### 15.4 关键缺口的优先级判定

按"对 OOC / 拟人 / 一致性的实际影响"排序：

| 优先级 | 缺口 | 理由 |
| --- | --- | --- |
| **P0**（必须补） | Thinker 决策 schema（#20） | 两阶段输出是 R4.2 核心；importer 不收则用户连 `tone`/`retrieve_mode` 都没法配置 |
| P0 | Mood / Schedule / day-context（#10-#12） | 这三个是 plugin_dynamic 顶部，对 LLM 的影响仅次于 identity；mood prompt 直接改语气倾向 |
| P0 | Affection 关系层（#13） | tier 决定"在群里说话亲疏"；importer §5 当前的 disposition 远不够 |
| P0 | Scheduler 参数（G1-G7） | 决定 bot 是否开口；persona "主动性" 完全压在这一层 |
| **P1**（强烈建议） | Episode（#17） | 与 memory 紧耦合，importer 漏了 enabled_for_prompt 阈值 |
| P1 | Group profile（#8 / GroupOverride） | persona "在不同群的不同样子"；不收会导致一份 persona 套所有群 |
| P1 | 后处理（P1-P6） | 决定"看起来像不像在打字"；现行 voice.yaml 只管文字，不管节奏 |
| P1 | 输入侧 guard（`<context_data>` packing） | guard.yaml 默认模板必须扩展输入侧 |
| **P2**（次轮） | StateBoard / 全局索引 / RRF 权重 | 工程层调优，默认模板能扛 |

### 15.5 对 importer 方案的修订建议

> 如果 §13 Q1-Q8 用户拍板进 S1，以下修订必须先打入 v0.2 草案，再进入实施。

1. **v2 spec 扩容**：[docs/persona-spec-format.md](../persona-spec-format.md) 必须新增 / 扩展若干文件（最终形态见 §16.8 = 15 文件 + `modules/`）：
   - 新增 `state.yaml`（mood / schedule / day-context / state_board，对应 #9-#12）
   - 新增 `thinker.yaml`（对应 #20，承担 R4.2 schema）
   - 扩展 `relationships.yaml`：增加 affection.tiers / decay / privacy_mask（对应 #13）
   - 扩展 `runtime.yaml`：scheduler block（G1-G7）+ per_group_overrides + post_process（P1-P6）+ context_retrieval（RRF）

2. **importer §6 字段映射表扩容**（v0.2）：
   - source.md 新增 §9「主动性 / 调度」章节 → runtime.yaml.scheduler
   - source.md 新增 §10「日程与心情」章节 → state.yaml
   - source.md 新增 §11「思考器倾向」章节 → thinker.yaml.policy
   - source.md §5 关系扩容：增加"好感度阈值与脱敏"子节 → relationships.yaml.affection
   - source.md §8 偏好覆写改为细分 8 个子段（runtime.scheduler / runtime.context / state.calendar / capabilities / adapter / guard.input / guard.output / eval）

3. **默认模板扩容**：§8 默认模板首版只列了 guard / eval / trace，需要补：
   - `runtime.yaml` 默认（含 scheduler / context_retrieval / post_process）
   - `state.yaml` 默认（mood 阈值 + schedule generator 配置 + calendar 数据源）
   - `thinker.yaml` 默认（thinker_enabled / thinker_max_tokens / 决策原则 prompt）
   - `adapter.yaml` 默认（QQ/NapCat 事件、引用回复、撤回窗口、CQ:reply 规则）

4. **校验规则补强**（importer §7.2 引用闭环）：
   - 增加：`thinker.policy.tone` 列表必须 ⊂ `voice.tone_palette`
   - 增加：`runtime.scheduler.talk_value` 与 `state.mood.multipliers` 不能同向放大（防 fire 风暴）
   - 增加：`relationships.affection.tier_break` 必须单调递增
   - 增加：`adapter.permissions.admins` 必须出现在 `persona.yaml.identity.privileged_users` 或独立 admin schema（可与 kernel/config admins 解耦）

5. **不变量补强**（importer §9）：
   - 增加 #7：persona.yaml 不得直接写 mood / schedule / affection；它们必须落到 state.yaml / relationships.yaml
   - 增加 #8：thinker.yaml 不得放置 hard_rules（hard_rules 只能在 persona.yaml）
   - 增加 #9：runtime.yaml.scheduler.* 的最大值必须 ≤ kernel/config 全局上限（importer 启动时校验）

### 15.6 审计结论

- v2 原 12 文件 + importer v0.1 的 §6 字段映射表，**漏掉了运行时实际存在的 11 类注入源**（§15.3.C）；其中 4 类（Thinker / Mood / Schedule / Scheduler 参数）是 P0 必须补。已在 §16.8 / §17.2-5 收口。
- 漏掉的根因：v2 spec 原"创作型 6 文件"是基于"静态人设"假设设计的，没考虑 Omubot 已经有 mood/schedule/affection/thinker 这几个**runtime 状态机**——它们是 persona 的动态化身，而不是补充材料。
- 修订方向：把"runtime 状态机"独立成 `state.yaml` + `thinker.yaml` + `system.yaml` + `modules/`，并扩展 `runtime.yaml` / `relationships.yaml` / `adapter.yaml`，importer 同步扩 §6 / §8 / §9 / §12 / §13（详见 §16.9 / §17.2）。
- 修订**不**要求修改 Omubot 运行时代码——本审计只发现 importer 范围不够，不发现运行时缺陷。

### 15.7 已确认的衍生开放问题（首轮审计 → §17）

> Q9-Q12 已在 §17.1 决策表锁定。下方保留原文 + 选项 + 已确认结论作为可追溯审计记录。后续修订请改 §17。

- **Q9：是否把 v2 12 文件扩展？**
  - A. 是，独立两个文件（state.yaml + thinker.yaml）
  - B. 否，state 合并进 runtime.yaml，thinker 合并进 runtime.yaml
  - C. 否，state 合并进 persona.yaml（破坏 core 只读）
  - **已确认：A**（§17.1 / 已被 §16.8 进一步升级为 15 文件 + `modules/`，纳入 system.yaml）

- **Q10：mood prompt 模板是否进 persona-spec？**
  - A. 进 state.yaml.mood.prompts（用户可写）
  - B. 留在代码里（plugins/schedule/mood.py:_MOOD_PROMPTS），importer 只让用户调阈值
  - **已确认：B**（§17.1 / `state.yaml.mood.prompts: {}` 仅占位，文案不暴露给用户）

- **Q11：affection tier 文案归属**
  - A. 进 voice.yaml.affection_phrasing（在群聊脱敏文案、私聊明示文案）
  - B. 进 relationships.yaml.affection.phrasing
  - **已确认：B**（§17.1 / 与数据共置）

- **Q12：post_process（kaomoji enforce / 段数上限 / humanizer 节奏）放 voice 还是 adapter？**
  - A. voice.yaml.post_process（语言层）
  - B. adapter.yaml.send（发送层）
  - **已确认：B**（§17.1 / kaomoji enforce / humanizer / segmenter / reply_prefix 都涉及 QQ 平台特性，voice.yaml 不再保留 post_process）

---

## 16. 系统模块体系（已拆出）

> 原 §16 `SystemModule / RuntimeStateBus / SwitchMatrix / DAG` 已拆出为独立架构文档：
> [system-module-architecture.md](./system-module-architecture.md)。
>
> 本文档当前只保留 Source Importer Part A 的方案边界：`source.md -> v2 draft + _import_report.json + CLI`。
> Importer 首版不依赖 RuntimeStateBus、26 个模块实现或 persona compiler。
> §17 中 Q13-Q17 仍保留为 Part B 的已确认决策索引，后续实现前以架构文档为准。

---

## 17. 首轮确认（v0.2 决策表）

> 本节是 v0.2 草案的**唯一权威决策入口**。§13 / §15.7 / §16.12 各开放问题块均已 backlink 至此；后续修订必改本节，不要回填那些块。
>
> 范围声明：本节不触动 Omubot 运行时代码，也不修改 `persona-spec-format.md`；仅作为 v0.2 草案的合规摘要 + 实施前置输入。Q10 在原 §15.7 没有给推荐，本节同步补一个推荐并一并锁定。

### 17.1 决策表

> 2026-05-24 用户确认：按下表当前建议执行。`输入来源 / 归属` 用于区分 importer 首版合同、审计整改项和 Part B 架构项。

| # | 主题 | 决策 | 理由 | 输入来源 / 归属 |
| --- | --- | --- | --- | --- |
| Q1 | persona_id 命名空间 | **B**：v2 独立命名空间（如 `fengxiaomeng-v2`），与 soul/ 并存 | v2 compiler 未落地前隔离运行时风险 | 用户确认 / Part A |
| Q2 | source.md 是否进 git | **A**：进 git | 与 `config/soul/identity.md` 一致，可 review、可与 maintenance-log 联动；`.draft` / `source.frozen.md` / `_pending_freeze` 走 ignore | 用户确认 / Part A |
| Q3 | LLM 抽取模型 | **A**：新增 `persona_import` task profile | importer 非热路径，默认可绑定低成本 JSON 能力模型；具体模型由配置选择，不在代码里硬编码 | 审计整改 / Part A |
| Q4 | 默认模板存放 | **A**：`config/persona/_defaults/v2/` | 与 persona 平级、git 可跟踪、admin 可直接 diff | 用户确认 / Part A |
| Q5 | 表达素材 review_status | **A**：`candidate / approved / muted` 三态 | 与 [services/style/store.py](../../services/style/store.py) 对齐 | 用户确认 / Part A |
| Q6 | admin 编辑 source.md 鉴权 | **B**：admin token + freeze 二次确认 | compiler 前 freeze = Pending Freeze，仅暂存；Schema Freeze 写运行时前必须二次确认 | 审计整改 / Part A |
| Q7 | S6 admin SPA 高亮是否本期上 | **B**：S1-S5 + CLI 先上；UI 留到 S10' | S1-S5 风险更低，UI 与 v2 compiler 一起上更经济 | 用户确认 / Part A |
| Q8 | 默认模板首版覆盖范围 | **A**：仅 guard / eval / trace 三份 | 与 v2 compiler 落地节奏对齐；其余文件首版生成 skeleton | 用户确认 / Part A |
| Q9 | v2 文件清单扩展 | **A**：15 文件 + `modules/` | state.yaml + thinker.yaml + system.yaml 三者职责差异大 | 审计交集 / v2.1 spec |
| Q10 | mood prompt 模板归属 | **B**：留代码；`state.yaml.mood` 只暴露阈值 | mood prompt 触及 voice 出口；用户写易污染语气，留代码减小 v0.2 表面 | 用户确认 / Part A |
| Q11 | affection 文案归属 | **B**：进 `relationships.yaml.affection.phrasing` | 与数据共置，避免 voice/relationships 双写 | 用户确认 / Part A |
| Q12 | post_process 归属 | **B**：进 `adapter.yaml.send` | kaomoji enforce / humanizer / segmenter 都涉及 QQ 平台特性 | 用户确认 / Part A |
| Q13 | 模块边界粒度 | **A**：接受首版 26 + 7 预留 | state/runtime/calendar 三者职责差异大，不合并；7 预留只占 schema 不参与 DAG | 用户确认 / Part B |
| Q14 | L2 单轮关模块的主导 | **C**：thinker 自动 + adapter event flag 并存 | thinker 决定语义层（如 sticker_off），adapter 决定平台层（如 muted_user） | 用户确认 / Part B |
| Q15 | "禁裸读 store" 检查方式 | **C**：静态（importer 扫 `module.yaml` + Python AST）+ 运行时（store 加 caller 拦截） | 静态防新写漏洞，运行时兜底既存调用 | 用户确认 / Part B |
| Q16 | source.md 是否暴露模块开关 | **A**：source.md §12/§13 暴露开关 + 覆写；admin SPA 仍是主要入口 | source.md 是版本化真相源；SPA 改完最终回写 source.md | 用户确认 / Part B 接口 |
| Q17 | persona compiler v2 落地节奏 | **B**：importer 先产 draft；compiler 后消费 draft | 灰度更安全，与 Q1 / Q7 形成一致路径 | 用户确认 / Part B |

### 17.2 v0.2 修订清单（按上表导出）

> 本清单是 v0.2 草案的实施合同。已完成项打 ✅；未完成项保留至 v0.2 → 实施版的 PR/迁移清单。

1. **文档锚点** ✅
   - 文首 §0 阅读次序声明锚到 §17。
   - §13 / §15.7 / §16.12 三段「推荐」字样改成「**已确认**」并 backlink 到 §17（已回填，2026-05-24）。
   - `persona-spec-format.md` 已增 v2.1 扩展（15 文件 + `modules/` 子目录）；完整 SystemModule runtime 细节已拆到 `system-module-architecture.md`。
2. 命名空间与 git 入径（Q1 / Q2）
   - `config/persona/<persona_id>-v2/` 为 v2 默认目录形态；soul/ 不动。
   - `.gitignore` 新增 `config/persona/*/.draft/`、`config/persona/*/source.frozen.md`、`config/persona/*/_pending_freeze/`；`source.md` 进 git。
3. importer 抽取链（Q3 / Q4 / Q5）
   - importer LLM 抽取器走全局 LLM client，task = `persona_import`；默认推荐低成本 JSON 能力模型，但具体 profile 由 BotConfig / admin provider 配置决定。
   - 默认模板放 `config/persona/_defaults/v2/`（首版只含 guard.yaml / eval.yaml / trace.yaml）。
   - 表达素材 review_status 三态 = `candidate | approved | muted`，importer 写出全部为 `candidate`。
4. importer 范围（Q7 / Q8）
   - 首版交付 = 解析器 + 抽取器 + 校验器 + 默认模板 + CLI（`uv run python -m services.persona.importer <persona_id>`）。
   - admin SPA 高亮 UI 推迟到 S10'；本轮不做前端预研。
   - 默认模板补全（runtime / state / thinker / adapter / system / module）随后续 S10' / Part B 消费端一并上；Part A 首版只生成 skeleton。
5. spec 扩容收口（Q9 / Q10 / Q11 / Q12）
   - 文件清单按 `persona-spec-format.md` v2.1 = 15 + `modules/`（覆盖原 Q9-A 的 14 文件方案）。
   - `state.yaml.mood` 仅暴露阈值与 prompt 占位字段（`prompts: {}`，schema_only）；mood 文案保留在 [plugins/schedule/mood.py](../../plugins/schedule/mood.py)。
   - `relationships.yaml.affection.phrasing` 收 affection tier 文案（群聊脱敏 + 私聊明示）。
   - `adapter.yaml.send` 收 post_process（segmenter / humanizer / kaomoji enforce / reply_prefix）；voice.yaml 不再保留 post_process 字段。
6. 模块体系收口（Q13 / Q14 / Q15 / Q16 / Q17）
   - 26 一等公民 + 7 预留归属 Part B [system-module-architecture.md](./system-module-architecture.md)；`reserved: true` 不进 DAG。
   - L2 单轮关模块同时由 thinker `ThinkDecision.disable_modules: list[str]` 与 adapter `event.flags.disable_modules` 触发；二者并集后取严。
   - 不变量 #8（禁裸读 store）= 静态检查（importer scanner）+ 运行时检查（`RuntimeStateBus` caller 校验 `state_consumes` 白名单）。
   - source.md 增 §12「模块开关」（checkbox_md）、§13「模块定制」（yaml_patch），SPA 修改最终回写 source.md。
   - compiler v2 排在 importer Part A 之后；灰度 feature flag = `persona_v2.enabled`，默认 off。

### 17.3 本轮明确不进 v0.2 草案的项

- v2 compiler（S11'）的实现，以及 admin SPA 高亮 UI（S10'）。
- 7 个 reserved 模块（`state.world` / `state.desire` / `state.values_drift` / `runtime.planner` / `state.intimacy` / `observer.feedback` / `self.reflection`）的实际逻辑，仅占命名空间。
- mood prompt / scheduler 全局上限 等任何"在代码里"的字段挪进 source.md。
- 任何对 Omubot 运行时（`services/` / `plugins/` / `kernel/`）的修改。

### 17.4 实施前置与当前状态

1. ✅ 把决策回填到 §13 / §15.7 / §16.12（已完成 2026-05-24）。
2. ✅ 在 [docs/persona-spec-format.md](../persona-spec-format.md) 增 v2.1 扩展，与 15 文件 + `modules/` 对齐（已完成，2026-05-24）。
3. ✅ §16 SystemModule / RuntimeStateBus 已拆到 [system-module-architecture.md](./system-module-architecture.md)（已完成，2026-05-24）。
4. ✅ D3 迁移清单（旧→新 文件 / 路由 / 菜单 / API）已放 `docs/migrations/persona-v2-importer.md`。
5. ✅ Part A S1-S5 后端/CLI 首版已完成：`services/persona`、`persona_import`、CLI、`/api/admin/persona/*`、Pending Freeze。
6. ⏳ 后续仍需单独发令：S6/S10' admin SPA、v2 compiler / Schema Freeze、Part B RuntimeStateBus/SystemModule。

---

## 18. GPT 审计记录（2026-05-24）

> 审计人：GPT
>
> 审计范围：本节审计 `Persona Source Importer` 方案与当前 `persona-spec-format.md`、admin API 路由、LLM 请求契约、现有配置入口之间的一致性。结论仅针对方案落地风险，不代表已进入实现。

### 18.1 总结结论

当前方案方向成立，但**不能直接进入实现**。主要阻断不是 importer 思路本身，而是文档同时承载了两件事：

1. `source.md -> v2 draft` 的导入器方案；
2. Persona v2 runtime / SystemModule / RuntimeStateBus 的清洁重构方案。

这导致目标 schema、首版交付范围、Freeze 行为、API 路径和 LLM 路由之间存在冲突。进入实现前应先做一次收口：把 importer 首版压回“schema draft + CLI + report”，把运行时模块体系拆到独立架构文档。

### 18.2 阻断项

1. **目标 schema 未对齐。** 本方案文首已经声明目标为 `15 文件 + modules/`，但 `docs/persona-spec-format.md` 仍是 12 文件目录结构，文件优先级和编译契约也没有纳入 `state.yaml` / `thinker.yaml` / `system.yaml` / `modules/`。实现前必须先升级 `persona-spec-format.md`，否则 parser、默认模板、validator、compiler 没有同一个权威目标。

2. **首版输出合同自相矛盾。** 方案目标要求产出全部 `15 + modules` draft，并为字段保留 `source_span` / `confidence`；但流水线图仍写 `.draft/*.yaml（12 个）`，Q8 又确认首版默认模板只覆盖 `guard.yaml` / `eval.yaml` / `trace.yaml`。这会让 S1-S5 无法产出完整且可校验的 draft。需要明确首版到底是完整 15 文件 skeleton，还是 partial draft。

3. **Importer 范围被 §16 放大成运行时重构。** 前文说本文不修改 Omubot 运行时代码，但 §16.11 把实施切片改为 `services/system_module/`、`RuntimeStateBus`、26 个模块、compiler 和灰度切流。这已经不是 importer 项目。建议拆分为两个文档：`source importer` 只负责 source 解析、抽取、校验、draft/report；`system module/runtime v2` 单独管理 RuntimeStateBus、DAG、模块实现和 compiler。

4. **Freeze 语义冲突。** UI 章节说 Freeze 会把 `.draft/` 拷到 `config/persona/<id>/` 并写维护日志；风险章节又说 v2 compiler 未落地前 Freeze 不写正式路径，只落 `_pending_freeze/`。首版必须统一为 `Pending Freeze` 或 `Schema Freeze`，在 compiler dry-run 存在前不得写正式 persona 目录。

### 18.3 高风险项

1. **API 路径不符合现有后台约定。** 方案写 `POST /api/persona/import`、`GET /api/persona/draft/<id>`、`POST /api/persona/freeze/<id>`，但当前后台 API 聚合器统一挂载在 `/api/admin`。应改为 `/api/admin/persona/import`、`/api/admin/persona/draft/{id}`、`/api/admin/persona/freeze/{id}`。

2. **LLM 模型硬编码无法按现有路由落地。** 方案固定 `claude-haiku-4-5` 且不读 BotConfig，但当前 LLM 请求契约要求调用带 `LLMTask`，现有任务列表没有 `persona_import`；本地配置也只声明了当前可用 profile。建议新增 `persona_import` task profile，由配置选择模型，而不是在 importer 内硬编码模型 id。

3. **draft YAML 内嵌元数据会冲突正式 schema。** 方案要求 draft 字段写成 `{ value, source_span, confidence, extractor }`，但现有 v2 spec 示例字段是普通标量/数组。需要二选一：定义独立 draft schema，或让正式 YAML 保持纯净，把来源与置信度全部放进 `_import_report.json`。

4. **`hard_rule 必须可机器化` 过严。** 很多 hard rule 是语义约束，不能稳定映射为 regex/pattern。建议把规则拆为 `pattern_guardable`、`judge_guardable`、`eval_only` 三类，避免为了通过校验写出虚假的 hard_check。

### 18.4 次要修正项

- 默认模板出处写到 `config/runtime.toml`，但当前项目实际配置入口是 `kernel/config.py` / `config/config.json`。
- Q7 已确认 UI 延后到 S10'，但前文仍把 admin 双栏高亮和 Freeze 作为首轮交互保证；建议首版文档删去 UI 验收，只保留 CLI + JSON report。
- `.gitignore` 规则应在 importer 第一次落盘前补齐：`config/persona/*/.draft/`、`config/persona/*/source.frozen.md`、`config/persona/*/_pending_freeze/`。

### 18.5 建议收口顺序

1. 先升级 `docs/persona-spec-format.md`，确认 `15 文件 + modules/` 是唯一权威 schema。
2. 把 importer 首版限定为：`source.md -> draft + _import_report.json + CLI`，不实现 RuntimeStateBus 和 SystemModule。
3. 把 §16 runtime/module 体系拆成独立架构文档。
4. 明确 compiler 前 Freeze 只落 `_pending_freeze/`，不写正式 persona 目录。
5. 新增 `persona_import` LLMTask/profile，避免 importer 硬编码模型。

### 18.6 审计状态

- 结论：**需整改后再进入实现**。
- 未执行项：本节未跑测试，未修改运行时代码。
- 后续动作：待用户确认后，优先执行 `persona-spec-format.md` 对齐与 importer/runtime 文档拆分。

---

## 19. deepseek 独立审计报告（2026-05-24）

### 19.0 审计说明

审计人：**deepseek**

审计范围：§0–§17 全文（独立形成判断，不受已有审计或其他文档影响）。

已独立对阅：

| 文件 | 核实内容 |
|---|---|
| `docs/persona-spec-format.md`（934 行） | v2 spec 当前文件清单是否包含 state.yaml/thinker.yaml/system.yaml/modules/ |
| `docs/tracking/persona-system-research.md`（3044 行） | R4.3/R4.4 guard/eval 模板是否已文档化 |
| `docs/ai-persona-generation-rules.md` | v1 生成规则当前范围 |
| `config/soul/identity.md` / `config/soul/instruction.md` | 现行 v1 运行时人设文件 |
| `services/llm/thinker.py`（403 行） | ThinkDecision 是否存在（§15.2 #20 声称） |
| `services/llm/client.py`（2000+ 行） | guard / soft_judge 是否存在于当前运行时 |
| `plugins/style/plugin.py` | `on_pre_prompt` 与 `_provider_superseded` 变量 |
| `config/persona/_defaults/v2/` | 默认模板目录是否存在 |

### 19.1 总体结论

**方案的方向正确但膨胀过快。**

v0.2 草案的核心洞见——"创作型 6 文件由人写、工程型文件由系统提供默认模板"——是正确的。§15 的 21 注入源全表审计也是高质量的工作，成功暴露了 v2 原 12 文件方案对 mood/schedule/affection/thinker 等 runtime 状态机的系统性遗漏。

**但这个发现被用来推动了一次远超 importer 范围的设计扩张**：§16 引入了 SystemModule（26 一级公民 + 7 预留）、RuntimeStateBus、SwitchMatrix、DAG 编译期校验——这相当于对 omubot 的 plugin/provider/store 三层体系做了完整的替代架构提案，而不是"人设源→ YAML 的导入器"。

**如果将 §16 视为独立的长远架构方向文档（RFC），它是高质量的设计。如果将 §16 视为 importer 方案的前置条件，它是错误的——让一个导入器承载了整个运行时架构重写的依赖。**

### 19.2 阻塞项（5 条）

| # | 严重度 | 发现 | 证据 | 修订要求 |
|---|---|---|---|---|
| B1 | **致命** | 方案 §16.8 声称 v2 spec 是"15 文件 + `modules/`"，引用 `persona-spec-format.md` 作为权威来源。但 **该文件当前未包含 `state.yaml`、`thinker.yaml`、`system.yaml` 的任何定义**。 | `grep -c 'state.yaml\|thinker.yaml\|system.yaml' docs/persona-spec-format.md` 三行全返回 **0**。spec 文件 934 行，全是 12 文件 v2 规范的字段细节（persona/voice/knowledge/relationships/memory/examples/runtime/capabilities/adapter/guard/eval/trace）。新增的三文件是方案内部的提案，尚未回写到 spec。 | 在进入实施前，先完成 spec 扩容（`persona-spec-format.md` 新增 state/thinker/system 三个文件的 schema 定义），或把方案中的 spec 引用改为"proposed addition"而非既成事实。当前写法会让实施者去 spec 文件找 schema 时发现什么都不存在。 |
| B2 | **致命** | 方案 §8 声称"默认模板独立维护在 `config/persona/_defaults/v2/`"。**该目录不存在。** gurad/eval/trace 三份默认模板没有实质文件。 | `ls config/persona/_defaults/v2/` → No such file or directory。§8.1/8.2/8.3 的 YAML 块只存在于方案正文中，未以文件形式落地。 | 进入实施前至少创建目录并写入 guard/eval/trace 三份 YAML 文件；方案中的 YAML 代码块与文件的对应关系需要标注文件名和路径。 |
| B3 | **阻塞** | §16 的 SystemModule 架构（26 模块 + RuntimeStateBus + SwitchMatrix + DAG）被作为 §3 中"创作 vs 工程"分层的自然延伸，但**方案自身未解释为什么一个导入器需要依赖完整的运行时架构重写**。§4.1 的目录结构中 `.draft/` 是 importer 输出，§16 是运行时架构——两者之间缺一条明确的边界线。 | §3 说"importer 只对创作型 6 文件做 LLM 抽取"——这在 §16.9 之前是成立的。但 §16 引入后，"创作型"定义变了（mood 阈值、thinker 决策原则、scheduler 参数都进了 source.md §9-§13），importer 需要理解 SystemModule 的 `persona_bindings` 和 `state_consumes` 来校验。这意味着 importer 必须等待至少 S1'（RuntimeStateBus 接口骨架）完成才能跑全量校验。 | 将 §16 拆出独立文档（如 `docs/tracking/system-module-architecture.md`），importer 方案仅保留引用。**或者**在方案开头明确写："本方案含两个独立提案——Part A（§0-§15）importer、Part B（§16）运行时模块架构重写。Part A 不依赖 Part B 进入实施。"当前两部分的捆绑会拖延 importer 的落地。 |
| B4 | **阻塞** | §15.2 注入源全表（#10-#12 mood/schedule/calendar、#20 thinker、G1-G7 scheduler）被用于论证"必须扩 v2 spec 到 15 文件"。但这 21 个注入源中，**部分（如 Mood block）的 prompt 文案当前在 `plugins/schedule/mood.py` 中被硬编码为 `_MOOD_PROMPTS` 常量**——Q10 用户也确认"mood prompt 留代码"。如果 mood prompt 本身就是代码常量而非配置文件，为什么 mood 的阈值需要进一个新 YAML 文件而非直接留在现有 `config/talk_schedule.json`？ | §15.4 P0 列 Mood/Schedule 为"必须补"，但 Q10 又确认 mood prompt 不进 spec。阈值是否需要独立文件是可以讨论的，但方案未给出"为什么是 YAML 而不是 JSON，为什么独立而不是扩展现有 config"的论证。 | 为每个新增 YAML 文件给出"为什么不在现有 config/ 结构中扩展"的简短理由（一行即可）。例如 state.yaml 独立是因为 mood/schedule/calendar/board 都共享"单轮有效的运行时状态"语义，与 `config/talk_schedule.json` 的"静态配置"不同。 |
| B5 | **阻塞** | 方案声称"首轮确认（§17）已锁定 17 项"，但**该确认是方案内部的决策表，不是外部审计**。§17.1 的 17 条决策全部由方案自身给出推荐选项并"确认"——这是一个自洽的提案，不是经过外部审计或用户独立验证的证据。 | §17 标题"首轮确认（v0.2 决策表）"，但文中唯一的外部输入是"用户审计于 2026-05-23 给出 Q1~Q6 决议"且选项与方案推荐完全一致。Q7-Q17 没有标注任何外部输入来源。 | 在 §17 的每条决策后标注输入来源（"用户选择" / "方案推荐" / "外部审计建议"）。未标注来源的条目默认视为"方案推荐待外部确认"，不能直接作为实施合同。 |

### 19.3 条件项（6 条）

| # | 严重度 | 发现 | 证据 | 建议 |
|---|---|---|---|---|
| C1 | **高** | `source.md` 模板（§4.2）约 200 行 markdown，与 v1 的 `identity.md + instruction.md` 双文件门槛相比**不降反升**。用户需要理解"句子形态""节奏""禁用句式""expression_library""slang_policy""已知事实/不知道边界/禁说事实""关系倾向""经历种子""模块开关"等大量概念才能写出合格的 source.md。 | v1 示例：`config/soul/identity.md` 是自由形式 markdown。v2 source.md 模板中 §3.4 要求每条表达素材标 `use_when` / `avoid_when`，§7 要求 5 正例 + 3 反例并标注场景、对错对比。 | 增加一个"最小 source.md"模板（~30 行，只覆盖 persona/voice/knowledge 必填），让用户 5 分钟写完就能跑 importer 看到 draft。完整模板作为"进阶"，不设为首屏。这是 importer 可用性的关键。 |
| C2 | **高** | LLM 抽取器（§5.2）固定 `claude-haiku-4-5` 且锁死 model id。如果该模型不可用（余额不足、中转站下线、API 变更），importer 完全不可用——没有任何 fallback 路径。 | §5.2："禁止补全没写的内容"约束需要 LLM 理解 source_span 映射。这是小模型也能胜任的受限抽取，不需要仅绑定一个特定模型。 | 加一个环境变量 `PERSONA_IMPORTER_LLM_MODEL` 允许覆盖模型 id，默认 `claude-haiku-4-5`。同时文档说明"任何支持 JSON schema 输出的小模型均可作为 fallback"。 |
| C3 | **中** | §15.3.C 将 `context_retrieval RRF 权重`、`context packing prompt-injection guard` 等列为"完全漏掉"的注入源，并在 §16.9 将它们收口到 runtime.yaml / guard.yaml。但**这些字段与"人设"的关联是间接的**——它们更像是系统运维配置而非 persona 创作内容。不加区分地把所有注入源都纳入 persona YAML，会模糊"这个人是谁"和"系统怎么跑"的边界。 | §15.3.C 列了 12 个"完全漏掉的注入源"，其中至少 4 个（StateBoard、RRF 权重、后处理切段参数、per-group at_only/sticker_mode 开关）与 persona 创作无关。 | 在 §3 的"创作 vs 工程"二分类基础上增加第三类——"运维配置"——这些字段不进 source.md，仅在 admin SPA 的可视化配置面板中调整。避免让写 source.md 的用户困惑"为什么我还要关心切段上限"。 |
| C4 | **中** | §6 字段映射表在 v0.1 有 20+ 行，到 §16.9.2 新增 11 行，总计 30+ 行映射。但**没有任何一行映射标注"当 source.md 这节为空时的退化行为"**。如果用户只写了 §1-§4（必填），§5-§13 全空，importer 是报 error、warn、还是静默套用默认模板？ | §6 的表只有"必填/选填"二元标注。但选填字段的退化行为（默认 YAML 值 vs 空键 vs 不生成该字段）直接影响 draft 的正确性。 | 在映射表中增加"缺失时行为"列，枚举三种：静默默认、warn + 默认、error。 |
| C5 | **中** | §7.2"引用闭环检查"定义的 4 条规则和 §16.9.4 新增的 8 条校验规则中，有几条依赖 v2 compiler 的存在才能实际验证（如 "hard_rule 必须能映射到 guard.hard_check.patterns" 需要 compiler 的 pattern 引擎）。但 importer 的定位是独立于 compiler 运行的。 | §7.2："每条 hard_rule 必须可被 guard.hard_check 检查"依赖 guard 引擎。如果 compiler 未落地，该检查只能是正则模式匹配，不能验证语义正确性。 | 区分 importer 可独立完成的检查（语法层：source_span 存在、confidence ≥ 0.6、必填字段非空）和需 compiler 配合的检查（语义层：hard_rule 可被 guard 检查、critical_failure 覆盖）。后者在 compiler 未上线时降级为 warn 而非 error。 |
| C6 | **低** | 方案在 §0 说"v2 schema 尚未冻结时，importer 只生成 draft"——这是正确的保守策略。但 §16 的 SystemModule 架构本质上是**在 v2 schema 冻结前就锁定了 schema 细节**（15 文件每个都有指定字段清单）。这自相矛盾。 | §0："v2 schema 尚未冻结"。§16.8：15 个 YAML 文件的具体 schema 被分别描述。 | 在方案开头明确标注："本方案对 v2 schema 的描述是提案级（proposal-level），仅用于 importer 内部映射。最终 schema 以 persona-spec-format.md 的后续版本为准。importer 的字段映射实现使用松 schema（unknown keys allowed），避免紧跟 spec 迭代。" |

### 19.4 已确认可行项

| 确认项 | 核实结果 |
|---|---|
| v0.2 草案的核心二分法（创作 vs 工程）正确 | §3 的分类完整覆盖了 persona 的所有运行时注入源 |
| §15 全流程注入审计的方法和证据链扎实 | 21 个注入源全部标注了 file:line，对阅了实际代码路径 |
| thinker 确实存在且本方案正确识别了它的遗漏 | `services/llm/thinker.py` 403 行，ThinkDecision 在 §15.2 #20 |
| style plugin 的 superseded 逻辑存在 | `plugins/style/plugin.py` 含 `_provider_superseded` 变量（grep 命中 4 次） |
| v1 双文件运行时确实存在 | `config/soul/identity.md` + `config/soul/instruction.md` 确认存在 |
| `persona-system-research.md` 确实存在且含 guard 讨论 | 3044 行，101 处 guard 引用 |
| source.md 模板的必填/选填分层设计清楚 | §4.3 的表格准确区分了必写字段和选填/选改字段 |
| §9 不变量（不创造/不混层/hard_rule 可机器化等 6 条）设计合理 | 6 条约束都在语法层可校验，不依赖 v2 compiler |

### 19.5 建议的修订顺序

| 优先级 | 修订内容 |
|---|---|
| **立即** | 完成 B1：在 `persona-spec-format.md` 中增加 state/thinker/system 三个文件的字段 schema（最小版本，先有键名 + 类型） |
| **立即** | 完成 B2：创建 `config/persona/_defaults/v2/` 目录，写入 guard.yaml / eval.yaml / trace.yaml 三份文件 |
| **进入 PR 前** | 完成 B3：将 §16 拆出或标注边界——importer 不依赖 SystemModule 进入实施 |
| **进入 PR 前** | 完成 B5：标注 §17 每项决策的输入来源 |
| **实施阶段** | C2：加 `PERSONA_IMPORTER_LLM_MODEL` 环境变量 |
| **实施阶段** | C3/C4：增加"运维配置"类别和映射表退化行为列 |

### 19.6 最终裁决

**方案的核心设计（创作型 6 文件抽取 + 工程型默认模板 + 受限 LLM 抽取 + 校验与高亮）是正确的，可以在修正 B1/B2/B3/B5 后进入分阶段实施。**

**§16 的 SystemModule 架构是独立的高质量提案，但不应作为 importer 的前置条件。** 将 §16 拆为独立文档（或明确标注为 Part B / 长远方向），importer（Part A）即可按 §10 的 S1-S7 切片独立推进——工期不会更长，风险反而更小。

---

## 20. 维护纪律（2026-05-24 收口）

> 目的：防止 importer 主方案继续膨胀，保持 Part A 可实施。

1. 本文档只保留 Source Importer Part A 的稳定合同、审计摘要和关键决策索引。
2. Runtime/SystemModule 细节写入 [system-module-architecture.md](./system-module-architecture.md)，不得回填到本文 §16。
3. 执行过程、逐步风险和完成证据写入 [persona-source-importer-remediation-execution.md](./persona-source-importer-remediation-execution.md)，不得继续追加长篇过程日志到本文。
4. 迁移表、旧→新路径、API/菜单变更写入 `docs/migrations/persona-v2-importer.md`。
5. 本文档软上限 1500 行；超过时必须拆分或删除已过时草案段落，审计历史除外。
6. 新增字段必须同步回答三件事：来源、缺失时行为、是否属于 Part A 首版。

方案最被低估的风险不是技术架构，而是**一个尚未进入实施的草案已经膨胀到了 2000+ 行、26 个模块、15 个 YAML 文件**。这会让后续每个技术决策都背负"已文档化"的重量。建议从现在开始，每次修订都删掉一段可推迟的内容，而不是只追加新内容。
