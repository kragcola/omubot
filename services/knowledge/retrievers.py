"""Dependency-free keyword/BM25-ish retriever for local markdown chunks."""

from __future__ import annotations

import math
import re
from collections import Counter

from services.knowledge.types import KnowledgeChunk

_CJK_STOP_CHARS = frozenset("的了是在和与或及而就都也还很更最把被对给从到于这那哪谁啥么怎什")
_CJK_STOP_TERMS = frozenset({
    "什么",
    "怎么",
    "怎样",
    "如何",
    "一下",
    "这个",
    "那个",
    "哪些",
    "哪里",
    "多少",
    "是否",
    "是不是",
    "有没有",
})
_LATIN_STOP_TERMS = frozenset({
    "the",
    "and",
    "or",
    "for",
    "with",
    "what",
    "how",
    "is",
    "are",
    "a",
    "an",
})


def tokenize(text: str) -> list[str]:
    """Extract mixed Chinese/English tokens for local retrieval."""
    tokens: list[str] = []
    normalized = (text or "").strip()
    if not normalized:
        return tokens
    stop_positions = _stop_positions(normalized)

    for index in range(len(normalized) - 1):
        left, right = normalized[index], normalized[index + 1]
        term = left + right
        if (
            is_cjk(left)
            and is_cjk(right)
            and term not in _CJK_STOP_TERMS
            and not stop_positions[index]
            and not stop_positions[index + 1]
            and left not in _CJK_STOP_CHARS
            and right not in _CJK_STOP_CHARS
        ):
            tokens.append(term)
    for index, char in enumerate(normalized):
        if is_cjk(char) and not stop_positions[index] and char not in _CJK_STOP_CHARS:
            tokens.append(char)
    for word in re.findall(r"[a-zA-Z0-9_]{2,}", normalized):
        term = word.lower()
        if term not in _LATIN_STOP_TERMS:
            tokens.append(term)
    return tokens


def _stop_positions(text: str) -> list[bool]:
    positions = [False] * len(text)
    for term in _CJK_STOP_TERMS:
        start = text.find(term)
        while start >= 0:
            for index in range(start, start + len(term)):
                if 0 <= index < len(positions):
                    positions[index] = True
            start = text.find(term, start + 1)
    return positions


def is_cjk(char: str) -> bool:
    codepoint = ord(char)
    return (
        (0x4E00 <= codepoint <= 0x9FFF)
        or (0x3400 <= codepoint <= 0x4DBF)
        or (0x20000 <= codepoint <= 0x2A6DF)
        or (0xF900 <= codepoint <= 0xFAFF)
    )


class KeywordBM25Retriever:
    """Tiny BM25-style scorer with an inverted index."""

    def __init__(self) -> None:
        self._chunks: dict[str, KnowledgeChunk] = {}
        self._term_freqs: dict[str, Counter[str]] = {}
        self._doc_freqs: Counter[str] = Counter()
        self._doc_lengths: dict[str, int] = {}
        self._avg_doc_len = 0.0

    def rebuild(self, chunks: dict[str, KnowledgeChunk]) -> None:
        self._chunks = dict(chunks)
        self._term_freqs.clear()
        self._doc_freqs.clear()
        self._doc_lengths.clear()

        total_len = 0
        for chunk_id, chunk in self._chunks.items():
            terms = tokenize(f"{chunk.title}\n{chunk.content}")
            freq = Counter(terms)
            self._term_freqs[chunk_id] = freq
            self._doc_lengths[chunk_id] = len(terms)
            total_len += len(terms)
            for term in freq:
                self._doc_freqs[term] += 1

        self._avg_doc_len = total_len / max(len(self._chunks), 1)

    def score(self, query: str) -> list[tuple[str, float]]:
        query_terms = tokenize(query)
        if not query_terms or not self._chunks:
            return []

        scores: dict[str, float] = {}
        unique_terms = set(query_terms)
        total_docs = len(self._chunks)
        k1 = 1.5
        b = 0.75

        for term in unique_terms:
            doc_freq = self._doc_freqs.get(term, 0)
            if doc_freq <= 0:
                continue
            idf = math.log(1 + (total_docs - doc_freq + 0.5) / (doc_freq + 0.5))
            for chunk_id, freqs in self._term_freqs.items():
                tf = freqs.get(term, 0)
                if tf <= 0:
                    continue
                doc_len = self._doc_lengths.get(chunk_id, 0)
                denominator = tf + k1 * (1 - b + b * doc_len / max(self._avg_doc_len, 1.0))
                scores[chunk_id] = scores.get(chunk_id, 0.0) + idf * (tf * (k1 + 1) / denominator)

        return sorted(scores.items(), key=lambda item: item[1], reverse=True)
