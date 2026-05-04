"""KnowledgePlugin: 文件知识库注入 system prompt。

启动时扫描 docs/ 目录的 markdown 文件，按 ## 标题分块构建倒排索引。
每轮对话通过 on_pre_prompt 检索相关块，挂载为 PromptBlock。
"""

from __future__ import annotations

from loguru import logger
from pydantic import BaseModel

from kernel.config import load_plugin_config
from kernel.types import AmadeusPlugin, PluginContext, PromptContext
from services.knowledge import KnowledgeBase

_L = logger.bind(channel="system")


class KnowledgePlugin(AmadeusPlugin):
    name = "knowledge"
    description = "知识库：扫描文档目录，关键词匹配注入 system prompt"
    version = "0.1.0"
    priority = 8  # 基础设施层

    def __init__(self) -> None:
        super().__init__()
        self._kb: KnowledgeBase | None = None
        self._max_chunks: int = 3
        self._enabled: bool = False

    async def on_startup(self, ctx: PluginContext) -> None:
        class _Cfg(BaseModel):
            enabled: bool = False
            dir: str = "docs"
            max_chunks: int = 3

        cfg = load_plugin_config("plugins/knowledge.toml", _Cfg)
        self._enabled = cfg.enabled
        if not self._enabled:
            _L.info("knowledge plugin disabled")
            return

        self._kb = KnowledgeBase(docs_dir=cfg.dir)
        n = self._kb.reload()
        self._max_chunks = cfg.max_chunks
        _L.info("knowledge base loaded | dir={} chunks={}", cfg.dir, n)

    async def on_pre_prompt(self, ctx: PromptContext) -> None:
        if not self._enabled or self._kb is None:
            return
        if not ctx.conversation_text:
            return
        chunks = self._kb.retrieve(ctx.conversation_text, top_k=self._max_chunks)
        if chunks:
            ctx.add_block(
                text="\n---\n".join(chunks),
                label="知识库",
                position="static",
            )
