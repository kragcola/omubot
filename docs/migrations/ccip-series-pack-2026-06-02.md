# CCIP 系列角色 Pack 支持 — 实施记录

> 2026-06-02。承接 2026-06-01 的 CCIP sidecar、网页录入、PJSK 多形态录入与 `/identify-multi`。

## 目标

把角色包从“一个 `.charpack` 基本等于一个角色”升级为“一部作品/系列一个 `.charpack`，内部包含多个角色”，同时保持现有识别、per-bot relation/name DB、sidecar 依赖隔离不变。

## 关键改动

- **继承式 manifest**：顶层支持 `series`、`work`、`relation_default`；角色项可省略 `work/relation`，读取时按 `character.work -> manifest.work`、`character.relation -> relation_default -> known` 继承。旧 per-character manifest 继续有效。
- **sidecar 构建入口**：新增 `POST /build-series-pack`，按 `characters_json[].file_prefix || character_id` 把 multipart 图片归组，输出一个多角色 `.charpack` zip；样例图写入 `samples/<character_id>/<idx>.jpg`。`/build-pack` 保持兼容。
- **管理端**：`POST /api/admin/characters/build-series` 转发 sidecar 并落盘；`POST /api/admin/characters/merge-series` 可把已存在的安全单角色 pack 合并成系列 pack；`GET /characters` 增补 `pack/series/work/pack_character_count/mergeable`；样例接口兼容系列 pack 嵌套样例和旧单角色样例。前端录入弹窗增加“系列 pack”tab，支持“从新图片生成”和“合并已有角色”两种入口。
- **启动自动合并**：新增 `services/media/character_pack_migrator.py`，在 `scan_and_sync()` 前运行。它只合并“单包单角色、manifest+npz 完整、effective work 非空”的包；用 zipfile 复制 `.npz` 内的 `.npy` 成员，不把 numpy 引入 bot runtime；原包移动到 `.merged/<series>/`，不删除。
- **CLI 同源**：`tools/build_character_pack.py` 改为调用 `/build-series-pack`，新增 `--work`、`--series`，生成与管理端一致的 manifest 和样例布局。

## 验证

- `uv run ruff check ...`：改动 Python 文件全通过。
- `uv run pyright ...`：0 errors。
- 定向 pytest：`54 passed, 2 skipped`。本机 `readline` import 会 139，测试通过预置 no-op `readline` module 规避；2 个 sidecar builder 用例因 bot venv 无 numpy 被跳过，符合 sidecar 依赖隔离。
- 前端：`./node_modules/.bin/vue-tsc --noEmit` 通过；`npm run build` 通过，生成新的 `admin/static/index.html`。

## 回滚

- 关闭启动合并：`vision.character_recognition.auto_merge_series_packs = false` 后重启 bot。
- 已合并数据回滚：把 `config/character_packs/.merged/<series>/*.charpack` 移回 `config/character_packs/`，删除对应系列 `.charpack`，再调用 `/api/admin/characters/reload`。
- 代码回滚：撤销本次改动文件并 rebuild bot/sidecar；NapCat 不需要也不允许 recreate/down+up。
