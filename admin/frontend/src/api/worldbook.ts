/** GET /api/admin/worldbook/snapshot — typed to match admin/routes/api/worldbook.py */

export const WORLDBOOK_SNAPSHOT_PATH = '/api/admin/worldbook/snapshot'
export const WORLDBOOK_PLUGIN_NAME = 'worldbook'
export const WORLDBOOK_ROUTE_PATH = '/worldbook'

export interface WorldbookGates {
  enabled: boolean
  chat_projection_enabled: boolean
  schedule_projection_enabled: boolean
  storylet_enabled: boolean
  dream_proposal_enabled: boolean
  social_evidence_enabled: boolean
  allowlist_count: number
  total_budget_chars: number
  max_setbacks_per_arc: number
  max_events_per_tick: number
  state_dir: string
  canon_dir: string
  storylet_dir: string
}

export interface WorldbookCanonEntry {
  entry_id: string
  title: string
  priority: number
  always_active: boolean
  keyword_count: number
  alias_count: number
}

export interface WorldbookStoryletEntry {
  storylet_id: string
  title: string
  severity: string
  priority: number
  saliency: number
  target_arc_id: string
  once: boolean
}

export interface WorldbookRegistry {
  runtime_loaded: boolean
  canon: {
    loaded: boolean
    count: number
    entries: WorldbookCanonEntry[]
  }
  storylets: {
    loaded: boolean
    count: number
    entries: WorldbookStoryletEntry[]
  }
}

export interface WorldbookArcSummary {
  arc_id: string
  title: string
  stage: string
  status: string
  arc_role: string
  stack_order: number
  goal_count: number
  open_thread_count: number
}

export interface WorldbookLedger {
  main: WorldbookArcSummary | null
  sides: WorldbookArcSummary[]
  ambient: WorldbookArcSummary[]
  ordering: string[]
  main_count: number
  side_count: number
  ambient_count: number
  error?: string
}

export interface WorldbookLifeProvenance {
  source: string
  scope: string
  confidence: string
  privacy: string
  updated_at: string
  revision: number
  evidence_ref_count: number
  evidence_refs: string[]
}

export interface WorldbookLifeItem {
  key: string
  value: string
  expired: boolean
  decay_at: string | null
  provenance: WorldbookLifeProvenance
}

export interface WorldbookLife {
  revision: number
  updated_at: string
  item_count: number
  expired_count: number
  active_count: number
  items: WorldbookLifeItem[]
  applied_event_id_count: number
  error?: string
}

export interface WorldbookProposal {
  proposal_id: string
  kind: string
  status: string
  arc_id: string
  source: string
  created_at: string
  payload_key_count: number
  payload_keys: string[]
}

export interface WorldbookDecisionEffects {
  stage: string | null
  open_thread_count: number
  resolve_thread_count: number
  partner_update_count: number
  life_update_count: number
  variable_delta_keys: string[]
}

export interface WorldbookDecision {
  decision_id: string
  proposal_id: string
  status: string
  reason_code: string
  reason: string
  arc_id: string
  decided_at: string
  proposal_fingerprint: string
  effects: WorldbookDecisionEffects | null
}

export interface WorldbookCommit {
  commit_id: string
  proposal_id: string
  decision_id: string
  event_id: string
  arc_id: string
  status: string
  committed_at: string
}

export interface WorldbookLifecycle {
  proposal_count: number
  decision_count: number
  commit_count: number
  proposals: WorldbookProposal[]
  decisions: WorldbookDecision[]
  commits: WorldbookCommit[]
}

export interface WorldbookBlockTrace {
  trace_id: string
  request_id: string
  task: string
  source: string
  provider: string
  candidate_id: string
  decision: string
  hit_reason: string
  label: string
  char_count: number
  priority: number
  budget_reason: string
  created_at: string
}

export interface WorldbookShadowContent {
  overall_verdict: string
  step_count: number
  registry_counts: {
    canon: unknown
    storylet: unknown
  }
  continuity: {
    main_arc_id: unknown
    side_arc_ids: string[]
    ambient_arc_ids: string[]
  }
  setback_recovery: {
    major_setback_count: unknown
    recovery_observed: unknown
  }
  invariant_verdict: unknown
  selected_storylet_count: number
  report_essential_hash: string
  pack_id: string
}

export interface WorldbookShadow {
  status: string
  reason: string
  path_name?: string
  size_bytes?: number
  max_bytes?: number
  content?: WorldbookShadowContent
}

export interface WorldbookSnapshot {
  ok: boolean
  available: boolean
  reason: string
  gates: WorldbookGates | null
  registry: WorldbookRegistry | null
  ledger: WorldbookLedger | null
  life: WorldbookLife | null
  lifecycle: WorldbookLifecycle | null
  block_traces: WorldbookBlockTrace[]
  shadow: WorldbookShadow
}

export type WorldbookLoadState =
  | 'idle'
  | 'loading'
  | 'ready'
  | 'unavailable'
  | 'error'

