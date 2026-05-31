# Issue 17 pre-part0 落地方案 v2 — 表情包/图片角色身份识别（重写，对齐当前代码）

> 状态：**落地执行方案（待编码）· 2026-05-31**
> 取代：[omubot-grayscale-issue17-pre-part0-sticker-identity.md](omubot-grayscale-issue17-pre-part0-sticker-identity.md)（2026-05-27，架构判断仍有效但所有现有接缝引用已过期）。
> 触发：落地前最后审计发现 6 处 BLOCKER/不一致（vision 管线行号变、hash 截断、storage 切 named volume、OCR 已先落地、desc_cache 位置、依赖未装），原文档照抄会出错。本文是按当前代码现实重写的执行方案。

## 0. 用户已拍板的设计决策（2026-05-31）

| 决策点 | 选择 | 影响 |
| --- | --- | --- |
| 识别缓存 PK | **完整 64 字符 SHA-256** | 不沿用现有 `[:8]` 截断（32-bit 万级图量碰撞会张冠李戴）；识别缓存独立 key 体系 |
| charpack 分发 | **config 目录 + admin 上传 两路都做** | 热加载目录走 bind-mount `./config/character_packs/`；admin SPA 也能上传写入 |
| 首版范围 | **Phase 1+2+3 全做** | 但 Phase 3 按「实际存在的信号汇」重写（见 §6，原 Climate 系统不存在） |
| AnimeTrace | **保留 + CCIP 并行合并** | 已知动漫角色走 AnimeTrace、原创角色走 CCIP，交叉验证 |

## 1. 架构判断（沿用原文档，仍成立）

- **CCIP**（`dghs-imgutils` 的 `ccip_extract_feature`/`ccip_difference`）：对比学习角色嵌入，支持原创角色（凤笑梦）零样本注册，本地 ONNX，F1≈0.94。主路径。
- **AnimeTrace**（`aiapiv2.animedb.cn`）：在线已知角色库，补 CCIP 未注册的动漫角色。并行跑、超时降级。
- **整合进 `services/media/`**，与 VisionClient/StickerStore 同级进程内 class，不做微服务（omubot 单体，2GB limit 余量足）。
- **降级**：CCIP/AnimeTrace 任意异常 → try/except 回退现有 Qwen VL 纯描述，不影响核心。

## 2. 当前代码现实（审计核实，新方案的真实接缝）

| 接缝 | 原文档假设 | **当前现实（已核实）** |
| --- | --- | --- |
| vision 渲染入口 | `router.py:714-746` | `_render_message` 在 [router.py:652-833](../../kernel/router.py)，图片识别块在 **785-819** |
| 识别顺序 | desc_cache→sticker_store→VL | 同，在 [router.py:792-811](../../kernel/router.py)：`desc_cache(内存dict)` → `sticker_store.lookup_by_hash` → `vision_client.describe_image` |
| 新增的 json 分支 | 不存在 | [router.py:709](../../kernel/router.py) F-γ G1 已加（B站卡片），CCIP 不碰它 |
| hash | 完整 SHA-256 | 现状 `hashlib.sha256(data).hexdigest()[:8]`（[router.py:791](../../kernel/router.py)）；**新缓存改用完整 hash** |
| desc_cache 注入 | `plugin.py:1259` | 构造在 [plugin.py:1496](../../plugins/chat/plugin.py) `ctx.desc_cache={}`，作 kwarg 传入 `_render_message`（[router.py:665/1169/1540](../../kernel/router.py)） |
| 组件注入模式 | — | 仿 `sticker_store`：`ctx.<x>` 属性 → `_render_message(..., <x>=ctx.<x>)`（[router.py:1167/1538](../../kernel/router.py)） |
| storage | bind mount，可 cp | **named volume** `omubot-storage`（[docker-compose.yml:34-47](../../docker-compose.yml) external:true）；DB 落这里，**charpack 走 bind-mount `./config/`** |
| OCR / sticker 语义 | 待协同，共用 character_recognition.db | **已先落地且未用该 DB**：`ocr_text` 在 [sticker_store.py:45](../../services/media/sticker_store.py) schema、[sticker_capture.py:91](../../services/media/sticker_capture.py) 拆描述、[dream/plugin.py:282](../../plugins/dream/plugin.py) 回填。**「缓存合一」约定作废，各管各**（见 §7） |
| 依赖 | 待装 | numpy/onnxruntime/imgutils **全未装**（pyproject 0 命中），纯新增 ~300MB；imgutils 钉死 numpy<2 |

