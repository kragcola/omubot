# 表情包系统

## 功能

- **收藏**：管理员通过对话收录表情包，SHA256 去重
- **发送**：LLM 按场景和情绪匹配合适的表情包
- **管理**：更新描述/场景说明，删除过期表情包
- **整理**：Dream Agent 定期清理低使用率表情包

## LLM 工具

| 工具 | 权限 | 说明 |
|------|------|------|
| `send_sticker` | 所有 | 从库中选择合适的表情包发送 |
| `save_sticker` | 管理员 | 收录对话中的图片到表情包库 |
| `manage_sticker` | 管理员 | 更新描述/场景说明，或删除 |

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

## 表情包频率

`config/config.toml` → `[sticker].frequency`：

| 频率 | 规则 |
|------|------|
| `rarely` | 颜文字强制配图，其他需评分 ≥ 8 |
| `normal` | 颜文字强制配图，其他需评分 ≥ 6 |
| `frequently` | 每条消息默认配图，纯技术解答可免 |

## 颜文字强制规则

回复中含颜文字（`(≧▽≦)/` `(*^▽^*)` 等）或括号动作描写（（笑）（叹气）等）→ **必须**调用 `send_sticker`。
