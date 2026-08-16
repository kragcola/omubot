# Docker & Operations

## Docker / NapCat

- NapCat persists two directories: `./napcat/config` (config) and `./napcat/data` (QQ sessions/device fingerprint)
- Device fingerprint at `napcat/data/nt_qq/global/nt_data/mmkv/`, login tokens at `napcat/data/nt_qq/global/nt_data/Login/`
- **Always use `docker compose restart napcat`** — never `down` + `up` (device fingerprint changes trigger Tencent anti-fraud)
- Disconnections are usually Tencent anti-fraud, not persistence issues. Tokens are server-side invalidated; re-login required
- NapCat uses NTQQ protocol, supports concurrent mobile QQ sessions (multi-device)

## Building & Updating

`config/` is volume-mounted. Changes to config files take effect with a restart:

```bash
docker compose restart bot           # Config changes only
docker compose build bot             # Code/dependency/Dockerfile changes
docker compose up -d --no-deps --force-recreate bot
```

**Note**: `docker compose restart` does not rebuild images.
The bot-only recreate command must include `--no-deps`; otherwise Compose may
start or otherwise touch the `napcat` dependency.

The bot image uses a two-stage Docker build. `GIT_COMMIT` build arg is baked in and logged at startup for version identification.

## Bot-only deploy

There is no repository `scripts/deploy.sh`. Use the explicit, auditable path:

1. Run `git stash list`, `git status -uno`, and
   `git ls-files --others --exclude-standard`.
2. Before rebuilding the SPA, snapshot the current host `admin/static` under
   `.workspace/rollback/<deployment-id>/admin-static`; record its file count
   and SHA256 manifest.
3. Build the Admin SPA with `cd admin/frontend && npm run build`.
4. Tag the currently running bot image as the rollback image, and record the
   matching host-static snapshot path next to that tag.
5. Run `docker compose build bot`.
6. Run `docker compose up -d --no-deps --force-recreate bot`.
7. Verify bot health and confirm the NapCat container identity, `StartedAt`,
   and restart count are unchanged.

Rollback must restore both halves: replace host `admin/static` from the
recorded snapshot, retag the rollback bot image as `omubot-bot:latest`, then
run only `docker compose up -d --no-deps --force-recreate bot`.

## Storage Layout

```
storage/
├── usage.db                    # SQLite — LLM usage tracking
├── messages.db                 # SQLite — raw group message persistence
├── logs/
│   ├── bot_*.log               # Main bot logs (10MB rotation, 30 days retention)
│   └── dream_*.log             # Dream agent logs (separate sink)
├── memories/
│   ├── users/                  # Per-user .md memo files
│   ├── groups/                 # Per-group .md memo files
│   └── index.md                # Cross-reference index
├── image_cache/
│   └── {2-char-bucket}/        # Cached images (auto-cleanup on startup)
│       └── {file_id}.jpg
└── stickers/
    ├── index.json              # Sticker metadata & usage stats
    └── stk_{hash}.{ext}        # Sticker image files
```

## Key Dependencies

| Package | Purpose |
|---------|---------|
| `nonebot2[fastapi]` | Bot framework + HTTP server |
| `nonebot-adapter-onebot` | OneBot V11 protocol adapter |
| `aiohttp` | Anthropic API SSE streaming |
| `pydantic` | Config validation |
| `jsonschema` | Plugin ManifestV3 defaults/Admin override schema validation |
| `aiosqlite` | Usage tracking SQLite async |
| `rich` | Usage TUI dashboard |
| `pyvips` | Image downscaling (requires libvips system lib) |
| `ddgs` | Explicit DuckDuckGo compatibility backend; `auto` without a key uses bounded Bing RSS |
| `aiofiles` | Async file I/O |
| `tenacity` | Retry logic |
| `loguru` | Structured logging |
| `httpx` | HTTP client (NapCat API) |

## Usage Monitoring

**TUI**: `uv run python -m src.llm.usage_cli tui day|week|month [date]` — interactive Rich dashboard showing token usage, cache hit rates, call breakdowns.

**API**: when `llm.usage.enabled = true`, FastAPI routes are mounted:

| Endpoint | Description |
|----------|-------------|
| `GET /usage/summary/today` | Today's usage summary |
| `GET /usage/summary/month` | Current month's summary |
| `GET /usage/top-users` | Top users by token usage |
| `GET /usage/top-groups` | Top groups by token usage |
| `GET /usage/timeseries` | Hourly token breakdown |

**Alerts**: when enabled, bot PMs all configured `admins` when:
- Average cache hit rate drops below `compact.cache_hit_warn` % over a rolling window
- A single LLM call exceeds `llm.usage.slow_threshold_s` seconds
