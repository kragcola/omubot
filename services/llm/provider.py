"""LLM provider abstraction — decouples API format from business logic.

Provider implementations handle:
- Request body + headers construction (Anthropic vs OpenAI format)
- SSE stream parsing (different event types and delta structures)
- Thinking block extraction and fallback text retrieval
- Assistant message construction for tool-loop history
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ToolUse:
    """A tool call extracted from an LLM response stream."""
    __slots__ = ("id", "input", "name")

    def __init__(self, id: str, name: str, input: dict[str, Any]) -> None:
        self.id = id
        self.name = name
        self.input = input


def extract_text(result: dict[str, Any]) -> str:
    """Extract usable text from a parse result.

    Only returns the actual response text, never thinking blocks.
    Thinking blocks contain internal reasoning that must not be shown to users.
    """
    return result.get("text", "")


def build_assistant_message(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Build assistant message content for tool-loop history.

    Preserves thinking blocks for models that require them echoed back
    (DeepSeek), then appends text and tool_use blocks.
    """
    content: list[dict[str, Any]] = []
    for tb in result.get("thinking_blocks", []):
        content.append(tb)
    text = extract_text(result)
    if text:
        content.append({"type": "text", "text": text})
    for tu in result.get("tool_uses", []):
        content.append({
            "type": "tool_use",
            "id": tu.id,
            "name": tu.name,
            "input": tu.input,
        })
    return content


class LLMProvider(ABC):
    """Abstract LLM API provider.

    Each provider knows how to format requests and parse responses for a
    specific API format (Anthropic Messages, OpenAI Chat Completions, etc.).
    """

    @abstractmethod
    def build_request(
        self,
        system_blocks: list[dict[str, Any]],
        messages: list[Any],
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
        model: str,
        thinking: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """Build (body, headers) for the HTTP POST request.

        Args:
            thinking: Optional Anthropic-format thinking control
                      (e.g. {"type": "disabled"} to suppress reasoning).
        """
        ...

    @abstractmethod
    def parse_sse_stream(self, raw_lines: list[str]) -> dict[str, Any]:
        """Parse raw SSE lines into a dict.

        Returns: {text, thinking_blocks, tool_uses, usage,
                  input_tokens, output_tokens, cache_read, cache_create}
        """
        ...

    def extract_text(self, result: dict[str, Any]) -> str:
        """Extract usable text from a parse result. Never returns thinking blocks."""
        return extract_text(result)

    def build_assistant_message(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        """Build assistant message content for tool-loop history."""
        return build_assistant_message(result)


def create_provider(api_format: str, base_url: str, api_key: str) -> LLMProvider:
    """Factory: create the correct provider for the configured API format."""
    if api_format == "openai":
        from services.llm.providers.openai import OpenAIProvider
        return OpenAIProvider(base_url, api_key)
    # Default: Anthropic/DeepSeek Anthropic-compatible
    from services.llm.providers.anthropic import AnthropicProvider
    return AnthropicProvider(base_url, api_key)
