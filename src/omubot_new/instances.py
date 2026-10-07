"""Explicit, independent local runtime directories; never overlay an existing bot."""

from __future__ import annotations

import json
import tomllib
import uuid
from pathlib import Path

from .config import Config, load_config


def initialize_instance(directory: Path, name: str, port: int, bot_id: str = "10001") -> Path:
    identity = uuid.uuid4().hex
    # Validate before making any filesystem changes.
    config = Config(instance_id=identity, instance_name=name, listen_port=port, bot_id=bot_id)
    root = directory.absolute()
    prefix = "OMUBOT_" + name.upper().replace("-", "_")
    content = (
        "# Independent instance; offline until explicitly started with --live.\n"
        f"instance_id = {json.dumps(identity)}\ninstance_name = {json.dumps(name)}\n"
        f"listen_port = {port}\nbot_id = {json.dumps(config.bot_id)}\n"
        f'onebot_token_env = "{prefix}_ONEBOT_TOKEN"\n'
        'mode = "offline"\nthinker_enabled = true\nactive_model = "default"\n'
        '[models.default]\napi_format = "anthropic"\n'
        'endpoint = "https://api.anthropic.com/v1/messages"\nmodel = "your-model-id"\n'
        f'api_key_env = "{prefix}_MODEL_KEY"\n'
    )
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    try:
        (root / "config.toml").write_text(content, encoding="utf-8")
        (root / "config.toml").chmod(0o600)
        (root / ".gitignore").write_text("*\n", encoding="utf-8")
    except BaseException:
        # Only files created by this call in a new directory are eligible for cleanup.
        for name in ("config.toml", ".gitignore"):
            (root / name).unlink(missing_ok=True)
        root.rmdir()
        raise
    return root


def load_instance(directory: Path, *, live: bool = False) -> Config:
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("instance must be a real directory")
    root = directory.resolve()
    for name in (
        "config.toml",
        "credentials.toml",
        "state.sqlite3",
        "state.sqlite3.lock",
        "state.sqlite3-wal",
        "state.sqlite3-shm",
    ):
        if (root / name).is_symlink():
            raise ValueError("instance files cannot be symlinks")
    path = root / "config.toml"
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    if "db_path" in raw or not raw.get("instance_id") or raw.get("instance_id") == "standalone":
        raise ValueError("instance requires its own identity and fixed local database")
    config = load_config(path, live=live)
    values = config.model_dump()
    values["db_path"] = str(root / "state.sqlite3")
    return Config.model_validate(values)
