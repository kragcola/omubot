"""Style extraction orchestration shared by plugins and Admin adapters."""

from __future__ import annotations

import asyncio
from typing import Any, cast

from services.conversation_archive import (
    add_evidence_message_ref,
    finish_scan_batch,
    read_scan_batch,
)
from services.slang import SlangStore, SlangTerm, normalize_term
from services.style.extractor import (
    StyleExtraction,
    StyleExtractor,
    format_style_messages,
    is_style_human_evidence_eligible,
    select_style_source_row,
)
from services.style.store import StyleScope, StyleStatus, StyleStore

_AUTO_APPROVE_MIN_CONFIDENCE = 0.86
_AUTO_APPROVE_MIN_PERSONA_FIT = 0.72
_DEFAULT_BATCH_LIMIT = 120
_DEFAULT_MAX_BATCHES = 5
_DEFAULT_TARGET_TEXT_ROWS = 200
_MAX_MANUAL_BATCH_LIMIT = 500
_MAX_MANUAL_BATCHES = 12
_MAX_TARGET_TEXT_ROWS = 800


async def run_style_manual_extract(
    *,
    style_store: StyleStore,
    message_log: Any,
    llm_client: Any,
    slang_store: SlangStore | None = None,
    group_id: str = "",
    scope: str = "group",
    limit: int = _DEFAULT_BATCH_LIMIT,
    max_batches: int = _DEFAULT_MAX_BATCHES,
    target_text_rows: int = _DEFAULT_TARGET_TEXT_ROWS,
    auto_approve: bool = False,
) -> dict[str, Any]:
    if scope not in {"group", "global"}:
        return {"ok": False, "error": "scope must be group or global"}
    if message_log is None:
        return {"ok": False, "error": "MessageLog not available"}
    if llm_client is None:
        return {"ok": False, "error": "LLMClient not available"}

    scope_value: StyleScope = "global" if scope == "global" else "group"
    limit = max(1, min(int(limit or _DEFAULT_BATCH_LIMIT), _MAX_MANUAL_BATCH_LIMIT))
    max_batches = max(1, min(int(max_batches or _DEFAULT_MAX_BATCHES), _MAX_MANUAL_BATCHES))
    target_text_rows = max(
        1,
        min(int(target_text_rows or _DEFAULT_TARGET_TEXT_ROWS), _MAX_TARGET_TEXT_ROWS),
    )

    if group_id:
        groups = [group_id]
    elif hasattr(message_log, "list_group_ids"):
        groups = [str(item) for item in await message_log.list_group_ids() if str(item).strip()]
    else:
        return {"ok": False, "error": "group_id is required when MessageLog cannot list groups"}

    extractor = StyleExtractor(llm_client)
    scanned = 0
    raw_scanned = 0
    backlog_raw = 0
    backlog_text = 0
    extracted_count = 0
    saved = 0
    approved = 0
    pending = 0
    filtered = 0
    expression_ids: list[str] = []
    per_group: list[dict[str, Any]] = []

    try:
        for gid in groups:
            group_result: dict[str, Any] = {
                "group_id": str(gid),
                "scanned": 0,
                "text_scanned": 0,
                "raw_scanned": 0,
                "batches": 0,
                "backlog_raw": 0,
                "backlog_text": 0,
                "has_more": False,
                "extracted": 0,
                "filtered": 0,
                "saved": 0,
                "approved": 0,
                "pending": 0,
                "expression_ids": [],
            }
            per_group.append(group_result)
            slang_keys = await _load_style_slang_keys(slang_store, str(gid))
            for batch_index in range(max_batches):
                batch = await read_scan_batch(
                    message_log,
                    scanner_name="style_manual_extract",
                    group_id=str(gid),
                    limit=limit,
                    scanner_version="v1",
                    params_hash="",
                    meta={
                        "admin_manual": True,
                        "scope": scope_value,
                        "batch_index": batch_index + 1,
                        "max_batches": max_batches,
                        "target_text_rows": target_text_rows,
                    },
                )
                if batch_index == 0:
                    group_result["scan_source"] = batch.get("source")
                    group_result["from_message_pk"] = batch.get("from_message_pk")
                group_result["to_message_pk"] = batch.get("to_message_pk")
                try:
                    rows = list(batch.get("rows") or [])
                    batch_raw_count = len(rows)
                    user_rows = [
                        row
                        for row in rows
                        if is_style_human_evidence_eligible(row)
                    ]
                    batch_text_count = len(user_rows)
                    batch_filtered_count = 0
                    batch_saved_count = 0
                    raw_scanned += batch_raw_count
                    scanned += batch_text_count
                    group_result["raw_scanned"] += batch_raw_count
                    group_result["text_scanned"] += batch_text_count
                    group_result["scanned"] = group_result["text_scanned"]
                    group_result["batches"] += 1

                    extractions = await extractor.extract(user_rows)
                    extracted_count += len(extractions)
                    group_result["extracted"] += len(extractions)
                    for item in extractions:
                        if _style_extraction_mentions_slang(item, slang_keys):
                            filtered += 1
                            group_result["filtered"] += 1
                            batch_filtered_count += 1
                            continue
                        status = _status_for_extraction(item, auto_approve=auto_approve)
                        source = select_style_source_row(item.evidence, user_rows)
                        expression = await style_store.upsert_expression(
                            item.to_new_expression(group_id=str(gid), scope=scope_value, status=status),
                            evidence={
                                "group_id": str(gid),
                                "speaker": str(source.get("speaker") or ""),
                                "raw_text": str(source.get("content_text") or item.evidence),
                                "context": format_style_messages(user_rows[-12:]),
                                "source_type": "human",
                                "message_id": _message_id(source.get("message_id")),
                            },
                            actor="admin_manual_extract",
                            reason=item.reason or "manual style extraction",
                        )
                        if status == "approved" and expression.status != "approved":
                            await style_store.set_status(
                                expression.expression_id,
                                "approved",
                                actor="admin_manual_extract",
                                reason="high-confidence manual style extraction",
                            )
                            refreshed = await style_store.get_expression(expression.expression_id)
                            expression = refreshed or expression
                        saved += 1
                        expression_ids.append(expression.expression_id)
                        await add_evidence_message_ref(
                            message_log,
                            group_id=str(gid),
                            source_row=source,
                            ref_owner="style",
                            external_table="style_expressions",
                            external_id=expression.expression_id,
                            snapshot_text=str(source.get("content_text") or item.evidence),
                            meta={"source": "style_manual_extract"},
                        )
                        if expression.status == "approved":
                            approved += 1
                            group_result["approved"] += 1
                        else:
                            pending += 1
                            group_result["pending"] += 1
                        group_result["saved"] += 1
                        batch_saved_count += 1
                        group_result["expression_ids"].append(expression.expression_id)
                    await finish_scan_batch(
                        message_log,
                        batch,
                        status="success",
                        scanned_count=batch_text_count,
                        extracted_count=len(extractions),
                        filtered_count=batch_filtered_count,
                        saved_count=batch_saved_count,
                        meta={
                            "expression_ids": group_result["expression_ids"],
                            "batch_text_scanned": batch_text_count,
                            "batch_raw_scanned": batch_raw_count,
                        },
                    )
                except asyncio.CancelledError:
                    cleanup_task = asyncio.create_task(
                        finish_scan_batch(
                            message_log,
                            batch,
                            status="abandoned",
                            scanned_count=group_result["text_scanned"],
                            extracted_count=group_result["extracted"],
                            filtered_count=group_result["filtered"],
                            saved_count=group_result["saved"],
                            error="cancelled",
                            advance_cursor=False,
                        ),
                        name=f"style-scan-cleanup:{batch.get('run_id') or 'legacy'}",
                    )
                    while not cleanup_task.done():
                        try:
                            await asyncio.shield(cleanup_task)
                        except asyncio.CancelledError:
                            continue
                    await cleanup_task
                    raise
                except Exception as exc:
                    await finish_scan_batch(
                        message_log,
                        batch,
                        status="failed",
                        scanned_count=group_result["text_scanned"],
                        extracted_count=group_result["extracted"],
                        filtered_count=group_result["filtered"],
                        saved_count=group_result["saved"],
                        error=str(exc),
                        advance_cursor=False,
                    )
                    raise
                if (
                    batch.get("source") != "archive"
                    or not batch.get("can_advance")
                    or batch_raw_count < limit
                    or group_result["text_scanned"] >= target_text_rows
                ):
                    break
            backlog = await _style_scan_backlog(message_log, str(gid), scanner_name="style_manual_extract")
            group_result["backlog_raw"] = backlog["raw"]
            group_result["backlog_text"] = backlog["text"]
            group_result["has_more"] = backlog["raw"] > 0
            backlog_raw += backlog["raw"]
            backlog_text += backlog["text"]
    except Exception as exc:
        if per_group:
            per_group[-1]["error"] = str(exc)
        return {
            "ok": False,
            "error": str(exc),
            "groups": groups,
            "scope": scope_value,
            "scanned": scanned,
            "text_scanned": scanned,
            "raw_scanned": raw_scanned,
            "backlog_raw": backlog_raw,
            "backlog_text": backlog_text,
            "has_more": backlog_raw > 0,
            "batch_limit": limit,
            "max_batches": max_batches,
            "target_text_rows": target_text_rows,
            "extracted": extracted_count,
            "filtered": filtered,
            "saved": saved,
            "approved": approved,
            "pending": pending,
            "expression_ids": expression_ids,
            "per_group": per_group,
        }

    return {
        "ok": True,
        "groups": groups,
        "scope": scope_value,
        "scanned": scanned,
        "text_scanned": scanned,
        "raw_scanned": raw_scanned,
        "backlog_raw": backlog_raw,
        "backlog_text": backlog_text,
        "has_more": backlog_raw > 0,
        "batch_limit": limit,
        "max_batches": max_batches,
        "target_text_rows": target_text_rows,
        "extracted": extracted_count,
        "filtered": filtered,
        "saved": saved,
        "approved": approved,
        "pending": pending,
        "expression_ids": expression_ids,
        "per_group": per_group,
    }




