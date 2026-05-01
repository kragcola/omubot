"""Omubot 插件清单与版本工具。

PluginManifest 描述插件元数据，用于发现、打包和分发。
parse_semver / check_version 提供 SemVer 版本解析与约束检查。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class PluginManifest:
    """插件清单——描述插件的完整元数据。

    与 AmadeusPlugin 类属性分开：Manifest 可独立于代码存在（如 plugin.json），
    而类属性是运行时的真实值。发现时以类属性为准，Manifest 用于分发/校验。
    """

    name: str
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    license: str = "MIT"
    homepage: str = ""
    priority: int = 100
    dependencies: dict[str, str] = field(default_factory=dict)
    min_omu_version: str = "0.1.0"

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "license": self.license,
            "homepage": self.homepage,
            "priority": self.priority,
            "dependencies": self.dependencies,
            "min_omu_version": self.min_omu_version,
        }


# SemVer regex: major.minor.patch with optional pre-release
_SEMVER_RE = re.compile(
    r"^(\d+)\.(\d+)\.(\d+)(?:-[a-zA-Z0-9._]+)?(?:\+[a-zA-Z0-9._]+)?$"
)


def parse_semver(version: str) -> tuple[int, int, int]:
    """解析 SemVer 字符串为 (major, minor, patch) 三元组。

    预发布标识（-alpha.1）和构建元数据（+build）被忽略。
    无效版本返回 (0, 0, 0)。
    """
    m = _SEMVER_RE.match(version.strip())
    if m is None:
        return (0, 0, 0)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def check_version(actual: str, constraint: str) -> bool:
    """检查 actual 版本是否满足 constraint 约束。

    支持的约束操作符：
    - ">=1.2.0", ">1.0.0", "<=2.0.0", "<2.0.0", "==1.0.0"
    - "^1.2.3" — 兼容版本（>=1.2.3, <2.0.0）
    - "~1.2.3" — 近似版本（>=1.2.3, <1.3.0）
    - "*" — 任意版本
    - 纯版本号（如 "1.2.3"）— 等效于 ">=1.2.3"
    """
    constraint = constraint.strip()
    actual_t = parse_semver(actual)

    if constraint == "*":
        return True

    # 操作符前缀匹配
    op_match = re.match(r"^(>=|<=|>|<|==|\^|~)\s*(\S.*)", constraint)
    if op_match:
        op = op_match.group(1)
        ver_str = op_match.group(2).strip()
    else:
        op = ">="
        ver_str = constraint

    req_t = parse_semver(ver_str)

    if op == "==":
        return actual_t == req_t
    if op == ">=":
        return actual_t >= req_t
    if op == "<=":
        return actual_t <= req_t
    if op == ">":
        return actual_t > req_t
    if op == "<":
        return actual_t < req_t
    if op == "^":
        # ^1.2.3: >=1.2.3, <2.0.0 (unless major is 0, then ^0.x.y pins minor)
        upper = (0, req_t[1] + 1, 0) if req_t[0] == 0 else (req_t[0] + 1, 0, 0)
        return actual_t >= req_t and actual_t < upper
    if op == "~":
        # ~1.2.3: >=1.2.3, <1.3.0
        upper = (req_t[0], req_t[1] + 1, 0)
        return actual_t >= req_t and actual_t < upper

    return False
