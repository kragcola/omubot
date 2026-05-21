/**
 * Pure formatters for the SlangView console.
 *
 * Extracted in PR B-1 of the SlangView refactor.
 */

import type { SlangSettings, SlangTerm } from './types'

export function formatTime(value?: string): string {
  if (!value) return '--'
  return value.replace('T', ' ').slice(0, 16)
}

export function confidenceText(value: number | null | undefined): string {
  return `${Math.round(Number(value || 0) * 100)}%`
}

export function numberSetting(value: unknown, fallback: number): number {
  const next = Number(value)
  return Number.isFinite(next) ? next : fallback
}

export function formatSearchQueries(term?: SlangTerm | null): string {
  const value = term?.meta?.search_queries
  if (Array.isArray(value)) return value.join(' / ')
  return typeof value === 'string' ? value : ''
}

export const DEFAULT_SLANG_SETTINGS: SlangSettings = {
  learning_enabled: true,
  injection_enabled: true,
  review_required: true,
  max_injected_terms: 8,
  max_indirect_inject_terms: 0,
  extract_interval_minutes: 30,
  candidate_min_count: 2,
  group_allowlist: [],
  repeat_policy: 'understand_only',
  extraction_batch_limit: 80,
  auto_promote_global_enabled: false,
  global_promote_min_groups: 3,
  bulk_page_size: 50,
  stats_days: 14,
  stoplist: [],
  max_prompt_chars: 1200,
  daily_ai_review_enabled: true,
  daily_ai_review_times: ['04:00', '16:00'],
  daily_ai_review_search_enabled: true,
  daily_ai_auto_approve_enabled: false,
  daily_ai_auto_approve_min_confidence: 0.82,
  daily_ai_max_terms_per_group: 5,
  daily_ai_recent_message_limit: 200,
  backlog_review_enabled: true,
  backlog_review_batch_size: 50,
  backlog_review_min_confidence: 0,
  backlog_review_min_usage_count: 3,
  backlog_review_search_enabled: true,
  backlog_auto_approve_enabled: false,
  backlog_auto_approve_min_confidence: 0.82,
  backlog_kept_streak_limit: 2,
  backlog_local_evidence_count: 5,
  backlog_threshold_gating_enabled: true,
  drift_detection_enabled: true,
  drift_min_confidence: 0.65,
  drift_semantic_gate_enabled: true,
  drift_age_out_days: 14,
  lookup_tool_enabled: true,
  min_inject_confidence: 0,
  semantic_backend: 'ngram',
}

/**
 * Merge an incoming partial settings payload onto a fallback (current state)
 * with defaults filling any missing keys. Numeric fields are coerced through
 * `numberSetting()` so backend strings never poison the form state.
 */
export function mergeSettings(
  incoming: Partial<SlangSettings> = {},
  fallback: Partial<SlangSettings> = {},
): SlangSettings {
  const merged: SlangSettings = { ...DEFAULT_SLANG_SETTINGS, ...fallback, ...incoming }
  return {
    ...merged,
    group_allowlist: Array.isArray(merged.group_allowlist) ? merged.group_allowlist : [],
    stoplist: Array.isArray(merged.stoplist) ? merged.stoplist : [],
    daily_ai_review_times: Array.isArray(merged.daily_ai_review_times) ? merged.daily_ai_review_times : DEFAULT_SLANG_SETTINGS.daily_ai_review_times,
    max_injected_terms: numberSetting(merged.max_injected_terms, DEFAULT_SLANG_SETTINGS.max_injected_terms),
    max_indirect_inject_terms: numberSetting(merged.max_indirect_inject_terms, DEFAULT_SLANG_SETTINGS.max_indirect_inject_terms),
    extract_interval_minutes: numberSetting(merged.extract_interval_minutes, DEFAULT_SLANG_SETTINGS.extract_interval_minutes),
    candidate_min_count: numberSetting(merged.candidate_min_count, DEFAULT_SLANG_SETTINGS.candidate_min_count),
    extraction_batch_limit: numberSetting(merged.extraction_batch_limit, DEFAULT_SLANG_SETTINGS.extraction_batch_limit),
    global_promote_min_groups: numberSetting(merged.global_promote_min_groups, DEFAULT_SLANG_SETTINGS.global_promote_min_groups),
    bulk_page_size: numberSetting(merged.bulk_page_size, DEFAULT_SLANG_SETTINGS.bulk_page_size),
    stats_days: numberSetting(merged.stats_days, DEFAULT_SLANG_SETTINGS.stats_days),
    max_prompt_chars: numberSetting(merged.max_prompt_chars, DEFAULT_SLANG_SETTINGS.max_prompt_chars),
    daily_ai_auto_approve_min_confidence: numberSetting(
      merged.daily_ai_auto_approve_min_confidence,
      DEFAULT_SLANG_SETTINGS.daily_ai_auto_approve_min_confidence,
    ),
    daily_ai_max_terms_per_group: numberSetting(
      merged.daily_ai_max_terms_per_group,
      DEFAULT_SLANG_SETTINGS.daily_ai_max_terms_per_group,
    ),
    daily_ai_recent_message_limit: numberSetting(
      merged.daily_ai_recent_message_limit,
      DEFAULT_SLANG_SETTINGS.daily_ai_recent_message_limit,
    ),
    drift_min_confidence: numberSetting(merged.drift_min_confidence, DEFAULT_SLANG_SETTINGS.drift_min_confidence),
    drift_semantic_gate_enabled: typeof merged.drift_semantic_gate_enabled === 'boolean' ? merged.drift_semantic_gate_enabled : DEFAULT_SLANG_SETTINGS.drift_semantic_gate_enabled,
    drift_age_out_days: numberSetting(merged.drift_age_out_days, DEFAULT_SLANG_SETTINGS.drift_age_out_days),
    min_inject_confidence: numberSetting(merged.min_inject_confidence, DEFAULT_SLANG_SETTINGS.min_inject_confidence),
    backlog_review_min_usage_count: numberSetting(merged.backlog_review_min_usage_count, DEFAULT_SLANG_SETTINGS.backlog_review_min_usage_count),
    backlog_review_search_enabled: merged.backlog_review_search_enabled ?? DEFAULT_SLANG_SETTINGS.backlog_review_search_enabled,
    backlog_auto_approve_enabled: merged.backlog_auto_approve_enabled ?? DEFAULT_SLANG_SETTINGS.backlog_auto_approve_enabled,
    backlog_auto_approve_min_confidence: numberSetting(merged.backlog_auto_approve_min_confidence, DEFAULT_SLANG_SETTINGS.backlog_auto_approve_min_confidence),
    backlog_kept_streak_limit: numberSetting(merged.backlog_kept_streak_limit, DEFAULT_SLANG_SETTINGS.backlog_kept_streak_limit),
    backlog_local_evidence_count: numberSetting(merged.backlog_local_evidence_count, DEFAULT_SLANG_SETTINGS.backlog_local_evidence_count),
    backlog_threshold_gating_enabled: typeof merged.backlog_threshold_gating_enabled === 'boolean' ? merged.backlog_threshold_gating_enabled : DEFAULT_SLANG_SETTINGS.backlog_threshold_gating_enabled,
    semantic_backend: merged.semantic_backend === 'embedding' ? 'embedding' : 'ngram',
  }
}
