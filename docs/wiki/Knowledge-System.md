# 知识库与知识导入指南

本文说明 Omubot 当前知识库是什么、遵循什么规则，以及新增知识文档时应该怎么写。

## 一句话理解

Omubot 现在有三类“长期上下文”：

| 类型 | 用途 | 权威存储 | 是否承载用户/群记忆 |
| --- | --- | --- | --- |
| 记忆卡片 | 用户偏好、群内事实、互动记录 | `CardStore` / `storage/memory_cards.db` | 是 |
| 文档知识库 | 项目说明、设定文档、规则手册、可查资料 | Markdown 文件 + 运行时内存索引 | 否 |
| 知识图谱 | 从卡片或文档派生出的实体关系事实 | `storage/knowledge_graph.db` | 否，派生层 |

当前文档知识库不替代记忆卡片。用户/群相关事实仍应写入记忆卡片；文档知识库适合放“稳定资料”和“可查手册”。

## 当前运行状态

本机当前已通过运行时覆盖配置打开文档知识库：

```text
storage/plugins/config/knowledge.json
```

当前运行态：

| 项目 | 当前值 |
| --- | --- |
| 插件状态 | 已启用 |
| 扫描目录 | `docs/knowledge` |
| 持久索引 | `storage/knowledge_index.db` |
| 当前仓库文档源 | 7 个 Markdown 文件（`docs/knowledge/music-games/*` 与 `docs/knowledge/omubot/*`） |
| 动态注入方式 | 由 `ContextPlugin` 统一打包为 `上下文资料` |

说明：生产聊天知识现在只扫描 `docs/knowledge`。`docs/wiki`、`docs/audits`、`docs/superpowers` 继续保留为研发和留档资料，但默认不进入日常聊天检索。实际 chunk 数以 `/admin/knowledge` 的索引状态为准；修改知识文档后需要重新扫描。

## 当前结构

默认知识库插件配置在：

```text
plugins/knowledge/config.default.json
```

运行时 Web 覆盖配置在：

```text
storage/plugins/config/knowledge.json
```

默认配置含义：

```json
{
  "schema_version": 1,
  "plugin": "knowledge",
  "values": {
    "enabled": false,
    "dir": "docs/knowledge",
    "index_db_path": "storage/knowledge_index.db",
    "max_chunks": 3,
    "recursive": true,
    "include": ["*.md", "**/*.md"],
    "exclude": ["drafts/**", "**/*.draft.md", "_archive/**", "._*.md", "**/._*.md"]
  }
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `enabled` | 是否启用知识库插件。默认关闭。 |
| `dir` | 扫描哪个目录，生产默认 `docs/knowledge`。 |
| `index_db_path` | SQLite 持久索引数据库路径，默认 `storage/knowledge_index.db`。 |
| `max_chunks` | 旧 Prompt 注入路径每轮最多注入几个文档片段。 |
| `recursive` | 是否递归扫描子目录。 |
| `include` | 相对 `dir` 的包含规则，使用 glob。 |
| `exclude` | 相对 `dir` 的排除规则，使用 glob；默认排除草稿、归档和 macOS `._*.md` 资源叉文件。 |

生产聊天知识推荐集中放在：

```text
docs/knowledge/
```

然后把知识库配置改成：

```json
{
  "schema_version": 1,
  "plugin": "knowledge",
  "values": {
    "enabled": true,
    "dir": "docs/knowledge",
    "index_db_path": "storage/knowledge_index.db",
    "max_chunks": 3,
    "recursive": true,
    "include": ["*.md", "**/*.md"],
    "exclude": ["drafts/**", "**/*.draft.md", "_archive/**", "._*.md", "**/._*.md"]
  }
}
```

这样可以避免把审计报告、开发计划、维护日志全部混进日常知识库。

## 索引规则

当前知识库索引规则很简单：

1. 只扫描 Markdown 文件：`*.md`。
2. 文件必须是 UTF-8。
3. 默认递归扫描子目录。
4. 文件是否进入索引由 `include` 和 `exclude` 决定。
5. 每个文件按 `## 二级标题` 切成片段。
6. `# 一级标题` 会作为文档标题。
7. 每个 `##` 下方的正文会形成一个 chunk。
8. 空的 `##` 不会被索引。
9. `###`、列表、表格、代码块会保留在当前 `##` chunk 内。
10. chunk_id 使用 `source + 章节序号 + 标题`，例如 `guide.md::section-2::排查`。
11. 同一个文件里允许出现重复 `##` 标题；章节序号会保证 chunk_id 不冲突。
12. chunk/source 索引会持久化到 SQLite；运行时仍使用内存 BM25/ngram 检索。
13. 重启后会优先从 SQLite 恢复索引；执行重建索引时，未变化的文件会按 source hash 复用持久索引，变化文件才重新切 chunk。
14. 修改文件后仍建议在 Web 中点击“重新扫描”，让 hash 与 chunk 及时更新。

