# 进阶话题块 Phase 2 派生层迁移清单

## 旧到新

| ID | 旧行为 | 新行为 | 兼容与回滚 |
| --- | --- | --- | --- |
| P2-1 | `research_events.db user_version=1`，仅 raw `research_message_event` | raw DB 保持不变；新增独立 `research_topic_assignments.db user_version=1` | 派生库失败/删除不影响 raw capture |
| P2-2 | 在线 `TopicBlockTracker` 只返回 block | additive evidence API 返回 reason/score/margin，`observe()` 仍返回相同 block | 旧 scheduler/tests 不改调用合同；可单独回退 evidence API |
| P2-3 | `b1/b2` 仅进程内有效 | projector 用 algorithm fingerprint + group + seed event 生成确定性 UUIDv5 | 不把 UUID 写回 raw；换版本生成并存新结果 |
| P2-4 | raw event 无版本化归属 | `(event_uid, algorithm_version)` append-only assignment | 冲突 insert 不覆盖；关闭 Phase 2 即停止新写入 |
| P2-5 | 无 raw→logical utterance 映射 | versioned membership 支持一 utterance 多 raw event | 默认确定性 gap grouping；删除派生表不损伤 raw |
| P2-6 | raw event 尚无可复现实验批次 | 显式 CLI 对固定 cutoff/digest 全量稳定重放并原子提交 run | 不接启动/ingress/scheduler；runner 可随时重跑 |
| P2-7 | reply 前驱解析可被未来复用 message ID 污染；ISO cutoff 曾按 TEXT 比较 | projector 使用因果 message index 与 timezone-aware instant cutoff；语义写入 algorithm fingerprint | 新版本与旧派生结果并存；raw 不改写 |
| P2-8 | restore 只验业务 schema，取消期间 rollback 可被再次取消 | restore 同验 migration ledger；projection rollback shield cancellation | 不可恢复 payload fail-closed；取消后连接仍可复用 |

## 顺序

1. RED：独立派生 schema v1、future-version、raw immutable。
2. GREEN：迁移与 store append/read API。
3. RED：tracker evidence 不改变原归属结果。
4. GREEN：additive evidence API。
5. RED：projector 稳定 UUID、版本并存、utterance 多成员、幂等重跑。
6. GREEN：离线 projector + CLI + input digest/cutoff/run 元数据。
7. 回归：legacy topic tracker/corpus、Phase 1 recorder/cancel/error paths。
8. 部署前 migration-profile backup；bot-only recreate；生产只读验证。

## 不变量

- `research_message_event` 的列、主键和既有 row 内容不变。
- Phase 2 不参与回复决策，不进入 prompt/cache prefix，不发送消息。
- assignment/membership 永不用 UPDATE 改写历史版本。
- derived write failure 不得接触 raw writer，也不得阻断 ingress/outbound send。
- NapCat 不 restart/recreate/down。
