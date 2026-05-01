"""Tests for the group echo tracker."""

from __future__ import annotations

from dataclasses import dataclass

from plugins.echo import EchoTracker, build_echo_key


@dataclass
class _MockSeg:
    type: str
    data: dict


def _msg(*segs: _MockSeg) -> list[_MockSeg]:
    return list(segs)


def make_tracker(rand_vals: list[float] | None = None) -> EchoTracker:
    """Create an EchoTracker with a deterministic random stream."""
    if rand_vals is None:
        return EchoTracker()
    it = iter(rand_vals)

    def fake_rand() -> float:
        return next(it)

    return EchoTracker(rand=fake_rand)


# ---- basic echo ----

def test_same_text_3_times_echoes():
    t = make_tracker([0.5])  # rand >= 0.05 → normal echo
    assert t.process("g1", "hello", 100.0) is None  # 1
    assert t.process("g1", "hello", 101.0) is None  # 2
    assert t.process("g1", "hello", 102.0) == "hello"  # 3 → echo


def test_less_than_3_no_echo():
    t = make_tracker()
    t.process("g1", "hi", 100.0)
    assert t.process("g1", "hi", 101.0) is None


def test_expired_window_no_echo():
    t = make_tracker()
    t.process("g1", "yo", 100.0)
    t.process("g1", "yo", 200.0)
    assert t.process("g1", "yo", 500.0) is None  # 400s > 300s window


def test_window_reset_on_fresh_text():
    """New text resets the window, old expiry doesn't matter."""
    t = make_tracker([0.5])  # normal echo
    t.process("g1", "old", 100.0)
    t.process("g1", "old", 200.0)
    # Switch text — resets
    t.process("g1", "new", 500.0)
    t.process("g1", "new", 510.0)
    assert t.process("g1", "new", 520.0) == "new"


def test_different_text_resets():
    t = make_tracker()
    t.process("g1", "aaa", 100.0)
    t.process("g1", "aaa", 101.0)
    t.process("g1", "bbb", 102.0)  # reset
    t.process("g1", "bbb", 103.0)
    t.process("g1", "bbb", 104.0)
    # aaa count reset, won't echo
    t.process("g1", "aaa", 105.0)  # count=1 for aaa now
    assert t.process("g1", "aaa", 106.0) is None


def test_already_echoed_no_repeat():
    t = make_tracker([0.5])  # normal echo
    t.process("g1", "x", 100.0)
    t.process("g1", "x", 101.0)
    assert t.process("g1", "x", 102.0) == "x"  # echo
    # More of same text — already echoed, no more echo
    assert t.process("g1", "x", 103.0) is None
    assert t.process("g1", "x", 104.0) is None


def test_groups_isolated():
    t = make_tracker([0.5])  # normal echo
    t.process("g1", "hey", 100.0)
    t.process("g1", "hey", 101.0)
    t.process("g2", "hey", 100.0)
    # g1 has 3
    assert t.process("g1", "hey", 102.0) == "hey"
    # g2 only has 1
    assert t.process("g2", "hey", 101.0) is None


# ---- 5% break ----

def test_5_percent_break_triggers():
    """rand returns 0.01 (< 0.05) → break instead of echo."""
    t = make_tracker([0.01])
    t.process("g1", "echo", 100.0)
    t.process("g1", "echo", 101.0)
    result = t.process("g1", "echo", 102.0)
    assert result == "打断复读！"


def test_5_percent_no_break_normal():
    """rand returns 0.5 (>= 0.05) → normal echo."""
    t = make_tracker([0.5])
    t.process("g1", "echo", 100.0)
    t.process("g1", "echo", 101.0)
    result = t.process("g1", "echo", 102.0)
    assert result == "echo"


# ---- interrupt chain ----

def test_interrupt_chain():
    t = make_tracker()
    assert t.process("g1", "打断复读！", 100.0) == "打断复读！"
    assert t.process("g1", "打断复读！", 101.0) == "打断打断复读！"
    assert t.process("g1", "打断复读！", 102.0) == "打断打断打断复读！"


