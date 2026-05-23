<script setup lang="ts">
import { TimeOutline } from '@vicons/ionicons5'
import type { DataTableColumns } from 'naive-ui'
import EmptyState from '../../../../components/common/EmptyState.vue'
import {
  EPISODE_ACTION_LABEL,
  EPISODE_STATE_LABEL,
  EPISODE_STATE_TAG_TYPE,
  type EpisodeRevision,
  decayHint,
  useEpisodeConsoleInject,
} from './state'

const console_ = useEpisodeConsoleInject()
const {
  actionTarget,
  actionType,
  actionReason,
  actionSubmitting,
  closeActionDialog,
  submitAction,
  detailTarget,
  closeDetail,
  revisions,
  revisionsLoading,
} = console_

const showActionDialog = computed({
  get: () => actionTarget.value !== null,
  set: (val: boolean) => {
    if (!val) closeActionDialog()
  },
})

const showDetail = computed({
  get: () => detailTarget.value !== null,
  set: (val: boolean) => {
    if (!val) closeDetail()
  },
})

const revisionColumns = computed<DataTableColumns<EpisodeRevision>>(() => [
  { title: '时间', key: 'created_at', width: 170 },
  {
    title: '动作',
    key: 'action',
    width: 200,
    render: row => h(
      resolveComponent('NTag') as any,
      { size: 'small', type: 'default' },
      () => row.action,
    ),
  },
  { title: '操作者', key: 'actor', width: 110 },
  {
    title: '状态变化',
    key: 'transition',
    width: 200,
    render: row => row.prev_state || row.new_state
      ? `${row.prev_state || '—'} → ${row.new_state || '—'}`
      : h('span', { style: 'color: var(--om-text-3); font-size: 12px' }, '—'),
  },
  {
    title: '理由',
    key: 'reason',
    minWidth: 220,
    ellipsis: { tooltip: true },
    render: row => row.reason
      ? row.reason
      : h('span', { style: 'color: var(--om-text-3); font-size: 12px' }, '—'),
  },
])
</script>

