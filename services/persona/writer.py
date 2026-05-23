"""Filesystem writer for Persona v2 drafts and Pending Freeze."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import yaml

from .builder import DEFAULT_TEMPLATE_FILES, DRAFT_YAML_FILES, build_persona_draft
from .models import ImportResult
from .parser import parse_source_file


def persona_namespace(persona_id: str) -> str:
    raw = str(persona_id or "").strip()
    if not raw:
        raw = "unknown"
    return raw if raw.endswith("-v2") else f"{raw}-v2"


class PersonaDraftWriter:
    """Write importer output under ``config/persona/<id>-v2/.draft``."""

    def __init__(
        self,
        *,
        persona_root: str | Path = "config/persona",
        defaults_dir: str | Path = "config/persona/_defaults/v2",
    ) -> None:
        self.persona_root = Path(persona_root)
        self.defaults_dir = Path(defaults_dir)

    def import_source(
        self,
        persona_id: str,
        *,
        source_path: str | Path | None = None,
        strict: bool = False,
        write: bool = True,
    ) -> ImportResult:
        namespace = persona_namespace(persona_id)
        source = Path(source_path) if source_path is not None else self.persona_root / namespace / "source.md"
        document = parse_source_file(source)
        defaults = self.load_defaults()
        result = build_persona_draft(document, defaults=defaults)
        result.persona_id = persona_namespace(result.persona_id if result.persona_id != "unknown-v2" else persona_id)
        result.report.persona_id = result.persona_id
        if strict and result.report.has_errors:
            return result
        if write:
            self.write_draft(result)
        return result

    def load_defaults(self) -> dict[str, dict[str, Any]]:
        defaults: dict[str, dict[str, Any]] = {}
        for filename in DEFAULT_TEMPLATE_FILES:
            path = self.defaults_dir / filename
            if not path.is_file():
                continue
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            defaults[filename] = data if isinstance(data, dict) else {}
        return defaults

    def draft_dir(self, persona_id: str) -> Path:
        return self.persona_root / persona_namespace(persona_id) / ".draft"

    def pending_freeze_dir(self, persona_id: str) -> Path:
        return self.persona_root / persona_namespace(persona_id) / "_pending_freeze"

    def source_path(self, persona_id: str) -> Path:
        return self.persona_root / persona_namespace(persona_id) / "source.md"

    def write_draft(self, result: ImportResult) -> Path:
        draft_dir = self.draft_dir(result.persona_id)
        if draft_dir.exists():
            shutil.rmtree(draft_dir)
        (draft_dir / "modules").mkdir(parents=True, exist_ok=True)

        for filename in DRAFT_YAML_FILES:
            payload = result.draft.get(filename, {})
            (draft_dir / filename).write_text(
                yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
        (draft_dir / "modules" / "_README.md").write_text(result.modules_readme, encoding="utf-8")
        (draft_dir / "_import_report.json").write_text(
            json.dumps(result.report.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return draft_dir

    def read_draft_summary(self, persona_id: str) -> dict[str, Any]:
        draft_dir = self.draft_dir(persona_id)
        report_path = draft_dir / "_import_report.json"
        if not report_path.is_file():
            return {
                "ok": False,
                "persona_id": persona_namespace(persona_id),
                "error": "draft not found",
            }
        report = json.loads(report_path.read_text(encoding="utf-8"))
        files = sorted(
            str(path.relative_to(draft_dir))
            for path in draft_dir.rglob("*")
            if path.is_file()
        )
        return {
            "ok": True,
            "persona_id": persona_namespace(persona_id),
            "draft_dir": str(draft_dir),
            "files": files,
            "report": report,
        }

    def pending_freeze(self, persona_id: str) -> dict[str, Any]:
        namespace = persona_namespace(persona_id)
        draft_dir = self.draft_dir(namespace)
        if not draft_dir.is_dir():
            return {"ok": False, "persona_id": namespace, "error": "draft not found"}
        report_path = draft_dir / "_import_report.json"
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
        if report.get("status") == "error":
            return {
                "ok": False,
                "persona_id": namespace,
                "error": "draft has errors; re-import with a complete source before Pending Freeze",
                "report": report,
            }

        target = self.pending_freeze_dir(namespace)
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(draft_dir, target)

        source = self.source_path(namespace)
        if source.is_file():
            shutil.copy2(source, target / "source.frozen.md")
        return {
            "ok": True,
            "mode": "pending_freeze",
            "persona_id": namespace,
            "path": str(target),
            "report": report,
        }
