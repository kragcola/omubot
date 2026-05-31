# Issue 17 前置项 — 表情包/图片角色身份识别

> 状态：2026-05-27 深度调研完成，方案确定
>
> 来源：用户指出——bot 收到表情包时只看到"粉色头发的小女孩很开心的在跳"，而非"凤笑梦（bot 自己）与晓山瑞希（bot 熟人）"。视觉描述在发送时有效（告诉 LLM 这张表情包表达什么情绪），但在接收时会错意（bot 不知道图里是谁）。
>
> 优先级：Part 0 前置。影响 Climate 系统的情绪信号准确性 + 用户体验核心指标。
>
> 方法：搜索成熟项目、论文、API 服务，拆解代码验证可行性。

---

## 一、问题定义

### 当前管线

```
用户发表情包 → image segment 下载 → SHA-256 hash
    → ① desc_cache 命中？→ 用缓存描述
    → ② sticker_store.lookup_by_hash() 命中？→ 用库内 description
    → ③ 都没命中 → Qwen VL describe_image() → 生成描述
    → 最终文本：«动画表情1: 粉色头发的小女孩很开心的在跳»
```

### 核心缺陷

Qwen VL 的 prompt 没有角色知识，只能描述外观特征。即使给 VL 注入角色提示（方案 A），对于：
- **原创角色**（凤笑梦）：VL 可能误判（粉色头发不一定是凤笑梦）
- **相似角色**：VL 无法区分同色系不同角色
- **变装/不同画风**：VL 完全失效

需要的是**真正的角色识别**——基于视觉特征匹配，而非文本描述推理。

---

## 二、调研结果——解决方案全景

### 2.1 CCIP（Contrastive Character Image Pretraining）— 最强方案

**项目**：`deepghs/imgutils` (GitHub)，模型 `deepghs/ccip` (HuggingFace)

**原理**：对比学习模型，专为动漫角色视觉相似度设计。训练于 ~240,000 张图片、3,982 个角色。本质是"CLIP for anime characters"——不对齐图文，而是对齐同一角色的不同图片。

**核心能力**：

```python
from imgutils.metrics import ccip_difference, ccip_extract_feature

# 注册：存储凤笑梦的参考图嵌入
ref_feature = ccip_extract_feature('fengxiaomeng_ref1.jpg')

# 运行时：比较收到的表情包
score = ccip_difference('incoming_sticker.jpg', 'fengxiaomeng_ref1.jpg')
# score < 0.25 = 同一角色
# score 0.25-0.35 = 不确定
# score > 0.35 = 不同角色
```

**关键指标**：

| 指标 | 值 |
|---|---|
| F1 Score | ~0.94 |
| Precision | ~0.94 |
| 同角色对典型分数 | 0.15-0.25 |
| 不同角色对典型分数 | 0.35-0.45 |
| 训练数据 | 240K 图片 / 3,982 角色 |
| 模型格式 | ONNX（CPU/GPU 均可） |
| 推理延迟 | ~50-200ms/张（GPU），~500ms（CPU） |

**为什么适合 omubot**：

1. **支持原创角色**——零样本学习，不需要角色在训练集中。只需 5-10 张参考图即可注册新角色
2. **不需要重训练**——添加新角色只需存储参考嵌入，无需微调模型
3. **本地运行**——无 API 成本，无网络延迟依赖
4. **生产验证**——DeepGHS 用于自动化 LoRA 训练管线、BangumiBase 数百部动画的角色分类
5. **极简集成**——`pip install dghs-imgutils`，3 行代码核心逻辑

**已知局限**：
- 强于捕捉发型特征，对肤色/发色单独区分较弱
- 需要多张参考图覆盖不同姿态/表情
- 对"不属于任何已注册角色"的判定较弱（倾向于强制匹配）→ 需要设置合理阈值

---

### 2.2 AnimeTrace（ai.animedb.cn）— 已知角色数据库查询

**原理**：在线 API，基于大规模动漫角色数据库的人脸识别。

```python
# POST https://aiapiv2.animedb.cn/ai/api/detect
# 返回：
{
  "data": [{
    "box": [x1, y1, x2, y2],
    "character": [
      {"character": "晓山瑞希", "work": "Project SEKAI"},
      {"character": "候选2", "work": "..."}
    ]
  }]
}
```

**优势**：
- 免费，无需 API key
- 已有 NoneBot2 插件（`nonebot-plugin-anime-trace`）
- 覆盖大量动漫/Galgame 角色

