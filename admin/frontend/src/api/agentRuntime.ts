export interface AdminEnvelope {
  available: boolean
  mode: 'offline_dark'
  reason: string
  contract_version: string
  admin_schema_version?: number
  source_schema_version?: number
  snapshot_at?: string
}

export interface PageResult<T> extends AdminEnvelope {
  items: T[]
  next_cursor: string | null
}

export interface RuntimeSummary extends AdminEnvelope {
  counts: {
    runs: number
    tool_calls: number
    events: number
  } | null
}

export interface RuntimeRun {
  run_id: string
  trigger_type: string
  status: string
  registry_generation: number
  created_at: string
  updated_at: string
  terminal_at: string
}

export interface RuntimeToolCall {
  call_id: string
  run_id: string
  step_id: string
  tool_name: string
  tool_version: string
  owner: string
  effect: string
  idempotency_mode: string
  concurrency_mode: string
  status: string
  attempt_count: number
  approval_requested_at: string | null
  approval_granted_at: string | null
  created_at: string
  updated_at: string
  terminal_at: string
}

export interface RuntimeEvent {
  event_id: number
  run_id: string
  call_id: string
  entity_kind: string
  event_type: string
  from_status: string
  to_status: string
  event_at: string
}

export interface RuntimeDetail extends AdminEnvelope {
  item: RuntimeRun | null
}

export interface MemorySummary extends AdminEnvelope {
  observation_count: number | null
  candidate_count: number | null
  conflict_count: number | null
  promotion_event_count: number | null
}

export interface MemoryObservation {
  contract_version: string
  observation_id: string
  observation_sha256: string
  source_kind: string
  producer_kind: string
  evidence_count: number
  observed_at: string
}

export interface MemoryCandidate {
  contract_version: string
  candidate_id: string
  candidate_sha256: string
  observation_id: string
  projection_kind: string
  operation: string
  fold_status: string
  conflict_count: number
  promotion_event_count: number
  produced_at: string
  resolved_conflict_count?: number
  unresolved_conflict_count?: number
}

export interface MemoryConflict {
  contract_version: string
  conflict_id: string
  kind: string
  observation_count: number
  candidate_count: number
  detected_at: string
}

export interface MemoryCandidateDetail extends AdminEnvelope {
  item: MemoryCandidate | null
}

export interface DecisionContextResource {
  kind: 'tool_call' | 'memory_candidate'
  id: string
}

export interface DecisionContextPreview {
  tool_name?: string
  tool_version?: string
  owner?: string
  effect?: string
  idempotency_mode?: string
  target_class?: string
  target_fingerprint?: string
  argument_summary?: string
  argument_fingerprint?: string
  projection_kind?: string
  operation?: string
  source_kind?: string
  producer_kind?: string
  owner_scope?: string
  visibility?: string
  evidence_count?: number
  conflict_count?: number
  conflict_ids?: string[]
  decision_event_count?: number
  produced_at?: string
}

export interface DecisionContext {
  contract_version: string
  schema_version: number
  mode: 'offline_dark'
  report_only: true
  resource: DecisionContextResource
  state: string
  updated_at: string
  preview: DecisionContextPreview
  preview_digest: string
  expected_token: string
}

export interface DecisionReceipt {
  contract_version: string
  schema_version: number
  mode: 'offline_dark'
  decision_id: string
  decision_type: string
  status: string
  exact_retry: boolean
  execution_started?: false
  projection_started?: false
}

export interface ToolApprovalDecisionRequest {
  expected_token: string
  approval_ref: string
}

export interface ReconciliationDecisionRequest {
  expected_token: string
  decision: 'confirmed_succeeded' | 'confirmed_not_applied'
  evidence_ref: string
  operator_note: string
  external_id: string | null
}

export interface MemoryCandidateDecisionRequest {
  expected_token: string
  decision: 'approve' | 'reject'
  reason_code: string
  operator_note: string | null
  occurred_at: string
}

export interface AgentRuntimeOperatorCredentials {
  operatorId: string
  credential: string
}

export interface SourceReadiness {
  available: boolean
  schema_version: number | null
  integrity: string
  reason?: string
}

export interface ReadinessGate {
  status: 'ready' | 'not_ready' | 'not_assessed' | 'unknown'
  reason: string
  evidence_at: string | null
}

export interface ReadinessReport {
  contract_version: string
  schema_version: number
  mode: 'offline_dark'
  status: string
  report_only: true
  activation_authorized: boolean
  gates: Record<string, ReadinessGate>
}

