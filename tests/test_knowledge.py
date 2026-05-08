"""KnowledgeBase tests: indexing and retrieval."""

import pytest

from services.knowledge import KnowledgeBase
from services.knowledge.retrievers import tokenize


@pytest.fixture
def kb_with_docs(tmp_path) -> KnowledgeBase:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "test.md").write_text(
        "# 测试文档\n\n"
        "## 安装指南\n"
        "使用 Docker Compose 部署机器人，需要 Python 3.12+。\n\n"
        "## 配置说明\n"
        "LLM API Key 在 config.toml 中设置，支持 Anthropic 和 OpenAI 格式。\n\n"
        "## 故障排查\n"
        "如果 NapCat 连接失败，检查 WebSocket 地址和防火墙设置。\n",
        encoding="utf-8",
    )
    kb = KnowledgeBase(docs_dir=str(docs))
    kb.reload()
    return kb


def test_reload_empty_dir(tmp_path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    kb = KnowledgeBase(docs_dir=str(empty))
    n = kb.reload()
    assert n == 0
    assert kb.chunk_count == 0


def test_reload_missing_dir() -> None:
    kb = KnowledgeBase(docs_dir="/nonexistent/path/xyz")
    n = kb.reload()
    assert n == 0


def test_chunk_count(kb_with_docs: KnowledgeBase) -> None:
    assert kb_with_docs.chunk_count == 3


def test_retrieve_exact_match(kb_with_docs: KnowledgeBase) -> None:
    results = kb_with_docs.retrieve("Docker Compose 部署")
    assert len(results) > 0
    assert any("Docker Compose" in r for r in results)


def test_retrieve_chinese_query(kb_with_docs: KnowledgeBase) -> None:
    results = kb_with_docs.retrieve("怎么配置 API Key")
    assert len(results) > 0
    assert any("API Key" in r or "config.toml" in r for r in results)


def test_retrieve_napcat(kb_with_docs: KnowledgeBase) -> None:
    results = kb_with_docs.retrieve("NapCat 连不上")
    assert len(results) > 0
    assert any("NapCat" in r for r in results)


def test_retrieve_no_match(kb_with_docs: KnowledgeBase) -> None:
    results = kb_with_docs.retrieve("今天天气怎么样")
    assert results == []


def test_retrieve_empty_query(kb_with_docs: KnowledgeBase) -> None:
    results = kb_with_docs.retrieve("")
    assert results == []


def test_retrieve_respects_top_k(kb_with_docs: KnowledgeBase) -> None:
    results = kb_with_docs.retrieve("部署 配置 排查", top_k=2)
    assert len(results) <= 2


def test_retrieve_auto_loads(tmp_path) -> None:
    kb = KnowledgeBase(docs_dir=str(tmp_path))
    results = kb.retrieve("nonexistent_query_string_xyz")
    assert results == []


def test_persistent_index_restores_chunks_without_reindex(tmp_path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text(
        "# 指南\n\n"
        "## 持久索引\n"
        "SQLite 持久索引可以在重启后恢复知识库 chunk。\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "knowledge_index.db"

    kb = KnowledgeBase(docs_dir=str(docs), index_db_path=str(db_path))
    kb.reload()
    kb.close()

    restored = KnowledgeBase(docs_dir=str(docs), index_db_path=str(db_path), auto_load=False)
    try:
        assert restored.loaded is True
        assert restored.chunk_count == 1
        results = restored.retrieve("SQLite 持久索引")
        assert len(results) == 1
        assert "重启后恢复" in results[0]
        assert restored.stats()["index_persisted"] is True
    finally:
        restored.close()


def test_reindex_only_rechunks_changed_sources(tmp_path, monkeypatch) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n\n## Alpha\nAlpha 初始内容。\n", encoding="utf-8")
    (docs / "b.md").write_text("# B\n\n## Beta\nBeta 初始内容。\n", encoding="utf-8")
    db_path = tmp_path / "knowledge_index.db"

    kb = KnowledgeBase(docs_dir=str(docs), index_db_path=str(db_path))
    kb.reload()
    kb.close()

    (docs / "a.md").write_text("# A\n\n## Alpha\nAlpha 修改后的内容。\n", encoding="utf-8")

    from services.knowledge import service as knowledge_service_module

    original_chunk_markdown = knowledge_service_module.chunk_markdown
    rechunked: list[str] = []

    def _recording_chunk_markdown(**kwargs):
        rechunked.append(kwargs["path"].name)
        return original_chunk_markdown(**kwargs)

    monkeypatch.setattr(knowledge_service_module, "chunk_markdown", _recording_chunk_markdown)
    restored = KnowledgeBase(docs_dir=str(docs), index_db_path=str(db_path), auto_load=False)
    try:
        restored.reindex()
        assert rechunked == ["a.md"]
        assert restored.chunk_count == 2
        assert any("修改后的内容" in item for item in restored.retrieve("Alpha 修改"))
        assert any("Beta 初始内容" in item for item in restored.retrieve("Beta 初始"))
    finally:
        restored.close()


def test_reindex_missing_dir_clears_persistent_index(tmp_path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text(
        "# 指南\n\n## 清理\n目录缺失时不能恢复旧 chunk。\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "knowledge_index.db"

    kb = KnowledgeBase(docs_dir=str(docs), index_db_path=str(db_path))
    kb.reload()
    kb.close()
    (docs / "guide.md").unlink()
    docs.rmdir()

    missing = KnowledgeBase(docs_dir=str(docs), index_db_path=str(db_path), auto_load=False)
    try:
        assert missing.reindex() == 0
        assert missing.chunk_count == 0
    finally:
        missing.close()

    restored = KnowledgeBase(docs_dir=str(docs), index_db_path=str(db_path), auto_load=False)
    try:
        assert restored.chunk_count == 0
        assert restored.retrieve("目录缺失") == []
    finally:
        restored.close()


def test_reindex_allows_duplicate_markdown_headings(tmp_path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "duplicates.md").write_text(
        "# 重复标题\n\n"
        "## 排查\n"
        "第一段排查内容。\n\n"
        "## 排查\n"
        "第二段排查内容。\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "knowledge_index.db"

    kb = KnowledgeBase(docs_dir=str(docs), index_db_path=str(db_path))
    try:
        assert kb.reload() == 2
        hits = kb.search_hits("排查 内容", top_k=5)
        ids = {hit.chunk_id for hit in hits}
        assert "duplicates.md::section-1::排查" in ids
        assert "duplicates.md::section-2::排查" in ids
    finally:
        kb.close()


def test_production_knowledge_dir_excludes_development_docs(tmp_path) -> None:
    root = tmp_path / "repo"
    knowledge_dir = root / "docs" / "knowledge"
    audits_dir = root / "docs" / "audits"
    (knowledge_dir / "omubot").mkdir(parents=True)
    audits_dir.mkdir(parents=True)
    (knowledge_dir / "omubot" / "architecture.md").write_text(
        "# Omubot 系统架构\n\n"
        "## Omubot 的系统架构是什么\n"
        "Omubot 使用 Kernel、Services、Plugins 三层架构，PluginBus 负责插件调度。\n",
        encoding="utf-8",
    )
    (audits_dir / "noisy-audit.md").write_text(
        "# 审计噪声\n\n"
        "## Omubot 的系统架构是什么\n"
        "这是一份不应该进入生产知识库的审计记录。\n",
        encoding="utf-8",
    )

    kb = KnowledgeBase(docs_dir=str(knowledge_dir), recursive=True)
    try:
        assert kb.reload() == 1
        assert {source.source for source in kb.sources()} == {"omubot/architecture.md"}
    finally:
        kb.close()


def test_production_knowledge_prioritizes_architecture_query(tmp_path) -> None:
    docs = tmp_path / "knowledge"
    (docs / "omubot").mkdir(parents=True)
    (docs / "omubot" / "architecture.md").write_text(
        "# Omubot 系统架构\n\n"
        "## Omubot 的系统架构是什么\n"
        "Omubot 的系统架构是 Kernel、Services、Plugins 三层架构。"
        "Kernel 负责 PluginBus 和类型契约，Services 负责 LLM、记忆、知识库，Plugins 负责业务行为。\n\n"
        "关键词：omubot的系统架构是什么、Omubot 系统架构、三层架构、PluginBus\n",
        encoding="utf-8",
    )
    (docs / "omubot" / "knowledge-system.md").write_text(
        "# Omubot 知识系统\n\n"
        "## 文档知识库\n"
        "文档知识库保存稳定资料，ContextPlugin 会统一注入上下文资料。\n",
        encoding="utf-8",
    )

    kb = KnowledgeBase(docs_dir=str(docs), recursive=True)
    try:
        kb.reload()
        hits = kb.search_hits("omubot的系统架构是什么", top_k=3)
        assert hits
        assert hits[0].source == "omubot/architecture.md"
        assert any(hit.source == "omubot/architecture.md" for hit in kb.search_hits("三层架构", top_k=3))
        assert all("ai-persona-generation-rules" not in hit.source for hit in kb.search_hits("AI人设负责什么", top_k=5))
    finally:
        kb.close()


def test_chinese_stopword_only_query_has_no_tokens() -> None:
    assert tokenize("的了是什么怎么如何一下这个") == []
