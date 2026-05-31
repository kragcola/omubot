<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import { useMessage } from 'naive-ui'
import AppDrawerHeader from '../../../../components/common/AppDrawerHeader.vue'
import AppDrawerLayout from '../../../../components/common/AppDrawerLayout.vue'
import AppPanelSection from '../../../../components/common/AppPanelSection.vue'

const props = defineProps<{
  itemId: string
  noun: string
}>()

const show = defineModel<boolean>('show', { required: true })
const emit = defineEmits<{ saved: [] }>()

const message = useMessage()
const loading = ref(false)
const saving = ref(false)
const detail = ref<Record<string, any> | null>(null)

const entityId = computed(() => {
  const id = props.itemId
  if (props.noun === 'slang') return id.replace(/^slang-hit-/, '').replace(/^slang-/, '')
  if (props.noun === 'style') return id.replace(/^style-/, '')
  if (props.noun === 'episode') return id.replace(/^episode-/, '')
  return id
})

const title = computed(() => {
  if (!detail.value) return '加载中…'
  return detail.value.term || detail.value.pattern || detail.value.title || props.itemId
})

const slangStatusOptions = [
  { label: '候选', value: 'candidate' },
  { label: '已通过', value: 'approved' },
  { label: '静音', value: 'muted' },
  { label: '过期', value: 'expired' },
]

const styleStatusOptions = [
  { label: '待审', value: 'pending' },
  { label: '已通过', value: 'approved' },
  { label: '已拒绝', value: 'rejected' },
  { label: '静音', value: 'muted' },
]

const aliasesText = computed({
  get: () => (detail.value?.aliases ?? []).join(', '),
  set: (v: string) => {
    if (detail.value) {
      detail.value.aliases = v.split(/[,，\n]/).map((s: string) => s.trim()).filter(Boolean)
    }
  },
})

watch(
  () => [show.value, props.itemId],
  ([visible]) => {
    if (visible && props.itemId) void fetchDetail()
  },
  { immediate: true },
)

async function fetchDetail() {
  loading.value = true
  detail.value = null
  try {
    if (props.noun === 'slang') {
      const res = await fetch(`/api/admin/slang/terms/${entityId.value}`)
      const data = await res.json()
      detail.value = data.term || null
    } else if (props.noun === 'style') {
      const res = await fetch(`/api/admin/style/expressions/${entityId.value}`)
      const data = await res.json()
      detail.value = data.expression || null
    } else if (props.noun === 'episode') {
      const res = await fetch(`/api/admin/episodes/${entityId.value}`)
      const data = await res.json()
      detail.value = data.episode || null
    }
  } catch {
    detail.value = null
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!detail.value) return
  saving.value = true
  try {
    if (props.noun === 'slang') {
      const { term, meaning, aliases, scope, group_id, confidence, status, repeat_policy, notes } = detail.value
      await fetch(`/api/admin/slang/terms/${entityId.value}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ term, meaning, aliases, scope, group_id, confidence, status, repeat_policy, notes }),
      })
    } else if (props.noun === 'style') {
      await fetch(`/api/admin/style/expressions/${entityId.value}/status`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: detail.value.status, actor: 'admin' }),
      })
    }
    message.success('保存成功')
    emit('saved')
    show.value = false
  } catch {
    message.error('保存失败')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <NDrawer v-model:show="show" :width="560">
    <NDrawerContent closable>
      <template #header>
        <AppDrawerHeader
          eyebrow="Item Editor"
          :title="title"
          :description="noun"
        />
      </template>

      <NSkeleton v-if="loading" :repeat="6" text />

      <AppDrawerLayout v-else-if="detail">
        <!-- Slang editor -->
        <template v-if="noun === 'slang'">
          <AppPanelSection eyebrow="Editor" title="术语与释义">
            <div class="editor-grid">
              <label>
                <span>术语</span>
                <NInput v-model:value="detail.term" />
              </label>
              <label>
                <span>状态</span>
                <NSelect
                  v-model:value="detail.status"
                  :options="slangStatusOptions"
                />
              </label>
              <label>
                <span>置信度</span>
                <NInputNumber v-model:value="detail.confidence" :min="0" :max="1" :step="0.05" />
              </label>
              <label>
                <span>作用域</span>
                <NSelect
                  v-model:value="detail.scope"
                  :options="[{ label: '当前群', value: 'group' }, { label: '全局', value: 'global' }]"
                />
              </label>
              <label class="editor-grid__full">
                <span>释义</span>
                <NInput v-model:value="detail.meaning" type="textarea" :autosize="{ minRows: 3, maxRows: 6 }" />
              </label>
              <label class="editor-grid__full">
                <span>别名（逗号分隔）</span>
                <NInput v-model:value="aliasesText" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }" />
              </label>
              <label class="editor-grid__full">
                <span>备注</span>
                <NInput v-model:value="detail.notes" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }" />
              </label>
            </div>
          </AppPanelSection>
        </template>

        <!-- Style editor -->
        <template v-else-if="noun === 'style'">
          <AppPanelSection eyebrow="Editor" title="表达模式">
            <div class="editor-grid">
              <label class="editor-grid__full">
                <span>模式</span>
                <NInput v-model:value="detail.pattern" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }" />
              </label>
              <label class="editor-grid__full">
                <span>示例</span>
                <NInput v-model:value="detail.example" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }" />
              </label>
              <label>
                <span>状态</span>
                <NSelect
                  v-model:value="detail.status"
                  :options="styleStatusOptions"
                />
              </label>
            </div>
          </AppPanelSection>
        </template>

        <!-- Generic fallback -->
        <template v-else>
          <AppPanelSection eyebrow="Detail" title="条目详情">
            <pre class="editor-json">{{ JSON.stringify(detail, null, 2) }}</pre>
          </AppPanelSection>
        </template>

        <template #footer>
          <NButton secondary @click="show = false">取消</NButton>
          <NButton type="primary" :loading="saving" @click="save">保存</NButton>
        </template>
      </AppDrawerLayout>

      <div v-else class="editor-empty">
        <p>无法加载条目数据</p>
      </div>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.editor-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.editor-grid label {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.editor-grid label > span {
  color: var(--om-text-3);
  font-size: 12px;
  font-weight: 500;
}

.editor-grid__full {
  grid-column: 1 / -1;
}

.editor-json {
  overflow: auto;
  max-height: 400px;
  margin: 0;
  padding: 12px;
  border-radius: 8px;
  background: var(--om-fill);
  color: var(--om-text-2);
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
}

.editor-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 200px;
  color: var(--om-text-3);
  font-size: 13px;
}
</style>
