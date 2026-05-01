# 08 — 迁移指南

从旧 `src/` 单体到 Omubot 插件架构的逐步迁移。

## 迁移状态

| 阶段 | 内容 | 状态 |
|------|------|------|
| Phase 1 | 内核层（types.py + bus.py） | ✅ 完成 |
| Phase 2 | 配置系统重组 | ✅ 完成 |
| Phase 3 | 系统服务迁移 | ✅ 完成 |
| Phase 4 | ChatPlugin 拆分 | ✅ 完成 |
| Phase 5 | 全部 14 个插件切出 | ✅ 完成 |
| Phase 6 | src/ 耦合清理 + 垫片删除 | ✅ 完成 (2026-05-01) |
| Phase 6a | 人格硬编码解耦（凤笑梦 → identity.name） | ✅ 完成 (2026-05-01) |
| Phase 6b | config/ 目录隔离 + 开源准备 | ✅ 完成 (2026-05-01) |
| Phase 7 | 单文件插件 + plugin.json + 依赖解析 | ✅ 完成 (2026-05-01) |
| Phase 7b | .omu 打包 | 远期 |
| Phase 8 | 热重载 + 第三方生态 | 远期 |

## 通用迁移模式

### Step 1: 识别迁移对象

查看 `omubot/feature-classification.md` 了解每个现有模块的分类和目标位置。

### Step 2: 创建插件骨架

```python
# plugins/<name>/plugin.py
from kernel.types import AmadeusPlugin

class XxxPlugin(AmadeusPlugin):
    name = "xxx"
    description = "..."
    priority = 50
```

### Step 3: 迁移钩子逻辑

旧模式（直接调用）：
```python
# bot.py 中
await affection.update_on_reply(user_id, reply_content)
```

新模式（钩子）：
```python
class AffectionPlugin(AmadeusPlugin):
    async def on_post_reply(self, ctx: ReplyContext) -> None:
        await self.update_affection(ctx.user_id, ctx.reply_content)
```

### Step 4: 迁移工具

旧模式：
```python
# src/tools/memo_tools.py
class LookupCardsTool(Tool):
    ...
# bot.py 中手动注册
tools = [LookupCardsTool(), ...]
```

新模式：
```python
class MemoToolsPlugin(AmadeusPlugin):
    def register_tools(self) -> list[Tool]:
        return [LookupCardsTool(), AppendMemoTool()]
# PluginBus 自动收集
```

### Step 5: 更新调用点

在 `bot.py` 中将直接调用改为 `fire_*` 调用：

```python
# 旧
await affection.update_on_reply(user_id, content)

# 新
await bus.fire_on_post_reply(reply_ctx)
```

### Step 6: 测试

```bash
uv run pytest tests/              # 确保零回归
uv run ruff check                  # lint
uv run pyright                    # type check
```

## 迁移期间的安全规则

1. **新旧代码可共存** — 不急于删除旧代码
2. **每次只迁移一个模块** — 不要一次迁移多个功能
3. **每个模块迁移后运行全部测试** — 确保零回归
4. **在 `bot.py` 中用功能开关控制** — 可随时回退到旧路径
5. **更新维护日志** — 记录每次迁移

## 服务迁移详细步骤（Phase 3）

以 CardStore 为例：

1. 将 `src/memory/card_store.py` 的核心类复制到 `services/memory/card_store.py`
2. 保持接口不变，只调整 import 路径
3. 在 `src/memory/card_store.py` 中保留薄兼容层：`from omubot.services.memory.card_store import CardStore`
4. 更新 `bot.py` 从新路径 import
5. 运行测试确认无回归
6. 重复下一个服务

## 插件迁移详细步骤（Phase 4-7）

以好感度为例：

1. 创建 `plugins/affection/plugin.py`
2. 实现 `AffectionPlugin`：在 `on_startup` 初始化引擎，用 `on_pre_prompt` 追加好感度信息，用 `on_post_reply` 更新数值
3. 将旧好感度文件移到插件的子模块：`plugins/affection/engine.py`
4. 在 `bot.py` 中注册插件替代旧调用
5. 确认 `register_tools()` 返回好感度相关工具
6. 运行测试确认无回归
7. 删除旧文件

## 回归检查清单

每个阶段完成后：

- [ ] `uv run pytest` 全部通过
- [ ] `uv run ruff check src/` 无错误
- [ ] `uv run pyright` 无新增类型错误
- [ ] Docker 构建成功
- [ ] 部署后 bot 正常启动（日志无异常）
- [ ] 群聊 @bot 正常回复
- [ ] 私聊正常回复
- [ ] 工具调用正常（记忆卡片、表情包等）
- [ ] 更新维护日志