export function reasonLabel(reason: string | null | undefined): string {
  const key = (reason ?? '').trim()
  const labels: Record<string, string> = {
    ok: '正常',
    runtime_not_mounted: '运行时未挂载',
    worldbook_disabled: '插件已禁用',
    shadow_report_missing: 'Shadow 报告缺失',
    shadow_report_invalid_json: 'Shadow 报告 JSON 无效',
    shadow_report_not_object: 'Shadow 报告结构异常',
    shadow_report_over_2mib: 'Shadow 报告过大',
    state_dir_unresolved: 'state_dir 未解析',
    path_outside_state_dir: '路径越界',
    path_resolve_failed: '路径解析失败',
    stat_failed: '文件状态读取失败',
    read_failed: '文件读取失败',
  }
  if (labels[key]) return labels[key]
  if (key.startsWith('snapshot_failed:')) {
    return `快照构建失败（${key.slice('snapshot_failed:'.length)}）`
  }
  if (key.startsWith('ledger_load_failed:')) {
    return `Ledger 加载失败（${key.slice('ledger_load_failed:'.length)}）`
  }
  if (key.startsWith('life_load_failed:')) {
    return `Life 加载失败（${key.slice('life_load_failed:'.length)}）`
  }
  return key || '未知'
}

export function unavailableCopy(reason: string | null | undefined): {
  title: string
  description: string
} {
  const key = (reason ?? '').trim()
  if (key === 'worldbook_disabled') {
    return {
      title: '世界书插件不可用',
      description:
        'worldbook 插件当前未启用。可在插件页开启后返回本页刷新；直达路由保持可用，不会自动跳转。',
    }
  }
  if (key === 'runtime_not_mounted') {
    return {
      title: '世界书运行时未挂载',
      description:
        '后端未挂载 worldbook_runtime。请确认插件已加载且 bot 已完成启动，然后刷新本页。',
    }
  }
  if (key.startsWith('snapshot_failed:')) {
    return {
      title: '快照暂时不可用',
      description: `${reasonLabel(key)}。本页只读，不会触发 ensure_loaded 或写入操作。`,
    }
  }
  return {
    title: '世界书暂不可用',
    description: reasonLabel(key) || '快照返回 available=false。刷新后重试。',
  }
}

export function gateMetricRows(gates: WorldbookGates | null | undefined): Array<{
  title: string
  value: string | number
  hint?: string
  accent: 'primary' | 'success' | 'warning' | 'info'
}> {
  if (!gates) {
    return [
      { title: 'Enabled', value: '—', hint: 'gates 不可用', accent: 'info' },
      { title: 'Chat 投影', value: '—', accent: 'info' },
      { title: 'Storylet', value: '—', accent: 'info' },
      { title: 'Dream 提案', value: '—', accent: 'info' },
    ]
  }
  const onOff = (v: boolean) => (v ? '开' : '关')
  return [
    {
      title: 'Enabled',
      value: onOff(gates.enabled),
      hint: `allowlist ${gates.allowlist_count}`,
      accent: gates.enabled ? 'success' : 'warning',
    },
    {
      title: 'Chat 投影',
      value: onOff(gates.chat_projection_enabled),
      hint: `schedule ${onOff(gates.schedule_projection_enabled)}`,
      accent: gates.chat_projection_enabled ? 'success' : 'info',
    },
    {
      title: 'Storylet',
      value: onOff(gates.storylet_enabled),
      hint: `budget ${gates.total_budget_chars}`,
      accent: gates.storylet_enabled ? 'primary' : 'info',
    },
    {
      title: 'Dream 提案',
      value: onOff(gates.dream_proposal_enabled),
      hint: `social ${onOff(gates.social_evidence_enabled)}`,
      accent: gates.dream_proposal_enabled ? 'primary' : 'info',
    },
  ]
}

export function lifecycleStatusTone(
  status: string | null | undefined,
): 'success' | 'warning' | 'error' | 'default' | 'info' {
  const s = (status ?? '').toLowerCase()
  if (
    s === 'committed'
    || s === 'validated'
    || s === 'accepted'
    || s === 'approved'
    || s === 'ok'
    || s === 'pass'
  ) {
    return 'success'
  }
  if (s === 'rejected' || s === 'failed' || s === 'fail' || s === 'error') return 'error'
  if (s === 'pending' || s === 'proposal' || s === 'proposed' || s === 'skipped') {
    return 'warning'
  }
  if (s === 'missing' || s === 'corrupt' || s === 'oversized' || s === 'unavailable') {
    return 'warning'
  }
  return 'default'
}

export function formatTimestamp(value: string | null | undefined): string {
  const raw = (value ?? '').trim()
  if (!raw) return '—'
  const ms = Date.parse(raw)
  if (Number.isNaN(ms)) return raw
  return new Date(ms).toLocaleString()
}

/** Fetch snapshot via GET. Mutation methods are intentionally absent. */
export async function fetchWorldbookSnapshot(): Promise<WorldbookSnapshot> {
  // Dynamic import keeps pure DTO helpers loadable under node:test without ofetch/pinia.
  const { api } = await import('./client')
  return api<WorldbookSnapshot>(WORLDBOOK_SNAPSHOT_PATH)
}
