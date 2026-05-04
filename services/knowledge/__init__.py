"""KnowledgeBase: inverted-index retrieval over markdown docs, chunked by ## headings."""

from __future__ import annotations

import re
from pathlib import Path


def _tokenize(text: str) -> set[str]:
    """Extract keywords: character bigrams, CJK singletons, and English words."""
    tokens: set[str] = set()
    # CJK character bigrams
    for i in range(len(text) - 1):
        a, b = text[i], text[i + 1]
        if _is_cjk(a) and _is_cjk(b):
            tokens.add(a + b)
    # Individual CJK characters
    for ch in text:
        if _is_cjk(ch):
            tokens.add(ch)
    # English words (2+ chars, no stopwords)
    for w in re.findall(r"[a-zA-Z]{2,}", text):
        tokens.add(w.lower())
    return tokens


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return (
        (0x4E00 <= cp <= 0x9FFF)
        or (0x3400 <= cp <= 0x4DBF)
        or (0x20000 <= cp <= 0x2A6DF)
        or (0xF900 <= cp <= 0xFAFF)
    )


class KnowledgeBase:
    """Markdown knowledge base with inverted keyword index.

    Scans a directory of .md files, chunks them by ## headings,
    and retrieves relevant chunks via keyword intersection scoring.
    """

    def __init__(self, docs_dir: str = "docs") -> None:
        self._docs_dir = Path(docs_dir)
        self._chunks: dict[str, tuple[str, str]] = {}  # chunk_id → (title, content)
        self._index: dict[str, set[str]] = {}  # keyword → {chunk_ids}
        self._loaded = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def reload(self) -> int:
        """Rescan docs_dir and rebuild index. Returns chunk count."""
        self._chunks.clear()
        self._index.clear()

        if not self._docs_dir.is_dir():
            self._loaded = True
            return 0

        for md_path in sorted(self._docs_dir.glob("*.md")):
            try:
                text = md_path.read_text(encoding="utf-8")
            except Exception:
                continue
            self._index_file(md_path.name, text)

        self._loaded = True
        return len(self._chunks)

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def retrieve(self, query: str, top_k: int = 3) -> list[str]:
        """Return top_k chunk contents matching the query by keyword score."""
        if not self._loaded:
            self.reload()

        if not self._chunks:
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        candidates: dict[str, int] = {}  # chunk_id → match count
        for token in query_tokens:
            chunk_ids = self._index.get(token, set())
            for cid in chunk_ids:
                candidates[cid] = candidates.get(cid, 0) + 1

        if not candidates:
            return []

        ranked = sorted(candidates.items(), key=lambda x: x[1], reverse=True)
        results: list[str] = []
        for cid, _ in ranked[:top_k]:
            _title, content = self._chunks[cid]
            results.append(content)
        return results

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    @property
    def loaded(self) -> bool:
        return self._loaded

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _index_file(self, filename: str, text: str) -> None:
        """Split a markdown file by ## headings and index each chunk."""
        # Extract level-1 title from the first # heading
        doc_title = filename
        m = re.search(r"^# (.+)$", text, re.MULTILINE)
        if m:
            doc_title = m.group(1).strip()

        # Split by ## headings (level 2+)
        sections = re.split(r"^## (.+)$", text, flags=re.MULTILINE)
        # sections[0] = content before first ## heading
        # sections[1] = first heading title
        # sections[2] = first heading body
        # ...

        # If there's preamble content before any ##, index it under the doc title
        preamble = sections[0].strip()
        if preamble and not preamble.startswith("# "):
            # Skip if it's just the level-1 title line
            lines = preamble.split("\n")
            meaningful = [ln for ln in lines if ln.strip() and not ln.strip().startswith("# ")]
            if meaningful:
                cid = f"{filename}::preamble"
                self._chunks[cid] = (doc_title, "\n".join(meaningful))
                self._add_to_index(cid, "\n".join(meaningful))

        for i in range(1, len(sections), 2):
            heading = sections[i].strip()
            body = sections[i + 1].strip() if i + 1 < len(sections) else ""
            if not body:
                continue
            cid = f"{filename}::{heading}"
            full = f"## {heading}\n{body}"
            self._chunks[cid] = (f"{doc_title} › {heading}", full)
            self._add_to_index(cid, full)

    def _add_to_index(self, chunk_id: str, text: str) -> None:
        for token in _tokenize(text):
            self._index.setdefault(token, set()).add(chunk_id)
