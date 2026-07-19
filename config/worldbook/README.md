# Worldbook content layout

Worldbook Runtime v1 reads version-controlled JSON registries from:

- `config/worldbook/canon/*.json`: immutable Native World Canon entries.
- `config/worldbook/storylets/*.json`: fiction-only structured event candidates.

Empty registries safely produce no prompt block and no story event. Runtime
learning, chat messages, Dream output, and Social Narrative evidence must never
write these files.

## Canon vs Storylets (source of truth)

| Pack | Truth kind | May cite real Persona facts? | May invent plot? | Runtime mutation |
| --- | --- | --- | --- | --- |
| **Canon** | Reviewed identity / world anchors | Yes — only from local reviewed Persona config (`config/persona/...`) | No | Forbidden |
| **Storylets** | Fiction event candidates | May reference Canon entity ids only | Yes — pure fiction | Registry immutable; selection state lives on StoryArc `event_budget` |

- **Canon is sourced**: every entry `metadata.source` must point at a local
  reviewed path and section (for example
  `config/persona/fengxiaomeng-v2/source.md#4`). Do not invent real-person
  facts. Do not scrape the web. Do not encode real group member data.
- **Storylets are fiction**: `scope` is always `fiction`. They dramatize
  rehearsal / study / setback / recovery arcs for Living Story offline replay.
  They are not factual memory and must never be promoted to Canon.

## Canon file

Validate against `schemas/worldbook-canon-v1.schema.json`.

```json
{
  "schema_version": 1,
  "entries": [
    {
      "entry_id": "place.example_stage",
      "title": "Example stage",
      "text": "A verified, atomic world fact.",
      "keywords": ["stage"],
      "aliases": [],
      "regexes": [],
      "entity_ids": ["example_stage"],
      "related_entities": [],
      "priority": 100,
      "always_active": false,
      "metadata": {
        "source": "config/persona/<id>/source.md#section"
      }
    }
  ]
}
```

### Canon authoring rules

- one entry is one atomic fact block; do not concatenate an entire setting book;
- `always_active` must be `false` (or omitted) — projection activates only via
  keyword / alias / regex / entity cascade (optional semantic scorer);
- record a reviewable local source in `metadata.source`;
- never include group member facts, QQ numbers, or private chat evidence;
- aliases and regexes activate existing facts but do not create new facts;
- runtime mutation is forbidden; changes require a reviewed repository edit.

### Canon review checklist (exact)

1. **Schema**: file validates against `schemas/worldbook-canon-v1.schema.json`.
2. **Atomicity**: each `entry_id` is one fact; no multi-topic dump.
3. **Activation**: at least one of `keywords` / `aliases` / `regexes` /
   `entity_ids` is non-empty so the entry can fire without `always_active`.
4. **always_active**: every entry has `always_active: false`.
5. **Source**: `metadata.source` is a repo-relative path (+ optional `#section`)
   under reviewed Persona config or freeze artifacts — not a URL, not "memory".
6. **No real group data**: no real QQ ids, group ids, member nicknames from
   live chat, or private logs.
7. **No invention**: text is entailed by the cited local source (or is a neutral
   structural label of that source fact).
8. **IDs stable**: `entry_id` / `entity_ids` use `[A-Za-z0-9][A-Za-z0-9_.-]*`.
9. **Pack size**: prefer 8–15 atomic anchors per Living Story persona pack.

## Storylet file

Validate against `schemas/worldbook-storylet-v1.schema.json`.

```json
{
  "schema_version": 1,
  "storylets": [
    {
      "storylet_id": "rehearsal.example_problem",
      "title": "Example rehearsal problem",
      "text": "A fictional rehearsal problem appears and requires recovery.",
      "conditions": {
        "var_gte": {"pressure": 0.5}
      },
      "saliency": 1.2,
      "severity": "tension",
      "once": false,
      "cooldown_steps": 3,
      "delay_steps": 0,
      "cost": {},
      "consequence": {},
      "recovery_steps": 1,
      "required_evidence": ["arc:example"],
      "scope": "fiction",
      "priority": 100
    }
  ]
}
```

### Supported condition vocabulary (Stage 0/1)

Only these keys are evaluated by `DramaManager` (unknown keys fail closed):

| Key | Meaning |
| --- | --- |
| `min_step` / `after_step` | Eligible when `now_step >= value` |
| `var_gte` | Map of variable → minimum float |
| `var_lte` | Map of variable → maximum float |
| `var_eq` | Map of variable → exact value |

### Storylet authoring rules

- `scope` is always `fiction`;
- `required_evidence` tokens must match fixture / ledger evidence
  (typically `arc:<arc_id>`);
- major / setback / crisis require explicit `consequence` and a recovery path
  (`recovery_steps` and/or a later `severity: recovery` storylet);
- do not name or simulate real group members;
- once / cooldown / delay are state contracts on `event_budget`, not prose.

### Storylet review checklist (exact)

1. **Schema**: validates against `schemas/worldbook-storylet-v1.schema.json`.
2. **Fiction only**: every storylet has `scope: "fiction"`.
3. **Conditions**: only known keys (`min_step`/`after_step`, `var_gte`,
   `var_lte`, `var_eq`).
