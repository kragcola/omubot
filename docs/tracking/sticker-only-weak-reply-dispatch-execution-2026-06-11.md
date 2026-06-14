# STICKER_ONLY 弱回复激活 派单 — 第四档 light_kind（执行追踪）

> 状态：2026-06-11 立。**接单人：待指派。** 本文是弱回复机制「阶段3」的执行派发版——把"一张表情就够、不必出文字"的场景作为弱回复的第四种载体接通。
>
> 范围：弱回复机制（强/弱二分 + 四档 `ResponseClass`）的 closing/greeting/companion 三态 **已全线上线**（commit `6e50f7d`，06-07 审计 C1/C2 已修，live 实证 06-14 companion 走修复后路径正确）。本批只补**最后一档 STICKER_ONLY**：thinker 判定"这一轮一张表情足矣"→ 直发语义检索选出的表情、**不出文字**。前置（sticker_store 已迁 SQLite + `search_by_intent` + `ocr_text` 三态）均已就绪。
>
> 配套（冲突时以**本文 §0 决策冻结表**为准）：[弱回复机制设计](weak-reply-mechanism-design.md) §阶段3、[06-07 审计](_archive/weak-reply-audit-2026-06-07.md)（已归档，方案A 已落地）、[语义检索实施](sticker-semantic-retrieval-impl.md)（前置项）。
>
> **执行原则（覆盖任何冲突项，逐条照做）：**
> 1. **复用优先，禁止新建管线**——弱回复短路已有成熟入口 `_handle_light_reply`，表情发送已有成熟路径 `_send_post_reply_sticker_if_needed(force_send=True)`。本批是**在已有入口加一个分支**，不是新写一套。任何"为将来灵活性"的抽象、参数、配置开关，未经验收人点头一律不写。
> 2. **每步自带：D1 grep 证据 + 验证命令 + 回滚命令**——三者缺一不算闭环。动手前先跑 D1 grep，把实际行号/输出贴回本文对应回执位置。
> 3. **遇到与本文不符的现实（行号变了/文件不在/命令报错）→ 停，记录到 §5 偏差表，等验收人确认，不要自行猜着改。**
> 4. **死代码红线（本批核心纪律，见 §0 F1/F2 与 §6）**：`ResponseClass.STICKER_ONLY` 这个 enum 成员**当前全仓零消费者**，弱回复真实链路走 thinker `light_kind` + `_handle_light_reply`，**不读 ResponseClass**。本批激活走 light_kind 链路；scheduler 的 `slot.last_response_class` 仅作可观测标记（已有 SILENCE/FULL_REPLY/LIGHT_ACK 写入），STICKER_ONLY 是否回填该字段见 §0 F4——**不要为了"让 enum 有人用"去给 ResponseClass 接一条没人消费的分支**。
> 5. **D5：跑全量 pytest 前先 `pkill -9 -f pytest`**，避免与 IDE 抢 sqlite 文件锁死锁。`PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-}`。
> 6. **NapCat 红线**：本批纯 bot 侧逻辑（thinker prompt + client 分支 + 测试），不触 NapCat、不需重启容器；本地验证用 pytest，**禁止**任何需重启/重配 NapCat 的手段。真要看 live 发图，用 `localhost:29300` OneBot HTTP API 被动观察，不动 bot/napcat 进程。
> 7. **回滚底线**：原地改 `thinker.py` / `client.py`，回滚 = `git revert` 该 PR。thinker prompt 不产出新 light_kind → `_handle_light_reply` 新分支自然永不进入（行为回到三态弱回复）。每步落地前确认这条可用。
> 8. **语言**：用户可见串（表情发送无文字，故无新用户串）/ identity 配置用中文；代码、注释、docstring、日志、commit 用英文。

---

## 0. 决策冻结表（执行者不得改；与设计/审计冲突时以本表为准）

