<script setup lang="ts">
import { FlashOutline } from '@vicons/ionicons5'
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
        <div class="candidate-card__body">
          <div>
            <div class="relationship-card__triple">
              <strong>{{ candidate.subject }}</strong>
              <span>{{ candidate.predicate }}</span>
              <strong>{{ candidate.object }}</strong>
            </div>
            <p>{{ evidenceText(candidate) }}</p>
            <div class="relationship-card__meta">
              <NTag round size="small" type="warning">
                {{ percentText(candidate.confidence) }}
              </NTag>
              <span>{{ candidate.source }}</span>
              <span>{{ candidate.candidate_id }}</span>
            </div>
          </div>
          <div class="candidate-card__actions">
            <NInput
              v-model:value="rejectNotes[candidate.candidate_id]"
              clearable
              placeholder="拒绝备注，可选"
            />
            <NSpace justify="end" :size="8">
              <NPopconfirm
                :positive-text="'确认拒绝'"
                :negative-text="'取消'"
                @positive-click="emit('reject', candidate)"
              >
                <template #trigger>
                  <NButton
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
                type="primary"
                :loading="candidateBusy === candidate.candidate_id"
                @click="emit('approve', candidate)"
              >
                通过
              </NButton>
            </NSpace>
          </div>
        </div>
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
  padding: 16px;
}

.candidate-card__body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(280px, 360px);
  gap: 16px;
  align-items: start;
}

.candidate-card__actions {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.relationship-card__triple {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  color: var(--om-text-1);
}

.relationship-card__triple span {
  padding: 4px 10px;
  border-radius: 999px;
  background: rgba(49, 108, 114, 0.1);
  color: var(--om-primary);
  font-size: 12px;
  font-weight: 700;
}

.relationship-card__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
  color: var(--om-text-3);
  font-size: 12px;
}

.candidate-card p {
  margin: 12px 0 0;
  color: var(--om-text-1);
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
}

@media (max-width: 1180px) {
  .candidate-card__body {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
