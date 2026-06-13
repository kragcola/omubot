# Omubot 生态借鉴式改进剩余阶段计划表

日期：2026-05-07  
来源：`docs/audits/qqbot-comparison-2026-05-07.md` 与当前 Omubot 已落地能力  
原则：不迁移架构、不默认引入重依赖、不替换 NapCat；以稳定性、扩展性、多样性为主线分阶段吸收成熟生态能力。

## 实时追踪

最后同步：2026-05-07 由 Codex 更新  
当前焦点：本轮可实施尾项已清空，进入“真实运行反馈微调 + optional extra 评估”阶段  
刚完成：Phase 2 profile 定义编辑器与更细粒度 Provider 管理、Phase 6 群级工具矩阵 / blocked users 编辑器 / 策略审计历史、Phase 3 插件软隔离/限流、Phase 1 健康告警降噪与策略化告警门槛、Phase 8 维护窗口提示、健康告警摘要与重启影响说明、Phase 8 配置回滚向导与基础备份、Phase 8 配置变更审计与保存前 diff 预览、Phase 7 plugin.sig 签名/来源校验预留、Phase 7 本地插件治理队列、未加载包排障与 action hint、Phase 7 本地插件包索引与来源/兼容检查、Phase 5 黑话质量守卫与 semantic metrics 系统页可视化、Phase 5 记忆检索语义接线、Phase 6 群 Profile、Phase 4 mock 协议测试、Phase 2 profile 热切换、Phase 1 关键错误聚合、Phase 2 分 profile rate limit、Phase 4 历史连接记录  
下一步建议：后续只保留三类工作：真实运行反馈下的门槛微调、协议端差异补缝，以及 optional extra 语义后端/插件分发增强；它们不再属于本轮必须收口的尾项。  

| 队列 | 阶段 | 子任务 | 状态 | 下一动作 |
| --- | --- | --- | --- | --- |
| Done | Phase 4 | 历史连接记录 | 已完成 | 已记录 NapCat 连接/断开时间、最近错误、恢复耗时，并在系统页展示 |
| Done | Phase 2 | 分 profile rate limit | 已完成 | 已按 profile 记录冷却、失败、限流和快失败状态，系统页可见 |
| Done | Phase 1 | 关键错误聚合 | 已完成 | 已从 loguru sink 聚合 WARNING/ERROR/CRITICAL，并在系统 API、服务健康和系统页展示 |
| Done | Phase 2 | profile 热切换 | 已完成 | 系统页可切换默认 profile 与任务映射，并立即更新运行中 LLMClient、写入 JSON 配置 |
| Done | Phase 4 | mock 协议测试 | 已完成 | 已用 mock OneBot 覆盖无 Bot、缺方法、失败响应、trace 脱敏和兼容清单契约 |
| Done | Phase 6 | 群 Profile | 已完成 | 已新增每群风格/工具/贴纸/黑话策略配置模型、保存 API、运行时生效链路与 Groups 模块化编辑抽屉 |
| Done | Phase 8 | 运维体验第二刀 | 已完成 | 已补配置回滚向导、可恢复快照列表、恢复接口与恢复审计 |
| Done | Phase 8 | 运维体验第三刀 | 已完成 | 已补维护窗口提示、健康告警摘要、系统页运维建议区与全局重启影响说明 |
| Done | Phase 1 | 告警策略尾项 | 已完成 | 已补顶层告警阈值、轻量 warning 折叠与维护窗口建议降噪 |
| Done | Phase 3 | 插件隔离尾项 | 已完成 | 已补错误/慢调用爆发后的软隔离冷却、抑制计数和系统/插件页可见状态 |
| Done | Phase 6 | 群 Profile 尾项 | 已完成 | 已补群级工具矩阵、blocked users 编辑器与策略审计历史 |
| Done | Phase 2 | Provider 尾项 | 已完成 | 已补 profile 定义编辑器、API Key 处理、定义保存与运行时同步 |
| Done | Phase 5 | 轻量语义增强正式版 | 基本收口 | 已完成默认 ngram、记忆检索语义兜底、黑话质量守卫、系统页 semantic metrics 与安全降级链路 |

## 当前状态

