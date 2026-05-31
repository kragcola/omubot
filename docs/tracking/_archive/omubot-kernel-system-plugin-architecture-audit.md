# Omubot 内核-系统-应用插件架构审计追踪

> 状态：完成
> 启动时间：2026-05-24
> 收口时间：2026-05-24
> 执行人：Codex
> 审计对象：Omubot 当前 `kernel/`、`services/`、`plugins/` 与相关 admin/API 支撑
> 方法约束：外部成熟项目必须拉取到 `.research/` 本地后以源码为证据；不以 README/简介作为结论依据；论文必须读取正文/抽取文本，不只看摘要。

---

## 0. 执行规则

1. 每个步骤开始前写清楚：细分动作、风险、回滚方式、验收证据。
2. 每个步骤完成后立刻回填：实际读取/拉取内容、代码证据、初步发现、自审遗漏。
3. 外部项目只作为对照样本；缺陷判断必须回到 Omubot 当前代码证据。
4. 本轮默认只产出研究和审计文档，不直接改内核运行时代码。
5. `.research/` 已被 `.gitignore` 忽略，外部源码/论文只放本地研究目录，不入库。

---

## 1. 步骤总览

| 步骤 | 名称 | 状态 | 完成证据 |
|---|---|---|---|
| A0 | 建立追踪文档与研究目录索引 | ✅ 完成 | `.research/` ignore + 本地 repos/papers 盘点 |
| A1 | Omubot 当前架构源码解析 | ✅ 完成 | `bot.py` / `kernel/bus.py` / `plugins/chat` / prompt pipeline 事实表 |
| A2 | 外部成熟项目拉取/更新与候选筛选 | ✅ 完成 | 10 个本地仓库 HEAD + 9 篇论文文本 |
| A3 | 外部项目源码拆分解析 | ✅ 完成 | 10 个项目源码证据表 |
| A4 | 论文正文解析与可落地原则提炼 | ✅ 完成 | 9 篇论文正文原则表 |
| A5 | 审计对比：缺陷、风险、改进方向 | ✅ 完成 | 10 项主审计矩阵 |
| A6 | 自审、证据链检查与最终报告收口 | ✅ 完成 | `git diff --check` + ignored 研究目录验证 |

---

## 2. 执行日志

### A0 建立追踪文档与研究目录索引

**开始前拆分**

1. 检查当前工作树，识别并行脏文件，避免把无关插件/版本改动纳入本轮判断。
2. 检查 `.research/` 是否已忽略，避免外部项目误入 git。
3. 盘点本地已有成熟项目和论文，优先复用本地 clone，再按需 `git pull` / 补 clone。
4. 建立本追踪文档，后续每步逐段回填。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 只看 README 导致结论空泛 | 高 | 本文记录每个结论对应的代码文件/函数/类 |
| 外部项目体量大导致审计散焦 | 中 | 每个项目只抽取与 kernel/service/plugin 相关的启动、注册、调度、隔离、治理代码 |
| 当前 worktree 有并行改动污染审计 | 中 | 本轮只读相关代码；若写文档，只 stage 明确文档路径 |
| 论文与工程实践脱节 | 中 | 论文只提炼原则，不单独作为 Omubot 缺陷证据 |

**回滚方式**

- 删除本文即可回滚本轮入库文档；`.research/` 是 ignored 本地素材，不影响 git。

**验收证据**

- `git status --short`
- `git check-ignore -v .research/`
- `.research/README.md` 与本地 repos/papers 盘点

**完成后回填**

- 实际确认：
  - 当前工作树已有并行改动：`CHANGELOG.md`、`pyproject.toml`、多处 `plugins/*`，以及 persona S12 parity 相关未跟踪文件；本轮不接管这些改动。
  - `.research/` 已由 `.gitignore:54` 忽略，外部源码/论文不会进入 git。
  - 本地已有 bot/framework 对照仓：`nonebot2`、`koishi`、`AstrBot`、`LangBot`、`MaiBot`、`nekro-agent`、`mirai`。
  - 本地已有 persona/agent 对照仓：`SillyTavern`、`letta`、`generative_agents`、`ai-town`、`PersonaGym`、`trainable-agents`、`autogen`、`camel` 等。
  - 本地已有论文文本：Character-LLM、Generative Agents、MemGPT、PersonaGym、RoleLLM，总计 8510 行抽取文本。
- 验证证据：
  - `git check-ignore -v .research/` → `.gitignore:54:.research/`
  - `git -C .research/bot-systems/repos/nonebot2 log -1` → `bc2fe15 2026-05-20`
  - `git -C .research/bot-systems/repos/koishi log -1` → `fb6e2c0 2026-02-28`
  - `git -C .research/bot-systems/repos/AstrBot log -1` → `dcc99e6 2026-05-22`
  - `git -C .research/bot-systems/repos/LangBot log -1` → `f006181 2026-05-19`
  - `git -C .research/persona-systems/repos/letta log -1` → `1131535 2026-05-14`
  - `wc -l .research/persona-systems/papers/text/*.txt` → `8510 total`
- 遗留风险：
  - 外部 clone 未在 A0 统一 `git pull`；A2 会先检查远端/本地状态，再决定是否更新或仅用现有 HEAD。

### A1 Omubot 当前架构源码解析

**开始前拆分**

1. 启动与装配链路：
   - 读取 `bot.py` / `kernel/router.py`，确认 `PluginContext` 如何被构建、服务如何注入、消息如何进入 `PluginBus`。
2. 内核契约：
   - 读取 `kernel/types.py` 的 `AmadeusPlugin`、`PluginContext`、`MessageContext`、`PromptContext`、`PromptBlock`。
   - 读取 `kernel/bus.py` 的注册、发现、依赖排序、hook 调度、异常隔离、soft isolation。
3. 配置与治理：
   - 读取 `kernel/config.py` 的 `BotConfig`、`GroupOverride`、插件开关/群策略。
   - 读取 `services/plugin_index.py`、`services/plugin_state.py`、`services/plugin_config.py`。
4. 系统服务边界：
   - 抽样读取 `services/llm/client.py`、`services/llm/prompt_builder.py`、`services/context/service.py`、`services/memory/*`、`services/system_module/*`。
