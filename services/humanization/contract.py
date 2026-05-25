"""ModuleContract for the Part 1 humanization runtime owner."""

from __future__ import annotations

from services.system_module import ModuleContract, StateSlotDefinition

HUMANIZATION_MODULE_ID = "humanization.runtime"

REGISTER_LABEL_SLOT = "state.register.label"
REGISTER_RECENT_USED_SLOT = "state.register.recent_used"
STICKER_RECENT_USED_SLOT = "state.sticker.recent_used"
AFFECTION_FAMILIARITY_SLOT = "state.affection.<uid>.familiarity"
MOOD_CURRENT_SLOT = "humanization.mood.current"
THINKER_LAST_DECISION_SLOT = "bus.state.thinker.last_decision"
CLOCK_CURRENT_SLOT = "bus.state.clock.current"
LAST_METRICS_SLOT = "humanization.last_metrics"


def _slot(slot_id: str, schema: str, *, ttl: str, privacy: str = "group") -> StateSlotDefinition:
    return StateSlotDefinition.from_dict({
        "id": slot_id,
        "schema": schema,
        "ttl": ttl,
        "privacy": privacy,
    })


HUMANIZATION_CONTRACT = ModuleContract(
    id=HUMANIZATION_MODULE_ID,
    group="runtime",
    version="1.0.0",
    enabled=True,
    required=False,
    state_owns=(
        _slot(REGISTER_LABEL_SLOT, "omubot.state.humanization_register_label.v1", ttl="per_session"),
        _slot(REGISTER_RECENT_USED_SLOT, "omubot.state.humanization_register_recent_used.v1", ttl="per_session"),
        _slot(STICKER_RECENT_USED_SLOT, "omubot.state.humanization_sticker_recent_used.v1", ttl="per_session"),
        _slot(AFFECTION_FAMILIARITY_SLOT, "omubot.state.humanization_affection_familiarity.v1", ttl="per_user"),
        _slot(MOOD_CURRENT_SLOT, "omubot.state.humanization_mood_current.v1", ttl="per_session"),
        _slot(THINKER_LAST_DECISION_SLOT, "omubot.state.humanization_thinker_decision.v1", ttl="per_turn"),
        _slot(CLOCK_CURRENT_SLOT, "omubot.state.humanization_clock_current.v1", ttl="per_turn"),
        _slot(LAST_METRICS_SLOT, "omubot.state.humanization_metrics.v1", ttl="per_turn", privacy="admin_only"),
    ),
)