**局限**：
- **不支持原创角色**——凤笑梦不在数据库中
- 偶有 504 超时
- 只能识别数据库中已有的角色

**定位**：作为 CCIP 的补充——对于晓山瑞希等已知动漫角色，AnimeTrace 可以直接返回角色名，无需本地参考图。

---

### 2.3 WD-Tagger v3 / Camie Tagger — Danbooru 标签预测

**原理**：图像分类模型，输出 Danbooru 风格标签（含角色标签）。

| 模型 | 角色标签数 | 角色 F1 |
|---|---|---|
| WD-Tagger v3 (EVA02-Large) | ~数千 | ~0.68 |
| Camie Tagger | 26,968 | 0.757 |

**优势**：
- 本地运行，ONNX 格式
- 同时输出通用标签（情绪、动作、场景）——可辅助描述
- Camie Tagger 覆盖 26,968 个角色

**局限**：
- **不支持原创角色**——只能识别 Danbooru 训练集中的角色
- 角色识别准确率不如 CCIP（F1 0.76 vs 0.94）

**定位**：作为 CCIP 的补充信号——对于 Danbooru 上有大量图片的角色（初音未来、晓山瑞希），tagger 可以直接输出角色标签。

---

### 2.4 ArcFace 动漫人脸嵌入 — 自建方案

**原理**：人脸检测（SCRFD/YOLOv8）+ 人脸嵌入（ArcFace）+ 最近邻匹配。

**相关项目**：
- `FlowElement-ai/fanjing-face-recognition` — Docker 部署，SCRFD + ArcFace
- iCartoonFace（爱奇艺）— 最大标注卡通人脸数据集（5000+ 角色，400K+ 图片）
- `Fuyucch1/yolov8_animeface` — YOLOv8x6 动漫人脸检测，mAP50: 0.953

**优势**：支持原创角色（嵌入比较）
**局限**：只看脸部——动漫角色常靠服装/配饰/体型区分，纯人脸嵌入不够

**定位**：CCIP 已经是这个思路的升级版（全身特征而非仅人脸），直接用 CCIP 更优。

---

### 2.5 VLM 多模态推理 — 兜底方案

**原理**：给 Qwen VL / Claude 提供参考图 + 查询图，让模型推理是否同一角色。

**优势**：支持任何角色，可解释性强
**局限**：贵（$0.01-0.05/张）、慢（1-5s）、不稳定（可能幻觉）

**定位**：仅作为不确定情况的兜底验证，不作为主路径。

---

## 三、推荐架构——AnimeTrace + CCIP 并行，结果合并

> 设计原则：CCIP 无论 AnimeTrace 是否命中都需要运行（命中时交叉验证，未命中时兜底匹配），因此并行执行、结果合并是最优延迟策略。

```
收到表情包/图片
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ Layer 1: 持久化缓存查询（0ms）                            │
│                                                         │
│ SHA-256 hash → image_recognition_cache（SQLite）         │
│ 命中 → 直接返回 character_name + description             │
│ 未命中 → 进入 Layer 2                                    │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│ Layer 2: 并行识别（延迟 = max(AnimeTrace, CCIP) ≈ 300ms）│
│                                                         │
│  ┌──────────────────┐    ┌──────────────────────┐       │
│  │ AnimeTrace API   │    │ CCIP 本地注册库匹配   │       │
│  │ (~300ms, 网络)   │    │ (~200ms, 本地模型)   │       │
│  │                  │    │                      │       │
│  │ → character_name │    │ → character_id       │       │
│  │ → work (作品名)  │    │ → score (相似度)     │       │
│  │ → confidence     │    │ → confidence         │       │
│  └────────┬─────────┘    └──────────┬───────────┘       │
│           │                         │                   │
│           └────────────┬────────────┘                   │
│                        ▼                                │
│              ┌─────────────────┐                        │
│              │   合并决策逻辑   │                        │
│              └─────────────────┘                        │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│ 合并决策规则                                              │
│                                                         │
│ ① 两者一致（AnimeTrace=X, CCIP=X）→ 高置信采用           │
│ ② CCIP 高置信 + AnimeTrace 无结果 → 采用 CCIP（原创角色）│
│ ③ AnimeTrace 有结果 + CCIP 无匹配 → 采用 AnimeTrace     │
│    （已知动漫角色，未在本地注册）                          │
│ ④ 两者冲突（AnimeTrace=X, CCIP=Y）→ 信任 AnimeTrace     │
│    （CCIP 强匹配 false positive 概率更高）                │
│ ⑤ 都无结果 → 回退 Qwen VL 纯描述（现有行为）             │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│ 输出组装 + 持久化写入                                     │
│                                                         │
│ 有角色名：«动画表情1: 凤笑梦开心地跳舞»                   │
│ 无角色名：«动画表情1: 粉色头发的女孩开心地跳舞»（现有行为） │
│                                                         │
│ 写入 image_recognition_cache（SQLite）                   │
│ → 下次同 hash 直接命中，不再走网络/模型                   │
└─────────────────────────────────────────────────────────┘
```

