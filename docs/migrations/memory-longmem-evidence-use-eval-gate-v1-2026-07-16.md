# memory-longmem-evidence-use-eval-gate-v1 migration / acceptance

Date: 2026-07-16
Slice: `memory-longmem-evidence-use-eval-gate-v1`
Status: code accepted offline (not deployed; no production runtime change)

## Intent

Extend the existing offline ContextService eval runner so CI can jointly score:

1. **Ordinary active-only hits** (required/forbidden/duplicate/pack/hit budgets — unchanged semantics).
2. **Structured Temporal Trace evidence-use** (present/absent, reason, current / earlier / trajectory, typed evidence refs, KP/char budgets).

This maps LongMemEval-V2 **premise / temporal / evidence-use** and MemTrace **current / earlier / trajectory** *capability dimensions* onto Omubot contracts. It is **not** official dataset parity, not a public leaderboard, and not answer-generation judging.

## Old → New

| Area | Old | New | Compat |
| --- | --- | --- | --- |
| Case schema | `ContextEvalCase` hits/budgets only | + `current_message`, `rewritten_query`, optional `trace: TemporalTraceExpectation` | Additive; legacy fixtures omit new keys |
| Trace expectation | (none) | present/absent, exact reason, required/forbidden substrings on structured fields, evidence ref exact/prefix, max KPs/chars | Optional; `is_active()` decides scoring |
| Result | hit metrics + error | + `trace_checked`, `trace_present`, `trace_reason`, `trace_chars`, `trace_violations`, `missing_evidence_use` | Old keys preserved; new keys only added |
| Summary | aggregate hit metrics | + `trace_violations` count | Old keys preserved |
| Runner API | `evaluate_context_case(service, case)` | + optional `trace_assembler=` | Default `None` preserves prior behavior |
| Fixture | basic / owner / long_memory_frontier | + `tests/fixtures/context_eval/long_memory_evidence_use_v1.json` | Existing fixtures still green |
| Production retrieval | ContextService / RRF / RetrievalGate / TemporalTraceAssembler | **Unchanged** | Eval-only surface |

## API contract (frozen)

### `TemporalTraceExpectation`

- `expected_present: bool | None`
- `reason: str` (non-empty means a trace is required, then exact match)
- `required_*_contains` / `forbidden_*_contains` for: `current`, `earlier`, `trajectory`, `text`
- `required_evidence_refs` / `forbidden_evidence_refs` (exact)
- `required_evidence_ref_prefixes` / `forbidden_evidence_ref_prefixes`
- `max_knowledge_points` / `max_trace_chars`

**Scoring rule:** current / earlier / trajectory / evidence_refs are read from `TemporalTrace.knowledge_points[*]` structured fields (dataclass or dict). Required KP-scoped fields must co-locate in one knowledge point; unrelated KPs cannot jointly satisfy one expected trajectory. Rendered `text` is only checked via explicit `*_text_contains` needles — never parsed to invent dimensions.

**Fixture schema:** `expected_present` accepts only a real JSON boolean/null; string-list fields accept only JSON arrays of strings; budgets accept only non-negative integer/null; unknown trace keys and non-object `trace` values fail at load time. `expected_present=false` cannot be combined with `reason` or required evidence fields.

### Fail-closed rules

| Condition | Outcome |
| --- | --- |
| Case has active `trace` expectation, `trace_assembler is None` | Fail; `error=missing_trace_assembler`; violation + missing_evidence_use |
| Assembler raises non-cancel exception | Fail; `error=<ExcName>`; `trace_assembler_error` violation |
| Assembler raises `CancelledError` | Propagates (not swallowed) |
| `expected_present=true` but assembler returns `None` | Fail; `trace_expected_present` |
| `reason` / required evidence declared but assembler returns `None` | Fail; `trace_expected_present` |
| `expected_present=false` but trace returned | Fail; `trace_expected_absent` |
| Assembler returns a non-`None` malformed DTO | Fail; `trace_malformed` |
| Required fields are split across unrelated KPs | Fail; `trace_required_fields_split_across_knowledge_points` |
| Field / budget mismatches | Fail with readable `trace_violations` codes |

### Assembler invocation (trace cases only)

```text
assemble(
  current_message=case.current_message or case.query,
  rewritten_query=case.rewritten_query or case.query,
  session_id, user_id, group_id,
  active_memory_hits=ordinary memory_card hits (fallback: all hits),
)
```

Ordinary `ContextService.search` + pack scoring always run independently (active-only path unchanged).

## Acceptance scenarios covered

