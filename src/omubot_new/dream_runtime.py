"""One administrator-triggered Dream proposal through the existing model exit.

This runner has no clock or background task.  It only creates a pending fiction
proposal; reviewing and committing remain separate administrator operations.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections.abc import Sequence

from .actions import Actions
from .config import Config
from .dream_model import DreamModelInput, DreamModelPlanner
from .policy import Policy
from .store import StoreConnection
from .story import DreamProposalRecord, StoryArcStore
from .types import ActionCall, ModelPort, ModelRequest, OperationError, Scope


class ManualDreamRunner:
    """Bind an explicit admin request to an authorized model action and Arc."""

    def __init__(
        self,
        story: StoryArcStore,
        actions: Actions,
        policy: Policy,
        model: ModelPort,
        *,
        model_config: Config,
        allowed_groups: Sequence[str],
        source_fingerprint: str,
    ) -> None:
        if story.store is not actions.store or actions.store is not policy.store:
            raise OperationError("dream_runtime_boundary_mismatch")
        if not source_fingerprint or len(source_fingerprint) > 128:
            raise OperationError("invalid_dream_source")
        if model_config.bot_id != policy.bot_id:
            raise OperationError("dream_runtime_boundary_mismatch")
        self.story = story
        self.actions = actions
        self.policy = policy
        self.model = model
        self.model_name = model_config.model
        self.provider = model_config.policy_provider
        self.timeout = model_config.model_timeout
        self.allowed_groups = frozenset(allowed_groups)
        self.source_fingerprint = source_fingerprint

    async def propose(self, scope: Scope, arc_id: str) -> DreamProposalRecord:
        if scope.bot_id != self.policy.bot_id or scope.group_id not in self.allowed_groups:
            raise OperationError("dream_scope_denied")
        arc = await self.story.read_arc(scope, arc_id)
        if arc is None:
            raise OperationError("dream_arc_missing")
        if arc.status != "active":
            raise OperationError("dream_arc_closed")
        await self.policy.check(
            "system:dream", scope, "model.dream", provider=self.provider, model=self.model_name
        )
        model_input = DreamModelInput(
            scope=scope,
            arc=arc,
            created_at=time.time(),
            source_fingerprint=self.source_fingerprint,
        )
        request_id = "dream:" + secrets.token_hex(16)
        action_key = request_id + ":model"

        async def invoke(request: ModelRequest):
            material = request.model_dump(mode="json")
            digest = hashlib.sha256(
                json.dumps(
                    material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest()
            call = ActionCall(
                key=action_key,
                request_id=request_id,
                subject="system:dream",
                scope=scope,
                action="model.dream",
                payload_hash=digest,
                provider=self.provider,
                model=self.model_name,
            )

            def preflight(db: StoreConnection) -> None:
                row = db.execute(
                    "SELECT a.revision,a.status FROM story_arcs a "
                    "JOIN story_arc_groups g ON g.bot_id=a.bot_id AND g.arc_id=a.arc_id "
                    "WHERE a.bot_id=? AND a.arc_id=? AND g.group_id=?",
                    (scope.bot_id, arc.arc_id, scope.group_id),
                ).fetchone()
                if row is None or row["revision"] != arc.revision or row["status"] != "active":
                    raise OperationError("dream_arc_revision_conflict")

            return await self.actions.execute(
                call,
                lambda: self.model.request(request),
                external=self.model.is_external,
                preflight_transaction=preflight,
                timeout=self.timeout,
            )

        proposal = await DreamModelPlanner(invoke, model=self.model_name).propose(model_input)
        return await self.story.submit_dream_proposal(scope, proposal)


__all__ = ["ManualDreamRunner"]
