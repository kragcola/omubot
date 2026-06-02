# 表情包系统

## 功能

- **收藏**：管理员或静默学习链路把群内图片收录为表情包，按内容哈希去重。
- **发送**：LLM 通过 `send_sticker` 工具选择合适表情包，作为回复的一部分发送。
- **管理**：更新描述、使用场景和 OCR 文本，删除过期表情包。
- **检索**：本地 BM25 意图检索会同时利用 `description`、`usage_hint`、`ocr_text`。
- **整理**：Dream / 后台任务会回填 OCR 与情绪标签，并可做低频清理。

## LLM 工具

| 工具 | 权限 | 说明 |
|------|------|------|
| `send_sticker` | 所有 | 从表情包库中选择并发送合适的表情包 |
| `save_sticker` | 管理员 | 收录对话中的图片到表情包库 |
| `manage_sticker` | 管理员 | 更新描述、使用场景、OCR 文本，或删除 |

## 存储格式

```text
storage/stickers/
├── stickers.db              # SQLite 索引
├── index.json               # 旧版 JSON 索引迁移后的冻结回滚快照（若存在）
├── stk_a1b2c3d4.jpg
├── stk_962a7e68.gif
└── ...
```

`StickerStore` 现在以 SQLite 为主存储，运行时会把小规模表情库镜像到内存，并在需要时重建 BM25 检索索引。早期 `index.json` 会在首次迁移时导入 SQLite，之后仅作为回滚快照保留。

## 检索与描述

当前检索使用本地轻量语义路径：

- `description`：一句话描述这个表情包看起来像什么。
- `usage_hint`：建议在什么语气或场景下使用。
- `ocr_text`：图上文字；由富描述 / OCR 回填后纳入检索。
- `search_by_intent()`：对上述字段做 BM25 评分，返回最可能的候选。

这意味着表情包系统已经不再只是“全库 prompt 盲挑”；即使没有向量库，也能对“告别、无语、安慰、吐槽”这类意图做本地召回。

## 发送机制

- bot 会把图片文件通过 NapCat API 发出；动图会保留 GIF 形态。
- `summary=[动画表情]` / 贴图参数由发送链路处理，QQ 侧按贴图渲染。
- 支持 JPEG、PNG、WEBP、GIF。

## 表情包频率

`config/config.json` -> `sticker.frequency`。已有 `config/config.toml` 的 `[sticker].frequency` 仍兼容读取：

| 频率 | 规则 |
|------|------|
| `rarely` | 颜文字强制配图，其他需评分 >= 8 |
| `normal` | 颜文字强制配图，其他需评分 >= 6 |
| `frequently` | 每条消息默认配图，纯技术解答可免 |

## 颜文字强制规则

回复中含颜文字（如 `(≧▽≦)/`、`(*^▽^*)`）或括号动作描写（如 `（笑）`、`（叹气）`）时，`StickerPlugin` 会在 prompt 中强制要求同时调用 `send_sticker`。在 `frequently` 模式下，这几乎等同于“表情包是回复正文的一部分”。
