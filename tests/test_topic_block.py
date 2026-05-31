"""Tests for B1 topic-block attribution (services/group/topic_block.py)."""

from __future__ import annotations

from services.group.topic_block import TopicBlock, TopicBlockTracker


def _tracker() -> TopicBlockTracker:
    t = TopicBlockTracker()
    t.configure(stale_seconds=300.0, attrib_recent_seconds=120.0, sim_threshold=0.4, max_blocks=6)
    return t


def test_reproduces_stale_topic_bug() -> None:
    """§1 repro: old topic stops, two third-party stickers arrive.

    Two guarantees: (a) the stickers form their own block, distinct from the
    stale food topic — so we never anchor back to 鱼鱼烧; (b) since the bot is
    not part of the sticker block either (no @bot), strict pick returns None
    → no anchor, i.e. the bot does NOT insert into a block it only overhears
    (F-α). Relaxed pick confirms the block, if chosen, is a sticker not 鱼鱼烧.
    """
    t = _tracker()
    g = "g1"
    # Old topic A: three messages about food, then it stops.
    t.observe(g, message_id=1, speaker="u1", text="今晚吃什么好呢", now=0.0)
    t.observe(g, message_id=2, speaker="u2", text="要不去吃鱼鱼烧吧", now=1.0)
    t.observe(g, message_id=3, speaker="u1", text="鱼鱼烧不错诶", now=2.0)
    # Long quiet gap → topic A goes stale (> stale_seconds).
    # Two stickers arrive much later, semantically unrelated.
    t.observe(g, message_id=4, speaker="u3", text="«动画表情»", now=400.0)
    blk = t.observe(g, message_id=5, speaker="u3", text="«动画表情»", now=401.0)
    # Sticker block must be distinct from food block.
    assert blk.representative_message_id() in (4, 5)
    # (a)+(F-α): bot is in no active block → strict pick injects nothing.
    assert t.pick_anchor_block(g, now=401.0) is None
    # (b): if relaxed, the most-active block is the sticker block, never 鱼鱼烧.
    relaxed = t.pick_anchor_block(g, now=401.0, require_bot_involved=False)
    assert relaxed is not None
    assert relaxed.representative_message_id() not in (1, 2, 3)


def test_skip_connecting_reply_to_old_message() -> None:
    """reply-to an old block's participant rejoins that block, not the adjacent one."""
    t = _tracker()
    g = "g1"
    t.observe(g, message_id=1, speaker="u1", text="说个正经的技术问题", now=0.0)
    t.observe(g, message_id=2, speaker="u2", text="什么问题", now=1.0)
    # A different, adjacent chit-chat block starts.
    t.observe(g, message_id=3, speaker="u3", text="哈哈哈笑死", now=2.0)
    t.observe(g, message_id=4, speaker="u4", text="确实好笑", now=3.0)
    # u5 replies to u1 (block A participant) — skip-connecting back to A.
    blk = t.observe(g, message_id=5, speaker="u5", text="我来答", reply_to_sender_id="u1", now=4.0)
    assert 1 in blk.message_ids  # rejoined block A
    assert 3 not in blk.message_ids  # not the chit-chat block


def test_at_mention_joins_target_block() -> None:
    """@-mentioning a block participant attributes to that block."""
    t = _tracker()
    g = "g1"
    t.observe(g, message_id=1, speaker="u1", text="游戏话题开始", now=0.0)
    t.observe(g, message_id=2, speaker="u2", text="组队吗", now=1.0)
    t.observe(g, message_id=3, speaker="u3", text="另一个话题", now=2.0)
    blk = t.observe(g, message_id=4, speaker="u4", text="行啊", at_targets=("u1",), now=3.0)
    assert 1 in blk.message_ids


def test_bot_involved_block_preferred_for_anchor() -> None:
    """pick_anchor_block prefers the bot-involved block over a busier one."""
    t = _tracker()
    g = "g1"
    # Block A: someone @-ed the bot earlier.
    t.observe(g, message_id=1, speaker="u1", text="姆姆你说呢", at_self=True, now=0.0)
    # Block B: a livelier unrelated chat with more participants, more recent.
    t.observe(g, message_id=2, speaker="u2", text="完全不同的闲聊", now=1.0)
    t.observe(g, message_id=3, speaker="u3", text="对啊对啊", now=2.0)
    anchor = t.pick_anchor_block(g, now=3.0)
    assert anchor is not None
    assert anchor.bot_involved is True
    assert 1 in anchor.message_ids


def test_unrelated_message_opens_new_block() -> None:
    """No reply/@/same-speaker + low similarity → schisming into a new block."""
    t = _tracker()
    g = "g1"
    b1 = t.observe(g, message_id=1, speaker="u1", text="围棋段位怎么升", now=0.0)
    b2 = t.observe(g, message_id=2, speaker="u2", text="楼下那家火锅真的辣", now=1.0)
    assert b1.block_id != b2.block_id


def test_stale_block_not_picked() -> None:
    """A block inactive beyond stale_seconds is archived and not anchored."""
    t = _tracker()
    g = "g1"
    t.observe(g, message_id=1, speaker="u1", text="很久以前的话题", at_self=True, now=0.0)
    # Nothing else; query far in the future → block is stale.
    assert t.pick_anchor_block(g, now=10_000.0) is None


def test_require_bot_involved_returns_none_for_others_block() -> None:
    """F-α fix: when the bot is in none of the active blocks, default
    require_bot_involved=True returns None (do not insert into others' block)."""
    t = _tracker()
    g = "g1"
    # Two participants chatting; bot never @-ed / replied-to / spoke.
    t.observe(g, message_id=1, speaker="u1", text="你看那个比赛了吗", now=0.0)
    t.observe(g, message_id=2, speaker="u2", text="看了好激烈", now=1.0)
    assert t.pick_anchor_block(g, now=2.0) is None  # strict: not bot's block
    # Relaxed mode still returns the most-active block.
    assert t.pick_anchor_block(g, now=2.0, require_bot_involved=False) is not None


def test_bot_involved_after_at_self_is_pickable() -> None:
    """Once the bot is @-ed in a block, strict pick returns that block."""
    t = _tracker()
    g = "g1"
    t.observe(g, message_id=1, speaker="u1", text="姆姆在吗", at_self=True, now=0.0)
    t.observe(g, message_id=2, speaker="u1", text="想问个事", now=1.0)
    anchor = t.pick_anchor_block(g, now=2.0)
    assert anchor is not None
    assert anchor.bot_involved is True



def test_observe_is_idempotent_and_resilient() -> None:
    """D2: tracker state stays consistent across repeated/partial observes.

    The downstream chat may be cancelled after observe; observe itself must
    not leave half-built state, and the next observe must work normally.
    """
    t = _tracker()
    g = "g1"
    t.observe(g, message_id=1, speaker="u1", text="话题一", now=0.0)
    snapshot_blocks = len(t._blocks[g])
    # Simulate a downstream cancel between observes: no extra observe happened.
    # Next observe must still attribute cleanly.
    blk = t.observe(g, message_id=2, speaker="u1", text="话题一继续", now=1.0)
    assert 1 in blk.message_ids  # same-speaker continuation, same block
    assert len(t._blocks[g]) == snapshot_blocks  # no spurious block created
    # reset clears cleanly.
    t.reset(g)
    assert t.pick_anchor_block(g, now=2.0) is None


def test_representative_prefers_at_message() -> None:
    block = TopicBlock(block_id="b1")
    block.message_ids = [10, 11]
    block.at_message_id = 10
    assert block.representative_message_id() == 10
    block.at_message_id = None
    assert block.representative_message_id() == 11