5. 应用插件边界：
   - 抽样读取 `plugins/chat`、`plugins/context`、`plugins/knowledge`、`plugins/slang`、`plugins/sticker`、`plugins/history_loader`、`plugins/bilibili`。
6. 产出 Omubot 当前架构事实表：
   - “代码证据 → 当前机制 → 审计关注点”，不写外部对比结论。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 只读文档不读实际装配代码 | 高 | A1 必须以 `bot.py` / `kernel/bus.py` / 插件源码为主证据 |
| 当前 worktree 插件文件有并行改动 | 中 | 记录“当前工作树源码事实”，但最终缺陷判断标注不确定性 |
| 抽样插件遗漏关键模式 | 中 | 覆盖核心聊天、prompt provider、学习/记忆、静默学习、拦截器几类 |

**回滚方式**

- A1 只更新本文；不改运行时代码。

**验收证据**

- `rg`/`sed` 读取关键文件。
- A1 完成后本文包含 Omubot 当前机制事实表与初步审计关注点。

**完成后回填**

- 实际读取：
  - `bot.py`：启动装配、手写注册核心插件、目录插件发现、admin router 挂载。
  - `kernel/bus.py`：插件注册、发现、依赖排序、hook 调度、权限、health、soft isolation。
  - `kernel/types.py`：`PluginContext` service locator、`PromptBlock`、`PromptContext`、`AmadeusPlugin`。
  - `kernel/config.py`：群级 override、presence/silent_learn、reply_style/custom_prompt。
  - `kernel/router.py`：NoneBot 事件入口、群消息门控、silent_learn 分流、scheduler 触发。
  - `plugins/chat/plugin.py`：系统服务组合根，集中创建 memory/LLM/context/provider/scheduler 等服务。
  - `services/llm/client.py` / `services/llm/prompt_builder.py`：prompt block 收集、group profile、legacy soul static block。
  - `services/block_trace/*`：ProviderBus、BudgetManager、prompt trace。
  - 代表插件：`plugins/context`、`plugins/knowledge`、`plugins/slang`、`plugins/sticker`。
- Omubot 当前架构事实表：

| 机制 | 代码证据 | 当前事实 | 审计关注点 |
|---|---|---|---|
| 组合根 | `bot.py:225-256` | 先手写注册 `Chat/Affection/Dream/Echo/ElementDetector/HistoryLoader/Memo/Schedule/Sticker`，再 `discover_plugins("plugins")` 自动发现目录插件 | 手写 + 发现双轨并存；系统插件白名单与实际装配分散 |
| 插件运行时契约 | `kernel/types.py:141`、`kernel/types.py:429` | `PluginContext` 暴露大量 `Any` 服务字段；`AmadeusPlugin` 提供 hook 默认实现 | 接入成本低，但缺少 typed capability/context scope；插件可直接拿到过多系统对象 |
| 消息 hook | `kernel/bus.py:254` | `fire_on_message()` 串行按 priority 调用，返回 True 即消费；silent_mode 只跑 `silent_safe=True` | 对 silent_learn 友好；但没有 per-hook 超时取消，只记录慢调用并在 burst 后 soft isolation |
| Prompt hook | `kernel/bus.py:281`、`services/llm/client.py:2031` | `LLMClient` 构造 `PromptContext` 后 `bus.fire_on_pre_prompt()` 收集插件 block | Prompt 注入已从插件散落转为统一入口；但 legacy group_profile/static soul 仍在 LLMClient/PromptBuilder 里 |
| 插件权限 | `kernel/bus.py:795` | `permissions` 为空时全放行；非空时按 hook 类别检查 | 兼容旧插件，但默认全权限导致治理不是 deny-by-default |
| 插件隔离 | `kernel/bus.py:674`、`kernel/bus.py:909` | `_safe_call()` 记录错误/慢调用，burst 后 90s soft isolation | 有健康和自保护；但 hook await 本身没有 `asyncio.wait_for` 硬超时，单次挂起仍可能拖住串行链路 |
| 插件发现 | `kernel/bus.py:392` | 只支持目录插件 `plugins/<name>/plugin.py`，读取 `plugin.json` 覆盖实例属性 | 目录形态清晰；但没有独立安装/加载阶段的 sandbox 或 import 副作用隔离 |
| 依赖排序 | `kernel/bus.py:620` | Kahn 拓扑；缺失/版本不兼容依赖只 warning 并跳过边，循环回退 priority | 启动韧性高；但关键依赖失效时可能“半功能启动” |
| 群级 runtime | `kernel/config.py:321`、`kernel/config.py:471` | `ResolvedGroupConfig` 聚合 access/presence/tools/reply_style/custom_prompt 等 | 当前配置中心成熟；Part B v2 persona 仍未正式切流，只是 dry-run 映射 |
| 路由入口 | `kernel/router.py:696-734` | router 负责构建 `MessageContext`、silent_learn 分流、命令、timeline、scheduler 触发 | router 承担过多 workflow 逻辑，不只是 adapter bridge |
| 系统服务组合 | `plugins/chat/plugin.py:584-1045` | `ChatPlugin.on_startup()` 创建 image/sticker/memory/identity/context/knowledge_graph/LLM/provider/scheduler 等几乎全部系统服务 | ChatPlugin 实际是 service composition root，和“插件可卸载业务层”概念混在一起 |
| Prompt provider 子架构 | `services/block_trace/provider_bus.py:19`、`services/block_trace/budget_manager.py:25` | 新增 provider bus、active/shadow、预算裁剪、trace | 这是较成熟的系统层抽象苗头，可推广到更多 context/memory/persona 注入 |
| 工具注册 | `services/tools/registry.py:12` | `ToolRegistry` 只做 name→tool、JSON parse、异常兜底 | 简单可用；缺少工具级权限、timeout、schema validation、调用审计统一结构 |
| SystemModule v2 | `services/system_module/models.py:1`、`services/system_module/state_bus.py:1` | 已有 dry-run contract/state bus，但未接正式 runtime | 未来改进方向已有雏形；目前与 PluginBus/PromptBuilder 仍是平行体系 |

