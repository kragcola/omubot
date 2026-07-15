# 富消息上下文补全

> 状态：complete
> mode: task-bug
> 最后更新：2026-07-15 CST
> 当前下一步：无；实现、部署与运行验证均已完成。
> 阻塞：无。
> 回滚入口：代码回退后只 rebuild/recreate bot；不改 schema、不删除 timeline/message log/研究数据、不碰 NapCat。

## Resume Capsule

- objective: 补齐嵌套引用回复、history backfill 富消息、silent_learn 富消息上下文，同时保持公开群严格零出站。
- deployed: 实现提交 `d51a7d4`；image `56f51b2ce8d5...` / container `0f7f47c3ffae...` / runtime commit `d51a7d41bed5b031659e09dcfd148c10e6cd4e0a`。
- confirmed_gaps:
  - active reply renderer 只消费 adapter 提供的一层 `reply.message`，不递归其中的 `reply.data.id`。
  - `services/history_backfill.py::_extract_content` 只处理 text/face/image，reply/json/forward 静默丢弃。
  - silent_learn 在 rich renderer 前返回，timeline 只接收 `semantic_plain_text`。
- invariants: 不改原始 event；新增的 silent rich renderer/递归器不触发 API、下载、视觉、LLM 或出站；递归有深度/节点/segment/字符/循环预算；局部失败保留外层与兄弟内容。

## Acceptance Contract

- [x] active reply→reply 至少展开两层发送者与文本。
- [x] 祖父级 image/json/forward 有可读摘要；图片像素只沿既有 active 视觉路径处理。
- [x] reply 循环、深度、节点/segment、字符均有硬上限。
- [x] 单层 `get_msg` 失败局部降级，不丢当前引用和本条正文。
- [x] history backfill 保留 reply/json/forward 的可读结构，并保持 text/face/image 行为。
- [x] history backfill 不调用 LLM，不启动视觉识别，不因单条富消息失败阻断整批。
- [x] silent_learn timeline 保存本条富消息结构文本；本次新增 renderer/递归器不下载图片、不调 OneBot API、不调用视觉/LLM、不主动发送。
- [x] active/silent/off 群访问策略不变；公开群保持零出站（部署固定窗已闭环）。
- [x] 原始 timeline、message log、研究事件与数据库 schema 不改写。

## Test Ledger