export interface DarkReadiness {
  contract_version: string
  schema_version: number
  mode: 'offline_dark'
  report_only: true
  activation_authorized: false
  status: string
  sources: {
    runtime: SourceReadiness
    memory: SourceReadiness
    worldbook: SourceReadiness
  }
}

export interface ActivationReadiness extends ReadinessReport {
  contract_version: string
  schema_version: number
  mode: 'offline_dark'
  blocking_gates: string[]
}

export interface RollbackReadiness extends ReadinessReport {
  contract_version: string
  schema_version: number
  mode: 'offline_dark'
  blocking_gates: string[]
}

export interface WorldbookGovernanceSummary extends AdminEnvelope {
  proposal_count: number | null
  pending_count: number | null
  approved_count: number | null
  rejected_count: number | null
  committed_count: number | null
}

export interface WorldbookGovernanceProvenance {
  source_binding_sha256: string
  evidence_count: number
  time_basis: string
}

export interface WorldbookGovernanceProposal {
  contract_version: string
  proposal_id: string
  proposal_sha256: string
  event_id: string
  world_id: string
  source_kind: string
  status: string
  decision_present: boolean
  receipt_present: boolean
  occurred_at: string
  proposed_at: string
  provenance: WorldbookGovernanceProvenance
}

export interface WorldbookGovernanceList extends AdminEnvelope {
  items: WorldbookGovernanceProposal[]
  next_cursor: string | null
}

export interface WorldbookGovernanceDetail extends AdminEnvelope {
  item: WorldbookGovernanceProposal | null
}

export const AGENT_RUNTIME_GOVERNANCE_PATHS = {
  toolApprovalContext: (callId: string) => `/api/admin/agent-runtime/tool-calls/${encodeURIComponent(callId)}/approval/context`,
  toolApprovalAction: (callId: string) => `/api/admin/agent-runtime/tool-calls/${encodeURIComponent(callId)}/approval`,
  reconciliationContext: (callId: string) => `/api/admin/agent-runtime/tool-calls/${encodeURIComponent(callId)}/reconciliation/context`,
  reconciliationAction: (callId: string) => `/api/admin/agent-runtime/tool-calls/${encodeURIComponent(callId)}/reconciliation`,
  memoryCandidateContext: (candidateId: string) => `/api/admin/memory-governance/candidates/${encodeURIComponent(candidateId)}/decision/context`,
  memoryCandidateAction: (candidateId: string) => `/api/admin/memory-governance/candidates/${encodeURIComponent(candidateId)}/decision`,
  worldbookSummary: '/api/admin/worldbook-governance/summary',
  worldbookList: '/api/admin/worldbook-governance/proposals',
  worldbookDetail: (proposalId: string) => `/api/admin/worldbook-governance/proposals/${encodeURIComponent(proposalId)}`,
} as const

export function classifyReadinessGate(status: unknown): { label: string, tone: 'success' | 'error' | 'warning' } {
  if (status === 'ready') return { label: '就绪', tone: 'success' }
  if (status === 'not_ready') return { label: '未就绪', tone: 'error' }
  return { label: '未评估', tone: 'warning' }
}

async function request<T>(path: string, options?: Record<string, unknown>): Promise<T> {
  // Dynamic import keeps pure contract helpers loadable under node:test.
  const { api } = await import('./client')
  return api<T>(path, options)
}

export function buildAgentRuntimeOperatorHeaders(
  credentials: AgentRuntimeOperatorCredentials,
): Record<string, string> {
  return {
    'X-Agent-Runtime-Operator-Id': credentials.operatorId.trim(),
    Authorization: `Bearer ${credentials.credential}`,
  }
}

export async function fetchRuntimeSummary() {
  return request<RuntimeSummary>('/api/admin/agent-runtime/summary')
}

export async function fetchRuntimeRuns(params: Record<string, string | number | undefined>) {
  return request<PageResult<RuntimeRun>>('/api/admin/agent-runtime/runs', { params })
}

export async function fetchRuntimeRun(runId: string) {
  return request<RuntimeDetail>(`/api/admin/agent-runtime/runs/${encodeURIComponent(runId)}`)
}

export async function fetchRuntimeToolCalls(params: Record<string, string | number | undefined>) {
  return request<PageResult<RuntimeToolCall>>('/api/admin/agent-runtime/tool-calls', { params })
}

export async function fetchRuntimeEvents(runId: string, params: Record<string, string | number | undefined>) {
  return request<PageResult<RuntimeEvent>>(
    `/api/admin/agent-runtime/runs/${encodeURIComponent(runId)}/events`,
    { params },
  )
}

