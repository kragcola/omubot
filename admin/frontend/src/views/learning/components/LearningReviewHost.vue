<script setup lang="ts">
import { CheckmarkCircleOutline, CloseCircleOutline, ReturnDownBackOutline } from '@vicons/ionicons5'
import { useMessage } from 'naive-ui'
import type { Component } from 'vue'
import AppDrawerHeader from '../../../components/common/AppDrawerHeader.vue'
import AppDrawerLayout from '../../../components/common/AppDrawerLayout.vue'
import AppPanelSection from '../../../components/common/AppPanelSection.vue'
import { api } from '../../../api/client'
import type { LearningItem } from '../types'

type ButtonType = 'default' | 'primary' | 'success' | 'warning' | 'error' | 'info'

interface ReviewAction {
  key: string
  label: string
  type?: ButtonType
  icon?: Component
}

const props = defineProps<{
  item: LearningItem | null
}>()

const show = defineModel<boolean>('show', { required: true })
const emit = defineEmits<{
  done: []
}>()

const router = useRouter()
const message = useMessage()
const loading = ref(false)
const submitting = ref(false)
const reason = ref('')
const detail = ref<Record<string, any> | null>(null)

const entityId = computed(() => {
  const item = props.item
  if (!item) return ''
  if (item.review_drawer === 'slang') return item.id.replace(/^slang-hit-/, '').replace(/^slang-/, '')
  if (item.review_drawer === 'style') return item.id.replace(/^style-/, '')
  if (item.review_drawer === 'episode') return item.id.replace(/^episode-/, '')
  if (item.review_drawer === 'consolidator') return item.id.replace(/^consolidator-/, '')
  return ''
})

const title = computed(() => props.item?.content || '学习条目')
const subtitle = computed(() => {
  if (!props.item) return ''
  const group = props.item.group_id ? `群 ${props.item.group_id}` : '全局'
  return `${props.item.kind_label} · ${group} · ${props.item.status_label}`
})

const actions = computed<ReviewAction[]>(() => {
  const item = props.item
  if (!item) return []
  if (item.review_drawer === 'slang') return slangActions(item)
  if (item.review_drawer === 'style') return styleActions(item)
  if (item.review_drawer === 'episode') return episodeActions(item)
  if (item.review_drawer === 'consolidator') return consolidatorActions(item)
  return []
})

const detailRows = computed(() => {
  const item = props.item
  if (!item) return []
  return [
    ['类型', item.kind_label],
    ['状态', item.status_label],
    ['来源群', item.group_id || '--'],
    ['置信度', formatConfidence(item.confidence)],
    ['来源', item.source || '--'],
    ['时间', formatTime(item.created_at)],
  ]
})

watch(
  () => [show.value, props.item?.id],
  () => {
    reason.value = ''
    detail.value = null
    if (show.value && props.item) {
      void loadDetail()
    }
  },
  { immediate: true },
)

async function loadDetail() {
  if (!props.item || !entityId.value) return
  loading.value = true
  try {
    if (props.item.review_drawer === 'slang') {
      const payload = await api<{ term?: Record<string, any> }>(`/api/admin/slang/terms/${entityId.value}`)
      detail.value = payload.term || null
    } else if (props.item.review_drawer === 'style') {
      const payload = await api<{ expression?: Record<string, any> }>(`/api/admin/style/expressions/${entityId.value}`)
      detail.value = payload.expression || null
    } else if (props.item.review_drawer === 'episode') {
      const payload = await api<{ episode?: Record<string, any> }>(`/api/admin/episodes/${entityId.value}`)
      detail.value = payload.episode || null
    }
  } catch (err) {
    detail.value = null
  } finally {
    loading.value = false
  }
}

async function submitAction(action: ReviewAction) {
  if (!props.item || !entityId.value) return
  submitting.value = true
  try {
    await runAction(props.item, action.key)
    message.success(`${action.label}成功`)
    show.value = false
    emit('done')
  } catch (err: any) {
    message.error(err?.data?.error || err?.message || `${action.label}失败`)
  } finally {
    submitting.value = false
  }
}

function openOriginalPage() {
  if (!props.item?.deep_link) return
  show.value = false
  void router.push(props.item.deep_link).catch(() => {})
}

async function runAction(item: LearningItem, actionKey: string) {
  if (item.review_drawer === 'slang') {
    await api(`/api/admin/slang/terms/${entityId.value}/${actionKey}`, { method: 'POST' })
    return
  }
  if (item.review_drawer === 'style') {
    await api(`/api/admin/style/expressions/${entityId.value}/status`, {
      method: 'POST',
      body: {
        status: actionKey,
        actor: 'admin',
        reason: reason.value.trim(),
      },
    })
    return
  }
  if (item.review_drawer === 'episode') {
    await api(`/api/admin/episodes/${entityId.value}/${actionKey}`, {
      method: 'POST',
      body: { reason: reason.value.trim() },
    })
    return
  }
  if (item.review_drawer === 'consolidator') {
    await api(`/api/admin/memory_consolidator/candidates/${entityId.value}/decide`, {
      method: 'POST',
      body: {
        state: actionKey,
        decided_by: 'admin',
        reason: reason.value.trim(),
      },
    })
  }
}