### 合并决策矩阵

| AnimeTrace 结果 | CCIP 结果 | 决策 | 置信度 |
|---|---|---|---|
| X（命中） | X（一致，score < 0.25） | 采用 X | 最高 |
| X（命中） | Y（冲突，score < 0.25） | 采用 AnimeTrace X | 高（CCIP 强匹配嫌疑） |
| X（命中） | 无匹配（score > 0.35） | 采用 AnimeTrace X | 中高 |
| X（命中） | 不确定（0.25-0.35） | 采用 AnimeTrace X | 中 |
| 无结果/超时 | Y（score < 0.25） | 采用 CCIP Y | 高（原创角色路径） |
| 无结果/超时 | 不确定（0.25-0.35） | 采用 CCIP Y，标记低置信 | 低 |
| 无结果/超时 | 无匹配（score > 0.35） | VL 纯描述 | — |

### 为什么并行而非串行

| 维度 | 并行 | 串行（AnimeTrace → CCIP） |
|---|---|---|
| 延迟 | max(300ms, 200ms) = **~300ms** | 300ms + 200ms = **~500ms** |
| CCIP 必要性 | 无论如何都跑（验证或兜底） | 同 |
| 实现复杂度 | `asyncio.gather()` 一行 | if-else 分支 |
| AnimeTrace 超时影响 | CCIP 不受阻塞，超时后直接用 CCIP 结果 | 阻塞整条管线 |

---

## 四、持久化识别缓存

### 4.1 设计动机

现有 `desc_cache` 是纯内存 dict（`plugins/chat/plugin.py:1259`），重启即清零。角色识别管线涉及网络请求（AnimeTrace）和模型推理（CCIP），首次识别成本 ~300ms。群聊表情包高度重复——持久化后同一张图只走一次完整管线，后续全部 0ms 命中。

### 4.2 统一 SQLite 存储

识别缓存与角色注册共用同一个 SQLite 文件 `storage/character_recognition.db`（完整 schema 见 §五.3）。识别缓存表：

```sql
CREATE TABLE image_recognition_cache (
    hash         TEXT PRIMARY KEY,   -- SHA-256
    character_id TEXT,               -- 注册库 ID，NULL = 未识别出角色
    character_name TEXT,             -- 角色显示名（冗余，避免 join）
    description  TEXT NOT NULL,      -- 完整描述文本（含角色名）
    confidence   REAL,              -- 识别置信度
    source       TEXT NOT NULL,      -- 'animetrace' / 'ccip' / 'animetrace+ccip' / 'vl_fallback'
    created_at   TEXT DEFAULT (datetime('now')),
    last_hit_at  TEXT DEFAULT (datetime('now'))  -- 用于 LRU 淘汰
);
```

### 4.3 容量控制

| 策略 | 参数 | 说明 |
|---|---|---|
| 单行大小 | ~200 bytes | hash 64B + name ~20B + desc ~100B + meta |
| 容量上限 | 10,000 行 | ~2MB |
| 淘汰策略 | LRU by `last_hit_at` | 超过上限时删除最久未命中的 20% |
| 定期清理 | 每日凌晨（DreamAgent 同批） | 删除 90 天未命中的行 |

### 4.4 与现有 desc_cache 的关系

```
[启动时] image_recognition_cache 加载为内存 dict（热缓存）
[运行时] hash 命中内存 → 直接返回 + 更新 last_hit_at（异步）
[运行时] hash 未命中 → 走识别管线 → 写入内存 + 写入 SQLite
[重启后] 从 SQLite 恢复热缓存 → 无冷启动损失
```

现有 `ctx.desc_cache` 保留为热缓存层（内存 dict），SQLite 作为持久化后端。两层结构：内存读 O(1)，持久化保证跨重启不丢。

---

## 五、角色注册系统设计——SQLite 统一存储 + 文件包热加载

### 5.1 设计原则

