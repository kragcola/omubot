<script setup lang="ts">
import { ArrowForwardOutline, FlashOutline } from '@vicons/ionicons5'
import AppCard from '../../../components/common/AppCard.vue'
import EmptyState from '../../../components/common/EmptyState.vue'
import { evidenceText, percentText } from '../helpers/formatters'
import type { GraphCandidate } from '../helpers/types'

interface Props {
  candidates: GraphCandidate[]
  candidateLoading: boolean
  candidateBusy: string
  graphUnsupported: boolean
}

defineProps<Props>()

const rejectNotes = defineModel<Record<string, string>>('rejectNotes', { required: true })

const emit = defineEmits<{
  (e: 'approve', candidate: GraphCandidate): void
  (e: 'reject', candidate: GraphCandidate): void
}>()

function confidenceTone(value: number): 'success' | 'warning' | 'error' {
  if (value >= 0.75) return 'success'
  if (value >= 0.5) return 'warning'
  return 'error'
}
</script>

<template>
  <NSpin :show="candidateLoading">
    <div v-if="candidates.length" class="candidate-list">
      <AppCard
        v-for="candidate in candidates"
        :key="candidate.candidate_id"
        bordered
        embedded
        class="candidate-card"
      >
        <header class="candidate-card__head">
          <div class="candidate-triple">
            <span class="candidate-triple__node candidate-triple__node--subject">
              {{ candidate.subject }}
            </span>
            <span class="candidate-triple__arrow">
              <NIcon :component="ArrowForwardOutline" />
            </span>
            <span class="candidate-triple__predicate">
              {{ candidate.predicate }}
            </span>
            <span class="candidate-triple__arrow">
              <NIcon :component="ArrowForwardOutline" />
            </span>
            <span class="candidate-triple__node candidate-triple__node--object">
              {{ candidate.object }}
            </span>
          </div>
          <NTag round size="small" :type="confidenceTone(candidate.confidence)">
            置信度 {{ percentText(candidate.confidence) }}
          </NTag>
        </header>

        <p class="candidate-card__evidence">{{ evidenceText(candidate) }}</p>

        <footer class="candidate-card__foot">
          <div class="candidate-card__meta">
            <span class="candidate-card__meta-item">
              <em>来源</em>
              <span>{{ candidate.source }}</span>
            </span>
            <span class="candidate-card__meta-item">
              <em>ID</em>
              <span class="candidate-card__meta-mono">{{ candidate.candidate_id }}</span>
            </span>
          </div>
          <div class="candidate-card__actions">
            <NInput
              v-model:value="rejectNotes[candidate.candidate_id]"
              size="small"
              clearable
              placeholder="拒绝备注，可选"
              class="candidate-card__note"
            />
            <NPopconfirm
              :positive-text="'确认拒绝'"
              :negative-text="'取消'"
              @positive-click="emit('reject', candidate)"
            >
              <template #trigger>
                <NButton
                  size="small"
                  secondary
                  type="error"
                  :loading="candidateBusy === candidate.candidate_id"
                >
                  拒绝
                </NButton>
              </template>
              拒绝后该候选不再进入图谱，确认？
            </NPopconfirm>
            <NButton
              size="small"
              type="primary"
              :loading="candidateBusy === candidate.candidate_id"
              @click="emit('approve', candidate)"
            >
              通过
            </NButton>
          </div>
        </footer>
      </AppCard>
    </div>
    <EmptyState
      v-else-if="graphUnsupported"
      title="当前后端还没有图谱候选接口"
      description="请重建/重启 Bot，让后端 API 与新版前端保持一致。"
      :icon="FlashOutline"
    />
    <EmptyState
      v-else
      title="没有待审核候选"
      description="当前没有中置信图谱候选。后续接入自动抽取后，这里会成为治理入口。"
      :icon="FlashOutline"
    />
  </NSpin>
</template>

<style scoped>
.candidate-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.candidate-card {
  padding: 16px 18px;
  display: grid;
  gap: 12px;
}

.candidate-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.candidate-triple {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
}

.candidate-triple__node {
  padding: 4px 12px;
  border-radius: 8px;
  background: var(--om-surface-2);
  border: 1px solid var(--om-border);
  color: var(--om-text-1);
  font-weight: 600;
  font-size: 13px;
  line-height: 1.5;
  word-break: break-word;
}

.candidate-triple__node--subject {
  border-color: color-mix(in srgb, var(--om-primary) 35%, var(--om-border));
  color: var(--om-primary);
  background: color-mix(in srgb, var(--om-primary) 8%, var(--om-surface-2));
}

.candidate-triple__node--object {
  border-color: color-mix(in srgb, var(--om-primary) 24%, var(--om-border));
  color: var(--om-text-1);
  background: color-mix(in srgb, var(--om-primary) 5%, var(--om-surface-2));
}

.candidate-triple__predicate {
  padding: 3px 10px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--om-primary) 12%, transparent);
  color: var(--om-primary);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.02em;
}

.candidate-triple__arrow {
  display: inline-flex;
  align-items: center;
  color: var(--om-text-3);
  font-size: 14px;
}

.candidate-card__evidence {
  margin: 0;
  padding: 10px 12px;
  border-left: 2px solid color-mix(in srgb, var(--om-primary) 40%, var(--om-border));
  background: var(--om-surface-2);
  border-radius: 0 8px 8px 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}

.candidate-card__foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding-top: 6px;
  border-top: 1px dashed var(--om-border);
}

.candidate-card__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  color: var(--om-text-3);
  font-size: 12px;
}

.candidate-card__meta-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.candidate-card__meta-item em {
  font-style: normal;
  color: var(--om-text-3);
  letter-spacing: 0.04em;
  font-size: 11px;
  text-transform: uppercase;
}

.candidate-card__meta-item span {
  color: var(--om-text-2);
}

.candidate-card__meta-mono {
  font-family:
    ui-monospace,
    SFMono-Regular,
    Menlo,
    Consolas,
    monospace;
  font-size: 11px;
}

.candidate-card__actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.candidate-card__note {
  width: 220px;
  max-width: 240px;
}

@media (max-width: 1180px) {
  .candidate-card__foot {
    align-items: stretch;
  }

  .candidate-card__actions {
    width: 100%;
    justify-content: flex-end;
  }

  .candidate-card__note {
    flex: 1;
    width: auto;
    max-width: none;
  }
}
</style>
