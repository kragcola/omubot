/**
 * Pure formatters for the KnowledgeView console.
 *
 * Extracted in PR B-1 of the KnowledgeView refactor.
 */

import type { GraphCandidate, GraphRelationship } from './types'

export function scoreText(value?: number): string {
  if (typeof value !== 'number') return '--'
  return value.toFixed(value >= 10 ? 1 : 3)
}

export function percentText(value?: number): string {
  if (typeof value !== 'number') return '--'
  return `${Math.round(value * 100)}%`
}

export function numberText(value?: number): string {
  if (typeof value !== 'number') return '--'
  return Math.round(value).toLocaleString()
}

export function shortHash(value?: string): string {
  if (!value) return '--'
  return value.slice(0, 12)
}

export function evidenceText(candidate: GraphCandidate): string {
  const evidence = candidate.evidence || {}
  const id = (evidence as Record<string, unknown>).id
    ?? (evidence as Record<string, unknown>).card_id
    ?? (evidence as Record<string, unknown>).chunk_id
    ?? ''
  const quoteRaw = (evidence as Record<string, unknown>).quote
  const quote = quoteRaw ? ` · ${String(quoteRaw).slice(0, 80)}` : ''
  return id ? `${String(id)}${quote}` : '未记录证据'
}

export function relationshipEvidenceText(rel: GraphRelationship): string {
  const evidence = rel.evidence || []
  if (!evidence.length) return '未记录证据'
  return evidence
    .map((item) => {
      const record = item as Record<string, unknown>
      const id = record.id ?? record.card_id ?? record.chunk_id ?? ''
      const quote = record.quote ? ` · ${String(record.quote).slice(0, 90)}` : ''
      return id ? `${String(id)}${quote}` : quote.replace(/^ · /, '')
    })
    .filter(Boolean)
    .join(' / ') || '未记录证据'
}

export function relationshipScopeText(rel: GraphRelationship): string {
  const scope = rel.scope || 'global'
  const scopeId = rel.scope_id || 'global'
  if (scope === 'user') return `用户 ${scopeId}`
  if (scope === 'group') return `群 ${scopeId}`
  return '全局'
}

export function metricRatioEntries(data?: Record<string, number>): Array<[string, number]> {
  return Object.entries(data || {})
    .sort((left, right) => right[1] - left[1])
    .slice(0, 8)
}

export function topEntry(data?: Record<string, number>): string {
  const entries = Object.entries(data || {})
  if (!entries.length) return '—'
  entries.sort((a, b) => b[1] - a[1])
  const [name, count] = entries[0]
  return `${name} · ${count}`
}

export function isNotFound(error: unknown): boolean {
  return (error as { response?: { status?: number } })?.response?.status === 404
}
