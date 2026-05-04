"""KnowledgeBase tests: indexing and retrieval."""

import pytest

from services.knowledge import KnowledgeBase


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
