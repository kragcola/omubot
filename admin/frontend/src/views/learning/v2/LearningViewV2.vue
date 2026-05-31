<script setup lang="ts">
import { computed, onDeactivated, onMounted, onUnmounted, provide, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NTabs, NTabPane, NButton } from 'naive-ui'
import { useLearningConsole } from './useLearningConsole'
import DashboardView from './dashboard/DashboardView.vue'
import PipelineView from './pipeline/PipelineView.vue'
import SettingsView from './settings/SettingsView.vue'
import ItemEditorDrawer from './pipeline/ItemEditorDrawer.vue'

const route = useRoute()
const router = useRouter()
const console = useLearningConsole()
provide('learningConsole', console)

const editorShow = ref(false)
const editorItemId = ref('')
const editorNoun = ref('')

function openEditorFromQuery() {
  const id = route.query.id as string | undefined
  const noun = route.query.noun as string | undefined
  if (!id) return
  editorItemId.value = id
  editorNoun.value = noun || 'slang'
  editorShow.value = true
}

watch(() => route.query.id, (newId) => {
  if (newId) openEditorFromQuery()
  else editorShow.value = false
})

function onEditorSaved() {
  editorShow.value = false
  router.replace({ query: { ...route.query, id: undefined, noun: undefined } })
  if (console.activeView.value === 'pipeline') console.fetchItems()
}

watch(editorShow, (val) => {
  if (!val && route.query.id) {
    router.replace({ query: { ...route.query, id: undefined, noun: undefined } })
  }
})

onMounted(() => {
  console.fetchDashboard()
  if (console.activeView.value === 'pipeline') console.fetchItems()
  if (console.activeView.value === 'settings') console.fetchSettings()
  openEditorFromQuery()
  fetchAutopilotRemaining()
})

function onTabChange(tab: string) {
  console.activeView.value = tab as 'dashboard' | 'pipeline' | 'settings'
  if (tab === 'dashboard') console.fetchDashboard()
  else if (tab === 'pipeline') console.fetchItems()
  else if (tab === 'settings') console.fetchSettings()
}

async function triggerExtractAll() {
  await fetch('/api/admin/learning/extract-all', { method: 'POST' })
  if (console.activeView.value === 'dashboard') console.fetchDashboard()
}

const autopilotRunning = ref(false)
const autopilotProcessed = ref(0)
const autopilotTotal = ref(0)
const autopilotLabel = ref('')

// Hoisted so a mid-run unmount/deactivate (keepAlive) can clear the poll —
// without this the 3s status poll leaks if the view goes away while
// run-all is still awaiting. See docs/admin-runtime-interaction.md.
let autopilotPoll: ReturnType<typeof setInterval> | null = null
function stopAutopilotPoll() {
  if (autopilotPoll) {
    clearInterval(autopilotPoll)
    autopilotPoll = null
  }
}
onUnmounted(stopAutopilotPoll)
onDeactivated(stopAutopilotPoll)

const progressPercent = computed(() => {
  if (autopilotTotal.value === 0) return 0
  return Math.min(100, Math.round((autopilotProcessed.value / autopilotTotal.value) * 100))
})

async function fetchAutopilotRemaining() {
  try {
    const res = await (await fetch('/api/admin/learning/autopilot/status')).json()
    if (!res.ok) return
    let remaining = 0
    for (const info of Object.values(res.domains) as any[]) {
      remaining += (info.remaining ?? 0)
    }
    if (!autopilotRunning.value) {
      autopilotLabel.value = `待处理 ${remaining} 条`
      autopilotTotal.value = remaining
      autopilotProcessed.value = 0
    }
  } catch { /* ignore */ }
}

