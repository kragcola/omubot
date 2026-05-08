"""Lightweight document knowledge service."""

from __future__ import annotations

import fnmatch
import hashlib
from collections.abc import Iterable
from pathlib import Path

from services.knowledge.chunking import chunk_markdown
from services.knowledge.retrievers import KeywordBM25Retriever
from services.knowledge.store import KnowledgeIndexStore
from services.knowledge.types import KnowledgeChunk, KnowledgeHit, KnowledgeSourceStatus


class KnowledgeService:
    """Local markdown knowledge base with structured search hits."""

    def __init__(
        self,
        docs_dir: str = "docs",
        *,
        include: Iterable[str] | None = None,
        exclude: Iterable[str] | None = None,
        recursive: bool = True,
        auto_load: bool = True,
        index_db_path: str | None = None,
    ) -> None:
        self._docs_dir = Path(docs_dir)
        self._include = tuple(include or ("*.md", "**/*.md"))
        self._exclude = tuple(exclude or ())
        self._recursive = recursive
        self._auto_load = auto_load
        self._chunks: dict[str, KnowledgeChunk] = {}
        self._sources: dict[str, KnowledgeSourceStatus] = {}
        self._retriever = KeywordBM25Retriever()
        self._loaded = False
        self._index_store = KnowledgeIndexStore(index_db_path) if index_db_path else None
        self._load_persisted_index()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def reload(self) -> int:
        """Backward-compatible alias for reindex()."""
        return self.reindex()

    def reindex(self) -> int:
        """Rescan docs_dir and rebuild/update the local index.

        When a SQLite index store is configured, unchanged sources are restored
        from the persistent index and only changed files are reparsed.
        """
        persisted_hashes = self._index_store.source_hashes() if self._index_store is not None else {}
        next_chunks: dict[str, KnowledgeChunk] = {}
        next_sources: dict[str, KnowledgeSourceStatus] = {}
        seen_sources: set[str] = set()

        if not self._docs_dir.is_dir():
            self._chunks.clear()
            self._sources.clear()
            self._loaded = True
            self._retriever.rebuild(self._chunks)
            if self._index_store is not None:
                self._index_store.clear()
            return 0

        for md_path in self._iter_markdown_files():
            source = self._source_name(md_path)
            seen_sources.add(source)
            try:
                text = md_path.read_text(encoding="utf-8")
            except Exception as exc:
                status = KnowledgeSourceStatus(
                    source=source,
                    path=str(md_path),
                    status="skipped",
                    skipped_reason=f"read_error:{type(exc).__name__}",
                )
                next_sources[source] = status
                if self._index_store is not None:
                    self._index_store.replace_source(status, [])
                continue

            source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            chunks = self._chunks_for_source(source)
            source_status = self._sources.get(source)
            unchanged = (
                source_hash == persisted_hashes.get(source)
                and source_status is not None
                and bool(chunks)
            )
            if not unchanged:
                chunks = chunk_markdown(
                    path=md_path,
                    text=text,
                    root=self._docs_dir,
                    source_hash=source_hash,
                )
                source_status = KnowledgeSourceStatus(
                    source=source,
                    path=str(md_path),
                    status="indexed" if chunks else "skipped",
                    chunk_count=len(chunks),
                    source_hash=source_hash,
                    skipped_reason="" if chunks else "empty_or_no_sections",
                )
                if self._index_store is not None:
                    self._index_store.replace_source(source_status, chunks)

            for chunk in chunks:
                next_chunks[chunk.chunk_id] = chunk
            if source_status is not None:
                next_sources[source] = source_status

        if self._index_store is not None:
            removed_sources = set(persisted_hashes) - seen_sources
            self._index_store.delete_sources(removed_sources)

        self._chunks = next_chunks
        self._sources = next_sources
        self._retriever.rebuild(self._chunks)
        self._loaded = True
        return len(self._chunks)

    def close(self) -> None:
        if self._index_store is not None:
            self._index_store.close()

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def search_hits(self, query: str, top_k: int = 3) -> list[KnowledgeHit]:
        """Return structured hits for a natural-language query."""
        if not self._loaded and self._auto_load:
            self.reindex()
        query = (query or "").strip()
        if not query:
            return []

        hits: list[KnowledgeHit] = []
        for chunk_id, score in self._retriever.score(query)[:top_k]:
            chunk = self._chunks.get(chunk_id)
            if chunk is None:
                continue
            hits.append(KnowledgeHit(
                chunk_id=chunk.chunk_id,
                content=chunk.content,
                source=chunk.source,
                title=chunk.title,
                score=round(float(score), 6),
                metadata={
                    **chunk.metadata,
                    "source_hash": chunk.source_hash,
                    "source_path": chunk.source_path,
                    "retriever": "bm25_ngram",
                },
            ))
        return hits

    def search(self, query: str, top_k: int = 3) -> list[KnowledgeHit]:
        """Structured search alias for new callers."""
        return self.search_hits(query, top_k=top_k)

    def retrieve(self, query: str, top_k: int = 3) -> list[str]:
        """Backward-compatible API returning chunk contents only."""
        return [hit.content for hit in self.search_hits(query, top_k=top_k)]

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def docs_dir(self) -> str:
        return str(self._docs_dir)

    def stats(self) -> dict[str, object]:
        indexed = sum(1 for item in self._sources.values() if item.status == "indexed")
        skipped = sum(1 for item in self._sources.values() if item.status != "indexed")
        return {
            "docs_dir": str(self._docs_dir),
            "loaded": self._loaded,
            "chunk_count": len(self._chunks),
            "source_count": len(self._sources),
            "indexed_sources": indexed,
            "skipped_sources": skipped,
            "recursive": self._recursive,
            "include": list(self._include),
            "exclude": list(self._exclude),
            "index_persisted": self._index_store is not None,
            "index_db_path": self._index_store.path if self._index_store is not None else "",
        }

    def sources(self) -> list[KnowledgeSourceStatus]:
        return sorted(self._sources.values(), key=lambda item: item.source)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _iter_markdown_files(self) -> list[Path]:
        candidates = (
            self._docs_dir.rglob("*.md")
            if self._recursive
            else self._docs_dir.glob("*.md")
        )
        return [
            path
            for path in sorted(candidates)
            if path.is_file() and self._is_included(path) and not self._is_excluded(path)
        ]

    def _is_included(self, path: Path) -> bool:
        source = self._source_name(path)
        return any(fnmatch.fnmatch(source, pattern) for pattern in self._include)

    def _is_excluded(self, path: Path) -> bool:
        source = self._source_name(path)
        return any(fnmatch.fnmatch(source, pattern) for pattern in self._exclude)

    def _source_name(self, path: Path) -> str:
        try:
            return str(path.relative_to(self._docs_dir))
        except ValueError:
            return path.name

    def _load_persisted_index(self) -> None:
        if self._index_store is None:
            return
        self._chunks, self._sources = self._index_store.load()
        if self._chunks or self._sources:
            self._retriever.rebuild(self._chunks)
            self._loaded = True

    def _chunks_for_source(self, source: str) -> list[KnowledgeChunk]:
        return [chunk for chunk in self._chunks.values() if chunk.source == source]


# Backward-compatible public name used by existing plugins/tests.
KnowledgeBase = KnowledgeService
