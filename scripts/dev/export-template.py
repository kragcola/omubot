#!/usr/bin/env python3
"""Create a small, shareable source archive from this repository.

The archive is deliberately assembled from an allowlist.  It is a source
release template, rather than a backup of the development checkout: tests,
documentation, repository metadata, local state, and credentials are never
read for inclusion.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from typing import Final
from zipfile import ZIP_STORED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIRECTORY: Final = Path("src") / "omubot_new"
REQUIRED_ROOT_FILES: Final = (
    "config.example.toml", "pyproject.toml", "uv.lock", "hatch_build.py",
    "scripts/dev/export-web-contracts.py", "scripts/dev/check-web-state.mjs",
)
FIXED_ZIP_TIME: Final = (1980, 1, 1, 0, 0, 0)
LOCAL_PATH_MARKERS: Final = (b"/Users/", b"/Volumes/")

# These two files are generated instead of copied from the development tree.
# Their content is intentionally generic and contains no checkout-specific
# paths, history, or test instructions.
TEMPLATE_README: Final = """# omubot-new — basic conversation bot

Native Python 3.12 bot with named model profiles, default Thinker, bounded group
conversation, ordered text delivery and a time tool. Docker is optional.

## Start an isolated instance

```sh
uv sync --locked
uv run omubot-new --init-instance instances/demo --name demo --bot-id 10001 --port 18081
uv run omubot-new --instance instances/demo
```

Open http://127.0.0.1:18081 in your browser. First start creates
`instances/demo/credentials.toml`: use its `admin` token for configuration,
authorization and offline chat, or its separate `status` token for read-only status.
Do not share this file. Initialization never overwrites an existing instance.

1. Configure named model profiles, the default profile and optional task bindings.
   Endpoints are complete request URLs. A model key may come from its configured
   environment variable or be saved from Web model settings. Web-saved keys stay
   in this instance's local `model-secrets.json` and are never exported with a
   template; environment variables take precedence. Never paste keys into model
   names, URLs or configuration JSON.
2. Saving or rolling back creates a configuration version. Use Web runtime
   management to apply saved settings by rebuilding the core. Active conversations
   block restart; unsaved drafts must first be saved or discarded. Log in again
   afterward; short-term history clears, while NapCat and QQ login remain intact.
   This is not a code upgrade or a crash-recovery daemon. SQLite is authoritative after the first startup;
   editing bootstrap TOML does not overwrite saved daily settings.
3. Explicitly authorize a group/user using the currently running model profiles.
   Permissions expire; changing a model destination may require new grants.
4. Set an optional instance persona name and speaking instructions on the persona
   page. This shares the versioned settings draft and takes effect after restart;
   it affects replies and tool continuations, not Thinker decisions or permissions.
5. Use offline chat to verify a normal reply and a time query. Offline mode uses
   simulated models and sends no messages to QQ. The diagnostics page shows recent
   request/action outcomes and provider-reported tokens; missing usage is unknown,
   and costs are not estimated.

## Rebuild the Web interface

The archive includes prebuilt static files for Python-only installation. To edit
the interface, install Node 22.14 or a compatible supported version, then run:

```sh
cd web
npm ci
npm run build
cd ..
uv build
```

Rebuild Web before packaging changed frontend sources. Do not include node_modules
or personal configuration in releases.

## Real acceptance comes separately

Initialize a separate instance with the real bot ID. Configure model format
(OpenAI Chat Completions, OpenAI Responses, Anthropic Messages or DeepSeek),
model ID and full endpoint, then save the model key in Web settings or provide
its configured key environment variable. Restart with `--live` only for
deliberate real acceptance.

For the authenticated reverse WebSocket path, start the instance with:

```sh
uv run omubot-new --instance instances/demo --live --reverse-ws
```

