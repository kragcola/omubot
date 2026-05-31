"""Regression tests for kernel.router._last_assistant_replied_to_user.

The function decides whether the latest assistant turn was a reply to the
*current* user, which is the entry condition for the semantic reply gate
(directed follow-up). Because GroupTimeline lazy-flushes pending messages —
merging every user's messages during a bot-silence window into ONE user turn —
the previous all()-over-lines logic mis-fired whenever anyone else spoke in the
gap, suppressing legitimate two-person follow-ups (see 2026-05-30 incident in
group "烤").
"""

from __future__ import annotations

from kernel.router import _last_assistant_replied_to_user
from services.memory.timeline import GroupTimeline

GROUP = "test_group"
ME = "10001"
OTHER = "20002"


def _build(turns: list[tuple[str, str, str | None]]) -> GroupTimeline:
    """Construct a timeline from (role, content, speaker) tuples.

    user messages buffer to pending; an assistant message flushes them into one
    merged user turn — exactly mirroring production lazy-flush semantics.
    """
    tl = GroupTimeline()
    for role, content, speaker in turns:
        if role == "user":
            tl.add(GROUP, role="user", content=content, speaker=speaker)
        else:
            tl.add(GROUP, role="assistant", content=content)
    return tl


def test_single_user_followup_returns_true() -> None:
    """Plain two-person Q&A: assistant answered me, I follow up → True."""
    tl = _build([
        ("user", "在吗", f"我({ME})"),
        ("assistant", "在的", None),
    ])
    assert _last_assistant_replied_to_user(tl, GROUP, ME, within_s=180.0) is True


def test_multi_user_merge_followup_returns_true() -> None:
    """The incident: someone else spoke during bot silence, their message got
    merged into the same user turn as mine, but the assistant was answering me
    (last real line). Must still return True."""
    tl = _build([
        ("user", "随便聊聊", f"丛非凡({OTHER})"),
        ("user", "你为什么叫我管理员", f"我({ME})"),
        ("assistant", "因为你有管理权限", None),
    ])
    assert _last_assistant_replied_to_user(tl, GROUP, ME, within_s=180.0) is True


def test_last_line_belongs_to_other_returns_false() -> None:
    """If the triggering (last) real user line is someone else's, the assistant
    was not replying to me → False, so I don't get a free follow-up pass."""
    tl = _build([
        ("user", "你为什么叫我管理员", f"我({ME})"),
        ("user", "那我呢", f"丛非凡({OTHER})"),
        ("assistant", "你也是常驻群友", None),
    ])
    assert _last_assistant_replied_to_user(tl, GROUP, ME, within_s=180.0) is False


def test_outside_window_returns_false() -> None:
    """Assistant reply is older than the follow-up window → False."""
    tl = _build([
        ("user", "在吗", f"我({ME})"),
        ("assistant", "在的", None),
    ])
    assert _last_assistant_replied_to_user(tl, GROUP, ME, within_s=0.0) is False


def test_trigger_marker_line_is_ignored() -> None:
    """A «触发原因: ...» marker in the merged turn must not be treated as a
    user line; the real last line still decides ownership."""
    tl = GroupTimeline()
    tl.add_pending_trigger(GROUP, reason="有人@了你", target_user_id=ME)
    tl.add(GROUP, role="user", content="你为什么叫我管理员", speaker=f"我({ME})")
    tl.add(GROUP, role="assistant", content="因为你有管理权限")
    assert _last_assistant_replied_to_user(tl, GROUP, ME, within_s=180.0) is True


def test_no_assistant_turn_returns_false() -> None:
    """No finalized assistant turn at all → False."""
    tl = GroupTimeline()
    tl.add(GROUP, role="user", content="在吗", speaker=f"我({ME})")
    assert _last_assistant_replied_to_user(tl, GROUP, ME, within_s=180.0) is False