重要提醒：请把真正想被检索的内容写在 `##` 标题下面。不要只写一个 `# 标题` 后直接堆全文。

## 检索规则

当前默认检索是本地轻量检索，不依赖向量数据库：

- 中文：会使用单字和相邻双字。
- 中文查询会过滤常见低信息疑问词，例如“的、了、是、什么、怎么、如何、一下、这个”。
- 英文 / 数字：会使用长度 2 以上的单词，统一小写。
- 排序：使用轻量 BM25/ngram 分数。
- 命中结果会带 `title`、`source`、`chunk_id`、`score`、`content`。

这意味着：

- 标题很重要，`##` 标题应包含用户可能搜索的关键词。
- 中文别名、英文名、缩写最好在正文里都出现一次。
- 它不是 embedding，不会真正理解复杂同义改写。
- 如果一个概念有多个常用叫法，请在同一个 chunk 中写清楚。
- 如果 query 全是停用词，会返回空结果，避免为了填充 Prompt 误召无关资料。

## 推荐导入格式

推荐每个主题一个文件，每个可独立回答的问题一个 `##`。

```markdown
# 群聊规则手册

## 发言边界

本群允许轻松闲聊，但不要刷屏、不要连续发送大段无关内容。
如果用户问“群里能不能刷屏”，应回答：不建议刷屏，会影响其他人阅读。

关键词：刷屏、发言规则、聊天边界、群规

## 图片与表情包

可以发送表情包，但不要连续发送大量重复图片。
如果用户问“能不能发表情包”，应回答：可以，但要适量。

关键词：表情包、图片、刷图、重复发送
```

为什么这样写：

- `# 群聊规则手册` 会成为文档级标题。
- `## 发言边界` 和 `## 图片与表情包` 会变成两个独立 chunk。
- 每个 chunk 都能单独回答一个问题。
- “关键词”不是特殊语法，只是为了提升字面检索命中。

## 不推荐写法

不要这样写：

```markdown
# 群聊规则手册

这里写了很多很多规则……
这里继续写很多内容……
这里又写另一个主题……
```

原因：

- 当前切分主要依赖 `##`。
- 只写 `#` 后直接堆正文，容易不形成稳定 chunk。
- 多个主题混在一起会导致检索命中不准。

也不要这样写：

```markdown
# 群聊规则手册

## 规则

这里把所有规则全部塞进一个超长章节……
```

原因：

- chunk 太长会稀释检索分数。
- 结果注入 Prompt 时会带入太多无关内容。

## 目录建议

如果知识很多，推荐按主题拆目录：

```text
docs/knowledge/
  bot/
    persona.md
    reply-style.md
  groups/
    group-993065015.md
    shared-rules.md
  projects/
    omubot.md
    deployment.md
  glossary/
    terms.md
```

命名建议：

- 文件名用英文或拼音，稳定、短、可读。
- 文件内标题用中文，方便搜索和阅读。
- 一个文件负责一个主题，不要把完全无关的知识塞进同一文件。

## 导入流程

### 1. 准备目录

推荐新建：

```text
docs/knowledge/
```

### 2. 写 Markdown

每份文档遵循：

```markdown
# 文档标题

## 一个明确问题或主题

这里写答案、规则、事实、边界和必要例子。

关键词：可能会被用户问到的说法、别名、缩写

## 另一个明确问题或主题

这里写另一段可独立命中的内容。
```

### 3. 启用知识库插件

通过插件中心配置，或写入：

```text
storage/plugins/config/knowledge.json
```

示例：