- 初步关注点（只基于 Omubot 代码，还未外部对比）：
  1. `ChatPlugin` 同时是核心聊天插件、系统服务组合根、调试命令提供者，边界过宽。
  2. `PluginContext` 是强大的 service locator，但 `Any` 字段太多，插件能力边界不够硬。
  3. `PluginBus` 有 health/soft isolation，但缺少单次 hook 硬 timeout 和插件任务生命周期统一管理。
  4. 插件权限是 opt-in，旧插件默认全权限；适合兼容，不适合作为长期治理默认。
  5. Prompt 注入正从 hook 走向 provider/budget/trace，但 legacy 静态 prompt、group profile、插件 prompt 三套入口仍并存。
  6. `SystemModule` dry-run 和现有 PluginBus 尚未合流，短期容易形成“双架构”。
- 自审遗漏：
  - A1 还没有深入 admin 插件页 toggle API 细节；A5 缺陷表会补读 `admin/routes/api/plugins.py`。
  - A1 抽样插件没有覆盖所有业务插件；A3/A5 会只对代表性机制下结论，不把抽样结论扩大到全部插件。
  - 当前插件文件有并行未提交改动，最终报告需标注“以当前 worktree 源码为准”。

### A2 外部成熟项目拉取/更新与候选筛选

**开始前拆分**

1. 本地已有仓库状态检查：
   - bot/framework：`nonebot2`、`koishi`、`AstrBot`、`LangBot`、`MaiBot`、`nekro-agent`。
   - persona/agent：`letta`、`SillyTavern`、`ai-town`、`generative_agents`。
2. 先检查 dirty 状态，再对干净仓库执行 `git pull --ff-only`；若失败，记录失败原因，使用现有 HEAD。
3. 补拉架构相关论文到 `.research/bot-systems/papers/`：
   - AutoGen multi-agent framework。
   - AgentScope multi-agent platform。
   - ReAct / Toolformer 作为 tool/action orchestration 对照。
4. 确认文本抽取可用；若 `pdftotext` 不可用，则记录 PDF 已拉取但正文解析受限。
5. 筛选 A3 主样本：
   - 框架层：NoneBot2、Koishi。
   - LLM bot 应用层：AstrBot、LangBot、MaiBot、nekro-agent。
   - agent/persona 系统层：Letta、SillyTavern、ai-town。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| `git pull` 改动本地研究素材导致证据版本漂移 | 中 | 每个仓库记录 HEAD hash/date；若更新失败则记录现有 HEAD |
| 网络或 PDF 抽取失败 | 中 | 不阻塞源码项目审计；失败项不作为结论依据 |
| 候选太多审不深 | 高 | A3 主样本限制为 6-9 个，每个只读启动/注册/调度/隔离/配置核心代码 |

**回滚方式**

- `.research/` 是 ignored 本地素材；如需回滚，删除或重置对应 clone 即可，不影响仓库代码。

**验收证据**

- 每个主样本的 `git log -1 --format='%h %cs'`。
- 论文 PDF 与 text 文件存在。
- A2 完成后本文列出主样本清单和排除项。

**完成后回填**

- 实际操作：
  - 已检查主样本仓库均为 clean。
  - 已对主样本执行 `git pull --ff-only`；`nonebot2`、`AstrBot`、`nekro-agent` 发生 fast-forward，其余已是最新。
  - 已新增 `.research/bot-systems/papers/` 与 `text/`，下载并抽取 AutoGen、AgentScope、ReAct、Toolformer 四篇 PDF。
- 主样本 HEAD：

| 样本 | HEAD | 日期 | 用途 |
|---|---|---|---|
| NoneBot2 | `5aa2b3d` | 2026-05-24 | Python bot 框架：driver/plugin/matcher/rule/permission |
| Koishi | `fb6e2c0` | 2026-02-28 | TS bot 框架：context/plugin/service/middleware/command |
| AstrBot | `ff28eca` | 2026-05-23 | 多平台 LLM bot：provider/platform/plugin/persona |
| LangBot | `f006181` | 2026-05-19 | LLM bot：pipeline/provider/platform/plugin |
| MaiBot | `8b8118e` | 2026-05-21 | 同类人格群聊 bot：chat loop/memory/prompt |
| nekro-agent | `c2254a3` | 2026-05-23 | agent bot：workspace/memory/sandbox/plugin/API |
| Letta | `1131535` | 2026-05-14 | agent memory：core archival memory/tool loop |
| SillyTavern | `51ad27f` | 2026-05-03 | 角色 prompt 编排：角色卡/world info/injection order |
| ai-town | `2693ed6` | 2026-01-07 | 多 agent 状态/记忆/对话 lifecycle |
| generative_agents | `fe05a71` | 2023-08-11 | 学术实现：perceive/retrieve/plan/reflect/converse |

- 论文素材：
  - `.research/bot-systems/papers/text/autogen_2308.08155.txt`，3842 行。
  - `.research/bot-systems/papers/text/agentscope_2402.14034.txt`，2498 行。
  - `.research/bot-systems/papers/text/react_2210.03629.txt`，2962 行。
  - `.research/bot-systems/papers/text/toolformer_2302.04761.txt`，2040 行。
  - 复用 persona 方向 5 篇论文文本，总文本行数 `19852 total`。
- 排除/降级：
  - `mirai` 本轮只作为 QQ adapter/生态背景，不进入主对照表；Omubot 当前核心问题更靠近 plugin/prompt/system service 架构。
  - `autogen` / `camel` 源码可作为后续多代理组织参考，但本轮优先已拉本地 bot 与 persona 系统。
- 自审：
  - A2 完成了“拉到本地”要求；A3/A4 才能引用其代码/正文做判断。
  - A2 未把任何 `.research/` 内容 stage；`.research/` ignored。

### A3 外部项目源码拆分解析

**开始前拆分**

1. 框架层源码解析：
   - NoneBot2：定位 driver、plugin manager、matcher、rule、permission、dependency 注入。
   - Koishi：定位 context/service/plugin/middleware/command 生命周期。
2. LLM bot 应用层源码解析：
   - AstrBot：定位 provider/platform/plugin/event bus/persona/pipeline。
   - LangBot：定位 pipeline、provider、platform、plugin runtime。
   - nekro-agent：定位 core config、plugin/sandbox/workspace/memory。
   - MaiBot：定位聊天 loop、memory、prompt/personality 组织。