| # | 冻结决策 | 来源 | 执行含义 |
| --- | --- | --- | --- |
| F1 | **走 light_kind 链路，不走 ResponseClass enum** | §原则4 + 现状 grep（`ResponseClass.STICKER_ONLY` 零消费者） | 新增 `light_kind="sticker_only"`，在 `_handle_light_reply` 加分支；**不**给 `ResponseClass.STICKER_ONLY` 接消费分支 |
| F2 | **复用 `_send_post_reply_sticker_if_needed(force_send=True)` 发图**，不新写发送函数 | `client.py:1677` 已有，closing/greeting 经 `_maybe_light_reply_sticker` 已在用 | sticker_only 分支调同一函数；`force_send=True` 跳过概率门（这一轮是 thinker 主动选了"只发图"，发送意图明确，等同 kaomoji-enforce 语义） |
| F3 | **选图复用语义检索**：`reply=""` 空文本 + mood_valence 偏置 query | `_select_post_reply_sticker`（`client.py:1795`）reply 空时靠 `_bias_query_by_valence` 注入的情绪词检索 | 不新增检索逻辑；空 reply + valence 词已验证能独立选对类（设计 §选类 2 已记） |
| F4 | **`slot.last_response_class` 仅作可观测标记**：若改动顺手，sticker_only 落地后回填 `ResponseClass.STICKER_ONLY.value`；**做不到也不阻塞**（不是发送必经） | scheduler 已有 LIGHT_ACK 等回填（`scheduler.py:991`） | 回填是"让监控看得见这一档触发了"，**非**逻辑分支依赖；若回填点不在 client 可达范围，记 §5 偏差表，留作后置，不硬接 |
| F5 | **空检索 → 降级，不兜底瞎发**：sticker_only 选不到图（`intent_floor` 拦截 / 库空）时，**回退到 companion 短 ack**（出一句文字），不是静默、也不是硬塞无关图 | 设计 §阶段3 + 现有 `intent_floor` 文字降级范式（`client.py:1844`） | thinker 想"只发图"但选不到合适图时，宁可退一步出短文字（仍是弱回复），不破"图文效价一致"红线 |
| F6 | **冷却/去重对齐现有路径**：sticker_only 发图走与 closing/greeting 同款 `turn_id`/`already_sent`/cooldown 机制 | `_maybe_light_reply_sticker`（`client.py:2908`）已建 `turn_id` 范式 | 不新建冷却状态；复用 `_send_post_reply_sticker_if_needed` 内已有冷却 |
| F7 | **不做（本批范围外）**：不改 RWS 概率门；不改 closing/greeting/companion 现有行为；不改 sticker 二轴权重（`decision_provider._W_*`）；不加任何 config 开关（`sticker_placement_enabled` + `base_frequency` 现有总闸足够） | 出本批范围 | 这些是后续校准项，本批只接通第四档 |

> **派单规则**：接单人先跑 §2 Wave 0 锚点复验。任何一项与下表预期不符 → 记 §5 偏差表，停，问验收人。

---

## 1. 主线锚点与证据订正（执行前必读）

下表是接入点的 grep 实证锚点，**2026-06-11 派单人已复核**。派单按本表执行；接单人 Wave 0 第一步就是重新 grep 确认仍成立（行号会随其他改动漂移，以函数名为准，行号仅供定位）。

