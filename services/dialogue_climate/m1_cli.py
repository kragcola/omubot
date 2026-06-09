"""CLI for the M1 dialogue-climate gray-run calibration report.

Run: ``uv run python -m services.dialogue_climate.m1_cli [--group GID] [--db PATH]``

Prints durable injection / trigger counts, the prompt-trigger rate, and the
*observed* vs *configured* half-life so Part A M1 tuning (tau / threshold) can be
decided from real gray-run data instead of guesses (Part A R7/R8).
"""

from __future__ import annotations

import argparse

from rich.console import Console
from rich.table import Table

from services.dialogue_climate.m1_metrics import _DEFAULT_DB_PATH, M1MetricsRecorder


def _fmt_secs(s: float) -> str:
    if not s:
        return "—"
    if s >= 3600:
        return f"{s / 3600:.1f}h"
    if s >= 60:
        return f"{s / 60:.1f}min"
    return f"{s:.0f}s"


def main() -> None:
    parser = argparse.ArgumentParser(description="M1 dialogue-climate gray-run report")
    parser.add_argument("--group", default=None, help="restrict to one group_id")
    parser.add_argument("--db", default=_DEFAULT_DB_PATH, help="metrics db path")
    args = parser.parse_args()

    rec = M1MetricsRecorder(db_path=args.db)
    s = rec.summary(group_id=args.group)
    rec.close()

    console = Console()
    scope = f"group={args.group}" if args.group else "all groups"
    table = Table(title=f"M1 tension gray-run · {scope}", title_justify="left")
    table.add_column("metric", style="cyan")
    table.add_column("value", justify="right")

    table.add_row("injection_count", str(int(s["injection_count"])))
    table.add_row("prompt_trigger_count", str(int(s["prompt_trigger_count"])))
    table.add_row("prompt_trigger_rate", f"{s['prompt_trigger_rate'] * 100:.1f}%")
    table.add_row("active keys (groups)", str(int(s["key_count"])))
    table.add_row("peak_tension", f"{s['peak_tension']:.3f}")
    table.add_row("median inter-arrival", _fmt_secs(s["median_interarrival_s"]))
    table.add_row("decay samples", str(int(s["decay_sample_count"])))
    table.add_row("configured half-life", _fmt_secs(s["configured_half_life_s"]))
    table.add_row("observed half-life", _fmt_secs(s["observed_half_life_s"]))

    console.print(table)

    if s["injection_count"] == 0:
        console.print(
            "[yellow]No M1 events recorded yet — is m1_enabled on, "
            "and has any @/poke burst happened?[/yellow]"
        )
    elif s["decay_sample_count"] == 0:
        console.print(
            "[yellow]No decay samples yet — need ≥2 injections on the same "
            "group with a gap between them.[/yellow]"
        )


if __name__ == "__main__":
    main()