async def _load_style_slang_keys(slang_store: SlangStore | None, group_id: str) -> set[str]:
    if slang_store is None:
        return set()
    terms: list[SlangTerm] = []
    try:
        chunk, _total = await slang_store.list_terms(group_id=group_id, limit=1000)
        terms.extend(chunk)
    except Exception:
        pass
    try:
        chunk, _total = await slang_store.list_terms(scope="global", limit=1000)
        terms.extend(chunk)
    except Exception:
        pass
    keys: set[str] = set()
    for term in terms:
        if term.status not in {"candidate", "approved"}:
            continue
        for value in [term.term, *term.aliases]:
            key = normalize_term(value)
            if _is_style_slang_key(key):
                keys.add(key)
    return keys


async def _style_scan_backlog(message_log: Any, group_id: str, *, scanner_name: str) -> dict[str, int]:
    get_cursor = getattr(message_log, "get_cursor", None)
    count_after = getattr(message_log, "count_messages_after_pk", None)
    if not callable(get_cursor) or not callable(count_after):
        return {"raw": 0, "text": 0}
    try:
        cursor = await cast(Any, get_cursor)(
            scanner_name=scanner_name,
            chat_type="group",
            chat_id=str(group_id),
            scope_key="chat",
        )
        after_pk = int(cursor["last_message_pk"] or 0) if cursor else 0
        counts = await cast(Any, count_after)(
            chat_type="group",
            chat_id=str(group_id),
            after_message_pk=after_pk,
        )
    except Exception:
        return {"raw": 0, "text": 0}
    return {"raw": int(counts.get("raw") or 0), "text": int(counts.get("text") or 0)}


def _style_extraction_mentions_slang(item: StyleExtraction, slang_keys: set[str]) -> bool:
    if not slang_keys:
        return False
    candidate_key = normalize_term(f"{item.situation} {item.style}")
    if not candidate_key:
        return False
    return any(key in candidate_key for key in slang_keys)


def _is_style_slang_key(key: str) -> bool:
    if len(key) >= 3:
        return True
    return len(key) >= 2 and any(ord(char) > 127 for char in key)


def _status_for_extraction(item: StyleExtraction, *, auto_approve: bool) -> StyleStatus:
    if (
        auto_approve
        and item.confidence >= _AUTO_APPROVE_MIN_CONFIDENCE
        and item.persona_fit >= _AUTO_APPROVE_MIN_PERSONA_FIT
        and item.output_policy != "observe_only"
    ):
        return "approved"
    return "pending"


def _message_id(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