| 锚点 | 表述 | 验证命令 | 预期（2026-06-11 复核） |
| --- | --- | --- | --- |
| light_kind 白名单 | thinker 侧允许的 light_kind 集合 | `grep -n '_ALLOWED_LIGHT_KINDS' services/llm/thinker.py` | `:33`（当前 `{"", "companion", "closing", "greeting"}`，**需加 `"sticker_only"`**） |
| light_kind 归一化 | 非白名单值清空 | `grep -n 'def _normalize_light_kind' services/llm/thinker.py` | `:287`（白名单驱动，加成员即自动放行，无需改函数体） |
| thinker prompt 弱回复段 | 三态说明，需补第四态 | `grep -n '弱回复（light_reply' services/llm/thinker.py` | `:156`（companion/closing/greeting 三段在 158-160，**需加 sticker_only 段 + JSON schema 注释同步**） |
| 弱回复统一入口 | closing/greeting/companion 分发 | `grep -n 'async def _handle_light_reply' services/llm/client.py` | `:2939`（三分支在 2975/3027/3080，**需加 sticker_only 分支**） |
| 弱回复发图助手 | closing/greeting 带图复用点 | `grep -n 'async def _maybe_light_reply_sticker' services/llm/client.py` | `:2908`（直接复用；sticker_only 分支调它，传 `light_kind="sticker_only"`） |
| 发图主路径 | 选图 + 概率门 + 文字降级 | `grep -n 'async def _send_post_reply_sticker_if_needed' services/llm/client.py` | `:1677`（`force_send` 跳概率门在 `:1758`；`intent_floor` 文字降级在 `:1844`） |
| 选图语义检索 | reply 空 + valence 偏置 | `grep -n 'def _select_post_reply_sticker' services/llm/client.py` | `:1795`（`_bias_query_by_valence` 在 `:1865`；reply 空时靠 valence 词检索） |
| 入口消费侧 | `_handle_light_reply` 返回值如何被 caller 处理 | `grep -n 'light_result = await self._handle_light_reply' services/llm/client.py` | `:4800`（closing/greeting return None 短路在 4817-4820；**sticker_only 需在此加短路处理**） |
| send_sticker intent 参数 | 语义检索发送已支持 | `grep -n 'intent' services/tools/sticker_tools.py` | `:220`（`intent` 与 `sticker_id` 二选一，已就绪，本批经 `_select_post_reply_sticker` 间接用，**不直接调 tool**） |
| 弱回复测试 | 现有 closing/companion 测试 | `ls tests/test_closing_light_reply_client.py tests/test_thinker.py` | 存在；新增 sticker_only 测试参照其结构 |

---

## 2. Wave 0 — 前置零代码锚点复验（必做，零代码）

接单人动任何代码前，先跑下列命令，确认现状与 §1 表一致。**任一不符 → 记 §5 偏差表，停，问验收人。**

```bash
# 1. light_kind 链路确认（确认走 thinker 而非 ResponseClass）
grep -n '_ALLOWED_LIGHT_KINDS' services/llm/thinker.py
grep -n 'async def _handle_light_reply' services/llm/client.py
# 2. 死代码红线确认：ResponseClass.STICKER_ONLY 确实零消费者
grep -rn 'STICKER_ONLY\|response_class.*sticker\|sticker_only' kernel/ services/ plugins/ | grep -v test
#    预期：仅 kernel/types.py 的 enum 定义命中，无任何读取分支。若已有人接 → 停，问验收人。
# 3. 复用点存在性
grep -n 'async def _maybe_light_reply_sticker\|async def _send_post_reply_sticker_if_needed\|def _select_post_reply_sticker' services/llm/client.py
# 4. 前置项就绪（SQLite + 语义检索）
grep -n 'def search_by_intent' services/media/sticker_store.py
ls -l storage/stickers/stickers.db
```

回执（接单人回填）：

- [x] §1 表逐行 grep 结果（贴实际行号）：**全命中**。`_ALLOWED_LIGHT_KINDS` `:33`；`_normalize_light_kind` `:287`；prompt 弱回复段 `:156-161`；`_handle_light_reply` `:2939`；`_maybe_light_reply_sticker` `:2908`；`_send_post_reply_sticker_if_needed` `:1677`；`_select_post_reply_sticker` `:1795`；caller 短路 `:4800:4818`
- [x] 死代码红线确认 `STICKER_ONLY` 仅 enum 定义命中、无消费者：`kernel/types.py:46` 仅 enum 定义，全仓零消费者。✅
- [x] 前置项 `search_by_intent` + `stickers.db` 在位：`search_by_intent` `sticker_store.py:557`；`stickers.db` 28KB，最近写入 06-13。✅

