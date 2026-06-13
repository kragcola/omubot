"""Tests for B1 topic-block attribution (services/group/topic_block.py)."""

from __future__ import annotations

from services.group.topic_block import TopicBlock, TopicBlockTracker


def _tracker() -> TopicBlockTracker:
    t = TopicBlockTracker()
    t.configure(attrib_recent_seconds=120.0, max_blocks=6)
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


# ── Wave L0 tests ──────────────────────────────────────────────────────


def test_observe_without_reply_to_message_id_still_works() -> None:
    """L0-2 backward compat: observe without reply_to_message_id (old signature)"""
    t = _tracker()
    g = "g1"
    blk = t.observe(g, message_id=1, speaker="u1", text="hello", now=0.0)
    assert blk is not None
    assert blk.representative_message_id() == 1


def test_reply_to_message_id_reverse_lookup_active_block() -> None:
    """L0-4: reply to a known message_id → O(1) reverse lookup, same block."""
    t = _tracker()
    g = "g1"
    # Block A: two messages.
    t.observe(g, message_id=1, speaker="u1", text="技术问题", now=0.0)
    t.observe(g, message_id=2, speaker="u2", text="我来答", now=1.0)
    # Block B: a chit-chat block starts.
    t.observe(g, message_id=3, speaker="u3", text="哈哈哈", now=2.0)
    t.observe(g, message_id=4, speaker="u4", text="确实", now=3.0)
    # u5 replies to message 1 (in block A). Even though block B is more recent,
    # the reverse lookup must attribute to block A.
    blk = t.observe(
        g, message_id=5, speaker="u5", text="回答",
        reply_to_sender_id="u1", reply_to_message_id=1, now=4.0,
    )
    assert 1 in blk.message_ids  # joined block A
    assert 3 not in blk.message_ids  # NOT the chit-chat block B


def test_reply_to_message_id_stale_block_guardrail_2() -> None:
    """L0-4 guardrail 2: reply to a stale block with consistent speaker → revive.
    Inconsistent speaker → fall through to cascade (don't blindly merge)."""
    t = _tracker()
    g = "g1"
    # Block A: u1 and u2 chatting (same-speaker continuation keeps them together).
    t.observe(g, message_id=1, speaker="u1", text="老话题开始聊聊", now=0.0)
    t.observe(g, message_id=2, speaker="u1", text="老话题继续聊聊", now=1.0)
    t.observe(g, message_id=3, speaker="u1", text="老话题再聊聊", now=2.0)
    # Long gap → block A goes stale (>300s).
    # Block B: new topic starts.
    t.observe(g, message_id=4, speaker="u2", text="新话题", now=400.0)
    t.observe(g, message_id=5, speaker="u3", text="新话题继续", now=401.0)
    # u1 replies to message 2 (in stale block A). reply_to_sender_id="u1" is
    # in block A's participants → consistent revival → attribute to block A.
    blk = t.observe(
        g, message_id=6, speaker="u4", text="回老话题",
        reply_to_sender_id="u1", reply_to_message_id=2, now=402.0,
    )
    assert 1 in blk.message_ids  # revived block A


def test_reply_to_message_id_broadcast_guardrail_2_rejected() -> None:
    """L0-4 guardrail 2: reply to old message with mismatched speaker context.
    The reply_to_sender_id is NOT in the found block → reframe/broadcast →
    fall through to cascade (open new block, don't blindly merge)."""
    t = _tracker()
    g = "g1"
    t.observe(g, message_id=1, speaker="u1", text="主题A", now=0.0)
    t.observe(g, message_id=2, speaker="u2", text="主题A继续", now=1.0)
    # Long gap — block A stale.
    t.observe(g, message_id=3, speaker="u3", text="主题B", now=400.0)
    # u4 replies to message 1 (in stale block A), but reply_to_sender_id="u5"
    # who was NEVER in block A → guardrail 2 rejects → falls through to open new
    # block OR join the active block B.
    blk = t.observe(
        g, message_id=4, speaker="u4", text="引用旧消息开新话题",
        reply_to_sender_id="u5", reply_to_message_id=1, now=401.0,
    )
    # Must NOT attribute to block A.
    assert 1 not in blk.message_ids
    assert 2 not in blk.message_ids


