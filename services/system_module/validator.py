"""Validation helpers for SystemModule contracts."""

from __future__ import annotations

from collections import defaultdict

from .catalog import REQUIRED_MODULE_IDS
from .models import ModuleContract, ModuleGraphValidationResult, ModuleIssue


def validate_module_graph(contracts: list[ModuleContract] | tuple[ModuleContract, ...]) -> ModuleGraphValidationResult:
    issues: list[ModuleIssue] = []
    modules = {contract.id: contract for contract in contracts}

    for contract in contracts:
        if not _valid_module_id(contract.id):
            issues.append(ModuleIssue("error", "invalid_module_id", "module id must be <group>.<id>", contract.id))
        if contract.id in REQUIRED_MODULE_IDS and not contract.enabled:
            issues.append(
                ModuleIssue("error", "required_module_disabled", "required module cannot be disabled", contract.id)
            )
        if contract.required and not contract.enabled:
            issues.append(
                ModuleIssue("error", "required_module_disabled", "required module cannot be disabled", contract.id)
            )
        if contract.reserved and contract.enabled:
            issues.append(
                ModuleIssue("error", "reserved_module_enabled", "reserved module has no implementation", contract.id)
            )

    slot_owners: dict[str, list[str]] = defaultdict(list)
    for contract in contracts:
        if not contract.enabled:
            continue
        for slot in contract.state_owns:
            if not slot.id:
                issues.append(
                    ModuleIssue("error", "empty_state_slot", "state_owns contains an empty slot id", contract.id)
                )
                continue
            slot_owners[slot.id].append(contract.id)
            if not slot.schema:
                issues.append(
                    ModuleIssue("error", "missing_slot_schema", "state slot schema is required", contract.id, slot.id)
                )

    for slot_id, owners in slot_owners.items():
        if len(owners) > 1:
            issues.append(
                ModuleIssue(
                    "error",
                    "duplicate_state_owner",
                    f"state slot has multiple owners: {', '.join(sorted(owners))}",
                    slot_id=slot_id,
                )
            )

    for contract in contracts:
        if not contract.enabled:
            continue
        for slot_id in contract.state_consumes:
            if slot_id not in slot_owners:
                issues.append(
                    ModuleIssue(
                        "error",
                        "missing_state_owner",
                        "state_consumes references a slot with no enabled owner",
                        contract.id,
                        slot_id,
                    )
                )
        for dep in contract.depends_on:
            dep_contract = modules.get(dep)
            if dep_contract is None:
                issues.append(
                    ModuleIssue("error", "missing_dependency", "depends_on references an unknown module", contract.id)
                )
                continue
            if not dep_contract.enabled and contract.switch_surface.on_disabled.behavior == "fail":
                issues.append(
                    ModuleIssue(
                        "error",
                        "disabled_dependency_without_degrade",
                        "dependency is disabled and downstream module cannot degrade",
                        contract.id,
                    )
                )

    graph = {
        contract.id: tuple(dep for dep in contract.depends_on if modules.get(dep) and modules[dep].enabled)
        for contract in contracts
        if contract.enabled
    }
    cycle = _find_cycle(graph)
    if cycle:
        issues.append(
            ModuleIssue(
                "error",
                "module_dependency_cycle",
                "module dependency graph contains a cycle: " + " -> ".join(cycle),
            )
        )

    order = () if cycle else tuple(_topological_order(graph))
    return ModuleGraphValidationResult(
        ok=not any(issue.level == "error" for issue in issues),
        issues=tuple(issues),
        module_order=order,
    )


def _valid_module_id(module_id: str) -> bool:
    if "." not in module_id:
        return False
    group, name = module_id.split(".", 1)
    return bool(group and name and "/" not in module_id and "\\" not in module_id and ".." not in module_id)


def _find_cycle(graph: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(node: str) -> tuple[str, ...]:
        if node in visiting:
            start = stack.index(node)
            return tuple([*stack[start:], node])
        if node in visited:
            return ()
        visiting.add(node)
        stack.append(node)
        for dep in graph.get(node, ()):
            cycle = visit(dep)
            if cycle:
                return cycle
        stack.pop()
        visiting.remove(node)
        visited.add(node)
        return ()

    for node in graph:
        cycle = visit(node)
        if cycle:
            return cycle
    return ()


def _topological_order(graph: dict[str, tuple[str, ...]]) -> list[str]:
    visited: set[str] = set()
    order: list[str] = []

    def visit(node: str) -> None:
        if node in visited:
            return
        visited.add(node)
        for dep in graph.get(node, ()):
            visit(dep)
        order.append(node)

    for node in graph:
        visit(node)
    return order
