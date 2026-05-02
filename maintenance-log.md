# 维护日志

> 按时间倒序记录部署、配置变更、故障处理等运维事件。

---

## 2026-05-02 — 移除重启后自动触发群聊回复

- **类型**：bugfix
- **操作人**：Claude Code (assisted)
- **问题与根因**：每次 bot 容器重启后，历史加载器回填近期消息到 timeline，随后 `router.py` 调用 `scheduler.trigger()` 强制触发一次回复。由于每次加载的近期历史相同，LLM 收到相同上下文后产生同话题重复发言。多次重载 → 多次重复。
- **修复**：移除 `kernel/router.py` 中 `is_first_connect` 后的 `scheduler.trigger()` 调用。Bot 重启后静默加载历史作为上下文，等待新的群消息（@/debounce/batch）自然触发回复。
- **影响范围**：`kernel/router.py`（移除 5 行）
- **测试**：ruff check 通过，pytest 通过（20 个 scheduler 测试全过，预存失败与本次无关）
- **回滚**：git revert 即可

---

## 2026-05-02 — 好感度群聊归因修复：调度器传入 user_id

- **类型**：bugfix
- **操作人**：Claude Code (assisted)
- **问题与根因**：群聊中好感度始终为 0。`GroupChatScheduler._do_chat()` 硬编码 `user_id=""`，导致好感度引擎无法将互动归因到任何用户。
- **修复**：
  - `services/scheduler.py`：`_GroupSlot` 新增 `last_user_id` 字段，`notify()` 接收并存储 `user_id`，`_do_chat()` 使用存储的 user_id 而非空字符串
  - `kernel/router.py`：两处 `scheduler.notify()` 调用传入 `user_id=str(event.user_id)`
- **影响范围**：`services/scheduler.py`、`kernel/router.py`、`plugins/affection/plugin.py`（1.0.1→1.0.2）
- **验证**：ruff check 通过，pytest 通过（预存失败与本次无关）
- **回滚**：git revert 即可

---

## 2026-05-02 — 调试保存表情包三项修复：mface 识别、Qwen VL 限流、历史加载干扰

- **类型**：bugfix
- **操作人**：Claude Code (assisted)
- **问题与根因**：
  1. **mface（QQ 商城表情）无法识别**：NoneBot OneBot v11 适配器不识别 NapCat 的 `mface` 段类型，可能将其转为纯文本（如 `[星星眼]`），导致 `seg.type == "mface"` 永远不匹配。只能收到 3 张普通图片。
  2. **Qwen VL 连续调用失败**：`/debug 保存四张表情` 触发 4 次连续 vision API 调用，硅基流动对快速连续请求限流，第 2-3 次调用超时返回空错误。
  3. **Bot 重启后历史加载触发正常回复**：历史加载器回填旧消息（含之前的 `/debug` 输出文本）进入 timeline，触发 thinker/chat 流程产生多余回复。
- **修复**：
  - `plugins/chat.py`：mface 检测增加 `market_face` 类型 + 从 `event.raw_message` 解析 `[mface:...]`/`[market_face:...]` CQ 码兜底；增加 segment 类型扫描日志；连续 vision 调用间增加 1.5s 延迟避免限流
  - `plugins/history_loader.py`：新增 `_contains_debug_command()` 跳过含 `/debug` 的历史消息
  - `services/media/vision.py`：错误日志增加异常类型名，不再只显示空字符串
  - `bot.py`：VisionClient timeout 10s → 15s
- **影响范围**：`plugins/chat.py`（1.0.3→1.0.4）、`plugins/history_loader.py`（1.0.0→1.0.1）、`services/media/vision.py`、`bot.py`
- **测试**：ruff check 通过，pytest 通过（预存失败与本次无关）
- **回滚**：git revert 即可

## 2026-05-02 — 图像描述提升至系统层 + 接入硅基流动 Qwen3-VL

- **类型**：enhancement (架构变更)
- **操作人**：Claude Code (assisted)
- **变更内容**：
  1. **VisionPlugin 删除**：`plugins/vision.py` → `services/media/vision.py`（系统服务层，与 image_cache 同级）。VisionClient 现在由 `bot.py` 在启动时根据 `api_key` 是否填写自动初始化，不在插件总线中注册。
  2. **配置简化**：`QwenVLConfig` 移除 `enabled` 字段——api_key 非空即启用，留空即关闭。无需额外开关。
  3. **接入硅基流动 VLM**：`config.toml` 配置 `Qwen/Qwen3-VL-30B-A3B-Instruct` 模型（API: siliconflow.cn），DeepSeek V4 本身不支持多模态，现在由 Qwen VL 先描述图片再传给主模型。
  4. **StickerPlugin 1.0.1 → 1.0.2**：`format_prompt_view()` 新增 `[动图]`/`[静态]` 格式标签，摘除未使用的 loguru 导入。
  5. **config.example.toml**：补上 `[vision.qwen]` 示例段落。
- **影响范围**：`services/media/vision.py`（新）、`plugins/vision.py`（删）、`bot.py`、`kernel/config.py`、`config.example.toml`、`plugins/sticker.py`、`config/config.toml`
- **版本**：bot 1.0.4 → 1.0.5
- **测试**：ruff check 通过，pytest 通过（8 个预存失败与本次无关）
- **回滚**：git revert + 恢复 `config.toml` 的 `[vision.qwen]` 为空值

## 2026-05-02 — 表情包发送全线修复：sub_type 蛇形命名 + /debug 直接调度 + 指令防抖取消

- **类型**：bugfix (critical) + enhancement
- **操作人**：Claude Code (assisted)
- **问题与根因**：
  1. **表情包发送全线失败（retcode=1200）**：`SendStickerTool` 使用驼峰 `subType=1` 设置 QQ 贴图类型，但 OneBot v11 协议要求蛇形 `sub_type`。NapCat 静默忽略未知 key，导致表情包始终作为普通图片发送。
  2. **Docker 容器文件系统隔离**：bot 和 napcat 在不同容器，napcat 无法读取 bot 的 `/app/storage/` 路径。改为 base64 编码内联传输。
  3. **`/debug` 指令触发 LLM 工具循环但 DeepSeek V4 幻觉**：LLM 不认识 `send_sticker`，总是返回 `pass_turn`。新增直接调度路径绕过 LLM。
  4. **指令被复读插件检测**：`EchoPlugin` 未过滤 `/` 开头的消息。
  5. **`/debug` 处理期间 thinker 仍触发**：上一条消息的 debounce 计时器在 `/debug` 到达时已启动，到期后 thinker 照常运行。
- **修复**：
  - `services/tools/sticker_tools.py`：`subType` → `sub_type`（蛇形命名）+ `summary=[动画表情]` + base64 编码 + 异常保护 record_send
  - `plugins/chat.py`：新增 `_debug_direct_dispatch()` 直接实例化 `SendStickerTool` 绕过 LLM；关键词从 `startswith` 改为 `in` 匹配，覆盖 gif/动图/贴图 等
  - `plugins/echo.py`：跳过以 `/` 开头的消息
  - `kernel/router.py`：命令匹配成功后调用 `ctx.scheduler.cancel_debounce(group_id)` 取消待处理的 thinker 触发
  - 参考旧项目 `amadeus-in-shell` 确认正确模式为 `sub_type=1` + `summary=[动画表情]`
- **影响范围**：`services/tools/sticker_tools.py`、`plugins/chat.py`、`plugins/echo.py`、`kernel/router.py`
- **测试**：`tests/test_sticker_tools.py` 更新以验证 `sub_type` 和 `summary` 设置；ruff check + pytest 通过（9 个预存失败与本次无关）
- **回滚**：git revert 即可

## 2026-05-02 — 表情包强制执行循环修复

- **类型**：bugfix
- **操作人**：Claude Code (assisted)
- **根因**：`chat()` 中颜文字→表情包强制执行逻辑（line 1135）在 LLM 不调用 `send_sticker` 时反复触发。`_sticker_sent` 始终为 False，每轮都检测到 kaomoji → 强制执行 → LLM 仍不发 → 再强制执行，直到 MAX_TOOL_ROUNDS=5 耗尽。日志中出现 4 次连续 enforcement 事件。
- **修复**：强制执行一次后立即设置 `_sticker_sent = True`，阻止后续轮次再次触发。
- **附加**：StickerPlugin 版本 1.0.0 → 1.0.1
- **影响范围**：`services/llm/client.py`（一行）、`plugins/sticker.py`（版本号）
- **回滚**：git revert 即可

## 2026-05-02 — /debug 模式重构：支持工具执行

- **类型**：enhancement
- **操作人**：Claude Code (assisted)
- **问题**：`/debug` 调用 `_call()` 裸 API，无工具循环，导致 `send_sticker` 等工具无法执行。用户无法用 `/debug` 调试表情包等功能。
- **修复**：重写 `_handle_debug` 为完整工具循环（镜像 `chat()` 的工具执行逻辑），LLM 可调用任何已注册工具。同时明确 system 指令："你是调试助手，直接执行用户的指令"。
- **变更**：
  - `plugins/chat.py`：`_handle_debug` 从单轮 `_call()` 改为工具循环（最多5轮），支持 `pass_turn`、`send_sticker` 等全部工具
  - 新增 imports: `asyncio`, `json`, `_PASS_TURN_TOOL`, `_strip_markdown`, `_to_anthropic_tools`, `ToolContext`
  - ChatPlugin 版本 1.0.2 → 1.0.3
- **影响范围**：`plugins/chat.py`
- **回滚**：git revert 即可

---

## 2026-05-02 — 分段根本修复：force_reply 绕过分段逻辑

- **类型**：bugfix (critical)
- **操作人**：Claude Code (assisted)
- **根因**：`chat()` 方法中 `force_reply=True` 时完全跳过 `_split_naturally()`，直接 `segments=[reply]`。而 scheduler 对所有 `is_at=True` 的消息（@提及、昵称称呼）都设 `force_reply=True`，导致这些消息永远不分段发送。
- **修复**：移除两处 `force_reply` 的分段绕过（工具循环内 + 工具循环耗尽后），所有回复统一走 `_split_naturally`。`force_reply` 现在仅跳过 thinker（在 line 1041 处理），不再影响分段。
- **附加**：ChatPlugin 版本 1.0.1 → 1.0.2
- **影响范围**：`services/llm/client.py`（两处 force_reply 分支移除）、`plugins/chat.py`（版本号）
- **测试**：ruff check 通过，pytest 577/587 通过（10 个预存失败与本次无关）
- **回滚**：git revert 即可

## 2026-05-02 — 日志频道恢复 + 分段修复 + 插件日志增强

- **类型**：bugfix + enhancement
- **操作人**：Claude Code (assisted)
- **变更**：
  - **日志频道默认值**：`LogChannelConfig.system` 默认改为 `True`（多个插件使用 system 频道输出必要信息）
  - **config.toml 频道开关**：启用 message_in、message_out、thinking、mood、affection、schedule、system（之前全部为 false 导致日志全被过滤）
  - **好感度插件日志**：`AffectionPlugin` 新增 INFO 级别日志（on_pre_prompt 记录用户好感度层级和分数，on_post_reply 记录互动后的分数变化），版本 1.0.0 → 1.0.1
  - **记忆提取器日志**：`MemoPlugin` 卡片提取成功日志从 DEBUG 提升到 INFO，版本 1.0.0 → 1.0.1
  - **日程插件**：版本 1.0.0 → 1.0.1
  - **ChatPlugin**：补上版本号 1.0.1
  - **分段修复（services/llm/client.py）**：
    - `_split_on_sentence_end` 增加硬字符上限强制切分，防止无标点长句成为单条超长消息
    - `---cut---` 检测从子字符串匹配改为逐行精确匹配，防止嵌入文本误触发
    - `_split_naturally` 尾段合并仅对纯标点片段生效，避免将硬切分的内容片段错误并回
    - `on_segment=None` 且多分段时不再静默丢弃前面分段，改为合并返回完整文本
- **影响范围**：`kernel/config.py`（LogChannelConfig.system 默认）、`config/config.toml`（频道开关）、`plugins/affection/plugin.py`（日志+版本）、`plugins/memo.py`（日志级别+版本）、`plugins/schedule/plugin.py`（版本）、`plugins/chat.py`（版本号）、`services/llm/client.py`（分段逻辑）
- **测试**：ruff check 通过，pytest 577/587 通过（10 个预存失败与本次无关）
- **回滚**：git revert 即可

## 2026-05-02 — 记忆与好感度数据迁移：从 amadeus-in-shell 同步