| ID | Experiment | Actual Result | Conclusion | Date |
| --- | --- | --- | --- | --- |
| RICH-000 | 静态检查 active reply / history backfill / silent early return | 三条路径分别只支持一层、只支持 text/face/image、只写 plain text | 富消息丢失发生在入站 render 边界，不是模型理解问题 | 2026-07-15 |
| RICH-001 | 对测试群真实 `get_msg` 链只读取样：text、image、JSON、reply→forward | `message_id` 为 number，`reply.data.id` 为 string；adapter 只展开最外层一次；祖父层保留 `reply{id}`；forward 同时带 embedded `content` 与 `id` | active 从 adapter 已展开层继续 `get_msg(int(id))`；embedded forward 优先；cycle key 规范化为 string | 2026-07-15 |
| RICH-002 | 静态核对 silent 既有插件副作用 | `silent_safe` 贴纸学习既有路径可下载/get_image，并可能调用 emotion vision | 本任务不虚报“silent 全链路绝对零网络”；硬合同收窄为新增 rich renderer/递归器零 I/O/视觉/LLM/出站，既有行为不在本次扩面 | 2026-07-15 |
| RICH-003 | 独立 Test Writer 对 active/history/suppressed 三路径写核心 RED | 8 个明确 assertion failure；active/private 缺祖父，history 丢 JSON/forward/reply，suppressed timeline 仅当前正文；无 import/collection error | 测试先于生产实现，根因可稳定复现 | 2026-07-15 |
| RICH-004 | 共享 renderer + 三路径接线后 focused/边界验证 | 新任务测试 28 passed；扩展 rich/forward/image/history 回归 54 passed；suppressed/outbound 聚合 76 passed；Ruff clean、Pyright 0 | reply/image/json/forward、local-only history、zero-new-I/O suppressed 与硬预算均转绿 | 2026-07-15 |
| RICH-005 | NapCat HTTP 只读回放真实 text/JSON/image/reply→forward 样本 | text/JSON/image 各只续取祖父 ID 一次并得到 2 层 quote；embedded forward API=0；四类目标摘要均存在 | 当前 host 实现符合真实 adapter/NapCat 形态；未发送 QQ、未改容器 | 2026-07-15 |
| RICH-006 | `uv run pytest -q` | 3560 passed / 17 skipped / 174 warnings | 全量回归通过；warning 为既有 aiohttp/NoneBot deprecation 与 aiosqlite fixture 收尾 | 2026-07-15 |
| RICH-007 | 两轮独立 code review + review-closure RED/GREEN | 首轮 6 Important、二轮 3 Important；共新增 13 个明确 RED，逐项关闭；最终 closure 0 Critical / 0 Important | 关闭 quote 原子闭合、silent segment 保序、image ref 超时保留、history 单消息预算、malformed node、image/forward timeout 等盲区 | 2026-07-15 |
| RICH-008 | 最终 expanded/static/full | expanded 164 passed；Ruff clean；Pyright 0；diff-check clean；full 3580 passed / 17 skipped / 183 warnings | 最终实现与新增 closure tests 全量通过；warning 仍为既有 deprecation/aiosqlite fixture 收尾 | 2026-07-15 |
| RICH-009 | bot-only 部署、容器内真实链回放、自然 silent reply + live DB、固定零出站窗 | image `56f51b2...` / container `0f7f47...` / commit `d51a7d4...`；四类真实链全部 balanced/expected；自然 silent message `1859087016` 在日志与 `messages.db` 均含完整 quote/marker/current/content_json；UTC 08:22:54–08:27:26 双方 43 入站、send/poke/error=0 | 生产实现、持久 timeline mirror 与公开群负向合同闭环；NapCat 未操作 | 2026-07-15 |

## Decisions

- 共享模块只负责 raw OneBot segment 的有界结构渲染，不持有 bot/session/cache/vision/LLM；外部内容只能通过显式 resolver 注入。
- active/private 可注入带 fetch 上限、timeout、cache 与 visited-ID 的 `get_msg/get_forward_msg` resolver；adapter 已展开的最外层不得重复获取。
- active 引用图片继续走现有 cache → sticker → character → VL enrichment，并保留 `image_ref`；history/silent 的引用内图片只输出可读摘要。
- history 优先按本次 `get_group_msg_history` 结果建立本地 `message_id` 索引；窗口外 reply 降级为 ID marker，不新增启动期 `get_msg`。embedded forward 展开，ID-only forward 降级为 marker。
- silent/muted-active 只使用 adapter 已提供的一层 reply 与本条 embedded 结构；不为更深 reply/forward 注入 resolver。off 群仍在更早访问门返回。
- 每条富消息普通异常局部降级；`CancelledError` 保持传播，不伪装成成功。

## Todo

- [x] 冻结真实 reply 链与各层 segment 形态
- [x] 设计共享的无副作用富消息文本 renderer 与 active-only enrichment 边界
- [x] RED/GREEN：active nested reply
- [x] RED/GREEN：history backfill reply/json/forward
- [x] RED/GREEN：silent_learn 富消息文本保留与零新增 I/O/零出站
- [x] focused/full pytest、Ruff、Pyright、独立 review
- [x] 精确提交、bot-only 部署、运行态与 silent 群验证

## Files Touched

| File | Change | Status |
| --- | --- | --- |
| `docs/tracking/ACTIVE.md` | 关闭本 task-bug | complete |
| `docs/tracking/rich-message-context-completion-2026-07-15.md` | 合同、实验与交付台账 | complete |
| `services/onebot_segments.py` | 共享有界 segment/reply/embedded-forward renderer | deployed |
| `kernel/router.py` | active/private resolver + suppressed rich timeline | deployed |
| `services/history_backfill.py` | local reply index + JSON/forward + 单条隔离 + no vision | deployed |
| `tests/test_*rich*context*.py` / `tests/test_onebot_segment_renderer_boundaries.py` | RED/GREEN、预算、负向与回归 | green |
