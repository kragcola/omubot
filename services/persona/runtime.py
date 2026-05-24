"""Persona v2 runtime loader (thin wrapper).

Loads ``_pending_freeze/<id>/`` into a :class:`PersonaRuntimeBundle`. This
module is the single entry point for runtime consumers. It is gated by
``BotConfig.persona_v2.runtime_consume``; when that flag is off, callers must
skip loading entirely (no side effects).

B1 scope: dataclass + load function + targeted tests. No PromptBuilder
integration — that is B3.

Runtime consumption protocol (see
``docs/tracking/persona-runtime-cutover-B1-execution.md`` §3):

- ``_pending_freeze/<id>/_persona_runtime.json`` MUST exist with
  ``schema_version`` (MAJOR.MINOR) and ``persona_id``. MAJOR mismatch is the
  only hard fail; MINOR mismatch warns but proceeds.
- ``compile_persona_runtime`` is delegated to for prompt-block compilation.
  Any of its failure modes (yaml parse error, DAG invalid, missing required
  files) bubble up as ``compile_result.ok=False``.
- ``source_sha256`` drift versus ``source.frozen.md`` is logged as a warning
  (the meta is the freeze truth; if the user edited ``source.frozen.md`` by
  hand, runtime will trust the meta and warn). It does NOT block load.

This loader is read-only and never raises. Callers
(``services/persona/runtime.py`` integration point in B3) decide whether to
fall back to v1 based on ``BotConfig.persona_v2.fallback_on_compile_error``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from .compiler import CompileResult, compile_persona_runtime
from .writer import PENDING_FREEZE_SCHEMA_VERSION, PersonaDraftWriter, persona_namespace

_SCHEMA_MAJOR = PENDING_FREEZE_SCHEMA_VERSION.split(".", 1)[0]


@dataclass(frozen=True)
class PersonaRuntimeBundle:
    """Outcome of loading ``_pending_freeze/<id>/``.

    ``ok`` is True only when the meta file is present, the schema MAJOR
    version matches, and ``compile_persona_runtime`` returned ``ok=True``.
    """

    persona_id: str
    schema_version: str
    source_sha256: str
    compile_result: CompileResult
    pending_freeze_dir: Path
    warnings: tuple[str, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.errors and self.compile_result.ok


def load_pending_freeze(
    persona_id: str,
    *,
    persona_root: str | Path = "config/persona",
    defaults_dir: str | Path = "config/persona/_defaults/v2",
) -> PersonaRuntimeBundle | None:
    """Load ``_pending_freeze/<id>/`` into a :class:`PersonaRuntimeBundle`.

    Returns ``None`` when the pending freeze directory does not exist (caller
    should fall back to v1). Returns a bundle with ``ok=False`` on any other
    failure (caller decides fallback based on
    ``BotConfig.persona_v2.fallback_on_compile_error``).

    Never raises. Read-only.
    """
    namespace = persona_namespace(persona_id)
    writer = PersonaDraftWriter(persona_root=persona_root, defaults_dir=defaults_dir)
    pending_dir = writer.pending_freeze_dir(persona_id)
    if not pending_dir.is_dir():
        return None

    warnings: list[str] = []
    errors: list[str] = []

    meta_path = pending_dir / "_persona_runtime.json"
    schema_version = ""
    source_sha256 = ""
    if not meta_path.is_file():
        errors.append("runtime_meta_missing")
    else:
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"runtime_meta_parse_error: {exc}")
            meta = {}
        if isinstance(meta, dict):
            schema_version = str(meta.get("schema_version", "")).strip()
            source_sha256 = str(meta.get("source_sha256", "")).strip()
            if schema_version:
                meta_major = schema_version.split(".", 1)[0]
                if meta_major != _SCHEMA_MAJOR:
                    errors.append(
                        f"schema_version_major_mismatch: expected {_SCHEMA_MAJOR}.x got {schema_version}"
                    )
            else:
                errors.append("schema_version_missing")

    source_frozen = pending_dir / "source.frozen.md"
    if source_frozen.is_file() and source_sha256:
        try:
            actual_sha = hashlib.sha256(source_frozen.read_bytes()).hexdigest()
        except OSError as exc:
            warnings.append(f"source_sha256_read_error: {exc}")
        else:
            if actual_sha != source_sha256:
                warnings.append("source_sha256_drift")

    compile_result = compile_persona_runtime(
        persona_id, persona_root=persona_root, defaults_dir=defaults_dir
    )

    return PersonaRuntimeBundle(
        persona_id=namespace,
        schema_version=schema_version,
        source_sha256=source_sha256,
        compile_result=compile_result,
        pending_freeze_dir=pending_dir,
        warnings=tuple(warnings),
        errors=tuple(errors),
    )
