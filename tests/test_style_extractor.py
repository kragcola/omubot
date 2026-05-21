import pytest

from services.style import StyleExtractor, select_style_source_row


class _RiskStyleLLM:
    async def _call(self, request):
        del request
        return {
            "text": (
                '{"expressions":[{"situation":"大家在尖锐吐槽","style":"理解脏话里的情绪强度，输出时转成凤笑梦式调皮回应",'
                '"evidence":"这也太离谱了吧我靠","confidence":0.88,"risk_tags":["profanity","sarcasm"],'
                '"output_policy":"allow_use","persona_fit":0.62,"mood_fit":0.8,"reason":"脏话表达情绪强度"}]}'
            )
        }


class _NoisyStyleLLM:
    async def _call(self, request):
        del request
        return {
            "text": (
                '{"expressions":[{"situation":"有人说话","style":"可以接话",'
                '"evidence":"有人说了句话","confidence":0.92,"risk_tags":[],'
                '"output_policy":"allow_use","persona_fit":0.9,"mood_fit":0.9,"reason":"太泛化"}]}'
            )
        }


class _MalformedStyleLLM:
    async def _call(self, request):
        del request
        return {"text": "不是 JSON，也没有候选"}


@pytest.mark.asyncio
async def test_style_extractor_keeps_risky_expression_but_transforms_output_policy() -> None:
    extractor = StyleExtractor(_RiskStyleLLM())

    items = await extractor.extract([
        {
            "role": "user",
            "speaker": "Alice(10001)",
            "content_text": "这也太离谱了吧我靠",
            "message_id": 42,
        }
    ])

    assert len(items) == 1
    [item] = items
    assert item.situation == "大家在尖锐吐槽"
    assert item.risk_tags == ["profanity", "sarcasm"]
    assert item.output_policy == "transform"
    assert item.confidence == 0.88


@pytest.mark.asyncio
async def test_style_extractor_rejects_generic_low_signal_candidate() -> None:
    extractor = StyleExtractor(_NoisyStyleLLM())

    items = await extractor.extract([
        {
            "role": "user",
            "speaker": "Alice(10001)",
            "content_text": "有人说了句话",
            "message_id": 42,
        }
    ])

    assert items == []


@pytest.mark.asyncio
async def test_style_extractor_returns_empty_for_malformed_json() -> None:
    extractor = StyleExtractor(_MalformedStyleLLM())

    items = await extractor.extract([{"role": "user", "content_text": "这句挺自然"}])

    assert items == []


@pytest.mark.asyncio
async def test_style_extractor_returns_empty_without_llm() -> None:
    assert await StyleExtractor(None).extract([{"role": "user", "content_text": "hello"}]) == []


def test_select_style_source_row_prefers_matching_evidence() -> None:
    rows = [
        {"content_text": "先来一句", "message_id": 1},
        {"content_text": "这也太离谱了吧我靠", "message_id": 2},
    ]

    selected = select_style_source_row("太离谱了吧我靠", rows)

    assert selected["message_id"] == 2


def test_select_style_source_row_falls_back_to_latest_without_match() -> None:
    rows = [
        {"content_text": "先来一句", "message_id": 1},
        {"content_text": "最后一句", "message_id": 2},
    ]

    selected = select_style_source_row("完全无关证据", rows)

    assert selected["message_id"] == 2
