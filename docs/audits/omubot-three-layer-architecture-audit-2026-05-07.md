# Omubot 三层架构审计报告（2026-05-07 更新版）

审计日期：2026-05-07  
仓库基线：`/Volumes/我的电脑/omubot`，commit `d401432`  
定位：对旧版《Omubot 三层架构审计报告》做一次基于当前代码的重写，保留仍然成立的判断，修正已经过时的结论，并给出当前阶段更准确的改进优先级。

> 这份新版结论覆盖了本轮已经落地的改动：Provider profile 管理、插件治理、协议追踪、群 Profile、轻量语义检索、黑话系统、配置审计回滚和 Admin Web 运维面板。

## 一句话结论

Omubot 的大方向没有变：它仍然是一个以 `PluginBus` 为核心、围绕 QQ 群陪伴场景打磨的三层架构 bot。  
真正变了的是上层能力已经比旧审计时强很多了，尤其是：

- Provider 治理已经从“能接 API”升级成“能按任务切模型、热切换、后台编辑”
- 插件治理已经从“只有异常隔离”升级成“有预算、软隔离、健康、来源校验和治理队列”
- 记忆与黑话已经不再只是关键词硬匹配，补上了轻量语义检索和更强的审核治理
- Admin Web 已经不再只是基础面板，而是具备明显更强的运行管理深度

但旧审计里指出的几个“骨头级短板”依然存在：

- 插件开发方式仍然偏重，还是类继承 + 重写钩子
- 仍然没有 IoC 容器
- 仍然没有 YAML / 可编排 Workflow
- 仍然没有进程级插件隔离
- 仍然没有真正的向量数据库 / 图谱记忆 / Alembic 式迁移体系

## 一、当前三层架构概览

```text
┌────────────────────────────────────────────────────────────┐
│                      kernel/ 内核层                         │
│  PluginBus · 类型契约 · 配置系统 · NoneBot 路由桥接        │
│  原则：不做业务 I/O，不直接调 LLM，不 import 服务/插件     │
├────────────────────────────────────────────────────────────┤
│                    services/ 服务层                         │
│  LLMClient · PromptBuilder · RetrievalGate · CardStore     │
│  SimilarityProvider · ToolRegistry · Scheduler             │
│  Health · PluginIndex · GroupProfileAudit · SlangStore     │
├────────────────────────────────────────────────────────────┤
│                    plugins/ 插件层                          │
│  Chat · Slang · Memo · Sticker · Affection · Schedule      │
│  Dream · Knowledge · Vision · Bilibili · Food ...          │
└────────────────────────────────────────────────────────────┘
         ↑ NoneBot2 + adapter-onebot + NapCatQQ
```

当前代码证据：

- 三层约束与目录说明：`docs/architecture.md`
- 内核类型与 `PluginContext`：`kernel/types.py`
- `PluginBus` 注册、拓扑排序、钩子调度、软隔离：`kernel/bus.py`
- 路由桥接：`kernel/router.py`
- 运行期本地插件总数：19 个 `AmadeusPlugin` 子类，来源于 `plugins/`

当前 `PluginBus` 仍然围绕 8 个主钩子工作：

- `on_startup`
- `on_shutdown`
- `on_bot_connect`
- `on_message`
- `on_pre_prompt`
- `on_post_reply`
- `on_thinker_decision`
- `on_tick`

和旧审计相比，`PluginBus` 的核心形态没变，但外围治理能力已经增强：

- 依赖拓扑排序仍然是 Kahn 算法
- 仍然有单插件异常隔离
- 新增了 `hook_budget_ms`
- 新增了软隔离冷却和抑制计数
- 新增了插件健康快照
- 新增了 manifest / source / `plugin.sig` 治理信息

## 二、旧审计结论中，哪些已经变了

