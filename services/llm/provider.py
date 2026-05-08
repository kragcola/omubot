"""LLM provider abstraction — decouples API format from business logic.

Provider implementations handle:
- Request body + headers construction (Anthropic vs OpenAI format)
- SSE stream parsing (different event types and delta structures)
- Thinking block extraction and fallback text retrieval
- Assistant message construction for tool-loop history
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal


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


ThinkingMode = Literal["default", "disabled"] | dict[str, Any] | None


def normalize_thinking_mode(thinking: ThinkingMode) -> str:
    """Normalize legacy/provider-specific thinking configs to a small core set."""
    if thinking is None:
        return "default"
    if isinstance(thinking, str):
        return "disabled" if thinking == "disabled" else "default"
    thinking_type = str(thinking.get("type", "") or "").lower()
    if thinking_type == "disabled":
        return "disabled"
    return "default"


def provider_mode(api_format: str, base_url: str) -> str:
    """Return a human-readable provider mode for diagnostics/UI."""
    normalized = str(api_format or "anthropic").strip().lower()
    url = str(base_url or "").strip().lower()
    if normalized == "deepseek":
        return "native-beta" if "/beta" in url else "native"
    if normalized == "anthropic" and "api.deepseek.com/anthropic" in url:
        return "anthropic-compat"
    if normalized == "openai" and "api.deepseek.com" in url:
        return "openai-compat"
    return normalized


def is_deepseek_v4_model(model: str) -> bool:
    normalized = str(model or "").strip().lower()
    return normalized.startswith("deepseek-v4-")


class LLMProvider(ABC):
    """Abstract LLM API provider.

    Each provider knows how to format requests and parse responses for a
    specific API format (Anthropic Messages, OpenAI Chat Completions, etc.).
    """

    @abstractmethod
    def request_url(self) -> str:
        """Return the HTTP endpoint URL for this provider."""
        ...

    @abstractmethod
    def build_request(
        self,
        system_blocks: list[dict[str, Any]],
        messages: list[Any],
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
        model: str,
        thinking: ThinkingMode = None,
        request_options: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
        """Build (body, headers, request_meta) for the HTTP POST request.

        Args:
            thinking: Provider-neutral thinking control with legacy dict
                compatibility. Providers should at least honor "default" and
                "disabled".
            request_options: Provider-specific request hints, e.g. stable
                hashed user_id, reasoning effort, or observability flags.
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
    if api_format == "deepseek":
        from services.llm.providers.deepseek import DeepSeekProvider
        return DeepSeekProvider(base_url, api_key)
    if api_format == "openai":
        from services.llm.providers.openai import OpenAIProvider
        return OpenAIProvider(base_url, api_key)
    # Default: Anthropic/DeepSeek Anthropic-compatible
    from services.llm.providers.anthropic import AnthropicProvider
    return AnthropicProvider(base_url, api_key)
