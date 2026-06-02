# CCIP 多角色识别 — 实施方案

> 状态：方案定稿，待实施。2026-06-01。

## 背景

CCIP 当前 `/identify` 对整张图提取一个 768 维全局特征，做 single-nearest-neighbor 返回单角色。实测：2×2 四人图只有 1/4 命中。`dghs-imgutils`（sidecar 已有依赖，v0.19.0）内置 `detect_heads`（YOLO-based ONNX 动漫头部检测器，`deepghs/anime_head_detection` head_detect_v2.0_s），逐 crop 跑 CCIP 可命中全部角色。

### 实测数据（容器内 warm 路径）

| 场景 | detect_heads | CCIP | 总耗时 | 识别结果 |
|---|---|---|---|---|
| 4 角色合成图（512×512） | 4 heads, 0.77s | 4× ~0.78s | **3.9s** | 4/4 ✓ |
| 单角色（天马司） | 1 head, 0.27s | 0.67s | ~0.94s | ✓ |
| 单角色（凤笑梦） | 1 head, 0.27s | 0.11s | ~0.38s | ✓ |
| 空白图 | 0 heads, 0.19s | — | ~0.19s | 空 |

### 延迟优化（方案中纳入）

1. **detection 与 full-image CCIP 并行**：`asyncio.gather(detect_heads, ccip_full)` → `max(0.8, 0.8) = 0.8s`
2. **CCIP batch extraction**：`ccip_batch_extract_features(crops)` 一次调提取所有 crop 特征，4 crops 从 4×~0.8s → ~1.0s

优化后期望：多角色 ~1.8s，单角色 ~0.8s（与当前持平）。

---

## 架构决策

### D1：新增 sidecar 端点 `/identify-multi`，不动 `/identify`

**理由**：
- `/identify` 的 schema 被 `CharacterRecognizer._request_identify` → `_ccip_identify` → `CharacterRecognition` 整条链路消费，下游 7 个文件、L2 缓存 SQL schema 全是单角色假设
- 新端点返回 `characters: [...]` 列表，旧端点保持不变，**零回退风险**
- 调用方通过 `CCIP_MULTI_CHAR_ENABLED` 环控变量选择路径

### D2：`CharacterRecognition` 保持单角色不变

`identify()` 的返回类型从 `CharacterRecognition | None` → `list[CharacterRecognition]`（空列表 = 无匹配）。

**理由**：`CharacterRecognition` 是 frozen dataclass，11 个字段全是标量，语义清晰。不需要包装类。

### D3：`_describe_image_data` 格式：逗号分隔角色名

```
单角色：«图片1: 凤笑梦（プロセカ）：描述»
多角色：«图片1: 凤笑梦（プロセカ）、天马司（プロセカ）、宵崎奏（プロセカ）：描述»
无角色：«图片1: 描述»  （回落 VL）
```

当 ≥2 角色被识别时，VL 描述取第一个角色的 crop 或全图（共享一张 VL 描述）。

### D4：L2 缓存暂不缓存多角色结果

多角色图的组合爆炸（哪些角色同时出现）使 SHA-256 作为缓存键收益降低。首版不缓存多角色路径；单角色路径照常走 L2。

**理由**：单角色图占比 >90%，多角色是少数路径。多角色路径已有 detection + batch CCIP 成本，再加一层缓存设计会增加迁移复杂度。后续可根据生产数据加。

### D5：功能开关在 sidecar 和 bot 端各一个

- **Sidecar 端**：环控变量 `CCIP_MULTI_CHAR_ENABLED`（默认 `"1"`），控制 `/identify-multi` 是否走 detection 路径（关闭时直接返 full-image CCIP 单结果）
- **Bot 端**：`CharacterRecognitionConfig.multi_char_enabled: bool = True`，控制 `identify()` 是否调新端点

双层开关：任一关闭即回退当前行为。

---

## 改造清单

### 阶段 1：Sidecar（`ccip-sidecar/server.py`）