The OneBot peer connects to `/onebot/events` with the instance's `ingress`
Bearer token and an `X-Self-ID` header matching the configured bot ID. This
single authenticated WebSocket carries incoming events and outgoing OneBot API
calls. On connection the bot probes `get_login_info` and `get_status` separately;
new writes require a matching actual account and confirmed online state. Native
QQ delivery governance starts held on first installation and retains quotas and
unknown outcomes across restart. An authenticated Web administrator must review
the exact account or target before explicitly resuming new requests. The default remains
OneBot HTTP: omit `--reverse-ws`, configure its endpoint and token environment
variable, and outbound calls use that token. Grant only the intended group/user
after confirming running configuration. There are no automatic retries.

The default setup starts a bounded group conversation. This source package also
includes optional private chat, media, history recovery, memory and journal features;
each follows its own explicit settings, current permissions and source retention.
Saving configuration does not activate these features or authorize external actions.
Quoted originals require retained, authorized same-group sources; unavailable sources
remain unavailable. Code and package checks do not establish real platform acceptance.
Production data migration, Linux/Windows runtime acceptance and real model/QQ/QZone
acceptance must be verified separately on their target systems.

## Keep personal and development bots separate

Use different instance directories and ports. Install a selected wheel into a
separate virtual environment for an operational bot; do not point that environment
at an editable development checkout. Stop the bot and take a consistent database
backup before upgrades. Schema downgrade requires restoring the pre-upgrade backup;
unknown outgoing actions must not be replayed automatically. The native wheel also
ships `omubot-qq-test-runner --instance PATH --plan PATH --validate-only` to validate
a finite second-account test plan without opening a database or network client.
Actual tests require explicit `--live`, a separately approved Bot batch and the
dedicated second account. A test batch never resumes automatically after restart.

For GitHub, initialize a fresh repository from this generic archive. Do not copy
personal runtime data or the development repository history. The included manifest
hashes every payload file. Export checks are a hygiene guard, not a full secret
scanner. The full developer test suite and project history remain in the development
repository; this source archive contains only runtime/build inputs and this guide.
"""

TEMPLATE_GITIGNORE: Final = """# Local Python environments and caches
.venv/
.cache/
__pycache__/
*.py[cod]
.pytest_cache/
.ruff_cache/
.mypy_cache/

# Build and coverage output
node_modules/
dist/
build/
*.egg-info/
coverage/
.coverage

# Runtime state and credentials
storage/
instances/
credentials.toml
config.local.toml
*.db
*.db-*
*.sqlite*
.env
.env.*
!.env.example

# OS/editor state
.DS_Store
"""


class TemplateExportError(RuntimeError):
    """Raised when the source release cannot be assembled safely."""


def _regular_file(path: Path, description: str) -> bool:
    """Check that *path* is a regular, non-link file when it exists."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise TemplateExportError(f"cannot inspect {description}: {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode):
        raise TemplateExportError(f"refusing symlink in allowlist: {path}")
    if not stat.S_ISREG(info.st_mode):
        raise TemplateExportError(f"allowlisted path is not a regular file: {path}")
    return True


