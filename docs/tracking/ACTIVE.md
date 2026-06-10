# Active Omubot Work

> This is the compaction/new-session recovery entrypoint. Keep it short.

## Current

- mode: task
- tracker: docs/tracking/character-pack-batch-fill-2026-06-06.md
- objective: Character pack gap filling / 人物角色识别训练包缺口补齐。
- status: active
- next_step: `zh_virtual_singers` 已清零；继续补 `bangdream` 10 个 `chibi` 缺口，或 `ja_virtual_singers` 的 `lily:expression`、`haru:chibi`。优先找官方/授权、单角色、同页强绑定来源；不得重跑已记录负例。
- last_verified: 2026-06-09 23:48 日V猫村いろは AHS press VOCALOID4 插图右侧 SD 裁剪 chibi 小批上线，`ja_virtual_singers` 34 人 / 302 图；猫村从 `chibi` 缺口变为无缺口。sidecar `/health` ok，4 packs / 136 characters；新增 PNG crop SHA256 `0837299bfc3a30f9f13d27e5a47c7c29f8e34f289c6b03da67cba334d345c011`，`/identify` diff `0.04974418133497238`，`/identify-multi` 1 条命中 `nekomura_iroha`，全 136 top8 collision top1 `nekomura_iroha`、top2 `dongfang_zhizi`，margin `0.1687510535120964`；NapCat Created 仍 `2026-05-28T10:56:06.736616338Z running 0`。
- rollback: 本轮日V回滚可恢复 `config/character_packs/backups/ja_virtual_singers.charpack.bak-20260609-234739-pre-iroha-chibi-active` 到 `config/character_packs/ja_virtual_singers.charpack` 后执行 `docker compose restart ccip-sidecar`。更早日V MAYU 回滚为 `ja_virtual_singers.charpack.bak-20260609-224212-pre-mayu-atpress-chibi-active`；更早日V猫村 expression 回滚为 `ja_virtual_singers.charpack.bak-20260609-211700-pre-iroha-expression-active`；更早中V回滚为 `zh_virtual_singers.charpack.bak-20260609-200302-pre-dongfang-bilibili-expression-active`。角色包任务只允许重启 `ccip-sidecar`，不得 recreate NapCat。

## Recovery Order

1. Read `.workspace/agent-session-state.md` if present.
2. Read this file.
3. Read the tracker above.
4. Run `git status --short`.
5. Continue from `next_step`.

## Notes

- Worktree contains unrelated Living Persona/admin/runtime dirty files; never use `git add -A`.
- Pytest baseline on this host uses `PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-}`.

## Pending (separate line — not the Current task)

- **表情包配图 · 心情二轴改造**：**已实施 2026-06-10**（decision_provider + client.py + 3 测试，全量 pytest 2588 passed，待下次部署 rebuild 随车）。方案 + 落地差异：`docs/tracking/sticker-mood-two-axis-plan-2026-06-09.md`；维护日志见 maintenance-log 2026-06-10 顶部条目。
  - 落地要点：删 `_blocked_by_mood`/英文 mood 集合死代码 → 二轴乘子（energy 缩放概率地板 0.5、valence 偏置选图 query）；thinker:false 改 ×0.6 降权非否决（D1=a）；affection_stage 真实接线（B3）；base_frequency 接 `GroupStickerMode`（off→熄火）；D1 同模式顺修 `_should_force_kaomoji_sticker_round` 同源死代码。
  - F1 已满足：`config/config.json` 的 `sticker_placement.enabled` 已为 true，部署即生效。回滚：config 关该开关秒级熄火，或 `git checkout` 两源文件。
  - NapCat 不得 recreate（D6）。
