# 09 — 术语表

| 术语 | 英文 | 定义 |
|------|------|------|
| 内核 | Kernel | PluginBus + 类型契约，零 I/O，框架 ABI |
| 系统服务 | System Service | 封装外部资源的可复用能力 |
| 插件 | Plugin | 继承 AmadeusPlugin 的功能模块 |
| 插件总线 | PluginBus | 内核调度器，管理插件注册和钩子调用 |
| 钩子 | Hook | 插件可覆写的方法，内核在特定时机调用 |
| 管线 | Pipeline | 消息从接收到回复的完整处理路径 |
| 消费 | Consume | on_message 返回 True，阻止后续处理 |
| Context | Context | 钩子的入参 dataclass，携带所有需要的信息 |
| PromptBlock | PromptBlock | 插件向 system prompt 注入的文本块 |
| position | position | PromptBlock 的缓存策略：static/stable/dynamic |
| 工具 | Tool | LLM 可调用的函数，继承 Tool ABC |
| ToolContext | ToolContext | 工具执行上下文 |
| Identity | Identity | Bot 人格模型（名字、性格、插话方式） |
| 插件发现 | Plugin Discovery | 扫描目录自动注册插件 |
| 异常隔离 | Error Isolation | 单插件异常不影响其他插件 |
| 优先级 | Priority | 控制同钩子中插件的执行顺序 |
| 短路 | Short-circuit | on_message 返回 True 后停止调用后续插件 |
| Thinker | Thinker | 轻量决策模块：reply/wait/search |
| Dream Agent | Dream Agent | 后台整合记忆、清理数据的周期性任务 |
| 记忆卡片 | Memo Card | 长期记忆单元，存储为 .md 文件 |
| 检索门控 | Retrieval Gate | 按需注入卡片的决策模块 |
| 状态面板 | State Board | 群聊活跃用户和话题摘要 |
| 表情包库 | Sticker Store | SHA256 去重的本地图片库 |
| 上下文压缩 | Context Compaction | 将旧消息压缩为摘要 |
| 提示缓存 | Prompt Caching | Anthropic API 的 prompt cache breakpoint 机制 |
| NapCat | NapCat | QQ 协议适配器 |
| NoneBot2 | NoneBot2 | Python 异步机器人框架 |
| OneBot V11 | OneBot V11 | QQ 机器人标准接口 |
| 群覆盖 | Group Override | 群级别的配置覆盖（at_only, debounce 等） |
| Debounce | Debounce | 群聊静默 N 秒后才触发的防抖机制 |
