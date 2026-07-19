"""RED/GREEN matrix for Query-Aware Retrieval Planner v1 (qa_rg_v1).

Contract: pure deterministic planner; post-RRF type_caps + pack budget only;
never mutates retrieve_mode; max two closed needs; disabled/ordinary = identity.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from services.context.packing import ContextBudget


def _plan(**kwargs):
    from services.context.query_plan import plan_query_aware_retrieval

    return plan_query_aware_retrieval(**kwargs)


def _base_budget() -> ContextBudget:
    return ContextBudget(
        total_tokens=6000,
        memory_tokens=1500,
        doc_tokens=2500,
        graph_tokens=1700,
        buffer_tokens=300,
    )


def _assert_content_capacity(plan, budget: ContextBudget) -> None:
    """Non-identity profiles must keep buckets inside content capacity."""
    capacity = max(0, budget.total_tokens - budget.buffer_tokens)
    b = plan.budget
    assert b.total_tokens == budget.total_tokens
    assert b.buffer_tokens == budget.buffer_tokens
    for val in (b.memory_tokens, b.doc_tokens, b.graph_tokens):
        assert 0 <= val <= capacity
    assert b.memory_tokens + b.doc_tokens + b.graph_tokens <= capacity


# ---------------------------------------------------------------------------
# Ordinary identity + core profiles
# ---------------------------------------------------------------------------


def test_ordinary_fact_is_exact_identity_profile() -> None:
    budget = _base_budget()
    plan = _plan(
        query="雾青控制台是什么",
        current_message="雾青控制台是什么",
        retrieve_mode="hybrid",
        max_hits=5,
        max_doc_hits=3,
        budget=budget,
        enabled=True,
    )
    assert plan.version == "qa_rg_v1"
    assert plan.enabled is True
    assert plan.identity is True
    assert plan.needs == ("ordinary_fact",)
    assert plan.profile_id == "ordinary_identity"
    assert plan.retrieve_mode == "hybrid"
    assert plan.top_k == 5
    assert plan.type_caps == {"doc_chunk": 3}
    assert plan.budget is budget or plan.budget == budget
    assert plan.budget.total_tokens == 6000
    assert plan.budget.buffer_tokens == 300
    assert "identity" in plan.reason_codes or "ordinary" in " ".join(plan.reason_codes)


def test_preference_favors_memory_and_limits_doc() -> None:
    budget = _base_budget()
    plan = _plan(
        query="我喜欢吃什么",
        current_message="我喜欢吃什么",
        retrieve_mode="hybrid",
        max_hits=5,
        max_doc_hits=3,
        budget=budget,
        enabled=True,
    )
    assert "preference" in plan.needs
    assert len(plan.needs) <= 2
    assert plan.identity is False
    assert plan.retrieve_mode == "hybrid"
    assert plan.top_k == 5
    assert plan.type_caps.get("doc_chunk", 0) <= 1
    _assert_content_capacity(plan, budget)
    assert plan.budget.memory_tokens >= plan.budget.doc_tokens
    assert plan.profile_id.startswith("preference")


def test_temporal_current_memory_profile() -> None:
    budget = _base_budget()
    plan = _plan(
        query="我现在住在哪里",
        current_message="我现在住在哪里",
        retrieve_mode="fact",
        max_hits=5,
        max_doc_hits=3,
        budget=budget,
        enabled=True,
    )
    assert "temporal_current" in plan.needs
    assert plan.identity is False
    assert plan.retrieve_mode == "fact"
    assert plan.type_caps.get("doc_chunk", 0) <= 1
    _assert_content_capacity(plan, budget)
    assert plan.profile_id.startswith("temporal_current")


def test_temporal_earlier_memory_profile() -> None:
    budget = _base_budget()
    plan = _plan(
        query="我以前住在哪里",
        current_message="我以前住在哪里",
        retrieve_mode="hybrid",
        max_hits=5,
        max_doc_hits=3,
        budget=budget,
        enabled=True,
    )
    assert "temporal_earlier" in plan.needs
    assert plan.identity is False
    assert plan.retrieve_mode == "hybrid"
    assert plan.type_caps.get("doc_chunk", 0) <= 1
    _assert_content_capacity(plan, budget)
    assert plan.profile_id.startswith("temporal_earlier")


def test_premise_check_memory_profile() -> None:
    budget = _base_budget()
    plan = _plan(
        query="我不是还住杭州吗",
        current_message="我不是还住杭州吗",
        retrieve_mode="hybrid",
        max_hits=5,
        max_doc_hits=3,
        budget=budget,
        enabled=True,
    )
    assert "premise_check" in plan.needs
    assert plan.identity is False
    assert plan.type_caps.get("doc_chunk", 0) <= 1
    _assert_content_capacity(plan, budget)
    assert plan.profile_id.startswith("premise")


def test_relation_multihop_favors_graph_budget() -> None:
    budget = _base_budget()
    plan = _plan(
        query="小明和雾青是什么关系",
        current_message="小明和雾青是什么关系",
        retrieve_mode="hybrid",
        max_hits=5,
        max_doc_hits=3,
        budget=budget,
        enabled=True,
    )
    assert "relation_multihop" in plan.needs
    assert plan.identity is False
    assert plan.retrieve_mode == "hybrid"
    _assert_content_capacity(plan, budget)
    assert plan.budget.graph_tokens >= plan.budget.memory_tokens
    assert plan.profile_id.startswith("relation")
    if "graph_fact" in plan.type_caps:
        assert 1 <= plan.type_caps["graph_fact"] <= 5


def test_doc_grounding_honors_max_doc_hits() -> None:
    budget = _base_budget()
    plan = _plan(
        query="根据部署手册 Docker Compose 怎么配",
        current_message="根据部署手册 Docker Compose 怎么配",
        retrieve_mode="doc",
        max_hits=5,
        max_doc_hits=3,
        budget=budget,
        enabled=True,
    )
    assert "doc_grounding" in plan.needs
    assert plan.identity is False
    assert plan.retrieve_mode == "doc"
    assert plan.type_caps.get("doc_chunk", 0) == 3
    assert plan.type_caps["doc_chunk"] <= 3
    _assert_content_capacity(plan, budget)
    assert plan.budget.doc_tokens >= plan.budget.memory_tokens
    assert plan.profile_id.startswith("doc_grounding")


def test_broad_recall_clamps_hard_caps_keeps_hybrid() -> None:
    budget = _base_budget()
    plan = _plan(
        query="关于我的记忆都有哪些",
        current_message="关于我的记忆都有哪些",
        retrieve_mode="hybrid",
        max_hits=5,
        max_doc_hits=3,
        budget=budget,
        enabled=True,
    )
    assert "broad_recall" in plan.needs
    assert plan.retrieve_mode == "hybrid"
    assert plan.top_k == 5
    assert plan.type_caps.get("doc_chunk", 3) <= 3
    assert all(v >= 0 for v in plan.type_caps.values())
    for key, val in plan.type_caps.items():
        if key == "doc_chunk":
            assert val <= 3
        else:
            assert val <= 5
    _assert_content_capacity(plan, budget)
    assert plan.profile_id.startswith("broad_recall")


# ---------------------------------------------------------------------------
# Important #1/#2: English word-boundary + Chinese weak-marker false positives
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "I don't know where I live",
        "knowledge base lookup",
        "unknown user",
        "acknowledge receipt",
        "manually configure docker",
        "请全部写完作业",
        "列出安装步骤",
        "我已经习惯了这里",
        "你认识路吗",
        "这个问题和那个相关吗",
        "依据什么做判断",
        "朋友们都来了",
        "当前版本号",
        "我仍然在这里",
        "根据我的情况怎么办",
        "关于我的问题",
        "我今天想吃什么",
    ],
)
def test_ordinary_identity_no_false_positive_markers(query: str) -> None:
    """Weak/substring markers must not promote ordinary queries to specialized profiles."""
    budget = _base_budget()
    plan = _plan(
        query=query,
        current_message=query,
        retrieve_mode="hybrid",
        max_hits=5,
        max_doc_hits=3,
        budget=budget,
        enabled=True,
    )
    assert plan.identity is True, f"{query!r} -> {plan.profile_id} needs={plan.needs}"
    assert plan.profile_id == "ordinary_identity"
    assert plan.needs == ("ordinary_fact",)
    assert plan.budget == budget


@pytest.mark.parametrize(
    "query,expected_need",
    [
        ("where do I live now", "temporal_current"),
        ("the manual says restart", "doc_grounding"),
        ("what is Alice's relationship to Bob", "relation_multihop"),
        ("我喜欢吃什么", "preference"),
        ("我以前住在哪里", "temporal_earlier"),
        ("我不是还住杭州吗", "premise_check"),
        ("小明和雾青是什么关系", "relation_multihop"),
        ("根据部署手册 Docker Compose 怎么配", "doc_grounding"),
        ("关于我的记忆都有哪些", "broad_recall"),
    ],
)
def test_strong_markers_still_classify(query: str, expected_need: str) -> None:
    budget = _base_budget()
    plan = _plan(
        query=query,
        current_message=query,
        retrieve_mode="hybrid",
        max_hits=5,
        max_doc_hits=3,
        budget=budget,
        enabled=True,
    )
    assert expected_need in plan.needs, f"{query!r} -> {plan.needs}"
    assert plan.identity is False


# ---------------------------------------------------------------------------
# Important #3: budget capacity normalization
# ---------------------------------------------------------------------------


def test_custom_small_budget_buckets_within_content_capacity() -> None:
    budget = ContextBudget(
        total_tokens=1000,
        memory_tokens=800,
        doc_tokens=800,
        graph_tokens=800,
        buffer_tokens=200,
    )
    capacity = 800
    for q in (
        "我喜欢吃什么",
        "我现在住在哪里",
        "我以前住在哪里",
        "我不是还住杭州吗",
        "小明和雾青是什么关系",
        "根据部署手册怎么配",
        "关于我的记忆都有哪些",
    ):
        plan = _plan(
            query=q,
            current_message=q,
            retrieve_mode="hybrid",
            max_hits=5,
            max_doc_hits=3,
            budget=budget,
            enabled=True,
        )
        assert plan.identity is False
        b = plan.budget
        assert b.total_tokens == 1000
        assert b.buffer_tokens == 200
        assert 0 <= b.memory_tokens <= capacity
        assert 0 <= b.doc_tokens <= capacity
        assert 0 <= b.graph_tokens <= capacity
        assert b.memory_tokens + b.doc_tokens + b.graph_tokens <= capacity


def test_broad_recall_normalizes_configured_proportions() -> None:
    budget = ContextBudget(
        total_tokens=1000,
        memory_tokens=100,
        doc_tokens=200,
        graph_tokens=100,
        buffer_tokens=200,
    )
    plan = _plan(
        query="关于我的记忆都有哪些",
        current_message="关于我的记忆都有哪些",
        retrieve_mode="hybrid",
        max_hits=5,
        max_doc_hits=3,
        budget=budget,
        enabled=True,
    )
    assert "broad_recall" in plan.needs
    _assert_content_capacity(plan, budget)
    # Configured proportions 100:200:100 → 1:2:1 into capacity 800 → 200:400:200
    assert plan.budget.memory_tokens == 200
    assert plan.budget.doc_tokens == 400
    assert plan.budget.graph_tokens == 200


def test_buffer_gte_total_zeros_non_identity_buckets() -> None:
    budget = ContextBudget(
        total_tokens=500,
        memory_tokens=200,
        doc_tokens=200,
        graph_tokens=200,
        buffer_tokens=500,
    )
    plan = _plan(
        query="我喜欢吃什么",
        current_message="我喜欢吃什么",
        retrieve_mode="hybrid",
        max_hits=5,
        max_doc_hits=3,
        budget=budget,
        enabled=True,
    )
    assert plan.identity is False
    assert plan.budget.total_tokens == 500
    assert plan.budget.buffer_tokens == 500
    assert plan.budget.memory_tokens == 0
    assert plan.budget.doc_tokens == 0
    assert plan.budget.graph_tokens == 0


def test_budget_ratio_rounding_deterministic() -> None:
    """Exact integer rounding is stable across calls (largest-remainder)."""
    budget = ContextBudget(
        total_tokens=1001,
        memory_tokens=1,
        doc_tokens=1,
        graph_tokens=1,
        buffer_tokens=2,
    )
    # content_capacity = 999; preference 60/15/25
    plans = [
        _plan(
            query="我喜欢吃什么",
            current_message="我喜欢吃什么",
            retrieve_mode="hybrid",
            max_hits=5,
            max_doc_hits=3,
            budget=budget,
            enabled=True,
        )
        for _ in range(3)
    ]
    first = plans[0].budget
    for p in plans[1:]:
        assert p.budget == first
    capacity = 999
    assert first.memory_tokens + first.doc_tokens + first.graph_tokens == capacity
    # 60% of 999 = 599.4 → remainder method yields stable triple
    assert first.memory_tokens == 599
    assert first.doc_tokens == 150
    assert first.graph_tokens == 250


def test_identity_returns_exact_configured_budget_object() -> None:
    budget = _base_budget()
    plan = _plan(
        query="雾青控制台是什么",
        retrieve_mode="hybrid",
        max_hits=5,
        max_doc_hits=3,
        budget=budget,
        enabled=True,
    )
    assert plan.identity is True
    assert plan.budget is budget

    disabled = _plan(
        query="我喜欢吃什么",
        retrieve_mode="hybrid",
        max_hits=5,
        max_doc_hits=3,
        budget=budget,
        enabled=False,
    )
    assert disabled.budget is budget


# ---------------------------------------------------------------------------
# Important #4: metrics sanitizer
# ---------------------------------------------------------------------------


def test_sanitize_plan_meta_drops_unknown_and_isolates_nested() -> None:
    from services.context.query_plan import sanitize_plan_meta

    dirty = {
        "version": "qa_rg_v1",
        "needs": ["preference", "hacked_need"],
        "profile_id": "preference_memory_v1",
        "mode": "hybrid",
        "top_k": 5,
        "type_caps": {"doc_chunk": 1, "memory_card": 5, "evil": 99},
        "reason_codes": ["need:preference", "secret_injection"],
        "enabled": True,
        "identity": False,
        "extra_secret": "should-not-persist",
        "api_key": "sk-leak",
    }
    clean = sanitize_plan_meta(dirty)
    assert "extra_secret" not in clean
    assert "api_key" not in clean
    assert set(clean.keys()) <= {
        "version",
        "needs",
        "profile_id",
        "mode",
        "top_k",
        "type_caps",
        "reason_codes",
        "enabled",
        "identity",
    }
    assert clean["needs"] == ["preference"]
    assert "evil" not in clean["type_caps"]
    assert "secret_injection" not in clean["reason_codes"]

    # Nested isolation: mutate original after sanitize.
    dirty["type_caps"]["doc_chunk"] = 999
    dirty["needs"].append("doc_grounding")
    dirty["reason_codes"].append("need:doc_grounding")
    assert clean["type_caps"]["doc_chunk"] == 1
    assert clean["needs"] == ["preference"]
    assert "need:doc_grounding" not in clean["reason_codes"]


def test_to_metrics_is_secret_free_closed() -> None:
    budget = _base_budget()
    plan = _plan(
        query="secret token abc123",
        current_message="我的密码是 xyz",
        retrieve_mode="hybrid",
        max_hits=5,
        max_doc_hits=3,
        budget=budget,
        enabled=True,
    )
    joined = " ".join(plan.reason_codes)
    assert "abc123" not in joined
    assert "xyz" not in joined
    assert "密码" not in joined
    meta = plan.to_metrics()
    assert set(meta.keys()) == {
        "version",
        "needs",
        "profile_id",
        "mode",
        "top_k",
        "type_caps",
        "reason_codes",
        "enabled",
        "identity",
    }
    blob = str(meta)
    assert "abc123" not in blob
    assert "xyz" not in blob


def test_sanitize_plan_meta_rejects_loose_scalars_and_oversized_doc_cap() -> None:
    from services.context.query_plan import sanitize_plan_meta

    clean = sanitize_plan_meta(
        {
            "version": "wrong",
            "needs": ["preference"],
            "profile_id": "preference_memory_v1",
            "mode": "hybrid",
            "top_k": True,
            "type_caps": {"doc_chunk": True, "memory_card": 5},
            "reason_codes": ["need:preference", "doc_cap:99"],
            "enabled": "false",
            "identity": "true",
        }
    )
    assert clean["version"] == "qa_rg_v1"
    assert clean["top_k"] == 1
    assert clean["type_caps"] == {"memory_card": 5}
    assert clean["reason_codes"] == ["need:preference"]
    assert clean["enabled"] is True
    assert clean["identity"] is False


# ---------------------------------------------------------------------------
# General contract
# ---------------------------------------------------------------------------


def test_max_two_needs_deterministic() -> None:
    budget = _base_budget()
    plan = _plan(
        query="我以前喜欢吃什么",
        current_message="我以前喜欢吃什么",
        retrieve_mode="hybrid",
        max_hits=5,
        max_doc_hits=3,
        budget=budget,
        enabled=True,
    )
    assert 1 <= len(plan.needs) <= 2
    assert all(
        n
        in {
            "ordinary_fact",
            "preference",
            "temporal_current",
            "temporal_earlier",
            "premise_check",
            "relation_multihop",
            "broad_recall",
            "doc_grounding",
        }
        for n in plan.needs
    )
    plan2 = _plan(
        query="我以前喜欢吃什么",
        current_message="我以前喜欢吃什么",
        retrieve_mode="hybrid",
        max_hits=5,
        max_doc_hits=3,
        budget=budget,
        enabled=True,
    )
    assert plan.needs == plan2.needs
    assert plan.profile_id == plan2.profile_id
    assert plan.type_caps == plan2.type_caps


def test_top_k_clamped_to_max_hits_range() -> None:
    budget = _base_budget()
    plan = _plan(
        query="雾青控制台",
        current_message="",
        retrieve_mode="hybrid",
        max_hits=5,
        max_doc_hits=3,
        budget=budget,
        enabled=True,
    )
    assert 1 <= plan.top_k <= 5
    plan_one = _plan(
        query="雾青控制台",
        retrieve_mode="hybrid",
        max_hits=1,
        max_doc_hits=1,
        budget=budget,
        enabled=True,
    )
    assert plan_one.top_k == 1


def test_retrieve_mode_never_widened_or_narrowed() -> None:
    budget = _base_budget()
    for mode in ("skip", "doc", "fact", "hybrid"):
        plan = _plan(
            query="小明和雾青是什么关系",
            current_message="小明和雾青是什么关系",
            retrieve_mode=mode,
            max_hits=5,
            max_doc_hits=3,
            budget=budget,
            enabled=True,
        )
        assert plan.retrieve_mode == mode


def test_disabled_is_exact_identity() -> None:
    budget = _base_budget()
    plan = _plan(
        query="我喜欢吃什么",
        current_message="我喜欢吃什么",
        retrieve_mode="hybrid",
        max_hits=5,
        max_doc_hits=3,
        budget=budget,
        enabled=False,
    )
    assert plan.enabled is False
    assert plan.identity is True
    assert plan.needs == ("ordinary_fact",)
    assert plan.profile_id == "ordinary_identity"
    assert plan.top_k == 5
    assert plan.type_caps == {"doc_chunk": 3}
    assert plan.budget is budget
    assert plan.retrieve_mode == "hybrid"
    assert any("disabled" in c for c in plan.reason_codes)


def test_bie_cai_does_not_pre_skip_or_force_ordinary_only() -> None:
    """Natural-language '别猜/不知道' must NOT pre-skip retrieval or force abstain."""
    budget = _base_budget()
    plan = _plan(
        query="别猜，我住在哪里你不知道吗",
        current_message="别猜，我住在哪里你不知道吗",
        retrieve_mode="hybrid",
        max_hits=5,
        max_doc_hits=3,
        budget=budget,
        enabled=True,
    )
    assert plan.top_k >= 1
    assert plan.retrieve_mode == "hybrid"
    assert not any(
        code in {"pre_skip", "abstain", "bie_cai_skip", "unknown_skip"}
        for code in plan.reason_codes
    )
    assert plan.needs


def test_empty_and_punctuation_remain_ordinary() -> None:
    budget = _base_budget()
    for q in ("", "   ", "...", "？？", "!!"):
        plan = _plan(
            query=q,
            current_message=q,
            retrieve_mode="hybrid",
            max_hits=5,
            max_doc_hits=3,
            budget=budget,
            enabled=True,
        )
        assert plan.identity is True
        assert plan.needs == ("ordinary_fact",)


def test_type_caps_within_max_hits_and_max_doc_hits() -> None:
    budget = _base_budget()
    plan = _plan(
        query="小明和雾青是什么关系",
        current_message="小明和雾青是什么关系",
        retrieve_mode="hybrid",
        max_hits=4,
        max_doc_hits=2,
        budget=budget,
        enabled=True,
    )
    for hit_type, cap in plan.type_caps.items():
        assert cap >= 0
        if hit_type == "doc_chunk":
            assert cap <= 2
        else:
            assert cap <= 4


def test_closed_need_set_only() -> None:
    from services.context.query_plan import CLOSED_NEEDS

    assert set(CLOSED_NEEDS) == {
        "ordinary_fact",
        "preference",
        "temporal_current",
        "temporal_earlier",
        "premise_check",
        "relation_multihop",
        "broad_recall",
        "doc_grounding",
    }


def test_plan_is_sync_pure_structural() -> None:
    """Structural purity: sync entrypoint + no forbidden imports (AST, not source scan)."""
    from services.context import query_plan as qp

    assert not inspect.iscoroutinefunction(qp.plan_query_aware_retrieval)
    assert inspect.isfunction(qp.plan_query_aware_retrieval)

    src_path = Path(inspect.getsourcefile(qp) or "")
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    forbidden_modules = {
        "aiohttp",
        "asyncio",
        "sqlite3",
        "plugins",
        "services.memory",
        "services.knowledge",
        "services.retrieval",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert alias.name not in forbidden_modules
                assert root not in {"aiohttp", "asyncio", "sqlite3", "plugins"}
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert mod not in forbidden_modules
            assert not mod.startswith("plugins.")
            assert not mod.startswith("services.memory")
            assert "aiohttp" not in mod
            assert "asyncio" not in mod
            assert "sqlite3" not in mod


@pytest.mark.parametrize(
    "mode",
    ["skip", "doc", "fact", "hybrid"],
)
def test_skip_mode_preserved_not_forced_to_retrieve(mode: str) -> None:
    budget = _base_budget()
    plan = _plan(
        query="雾青控制台",
        retrieve_mode=mode,
        max_hits=5,
        max_doc_hits=3,
        budget=budget,
        enabled=True,
    )
    assert plan.retrieve_mode == mode


def test_current_message_can_influence_needs_without_query_markers() -> None:
    """current_message-only intent may classify; rewritten query alone is not the only input."""
    budget = _base_budget()
    plan = _plan(
        query="用户居住地",
        current_message="我以前住在哪里",
        retrieve_mode="hybrid",
        max_hits=5,
        max_doc_hits=3,
        budget=budget,
        enabled=True,
    )
    assert "temporal_earlier" in plan.needs
    assert plan.identity is False