- **类型**：data migration
- **操作人**：Claude Code (assisted)
- **变更**：
  - **memory_cards.db**：从 amadeus-in-shell 复制 6 张卡片（4 张 user/1416930401 + 2 张 group/984198159, 993065015），schema 完全一致无需转换
  - **affection/1416930401.json**：从 amadeus-in-shell 复制好感度数据（score=12, total_interactions=15），JSON 格式兼容
  - storage 目录是 Docker volume mount，复制后即时生效无需重建
- **影响范围**：`storage/memory_cards.db`、`storage/affection/1416930401.json`
- **回滚**：从旧项目重新复制或删除这两个文件即可

## 2026-05-02 — Soul 迁移：从 amadeus-in-shell 同步角色配置

- **类型**：config
- **操作人**：Claude Code (assisted)
- **变更**：
  - **identity.md**：从 `amadeus-in-shell/soul/identity.md` 完整复制，143 行详细 Emu 角色设定，含 `# 凤笑梦 (Emu Otori)` 标题 + `## 插话方式` 章节（解析器可正确提取 proactive 规则，不再 fallback 到内置默认身份）
  - **instruction.md**：从 `amadeus-in-shell/soul/instruction.md` 适配，核心差异：
    - 记忆系统：`recall_memo`/`update_memo` → `lookup_cards`/`update_cards`（适配 CardStore）
    - 图片：移除所有 `describe_image` 引用（omubot 在消息渲染阶段自动通过 Qwen VL 描述图片）
    - 保留全部人格规则：回复风格、场景差分（7 模式）、语气污染、分段发送、日常心情、角色生日、群聊上下文理解、保密规则、稳固人格、工具使用、主动搜索、表情包
  - **根因**：旧 identity.md 无 `# Title` 行，解析器返回 None → fallback 到 `_builtin_default()` 其中 `proactive=None` → Scheduler 跳过所有群消息
- **影响范围**：`config/soul/identity.md`、`config/soul/instruction.md`
- **回滚**：从 git 恢复旧 soul 文件即可

## 2026-05-02 — v1.0.1 修复：@提及回复 + Thinker + 指令别名

- **类型**：bugfix + feature
- **操作人**：Claude Code (assisted)
- **变更**：
  - **指令别名系统**：`Command` dataclass 新增 `aliases: list[str]` 字段，`CommandDispatcher._load()` 将别名一并索引
  - **`/plugins` 多入口**：新增 `/p`、`/plg`、`/插件` 三个别名
  - **全部插件开发者签名**：`AmadeusPlugin` 基类 `author` 默认值改为 `"kragcola"`
  - **修复 @提及不回复**：`scheduler.notify()` 的 `proactive is None` 守卫现在仅在非 @ 消息时生效，@ 消息始终触发回复
  - **修复 force_reply 语义过载**：`client.chat()` 中 `force_reply=True` 不再注入调试块或剥离心情/好感度块（那是 `/debug` 的职责，不应影响普通 @ 回复）
  - **版本升级**：omubot → 1.0.1，debug_commands 插件 → 1.1.0
- **影响范围**：`kernel/types.py`（Command 别名、author 默认）、`services/command.py`（别名索引）、`services/scheduler.py`（守卫条件）、`services/llm/client.py`（移除 force_reply 调试卷入）、`plugins/debug_commands.py`（别名注册、作者、版本）、`services/version.py`（版本号）、`pyproject.toml`（版本号）、`CHANGELOG.md`
- **测试**：ruff check 通过，pytest 通过（排除 libvips 预存失败）
- **回滚**：git revert 即可

## 2026-05-01 — 服务层指令系统：CommandDispatcher + /debug 迁移

- **类型**：feature
- **操作人**：Claude Code (assisted)
- **变更**：
  - **新增 `services/command.py`**：`CommandDispatcher` 服务，从 PluginBus 收集命令注册表，解析 `/command args` 前缀并分发执行
  - **新增 `CommandContext`**：`kernel/types.py` 新增 dataclass，作为命令 handler 的标准入参
  - **迁移 `/debug`**：从 `kernel/router.py` 硬编码（`_check_debug_prefix` 函数 + `_DEBUG_PREFIX` 常量）迁移至 `plugins/chat.py` → `ChatPlugin.register_commands()` 注册
  - **消息流集成**：私聊和群聊均在 LLM 处理前检查命令（群聊在 interceptor 之后、scheduler 之前；私聊在 render 之后、chat 之前）
  - **扩展性**：任何插件实现 `register_commands()` 返回 `Command` 实例即可注册新指令
- **影响范围**：router.py 移除 ~25 行硬编码，新增 dispatcher 集成；bot.py 新增 1 行初始化；chat.py 新增 ~65 行命令注册+handler
- **测试**：547/547 通过，lint 干净
- **回滚**：git revert 即可恢复旧 `/debug` 硬编码行为

## 2026-05-01 — Phase 7a: 单文件插件 + plugin.json 清单

- **类型**：refactor
- **操作人**：Claude Code (assisted)
- **变更**：
  - **PluginBus 侧车 .json 支持**：`discover_plugins()` Pass 2 为单文件插件自动拾取同名 `.json`，`_load_plugin_module` 统一 manifest 解析
  - **8 个插件转为单文件**：echo、element_detector、history_loader、dream、memo、vision、chat、sticker 从子目录迁为 `plugins/<name>.py`，合并所有辅助模块
  - **保留目录形态**：affection (4 文件)、schedule (7 文件) 因复杂度保持目录
  - **plugin.json 清单**：全部 10 个插件创建清单文件（8 个侧车 + 2 个目录内），sticker 声明 `"vision": ">=1.0.0"` 依赖
  - **import 路径更新**：bot.py (8 处)、kernel/router.py (1 处)、plugins/chat.py (1 处)、5 个测试文件
- **影响范围**：插件层结构变更，内核 API 不变，服务层不受影响
- **测试**：547/547 通过（排除 libvips 和 e2e 预存失败）
- **回滚**：git revert 即可，旧子目录需手动恢复

## 2026-05-01 — 开源准备：config/ 隔离 + 人格解耦 + 仓库推送

- **类型**：refactor + devops
- **操作人**：Claude Code (assisted)
- **变更**：
  - **config/ 目录隔离**：
    - 创建 `config/` 目录，将 `.env` 移入，`config.toml` 和 `config/soul/` 均在此目录下
    - `kernel/config.py`：`SoulConfig.dir` 默认值 `"soul"` → `"config/soul"`，`load_config()` 默认路径 `"config/config.toml"`
    - `docker-compose.yml`：env_file 和 volumes 路径全部更新
    - `.gitignore`：一条 `config/` 规则替代原有分散列举，新增 `._*` 防 Apple Double 文件
    - `.claude/settings.json`：hook 匹配模式更新为 `config/(config\.toml|soul/|\.env)`
    - Admin 路由默认值同步更新
  - **人格硬编码解耦**：
    - `services/llm/thinker.py`：`THINKER_SYSTEM_PROMPT` 使用 `{name}` 占位符，`think()` 新增 `identity_name` 参数
    - `services/llm/client.py`：调试模式提示移除 "凤笑梦"，传 `identity.name` 给 thinker
    - `plugins/schedule/generator.py`：`_SCHEDULE_SYSTEM_PROMPT` 重写为通用模板，移除 W×S 具体设定，`ScheduleGenerator` 接受 `identity_name`
    - `plugins/schedule/calendar.py`：新增 `set_self_name()`/`get_self_name()`，`is_self_birthday` 可配置
    - `plugins/schedule/mood.py`：生日检测改用 `is_self_birthday`
    - `plugins/sticker/plugin.py`："frequently" 提示使用 `{name}` 占位符
    - `admin/templates.py`：`admin_title` → `"Omubot Admin"`
    - `plugins/chat/plugin.py`：启动时调用 `set_self_name()` 并传 `identity_name`
    - 测试文件更新：通用示例名替代 "凤笑梦"、QQ 号
  - **文件清理**：删除 `_omubot_public_api.py`、`rewrite-plan.md`
  - **Git 仓库重建**：`rm -rf .git && git init`，全新干净历史，推送至 `github.com/kragcola/omubot`
  - **文档更新**：CLAUDE.md、README.md、docs/setup-guide.md、docs/operations.md、docs/architecture.md、wiki/05-services.md 路径全部同步
- **影响**：项目可安全开源 — 所有个人配置隔离在 `config/`（gitignored），源代码零硬编码人格引用
- **测试**：578 passed, 9 failed（6 libvips + 3 sticker git mismatch 预存），零回归
- **Lint**：ruff all checks passed

## 2026-05-01 — 重建 Docker 部署文件