| 阶段 | 状态 | 已落地 | 仍需完成 |
| --- | --- | --- | --- |
| Phase 1 稳定性地基 | 基本收口 | SQLite WAL helper、系统页基础健康、Provider/协议概览、服务级健康聚合、SQLite quick_check、系统页服务健康面板、OneBot 请求 echo 追踪、关键错误聚合、维护窗口提示、健康告警摘要、告警阈值降噪 | 后续按真实运行反馈微调阈值 |
| Phase 2 Provider 多样性 | 已收口 | `LLMProfile`、`llm.profiles`、`llm.task_profiles`、Anthropic/OpenAI provider registry、Admin Provider 概览、thinker/compact/slang 分任务调用、profile 手动测试 API 与系统页测试按钮、分 profile rate limit、profile 热切换、profile 定义编辑器、API Key 保留/替换/清空、定义保存热同步 | 后续只按真实模型运营反馈微调默认映射和文案 |
| Phase 3 插件治理 | 已收口 | manifest v2 元数据、hook 健康、运行时启停、启停状态持久化、权限门禁、配置 schema 展示、插件配置保存、hook 耗时预算、插件软隔离/限流、Admin 插件页治理入口 | 后续仅在确有需要时再评估进程级隔离 |
| Phase 4 协议韧性 | 基本收口 | NapCat 只读健康和安全能力探测、OneBot 请求 echo 追踪、NapCat/LLOneBot 兼容检查清单、历史连接记录、mock 协议测试 | 后续按实际协议端差异补兼容项 |
| Phase 5 轻量语义增强 | 基本收口 | `SimilarityProvider`、默认 ngram、embedding 安全 stub、记忆检索语义兜底、语义后端健康指标、黑话质量守卫、系统页 semantic metrics、optional extra wiki | 真正的 embedding extra 仍放 v3.5，可按依赖策略再评估 |
| Phase 6 群 Profile | 已收口 | `GroupConfig` 扩展每群风格/工具/贴纸/黑话字段，新增保存/恢复 API，`LLMClient` / `StickerPlugin` / `SlangPlugin` 读取群策略，Groups 页抽屉改为模块化 Profile 编辑、群级工具矩阵、blocked_users 编辑器、群策略审计历史 | 后续按运营反馈细调分组和文案 |
| Phase 7 本地插件生态 | 基本收口 | manifest v2 起点、本地插件包索引、来源/清单/兼容检查、插件页本地包视图、未加载包治理队列、兼容告警 action hint、plugin.sig 签名/来源校验预留 | 后续只按真实插件分发需求再增强签名格式 |
| Phase 8 运维体验与发布治理 | 基本收口 | 重启按钮、配置变更审计、保存前 diff 预览、最近保存记录、配置回滚向导、基础备份、维护窗口、健康告警 | 后续按真实运维反馈微调文案与阈值 |

## 执行顺序

| 优先级 | 阶段 | 目标 | 验收点 |
| --- | --- | --- | --- |
| Archive | Phase 2 Provider 尾项 | 让模型 Provider 不只是可切换，而是真正可编辑与可治理 | 已满足：系统页可编辑 profile 定义、能力声明、任务映射，并保持旧配置兼容 |
| Archive | Phase 1 稳定性补强 | 让长期运行问题更容易定位 | 已满足：系统页可见 LLM、PluginBus、SQLite、Slang、Memory、NapCat、Runtime Errors 的服务级状态 |
| Archive | Phase 2 Provider 深化 | 让不同任务可用不同模型 | 已满足：thinker/slang/compact 可选择 profile，系统页可手动测试 profile 连通性 |
| Archive | Phase 4 协议韧性 | 降低 NapCat 断连/能力差异排查成本 | 已满足：协议能力矩阵、兼容清单、最近连接变化和安全探测结果均已上线 |
| Archive | Phase 6 群 Profile | 提升多群差异化运营能力 | 已满足：不同群可独立配置风格、主动插话、工具、表情、黑话策略 |
| Archive | Phase 5 语义增强正式版 | 在不加重默认 Docker 的前提下提升质量 | 已满足：默认 ngram 可用，embedding 未安装时安全降级，optional extra 文档已补齐 |
| Archive | Phase 7 本地插件生态 | 为实验和第三方插件留通道 | 已满足：只识别本地插件包，不允许 Web 远程下载执行 |
| Archive | Phase 8 运维体验 | 降低非开发用户维护成本 | 已满足：配置变更可审计、可回滚，健康告警可读 |

## Phase 3 详细拆解

