"""Explicit one-body document extraction; no autonomous scheduler or new model owner."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from time import monotonic
from typing import Literal

from .actions import Actions
from .config import Config
from .graph import GraphRelation, GraphService
from .graph_extractor import GraphSuggestion, GraphVocabulary, extraction_request, parse_suggestions
from .knowledge import KnowledgeChunkPointer
from .model_budget import ModelBudget
from .store import StoreConnection, drain_on_cancel
from .types import ActionCall, ModelPort, OperationError, Scope


@dataclass(frozen=True, slots=True)
class GraphExtractionResult:
    state: Literal["disabled", "proposed", "no_candidates", "already_attempted"]
    action_key: str = ""
    relations: tuple[GraphRelation, ...] = ()
    suggestions: tuple[GraphSuggestion, ...] = ()
    discarded_below_threshold: int = 0


class GraphExtractionRunner:
    def __init__(
        self,
        graph: GraphService,
        actions: Actions,
        model: ModelPort,
        budget: ModelBudget,
        config: Config,
        *,
        enabled: bool = False,
        targets: tuple[Scope, ...] = (),
    ) -> None:
        if actions.store is not graph.store or actions.policy is not graph.policy:
            raise OperationError("graph_owner_mismatch")
        if (
            type(enabled) is not bool
            or len(targets) > 128
            or len(set(s.key for s in targets)) != len(targets)
        ):
            raise OperationError("invalid_graph_targets")
        if enabled and not targets:
            raise OperationError("invalid_graph_targets")
        self.graph, self.actions, self.model, self.budget = graph, actions, model, budget
        self.config = config.for_task("memory")
        self.enabled, self.targets = enabled, targets
        self._tasks: set[asyncio.Task[GraphExtractionResult]] = set()
        self._closed = False

    async def run_once(
        self,
        *,
        scope: Scope,
        actor: str,
        pointer: KnowledgeChunkPointer,
        run_id: str,
        vocabulary: GraphVocabulary,
    ) -> GraphExtractionResult:
        if self._closed:
            raise OperationError("closed")
        if not self.enabled:
            return GraphExtractionResult("disabled")
        if scope not in self.targets or pointer.scope != scope:
            raise OperationError("denied")
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,63}", run_id) is None:
            raise OperationError("invalid_graph_run_id")
        vocabulary = GraphVocabulary.model_validate(vocabulary.model_dump())
        task = asyncio.create_task(self._run(scope, actor, pointer, run_id, vocabulary))
        self._tasks.add(task)
        try:
            return await task
        finally:
            self._tasks.discard(task)

    def _preflight(
        self, db: StoreConnection, scope: Scope, actor: str, pointer: KnowledgeChunkPointer
    ) -> None:
        if self._closed:
            raise OperationError("closed")
        config, policy = self.config, self.graph.policy
        policy.check_transaction(db, actor, scope, "graph.learn", "", "", False, False)
        self.graph.knowledge.assert_chunk_pointer_transaction(db, scope=scope, actor=actor, pointer=pointer)
        for subject in (actor, pointer.uploader_id):
            policy.check_transaction(
                db, subject, scope, "model.invoke", config.policy_provider, config.model, True, False
            )
        policy.check_transaction(
            db,
            pointer.uploader_id,
            scope,
            "knowledge.import",
            config.policy_provider,
            config.model,
            False,
            False,
        )

    async def _run(
        self,
        scope: Scope,
        actor: str,
        pointer: KnowledgeChunkPointer,
        run_id: str,
        vocabulary: GraphVocabulary,
    ) -> GraphExtractionResult:
        config = self.config
        chunk = await self.graph.knowledge.resolve_chunk_pointer(scope=scope, actor=actor, pointer=pointer)
        await self.graph.store.transaction(lambda db: self._preflight(db, scope, actor, pointer))
        request = extraction_request(chunk.body, vocabulary, config.model)
        source_identity = hashlib.sha256(pointer.model_dump_json().encode()).hexdigest()[:24]
        scope_identity = hashlib.sha256(scope.model_dump_json().encode()).hexdigest()[:16]
        key = f"graph-extract:v1:{scope_identity}:{run_id}:{source_identity}:body0"
        payload = json.dumps(
            {
                "request": request.model_dump(mode="json"),
                "source": pointer.model_dump(mode="json"),
                "vocabulary": vocabulary.model_dump(mode="json"),
                "actor": actor,
                "run_id": run_id,
                "profile": config.selected_model.model_dump(mode="json"),
                "provider": config.policy_provider,
                "scope": scope.model_dump(mode="json"),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        call = ActionCall(
            key=key,
            request_id=key,
            subject=actor,
            scope=scope,
            action="model.invoke",
            payload_hash=hashlib.sha256(payload.encode()).hexdigest(),
            provider=config.policy_provider,
            model=config.model,
            includes_history=True,
        )
        deadline = monotonic() + config.model_timeout
        try:
            async with asyncio.timeout(config.model_timeout):
                async with self.budget.slot("reply"):
                    reply = await self.actions.execute(
                        call,
                        lambda: self.model.request(request),
                        external=self.model.is_external,
                        model_task="memory",
                        timeout=max(0.0, deadline - monotonic()),
                        preflight_transaction=lambda db: self._preflight(db, scope, actor, pointer),
                        document_upload_subjects=(pointer.uploader_id,),
                    )
        except OperationError as exc:
            if exc.code != "duplicate":
                raise
            # Actions stores intent/outcome, never a recoverable ModelReply body.
            return GraphExtractionResult("already_attempted", action_key=key)
        except TimeoutError as exc:
            raise OperationError("graph_extraction_timeout") from exc
        suggestions, discarded = parse_suggestions(
            reply,
            body=chunk.body,
            pointer=pointer,
            vocabulary=vocabulary,
            action_key=key,
        )
        if not suggestions:
            await self.graph.store.transaction(lambda db: self._preflight(db, scope, actor, pointer))
            return GraphExtractionResult("no_candidates", key, discarded_below_threshold=discarded)
        relations = await self.graph.propose_relations(
            tuple(suggestion.relation for suggestion in suggestions),
            actor=actor,
            scope=scope,
            preflight_transaction=lambda db: self._preflight(db, scope, actor, pointer),
        )
        return GraphExtractionResult("proposed", key, relations, suggestions, discarded)

    async def close(self) -> None:
        async def shutdown() -> None:
            self._closed = True
            tasks = tuple(self._tasks)
            for task in tasks:
                task.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                    raise result

        await drain_on_cancel(asyncio.create_task(shutdown()))
