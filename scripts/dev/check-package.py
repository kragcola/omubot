#!/usr/bin/env python3
"""Smoke-test the wheel produced by the native build.

The check intentionally uses only the standard library in this process.  The
installed application and its TestClient dependencies are exercised in an
isolated child interpreter after the wheel has been extracted.
"""

from __future__ import annotations

import argparse
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIST = ROOT / ".cache" / "dist"
SMOKE_TIMEOUT_SECONDS = 60
FORBIDDEN_MARKERS = ("credential", "state")


class PackageSmokeError(Exception):
    """A user-facing package smoke-test failure."""


def find_wheel(dist_dir: Path) -> Path:
    """Return the sole wheel below *dist_dir*, or fail clearly."""
    if not dist_dir.is_dir():
        raise PackageSmokeError(f"distribution directory does not exist: {dist_dir}")
    root = dist_dir.resolve()
    wheels = sorted(
        path
        for path in dist_dir.rglob("*")
        if path.is_file() and path.suffix.casefold() == ".whl"
    )
    if len(wheels) != 1:
        names = ", ".join(str(path.relative_to(dist_dir)) for path in wheels) or "none"
        raise PackageSmokeError(f"expected exactly one wheel under {dist_dir}; found {len(wheels)} ({names})")
    wheel = wheels[0].resolve()
    if not wheel.is_relative_to(root):
        raise PackageSmokeError(f"wheel resolves outside distribution directory: {wheels[0]}")
    return wheel


