# issue17 D3 实施清单 — 角色识别全套落地（sidecar 基线，已编码）

> 2026-06-01。承接 [issue17-pre-part0-landing-v2-2026-05-31.md](../tracking/issue17-pre-part0-landing-v2-2026-05-31.md)。
> **架构基线订正（用户拍板）**：v2 文档假设进程内 CCIP；实际 P3 已落地 ccip-sidecar，本次以 **sidecar 为准**。recognizer 维持 HTTP client，CCIP 重依赖留 sidecar（bot 镜像不装 numpy/onnxruntime，保 P3 依赖隔离）。
> 决策：① sidecar 为准；② 建 omubot 侧本地 registry DB（relation per-bot + 持久缓存）；③ 本轮跳过 AnimeTrace；④ Phase 3 做 mood 信号注入。

## 0. 目标与不做

**做**：sidecar 加 `/embed`；建 build_character_pack 工具；omubot 侧本地 `character_recognition.db`（relation per-bot + 持久识别缓存）；recognizer 改读本地 DB + L2 缓存；admin 角色管理页（列表/relation 编辑/上传/重扫）；relation→mood 瞬时衰减 nudge。

**不做**：进程内 CCIP（保 sidecar 隔离）；AnimeTrace（本轮跳过，可后续单加）；不碰 napcat（D6）；flag 默认仍 false（需真实参考图才有可见价值）。

## 1. 改动清单（四列：旧 → 新 / 文件 / 类型 / 回归）

| # | 旧 | 新 | 文件 | 类型 | 回归 |
| --- | --- | --- | --- | --- | --- |
| A1 | sidecar 仅 `/identify` `/health` | 加 `POST /embed`（图→768维 CCIP 特征），build 工具借模型提嵌入 | ccip-sidecar/server.py | 新增端点 | curl /embed 返回 dim=768 ✅ |
| A2 | 无 pack 构建工具 | `tools/build_character_pack.py`：参考图目录→调 /embed→均值向量→写 .charpack（manifest+npz） | tools/build_character_pack.py（新） | 新增工具 | 合成 2 角色 pack→sidecar 加载→identify 命中 ✅ |
| B1 | relation 来自 charpack manifest（P3.3 权宜） | 本地 `character_registry` 表（character_id/name/aliases/relation），per-bot 真相源 | services/media/character_registry_db.py（新） | 新增 DB | scan_and_sync + admin-edit-survive-resync 单测 ✅ |
| B2 | 识别缓存仅 sidecar 内存（L1，重启丢） | omubot L2 持久缓存 `image_recognition_cache`（完整 SHA-256 PK，aiosqlite+WAL+checkpoint） | services/media/recognition_cache.py（新） | 新增 DB | get/put/prune/cancel-path 单测 ✅ |
| B3 | recognizer relation/name 读 manifest | 优先读 registry DB；identify 前查 L2 缓存短路、命中后回填 | services/media/character_recognizer.py | 改逻辑 | cache-short-circuit 单测（第二次不调 sidecar）✅ |
| B4 | plugin 只构造 recognizer | flag 开时构造 registry_db + cache，on_connect 跑 scan_and_sync，注入 ctx | plugins/chat/plugin.py:1006 | 改构造 | rebuild 后 in-container 链路测试 ✅ |
| C1 | router 识别块无持久缓存 | 缓存逻辑内聚进 recognizer.identify（router 无需改缓存接线） | services/media/character_recognizer.py | 重构 | 同 B3 |
| D1 | 无角色管理 API | `admin/routes/api/characters.py`：GET 列表+stats+sidecar / PATCH relation / POST reload / POST upload(zip) | admin/routes/api/characters.py（新）+ __init__.py 注册 | 新增路由 | GET 返回 401(已注册) ✅；in-container PATCH 持久化 ✅ |
| D2 | 无角色管理页 | SPA 角色识别页（Calm Ops：MetricCard+DataTable+relation 下拉+上传），路由+菜单 | admin/frontend/src/views/characters/CharactersView.vue（新）+ router/index.ts + SideMenu.vue | 新增页 | vue-tsc + npm run build ✅ |
| E1 | mood 无识别信号 | `MoodEngine.register_recognition_signal`（self→valence+/friend→openness+，30min 线性衰减，cap 0.2，invalidate cache） | plugins/schedule/mood.py | 新增机制 | nudge 累加/衰减/cap/known-noop 单测 ✅ |
| E2 | router 识别命中无 mood 钩子 | 命中 self/friend → 经 ctx.mood_engine 注册信号（群 session_id=`group_{gid}`、私聊 `private_{uid}`，对齐 client 约定） | kernel/router.py:811,1183,1556 | 接线 | session_id 约定核对 client.py:2451 ✅ |

## 2. 数据落点

```
config/character_packs/<name>.charpack/   # bind-mount config(rw)，热加载；manifest.json + embeddings.npz
storage/character_recognition.db          # named volume；两表：character_registry + image_recognition_cache
                                          #  + character_pack_meta（增量检测）
```
嵌入向量留 sidecar（charpack npz 由 sidecar 加载）；relation/name/缓存留 omubot DB。多 bot 时 relation per-bot 隔离（supervisor §A.7 分野），本轮单 bot。

## 3. D1 同模式 / D2 cancel-path / D4 证据

- **D1**：aiosqlite+WAL+`close_with_checkpoint` 照搬 slang_store/card_store（services/storage/sqlite.py）；admin route 工厂照搬 stickers.py；SPA 页照搬 StickersView（AppPage/AppCard/MetricCard/EmptyState/PageToolbar）。
- **D2**：tests/test_character_registry_db.py 含 `test_registry_db_cancel_path_leaves_no_partial_row`（cancel update → DB 仍可读、relation 非旧即新无中间态）。
- **D4 证据**：sidecar /embed dim=768；build pack 2 角色 e2e identify 命中 diff=0.0001；in-container 链路（部署镜像）scan_and_sync+admin-edit-survive+cache roundtrip 全过；全量 pytest 2321 passed；ruff/pyright 0 err；vue-tsc+build 通过；napcat Created=05-28 未变（D6）。

## 4. 回滚 / flag

- flag `vision.character_recognition.enabled=false`（默认）→ 全旁路回 desc_cache→sticker_store→VL，registry/cache 不构造。
- `git restore` 各新文件 + 改动文件；DB 在 named volume 可单独 `docker volume` 清；charpack 在 config 可删。
- **flag 未开**：需真实角色参考图（凤笑梦/熟人立绘）才有"描述变准"可见价值；合成 pack 仅验证管线。开 flag 前提：① 用 build_character_pack 建真 pack ② 放 config/character_packs/ ③ config 开 enabled ④ restart bot。
