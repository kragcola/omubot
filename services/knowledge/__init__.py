"""Lightweight document knowledge service."""

from services.knowledge.service import KnowledgeBase, KnowledgeService
from services.knowledge.types import KnowledgeChunk, KnowledgeHit, KnowledgeSourceStatus

__all__ = [
    "KnowledgeBase",
    "KnowledgeChunk",
    "KnowledgeHit",
    "KnowledgeService",
    "KnowledgeSourceStatus",
]