3. Persona/agent 系统源码解析：
   - Letta：定位 agent loop、memory blocks、tools。
   - SillyTavern：定位 prompt assembly、world info、injection/budget 顺序。
   - ai-town / generative_agents：定位 agent state、memory、reflection、conversation lifecycle。
4. 每个项目只记录源码事实，不写“谁更好”；A5 再集中映射到 Omubot 缺陷/改进。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 仓库结构变化导致定位耗时 | 中 | 先 `rg` 关键类名/函数名，再读命中的源码切片 |
| 误把 README 描述当事实 | 高 | 不引用 README；只引用代码路径、类、函数、配置 |
| 项目过多导致浅尝辄止 | 高 | 每个项目固定 3-5 个机制点：注册、上下文/依赖、调度、隔离、prompt/memory |

**回滚方式**

- A3 只更新本文；外部 clone 已在 `.research/`。

**验收证据**

- 本文 A3 完成后，至少 6 个外部项目有“代码证据 → 机制事实 → 对照价值”表。

**完成后回填**

- 实际读取范围：
  - 框架层：NoneBot2 `PluginManager` / `Matcher` / `Rule` / `Permission` / DI；Koishi `Context.provide()`、middleware、plugin group reload/unload。
  - LLM bot 应用层：AstrBot event bus / pipeline scheduler / star manager / plugin tool filtering；LangBot runtime pipeline / plugin runtime connector；nekro-agent plugin base/manager/sandbox/command export；MaiBot chat loop / runtime / tooling / plugin supervisor。
  - Persona/agent 系统层：Letta agent loop / memory blocks / tool rule solver；SillyTavern prompt manager / world info / extension prompt injection；ai-town conversation memory / reflection / vector retrieval；generative_agents perceive/retrieve/plan/reflect/converse 显式认知循环。

| 项目 | 代码证据 | 机制事实 | 对照价值 |
|---|---|---|---|
| NoneBot2 | `.research/bot-systems/repos/nonebot2/nonebot/plugin/manager.py`、`nonebot/internal/matcher/matcher.py`、`nonebot/internal/rule.py`、`nonebot/internal/permission.py` | 插件管理、matcher、rule、permission、依赖注入被拆成细粒度对象；事件处理不是把所有插件挂到同一个宽泛 hook 上。 | 可作为 Omubot 后续“插件 hook 细分为 matcher/capability/permission”的框架级参考。 |
| Koishi | `.research/bot-systems/repos/koishi/packages/core/src/context.ts`、`packages/core/src/registry.ts`、`packages/core/src/middleware.ts` | `Context` 既是作用域也是 service lifecycle 容器；插件以 context 派生作用域运行，支持 service provide、middleware stack、plugin group reload/unload。 | 可对照 Omubot `PluginContext` 当前宽 service locator，思考服务作用域、卸载和生命周期边界。 |
| AstrBot | `.research/bot-systems/repos/AstrBot/astrbot/core/pipeline/scheduler.py`、`astrbot/core/star/star_manager.py`、`astrbot/core/star/context.py` | 事件进入 event bus 后由 pipeline scheduler/stage 驱动；star manager 管理插件热加载；plugin context 参与工具过滤。 | 可对照 Omubot router/bus/chat plugin 三处 workflow 混合问题。 |
| LangBot | `.research/bot-systems/repos/LangBot/pkg/pipeline/runtime.py`、`pkg/plugin/runtime/connector.py`、`pkg/plugin/runtime/manager.py` | RuntimePipeline 有 stage container；插件 runtime connector 支持 stdio/ws、heartbeat、install/upgrade/delete/list，工具调用按 include_plugins 过滤。 | 可作为插件 out-of-process runtime、健康检查、热更新和工具过滤治理样本。 |
| nekro-agent | `.research/bot-systems/repos/nekro-agent/nekro_agent/services/plugin/base.py`、`services/plugin/manager.py`、`services/sandbox/runner.py`、`services/message_service.py`、`command/registry.py`、`command/tool_export.py` | 插件可挂 init/config/router/sandbox method/prompt inject/message hook/webhook/cleanup/async task/command；频道级 Agent 调用有 `asyncio.timeout`；Docker sandbox 约束 Memory/CPU/User 并 timeout kill；命令注册有冲突检测、启用态/权限覆盖，AI tool export 把命令导出为 function schema。 | 可对照 Omubot 目前 in-process 插件、默认 allow 权限、工具 registry 弱治理、单 hook 无硬超时问题。 |
| MaiBot | `.research/bot-systems/repos/MaiBot/src/maisaka/chat_loop_service.py`、`runtime.py`、`core/tooling.py`、`plugin_runtime/host/supervisor.py`、`hook_dispatcher.py`、`authorization.py`、`capability_service.py`、`tool_provider.py` | 人格 prompt 由 nickname/alias/personality 构建；planner before/after hook 有 schema、timeout、kwargs mutation；上下文选择有 cache stability window 与 mid-term memory pin；ToolRegistry 统一 provider 去重/invoke；插件 runtime 是 runner 子进程 + RPC + health + reload + capability token，hook 区分 observe/blocking。 | 同类群聊 bot 对照价值高，尤其是 persona/prompt、plugin runtime、tool/provider 治理与 Omubot 当前 `ChatPlugin` 组合根的差异。 |
| Letta | `.research/persona-systems/repos/letta/letta/agents/letta_agent_v2.py`、`letta_agent_v3.py`、`schemas/memory.py`、`helpers/tool_rule_solver.py`、`schemas/tool_rule.py` | Agent loop 有 max steps、step/run metrics、context overflow summarization retry；core memory blocks 带 label/description/metadata/limits；工具规则控制 allowed/required/terminal/continue/requires approval；v3 支持按开关并行或串行工具执行。 | 可对照 Omubot 记忆块结构化、工具调用规则、审批/终止语义和上下文溢出治理。 |
| SillyTavern | `.research/persona-systems/repos/SillyTavern/public/script.js`、`public/scripts/world-info.js`、`PromptManager.js`、`openai.js` | 生成入口组合 character fields、depth prompt、world info、persona、extension prompts、chat history、token budget；extension prompt 按 depth/role 注入；WorldInfo 支持 budget、recursive、case/whole-word、persona/character/scenario scan flags；PromptManager 有 order/marker/toggle/injection depth。 | 可对照 Omubot prompt 注入路径分散问题，尤其是 prompt order、预算、开关、追踪的统一编排。 |
| ai-town | `.research/persona-systems/repos/ai-town/convex/agent/memory.ts:24`、`:158`、`:187`、`:325`；`convex/agent/conversation.ts:13`、`:78`、`:185`；`convex/aiTown/game.ts:177`、`:343` | 对话结束后总结为第一人称记忆，计算 importance 与 embedding 并入库；检索先 vector overfetch，再按 relevance + recency + importance 排序并更新 lastAccess；importance 累积超过阈值后生成 reflection memory；game tick 产出 pending agent operations 并在 saveDiff 后启动异步 agent 操作。 | 可对照 Omubot 学习/记忆管线：记忆不是只追加文本，而是“事件总结 → 重要性 → embedding → 检索排序 → 反思”闭环。 |
| generative_agents | `.research/persona-systems/repos/generative_agents/reverie/backend_server/persona/persona.py:219`；`memory_structures/scratch.py:14`；`memory_structures/associative_memory.py:50`；`cognitive_modules/perceive.py:25`；`retrieve.py:199`；`reflect.py:172`；`converse.py:76` | `Persona.move()` 显式执行 perceive → retrieve → plan → reflect → execute；Scratch 保存核心身份、当前状态、计划、会话状态、反思参数；AssociativeMemory 拆 event/thought/chat 并记录 keywords/poignancy/embedding；检索结合 recency/relevance/importance；反思由 importance trigger 触发，并把 insight 写回 thought。 | 可作为 Omubot persona/memory/learning 设计对照：拟人稳定性来自状态、计划、记忆、反思、对话生成的系统闭环，不只是静态人设文本。 |

