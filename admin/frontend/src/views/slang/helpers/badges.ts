/**
 * Badge labels, tag types, select options and AI review predicates for SlangView.
 *
 * Extracted in PR B-1 of the SlangView refactor.
 */

import type {
  RepeatPolicy,
  SlangDriftReview,
  SlangExtractionRun,
  SlangStatus,
  SlangTerm,
} from './types'

type NaiveTagType = 'default' | 'success' | 'warning' | 'error' | 'info'

export function statusLabel(status: SlangStatus): string {
  return {
    candidate: '待审核',
    approved: '已批准',
    muted: '已静音',
    expired: '已过期',
  }[status]
}

export function statusType(status: SlangStatus): NaiveTagType {
  return ({
    candidate: 'warning',
    approved: 'success',
    muted: 'default',
    expired: 'error',
  } as Record<SlangStatus, NaiveTagType>)[status]
}

export function driftStatusLabel(status: SlangDriftReview['status']): string {
  return ({
    open: '待处理',
    accepted: '已采纳',
    rejected: '已保留旧义',
    aliased: '已转别名',
    muted: '已静音',
    aged_out: '自动归档',
    auto_dismissed: 'AI 判同义',
    auto_aliased: 'AI 自动转别名',
  } as Record<SlangDriftReview['status'], string>)[status] || status
}

export function revisionActionLabel(action: string): string {
  const labels: Record<string, string> = {
    create_term: '创建词条',
    update_term: '编辑词条',
    candidate_update: '候选更新',
    ai_auto_review: 'AI 通过',
    human_approve: '人工确认',
    human_deny: '人工否决',
    return_to_candidate: '退回候选',
    merge_terms: '合并词条',
    merge_source_expired: '合并归档',
    drift_detected: '发现漂移',
    drift_dismissed: 'AI 判同义',
    drift_aliased_auto: 'AI 自动转别名',
    drift_aged_out: '漂移自动归档',
    drift_accept: '采纳新义',
    drift_reject: '保留旧义',
    drift_alias: '转成别名',
    drift_mute: '静音漂移',
    'set_status:approved': '批准',
    'set_status:muted': '静音',
    'set_status:expired': '过期',
    'set_status:candidate': '改为候选',
  }
  return labels[action] || action
}

export function policyLabel(policy: RepeatPolicy): string {
  return REPEAT_POLICY_OPTIONS.find(option => option.value === policy)?.label || '仅理解，不主动复述'
}

export function runKindLabel(run: SlangExtractionRun): string {
  if (run.meta?.kind === 'daily_ai_review') return 'AI 清池'
  if (run.meta?.kind === 'backlog_ai_review') return 'AI 清池'
  return '抽取'
}

export function isAiApproved(term?: SlangTerm | null): boolean {
  return Boolean(
    term && (
      term.source === 'ai_auto_review'
      || term.meta?.ai_approved === true
      || term.meta?.ai_review_decision === 'approved'
    ),
  )
}

export function isHumanReviewed(term?: SlangTerm | null): boolean {
  return Boolean(term?.meta?.human_reviewed === true)
}

export function needsHumanReview(term?: SlangTerm | null): boolean {
  return Boolean(isAiApproved(term) && term?.status === 'approved' && !isHumanReviewed(term))
}

export const STATUS_OPTIONS: Array<{ label: string, value: '' | SlangStatus }> = [
  { label: '全部状态', value: '' },
  { label: '待审核', value: 'candidate' },
  { label: '已批准', value: 'approved' },
  { label: '已静音', value: 'muted' },
  { label: '已过期', value: 'expired' },
]

export const CONFIDENCE_OPTIONS: Array<{ label: string, value: string }> = [
  { label: '全部置信度', value: '' },
  { label: '≥ 0.3', value: '0.3' },
  { label: '≥ 0.6', value: '0.6' },
  { label: '≥ 0.8', value: '0.8' },
]

export const SCOPE_OPTIONS: Array<{ label: string, value: '' | 'group' | 'global' }> = [
  { label: '全部作用域', value: '' },
  { label: '群内词条', value: 'group' },
  { label: '跨群候选', value: 'global' },
]

export const REPEAT_POLICY_OPTIONS: Array<{ label: string, value: RepeatPolicy }> = [
  { label: '仅理解，不主动复述', value: 'understand_only' },
  { label: '可自然改写解释', value: 'allow_rephrase' },
  { label: '可在合适语境使用', value: 'allow_use' },
]
