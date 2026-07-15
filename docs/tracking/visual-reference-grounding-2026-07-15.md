# 图片人物指代错误修复

> 状态：in_progress
> mode: bug
> 最后更新：2026-07-15 CST
> 当前下一步：精确提交、bot-only 部署与公开 silent 群负向验证。
> 阻塞：无。
> 回滚入口：回退本任务代码提交后只 rebuild/recreate bot；不碰 NapCat。

## Resume Capsule

- objective: 修复用户问“这是谁/他是谁”时，低质量角色候选压过图片本身，或历史图片/人物污染本轮指代的问题。
- primary_production_anchor: 群 `993065015`，提问消息 `1048261473`，2026-07-13 11:26:05 CST；引用 bot 图片 `980229022` / sticker `stk_01db713d`。
- primary_observed: 图片实际为普瑞塞斯，群友随后明确纠正“这是普瑞塞斯”；sidecar 把它 matched 为 `fuji_miyako`，bot 回答“藤都子さん喔”。原图 SHA-256 `01db713d78976e6194272c863637423dda4887e40f181c0717d007c5f6fd06ff`。
- reproduced_score: 当前同图 `/identify` 与 `/identify-multi` 均返回 `difference=0.17129391431808472`、`threshold=0.17847511429108218`，只留约 `0.00718` 余量；sticker/Qwen 只描述托腮思考女孩，不含藤都子，错误身份唯一明确来源为边缘 CCIP 命中。
- secondary_anchor: 群 `993065015`，消息 `221123511`，2026-07-15 11:39:28 CST。本轮只有 `[at:bot] 这是谁`，无图片、无 reply；Thinker/主模型继承四分钟前草薙宁宁图片并错称“这张图里的是宁宁”。
- root_cause: (1) sidecar 硬 threshold 被直接等同于人类可见“可信身份”，没有额外安全余量；(2) 完整历史、历史图片像素和 RAG 可与当前 pending 一同进入模型，没有本轮视觉证据所有权；(3) 引用图片只保留描述文本，缓存后的 `image_ref` 没进入最终主模型输入。
- invariant: 原始 timeline、图片缓存、日志与研究证据不改写；视觉约束只在请求期构造模型输入。
- deploy_boundary: 只允许 `docker compose build bot` 与 `docker compose up -d --no-deps --force-recreate bot`；NapCat 不 restart/recreate。

## Acceptance Contract

- [x] 有本轮直接/引用图片时，“这是谁/他是谁/她是谁”等视觉人物指代只绑定本轮图片。
- [x] 本轮无图片、无引用图片时，“这是谁/图里是谁”等显式视觉指示语不得从历史图片或历史人物名猜身份；应要求补发/引用图片。
- [x] 无图“他是谁/她是谁”保留正常文本指代能力，不强制误判为看图问题。
- [x] 本轮存在图片时，当前图片像素与当前视觉识别结果优先于聊天历史中的人物名。
- [x] 引用图片缓存成功时，`image_ref` 必须随本轮内容进入最终主模型输入，不能只传描述文本。
- [x] sidecar matched 若距离接近硬阈值，必须降级为“未能可信识别”，不得把候选角色名暴露给回答模型。
- [x] 视觉人物请求只保留当前 pending 请求批次并强制 `retrieve_mode=skip`，历史文本/摘要/像素/RAG 均不得竞争当前指代。
- [x] 视觉证据不足或冲突时表达不确定，不用旧上下文补全人物身份。
- [ ] 公开 silent 群保持零出站。

## Evidence

| Evidence | Result |
| --- | --- |
| `group_messages` row `878418` | `Religion(3286443160): 这是谁`，`content_json` 为纯字符串，无图片块 |
| OneBot event `221123511` | `[at:qq=384801062] 这是谁`，无 reply/image segment |
| Thinker first output | 直接生成“是我最爱的宁宁呀”，结构解析失败 |
| Thinker retry | `thought='是宁宁哦'` |
| Final assistant row `878422` | “这张图里的是宁宁——草薙宁宁” |
| Prior visual context | 11:34 的引用图识别为草薙宁宁，证明错误身份来自历史视觉上下文 |
| Corrected image sample | `stk_01db713d` 实图为普瑞塞斯；群友 11:27 明确纠正 |
| Reproduced sidecar score | `fuji_miyako 0.1712939143 <= 0.1784751143`，边缘硬命中 |

## Todo

- [x] 核对生产数据库与当天日志，冻结真实样本
- [x] 审计直接图片、引用图片、timeline、Thinker 与主模型链路
- [x] RED/GREEN：无本轮视觉证据时禁止继承历史图片
- [x] RED/GREEN：当前/引用图片形成显式视觉所有权并优先
- [x] RED/GREEN：视觉请求隔离当前 pending，并强制禁用 RAG
- [x] RED/GREEN：引用图片 `image_ref` 透传，描述失败仍保留像素
- [x] RED/GREEN：边缘 CCIP matched 降级，不渲染候选姓名
- [x] focused/full pytest、Ruff、Pyright、独立 review
- [ ] 精确提交、bot-only 部署、运行态与 silent 群验证

## Files Touched

| File | Change | Status |
| --- | --- | --- |
| `docs/tracking/ACTIVE.md` | 指向本 bug | in_progress |
| `docs/tracking/visual-reference-grounding-2026-07-15.md` | 证据、合同、验证与回滚台账 | in_progress |
| `kernel/router.py` | 引用图片像素透传；识别日志补 difference/threshold | done |
| `services/media/visual_evidence.py` | 人类可见身份增加 0.03 安全余量；边缘命中隐藏候选姓名 | done |
| `services/llm/client.py` | 当前视觉指代约束、上下文隔离、禁 RAG | done |
| `tests/test_build_group_messages.py` | 历史污染与当前图片所有权回归 | done |
| `tests/test_render_message_character_recognition.py` | 引用图与生产边缘误判回归 | done |
| `tests/test_thinker_runtime_state.py` | Thinker hybrid 被强制收敛为 skip | done |

## Verification

| Check | Result |
| --- | --- |
| RED 1 | 3 failed / 15 passed：无图 grounding、有图 grounding、引用图 ref 均缺失 |
| RED 2 | 3 failed / 17 passed：历史 pixels 未隔离、描述失败丢 ref |
| RED 3 | 2 failed / 19 passed：边缘命中仍输出藤都子、候选禁答约束缺失 |
| RED 4 | 2 failed / 9 passed：视觉请求仍保留 finalized 历史 |
| Focused | 视觉/消息构造最终 21 passed；Thinker runtime 27 passed |
| Expanded | 视觉、timeline、LLMClient、AnimeTrace、cache 聚合 135 passed / 5 skipped |
| Static | scoped Ruff clean；Pyright 0 errors / 0 warnings；diff-check clean |
| Full before final isolation | 3513 passed / 17 skipped / 167 warnings |
| Final expanded | 173 passed / 5 skipped / 64 warnings |
| Final full | 3519 passed / 17 skipped / 172 warnings；额外 thread warnings 为既有 aiosqlite fixture 清理债 |
| Independent review | 初轮 3 Important 均 RED→GREEN；语义裁定后最终 0 Critical / 0 Important |
| Same-pattern scan | 人物名进入 prompt 仅 `render_visual_evidence`；图片块主链仅 router/client，无第二个旁路 |
