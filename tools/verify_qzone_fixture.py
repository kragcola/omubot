#!/usr/bin/env python3
"""Offline QZone sanitized-fixture verifier (no network, no credentials).

Loads one JSON fixture, validates the frozen secret-safe schema, compares the
request fingerprint to a candidate unverified WireProfile (names only),
builds an ``httpx.Response`` from the sanitized response block, invokes
``parse_qzone_publish_response``, and prints a secret-free JSON report.

Exit codes:
  0 — fixture valid and conformance ok
  1 — fixture invalid or non-conforming
  2 — usage / IO error

This CLI never mutates WireProfile, never sets validated=True, and never
echoes response bodies or secrets.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Repo root on sys.path when invoked as ``python tools/verify_qzone_fixture.py``.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.qzone_journal.delivery import BUILTIN_WIRE_PROFILE  # noqa: E402
from plugins.qzone_journal.fixture_conformance import (  # noqa: E402
    FixtureValidationError,
    load_fixture,
    run_conformance,
)
from plugins.qzone_journal.transport import WireProfile  # noqa: E402

_DEFAULT_ENDPOINT = (
    "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/"
    "cgi-bin/emotion_cgi_publish_v6"
)


def _candidate_profile() -> WireProfile:
    """Unverified candidate matching current QZone request shape (names only)."""

    # Prefer the built-in endpoint shape so fingerprint comparison is meaningful;
    # never flip validated.
    return WireProfile(
        profile_id="qzone-text-v1-candidate-unverified",
        endpoint=BUILTIN_WIRE_PROFILE.endpoint or _DEFAULT_ENDPOINT,
        validated=False,
        content_field=BUILTIN_WIRE_PROFILE.content_field,
        uin_field=BUILTIN_WIRE_PROFILE.uin_field,
        static_form_fields=dict(BUILTIN_WIRE_PROFILE.static_form_fields),
        max_content_chars=BUILTIN_WIRE_PROFILE.max_content_chars,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify one offline QZone sanitized fixture against the strict "
            "parser and an unverified candidate WireProfile. No network."
        ),
    )
    parser.add_argument(
        "fixture",
        type=Path,
        help="Path to a schema_version=1 fixture JSON file",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indent for the secret-free report (default: 2)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    fixture_path: Path = args.fixture

    if not fixture_path.is_file():
        err: dict[str, Any] = {
            "ok": False,
            "error": "fixture_not_found",
            "path": str(fixture_path),
        }
        print(json.dumps(err, ensure_ascii=False, indent=args.indent))
        return 2

    try:
        fixture = load_fixture(fixture_path)
    except FixtureValidationError as exc:
        err = {
            "ok": False,
            "error": "fixture_invalid",
            "reasons": [str(exc)],
            "profile_creation_eligible": False,
        }
        print(json.dumps(err, ensure_ascii=False, indent=args.indent))
        return 1

    profile = _candidate_profile()
    # Hard guard: this tool must never validate profiles.
    if profile.validated:
        err = {
            "ok": False,
            "error": "internal_profile_must_remain_unvalidated",
            "profile_creation_eligible": False,
        }
        print(json.dumps(err, ensure_ascii=False, indent=args.indent))
        return 1
    if BUILTIN_WIRE_PROFILE.validated:
        err = {
            "ok": False,
            "error": "builtin_profile_unexpectedly_validated",
            "profile_creation_eligible": False,
        }
        print(json.dumps(err, ensure_ascii=False, indent=args.indent))
        return 1

    report = run_conformance(fixture, profile=profile)
    payload = report.to_dict()
    # Never include body/secret material; extra safety strip.
    for forbidden in ("body", "cookie", "p_skey", "skey", "authorization"):
        payload.pop(forbidden, None)
    print(json.dumps(payload, ensure_ascii=False, indent=args.indent, sort_keys=True))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
