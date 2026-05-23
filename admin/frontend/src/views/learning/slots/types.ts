import type { LearningDateFilter, LearningNounFilter, LearningStageKey } from '../types'

export interface NounSlotContext {
  noun: LearningNounFilter
  stage: LearningStageKey
  group: string
  date: LearningDateFilter
  refresh: () => void
}