| ID | What |
| --- | --- |
| E1 | Ordinary current after 杭州→上海 supersede; required 上海, forbidden 杭州; no trace |
| E2 | Premise `我不是还住杭州吗`: ordinary 上海/not杭州; trace reason `premise_conflict`; current/earlier/trajectory; `card:`/`message:` prefixes |
| E3 | Historical intent on `current_message`; ordinary active-only; trace earlier/trajectory |
| E4 | Historical marker only in `rewritten_query` → trace absent |
| E5 | Cross-group isolation: no beta leak in hits or trace fields |
| E6 | No-evidence: zero hits + trace absent |
| E7 | Trusted cross-category correction is stored; `walk_supersedes_chain` rejects it and the real assembler/eval path remains absent |
| E8 | Missing assembler fail closed |
| E9 | Structured field mismatches → readable violations |
| E10 | KP / char / duplicate / pack budgets still enforced |
| E11 | Existing 2-hop graph single-seed/hub negatives remain in related focused suite (not reimplemented as PPR) |

## Files touched

- `services/context/eval.py` — additive schema + scoring
- `services/context/__init__.py` — export `TemporalTraceExpectation`
- `tests/test_context_eval_evidence_use.py` — RED/GREEN contracts + real integration
- `tests/fixtures/context_eval/long_memory_evidence_use_v1.json` — versioned synthetic fixture
- `docs/migrations/memory-longmem-evidence-use-eval-gate-v1-2026-07-16.md` — this file
- tracker / ACTIVE / maintenance-log (after GREEN)

## Explicit non-goals

- No production ContextService fusion / RetrievalGate / RRF weight changes
- No TemporalTraceAssembler runtime semantic changes
- No PPR / GraphRAG / Memory Tools
- No official LongMemEval-V2 / LoCoMo dataset download
- No answer LLM judge / leaderboard claims
- No deploy, Docker, NapCat, QZone, credentials/live DB

## Verification evidence

1. **RED (pre-implementation):** importing `TemporalTraceExpectation` / new result keys failed (`ImportError`); new test module collection error before additive schema landed.
2. **Initial GREEN (evidence-use):** `tests/test_context_eval_evidence_use.py` → **21 passed**.
3. **Independent adversarial review:** confirmed **2 Critical / 5 Important / 1 Minor** across permissive schema, absent-path, malformed DTO, split-KP scoring, and E7 assertion quality; Codex probes reproduced all eight listed risks.
4. **Remediation RED → GREEN:** 20 strictness regressions failed before correction, then evidence-use → **41 passed**; context eval + evidence-use + temporal_trace + context_plugin + context_service + retrieval → **167 passed**.
5. **Post-remediation Grok review:** **0 Critical / 0 Important**; real DTO, live fixture stack, cancellation, E7, malformed and split-KP probes verified.
6. **Compat:** basic / owner / long_memory_frontier fixtures remain pass-all; legacy `to_dict` keys preserved with new keys only additive.
7. **Static:** scoped `ruff check` clean; scoped `pyright` **0 errors** on `eval.py` / `__init__.py` / evidence-use tests.
8. **QZone baseline preserved:** seven QZone files + humanization metrics → **154 passed**; `BUILTIN_WIRE_PROFILE.validated is False`.
9. **Final full pytest:** **4168 passed / 17 skipped / 186 warnings** (initial accepted baseline 4148; net +20 strictness tests; 0 failures).
10. **Lightweight CI configured locally:** `.github/workflows/typed-boundaries.yml` runs `uv run pytest tests/test_context_eval_evidence_use.py`; exact command → **41 passed** and YAML/step assertion passed. No remote GitHub Actions run is claimed.

## Rollback

1. Revert `services/context/eval.py`, `__init__.py`, new test module, new fixture, and this migration doc.
2. No runtime config or DB migration; production bots unaffected (eval-only surface).
3. Existing callers without `trace` / `trace_assembler` keep prior behavior.

## Honest limitations

- Fixture queries are calibrated to current ngram/keyword RetrievalGate sensitivity; they validate contracts, not embedding quality.
- Does not score free-form LLM answers or multi-turn dialogue judges.
- Does not claim parity with published LongMemEval-V2 / MemTrace numbers.
- Cycle/missing/expired-chain detection remains in CardStore / temporal_trace deterministic tests; E7 is a dedicated real-stack test, not one of the six JSON fixture cases.
- Forbidden-only or budget-only expectations do not imply trace presence; fixture authors should set `expected_present` explicitly when presence/absence itself matters.
- Dict test doubles must follow the producer DTO (`earlier` is a node list, not a bare string list); malformed traces may report both `trace_malformed` and field-level missing diagnostics.

## Next candidates (Codex-owned)

1. Bounded PPR / diffusion only after density/hub leak baseline + this gate stays green.
2. MemGPT-style memory tools still deferred.
