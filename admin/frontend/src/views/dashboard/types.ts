/**
 * Shared dashboard payload types.
 *
 * Mirrors the response of /api/admin/dashboard/cache-pipelines (built by
 * services/llm/llm_pipelines.build_cache_pipelines_payload + folded with
 * fold_recent_into_pipelines). Keeping types here lets both
 * CachePipelinePanel.vue and DashboardView.vue import without circular refs.
 */

export interface CachePipelineTaskMetric {
  task: string
  calls: number
  hit_tokens: number
  miss_tokens: number
  hit_pct: number | null
}

export interface CachePipelineSample {
  ts: string
  task: string
  hit_pct: number | null
  hit_tokens: number
  miss_tokens: number
}

export interface CachePipelineRecent {
  calls: number
  hit_tokens: number
  miss_tokens: number
  hit_pct: number | null
  samples: CachePipelineSample[]
}

export interface CachePipelineGroup {
  key: 'core_chat' | 'slang' | 'learning' | 'memory_graph'
  label: string
  tasks: string[]
  calls: number
  hit_tokens: number
  miss_tokens: number
  hit_pct: number | null
  per_task: CachePipelineTaskMetric[]
  recent?: CachePipelineRecent
}

export interface CachePipelineOverall {
  calls: number
  hit_tokens: number
  miss_tokens: number
  hit_pct: number | null
  recent?: CachePipelineRecent
}

export interface CachePipelineData {
  period: 'day' | 'week' | 'month'
  generated_at: string
  overall: CachePipelineOverall
  pipelines: CachePipelineGroup[]
}

// Chinese display labels for the 18 LLMTask values. Kept here (not in
// llm_pipelines.py) because it's purely a UI concern — the backend stays
// free of locale strings, and the dashboard owns its own naming.
export const TASK_LABEL_ZH: Record<string, string> = {
  main: '主聊',
  thinker: '思考',
  compact: '压缩',
  reply_gate: '闸门',
  slang: '黑话',
  slang_review: '复审',
  slang_drift: '漂移',
  slang_semantic: '语义',
  style: '风格',
  memo: '备忘',
  chat_private: '私聊',
  bilibili_intent: 'B 站',
  element_detect: '元素',
  vision: '视觉',
  graph_review: '图审',
  graph_edge_classifier: '边类',
  reflection_consolidator: '反思',
  episode_summarizer: '情节',
}

export function taskLabelZh(task: string): string {
  return TASK_LABEL_ZH[task] ?? task
}
