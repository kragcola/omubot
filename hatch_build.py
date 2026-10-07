"""Fail release builds that omit frontend assets; editable installs stay lightweight."""

import re
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        if version == "editable":
            return
        directory = Path(self.root) / "src/omubot_new/web_static"
        index = directory / "index.html"
        if not index.is_file():
            raise RuntimeError("Web build missing. Run npm ci and npm run build in web before packaging.")
        assets = re.findall(r'(?:src|href)="/web/([^\"]+)"', index.read_text(encoding="utf-8"))
        if not assets or any(not (directory / name).is_file() for name in assets):
            raise RuntimeError("Web assets incomplete. Rebuild web before packaging.")
