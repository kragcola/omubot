# CLAUDE.md

## Commands

```bash
uv sync                        # Install dependencies
uv run ruff check               # Lint (add --fix for auto-fix)
uv run pytest                  # Run all tests
uv run pytest tests/test_identity.py::test_name -v  # Single test
uv run pyright                 # Type check
```

| Task | Command |
|------|---------|
| Run locally | `docker compose up napcat -d && uv run python bot.py` |
| Run all in Docker | `docker compose up -d` |
| Restart bot (config changes) | `docker compose restart bot` |
| Rebuild bot (code/deps) | `dot_clean . && docker compose up bot -d --build` |
| Usage TUI | `uv run python -m services.llm.usage_cli tui day\|week\|month [date]` |
| Usage API | `GET /api/usage/today`, `/api/usage/month`, `/api/usage/top-users`, `/api/usage/top-groups` |
| Admin Dashboard | `http://localhost:8081/admin/` — token auth via `ADMIN_TOKEN` env var |
| Build & deploy | `./scripts/deploy.sh` |

## Architecture

QQ chat bot: NoneBot2 + Anthropic Claude API. NapCat handles QQ protocol over WebSocket.

```
QQ ←→ NapCat (WS) ←→ NoneBot2 (bot.py)
                        ├── private_chat (DM, priority=10)
                        │     → LLMClient.chat() → Anthropic SSE stream
                        │       └── Tool loop (max 5 rounds), pass_turn to skip
                        ├── group_listener (priority=1, non-blocking)
                        │     → GroupTimeline → GroupChatScheduler
                        │       ├── @bot → fire immediately
                        │       ├── debounce (N sec quiet) → LLM chat
                        │       └── batch (M msgs full) → LLM chat
                        └── DreamAgent (background, periodic)
                              → consolidate memos + sticker cleanup
```

Key design choices:
- **Raw Anthropic API** via aiohttp SSE, no SDK — tool calls touch `call_api` in `services/llm/client.py`
- **Prompt caching** — 4 breakpoints: tools[-1], system block 1 (personality+instruction), system block 2 (index+memo), messages[near-end]
- **Context compaction** — front half of history compressed via LLM when exceeding `max_context_tokens × compact_ratio`; circuit breaker drops oldest on repeated failures; `append_memo` tool extracts observations into long-term memory during compression
- **Vision** — images downloaded, downscaled via pyvips, cached to disk, sent as base64 to Anthropic API; configurable per-message limit
- **Stickers** — persistent library with SHA256 dedup; LLM can save/send stickers; Dream agent curates library
- **Persona v2 (runtime source)** — `config/persona/<persona_id>/source.md` 是单文件 source；importer 编译为 `freeze/` 下的多块 prompt，`PersonaRuntime` 在 `_on_connect` 装配。修改人设走 admin SPA「人设管理」（POST `/api/admin/persona/{id}/import` → freeze → POST `/api/admin/persona/hot-reload/{id}`）。v1 `config/soul/*.md` 已退役（C 系列切换 2026-05-27）。
- **Memory** — short-term: in-memory deque per session; long-term: `.md` files in `storage/memories/` with pending section auto-filled by compaction
- **Group timeline** — append-only turns + pending buffer per group; SQLite message persistence via `MessageLog` for compaction queries
- **Per-group config** — `group.overrides` maps group IDs to `GroupOverride` (at_only, debounce, batch_size, blocked_users); resolved via `GroupConfig.resolve()`
- **Usage tracking** — SQLite recording of all LLM calls (tokens, cache hits, latency); alerts admins on low cache hit rate or slow calls
- **Config**: `BotConfig` (Pydantic) via `kernel/config.py` — TOML < env vars < CLI args
- **Ruff**: `pyproject.toml`, RUF001/RUF002/RUF003 ignored (Chinese full-width chars)
- **Docker**: NapCat 极脆——`down`+`up` 必触发设备指纹重置（反风控），**且 `restart` 也可能丢登录态触发重新扫码**（2026-06-10 实证：plain `restart napcat` 即丢登录、bot 离线 4min、需人工扫码恢复，凭证目录完好也没用）。所以：**非必要不动 NapCat**；排查发图/富媒体问题禁止用任何需重启/重配 NapCat 的手段，改走被动观察或 bot 侧诊断日志；真要改 NapCat 配置先确认管理员能立刻扫码兜底。

