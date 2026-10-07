#!/usr/bin/env python3
"""Small AST checks for the boundaries used by this application, not a framework."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/omubot_new"
errors: list[str] = []
# Real owners, not a directory scaffold. Composition may see all layers.
CORE = {"types", "runtime", "rich_messages"}
APPLICATION = {"conversation", "contact"}
MODULES = {p.stem for p in SOURCE.glob("*.py")}

for path in SOURCE.glob("*.py"):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = (
                [node.module or ""] if isinstance(node, ast.ImportFrom) else [n.name for n in node.names]
            )
            for module in modules:
                top = module.split(".")[0]
                local = module.removeprefix("omubot_new.").split(".")[0]
                is_local = module.startswith("omubot_new.") or (
                    isinstance(node, ast.ImportFrom) and node.level > 0
                )
                if is_local and local in MODULES:
                    if path.stem in CORE and local not in CORE:
                        errors.append(f"{path.name}:{node.lineno}: core imports concrete service/application")
                    if path.stem not in CORE | APPLICATION | {"bootstrap"} and local in APPLICATION | {
                        "bootstrap"
                    }:
                        errors.append(f"{path.name}:{node.lineno}: service imports application/composition")
                if top in {"httpx", "uvicorn", "fastapi"} and path.name not in {
                    "adapters.py",
                    "models_chat.py",
                    "model_catalog.py",
                    # Public annual policy search/download is owned by this adapter.
                    "official_calendar.py",
                    "napcat.py",
                    "models_responses.py",
                    "bootstrap.py",
                    # Explicit finite test composition creates the fixed sender client.
                    "qq_test_runner.py",
                    # Fixed-destination HTTP adapters; no tool-owned sockets.
                    "native_media.py",
                    "search.py",
                    "character_recognition.py",
                    # Allowlisted public GET owner; actual URL is gated by Actions.
                    "web_fetch.py",
                    "http_api.py",
                } and not (top == "httpx" and path.name in {
                    # Exact CCIP build destination, invoked only through CharacterActions.
                    "character_pack_client.py",
                    # Fixed QZone wire; JournalPublisher/Actions own current-source/live gates.
                    "journal_qzone.py",
                }):
                    errors.append(f"{path.name}:{node.lineno}: network outside adapter/composition")
                if top == "sqlite3" and path.name not in {
                    "store.py", "policy.py", "actions.py",
                    # Own sealed backup copies only; never a runtime SQL writer.
                    "release_management.py",
                    # CLI-only evaluation owns one disposable case DB, not runtime Store.
                    "longmemeval_replay.py",
                }:
                    errors.append(f"{path.name}:{node.lineno}: SQL outside transaction owners")
                if path.name == "tools.py" and top in {"httpx", "socket", "sqlite3", "subprocess", "os"}:
                    errors.append(f"{path.name}:{node.lineno}: tool has unrestricted resource import")
        if isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            if name in {"eval", "exec", "__import__", "importlib.import_module"}:
                errors.append(f"{path.name}:{node.lineno}: dynamic execution requires explicit review")
            if (
                path.name == "conversation.py"
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"send", "send_echo", "send_sticker", "request", "request_stream"}
            ):
                ancestor = parents.get(node)
                while ancestor is not None:
                    if (
                        isinstance(ancestor, ast.Call)
                        and ast.unparse(ancestor.func) == "self.actions.execute"
                    ):
                        break
                    ancestor = parents.get(ancestor)
                if ancestor is None:
                    # Sticker source revalidation uses a named local callback;
                    # require that exact function as Actions' operation argument.
                    local = parents.get(node)
                    while local is not None and not isinstance(local, ast.AsyncFunctionDef):
                        local = parents.get(local)
                    owner = parents.get(local) if local is not None else None
                    while owner is not None and not isinstance(owner, ast.AsyncFunctionDef):
                        owner = parents.get(owner)
                    controlled = local is not None and owner is not None and any(
                        isinstance(call, ast.Call)
                        and ast.unparse(call.func) == "self.actions.execute"
                        and len(call.args) > 1 and isinstance(call.args[1], ast.Name)
                        and call.args[1].id == local.name
                        for call in ast.walk(owner)
                    )
                    if not controlled:
                        errors.append(f"{path.name}:{node.lineno}: external port bypasses actions")
            if path.name == "qq_test_runner.py" and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"post", "put", "patch", "delete", "request", "request_envelope"}:
                    errors.append(f"{path.name}:{node.lineno}: finite runner bypasses fixed sender")
if errors:
    raise SystemExit("\n".join(errors))
print("PASS: three-layer imports, resource/tool boundary, controlled external callbacks, no dynamic loading")