## 3. 数据落点（决策 B2 + B3 落地）

```
config/character_packs/              # bind-mount(./config rw)，热加载目录，cp 即生效
├── pjsk-all.charpack/
│   ├── manifest.json                # pack 元数据 + 角色清单
│   └── embeddings.npz               # {embedding_key: ndarray(768,) float32}
└── custom.charpack/                 # admin 注册的角色，系统维护

/app/storage/character_recognition.db  # named volume，运行时真相源
├── character_registry      # 角色嵌入(BLOB) + relation + name/aliases
├── character_pack_meta     # pack source_hash，增量检测
└── image_recognition_cache # hash(完整SHA-256) → character + desc + source
```

**双路分发（决策"两路都做"）**：
- **路 A（config 热加载）**：包丢进 `./config/character_packs/`，启动 `scan_and_sync()` 或 `POST /api/admin/characters/reload` 增量同步到 DB。
- **路 B（admin 上传）**：admin SPA 上传 `.charpack` → 写入 `./config/character_packs/`（bind-mount 容器可写）→ 触发同一 `scan_and_sync()`。两路最终都收敛到同一个 `scan_and_sync`，admin 上传只是多了「写文件」一步。
- **为什么 DB 在 storage、包在 config**：DB 是 sqlite，必须在 named volume（避 macOS 共享盘 WAL 损坏，2026-05-24 教训）；包是只读资源文件，放 bind-mount config 才能让用户 cp/admin 写。

**hash（决策"完整 SHA-256"）**：`image_recognition_cache.hash` 用完整 `hashlib.sha256(data).hexdigest()`（64 字符）。**不复用** `_render_message` 现有的 `[:8]` desc_cache key——两套独立：旧 `[:8]` 继续服务 VL 描述热缓存，新识别缓存用完整 hash 作 PK。识别管线内部自己算完整 hash。

## 4. Phase 1 — 持久化缓存 + 并行识别核心 + router 接入

**新增文件**（`services/media/`，仿现有 media 服务）：

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| `recognition_cache.py` | ~60 | aiosqlite 持久化缓存（建表+CRUD+LRU），完整-hash PK；遵循 `close_with_checkpoint`/DELETE journal 纪律（slang.db 损坏教训） |
| `character_recognizer.py` | ~90 | CCIP：`ccip_extract_feature`(bytes)→768维 + 与注册库 `ccip_difference` 比对；`asyncio.to_thread` 包 ONNX 同步调用 |
| `animetrace_client.py` | ~45 | AnimeTrace POST + timeout + 降级（aiohttp，复用现有 session 模式） |
| `character_registry_db.py` | ~70 | character_registry / pack_meta 读写 + `scan_and_sync` 增量加载 |

**router 接入**（改 [router.py:792-811](../../kernel/router.py) 的识别块，**严格保序**）：

```
图片 bytes
 ├─0. full_hash = sha256(data).hexdigest()            # 完整
 ├─1. recognition_cache.get(full_hash) 命中 → 用       # 持久化命中 0ms
 ├─2. sticker_store.lookup_by_hash(data) 命中 → 用      # 现有逻辑保留(已入库表情包优先)
 ├─3. 都没命中 → 并行 asyncio.gather(
 │       animetrace_client.identify(data),
 │       character_recognizer.identify(data),          # CCIP 比对注册库
 │    ) → merge_recognition(决策矩阵 §原文档"五")
 │    ├─ 命中角色 → desc = f"{name}{VL情绪描述}"
 │    └─ 未命中   → desc = VL 纯描述(现有)
 └─4. recognition_cache.put(full_hash, desc, source, confidence)
```