| 旧结论 | 现在的结论 | 当前状态 |
| --- | --- | --- |
| LLM 多模型能力一般，Provider 抽象偏少 | 现在已经有 profile 体系、任务映射、热切换、限流、后台编辑 | 已明显改善 |
| Dashboard 功能较基础 | 现在已有系统健康、协议追踪、插件治理、群 Profile、黑话审核、配置 diff/回滚 | 已明显改善 |
| 无内置知识库 | 现在有轻量知识库插件和 `/admin/knowledge` 检索页，但还不是重型 RAG | 已部分改善 |
| 无向量检索 / RAG | 现在有轻量语义检索 `SimilarityProvider + RetrievalGate`，默认 `ngram`，仍无真正向量库 | 已部分改善 |
| 插件隔离差 | 现在仍是同进程，但已有软隔离、冷却、预算、健康可视化 | 已部分改善 |
| 插件生态弱 | 仍然没有社区级插件生态，但已有本地插件索引、兼容检查、签名预留、治理队列 | 已部分改善 |
| 插件注册方式偏重 | 仍然是类继承 `AmadeusPlugin` + 重写方法 | 没变 |
| 没有 IoC | `PluginContext` 仍是大上下文对象，靠手动传服务 | 没变 |
| 没有 Workflow 引擎 | 流程仍主要写在 router / LLM 调用链里 | 没变 |
| 数据库迁移能力弱 | 仍是幂等 `CREATE TABLE IF NOT EXISTS` / `ALTER TABLE`，没有 Alembic | 没变 |

## 三、Omubot 当前的强项

### 1. 内核纯净度仍然是强项

这条旧审计没有变，而且今天依然是 Omubot 的底牌。

- `kernel/` 负责类型、总线、路由桥接和配置解析
- `services/` 提供能力
- `plugins/` 负责业务行为

这种边界让 Omubot 很适合做“针对陪伴型 QQ bot 的持续增强”，不会轻易把底层拖成一锅粥。

### 2. Prompt 和上下文编排能力依然强

旧审计里把它列为优势，这条现在仍然成立，而且更完整了。

原因：

- `PluginBus.on_pre_prompt` 仍然是统一入口
- `PromptBlock` 仍然能区分 `static / stable / dynamic`
- 仍然有上下文压缩、时间线、短期记忆、memo 注入
- 现在又加上了群级提示词、工具矩阵、黑话注入和轻量语义检索

这意味着 Omubot 依然不是“把所有上下文都胡乱塞进 prompt”的架构。

### 3. Provider 治理已经从弱项转成中强项

这是这轮变化里最明显的一块。

现在已有：

- `llm.profiles`
- `llm.default_profile`
- `llm.task_profiles`
- Anthropic / OpenAI provider registry
- 按任务选模型
- 按 profile 看限流、冷却、错误
- 系统页热切换
- 系统页 profile 连通性测试
- 系统页 profile 定义编辑器
- API Key 保留 / 替换 / 清空

和旧审计相比，Omubot 现在的短板不再是“不会治理多模型”，而是“适配器家族数量还不够多”。

### 4. 插件治理能力已经明显上了一个台阶

旧审计只看到“异常隔离”，现在应该改写成“治理能力成型，但仍未到进程级”。

当前已有：

- 插件启停持久化
- 权限门禁
- `settings_schema`
- 配置保存
- 健康快照
- `hook_budget_ms`
- 软隔离冷却
- 本地插件索引
- 来源检查
- 兼容性检查
- `plugin.sig` 预留

所以现在更准确的说法是：

- Omubot 还没有 LangBot 那种独立进程隔离
- 但已经不是“插件一旦异常就只能看日志干瞪眼”的水平了

### 5. 黑话系统已经从“功能点”变成“治理子系统”

这在旧审计里几乎没有被完整体现，现在必须单独写出来。

当前黑话系统已具备：

- 候选学习
- 人工审核
- AI 通过标记
- pending buffer
- 复核队列
- 语义漂移审核
- 修订历史
- Prompt 注入
- lookup tool
- 群级隔离
- 每日 AI 识别
- Web 审核与设置

