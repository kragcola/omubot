from __future__ import annotations

from typing import Any
from urllib.parse import quote

from services.url_meta.video_adapter import collect_video_metadata


class _Response:
    def __init__(self, data: object, *, status: int = 200) -> None:
        self.data = data
        self.status = status

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def json(self, **_kwargs: object) -> object:
        return self.data


class _Session:
    def __init__(self, responses: dict[str, _Response | Exception]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str, **_kwargs: Any) -> _Response:
        self.calls.append(url)
        result = self.responses[url]
        if isinstance(result, Exception):
            raise result
        return result


def _yt_endpoint(video_id: str) -> str:
    canonical = f"https://www.youtube.com/watch?v={video_id}"
    return f"https://www.youtube.com/oembed?format=json&url={quote(canonical, safe='')}"


async def test_collect_video_metadata_defaults_disabled_noop() -> None:
    session = _Session({})

    assert await collect_video_metadata("https://www.youtube.com/watch?v=dQw4w9WgXcQ", session=session) == []
    assert session.calls == []


async def test_collect_video_metadata_reads_bilibili_bv_and_av() -> None:
    bv_api = "https://api.bilibili.com/x/web-interface/view?bvid=BV1xx1234567"
    av_api = "https://api.bilibili.com/x/web-interface/view?aid=123456"
    session = _Session({
        bv_api: _Response({"code": 0, "data": {"title": "  BV 视频  "}}),
        av_api: _Response({"code": 0, "data": {"title": "av 视频"}}),
    })

    metas = await collect_video_metadata(
        "看 https://www.bilibili.com/video/BV1xx1234567 和 https://www.bilibili.com/video/av123456",
        enabled=True,
        session=session,
    )

    assert [(item.platform, item.video_id, item.title) for item in metas] == [
        ("bilibili", "BV1xx1234567", "BV 视频"),
        ("bilibili", "av123456", "av 视频"),
    ]
    assert session.calls == [bv_api, av_api]


async def test_collect_video_metadata_reads_youtube_watch_shorts_and_short_url() -> None:
    ids = ["dQw4w9WgXcQ", "AbCdEfGhIjK", "ZZZZZZZZZZZ"]
    session = _Session({endpoint: _Response({"title": f"YT {i}"}) for i, endpoint in enumerate(map(_yt_endpoint, ids))})

    metas = await collect_video_metadata(
        " ".join([
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtube.com/shorts/AbCdEfGhIjK",
            "https://youtu.be/ZZZZZZZZZZZ",
        ]),
        enabled=True,
        session=session,
        limit=3,
    )

    assert [(item.platform, item.video_id, item.title) for item in metas] == [
        ("youtube", "dQw4w9WgXcQ", "YT 0"),
        ("youtube", "AbCdEfGhIjK", "YT 1"),
        ("youtube", "ZZZZZZZZZZZ", "YT 2"),
    ]


async def test_collect_video_metadata_skips_non_video_urls() -> None:
    session = _Session({})

    assert await collect_video_metadata("https://example.com/post", enabled=True, session=session) == []
    assert session.calls == []


async def test_collect_video_metadata_fetch_failure_is_silent() -> None:
    endpoint = _yt_endpoint("dQw4w9WgXcQ")
    session = _Session({endpoint: RuntimeError("network down")})

    assert await collect_video_metadata(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        enabled=True,
        session=session,
    ) == []
    assert session.calls == [endpoint]