**保序要点（D1 同模式）**：识别缓存(1) 必须在 sticker_store(2) **之后**？——否。新设计中**识别缓存 1 在最前**（它缓存的是最终 desc，含 sticker_store 命中的也可缓存），sticker_store 2 保留为「已入库表情包」快路。两层 hash 不同源（cache 用完整、sticker_store 用 `[:8]`），不冲突。

**注入**：`ctx.recognition_cache` / `ctx.character_recognizer` / `ctx.animetrace_client` 在 [plugin.py:1496](../../plugins/chat/plugin.py) 附近构造（仿 `ctx.sticker_store`），作 kwarg 传入 `_render_message` 两处调用点（[router.py:1167/1538](../../kernel/router.py)）。

**依赖**：pyproject 加 `dghs-imgutils>=0.17.0` + `onnxruntime>=1.17.0`；**落地前重核版本**（原文档记 v0.19.0@2025-09，今已 2026-05，可能有新版/API 变动）。确认 **numpy<2 长期钉死**可接受。Dockerfile 加模型预下载层（~100MB），`on_startup` 加 CCIP 预热（仿 vision_client 预热）。

## 5. Phase 2 — 角色注册 CLI + Admin SPA

| 任务 | 行数 | 说明 |
| --- | --- | --- |
| `tools/build_character_pack.py` | ~50 | 输入参考图目录（每子目录=1角色，5-10张），CCIP 提嵌入 → 输出 `.charpack`（manifest + npz） |
| admin API `admin/routes/api/characters.py` | ~60 | `GET /characters`（列注册角色+缓存统计）、`POST /characters/reload`（触发 scan_and_sync）、`POST /characters/upload`（上传 charpack 写 ./config）、`POST /characters/register`（admin 手动注册单角色，写 custom.charpack） |
| Admin SPA 角色管理页 | ~80 | 走 omubot-admin-console skill 的 Calm Ops 风格：复用 AppPage/AppCard/MetricCard/PageToolbar/EmptyState；上传 charpack + 角色列表 + relation 编辑 + 识别缓存命中率指标。挂在「系统/插件」体系下 |

**admin 改动遵循 D6**：`admin/static` bind-mount，前端 `npm run build` 即生效，无需 rebuild bot；改 `admin/routes/*.py` 才 rebuild。

## 6. Phase 3 — 信号消费（按实际存在的汇重写）⚠️

**审计纠正**：原文档 §5.9 把 relation 映射到「Climate 系统」的 familiarity/openness。**Climate 系统不存在**（无 `services/dialogue_climate/`，`MoodClassifier` 仍是死代码）。Phase 3 改接**实际存在的三个汇**：

| relation | 主收益（已天然生效，无需代码） | 可选信号注入（接真实汇） |
| --- | --- | --- |
| `self`（bot自己） | 描述变准：`«凤笑梦开心地跳»` 而非 `«粉发女孩跳»`——**这是 pre-part0 的第一性价值，Phase 1 一落地就有** | 可经 [qq_interactions.py](../../services/humanization/qq_interactions.py) 注入 mood valence 小幅+（"用户在用我的表情包"） |
| `friend`（熟人） | 同上，bot 知道图里是谁 | 可注入 mood openness 小幅+ |
| `known` | 描述准确 | 无 |

**Phase 3 接法（保守）**：relation→mood 微调走现有 `MoodEngine`（[plugins/schedule/mood.py](../../plugins/schedule/mood.py) 的 valence/openness 维度真实存在），或经 RWS `memory_signals.familiarity`（[memory_signals.py:49](../../services/scheduler_rws/memory_signals.py) 已是真实汇）。**不新建 Climate**。若觉得 mood 注入复杂度不值，Phase 3 可只保留"描述变准"这一自然收益（Phase 1 已含），relation 信号注入降级为 backlog。

**结论**：Phase 1 已交付核心价值（认出角色→描述准）。Phase 3 的"信号消费"在当前架构下只能接 mood/RWS 这两个已存在的汇，**不是原文档设想的 Climate 闭环**——这点务必对齐预期。

## 7. 与已落地 OCR / sticker 语义检索的关系（B4 对齐）