- **运行时唯一真相源**：SQLite `character_recognition.db`，所有角色嵌入统一存 DB
- **热加载入口**：`storage/character_packs/` 目录，丢入 `.charpack` 文件夹即可加载（类比知识库 `docs/knowledge/` 放 .md）
- **增量同步**：启动时扫描包目录，hash 比对，仅变更部分写入 DB
- **不需要魔改 CCIP**：嵌入是标准 numpy 数组，打包/加载纯粹是 omubot 层的设计

### 5.2 存储架构

```
storage/
├── character_recognition.db     # SQLite：角色注册 + 识别缓存（统一）
└── character_packs/             # 热加载目录（类比 docs/knowledge/）
    ├── pjsk-all.charpack/       # 社区角色包
    │   ├── manifest.json
    │   ├── embeddings.npz
    │   └── thumbnails/          # 可选：admin SPA 展示用
    │       ├── tenma_tsukasa.jpg
    │       └── ...
    ├── vocaloid.charpack/       # 另一个角色包
    │   ├── manifest.json
    │   └── embeddings.npz
    └── custom.charpack/         # admin SPA 手动注册的角色（系统自动维护）
        ├── manifest.json
        └── embeddings.npz
```

### 5.3 SQLite Schema

```sql
-- storage/character_recognition.db

-- 角色注册表（从包加载 + admin 手动注册，统一存储）
CREATE TABLE character_registry (
    id           TEXT PRIMARY KEY,    -- 'pjsk:tenma_tsukasa' / 'custom:fengxiaomeng'
    pack_id      TEXT NOT NULL,       -- 'pjsk-all' / 'vocaloid' / 'custom'
    name         TEXT NOT NULL,       -- '天馬司'
    aliases      TEXT,                -- JSON array: ["司","tsukasa"]
    relation     TEXT DEFAULT 'known',-- self / friend / known
    source_work  TEXT,                -- 'Project SEKAI'
    embedding    BLOB NOT NULL,       -- numpy tobytes(), 768*4 = 3072 bytes
    model_name   TEXT NOT NULL,       -- 'ccip-caformer-24-randaug-pruned'
    created_at   TEXT DEFAULT (datetime('now'))
);

-- 角色包元数据（用于增量检测）
CREATE TABLE character_pack_meta (
    pack_id         TEXT PRIMARY KEY,
    version         TEXT,
    source_hash     TEXT NOT NULL,    -- sha256(manifest.json)，变更检测
    character_count INTEGER,
    loaded_at       TEXT DEFAULT (datetime('now'))
);

-- 识别缓存（§四已设计，同库）
CREATE TABLE image_recognition_cache (
    hash         TEXT PRIMARY KEY,
    character_id TEXT,
    character_name TEXT,
    description  TEXT NOT NULL,
    confidence   REAL,
    source       TEXT NOT NULL,
    created_at   TEXT DEFAULT (datetime('now')),
    last_hit_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX idx_registry_pack ON character_registry(pack_id);
CREATE INDEX idx_cache_last_hit ON image_recognition_cache(last_hit_at);
```

### 5.4 角色包格式（.charpack）

```json
// manifest.json
{
  "pack_id": "pjsk-all",
  "version": "1.0.0",
  "description": "Project SEKAI 全角色（26 人）",
  "model": "ccip-caformer-24-randaug-pruned",
  "characters": [
    {
      "id": "tenma_tsukasa",
      "name": "天馬司",
      "aliases": ["司", "tsukasa"],
      "relation": "known",
      "source_work": "Project SEKAI",
      "embedding_key": "tenma_tsukasa"
    },
    {
      "id": "akiyama_mizuki",
      "name": "暁山瑞希",
      "aliases": ["瑞希", "mizuki"],
      "relation": "friend",
      "source_work": "Project SEKAI",
      "embedding_key": "akiyama_mizuki"
    }
  ]
}
```

```python
# embeddings.npz 内容（numpy savez_compressed 格式）
# 每个 key 对应 manifest 中的 embedding_key
# 值为 ndarray, shape=(768,), dtype=float32
{
    "tenma_tsukasa": array([0.123, -0.456, ...]),   # 768 floats
    "akiyama_mizuki": array([0.789, -0.012, ...]),  # 768 floats
    ...
}
```

**包体积估算**：

| 规模 | embeddings.npz | manifest | 缩略图 | 总计 |
|---|---|---|---|---|
| PJSK 26 角色 | ~40 KB | ~3 KB | ~260 KB | **~300 KB** |
| Vocaloid 50 角色 | ~80 KB | ~5 KB | ~500 KB | **~600 KB** |
| 100 角色 | ~150 KB | ~10 KB | ~1 MB | **~1.2 MB** |

