from __future__ import annotations

import asyncio
from typing import Any

from services.llm.prompt_builder import PromptBuilder
from services.persona import PersonaRuntime
from services.url_meta.blacklist import is_blocked_url
from services.url_meta.og_title import clear_url_title_cache, collect_url_titles


class _Response:
    def __init__(self, body: str, *, status: int = 200, delay_s: float = 0.0) -> None:
        self.body = body
        self.status = status
        self.delay_s = delay_s
        self.headers = {"content-type": "text/html; charset=utf-8"}

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def text(self, **_kwargs: object) -> str:
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        return self.body


class _Session:
    def __init__(self, responses: dict[str, _Response | Exception]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str, **_kwargs: object) -> _Response:
        self.calls.append(url)
        result = self.responses[url]
        if isinstance(result, Exception):
            raise result
        return result


def _builder(persona_runtime: PersonaRuntime) -> PromptBuilder:
    return PromptBuilder(persona_runtime=persona_runtime)


async def test_collect_url_titles_reads_og_title() -> None:
    clear_url_title_cache()
    url = "https://example.com/post"
    session = _Session({url: _Response('<meta property="og:title" content="  Example &amp; Title  ">')})

    titles = await collect_url_titles(f"看这个 {url}", session=session)

    assert titles[0].title == "Example & Title"
    assert session.calls == [url]


async def test_collect_url_titles_falls_back_to_title_tag() -> None:
    clear_url_title_cache()
    url = "https://example.com/a"
    session = _Session({url: _Response("<title>Fallback Title</title>")})

    titles = await collect_url_titles(url, session=session)

    assert titles[0].title == "Fallback Title"


async def test_collect_url_titles_skips_admin_and_banking_domains() -> None:
    clear_url_title_cache()
    session = _Session({})

    assert is_blocked_url("https://admin.example.com/panel")
    assert is_blocked_url("https://bank.example.com/login")
    assert await collect_url_titles("https://admin.example.com/panel", session=session) == []
    assert session.calls == []


async def test_collect_url_titles_skips_private_hosts() -> None:
    clear_url_title_cache()
    session = _Session({})

    assert is_blocked_url("http://127.0.0.1/meta")
    assert is_blocked_url("http://service.internal/meta")
    assert await collect_url_titles("http://127.0.0.1/meta", session=session) == []
    assert session.calls == []


async def test_collect_url_titles_timeout_is_silent() -> None:
    clear_url_title_cache()
    url = "https://example.com/slow"
    session = _Session({url: _Response("<title>Slow</title>", delay_s=0.05)})

    assert await collect_url_titles(url, session=session, timeout_s=0.001) == []


async def test_collect_url_titles_fetch_failure_is_silent() -> None:
    clear_url_title_cache()
    url = "https://example.com/fail"
    session = _Session({url: RuntimeError("network down")})

    assert await collect_url_titles(url, session=session) == []


async def test_collect_url_titles_uses_lru_cache() -> None:
    clear_url_title_cache()
    url = "https://example.com/cache"
    session = _Session({url: _Response("<title>Cached</title>")})

    first = await collect_url_titles(url, session=session)
    second = await collect_url_titles(url, session=session)

    assert [item.title for item in first] == ["Cached"]
    assert [item.title for item in second] == ["Cached"]
    assert session.calls == [url]


async def test_prompt_builder_injects_group_url_titles(
    monkeypatch: Any, persona_runtime: PersonaRuntime,
) -> None:
    async def fake_context(text: str, *, limit: int = 3) -> str:
        assert "https://example.com/post" in text
        assert limit == 3
        return "【链接标题】\n- Example (https://example.com/post)"

    monkeypatch.setattr("services.llm.prompt_builder.build_url_title_context", fake_context)
    blocks = await _builder(persona_runtime).build_blocks(
        group_id="200",
        conversation_text="看 https://example.com/post",
        include_state_board=False,
    )

    assert blocks[1]["text"] == "【链接标题】\n- Example (https://example.com/post)"


async def test_prompt_builder_skips_url_titles_for_private_chat(
    monkeypatch: Any, persona_runtime: PersonaRuntime,
) -> None:
    called = False

    async def fake_context(_text: str, *, limit: int = 3) -> str:
        nonlocal called
        called = True
        return "bad"

    monkeypatch.setattr("services.llm.prompt_builder.build_url_title_context", fake_context)
    blocks = await _builder(persona_runtime).build_blocks(
        group_id=None,
        conversation_text="看 https://example.com/post",
        include_state_board=False,
    )

    assert called is False
    assert len(blocks) == 1