| # | 改动 | 说明 |
|---|---|---|
| 1.1 | 新增 `import asyncio` | sidecar 原来纯同步；detection + CCIP 并行需要协程 |
| 1.2 | 新增 `from imgutils.detect import detect_heads` | 头部检测 |
| 1.3 | 新增 `_MULTI_CHAR_ENABLED = os.getenv("CCIP_MULTI_CHAR_ENABLED", "1") == "1"` | 开关 |
| 1.4 | 新增 `async def _identify_multi(image_data: bytes) -> dict` | 并行跑 full-image CCIP + detect_heads → 如 ≥2 heads → batch CCIP per crop → merge |
| 1.5 | 新增 `POST /identify-multi` 端点 | 接受图片，返回 `{"matched": bool, "characters": [{...}, ...], "detection_count": int, "source": "ccip-sidecar"}` |
| 1.6 | 新增 `ccip_batch_extract_features` 调用 | 从 `imgutils.metrics` import，一次 batch 提取所有 crop 特征 |

**`/identify-multi` 响应 schema**：

```json
{
  "matched": true,
  "characters": [
    {
      "character_id": "tenma_tsukasa",
      "character_name": "天马司",
      "difference": 0.0567,
      "bbox": [37, 23, 139, 116]
    }
  ],
  "detection_count": 4,
  "threshold": 0.17847511429108218,
  "registry_version": "...",
  "api_version": "2026-06-01.v1",
  "source": "ccip-sidecar"
}
```

- `matched`: 任一角色命中阈值即为 `true`
- `characters`: 每个 crop 的 CCIP 最近邻结果（已在阈值内的排在前面，超出阈值的 append 在后）
- `detection_count`: detect_heads 返回的 head 数
- `bbox`: 归一化到 [0,1] 的检测框坐标

**`_identify_multi` 伪代码**：

```python
async def _identify_multi(image_data):
    # 1. 并行：全图 CCIP + detect_heads
    full_ccip, heads = await asyncio.gather(
        run_in_executor(_identify, image_data),   # 复用现有全图识别
        run_in_executor(detect_heads, image),
    )
    
    # 2. 决策
    if len(heads) <= 1:
        # 单角色或无角色 → 退化为全图结果
        return _format_single(full_ccip, heads)
    
    # 3. 多角色：逐 crop batch CCIP
    crops = [img.crop(bbox) for bbox, _, _ in heads]
    features = ccip_batch_extract_features(crops, model=MODEL_NAME)
    
    # 4. 每个 crop 匹配最近 centroid
    results = []
    for feat, (bbox, _, score) in zip(features, heads):
        best = find_nearest(feat, registry.entries)
        results.append({...best..., bbox=bbox})
    
    return {"matched": any(r["matched"] for r in results), "characters": results, ...}
```

### 阶段 2：Recognizer（`services/media/character_recognizer.py`）

| # | 改动 | 说明 |
|---|---|---|
| 2.1 | `identify()` 返回类型改为 `list[CharacterRecognition]` | 空列表 = 无匹配；单元素 = 单角色；多元素 = 多角色 |
| 2.2 | 新增 `_identify_multi_path()` | 调 sidecar `/identify-multi`，解析 `characters` 数组，每项查 `_metadata_from_db` / `_metadata_for` 补 relation/name/work |
| 2.3 | `identify()` 内分支：`if self._multi_char_enabled → _identify_multi_path() else 当前路径` | 当前路径返回 `[result]`（单元素列表）以统一返回类型 |
| 2.4 | 新增 `self._multi_char_enabled` 属性 | 从 `CharacterRecognitionConfig.multi_char_enabled` 读取 |
| 2.5 | AnimeTrace 不参与多角色路径 | `_identify_multi_path` 只调 sidecar，不并行 AnimeTrace（detection 已占满 CPU，加 AT 无收益且可能返回错 work） |

### 阶段 3：Router 格式化（`kernel/router.py`）

| # | 改动 | 说明 |
|---|---|---|
| 3.1 | `_describe_image_data` 适配 `list[CharacterRecognition]` | 接收列表，循环取 `character_name`、`relation`（取第一个非 None）、`work` |
| 3.2 | 新格式逻辑 | 0 角色 → 纯 VL；1 角色 → 现有格式；≥2 角色 → 逗号分隔 `A（出处）、B（出处）：描述` |
| 3.3 | mood nudge 取第一个 self/friend | 从列表中取第一个 relation ∈ {self, friend} 的，无则跳过 |

### 阶段 4：Config（`kernel/config.py`）

| # | 改动 | 说明 |
|---|---|---|
| 4.1 | `CharacterRecognitionConfig` 新增 `multi_char_enabled: bool = True` | 默认开启 |
| 4.2 | Sidecar `docker-compose.yml` 加 `CCIP_MULTI_CHAR_ENABLED=1` | 环控变量 |