这使 Omubot 在“群内语境治理”这件事上，已经明显强于很多只会自动学习、不太可回滚的竞品路线。

### 6. Admin Web 已经明显超出“基础面板”阶段

旧审计里的“后台较基础”现在不再准确。

当前后台已经有：

- 系统健康聚合
- 协议能力探测
- Provider 管理
- 插件治理
- 群 Profile 编辑
- 记忆管理
- 黑话审核与统计
- 配置结构化编辑
- 配置 diff 预览
- 配置审计历史
- 配置快照恢复
- 重启提示与维护窗口建议

它还没有 AstrBot 那种更强的平台化控制台厚度，但已经不能再归类为“只是基础面板”。

## 四、Omubot 当前仍然明显偏弱的地方

| 维度 | 当前评价 | 说明 |
| --- | --- | --- |
| 插件开发门槛 | 偏弱 | 还是类继承 + 重写方法；和 AstrBot 装饰器式注册相比更重 |
| IoC / 解耦 | 偏弱 | `PluginContext` 仍然很大，插件仍需知道较多上下文结构 |
| Workflow 可编排性 | 偏弱 | 没有 kirara-ai 那种 YAML / block workflow |
| 进程级隔离 | 偏弱 | 只有软隔离，没有独立进程 / stdio / websocket 插件运行时 |
| 真正的向量检索 / RAG | 偏弱 | 只有轻量语义检索，没有 FAISS / Chroma / Qdrant 主线 |
| 知识图谱 | 偏弱 | 仍然没有 Graph Memory / Knowledge Graph |
| 多平台 | 偏弱 | 仍绑定 OneBot v11 场景，不是多 IM 平台产品 |
| 数据库迁移 | 偏弱 | 仍缺 schema version ledger 和标准迁移工具 |
| 社区生态 | 偏弱 | 有本地治理，但没有 NoneBot / Koishi 那种广泛生态 |

## 五、更新后的竞品差异判断

### 对 AstrBot

Omubot 现在和 AstrBot 的差距已经不再集中在“后台太弱”或“Provider 太原始”，而更集中在：

- 插件开发方式不够轻
- 没有更通用的平台化抽象
- 没有默认重型 RAG 栈

换句话说，Omubot 现在更像“专注 QQ 陪伴型 bot 的可治理系统”，AstrBot 更像“更通用的 AI bot 平台”。

### 对 kirara-ai

差距仍主要在：

- IoC
- Workflow
- 多适配器数量

但 Omubot 现在在“群内语境治理、Prompt 运营、黑话审核、后台运维”上已经有自己很强的垂直优势。

### 对 LangBot

差距仍主要在：

- 进程级插件隔离
- 多向量数据库
- 更平台化的插件 SDK 和迁移体系

但 Omubot 默认更轻，QQ 陪伴场景也更聚焦，不需要照搬 LangBot 的重平台思路。

### 对 MoFox / MaiBot 路线

差距仍主要在：

- 图记忆 / embedding / 表达学习的激进程度

但 Omubot 当前明显更强调：

- 可审核
- 可回滚
- 可解释
- 可运营

这条路线没有错，反而是 Omubot 的差异化价值。

## 六、更新后的架构差异矩阵

| 特性 | Omubot 当前状态 | 和旧审计相比 |
| --- | --- | --- |
| 插件模型 | 类继承 + `PluginBus` | 没变 |
| 插件治理 | 健康、预算、软隔离、签名预留、本地索引 | 变强很多 |
| 事件机制 | `PluginBus` 自研总线 | 没变 |
| 流程控制 | 仍以代码主导的消息/Prompt/Tool 流程 | 基本没变 |
| 记忆系统 | CardStore + Timeline + ShortTerm + 轻量语义检索 | 变强 |
| 黑话系统 | 审核、AI 复核、漂移治理、lookup tool | 变强很多 |
| LLM 调用 | Provider registry + profile/task 映射 + 热切换 | 变强很多 |
| 知识库 | 轻量关键词知识库插件 | 从“无”变成“轻量有” |
| 多平台 | 仍是 OneBot v11 | 没变 |
| Dashboard | 运维能力显著增强 | 变强很多 |
| 数据迁移 | 幂等式迁移 | 没变 |