def test_interrupt_chain_resets_on_different_text():
    t = make_tracker()
    t.process("g1", "打断复读！", 100.0)  # chain=1
    t.process("g1", "打断复读！", 101.0)  # chain=2
    # Different text breaks the chain
    t.process("g1", "正常消息", 102.0)
    # Next interrupt starts fresh
    assert t.process("g1", "打断复读！", 103.0) == "打断复读！"


def test_interrupt_chain_per_group():
    t = make_tracker()
    t.process("g1", "打断复读！", 100.0)  # g1 chain=1
    assert t.process("g2", "打断复读！", 100.0) == "打断复读！"  # g2 chain=1


def test_break_then_interrupt_chain():
    """After 5% break triggers, users responding '打断复读！' continue chain from 0."""
    t = make_tracker([0.01, 0.5])  # first rand for break, second not used
    t.process("g1", "msg", 100.0)
    t.process("g1", "msg", 101.0)
    result = t.process("g1", "msg", 102.0)
    assert result == "打断复读！"  # 5% break
    # Someone then sends "打断复读！" — chain starts from 1
    assert t.process("g1", "打断复读！", 103.0) == "打断复读！"
    assert t.process("g1", "打断复读！", 104.0) == "打断打断复读！"


def test_same_text_after_interrupt_chain_still_echoes():
    """Interrupt chain doesn't prevent normal echo of other text."""
    t = make_tracker([0.5])  # normal echo
    t.process("g1", "打断复读！", 100.0)
    t.process("g1", "打断复读！", 101.0)
    # Now someone tries to echo normal text
    t.process("g1", "hehe", 102.0)
    t.process("g1", "hehe", 103.0)
    assert t.process("g1", "hehe", 104.0) == "hehe"


# ---- build_echo_key ----

def test_key_text_only():
    segs = _msg(_MockSeg("text", {"text": "hello"}))
    assert build_echo_key(segs) == "hello"


def test_key_image_sticker():
    """Stickers with same MD5 produce identical keys, enabling echo."""
    segs = _msg(_MockSeg("image", {"sub_type": 1, "file": "abc123", "url": "http://x/1"}))
    expected = "[image:1:abc123]"
    assert build_echo_key(segs) == expected
    # Same sticker re-sent (different URL but same file hash)
    segs2 = _msg(_MockSeg("image", {"sub_type": 1, "file": "abc123", "url": "http://x/2"}))
    assert build_echo_key(segs2) == expected


def test_key_image_regular():
    segs = _msg(_MockSeg("image", {"sub_type": 0, "file": "def456"}))
    assert build_echo_key(segs) == "[image:0:def456]"


def test_key_face():
    segs = _msg(_MockSeg("face", {"id": "178"}))
    assert build_echo_key(segs) == "[face:178]"


def test_key_at():
    segs = _msg(_MockSeg("at", {"qq": "123456"}))
    assert build_echo_key(segs) == "[at:123456]"


def test_key_mixed_text_and_image():
    segs = _msg(
        _MockSeg("text", {"text": "看这个"}),
        _MockSeg("image", {"sub_type": 1, "file": "stk001"}),
    )
    assert build_echo_key(segs) == "看这个[image:1:stk001]"


def test_key_empty():
    assert build_echo_key([]) == ""


def test_key_unknown_type():
    segs = _msg(_MockSeg("video", {}))
    assert build_echo_key(segs) == "[video]"


def test_key_image_no_file_hash():
    """Image without file hash still produces a key (weaker, matches any
    same-type sticker without hash)."""
    segs = _msg(_MockSeg("image", {"sub_type": "1"}))
    assert build_echo_key(segs) == "[image:1:]"


def test_key_missing_data():
    """Segment without .data attribute should not crash."""
    segs = _msg(_MockSeg("text", {}))
    assert build_echo_key(segs) == ""
