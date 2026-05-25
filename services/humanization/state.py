"""RuntimeStateBus factory for humanization-owned state slots."""

from __future__ import annotations

from collections.abc import Iterable

from services.humanization.contract import HUMANIZATION_CONTRACT, HUMANIZATION_MODULE_ID
from services.system_module import ModuleContract, RuntimeStateBus, SourceRef


def create_humanization_state_bus(
    extra_contracts: Iterable[ModuleContract] = (),
) -> RuntimeStateBus:
    """Create the production RuntimeStateBus for humanization state."""
    return RuntimeStateBus((HUMANIZATION_CONTRACT, *tuple(extra_contracts)))


def humanization_source(evidence_path: str) -> SourceRef:
    return SourceRef(module_id=HUMANIZATION_MODULE_ID, evidence_path=evidence_path)
