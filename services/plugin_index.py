"""Local plugin package index for Phase 7 plugin ecosystem governance."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from kernel.manifest import check_version
from services.version import VERSION


def _sha256_file(path: Path | None) -> str:
    if path is None or not path.is_file():
        return ""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                digest.update(chunk)
    except Exception:
        return ""
    return digest.hexdigest()


def _relative_label(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except Exception:
        return str(path)


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def _read_json_object(path: Path | None) -> tuple[dict[str, Any], str]:
    if path is None or not path.is_file():
        return {}, ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, str(exc)
    if not isinstance(data, dict):
        return {}, "json object required"
    return data, ""


def _read_manifest(path: Path | None) -> tuple[dict[str, Any], str]:
    data, error = _read_json_object(path)
    if error == "json object required":
        return {}, "manifest must be an object"
    return data, error


def _safe_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _normalize_source_claim(value: str) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"trusted", "repo", "repo_local", "local", "internal"}:
        return "trusted"
    if raw in {"linked", "symlink"}:
        return "linked"
    if raw in {"external", "outside_repo", "outside"}:
        return "external"
    if raw in {"missing", "none"}:
        return "missing"
    return raw


class PluginIndexService:
    """Build a local-only plugin package registry snapshot."""

    def __init__(
        self,
        plugin_root: str | Path = "plugins",
        *,
        repo_root: str | Path = ".",
        omubot_version: str = VERSION,
    ) -> None:
        self._plugin_root = Path(plugin_root)
        self._repo_root = Path(repo_root)
        self._omubot_version = str(omubot_version or "0.0.0")

    def build_index(self, *, bus: Any = None) -> dict[str, Any]:
        discovered = self._scan_local_packages()
        loaded = {
            str(getattr(plugin, "name", "") or ""): plugin
            for plugin in list(getattr(bus, "plugins", []) or [])
            if getattr(plugin, "name", "")
        }
        entries: list[dict[str, Any]] = []
        for name in sorted(set(discovered) | set(loaded)):
            entries.append(self._build_entry(name=name, discovered=discovered.get(name), plugin=loaded.get(name)))

        governance_counts = Counter(item["governance_status"] for item in entries)

        summary = {
            "indexed_count": len(entries),
            "loaded_count": sum(1 for item in entries if item["loaded"]),
            "not_loaded_count": sum(1 for item in entries if not item["loaded"]),
            "local_only": True,
            "manifest_missing_count": sum(1 for item in entries if item["manifest_status"] == "missing"),
            "manifest_invalid_count": sum(1 for item in entries if item["manifest_status"] == "invalid"),
            "compatibility_issue_count": sum(1 for item in entries if item["compatibility_status"] == "incompatible"),
            "external_source_count": sum(1 for item in entries if item["source_status"] == "external"),
            "warning_count": sum(1 for item in entries if item["warnings"]),
            "ready_to_load_count": governance_counts.get("ready", 0),
            "review_required_count": governance_counts.get("review", 0),
            "blocked_count": governance_counts.get("blocked", 0),
            "attention_count": governance_counts.get("attention", 0),
            "signature_verified_count": sum(1 for item in entries if item["signature_status"] == "verified"),
            "signature_issue_count": sum(
                1
                for item in entries
                if item["signature_status"] in {"invalid", "mismatch"}
                or item["source_attestation_status"] in {"invalid", "mismatch"}
            ),
            "unsigned_external_count": sum(
                1
                for item in entries
                if item["source_status"] in {"external", "linked"}
                and item["signature_status"] == "missing"
            ),
        }
        return {
            "summary": summary,
            "install_policy": {
                "mode": "local_only",
                "remote_install_enabled": False,
                "detail": "当前只索引本地插件包，不支持 Web 端远程下载安装或执行未知插件。",
            },
            "plugin_root": str(self._plugin_root),
            "omubot_version": self._omubot_version,
            "entries": entries,
        }

    def entry_for(self, name: str, *, bus: Any = None) -> dict[str, Any] | None:
        index = self.build_index(bus=bus)
        for entry in index["entries"]:
            if entry["name"] == name:
                return entry
        return None

    def _scan_local_packages(self) -> dict[str, dict[str, Any]]:
        root = self._plugin_root
        if not root.is_dir():
            return {}

        discovered: dict[str, dict[str, Any]] = {}
        directory_names: set[str] = set()

        for subdir in sorted(root.iterdir()):
            if not subdir.is_dir():
                continue
            plugin_file = subdir / "plugin.py"
            manifest_file = subdir / "plugin.json"
            if not plugin_file.exists() and not manifest_file.exists():
                continue
            name = subdir.name
            directory_names.add(name)
            discovered[name] = {
                "name": name,
                "kind": "directory" if plugin_file.exists() else "capability",
                "package_path": subdir,
                "entry_path": plugin_file if plugin_file.exists() else None,
                "manifest_path": manifest_file if manifest_file.exists() else None,
                "signature_path": (subdir / "plugin.sig") if (subdir / "plugin.sig").exists() else None,
                "config_default_path": (subdir / "config.default.json") if (subdir / "config.default.json").exists() else None,
                "config_schema_path": (subdir / "config.schema.json") if (subdir / "config.schema.json").exists() else None,
                "config_paths": sorted(
                    [
                        path
                        for path in subdir.iterdir()
                        if path.is_file()
                        and path.suffix in {".toml", ".json"}
                        and path.name not in {"plugin.json", "config.default.json", "config.schema.json"}
                    ]
                ),
            }

        for entry in sorted(root.iterdir()):
            if not entry.is_file() or entry.suffix != ".py" or entry.name.startswith("__"):
                continue
            name = entry.stem
            if name in directory_names:
                continue
            manifest_path = entry.with_suffix(".json")
            discovered[name] = {
                "name": name,
                "kind": "legacy_single_file_unsupported",
                "package_path": entry,
                "entry_path": entry,
                "manifest_path": manifest_path if manifest_path.exists() else None,
                "signature_path": entry.with_suffix(".sig") if entry.with_suffix(".sig").exists() else None,
                "config_default_path": None,
                "config_schema_path": None,
                "config_paths": sorted(
                    [
                        path
                        for path in root.iterdir()
                        if path.is_file()
                        and path.stem == name
                        and path.suffix in {".toml"}
                    ]
                ),
            }

        for manifest_path in sorted(root.glob("*.json")):
            name = manifest_path.stem
            if name in discovered or (root / f"{name}.py").exists():
                continue
            manifest_data, _ = _read_manifest(manifest_path)
            if not manifest_data:
                continue
            discovered[name] = {
                "name": name,
                "kind": "legacy_manifest_unsupported",
                "package_path": manifest_path,
                "entry_path": None,
                "manifest_path": manifest_path,
                "signature_path": manifest_path.with_suffix(".sig") if manifest_path.with_suffix(".sig").exists() else None,
                "config_default_path": None,
                "config_schema_path": None,
                "config_paths": [],
            }

        return discovered

    def _build_entry(self, *, name: str, discovered: dict[str, Any] | None, plugin: Any = None) -> dict[str, Any]:
        package_path = Path(discovered["package_path"]) if discovered and discovered.get("package_path") else None
        entry_path = Path(discovered["entry_path"]) if discovered and discovered.get("entry_path") else None
        manifest_path = Path(discovered["manifest_path"]) if discovered and discovered.get("manifest_path") else None
        signature_path = Path(discovered["signature_path"]) if discovered and discovered.get("signature_path") else None
        config_default_path = Path(discovered["config_default_path"]) if discovered and discovered.get("config_default_path") else None
        config_schema_path = Path(discovered["config_schema_path"]) if discovered and discovered.get("config_schema_path") else None
        config_paths = [Path(path) for path in (discovered.get("config_paths") or [])] if discovered else []
        kind = str(discovered.get("kind") or "") if discovered else ""

        if plugin is not None and entry_path is None:
            module = __import__(plugin.__class__.__module__, fromlist=["__name__"])
            raw_module_file = str(getattr(module, "__file__", "") or "").strip()
            if raw_module_file:
                module_file = Path(raw_module_file)
                entry_path = module_file
                if module_file.name == "plugin.py":
                    kind = "directory"
                    package_path = module_file.parent
                    candidate_manifest = module_file.parent / "plugin.json"
                    candidate_signature = module_file.parent / "plugin.sig"
                    candidate_default = module_file.parent / "config.default.json"
                    candidate_schema = module_file.parent / "config.schema.json"
                    manifest_path = candidate_manifest if candidate_manifest.is_file() else None
                    signature_path = candidate_signature if candidate_signature.is_file() else None
                    config_default_path = candidate_default if candidate_default.is_file() else None
                    config_schema_path = candidate_schema if candidate_schema.is_file() else None
                else:
                    kind = kind or "legacy_single_file_unsupported"
                    package_path = module_file
                    candidate_manifest = module_file.with_suffix(".json")
                    candidate_signature = module_file.with_suffix(".sig")
                    manifest_path = candidate_manifest if candidate_manifest.is_file() else None
                    signature_path = candidate_signature if candidate_signature.is_file() else None

        manifest_data, manifest_error = _read_manifest(manifest_path)
        manifest_status = "missing"
        if manifest_path is not None and manifest_path.is_file():
            manifest_status = "invalid" if manifest_error else "ok"

        source_status = "missing"
        source_label = "未找到插件入口"
        if kind == "capability" and manifest_path is not None and manifest_path.exists():
            source_status = "trusted"
            source_label = "系统能力声明"
        elif kind.startswith("legacy_"):
            source_status = "legacy"
            source_label = "旧版根目录插件文件已不支持"
        if entry_path is not None and entry_path.exists():
            if kind.startswith("legacy_"):
                source_status = "legacy"
                source_label = "旧版根目录单文件插件已不支持"
            elif _is_within(entry_path, self._plugin_root):
                source_status = "linked" if entry_path.is_symlink() else "trusted"
                source_label = "仓库内目录插件" if kind == "directory" else "仓库内插件入口"
            else:
                source_status = "external"
                source_label = "外部本地插件入口"

        min_version = str(
            getattr(plugin, "min_omubot_version", "")
            or manifest_data.get("min_omubot_version")
            or manifest_data.get("min_omu_version")
            or ""
        ).strip()
        if min_version:
            compatibility_ok = check_version(self._omubot_version, min_version)
            compatibility_status = "compatible" if compatibility_ok else "incompatible"
        else:
            compatibility_ok = True
            compatibility_status = "unspecified"

        entry_sha256 = _sha256_file(entry_path)
        manifest_sha256 = _sha256_file(manifest_path)
        signature_data, signature_error = _read_json_object(signature_path)
        signature_status = "missing"
        signature_scheme = ""
        signature_signer = ""
        signature_key_id = ""
        signature_signed_at = ""
        source_attestation_status = "missing"
        declared_source_origin = ""
        declared_source_entry = ""
        if signature_path is not None and signature_path.is_file():
            if signature_error:
                signature_status = "invalid"
            else:
                signature_scheme = str(signature_data.get("scheme") or "sha256").strip().lower()
                signature_signer = str(signature_data.get("signer") or "").strip()
                signature_key_id = str(signature_data.get("key_id") or "").strip()
                signature_signed_at = str(signature_data.get("signed_at") or "").strip()
                declared_entry_hash = str(signature_data.get("entry_sha256") or "").strip().lower()
                declared_manifest_hash = str(signature_data.get("manifest_sha256") or "").strip().lower()
                source_data = signature_data.get("source")
                if source_data is not None and not isinstance(source_data, dict):
                    source_attestation_status = "invalid"
                elif isinstance(source_data, dict):
                    declared_source_origin = _normalize_source_claim(source_data.get("origin") or "")
                    declared_source_entry = str(
                        source_data.get("entry_path")
                        or source_data.get("relative_entry")
                        or ""
                    ).strip()
                    if declared_source_origin or declared_source_entry:
                        source_matches = True
                        if declared_source_origin and declared_source_origin != source_status:
                            source_matches = False
                        actual_relative_entry = (
                            _relative_label(entry_path, self._repo_root)
                            if entry_path is not None
                            else ""
                        )
                        plugin_root_relative_entry = (
                            _relative_label(entry_path, self._plugin_root)
                            if entry_path is not None
                            else ""
                        )
                        plugin_root_prefixed_entry = (
                            f"{self._plugin_root.name}/{plugin_root_relative_entry}"
                            if plugin_root_relative_entry
                            else ""
                        )
                        normalized_declared_entry = declared_source_entry.replace("\\", "/")
                        normalized_candidates = {
                            actual_relative_entry.replace("\\", "/"),
                            plugin_root_relative_entry.replace("\\", "/"),
                            plugin_root_prefixed_entry.replace("\\", "/"),
                        }
                        if declared_source_entry and normalized_declared_entry not in normalized_candidates:
                            source_matches = False
                        source_attestation_status = "verified" if source_matches else "mismatch"
                if signature_status != "invalid":
                    if signature_scheme and signature_scheme != "sha256":
                        signature_status = "invalid"
                        signature_error = f"unsupported signature scheme: {signature_scheme}"
                    else:
                        hash_matches = True
                        if declared_entry_hash and declared_entry_hash != entry_sha256.lower():
                            hash_matches = False
                        if declared_manifest_hash and declared_manifest_hash != manifest_sha256.lower():
                            hash_matches = False
                        signature_status = "verified" if hash_matches else "mismatch"

        warnings: list[str] = []
        if manifest_status == "missing":
            warnings.append("缺少 plugin.json，当前只能依赖类属性暴露元数据。")
        elif manifest_status == "invalid":
            warnings.append(f"plugin.json 解析失败：{manifest_error}")
        if source_status == "external":
            warnings.append("插件入口位于 plugins/ 根目录之外，请人工确认来源与代码可信度。")
        elif source_status == "linked":
            warnings.append("插件入口是符号链接，请确认链接目标仍受本地可信路径管理。")
        elif source_status == "missing":
            warnings.append("本地索引没有找到插件入口文件。")
        if compatibility_status == "incompatible":
            warnings.append(f"当前 Omubot v{self._omubot_version} 不满足插件最低版本要求 {min_version}。")
        if signature_status == "missing":
            if source_status in {"external", "linked"}:
                warnings.append("外部或符号链接插件未提供 plugin.sig 来源声明，建议补充再接入。")
        elif signature_status == "invalid":
            warnings.append(f"plugin.sig 无法校验：{signature_error or '格式无效'}")
        elif signature_status == "mismatch":
            warnings.append("plugin.sig 中声明的文件指纹与当前插件文件不一致。")
        if source_attestation_status == "invalid":
            warnings.append("plugin.sig 中的 source 声明格式无效。")
        elif source_attestation_status == "mismatch":
            warnings.append("plugin.sig 中的来源声明与当前入口路径或来源类型不一致。")
        if plugin is None and kind != "capability":
            warnings.append("本地包已被索引，但当前没有加载到运行时。")
        if kind.startswith("legacy_"):
            warnings.append("根目录单文件插件/清单已取消支持，请迁移为 plugins/<name>/plugin.py 目录插件。")

        governance_status, governance_label, action_hint = self._governance_for_entry(
            loaded=plugin is not None,
            source_status=source_status,
            manifest_status=manifest_status,
            compatibility_status=compatibility_status,
            signature_status=signature_status,
            source_attestation_status=source_attestation_status,
        )
        if kind.startswith("legacy_"):
            governance_status = "blocked"
            governance_label = "旧格式已禁用"
            action_hint = "迁移为 plugins/<name>/plugin.py + plugin.json + config.default.json + config.schema.json 后再接入。"
        elif kind == "capability":
            governance_status = "healthy"
            governance_label = "系统能力声明"
            action_hint = ""

        return {
            "name": name,
            "loaded": plugin is not None,
            "kind": kind or "unknown",
            "display_name": _safe_dict(getattr(plugin, "display_name", None) or manifest_data.get("display_name") or {}),
            "description": str(getattr(plugin, "description", "") or manifest_data.get("description") or ""),
            "version": str(getattr(plugin, "version", "") or manifest_data.get("version") or "0.0.0"),
            "priority": int(getattr(plugin, "priority", 100) if plugin is not None else manifest_data.get("priority", 100) or 100),
            "tier": str(getattr(plugin, "tier", "") or manifest_data.get("tier") or "user"),
            "toggle_policy": str(getattr(plugin, "toggle_policy", "") or manifest_data.get("toggle_policy") or "runtime"),
            "category": str(getattr(plugin, "category", "") or manifest_data.get("category") or "general"),
            "capabilities": list(getattr(plugin, "capabilities", None) or manifest_data.get("capabilities") or []),
            "store": _safe_dict(getattr(plugin, "store", None) or manifest_data.get("store") or {}),
            "source_status": source_status,
            "source_label": source_label,
            "package_path": str(package_path) if package_path is not None else "",
            "entry_path": str(entry_path) if entry_path is not None else "",
            "manifest_path": str(manifest_path) if manifest_path is not None else "",
            "signature_path": str(signature_path) if signature_path is not None else "",
            "config_default_path": str(config_default_path) if config_default_path is not None else "",
            "config_schema_path": str(config_schema_path) if config_schema_path is not None else "",
            "config_paths": [str(path) for path in config_paths],
            "manifest_status": manifest_status,
            "manifest_error": manifest_error,
            "entry_sha256": entry_sha256,
            "manifest_sha256": manifest_sha256,
            "signature_status": signature_status,
            "signature_error": signature_error,
            "signature_scheme": signature_scheme or ("sha256" if signature_status != "missing" else ""),
            "signature_signer": signature_signer,
            "signature_key_id": signature_key_id,
            "signature_signed_at": signature_signed_at,
            "source_attestation_status": source_attestation_status,
            "declared_source_origin": declared_source_origin,
            "declared_source_entry": declared_source_entry,
            "compatibility_status": compatibility_status,
            "min_omubot_version": min_version,
            "compatibility_ok": compatibility_ok,
            "omubot_version": self._omubot_version,
            "metadata_source": "plugin.json" if manifest_status == "ok" else "class",
            "manifest_name": str(manifest_data.get("name") or ""),
            "warnings": warnings,
            "governance_status": governance_status,
            "governance_label": governance_label,
            "action_hint": action_hint,
            "blocked_reason": "legacy_single_file_unsupported" if kind.startswith("legacy_") else "",
            "relative_entry": _relative_label(entry_path, self._repo_root) if entry_path is not None else "",
            "relative_manifest": _relative_label(manifest_path, self._repo_root) if manifest_path is not None else "",
            "relative_signature": _relative_label(signature_path, self._repo_root) if signature_path is not None else "",
        }

    def _governance_for_entry(
        self,
        *,
        loaded: bool,
        source_status: str,
        manifest_status: str,
        compatibility_status: str,
        signature_status: str,
        source_attestation_status: str,
    ) -> tuple[str, str, str]:
        if loaded:
            if source_status == "missing":
                return ("attention", "运行入口异常", "补齐或修复插件入口文件，再检查运行时装载链路。")
            if signature_status in {"invalid", "mismatch"} or source_attestation_status in {"invalid", "mismatch"}:
                return ("attention", "签名校验异常", "修复 plugin.sig 的指纹或来源声明，避免继续装载来源不明或已漂移的插件包。")
            if compatibility_status == "incompatible":
                return ("attention", "运行版本告警", "升级 Omubot 或调整插件最低版本声明，避免后续运行期失配。")
            if manifest_status == "invalid":
                return ("attention", "清单损坏", "修复 plugin.json，避免权限、来源和版本元数据继续失真。")
            if source_status in {"external", "linked"}:
                if signature_status == "verified" and source_attestation_status == "verified":
                    return ("attention", "来源已声明", "当前外部来源已附带 plugin.sig 声明，但仍建议人工确认并视情况迁回仓库内正式目录。")
                return ("attention", "来源待确认", "确认外部路径或符号链接目标可信，并考虑迁回仓库内正式目录。")
            if manifest_status == "missing":
                return ("attention", "缺少清单", "补充 plugin.json，声明来源、权限、最低版本和扩展元数据。")
            return ("healthy", "运行正常", "")

        if source_status == "missing":
            return ("blocked", "入口缺失", "补齐 plugins/<name>/plugin.py 后，再考虑把它纳入启动装载。")
        if signature_status in {"invalid", "mismatch"} or source_attestation_status in {"invalid", "mismatch"}:
            return ("blocked", "签名校验失败", "修复 plugin.sig 中的指纹或来源声明后，再考虑把该插件接入运行时。")
        if manifest_status == "invalid":
            return ("blocked", "清单损坏", "先修复 plugin.json 解析错误，避免把无效元数据带入运行时。")
        if compatibility_status == "incompatible":
            return ("blocked", "版本不兼容", "升级 Omubot，或下调插件要求的最低版本后再接入。")
        if source_status in {"external", "linked"}:
            if signature_status == "verified" and source_attestation_status == "verified":
                return ("review", "来源已声明", "已提供 plugin.sig 指纹与来源声明；仍建议人工确认后再决定是否纳入本地正式插件目录。")
            return ("review", "来源待确认", "先核对来源路径与代码可信度，再决定是否纳入本地正式插件目录。")
        return ("ready", "待接入运行时", "插件包本身可读，下一步检查启动装载链路，确认是否要注册到 PluginBus。")
