"""Tests for _build_group_messages: turns + pending merge + cache stability."""

from __future__ import annotations

from services.llm.client import LLMClient
from services.memory.timeline import GroupTimeline, merge_user_contents


class TestBuildGroupMessages:
    """Exercise GroupTimeline's turns/pending model for _build_group_messages."""

    def test_turns_are_included_directly(self) -> None:
        """Turns from timeline are extended directly into messages."""
        tl = GroupTimeline()
        tl.add("g1", role="user", content="hello", speaker="Alice(1)")
        tl.add("g1", role="assistant", content="hi!")

        turns = tl.get_turns("g1")
        assert len(turns) == 2
        assert turns[0]["role"] == "user"
        assert turns[1]["role"] == "assistant"
        assert turns[1]["content"] == "hi!"

    def test_pending_merged_as_tail(self) -> None:
        """Pending user messages are merged as the last message."""
        tl = GroupTimeline()
        tl.add("g1", role="user", content="hello", speaker="Alice(1)")
        tl.add("g1", role="assistant", content="hi!")
        tl.add("g1", role="user", content="follow-up 1", speaker="Alice(1)")
        tl.add("g1", role="user", content="follow-up 2", speaker="Bob(2)")

        turns = tl.get_turns("g1")
        pending = tl.get_pending("g1")
        assert len(turns) == 2  # user + assistant
        assert len(pending) == 2  # two pending user messages

        # Merge pending like _build_group_messages does
        merged_content = merge_user_contents(pending)
        assert isinstance(merged_content, str)
        assert "Alice(1): follow-up 1" in merged_content
        assert "Bob(2): follow-up 2" in merged_content

    def test_prefix_stability_across_appends(self) -> None:
        """Core cache invariant: turns prefix doesn't change when pending grows."""
        tl = GroupTimeline()
        tl.add("g1", role="user", content="Q1", speaker="Alice(1)")
        tl.add("g1", role="assistant", content="A1")

        # Snapshot turns before adding pending
        turns_before = list(tl.get_turns("g1"))

        # Add pending messages
        tl.add("g1", role="user", content="Q2a", speaker="Alice(1)")
        tl.add("g1", role="user", content="Q2b", speaker="Bob(2)")

        # Turns must be byte-identical
        turns_after = list(tl.get_turns("g1"))
        assert len(turns_before) == len(turns_after)
        for before, after in zip(turns_before, turns_after, strict=True):
            assert before is after  # same object identity

    def test_prefix_stability_after_flush(self) -> None:
        """After flush, old turns are the exact same objects (identity check)."""
        tl = GroupTimeline()
        tl.add("g1", role="user", content="Q1", speaker="Alice(1)")
        tl.add("g1", role="assistant", content="A1")

        # Capture references to existing turns
        turn0 = tl.get_turns("g1")[0]
        turn1 = tl.get_turns("g1")[1]

        # Add more messages and flush (assistant triggers flush)
        tl.add("g1", role="user", content="Q2", speaker="Bob(2)")
        tl.add("g1", role="assistant", content="A2")

        turns = tl.get_turns("g1")
        assert len(turns) == 4
        # Old turns remain the same objects
        assert turns[0] is turn0
        assert turns[1] is turn1

    def test_empty_group_returns_empty(self) -> None:
        """Non-existent group produces empty turns and pending."""
        tl = GroupTimeline()
        assert len(tl.get_turns("nonexistent")) == 0
        assert len(tl.get_pending("nonexistent")) == 0

    def test_pending_only_produces_single_message(self) -> None:
        """When there are only pending messages (no turns), merge produces one entry."""
        tl = GroupTimeline()
        tl.add("g1", role="user", content="msg1", speaker="Alice(1)")
        tl.add("g1", role="user", content="msg2", speaker="Bob(2)")

        turns = tl.get_turns("g1")
        pending = tl.get_pending("g1")
        assert len(turns) == 0
        assert len(pending) == 2

        merged = merge_user_contents(pending)
        assert isinstance(merged, str)
        assert "Alice(1): msg1" in merged
        assert "Bob(2): msg2" in merged

    def test_summary_precedes_turns(self) -> None:
        """Summary should be present in the timeline state for building messages."""
        tl = GroupTimeline()
        tl.add("g1", role="user", content="Q", speaker="A(1)")
        tl.add("g1", role="assistant", content="A")
        tl.compact("g1", split=0, new_summary="this is a summary")

        assert tl.get_summary("g1") == "this is a summary"
        turns = tl.get_turns("g1")
        assert len(turns) == 2  # split=0 means no truncation

    def test_bare_this_is_who_cannot_inherit_a_historical_image_identity(self) -> None:
        """A new image-deictic question without a current image must fail closed."""
        tl = GroupTimeline()
        tl.add(
            "g1",
            role="user",
            speaker="Alice(1)",
            content=[
                {"type": "text", "text": "«图片1: 草薙宁宁»"},
                {"type": "image_ref", "path": "/tmp/nene.jpg", "media_type": "image/jpeg"},
            ],
        )
        tl.add("g1", role="assistant", content="这是草薙宁宁。")
        tl.add("g1", role="user", speaker="Bob(2)", content="这是谁")

        client = object.__new__(LLMClient)
        client._timeline = tl
        messages = client._build_group_messages("g1")

        assert len(messages) == 1
        tail = messages[-1]["content"]
        assert isinstance(tail, str)
        assert "本轮待处理消息没有图片或引用图片" in tail
        assert "不得沿用历史图片" in tail
        assert tail.endswith("Bob(2): 这是谁")
        assert not any(
            isinstance(message.get("content"), list)
            and any(block.get("type") == "image_ref" for block in message["content"])
            for message in messages[:-1]
        )

    def test_current_image_identity_query_prioritizes_current_visual_evidence(self) -> None:
        """Current image pixels/evidence outrank conflicting names in finalized history."""
        tl = GroupTimeline()
        tl.add(
            "g1",
            role="user",
            speaker="Alice(1)",
            content=[
                {"type": "text", "text": "刚才是草薙宁宁«图片1: 草薙宁宁»"},
                {"type": "image_ref", "path": "/tmp/nene.jpg", "media_type": "image/jpeg"},
            ],
        )
        tl.add("g1", role="assistant", content="嗯，是宁宁。")
        tl.add(
            "g1",
            role="user",
            speaker="Bob(2)",
            content=[
                {"type": "text", "text": "这是谁«图片1: 初音未来»"},
                {"type": "image_ref", "path": "/tmp/miku.jpg", "media_type": "image/jpeg"},
            ],
        )

        client = object.__new__(LLMClient)
        client._timeline = tl
        messages = client._build_group_messages("g1")

        assert len(messages) == 1
        tail = messages[-1]["content"]
        assert isinstance(tail, list)
        assert tail[0]["type"] == "text"
        assert "只绑定本轮待处理消息中的图片" in tail[0]["text"]
        assert "历史人物名不能覆盖本轮视觉证据" in tail[0]["text"]
        assert "不确定" in tail[0]["text"] or "证据不足" in tail[0]["text"]
        assert "低置信候选" not in tail[0]["text"]
        assert any(block.get("type") == "image_ref" for block in tail)
        image_paths = [
            block["path"]
            for message in messages
            if isinstance(message.get("content"), list)
            for block in message["content"]
            if block.get("type") == "image_ref"
        ]
        assert image_paths == ["/tmp/miku.jpg"]

    def test_image_then_question_in_same_pending_batch_is_current_visual(self) -> None:
        """A separately posted image remains current until the pending batch is flushed."""
        tl = GroupTimeline()
        tl.add(
            "g1",
            role="user",
            speaker="Bob(2)",
            content=[
                {"type": "text", "text": "«图片1: 初音未来»"},
                {"type": "image_ref", "path": "/tmp/miku.jpg", "media_type": "image/jpeg"},
            ],
        )
        tl.add("g1", role="user", speaker="Bob(2)", content="这是谁")

        client = object.__new__(LLMClient)
        client._timeline = tl
        messages = client._build_group_messages("g1")

        tail = messages[-1]["content"]
        assert isinstance(tail, list)
        assert "只绑定本轮待处理消息中的图片" in tail[0]["text"]
        assert any(block.get("type") == "image_ref" for block in tail)

    def test_latest_pending_question_controls_visual_mode(self) -> None:
        """Earlier chatter in the same pending batch must not break bare-query detection."""
        tl = GroupTimeline()
        tl.add("g1", role="user", speaker="Alice(1)", content="旧话题")
        tl.add("g1", role="assistant", content="旧回复")
        tl.add("g1", role="user", speaker="Bob(2)", content="等等")
        tl.add("g1", role="user", speaker="Bob(2)", content="这是谁")

        client = object.__new__(LLMClient)
        client._timeline = tl
        messages = client._build_group_messages("g1")

        assert len(messages) == 1
        tail = messages[0]["content"]
        assert isinstance(tail, str)
        assert "本轮待处理消息没有图片或引用图片" in tail
        assert "Bob(2): 等等" in tail
        assert "Bob(2): 这是谁" in tail

    def test_named_person_question_with_unrelated_image_is_not_rebound_to_image(self) -> None:
        tl = GroupTimeline()
        tl.add("g1", role="user", speaker="Alice(1)", content="旧话题")
        tl.add("g1", role="assistant", content="旧回复")
        tl.add(
            "g1",
            role="user",
            speaker="Bob(2)",
            content=[
                {"type": "text", "text": "草薙宁宁是谁«图片1: 一张无关风景图»"},
                {"type": "image_ref", "path": "/tmp/scenery.jpg", "media_type": "image/jpeg"},
            ],
        )

        client = object.__new__(LLMClient)
        client._timeline = tl
        messages = client._build_group_messages("g1")

        assert len(messages) == 3
        tail = messages[-1]["content"]
        assert isinstance(tail, list)
        assert "当前视觉指代约束" not in tail[0]["text"]
        assert tail[0]["text"].startswith("Bob(2): 草薙宁宁是谁")

    def test_quoted_image_pronoun_question_binds_to_quoted_image(self) -> None:
        tl = GroupTimeline()
        tl.add(
            "g1",
            role="user",
            speaker="Bob(2)",
            content=[
                {
                    "type": "text",
                    "text": (
                        "[QUOTED_MSG sender_id=384801062 sender_name=我]\n"
                        "[图片: 一个托着下巴思考的女孩]\n"
                        "[/QUOTED_MSG] 他是谁。"
                    ),
                },
                {"type": "image_ref", "path": "/tmp/quoted.jpg", "media_type": "image/jpeg"},
            ],
        )

        client = object.__new__(LLMClient)
        client._timeline = tl
        messages = client._build_group_messages("g1")

        assert len(messages) == 1
        tail = messages[0]["content"]
        assert isinstance(tail, list)
        assert "只绑定本轮待处理消息中的图片" in tail[0]["text"]
        assert any(block.get("path") == "/tmp/quoted.jpg" for block in tail)

    def test_pronoun_question_without_image_keeps_textual_context(self) -> None:
        """Without visual evidence, '他是谁' may legitimately refer to prior text."""
        tl = GroupTimeline()
        tl.add("g1", role="user", speaker="Alice(1)", content="刚才提到张三")
        tl.add("g1", role="assistant", content="嗯，张三也在。")
        tl.add("g1", role="user", speaker="Bob(2)", content="他是谁")

        client = object.__new__(LLMClient)
        client._timeline = tl
        messages = client._build_group_messages("g1")

        assert len(messages) == 3
        assert messages[-1]["content"] == "Bob(2): 他是谁"

    def test_named_person_question_does_not_trigger_visual_grounding(self) -> None:
        tl = GroupTimeline()
        tl.add("g1", role="user", speaker="Bob(2)", content="草薙宁宁是谁")

        client = object.__new__(LLMClient)
        client._timeline = tl
        messages = client._build_group_messages("g1")

        assert messages[-1]["content"] == "«msg:None» Bob(2): 草薙宁宁是谁".replace("«msg:None» ", "")
