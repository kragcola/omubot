# Omubot 管理端与排查

## 管理端入口

Omubot 管理端也叫 Web 后台、Admin Console、控制台。Docker 默认访问地址通常是 `http://localhost:8081/admin`，前端开发服务常用 `http://localhost:5173/admin`。

管理端用于查看运行状态、插件配置、知识库索引、上下文调试、日志和系统健康。

关键词：Omubot 管理端、Web 后台、Admin Console、控制台、/admin

## 如何确认知识库是否触发

知识库触发不是工具调用。当前默认由 ContextPlugin 统一接管，命中结果会作为“上下文资料”进入 Prompt。

排查顺序：

1. 看启动日志是否出现 `knowledge base loaded`。
2. 在 `/admin/knowledge` 的文档源页确认 chunk 数不为 0。
3. 在搜索核对页输入真实问题，查看命中的 source 和 title。
4. 在上下文调试页输入真实聊天消息，查看是否出现 `doc_chunk`。
5. 查看 ContextPlugin DEBUG 日志，确认 doc_chunk 数和命中摘要。

关键词：知识库触发、doc_chunk、knowledge base loaded、上下文调试、搜索核对

## 如何重建知识库索引

修改 `docs/knowledge` 下的 Markdown 后，应在 `/admin/knowledge` 的文档源页点击重建索引。也可以重启 Bot，让知识库在启动时重新扫描。

如果修改了知识库代码或 Docker 镜像内文件，需要重建并重启：

```bash
docker compose build bot
docker compose up -d --no-deps --force-recreate bot
```

关键词：重建索引、reindex、Docker 重建、知识库文档源

## 插件配置在哪里

插件默认配置放在 `plugins/<name>/config.default.json`。管理端保存的运行时覆盖放在 `storage/plugins/config/<name>.json`。

知识库插件的运行时覆盖是：

```text
storage/plugins/config/knowledge.json
```

当前生产知识库推荐配置为启用插件，并把目录指向 `docs/knowledge`。

关键词：插件配置、config.default.json、storage/plugins/config、knowledge.json

## 常见误解

看到 `plugin permission denied | plugin=knowledge permission=message` 不代表知识库坏了。KnowledgePlugin 只需要 `prompt` 和 `storage` 权限，所以它不会处理 message、tick 或 tool。

看到模型调用 `web_search` 也不一定代表知识库没启动，可能是本地命中不够准，模型选择了联网工具。生产知识目录收口后，本地已有的 Omubot 架构、知识系统和管理端问题应该优先由知识库资料回答。

关键词：permission denied、web_search、知识库没触发、权限门控、日志排查