- A3 小结：
  1. 外部项目的共同点不是“插件多”，而是把事件匹配、权限、服务作用域、工具 schema、prompt order、记忆检索/反思拆成可治理对象。
  2. 同类 bot 项目（MaiBot、nekro-agent、LangBot、AstrBot）比通用框架更能解释 Omubot 当前问题：插件隔离、工具治理、prompt 编排、人格/记忆闭环。
  3. Persona/agent 系统显示：拟人不僵硬依赖动态状态、检索、反思、计划与对话上下文共同塑形；静态 persona 文档只是输入源之一。
- 自审遗漏：
  - A3 没有深读 mirai adapter 生态；本轮主问题是内核/系统/应用插件与 LLM prompt/memory，不把 mirai 纳入主要对照。
  - A3 没有运行外部项目测试；外部源码只用于机制拆解，不作为可运行性背书。
  - 上表为源码事实，不直接判定 Omubot 缺陷；A5 会把每条缺陷重新绑定 Omubot 当前源码证据。

### A4 论文正文解析与可落地原则提炼

**开始前拆分**

1. 系统/多代理论文：
   - AutoGen：定位 conversation programming、tool/function execution、agent interaction loop。
   - AgentScope：定位 message exchange、service/tool、distributed runtime、fault/monitoring。
   - ReAct：定位 reasoning-action-observation 交错模式，提炼工具/行动前的显式思考边界。
   - Toolformer：定位工具调用自监督生成、API call filtering/validation 思路。
2. Persona/记忆论文：
   - Generative Agents：定位 memory stream、retrieval、reflection、planning。
   - MemGPT：定位 memory tier、context management、function/tool mediated memory updates。
   - PersonaGym / Character-LLM / RoleLLM：定位 persona consistency、role-playing evaluation、训练/评测对人设偏离的约束。
3. 提炼方式：
   - 每篇只抽取与 Omubot 内核/系统/插件相关的工程原则。
   - 每条原则记录正文证据路径和关键词，不引用摘要作为证据。
   - A4 不下缺陷结论；A5 才把原则与 Omubot 源码事实连接。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 论文原则被误当作工程事实 | 高 | 每条写明“论文原则”，缺陷仍需 Omubot 源码证明 |
| 只看 abstract/intro | 高 | 使用 `rg` 命中正文段落并抽样读取上下文 |
| 论文偏研究场景，迁移成本高 | 中 | A5 给出风险/成本，不把论文方案直接落地为改造项 |

**回滚方式**

- A4 只更新本文；删除本节即可回滚。

**验收证据**

- 本节完成后包含论文正文证据表。
- 至少覆盖系统/工具方向 4 篇与 persona/记忆方向 5 篇。

**完成后回填**

- 实际读取：
  - 系统/工具方向：AutoGen、AgentScope、ReAct、Toolformer 抽取正文。
  - Persona/记忆方向：Generative Agents、MemGPT、PersonaGym、Character-LLM、RoleLLM 抽取正文。