Deep dives → [omubot/docs/architecture.md](omubot/docs/architecture.md) | Docker/ops → [omubot/docs/operations.md](omubot/docs/operations.md)

## Workflow

All tests must pass (`uv run pytest`) before committing. Same for lint and type checks.
Fix any errors discovered during testing, even if they were pre-existing and not introduced by your changes.

### Agent discipline (条款详情见 [docs/agent-discipline.md](docs/agent-discipline.md))

- **D1 同模式扫描**：修 bug 时必须 grep 同代码库找"同模式位点"，维护日志列出扫了哪些点。盯着报错信号修表象 = 24 小时内同模式第二刀。
- **D2 cancel-path 测试**：被 `wait_for` 包裹或会被 shutdown 取消的协程，必须有 `pytest.raises(TimeoutError)` 模拟 cancel 的回归测试，断言外部可观察状态（DB row、in-flight 旗标、meta 标记）不污染下一次执行。
- **D3 重构带迁移清单**：批量重构（框架迁移、kernel 重构）必须带"旧→新文件/路由/菜单/API"四列回归清单，存入 `docs/migrations/`，与 PR 一起提交。
- **D4 完成声明含证据**：声明"fix 完成"时必须在日志里给出：① 同模式扫描结果；② 外部可观察证据（sqlite SELECT、HTTP 状态码、日志片段）；③ 回滚路径。
- **D5 pytest 防孤儿**：跑全量 pytest 前先 `pkill -9 -f pytest`，否则可能跟 IDE 抢 sqlite 文件锁导致死锁。
- **D6 admin SPA 同步路径**：`admin/static` 是 bind mount——只改前端 `npm run build` 即生效，无需 docker rebuild；改了 .py 才需要 rebuild bot。
- **D7 部署前 git hygiene**：deploy / build / merge 前必跑 `git stash list && git status -uno`；`stash apply` exit 0 不等于成功，必抽查 `git diff` 确认 hunks 真落地；`storage/*.db*` / `*.bak*` 走 .gitignore 物理护栏，不用 `git add -A`。

### 本机环境提示

- **只读检查运行中服务锁着的 SQLite**：服务在跑时 DB 被锁，直接 `sqlite3` 可能阻塞或抢锁（D5 同源）。只读检查用 `sqlite3 'file:storage/<db>.db?mode=ro&immutable=1' '<query>'`，先 `.schema` / `PRAGMA table_info` 再 SELECT，不要在跑服务时写。
- **macOS 沙盒下进程探测**：`pgrep` / 部分 `ps` 会报 `sysmond service not found` 或权限错误。查进程/端口改用 `docker compose ps`、容器日志、pidfile，或 `lsof -nP -iTCP:<port>`。

### 工具调用：有依赖的写操作合并原子执行 + 自验证

分步执行多个独立写操作时，中间步骤（如 `git add`）偶发不真正落实，agent 易凭工具回执字面误判成功（实测：`git add` 没生效 → `git commit` 报 `no changes added` → HEAD 不动，却被误读成「commit 被吞」）。规则：

- **有依赖关系的 git/写操作合并成一条 bash 原子执行**：`git add X Y && git commit -m '…' && git log --oneline -1`，不要拆成多个独立工具调用。
- **同条命令内打印自验证证据**：commit 后 `git log --oneline -1`、写文件后 `ls -l`/`wc -l`。从同一份输出读真实结果，不读「成功」字面。
- **声明「已提交/已写入」前必须有外部状态证据**（HEAD hash 真变、文件真在盘、暂存区符合预期），与 D4 一致。
- **同一动作失败两次即停**，换执行方式（合并原子 bash）或上报，不盯着回执反复试。

