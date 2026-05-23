"""LLM extraction boundary for Persona Source Importer.

The deterministic importer does not require this path to run. It exists so
future richer fields can use the shared LLM spine without hardcoding a model.
"""

from __future__ import annotations

import json
from typing import Any

from services.llm.llm_request import LLMRequest


class PersonaLLMExtractor:
    def __init__(self, llm_client: Any) -> None:
        self.llm_client = llm_client

    async def extract_json(self, *, source_text: str, instruction: str, max_tokens: int = 1200) -> dict[str, Any]:
        request = LLMRequest(
            task="persona_import",
            static_blocks=[
                (
                    "你是 Omubot Persona Source Importer 的结构化抽取器。"
                    "只能抽取 source.md 中有明确证据的内容；不得补写、扩写或创造人设。"
                    "所有输出字段必须能关联 source_span，否则调用方会丢弃。"
                    "只输出 JSON。"
                )
            ],
            user_messages=[
                {
                    "role": "user",
                    "content": (
                        f"{instruction.strip()}\n\n"
                        "source.md:\n"
                        "```markdown\n"
                        f"{source_text}\n"
                        "```"
                    ),
                }
            ],
            max_tokens=max_tokens,
            requires_capabilities=("json",),
        )
        response = await self.llm_client._call(request)
        text = str(response.get("text", "") if isinstance(response, dict) else response)
        return _parse_json_object(text)


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
    data = json.loads(stripped)
    return data if isinstance(data, dict) else {"items": data}


def filter_items_with_source_span(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop LLM items that cannot point back to source.md."""
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        span = item.get("source_span")
        if not isinstance(span, dict):
            continue
        lines = span.get("lines")
        if not (
            isinstance(lines, list)
            and len(lines) == 2
            and all(isinstance(value, int) and value > 0 for value in lines)
        ):
            continue
        result.append(item)
    return result
