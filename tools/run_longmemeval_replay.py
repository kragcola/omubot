"""CLI for the offline LongMemEval raw-turn replay."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

from loguru import logger

from services.context.official_replay import run_longmemeval_dataset

_REPLAY_SERVICE_LOGGERS = ("services.context", "services.memory")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run an offline raw-turn LongMemEval replay through Omubot's "
            "CardStore and ContextService. This is not leaderboard parity."
        )
    )
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--question-type", action="append", default=[])
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--max-chars", type=int, default=2400)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--indent", type=int, default=2)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        for logger_name in _REPLAY_SERVICE_LOGGERS:
            logger.disable(logger_name)
        try:
            report = asyncio.run(
                run_longmemeval_dataset(
                    args.dataset,
                    limit=args.limit,
                    question_types=set(args.question_type) or None,
                    top_k=args.top_k,
                    max_chars=args.max_chars,
                )
            )
        finally:
            for logger_name in _REPLAY_SERVICE_LOGGERS:
                logger.enable(logger_name)
        rendered = json.dumps(
            report.to_dict(),
            ensure_ascii=False,
            indent=max(0, args.indent),
            sort_keys=True,
        )
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf-8")
        else:
            print(rendered)
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "error": f"replay_failed:{type(exc).__name__}"},
                sort_keys=True,
            )
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