---

## 3. 串行执行 Wave 表

两个 Wave，严格串行：W1（thinker 产出第四档）→ W2（client 消费第四档）。**W2 依赖 W1 的 light_kind 才有意义；但 W1 单独落地不改变任何行为**（新 light_kind 没有消费者时，`_handle_light_reply` 走末尾"unknown kind → 主 LLM"兜底，等于 thinker 偶尔多产一个被忽略的标签，安全）。这是有意的安全降级顺序。

| Wave | 改动 | 文件 | 行为锚 |
| --- | --- | --- | --- |
| W1 | thinker 白名单 + prompt 第四态 + JSON schema 注释 | `thinker.py` | 三态测试保持绿；新增 sticker_only 解析测试 |
| W2 | `_handle_light_reply` sticker_only 分支 + caller 短路 + F5 降级 | `client.py` | closing/greeting/companion 行为不变；新增 sticker_only 发图/降级测试 |

### 3.1 Wave W1 — thinker 产出第四档 light_kind（先行，零行为变更）

**目标**：让 thinker 能在"一张表情足矣"的场景产出 `action=light_reply, light_kind=sticker_only`。

**改动点（引导，不抄代码——按现有三态的写法对称扩展）：**

1. `_ALLOWED_LIGHT_KINDS`（`thinker.py:33`）集合**追加** `"sticker_only"`。`_normalize_light_kind` 是白名单驱动的，加成员即自动放行，**不要改函数体**。
2. prompt 弱回复段（`thinker.py:156` 起）**补第四小节**，与 companion/closing/greeting 三段对称。引导文案要点（中文，措辞自拟，对齐现有三段语气）：
   - 什么时候用：对方发了一张表情/一句纯情绪宣泄（"哈哈哈""草""😭"）、或气氛到了一个**用文字反而多余、回一张表情最自然**的点；你想用一张表情回应而不是说话。
   - 边界：**不是**所有能配图的场景都用它——它是"**只**发图、**不**出文字"。需要传达任何信息量、需要文字承载的，仍用 reply 或 companion（companion 是"短文字 ack 可带图"，sticker_only 是"纯图无字"，二者别混）。
   - 选不到合适表情时系统会自动退回一句短文字（F5），所以 thinker 不必担心"万一没有合适的图"。
3. JSON schema 那行注释（`thinker.py:172` 附近的 `light_kind` 取值枚举）**同步**加 `sticker_only`。

**防死代码检查（W1）**：W1 只产出标签，消费在 W2。若 W2 未落地就跑，新标签会落入 `_handle_light_reply` 末尾 `unknown kind` 兜底 → 走主 LLM。这是**预期的安全降级**，不是 bug。但 **W1 不允许单独 merge 上线**——必须与 W2 同 PR，否则就是"产出了没人消费的标签"=变相死代码。

**验证命令（W1）：**

```bash
grep -n 'sticker_only' services/llm/thinker.py     # 白名单 + prompt + schema 三处都有
pkill -9 -f pytest; PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest tests/test_thinker.py -q
uv run ruff check services/llm/thinker.py && uv run pyright services/llm/thinker.py
```

**新增测试（W1，必做）**：在 `tests/test_thinker.py` 加一例——喂 `{"action":"light_reply","light_kind":"sticker_only",...}`，断言 `decision.light_kind == "sticker_only"`（证明白名单放行）；再喂一例非法 light_kind 断言被清空（证明白名单仍收口）。

**回滚（W1）**：`git revert`；或白名单移除 `"sticker_only"` → 归一化自动把该值清空，thinker 退回三态。