### 5.5 热加载流程（类比知识库 reindex）

```python
class CharacterPackLoader:
    """扫描 packs_dir，增量同步到 SQLite，重建内存索引。"""

    def __init__(self, packs_dir: Path, db: CharacterRecognitionDB):
        self._packs_dir = packs_dir
        self._db = db
        self._registry: dict[str, RegisteredCharacter] = {}

    def scan_and_sync(self) -> int:
        """启动时 + admin API 触发。返回总角色数。"""
        on_disk = self._discover_packs()       # {pack_id: path}
        in_db = self._db.get_pack_hashes()     # {pack_id: source_hash}

        for pack_id, pack_path in on_disk.items():
            manifest_hash = sha256_file(pack_path / "manifest.json")
            if manifest_hash == in_db.get(pack_id):
                continue  # 未变更，跳过
            self._load_pack_to_db(pack_id, pack_path, manifest_hash)

        # 磁盘上已移除的包 → 从 DB 删除
        for removed_id in set(in_db) - set(on_disk):
            self._db.remove_pack(removed_id)

        # 重建内存索引（启动后比较用）
        self._registry = self._db.load_all_embeddings()
        return len(self._registry)

    def _load_pack_to_db(self, pack_id: str, path: Path, manifest_hash: str):
        manifest = json.loads((path / "manifest.json").read_text())
        embeddings = np.load(path / "embeddings.npz")
        characters = []
        for char in manifest["characters"]:
            characters.append(RegisteredCharacter(
                id=f"{pack_id}:{char['id']}",
                pack_id=pack_id,
                name=char["name"],
                aliases=char.get("aliases", []),
                relation=char.get("relation", "known"),
                source_work=char.get("source_work", ""),
                embedding=embeddings[char["embedding_key"]],
                model_name=manifest["model"],
            ))
        self._db.replace_pack(pack_id, manifest_hash, characters)
```

### 5.6 与知识库模式的对比

| 维度 | 知识库 | 角色包 |
|---|---|---|
| 热加载目录 | `docs/knowledge/` | `storage/character_packs/` |
| 文件格式 | `.md` 文件 | `.charpack/` 目录（manifest + npz） |
| 持久化 DB | `knowledge_index.db` | `character_recognition.db` |
| 增量检测 | SHA-256(文件内容) | SHA-256(manifest.json) |
| 内存索引 | BM25 retriever | `dict[str, np.ndarray]` |
| 热加载触发 | 启动时 `reindex()` | 启动时 `scan_and_sync()` + admin API |
| admin 管理 | 无（纯文件） | admin SPA 上传/删除包 + 手动注册角色 |

### 5.7 用户操作流程

```bash
# 安装社区角色包——丢进目录
cp -r pjsk-all.charpack/ storage/character_packs/

# 方式 A：重启 bot（自动扫描）
docker compose restart bot

# 方式 B：调 admin API 热加载（不重启）
curl -X POST http://localhost:8081/api/admin/characters/reload
# → {"loaded": 26, "packs": ["pjsk-all", "custom"]}

# 卸载角色包——删除目录 + 重载
rm -rf storage/character_packs/pjsk-all.charpack/
curl -X POST http://localhost:8081/api/admin/characters/reload
```

### 5.8 构建角色包的 CLI 工具

```bash
# tools/build_character_pack.py
# 输入：参考图目录（每个子目录 = 一个角色，内含 5-10 张参考图）
# 输出：.charpack 目录

python tools/build_character_pack.py \
    --refs-dir ./pjsk_reference_images/ \
    --pack-id pjsk-all \
    --source-work "Project SEKAI" \
    --relation known \
    --output storage/character_packs/pjsk-all.charpack/

# 目录结构要求：
# pjsk_reference_images/
# ├── tenma_tsukasa/
# │   ├── ref_01.jpg
# │   ├── ref_02.png
# │   └── ...
# ├── akiyama_mizuki/
# │   └── ...
# └── ...
```

### 5.9 relation 字段的 Climate 信号映射

| relation | 含义 | Climate 效果 |
|---|---|---|
| `self` | bot 自己 | familiarity + 0.08（用户在用我的表情包） |
| `friend` | bot 熟人 | openness + 0.03（共同话题） |
| `known` | 已知角色（非熟人） | 无特殊效果，仅改善描述准确性 |
| `unknown` | 未注册角色 | 无效果，走现有 VL 描述 |

---