<template>
  <NModal
    v-model:show="showActionDialog"
    preset="card"
    :title="actionTarget ? `${EPISODE_ACTION_LABEL[actionType]} · ${actionTarget.situation || actionTarget.episode_id}` : '操作'"
    style="width: 540px"
    :mask-closable="!actionSubmitting"
    :close-on-esc="!actionSubmitting"
  >
    <div v-if="actionTarget" class="ep-action-panel">
      <p class="ep-action-panel__line">
        <span class="ep-action-panel__label">当前状态</span>
        <NTag size="small" :type="EPISODE_STATE_TAG_TYPE[actionTarget.episode_state] || 'default'">
          {{ EPISODE_STATE_LABEL[actionTarget.episode_state] || actionTarget.episode_state }}
        </NTag>
      </p>
      <p class="ep-action-panel__line">
        <span class="ep-action-panel__label">来源 / 群</span>
        <span class="ep-action-panel__value">{{ actionTarget.source }} · {{ actionTarget.group_id || '—' }}</span>
      </p>
      <p class="ep-action-panel__line">
        <span class="ep-action-panel__label">置信度</span>
        <span class="ep-action-panel__value">{{ Math.round(actionTarget.confidence * 100) }}%</span>
      </p>

      <NDivider class="ep-action-panel__divider" />

      <NFormItem :label="`${EPISODE_ACTION_LABEL[actionType]}理由`">
        <NInput
          v-model:value="actionReason"
          type="textarea"
          :placeholder="actionType === 'approve' ? '说明为什么这条经验值得保留'
            : actionType === 'disable' ? '说明为什么停用（误学习 / 内容已过时 / 与人格冲突）'
              : '说明恢复理由'"
          :autosize="{ minRows: 2, maxRows: 5 }"
          maxlength="200"
          show-count
        />
      </NFormItem>

      <p class="ep-action-panel__note">
        理由会写入 episode_revisions，可在详情抽屉里查看完整历史。
      </p>
    </div>

    <template #footer>
      <NSpace justify="end" :size="8">
        <NButton :disabled="actionSubmitting" @click="showActionDialog = false">
          取消
        </NButton>
        <NButton
          :type="actionType === 'disable' ? 'error' : actionType === 'approve' ? 'success' : 'info'"
          :loading="actionSubmitting"
          @click="submitAction"
        >
          确认{{ EPISODE_ACTION_LABEL[actionType] }}
        </NButton>
      </NSpace>
    </template>
  </NModal>

  <NDrawer v-model:show="showDetail" :width="720" placement="right">
    <NDrawerContent
      :title="detailTarget?.situation || detailTarget?.episode_id || '经验详情'"
      :native-scrollbar="false"
    >
      <div v-if="detailTarget" class="ep-detail">
        <section class="ep-detail__section">
          <h3 class="ep-detail__title">
            基本信息
          </h3>
          <div class="ep-detail__grid">
            <div class="ep-detail__item">
              <div class="ep-detail__label">
                状态
              </div>
              <NTag size="small" :type="EPISODE_STATE_TAG_TYPE[detailTarget.episode_state] || 'default'">
                {{ EPISODE_STATE_LABEL[detailTarget.episode_state] || detailTarget.episode_state }}
              </NTag>
            </div>
            <div class="ep-detail__item">
              <div class="ep-detail__label">
                来源
              </div>
              <div>{{ detailTarget.source }}</div>
            </div>
            <div class="ep-detail__item">
              <div class="ep-detail__label">
                群 / 范围
              </div>
              <div>{{ detailTarget.scope }} · {{ detailTarget.group_id || '—' }}</div>
            </div>
            <div class="ep-detail__item">
              <div class="ep-detail__label">
                置信度
              </div>
              <div>{{ Math.round(detailTarget.confidence * 100) }}%</div>
            </div>
            <div class="ep-detail__item">
              <div class="ep-detail__label">
                衰减剩余
              </div>
              <div>{{ decayHint(detailTarget.decay_at) }}</div>
            </div>
            <div class="ep-detail__item">
              <div class="ep-detail__label">
                最近使用
              </div>
              <div>{{ detailTarget.last_used_at || '从未使用' }}</div>
            </div>
            <div class="ep-detail__item">
              <div class="ep-detail__label">
                创建时间
              </div>
              <div>{{ detailTarget.created_at }}</div>
            </div>
            <div class="ep-detail__item">
              <div class="ep-detail__label">
                更新时间
              </div>
              <div>{{ detailTarget.updated_at }}</div>
            </div>
          </div>
        </section>

        <section class="ep-detail__section">
          <h3 class="ep-detail__title">
            经验内容
          </h3>
          <div class="ep-detail__field">
            <div class="ep-detail__label">
              场景 situation
            </div>
            <p class="ep-detail__text">
              {{ detailTarget.situation || '—' }}
            </p>
          </div>
          <div v-if="detailTarget.observed_context" class="ep-detail__field">
            <div class="ep-detail__label">
              观察上下文
            </div>
            <p class="ep-detail__text">
              {{ detailTarget.observed_context }}
            </p>
          </div>
          <div v-if="detailTarget.action_taken" class="ep-detail__field">
            <div class="ep-detail__label">
              采取行动
            </div>
            <p class="ep-detail__text">
              {{ detailTarget.action_taken }}
            </p>
          </div>
          <div v-if="detailTarget.outcome_signal" class="ep-detail__field">
            <div class="ep-detail__label">
              结果信号
            </div>
            <p class="ep-detail__text">
              {{ detailTarget.outcome_signal }}
            </p>
          </div>
          <div v-if="detailTarget.reflection" class="ep-detail__field">
            <div class="ep-detail__label">
              反思
            </div>
            <p class="ep-detail__text">
              {{ detailTarget.reflection }}
            </p>
          </div>
          <div v-if="detailTarget.linked_memory_ids?.length" class="ep-detail__field">
            <div class="ep-detail__label">
              关联记忆
            </div>
            <NSpace :size="6" wrap>
              <NTag
                v-for="mid in detailTarget.linked_memory_ids"
                :key="mid"
                size="small"
                round
                type="info"
              >
                {{ mid }}
              </NTag>
            </NSpace>
          </div>
        </section>

        <section class="ep-detail__section">
          <h3 class="ep-detail__title ep-detail__title--with-icon">
            <NIcon :component="TimeOutline" :size="16" /> 修订历史
          </h3>
          <NDataTable
            v-if="revisions.length > 0"
            :columns="revisionColumns"
            :data="revisions"
            :loading="revisionsLoading"
            :bordered="false"
            size="small"
            :pagination="{ pageSize: 8 }"
          />
          <EmptyState
            v-else-if="!revisionsLoading"
            title="暂无修订记录"
            description="状态变更与跨群启用都会留下记录。"
            :icon="TimeOutline"
            compact
          />
          <div v-else class="ep-detail__loading">
            <NSpin size="small" />
          </div>
        </section>
      </div>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.ep-action-panel__line {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 0 0 8px;
  font-size: 13px;
}

.ep-action-panel__label {
  flex-shrink: 0;
  width: 90px;
  color: var(--om-text-2);
}

.ep-action-panel__value {
  color: var(--om-text-1);
}

.ep-action-panel__divider {
  margin: 14px 0;
}

.ep-action-panel__note {
  margin: 6px 0 0;
  color: var(--om-text-3);
  font-size: 12px;
}

.ep-detail__section {
  padding-bottom: 18px;
  border-bottom: 1px solid var(--om-border);
  margin-bottom: 18px;
}

.ep-detail__section:last-child {
  border-bottom: none;
}

.ep-detail__title {
  margin: 0 0 12px;
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 600;
}

.ep-detail__title--with-icon {
  display: flex;
  align-items: center;
  gap: 6px;
}

.ep-detail__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 18px;
}

.ep-detail__item {
  min-width: 0;
}

.ep-detail__label {
  margin-bottom: 4px;
  color: var(--om-text-3);
  font-size: 11px;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.ep-detail__field {
  margin-top: 12px;
}

.ep-detail__text {
  margin: 0;
  padding: 10px 12px;
  border-radius: 10px;
  background: var(--om-surface-2);
  color: var(--om-text-1);
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
}

.ep-detail__loading {
  display: flex;
  justify-content: center;
  padding: 24px 0;
}
</style>