**回执（接单人回填）：** grep 结果 `_ALLOWED_LIGHT_KINDS :33` `_normalize_light_kind :287` `prompt 弱回复段 :156` `schema :172` 四处均含 `sticker_only` / pytest **52 passed (0 fail)** / ruff+pyright **0** / 新测试名 `test_parse_light_kind_sticker_only_is_allowed` `test_thinker_prompt_mentions_sticker_only`

---

### 3.2 Wave W2 — client 消费第四档：发纯图 + F5 降级（依赖 W1）

**目标**：`_handle_light_reply` 收到 `light_kind="sticker_only"` 时，**只发一张语义检索选出的表情、不出文字**；选不到图则按 F5 退回 companion 短 ack。

**改动点（引导，重在复用，逐条说明为什么不新写）：**

1. **`_handle_light_reply`（`client.py:2939`）加 sticker_only 分支**，**位置在 companion 分支（`:3080`）之前或之后均可，但必须在末尾 `unknown kind` 兜底之前**。分支逻辑（复用现有件，不新写）：
   - 调 `_maybe_light_reply_sticker(reply="", thinker_decision=..., light_kind="sticker_only", ...)` 发图。**为什么传 `reply=""`**：sticker_only 没有文字载体，空 reply 让 `_select_post_reply_sticker` 靠 `_bias_query_by_valence` 注入的情绪词检索（F3，已验证可独立选类）。
   - **但** `_maybe_light_reply_sticker` 当前内部用的是默认概率门（非 force_send）。本档语义是 thinker **主动**选了"只发图"，发送意图明确——需要 `force_send=True` 跳过概率门（F2）。**有两条合规路径，二选一，记 §5 说明选哪条**：
     - (a) 给 `_maybe_light_reply_sticker` 加一个 `force_send: bool = False` 透传参（additive，默认不变 → closing/greeting 行为零影响），sticker_only 分支传 `True`；或
     - (b) sticker_only 分支**直接**调 `_send_post_reply_sticker_if_needed(reply="", force_send=True, ...)`，绕过 `_maybe_light_reply_sticker` 这层薄封装（它本身只是 try/except + turn_id 包装，sticker_only 分支自己建 `turn_id` 即可）。
     - **派单倾向 (a)**：改动最小、复用最大、closing/greeting/sticker_only 三者共用一个发图入口，最不易长出分叉死代码。但若 (a) 的 try/except 语义对 sticker_only 不合适（sticker_only 发图失败要触发 F5 降级，而 closing/greeting 失败是静默吞掉），则选 (b) 并在 §5 说明。
   - **F5 降级**：捕获"发图成功与否"。`_send_post_reply_sticker_if_needed` 返回 `bool`（`True`=已发）。**若返回 `False`（intent_floor 拦截 / 库空 / 概率即便 force 后仍无候选）→ 退回 companion**：return `{"inject_companion_hint": True, "light_kind": "companion"}`，让 caller 注入 hint 走主 LLM 出一句短文字。**为什么不静默**：thinker 已判定"这条该回应"，只是想用图；图不可得时退一步出短文字仍满足"被看见"，静默会变成 ostracism（设计红线）。
   - **若发图成功** → return `{"light_reply": True, "light_kind": "sticker_only", "text": ""}`（text 空，告诉 caller 这轮无文字、已处理、短路主 LLM）。
2. **caller 短路处理（`client.py:4817` 起）**：现有逻辑 `if light_result.get("light_kind") in ("closing", "greeting"): return None`（短路主 LLM）。**把 `"sticker_only"` 加进这个元组**——sticker_only 成功发图后同样短路主 LLM。**注意**：F5 降级时 `_handle_light_reply` 返回的是 `inject_companion_hint`（light_kind 已改回 companion），会自然走现有 companion 分支注入 hint，**不需要为降级单独写 caller 逻辑**——这是复用 companion 通路省一段代码的关键，别另写降级短路。