## 六、集成方式决策——服务层整合（非微服务）

### 6.1 结论

**整合进 `services/media/` 服务层**，与 VisionClient、ImageCache、StickerStore 同级。不做独立微服务/sidecar。

### 6.2 决策依据

| 维度 | 整合进服务层 ✅ | 独立微服务（挂载/sidecar）❌ |
|---|---|---|
| 架构复杂度 | 新增 1 个 .py，与 VisionClient 同模式 | 新 Dockerfile + compose service + HTTP API + 健康检查 |
| 调用延迟 | 函数调用 ~200ms | HTTP 序列化 + 网络 ~250-350ms |
| 内存占用 | ONNX session ~150MB，bot 进程内 | 同样 ~150MB，但多一个进程开销 |
| 容器余量 | 2GB limit，实测峰值 ~300MB + CCIP ~150MB = ~450MB，4x 余量 | 需额外分配 |
| 部署 | `docker compose restart bot` 一步 | 多一个 service 要管理 |
| 故障隔离 | CCIP 异常 → try/except 降级到 VL 描述 | 隔离更好，但运维成本高 |
| 代码风格 | 与现有 media 服务完全一致 | 打破单体架构先例 |

### 6.3 关键技术点

**ONNX 推理是同步阻塞的**——需要 `asyncio.to_thread()` 包装，避免阻塞 NoneBot 事件循环：

```python
import asyncio
import numpy as np
from imgutils.metrics import ccip_extract_feature, ccip_difference

class CharacterRecognizer:
    """CCIP-based anime character recognizer (services/media/ layer)."""

    async def extract_feature(self, image_data: bytes) -> np.ndarray:
        return await asyncio.to_thread(ccip_extract_feature, image_data)

    async def compare(self, feat: np.ndarray, ref: np.ndarray) -> float:
        return await asyncio.to_thread(ccip_difference, feat, ref)
```

**降级策略**——与 VisionClient 一致：

```python
try:
    result = await character_recognizer.identify(data)
except Exception:
    # CCIP 任何异常 → 回退到 Qwen VL 纯描述（现有行为）
    result = None
```

### 6.4 为什么不需要微服务隔离

1. **omubot 是单体架构**——所有 media 服务都是进程内 class，无微服务先例
2. **CCIP 不是常驻热路径**——持久化缓存命中后不再调用，只有首次新图才跑
3. **内存充裕**——2GB limit 远未触顶
4. **ONNX Runtime 稳定性高**——生产级推理引擎，不会随机 crash
5. **降级成本低**——回退到 VL 描述只是少了角色名，不影响核心功能

### 6.5 文件布局

```
services/media/
├── image_cache.py          # 已有：图片下载 + 缩放 + 磁盘缓存
├── vision.py               # 已有：Qwen VL 图片描述
├── sticker_capture.py      # 已有：表情包检测 + 语义标注
├── sticker_store.py        # 已有：表情包库管理
├── character_recognizer.py # 新增：CCIP 角色识别（~80 行）
├── animetrace_client.py    # 新增：AnimeTrace API 封装（~40 行）
└── recognition_cache.py    # 新增：SQLite 持久化缓存（~50 行）
```

---

## 七、实现路径

### Phase 1 — 持久化缓存 + AnimeTrace + CCIP 核心（~200 行）

| 任务 | 行数 | 说明 |
|---|---|---|
| `services/media/recognition_cache.py` | ~50 | SQLite 持久化缓存（建表 + CRUD + LRU 淘汰） |
| `services/media/animetrace_client.py` | ~40 | AnimeTrace API 封装（POST + 超时 + 降级） |
| `services/media/character_recognizer.py` | ~80 | CCIP 嵌入提取 + 比较 + 角色库加载 |
| `kernel/router.py` 集成 | ~30 | 三层管线插入到现有 vision 管线 |

**依赖**：`pip install dghs-imgutils`（已含 CCIP + 人脸检测 + 角色提取）

### Phase 2 — 角色注册 + Admin SPA（~100 行）

| 任务 | 行数 | 说明 |
|---|---|---|
| 角色注册 CLI 工具 | ~40 | `tools/register_character.py` — 批量提取参考图嵌入 |
| Admin SPA 角色管理页 | ~40 | 上传参考图 + 查看注册角色 + 缓存统计 |
| WD-Tagger 角色标签（可选） | ~20 | 作为 AnimeTrace 的补充验证信号 |

### Phase 3 — Climate 信号消费（~30 行）

