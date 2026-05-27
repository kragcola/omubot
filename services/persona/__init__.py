"""Persona v2 source importer.

Part A turns a versioned ``source.md`` into a draft-only Persona v2
directory. It deliberately does not write the runtime persona path.
"""

from .builder import build_persona_draft
from .compiler import (
    CompilePromptBlock,
    CompileResult,
    compile_persona_dry_run,
    compile_persona_runtime,
)
from .llm_extractor import PersonaLLMExtractor, filter_items_with_source_span
from .models import ImportIssue, ImportReport, ImportResult, SourceDocument
from .parser import parse_source_markdown
from .runtime import (
    IdentitySnapshot,
    PersonaRuntime,
    PersonaRuntimeBundle,
    hot_reload,
    join_static_blocks,
    load_pending_freeze,
)
from .writer import PersonaDraftWriter

__all__ = [
    "CompilePromptBlock",
    "CompileResult",
    "IdentitySnapshot",
    "ImportIssue",
    "ImportReport",
    "ImportResult",
    "PersonaDraftWriter",
    "PersonaLLMExtractor",
    "PersonaRuntime",
    "PersonaRuntimeBundle",
    "SourceDocument",
    "build_persona_draft",
    "compile_persona_dry_run",
    "compile_persona_runtime",
    "filter_items_with_source_span",
    "hot_reload",
    "join_static_blocks",
    "load_pending_freeze",
    "parse_source_markdown",
]