4. **Evidence**: `required_evidence` ids match the Living Story fixture arcs
   (see below) or other explicitly seeded ledger tokens.
5. **Coverage**: pack covers daily rehearsal/study, exam-vs-stage tension, one
   bounded setback, replan cost, recovery, partner independent state, and
   closure/aftermath (about 6–10 candidates).
6. **Setback budget**: at most one major setback path intended per main arc
   (`once: true` on the major setback storylet).
7. **No real people**: fiction names only from Canon / Persona partners, never
   live group members.
8. **IDs stable**: `storylet_id` pattern matches schema.

## Arc / life / social fixture semantics

Stage-1 offline fixtures live under
`tests/fixtures/worldbook/living_story_v1/` and are **not** production state.

### StoryArc seeds — production vs offline fixtures

Two trees share the same **stable arc IDs** (so Storylets resolve), but they are
**not** the same pack:

| Tree | Path | Purpose |
| --- | --- | --- |
| **Production seed pack** | `config/worldbook/arcs/*.json` | Versioned Living Story Arc seeds. Imported by the Worldbook plugin into the real `StoryArcStore` when `enabled` + `storylet_enabled`. |
| **Offline fixtures** | `tests/fixtures/worldbook/living_story_v1/arcs/*.json` | Shadow / unit replay only. Never copied into production storage. |

| Role | Stable id | Meaning |
| --- | --- | --- |
| **main** | `living_story_v1.main` | Explicit primary Living Story line; drama budget + setback counters attach here |
| **side** | `living_story_v1.side_study` | Parallel study/exam pressure line; may supply `arc:` evidence |
| **ambient** | `living_story_v1.ambient_park` | Low-intensity world continuation (e.g. partner independent motion) |

Production seed contract (fail closed before any write):

- exactly one `main`, ≥1 `side`, ≥1 `ambient`
- `scope=fiction`, `status=active`, `stage=active`, `revision=0`
- clean `last_events` / `event_history` / `committed_event_ids` / partner applied IDs
- fictional partners only (`tsukasa` / `nene`); no real group or user facts
- stable `stack_order` 0 / 10 / 20 for main / side / ambient
- no fixed end date required

Importer rules (`plugins/worldbook/arc_seed.py`):

- validate the **entire** pack first
- seed **only missing** arc IDs into the existing `StoryArcStore`
- never overwrite an existing matching arc
- fail closed on role/scope mismatch or a conflicting foreign explicit main
- startup seeding is config-driven (`arc_seed_dir`, default `config/worldbook/arcs`) and only when `enabled` + `storylet_enabled`
- social input / social gate alone never seeds
- selection uses explicit `arc_role` + `stack_order`, never mtime
- Storylet `required_evidence` / `target_arc_id` must resolve against these IDs
- a pre-existing legacy weekly arc (no raw `arc_role`) remains untouched and
  loads as an additional **side** once production seeds provide the explicit main

### Life State seed (`life_state.json`)

- Mutable self snapshot with mandatory metadata:
  `source`, `scope`, `privacy`, `updated_at`, `decay_at` (TTL).
- Trusted sources for `scope=self` / `privacy=private` are
  `life_state` / `story_ledger` / `drama` / `system` only.
- Missing TTL or expired `decay_at` fails closed at projection.

### Social evidence corpus (`social_evidence.json`)

- **Accepted in chat shadow**: current `group_id` + `user_id`, privacy
  `public` or `group`, complete evidence fields
  (`evidence_message_id`, `evidence_time`, non-empty summary).
- **Rejected**: cross-group, cross-user, `privacy=private`, missing evidence
  fields, or schedule mode (schedule projection must have zero social blocks).
- Social evidence is **fixture-only** in Stage 1; never write production
  Social Narrative stores from shadow.

### 7-day scenario (`scenario_7day.json`)

- Ordered list of 7 steps (day 0..6) with conversation text, scope ids,
  optional variable nudges, and mode (`chat` / `schedule` / `both`).
- Shadow evaluator advances `event_budget` clocks, projects blocks, selects
  storylets, commits fiction events via the reducer, and records traces.

### Golden / expected report (`expected_shadow_report.json`)

- Stable invariant expectations for the offline shadow (registry non-empty,
  continuity, social accept/reject reasons, setback≤1 + recovery observed,
  reload checkpoint survival, partner/life persisted-truth facts, recovery
  gap window, final main stage + setback flag). Not a live prompt snapshot.

## Shadow evaluator (Stage 1)

- Service: `plugins/worldbook/shadow.py`
- Optional CLI: `tools/run_worldbook_shadow.py`
- Runs only against a **caller-supplied** temp/output root.
- Loads real Canon / Storylet packs (read-only) and fixture seeds into the
  temp root.
- **Never** registers `PromptProvider`, never writes `config/worldbook`, never
  touches production `storage/`, Docker, QZone, or NapCat.
- Default production plugin gates remain all `false`
  (`plugins/worldbook/config.default.json`).

## Stage gates (product)

1. Stage 0: structure/tests; all gates false. **Closed.**
2. Stage 1: offline fixture + shadow (this content). **No production Prompt impact.**
3. Stage 2+: gated chat / schedule / Dream — separate authorization.