| 任务 | 行数 | 说明 |
|---|---|---|
| character → Climate signal 映射 | ~30 | relation 字段驱动 familiarity/openness 信号 |

---

## 八、依赖与部署

### 8.1 CCIP 库状态（2026-05-27 调研）

| 维度 | 数据 |
|---|---|
| 包名 | `dghs-imgutils` |
| 最新版本 | v0.19.0 |
| 发布日期 | 2025-09-10 |
| 最后 push | 2025-10-11 |
| 2025 年发版数 | 12 个（约每月一版） |
| GitHub Stars | 386 |
| 维护者 | narugo1992, 7eu7d7 (IrisRainbowNeko) |
| 许可证 | MIT |
| Python 要求 | >=3.8（omubot 用 3.12，兼容） |

### 8.2 包体大小

| 组件 | 大小 | 说明 |
|---|---|---|
| `dghs-imgutils` wheel | ~465 KB | 纯 Python，极轻 |
| CCIP ONNX 模型 (`deepghs/ccip_onnx`) | ~100 MB | 首次运行自动从 HuggingFace 下载 |
| 运行时依赖链 (onnxruntime + numpy + PIL + opencv + scikit-learn) | ~150-200 MB | 新增安装量 |
| **Docker 镜像总增量** | **~300 MB** | 依赖 + 模型 |

### 8.3 依赖链与冲突分析

imgutils 核心依赖（omubot 当前均未安装）：

| 依赖 | 约束 | omubot 冲突风险 |
|---|---|---|
| numpy | <2 | 无（omubot 无 numpy）。未来若引入 numpy>=2 的库会冲突 |
| pillow | 无 | 无（omubot 用 pyvips，不冲突） |
| opencv-contrib-python | 无 | 无 |
| scikit-learn | 无 | 无 |
| huggingface-hub | 无 | 无 |
| onnxruntime | 隐式（lazy import） | 需显式声明 |
| hbutils, hfutils | >=0.9.0 | DeepGHS 自家工具，无冲突 |

### 8.4 新增依赖声明

```toml
# pyproject.toml — 在 dependencies 列表追加
"dghs-imgutils>=0.17.0",    # CCIP 角色识别（0.17 起 API 稳定）
"onnxruntime>=1.17.0",      # ONNX 推理引擎（imgutils lazy import，需显式声明）
```

注意：不需要 `dghs-imgutils[gpu]`，CPU 推理 ~200ms/张，配合持久化缓存足够。

### 8.5 CCIP API 用法（omubot 集成所需）

```python
from imgutils.metrics import ccip_extract_feature, ccip_difference, ccip_default_threshold

# ---- 注册阶段（管理员操作，一次性）----
# 输入：参考图 bytes / path / PIL.Image
feature = ccip_extract_feature('ref_fengxiaomeng_01.jpg')
# 输出：numpy.ndarray, shape=(768,), dtype=float32
# 存储为 .npy 文件

# ---- 运行时（每张新图）----
# 输入可以是 bytes（omubot 已下载的图片数据，无需转换）
incoming_feature = ccip_extract_feature(image_bytes)

# 与已注册角色嵌入比较
diff = ccip_difference(incoming_feature, stored_feature)
# diff < threshold → 同一角色

# 模型推荐阈值
threshold = ccip_default_threshold()  # → 0.178（默认模型）
```

**关键**：`ccip_extract_feature()` 直接接受 `bytes`——omubot 现有管线中 `data` 已经是下载好的图片 bytes，零转换成本。

### 8.6 模型选择

| 模型 | 嵌入维度 | 默认阈值 | 速度 | 推荐 |
|---|---|---|---|---|
| `ccip-caformer-24-randaug-pruned` | 768 | 0.178 | ~200ms | ✅ 生产用（默认） |
| `ccip-caformer-6-randaug-pruned_fp32` | 768 | 0.195 | ~80ms | 低延迟备选 |
| `ccip-caformer-5_fp32` | 768 | 0.184 | ~60ms | 极速备选 |

推荐默认模型——有持久化缓存，首次 200ms 可接受，后续 0ms。

### 8.7 Docker 集成

```dockerfile
# Dockerfile 新增（可选：预下载模型避免首次启动延迟）
RUN python -c "from imgutils.metrics import ccip_default_threshold; ccip_default_threshold()"
# 触发模型下载到 ~/.cache/huggingface/，约 100MB
```

### 8.8 冷启动预热

