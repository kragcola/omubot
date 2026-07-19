/** Draft lifecycle statuses supported by QZone Journal v0.3. */
export type QzoneDraftStatus =
  | 'pending_review'
  | 'approved'
  | 'dispatching'
  | 'published'
  | 'rejected'
  | 'failed'
  | 'unknown'

export const QZONE_DRAFT_STATUSES: readonly QzoneDraftStatus[] = [
  'pending_review',
  'approved',
  'dispatching',
  'published',
  'rejected',
  'failed',
  'unknown',
] as const

/** Safe public alias view after factual projection (v0.7+). Never internal refs. */
export interface QzonePublicAliasView {
  alias_id?: string
  public_label?: string
  policy_id?: string
  allowed_claim_classes?: string[]
}

/**
 * Bounded public projection metadata (provenance schema_version 2).
 * Backend sanitizes; UI must not invent internal entity keys.
 * public_template_id is the closed renderer template id (v0.7 by-construction).
 */
export interface QzonePublicProjectionMeta {
  schema_version?: number
  policy_id?: string
  public_template_id?: string
  source_event_hash?: string
  applied_claim_classes?: string[]
  aliases?: QzonePublicAliasView[]
}

/** Sanitized review provenance allowed by the backend contract. */
export interface QzoneReviewProvenance {
  schema_version?: number | string
  arc_id?: string
  arc_revision?: string | number
  arc_stage?: string
  arc_scope?: string
  advanced_context_included?: boolean
  fiction_partner_entity_ids?: string[]
  /** Present only when schema_version is 2 (factual projected events). */
  public_projection?: QzonePublicProjectionMeta
}

export interface QzoneReviewBundle {
  stable_id: string | null
  subject_kind: string | null
  privacy: string | null
  salience: number | null
  source_summary: string | null
  approval_scope: 'dry_run' | 'live'
  provenance: QzoneReviewProvenance | null
}

export interface QzoneDraft {
  draft_id: string
  dedupe_key: string
  event_date: string
  source: string
  content: string
  status: QzoneDraftStatus | string
  publish_date: string | null
  external_post_id: string | null
  last_error_code: string | null
  created_at: string
  updated_at: string
  stable_id: string | null
  subject_kind: string | null
  privacy: string | null
  salience: number | null
  source_summary: string | null
  approval_scope: 'dry_run' | 'live'
  /** Append-only revision lineage (v0.8+). */
  revision_root_id?: string | null
  revision?: number | null
  supersedes_draft_id?: string | null
  review: QzoneReviewBundle
}

/** One immutable revision row in a draft lineage (v0.8+). */
export interface QzoneDraftRevision {
  draft_id: string
  revision: number
  revision_root_id: string
  supersedes_draft_id: string | null
  status: QzoneDraftStatus | string
  content: string
  created_at: string
  updated_at: string
}

export interface QzoneDraftRevisionsResponse {
  revision_root_id: string
  revisions: QzoneDraftRevision[]
}

export interface QzoneDraftListResponse {
  drafts: QzoneDraft[]
  total: number
  limit: number
  offset: number
  has_more: boolean
}

export interface QzoneReviewDecision {
  decision_id: number
  draft_id: string
  decision: string
  note: string
  previous_status: string
  new_status: string
  created_at: string
}

export interface QzoneManualResolution {
  resolution_id: number
  draft_id: string
  decision: string
  note: string
  previous_status: string
  new_status: string
  external_post_id: string | null
  created_at: string
}

export interface QzoneDraftAuditResponse {
  draft_id: string
  review_decisions: QzoneReviewDecision[]
  manual_resolutions: QzoneManualResolution[]
}

export interface QzoneGateReason {
  code: string
  message: string
}

export interface QzoneLivePublishGate {
  ready: boolean
  reasons: QzoneGateReason[]
}

export interface QzoneHealthCounts {
  pending_review: number
  approved: number
  dispatching: number
  published: number
  rejected: number
  failed: number
  unknown: number
}

