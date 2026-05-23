export type LearningStageKey = 'candidate' | 'review' | 'approved' | 'hits' | 'archived'

export type LearningSortKey = 'newest' | 'confidence' | 'group'

export type LearningNounKey =
  | 'slang'
  | 'style'
  | 'episode'
  | 'memory'
  | 'fact'
  | 'graph_relation'

export type LearningExtractNounKey = 'slang' | 'style' | 'consolidator'

export type LearningExtractRunStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'partial_failed'
  | 'failed'
  | 'not_found'

export type LearningExtractNounStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'skipped'
  | 'failed'
  | 'timeout'
  | 'cancelled'

export interface LearningExtractResult {
  ok?: boolean
  noun?: string
  run_id?: string
  error?: string
  skipped?: boolean
  [key: string]: unknown
}

export interface LearningExtractNounProgress {
  status: LearningExtractNounStatus
  result: LearningExtractResult | null
  error: string
  updated_at: string
}

export interface LearningExtractRun {
  ok: boolean
  run_id: string
  status: LearningExtractRunStatus
  error?: string
  started_at: string
  updated_at: string
  finished_at: string
  group_id: string
  params: Record<string, unknown>
  nouns: Partial<Record<LearningExtractNounKey, LearningExtractNounProgress>>
  results: Record<string, LearningExtractResult>
}

export interface StageStripItem {
  key: LearningStageKey
  eyebrow: string
  label: string
  description: string
  total: number
  byNoun: Record<LearningNounKey, number | null>
}

export interface LearningItem {
  id: string
  noun: LearningNounKey
  kind_label: string
  content: string
  content_full: string
  group_id: string
  created_at: string
  status: string
  status_label: string
  confidence: number | null
  deep_link: string
  review_drawer: 'slang' | 'style' | 'episode' | 'consolidator' | null
  source: string
}

export interface LearningItemsResponse {
  items: LearningItem[]
  next_cursor: string
  has_more: boolean
  warnings: Array<{ noun: string, error: string }>
}