def test_anchor_speaker_from_edge_source() -> None:
    """L0-5: representative_speaker uses anchor_speaker from edge source,
    not arbitrary set ordering."""
    t = _tracker()
    g = "g1"
    t.observe(g, message_id=1, speaker="u1", text="核心问题", now=0.0)
    t.observe(g, message_id=2, speaker="u2", text="补充一句", now=1.0)
    # u3 replies to u1's message (edge source = u1).
    blk = t.observe(
        g, message_id=3, speaker="u3", text="回答",
        reply_to_sender_id="u1", reply_to_message_id=1, now=2.0,
    )
    # anchor_speaker should be u1 (the edge source), not u3 (last speaker).
    assert blk.anchor_speaker == "u1"
    assert blk.representative_speaker() == "u1"


def test_mark_bot_involved_with_block_id_d2_no_pollution() -> None:
    """L0-6 D2: mark_bot_involved(block_id=b1) marks only b1, not b2.
    Simulates: bot replies to b1, but b2 becomes most-active during generation."""
    t = _tracker()
    g = "g1"
    # Block A: bot was @-ed.
    t.observe(g, message_id=1, speaker="u1", text="姆姆", at_self=True, now=0.0)
    t.observe(g, message_id=2, speaker="u1", text="在吗", now=1.0)
    # Block B: becomes busier during bot's generation.
    t.observe(g, message_id=3, speaker="u2", text="大新闻!", now=2.0)
    t.observe(g, message_id=4, speaker="u3", text="真的假的", now=3.0)
    t.observe(g, message_id=5, speaker="u4", text="展开说说", now=4.0)
    # Bot replies — firing_block_id is block A's id.
    block_a = t.pick_anchor_block(g, now=0.5)  # bot-involved after @
    assert block_a is not None
    b1_id = block_a.block_id
    # Meanwhile block B is more active.
    block_b = max(t._blocks[g].values(), key=lambda b: b.last_active)
    assert block_b.block_id != b1_id
    # Mark bot involved in block A specifically.
    t.mark_bot_involved(g, now=5.0, block_id=b1_id)
    # Block A is bot_involved, block B is NOT.
    assert block_a.bot_involved is True
    assert block_b.bot_involved is False
    # pick_anchor_block should still prefer block A.
    anchor = t.pick_anchor_block(g, now=5.0)
    assert anchor is not None
    assert anchor.block_id == b1_id


def test_mark_bot_involved_without_block_id_fallback() -> None:
    """L0-6: mark_bot_involved without block_id falls back to most-active."""
    t = _tracker()
    g = "g1"
    t.observe(g, message_id=1, speaker="u1", text="hello", now=0.0)
    t.observe(g, message_id=2, speaker="u2", text="hi", now=1.0)
    t.mark_bot_involved(g, now=2.0)  # no block_id → most-active
    anchor = t.pick_anchor_block(g, now=2.0)
    assert anchor is not None
    assert anchor.bot_involved is True


# ── Wave L1 tests ──────────────────────────────────────────────────────


def test_same_speaker_soft_feature_can_switch_topic() -> None:
    """L1-2: same-speaker is now a soft feature (w=0.25), not a hard rule.
    A talks about topic A, then A talks about topic B — the second message
    should open a new block (or join the B block), NOT be glued to block A."""
    t = _tracker()
    g = "g1"
    # Block A: u1 talks about food.
    t.observe(g, message_id=1, speaker="u1", text="今晚吃什么好呢", now=0.0)
    t.observe(g, message_id=2, speaker="u2", text="火锅吧", now=1.0)
    # 120s later, u1 starts a completely different topic.
    b3 = t.observe(g, message_id=3, speaker="u1", text="话说那场比赛真是精彩", now=121.0)
    # With same-speaker as hard rule, this would have been glued to block A.
    # With soft scoring: spk_c=1.0 but time recency=0 and similarity ~0 → score
    # should be below floor → new block opened.
    assert b3 is not None
    # Must NOT be in block A (message 1).
    assert 1 not in b3.message_ids


def test_multi_topic_no_mega_block_collapse() -> None:
    """L1-3: three concurrent topics stay in separate blocks.
    Defect 1/5 fix: no mega-block collapse from same-speaker hard rule."""
    t = _tracker()
    g = "g1"
    # Topic A: game.
    t.observe(g, message_id=1, speaker="u1", text="游戏更新了", now=0.0)
    # Topic B: food.
    t.observe(g, message_id=2, speaker="u2", text="中午吃啥", now=1.0)
    # Topic C: tech.
    t.observe(g, message_id=3, speaker="u3", text="新框架发布了", now=2.0)
    # Continuations.
    b4 = t.observe(g, message_id=4, speaker="u1", text="更新了好多内容", now=3.0)
    b5 = t.observe(g, message_id=5, speaker="u2", text="点个外卖吧", now=4.0)
    b6 = t.observe(g, message_id=6, speaker="u3", text="性能提升很大", now=5.0)
    # Each continuation should join its own block (no cross-contamination).
    assert 1 in b4.message_ids  # u1 → game block
    assert 2 in b5.message_ids  # u2 → food block
    assert 3 in b6.message_ids  # u3 → tech block
    # All three blocks are distinct.
    assert len({b4.block_id, b5.block_id, b6.block_id}) == 3


