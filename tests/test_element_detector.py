
from kernel.config import ElementRule
from plugins.element_detector import ElementDetector


def test_detect_simple_match() -> None:
    rules = [ElementRule(pattern="早安|早上好", reply="哦哈哟，{nickname}！")]
    detector = ElementDetector(rules)
    result = detector.detect("大家早上好呀", nickname="小明", user_id="123")
    assert result is not None
    assert result.reply_template == "哦哈哟，小明！"
    assert result.use_llm is False


def test_detect_named_groups() -> None:
    rules = [ElementRule(pattern=r"叫我\s*(?P<name>.+)", reply="好的{nickname}，以后叫你{name}！")]
    detector = ElementDetector(rules)
    result = detector.detect("以后叫我小可爱吧", nickname="小红", user_id="456")
    assert result is not None
    assert "小可爱" in result.reply_template


def test_detect_no_match() -> None:
    rules = [ElementRule(pattern="晚安", reply="晚安～")]
    detector = ElementDetector(rules)
    result = detector.detect("大家早上好", nickname="小明", user_id="123")
    assert result is None


def test_detect_first_match_wins() -> None:
    rules = [
        ElementRule(pattern="Hello", reply="first"),
        ElementRule(pattern="Hello", reply="second"),
    ]
    detector = ElementDetector(rules)
    result = detector.detect("Hello world", nickname="x", user_id="1")
    assert result is not None
    assert result.reply_template == "first"


def test_detect_empty_rules() -> None:
    detector = ElementDetector([])
    assert detector.detect("anything", nickname="x", user_id="1") is None


def test_detect_invalid_pattern_skipped() -> None:
    rules = [
        ElementRule(pattern="***invalid[[", reply="bad"),
        ElementRule(pattern="hello", reply="good"),
    ]
    detector = ElementDetector(rules)
    result = detector.detect("hello", nickname="x", user_id="1")
    assert result is not None
    assert result.reply_template == "good"


def test_detect_match_placeholder() -> None:
    rules = [ElementRule(pattern=r"hello", reply="{nickname} saw: {match}")]
    detector = ElementDetector(rules)
    result = detector.detect("hello world", nickname="Alice", user_id="99")
    assert result is not None
    assert result.reply_template == "Alice saw: hello"


def test_detect_use_llm_flag() -> None:
    rules = [ElementRule(pattern="test", reply="generate something", use_llm=True)]
    detector = ElementDetector(rules)
    result = detector.detect("test message", nickname="x", user_id="1")
    assert result is not None
    assert result.use_llm is True
    assert result.reply_template == "generate something"


def test_detect_named_group_with_llm() -> None:
    rules = [ElementRule(
        pattern=r"(?P<content>.+?)是这样的",
        reply="用户说「{match}」。生成反差回复。",
        use_llm=True,
    )]
    detector = ElementDetector(rules)
    result = detector.detect("前线的战士是这样的", nickname="小红", user_id="456")
    assert result is not None
    assert result.use_llm is True
    assert "前线的战士是这样的" in result.reply_template
