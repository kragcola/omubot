"""Lightweight similarity providers.

v3 keeps semantic features optional. The default ngram provider is dependency
free and deterministic; heavier embedding backends can be plugged in later
without changing slang or memory call sites.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from services.learning_normalizer.normalize import normalize_key

SimilarityBackend = Literal["ngram", "embedding"]


def normalize_text_key(value: str) -> str:
    """Normalize Chinese/English mixed text for fuzzy matching."""
    return normalize_key(value, "general")


class SimilarityProvider(ABC):
    """Small abstraction for term/meaning similarity."""

    backend: SimilarityBackend

    @abstractmethod
    def similarity(self, left: str, right: str) -> float:
        ...


class NgramSimilarityProvider(SimilarityProvider):
    backend: SimilarityBackend = "ngram"

    def similarity(self, left: str, right: str) -> float:
        left_key = normalize_text_key(left)
        right_key = normalize_text_key(right)
        if not left_key or not right_key:
            return 0.0
        if left_key == right_key:
            return 1.0
        if left_key in right_key or right_key in left_key:
            return 0.82
        n = 2 if len(left_key) > 2 and len(right_key) > 2 else 1
        left_grams = {left_key[i:i + n] for i in range(max(1, len(left_key) - n + 1))}
        right_grams = {right_key[i:i + n] for i in range(max(1, len(right_key) - n + 1))}
        if not left_grams or not right_grams:
            return 0.0
        return len(left_grams & right_grams) / len(left_grams | right_grams)


class EmbeddingSimilarityProvider(SimilarityProvider):
    backend: SimilarityBackend = "embedding"

    def similarity(self, left: str, right: str) -> float:
        raise RuntimeError("embedding similarity backend is not installed/enabled")


def create_similarity_provider(backend: SimilarityBackend = "ngram") -> SimilarityProvider:
    if backend == "embedding":
        return EmbeddingSimilarityProvider()
    return NgramSimilarityProvider()
