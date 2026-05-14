# 表情包系统

## 功能

- **收藏**：管理员通过对话收录表情包，SHA256 去重
- **静默学习**：`silent_learn` 群可在不发言的前提下收录群友发送的表情包
- **发送**：LLM 按场景和情绪匹配合适的表情包
- **管理**：更新描述/场景说明，删除过期表情包
- **整理**：Dream Agent 定期清理低使用率表情包

## LLM 工具

| 工具 | 权限 | 说明 |
|------|------|------|
| `send_sticker` | 所有 | 从库中选择合适的表情包发送 |
| `save_sticker` | 管理员 / bot 主动 | 收录对话中的图片到表情包库；管理员要求时填写 `requested_by`，bot 主动偷表情时留空 |
| `manage_sticker` | 管理员 | 更新描述/场景说明，或删除 |

## 静默学习群

- `presence_mode = "silent_learn"` 的群不会触发回复、LLM 或工具调用，但 StickerPlugin 会在 `on_message` 阶段轻量识别 QQ 表情图片。
- 只收录表情特征明确的图片：`sub_type=1/7`，或摘要包含“动画表情 / 表情 / mface / sticker”。普通截图、照片不会自动入库。
- 每条消息最多收录 2 张，使用 SHA256 去重，来源标记为 `stolen_silent_learn`。
- 若全局或该群显式配置 `tools_enabled = false`，或该群 `sticker_mode = "off"`，静默收录也会关闭。仅因群处于 `silent_learn`、不允许主动发言而派生出的 `tools_enabled=false` 不会阻止静默收录。

## 存储格式

```
storage/stickers/
├── index.json                     # 索引
├── sha256_map.json                # SHA256 → id 去重
└── files/
    ├── stk_a1b2c3d4.jpg
    └── stk_962a7e68.gif           # GIF 动图支持
```

## 发送机制

- `sub_type=1` + `summary=[动画表情]` 使 QQ 渲染为贴图样式
- base64 编码内联传输（bot 与 napcat 在不同容器）
- 支持 JPEG、PNG、GIF 格式
- 发送成功后会记录到 `storage/stickers/usage.json`，按全局、群聊、私聊会话保留近期使用窗口；`index.json` 的 `send_count/last_sent` 继续作为长期统计。

## 高频冷却

表情包系统允许凤笑梦偏爱元气、可爱、正向的表情，但会避免长期只发同一张：

- 同一群最近 6 次表情发送中出现过的表情，会被暂时拦截。
- 全局最近 20 次中同一表情出现 4 次后，会被暂时拦截。
- 单张表情历史占比达到 20% 且全库总发送不少于 10 次时，如果 24 小时内刚发过，也会被暂时拦截。
- 表情库少于 8 张，或可替代候选少于 3 张时，不启用硬拦截，避免小库不可用。
- `send_sticker` 被冷却拦截时不会发图、不会增加计数，会把 3-6 个替代表情 ID 返回给模型，让模型在同一轮改选。
- Prompt 中的表情包库会优先展示推荐候选，并少量列出“冷却中，请不要选择”的 ID，减少模型继续撞墙。

## 表情包频率

`config/config.json` → `sticker.frequency`。已有 `config/config.toml` 的 `[sticker].frequency` 仍兼容读取：

| 频率 | 规则 |
|------|------|
| `rarely` | 颜文字强制配图，其他需评分 ≥ 8 |
| `normal` | 颜文字强制配图，其他需评分 ≥ 6 |
| `frequently` | 每条消息默认配图，纯技术解答可免 |

## 颜文字强制规则

回复中含颜文字（`(≧▽≦)/` `(*^▽^*)` 等）或括号动作描写（（笑）（叹气）等）→ **必须**调用 `send_sticker`，并选择合适且近期未重复的表情包。