```python
# plugins/chat/plugin.py 启动阶段追加
async def _warmup_ccip():
    """预加载 CCIP ONNX session，避免首次请求延迟"""
    from PIL import Image
    from imgutils.metrics import ccip_extract_feature
    dummy = Image.new('RGB', (64, 64))
    ccip_extract_feature(dummy)  # 触发 ONNX session 初始化

# 在 on_startup 中调用（与现有 vision_client 预热同批）
```

### 8.9 与现有图像管线的兼容性

```
现有流程：
  image segment → httpx 下载 → pyvips 缩放 → bytes 缓存 → Qwen VL

CCIP 插入点：
  image segment → httpx 下载 → bytes（直接传 CCIP，不需要 pyvips 缩放）
                              ↘ pyvips 缩放 → Qwen VL（现有，不变）
```

CCIP 内部自行 resize 到 384×384，不依赖外部预处理。omubot 的 pyvips 缩放是给 Qwen VL 用的（控制 token 成本），CCIP 走原始 bytes 即可。

---

## 九、与现有系统的集成点

```
kernel/router.py:714-746（现有 vision 管线）
    │
    │ 替换现有 desc_cache 内存 dict 为持久化缓存：
    │
    ├── hash = sha256(data)
    ├── desc = await recognition_cache.get(hash)
    │   命中 → 直接用
    │
    ├── if desc is None:
    │       # 并行执行 AnimeTrace + CCIP
    │       at_result, ccip_result = await asyncio.gather(
    │           animetrace_client.identify(data),
    │           character_recognizer.identify(data),
    │       )
    │       # 合并决策
    │       character = merge_recognition(at_result, ccip_result)
    │       if character:
    │           emotion_desc = await vision_client.describe_emotion(data)
    │           desc = f"{character.name}{emotion_desc}"
    │       else:
    │           desc = await vision_client.describe_image(data)
    │
    └── await recognition_cache.put(hash, desc, source, confidence)
```

**与 sticker_store 的关系**：
- 已入库表情包：sticker_store.lookup_by_hash() 仍优先（在 Layer 1 之前，现有逻辑不变）
- 未入库表情包：走并行识别管线 → 结果写入 recognition_cache
- sticker_store 不加 character 字段——职责分离，识别缓存独立管理

---

## 十、引证索引

### 项目

| # | 项目 | 核心能力 | 适用场景 |
|---|---|---|---|
| 1 | deepghs/imgutils (CCIP) | 对比学习角色嵌入，F1=0.94 | 原创角色 + 已知角色 |
| 2 | AnimeTrace API | 在线动漫角色数据库查询 | 已知动漫角色 |
| 3 | SmilingWolf/wd-tagger-v3 | Danbooru 标签预测含角色标签 | 已知 Danbooru 角色 |
| 4 | Camais03/camie-tagger | 26,968 角色标签，F1=0.757 | 更广覆盖的已知角色 |
| 5 | Fuyucch1/yolov8_animeface | 动漫人脸检测，mAP50=0.953 | 预处理（人脸定位） |
| 6 | FlowElement-ai/fanjing-face-recognition | ArcFace 动漫人脸识别 | 自建方案参考 |

### 论文/数据集

| # | 名称 | 贡献 |
|---|---|---|
| 1 | iCartoonFace (iQiyi) | 最大卡通人脸数据集，5000+ 角色，400K+ 图片 |
| 2 | Danbooru 2018 Character Recognition | 970K 图片，70K 角色 |
| 3 | AniWho (2022) | Prototypical Networks 动漫人脸分类，5-way 5-shot 89.27% |
| 4 | CCIP 技术文档 (DeepGHS) | 对比学习 + 角色聚类方法论 |

---

## 十一、决策模板

```text
角色识别方案：
[x] AnimeTrace + CCIP 并行，结果合并 + 持久化缓存 — 已确定
[ ] 串行（AnimeTrace → CCIP）— 延迟劣势，弃用
[ ] 仅 CCIP（强匹配问题，不推荐）
[ ] 仅 AnimeTrace API（不支持原创角色，不推荐）

实现范围：
[ ] Phase 1 only（缓存 + 并行识别核心，~200 行）
[ ] Phase 1 + 2（含注册工具 + Admin SPA，~300 行）
[ ] Phase 1 + 2 + 3（含 Climate 信号，~330 行）

部署方式：
[ ] CPU 推理（~500ms/张，无需 GPU）
[ ] GPU 加速（~100ms/张，需 CUDA）

执行批次：
[ ] 独立 PR（Part 0 前置）
[ ] 与 Part 0 P0-1~P0-4 同 PR
[ ] 其他：___
```
