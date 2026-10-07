<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NAlert, NButton, NCard, NEmpty, NInput, NTag } from 'naive-ui'
import { apiErrorMessage, apiRequest, isApiError } from '@/api/client'
import type { StyleManagementView, StyleItemView, StyleProfileRequest } from '@/api/generated'
import { currentAdminEpoch, expireAdminSession, isCurrentAdminEpoch, sessionState } from '@/app/session'

const group = ref('')
const loadedGroup = ref('')
const snapshot = ref<StyleManagementView | null>(null)
const busy = ref(false)
const error = ref('')
let sequence = 0
let disposed = false
const canWrite = computed(() => sessionState.adminAuthenticated && !busy.value
  && snapshot.value !== null && group.value.trim() === loadedGroup.value)
const labels: Record<string, string> = { draft: '草稿', enabled: '已启用', disabled: '已停用', stale: '来源或样本已失效', active: '当前有效' }

function clear() {
  sequence += 1
  snapshot.value = null
  loadedGroup.value = ''
  error.value = ''
  busy.value = false
}
watch(() => sessionState.generation, clear)
watch(() => sessionState.adminAuthenticated, (authenticated) => { if (!authenticated) clear() })
onBeforeUnmount(() => { disposed = true; clear() })

async function request(path: string, body?: unknown, more = false) {
  if (busy.value || !sessionState.adminAuthenticated) return
  const target = group.value.trim()
  if (!target) return
  const requestSequence = ++sequence
  const epoch = currentAdminEpoch()
  busy.value = true
  error.value = ''
  try {
    const result = await apiRequest<StyleManagementView>(path, body === undefined
      ? {} : { method: 'POST', body, adminMutation: true })
    if (disposed || requestSequence !== sequence || !isCurrentAdminEpoch(epoch)) return
    snapshot.value = more && snapshot.value
      ? { ...result, items: [...snapshot.value.items, ...result.items] } : result
    loadedGroup.value = target
  } catch (cause) {
    if (disposed || requestSequence !== sequence || !isCurrentAdminEpoch(epoch)) return
    if (isApiError(cause) && cause.status === 401) { expireAdminSession(); clear() }
    else error.value = apiErrorMessage(cause) + ' 写操作失败或结果不确定时，请重新读取核对后再操作。'
  } finally {
    if (requestSequence === sequence && isCurrentAdminEpoch(epoch)) busy.value = false
  }
}
function load(more = false) {
  const cursor = more ? snapshot.value?.next_cursor : null
  return request(`/api/admin/memory/style?group_id=${encodeURIComponent(group.value.trim())}${cursor ? `&after=${encodeURIComponent(cursor)}` : ''}`, undefined, more)
}
function profile(action: StyleProfileRequest['action'], profileId: string | null = null) {
  if (!canWrite.value || !snapshot.value) return
  return request('/api/admin/memory/style/profile', {
    group_id: loadedGroup.value, expected_revision: snapshot.value.revision, action, profile_id: profileId,
  } satisfies StyleProfileRequest)
}
function disable(item: StyleItemView) {
  if (!canWrite.value) return
  return request('/api/admin/memory/style/disable', {
    group_id: loadedGroup.value, object_id: item.object_id, expected_revision: item.revision,
  })
}
function feedback(item: StyleItemView, rating: 'positive' | 'negative' | 'neutral') {
  if (!canWrite.value) return
  return request('/api/admin/memory/style/feedback', {
    group_id: loadedGroup.value, object_id: item.object_id, expected_revision: item.revision,
    feedback_id: crypto.randomUUID(), rating,
  })
}
</script>

<template>
  <n-card class="page-card" :bordered="false" title="表达参考与动态档案">
    <p class="form-hint">仅使用本群已审核、已应用且来源有效的表达。参考最多 3 条 / 800 字；档案最多 900 字，不修改固定人格。人工反馈只留评级和对象身份，不保存聊天正文，也不自动调整样本。</p>
    <form class="style-controls" @submit.prevent="void load()">
      <label for="style-group">群 ID</label>
      <n-input id="style-group" v-model:value="group" :disabled="busy" placeholder="输入已获授权的群 ID" />
      <n-button attr-type="submit" :disabled="!sessionState.adminAuthenticated || busy || !group.trim()" :loading="busy">读取表达管理</n-button>
    </form>
    <n-alert v-if="error" type="error" :show-icon="false">{{ error }}</n-alert>
    <template v-if="snapshot">
      <div class="style-controls">
        <n-button :disabled="!canWrite" @click="void profile('generate')">生成档案草稿</n-button>
        <n-button :disabled="!canWrite || !snapshot.profiles.some(item => item.status === 'enabled' || item.status === 'stale')" @click="void profile('rollback')">回滚上一版本</n-button>
      </div>
      <p class="form-hint">生成不自动启用。停用档案后，相关的已应用表达参考仍可使用；停用单条样本会使引用它的旧档案失效。档案显示最近 32 版。</p>
      <n-empty v-if="!snapshot.profiles.length" description="尚无动态档案" />
      <article v-for="item in snapshot.profiles" :key="item.profile_id" class="style-entry">
        <div class="style-controls">
          <strong>档案 v{{ item.version }}</strong><n-tag size="small">{{ labels[item.status] ?? item.status }}</n-tag>
          <n-button :disabled="!canWrite || item.status === 'stale' || item.status === 'enabled'" @click="void profile('enable', item.profile_id)">启用此版本</n-button>
          <n-button :disabled="!canWrite || item.status === 'disabled' || item.status === 'draft'" @click="void profile('disable', item.profile_id)">停用档案</n-button>
        </div>
        <p class="style-content">{{ item.content || '来源或样本已变化；此档案不进入聊天，也不能重新启用。' }}</p>
      </article>
      <h3>已应用表达样本</h3>
      <n-empty v-if="!snapshot.items.length" description="暂无可见的已应用表达" />
      <article v-for="item in snapshot.items" :key="item.object_id" class="style-entry">
        <p><strong>当 {{ item.value.situation }} 时：</strong>{{ item.value.style }}</p>
        <p class="form-hint">{{ labels[item.status] ?? item.status }} · {{ item.value.output_policy }} · {{ item.value.risk_tags.join('、') || '无风险标签' }}</p>
        <div class="style-controls">
          <n-button :disabled="!canWrite" @click="void feedback(item, 'positive')">有帮助</n-button>
          <n-button :disabled="!canWrite" @click="void feedback(item, 'negative')">不合适</n-button>
          <n-button :disabled="!canWrite" @click="void feedback(item, 'neutral')">中性记录</n-button>
          <n-button :disabled="!canWrite || item.status !== 'active'" @click="void disable(item)">停用样本</n-button>
        </div>
      </article>
      <n-button v-if="snapshot.next_cursor" :disabled="!canWrite" @click="void load(true)">加载更多表达</n-button>
      <details><summary>最近 32 条人工反馈</summary>
        <p v-for="item in snapshot.feedback" :key="item.feedback_id">{{ item.rating }} · {{ item.object_id }} · 修订 {{ item.object_revision }} · {{ item.actor }}</p>
      </details>
    </template>
  </n-card>
</template>

<style scoped>
.style-controls { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
.style-entry { padding: 12px 0; }
.style-content { white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
