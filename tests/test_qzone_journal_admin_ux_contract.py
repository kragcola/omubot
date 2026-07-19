from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEW_PATH = ROOT / "admin/frontend/src/views/qzone-journal/QzoneJournalView.vue"
DRAWER_PATH = ROOT / "admin/frontend/src/views/qzone-journal/DraftDetailDrawer.vue"
HISTORY_PATH = ROOT / "admin/frontend/src/views/qzone-journal/DraftHistoryPanels.vue"
CONTEXT_PATH = ROOT / "admin/frontend/src/views/qzone-journal/DraftContextPanels.vue"
API_PATH = ROOT / "admin/frontend/src/api/qzoneJournal.ts"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_qzone_console_prioritizes_runtime_gate_and_operator_work() -> None:
    source = _source(VIEW_PATH)

    for component in (
        "<AppPage",
        "<MetricCard",
        "<PageToolbar",
        "<AppPanelSection",
        "<EmptyState",
    ):
        assert component in source

    assert "attentionCount" in source
    assert "attentionHint" in source
    assert 'title="待处理"' in source
    assert 'title="已发布"' in source
    assert "counts?.published" in source
    assert "运行门" in source
    assert "qzone-gate__meta-item" in source

    # The actionable queue must appear before process-lifetime diagnostics.
    assert source.index('eyebrow="Review Queue"') < source.index(
        'eyebrow="Selection Trace"'
    )


def test_qzone_drawer_groups_context_history_and_action_boundary() -> None:
    source = _source(DRAWER_PATH)

    assert "<AppDrawerLayout" in source
    assert "actionBoundary" in source
    assert "qzone-drawer__boundary" in source
    assert "<NTabs" in source
    assert 'name="context"' in source
    assert 'name="history"' in source
    assert "草稿与来源" in source
    assert "版本与审计" in source
    assert "<DraftContextPanels" in source
    assert "<DraftHistoryPanels" in source

    # Existing mutation/state-machine boundaries remain represented in the drawer.
    for contract in (
        "canRecompose",
        "canDryRun",
        "canResolve",
        "isLineageTip",
        "actionGeneration",
        "detailRequestGeneration",
    ):
        assert contract in source


def test_qzone_console_uses_calm_ops_tokens_and_target_breakpoints() -> None:
    combined = "\n".join(
        (
            _source(VIEW_PATH),
            _source(DRAWER_PATH),
            _source(CONTEXT_PATH),
            _source(HISTORY_PATH),
        )
    )

    assert "@media (max-width: 1280px)" in combined
    assert "@media (max-width: 900px)" in combined
    assert "@media (min-width: 1440px)" in combined
    assert "var(--om-surface" in combined
    assert "var(--om-text-1)" in combined
    assert "rgb(var(--primary-color))" in combined
    assert "!important" not in combined
    assert "linear-gradient" not in combined


def test_qzone_standard_review_approval_is_explicitly_dry_run() -> None:
    source = _source(API_PATH)

    assert "approval_scope: 'dry_run'" in source