| 论文 | 正文证据 | 原则提炼 | 对 Omubot 的后续审计用途 |
|---|---|---|---|
| AutoGen | `.research/bot-systems/papers/text/autogen_2308.08155.txt:170`、`:175`、`:187`、`:325` | Agent 应是可对话、可组合能力单元；workflow 可建模为 conversation-centric computation + control flow，工具/人/LLM 是可配置能力。 | 对照 Omubot 时，应检查插件/系统服务是否也能被拆为能力单元，还是集中在单个宽对象里。 |
| AgentScope | `.research/bot-systems/papers/text/agentscope_2402.14034.txt:108`、`:163`、`:195`、`:210`、`:215`、`:306` | 多代理平台核心对象是 message、agent、service、workflow；service/tool 要有格式化输出、描述、参数和容错；平台层需有 retry/correction/logging。 | 对照 Omubot 的 `MessageContext`、插件 hook、tool registry、bus health 是否具备 traceability、参数 schema、容错和日志闭环。 |
| ReAct | `.research/bot-systems/papers/text/react_2210.03629.txt:215`、`:262`、`:267`、`:337` | 推理、行动、观察交错能降低静态 CoT 的幻觉和错误传播；行动前后的 thought 可用于计划、异常处理和可诊断性。 | 对照 Omubot 工具/学习/记忆操作是否有可审计的“为什么调用、调用后观察、如何影响下一步”。 |
| Toolformer | `.research/bot-systems/papers/text/toolformer_2302.04761.txt:131`、`:176`、`:192`、`:226`、`:335` | 工具调用应表达为明确 API call + 参数 + 结果，并通过执行与过滤判断调用是否有帮助；推理过程中可中断生成、执行工具、插回结果。 | 对照 Omubot tool registry 只做 name→call 的简单映射，后续可补 schema、调用结果、过滤/验证和 trace。 |
| Generative Agents | `.research/persona-systems/papers/text/generative_agents_2304.03442.txt:448`、`:471`、`:504`、`:560`、`:596`、`:603` | 拟人稳定性来自 memory stream、recency/relevance/importance 检索、reflection、planning、reaction 的闭环；长程一致性不能只靠当前 prompt。 | 对照 Omubot persona/learning/memory 是否形成“观察 → 记忆 → 检索 → 反思 → 计划/回复”的闭环，而非静态人设堆叠。 |
| MemGPT | `.research/persona-systems/papers/text/memgpt_2310.08560.txt:151`、`:180`、`:212`、`:220`、`:255`、`:323`、`:375` | 长期对话需要分层 memory：system instructions、working context、FIFO/recall、archival；记忆更新/检索由函数执行器和 token pressure 触发，并带校验与分页。 | 对照 Omubot 的 context/memory/persona 注入是否有分层、容量压力、显式写入策略和检索分页，而不是多个来源直接拼 prompt。 |
| PersonaGym | `.research/persona-systems/papers/text/personagym_2407.18416.txt:79`、`:117`、`:213`、`:270`、`:335` | 人设能力不是模型大小保证；需要环境化、任务化、多维评估，包括 action justification、expected action、linguistic habits、persona consistency、toxicity control。 | 对照 Omubot 后续 persona 变更验收不能只看少量聊天样例，应有 persona-specific probe 和 rubric。 |
| Character-LLM | `.research/persona-systems/papers/text/character_llm_2310.10158.txt:31`、`:94`、`:220`、`:240`、`:656`、`:743` | 角色不是只写 profile；experience reconstruction、目标角色视角的 thoughts/utterances、protective experiences 可降低角色幻觉和现代知识越界。 | 对照 Omubot 人设导入文档时，应考虑经历/边界/保护性反例，不只收“性格标签”。 |
| RoleLLM | `.research/persona-systems/papers/text/rolellm_2310.00746.txt:123`、`:170`、`:203`、`:240`、`:287` | 角色能力拆成 speaking style、role-specific knowledge、episodic memory；profile 可分段生成 QA/置信理由；数据清洗要求完整、非拒答、隐藏 AI/role 元标记。 | 对照 Omubot persona source importer 的格式规范、风格样本、知识/记忆边界和清洗校验。 |

- A4 小结：
  1. 系统方向论文都强调“message/workflow/tool/service”可追踪对象，而不是把所有扩展能力塞入一个全能上下文。
  2. Persona 方向论文共同强调：拟人稳定性来自可检索经历、反思、计划、评估与保护性边界，不是静态设定越长越好。
  3. 论文给的是原则与评估框架；A5 必须回到 Omubot 源码判定缺陷和优先级。
- 自审遗漏：
  - A4 未复现实验，也不把论文结论当性能保证。
  - A4 只读取抽取文本；PDF 排版导致部分双栏文本交错，但关键机制段落与 A3 源码样本相互印证。
  - A4 没有引入新论文；本轮范围先覆盖已下载 9 篇，避免审计继续扩散。

### A5 审计对比：缺陷、风险、改进方向

**开始前拆分**

1. Omubot 证据补读：
   - 内核：`kernel/bus.py`、`kernel/types.py`、`kernel/router.py`。
   - 组合根：`bot.py`、`plugins/chat/plugin.py`。
   - 工具/Prompt/记忆：`services/tools/registry.py`、`services/llm/client.py`、`services/llm/prompt_builder.py`、`services/block_trace/*`、`services/memory/*`、`services/context/*`。
   - 插件治理：admin 插件 API、`services/plugin_state.py`、`services/plugin_index.py`、`services/plugin_config.py`。
   - v2 平行架构：`services/system_module/*`。
2. 矩阵输出字段：
   - 审计项、严重度、Omubot 证据、外部源码/论文对照、风险、改进方向、实施成本。
3. 判断规则：
   - 只把 Omubot 源码能证明的问题写成缺陷。
   - 外部项目/论文只用于说明可行替代设计或业界样本。
   - 小瑕疵不展开，聚焦内核-系统-应用插件的主结构问题。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 对比对象不同导致“拿别人优点打自己” | 高 | 每项必须有 Omubot 当前源码证据；外部只当改进参考 |
| 当前工作树有并行改动 | 中 | 标注“以当前 worktree 源码为准”，不回滚用户改动 |
| 改进方向过大不可执行 | 中 | 按阶段给出短期/中期/长期方向和成本 |

**回滚方式**

- A5 只更新本文；删除本节即可回滚。

**验收证据**

- A5 完成后包含不少于 8 个主审计项。
- 每个主审计项至少包含一个 Omubot 源码证据。

**完成后回填**

- 结论边界：
  - 以下“缺陷/风险”均以 Omubot 当前 worktree 源码为证据。
  - 外部项目和论文只作为成熟实现样本或设计原则参考，不单独证明 Omubot 有问题。