| 子任务 | 状态 | 说明 |
| --- | --- | --- |
| 启停状态持久化 | 已完成 | 新增 `storage/plugins/plugin-state.json`，Admin 切换后写入，启动时回放 |
| 权限门禁 | 已完成 | 旧插件未声明 permissions 时兼容放行；manifest v2 显式声明后按 hook/tool/command/admin 权限收集 |
| 配置 schema 展示 | 已完成 | 插件详情抽屉展示 `settings_schema`，先只读展示，不做复杂编辑器 |
| 插件配置保存 | 已完成 | 已升级为 `storage/plugins/config/<name>.json` per-plugin JSON 覆盖文件，Admin 按 schema 生成控件并保存插件私有配置 |
| hook 耗时预算 | 已完成 | `hook_budget_ms` 可由插件类或 manifest 声明，超预算写入健康快照并标记慢调用 |
| 插件限流/隔离 | 已完成 | 已补错误/慢调用爆发后的短时冷却、Hook 抑制计数与系统/插件页可见状态，先用软隔离兜住异常插件 |

## Phase 2 详细拆解

| 子任务 | 状态 | 说明 |
| --- | --- | --- |
| `LLMProfile` 与旧配置兼容 | 已完成 | 旧 `llm.base_url/api_key/model/max_tokens` 自动映射为 `main` profile |
| Provider registry | 已完成 | Anthropic / OpenAI provider 统一走 `services/llm/provider.py` |
| 任务 profile 映射 | 已完成 | `main / thinker / compact / slang / vision` 可由 `llm.task_profiles` 指向不同 profile |
| 运行路径接入 | 已完成 | thinker、compact、黑话抽取分别走对应 profile；未配置时回退默认 |
| Admin Provider 概览 | 已完成 | 系统页展示 profile 列表、默认 profile 和任务矩阵 |
| profile 手动测试 | 已完成 | 新增 `POST /api/admin/providers/{name}/test` 与系统页测试按钮；只在点击时发起 |
| 分 profile rate limit | 已完成 | 按 profile 记录限流、冷却、快失败和成功/失败计数；辅助任务限流不会污染不同 profile 的主聊天 |
| profile 热切换 | 已完成 | 系统页保存默认 profile 与任务映射后，运行时 LLMClient 立即切换，并同步写入 `config/config.json` |
| profile 定义编辑器 | 已完成 | 系统页抽屉可新增/删除/编辑 profile，支持能力声明、API Key 保留/替换/清空，并在保存后自动清洗任务映射与刷新运行时 |

## Phase 4 详细拆解

| 子任务 | 状态 | 说明 |
| --- | --- | --- |
| NapCat 只读健康 | 已完成 | `/api/admin/protocol/health` 展示适配器、API URL、Bot 连接数 |
| 安全能力探测 | 已完成 | `/api/admin/protocol/probe` 只调用登录信息与群列表，不发送消息、不做破坏性动作 |
| OneBot 请求 echo/追踪 | 已完成 | 本地 `ob_*` 追踪号记录 action、耗时、成功/失败和错误摘要 |
| NapCat / LLOneBot 兼容清单 | 已完成 | 系统页展示协议能力兼容表，明确哪些能力只做手动确认 |
| 历史连接记录 | 已完成 | 新增协议连接历史 store，记录连接状态变化、断连时间、最近恢复时间和错误摘要，系统页协议卡展示 |
| mock 协议测试 | 已完成 | 用模拟 bot 覆盖健康、probe、trace、compatibility 的稳定契约，包括无 Bot、缺方法、失败响应与敏感参数脱敏 |

## Phase 5 详细拆解

| 子任务 | 状态 | 说明 |
| --- | --- | --- |
| `SimilarityProvider` 抽象 | 已完成 | 默认 `ngram` 可用，`embedding` 维持安全 stub，未安装不会直接破坏运行链 |
| 记忆检索接入语义后端 | 已完成 | `RetrievalGate` 在关键词未命中时追加轻量语义匹配，并支持 `memory.semantic.enabled/backend` 配置 |
| 安全降级与健康状态 | 已完成 | `embedding` 未安装时自动回退到 `ngram`，并在 `services/health` 的 Memory 项暴露后端、fallback、error 状态 |
| optional extra 文档 | 已完成 | wiki 新增轻量语义检索说明，明确默认轻量和 optional extra 路线 |
| 黑话常识对比增强 | 已完成 | 已新增共享质量守卫，统一过滤噪声 term、泛化释义和脏 alias，并接入 extractor / daily reviewer |
| semantic metrics 细化 | 已完成 | 系统页 Memory 服务卡现已显示 semantic backend、hits/queries、fallbacks、errors 和最近错误 |
| 可选 embedding 真实现 | 后续 | 保持 optional extra 路线，只有在明确接受重依赖后再补真实实现 |

## Phase 6 详细拆解