export type QzoneSelectionReason =
  | 'accept'
  | 'reject_source_not_allowed'
  | 'reject_privacy_not_public'
  | 'reject_subject_not_allowed'
  | 'reject_empty_identity'
  | 'reject_salience_out_of_range'
  | 'reject_below_threshold'
  | 'reject_adapter_unparseable'
  | 'reject_missing_subject_privacy'
  | 'reject_duplicate_dedupe'
  | 'reject_out_ranked'
  | 'reject_day_draft_budget'
  | 'reject_review_field'
  | 'reject_public_projection'

export const QZONE_SELECTION_REASONS: readonly QzoneSelectionReason[] = [
  'accept',
  'reject_source_not_allowed',
  'reject_privacy_not_public',
  'reject_subject_not_allowed',
  'reject_empty_identity',
  'reject_salience_out_of_range',
  'reject_below_threshold',
  'reject_adapter_unparseable',
  'reject_missing_subject_privacy',
  'reject_duplicate_dedupe',
  'reject_out_ranked',
  'reject_day_draft_budget',
  'reject_review_field',
  'reject_public_projection',
] as const

export const QZONE_SELECTION_REASON_LABELS: Record<QzoneSelectionReason, string> = {
  accept: '已生成草稿',
  reject_source_not_allowed: '来源未获准',
  reject_privacy_not_public: '非公开材料',
  reject_subject_not_allowed: '主体类型受限',
  reject_empty_identity: '事件标识不完整',
  reject_salience_out_of_range: '显著度无效',
  reject_below_threshold: '显著度不足',
  reject_adapter_unparseable: '事件格式无法解析',
  reject_missing_subject_privacy: '缺少主体或隐私声明',
  reject_duplicate_dedupe: '重复事件',
  reject_out_ranked: '排序后未入选',
  reject_day_draft_budget: '当日草稿预算已满',
  reject_review_field: '审核字段未通过',
  reject_public_projection: '公开投影未通过',
}

export interface QzoneSelectionSummary {
  scope: 'process_lifetime' | string
  total: number
  accepted: number
  rejected: number
  acceptance_rate: number
}

export interface QzoneHealthResponse {
  configured_enabled: boolean
  dry_run: boolean
  live_allowed: boolean
  advanced_enabled: boolean
  manual_review: boolean
  wire_profile_id: string
  profile_validated: boolean
  salience_threshold: number
  allowed_sources: string[]
  max_drafts_per_tick: number
  max_drafts_per_day: number
  counts: QzoneHealthCounts
  selection_decisions: Record<QzoneSelectionReason, number>
  selection_summary: QzoneSelectionSummary
  live_publish_gate: QzoneLivePublishGate
}

/**
 * Sanitized dry-run descriptor from transport.describe().
 * Credentials and raw HTTP payloads are intentionally absent.
 */
export interface QzoneDryRunDescriptor {
  profile_id?: string
  validated?: boolean
  method?: string
  host?: string
  path?: string
  field_names?: string[]
  content_chars?: number
  content_sha256?: string
  follow_redirects?: boolean
  [key: string]: unknown
}

export const QZONE_STATUS_LABELS: Record<QzoneDraftStatus, string> = {
  pending_review: '待审核',
  approved: '已通过',
  dispatching: '派发中',
  published: '已发布',
  rejected: '已拒绝',
  failed: '失败',
  unknown: '状态未知',
}

export type QzoneStatusBadge = 'success' | 'warning' | 'error' | 'info' | 'default'

export const QZONE_STATUS_BADGE: Record<QzoneDraftStatus, QzoneStatusBadge> = {
  pending_review: 'warning',
  approved: 'info',
  dispatching: 'info',
  published: 'success',
  rejected: 'error',
  failed: 'error',
  unknown: 'warning',
}

export function isQzoneDraftStatus(value: string): value is QzoneDraftStatus {
  return (QZONE_DRAFT_STATUSES as readonly string[]).includes(value)
}

export function statusLabel(status: string): string {
  if (isQzoneDraftStatus(status)) return QZONE_STATUS_LABELS[status]
  return status || '未知'
}

export function statusBadge(status: string): QzoneStatusBadge {
  if (isQzoneDraftStatus(status)) return QZONE_STATUS_BADGE[status]
  return 'default'
}
