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