function slangActions(item: LearningItem): ReviewAction[] {
  if (item.status_label === 'AI 待复核') {
    return [
      { key: 'human-approve', label: '真实通过', type: 'success', icon: CheckmarkCircleOutline },
      { key: 'return-candidate', label: '退回候选', type: 'warning', icon: ReturnDownBackOutline },
      { key: 'deny', label: '否决静音', type: 'error', icon: CloseCircleOutline },
    ]
  }
  if (item.status === 'candidate') {
    return [
      { key: 'approve', label: '通过', type: 'success', icon: CheckmarkCircleOutline },
      { key: 'mute', label: '静音', type: 'warning', icon: CloseCircleOutline },
    ]
  }
  if (['muted', 'expired'].includes(item.status)) {
    return [{ key: 'approve', label: '重新通过', type: 'success', icon: CheckmarkCircleOutline }]
  }
  return [
    { key: 'mute', label: '静音', type: 'warning', icon: CloseCircleOutline },
    { key: 'expire', label: '过期归档', type: 'error', icon: CloseCircleOutline },
  ]
}

function styleActions(item: LearningItem): ReviewAction[] {
  if (item.status === 'pending') {
    return [
      { key: 'approved', label: '通过', type: 'success', icon: CheckmarkCircleOutline },
      { key: 'rejected', label: '拒绝', type: 'error', icon: CloseCircleOutline },
      { key: 'muted', label: '静音', type: 'warning', icon: CloseCircleOutline },
    ]
  }
  if (['rejected', 'muted'].includes(item.status)) {
    return [{ key: 'approved', label: '重新通过', type: 'success', icon: CheckmarkCircleOutline }]
  }
  return [
    { key: 'muted', label: '静音', type: 'warning', icon: CloseCircleOutline },
    { key: 'rejected', label: '拒绝', type: 'error', icon: CloseCircleOutline },
  ]
}

function episodeActions(item: LearningItem): ReviewAction[] {
  if (item.status === 'candidate') {
    return [
      { key: 'approve', label: '批准', type: 'success', icon: CheckmarkCircleOutline },
      { key: 'disable', label: '停用', type: 'warning', icon: CloseCircleOutline },
    ]
  }
  if (item.status === 'disabled') {
    return [{ key: 'restore', label: '恢复', type: 'success', icon: ReturnDownBackOutline }]
  }
  return [{ key: 'disable', label: '停用', type: 'warning', icon: CloseCircleOutline }]
}

function consolidatorActions(item: LearningItem): ReviewAction[] {
  if (item.status === 'approved') {
    return [{ key: 'rejected', label: '撤回拒绝', type: 'warning', icon: ReturnDownBackOutline }]
  }
  if (item.status === 'rejected') {
    return [{ key: 'approved', label: '重新通过', type: 'success', icon: CheckmarkCircleOutline }]
  }
  return [
    { key: 'approved', label: '通过', type: 'success', icon: CheckmarkCircleOutline },
    { key: 'rejected', label: '拒绝', type: 'error', icon: CloseCircleOutline },
  ]
}

function formatConfidence(value: number | null) {
  if (value === null || Number.isNaN(Number(value))) return '--'
  return `${Math.round(Number(value) * 100)}%`
}

function formatTime(value: string) {
  if (!value) return '--'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}
</script>

<template>
  <NDrawer v-model:show="show" :width="560">
    <NDrawerContent closable>
      <template #header>
        <AppDrawerHeader
          eyebrow="Learning Review"
          :title="title"
          :description="subtitle"
        />
      </template>

      <AppDrawerLayout>
        <AppPanelSection eyebrow="Item" title="条目信息">
          <NSpin :show="loading">
            <div class="learning-review__grid">
              <div v-for="[label, value] in detailRows" :key="label" class="learning-review__field">
                <span>{{ label }}</span>
                <strong>{{ value }}</strong>
              </div>
            </div>
            <p class="learning-review__content">
              {{ item?.content_full || item?.content || '--' }}
            </p>
          </NSpin>
        </AppPanelSection>

        <AppPanelSection eyebrow="Decision" title="处理动作">
          <NInput
            v-model:value="reason"
            type="textarea"
            placeholder="处理理由"
            :autosize="{ minRows: 2, maxRows: 4 }"
            maxlength="200"
            show-count
          />
          <NSpace :size="8">
            <NButton
              v-for="action in actions"
              :key="action.key"
              :type="action.type"
              secondary
              :loading="submitting"
              @click="submitAction(action)"
            >
              <template #icon>
                <NIcon :component="action.icon" />
              </template>
              {{ action.label }}
            </NButton>
          </NSpace>
        </AppPanelSection>

        <AppPanelSection v-if="detail" eyebrow="Source" title="来源详情">
          <pre class="learning-review__json">{{ JSON.stringify(detail, null, 2) }}</pre>
        </AppPanelSection>

        <template #footer>
          <NButton secondary @click="show = false">
            关闭
          </NButton>
          <NButton type="primary" @click="openOriginalPage">
            打开原页面
          </NButton>
        </template>
      </AppDrawerLayout>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.learning-review__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.learning-review__field {
  display: grid;
  gap: 4px;
}

.learning-review__field span {
  color: var(--om-text-3);
  font-size: 12px;
}

.learning-review__field strong {
  overflow-wrap: anywhere;
  color: var(--om-text-1);
  font-size: 13px;
  font-weight: 600;
}

.learning-review__content {
  margin: 16px 0 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
}

.learning-review__json {
  overflow: auto;
  max-height: 320px;
  margin: 0;
  color: var(--om-text-2);
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
}

@media (max-width: 760px) {
  .learning-review__grid {
    grid-template-columns: 1fr;
  }
}
</style>
