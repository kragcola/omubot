<script setup lang="ts">
import {
  CheckmarkCircleOutline,
  CloseCircleOutline,
  CreateOutline,
  TimeOutline,
} from '@vicons/ionicons5'
import type { DataTableColumns } from 'naive-ui'
import EmptyState from '../../../../components/common/EmptyState.vue'
import {
  DOMAIN_LABEL,
  DOMAIN_TAG_TYPE,
  EPISODE_FIELDS,
  type Revision,
  STATE_LABEL,
  STATE_TAG_TYPE,
  timeText,
  useMemoryConsoleInject,
} from './state'

const console_ = useMemoryConsoleInject()
const {
  detailTarget,
  closeDetail,
  revisions,
  revisionsLoading,
  editing,
  editPayload,
  editReason,
  editSubmitting,
  startEdit,
  cancelEdit,
  submitEdit,
  decideTarget,
  decideAction,
  decideReason,
  decideSubmitting,
  closeDecide,
  submitDecide,
  openDecide,
  canEdit,
} = console_

const showDetail = computed({
  get: () => detailTarget.value !== null,
  set: (val: boolean) => {
    if (!val) closeDetail()
  },
})

const showDecideDialog = computed({
  get: () => decideTarget.value !== null,
  set: (val: boolean) => {
    if (!val) closeDecide()
  },
})

