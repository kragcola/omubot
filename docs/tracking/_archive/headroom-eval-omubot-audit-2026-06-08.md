# Headroom 项目考察 + Omubot 使用审计

> 类型：外部项目考察 / 选型审计
> 日期：2026-06-08（2026-06-09 修正审计对象 + 补第一档实测）
> **审计对象修正**：初版误把 headroom 当成「塞进 omubot 运行时」评估，结论是不接入。用户本意是 **omubot 的开发工具链**（Claude Code + Codex 双代理流程），这才是 headroom 的设计主场，结论反转。本文主体改为**开发流程审计**；运行时结论降级为附录 A（仍然成立：不要塞进 bot 运行路径）。

---

## 0. 结论先行（开发流程视角）

- **对 omubot 的开发流（Claude Code + Codex）是合适的**，但**分三档试，不能一把梭**。
- **第一档（零风险，强烈建议先吃）**：`headroom learn` 离线挖历史 session 写 `CLAUDE.md`/`AGENTS.md`。不进 LLM 路径、不碰中转。**前提先 `HEADROOM_TELEMETRY=off`**。
- **第二档（中风险）**：headroom 当 **MCP server** 挂给 Claude Code，提供 `headroom_compress/retrieve`，只在工具层介入，**不动 endpoint**。
- **第三档（高风险，需先做兼容性验证）**：`wrap claude`/`wrap codex` 走本地 proxy。上之前必须先验证 headroom proxy 能否代理 Codex `responses` 制式 + 转发到中转 `ccapi.yuanapi.org`，以及 Claude 经 proxy→中转 的 cache 命中率不掉。
- **附录 A（运行时视角，不变）**：**严禁在 omubot 生产 LLM 路径插 headroom proxy**——会破坏 `apply_cache_breakpoints` 的 cache 对齐。

---

## 一、Headroom 是什么

