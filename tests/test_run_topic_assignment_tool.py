from pathlib import Path

from tools import run_topic_assignment


def test_topic_assignment_cli_defaults_are_anchored_to_repo_root() -> None:
    args = run_topic_assignment._parser().parse_args([])
    root = Path(run_topic_assignment.__file__).resolve().parents[1]

    assert Path(args.raw_db) == root / "storage" / "research_events.db"
    assert Path(args.derived_db) == root / "storage" / "research_topic_assignments.db"