### 阶段 5：L2 缓存（`services/media/recognition_cache.py`）

| # | 改动 | 说明 |
|---|---|---|
| 5.1 | 不改 schema | 多角色结果不写入 L2 缓存（D4 决策） |
| 5.2 | `CharacterRecognizer.identify()` 多角色路径跳过 `recognition_cache.put()` | 仅单角色路径继续缓存 |

### 阶段 6：测试

| # | 改动 | 说明 |
|---|---|---|
| 6.1 | `test_character_recognizer.py` | 新增 `test_identify_multi_char_returns_list`、`test_identify_single_char_still_works`、`test_identify_multi_char_disabled_falls_back` |
| 6.2 | `test_animetrace_merge.py` | 现有 9 个 merge 测试不变（merge 逻辑不改）；新增 `test_multi_path_skips_animetrace` |
| 6.3 | `test_render_message_character_recognition.py` | `_FakeCharacterRecognizer.identify` 改为返回 list；新断言验证多角色格式化 `"A、B：描述"` |
| 6.4 | `test_config_loader.py` | 新增 `multi_char_enabled` 默认值断言 |
| 6.5 | 新增 `test_ccip_sidecar_multi.py` | Sidecar `/identify-multi` 端点的集成测试 |
| 6.6 | `test_character_registry_db.py` | 多角色跳过 L2 缓存的断言 |

---

## 回滚路径

```
# 彻底关闭（bot + sidecar 两端）
# 1. 环控变量
CCIP_MULTI_CHAR_ENABLED=0  # docker-compose.yml → restart sidecar
# 2. 配置（或直接保留 sidecar 旧行为，bot 端关）
character_recognition.multi_char_enabled = false  # config.toml → restart bot
```

关闭后 `identify()` 走旧 `/identify` 端点，返回 `[single_result]`（单元素列表），`_describe_image_data` 取 `results[0]` 与现有行为完全一致。

---

## 不改的文件（明确排除）

| 文件 | 原因 |
|---|---|
| `services/media/animetrace_client.py` | 多角色不走 AnimeTrace（D2.5） |
| `services/media/character_registry_db.py` | 单行 PK 不变，`get()` 仍查单个 character_id |
| `admin/routes/api/characters.py` | 不消费识别结果 |
| `services/llm/client.py` | 不直接消费识别结果 |
| `ccip-sidecar/server.py:/identify` 端点 | 保持不变 |
| `ccip-sidecar/server.py:/build-pack` | 与识别无关 |
| `ccip-sidecar/Dockerfile` | `dghs-imgutils` 已含 detect_heads，无需新依赖 |
| config/character_packs/*.charpack | centroids 不变 |

---

## 文件变更总览

```
 新: docs/migrations/ccip-multi-character-2026-06-01.md  (本文件)
 改: ccip-sidecar/server.py           (+ ~120 行: /identify-multi, detect_heads, batch CCIP)
 改: services/media/character_recognizer.py  (+ ~60 行: multi path, list return type)
 改: kernel/router.py                  (+ ~15 行: 多角色格式化)
 改: kernel/config.py                  (+ 1 字段: multi_char_enabled)
 改: services/media/recognition_cache.py  (+ 0 行: 仅注释说明)
 改: docker-compose.yml                (+ 1 行: CCIP_MULTI_CHAR_ENABLED)
 新: tests/test_ccip_sidecar_multi.py  (~80 行)
 改: tests/test_character_recognizer.py   (+ ~30 行)
 改: tests/test_render_message_character_recognition.py (+ ~30 行)
 改: tests/test_config_loader.py          (+ ~5 行)
 改: tests/test_character_registry_db.py  (+ ~10 行)
 改: tests/test_animetrace_merge.py       (+ ~15 行)
```

## 阶段门禁

| 阶段 | 门禁 |
|---|---|
| 1 完成 | sidecar `/identify-multi` 可独立 curl 测试 |
| 2 完成 | `CharacterRecognizer.identify()` 返回 list |
| 3 完成 | 群聊消息里多角色图显示为 `A、B、C：描述` |
| 4-5 完成 | 配置可控，缓存跳过 |
| 6 完成 | 全量 pytest 通过，ruff + pyright 0 err |
| 发布 | rebuild sidecar + bot，napcat 不动（D6），群内实测 |