| 审计项 | 严重度 | Omubot 证据 | 外部源码/论文对照 | 风险 | 改进方向 | 成本 |
|---|---|---|---|---|---|---|
| `ChatPlugin` 过宽，承担事实组合根 | 高 | `plugins/chat/plugin.py:584` 起在 `on_startup()` 内创建 humanizer/image/sticker/card/identity/schedule/affection/LLM/provider_bus/memo/scheduler 等系统服务；`bot.py:225` 仍把它注册成普通插件 | MaiBot 把 chat loop、tooling、plugin runtime supervisor 分层；AgentScope 区分 utility/manager/agent layer | ChatPlugin 难卸载、难测试、难局部替换；系统初始化顺序被插件优先级隐式约束 | 设立 `services/runtime/composition.py` 或 `kernel/application.py` 作为系统组合根；ChatPlugin 只保留聊天业务 hook/debug 命令；先做只搬装配不改行为 | 中-高 |
| `PluginContext` 是宽 service locator，能力边界弱 | 高 | `kernel/types.py:141-212` 暴露大量 `Any` 服务字段，包括 bot、scheduler、llm、stores、runtime_errors 等；插件拿到同一个全局 ctx | Koishi Context 是作用域 service lifecycle；MaiBot/nekro-agent 有 capability token/authorization；Letta tool rules 明确 allowed/approval | 插件可直接访问过多系统对象，后续第三方插件或远程插件扩展时风险高；测试也难构造最小依赖 | 引入 `CapabilityContext`：按 hook/插件 permissions 注入窄接口；保留 legacy ctx，但新插件走 typed capability；先从 tool/prompt/message 三类开始 | 中 |
| Hook 调度缺单次硬 timeout/cancel | 高 | `kernel/bus.py:674-710` `_safe_call()` 直接 `await coro`，只在完成后记录 elapsed；`kernel/bus.py:722` 慢 burst 后 soft isolation | nekro-agent 频道级调用 `asyncio.timeout`；MaiBot hook dispatcher 有 timeout；AgentScope 强调 service-level retry/fault handler | 单个插件 await 卡住会阻塞串行 on_message/on_pre_prompt 链路；soft isolation 对“永不返回”无效 | 用 `asyncio.wait_for`/`asyncio.timeout` 包 hook，超时记录 health 并 cancel；先只对 `on_message/on_pre_prompt/on_post_reply` 加默认预算和 opt-out | 中 |
| 插件权限默认 allow，不是 deny-by-default | 高 | `kernel/bus.py:795-803` permissions 为空直接 True；`admin/routes/api/plugins.py:121-123` 同样空权限视为允许 | LangBot include_plugins 过滤；nekro-agent command 权限覆盖；Letta allowed/required/approval tool rules | 兼容旧插件方便，但长期无法区分“未声明”和“全权限”；第三方插件接入会扩大面 | 分阶段：manifest v3 新插件默认 deny；旧插件打 `legacy_permissions=allow_all` 标记；admin 显示“未声明全放行”风险 | 中 |
| 插件运行时基本 in-process，无硬隔离/heartbeat/reload 边界 | 中-高 | `kernel/bus.py:392-463` 直接 import `plugins/<name>/plugin.py` 并实例化；`bot.py:256` 启动时发现；无子进程/沙箱/心跳 | LangBot stdio/ws runtime + heartbeat/install/upgrade；MaiBot runner 子进程 + health/reload；nekro-agent Docker sandbox | 插件 import 副作用、CPU/内存阻塞、依赖冲突会污染主进程；热更新能力有限 | 短期补 import side-effect 记录和 hook timeout；中期设计 optional plugin runner，用于第三方/高风险插件；系统插件继续 in-process | 高 |
| Router 混 adapter bridge 与 workflow 决策 | 中-高 | `kernel/router.py:661-735` 既做 NoneBot 事件转换，又做 silent_learn、timeline、trigger、commands；`kernel/router.py:831-900` 又接 reply gate shadow/semantic workflow | AstrBot event bus→pipeline scheduler/stage；AgentScope workflow 是显式概念 | 入口文件变成消息工作流大杂烩，后续回复门控、学习、命令、调度互相影响时难定位 | 抽 `MessageIngressService`/`ReplyWorkflowRunner`：router 只转 `MessageContext` 并调用 service；先搬纯逻辑函数，保留行为 | 中 |
| Prompt 注入路径仍多轨并存 | 中-高 | `services/llm/client.py:2015` group_profile block 直接进 stable；`services/llm/client.py:2031` plugin hook；`services/llm/client.py:2035-2058` provider_bus + budget；`services/llm/prompt_builder.py:81-108` legacy identity/instruction static | SillyTavern PromptManager 管 order/toggle/injection depth；MemGPT 分 system/working/FIFO/archival；Generative Agents 检索/反思/计划写回 memory stream | 同一类上下文从 legacy static、group override、plugin hook、provider bus 注入，顺序和预算难全局推理 | 以 ProviderBus/BudgetManager 为唯一新入口；把 group_profile、state_board、identity tail 逐步包装成 provider/candidate；保留 static identity 但纳入 trace | 中 |
| Tool/command 治理弱：schema、冲突、timeout、审批、审计不足 | 中-高 | `services/tools/registry.py:12-37` 只 name→tool、json.loads、execute；无冲突检测、参数校验、工具级 timeout；`services/llm/client.py:1455-1488` 群级 allowed/blocked 过滤但没有调用审批规则 | Toolformer 明确 API call/input/result；Letta tool rules 有 allowed/required/terminal/approval；nekro-agent command registry 冲突检测 + tool export schema | LLM 工具调用出错只能返回泛化错误；同名覆盖静默发生；高风险工具缺审批和审计 | `ToolRegistry.register` 检测重复；执行前 JSON schema 校验；`call()` 加 timeout 和 audit record；高风险工具加 `requires_approval`/group policy | 中 |
| `system_module` v2 是 dry-run 平行架构，尚未接 runtime | 中 | `services/system_module/models.py:1` 明确 dry-run validation；`state_bus.py:43-48` 是 in-memory skeleton；A1 未发现 PluginBus/LLMClient runtime 使用它 | AgentScope workflow/service 是平台层实际抽象；MaiBot capability_service 与 hook dispatcher 接 runtime | 新架构和旧 PluginBus/PromptBuilder 并行，继续扩展会形成“双架构”维护成本 | 制定合流路线：先把 ProviderBus candidates 映射到 ModuleContract/StateSlot trace；再把 selected hooks 迁入 module graph，不一次性替换 | 中 |
| 记忆/学习已有组件但闭环仍分散 | 中 | `plugins/chat/plugin.py:981-998` late-bind MemoryConsolidator/ReflectionGenerator；`services/block_trace/episode_provider.py` 从 enabled_for_prompt episodes recall；`plugins/context/plugin.py` 仍独立 retrieval hook | ai-town/generative_agents 都有观察→重要性→embedding→检索排序→反思→回写；MemGPT 有 memory pressure 与函数化写入 | Omubot 已有 D.3/D.4 苗头，但学习、episode、context、slang/style provider 之间的状态生命周期与触发条件不够统一 | 用“事件流/episode/reflection/provider”四段定义 learning pipeline；为每段补 trace id、source/evidence、importance/decay、group scope；先文档化再收敛实现 | 中 |
| Admin 插件治理可视化已有，但不等于运行时强治理 | 中 | `admin/routes/api/plugins.py:418-456` list/health/index；`:521-537` health/state；`services/plugin_state.py:11-16` 只存 enabled runtime decision；`services/plugin_config.py:52` per-plugin config | LangBot/MaiBot/nekro-agent runtime 层有 heartbeat/reload/runner/authorization | UI 能显示状态和开关，但无法弥补 in-process、默认 allow、无 timeout 的底层风险 | Admin 保持现状，新增 risk badges：未声明权限、无 config schema、超时次数、legacy allow_all、in-process；和 bus health 共用数据 | 低-中 |