const revisionColumns = computed<DataTableColumns<Revision>>(() => [
  {
    title: '时间',
    key: 'created_at',
    width: 170,
    render: row => timeText(row.created_at),
  },
  {
    title: '动作',
    key: 'action',
    width: 160,
    render: row => h(
      resolveComponent('NTag') as any,
      { size: 'small', type: 'default' },
      () => row.action,
    ),
  },
  { title: '操作者', key: 'actor', width: 110 },
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
    v-model:show="showDecideDialog"
    preset="card"
    :title="decideTarget ? `${decideAction === 'approved' ? '批准' : '拒绝'} · ${decideTarget.candidate_id}` : '操作'"
    style="width: 540px"
    :mask-closable="!decideSubmitting"
    :close-on-esc="!decideSubmitting"
  >
    <div v-if="decideTarget" class="mc-decide">
      <p class="mc-decide__line">
        <span class="mc-decide__label">域 / 状态</span>
        <NTag size="small" :type="DOMAIN_TAG_TYPE[decideTarget.domain] || 'default'">
          {{ DOMAIN_LABEL[decideTarget.domain] || decideTarget.domain }}
        </NTag>
        <NTag size="small" :type="STATE_TAG_TYPE[decideTarget.state] || 'default'">
          {{ STATE_LABEL[decideTarget.state] || decideTarget.state }}
        </NTag>
      </p>
      <p class="mc-decide__line">
        <span class="mc-decide__label">置信 / 群</span>
        <span class="mc-decide__value">{{ Math.round(decideTarget.confidence * 100) }}% · {{ decideTarget.group_id || '—' }}</span>
      </p>
      <NDivider class="mc-decide__divider" />
      <NFormItem :label="`${decideAction === 'approved' ? '批准' : '拒绝'}理由`">
        <NInput
          v-model:value="decideReason"
          type="textarea"
          :placeholder="decideAction === 'approved'
            ? '说明为什么这条候选值得保留'
            : '说明为什么拒绝（误学习 / 与人格冲突 / 重复）'"
          :autosize="{ minRows: 2, maxRows: 5 }"
          maxlength="200"
          show-count
        />
      </NFormItem>
      <p
        v-if="decideAction === 'approved' && decideTarget.domain === 'episode'"
        class="mc-decide__note"
      >
        批准后将自动 promote 为 EpisodeStore 中的 dry_run 经验；可在「经验」节点继续推进。
      </p>
    </div>
    <template #footer>
      <NSpace justify="end" :size="8">
        <NButton :disabled="decideSubmitting" @click="showDecideDialog = false">
          取消
        </NButton>
        <NButton
          :type="decideAction === 'approved' ? 'success' : 'error'"
          :loading="decideSubmitting"
          @click="submitDecide"
        >
          确认{{ decideAction === 'approved' ? '批准' : '拒绝' }}
        </NButton>
      </NSpace>
    </template>
  </NModal>

  <NDrawer v-model:show="showDetail" :width="780" placement="right">
    <NDrawerContent
      :title="detailTarget?.candidate_id || '候选详情'"
      :native-scrollbar="false"
    >
      <div v-if="detailTarget" class="mc-detail">
        <section class="mc-detail__section">
          <h3 class="mc-detail__title">
            基本信息
          </h3>
          <div class="mc-detail__grid">
            <div class="mc-detail__item">
              <div class="mc-detail__label">
                域
              </div>
              <NTag size="small" :type="DOMAIN_TAG_TYPE[detailTarget.domain] || 'default'">
                {{ DOMAIN_LABEL[detailTarget.domain] || detailTarget.domain }}
              </NTag>
            </div>
            <div class="mc-detail__item">
              <div class="mc-detail__label">
                状态
              </div>
              <NTag size="small" :type="STATE_TAG_TYPE[detailTarget.state] || 'default'">
                {{ STATE_LABEL[detailTarget.state] || detailTarget.state }}
              </NTag>
            </div>
            <div class="mc-detail__item">
              <div class="mc-detail__label">
                群 / scope
              </div>
              <div>{{ detailTarget.scope }} · {{ detailTarget.group_id || '—' }}</div>
            </div>
            <div class="mc-detail__item">
              <div class="mc-detail__label">
                置信度
              </div>
              <div>{{ Math.round(detailTarget.confidence * 100) }}%</div>
            </div>
            <div class="mc-detail__item">
              <div class="mc-detail__label">
                Run ID
              </div>
              <div class="mc-detail__mono">
                {{ detailTarget.run_id }}
              </div>
            </div>
            <div class="mc-detail__item">
              <div class="mc-detail__label">
                Cluster ID
              </div>
              <div class="mc-detail__mono">
                {{ detailTarget.normalizer_cluster_id || '—' }}
              </div>
            </div>
            <div class="mc-detail__item">
              <div class="mc-detail__label">
                创建时间
              </div>
              <div>{{ timeText(detailTarget.created_at) }}</div>
            </div>
            <div v-if="detailTarget.decided_at" class="mc-detail__item">
              <div class="mc-detail__label">
                审决时间
              </div>
              <div>{{ timeText(detailTarget.decided_at) }} · {{ detailTarget.decided_by }}</div>
            </div>
          </div>
        </section>

        <section class="mc-detail__section">
          <div class="mc-detail__title-row">
            <h3 class="mc-detail__title">
              Payload
            </h3>
            <div v-if="canEdit && !editing">
              <NButton size="tiny" secondary @click="startEdit">
                <template #icon>
                  <NIcon :component="CreateOutline" />
                </template>
                编辑 reflection
              </NButton>
            </div>
          </div>

          <div v-if="detailTarget.domain === 'episode' && editing" class="mc-edit">
            <NFormItem
              v-for="field in EPISODE_FIELDS"
              :key="field.key"
              :label="field.label"
            >
              <NInput
                v-model:value="editPayload[field.key]"
                type="textarea"
                :autosize="{ minRows: 2, maxRows: 6 }"
                :placeholder="field.key === 'reflection' ? '补改 LLM 漏写的反思 — 这条会用作 prompt 注入材料' : '可留空'"
              />
            </NFormItem>
            <NFormItem label="编辑理由">
              <NInput
                v-model:value="editReason"
                type="textarea"
                placeholder="说明为什么补改（如：reflection 字段 LLM 漏写）"
                :autosize="{ minRows: 2, maxRows: 4 }"
                maxlength="200"
                show-count
              />
            </NFormItem>
            <NSpace justify="end" :size="8">
              <NButton :disabled="editSubmitting" @click="cancelEdit">
                取消
              </NButton>
              <NButton
                type="primary"
                :loading="editSubmitting"
                @click="submitEdit"
              >
                保存 payload
              </NButton>
            </NSpace>
            <p class="mc-edit__note">
              Payload 会按 episode 域 schema 投影；未知字段会被静默丢弃；保存后会写入 candidate_revisions。
            </p>
          </div>

          <template v-else-if="detailTarget.domain === 'episode'">
            <div
              v-for="field in EPISODE_FIELDS"
              :key="field.key"
              class="mc-detail__field"
            >
              <div class="mc-detail__label">
                {{ field.label }}
              </div>
              <p class="mc-detail__text">
                {{ detailTarget.payload[field.key] || '—' }}
              </p>
            </div>
          </template>

          <pre v-else class="mc-detail__json">{{ JSON.stringify(detailTarget.payload, null, 2) }}</pre>
        </section>

        <section v-if="detailTarget.source_message_pks.length" class="mc-detail__section">
          <h3 class="mc-detail__title">
            源消息 PK
          </h3>
          <NSpace :size="6" wrap>
            <NTag
              v-for="pk in detailTarget.source_message_pks"
              :key="pk"
              size="small"
              round
            >
              {{ pk }}
            </NTag>
          </NSpace>
        </section>

        <section class="mc-detail__section">
          <h3 class="mc-detail__title mc-detail__title--with-icon">
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
            description="payload 编辑会写入此处，便于审计。"
            :icon="TimeOutline"
            compact
          />
          <div v-else class="mc-detail__loading">
            <NSpin size="small" />
          </div>
        </section>

        <NSpace v-if="detailTarget.state === 'dry_run'" justify="end" :size="8">
          <NButton type="error" secondary @click="openDecide(detailTarget, 'rejected')">
            <template #icon>
              <NIcon :component="CloseCircleOutline" />
            </template>
            拒绝
          </NButton>
          <NButton type="success" @click="openDecide(detailTarget, 'approved')">
            <template #icon>
              <NIcon :component="CheckmarkCircleOutline" />
            </template>
            批准
          </NButton>
        </NSpace>
      </div>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.mc-decide__line {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 0 0 8px;
  font-size: 13px;
}

.mc-decide__label {
  flex-shrink: 0;
  width: 90px;
  color: var(--om-text-2);
}

.mc-decide__value {
  color: var(--om-text-1);
}

.mc-decide__divider {
  margin: 14px 0;
}

.mc-decide__note {
  margin-top: 8px;
  padding: 10px 12px;
  border: 1px solid var(--om-border);
  border-radius: 8px;
  background: var(--om-surface-2);
  color: var(--om-text-2);
  font-size: 12px;
  line-height: 1.55;
}

.mc-detail {
  padding: 4px 2px 16px;
}

.mc-detail__section {
  margin-bottom: 22px;
}

.mc-detail__section + .mc-detail__section {
  margin-top: 22px;
  padding-top: 22px;
  border-top: 1px dashed var(--om-border);
}

.mc-detail__title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.mc-detail__title {
  margin: 0 0 12px;
  color: var(--om-text-1);
  font-size: 15px;
  font-weight: 600;
  letter-spacing: -0.01em;
}

.mc-detail__title--with-icon {
  display: flex;
  align-items: center;
  gap: 8px;
}

.mc-detail__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px 18px;
}

.mc-detail__item {
  min-width: 0;
}

.mc-detail__label {
  margin-bottom: 4px;
  color: var(--om-text-2);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.02em;
}

.mc-detail__field {
  margin-bottom: 14px;
}

.mc-detail__text {
  margin: 0;
  padding: 10px 12px;
  border: 1px solid var(--om-border);
  border-radius: 10px;
  background: var(--om-surface-2);
  color: var(--om-text-1);
  font-size: 13px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
}

.mc-detail__mono {
  font-family: ui-monospace, 'SFMono-Regular', Menlo, monospace;
  font-size: 12px;
  color: var(--om-text-2);
  word-break: break-all;
}

.mc-detail__json {
  margin: 0;
  padding: 12px;
  border: 1px solid var(--om-border);
  border-radius: 10px;
  background: var(--om-surface-2);
  color: var(--om-text-1);
  font-family: ui-monospace, 'SFMono-Regular', Menlo, monospace;
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}

.mc-detail__loading {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px 0;
}

.mc-edit__note {
  margin: 12px 0 0;
  padding: 10px 12px;
  border: 1px solid var(--om-border);
  border-radius: 8px;
  background: var(--om-surface-2);
  color: var(--om-text-2);
  font-size: 12px;
  line-height: 1.55;
}
</style>