- **仓库**：[chopratejas/headroom](https://github.com/chopratejas/headroom)（PyPI/npm `headroom-ai`，Apache 2.0，Python 3.10+ / TS）
- **定位**：面向 AI agent 的**本地上下文压缩层**——在 tool 输出、日志、RAG chunk、文件、对话历史进入 LLM 之前压缩，宣称同样答案、60–95% 更少 token。
- **文档**：<https://headroom-docs.vercel.app/docs>
- **模型**：[Kompress-base on HuggingFace](https://huggingface.co/chopratejas/kompress-base)（自训文本压缩模型）

### 接入形态（4 种）

| 形态 | 用法 | 目标场景 |
|---|---|---|
| Library | `compress(messages)` (Py/TS) | 自己代码内联 |
| Proxy | `headroom proxy --port 8787` | 任意语言、零代码改动（OpenAI + Anthropic-compatible，可放中转前面） |
| Agent wrap | `headroom wrap claude\|codex\|cursor\|aider\|copilot\|gemini` | **CLI 编码代理**（← omubot 开发流主场） |
| MCP server | `headroom_compress / retrieve / stats` | MCP 客户端（Claude Code / Cursor） |

### 核心组件

- **ContentRouter** — 检测内容类型，分派压缩器
- **SmartCrusher**（JSON：dict 数组 / 嵌套 / 混合类型，70–90%）
- **CodeCompressor**（AST-aware via tree-sitter：Py/JS/Go/Rust/Java/C++）
- **Kompress-base**（自训 HF 文本模型，训练于 agentic traces）
- **CacheAligner** — 稳定 prefix 让 Anthropic/OpenAI 的 KV cache 真命中
- **CCR（可逆压缩）** — 原文不删，LLM 按需 `headroom_retrieve` 取回
- **Cross-agent memory** — Claude/Codex/Gemini 共享记忆、per-project SQLite + HNSW、自动去重
- **`headroom learn`** — 挖失败 session，把纠正写进 `CLAUDE.md` / `AGENTS.md` / `GEMINI.md`

### 客观评价

**亮点**：
- **CCR 可逆**是真差异点。RTK / lean-ctx 等竞品有损不可逆；headroom 保原文 + 检索取回，「压错了还能救」。
- **对比表诚实**：自己列出 RTK / lean-ctx / OpenAI Compaction 的边界。
- 基准自洽（GSM8K ±0.000、TruthfulQA +0.030），但均 N=100 小样本、项目方自测。

**警惕**：
- **很新、单作者主导**（chopratejas 个人项目，虽上了 Trendshift）。README 自承 Copilot CLI 的 Windows/Linux/Docker auth 路径「尚未真机验证」——成熟度不均。
- **默认开匿名遥测**（`HEADROOM_TELEMETRY=off` 才关）——对数据外发敏感的项目接入前**必须先关**。
- 引入本地常驻进程/依赖 = 多一层故障面。

---

## 二、Omubot 开发流程审计（主体）

### 开发流现状（实测 2026-06-09）

| 维度 | 现状 |
|---|---|
| 双代理 | Claude Code + Codex 并行开发同一个 repo |
| **endpoint** | **两者都走同一个中转 `ccapi.yuanapi.org`**（Claude `ANTHROPIC_BASE_URL`，Codex `base_url`） |
| Codex wire | `responses` 制式（`~/.codex/config.toml`） |
| 跨代理交接 | 纯文件：`ACTIVE.md`(36 行) + `maintenance-log.md`(**14407 行**) + `.claude/handoff/` + 4 个 skills 双份镜像（`.claude/skills` ⇄ `.agents/skills`） |
| continuity | Codex 靠 `.codex/hooks.json` SessionStart 注入快照（它会失忆）；Claude 靠 TodoWrite + 自动 compaction |
| 成本敏感度 | 对 token/cache 命中率有专门监控（`services/llm/usage_cli.py`、慢调用告警）；记忆有中转 413 字节墙教训 |

### 为什么这次结论反转

headroom 的 `wrap claude\|codex`、cross-agent memory、`headroom learn` 全部为「多 CLI 编码代理共享开发上下文」设计，正好是 omubot 的开发拓扑。三个真实痛点都对得上：

1. **大体量 tool 输出吃 context**：`git status`（被截断）、全量 pytest 输出、大面积 grep、`docker build` 日志反复进 context。SmartCrusher（JSON 70-90%）+ log/diff 压缩正冲这些来。
2. **`maintenance-log.md` 14407 行 + 45 个 tracking 文档的 continuity 负担**：每次 Codex SessionStart 注入、每次 compaction 重读。CCR 可逆压缩能压「偶尔要全文、平时只要摘要」的场景，原文可 `headroom_retrieve` 取回。
3. **Claude/Codex 交接靠手写文件易漂移**：已用 `ACTIVE.md` 当交接总线；headroom SharedContext / cross-agent memory 是这套机制的自动化版（自动去重、agent provenance）。

### 三个真实拦路虎（上之前必验）

1. **中转 + Codex `responses` 制式兼容性（最大风险）**：headroom proxy 宣称 Anthropic-compatible，但 Codex 走 `responses` wire（非 `chat/completions`、非 Anthropic messages）。headroom 是否完整代理 `responses` + 透传到非官方上游 `ccapi.yuanapi.org`，README/llms.txt **都没保证**。同类于 [Codex 中转 413 根因] 教训：中转叠中转，任一层对字节/制式敏感就炸。
2. **Claude Code 现就跑在 `ccapi.yuanapi.org`**：`wrap claude` 要改 `ANTHROPIC_BASE_URL` 指向本地 proxy 再转中转——多一跳，proxy 重写可能影响中转缓存行为。
3. **默认遥测**：接入前必 `HEADROOM_TELEMETRY=off`，且需确认不外发代码/prompt。

### 分档建议

| 档 | 动作 | 风险 | 是否动 endpoint |
| --- | --- | --- | --- |
| 第一档 | `headroom learn` 离线挖历史 session | **零**（纯离线纯赚） | 否 |
| 第二档 | headroom 当 MCP server 给 Claude Code | 中（只在工具层） | 否 |
| 第三档 | `wrap claude`/`wrap codex` 走 proxy | 高（必先过兼容性验证关） | **是** |

---

## 三、第一档实测记录（headroom learn）

> 实测时间：2026-06-08 · 模式：dry-run（不写文件）· 范围：当前项目 + Codex（不扫旧项目）

### 安装

- 开发机 Python 3.12.6（python.org framework 版）`pip` 装 `headroom-ai` 报 `CERTIFICATE_VERIFY_FAILED`（该 Python 不读系统钥匙串证书）。**绕过：用 `uv pip install`**（uv 自带证书处理）装进隔离 venv `/tmp/headroom-eval`，成功（含 torch/transformers/tree-sitter，version 0.23.0）。**不污染开发机全局 / 项目 .venv。**
- 全程 `HEADROOM_TELEMETRY=off`。

### 执行事实（与预期不符，重要）

- **没用 `ANTHROPIC_AUTH_TOKEN` / ccapi 中转**：headroom auto-detect 了本地 `codex` CLI 当分析后端（`No API key found — auto-detected codex CLI as LLM backend`）。即分析**没花中转额度**，但 session 内容喂给了 codex CLI（它自己走 codex 的 `responses` endpoint）。
- `learn` 在 0.23.0 是扁平接口（无 `status`/`analyze` 子命令，旧帮助过期）；默认 dry-run，`--apply` 才写。

### 扫描规模与产出

| Agent | Sessions | Calls | Failures | 产出 |
| --- | --- | --- | --- | --- |
| Claude (omubot) | 15 | 24640 | 1107 (4.5%) | **LLM 分析 120s 超时 → 0** |
| Codex (omubot) | 43 | 41640 | 983 (2.4%) | **12 条建议** |

**Claude 侧最大数据源超时挂了**（`codex exec did not respond within 120s`）——headroom 用 `codex exec` 做后端、单次 120s 硬超时撑不住大 session，是工程缺陷。只有 Codex 侧出了产出。

### 12 条建议的去向（dry-run，全是 `[WOULD WRITE]`）

⚠️ 目标文件是 **`~/.codex/AGENTS.md` 和 `~/.codex/instructions.md`（全局 Codex 配置），不是 repo 内 AGENTS.md**。`--apply` 会污染全局 Codex 配置并带 "do not edit manually" 标记。

主要条目（按宣称省 token 排序）：Large Files(~10k) / Continuity(~9k) / Workspace(~6k) / Admin UI(~5k) / SQLite(~4k) / Runtime Commands(~3.5k) / Skills(~3k) / Environment(~2.5k) / File Paths(~1.5k) / Process Checks(~900) + 全局 instructions 的 CPA Tooling(~3k) / Sensitive Output(~800)。

### 评价：有用但不惊艳

**问题：**

1. **Claude 侧直接超时**，24640 calls 的最大源 0 产出。要救须 `--model` 指真 API 后端。
2. **大半是「已知的事」**：Large Files / Continuity / Workspace / Skills 这几条，CLAUDE.md / AGENTS.md 早写了。headroom 从失败 session「重新发现」了既有纪律，对文档纪律已重的项目边际价值有限。
3. **`--apply` 会写全局 Codex 配置**（`~/.codex/`），不是 repo 文件——手滑风险。

**真有增量的两条**（从真实失败挖出、文档里没有）：

- SQLite 只读用 `sqlite3 'file:storage/<db>.db?mode=ro&immutable=1'` —— 比 D5「pkill pytest」更细。
- macOS sandbox 里 `pgrep`/`ps` 会 `sysmond service not found`，prefer `docker compose ps`/`lsof -nP -iTCP:<port>`。

### 第一档结论

- **零生产风险已坐实**：dry-run 没写任何文件，没走中转额度，隔离 venv 不污染项目。
- **价值评级：中低**。对本仓（文档纪律已重）增量有限；真正有用的是 2 条环境操作建议，可手动吸收进 CLAUDE.md/AGENTS.md，**无需常驻 headroom**。
- **不推荐 `--apply`**（会写全局 Codex 配置）。
- 若要救 Claude 侧超时再评一次，须 `--model` 指定真 API 后端——但那会**走中转额度 + 把 session 外发**，性价比存疑。

---

## 四、压缩工序实测（用户最关心 —— 省 token 工序到底好不好用）

> 方法：隔离 venv 装 headroom-ai 0.23.0，用 `from headroom import compress` 在四类真实负载上实测。`compress()` 自带 `tokens_before/after/saved/compression_ratio`，role=assistant 模拟 tool 输出。

### 压缩率（真实负载）

| 负载 | 压缩前 (tok) | 压缩后 | 省 | transform |
| --- | --- | --- | --- | --- |
| pytest 800 行 PASSED | 9121 | 321 | **96.5%** | text |
| maintenance-log 50KB md | 14296 | 2081 | **85.4%** | log |
| JSON 记忆卡 300 条 | 15279 | 4511 | **70.5%** | smart_crusher |
| 大 tokenizer JSON 4MB | 1007788 | 616088 | **38.9%** | smart_crusher |
| git status ×6 | 6452 | 6452 | **0%** | protected:recent_code |

数字漂亮，但**压缩率只是一半，关键是压完还剩什么**。

### 保真度实测（决定能不能用 —— 比压缩率重要得多）

在 800 行 PASSED 里**故意埋一行 `FAILED` + `test_critical`**，压缩后检查是否保留：

| 内容类型 | 保真结果 | 判定 |
| --- | --- | --- |
| **pytest / 测试输出** | 埋入的 `FAILED` 行被采样删除 —— `FAILED 保留=False, critical 保留=False` | ❌ **危险，绝不能用** |
| **结构化 JSON**（记忆卡/manifest） | 压成 `[300]{category,content,id}` 表头 + CSV 行，结构完整、每条可读可还原 | ✅ **无损精简，最强项** |
| **大 markdown / 日志** | 保 `## 标题`/`**字段**`/关键命令，段内删正文行 | ✅ 当「导航索引」可用，当「逐字原文」不可用 |
| **git status / 代码** | 默认 protected 不压 | ⚪ 0 收益但无害 |

### 致命陷阱：有损压缩 + 关键信号稀疏 = 谎报

**pytest 丢 `FAILED`** 是最危险的反例：测试输出 99% 是噪声 PASSED、1% 是关键 FAILED，headroom 的 text 压缩器按「重复行采样」把 PASSED 删掉时，**连唯一的 FAILED 一起删了**。Agent 读到被压成「全过」的输出 → 得出错误结论。

CCR 可逆（`headroom_retrieve` 取回原文）理论上能救，但**对「自己不知道丢了关键信息」的场景救不了**——你不会去 retrieve 一个你以为全过的结果。

### 压缩工序结论

| 用途 | 判定 |
| --- | --- |
| 压 pytest/日志给 Claude Code 省 token | ❌ 最大省 token 项恰是最危险有损区，会谎报测试结果 |
| 压结构化 JSON（记忆卡/manifest/API 响应） | ✅ SmartCrusher 真无损省 70%，**但用库 `compress()` 单点调用即可，犯不上整套接入** |
| proxy/wrap 全量压开发流 | ⚠️ 等于把有损压缩无差别套到 pytest 上，风险 > 收益 |

**一句话**：压缩率真实漂亮，但**有损压缩用在「关键信号稀疏」的 pytest/日志上是陷阱**；真正安全好用的只有结构化 JSON 一类，而那一类用 `compress()` 库单点调用就够，不必接 proxy/wrap/常驻。

---

## 附录 A：运行时视角审计（不变 —— 不要塞进 bot 运行路径）

> 这是初版审计的对象。结论仍成立：作为 omubot **运行时**组件不接入。

### 逐能力对照

| Headroom 能力 | Omubot 现状（证据） | 判定 |
| --- | --- | --- |
| 对话历史压缩 | **已自研** `_compact` / `_compact_group`：LLM 压缩前半段 + circuit breaker 兜底 + `add_card` memo 提取（`services/llm/client.py:5548` / `:5635`） | ❌ 重复造轮子；omubot 版深绑人设/记忆卡 |
| Prompt cache 对齐（CacheAligner） | **已自研** `apply_cache_breakpoints` 4 断点 spine（`services/llm/client.py:2081`），usage 表实时追踪命中率（`:2376-2381`） | ❌ 已是单一可信源，引第三方会打架 |
| Cross-agent memory | 单 bot 单 provider，记忆卡是自有 `card_store` | ❌ 运行时场景不存在 |
| 图像压缩 | **已自研** pyvips 下采样 + 磁盘缓存 + 2MiB cap（`services/media/image_cache.py`） | ❌ 已覆盖 |
| Proxy 模式 | omubot LLM 走 `base_url`（`kernel/config.py:81`） | ⚠️ **致命错位**，见下 |

### 致命错位：proxy 会破坏 cache 对齐

Headroom 的 `compress(messages)` 工作在「messages 数组」层，而 omubot 把 **prompt 缓存断点的唯一可信源**放在 `apply_cache_breakpoints`（spine 会 strip 掉 caller 自带的 `cache_control` 再按 profile 重打断点）。若 headroom proxy 在中途重写 message，**会打碎 omubot 对齐的 cache prefix**，拉低在监控的命中率。

### 运行时唯一缺口：tool / RAG 输出压缩 —— 自研，不引依赖

- omubot 的 `search_cards` / RAG 结果、context plugin 注入块（`plugins/context/plugin.py`，`budget_total=6000`）**直接进 prompt**，只有 token budget 截断。
- 借鉴 SmartCrusher 思路在自有 RAG 注入路径做轻量压缩即可，**不引整个常驻框架**。

---

## 来源

- [chopratejas/headroom](https://github.com/chopratejas/headroom)
- [Headroom Docs](https://headroom-docs.vercel.app/docs) · [llms.txt](https://github.com/chopratejas/headroom/blob/main/llms.txt)
- [Kompress-base on HuggingFace](https://huggingface.co/chopratejas/kompress-base)
