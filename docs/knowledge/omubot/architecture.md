# Omubot 系统架构

## Omubot 的系统架构是什么

Omubot 是一个面向 QQ 群陪伴场景的机器人，运行链路是：QQ 消息通过 NapCat / OneBot v11 进入 NoneBot2，再交给 Omubot 的三层框架处理，最后由 LLM 和工具系统生成回复。

Omubot 的核心不是一个单体脚本，而是三层架构：内核层、系统服务层、插件层。

关键词：Omubot 系统架构、omubot的系统架构是什么、三层架构、QQ 机器人架构、NapCat、NoneBot2、OneBot v11

## 三层架构

Omubot 的三层架构分别是：

- 内核层 Kernel：负责 PluginBus、类型契约、配置解析和 NoneBot 路由。内核保持小而稳定，不直接 import 系统服务或插件。
- 系统服务层 Services：负责 LLM 调用、Prompt 构建、记忆卡片、时间线、调度器、图片缓存、表情库、知识库和知识图谱等可复用能力。
- 插件层 Plugins：负责聊天、记忆、好感度、日程、表情包、黑话、知识库、上下文系统等可治理功能，通过钩子接入 PluginBus。

判断归属的简单规则：只做调度和类型的是内核；提供可复用 I/O 或计算能力的是系统服务；可以单独启停、面向业务行为的是插件。

关键词：三层架构、Kernel、Services、Plugins、内核层、系统服务层、插件层

## PluginBus 的职责

PluginBus 是 Omubot 的插件总线，负责注册插件、按优先级和依赖顺序调用生命周期钩子，并在消息、Prompt、回复、tick 等阶段调度插件。

常见钩子包括：

- `on_startup`：Bot 启动时初始化资源。
- `on_message`：消息进入时拦截或消费。
- `on_pre_prompt`：构建 Prompt 前追加上下文资料。
- `on_post_reply`：回复后记录副作用。
- `on_tick`：周期性后台任务。

关键词：PluginBus、插件总线、钩子、on_pre_prompt、on_message、on_startup

## ContextService 与知识库的位置

ContextService 位于系统服务层，负责统一检索记忆卡片、文档知识库和知识图谱事实，并把结果打包成一个动态上下文块。

KnowledgePlugin 负责加载 Markdown 文档知识库；ContextPlugin 默认接管动态 Prompt 注入，把 `memory_card`、`doc_chunk`、`graph_fact` 统一注入为“上下文资料”。因此日常日志里不一定会出现单独的“知识库注入”，而是表现为 ContextPlugin 的上下文命中。

关键词：ContextService、ContextPlugin、KnowledgePlugin、文档知识库、doc_chunk、上下文资料

## 为什么不是通用运维平台

Omubot 的设计目标是陪伴型 QQ 群 bot，不是通用多平台运维平台。它保留轻量默认栈：SQLite、本地 BM25/ngram 检索、插件目录化治理，不默认依赖 Neo4j、FAISS、Redis 或向量数据库。

这让 Omubot 更容易在 Docker Compose 下部署、重建和排查，同时保留未来扩展知识图谱、向量检索和插件生态的接口。

关键词：轻量默认栈、Docker Compose、SQLite、BM25、ngram、向量数据库、知识图谱
