# 命令

所有命令以 `/` 开头，群聊和私聊均可使用（部分命令有权限限制）。

## 命令系统

omubot 使用声明式命令注册，在 `register_commands()` 中返回 `Command` 对象即可。框架自动处理权限门禁、参数校验、帮助文本生成。

### 门禁字段

`Command` 支持以下元数据字段，由 `CommandDispatcher` 在调用 handler 之前统一检查：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `admin_only` | `bool` | `False` | 仅管理员可用。非管理员自动回复"无权限" |
| `private_only` | `bool` | `False` | 仅私聊可用。群聊自动回复"请在私聊中使用此指令" |
| `require_args` | `bool` | `False` | 需要参数。无参数时自动回复 `usage` |
| `hidden` | `bool` | `False` | 在 `format_help()` 中隐藏（用于占位父命令） |
| `passthrough_unknown` | `bool` | `False` | 未知子命令不报错，透传给父 handler（如 `/debug <text>` 送入 LLM） |

门禁从父命令继承：父命令设 `admin_only=True`，所有子命令自动受保护。

### 自动帮助

`Command.format_help()` 从元数据递归生成帮助文本，自动标注门禁：

```
管理食物偏好配置：
/food help — 显示全部食物偏好管理指令
/food search <参数> — 开关 Web 搜索功能（仅管理员）
/food like <参数> — 添加喜欢的食物偏好
/food info — 查看你当前的食物偏好和地区（仅私聊）
```

## 全部命令

### 食物推荐

| 命令 | 别名 | 权限 | 说明 |
|------|------|------|------|
| `/吃什么 [口味]` | `/吃`, `/c` | 所有人 | 推荐食物，首次使用显示提示（仅一次） |
| `/food help` | `/food h` | 所有人 | 显示食物偏好管理帮助（自动生成） |
| `/food like <食物>` | `喜欢`, `爱吃` | 所有人 | 添加喜欢的食物，群聊回复"已记录，试试 /吃什么" |
| `/food dislike <食物>` | `不喜欢`, `不吃`, `讨厌` | 所有人 | 添加不喜欢的食物 |
| `/food location <地区>` | `地区`, `位置`, `在哪` | 所有人 | 设置所在地区 |
| `/food info` | `查看`, `信息`, `我的` | 仅私聊 | 查看当前偏好（含个人数据） |
| `/food search on\|off` | `搜索` | 管理员 | 开关 Web 搜索（默认关，用本地食物库） |

### 调试与管理

| 命令 | 别名 | 权限 | 说明 |
|------|------|------|------|
| `/debug [问题]` | — | 管理员 | 调试模式。无参数显示系统状态；中文文本送入 LLM 直接执行 |
| `/debug save [描述]` | `保存`, `收录` | 管理员 | 保存最近图片到表情包库 |
| `/debug send [id\|gif]` | `发`, `发送` | 管理员 | 发送表情包（指定 ID 或随机；加 `gif` 只发动图） |
| `/debug split <文本>` | `分段`, `分割` | 管理员 | 测试文本分段效果 |
| `/authority <QQ号> [0-4\|reset]` | `权限`, `授权` | 管理员 | 查询或设置用户指令权限等级（隐藏管理命令） |
| `/plugins` | `/p`, `/plg`, `/插件` | 管理员 | 列出所有已加载插件 |
| `/version` | — | 所有人 | 查看版本并检查 GitHub 更新 |

### `/debug` 详解

调试模式直接操作工具，不经过 thinker：

```
/debug                       # 无参数 → 显示系统状态摘要
/debug 发送表情               # 送入 LLM → 调用 send_sticker
/debug 我的好感度是多少        # 送入 LLM → 调用 lookup_cards
/debug send stk_cdf94f9b     # 子命令 → 发指定 ID
/debug save                  # 子命令 → 保存上一张图片
/debug split 测试文本         # 子命令 → 显示分段结果
```

### `/version` 输出示例

```
Omubot v1.5.0
GitHub: https://github.com/kragcola/omubot

（无法连接 GitHub，未检查更新）
```

本地版本号由 `services/version.py` 从 `pyproject.toml` 读取，`/version` 再按配置决定是否联网检查 GitHub release。