- 正向资产：
  1. `PromptProviderBus` + `PromptBudgetManager` 已经具备 provider registry、active/shadow、预算裁剪、trace 记录，是最适合向“系统模块化”演进的现有支点。
  2. `PluginBus` 已有依赖排序、health、soft isolation、permission 元数据、locked system plugin，说明不是从零开始治理。
  3. 群级 `ResolvedGroupConfig`、tool allowed/blocked、presence/silent_learn 的策略中心相对成熟，可作为 capability scope 的输入。

- 建议路线：
  1. **第一阶段：不改架构形态，补硬边界。** Hook timeout/cancel、ToolRegistry 冲突/timeout/audit、admin risk badges、manifest 未声明权限警告。
  2. **第二阶段：收敛 prompt/context。** 把 group_profile/state_board/context plugin/episode/slang/style 统一成 ProviderBus candidates，BudgetManager 成为唯一裁剪和 trace 点。
  3. **第三阶段：拆组合根。** 从 ChatPlugin 搬出系统服务装配，建立 runtime composition root，ChatPlugin 回归业务插件。
  4. **第四阶段：插件 runner。** 只对第三方/实验性/高风险插件启用 out-of-process runner；系统插件继续 in-process，避免一次性重构风险。
  5. **第五阶段：SystemModule 合流。** 将 dry-run contract 绑定实际 ProviderBus/RuntimeStateBus/selected hooks，避免形成长期平行架构。

- 自审：
  - 每项缺陷都绑定 Omubot 源码；没有仅凭外部项目下结论。
  - 对外部成熟项目没有主张“必须照搬”，只提可行的工程方向。
  - 没展开小瑕疵，如 `ChatPlugin.on_shutdown()` 中 `ctx.card_store.close()` 重复调用；这类问题不影响本轮主架构审计。

### A6 自审、证据链检查与最终报告收口

**开始前拆分**

1. 证据链自审：
   - 检查 A3 外部项目结论是否均引用本地源码路径。
   - 检查 A4 论文原则是否均引用抽取正文路径。
   - 检查 A5 缺陷是否均引用 Omubot 当前源码路径。
2. 文档一致性：
   - 更新步骤总览状态。
   - 确认本文顶部状态改为完成。
   - 确认 A0-A6 均有开始前拆分、风险、验收/回填。
3. 工作区自审：
   - 确认 `.research/` ignored，不纳入 git。
   - 检查本轮只修改审计追踪文档；现有 unrelated dirty 不回滚。
4. 验证：
   - 运行 `git diff --check -- docs/tracking/omubot-kernel-system-plugin-architecture-audit.md`。
   - 运行 `git status --short` 记录本轮新增/修改边界。

**风险评估**

| 风险 | 等级 | 应对 |
|---|---|---|
| 追踪文档太长导致最终结论难读 | 中 | Final 只给短摘要与 Top findings，细节留在文档 |
| 维护日志产生额外冲突 | 中 | 本轮是审计文档产出，且 `maintenance-log.md` 已有并行改动；不主动写入维护日志 |
| 工作区路径与 AGENTS 首选路径不一致 | 中 | 在 A6 明确记录本轮使用 `/Volumes/OmubotDisk/omubot`，避免与 `$HOME/OmubotWorkspace/omubot` 分叉认知 |

**回滚方式**

- 删除或回滚 `docs/tracking/omubot-kernel-system-plugin-architecture-audit.md` 即可回滚本轮审计产物。

**验收证据**

- `git diff --check -- docs/tracking/omubot-kernel-system-plugin-architecture-audit.md` 通过。
- `git status --short` 显示本文为本轮主要产物，`.research/` 未进入 git。

**完成后回填**

- 证据链自审结果：
  - A3 外部项目表均引用 `.research/` 下本地源码路径；没有使用 README/项目简介作为判断依据。
  - A4 论文原则表均引用 `.research/**/papers/text/*.txt` 抽取正文路径；没有只引用 abstract。
  - A5 每个缺陷项均绑定 Omubot 当前源码路径；外部项目/论文只作为对照样本。
- 文档一致性：
  - A0-A6 均已包含开始前拆分、风险评估、回滚/验收与完成回填。
  - 顶部状态已从“进行中”改为“完成”，步骤总览已全部收口。
- 工作区边界：
  - 本轮实际工作区为 `/Volumes/OmubotDisk/omubot`。AGENTS 首选 `$HOME/OmubotWorkspace/omubot`，但本轮从接手时文档与研究素材都在当前路径，为避免分叉继续在当前路径收口。
  - 当前 worktree 存在大量并行改动（persona、plugins、`maintenance-log.md` 等），本轮未回滚、未接管、未提交这些改动。
  - 本轮主要产物是 `docs/tracking/omubot-kernel-system-plugin-architecture-audit.md`。
- 验证：
  - `git diff --check -- docs/tracking/omubot-kernel-system-plugin-architecture-audit.md`：通过，无输出。
  - `git check-ignore -v .research/`：`.gitignore:54:.research/`，研究目录不会入库。
  - `git status --short`：本文仍为 untracked；`.research/` 未出现。
- 维护日志：
  - 本轮是审计追踪文档产出，未改变运行时代码；且 `maintenance-log.md` 已有并行改动，因此不追加维护日志，避免污染用户现有修改。
