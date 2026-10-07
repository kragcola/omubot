import { apiRequest } from './client'
import type { GraphSelfFactObjectRequest, GraphSelfFactProposeRequest, GraphSelfFactReviewRequest, GraphSelfFactSourceView, GraphSelfFactView } from './generated'
import type { GraphAliasProposeRequest, GraphAliasView, GraphExtractRequest, GraphExtractView, GraphObjectPageView, GraphObjectRequest, GraphProjectionView, GraphRelationProposeRequest, GraphRelationView, GraphReviewRequest, GraphWalkRequest, KnowledgeSearchView } from './generated'

export type GraphObject = GraphRelationView | GraphAliasView
export type GraphKind = 'relation' | 'alias'
export function graphObjects(group: string, kind: GraphKind, after: string | null, signal: AbortSignal) {
  const query = new URLSearchParams({ group_id: group, kind, limit: '32' })
  if (after) query.set('after', after)
  return apiRequest<GraphObjectPageView>(`/api/admin/graph/objects?${query}`, { signal })
}
export function graphSources(group: string, query: string, signal: AbortSignal) {
  const params = new URLSearchParams({ group_id: group, query, limit: '5' })
  return apiRequest<KnowledgeSearchView>(`/api/admin/knowledge/search?${params}`, { signal })
}
export function proposeRelation(body: GraphRelationProposeRequest, signal: AbortSignal) {
  return apiRequest<GraphRelationView>('/api/admin/graph/relations', { method: 'POST', body, signal, adminMutation: true })
}
export function proposeAlias(body: GraphAliasProposeRequest, signal: AbortSignal) {
  return apiRequest<GraphAliasView>('/api/admin/graph/aliases', { method: 'POST', body, signal, adminMutation: true })
}
export function graphReview(body: GraphReviewRequest, signal: AbortSignal) {
  return apiRequest<GraphObject>('/api/admin/graph/review', { method: 'POST', body, signal, adminMutation: true })
}
export function graphTransition(action: 'apply' | 'revoke', body: GraphObjectRequest, signal: AbortSignal) {
  return apiRequest<GraphObject>(`/api/admin/graph/${action}`, { method: 'POST', body, signal, adminMutation: true })
}
export function graphWalk(body: GraphWalkRequest, signal: AbortSignal) {
  return apiRequest<GraphProjectionView>('/api/admin/graph/walk', { method: 'POST', body, signal, adminMutation: true })
}

export function extractGraph(body: GraphExtractRequest, signal: AbortSignal) {
  return apiRequest<GraphExtractView>('/api/admin/graph/extract', { method: 'POST', body, signal, adminMutation: true })
}

export function graphSelfSource(group: string, factId: string, signal: AbortSignal) {
  const query = new URLSearchParams({ group_id: group, fact_id: factId })
  return apiRequest<GraphSelfFactSourceView>(`/api/admin/graph/self-source?${query}`, { signal })
}
export function graphSelfRelation(group: string, relationId: string, signal: AbortSignal) {
  const query = new URLSearchParams({ group_id: group })
  return apiRequest<GraphSelfFactView>(`/api/admin/graph/self-relations/${encodeURIComponent(relationId)}?${query}`, { signal })
}
export function graphSelfPropose(body: GraphSelfFactProposeRequest, signal: AbortSignal) {
  return apiRequest<GraphSelfFactView>('/api/admin/graph/self-relations', { method: 'POST', body, signal, adminMutation: true })
}
export function graphSelfReview(body: GraphSelfFactReviewRequest, signal: AbortSignal) {
  return apiRequest<GraphSelfFactView>('/api/admin/graph/self-review', { method: 'POST', body, signal, adminMutation: true })
}
export function graphSelfTransition(action: 'apply' | 'revoke', body: GraphSelfFactObjectRequest, signal: AbortSignal) {
  return apiRequest<GraphSelfFactView>(`/api/admin/graph/self-${action}`, { method: 'POST', body, signal, adminMutation: true })
}