**防死代码检查（W2，逐条对照 §6）：**
- 不新建发送函数（复用 `_send_post_reply_sticker_if_needed`）。
- 不给 `ResponseClass.STICKER_ONLY` 接消费分支（F1）；`last_response_class` 回填仅按 F4 可选。
- 降级路径复用 companion 现有通路，不另写短路。
- 若选 (a) 给 `_maybe_light_reply_sticker` 加 `force_send` 参，**确认 closing/greeting 调用点仍用默认值** → 它们行为零变更（grep 两处调用点确认未传 `force_send`）。

**验证命令（W2）：**

```bash
grep -n 'sticker_only' services/llm/client.py    # _handle_light_reply 分支 + caller 元组都有
pkill -9 -f pytest
PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest tests/test_closing_light_reply_client.py tests/test_client.py tests/test_reply_workflow.py -q
uv run ruff check services/llm/client.py && uv run pyright services/llm/client.py
```

**新增测试（W2，必做，至少 3 例）：**
1. **发图成功**：mock store 有命中、`_send_post_reply_sticker_if_needed` 返回 True → 断言 `_handle_light_reply` 返回 `light_kind=="sticker_only"` 且 `text==""`，且**没有**调用主 LLM（短路）。
2. **F5 降级**：mock 发图返回 False（intent_floor 拦截 / 空库）→ 断言返回 `inject_companion_hint=True`、`light_kind=="companion"`（证明退回短文字而非静默）。
3. **closing/greeting 回归**：断言加了 sticker_only 分支后，closing/greeting 仍走各自原路径、`force_send`（若选 (a)）对它们仍是默认 False、行为字节级不变。
4. **（D2，若 (a) 路径）cancel 安全**：发图协程被 cancel 时不污染 turn 状态（参照现有 closing D2 测试范式）。

**回滚（W2）**：`git revert`；或撤掉 `_handle_light_reply` 的 sticker_only 分支 → 新标签落 `unknown kind` 兜底走主 LLM（退化但不崩）。

**回执（接单人回填）：** 选 **(b)** sticker_only 分支直接调 `_send_post_reply_sticker_if_needed(reply="", force_send=True)`，绕过 `_maybe_light_reply_sticker`。理由：`_maybe_light_reply_sticker` 的 try/except 是静默吞错（closing/greeting 语义），sticker_only 的 F5 降级需"失败→回退文字"而非静默，语义不同故不共用同一封装。`_maybe_light_reply_sticker` 签名未变（未加 `force_send` 参）。**grep**: `client.py:3082` sticker_only 分支、`:3113` 返回 `light_kind="sticker_only"`、`:3120` F5 降级返回 `companion`、`:4860` caller 短路含 `sticker_only` / **pytest 52 passed (0 fail)** / **ruff+pyright 0** / **新测试**: `test_handle_light_reply_sticker_only_sends_sticker_short_circuits` `test_handle_light_reply_sticker_only_f5_fallback_to_companion` `test_handle_light_reply_sticker_only_exception_f5_fallback` `test_closing_greeting_still_default_force_send_false`

---

## 4. 总收口判据（全 Wave 完成后）

- [ ] W1+W2 同一 PR，未拆开单独上线（避免产出无消费者的标签 = 死代码）。
- [ ] `grep -rn 'STICKER_ONLY' kernel/ services/ plugins/ | grep -v test` 结果：除 enum 定义 + 本批按 F4 可选回填的可观测标记外，**无新增的"接了没人调"的分支**。
- [ ] closing/greeting/companion 三态行为字节级不变（回归测试绿 + grep 确认调用点未变默认值）。
- [ ] sticker_only 发图成功 → 短路主 LLM、无文字；失败 → 退回 companion 短文字，**绝不静默**。
- [ ] 全量 `pkill -9 -f pytest && PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-} uv run pytest` 绿；`uv run ruff check` 0；`uv run pyright` 不新增错误。
- [ ] D4 完成声明含：① §6 死代码扫描结果；② 外部可观察证据（pytest 计数 / grep 输出）；③ 回滚路径。
- [ ] `maintenance-log.md` 追加 dated 条目（中文，逆序），记本批改了什么、为什么、影响面、回滚。

