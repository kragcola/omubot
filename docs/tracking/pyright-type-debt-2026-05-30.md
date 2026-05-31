# pyright 存量类型债清单（2026-05-30 全量扫描）

> 目的：为「是否将 `uv run pyright` 纳入 CI 门禁」提供决策依据。
> 扫描命令：`uv run pyright`（项目根，含工作树未提交改动）。
> 基线：**353 errors, 2 warnings**，分布 67 个文件。
> 进度：阶段 1 已执行（2026-05-30），清 97 个，当前 **256 errors**。
> 关联：[[maintenance-log]] 2026-05-30「清理 slang store 历史类型债」已先清掉 `services/slang/store.py` 的 19 个（同款 Optional-cursor 模式）。

---

## ⏸ 待办（2026-05-30 中止于此，下次接手从这里继续）

阶段 1 完成后**主动中止**。剩余按优先级：

- [ ] **阶段 2a — src 长尾混合（~84）**：`memory_signals.py`(8)、`memo_tools.py`(7) 等，Optional 家族为主 + 少量其他，逐文件清。仍偏机械，可复用 `_require_db()`。
- [ ] **阶段 2b — plugin override 签名债（10）**：⚠️ 非机械。需逐个对齐 plugin 基类契约（§三「方法 override 不兼容」家族），有判断成本，单独评估再动。
- [ ] **阶段 3 — CI 门禁**：`services/`+`kernel/`+`plugins/` 开 pyright，`tests/`+`scripts/dev/`（含 `slang_semantic_smoke.py` 7 个）走 `pyproject.toml` exclude；假阳性（§四）加行级 `# pyright: ignore`；建议 `--warnings` 起步。

接手提示：先 `uv run pyright 2>&1 | grep "^[0-9]* errors"` 确认当前基线（应为 256，若漂移说明期间有新债）。修法模板见 [[maintenance-log]] 同日「pyright 阶段 1」。

---

## 一、总览（按规则聚合）

| 规则 | 数量 | 占比 | 性质 |
|------|------|------|------|
| `reportOptionalMemberAccess` | 183 | 52% | `self._db: Conn \| None` 未收窄就 `.execute()` —— 运行时安全，纯类型噪音 |
| `reportOptionalSubscript` | 30 | 8% | `(await cursor.fetchone())[...]` —— 同上家族 |
| 未标 rule（参数/赋值/override 类型不匹配） | 123 | 35% | 见 §三，需逐类判断 |
| `reportIndexIssue` | 6 | 2% | 索引类型 |
| `reportPossiblyUnbound` | 3 | <1% | flow 假阳性（见 §四） |
| `reportCallIssue` | 3 | <1% | 重载不匹配（多在 test mock） |
| `reportAttributeAccessIssue` | 2 | <1% | |
| `reportOptionalIterable` / `reportOperatorIssue` / `reportMissingImports` | 各 1 | <1% | MissingImports 是可选依赖假阳性（见 §四） |

**关键结论**：`Optional(MemberAccess+Subscript)` 合计 **213 个 = 60%**，是**同一个根因的同一种修法**——和我刚清掉的 slang store 一模一样。

---

## 二、src vs tests 拆分

- **非测试代码**：186 errors，其中 119（64%）是 Optional-cursor 家族。
- **测试代码**：167 errors，其中大量是 fixture 的 `AsyncGenerator` 返回类型 + mock 的 override/参数不匹配（见 §三）。

CI 门禁通常先卡**非测试代码**（生产代码的类型安全优先级 > 测试），所以下面优先列 src。

---

## 三、按文件（非测试，count desc）

### 「纯机械修」文件 —— 一个 `_require_db()` 收窄即清零

这些文件 100% 是 Optional-cursor 家族，根因都是 `self._db: aiosqlite.Connection | None = None` 后直接 `await self._db.execute(...)`。修法照搬 slang store 的 `_require_db()`/`_fetch_scalar()` 模式（已验证零行为变更）。

| 文件 | errors | 全是 Optional 家族? | 状态 |
|------|--------|--------|------|
| `services/memory/card_store.py` | 29 | ✅ | ✅ 已清（阶段 1，2026-05-30） |
| `services/knowledge_graph/graph_writer.py` | 27 | ✅ | ✅ 已清（阶段 1，2026-05-30） |
| `services/health.py` | 15 | ✅ | ✅ 已清（阶段 1，2026-05-30） |
| `scripts/dev/slang_semantic_smoke.py` | 7 | ✅（dev 脚本，优先级低） | 待办 |

