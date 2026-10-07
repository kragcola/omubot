"""Run the independent N6 encrypted-spool deadline cleaner.

The service must be supervised by the host OS and remain running when the Bot
stops. It never opens the application database or sends network traffic.
"""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path
from threading import Event
from types import FrameType

from omubot_new.archive_spool import (
    EncryptedTextSpool,
    EncryptedTextSpoolError,
    spool_binding_id,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spool-dir", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--bot-id", required=True)
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument(
        "--check", action="store_true", help="check that a separate cleaner is alive"
    )
    args = parser.parse_args()
    try:
        spool = EncryptedTextSpool(
            args.spool_dir, args.key_file,
            binding_id=spool_binding_id(
                args.bot_id, args.db_path, args.spool_dir, args.key_file
            ),
        )
        if args.check:
            if spool.cleaner_available():
                print("ready")
                return 0
            print("unavailable")
            return 1
        stop = Event()

        def request_stop(_signal: int, _frame: FrameType | None) -> None:
            stop.set()

        signal.signal(signal.SIGINT, request_stop)
        signal.signal(signal.SIGTERM, request_stop)
        spool.serve_deadline_cleaner(stop)
        return 0
    except EncryptedTextSpoolError:
        print("memory_spool_cleaner_failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