export async function fetchMemorySummary() {
  return request<MemorySummary>('/api/admin/memory-governance/summary')
}

export async function fetchMemoryObservations(params: Record<string, string | number | undefined>) {
  return request<PageResult<MemoryObservation>>('/api/admin/memory-governance/observations', { params })
}

export async function fetchMemoryCandidates(params: Record<string, string | number | undefined>) {
  return request<PageResult<MemoryCandidate>>('/api/admin/memory-governance/candidates', { params })
}

export async function fetchMemoryCandidate(candidateId: string) {
  return request<MemoryCandidateDetail>(
    `/api/admin/memory-governance/candidates/${encodeURIComponent(candidateId)}`,
  )
}

export async function fetchMemoryConflicts(params: Record<string, string | number | undefined>) {
  return request<PageResult<MemoryConflict>>('/api/admin/memory-governance/conflicts', { params })
}

export async function fetchDarkReadiness() {
  return request<DarkReadiness>('/api/admin/agent-runtime/readiness/dark')
}

export async function fetchActivationReadiness() {
  return request<ActivationReadiness>('/api/admin/agent-runtime/readiness/activation')
}

export async function fetchRollbackReadiness() {
  return request<RollbackReadiness>('/api/admin/agent-runtime/readiness/rollback')
}

export async function fetchToolApprovalContext(
  callId: string,
  credentials: AgentRuntimeOperatorCredentials,
) {
  return request<DecisionContext>(AGENT_RUNTIME_GOVERNANCE_PATHS.toolApprovalContext(callId), {
    headers: buildAgentRuntimeOperatorHeaders(credentials),
  })
}

export async function approveToolCall(
  callId: string,
  body: ToolApprovalDecisionRequest,
  credentials: AgentRuntimeOperatorCredentials,
) {
  return request<DecisionReceipt>(AGENT_RUNTIME_GOVERNANCE_PATHS.toolApprovalAction(callId), {
    method: 'POST',
    body: { ...body, expected_token: body.expected_token },
    headers: buildAgentRuntimeOperatorHeaders(credentials),
  })
}

export async function fetchReconciliationContext(
  callId: string,
  credentials: AgentRuntimeOperatorCredentials,
) {
  return request<DecisionContext>(AGENT_RUNTIME_GOVERNANCE_PATHS.reconciliationContext(callId), {
    headers: buildAgentRuntimeOperatorHeaders(credentials),
  })
}

export async function reconcileToolCall(
  callId: string,
  body: ReconciliationDecisionRequest,
  credentials: AgentRuntimeOperatorCredentials,
) {
  return request<DecisionReceipt>(AGENT_RUNTIME_GOVERNANCE_PATHS.reconciliationAction(callId), {
    method: 'POST',
    body: { ...body, expected_token: body.expected_token },
    headers: buildAgentRuntimeOperatorHeaders(credentials),
  })
}

export async function fetchMemoryCandidateContext(
  candidateId: string,
  credentials: AgentRuntimeOperatorCredentials,
) {
  return request<DecisionContext>(AGENT_RUNTIME_GOVERNANCE_PATHS.memoryCandidateContext(candidateId), {
    headers: buildAgentRuntimeOperatorHeaders(credentials),
  })
}

export async function decideMemoryCandidate(
  candidateId: string,
  body: MemoryCandidateDecisionRequest,
  credentials: AgentRuntimeOperatorCredentials,
) {
  return request<DecisionReceipt>(AGENT_RUNTIME_GOVERNANCE_PATHS.memoryCandidateAction(candidateId), {
    method: 'POST',
    body: { ...body, expected_token: body.expected_token },
    headers: buildAgentRuntimeOperatorHeaders(credentials),
  })
}

export async function fetchWorldbookGovernanceSummary() {
  return request<WorldbookGovernanceSummary>(AGENT_RUNTIME_GOVERNANCE_PATHS.worldbookSummary)
}

export async function fetchWorldbookGovernanceList(params: Record<string, string | number | undefined>) {
  const boundedParams = {
    limit: params.limit,
    cursor: params.cursor,
    world_id: params.world_id,
    source_kind: params.source_kind,
    status: params.status,
  }
  return request<WorldbookGovernanceList>(AGENT_RUNTIME_GOVERNANCE_PATHS.worldbookList, {
    params: boundedParams,
  })
}

export async function fetchWorldbookGovernanceDetail(proposalId: string) {
  return request<WorldbookGovernanceDetail>(AGENT_RUNTIME_GOVERNANCE_PATHS.worldbookDetail(proposalId))
}
