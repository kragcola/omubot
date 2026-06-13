# 角色识别

Omubot 的角色识别链路现在已经从“单角色试验”发展成一条独立的运行时子系统：模型和嵌入构建在 `ccip-sidecar`，bot 本地维护角色 registry、识别缓存、`self/friend/known` 关系，以及 Admin 录入和运维界面。

## 组件分工

| 组件 | 位置 | 职责 |
| --- | --- | --- |
| sidecar | `ccip-sidecar/server.py` | `CCIP` 嵌入、`/identify`、`/identify-multi`、`/build-pack`、`/build-series-pack`、`/health` |
| bot 识别客户端 | `services/media/character_recognizer.py` | 调 sidecar、合并 AnimeTrace、补齐名字 / 关系 / work / `context_label` |
| 角色注册表 | `storage/character_recognition.db` | `character_registry`：角色名、别名、relation、pack/work/series 元数据 |
| 识别缓存 | `storage/character_recognition.db` | `image_recognition_cache`：按图片哈希缓存识别结果与 prompt 上下文字段 |
| 角色包 | `config/character_packs/*.charpack/` | manifest、`embeddings.npz`、样例图；属于 gitignored 运行时数据 |
| 管理端 | `/admin/characters` | 录入、合并、重扫、关系编辑、sidecar 健康观测 |

## 当前能力

- **多角色识别**：sidecar 暴露 `/identify-multi`，对同一张图做 head detection + per-crop CCIP；每个 head 会保留 `candidate_*`、`detection_count`、`crop_padding`、`crop_bbox` 等审计字段。
- **单图命中缓存**：bot 会把已识别过的图片写入 `image_recognition_cache`，下次优先走缓存。
- **关系是 per-bot 语义**：角色 embedding 与样例属于系列包；`self/friend/known` 只在 bot 本地 registry 维护。
- **系列 pack**：一个 `.charpack` 可以包含多个角色，manifest 顶层支持 `series/work/relation_default` 继承。
- **细上下文**：`work` 主要用于聚合和管理，`context_label` 用于 prompt 中更细的出处展示，例如 `Project SEKAI / Virtual Singer`。
- **证据渲染**：图片 prompt 会区分“可信识别”和“低置信候选”；候选只用于提醒模型可能是谁，不作为确定身份。
- **Admin 录入链路**：支持单角色录入、从新图片生成系列 pack、把已有安全单角色包合并成系列 pack。
- **启动自动归并**：`auto_merge_series_packs=true` 时，bot 启动前会把同一 `work` 下的安全单角色包归并为系列包。

近期运行时已经用系列 pack 管理 `project_sekai`、`bangdream`、`zh_virtual_singers`、`ja_virtual_singers`。具体数量与命中状态以 `/admin/characters` 和 sidecar `/health` 为准。

## 配置

主配置位于 `config/config.json` 的 `vision.character_recognition`：

```json
{
  "vision": {
    "character_recognition": {
      "enabled": false,
      "sidecar_url": "http://host.docker.internal:8620",
      "packs_dir": "config/character_packs",
      "timeout_seconds": 5.0,
      "animetrace_enabled": true,
      "animetrace_model": "anime_model_lovelive",
      "animetrace_timeout_seconds": 8.0,
      "multi_char_enabled": true,
      "auto_merge_series_packs": true
    }
  }
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `enabled` | 是否启用整条角色识别链路；关闭时完全旁路回普通图片描述 |
| `sidecar_url` | sidecar 基地址，默认 `http://host.docker.internal:8620` |
| `packs_dir` | 角色包目录，默认 `config/character_packs` |
| `timeout_seconds` | sidecar 识别超时 |
| `animetrace_enabled` | 是否并行使用 AnimeTrace 辅助 work/作品判断 |
| `multi_char_enabled` | bot 侧是否调用 `/identify-multi` |
| `auto_merge_series_packs` | 启动前是否自动把安全单角色包归并为系列包 |

## 角色包格式

当前推荐的角色包是目录式 `.charpack/`：

```text
config/character_packs/project_sekai.charpack/
├── manifest.json
├── embeddings.npz
└── samples/
    └── hatsune_miku/
        ├── 0.jpg
        └── 1.jpg
```

manifest 已支持继承式字段：

```json
{
  "pack": "project_sekai",
  "series": "project_sekai",
  "work": "Project SEKAI",
  "relation_default": "known",
  "characters": [
    {
      "character_id": "hatsune_miku",
      "name": "初音未来",
      "context_label": "Project SEKAI / Virtual Singer"
    }
  ]
}
```

要点：

- `work` 适合放粗粒度作品或系列名，用于聚合和自动归并。
- `context_label` 适合放 prompt 里真正想展示给模型的细出处。
- `relation_default` 只提供默认值；具体 `self/friend/known` 仍可在 registry 中覆盖。

## Admin 与 API

主要页面与接口：

| 页面 / 接口 | 用途 |
| --- | --- |
| `/admin/characters` | 角色列表、系列聚合、缓存命中率、sidecar 状态 |
| `GET /api/admin/characters` | 角色总表、pack/work/series、cache、sidecar health |
| `POST /api/admin/characters/build` | 单角色录入 |
| `POST /api/admin/characters/build-series` | 从新图片生成系列 pack |
| `POST /api/admin/characters/merge-series` | 合并已有安全单角色包 |
| `POST /api/admin/characters/reload` | 重扫角色包并同步 registry |
| `PATCH /api/admin/characters/{character_id}` | 更新名字、relation、aliases |
| `GET /api/admin/characters/{character_id}/sample` | 返回样例缩略图 |

## 运维与回滚

常用检查：

1. `/admin/characters`：看角色数、pack 聚合、sample 是否可读。
2. `http://localhost:8620/health`：看 sidecar `pack_count`、`character_count`、`registry_version`。
3. `storage/character_recognition.db`：核对 registry 与 cache 是否同步。
4. `config/character_packs/`：确认系列包和 `.merged/` 归档状态。

回滚策略：

- **关功能**：`vision.character_recognition.enabled=false` 后重启 bot。
- **关自动归并**：`auto_merge_series_packs=false` 后重启 bot。
- **回滚系列包**：把 `.merged/<series>/*.charpack` 移回 `config/character_packs/`，删除归并后的系列包，再调用 `/api/admin/characters/reload`。
- **回滚代码**：按需 rebuild `bot` 和 `ccip-sidecar`；NapCat 不动。
