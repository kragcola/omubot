from __future__ import annotations

from services.llm.sentinel_registry import GuardrailHit, GuardrailResult, SentinelEntry, SentinelRegistry


def test_registry_strips_default_sentinels() -> None:
    registry = SentinelRegistry()

    result = registry.apply("你好 «图片: foo» 世界 «表情»")

    assert result.passed is True
    assert result.text == "你好  世界"
    assert [hit.name for hit in result.hits] == ["sentinel_image", "sentinel_face"]


def test_registry_redact_and_warn_are_supported() -> None:
    registry = SentinelRegistry()
    registry.register(SentinelEntry("secret", r"token-\d+", action="redact", replacement="[masked]"))
    registry.register(SentinelEntry("warn_only", r"注意我", action="warn"))

    result = registry.apply("token-42 注意我")

    assert result.passed is True
    assert result.text == "[masked] 注意我"
    assert {hit.name for hit in result.hits} >= {"secret", "warn_only"}


def test_registry_custom_rule_can_fail_closed_without_blocking() -> None:
    registry = SentinelRegistry()

    def _real_rule(text: str, _ctx):
        if "复读" not in text:
            return GuardrailResult(passed=True, text=text)

        hit = GuardrailHit(name="custom", severity="medium", action="rewrite")
        return GuardrailResult(passed=False, text="", hits=(hit,))

    registry.register_rule(_real_rule)

    result = registry.apply("这句像复读")

    assert result.passed is False
    assert result.blocked is False
    assert result.hits[0].name == "custom"


def test_registry_accumulates_multiple_rewrite_hits_before_fail_closed() -> None:
    registry = SentinelRegistry()

    def _first_rule(text: str, _ctx):
        if "复读" not in text:
            return GuardrailResult(passed=True, text=text)
        return GuardrailResult(
            passed=False,
            text="",
            hits=(GuardrailHit(name="first", severity="medium", action="rewrite"),),
        )

    def _second_rule(text: str, _ctx):
        if "复读" not in text:
            return GuardrailResult(passed=True, text=text)
        return GuardrailResult(
            passed=False,
            text="",
            hits=(GuardrailHit(name="second", severity="medium", action="rewrite"),),
        )

    registry.register_rule(_first_rule)
    registry.register_rule(_second_rule)

    result = registry.apply("这句像复读")

    assert result.passed is False
    assert result.blocked is False
    assert [hit.name for hit in result.hits] == ["first", "second"]
