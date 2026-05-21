from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kernel.config import ReplySegmentationConfig  # noqa: E402
from services.llm.segmentation import segment_reply  # noqa: E402


def _load_cases() -> list[dict[str, str]]:
    path = Path("tests/fixtures/reply_segmentation_cases.jsonl")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    cfg = ReplySegmentationConfig()
    print("reply segmentation benchmark")
    print(f"config: {cfg.model_dump()}")
    print()

    for case in _load_cases():
        result = segment_reply(case["text"], cfg)
        print(f"[{case['name']}] strategy={result.strategy} raw={result.raw_count} capped={result.capped_count}")
        for index, segment in enumerate(result.segments, 1):
            print(f"  {index}. ({segment.reason}) {segment.text}")
        print()

    syntok_dir = Path("/private/tmp/seg-audit-syntok")
    blingfire_dir = Path("/private/tmp/seg-audit-blingfire")
    print(f"syntok available: {syntok_dir.exists()} @ {syntok_dir}")
    print(f"blingfire available: {blingfire_dir.exists()} @ {blingfire_dir}")


if __name__ == "__main__":
    main()
