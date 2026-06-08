# Claude Code 省 token：ENABLE_TOOL_SEARCH

> 类型：开发环境调优 / Claude Code 设置
> 日期：2026-06-08（headroom 考察的衍生产出）
> 一句话：你的 Claude Code 走 `ccapi.yuanapi.org` 中转 + 未设 `ENABLE_TOOL_SEARCH`，触发工具按需加载（deferral）被关闭，每会话每轮把全部工具定义一次性塞进 context，白白膨胀几十 K token。

---

## 一、根因（本机二进制逐函数确认）

从本机运行的 Claude Code 二进制（VS Code 扩展 2.1.168，`resources/native-binary/claude`）确认，决策函数 `nN()`：

```text
ENABLE_TOOL_SEARCH 未设  +  ANTHROPIC_BASE_URL 非 api.anthropic.com  →  deferral 关闭
```

- `T3()` 只认 `api.anthropic.com` 为第一方 host；`ccapi.yuanapi.org` 不是 → `!T3()` 为真。
- `ENABLE_TOOL_SEARCH` 当前为空。
- 两条件全中 → tool search 关闭 → 所有工具定义（含内置工具）一次性进 context。

这是 Claude Code 的**保守安全默认**：它不确定第三方中转是否原样转发 tool-search 协议（`tool_reference` 块 + beta 头），故对非官方 host 默认关掉。不是 bug，但对本机就是纯浪费。

deferral 开启时，工具定义延迟加载（模型按需通过 `tool_reference` 拉取），从"上来就占 context"里摘出去——这就是几十 K 差异来源。作用对象不止 MCP 工具，部分内置工具同样走 deferral。

## 二、已落地改动

`~/.claude/settings.json` 的 `env` 块新增（与 `ANTHROPIC_BASE_URL` 同级）：

```json
"ENABLE_TOOL_SEARCH": "true"
```

取值参考（来自二进制 `sb8()`/`ab8()`）：`true`=全开 deferral；`auto`=按 token 阈值自动（默认 10%）；`auto:N`=自定义阈值；`false`/`0`=显式关闭。

## 三、生效条件 + 实测步骤

**生效条件**：`ENABLE_TOOL_SEARCH` 是会话启动时读一次的环境变量。

- **必须重启 Claude Code（扩展/VS Code）后开一个全新对话**才生效。
- 继续/恢复旧对话不生效（仍带启动时的旧环境）。

**实测（新对话里做一次，确认中转支不支持 tool_reference）**：

1. 让 agent **Read** 一个文件（内置工具）
2. 让 agent **Bash** 一条命令（内置工具）
3. 让 agent 调一个 **MCP 工具**（如 anysearch 搜一下）

判读：

- 三个都正常 → 中转转发了 `tool_reference`，deferral 生效，省 token 成功，留着。
- 某个工具调不出/报错 → 中转剥了 beta 头，改回 `false`（或删掉那行）秒级回滚，无持久副作用。

**更快的硬验证**：重启后 `claude --debug` 启动，开关打开后日志里**不会再出现** `[ToolSearch:optimistic] disabled: ... is not a first-party Anthropic host`；若仍报 tool-search 相关错，即中转不认。

**回滚**：删掉 `ENABLE_TOOL_SEARCH` 行或改 `"false"`，重启即恢复原行为。无持久副作用。

## 四、其他可调项（二进制确认存在）

| 开关 | 作用 | 对本机判断 |
|------|------|------------|
| `MAX_MCP_OUTPUT_TOKENS` | 封顶单次 MCP 工具返回 | 挂了 anysearch，搜索返回常很大，值得设（如 `"1000"`） |
| `MAX_THINKING_TOKENS` | 封顶 extended thinking 预算 | thinking 也吃 token，可调小 |
| `CLAUDE_CODE_SIMPLE` | 跳过 CLAUDE.md/memory 加载 | ❌ 别用，本仓重度依赖 CLAUDE.md 规则，副作用太大 |

## 五、附带：磁盘（非 token）

