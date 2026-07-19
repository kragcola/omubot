# memory-graph-window-and-hub-control-fix-v1

Date: 2026-07-16
Status: implemented and reviewed offline; not deployed, not committed

## Intent

Fix two production `GraphContextSource` failures before considering PPR:

1. A global confidence-ordered `list_relationships(limit=200)` window could exclude a lower-confidence fact from the current user/group scope before scope filtering.
2. High-confidence leaf edges around a hub could crowd out a lower-confidence branch that has a bounded continuation.

This slice also hardens the offline graph evaluation gate so malformed metrics and thresholds fail closed instead of producing a false GO or raising numeric conversion exceptions.

## Old -> New

| Area | Old | New |
| --- | --- | --- |
| Candidate window | One global confidence top-200, then scope filter | Current scope -> global -> shared pools; at most 8 scope windows, each top-200 |
| Store query | One mixed-scope list | Parameterized SQLite window function partitions by `(scope, scope_id)` |
| Evidence loading | One query per returned fact | 400-ID parameter chunks; ordered evidence attached in batches |
| Hub traversal | Confidence-first expansion; traversed hub could remain in the next frontier | Raw lexical first, then continuation degree, then confidence; high-degree hub fanout bounded; next frontier contains only newly introduced entities |
| Graph limits | Dual seed, max 2 hops, hard top-k | Preserved |
| Eval input trust | NaN/Inf, bool coercion, impossible counts and invalid thresholds could pass or raise | Stable `invalid_metrics` / `invalid_thresholds`; conservative derived values; no conversion crash |
| Empty gold/window | Empty gold could report perfect recall | Recall is `0.0`; empty at-k window cannot claim positive recall |

## Frozen Runtime Contract

- Allowed scope filtering remains mandatory; no cross-group visibility expansion is introduced.
- Primary current user/group scope and global scope cannot be displaced by shared-pool truncation.
- At most 8 scope windows are loaded, with 200 active facts per scope.
- Expansion still requires at least two direct lexical seeds and cannot exceed two hops.
- Hub control starts at degree 8; per-hop hub fanout is `max(3, top_k // 2)`.
- Expansion ordering uses raw lexical relevance before the hop score floor. The floor affects hit scoring only, not lexical ordering.
- `ContextService` RRF, RetrievalGate, evidence-use assembly and prompt packing are unchanged.
- Production PPR remains NO-GO.

## Evaluation Contract

- Recall/hub rates must be finite and in `[0, 1]`.
- Integer counts must be real integers rather than bool and satisfy their non-negative structural bounds.
- `ordered_ids`, `hit_count`, hub count/rate, safety count and empty-window quality must agree.
- `deterministic` and threshold `require_deterministic` must be actual bool values.
- Thresholds must be finite; `max_hop` stays within the production cap `0..2`.
- Invalid `top_k`, query metadata, hop values and huge/overflowing numerics fail closed.

## Verification

- Exact graph CI command: `tests/test_graph_eval.py tests/test_context_service.py tests/test_knowledge_graph.py` -> **83 passed**.
- Graph eval alone -> **53 passed**.
- Final full repository regression -> **4272 passed, 17 skipped, 187 warnings**.
- Ruff clean; scoped Pyright 0 errors.
- Independent final review found no Critical or Important; all earlier findings were converted to regressions and remediated.

## Non-goals

- No Personalized PageRank, diffusion, GraphRAG community summaries or RRF weight change.
- No schema migration or graph fact backfill.
- No production deployment, Docker/NapCat operation, credentials or live database access.
- The synthetic harness is not official HippoRAG/LoCoMo/LongMemEval parity or a public benchmark claim.

## Rollback

1. Revert scoped list APIs and batch evidence loading in `KnowledgeGraphStore/Service`.
2. Revert `GraphContextSource` to the prior single window and bounded BFS selection.
3. Revert graph eval validators, tests and CI step.
4. No database rollback is required; the slice adds no table, column or index.