def test_defect5_silent_not_broken_by_mega_block() -> None:
    """Defect 5: silent-overhearer protection was broken because mega-block
    collapse made the bot "involved" in every block. After L1 eliminates the
    same-speaker hard rule, the bot stays out of blocks it never joined."""
    t = _tracker()
    g = "g1"
    # Users talk about three different topics. Bot never @-ed.
    t.observe(g, message_id=1, speaker="u1", text="话题A", now=0.0)
    t.observe(g, message_id=2, speaker="u2", text="话题B", now=1.0)
    t.observe(g, message_id=3, speaker="u3", text="话题C", now=2.0)
    # Same speakers continue — without mega-block, these stay separate.
    t.observe(g, message_id=4, speaker="u1", text="A继续", now=3.0)
    t.observe(g, message_id=5, speaker="u2", text="B继续", now=4.0)
    # Bot is NOT involved in any block.
    assert t.pick_anchor_block(g, now=5.0) is None
    # Relaxed pick returns a block but bot_involved is False on all.
    relaxed = t.pick_anchor_block(g, now=5.0, require_bot_involved=False)
    assert relaxed is not None
    assert relaxed.bot_involved is False


# ── Wave L2 tests ──────────────────────────────────────────────────────


def test_activity_decay_evicts_stale_blocks() -> None:
    """L2-1: blocks with low activity are moved to reservoir, not active pool."""
    t = _tracker()
    g = "g1"
    t.observe(g, message_id=1, speaker="u1", text="一个消息", now=0.0)
    # After 10,000s (> 16min), activity should be near zero → reservoir.
    active = t._active(g, now=10_000.0)
    assert len(active) == 0  # stale block moved to reservoir


def test_reservoir_revival_via_reply_edge() -> None:
    """L2-2 guardrail 3: a reservoir block's msgid reverse index survives,
    so a reply can revive it back to active pool."""
    t = _tracker()
    g = "g1"
    t.observe(g, message_id=1, speaker="u1", text="老话题", now=0.0)
    t.observe(g, message_id=2, speaker="u2", text="继续老话题", now=1.0,
              reply_to_sender_id="u1")
    # Push to reservoir by querying far in the future.
    t._active(g, now=10_000.0)
    # Block should now be in reservoir.
    assert len(t._blocks.get(g, {})) == 0
    assert len(t._reservoir.get(g, {})) == 1
    # Reply revival: a message at t=10001 replying to msg 1.
    blk = t.observe(g, message_id=3, speaker="u3", text="回复老话题",
                    reply_to_sender_id="u1", reply_to_message_id=1, now=10_001.0)
    assert 1 in blk.message_ids  # revived block
    # Block is back in active pool.
    assert len(t._blocks.get(g, {})) >= 1
    assert blk.block_id not in t._reservoir.get(g, {})


def test_guardrail_3_msgid_index_survives_reservoir() -> None:
    """L2 guardrail 3: _msg_to_block entries are NOT deleted when a block
    moves to reservoir. Reply edge can still find the block."""
    t = _tracker()
    g = "g1"
    t.observe(g, message_id=1, speaker="u1", text="测试消息", now=0.0)
    # Push to reservoir.
    t._active(g, now=10_000.0)
    # msgid reverse index must still exist.
    group_idx = t._msg_to_block.get(g, {})
    assert group_idx.get(1) is not None  # msg 1 → block still mapped


def test_guardrail_1_candidate_pool_includes_reservoir() -> None:
    """L2 guardrail 1: _attribute candidates include reservoir blocks,
    so a low-activity block can still be attributed (PC recall)."""
    t = _tracker()
    g = "g1"
    # Block A: old conversation.
    t.observe(g, message_id=1, speaker="u1", text="老朋友话题开始聊", now=0.0)
    t.observe(g, message_id=2, speaker="u1", text="老朋友话题继续聊", now=1.0)
    # Push block A to reservoir.
    t._active(g, now=10_000.0)
    assert len(t._blocks.get(g, {})) == 0
    # u2 sends a similar message within the attribution window.
    # Use high similarity to overcome recency=0 (10000s old).
    blk = t.observe(g, message_id=3, speaker="u2", text="老朋友话题继续聊吧",
                    now=10_001.0)
    # Should revive block A (high similarity), not open a new one.
    assert 1 in blk.message_ids