原文档预留「VL prompt 合一产 {情绪,内容,OCR}、共用 character_recognition.db」的协同约定。**该约定已被 sticker 语义检索项目先落地破坏**：它的 OCR 走 `sticker_store` 的 `ocr_text` 字段、不用 character_recognition.db。

**新方案处置：各管各，不强行合一。**
- pre-part0 用独立 `character_recognition.db`（角色识别）；sticker 语义检索继续用 `sticker_store`（OCR/语义）。
- **唯一交叉点**：两者都在 `_render_message` 图片块跑。保序：识别缓存(完整hash) → sticker_store(含OCR) → 并行识别 → VL。VL 仍是 single source，CCIP 只在 sticker_store 未命中时跑。
- **不回头改 sticker_store schema**，不强行塞 character 字段（原文档 §九已定"职责分离"，沿用）。

**多 bot 共享前置（与 [supervisor 文档 §A.7](supervisor-control-plane-design-2026-05-31.md) 对齐）**：CCIP 数据须分两层——① **角色识别**（image hash→character_id+嵌入，schema 无 bot_id）是 bot 无关的，可作无状态 sidecar 被多 bot 共享、识别缓存按 hash 全局命中省算力；② **`relation`（self/friend/known）是 per-bot 语义**（同一角色对不同 bot 关系不同），**绝不能焊进共享嵌入库**，须留各 bot 自己的 storage。即「嵌入库共享只读 + relation per-bot 可变」分离。当前 v2 schema 的 `character_registry.relation` 字段是为单 bot 设计的；若走多 bot，relation 要从共享 registry 拆出到 per-bot 关系表（落地多 bot 时再做，单 bot 阶段不影响）。

## 8. 风险与回滚

| 风险 | 缓解 |
| --- | --- |
| 依赖钉死 numpy<2 | 落地前确认；imgutils 是唯一 numpy 用户，未来引 numpy≥2 库才冲突 |
| CCIP 强匹配 false-positive（"倾向强制匹配"） | 阈值 `ccip_default_threshold()`≈0.178；不确定区间(0.25-0.35)标低置信；AnimeTrace 冲突时信 AnimeTrace |
| ONNX 阻塞事件循环 | `asyncio.to_thread` 包所有 CCIP 调用 |
| 首次识别 ~300-500ms 延迟 | 持久化缓存命中后 0ms；CPU 推理够（有缓存不需 GPU） |
| Docker +300MB | 可接受（2GB limit，实测峰值 ~300MB + CCIP ~150MB = 4x 余量） |
| sqlite 损坏（macOS 盘） | DB 在 named volume + `close_with_checkpoint` + DELETE journal（slang.db 全栈治本同款） |

**回滚**：识别管线整段 try/except 降级到 VL 纯描述（现有行为）。flag 化：加 `character_recognition.enabled`（默认 false），关掉即完全旁路、回到现 `desc_cache→sticker_store→VL`。依赖装了不开 flag 不影响运行（lazy import）。

## 9. 落地顺序

1. **Phase 1a**：装依赖 + `recognition_cache.py`（持久化缓存，完整hash）+ flag。先把缓存层落地、router 接入但识别返回 None（等价现状），验证缓存读写 + 不破坏现有管线。
2. **Phase 1b**：`character_recognizer.py`（CCIP）+ `character_registry_db.py` + `build_character_pack.py`，注册 bot 自己(凤笑梦)+核心熟人，验证「认出 self/friend→描述变准」。
3. **Phase 1c**：`animetrace_client.py` + merge 决策，补已知动漫角色。
4. **Phase 2**：admin 注册页 + 两路分发。
5. **Phase 3**：relation→mood/RWS 信号注入（或降级为只保留描述收益）。

每 Phase 出独立 D3 四列迁移清单 + 测试（cancel-path 覆盖 aiosqlite 缓存 + 网络降级）。Phase 1a 可独立上线（零行为变更、纯地基）。

## 10. 影响 & 回滚（本文档）

仅执行方案文档，无代码改动。编码按 §9 分步、各自 D3 清单 + 维护日志。flag 默认 false，依赖 lazy import，装了不开不影响运行态。
