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
- **Soul directory** — `config/soul/identity.md` (persona, `## 插话方式` section for proactive rules), `config/soul/instruction.md` (behavioral directives)
- **Memory** — short-term: in-memory deque per session; long-term: `.md` files in `storage/memories/` with pending section auto-filled by compaction
- **Group timeline** — append-only turns + pending buffer per group; SQLite message persistence via `MessageLog` for compaction queries
- **Per-group config** — `group.overrides` maps group IDs to `GroupOverride` (at_only, debounce, batch_size, blocked_users); resolved via `GroupConfig.resolve()`
- **Usage tracking** — SQLite recording of all LLM calls (tokens, cache hits, latency); alerts admins on low cache hit rate or slow calls
- **Config**: `BotConfig` (Pydantic) via `kernel/config.py` — TOML < env vars < CLI args
- **Ruff**: `pyproject.toml`, RUF001/RUF002/RUF003 ignored (Chinese full-width chars)
- **Docker**: **always `docker compose restart napcat`**, never `down`+`up` (device fingerprint → anti-fraud)

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

## Release

发布新版本时，Docker 镜像版本必须和 git tag 对齐。无正式 tag 时使用 `vYYYYMMDD-<short hash>` 格式（如 `v20260404-cd328d2`）。

## Language

Chinese: user-facing strings, identity configs. English: code, comments, docstrings, logs, commits.

## Maintenance Discipline

This project has automated hooks in `.claude/settings.json`:

- **SessionStart**: injects latest maintenance-log entry + bot logs into context — you'll see current state at the start of every session
- **PostToolUse on Write/Edit**: when you edit `docs/` or `config/`, you'll get a reminder to update the maintenance log

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