async function triggerAutopilotAll() {
  if (autopilotRunning.value) return
  autopilotRunning.value = true
  autopilotProcessed.value = 0
  autopilotTotal.value = 0
  autopilotLabel.value = '获取待处理数…'

  try {
    const statusRes = await fetch('/api/admin/learning/autopilot/status')
    const statusData = await statusRes.json()
    if (!statusData.ok) {
      autopilotLabel.value = statusData.error || '初始化失败'
      autopilotRunning.value = false
      return
    }
    let total = 0
    for (const info of Object.values(statusData.domains) as any[]) {
      total += (info.remaining ?? 0)
    }
    if (total === 0) {
      autopilotLabel.value = '无待处理项'
      autopilotRunning.value = false
      return
    }
    autopilotTotal.value = total
    autopilotLabel.value = `0 / ${total}`

    let allDone = false
    while (!allDone) {
      const runPromise = fetch('/api/admin/learning/autopilot/run-all', { method: 'POST' }).then(r => r.json())
      autopilotPoll = setInterval(async () => {
        try {
          const s = await (await fetch('/api/admin/learning/autopilot/status')).json()
          if (!s.ok) return
          let currentRemaining = 0
          for (const info of Object.values(s.domains) as any[]) {
            currentRemaining += (info.remaining ?? 0)
          }
          const done = Math.max(0, total - currentRemaining)
          autopilotProcessed.value = done
          autopilotLabel.value = `${done} / ${total}`
        } catch { /* ignore */ }
      }, 3000)

      const data = await runPromise
      stopAutopilotPoll()

      if (!data.ok) {
        autopilotLabel.value = data.error || '执行失败'
        break
      }
      let anyProcessed = false
      allDone = true
      for (const r of Object.values(data.results) as any[]) {
        if (r.ok && r.processed > 0) anyProcessed = true
        if (r.ok && !r.completed) allDone = false
        if (!r.ok) allDone = false
      }
      // Re-fetch actual remaining from status for accurate progress
      try {
        const freshStatus = await (await fetch('/api/admin/learning/autopilot/status')).json()
        if (freshStatus.ok) {
          let currentRemaining = 0
          for (const info of Object.values(freshStatus.domains) as any[]) {
            currentRemaining += (info.remaining ?? 0)
          }
          autopilotProcessed.value = Math.max(0, total - currentRemaining)
          autopilotLabel.value = `${autopilotProcessed.value} / ${total}`
          if (currentRemaining === 0) allDone = true
        }
      } catch { /* use last known value */ }
      if (!anyProcessed) break
      if (allDone) break
    }

    autopilotLabel.value = `完成 ${autopilotProcessed.value} / ${autopilotTotal.value}`
    if (console.activeView.value === 'dashboard') console.fetchDashboard()
    else if (console.activeView.value === 'pipeline') console.fetchItems()
  } catch {
    autopilotLabel.value = '请求失败'
  } finally {
    autopilotRunning.value = false
    fetchAutopilotRemaining()
  }
}
</script>

<template>
  <AppPage title="学习管线" description="管线工作流、指标监控与自动化设置">
    <template #action>
      <NButton size="small" :loading="autopilotRunning" @click="triggerAutopilotAll">
        AI 清池
      </NButton>
      <NButton type="primary" size="small" @click="triggerExtractAll">
        全量提取
      </NButton>
    </template>
    <div class="learning-v2">
      <div class="learning-v2__progress">
        <div class="learning-v2__progress-track">
          <div class="learning-v2__progress-fill" :style="{ width: progressPercent + '%' }" />
        </div>
        <span class="learning-v2__progress-text">{{ autopilotLabel }}</span>
      </div>
      <NTabs
        :value="console.activeView.value"
        type="line"
        animated
        @update:value="onTabChange"
      >
        <NTabPane name="dashboard" tab="仪表盘">
          <DashboardView />
        </NTabPane>
        <NTabPane name="pipeline" tab="工作流">
          <PipelineView />
        </NTabPane>
        <NTabPane name="settings" tab="设置">
          <SettingsView />
        </NTabPane>
      </NTabs>
    </div>

    <ItemEditorDrawer
      v-model:show="editorShow"
      :item-id="editorItemId"
      :noun="editorNoun"
      @saved="onEditorSaved"
    />
  </AppPage>
</template>

<style scoped>
.learning-v2 {
  min-height: 400px;
}

.learning-v2 :deep(.n-tab-pane) {
  padding-top: 20px;
}

.learning-v2__progress {
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 12px;
}

.learning-v2__progress-track {
  flex: 1;
  height: 8px;
  border-radius: 4px;
  background: var(--om-border);
  overflow: hidden;
}

.learning-v2__progress-fill {
  height: 100%;
  border-radius: 4px;
  background: rgb(var(--primary-color));
  transition: width 0.4s ease;
}

.learning-v2__progress-text {
  font-size: 12px;
  color: var(--om-text-2);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
</style>
