"""Pure Persona compilation and explicit last-known-good activation."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

PERSONA_VERSION_PREFIX = "persona-v1:sha256:"
PERSONA_SOURCE_HASH_PREFIX = "persona-source-v1:sha256:"
DEFAULT_MAX_SYSTEM_CHARS = 6200
_ALLOWED_FRONT_MATTER_KEYS = frozenset({"persona_id", "canonical_name", "version_hint", "language"})
_REQUIRED_SOURCE_SECTIONS = ("1", "3", "4", "7")
_PERSONA_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TOP_LEVEL_SECTION_RE = re.compile(r"^#\s*(\d+)\.\s+.+$")
_FRONT_MATTER_FIELD_RE = re.compile(r"^([a-z][a-z0-9_]*)\s*:\s*(.*)$")

PersonaStatus = Literal["default", "configured"]
PersonaRole = Literal["identity", "knowledge_boundary", "expression", "examples"]
_SECTION_SPECS: dict[str, tuple[PersonaRole, int]] = {
    "1": ("identity", 100),
    "4": ("knowledge_boundary", 90),
    "3": ("expression", 60),
    "7": ("examples", 50),
}


class PersonaCompileError(ValueError):
    """The source cannot produce a bounded effective Persona snapshot."""


class PersonaInitialLoadError(PersonaCompileError):
    """The required initial Persona could not be compiled."""


@dataclass(frozen=True)
class PersonaImportIssue:
    """A safe source import diagnostic that identifies a line, never its contents."""

    code: str
    line: int
    message: str


class PersonaSourceImportError(ValueError):
    """A source import result could not provide a valid Persona source."""

    def __init__(self, issue: PersonaImportIssue) -> None:
        self.issue = issue
        super().__init__(f"{issue.code} at line {issue.line}: {issue.message}")


@dataclass(frozen=True)
class PersonaSourceSection:
    """A required source section preserved with an explicit role and priority."""

    number: str
    role: PersonaRole
    priority: int
    content: str
    line: int


@dataclass(frozen=True)
class PersonaSource:
    """The small source DTO needed by the compiler.

    ``source_ref`` and ``source_revision`` are provenance only. They are kept
    beside the compiled output so a caller can bind a turn to the source that
    produced it, while the effective version remains a hash of the actual
    system text.
    """

    persona_id: str = ""
    name: str = ""
    instructions: str = ""
    source_ref: str = "config.persona"
    source_revision: str = ""
    required: bool = True
    source_hash: str = ""
    sections: tuple[PersonaSourceSection, ...] = ()


@dataclass(frozen=True)
class PersonaBlock:
    """A compiled block whose source and priority remain inspectable."""

    number: str
    role: PersonaRole
    priority: int
    source_ref: str
    content: str


@dataclass(frozen=True)
class CompiledPersona:
    """An immutable, model-ready Persona snapshot."""

    persona_id: str
    system: str
    version: str
    source_ref: str
    source_revision: str
    status: PersonaStatus
    source_hash: str = ""
    blocks: tuple[PersonaBlock, ...] = ()
    canonical_name: str | None = None

    def as_turn_state_system(self) -> str:
        """Return only the system text for ``TurnStateSnapshot.from_persona_system``.

        ``TurnStateSnapshot.from_persona_system`` computes its own Persona
        version and treats its second positional argument as the unrelated
        configuration revision. Returning a tuple here would make it easy to
        accidentally write the Persona version into that configuration field.
        """

        return self.system


class PersonaReloadStatus(StrEnum):
    ACTIVATED = "activated"
    REJECTED = "rejected"


@dataclass(frozen=True)
class PersonaReloadResult:
    """Outcome of an explicit compile-and-swap attempt."""

    status: PersonaReloadStatus
    effective: CompiledPersona | None
    attempted_version: str | None
    error: str | None = None


class PersonaCompiler:
    """Compile a source DTO without mutating runtime state."""

    def __init__(self, *, max_system_chars: int = DEFAULT_MAX_SYSTEM_CHARS) -> None:
        if max_system_chars < 1:
            raise ValueError("max_system_chars must be positive")
        self._max_system_chars = max_system_chars

    def compile(self, source: PersonaSource) -> CompiledPersona:
        name = source.name.strip()
        instructions = source.instructions.strip()
        if source.sections:
            blocks = self._compile_sections(source, name)
            system = "\n\n".join(
                f"【Persona block | source={block.source_ref} | role={block.role} | "
                f"priority={block.priority}】\n{block.content}"
                for block in blocks
            )
        else:
            blocks = ()
            system = "\n".join(
                part for part in (f"你的名称：{name}" if name else "", instructions) if part
            )

        if source.required and not system:
            raise PersonaCompileError("required persona source is empty")
        if len(system) > self._max_system_chars:
            raise PersonaCompileError("system prompt exceeds character budget")

        version = PERSONA_VERSION_PREFIX + hashlib.sha256(system.encode("utf-8")).hexdigest()
        status: PersonaStatus = "configured" if system else "default"
        return CompiledPersona(
            persona_id=source.persona_id,
            system=system,
            version=version,
            source_ref=source.source_ref,
            source_revision=source.source_revision,
            status=status,
            source_hash=source.source_hash,
            blocks=blocks,
            canonical_name=name if source.sections else None,
        )

    def _compile_sections(
        self, source: PersonaSource, name: str
    ) -> tuple[PersonaBlock, ...]:
        by_number = {section.number: section for section in source.sections}
        if set(by_number) != set(_SECTION_SPECS) or len(by_number) != len(source.sections):
            raise PersonaCompileError("persona source sections are incomplete or duplicated")
        blocks: list[PersonaBlock] = []
        for number, (role, priority) in _SECTION_SPECS.items():
            section = by_number[number]
            content = section.content.strip()
            if not content:
                raise PersonaCompileError(f"persona section {number} is empty")
            if number == "1" and name:
                content = f"你的名称：{name}\n{content}"
            blocks.append(
                PersonaBlock(
                    number=number,
                    role=role,
                    priority=priority,
                    source_ref=f"{source.source_ref}#section-{number}",
                    content=content,
                )
            )
        return tuple(blocks)


@dataclass(frozen=True)
class PersonaImportResult:
    """Pure import output; it never changes the active runtime Persona."""

    source: PersonaSource | None
    source_hash: str
    issues: tuple[PersonaImportIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return self.source is not None and not self.issues

    def require_source(self) -> PersonaSource:
        if self.source is not None and not self.issues:
            return self.source
        issue = self.issues[0] if self.issues else PersonaImportIssue(
            code="source_invalid", line=1, message="source did not produce a Persona"
        )
        raise PersonaSourceImportError(issue)


def _unquote_scalar(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _parse_front_matter_scalar(value: str) -> tuple[str, bool]:
    raw = value.strip()
    if not raw:
        return "", False
    if raw[0] in {'"', "'"}:
        if len(raw) < 2 or raw[-1] != raw[0]:
            return "", False
        return _unquote_scalar(raw), True
    if raw[0] in "{}[]|>&*!@`" or "\t" in raw:
        return "", False
    return raw, True


def _source_sections(
    lines: list[str], start_index: int
) -> tuple[dict[str, tuple[int, int]], tuple[tuple[str, int], ...]]:
    headings: list[tuple[str, int]] = []
    for index in range(start_index, len(lines)):
        match = _TOP_LEVEL_SECTION_RE.fullmatch(lines[index].strip())
        if match is not None:
            headings.append((match.group(1), index))
    sections: dict[str, tuple[int, int]] = {}
    duplicates: list[tuple[str, int]] = []
    for position, (number, heading_index) in enumerate(headings):
        end_index = headings[position + 1][1] if position + 1 < len(headings) else len(lines)
        if number in sections:
            duplicates.append((number, heading_index + 1))
        else:
            sections[number] = (heading_index + 1, end_index)
    return sections, tuple(duplicates)


def _section_body(lines: list[str], bounds: tuple[int, int] | None) -> str:
    if bounds is None:
        return ""
    start_index, end_index = bounds
    return "\n".join(lines[start_index:end_index]).strip()


class PersonaSourceImporter:
    """Import the deliberately small, source.md-shaped Persona contract.

    This parser accepts scalar front matter only and validates the four
    required top-level sections from the legacy source contract. It does not
    read paths, execute YAML, call a model, or activate a runtime Persona.
    """

    def import_markdown(
        self, text: str, *, source_ref: str = "source.md", required: bool = True
    ) -> PersonaImportResult:
        source_hash = PERSONA_SOURCE_HASH_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()
        lines = text.splitlines()
        issues: list[PersonaImportIssue] = []
        if not text.strip():
            return PersonaImportResult(
                source=None,
                source_hash=source_hash,
                issues=(PersonaImportIssue("empty_source", 1, "source is empty"),),
            )
        if not lines or lines[0].strip() != "---":
            return PersonaImportResult(
                source=None,
                source_hash=source_hash,
                issues=(PersonaImportIssue("missing_front_matter", 1, "source must start with ---"),),
            )

        closing_index = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
        if closing_index is None:
            return PersonaImportResult(
                source=None,
                source_hash=source_hash,
                issues=(
                    PersonaImportIssue(
                        "unterminated_front_matter", 1, "front matter has no closing ---"
                    ),
                ),
            )

        metadata: dict[str, str] = {}
        metadata_lines: dict[str, int] = {}
        for index, raw_line in enumerate(lines[1:closing_index], start=2):
            if not raw_line.strip():
                continue
            match = _FRONT_MATTER_FIELD_RE.fullmatch(raw_line.strip())
            if match is None:
                issues.append(
                    PersonaImportIssue(
                        "malformed_front_matter", index, "front matter must use key: value"
                    )
                )
                continue
            key, raw_value = match.groups()
            if key not in _ALLOWED_FRONT_MATTER_KEYS:
                issues.append(
                    PersonaImportIssue(
                        "unknown_front_matter", index, "front matter key is not allowed"
                    )
                )
                continue
            if key in metadata:
                issues.append(
                    PersonaImportIssue(
                        "duplicate_front_matter", index, "front matter key is duplicated"
                    )
                )
                continue
            parsed_value, is_scalar = _parse_front_matter_scalar(raw_value)
            if not is_scalar:
                issues.append(
                    PersonaImportIssue(
                        "invalid_front_matter_scalar", index, "front matter value must be a scalar"
                    )
                )
                continue
            metadata[key] = parsed_value
            metadata_lines[key] = index

        for key in ("persona_id", "canonical_name"):
            if not metadata.get(key, "").strip():
                issues.append(
                    PersonaImportIssue(
                        "missing_front_matter", closing_index + 1, f"front matter requires {key}"
                    )
                )
        persona_id = metadata.get("persona_id", "").strip()
        if persona_id and _PERSONA_ID_RE.fullmatch(persona_id) is None:
            issues.append(
                PersonaImportIssue(
                    "invalid_persona_id",
                    metadata_lines.get("persona_id", 1),
                    "persona_id must be a short exact identifier",
                )
            )
        canonical_name = metadata.get("canonical_name", "").strip()
        if len(canonical_name) > 80:
            issues.append(
                PersonaImportIssue(
                    "canonical_name_too_long",
                    metadata_lines.get("canonical_name", 1),
                    "canonical_name exceeds 80 characters",
                )
            )

        sections, duplicates = _source_sections(lines, closing_index + 1)
        for section_number, line in duplicates:
            issues.append(
                PersonaImportIssue(
                    "duplicate_required_section"
                    if section_number in _SECTION_SPECS
                    else "duplicate_top_level_section",
                    line,
                    "top-level section is duplicated",
                )
            )
        for section_number, bounds in sections.items():
            if section_number not in _SECTION_SPECS:
                issues.append(
                    PersonaImportIssue(
                        "unsupported_top_level_section",
                        bounds[0],
                        "top-level section is outside this compiler contract",
                    )
                )
        source_sections: list[PersonaSourceSection] = []
        for section_number in _REQUIRED_SOURCE_SECTIONS:
            if section_number not in sections:
                issues.append(
                    PersonaImportIssue(
                        "missing_required_section",
                        closing_index + 1,
                        f"source requires section {section_number}",
                    )
                )
                continue
            role, priority = _SECTION_SPECS[section_number]
            bounds = sections[section_number]
            content = _section_body(lines, bounds)
            if not content:
                issues.append(
                    PersonaImportIssue(
                        "empty_required_section", bounds[0] + 1, "required source section is empty"
                    )
                )
                continue
            source_sections.append(
                PersonaSourceSection(
                    number=section_number,
                    role=role,
                    priority=priority,
                    content=content,
                    line=bounds[0],
                )
            )

        instructions = _section_body(lines, sections.get("3"))

        if issues:
            return PersonaImportResult(source=None, source_hash=source_hash, issues=tuple(issues))

        source_revision = metadata.get("version_hint", "").strip() or source_hash
        return PersonaImportResult(
            source=PersonaSource(
                persona_id=persona_id,
                name=canonical_name,
                instructions=instructions,
                source_ref=source_ref,
                source_revision=source_revision,
                required=required,
                source_hash=source_hash,
                sections=tuple(source_sections),
            ),
            source_hash=source_hash,
        )


class PersonaRuntime:
    """Own the active Persona and protect its last-known-good snapshot."""

    def __init__(self, *, compiler: PersonaCompiler | None = None) -> None:
        self._compiler = compiler or PersonaCompiler()
        self._active: CompiledPersona | None = None

    @property
    def current(self) -> CompiledPersona | None:
        return self._active

    def activate(self, compiled: CompiledPersona) -> CompiledPersona:
        """Make a previously compiled snapshot effective."""

        self._active = compiled
        return compiled

    def reload(self, source: PersonaSource) -> PersonaReloadResult:
        """Compile and explicitly swap, retaining the last valid snapshot on error."""

        try:
            compiled = self._compiler.compile(source)
        except PersonaCompileError as exc:
            if self._active is None:
                raise PersonaInitialLoadError(str(exc)) from exc
            return PersonaReloadResult(
                status=PersonaReloadStatus.REJECTED,
                effective=self._active,
                attempted_version=None,
                error=str(exc),
            )

        self._active = compiled
        return PersonaReloadResult(
            status=PersonaReloadStatus.ACTIVATED,
            effective=compiled,
            attempted_version=compiled.version,
        )
