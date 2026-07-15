# 嵌套聊天记录读取修复

> 状态：complete
> mode: bug
> 最后更新：2026-07-15 CST
> 当前下一步：无。本 tracker 完成；离线 history backfill / silent_learn rich timeline 残留另行立项。
> 阻塞：无。烤群真实三层样本已经冻结；自建探针均已撤回。
> 回滚入口：切 `omubot-bot:pre-nested-forward-20260715-2027858` 后只 recreate bot；不碰 NapCat。

## Resume Capsule

- objective: 让 bot 能读取 QQ 嵌套聊天记录，而不是只看到 `«嵌套转发»` 占位符。
- test_group: 烤群 `993065015`，属于 active 测试群，可发送最小测试；公开群继续严格零出站。
- confirmed_static: `kernel/router.py::_render_forward_msg` 能调用 `get_forward_msg` 展开一层；节点内容遇到 `type=forward` 时只追加 `«嵌套转发»`，没有递归读取。
- runtime_confirmed: NapCat 4.15.0 在可获取的顶层 `get_forward_msg` 响应中，同时提供下一层 `forward.data.id` 与完整 `forward.data.content`；内层 ID 不能单独 `get_forward_msg`，必须优先消费内嵌 content。
- boundaries: 有界递归、循环检测、总字符/节点预算、单层失败局部降级；不得无限递归或因内层失败丢掉外层文本。
- deployment: 只允许 bot-only build/recreate；NapCat 不 restart/recreate/down。

## Acceptance Contract

- [x] 一层合并转发行为保持。
- [x] 嵌套 forward 节点能展开下一层发送者与文本。
- [x] 至少支持两层真实 QQ 聊天记录嵌套。
- [x] 深度、总节点数和总字符数有硬上限。
- [x] 重复/循环 forward ID 不会无限递归。
- [x] 单个内层获取失败只显示局部降级标记，外层内容继续保留。
- [x] 图片、表情、@、文件等既有一层摘要不回归。
- [x] 原始 timeline、message log、研究数据不改写。
- [x] 公开 silent 群保持零出站。

## Test Ledger

| ID | Experiment | Actual Result | Conclusion | Date |
| --- | --- | --- | --- | --- |
| NEST-000 | 静态检查 `_render_forward_msg` | 一层文本/图片等可读；`forward` 仅 `«嵌套转发»` | 当前不具备嵌套内容读取 | 2026-07-15 |
| NEST-001 | 烤群发送一层探针；`send_group_forward_msg` -> `get_msg` -> 撤回 | 发送响应提供相同的 `res_id`/`forward_id`；自己发送的卡片经 `get_msg` 是 `json` segment，不是 `forward` segment | 不能从顶层 `get_msg.message` 猜嵌套 forward 形态 | 2026-07-15 |
| NEST-002 | 把真实 inner `forward_id` 放入自定义 node 的 `forward.data.id`，发送外层后读取并撤回两张卡 | API 接受请求，但静默丢弃自定义 node 内的 forward segment，仅保留前后 text | `send_group_forward_msg` 自定义 node 不能构造真实嵌套样本 | 2026-07-15 |
| NEST-003 | 外层 node 通过 `data.id` 引用 inner 卡片消息，读取并撤回两张卡 | 外层卡发送成功，但 `get_forward_msg` 返回的 node.message 为空 | 引用节点构造同样不能代表 QQ 客户端真实嵌套 | 2026-07-15 |
| NEST-004 | 只读读取烤群真实消息 `2078207657` / forward A `7662630301810237144` | A 内嵌 B `7662630301810237148`，B 内嵌 C `7662630301810237152`，C 为 33 nodes（32 text + 1 image）；bot 日志只留下 `QQ用户(1094950020): «嵌套转发»` | 根因是 renderer 丢弃已有的 `forward.data.content` | 2026-07-15 |
| NEST-005 | 分别对 B/C ID 调 `get_forward_msg` | 两次均 `retcode=200`，`消息已过期或者为内层消息，无法获取转发消息` | 内嵌 content 是主路径；按 ID API 只能作为 content 缺失时的兼容回退 | 2026-07-15 |
| NEST-006 | RED：`pytest -q tests/test_nested_forward_rendering.py` | 9 failed / 1 passed；失败均为缺少递归、fallback、循环/截断占位的行为断言 | 测试能抓住当前缺陷，不是导入/收集假红 | 2026-07-15 |
| NEST-007 | GREEN + 烤群 A 实时 API 回放 | 10 passed；A 只调用 1 次，展开 35 nodes 为 33 sender lines + 1 image，851 chars，无旧占位/失败/截断 | 新 renderer 与 NapCat 4.15.0 真实三层结构匹配 | 2026-07-15 |
| NEST-008 | review 收口后全量测试与定向静态检查 | 3532 passed / 17 skipped；任务 Ruff clean；任务 Pyright 0；diff-check clean | 本任务代码无已知测试/类型/格式回归；全仓 Ruff/Pyright 仍被用户现有 coursework/research/IPv6 与旧测试基线阻断 | 2026-07-15 |
| NEST-009 | 两轮独立实现 review + 两轮增量 closure | 最终 0 Critical / 0 Important；字符预算后不再扫描后续 segment，非 dict/空 segment 也受 1000 总上限 | 递归与资源预算无部署阻断项 | 2026-07-15 |
| NEST-010 | bot-only 部署 + 容器内真实 A→B→C 回放 | image `a1792c614fec...` / container `bd58abf979f3...`；只请求 A，35 nodes / 35 segments -> 33 sender lines + 1 image，983 chars | 实际运行镜像可读取真实三层聊天记录 | 2026-07-15 |
| NEST-011 | UTC `06:39:27..06:41:57` silent 负向窗 | bot 20 群入站 / 15 silent；bot send=0/poke=0/error=0；NapCat 21 群入站/群出站=0 | 公开群严格零出站；NapCat 唯一 error 为 bot 替换瞬间预期 WS 拒绝 | 2026-07-15 |