`.workspace` 占 **8.4G**，其中 7.8G 是 2026-06-08 Docker.raw 迁移时的 `host-cache-offload-20260608`（uv/huggingface 缓存搬迁残留）。已 gitignore，不影响 token，但白占磁盘——可清。参见 [[project_docker_raw_migration]]。

## 六、与 headroom 考察的关系

这是 [headroom-eval-omubot-audit-2026-06-08.md](headroom-eval-omubot-audit-2026-06-08.md) 考察省 token 途径时的衍生发现。结论：**比 headroom 那套（wrap/proxy/有损压缩）安全得多、也更对症**——纯 Claude Code 原生开关，不碰 omubot 代码、不碰生产路径、秒级回滚。

## 七、工具调用偶发不落实 + agent 误读中间状态（根因修正）

> ⚠️ 本节经 2026-06-09 实测**推翻了初版归因**。初版一口咬定「中转把工具调用降级成纯文字」，证据不足、甩锅过满。下面是修正后的结论。

考察期间出现两个真实事件：(a) 一次 `Write` 回报成功但文件未落盘（用户侧看到纯文字）；(b) `git add` + `git commit` 分步执行时，连续多轮 commit 后 HEAD 不动。我一度把两者统一归因为「中转降级」，**这是错的**。

**实测证据**：把 `git add → git commit → git log` 塞进**同一条 bash 原子执行**后，一次成功（HEAD 从 `5aedc01` → `5970824`）。而之前分步失败时，`git commit` 的真实输出是 `no changes added to commit`——即**暂存区一直是空的**。真问题不在 commit，而在**前一步 `git add` 偶发没生效**，commit 正常退出（无内容可提交，HEAD 自然不动）。我却凭工具回执字面以为 add 成了，编出了「中转吞 commit」的故事。

修正后的现象归类：

| 现象 | 性质 | 真根因 | 置信度 |
| --- | --- | --- | --- |
| 分步写操作中间某步（如 `git add`）偶发不落实 | **真故障** | 多个独立工具调用串行时，中间步骤偶发未真正执行；agent 误读中间状态、凭回执字面判成功 | 高（同一条 bash 原子执行即稳定成功，证明 bash 链本身健康） |
| `Write` 偶发回报成功但未落盘 | 真故障（独立事件） | 同上：单次工具调用偶发未落实。**不应**泛化成「中转吞所有工具调用」 | 中（确有其事，但机制未坐实，不再归因到中转） |
| `File has not been read` / `modified since read` | 正常护栏 | context 自动 compact 清掉 in-context 文件读取状态，compact 后首次 Edit 必触发 | 确证 |
| `Wasted call — file unchanged` | 正常护栏 | 缓存去重，省 token | 确证 |

**关键结论**：大部分「工具失败」是 compact 后 harness 正常触发的安全护栏，不是 bug。真故障是**分步工具调用偶发丢失中间步骤 + agent 凭回执字面误判成功**——这是「不信回执、用外部状态自验证」（D4 同源）该解决的问题，**不再甩锅给中转**。endpoint（`ccapi.yuanapi.org` vs `api.anthropic.com`）是否相关未坐实，本节不再断言。

## 八、规则：有依赖的写操作合并原子执行 + 自验证

> 本次根因修正提炼出的可复用规则。已同步进 [CLAUDE.md](../../CLAUDE.md) 与 [AGENTS.md](../../AGENTS.md)。

1. **有依赖关系的 git/写操作合并成一条 bash 原子执行**：`git add X Y && git commit -m '…' && git log --oneline -1`。不要拆成多个独立工具调用——拆开时中间步骤偶发丢失，且 agent 难以察觉。
2. **同条命令内打印自验证证据**：commit 后立刻 `git log --oneline -1`、写文件后 `ls -l`/`wc -l`、`git diff --stat`。从同一份输出读真实结果，不读「工具回报成功」的字面。
3. **声明「已提交/已写入」前，必须有外部状态证据**（HEAD hash 真变、文件真在盘、暂存区符合预期），与 D4「完成声明含证据」一致。
4. **同一动作失败两次即停**，不重试第三次——换执行方式（合并成原子 bash）或上报，不要盯着回执反复试。
