# QZone sanitized fixture — offline conformance runbook

> Offline only. No NapCat, no production credentials, no Tencent network.
> This harness does **not** authorize live publish. `BUILTIN_WIRE_PROFILE`
> stays `validated=False`. A passing synthetic fixture is **not** live-release evidence.

## What this harness is

| Piece | Path | Role |
| --- | --- | --- |
| Contract + validator | `plugins/qzone_journal/fixture_conformance.py` | Schema, secret scan, fingerprint match, parser check, advisory report |
| CLI | `tools/verify_qzone_fixture.py` | Load one fixture → offline report JSON |
| Synthetic template | `tests/fixtures/qzone_journal/synthetic_publish_json_v1.json` | Mechanics only (`origin: synthetic`) |
| Tests | `tests/test_qzone_journal_fixture_conformance.py` | RED/GREEN contracts |

**Not in scope here:** capturing real cookies, setting `validated=True`, creating a production wire profile, or one-message canary.

## Fixture schema (version 1)

Top-level keys only:

- `schema_version` (int, must be `1`)
- `origin`: `synthetic` | `real_sanitized`
- `note` (optional string)
- `request_fingerprint` — **names/structure only**, never values
- `response` — `status_code`, allowlisted headers (`content-type` only), UTF-8 `body`
- `expected` — `status` (`published` / `failed` / `ambiguous`) plus optional safe `remote_id` / `reason`

Request fingerprint fields:

```text
method POST
scheme https
host   (e.g. user.qzone.qq.com)
path   (absolute path)
query_field_names   sorted unique names only (e.g. g_tk)
form_field_names    sorted unique names only (e.g. con, format, hostuin, …)
header_names        sorted unique names only (Cookie, Origin, Referer)
follow_redirects    false
```

Literal field **names** such as `g_tk` or `Cookie` are allowed in name lists.
**Values** (cookies, tokens, form text, raw request dumps) are rejected.

Body size cap matches the parser (`64 KiB`). File size hard cap is `128 KiB`.

## How to produce a genuine sanitized fixture (manual, human-operated)

Do this only with an account you control, outside the bot process, and only when
you intend to later create a **separate** validated profile (not this packet).

1. Capture one real publish CGI exchange (browser devtools or a controlled
   offline proxy you already trust). Prefer a failed/ambiguous path first if
   you are unsure about content.
2. **Immediately redact** before the file leaves your machine:
   - Strip all `Cookie` / `Set-Cookie` / `Authorization` **values**.
   - Strip `p_skey`, `skey`, `g_tk` **values**, tokens, and any UIN you do not
     want in-repo (hostuin values must not appear in the fixture at all —
     only the field **name** `hostuin` in `form_field_names`).
   - Remove request body form values (`con` text, etc.). Keep only field names
     in `request_fingerprint`.
   - Keep response body only if it is a short CGI JSON/JSONP envelope without
     secrets; still run the verifier’s secret scan.
3. Build JSON with `origin: "real_sanitized"` and `schema_version: 1`.
4. Set `expected` from what the strict parser should report
   (`published` + safe `remote_id`, or `failed`/`ambiguous` + reason code).
5. Store the file **outside** git until review (or under a local untracked path).
   Never commit a fixture that claims `real_sanitized` unless it is a genuine
   sanitized capture you verified by hand.
6. Run the verifier (below). Treat `profile_creation_eligible: true` as
   **advisory only** — it does not create or flip any wire profile.

Checked-in examples under `tests/fixtures/qzone_journal/` must stay
`origin: "synthetic"` and clearly labelled as templates.

## How to run the offline verifier

```bash
cd /Volumes/OmubotDisk/omubot
source ./scripts/dev/env.sh
export PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-}

# Synthetic template (expected: ok=true, profile_creation_eligible=false)
uv run python tools/verify_qzone_fixture.py \
  tests/fixtures/qzone_journal/synthetic_publish_json_v1.json

# Your local sanitized capture (path is local; do not commit secrets)
uv run python tools/verify_qzone_fixture.py /path/to/real_sanitized_v1.json
```

Exit codes:

- `0` — schema valid and conformance ok
- `1` — invalid fixture or non-conforming (request drift / parser mismatch)
- `2` — missing file / usage

Report shape (secret-free; no body):

```json
{
  "expected_status": "published",
  "fixture_origin": "synthetic",
  "ok": true,
  "parser_status": "published",
  "profile_creation_eligible": false,
  "reasons": [],
  "request_match": true,
  "response_match": true
}
```

## Interpreting `profile_creation_eligible`

| Condition | eligible |
| --- | --- |
| `origin=synthetic` and all checks pass | **false** (mechanics only) |
| `origin=real_sanitized` and all checks pass | **true** (advisory) |
| Any structural / request / parser failure | **false** |

Eligible **never**:

- mutates `WireProfile`
- sets `BUILTIN_WIRE_PROFILE.validated = true`
- creates a validated profile object

Creating a separate validated profile remains a **later, explicitly authorized**
step after real fixture evidence exists.

## Focused tests

```bash
source ./scripts/dev/env.sh
export PYTHONPATH=/tmp/omubot_pytest_stubs:${PYTHONPATH:-}
uv run pytest tests/test_qzone_journal_fixture_conformance.py -q
```

## Remaining genuine blockers (unchanged by this harness)

1. A real, human-sanitized CGI fixture + offline conformance against it.
2. A **new** wire profile with `validated=true` (never flip the built-in).
3. Separately authorized one-message canary with UIN allowlist and daily caps.

Until those three complete, `allow_live_publish=true` still cannot successfully
publish on the built-in unverified profile — fail-closed by design.