def archive_member_path(name: str) -> PurePosixPath:
    """Validate a ZIP member name before using it as a local path."""
    if not name or "\x00" in name or "\\" in name:
        raise PackageSmokeError(f"unsafe wheel member name: {name!r}")
    raw_parts = name.split("/")
    if name.endswith("/"):
        raw_parts.pop()
    if not raw_parts or any(part in {"", ".", ".."} for part in raw_parts):
        raise PackageSmokeError(f"unsafe wheel member path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or ":" in raw_parts[0]:
        raise PackageSmokeError(f"unsafe wheel member path: {name!r}")
    return path


def member_is_allowed(info: ZipInfo) -> bool:
    """Reject links and special files that a wheel could otherwise encode."""
    mode = (info.external_attr >> 16) & 0o170000
    return mode in {0, stat.S_IFREG, stat.S_IFDIR}


def forbidden_member(name: str) -> bool:
    """Identify credentials, state, or test material in a wheel."""
    parts = PurePosixPath(name).parts
    return PurePosixPath(name).name == "model-secrets.json" or any(
        marker in part.casefold()
        for part in parts
        for marker in FORBIDDEN_MARKERS
    ) or any(part.casefold() in {"test", "tests"} or part.casefold().startswith("test_") for part in parts)


def extract_wheel(wheel: Path, destination: Path) -> set[str]:
    """Safely extract a trusted local wheel and return its member names."""
    try:
        archive = ZipFile(wheel)
    except (BadZipFile, OSError) as exc:
        raise PackageSmokeError(f"cannot open wheel {wheel}: {exc}") from exc

    names: set[str] = set()
    root = destination.resolve()
    try:
        with archive:
            for info in archive.infolist():
                relative = archive_member_path(info.filename)
                if info.filename in names:
                    raise PackageSmokeError(f"duplicate wheel member: {info.filename}")
                if not member_is_allowed(info):
                    raise PackageSmokeError(f"wheel contains a link or special file: {info.filename}")
                names.add(info.filename)
                target = destination.joinpath(*relative.parts)
                if not target.resolve().is_relative_to(root):
                    raise PackageSmokeError(f"wheel member escapes extraction directory: {info.filename}")
                if info.is_dir() or (info.external_attr >> 16) & 0o170000 == stat.S_IFDIR:
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
    except (BadZipFile, KeyError, OSError, ValueError, RuntimeError) as exc:
        raise PackageSmokeError(f"cannot safely extract wheel {wheel}: {exc}") from exc
    return names


def run_installed_smoke(extracted: Path) -> None:
    """Exercise the application from the extracted wheel in an isolated process."""
    code = r'''
import importlib.metadata
import re
import sys
import tempfile
from pathlib import Path

wheel_root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(wheel_root))

import omubot_new
from fastapi.testclient import TestClient
from omubot_new.bootstrap import create_app
from omubot_new.config import Config, Credentials

module_path = Path(omubot_new.__file__).resolve()
assert module_path.is_relative_to(wheel_root), (module_path, wheel_root)

metadata = [
    item
    for item in importlib.metadata.distributions(path=[str(wheel_root)])
    if item.metadata.get("Name", "").casefold() == "omubot-new"
]
assert len(metadata) == 1, metadata
distribution = metadata[0]
metadata_path = Path(distribution.locate_file("METADATA")).resolve()
assert metadata_path.is_relative_to(wheel_root), metadata_path
entry_points = [
    item
    for item in distribution.entry_points
    if item.group == "console_scripts" and item.name == "omubot-new"
]
assert len(entry_points) == 1, entry_points
assert entry_points[0].value == "omubot_new.bootstrap:main", entry_points[0].value
runner = [item for item in distribution.entry_points
          if item.group == "console_scripts" and item.name == "omubot-qq-test-runner"]
assert len(runner) == 1 and runner[0].value == "omubot_new.qq_test_runner:main"
from omubot_new.qq_test_runner import QQTestPlan
assert Path(sys.modules[QQTestPlan.__module__].__file__).resolve().is_relative_to(wheel_root)

credentials = Credentials(admin="a" * 32, status="s" * 32, ingress="i" * 32)
with tempfile.TemporaryDirectory(prefix="omubot-package-") as temporary:
    database = Path(temporary) / "nested" / "state.sqlite3"
    config = Config(db_path=str(database))

    with TestClient(create_app(config, credentials)) as client:
        health = client.get("/health")
        assert health.status_code == 200 and health.json()["ready"] is True, health.text
        assert isinstance(health.json()["generation"], str) and len(health.json()["generation"]) == 32

        page = client.get("/")
        assert page.status_code == 200, page.text
        assert "<title>Omubot" in page.text
        assets = re.findall(r'(?:src|href)="(/web/[^\"]+)"', page.text)
        assert assets, "static resources missing from HTML"
        for asset in assets:
            assert client.get(asset).status_code == 200, asset

        denied = client.get("/api/status")
        assert denied.status_code == 401, denied.text

    assert database.is_file(), database
    with TestClient(create_app(config, credentials)) as client:
        assert client.get("/health").json()["ready"] is True
'''
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-c", code, str(extracted)],
            check=False,
            capture_output=True,
            text=True,
            timeout=SMOKE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise PackageSmokeError(f"installed package smoke timed out after {SMOKE_TIMEOUT_SECONDS}s") from exc
    if result.returncode:
        output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        raise PackageSmokeError(f"installed package smoke failed (exit {result.returncode}):\n{output}")


def parse_args(argv: list[str] | None) -> Path:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", nargs="?", type=Path, help="wheel directory (default: .cache/dist)")
    parser.add_argument("--dist", dest="dist_option", type=Path, help="wheel directory")
    args = parser.parse_args(argv)
    if args.dist is not None and args.dist_option is not None:
        parser.error("provide the wheel directory once, either positionally or with --dist")
    return (args.dist_option or args.dist or DEFAULT_DIST).resolve()


def main(argv: list[str] | None = None) -> int:
    dist_dir = parse_args(argv)
    try:
        wheel = find_wheel(dist_dir)
        with tempfile.TemporaryDirectory(prefix="omubot-wheel-") as temporary:
            extracted = Path(temporary)
            names = extract_wheel(wheel, extracted)
            if "omubot_new/web_static/index.html" not in names:
                raise PackageSmokeError("wheel does not bundle omubot_new/web_static/index.html")
            forbidden = sorted(name for name in names if forbidden_member(name))
            if forbidden:
                raise PackageSmokeError(f"wheel contains credentials, state, or test material: {forbidden}")
            run_installed_smoke(extracted)
        print(f"PASS: installed wheel smoke ({wheel.name})")
        return 0
    except PackageSmokeError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
