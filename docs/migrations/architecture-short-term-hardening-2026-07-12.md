# 短期架构加固迁移清单

> 状态：已完成并部署（2026-07-12）。对应 `docs/tracking/architecture-short-term-hardening-2026-07-12.md`。
> 原则：保持模块化单体；每个切片独立 RED-GREEN、独立回退；旧契约在迁移期继续兼容；不触碰 NapCat。

## 1. 旧到新迁移矩阵

| ID | 旧行为 | 新行为 | 兼容策略 | 回归与回滚 |
| --- | --- | --- | --- | --- |
| S1-1 | PluginBus 直接等待 hook；预算只在返回后统计 | 按 hook/插件预算执行硬 deadline | 沿用现有 `hook_budget_ms` 与 health payload | timeout/取消/soft-isolation 测试；恢复 `_safe_call` 旧 await |
| S1-2 | ToolRegistry 直接等待工具执行 | 工具执行 deadline，外部取消继续传播 | `call()` 返回类型与普通错误文案保持兼容 | 工具 timeout/cancel 测试；恢复旧 await |
| S1-3 | 同名工具后注册静默覆盖 | 冲突 fail-closed 或显式拒绝 | 不改变无冲突工具注册 | duplicate 测试；恢复 dict 赋值 |
| S1-4 | `dependencies` 缺失/禁用/版本不符只 warning 后跳边 | required fail-closed，optional 可降级 | 旧 `dependencies` 作为 required 兼容输入 | 缺失/禁用/版本/optional 测试；恢复旧拓扑逻辑 |
| S2-1 | 非 locked 插件均可由 state API 立即切 enabled | 仅真实 hot-safe 插件 runtime；其余 restart_required | runtime 工具候选完整验证后原子替换，失败恢复全 Bus 状态 | API/manifest/依赖闭包回滚测试；恢复 toggle policy 与 state API |
| S3-1 | 回复阶段靠跨对象字段和调用顺序隐式传递 | `ReplyRun` 记录输入、决策、生成、后处理、交付阶段 | 旧方法签名保留；新增可选 run 参数或 adapter | 普通/主动路径输出等价；移除 adapter 即回退 |
| S4-1 | `PluginContext` 持续增加独立 `Any` 字段 | 新能力通过 typed capability bundle | 旧 94 字段保持，不做一次性迁移 | contract Pyright 0；删除 bundle 接线即回退 |
| S5-1 | Admin/脚本分别推断配置有效值 | `EffectiveConfigSnapshot` 返回值、来源、apply mode | 原 config load/save 完全不变；只新增只读视图 | 来源/secret/API 测试；移除 snapshot route 即回退 |

## 2. 顺序与门禁

1. S1 先落地：它给后续插件和新 pipeline 提供故障边界。
2. S2 在 S1 后落地：避免 state API 与新的依赖/超时语义冲突。
3. S3 先定义纯类型和阶段记录，再接普通回复，最后接主动回复。
4. S4 只为新增能力提供 bundle，不迁移无关旧字段。
5. S5 可与 S1 并行，但最终 API 接线需与 S2 的 apply mode 对账。

每步进入下一步前必须满足：

- 定向测试 GREEN。
- 相关既有回归 GREEN。
- Ruff 与 targeted Pyright 通过。
- `git diff --check` 通过。
- tracker Test Ledger 已记录实际命令和结果。

## 3. 取消与副作用清单

### Hook / Tool

- 调用方取消必须传播 `CancelledError`，不能转换成 timeout 或普通插件错误。
- deadline 取消后不得继续修改插件 health 以外的业务状态。
- 工具若已产生外部副作用，timeout 不能谎称回滚；工具契约需区分可取消与不可取消操作。

### ReplyRun

- 创建/阶段记录不得改变回复概率、Prompt 文本、工具选择、发送内容或节奏。
- 普通与主动回复共享 contract，但不强行合并两者的业务策略。
- 生成中取消不得提交 delivery success 或 post-reply side effects。

### Effective Config

- snapshot 不写配置、不触发热应用、不更改插件状态。
- secret 只暴露 masked/present，不返回明文。
- 缺少某一来源时明确标记 absent，不伪造默认来源。

## 4. 同模式扫描

- PluginBus：扫描 startup/shutdown/connect/message/prompt/reply/tick 全部 `_safe_call` 调用。
- ToolRegistry：扫描所有 `register()`、`call()`、`clear()+collect_tools()` 路径。
- Plugin toggle：扫描所有 `plugin.json` 的 `toggle_policy`、`config.apply_mode` 与后台任务创建点。
- ReplyRun：扫描 Router 直接回复、Scheduler 主动回复、light reply、streaming/segmentation 与工具可见输出。
- Effective config：扫描主 config、环境覆盖、`group-policy.json`、plugin state/config override 和 Admin config/provider API。

## 5. 不迁移

- 不迁移数据库到 PostgreSQL。
- 不拆服务进程，不引入队列或 IoC 容器。
- 不统一重写 Router/Scheduler/LLMClient。
- 不迁移 OneBot/CQ 协议层。
- 不修改话题块研究采集、Persona、Climate 或学习产品行为。

## 6. 运行态与回滚

- 代码变更需运行态验证时，只重建 `qq-bot`。
- 部署前记录 qq-bot/NapCat container identity；部署后确认 NapCat ID、Created、restart count 不变。
- 任一切片回归失败时只回退该切片，不连带删除其他已验证 contract。
- S3/S4 双轨迁移期间，移除新 adapter 后旧路径必须立即可用。
