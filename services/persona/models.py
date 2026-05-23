"""Data models for the Persona Source Importer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

IssueLevel = Literal["error", "warn", "info"]


@dataclass(frozen=True)
class SourceSpan:
    file: str
    lines: tuple[int, int]

    def to_dict(self) -> dict[str, Any]:
        return {"file": self.file, "lines": [self.lines[0], self.lines[1]]}


@dataclass(frozen=True)
class SourceField:
    key: str
    value: Any
    span: SourceSpan


@dataclass(frozen=True)
class SourceSection:
    title: str
    level: int
    line: int
    body: str
    body_start_line: int
    end_line: int

    @property
    def normalized_title(self) -> str:
        return self.title.strip().lower()


@dataclass
class SourceDocument:
    path: str
    text: str
    frontmatter: dict[str, Any]
    body: str
    body_start_line: int
    sections: list[SourceSection]
    source_hash: str

    def section(self, *needles: str) -> SourceSection | None:
        lowered = [needle.lower() for needle in needles if needle]
        for section in self.sections:
            title = section.normalized_title
            if any(needle in title for needle in lowered):
                return section
        return None


@dataclass(frozen=True)
class ReportField:
    file: str
    key_path: str
    source_span: SourceSpan | None
    confidence: float
    extractor: str
    default_used: bool = False
    issue_level: IssueLevel = "info"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "file": self.file,
            "key_path": self.key_path,
            "confidence": self.confidence,
            "extractor": self.extractor,
            "default_used": self.default_used,
            "issue_level": self.issue_level,
        }
        if self.source_span is not None:
            payload["source_span"] = self.source_span.to_dict()
        return payload


@dataclass(frozen=True)
class ImportIssue:
    level: IssueLevel
    code: str
    message: str
    file: str | None = None
    key_path: str | None = None
    source_span: SourceSpan | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "level": self.level,
            "code": self.code,
            "message": self.message,
        }
        if self.file:
            payload["file"] = self.file
        if self.key_path:
            payload["key_path"] = self.key_path
        if self.source_span is not None:
            payload["source_span"] = self.source_span.to_dict()
        return payload


@dataclass
class ImportReport:
    persona_id: str
    source_file: str
    source_hash: str
    fields: list[ReportField] = field(default_factory=list)
    issues: list[ImportIssue] = field(default_factory=list)
    generated_files: list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(issue.level == "error" for issue in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "omubot.persona_import_report.v1",
            "persona_id": self.persona_id,
            "source_file": self.source_file,
            "source_hash": self.source_hash,
            "fields": [field.to_dict() for field in self.fields],
            "issues": [issue.to_dict() for issue in self.issues],
            "generated_files": list(self.generated_files),
            "status": "error" if self.has_errors else "ok",
        }


@dataclass
class ImportResult:
    persona_id: str
    draft: dict[str, dict[str, Any]]
    modules_readme: str
    report: ImportReport
