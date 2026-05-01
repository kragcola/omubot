# 10 — FAQ

## 基础

### Q: Omubot 和旧项目的关系？

Omubot 是 amadeus-in-shell 的重写版本，目标是解耦单体架构。Phase 1 的 kernel 包与旧代码零冲突，可以逐步迁移。

### Q: 为什么要分三层？

单体内核无法扩展——每次加功能都要改核心文件。三层分离后，新功能只需写一个插件类，通过钩子与系统交互。

### Q: 内核层为什么不能 import 项目内其他模块？

这是防止循环依赖和架构腐化的关键约束。内核只做调度，所有外部依赖通过 Context 注入。

## 插件开发

### Q: 我只需要一个简单的复读功能，怎么写？

```python
class EchoPlugin(AmadeusPlugin):
    name = "echo"
    async def on_message(self, ctx: MessageContext) -> bool:
        if "复读" in str(ctx.content):
            # 发送复读消息
            return True
        return False
```

### Q: 插件之间如何共享数据？

通过系统服务。例如两个插件都需要访问记忆卡片，它们都通过 `PluginContext.card_store` 获取引用。

### Q: 我的插件需要在多个钩子中做不同的事？

没问题。覆写你需要的那几个钩子即可，没覆写的钩子默认是空操作。

### Q: on_message 返回 True 后会发生什么？

消息被标记为"已消费"，PluginBus 停止调用后续插件的 on_message，调用方跳过默认的 thinker→LLM→reply 流程。

### Q: register_tools 何时被调用？

在 `fire_on_startup` 之后，由调用方手动调用 `collect_tools()`。

### Q: 如何设置优先级？

```python
class MyPlugin(AmadeusPlugin):
    priority = 50  # 默认 100，数字越小越先执行
```

参考：0=核心，1-9=基础设施，10-49=业务，200+=拦截器。

## 配置

### Q: 如何覆盖某个群的配置？

在 `config.toml` 中设置：

```toml
[group.overrides."123456789"]
at_only = true
debounce = 30.0
blocked_users = ["12345", "67890"]
```

### Q: 环境变量和 config.toml 哪个优先级高？

环境变量。合并顺序：TOML < 环境变量 < CLI 参数。

## 迁移

### Q: 迁移期间新旧代码能同时运行吗？

能。Phase 1 的 kernel 包是完全独立的，不依赖任何旧代码。后续 Phase 通过功能开关逐步切换。

### Q: 迁移后旧文件会删除吗？

会。Phase 8 统一清理。每个迁移阶段确认无回归后才删除旧文件。

### Q: 现有测试会受影响吗？

不会。Phase 1 新增 73 个测试，581 个已有测试零回归。

## 运维

### Q: 如何查看插件加载情况？

查看启动日志中 `channel=bus` 的记录：

```
INFO | plugin registered | name=chat priority=0
INFO | plugin registered | name=affection priority=10
INFO | all plugins started | count=5
```

### Q: 某个插件异常会导致 bot 崩溃吗？

不会。所有钩子调用都经过异常隔离——出错的插件只打 warning 日志，不影响其他插件。

### Q: 如何热重载一个插件？

当前不支持热重载。需要重启 bot。热重载在 Phase 8 规划中。

### Q: 如何调试单个插件的 prompt block 输出？

在 `on_pre_prompt` 中添加调试日志：

```python
async def on_pre_prompt(self, ctx: PromptContext) -> None:
    block_text = self.build_block()
    ctx.add_block(block_text, label="my_plugin", position="dynamic")
    logger.debug("my_plugin block | chars={}", len(block_text))
```