## Release

发布新版本时，Docker 镜像版本必须和 git tag 对齐。无正式 tag 时使用 `vYYYYMMDD-<short hash>` 格式（如 `v20260404-cd328d2`）。

## Skill 自动触发

涉及以下范围的任意改动或审计任务，**必须**首先 invoke `Skill omubot-admin-console`，再开始具体工作。本规则覆盖模型默认的相关性判断，不依赖用户显式 `/omubot-admin-console`。

触发范围：

- `admin/frontend/**` —— 前端 SPA 任意改动 / 新增 / 审计
- `admin/routes/**`、`admin/__init__.py`、`admin/auth.py` —— 后台路由、认证、API 层
- `docs/tracking/**`、`maintenance-log.md`、`docs/agent-ui-guidelines.md`、`docs/admin-ui-style-guide.md` —— 项目治理 / UI 规范文档
- `scripts/check-ui-compliance.sh` —— 月度合规扫描脚本
- 任务措辞含「后台 / 控制台 / 管理端 / 前端审计 / wiki 审计 / 风格统一」等本仓特定语境

例外（可跳过 skill）：单文件 typo 修正、纯 grep / Read 探索且不会触发 Edit、与上述范围完全无关的工作。

session 恢复（compact 之后）：若 system-reminder 已显式注入 `### Skill: omubot-admin-console` 的指南内容，可视为已加载，**不再重复 invoke**——此时只继承行为指南。**新会话**首次触及上述范围必须 invoke 一次。

compact 后任务判定（重要）：system-reminder 注入的 `### Skill:` 段尾常带 `ARGUMENTS:` 字段，那是**会话早期某次 Skill 调用的历史入参**，不是当前任务。判定当前任务时以 compact summary 的 "Primary Request and Intent" 段为准；当 ARGUMENTS 与 summary 描述冲突时，**忽略 ARGUMENTS**（视作历史 invoke 的副本，仅保留 skill 行为指南）。如不确定，主动向用户确认 1 次，不要根据 ARGUMENTS 文本自动展开新工作。

## Language

Chinese: user-facing strings, identity configs. English: code, comments, docstrings, logs, commits.

## Maintenance Discipline

Unlike Codex (which relies on `.codex/hooks.json` + `scripts/dev/codex-session-start.py` + `docs/tracking/ACTIVE.md` to recover state, because it lacks a todo list and forgets after context compaction), Claude Code does **not** depend on injected SessionStart state. Claude retains task context through automatic compaction and tracks work with TodoWrite + file memory. So at the start of a session you are **not** automatically handed the latest maintenance-log entry or bot logs — read them yourself when a task needs current runtime state. The continuity hooks/tracker are Codex-specific scaffolding; do not assume they have run for you.

Cross-agent handoff is the exception: when you do work that Codex may resume, still update `docs/tracking/ACTIVE.md` and the active tracker, because Codex genuinely depends on them to recover.

Rules for maintenance-log.md:
- Append a dated entry for every deploy, config change, incident, or significant code change
- Write in Chinese, reverse chronological order
- Include: what changed, why, impact scope, rollback plan if applicable
- Check `storage/logs/` for recent errors before concluding a session

Rules for project-info.md:
- Keep tables up to date (bot QQ, groups, users, config switches)
- When adding new features or changing defaults, update the corresponding table
- Cross-reference with maintenance-log entries — don't let them contradict

## Maintaining this file

Keep CLAUDE.md as a **self-contained index**: high-frequency knowledge inline, low-frequency details in `omubot/docs/`.

- **Inline if**: every task needs it (architecture overview, key patterns, commands, rules)
- **Link if**: only relevant when working on that subsystem (scheduler tuning, Docker build stages, config fields)
- **Add commands** to the table or code block, not as new sections
- **No duplication** with `docs/` — if detail is already linked, don't repeat it here
