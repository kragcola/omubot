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

- **话题块系统缺陷审计 + 重构评审 + 搜索审计**（待立项修复，**仅审计/评审未动代码**）：复杂多话题多人物极端模拟，发现 7 项缺陷（3 高 3 中/低）。报告 `docs/tracking/topic-block-multitopic-defects-audit-2026-06-11.md`（§1-5 缺陷，§6 用户"注册/合并/降级"提案评审，§7 学术搜索审计，§8 风险B再审）。根因：B 系列把"并发话题"建模成"参与者无序 set 并集"+无 message_id→block 反查+deque 插入序淘汰。**搜索审计关键转向**：该问题=NLP 成熟领域 conversation disentanglement（Kummerfeld 2019 IRC 语料），标准解=维护"消息→前驱边"、块=连通分量、单消息 attribute 为基本操作（**§6.3 澄清：用户"引用归入被引用块"=attribute 单条消息，正确，非 merge 块**）。**§8 风险B再审**：用户论点"引用回复复活低活跃块、绕开相似度门"结构正确（WeChat Q&R sequence-jumping 文献支持跨时复活），把风险B 收窄到"无引用接话"子集（保守~半数仍需相似度兜底）。**三条护栏**：①L2 不可"只审高活跃块"省算力（伤无引用复活）；②引用是强先验非硬真值（防 broadcast/reframe 误并）；③低活跃块衰减≠物理删除（否则 message_id 反查失败）。推荐路径 L0 边模型→L1 线性打分→L2 活跃度衰减（守护栏①）→L3 嵌入（可选）。缺陷6+B2 角色门 omubot 特有、文献不覆盖。**本轮未写任何修复代码。**