## 七、更新后的改进优先级

### P0

#### 1. 给插件开发再套一层更轻的注册外观

目标不是推翻 `PluginBus`，而是在它上面加更轻的开发方式，比如：

- 装饰器注册
- helper factory
- 更小的能力切片

这样可以保留现在的总线与 ABI，又降低写插件的心智负担。

#### 2. 把轻量语义检索继续往 optional extra 推进一小步

当前已经有：

- `SimilarityProvider`
- `ngram`
- `embedding` stub

下一步不是默认上重依赖，而是把真正的 embedding backend 做成可选安装、可安全降级。

### P1

#### 3. 缩小 `PluginContext`，逐步做轻量依赖注入

不一定一步上完整 IoC 容器，但至少可以先做：

- 更小的 context slice
- 按能力注入
- 减少插件直接感知所有全局服务

#### 4. 为不可信插件准备更强隔离方案

现在软隔离已经够本仓库内插件使用。  
如果未来真的要开放第三方插件包，才值得继续做：

- 子进程 runtime
- stdio / websocket bridge
- 更强的资源与超时限制

### P2

#### 5. 给聊天主路径补“可配置流程块”，但不要过度平台化

不建议直接搬 kirara-ai 的全套 workflow 引擎。  
更适合 Omubot 的方式是：

- 先把 pre-prompt / tool policy / post-reply 副作用做成可配置 block
- 优先服务陪伴型聊天，而不是做通用自动化编排平台

#### 6. 增加数据库 schema 版本账本

不一定非得一步上 Alembic，但至少应该有：

- schema version
- 迁移历史
- 升级失败后的回退点

### P3

#### 7. 只有在产品方向真的需要时，再考虑知识图谱

知识图谱不是“越早上越好”。  
对 Omubot 来说，它更像一个高成本方向，只有在确实要做：

- 实体关系长期记忆
- 更强的推理式知识引用
- 多层关系检索

时才值得进入主线。

## 八、最终结论

旧审计里的总判断“Omubot 底层强、上层偏弱”现在需要改写成更准确的话：

> Omubot 的底层仍然强，而且上层已经不再是“普遍偏弱”，而是进入了“治理能力更完整、平台能力仍偏专用”的阶段。

今天的 Omubot 更准确的画像是：

- 不是通用多平台 AI 机器人平台
- 不是默认重依赖的向量 / 图谱系统
- 不是社区插件市场型产品

而是一个：

- Kernel 边界清楚
- PluginBus 生命周期稳定
- QQ 场景适配深
- 群内语境治理强
- 后台运维能力成熟
- 默认依赖仍然轻的陪伴型 bot 架构

如果继续往下走，最值得做的已经不是“把底层推倒重写”，而是：

1. 降低插件开发门槛  
2. 继续补 optional 的语义能力  
3. 做更轻的服务注入与流程块  
4. 只在确实需要第三方生态时再上更重的隔离与迁移体系

## 主要证据文件

- `docs/architecture.md`
- `kernel/types.py`
- `kernel/bus.py`
- `kernel/router.py`
- `kernel/config.py`
- `services/llm/client.py`
- `services/memory/retrieval.py`
- `services/similarity.py`
- `services/plugin_index.py`
- `services/health.py`
- `services/group_profile_audit.py`
- `plugins/knowledge.py`
- `plugins/slang/plugin.py`
- `admin/routes/api/providers.py`
- `admin/routes/api/groups.py`
- `admin/frontend/src/views/system/SystemView.vue`
- `admin/frontend/src/views/groups/GroupsView.vue`
- `admin/frontend/src/views/plugins/PluginsView.vue`
- `admin/frontend/src/views/slang/SlangView.vue`
