"""Vision client: Qwen VL image description (system service).

Initialized at system layer in bot.py — not a plugin.
Enabled when vision.qwen.api_key is configured, disabled otherwise.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any, Literal

import aiohttp
from loguru import logger

ImageIntent = Literal["react", "describe", "identify", "ocr"]

# Structured internal evidence prompt — not chatty product/usage prose the model
# might echo as user-facing description. Fields guide VL extraction only.
_STICKER_DESCRIBE_PROMPT = (
    "请对这张图片/表情包做结构化视觉证据抽取（内部侧信道，不是给用户看的产品说明）。"
    "用一句简短中文覆盖：对象、动作、情绪/态度；有文字时附 OCR。"
    "格式示例：对象=…；动作=…；情绪=…；OCR=…（无文字则省略 OCR）。"
    "不要写聊天场景建议，不要写「适合在什么场景使用」，不要写成可直接转发的表情包文案。"
    "如果图上有文字，在句末用固定格式附上：图上文字：xxx（原样写出）；"
    "若无文字则不要写「图上文字」。"
)

_IDENTIFY_RE = re.compile(
    r"(这是谁|谁啊|谁呀|哪个角色|什么角色|哪个人|认一下|识别|是不是.{0,8}(？|\?|$))",
    re.IGNORECASE,
)
_OCR_RE = re.compile(
    r"(写了什么|上面写|图上文字|文字是|读一下|OCR|翻译|念一下|看下字)",
    re.IGNORECASE,
)
_DESCRIBE_RE = re.compile(
    r"(图里是什么|这是什么|什么图|描述|看看这|这张图|画面|讲讲这|解释一下这)",
    re.IGNORECASE,
)


def classify_image_intent(user_text: str) -> ImageIntent:
    """Route image-related user intent without defaulting to description prose.

    Empty / pure-reaction text → react (acknowledge / sticker path, no forced
    describe monologue). Explicit identity / OCR / describe cues win in that
    priority order.
    """
    text = str(user_text or "").strip()
    if not text:
        return "react"
    if _OCR_RE.search(text):
        return "ocr"
    if _IDENTIFY_RE.search(text):
        return "identify"
    if _DESCRIBE_RE.search(text):
        return "describe"
    # Short reaction / emoji / laugh → react; longer free text still react unless
    # describe cues fired above (non-description intents must not force prose).
    return "react"


class VisionClient:
    """Calls Qwen VL model (OpenAI-compatible API) to describe images."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout_s: float = 15.0,
        max_tokens: int = 200,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s
        self._max_tokens = max(1, int(max_tokens))
        self._calls = 0
        self._errors = 0
        self._last_error = ""

    def health_snapshot(self) -> dict[str, object]:
        available = bool(self._base_url and self._api_key and self._model)
        if not available:
            status = "unavailable"
        elif self._last_error:
            status = "failed"
        elif self._calls:
            status = "healthy"
        else:
            status = "idle"
        return {
            "available": available,
            "status": status,
            "calls": self._calls,
            "errors": self._errors,
            "last_error": self._last_error,
        }

    def _record_failure(self, error: str) -> None:
        self._errors += 1
        self._last_error = error

    async def describe_image(
        self,
        image_data: bytes,
        media_type: str = "image/jpeg",
        prompt: str | None = None,
    ) -> str | None:
        """Send an image to Qwen VL and return a short description.

        Returns None on any failure (network, API error, unexpected response).
        """
        self._calls += 1
        b64 = base64.b64encode(image_data).decode()
        data_url = f"data:{media_type};base64,{b64}"

        body: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": prompt or _STICKER_DESCRIBE_PROMPT},
                    ],
                }
            ],
            "max_tokens": self._max_tokens,
            "temperature": 0.3,
        }

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        url = f"{self._base_url}/chat/completions"

        try:
            timeout = aiohttp.ClientTimeout(total=self._timeout_s)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(url, json=body, headers=headers) as resp,
            ):
                    if resp.status >= 400:
                        body_text = await resp.text()
                        self._record_failure(
                            f"HTTP {resp.status}: {body_text[:300]}"
                        )
                        logger.error(
                            "Qwen VL {} | body={}", resp.status, body_text[:300]
                        )
                        return None
                    data = await resp.json()
        except (aiohttp.ClientError, TimeoutError, json.JSONDecodeError) as e:
            self._record_failure(f"{type(e).__name__}: {e}")
            logger.warning("Qwen VL request failed: {} ({})", e, type(e).__name__)
            return None

        try:
            desc = data["choices"][0]["message"]["content"]
            if not isinstance(desc, str):
                raise TypeError("vision response content must be a string")
            desc = desc.strip()
            if not desc:
                raise ValueError("empty vision response")
            self._last_error = ""
            return desc
        except (KeyError, IndexError, TypeError, ValueError) as e:
            self._record_failure(f"{type(e).__name__}: {e}")
            logger.warning("Qwen VL unexpected response format: {}", e)
            return None
