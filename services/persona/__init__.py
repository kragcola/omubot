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
from .parity_audit import (
    GroupOverrideSnapshot,
    ParityFinding,
    ParityReport,
    compare_v1_vs_v2_dry_run,
)
from .parser import parse_source_markdown
from .writer import PersonaDraftWriter

__all__ = [
    "CompilePromptBlock",
    "CompileResult",
    "GroupOverrideSnapshot",
    "ImportIssue",
    "ImportReport",
    "ImportResult",
    "ParityFinding",
    "ParityReport",
    "PersonaDraftWriter",
    "PersonaLLMExtractor",
    "SourceDocument",
    "build_persona_draft",
    "compare_v1_vs_v2_dry_run",
    "compile_persona_dry_run",
    "compile_persona_runtime",
    "filter_items_with_source_span",
    "parse_source_markdown",
]