| 子任务 | 状态 | 说明 |
| --- | --- | --- |
| `GroupConfig` Profile 字段扩展 | 已完成 | 新增 `reply_style`、`custom_prompt`、`tools_enabled`、`sticker_mode`、`slang_enabled`，并接入 `resolve()` |
| Group Profile 保存 / 恢复 API | 已完成 | 新增 `POST/DELETE /api/admin/groups/{group_id}/profile`，兼容 JSON 主格式与 legacy TOML 读取，保存时自动回退与全局默认相同的字段 |
| 运行时热生效 | 已完成 | 群策略保存后立即更新运行时 `GroupConfig`，调度器、LLM、贴纸与黑话链路无需重启即可读取新值 |
| LLM / Sticker / Slang 接入 | 已完成 | `LLMClient` 注入每群回复偏好并按群过滤工具；贴纸与黑话插件按群开关和策略执行 |
| Groups Admin 模块化编辑 | 已完成 | 群详情抽屉新增结构化 Profile 表单、恢复全局默认和保存草稿动作，保留实时状态与最近消息视图 |
| 群级工具矩阵 | 已完成 | 已补按工具名的 allow/block 矩阵，运行时 `LLMClient` 会按群过滤实际可用工具，贴纸/黑话额外规则继续生效 |
| `blocked_users` 编辑器 | 已完成 | 已补当前群额外屏蔽用户的可视化编辑，保留全局屏蔽名单只读提示，避免误以为能在群级移除全局屏蔽 |
| 群策略审计历史 | 已完成 | 已新增 `storage/groups/group-profile-audit.json` 与群详情审计时间线，记录保存/恢复动作和字段差异摘要 |

## Phase 7 详细拆解

| 子任务 | 状态 | 说明 |
| --- | --- | --- |
| 本地插件包索引 | 已完成 | 新增本地插件索引服务，扫描 `plugins/` 下目录插件、manifest 与 JSON 配置；旧根目录单文件标为 blocked |
| 来源/清单检查 | 已完成 | 为每个插件输出 source status、manifest status、相对路径与 warning 列表，明确仓库内/外部/符号链接/缺失入口 |
| 兼容版本检查 | 已完成 | 基于 `min_omubot_version` 对当前版本做兼容判断，并在插件 API 与插件页展示 |
| 插件页本地包视图 | 已完成 | `/admin/plugins` 新增本地插件索引横条，详情抽屉新增来源、清单、兼容性与指纹信息 |
| 未加载本地包治理 | 已完成 | 插件页新增治理队列，集中展示未加载、待确认、阻塞和已加载但需关注的本地包，并给出 action hint |
| 兼容告警优化 | 已完成 | index API 新增 governance status / label / action hint 与聚合 summary，前端可直接收口排障队列 |
| 指纹 / 签名预留 | 已完成 | 新增可选 `plugin.sig` detached attestation，校验 entry / manifest SHA256 与来源声明，并在插件页展示签名状态 |

## Phase 8 详细拆解

| 子任务 | 状态 | 说明 |
| --- | --- | --- |
| 配置变更审计 | 已完成 | 新增 `storage/config/config-audit.json`，保存最近配置落盘摘要，敏感字段只保留遮罩后的 before/after 展示 |
| 保存前 diff 预览 | 已完成 | 新增 `/api/admin/config/preview`，基于服务端校验后的规范化结构返回变更摘要与字段差异 |
| 配置页审计面板 | 已完成 | `/admin/config` 新增“查看变更”按钮、变更预览面板和最近保存记录面板，保持原有结构化编辑骨架 |
| 回滚向导 | 已完成 | 新增 `/api/admin/config/backups` 与 `/api/admin/config/restore`，配置页可查看快照摘要、确认恢复并自动写入新审计记录 |
| 基础备份 | 已完成 | 新增 `services/config_backup.py` 与 `storage/config/config-backups.json`，保存与恢复都会生成可恢复快照且不在 Web 暴露 secret 明文 |
| 维护窗口提示 | 已完成 | `services/health` 新增维护窗口摘要与建议清单，系统页新增“运维建议”区，集中展示当前是否建议进入维护窗口 |
| 健康告警收口 | 已完成 | `services/health` 新增 alerts 摘要，系统页展示高优先级健康告警；`RestartBotButton` 统一改为更清晰的重启影响说明 |

## 风险控制

- 不改变 `AmadeusPlugin` 现有 hook 签名，避免插件 ABI 断裂。
- 权限门禁只对显式声明 permissions 的 manifest v2 插件严格执行；旧插件保持兼容。
- 运行时启停不承诺热卸载插件内部已启动的后台任务；需要硬重启的插件仍由重启按钮处理。
- 不在 Web 端远程下载安装插件，避免执行未知代码。
- 每个阶段都需要同步 `maintenance-log.md`，并至少跑对应后端测试与前端构建。