def _read_regular_file(path: Path, description: str) -> bytes:
    """Read a regular file without following a symlink at the final path."""
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, os.O_RDONLY | no_follow)
    except OSError as exc:
        raise TemplateExportError(f"cannot read {description}: {path}: {exc}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise TemplateExportError(f"allowlisted path is not a regular file: {path}")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            return handle.read()
    finally:
        if descriptor != -1:
            os.close(descriptor)


def _check_content(path: str, content: bytes) -> None:
    """Reject obvious checkout-local absolute paths from exported content."""
    for marker in LOCAL_PATH_MARKERS:
        if marker in content:
            raise TemplateExportError(
                f"refusing to export {path}: contains an absolute {marker.decode()} path"
            )


def _reject_symlink_ancestors(path: Path, root: Path) -> None:
    """Reject symlinked components between *root* and an allowlisted path."""
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise TemplateExportError(f"source path escapes repository root: {path}") from exc
    current = root
    for part in relative.parts:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            break
        except OSError as exc:
            raise TemplateExportError(f"cannot inspect source path: {current}: {exc}") from exc
        if stat.S_ISLNK(info.st_mode):
            raise TemplateExportError(f"refusing symlink in source path: {current}")


def _web_files(root: Path, directory: Path, *, source: bool) -> list[tuple[str, bytes]]:
    """Read only frontend source or production assets, never arbitrary web files."""
    _reject_symlink_ancestors(directory, root)
    if not directory.is_dir():
        raise TemplateExportError(f"required web directory is missing: {directory}")
    selected: list[tuple[str, bytes]] = []
    for entry in sorted(directory.rglob("*")):
        _reject_symlink_ancestors(entry, root)
        if entry.is_dir():
            continue
        relative = entry.relative_to(directory)
        if source:
            if entry.name.endswith(".test.ts"):
                continue
            allowed = entry.suffix in {".ts", ".vue", ".css"}
        else:
            allowed = relative.as_posix() == "index.html" or (
                len(relative.parts) == 2 and relative.parts[0] == "assets"
                and re.fullmatch(r"[A-Za-z0-9_-]+\.(js|css|woff2|svg)", entry.name) is not None
            )
        if not allowed or not _regular_file(entry, "web file"):
            raise TemplateExportError(f"unexpected web file: {entry}")
        content = _read_regular_file(entry, "web file")
        member = entry.relative_to(root).as_posix()
        _check_content(member, content)
        selected.append((member, content))
    if not source and not (directory / "index.html").is_file():
        raise TemplateExportError("web build missing: run npm ci and npm run build in web")
    if not source:
        payload = dict(selected)
        index_member = (directory / "index.html").relative_to(root).as_posix()
        references = re.findall(rb'(?:src|href)="/web/([^\"]+)"', payload[index_member])
        prefix = directory.relative_to(root).as_posix() + "/"
        if not references or any(
            prefix + reference.decode("utf-8") not in payload for reference in references
        ):
            raise TemplateExportError("web build references missing assets; rebuild web before export")
    return selected


def _package_source_files(root: Path) -> list[tuple[str, bytes]]:
    """Collect package sources and the public Food catalog; reject other entries."""
    root = root.resolve()
    package = root / PACKAGE_DIRECTORY
    _reject_symlink_ancestors(package, root)
    if not package.is_dir() or package.is_symlink():
        raise TemplateExportError(f"source package directory is missing or unsafe: {package}")
    resolved_package = package.resolve()
    if not resolved_package.is_relative_to(root):
        raise TemplateExportError(f"resolved source package is outside repository root: {resolved_package}")

    selected: list[tuple[str, bytes]] = []
    try:
        entries = sorted(package.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise TemplateExportError(f"cannot inspect source package directory: {package}: {exc}") from exc

    for entry in entries:
        # Python bytecode is a known local cache and must never affect or enter
        # the release. Every other directory would make the allowlist unclear.
        if entry.name == "__pycache__" and entry.is_dir() and not entry.is_symlink():
            continue
        if entry.is_symlink():
            raise TemplateExportError(f"refusing symlink in source package: {entry}")
        if entry.name == "web_static" and entry.is_dir():
            selected.extend(_web_files(root, entry, source=False))
            continue
        allowed = entry.name.endswith(".py") or entry.name == "food_menu.json"
        if not allowed:
            raise TemplateExportError(f"unexpected source package entry: {entry}")
        if not _regular_file(entry, "source file"):
            raise TemplateExportError(f"source file is missing: {entry}")
        content = _read_regular_file(entry, "source file")
        member = f"{PACKAGE_DIRECTORY.as_posix()}/{entry.name}"
        _check_content(member, content)
        selected.append((member, content))

    if not any(name == f"{PACKAGE_DIRECTORY.as_posix()}/web_static/index.html" for name, _ in selected):
        raise TemplateExportError("web build missing: run npm ci and npm run build in web")
    return selected


def collect_files(root: Path | None = None) -> dict[str, bytes]:
    """Read the explicit release allowlist below *root*.

    The returned mapping is sorted by member name so callers get a stable
    order. The generated README and .gitignore are included in the mapping;
    the manifest is added only after these payloads have been hashed.
    """
    root = (ROOT if root is None else Path(root)).resolve()
    files: dict[str, bytes] = {
        "README.md": TEMPLATE_README.encode("utf-8"),
        ".gitignore": TEMPLATE_GITIGNORE.encode("utf-8"),
    }
    for name in REQUIRED_ROOT_FILES:
        path = root / name
        _reject_symlink_ancestors(path, root)
        if not _regular_file(path, "required root file"):
            raise TemplateExportError(f"required root file is missing: {path}")
        content = _read_regular_file(path, "required root file")
        _check_content(name, content)
        files[name] = content
    for name, content in _package_source_files(root):
        files[name] = content
    for name in ("package.json", "package-lock.json", "index.html", "tsconfig.json", "vite.config.ts"):
        path = root / "web" / name
        _reject_symlink_ancestors(path, root)
        if not _regular_file(path, "web build input"):
            raise TemplateExportError(f"required web build input missing: {path}")
        content = _read_regular_file(path, "web build input")
        _check_content("web/" + name, content)
        files["web/" + name] = content
    for name, content in _web_files(root, root / "web/src", source=True):
        files[name] = content
    return dict(sorted(files.items()))


def _manifest_bytes(files: dict[str, bytes]) -> bytes:
    """Return canonical JSON describing every non-manifest archive member."""
    records = [
        {
            "path": name,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
        }
        for name, content in sorted(files.items())
    ]
    manifest = {
        "format": "omubot-new-source-template",
        "version": 1,
        "files": records,
    }
    return (json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _zip_info(name: str) -> ZipInfo:
    """Create a deterministic regular-file ZIP member descriptor."""
    info = ZipInfo(filename=name, date_time=FIXED_ZIP_TIME)
    info.compress_type = ZIP_STORED
    info.create_system = 3
    info.create_version = 20
    info.extract_version = 20
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    info.flag_bits |= 0x800  # UTF-8 names and comments.
    return info


def _write_zip(path: Path, files: dict[str, bytes]) -> None:
    """Write the deterministic archive at an already exclusive temp path."""
    manifest = _manifest_bytes(files)
    members = dict(files)
    members["manifest.json"] = manifest
    try:
        with ZipFile(path, mode="w", compression=ZIP_STORED, allowZip64=False) as archive:
            for name, content in sorted(members.items()):
                archive.writestr(_zip_info(name), content)
    except (OSError, ValueError, RuntimeError) as exc:
        raise TemplateExportError(f"cannot write temporary ZIP: {path}: {exc}") from exc


def export_template(output: Path, root: Path | None = None) -> bytes:
    """Build and publish a source archive without ever overwriting *output*.

    A temporary file is created in the destination directory, fsynced, and
    published with an atomic hard-link operation. ``os.link`` fails if another
    process wins the destination race, so the existing file is preserved.
    The returned bytes are the manifest JSON stored in the archive.
    """
    output = Path(output)
    root = ROOT if root is None else Path(root)
    if os.path.lexists(output):
        raise TemplateExportError(f"output already exists: {output}")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise TemplateExportError(f"cannot create output directory: {output.parent}: {exc}") from exc
    if not output.parent.is_dir():
        raise TemplateExportError(f"output parent is not a directory: {output.parent}")
    if os.path.lexists(output):
        raise TemplateExportError(f"output already exists: {output}")

    files = collect_files(root)
    manifest = _manifest_bytes(files)
    temporary_name: str | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".omubot-template-", suffix=".tmp", dir=output.parent
        )
        os.close(descriptor)
        descriptor = -1
        temporary = Path(temporary_name)
        _write_zip(temporary, files)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        try:
            os.link(temporary, output, follow_symlinks=False)
        except FileExistsError as exc:
            raise TemplateExportError(f"output already exists: {output}") from exc
        except OSError as exc:
            raise TemplateExportError(f"cannot publish ZIP without overwrite: {output}: {exc}") from exc
        return manifest
    except TemplateExportError:
        raise
    except OSError as exc:
        raise TemplateExportError(f"template export failed: {exc}") from exc
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            except OSError:
                # Preserve the original failure if cleanup itself is refused.
                pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="new ZIP path (must not already exist)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the exporter and report failures without a traceback."""
    args = parse_args(argv)
    try:
        export_template(args.output)
    except TemplateExportError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