```json
{
  "schema_version": 1,
  "plugin": "knowledge",
  "values": {
    "enabled": true,
    "dir": "docs/knowledge",
    "max_chunks": 3,
    "recursive": true,
    "include": ["*.md", "**/*.md"],
    "exclude": ["drafts/**", "**/*.draft.md"]
  }
}
```

配置改动通常需要重启 Bot。

### 4. 重启或重建

如果只是改 `storage/plugins/config/knowledge.json`，通常重启 Bot 即可。

如果改了代码或 Docker 镜像内文件，需要重建：

```bash
docker compose up -d --build bot
```

### 5. 重建索引

进入 Web：

```text
/admin/knowledge
```

在 `文档源` 页点击 `重建索引`。

### 6. 核对命中

在 `搜索核对` 页输入几个真实问题：

- 用户可能怎么问？
- 群友会用什么简称？
- 英文名、缩写、别名是否能搜到？

确认命中结果的：

- `title` 是否正确。
- `source` 是否来自预期文件。
- `score` 是否大致合理。
- `content` 是否足够独立回答问题。

### 7. 调试上下文

在 `上下文调试` 页输入一条真实聊天消息，可选填用户 ID / 群 ID。

这里会展示：

- `memory_card`：记忆卡片命中。
- `doc_chunk`：文档知识库命中。
- `graph_fact`：知识图谱事实命中。
- `Prompt Pack`：最终会被打包进上下文的文本。

`ContextPlugin` 现在是 system/locked 能力包，默认接管动态上下文。调整知识库、记忆或图谱抽取规则后，建议先用这个页面做人工评测，再观察真实聊天效果。

### 8. 查看评测指标

`评测指标` 页展示最近统一上下文检索的运行指标：

- 最近查询数。
- Miss 率。
- 平均 / 最大 Prompt Pack 字符数。
- 重复命中率。
- 省略命中数量。
- memory/doc/graph 命中类型分布。
- 命中来源分布。
- 最近查询列表。

这些指标用于判断 `ContextPlugin` 接管后是否出现漏召、重复注入或 Prompt 成本上涨。真实聊天样本越多，这个面板越有参考价值。

## 写作规范

### 每个 chunk 只回答一个主题

推荐：

```markdown
## DeepSeek V4 模式如何启用

把 LLM profile 的 api_format 设置为 deepseek，并使用 deepseek-v4-flash 或 deepseek-v4-pro。
```

不推荐：

```markdown
## DeepSeek

这里同时写模型、缓存、计费、报错、历史迁移、Docker、NapCat……
```

### 标题写用户会问的词

推荐：

```markdown
## NapCat 连接失败怎么排查
```

不推荐：

```markdown
## 网络问题
```

### 别名写进正文

如果一个词有多个说法，要放在同一个 chunk 里：

```markdown
## Omubot 管理端

Omubot 管理端也叫 Web 后台、Admin Console、控制台。
常用地址是 /admin。
```

### 稳定事实进知识库，个人事实进记忆卡片

适合知识库：

- 项目部署步骤
- 群规则
- Bot 操作手册
- 固定设定说明
- 技术方案摘要
- 常见问题排查

不适合知识库：

- “某个用户今天心情不好”
- “某个用户喜欢吃什么”
- “某个群刚刚发生了什么”
- “一次对话中的临时上下文”

这些应进入记忆卡片或短期上下文。

## 和 ContextService 的关系

知识库文档不会直接变成记忆卡片。它会通过 `KnowledgeContextSource` 以 `doc_chunk` 形式进入统一上下文检索。

当前统一上下文有三类来源：

| ContextHit 类型 | 来源 |
| --- | --- |
| `memory_card` | `CardStore` |
| `doc_chunk` | 文档知识库 |
| `graph_fact` | 知识图谱 |

默认情况下，`ContextPlugin` 会统一打包 memory/doc/graph，并注入一个 `上下文资料` 动态块，避免 `MemoPlugin` 和 `KnowledgePlugin` 重复注入。`ContextPlugin` 的 manifest 为 `tier=system`、`toggle_policy=locked`；日常不应在插件中心关闭它。

如果需要回滚，可在插件配置中关闭 `ContextPlugin.enabled` 或 `takeover_dynamic_prompt`。关闭后，旧路径会恢复：`KnowledgePlugin` 直接把知识库 chunk 注入动态 Prompt，`MemoPlugin` 直接注入实体记忆动态块。

