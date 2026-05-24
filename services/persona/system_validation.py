"""Bridge Persona draft system.yaml to SystemModule dry-run validation."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from services.system_module import ModuleContract, catalog_contracts, validate_module_graph

from .models import ImportIssue, ImportReport, ReportField


def validate_system_modules(draft: dict[str, dict[str, Any]], report: ImportReport) -> None:
    system = draft.get("system.yaml") or {}
    modules = system.get("modules") if isinstance(system, dict) else {}
    if not isinstance(modules, dict):
        report.issues.append(
            ImportIssue("error", "missing_system_modules", "system.yaml.modules 缺失", "system.yaml", "modules")
        )
        return

    contracts = _merge_catalog_with_system_modules(modules)
    result = validate_module_graph(contracts)
    report.fields.append(
        ReportField(
            file="system.yaml",
            key_path="_system_module_validation",
            source_span=None,
            confidence=1.0,
            extractor="system_module_validator",
            default_used=False,
            issue_level="error" if not result.ok else "info",
        )
    )
    for issue in result.issues:
        report.issues.append(
            ImportIssue(
                issue.level,
                issue.code,
                issue.message,
                "system.yaml",
                _issue_key_path(issue.module_id, issue.slot_id),
            )
        )


def _merge_catalog_with_system_modules(modules: dict[str, Any]) -> tuple[ModuleContract, ...]:
    merged: list[ModuleContract] = []
    for contract in catalog_contracts():
        override = modules.get(contract.id)
        if not isinstance(override, dict):
            merged.append(contract)
            continue
        merged.append(
            replace(
                contract,
                enabled=bool(override.get("enabled", contract.enabled)),
                required=bool(override.get("required", contract.required)),
                reserved=bool(override.get("reserved", contract.reserved)),
            )
        )
    return tuple(merged)


def _issue_key_path(module_id: str, slot_id: str) -> str:
    if module_id:
        return f"modules.{module_id}"
    if slot_id:
        return f"state_slots.{slot_id}"
    return "_system_module_validation"