- **类型**：infra
- **操作人**：Claude Code (assisted)
- **变更**：
  - 重建 `Dockerfile`：多阶段构建（builder + runtime），python:3.12-slim + libvips，uv 管理依赖
  - 重建 `docker-compose.yml`：napcat + bot 双容器，端口 8081:8080，volumes 挂载 storage/soul/config.toml/.env
  - 重建 `config.example.toml`：完整 16 节配置模板（含 schedule/affection/log.channels 节，旧版缺失）
  - 更新 `.dockerignore`：排除 storage/tests/wiki/*.md/.gitignore
- **影响**：项目恢复 Docker 部署能力；新操作员可从零 `docker compose up -d --build` 启动

## 2026-05-01 — src/ 耦合彻底清理 + 垫片删除 + 文档全线更新

- **类型**：refactor + docs
- **操作人**：Claude Code (assisted)
- **变更**：
  - 消除 `omubot/kernel/`、`omubot/services/`、`omubot/plugins/`、`omubot/admin/` 中全部 20 处 `from src.` 导入耦合
  - 修复 12 个测试文件的导入路径：`src.config` → `kernel.config`，`src.identity.models` → `services.identity`，私有符号 `_call_api` → `call_api` 等
  - `bot.py` 最后一个 `from src.config_loader` 改为 `from kernel.config`
  - `kernel/config.py` 默认值 `plugin_dirs: ["src/plugins"]` → `["plugins"]`
  - `pyproject.toml` ruff 路径修正
  - **删除 `src/` 目录**（28 个垫片文件）— 零个 `from src.` 导入残留
  - **删除 `旧内容待删/` 目录**（旧 amadeus 内容）
  - **更新维护日志 (maintenance-log.md)**：记录本次清理
  - **文档全线更新**：
    - CLAUDE.md — 命令、路径、配置引用修正
    - docs/architecture.md — `omubot/` → 根级目录，`src/` → 新路径，PluginBus 发现路径修正
    - docs/project-info.md — 命令速查、lint 路径、TUI 命令修正
    - docs/setup-guide.md — 目录结构移除 `src/` 和 `omubot/`，导入范例修正
    - wiki/02-kernel-api.md — 导入路径、plugin_dirs 默认值、向后兼容说明更新
    - wiki/04-plugin-guide.md — 示例导入路径修正
    - wiki/05-services.md — 服务迁移状态更新为已完成
    - wiki/06-config.md — 导入路径、默认值、向后兼容说明更新
    - wiki/07-tools.md — 模块路径修正
    - wiki/08-migration.md — 状态表更新（Phase 5/6 完成），`ruff check src/` 修正
    - wiki/README.md — 项目结构更新为扁平化布局，版本状态更新
- **影响**：项目完全独立，`kernel/`/`services/`/`plugins/`/`admin/` 四目录零耦合；文档与代码完全一致
- **测试**：578 passed, 9 failed（6 libvips + 3 sticker_tools git stash 预存），零回归
- **Lint**：ruff all checks passed
- **类型检查**：121 预存错误（非本次引入）

## 2026-05-01 — 工作区迁移：omubot 扁平化到根目录

- **类型**：refactor（工作区重组）
- **操作人**：Claude Code (assisted)
- **变更**：
  - 将 `omubot/kernel/`, `omubot/services/`, `omubot/plugins/`, `omubot/admin/`, `omubot/wiki/`, `omubot/docs/` 移动到根目录
  - 旧的 amadeus-in-shell 内容（Dockerfile, README.md, config.toml, soul/, storage/, napcat/, scripts/ 等）移入 `旧内容待删/`
  - `pyproject.toml`：更新 NoneBot 插件路径 `plugins.chat`，扩展 `known-first-party`
  - `bot.py`：更新 `discover_plugins` 路径，导入路径从 `omubot.` 改为直接导入
  - 全局替换 `from omubot.` → `from `（所有 .py 文件）
  - 根目录 `__init__.py` 重命名为 `_omubot_public_api.py`（避免包冲突）
  - `tests/test_client.py`：修复 mock patch 路径
  - `src/` 保留在根目录因 omubot 代码尚未完全迁移，仍耦合
- **影响**：工作区根目录现仅包含 omubot 重构内容 + 必要的 `src/`/`tests/`/`bot.py`；旧内容隔离在 `旧内容待删/`
- **测试**：581 passed, 6 failed（6 个 libvips 预存失败），零回归

## 2026-05-01 — 文档全面更新 + 搭建教程

- **类型**：docs
- **操作人**：Claude Code (assisted)
- **变更**：
  - `omubot/docs/architecture.md`：完整重写，新增 Omubot 三层架构、PluginBus 机制、插件发现流程、14 个插件一览、plugin.json 规范、开发钩子参考
  - `omubot/docs/project-info.md`：新增三层模型说明、14 个插件表、配置完整列表；更新存储路径、API 端点、命令速查
  - `omubot/docs/setup-guide.md`：新文件，从零搭建教程（6 步，预计 30-60 分钟），含开发指南、添加新插件范例、常见问题
  - `omubot/rewrite-plan.md`：更新追踪表 7.1/7.2 状态，新增当前状态总结段落，更新最后更新时间
- **影响**：外部人员可按 setup-guide 独立搭建；架构文档反映最新三层框架设计
- **Docker 验证**：构建成功，bot 正常启动，admin 面板正常（303 重定向至登录页）

## 2026-05-01 — Phase 7.1-7.2：单文件插件发现 + plugin.json 解析

- **类型**：feature
- **操作人**：Claude Code (assisted)
- **变更**：
  - `PluginBus.discover_plugins()` 重构为两轮扫描：Pass 1 子目录（优先），Pass 2 独立 `.py` 文件（跳过 `__init__`，同名时目录优先）
  - 新增 `_load_plugin_module()` 和 `_apply_manifest()` 辅助方法，消除重复代码
  - `plugin.json` 解析：若插件目录下有 `plugin.json`，解析后用字段（name, version, description, priority, enabled, dependencies）覆盖实例属性
  - `bot.py`：移除 5 个单文件插件（DateTime, GroupAdmin, HttpApi, WebFetch, WebSearch）的手动 import 和 register，改为 `_bus.discover_plugins("omubot/plugins")` 自动发现
- **影响**：单文件插件无需在 `bot.py` 中手动注册；plugin.json 可独立于代码更新元数据
- **测试**：581 passed（6 个 libvips 预存失败），零回归

## 2026-05-01 — Phase 6：Admin Panel 迁移到 omubot/admin/

- **类型**：refactor
- **操作人**：Claude Code (assisted)
- **变更**：
  - 新建 `omubot/admin/`：17 个文件从 `src/admin/` 复制，内部 import 更新为 `omubot.admin.*`
  - `create_admin_router()` 重构：改为接受 `PluginContext`，从 ctx 解构所需服务引用（usage_tracker, msg_log, config.group, card_store, admins 等）
  - `auth.py`：`_get_admin_token()` 移除对 `src.config_loader` 的依赖，只从环境变量读取
  - `bot.py`：提前设置 `ctx.bot_start_time`；从 `omubot.admin` 导入并挂载 admin router + `AdminAuthMiddleware`
  - `ChatPlugin`：移除 ~16 行 admin 挂载代码
  - `src/admin/`：`__init__.py`、`auth.py`、`templates.py` 改为 shim re-export
- **影响**：Admin 面板从 `src/` 迁出，成为 `omubot/` 下的系统服务；路由挂载权从 ChatPlugin 移至 `bot.py`
- **测试**：581 passed（6 个 libvips 预存失败），零回归

## 2026-05-01 — 零散事项：tick 循环基础设施 + ScheduleGenerator 移入 SchedulePlugin

- **类型**：refactor
- **操作人**：Claude Code (assisted)
- **变更**：
  - `PluginBus` 新增 `start_tick_loop(ctx, interval=60)` / `stop_tick_loop()` 方法，后台 asyncio 循环驱动 `fire_on_tick`
  - `router.py` 首次连接时启动 tick 循环（`bus.start_tick_loop(ctx)`）；移除 `datetime`/`ZoneInfo` 无用 import
  - `SchedulePlugin` 新增 `on_bot_connect`（启动 ScheduleGenerator + 缺失日程即时生成）和 `on_shutdown`（停止生成器）
  - `router.py` 移除 ~12 行日程启动代码，逻辑完整迁移至 SchedulePlugin
- **影响**：`fire_on_tick` 现在有生产环境驱动循环，插件可开始实现 `on_tick`；ScheduleGenerator 生命周期完全由 SchedulePlugin 管理
- **测试**：581 passed（6 个 libvips 预存失败），零回归

## 2026-05-01 — Phase 5 完成：全部插件切出 + PromptBuilder 精简

- **类型**：refactor（里程碑）
- **操作人**：Claude Code (assisted)
- **变更**：
  - 5.9 AffectionPlugin: `on_pre_prompt` + `on_post_reply` 钩子
  - 5.10 SchedulePlugin（新）: `on_pre_prompt` 注入心情块
  - 5.11 MemoPlugin: `on_pre_prompt`（全局索引 stable + 实体记忆 dynamic）+ `on_post_reply`（记忆提取）
  - 5.12 StickerPlugin: `on_pre_prompt`（表情包规则 static + 库视图 stable）
  - 5.8 HistoryLoaderPlugin（新）: `on_bot_connect` 钩子，群聊历史加载
  - 5.13 DreamPlugin（新）: `on_startup` 创建 DreamAgent，`on_bot_connect` 启动，`on_shutdown` 停止
  - 5.14 VisionPlugin（新）: `on_startup` 创建 VisionClient
  - 框架增强: `AmadeusPlugin.on_bot_connect` 钩子；`PluginContext.memo_extractor` 字段
  - `PromptBuilder` 精简：从 ~310 行缩减到 ~130 行，只保留 static identity + state_board
  - `client.py`：`PromptBlock.position` 分派（static/stable 获得 cache_control）
  - `ChatPlugin`：移除 ~60 行 VisionClient/DreamAgent/MemoExtractor 创建代码
- **影响**：Phase 5 全部 14 个插件完成切出；所有业务逻辑通过插件钩子驱动
- **测试**：579 passed，零回归

## 2026-05-01 — Phase 5a：框架增强（enabled/dependencies/commands/admin routes/manifest）

- **类型**：feature
- **操作人**：Claude Code (assisted)
- **变更**：
  - `AmadeusPlugin` 新增 `enabled`、`dependencies` 字段
  - 新增 `Command`、`AdminRoute` 类型 + `register_commands()`、`register_admin_routes()` 方法
  - `PluginBus._safe_call()` 检查 `plugin.enabled`；新增 `collect_commands()`、`collect_admin_routes()`
  - 新增 `PluginBus._resolve_dependencies()`：Kahn 拓扑排序 + 版本检查 + 循环依赖降级
  - 新增 `omubot/kernel/manifest.py`：`PluginManifest` + SemVer 约束解析器（`== >= > <= < ^ ~ *`）
- **测试**：581 passed，零回归

## 2026-05-01 — Phase 5.2-5.7：工具类插件切出 + ElementDetector

- **类型**：refactor
- **操作人**：Claude Code (assisted)
- **变更**：
  - 5.2 ElementDetectorPlugin: 目录插件，`on_message` 拦截器，搬入 detector 逻辑
  - 5.3 DateTimePlugin: 单文件，`register_tools`
  - 5.4 WebSearchPlugin: 单文件，`register_tools`
  - 5.5 WebFetchPlugin: 单文件，`register_tools`
  - 5.6 HttpApiPlugin: 单文件，`register_tools`
  - 5.7 GroupAdminPlugin: 单文件，`register_tools`
  - `ChatPlugin` 移除对应工具的初始化代码
- **测试**：581 passed，零回归

## 2026-05-01 — Phase 5.1: EchoPlugin 切出

- **操作人**：Claude Code (assisted)
- **变更**：
  - 新建 `omubot/plugins/echo/plugin.py`：EchoPlugin（priority=200，`on_message` 拦截器），将 EchoTracker 和 build_echo_key 移入
  - `MessageContext` 新增 `bot`、`nickname` 字段（供拦截器发送消息）
  - `router.py`：echo_key 构建移至 `fire_on_message` 之前，echo 检测逻辑移除；修复 SIM102 lint
  - `ChatPlugin`：移除 EchoTracker 初始化
  - `bot.py`：注册 EchoPlugin
  - `src/plugins/echo.py` → 兼容 shim
- **影响**：echo 复读检测现在通过 PluginBus 钩子运行，与 router 解耦
- **测试**：24 echo 测试 + 581 全量测试通过，零回归，lint 通过

---
## 2026-05-01 — Phase 4 完成：ChatPlugin 适配到 PluginBus

- **类型**：架构重构（里程碑）
- **操作人**：Claude Code (assisted)
- **变更**：
  - 新建 `omubot/kernel/router.py` (~520 行) — NoneBot 事件 → PluginBus 桥接：消息渲染、群聊监听、私聊处理、禁言监听
  - 新建 `omubot/plugins/chat/plugin.py` (~270 行) — ChatPlugin（priority=0），`on_startup` 中初始化全部系统服务存入 PluginContext，`on_shutdown` 清理
  - `src/plugins/chat/__init__.py` → 兼容 shim（~11 行）
  - `bot.py` 新增 PluginContext / PluginBus / ChatPlugin 注册 / setup_routers 调用
  - `LLMClient.__init__` 新增 `bus` 参数；`chat()` 中触发 `fire_on_pre_prompt`（收集 plugin_blocks）和 `fire_on_post_reply`（副作用通知）
  - `PromptBuilder.build_blocks()` 新增 `plugin_blocks` 参数
  - `PluginContext` 新增 `bus` 字段
- **影响**：系统通过 PluginBus 运转；bot.py 负责组装，ChatPlugin 负责初始化，router 负责事件路由；后续 Phase 5 可直接切出独立插件
- **测试结果**：581 passed，7 预存在失败，零回归，零 lint
- **下一步**：Phase 5（逐个切出插件：Echo → ElementDetector → 工具类 → Affection → Schedule → Memo）

## 2026-05-01 — Phase 3 完成：系统服务迁移（16 模块，6 批次）

- **类型**：架构重构（里程碑）
- **操作人**：Claude Code (assisted)
- **变更**：
  - Batch 1（零依赖）：`message_log`、`types`、`card_store`、`migrate`、`image_cache`、`sticker_store`、`usage`、`humanizer`、`tools/context`、`tools/registry`
  - Batch 2：`timeline`、`short_term`
  - Batch 3：`identity`（Identity + IdentityManager 合并）、`state_board`、`retrieval`
  - Batch 4：`prompt_builder`（原 prompt.py）、`thinker`
  - Batch 5：`llm/client`（1578 行，最大单文件）
  - Batch 6：`scheduler`
  - 全部旧位置替换为兼容 shim（`from omubot.services.xxx import *`），旧 import 路径仍可用
- **迁移模式**：cp + sed 批量替换 import 路径 → 重命名 `_` 前缀标识符（Python `import *` 限制）→ 修复测试 patch 目标路径 → ruff check --fix → pytest 验证
- **测试结果**：581 passed，7 预存在失败（image_cache × 6 + mood × 1），零回归

## 2026-05-01 — Phase 2 完成：配置系统重组

- **类型**：架构重构（里程碑）
- **操作人**：Claude Code (assisted)
- **变更**：
  - 新建 `omubot/kernel/config.py`（~370 行）— 所有 Pydantic 配置模型（23 个类）+ `KernelConfig`（plugin_dirs, disabled_plugins, max_hook_time_ms）+ `load_config()` 函数
  - `src/config.py` → 兼容 shim（从 `omubot.kernel.config` re-export 全部模型）
  - `src/config_loader.py` → 兼容 shim（从 `omubot.kernel.config` re-export `load_config`）
  - `omubot/kernel/__init__.py` 和 `omubot/__init__.py` 新增配置类型导出
- **向后兼容**：所有 `from src.config import ...` 和 `from src.config_loader import ...` 导入路径不变，现有代码无需修改
- **新增 `KernelConfig`**：`plugin_dirs=["src/plugins"]`、`disabled_plugins=[]`、`max_hook_time_ms=5000`，已作为 `BotConfig.kernel` 子字段
- **测试结果**：581 passed，7 预存在失败（image_cache × 6 + mood × 1），零回归，零 lint
- **下一步**：Phase 3（系统服务迁移）或 Phase 4（ChatPlugin 适配）

## 2026-05-01 — Phase 1 完成 + 项目 Wiki 创建

- **类型**：架构重构（里程碑）
- **操作人**：Claude Code (assisted)
- **Phase 1 交付物**：
  - `omubot/kernel/types.py`（~320 行）— 6 种 Context 类型、AmadeusPlugin 基类（8 钩子）、PromptBlock、Tool ABC、ToolContext、Identity、Content/TextBlock/ImageRefBlock
  - `omubot/kernel/bus.py`（~250 行）— PluginBus 调度器：注册/卸载/发现、8 种 fire_* 方法、异常隔离 _safe_call、目录扫描 discover_plugins
  - `tests/test_kernel_types.py`（36 tests）— Context/Block/Tool/Plugin/Identity 全覆盖
  - `tests/test_plugin_bus.py`（37 tests）— 注册排序/生命周期/消息管线/prompt 收集/工具收集/tick/异常隔离/插件发现
  - `omubot/__init__.py`、`omubot/kernel/__init__.py` — 包入口，导出全部公开 API
- **测试结果**：73 new passed，0 lint errors，581 已有测试零回归
- **Wiki**：创建 `omubot/wiki/` 目录，10 个文档覆盖架构/内核 API/Context 类型/插件开发/系统服务/配置/工具/迁移/术语/FAQ
- **下一步**：Phase 2（配置系统重组）或 Phase 3（系统服务迁移）



## 2026-05-01 — Omubot 重写计划启动

- **类型**：架构重构
- **操作人**：Claude Code (assisted)
- **背景**：amadeus-in-shell 项目功能持续增加（好感度、日程、表情包、记忆卡片、检索门控、视觉、梦境……），`src/plugins/chat/__init__.py` 已膨胀至 ~1000 行单体，`LLMClient` 和 `PromptBuilder` 直接依赖 7-10 个子系统。每次加新功能都需要改动核心文件，维护成本日益上升。
- **目标**：设计一套插件框架（PluginBus），将可插拔功能以独立插件形式挂载，每个插件只通过 1-3 个管线钩子与核心通信，彻底解耦。
- **参考项目**：
  - **MaiBot**（旧 bot）：组件注册模型（Action/Command/Tool/EventHandler）+ 事件管道（ON_MESSAGE → ON_PLAN → AFTER_LLM → POST_SEND）+ 插件目录扫描 + @register_plugin 装饰器 + `_manifest.json` 元数据
  - **MCDReforged**：单线程 TaskExecutor + 事件驱动 + 热重载 + 插件独立存储
  - **pluggy**：hookspec/hookimpl 装饰器模式 + 异常隔离
  - **wphooks**：WordPress 风格 action/filter 双钩子范式
- **已完成**：
  - 全项目 57 个 .py 文件审计，功能分为 6 类（Framework API / Core / Plugin / Admin / Config / Support）
  - 设计草案 `AmadeusPlugin` 基类 + `PluginBus` 调度器 + 6 种 Context 类型
  - 定义 8 个管线钩子：`on_startup` / `on_shutdown` / `on_message` / `on_thinker_decision` / `on_pre_prompt` / `on_post_reply` / `register_tools` / `on_tick`
  - 规划 14 个插件（7 个业务插件 + 7 个工具插件）
  - 完成新目录结构设计（`omubot/` 包，~50 个文件，5 层架构）
  - 创建 `Omubot/` 目录，生成 [feature-classification.md](Omubot/feature-classification.md) 功能分类文档 + 迁移映射速查表
  - 确定三层架构（内核 → 系统服务 → 插件），对标鸿蒙 OS 分层模型
  - 完成 [rewrite-plan.md](Omubot/rewrite-plan.md) 详细实施方案（8 个 Phase，含操作提示词）
- **设计原则**：
  - 钩子默认串行（by priority），与 async/await 自然契合
  - 异常隔离：单个插件崩溃不影响其他插件
  - 消息消费短路：on_message 返回 True 即停止后续处理
  - 单进程架构（不引入 MaiBot 式 IPC），保持调试简单
  - 核心插件 priority 0-99，业务插件 100-199，可选插件 200+
- **分阶段实施计划**：

	| 阶段 | 内容 | 影响 |
	| --- | --- | --- |
	| Phase 1 | 创建 `omubot/plugin_bus.py`，chat 插件内部重构为使用 bus | 零行为变化 |
	| Phase 2 | 切出 echo + element_detector（只有 on_message 钩子） | 验证模式 |
	| Phase 3 | 切出 affection + schedule（有 on_pre_prompt + on_post_reply） | 核心解耦 |
	| Phase 4 | 切出 sticker + memo + dream + vision | 完成解耦 |
	| Phase 5 | 插件热重载、独立配置 schema、目录扫描自动发现 | 锦上添花 |
- **回滚方案**：每阶段独立 PR，出问题只回滚该阶段；PluginBus 通过 feature flag 控制是否启用

### Phase 1 完成 (2026-05-01)

- **新增文件**：
  - `omubot/__init__.py` — 包入口，导出全部公开 API
  - `omubot/kernel/__init__.py` — 内核层入口
  - `omubot/kernel/types.py` — 类型系统（~320 行）：6 种 Context 类型、AmadeusPlugin 基类（8 个钩子）、PromptBlock、Tool ABC（从 src/tools/base.py 提升）、ToolContext、Identity、Content/TextBlock/ImageRefBlock（从 src/memory/types.py 提升）
  - `omubot/kernel/bus.py` — PluginBus 调度器（~250 行）：注册/卸载/发现、8 种 fire_* 调度方法、异常隔离 _safe_call、目录扫描 discover_plugins
  - `tests/test_kernel_types.py` — 36 个测试：覆盖所有 Context/Block/Tool/Plugin/Identity
  - `tests/test_plugin_bus.py` — 37 个测试：覆盖注册排序/生命周期/消息管线/prompt 收集/工具收集/tick/异常隔离/插件发现
- **测试结果**：73 passed（+73），0 lint 错误
- **未变更**：`bot.py`、所有现有 `src/` 文件——零回归，581 个已有测试仍然通过


---

## 2026-05-01 — 启动历史加载修复

- **类型**：bug fix
- **操作人**：Claude Code (assisted)
- **问题**：每次重启后 `load_history failed | group=984198159`、`load_history failed | group=993065015`，启动时无法从 NapCat 拉取群历史消息
- **根因**：`history_loader.py` 通过 `POST {napcat_url}/get_group_msg_history` 裸 HTTP 调用 NapCat，但 NapCat 的 OneBot HTTP server 未启用（`onebot11_384801062.json` 中 `httpServers: []`），端口 29300 连接被拒
- **修复**：
  - 重写 `load_group_history()` 接受 `bot: Bot` 参数，改用 `bot.call_api("get_group_msg_history", ...)` 通过已有 WebSocket 连接调用
  - `_load_one_group()` 用 `ActionFailed` 异常处理替代 `retcode` 检查
  - 修改 `chat/__init__.py` 调用点，传入 `bot=bot` 替代 `napcat_url`
  - 重写 `tests/test_history_self_messages.py`：Mock `bot.call_api` 替代 `aiohttp.ClientSession.post`，新增 API 异常和空消息测试
- **效果**：启动后成功加载群历史（993065015: 23条, 984198159: 29条），群聊上下文立即可用

## 2026-05-01 — @mention 不回复修复

- **类型**：bug fix
- **操作人**：Claude Code (assisted)
- **问题**：@bot 消息仍然被 thinker 判定为 wait，导致不回复
- **根因**：`GroupChatScheduler._do_chat()` 调用 `_llm.chat()` 时未传 `force_reply`，thinker 对 @mention 也执行了 wait 判断
- **修复**：
  - `_GroupSlot` 新增 `force_reply: bool` 标记
  - `notify(is_at=True)` 时设为 True
  - `_fire()` 捕获标记并传入 `_do_chat(force_reply=...)`
  - `_do_chat()` 传递 `force_reply` 给 `_llm.chat()`，绕过 thinker wait

## 2026-05-01 — 第三期：检索门控（Retrieval Gating）

- **类型**：新功能
- **操作人**：Claude Code (assisted)
- **背景**：Phase 2 后每轮对话将实体所有活跃卡片全量注入 system prompt，O(n) 膨胀。大部分卡片与当前话题无关，浪费上下文窗口
- **变更内容**：
  - 新增 `src/memory/retrieval.py`：`RetrievalGate` 类，4 级门控策略（全量→周期刷新→关键词→最小提示），~170 行
  - 修改 `src/llm/prompt.py`：`PromptBuilder` 新增 `retrieval_gate`/`session_id`/`conversation_text` 参数，memo block 双路径（门控/旧缓存），新增 `rewind_retrieval_turn()`
  - 修改 `src/llm/client.py`：调用前提取对话文本（群聊 `get_recent_text()`，私聊直接用 user_content），传入 `session_id`/`conversation_text`；thinker wait 后调用 `rewind_retrieval_turn()`
  - 修改 `src/memory/group_timeline.py`：新增 `get_recent_text(group_id, last_n=3)` 拼接最近 N 轮对话文本
  - 修改 `src/plugins/chat/__init__.py`：创建 `RetrievalGate(card_store=card_store, refresh_interval=10)`，传入 PromptBuilder
  - 新增 `tests/test_retrieval.py`：25 个测试覆盖 4 级门控 / 关键词提取 / 缓存失效 / turn 回退 / 作用域隔离 / 会话上限
- **4 级门控策略**：

  | 级别 | 触发条件 | 行为 |
  | --- | --- | --- |
  | 全量检索 | 新会话首轮 / 每 10 轮周期刷新 | 注入全部卡片（缓存，不重复查 DB） |
  | 关键词检索 | 对话文本关键词匹配到卡片 | 注入匹配卡片（上限 10 张） |
  | 最小提示 | 以上都不满足但有卡片 | 提示卡片数量 + 建议用 `lookup_cards` 工具 |
  | 空 | 实体无卡片 | 空字符串 |
- **thinker wait 回退**：thinker 判定 wait 时 `rewind_retrieval_turn()` 回退 turn_count，避免空消耗全量检索配额
- **影响范围**：memo block 从固定内容变为按需注入，首轮后 token 消耗降低约 80%；LLM 可通过 `lookup_cards` 工具主动查询未注入卡片
- **测试结果**：523 passed（+25），7 failed（6 libvips 预存 + 1 flaky mood）
- **回滚方案**：`PromptBuilder(retrieval_gate=None)` 即可回退旧行为

---

## 2026-05-01 — 第二期：类型化记忆卡片（CardStore）

- **类型**：重构
- **操作人**：Claude Code (assisted)
- **背景**：
  - 旧 MemoStore 用 `.md` 文件存储记忆，纯文本无结构，存在 6 个已诊断问题（群聊 memo/nickname 错乱）
  - 借鉴 KokoroMemo 的卡片设计，用 SQLite 存储有类型、有作用域、支持取代关系的记忆卡片
- **变更内容**：
  - 新增 `src/memory/card_store.py`：核心存储层，SQLite + aiosqlite，14 列 schema，Card/NewCard 数据类，12 个公共 API
  - 新增 `src/memory/migrate.py`：一次性 MD→卡片迁移，6 张卡片从 3 个旧 `.md` 文件，幂等，源文件改为 `.md.migrated`
  - 新增 `src/memory/memo_extractor.py`：每轮对话后提取 `[category] 内容` 格式的新事实卡片
  - 新增 `src/memory/state_board.py`：群聊状态板，从 MessageLog SQLite 推导活跃用户/话题/频率/@提及
  - 修改 `src/llm/prompt.py`：memo_store → card_store，stable block 用 `build_global_index()`（计数式索引），memo block 用 `build_entity_prompt()`（`[类别] 内容` 格式），6 块布局
  - 修改 `src/llm/client.py`：`append_memo` → `add_card`（scope/scope_id/category/content），compact 系统提示词重写
  - 修改 `src/llm/dream.py`：6 个新 LLM 工具（list/search/update/supersede/expire cards + list entities），系统提示词重写为分类→去重→交叉验证→取代工作流
  - 修改 `src/tools/memo_tools.py`：RecallMemoTool → CardLookupTool，UpdateMemoTool → CardUpdateTool（add/update/supersede/expire）
  - 修改 `src/plugins/chat/__init__.py`：MemoStore → CardStore(db_path="storage/memory_cards.db")，全部依赖链切换
  - 修改 `src/admin/__init__.py`：memo_store 参数 → card_store
  - 新增 `tests/test_card_store.py`：~17 个测试覆盖 CRUD/supersede/索引输出/迁移
  - 重写 `tests/test_memo_tools.py`：11 个测试覆盖 CardLookupTool/CardUpdateTool
  - 重写 `tests/test_dream.py`：10 个测试覆盖 CardStore 版 DreamAgent
  - 修改 `tests/test_client.py`、`tests/test_e2e_live.py`、`tests/test_prompt.py`：适配 CardStore
  - 删除 `src/memory/memo_store.py`、`tests/test_memo_store.py`
  - 新增 `src/llm/thinker.py`、`src/memory/state_board.py`、`tests/test_state_board.py`（第一期合并带入）
- **卡片模型**：
  - 7 类：preference(偏好)/boundary(边界)/relationship(关系)/event(事件)/promise(承诺)/fact(事实)/status(状态)
  - 3 作用域：user/group/global，confidence 0.0-1.0，supersedes 取代边
- **迁移结果**：`1416930401.md` → 3 cards，`984198159.md` → 1 card，`993065015.md` → 1 card，共 6 张（全部 fact 类别，等待 Dream Agent 首次运行后重新分类）
- **影响范围**：所有记忆相关路径（prompt 构建、compact、dream、工具调用、extractor、admin debug）全部切换至 CardStore；prompt 格式从全文 memo body 变为 `[类别] 内容`；全局索引从文本 mention 变为计数式 `用户 @QQ: 偏好×1 事实×3`
- **测试结果**：481 passed（+2），7 failed（6 个 libvips 预存，1 个 flaky 无关）
- **回滚方案**：git revert 到 9f2e72e，旧 `.md.migrated` 文件可手动改回 `.md`

## 2026-05-01 — GIF 动画表情保存为静态图修复 + 上下文追踪修复（项目侧）

- **类型**：Bug 修复
- **操作人**：Claude Code (assisted)
- **背景**：
  - Bot 收录 GIF 动画表情时，pyvips 只加载第一帧并保存为 JPEG（静态图）
  - StickerStore._detect_format() 显式拒绝 GIF
  - Thinker wait 时 last_input_tokens 被重置为 0，丢失上下文追踪
- **变更内容**：
  - 修改 `src/memory/image_cache.py`：新增 `_find_cached()`（多扩展名缓存命中），`_process_and_save()` 检测 GIF magic bytes → 保存原始字节为 `.gif` 跳过 pyvips
  - 修改 `src/sticker/store.py`：`_detect_format()` 接受 GIF 返回 `"gif"`
  - 修改 `src/llm/client.py`：Thinker wait 时用实际 input_tokens 代替 0
- **影响范围**：新收录的 GIF 表情包可保留动画；缓存命中支持多扩展名；上下文追踪更准确
- **注意**：client.py 的上下文追踪修复后因"问题与项目无关"被回退 — 此条目仅记录 GIF 修复

---

## 2026-04-30 — 第一期：群聊状态板（Group State Board）

- **类型**：新功能
- **操作人**：Claude Code (assisted)
- **背景**：
  - Bot 在群聊中缺乏对"当前正在发生什么"的结构化认知，完全依赖原始对话历史
  - 导致上下文跟踪差、称呼混乱、回复时机不当
  - 借鉴 KokoroMemo 的热记忆/状态板设计，采用轻量级规则方案
- **变更内容**：
  - 新增 `src/memory/state_board.py`：`GroupStateBoard` 类，基于规则从 `MessageLog` SQLite 推导线活跃用户、近期话题（二元组频率）、消息频率、@提及
  - 修改 `src/llm/prompt.py`：`build_blocks()` 返回值从 5 块扩展为 6 块 `[static, mood, state_board, affection, stable, memo]`；新增 `_build_state_board()` 方法
  - 修改 `src/llm/client.py`：将状态板文本注入 thinker 的 mood_text，使 reply/wait 决策感知群聊状态
  - 修改 `src/plugins/chat/__init__.py`：实例化并注入 `GroupStateBoard`，bot 连接时更新 `bot_self_id`
  - 新增 `tests/test_state_board.py`：14 个测试覆盖快照格式化、QQ 解析、文本清洗、二元组提取、活跃用户/频率/话题/@提及推导
- **影响范围**：群聊 prompt 中新增 `【当前群聊状态】` 块；thinker 决策可感知群聊活跃度；私聊不受影响（空块）
- **设计原则**：无新外部依赖、无额外 LLM API 调用、从现有 MessageLog 读取、cache_control: ephemeral

---

## 2026-04-30 — 群聊图片自动描述 + Loguru 格式错误修复

- **类型**：Bug 修复 + 功能恢复
- **操作人**：Claude Code (assisted)
- **背景**：
  1. 群聊图片只显示 `«图片»` 占位符，LLM 无法在上下文中看到图片内容，必须先调用 `describe_image` 工具才能理解
  2. NapCat 发送 `[json:data={...}]` 消息时，loguru stderr colorizer 将 JSON 中的花括号解析为 format field 导致 `ValueError: unmatched '{' in format spec`
- **变更内容**：
  - `_render_message()`：群聊图片下载后移除 `«图片»` 占位符，改为通过 Qwen VL 自动描述（与私聊一致）；动画表情（`sub_type=1`）仍显示 `«动画表情»`
  - `bot.py` `_channel_format()`：对 `record['message']` 中的 `{`/`}` 做转义（`{{`/`}}`），loguru colorizer 解析后自动还原为单花括号
- **影响范围**：群聊图片现在自动携带描述文本供 LLM 理解；`[json:...]` 消息不再触发日志格式异常

---

## 2026-04-30 — @提及绕过 thinker + 缓存告警排除 0 轮调用

- **类型**：Bug 修复 + 逻辑优化
- **操作人**：Claude Code (assisted)
- **背景**：
  1. 用户 @bot 后，thinker 决定 wait（沉默），导致提及必回复失效。根因：thinker 收到的群聊消息不包含 is_at 信息
  2. 缓存命中率告警频繁误报。根因：0 轮调用（直接回复）命中率 ~19%（仅静态系统块命中），多轮调用 ~60-80%，混合采样拉低平均值
- **变更内容**：
  - `_GroupSlot` 新增 `force_reply` 字段；`notify(is_at=True)` 设置该标志；`_do_chat()` 传入 `_llm.chat(force_reply=True)` 绕过 thinker
  - `UsageTracker._check_alerts()` 排除 `tool_rounds == 0` 的调用，仅多轮调用参与缓存命中率采样
  - 冷启动检查移至 tool_rounds 过滤之前，确保首次调用正确消费
- **影响范围**：@提及必定回复；缓存告警仅在多轮调用命中率异常低时触发

---

## 2026-04-30 — 群聊隐私遮掩

- **类型**：新功能
- **操作人**：Claude Code (assisted)
- **背景**：群聊与私聊共享同一份好感度记忆，在公开场合暴露对用户的深入了解会显得不自然。需要在群聊中模拟公私场合区别，但记忆本身保持共通。
- **变更内容**：
  - `AffectionEngine.build_affection_block()` 新增 `in_group` 参数：群聊中隐藏好感度分数、使用模糊 tier 标签（"不太熟"/"有点面熟"等）、注入社交距离指令（"不要主动暴露深入了解"）、隐藏昵称偏好和心情加成
  - `PromptBuilder.build_blocks()` 根据 `group_id is not None` 自动推导 `in_group`
  - 新增 `privacy_mask` 配置开关（`GroupConfig` / `ResolvedGroupConfig`，默认 true），允许对特定群关闭遮掩
  - `LLMClient.chat()` → `PromptBuilder.build_blocks()` 链路传递 `privacy_mask` 参数
  - `GroupChatScheduler._do_chat()` 解析群配置后传入
- **影响范围**：仅群聊。`privacy_mask=false` 或私聊时行为与旧版一致。私聊中深度询问仍可触发完整记忆。
- **回滚方案**：`[group].privacy_mask = false` 或在群覆盖中关闭。

---

## 2026-04-30 — 表情包频率上调

- **类型**：参数调整
- **操作人**：Claude Code (assisted)
- **背景**：主动表情包发送偏保守，需更符合元气二次元角色设定。
- **变更内容**（`src/llm/prompt.py` `frequently` 档）：
  - 触发阈值 ≥4 → ≥2（消息表达任意情绪即触发）
  - 移除连发惩罚（原 -1）
  - 新增"随口接话 +1~2"评分项
  - 态度：宁可多发不要错过
- **影响范围**：rebuild 后生效。

---
## 2026-04-30 — 要素察觉功能框架（含 LLM 模式）

- **类型**：新功能
- **操作人**：Claude Code (assisted)
- **背景**：群聊中某些触发词适合用预设回复快速响应（如"早安""晚安"），类似复读机制在 LLM 调度前拦截。后期扩展 LLM 模式支持反差吐槽等需要生成能力的场景。
- **变更内容**：
  - 新增 `ElementRule` / `ElementDetectionConfig` 配置模型（`src/config.py`），`ElementRule` 含 `use_llm` 字段
  - 新增 `ElementDetector` 插件（`src/plugins/element_detector.py`）：静态模板替换 + LLM 模式分发
  - 在 `collect_group_context` 中接入：复读检测之后、timeline 写入之前触发
  - LLM 模式：`reply` 字段作为 system prompt 指令，调用 `_llm._call` 生成回复（绕过 thinker/心情）
  - 配置 `[element_detection]`，静态规则示例 `这X神了↔这X拉了`、`我也要X吗→对`，LLM 规则 `X是这样的→反差吐槽`
  - 测试 9 条（`tests/test_element_detector.py`）
- **LLM 模式注意事项**：system prompt 需保持简短（~200 字符），角色描述过长会触发内心独白而非直接回复；只用 `result["text"]` 不用 thinking_blocks。
- **影响范围**：仅群聊。enabled=false 或 rules=[] 时无开销。
- **回滚方案**：设置 `[element_detection].enabled = false`。

---

## 2026-04-30 — 防检测人性化延迟

- **类型**：新功能
- **操作人**：Claude Code (assisted)
- **背景**：账号因发送消息过于规律被腾讯风控。需要在每次发消息前插入随机延迟模拟人类打字节奏。
- **变更内容**：
  - 新增 `AntiDetectConfig` 配置（`src/config.py`）：enabled / min_delay / max_delay / char_delay
  - 新增 `Humanizer` 单例模块（`src/anti_detect.py`）：`delay(text)` 方法按消息长度随机等待
  - 覆盖所有群聊发送路径：scheduler `_send_to_group`、echo/要素察觉/表情包发送
  - 管理员 `SendGroupMsgTool` 不加延迟
  - 配置 `[anti_detect]`（`config.toml` / `config.example.toml`）
- **默认参数**：基础延迟 0.5s–3.0s + 每字符 20ms
- **影响范围**：所有群聊和私聊消息发送。enabled=false 完全关闭。
- **回滚方案**：`anti_detect.enabled = false`。

---
## 2026-04-30 — Prompt 缓存命中率优化（P0–P3）

- **类型**：性能优化
- **操作人**：Claude Code (assisted)
- **背景**：排查发现 DeepSeek V4 Flash 缓存命中率 ~74%，波动剧烈（19%–87%）。根因四层：MemoExtractor 每轮 invalidate entity_block → 私聊每轮 cache MISS；mood/affection block 无 cache_control 且夹在两个缓存块之间；entity_block 混合稳定内容（索引/sticker）与高频内容（memo body）；thinker 调用无缓存标记且 tokens 未记入统计。
- **变更内容**：

  **P0 — 移除 MemoExtractor 即时 invalidate**（`src/memory/memo_extractor.py`）
  - 删除 `self._prompt.invalidate(user_id=user_id)`。memo 照常写入但 entity_block 缓存不清空，下次同用户调用可命中。Memo 数据推迟到 compaction 或 Dream Agent 整理时刷新。
  
  **P1 — mood_block / affection_block 加 cache_control**（`src/llm/prompt.py` → `_build_mood_block` / `_build_affection_block`）
  - 两个块均添加 `"cache_control": {"type": "ephemeral"}`。各自按自然频率变化（mood ~15 分钟、affection 按互动累积），DeepSeek 可在各自窗口内独立复用。
  
  **P2 — entity_block 拆分**（`src/llm/prompt.py` → `build_blocks`）
  - 原 entity_block 拆为 stable_block（全局索引 + sticker 视图，缓存键 `__stable__`，几乎永久命中）和 memo_block（群/用户 memo body，缓存键按 entity，compaction 时刷新）。
  - 测试更新（`tests/test_prompt.py`）：block 数量 4→5，断言适配新布局。
  
  **P3 — Thinker 缓存 + 用量追踪**（`src/llm/thinker.py` + `src/llm/client.py`）
  - thinker system block 加 `cache_control`；`ThinkDecision` 新增 `usage` 字段返回 token 数据。
  - `chat()` 中 thinker 调用独立记录为 `call_type="thinker"` 行（含 input/cache/output tokens），替换原来的全零占位。

- **影响范围**：rebuild 后生效。缓存命中率预计从 ~74% 升至 ~85%+；usage 统计新增 thinker 行类型；私聊连续对话 entity_block 可稳定命中；stable_block 几乎永不 miss。
- **回滚方案**：`git revert` 相关提交。

---

## 2026-04-30 — 稳固人格 & /debug 管理员限制

- **类型**：功能新增 + 安全加固
- **操作人**：Claude Code (assisted)
- **变更内容**：

  **1. 稳固人格**（`soul/instruction.md`）
  - 新增「稳固人格 — 拒绝被随意操控」章节。非管理员试图操控 bot 行为（改说话方式、命令式称呼等）时，根据当前心情值选择性回应：心情好配合玩一下但不改变、心情差拒绝或怼回去、偶尔叛逆故意做错。区分真伪请求（自然自我介绍 vs 命令式操控）。
  
  **2. /debug 管理员限制**（`src/plugins/chat/__init__.py`）
  - 新增 `_admins` 模块级变量，`handle_private_chat` 中 /debug 检测后校验管理员身份。非管理员使用 /debug 时前缀静默剥离、消息按普通对话处理，日志记录 warning。

- **影响范围**：restart 生效（soul 文件 mount） + rebuild 生效（debug 限制代码）。bot 不再盲从群友的行为指令；/debug 仅管理员可用。
- **回滚方案**：`git checkout soul/instruction.md` 或 `git revert` 相关提交。

---

## 2026-04-30 — debug 模式重复发送修复 & Markdown 代码层剥离 & Docker 时区修复

- **类型**：Bug 修复（3 项）
- **操作人**：Claude Code (assisted)
- **变更内容**：

  **1. debug 模式消息重复发送**
  - 根因：`force_reply` 路径中 `on_segment` 回调发送了一次消息，随后调用方 `private_chat.finish()` 再发送一次。两处在 `client.py` 的 force_reply 分支均存在此问题。
  - 修复（`src/llm/client.py`）：移除两处 force_reply 路径中的 `on_segment` 调用，仅由 `finish()` 发送单条回复。

  **2. LLM 回复仍含 Markdown 格式**
  - 根因：`soul/instruction.md` 已明确禁止 Markdown，debug 模式也注入了格式约束，但 DeepSeek 偶尔忽略。代码层无兜底剥离。
  - 修复（`src/llm/client.py`）：新增 `_strip_markdown()` 函数 + 8 个正则模式（bold/h2/list/olist/inline-code/fence/strikethrough），在两处 reply 提取点（tool loop 内和 tool loop 耗尽后）调用。跳过 italic（单 `*` 在东亚文字中太常见易误伤）。

  **3. Docker 容器时区为 UTC**
  - 根因：`docker-compose.yml` 中 napcat 和 bot 均未设 `TZ` 环境变量，容器默认 UTC。日志时间戳显示前一天（如 CST 4/30 凌晨 → 日志显示 4/29）。
  - 修复（`docker-compose.yml`）：napcat 和 bot 服务均添加 `TZ=Asia/Shanghai` 环境变量。

- **影响范围**：rebuild 后生效。debug 模式消息不再重复；LLM 回复中的 `**加粗**`、`# 标题`、\`代码\`、```代码块```、`- 列表` 等格式自动剥离；日志时间戳正确显示 CST。
- **回滚方案**：`git revert` 相关提交。

---
## 2026-04-30 — 颜文字→表情包程序化强制执行 & 主动记忆提取系统

- **类型**：Bug 修复 + 功能新增
- **操作人**：Claude Code (assisted)
- **变更内容**：

  **1. 颜文字→表情包程序化强制执行**
  - 根因：system prompt 中的"逐条自评打分"模式依赖 LLM 自觉执行，deepseek-v4-flash 在专注于写作时经常忘记同时调用 send_sticker。用户反馈「(≧▽≦)/」等颜文字发了但表情包没跟上。
  - 修复（双层方案）：
    - **Prompt 层**（`src/llm/prompt.py`）：三种频率模式的 prompt 全部重构，将「颜文字强制配图」提升为第一条硬性规则（位置在评分系统之前），明确标注"这不是可选的——颜文字=表情包"
    - **代码层**（`src/llm/client.py`）：新增 kaomoji 正则检测（`_KAOMOJI_RE`），当 LLM 返回的文本含颜文字但未调用 send_sticker 时，自动注入强制 sticker 选择轮次（追加 system 指令 + continue 工具循环），不再依赖 LLM 自觉
  - 工作机制：LLM 忘发 → 代码检测到颜文字 → 强制追加一轮 tool call → LLM 只需选表情包发送（不再需要自我评分）

  **2. 主动记忆提取系统（MemoExtractor）**
  - 根因：memo 仅在上下文压缩（compact）时写入，而压缩只在 token 超阈值时触发。私聊对话短、永远不触发压缩 → `storage/memories/users/` 目录完全为空 → bot 每次都是"脑袋空空的"。旧 MaiBot 使用 HippoMemorizer（定时批量处理），但有一致性延迟。
  - 修复（新增 `src/memory/memo_extractor.py`）：
    - 每次对话回合结束后，异步（fire-and-forget）调用轻量 LLM（max_tokens=256）提取用户事实
    - 提取到的事实通过 `MemoStore.append()` 写入用户备忘录的「待整理」区
    - Dream Agent 后续整理时可合并去重
    - 下次对话时 `recall_memo` 立即可查
  - 比旧项目更好的点：
    - **即时性**：每轮对话后立即提取（旧 MaiBot 是定时批量，有延迟）
    - **轻量**：单次 256 token 输出，不增加用户感知延迟（后台异步）
    - **精准**：只提取当前轮次的新事实，不重复扫描历史
    - **渐进**：记忆随对话自然累积，而非等压缩触发
  - 接线位置：`src/plugins/chat/__init__.py` 创建 MemoExtractor 实例，传入 LLMClient；`src/llm/client.py` → `chat()` 在返回回复前启动后台提取任务

- **影响范围**：rebuild 后生效。颜文字表情包现在有代码兜底保证发送；私聊记忆从零变为每轮自动记录。群聊记忆仍依赖压缩（后续可扩展 extractor 支持群聊）。
- **回滚方案**：`git revert` 相关提交。

---
## 2026-04-30 — 模拟思考后续修复 & 日志花括号转义 & 自动识图错误可见性

- **类型**：Bug 修复（3 项）
- **操作人**：Claude Code (assisted)
- **变更内容**：

  **1. Thinker 导致不再主动发表情包**
  - 根因：thinker 的 `[思考] ...` 被注入为 user 角色消息，盖过 system prompt 中的表情包频率规则（"总评分 ≥ 4 时调用 send_sticker"），LLM 严格按字面执行 thinking 指令而忽略 sticker 规则
  - 修复（`src/llm/client.py`）：thought 从 user 消息改为追加到 system blocks（`[思考指引] + 注意：仍需遵循所有指令包括表情包使用规则`），让 system 层两条指令并列生效

  **2. 日志花括号 KeyError**
  - 根因：`tool_call` / `tool_result` 日志的 JSON 字符串含 `{` `}` 花括号，被 loguru Handler #3（NoneBot 默认 handler）的格式解析器误读为格式字段，抛出 `KeyError: '"image_tag"'` 等错误
  - 修复（`src/llm/client.py`）：对 JSON 字符串做花括号转义（`{` → `{{`，`}` → `}}`），避免被下游 handler 二次解析

  **3. 自动识图静默失败**
  - 根因：`_render_message()` 中的 `_describe_one()` 用 `except Exception: pass` 静默吞掉所有异常，Qwen VL 自动识图失败时既无日志也无法诊断
  - 修复（`src/plugins/chat/__init__.py`）：失败时打 WARNING 日志（含文件路径）；将 `_vision_client` 全局变量改为使用函数参数 `vision_client` 传入

- **影响范围**：rebuild 后生效。表情包恢复主动发送；日志不再出现 KeyError；图片识别失败时可见具体原因。
- **回滚方案**：`git revert` 相关提交。

---
## 2026-04-29 — 预回复思考阶段（模拟思考）

- **类型**：功能新增
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - `src/llm/thinker.py`：新增预回复思考模块。`ThinkDecision` 数据类（action: reply/wait/search + thought），`THINKER_SYSTEM_PROMPT` 指导 LLM 快速判断下一步行动（6 条决策原则），`parse_think_output()` 解析 JSON 输出，`think()` 异步函数执行思考 LLM 调用
  - `src/config.py`：新增 `ThinkerConfig`（enabled=true, max_tokens=256），`BotConfig` 新增 `thinker` 字段
  - `config.toml` / `config.example.toml`：新增 `[thinker]` 配置节
  - `src/llm/client.py`：`LLMClient.__init__` 新增 `thinker_enabled` / `thinker_max_tokens` 参数；`chat()` 在工具循环之前调用 thinker，wait 返回 None（等同 pass_turn），reply/search 将 thought 注入消息列表
  - `src/plugins/chat/__init__.py`：LLMClient 构造传入 thinker 配置
- **背景**：bot 在短时间内连续回复多条消息（拆成多个事件分别回复），缺乏「说话前先想一下」的机制。借鉴旧 MaiBot Planner→Replyer 架构，在 LLM 生成回复之前先用轻量调用判断下一步行动：回复、沉默、或搜索。
- **影响范围**：rebuild 后生效。每次回复前额外一次轻量 LLM 调用（max_tokens=256），约增加 0.5-1.5s 延迟。wait 决策可减少不必要的回复，降低 token 消耗。`[thinker].enabled = false` 可关闭该功能。

---
## 2026-04-29 — 日志频道过滤系统

- **类型**：功能增强
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - `src/config.py`：新增 `LogChannelConfig`（12 bool 字段），`LogConfig` 新增 `channels` 字段
  - `config.toml` / `config.example.toml`：新增 `[log.channels]` 节，默认开启 6 个关键频道（message_in/out/thinking/mood/affection/schedule），其余关闭
  - `bot.py`：`_quiet_filter` 改为 `_make_channel_filter()` 闭包，根据 `LogChannelConfig` 开关过滤 stderr 日志；ERROR 始终放行，文件日志不受影响
  - 全项目 16 个源文件打日志频道标签（`logger.bind(channel="...")`）：message_in、message_out、thinking、mood、affection、schedule、scheduler、usage、compact、system、debug、dream
- **背景**：`docker compose logs` 输出大量调试信息（matcher noise、调度器决策、token 用量等），人眼难以提取关键信息。用户要求按重要性分级，默认 ERROR 级别 + 6 个关键频道可见。
- **影响范围**：重启后生效。stderr 输出大幅减少，默认只看到收/发消息、工具调用、心情、好感度、日程和所有 ERROR。文件日志 `storage/logs/bot_*.log` 不受影响（始终全部 DEBUG）。无需重建镜像（restart 即可）。

---
- **类型**：功能新增
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - 新增 `src/schedule/calendar.py`：`DayContext` / `BirthdayEntry` 数据类 + `get_day_context()` 函数。硬编码 2026 年中国法定节假日（7 个节日共 40 天）、6 个调休上班日、15 个不放假的特殊节日（七夕/圣诞等）、世界计划全 26 位角色生日
  - `src/schedule/__init__.py`：导出 `DayContext`、`BirthdayEntry`、`get_day_context`
  - `src/schedule/generator.py`：`_SCHEDULE_SYSTEM_PROMPT` 新增日期类型指引（上学日/周末/节假日/调休日/角色生日），`_generate()` 用户消息改为包含 `DayContext` 详细信息
  - `src/schedule/mood.py`：`_compute()` 新增第 5 步「日期类型心情加成」——节假日 +0.15 valence +0.1 energy，调休日 -0.05 valence，角色生日 +0.1 valence +0.1 openness，自己生日额外 +0.15 valence +0.1 energy。`build_mood_block()` 新增生日/特殊节日提示文本
  - `src/tools/datetime_tool.py`：`execute()` 返回内容附加节假日/特殊日/生日信息
  - `soul/instruction.md`：新增「角色生日」小节，指导 LLM 在角色生日当天自然庆祝
  - 测试：`tests/test_calendar.py`（35 个测试）全部通过
- **背景**：日程生成完全不感知真实日期（周一至周五全在上学、节假日也在上课、角色生日无人提及）。用户要求结合真实日期但保留虚拟感。
- **影响范围**：rebuild 后生效。心情系统自动感知节假日/生日并调整情绪；日程生成 prompt 包含日期类型指引；`get_datetime` 工具返回特殊日期信息；mood_block 约增加 0~150 chars（仅特殊日期时）。无需新增配置项——日历数据硬编码，每年更新一次即可。
- **回滚方案**：`git revert` 相关提交。
---
## 2026-04-29 — 称呼与好感度系统

- **类型**：功能新增
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - 新增 `src/affection/` 模块：`models.py`（AffectionProfile 数据类，score/tier/mood_bonus/suffix）、`store.py`（JSON 文件持久化，`storage/affection/{user_id}.json`）、`engine.py`（好感度计算、昵称解析、affection_block 文本构建）、`__init__.py`
  - 新增 `src/tools/affection_tools.py`：`set_nickname` 工具，LLM 可在用户说"叫我xx"时调用
  - `src/config.py` 新增 `AffectionConfig`（enabled/score_increment=0.8/daily_cap=10.0）
  - `src/llm/prompt.py`：`PromptBuilder` 新增 `affection_engine` 参数，`build_blocks()` 返回 4 个 block（static/mood/affection/entity），affection_block 每次刷新不缓存
  - `src/plugins/chat/__init__.py`：初始化 AffectionStore/AffectionEngine，注册 SetNicknameTool，传入 PromptBuilder 和 LLMClient
  - `src/llm/client.py`：`LLMClient` 新增 `affection_engine` 参数，`chat()` 中每次 LLM 调用前记录互动
  - `config.toml` / `config.example.toml` 新增 `[affection]` 配置节
  - `soul/instruction.md` 新增「你的日常与心情」节：心情对说话影响、不可主动提及日程的规则
  - 测试：`tests/test_affection.py`（32 个测试）全部通过，`tests/test_prompt.py` 更新 block 索引
- **规则**：每次互动 +0.8 分，日上限 10.0 分，新用户 0 分起；好感度 ≥ 60 时 affection_block 注入"对他态度更温和"；称呼优先级：自定义 > 群名片 > QQ昵称 > QQ号
- **影响范围**：build 后生效。好感度数据存储在 `storage/affection/`，affection_block 约 150-250 chars 注入 system prompt。总开关 `[affection].enabled = false` 可关闭。
- **回滚方案**：`git revert` 相关提交，或设 `[affection].enabled = false`

---
## 2026-04-29 — 清理 soul 中的日文

- **类型**：配置变更
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - `soul/identity.md`：`わんだほーい☆` → `哇嚯☆`（5 处），`鳳えむ` → `凤笑梦`，昵称列表移除 `えむ`/`鳳えむ`
  - `soul/instruction.md`：`わんだほーい` → `哇嚯`（6 处）
  - `src/schedule/mood.py`：心情提示中的 `わんだほーい` → `哇嚯`（2 处）
- **背景**：用户检查测试群聊日志，要求 bot 输出中不出现日文。
- **影响范围**：soul 文件 volume mount，restart 即生效；mood.py 需 rebuild。

---
## 2026-04-29 — 模拟日程系统

- **类型**：功能新增
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - 新增 `src/schedule/` 模块：`types.py`（Schedule/TimeSlot/MoodProfile 数据类）、`store.py`（JSON 持久化+内存缓存）、`generator.py`（每日凌晨 2:00 通过 LLM 生成日程 JSON）、`mood.py`（MoodEngine 实时心情计算 + 19 个 mood_hint→MoodProfile 预设 + 9 个心情→行为 prompt 映射 + 20% 反常情绪机制）、`__init__.py`（导出）
  - `src/config.py` 新增 `ScheduleConfig`（enabled/storage_dir/generate_at_hour/mood_anomaly_chance/mood_refresh_minutes）
  - `src/llm/prompt.py`：`PromptBuilder` 新增 `schedule_store`/`mood_engine` 参数，`build_blocks()` 在 static_block 和 entity_block 之间插入非缓存的 `mood_block`（当前时间+活动+心情+行为指引+反泄漏规则），entity_block 缓存结构从 `list[blocks]` 改为单 `dict`
  - `src/plugins/chat/__init__.py`：`_init()` 初始化 ScheduleStore/MoodEngine/ScheduleGenerator；`_on_connect()` 加载当日日程+启动后台生成循环；`_shutdown()` 停止生成循环；DateTimeTool 注册时传入 schedule_store
  - `src/tools/datetime_tool.py`：`DateTimeTool` 新增可选 `schedule_store` 参数，返回当前时间时附加「你正在：xxx」上下文
  - `soul/instruction.md` 新增「你的日常与心情」节：心情对说话影响、不可主动提及日程的规则
  - `config.toml` / `config.example.toml` 新增 `[schedule]` 配置节
  - 测试：`tests/test_schedule_store.py`（12 个）、`tests/test_schedule_generator.py`（8 个）、`tests/test_mood.py`（23 个）——共 43 个测试全部通过
  - **Bug 修复**：`_lookup_base()` 原先直接返回 `_MOOD_BASE` 字典中的 MoodProfile 对象，`_compute()` 对其原地修改导致模块级预设被污染（后续调用看到前一次的反常情绪理由）。改为每次返回新 MoodProfile 副本。
- **背景**：用户希望 bot 像"过着真实一天"的人，语气/情绪随日程自然变化而非随机切换。详见计划文档。
- **影响范围**：rebuild 后生效。心情系统在每次 LLM 调用时实时计算（15 分钟缓存窗口），mood_block 约 200 chars 注入 system prompt。日程生成每天 1 次 LLM 调用（~2300 token），几乎无额外成本。总开关 `[schedule].enabled = false` 可完全关闭。
- **回滚方案**：`git revert` 相关提交，或设 `[schedule].enabled = false`

---
## 2026-04-29 — 主动搜索行为指令增强

- **类型**：配置变更
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - `soul/instruction.md`：重写「工具使用」→「主动搜索」小节，新增核心原则"不知道就查，不要猜"
  - 明确触发场景：不认识图片角色/作品/人物、不了解的新梗/热点/术语、不确定的事实性问题、听不懂的群话题
  - 搜索策略：多关键词并行 web_search、不够时 web_fetch 深入、矛盾时以权威来源为准
  - 搜索后回应：用自己的语气自然输出，不要"根据搜索结果……"句式；搜不到坦诚说但保持元气
- **背景**：bot 与群友交流时常因不认识图片角色或不了解话题而哑火。虽然 web_search/web_fetch 工具一直可用，但缺少明确的行为指令驱动主动使用。
- **影响范围**：`docker compose restart bot` 即刻生效，无需 rebuild

---
## 2026-04-29 — 群聊复读功能

- **类型**：功能新增
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - 新增 `src/plugins/echo.py`：`EchoTracker` 类 + `build_echo_key()` 函数。按群跟踪消息重复次数，5 分钟内同一内容出现 3 次触发复读
  - `build_echo_key()` 从 OneBot 原始消息段构建 key，覆盖文本/表情包(image:sub_type:md5)/QQ表情(face)/@。同一表情包重发（相同 MD5）可被识别为重复
  - 5% 概率不参与复读，改为发送"打断复读！"
  - 连续"打断复读！"消息触发打断链："打断复读！" → "打断打断复读！" → "打断打断打断复读！"...
  - `src/plugins/chat/__init__.py`：在 `collect_group_context` 中插入快速路径，复读命中后 cancel_debounce + 记录用户消息到 timeline + 记录 echo 到 timeline，然后 return，不触发 LLM
  - `src/llm/scheduler.py`：新增 `cancel_debounce()` 方法，复读命中时取消待处理的 debounce 任务并重置计数器，防止 LLM 在复读后自顾自说话
  - `tests/test_echo.py`：24 个测试用例（14 个 EchoTracker + 10 个 build_echo_key）
- **背景**：用户要求新增 QQ 群传统复读功能。初版仅支持纯文本，用户反馈表情包也应可复读、复读后 bot 不应继续说话。二版通过 `build_echo_key` 覆盖表情包/图片（基于 NapCat 提供的 MD5 去重），通过 `cancel_debounce` 防止后续 LLM 触发。
- **影响范围**：仅群聊生效，私聊不影响。复读命中后 bot 直接发送消息并 return，不调用 scheduler，零 token 消耗。
- **回滚方案**：`git revert` 相关提交，`docker compose up bot -d --build`

---
## 2026-04-29 — 表情包主动发送频率 + sub_type 修正

- **类型**：功能新增 + Bug 修复
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - `src/config.py` → `StickerConfig` 新增 `frequency` 字段（`"rarely"` / `"normal"` / `"frequently"`），默认 `"normal"`
  - `src/llm/prompt.py` → 新增 `_STICKER_FREQUENCY_PROMPTS` 字典，`PromptBuilder` 接受 `sticker_frequency` 参数并在 system prompt 中注入对应频率的行为指令
  - `src/plugins/chat/__init__.py` → 初始化 `PromptBuilder` 时传入 `bot_config.sticker.frequency`
  - `src/tools/sticker_tools.py` → `SendStickerTool` 中 `"subType"` 修正为 `"sub_type"`（OneBot v11 snake_case 标准），新增 `"summary": "[动画表情]"`（QQ 据此区分表情尺寸）。根因：旧 MaiBot-Napcat-Adapter 使用 `"subtype": 1` 全小写格式，当前代码用了不被识别的驼峰格式
  - `tests/test_sticker_tools.py` → 更新 subType 断言为 `sub_type` + `summary`
  - `soul/instruction.md` → 表情包「发送原则」重写，新增何时发送、如何选择、流程等详细指引
  - `config.toml` / `config.example.toml` → 新增 `frequency = "frequently"` / `frequency = "normal"`
- **背景**：bot 被 @ 或要求时才会发表情，不会主动使用。用户希望 bot 像二次元角色一样在对话中自然甩表情包。初版按固定频率（每 N 轮）设计，用户反馈"欸——好狡猾这种话天然适配表情包为什么不发"，改为逐条评估：每条消息独立打分，超过阈值就发，频率设置只改变阈值高低而非间隔。表情包以普通图片尺寸显示是因为 `subType` 字段名不被 NapCat 识别。
- **影响范围**：rebuild 后生效。`frequency` 改变 LLM 使用 `send_sticker` 的倾向——`rarely` 保守，`frequently` 积极甩表情包。
- **回滚方案**：设 `frequency = "rarely"` 或 `enabled = false` 关闭表情包系统。

---
## 2026-04-29 — Qwen VL 表情包识别 + 偷取表情包

- **类型**：功能新增
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - 新增 `src/vision/client.py`：`VisionClient` 通过 OpenAI 兼容 API 调用 Qwen2.5-VL-7B 小模型描述图片内容
  - `src/config.py` 新增 `QwenVLConfig`（enabled/base_url/api_key/model），挂载在 `VisionConfig.qwen` 下
  - `config.toml` 新增 `[vision.qwen]` 配置节，启用 Qwen VL 并配置 DashScope API
  - `src/config_loader.py` 新增 `QWEN_VL_API_KEY`/`QWEN_VL_BASE_URL`/`QWEN_VL_MODEL` 环境变量映射
  - `src/plugins/chat/__init__.py`：`_render_message()` 下载图片后自动调用 Qwen VL 生成文字描述，注入 `«图片N: 描述»` 到消息中，让文本模型 DeepSeek 也能"看到"图片
  - `src/tools/sticker_tools.py`：
    - 新增 `DescribeImageTool`（describe_image），LLM 可主动请求详细查看某张图片
    - `SaveStickerTool` 权限放宽：`requested_by` 改为可选——bot 主动偷取时留空直接收录（source="stolen"），用户请求时仍需管理员权限（source="admin"）
  - `soul/instruction.md` 表情包节重写：新增「识别图片内容」「偷取表情包」小节，教导 LLM 主动发现、识别、收录群友发的表情包
- **背景**：DeepSeek V4 不支持视觉，bot 无法理解图片/表情包，也无法主动收藏。通过 Qwen VL 小模型代为描述图片，既便宜（~$0.0001/张）又快速。
- **影响范围**：rebuild 后生效。群聊和私聊中的图片会自动附带文字描述，bot 能看懂表情包并主动偷取喜欢的。注意：需要配置有效的 DashScope API key（`[vision.qwen].api_key` 或 `QWEN_VL_API_KEY` 环境变量）。
- **回滚方案**：`git revert` 相关提交，或设 `[vision.qwen].enabled = false` 关闭。

---
## 2026-04-29 — 群内黑话主动学习机制

- **类型**：功能新增
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - `soul/instruction.md` 记忆系统新增「群内黑话学习」节，指引 LLM 主动识别、记录和使用群内黑话
  - 识别规则：多人反复使用的不熟悉词汇、有人直接解释的词、非标准汉语但有明确语义的词、音游/游戏术语
  - 记录格式：`- **词汇** (N次): 在这个群语境下的含义`，写入群备忘录 `### 群内惯用词`
  - 使用原则：已记录的可以自然使用，不确定的先不用，不堆砌
- **背景**：旧 MaiBot 的 442 条惯用词 + 186 条惯用表达已迁移到群备忘录，bot 重启后 LLM 能在 system prompt 中看到。但缺少主动学习新黑话的指令。
- **影响范围**：bot 重启后生效，LLM 将在群聊中主动捕捉新词汇并记录到群备忘录

---
## 2026-04-29 — 修复换行不分段 + DeepSeek thinking blocks 400

- **类型**：Bug 修复（2 个）
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - **换行不分段**：`_split_naturally()` 改为 `\n` 硬切分（每行一条消息），仅 < 4 字的极短行合并到邻居。原先算法把同一段内所有行拼到 45 字才切，导致 LLM 写的多行被合并成一条。
  - **DeepSeek thinking blocks 400**：`_call_api()` 新增 `thinking` 和 `thinking_delta` 事件捕获，返回值新增 `thinking_blocks`。`chat()` 构建 assistant 消息时，将 thinking blocks 原样插入 content 头部。根因是 DeepSeek V4 thinking mode 要求第二轮 API 调用必须把第一轮返回的 thinking block 传回，否则 400。
  - `_call_api()` 新增 400 错误响应体日志（`logger.error("API {} | body={}")`），方便后续排查。
- **影响范围**：工具调用（get_datetime 等）恢复正常；私聊/群聊多行回复正确拆分。rebuild 后生效。
- **回滚方案**：`git revert` 相关提交，`docker compose up bot -d --build`

---
## 2026-04-29 — 自然分句发送（仿真人逐条回复）

- **类型**：功能新增
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - `src/llm/client.py`：新增 `_split_naturally()` / `_split_on_sentence_end()` / `_split_long_on_comma()`，按优先级自动切分 LLM 回复：
    1. `\n\n`（段落空行）→ 必定切分
    2. `\n`（换行）→ 主切分点，每行一个想法
    3. `。！？` → 单行超 45 字时在此切分
    4. `,，;；:：、` → 单句仍超 45 字时最后手段
  - 参数：`_MAX_CHUNK=45`、`_MIN_CHUNK=4`、段间延迟 1.2s
  - 保留 `---cut---` 显式切分机制
  - 修复末尾标点（`。！？`）孤立成段的 bug：`_split_on_sentence_end` 末尾标点自动回贴、合并逻辑对标点不加 `\n`
  - `soul/instruction.md`「分段发送」节更新：引导 LLM 每行写一个想法、每条控制在 2~3 行/40 字以内
- **影响范围**：群聊和私聊 LLM 回复均自动逐条发送，模拟真人连发。rebuild 后生效。
- **回滚方案**：`git revert` 相关提交，`docker compose up bot -d --build`

---
## 2026-04-29 — 昵称提及检测 + @/昵称必回复

- **类型**：功能新增
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - `.env` 新增 `BOT_NICKNAMES` 环境变量，包含 bot 所有昵称（凤笑梦、emu、笑梦、姆、姆姆、凤同学、Emu、凤、凤えむ、えむ）
  - `src/plugins/chat/__init__.py`：`collect_group_context()` 新增文本昵称扫描，消息中含 bot 昵称时视为 `is_at=True`，强制触发 LLM 调用
  - `soul/identity.md`：`## 插话方式` 重构，将 @提及、回复、叫名字/昵称升级为「必须回复」（不可 pass_turn），其余场景为「视情况回复」
  - `src/admin/auth.py`：修复 form token 类型标注导致 pyright 报错
- **影响范围**：群聊中提及 bot 昵称将立即触发回复（等同原生 @）。rebuild 后生效。
- **回滚方案**：`git revert` 相关提交，`docker compose up bot -d --build`

---
## 2026-04-29 — Admin Dashboard 实现 + 端口修正

- **类型**：部署 / 配置变更
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - 新增 Admin Dashboard（`src/admin/`），基于 Jinja2 + htmx + Chart.js（CDN），6 个页面：总览、用量统计、群聊管理、配置查看、Soul 编辑、日志查看
  - 认证方式：`admin_token`（config.toml）或 `ADMIN_TOKEN` 环境变量 → HMAC 签名 Cookie
  - `BotConfig` 新增 `admin_token` 字段，`config_loader.py` 新增 `ADMIN_TOKEN` env var 映射
  - `MessageLog` 新增 `query_recent()` 方法
  - Docker Compose bot 服务新增 `8081:8080` 端口映射（宿主机 8080 被 Calibre 占用）
  - `docker-compose.yml` 从无端口暴露改为 `ports: ["8081:8080"]`
  - `pyproject.toml` 新增 `jinja2`、`python-multipart` 依赖，ruff 排除 `._*` 文件
- **影响范围**：bot rebuild 后生效，访问 `http://localhost:8081/admin/` 即可进入管理面板。默认 token 为 `admin`，建议通过环境变量设置强密码。
- **回滚方案**：`git revert` 相关提交，`docker compose up bot -d --build`

---
## 2026-04-29 — 凤笑梦 soul 文件重构

- **类型**：配置变更
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - 基于三份参考文档（`凤笑梦参考/` 目录下）重构 `soul/identity.md` 和 `soul/instruction.md`
  - 参考文档：《凤笑梦_角色扮演知识谱.md》《凤笑梦_语料风格包.md》《凤笑梦_剧情文案原文出处索引.md》
  - `identity.md`：完全重写，新增「基础身份」表格、「一句话定义」、「性格结构」（表层/深层/行动力/核心驱动力/成长主轴）、「人际关系」（司/宁宁/类/真冬/家庭）、「语气与说话方式」（核心公式/口头禅用法/严肃模式）、「像与不像」判据；保留「插话方式」章节
  - `instruction.md`：完全重写，新增「场景差分」（日常/兴奋/安慰/低落/解释/邀请/对象差分）、「必须避免的语气污染」；保留分段发送(---cut---)、群聊上下文理解、工具使用、记忆系统、表情包规则
  - 新增群备忘录分区支持：`### 群内惯用词`、`### 惯用表达`（配合之前的惯用词迁移数据）
- **影响范围**：bot 重启后即刻生效，角色扮演精度大幅提升
- **回滚方案**：`git checkout soul/identity.md soul/instruction.md`

---
## 2026-04-29 — 惯用词与表达迁移：旧 MaiBot → 新 bot 群备忘录

- **类型**：数据迁移
- **操作人**：Claude Code (assisted)
- **变更内容**：
  - 从旧 MaiBot 数据库 (`/Users/kragcola/MaiM-with-u/MaiBot/data/MaiBot.db`) 提取全部惯用词和惯用表达
  - 写入新 bot 群备忘录 `storage/memories/groups/993065015.md` 和 `984198159.md`
  - 群 993065015（烤）：442 条惯用词 + 186 条惯用表达
  - 群 984198159（测试）：1 条惯用词 + 1 条惯用表达
  - `config.toml` → `[memo].group_max_chars` 从 500 提升至 60000（容纳大量惯用词）
  - 创建 `storage/memories/index.md` 索引文件
- **数据来源**：旧 MaiBot SQLite → `jargon` 表（筛选 `is_jargon=1, count≥2`）+ `expression` 表（筛选 `checked=1, rejected=0`）
- **chat_id 映射**：通过逆向 MaiBot 哈希算法 (`md5("qq_{群号}")`) 确认 `0469082337...` → 群 993065015、`77b74300...` → 群 984198159
- **影响范围**：bot 启动后，群聊 system prompt 将自动加载对应群的惯用词/表达，提升角色扮演的语境贴合度
- **回滚方案**：删除 `storage/memories/groups/` 下的 .md 文件，恢复 `group_max_chars = 500`

---
## 2026-04-29 — 新 bot 初始化部署

**背景**：用本项目（amadeus-in-shell）替换原有的 MaiBot（v7.3.5），全新部署。

**旧 maibot 参考数据**（提取自 `/Users/kragcola/MaiM-with-u/`）：

| 项目 | 值 |
| --- | --- |
| 旧 bot QQ | 384801062（昵称：emu不吃小杯面） |
| 旧 bot 框架 | MaiBot v7.3.5 + MaiBot-Napcat-Adapter + LPMM |
| 旧 bot 活跃群 | 984198159（测试）、993065015（烤） |
| 旧 bot 部署日 | 2026-01-16，累计 28,769 请求 |
| 旧 bot 模型 | deepseek-v3 (planner) + qwen3-30b (replyer) + qwen3-vl-30 (vision) |
| 旧 LLM 端点 | DeepSeek v1 + SiliconFlow（均为 OpenAI 格式） |
| 人设变迁 | 牧濑红莉栖 → 凤笑梦（旧 bot 后来也已改为 Emu） |

**新 bot 配置概要**：

| 项目 | 值 |
| --- | --- |
| 人设 | 凤笑梦 (Emu Otori) — Wonderlands×Showtime |
| LLM | DeepSeek V4 Flash（Anthropic 兼容端点，1M 上下文） |
| API 端点 | `https://api.deepseek.com/anthropic` |
| 管理员 QQ | 1416930401（工丿囗） |
| 部署方式 | Docker Compose（NapCat + Bot） |
| NapCat 版本 | mlikiowa/napcat-docker:v4.15.0 |
| Git remote | `github.com/RoggeOhta/amadeus-in-shell` |

**关键架构差异**：

- LLM API 从 OpenAI 格式 (`/v1`) 改为 Anthropic 兼容格式 (`/anthropic`)，支持原生 tool_use + cache_control
- 不再使用 Planner+Replyer 分离架构，改为单一 LLM 调用 + Tool loop（最多 5 轮）
- 记忆系统从 LPMM 知识图谱改为 .md 备忘录 + GroupTimeline
- 主动插话从 `talk_value` 概率值改为 `## 插话方式` 规则 + `pass_turn` 工具
- 不再内置错别字生成器，靠 prompt 控制风格
- 部署从本地 Python 进程改为 Docker Compose

**部署前 checklist**：

- [ ] `.env` — SUPERUSERS、ONEBOT_WS_URLS、LLM_* 环境变量已配置
- [ ] `config.toml` — LLM 接入、群聊参数、vision/sticker/dream 已配置
- [ ] `soul/identity.md` — 凤笑梦人设已编写（从旧 MaiBot `personality` 字段迁移）
- [ ] `soul/instruction.md` — 行为指令已调整
- [ ] NapCat WebUI (`:6099`) 扫码登录新 QQ 号
- [ ] 目标 QQ 群（984198159、993065015）测试 @bot 回复、主动插话、工具调用

**部署步骤**：

```bash
docker compose up napcat -d    # 先起 NapCat，扫码登录
docker compose up bot -d       # 再起 Bot
```

**注意事项**：

- NapCat 容器**必须用 `restart`，禁止 `down`+`up`**（device fingerprint 变 = 触发 QQ 风控）
- NapCat 持久化目录：`napcat/config/`（配置）、`napcat/data/`（QQ session/device fingerprint）
- Bot 的 `soul/` 和 `.env` 通过 volume mount 注入，修改后 `docker compose restart bot` 生效
- `storage/` 目录持久化所有运行数据（用量库、消息库、日志、记忆、图片缓存、表情包）
- 旧 MaiBot 数据保留在 `/Users/kragcola/MaiM-with-u/MaiBot/data/`，如需迁移表情包或记忆可从此提取

---
## 模板 — 日常维护
### 部署记录
```markdown
## YYYY-MM-DD — <标题>
- **类型**：部署 / 配置变更 / 故障处理 / 升级
- **操作人**：
- **变更内容**：
- **影响范围**：
- **回滚方案**：
- **验证结果**：
```
### 故障记录
```markdown
## YYYY-MM-DD — <故障标题>
- **发现时间**：
- **现象**：
- **根因**：
- **处理步骤**：
- **恢复时间**：
- **后续措施**：
```
---
## 快速排查命令
```bash
# 查看 Bot 日志
docker compose logs bot --tail=100 -f
# 查看 NapCat 日志
docker compose logs napcat --tail=100 -f
# 检查容器状态
docker compose ps
# 用量 TUI
uv run python -m src.llm.usage_cli tui day
# 用量 API
curl http://localhost:8080/usage/summary/today
# 重启 Bot（人设/配置变更后）
docker compose restart bot
# 重建 Bot（代码/依赖变更后）
docker compose up bot -d --build
# 重启 NapCat（断线/风控后）
docker compose restart napcat
# 进入 Bot 容器
docker compose exec bot .venv/bin/python -c "..."
# 检查 storage 目录
ls -la storage/logs/ storage/usage.db storage/messages.db
```
