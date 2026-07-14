import aiohttp
import pytest

from services.media.vision import VisionClient


class _VisionResponse:
    status = 200

    def __init__(self, content: object) -> None:
        self._content = content

    async def __aenter__(self) -> "_VisionResponse":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def json(self) -> dict[str, object]:
        return {"choices": [{"message": {"content": self._content}}]}


class _SuccessfulClientSession:
    def __init__(self, content: object) -> None:
        self._content = content

    async def __aenter__(self) -> "_SuccessfulClientSession":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    def post(self, *args, **kwargs) -> _VisionResponse:
        return _VisionResponse(self._content)


@pytest.mark.asyncio
async def test_failed_vision_probe_updates_health_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingClientSession:
        async def __aenter__(self) -> "FailingClientSession":
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

        def post(self, *args, **kwargs):
            raise aiohttp.ClientConnectionError("vision probe failed")

    monkeypatch.setattr(
        "services.media.vision.aiohttp.ClientSession",
        lambda *args, **kwargs: FailingClientSession(),
    )
    client = VisionClient(
        base_url="https://vision.invalid",
        api_key="test-key",
        model="test-model",
    )

    assert await client.describe_image(b"image") is None

    snapshot = getattr(client, "health_snapshot", lambda: {})()
    assert snapshot.get("available") is True
    assert snapshot.get("status") == "failed"
    assert snapshot.get("calls") == 1
    assert snapshot.get("errors") == 1
    assert "vision probe failed" in snapshot.get("last_error", "")


@pytest.mark.asyncio
async def test_successful_vision_probe_reports_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.media.vision.aiohttp.ClientSession",
        lambda *args, **kwargs: _SuccessfulClientSession(" 识别成功 "),
    )
    client = VisionClient(
        base_url="https://vision.invalid",
        api_key="test-key",
        model="test-model",
    )

    assert await client.describe_image(b"image") == "识别成功"
    assert client.health_snapshot() == {
        "available": True,
        "status": "healthy",
        "calls": 1,
        "errors": 0,
        "last_error": "",
    }


@pytest.mark.asyncio
async def test_vision_health_recovers_after_a_later_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    class RecoveringClientSession(_SuccessfulClientSession):
        def post(self, *args, **kwargs) -> _VisionResponse:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise aiohttp.ClientConnectionError("temporary failure")
            return super().post(*args, **kwargs)

    monkeypatch.setattr(
        "services.media.vision.aiohttp.ClientSession",
        lambda *args, **kwargs: RecoveringClientSession("recovered"),
    )
    client = VisionClient(
        base_url="https://vision.invalid",
        api_key="test-key",
        model="test-model",
    )

    assert await client.describe_image(b"first") is None
    assert await client.describe_image(b"second") == "recovered"
    snapshot = client.health_snapshot()
    assert snapshot["status"] == "healthy"
    assert snapshot["calls"] == 2
    assert snapshot["errors"] == 1
    assert snapshot["last_error"] == ""


@pytest.mark.asyncio
async def test_blank_vision_response_remains_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.media.vision.aiohttp.ClientSession",
        lambda *args, **kwargs: _SuccessfulClientSession("   \n"),
    )
    client = VisionClient(
        base_url="https://vision.invalid",
        api_key="test-key",
        model="test-model",
    )

    assert await client.describe_image(b"image") is None
    snapshot = client.health_snapshot()
    assert snapshot["status"] == "failed"
    assert snapshot["errors"] == 1
    assert "empty vision response" in str(snapshot["last_error"])
