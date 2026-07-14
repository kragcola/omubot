# Food 插件一次性提示持久化修复

## Objective

修复 `/吃什么` 的“本消息只显示一次”提示在 bot 重启后重复发送的问题，并审计 FoodPlugin 全部进程内状态，确保永久状态、短期状态和配置状态各有明确 owner；不得再用测试直接修改私有内存集合绕过持久化合同。

## Status

- mode: task-bug
- status: complete
- started_at: 2026-07-15 CST
- completed_at: 2026-07-15 06:38 CST
- current_step: complete
- next_step: none
- implementation_commit: `232de5a`
- deployment: image `b3a40ac03839...`（tag `omubot-bot:food-tutorial-durable-20260715`）/ container `e9c44251c4de...` / restart=0 / OOM=false
- rollback: `omubot-bot:pre-food-tutorial-fix-20260715`=`e31c2a630cd3...`；只允许 bot-only recreate

## Root Evidence

- `FoodPlugin._tutorial_shown` 是进程内 `set[str]`，由 `653b7b3` 引入；启动不恢复，关闭不持久化。
- 2026-07-15 06:00:57，用户 `1416930401` 在群 `993065015` 执行 `/吃什么`，NapCat 记录一次 tutorial 出站和一次推荐出站。
- 生产 `memory_cards.db` 已存在 `food_tutorial:1416930401`（2026-05-09）、`food_pref:1416930401`、`food_served:1416930401`，当前代码仍重复提示，证明 durable marker 被实现忽略。
- 现有测试通过 `plugin._tutorial_shown.add("123")` 跳过提示，未覆盖跨实例/跨重启行为。
- 双 FoodPlugin/CardStore 实例的确定性交错测试复现：两个实例都看到 marker 缺失，先提交者创建 marker，后提交者的 `get_or_create_series()` 返回已有 marker；旧实现无法区分创建者，最终教程发送 2 次。

## State Ownership Audit

| State | Intended lifetime | Current owner | Verdict |
| --- | --- | --- | --- |
| tutorial shown | durable, per user, at-most-once | CardStore `food_tutorial:<user>` empty series + UNIQUE claim | fixed |
| preferences/location | durable | CardStore cards/series | correct |
| search toggle | durable config | PluginConfigStore | correct |
| served foods | durable history | CardStore series | correct |
| recent rejection | 30-minute session memory | process-local dict | intentional ephemeral |
| feedback window | 120-second per `(user, group)` interaction | process-local dict, opportunistic global expiry cleanup | fixed |
| feedback running/tasks | in-flight per `(user, group)` task | tuple-key guard + strong task refs + shutdown cancel/gather | fixed |
| web search cache | 30-minute optimization | process-local dict | intentional ephemeral |
| preference cache | none | removed `_pref_cache` dead writes and stale docstring | fixed |

## Implemented Contract

- Tutorial marker uses the production-compatible key `food_tutorial:<user>` and is persisted before outbound.
- Same-instance concurrency is serialized by `_tutorial_claim_lock`; cross-instance/process authority is the unique `card_series.series_key` constraint plus direct `create_series()`.
- Existing `food_tutorial:*` rows are honored. Users with `food_pref:*` or `food_served:*` history receive a silent marker migration and no tutorial.
- Missing CardStore or lookup/create failure fails closed: tutorial is skipped, recommendation continues.
- Send failure or cancellation after claim keeps the marker. This intentionally guarantees at-most-once, not exactly-once.
- Expired feedback windows are reclaimed globally at message/recommendation entry; in-flight feedback is keyed by `(user_id, group_id)`, matching pending ownership.
- `_pref_cache` was removed because it was written/invalidated but never read.

## Test Ledger