### Runtime evidence commands

以下调用均经 `Authorization: Bearer <redacted>` 访问本机 `127.0.0.1:29300`；探针消息用 `delete_msg` 清理，NapCat/bot 均未重启：

```bash
curl -H 'Content-Type: application/json' -d '{"message_id":2078207657}' \
  http://127.0.0.1:29300/get_msg
curl -H 'Content-Type: application/json' \
  -d '{"message_id":"7662630301810237144"}' \
  http://127.0.0.1:29300/get_forward_msg
curl -H 'Content-Type: application/json' \
  -d '{"message_id":"7662630301810237148"}' \
  http://127.0.0.1:29300/get_forward_msg
```

冻结结构：只有 A 可通过 API 读取；A 的 `forward.data.content` 已包含 B node，B 的同名字段已包含 C 的 33 个 nodes。生产实现不得依赖 B/C ID 再取。

## Todo

- [x] 定位烤群 ID 与当前 forward renderer
- [x] 确认现有测试账号均退出登录，不重启/重建它们
- [x] 烤群真实协议实验并撤回测试消息
- [x] RED：两层展开、深度/循环/失败预算
- [x] GREEN：有界递归 renderer
- [x] focused/full pytest、Ruff、Pyright、独立 review
- [x] 精确提交、bot-only 部署、运行态与 silent 群验证

## Same-pattern scan and residuals

- 实时入站只有 `kernel/router.py::_render_message -> _render_forward_msg` 负责把 forward 内容交给 LLM，本次已覆盖。
- `plugins/bilibili/plugin.py::_collect_segment_text` 只把顶层 forward ID 当作低噪声搜索文本，不是聊天记录 renderer，不应复制递归网络读取。
- `services/history_backfill.py::_extract_content` 只处理 text/face/image，离线补历史仍会忽略整个 forward；`silent_learn` 实时路径在 rich renderer 前返回。两者是已确认的独立残留，不影响本次 active 烤群实时读取结论，也未被误报为已修。
- 原始 event、timeline、message log 与研究库结构均未修改；递归只生成当前请求期的扁平文本。
- review 残留 Minor：已有可见 parts 后若出现异常长的纯尾随空白，可能按原始长度保守触发截断；不会越过 2000 字/100 节点/1000 segment 上限，正常 NapCat 数据不受影响。

## Files Touched

| File | Change | Status |
| --- | --- | --- |
| `docs/tracking/ACTIVE.md` | 指向本 bug，完成后复位 | complete |
| `docs/tracking/nested-chat-records-2026-07-15.md` | 实验、合同、验证与回滚台账 | complete |
| `kernel/router.py` | 有界递归 nested forward renderer | complete (`2027858`) |
| `tests/test_nested_forward_rendering.py` | 递归、预算、失败与回归黑盒测试 | complete |
| `maintenance-log.md` | durable 行为、部署与回滚记录 | complete |
