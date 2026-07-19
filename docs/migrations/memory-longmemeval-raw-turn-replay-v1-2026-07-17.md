# Memory LongMemEval Raw-Turn Replay v1

## Status

- Offline/manual acceptance only.
- No production wiring, schema migration, deployment, dataset download, or CI enrollment.
- This is not LongMemEval leaderboard parity and does not score memory extraction or answer generation.

## Upstream Contract

The replay is pinned to `xiaowu0162/LongMemEval` commit
`9e0b455f4ef0e2ab8f2e582289761153549043fc` (MIT). The pinned README defines
500 cases, `_abs` abstention IDs, parallel haystack session/date/content arrays,
optional turn-level `has_answer`, and session-level `answer_session_ids`.

The metric implementation follows pinned `src/retrieval/eval_utils.py`, including
its exact `log2(2..n)` discount after rank 1. This is deliberately not replaced
with a different standard-library or sklearn nDCG variant.

The parser also enforces the official six-value `question_type` set, required
non-empty `answer` / `question_date`, `user|assistant` turn roles, and non-empty
haystack/session-turn containers. The answer and question date are validated
then discarded because this tool does not score answer generation.

LongMemEval-V2 and LoCoMo remain comparison inputs only. Their full readers,
screenshots/GPU dependencies, LLM judges, and licensing/distribution constraints
are not added to this repository or regular CI.

## Replay Boundary

```text
user-supplied local JSON
  -> strict LongMemEval v1 parser
  -> per-case temporary CardStore
  -> MemoryContextSource
  -> RetrievalGate
  -> ContextService search + evidence gate + pack
  -> recall_any / recall_all / nDCG report
  -> temporary database cleanup
```

Each case gets a SHA-256-derived user scope. Every turn gets deterministic
`turn_ref` and `source_message_id` provenance. The full replay representation
retains `[date] role: content`; the CardStore search projection is
`[date] content`, so adapter-added `user`/`assistant` labels do not create
artificial keyword matches. The official question itself is passed unchanged.

The replay intentionally uses an empty online `session_id`. A synthetic new
session would trigger `full_new_session` and bypass query relevance, which is
not the retrieval behavior this offline question replay is measuring.

## Strict Input Rules

- JSON root and every case/turn have the required list/object shape.
- `question_id`, `question_type`, `question`, session IDs/dates, roles, and
  contents are non-empty strings.
- Question IDs are unique across the file; session IDs and answer session IDs
  are unique within a case.
- Haystack session IDs, dates, and session arrays have equal lengths.
- `has_answer` is absent or a real boolean; string booleans are rejected.
- Every `answer_session_id` references a haystack session.
- Invalid `limit`, `top_k`, and `max_chars` fail even when no case is selected.

## Report and Privacy Contract

Reports contain hashes, opaque case/session/turn identifiers, counts, and
metrics, but no question, answer, turn content, packed text, or parser message.
CLI failures expose only `replay_failed:<ExceptionType>`.

External question/session/turn identifiers are SHA-256-derived opaque report
IDs. Raw dataset identifiers remain internal to the one-case replay and are not
serialized, so a locally supplied human-readable or PII-bearing ID cannot leak
through the success report. The CLI temporarily disables `services.context`
and `services.memory` Loguru namespaces while replaying, then restores them in
`finally`, so DEBUG query/keyword logs cannot bypass the JSON privacy boundary.

The following flags are mandatory:

- `benchmark_parity=false`
- `memory_extraction_scored=false`
- `answer_generation_scored=false`
- `deterministic_provenance=true`
- `ranking_tie_break_deterministic=false`
- `upstream_turn_to_session_equivalent=false`
- `session_ranking_scope=packed_unique_sessions_at_turn_top_k`
- `turn_ranking_scope=packed_turns_at_turn_top_k`

The tie-break flag is important: production CardStore IDs are random. Equal-score
keyword hits can therefore change relative order across fresh replay databases.
The replay does not post-sort or rewrite production ranking to improve nDCG.
Generic English question words can expose this behavior; treat it as a measured
retrieval limitation, not a harness failure.

The replay uses the pinned recall/nDCG formula for both packed turn refs and the
order-preserving unique sessions represented in the final pack. It does **not**
claim equivalence to upstream `evaluate_retrieval_turn2session`, which may extend
the turn retrieval window until it has `k` unique sessions; pack truncation means
that wider ranking is intentionally unavailable here. Duplicate ranked refs are
rejected fail-closed so malformed provenance cannot inflate nDCG above 1.

## Usage

The tool never downloads data and never opens production storage:

```bash
source ./scripts/dev/env.sh
uv run python tools/run_longmemeval_replay.py \
  /absolute/path/to/longmemeval_s_cleaned.json \
  --limit 20 \
  --top-k 10 \
  --max-chars 2400 \
  --output /absolute/path/to/report.json
```

Full LongMemEval S/M execution remains a manual capacity job. It is not suitable
for regular CI: the released corpus is large, full benchmark parity also needs
the official extraction/reader setup, and answer correctness uses a separate
LLM-based evaluation path.

## Verification

- Initial replay focused: `32 passed`.
- Independent review correction RED: `11 failed / 31 passed` across strict
  official fields/enums, duplicate-ranked nDCG, opaque report IDs, and explicit
  session metric scope.
- Corrected replay focused: `42 passed`.
- Replay + CardStore/RetrievalGate/ContextService/pack contracts: `235 passed`.
- Full repository after correction: `4785 passed / 17 skipped / 186 warnings`.
- Initial independent Grok review: `ACCEPT` with four Important findings; I1/I3
  scorer-scope honesty, I4 adversarial coverage, and I2 CLI DEBUG query leakage
  were incorporated into the correction package.
- Post-fix Grok review `cf7d58ae-f213-4321-9ed8-c1aeadfc5d42`:
  `ACCEPT`, `0 Critical / 0 Important / 3 Minor`, `grok-exit: 0`; I1-I4 all
  closed. The stale pre-correction full count noted during review was removed;
  optional exact opaque-ID shape assertion and library-call logging behavior
  remain non-blocking because the privacy contract is the CLI/report boundary.
- Scoped Ruff: clean.
- Scoped Pyright: `0 errors / 0 warnings`.
- Cancellation tests cover both an externally cancelled search and cancellation
  after CardStore has opened during `init`; connections and temporary databases
  are cleaned before cancellation propagates.

## Rollback

No runtime flag or database rollback is required because nothing is wired into
the bot. Remove `services/context/official_replay.py`,
`tools/run_longmemeval_replay.py`, its focused test, and this document to remove
the offline tool. Never touch NapCat, live SQLite files, or
`BUILTIN_WIRE_PROFILE.validated` for this rollback.