| ID | Command / Input | Actual Result | Conclusion | Date |
| --- | --- | --- | --- | --- |
| F0 | runtime logs + read-only production `memory_cards.db` + source/blame scan | durable tutorial row exists, but current source only checks `_tutorial_shown` | persistence contract regressed; tests bypassed it | 2026-07-15 |
| F1 RED | `pytest tests/test_food_plugin.py::test_food_tutorial_is_durable_across_plugin_restart -q` before implementation | marker missing after first instance | process-local set cannot survive restart | 2026-07-15 |
| F2 RED/GREEN | `pytest tests/test_food_plugin.py::test_food_existing_history_migrates_without_showing_tutorial -q` | initially tutorial + recommendation; after history migration only recommendation | legacy active users are not re-onboarded | 2026-07-15 |
| F3 RED/GREEN | deterministic two-instance interleaving test | RED tutorial count 2; GREEN tutorial count 1 | `get_or_create` was not a claim API; direct UNIQUE insert closes race | 2026-07-15 |
| F4 | `pytest tests/test_food_tutorial_durability.py -q` | 6 passed | no-store, lookup failure, same-instance concurrency, existing marker, send error/cancel contracts covered | 2026-07-15 |
| F5 RED/GREEN | `pytest tests/test_food_feedback_state.py -q` | RED: 64 stale windows and second-group task absent; GREEN: 2 passed | feedback cleanup and owner-key defects closed | 2026-07-15 |
| F6 | Food/manifest/command/lifecycle aggregate | 66 passed | Food behavior, task lifecycle, mute and manifest surfaces stable | 2026-07-15 |
| F7 | scoped Ruff + scoped Pyright | 0 / 0 | changed production and test files statically clean | 2026-07-15 |
| F8 | final `pytest -q` | 3383 passed / 17 skipped / 162 warnings | full repository regression green; one pre-existing aiosqlite thread-close warning remains | 2026-07-15 |
| F9 | full `ruff check` / full `pyright` | blocked by unrelated coursework/research/IPv6 worktree files; Pyright 339 errors | do not modify unrelated user work; scoped gates are authoritative for this change | 2026-07-15 |
| F10 | final independent review | 0 Critical / 0 Important / 0 Minor | current Food source/tests require no further change | 2026-07-15 |
| F11 | image parity + runtime read-only probe | host/image Food SHA256 0 mismatch; DB quick_check=ok; existing user claim=False; marker count 3→3 | deployed image honors production marker without sending | 2026-07-15 |
| F12 | post-connect runtime window | Admin 200; OneBot connected; bot 0 error/Traceback/hook timeout; NapCat 0 outbound/send/error after connection | deployment healthy; window had 0 group inbound, so not evidence of live public-group guard traffic | 2026-07-15 |

## Same-Pattern Scan

- No other plugin uses a process-local set/dict as a permanent one-time tutorial marker.
- Sticker `_seen/_resolved` has bounded/expiring semantics; Echo state is five-minute session state; Mood nudges decay; Birthday sent-log is JSON durable; Slang daily/time gates persist in store meta.
- Residual migration limit: users who saw the old tutorial but have neither tutorial marker nor food preference/served history have no durable evidence and may see it once after upgrade. This is unavoidable without reconstructable data and is recorded as a P3 best-effort limit.

## Constraints

- Persist the claim before outbound to guarantee at-most-once behavior across normal restart and send failure.
- CardStore unavailable/read/write failure must fail closed: skip the tutorial, continue food recommendation, and log the reason.
- One process may receive concurrent commands for one user; only one command may claim/send the tutorial.
- Do not persist short-term rejection, feedback, or search cache as part of this fix.
- Bot-only rebuild if production code changes; never operate NapCat.
- Do not stage unrelated deep-delivery skill drafts, character-pack notes, coursework, research, NapCat data, or local artifacts.

## Deployment Evidence

- Build: `docker compose build bot`; strict plugin layout passed; build context 218.23 kB.
- Deploy: `docker compose up -d --no-deps --force-recreate bot`; only `qq-bot` was replaced.
- Runtime: Admin 200, Application startup complete, OneBot connected, outbound guard and protocol trace installed.
- Source parity: `plugins/food/plugin.py` and `plugin.json` host/image SHA256 match exactly.
- Production DB: read-only `PRAGMA quick_check=ok`; `food_tutorial:1416930401` exists; no write occurred during the semantic probe.
- NapCat remained container `19f6cf13607c...`, image `cde89d766604...`, StartedAt `2026-07-09T22:51:47.963549084Z`, restart=0, OOM=false.
