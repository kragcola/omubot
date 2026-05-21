/**
 * Badge labels and tag types for the KnowledgeView console.
 *
 * Extracted in PR B-1 of the KnowledgeView refactor.
 */

import type { ContextHit } from './types'

type NaiveTagType = 'default' | 'success' | 'warning' | 'error' | 'info'

export function sourceStatusType(status: string): NaiveTagType {
  return status === 'indexed' ? 'success' : 'warning'
}

export function hitTypeLabel(type: string): string {
  if (type === 'memory_card') return '记忆卡片'
  if (type === 'doc_chunk') return '文档片段'
  return '图谱事实'
}

export function hitTypeTag(type: ContextHit['type']): NaiveTagType {
  if (type === 'memory_card') return 'success'
  if (type === 'doc_chunk') return 'info'
  return 'warning'
}
