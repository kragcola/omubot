"""SystemModule / RuntimeStateBus dry-run contracts for Persona v2 Part B."""

from .catalog import (
    MODULE_CATALOG,
    REQUIRED_MODULE_IDS,
    RESERVED_MODULE_IDS,
    catalog_contracts,
    catalog_system_modules,
)
from .models import (
    DisabledBehavior,
    ModuleContract,
    ModuleGraphValidationResult,
    ModuleIssue,
    Scope,
    SourceRef,
    StateSlotDefinition,
    SwitchSurface,
)
from .state_bus import RuntimeStateBus, StateSlotOwnershipError
from .validator import validate_module_graph

__all__ = [
    "MODULE_CATALOG",
    "REQUIRED_MODULE_IDS",
    "RESERVED_MODULE_IDS",
    "DisabledBehavior",
    "ModuleContract",
    "ModuleGraphValidationResult",
    "ModuleIssue",
    "RuntimeStateBus",
    "Scope",
    "SourceRef",
    "StateSlotDefinition",
    "StateSlotOwnershipError",
    "SwitchSurface",
    "catalog_contracts",
    "catalog_system_modules",
    "validate_module_graph",
]
