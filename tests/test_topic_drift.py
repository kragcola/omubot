from __future__ import annotations

from services.group.topic_drift import TopicDriftDetector
from services.similarity import SimilarityProvider


class _FixedSimilarity(SimilarityProvider):
    backend = "ngram"

    def __init__(self, value: float) -> None:
        self.value = value

    def similarity(self, left: str, right: str) -> float:
        return self.value


class _BrokenEmbedding(SimilarityProvider):
    backend = "embedding"

    def similarity(self, left: str, right: str) -> float:
        raise RuntimeError("embedding similarity backend is not installed/enabled")


async def test_topic_drift_cold_start_returns_current_topic() -> None:
    detector = TopicDriftDetector()

    result = await detector.detect([{"role": "user", "speaker": "alice(1001)", "content_text": "猫猫今天很可爱"}])

    assert result.topic == "猫猫今天很可爱"
    assert result.drift_score == 0.0
    assert result.is_new_topic is False
    assert result.participants == ("1001",)


async def test_topic_drift_low_score_for_continuing_topic() -> None:
    detector = TopicDriftDetector(similarity=_FixedSimilarity(0.75))

    result = await detector.detect(
        [
            {"role": "user", "speaker_id": "1001", "content_text": "猫猫在睡觉"},
            {"role": "user", "speaker_id": "1002", "content_text": "猫猫还翻肚皮"},
            {"role": "user", "speaker_id": "1001", "content_text": "猫猫醒了"},
        ]
    )

    assert result.drift_score == 0.25
    assert result.is_new_topic is False
    assert result.participants == ("1001", "1002")


async def test_topic_drift_high_score_for_new_topic() -> None:
    detector = TopicDriftDetector(similarity=_FixedSimilarity(0.2))

    result = await detector.detect(
        [
            {"role": "user", "content_text": "猫猫在睡觉"},
            {"role": "assistant", "content_text": "嗯嗯"},
            {"role": "user", "content_text": "猫猫还翻肚皮"},
            {"role": "user", "content_text": "显卡驱动怎么装"},
        ]
    )

    assert result.topic == "显卡驱动怎么装"
    assert result.drift_score == 0.8
    assert result.is_new_topic is True


async def test_topic_drift_uses_last_three_user_messages_only() -> None:
    detector = TopicDriftDetector(similarity=_FixedSimilarity(0.9))

    result = await detector.detect(
        [
            {"role": "user", "speaker_id": "old", "content_text": "旧话题"},
            {"role": "assistant", "speaker_id": "bot", "content_text": "bot reply"},
            {"role": "user", "speaker_id": "a", "content_text": "第一条"},
            {"role": "user", "speaker_id": "b", "content_text": "第二条"},
            {"role": "user", "speaker_id": "c", "content_text": "第三条"},
        ]
    )

    assert result.topic == "第三条"
    assert result.participants == ("a", "b", "c")


async def test_topic_drift_cleans_cq_codes_and_urls() -> None:
    detector = TopicDriftDetector()

    result = await detector.detect(
        [
            {"role": "user", "content_text": "猫猫行为"},
            {"role": "user", "content_text": "继续猫猫"},
            {"role": "user", "content_text": "[CQ:at,qq=123] https://example.com/a 猫猫！！"},
        ]
    )

    assert result.topic == "猫猫"


async def test_topic_drift_embedding_failure_falls_back_to_ngram() -> None:
    detector = TopicDriftDetector(similarity=_BrokenEmbedding())

    result = await detector.detect(
        [
            {"role": "user", "content_text": "猫猫可爱"},
            {"role": "user", "content_text": "猫猫可爱"},
            {"role": "user", "content_text": "猫猫可爱行为"},
        ]
    )

    assert result.drift_score < 0.6
    assert result.is_new_topic is False