## 和知识图谱的关系

文档知识库是“原文资料层”，知识图谱是“派生事实层”。

例如文档里写：

```markdown
## Omubot 与 NapCat

Omubot 通过 OneBot v11 与 NapCatQQ 连接。
```

轻量抽取器可以从命中的记忆卡片和文档片段中生成图谱事实：

```text
Omubot --通过协议连接-> OneBot v11
Omubot --依赖-> NapCatQQ
```

图谱不会替代文档或记忆卡片。图谱事实必须有证据来源，例如 `chunk_id` 或 `card_id`。高置信事实会自动 active，中置信事实进入候选队列，低置信结果会被忽略。

从记忆卡片派生出的图谱事实会继承原卡片的 `scope/scope_id`。例如用户私聊记忆只会在该用户私聊召回，群记忆只会在对应群或共享群池召回，避免把私有事实提升成全局知识。

在 Web 的 `图谱关系` 页可以查看 active fact 的证据、来源、作用域和取代关系，也可以回滚或用更准确的三元组取代事实。如果页面显示 `作用域待查`，说明存在旧版本迁移留下的 `global/global` 记忆证据事实，需要人工确认是否回滚。

## 常见问题

### 为什么新增文件后搜不到？

按顺序检查：

1. 文件是不是 `.md`。
2. 文件是不是 UTF-8。
3. 文件是否在 `dir` 目录下。
4. `include/exclude` 是否把它排除了。
5. 内容是否写在 `##` 标题下。
6. 是否点击了 `重建索引`。
7. 知识库插件是否启用。
8. Docker 后端是否已经重建/重启到新版。

### 为什么打开知识库后启动日志显示 hook error？

先看完整日志里的 `knowledge` 错误原因。

已修复过的一类问题是：

```text
sqlite3.IntegrityError: UNIQUE constraint failed: knowledge_chunks.chunk_id
```

原因是旧索引规则只用 `文件名::标题` 做 chunk_id，同一文件里重复 `##` 标题会撞唯一键。现在 chunk_id 已加入章节序号，重复标题可以正常索引。修复代码后需要重建镜像并重启：

```bash
docker compose build bot
docker compose up -d --no-deps --force-recreate bot
```

重启后日志应看到类似：

```text
knowledge base loaded | dir=docs chunks=257
```

### 为什么文档源显示 skipped？

常见原因：

- 文件为空。
- 文件没有可索引的 `##` 内容。
- 文件读取失败。

可以在 `/admin/knowledge` 的 `文档源` 页查看 `跳过原因`。

### 为什么命中结果不准？

常见原因：

- chunk 太长，一个标题下塞了太多主题。
- 用户常用词没有出现在标题或正文。
- 缩写、别名、英文名没有写进去。
- 问题是同义表达，但当前默认不是 embedding 检索。

解决方式：

- 拆小 `##`。
- 在标题中使用更直接的词。
- 在正文最后写一行 `关键词：...`。
- 对特别重要的主题写一个“常见问法”小节。

### 知识库能不能放审计报告？

可以，但不一定推荐。

如果知识库 `dir` 是默认 `docs`，审计报告、计划文档、wiki 都可能被递归扫进去。这适合开发调试，但不一定适合日常聊天。

日常使用更推荐：

```text
docs/knowledge/
```

把真正希望 Bot 引用的资料放进去。

### 知识库是否需要向量数据库？

当前不需要。

默认路线是轻量、可部署、可解释：Markdown + 内存索引 + BM25/ngram。向量库、GraphRAG、Neo4j 都是未来 optional extra，不进入默认栈。

## 推荐检查清单

新增知识文档后，逐项确认：

- 文件放在知识库 `dir` 下。
- 文件扩展名是 `.md`。
- 文件有一个清晰的 `#` 标题。
- 每个知识点都有独立 `##` 标题。
- 每个 `##` 下正文能独立回答一个问题。
- 重要别名、缩写、英文名写进正文。
- 不把用户个人事实写进知识库。
- 已重建索引。
- 已在 `搜索核对` 页测试真实问法。
- 已在 `上下文调试` 页确认不会和记忆卡片重复或冲突。