小计 **78 个**；阶段 1 已清掉前 3 个（71 个），仅剩 dev 脚本 7 个未动。

### 「混合」文件 —— Optional 家族为主 + 少量其他

| 文件 | errors | 构成 |
|------|--------|------|
| `services/knowledge_graph/store.py` | 26 | 24 Optional + 2 参数类型 ✅ 已清（阶段 1，2026-05-30，status 参数收紧为 GraphStatus） |
| `services/scheduler_rws/memory_signals.py` | 8 | 待细分 |
| `services/tools/memo_tools.py` | 7 | 待细分 |
| `services/learning_normalizer/store.py` | 5 | Optional-cursor 为主 |
| `plugins/slang/plugin.py` | 5 | 含 1 个 override 不兼容 |
| `plugins/bilibili/plugin.py` | 5 | 待细分 |
| `services/memory_consolidator/event_boundary.py` | 4 | |
| `services/conversation_archive/scanner.py` | 4 | |
| `plugins/context/plugin.py` | 4 | |
| `kernel/bus.py` | 4 | |
| 其余 27 个文件 | 各 1-3 | 长尾 |

### 「方法 override 不兼容」家族（10 个）

`plugins/*/plugin.py` 的 `on_message`/`on_command` 等覆盖签名与基类漂移（web_search/web_fetch/memo/http_api/group_admin/datetime/affection/slang 各 1）。这类是**真·签名债**——基类契约和实现签名不一致，值得修但需对齐 plugin 基类定义，非纯机械。

---

## 四、确认为假阳性 / 不该修的（白名单候选）

逐一核过，这些**不是 bug**，纳入门禁前应加 `# type: ignore` 或配置豁免：

1. **`plugins/calendar_context/service.py:21` `reportMissingImports`（chinese_calendar）**：可选依赖，代码已 `try: import ... except: = None` 守护。是 pyright 不认 try-guard，非缺依赖。
2. **`tests/test_client.py:181/183/1368` `reportPossiblyUnbound`**：`result =` 在 `with` 块内赋值，pyright 无法证明 `with` 必进入，但运行时必然执行且后面紧跟 `assert result is not None`。flow 假阳性。
3. **测试 fixture 的 `AsyncGenerator` 返回类型（12 个，6 文件）**：`@pytest_asyncio.fixture` 的 `async def ... -> StyleStore: yield ...` 缺 `AsyncGenerator[StyleStore, None]` 注解。是注解风格问题，非 bug；可批量补注解或豁免 tests/。
4. **test mock 的 `reportCallIssue` 重载不匹配**：`SimpleNamespace`/dataclass mock 构造，运行时是 duck-typing，类型检查器看不懂。

---

## 五、决策建议

**不建议一次性全清后直接上严格门禁**——353 个里混了真债、假阳性、测试注解三类，盲目清会引入噪音改动。建议分阶段：

### 阶段 1（✅ 已执行，2026-05-30）

原计划「纯机械修」4 文件 + knowledge_graph/store.py 24 个 → 约 102 个。**实际执行清掉 97 个**（`card_store.py` 29 + `graph_writer.py` 27 + `knowledge_graph/store.py` 26 + `health.py` 15），全仓 **353 → 256**。剩 `scripts/dev/slang_semantic_smoke.py` 7 个属 dev 脚本，按门禁豁免范围（阶段 3）处理，未纳入本轮。详见 [[maintenance-log]] 同日「pyright 阶段 1」。

### 阶段 2
- src 长尾混合文件逐个清（约 84 个）。
- plugin override 签名债（10 个）——需先对齐 plugin 基类契约。

### 阶段 3（门禁）
- 先对 **`services/` + `kernel/` + `plugins/`**（非测试）开 pyright 门禁，`tests/` 与 `scripts/dev/` 暂豁免（在 `pyproject.toml` 的 pyright `exclude`/`ignore` 配置）。
- 假阳性（§四）加 `# pyright: ignore[reportXxx]` 行级豁免。
- 门禁建议用 `pyright --warnings` 起步观察，再切 error 阻断。

### 工作量粗估
- 阶段 1：1-2 小时
- 阶段 2：3-4 小时（含 override 对齐）
- 阶段 3：0.5 小时（配置 + 假阳性豁免）

---

## 六、附：完整 per-file 数据

非测试文件完整列表见扫描输出；重跑命令：

```bash
uv run pyright 2>&1 | grep -E "/omubot/.*:[0-9]+:[0-9]+ - error" \
  | sed -E 's|.*/omubot/([^:]+):.*|\1|' | sort | uniq -c | sort -rn
```
