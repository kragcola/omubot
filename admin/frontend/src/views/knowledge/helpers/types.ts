/**
 * Type definitions for the KnowledgeView console.
 *
 * Extracted in PR B-1 of the KnowledgeView refactor (web-refactor-plan §6.x).
 * Mirrors the shapes returned by `/api/admin/knowledge/*` and
 * `/api/admin/context/*`.
 */

export type KnowledgeTab =
  | 'sources'
  | 'workspace'

export type KnowledgeWorkspaceTab =
  | 'details'
  | 'pack'
  | 'metrics'

export type KnowledgeAdminTab =
  | 'candidates'
  | 'graph'
  | 'graph_nodes'

export interface KnowledgeStats {
  loaded?: boolean
  chunk_count?: number
  source_count?: number
  indexed_sources?: number
  skipped_sources?: number
  docs_dir?: string
  recursive?: boolean
  include?: string[]
  exclude?: string[]
  index_persisted?: boolean
  index_db_path?: string
}

export interface KnowledgeSource {
  source: string
  path: string
  status: string
  chunk_count: number
  source_hash?: string
  skipped_reason?: string
}

export interface KnowledgeResult {
  id?: string
  chunk_id?: string
  content: string
  source?: string
  title?: string
  score?: number
  metadata?: Record<string, unknown>
}

export interface ContextHit {
  id: string
  type: 'memory_card' | 'doc_chunk' | 'graph_fact'
  content: string
  score: number
  source: string
  title?: string
  scope?: string
  scope_id?: string
  status?: string
  retriever?: string
  metadata?: Record<string, unknown>
}

export interface ContextPack {
  text: string
  hits: ContextHit[]
  omitted_count: number
}

export interface ContextMetricRecent {
  created_at?: number
  query?: string
  user_id?: string
  group_id?: string
  hit_count?: number
  pack_chars?: number
  duplicate_count?: number
  omitted_count?: number
  error?: string
}

export interface ContextMetrics {
  total_queries: number
  miss_count: number
  miss_rate: number
  hit_count: number
  duplicate_hits: number
  duplicate_rate: number
  avg_pack_chars: number
  max_pack_chars: number
  omitted_total: number
  hit_type_counts: Record<string, number>
  hit_source_counts: Record<string, number>
  recent: ContextMetricRecent[]
}

export interface GraphEntity {
  name: string
  fact_count: number
}

export interface GraphRelationship {
  fact_id: string
  subject: string
  predicate: string
  object: string
  confidence: number
  status: string
  source: string
  scope?: string
  scope_id?: string
  supersedes?: string
  metadata?: Record<string, unknown>
  evidence?: Array<Record<string, unknown>>
  created_at?: string
  updated_at?: string
}

export interface GraphCandidate {
  candidate_id: string
  subject: string
  predicate: string
  object: string
  confidence: number
  status: string
  source: string
  evidence?: Record<string, unknown>
  created_at?: string
  updated_at?: string
  review_note?: string
}

export interface GraphNodeRow {
  node_id: string
  node_type: string
  source_table: string
  source_id: string
  scope: string
  group_id: string
  label: string
  status: string
  properties?: Record<string, unknown>
  created_at: string
  updated_at: string
  cross_group_visible?: boolean
}

export interface GraphEdgeRow {
  edge_id: string
  edge_type: string
  from_node_id: string
  to_node_id: string
  scope: string
  group_id: string
  confidence: number
  status: string
  evidence_refs?: string[]
  properties?: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface GraphNodeStats {
  total_nodes: number
  total_edges: number
  by_node_type: Record<string, number>
  by_edge_type: Record<string, number>
}

export interface SupersedeDraft {
  subject: string
  predicate: string
  object: string
  note: string
}