---

## 5. 偏差表 + 回执（执行者回填）

> 凡现实与本文不符（行号漂移、函数签名不同、命令报错、F5 降级语义需调整、(a)/(b) 选型），**先记这里，停，等验收人确认**，不要自行猜改。

| # | 位点 | 本文预期 | 实际发现 | 处理（等验收人批） |
| --- | --- | --- | --- | --- |
| | | | | |

---

## 6. 死代码红线自查（本批专项，收口前逐条勾）

本批最大风险是"为了激活第四档而长出没人调用的代码"。收口前逐条 grep 自查：

- [ ] **没碰 `ResponseClass.STICKER_ONLY` enum 做逻辑分支**（F1）——它本就零消费者，本批不给它接消费者；`last_response_class` 回填仅作可观测标记（F4），不被任何 `if` 读取做决策。
- [ ] **没新写发送/选图函数**——发图复用 `_send_post_reply_sticker_if_needed`，选图复用 `_select_post_reply_sticker`，封装复用 `_maybe_light_reply_sticker`。
- [ ] **没加 config 开关**（F7）——`_sticker_placement_enabled` + `base_frequency` 现有总闸已覆盖；没有"为将来"的新 tunable。
- [ ] **F5 降级复用 companion 通路**——没另写一条降级短路分支。
- [x] **路径 (b) 选型**：sticker_only 分支直接调 `_send_post_reply_sticker_if_needed`，未给 `_maybe_light_reply_sticker` 加参——closing/greeting 签名完全未变，零泄漏。
- [ ] **W1 的新 light_kind 在 W2 落地后确有消费者**——`grep 'sticker_only' services/llm/client.py` 必须命中 `_handle_light_reply` 分支，否则 W1 的标签就是死的。

---

## 7. 自审表（每 Wave 收口后勾）

| Wave | D1 同模式扫描 | 验证命令跑过 | 回滚路径确认 | 新测试名 | §6 死代码自查 |
| --- | --- | --- | --- | --- | --- |
| W1 | ☑ thinker白名单+prompt+schema | ☑ 52 passed | ☑ `git revert` or remove from frozenset | ☑ `test_parse_light_kind_sticker_only_is_allowed` `test_thinker_prompt_mentions_sticker_only` | ☑ |
| W2 | ☑ client sticker_only分支+caller短路+F5降级 | ☑ 52 passed | ☑ `git revert` | ☑ 4 tests (send/F5/exception/regression) | ☑ |

---

## 8. 验收人复核要点（落地后按 D4 逐条用外部证据复核，非照单全收回执）

1. **死代码（本批头号）**：`grep -rn 'STICKER_ONLY\|sticker_only' kernel/ services/ plugins/ | grep -v test` 亲自跑一遍，确认没有"接了没人调"的分支；确认 `ResponseClass.STICKER_ONLY` 仍只在 enum 定义（+ 可选 F4 回填），没被偷偷接成逻辑分支。
2. **复用而非新建**：确认 W2 没新写发送函数；`_send_post_reply_sticker_if_needed` / `_select_post_reply_sticker` / `_maybe_light_reply_sticker` 三者是被复用的，diff 里没有平行实现。
3. **closing/greeting/companion 零回归**：跑 `tests/test_closing_light_reply_client.py`，并 grep 确认这三态调用点签名/默认值未变。
4. **F5 降级真退文字不静默**：看 W2 降级测试断言的是 `inject_companion_hint`/`light_kind=="companion"`，不是返回 None 静默。
5. **W1/W2 同 PR**：确认没有"W1 先上线产出无消费者标签"的中间态。
6. **live 抽查（可选，被动）**：上线后用 `localhost:29300` 被动看 sticker_only 是否真触发、发图是否图文效价一致；**不重启 bot/napcat**。
