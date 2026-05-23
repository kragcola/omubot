"""Persona v2 source importer.

Part A turns a versioned ``source.md`` into a draft-only Persona v2
directory. It deliberately does not write the runtime persona path.
"""

from .builder import build_persona_draft
from .llm_extractor import PersonaLLMExtractor, filter_items_with_source_span
from .models import ImportIssue, ImportReport, ImportResult, SourceDocument
from .parser import parse_source_markdown
from .writer import PersonaDraftWriter

__all__ = [
    "ImportIssue",
    "ImportReport",
    "ImportResult",
    "PersonaDraftWriter",
    "PersonaLLMExtractor",
    "SourceDocument",
    "build_persona_draft",
    "filter_items_with_source_span",
    "parse_source_markdown",
]
