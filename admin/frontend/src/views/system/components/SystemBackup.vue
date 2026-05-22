<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useMessage } from 'naive-ui'
import {
  ArchiveOutline,
  SettingsOutline,
  ShieldCheckmarkOutline,
} from '@vicons/ionicons5'

import AppPanelSection from '../../../components/common/AppPanelSection.vue'

const message = useMessage()

const backupLoading = ref(false)
const selectedProfile = ref('daily')
const backups = ref<BackupListItem[]>([])
const listLoading = ref(false)

interface BackupListItem {
  backup_id: string
  created_at?: string
  trusted?: boolean
  complete?: boolean
  skipped_host_only?: string[]
  profile?: string
}

interface BackupSettings {
  enabled: boolean
  daily_time: string
  keep_days: number
  default_profile: string
  quick_check_enabled: boolean
  quick_check_interval_minutes: number
}

interface QuickCheckProbe {
  db_id: string
  path: string
  ok: boolean
  quick_check: string
  journal_mode: string
  error: string | null
}

interface QuickCheckSnapshot {
  last_run_at: string | null
  results: QuickCheckProbe[]
  ok_count: number
  fail_count: number
}

const settings = ref<BackupSettings>({
  enabled: true,
  daily_time: '04:30',
  keep_days: 7,
  default_profile: 'daily',
  quick_check_enabled: true,
  quick_check_interval_minutes: 60,
})
const settingsLoading = ref(false)
const saving = ref(false)

const quickCheck = ref<QuickCheckSnapshot>({
  last_run_at: null,
  results: [],
  ok_count: 0,
  fail_count: 0,
})
const quickCheckLoading = ref(false)
const probing = ref(false)

const profileOptions = [
  { label: '每日备份 (daily)', value: 'daily' },
  { label: '变更前备份 (pre-change)', value: 'pre-change' },
  { label: '完整迁移包 (migration)', value: 'migration' },
]

const lastQuickCheckLabel = computed(() => {
  if (!quickCheck.value.last_run_at) return '尚未执行'
  try {
    const dt = new Date(quickCheck.value.last_run_at)
    return dt.toLocaleString('zh-CN', { hour12: false })
  } catch {
    return quickCheck.value.last_run_at
  }
})

async function createBackup() {
  backupLoading.value = true
  try {
    const resp = await fetch('/api/admin/backup/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile: selectedProfile.value }),
    })
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}))
      message.error(`备份失败: ${err.detail || resp.statusText}`)
      return
    }
    const data = await resp.json()
    const manifest = data?.manifest
    if (manifest?.summary?.trusted) {
      message.success(`备份成功: ${manifest.backup_id}`)
    } else {
      message.warning(`备份完成但未通过信任校验: ${manifest?.backup_id || ''}`)
    }
    await loadBackups()
  } catch (e: any) {
    message.error(`请求失败: ${e.message}`)
  } finally {
    backupLoading.value = false
  }
}

async function loadBackups() {
  listLoading.value = true
  try {
    const resp = await fetch(`/api/admin/backup/list?profile=${selectedProfile.value}`)
    const data = await resp.json()
    backups.value = (data.items || []) as BackupListItem[]
  } finally {
    listLoading.value = false
  }
}

async function loadSettings() {
  settingsLoading.value = true
  try {
    const resp = await fetch('/api/admin/backup/settings')
    if (!resp.ok) return
    const data = await resp.json()
    settings.value = { ...settings.value, ...data }
  } finally {
    settingsLoading.value = false
  }
}

async function saveSettings() {
  saving.value = true
  try {
    const resp = await fetch('/api/admin/backup/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings.value),
    })
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}))
      message.error(`保存失败: ${err.detail || resp.statusText}`)
      return
    }
    const data = await resp.json()
    settings.value = { ...settings.value, ...data }
    message.success('备份配置已保存')
  } catch (e: any) {
    message.error(`请求失败: ${e.message}`)
  } finally {
    saving.value = false
  }
}

async function loadQuickCheck() {
  quickCheckLoading.value = true
  try {
    const resp = await fetch('/api/admin/backup/quick-check')
    if (!resp.ok) return
    quickCheck.value = (await resp.json()) as QuickCheckSnapshot
  } finally {
    quickCheckLoading.value = false
  }
}

async function runQuickCheck() {
  probing.value = true
  try {
    const resp = await fetch('/api/admin/backup/quick-check', { method: 'POST' })
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}))
      message.error(`巡检失败: ${err.detail || resp.statusText}`)
      return
    }
    quickCheck.value = (await resp.json()) as QuickCheckSnapshot
    if (quickCheck.value.fail_count === 0) {
      message.success(`SQLite quick_check 全部通过 (${quickCheck.value.ok_count} 个数据库)`)
    } else {
      message.warning(
        `SQLite quick_check 失败 ${quickCheck.value.fail_count}/${
          quickCheck.value.ok_count + quickCheck.value.fail_count
        }`,
      )
    }
  } catch (e: any) {
    message.error(`请求失败: ${e.message}`)
  } finally {
    probing.value = false
  }
}

onMounted(() => {
  loadBackups()
  loadSettings()
  loadQuickCheck()
})
</script>

<template>
  <AppPanelSection
    class="system-backup"
    eyebrow="Backup"
    title="备份管理"
    description="创建、查看备份历史，配置自动备份调度，巡检 SQLite 完整性。"
  >
    <template #aside>
      <NSpace>
        <NSelect
          v-model:value="selectedProfile"
          :options="profileOptions"
          size="small"
          class="sb-profile-select"
          @update:value="loadBackups"
        />
        <NButton type="primary" :loading="backupLoading" @click="createBackup">
          <template #icon><NIcon :component="ArchiveOutline" /></template>
          创建备份
        </NButton>
      </NSpace>
    </template>

    <!-- Backup history -->
    <section class="sb-block sb-block--first">
      <header class="sb-block__head">
        <h4 class="sb-block__title">最近备份</h4>
      </header>
      <NSpin :show="listLoading">
        <p v-if="backups.length === 0" class="sb-empty">
          暂无备份记录
        </p>
        <ul v-else class="sb-list">
          <li
            v-for="b in backups.slice(0, 5)"
            :key="b.backup_id"
            class="sb-row"
          >
            <NTag :type="b.trusted ? 'success' : 'error'" size="small">
              {{ b.trusted ? '可信' : '不可信' }}
            </NTag>
            <NTag v-if="b.complete === false" type="warning" size="small">
              不完整
            </NTag>
            <span class="sb-row__id">{{ b.backup_id }}</span>
            <NText depth="3" class="sb-row__meta">
              {{ b.created_at?.slice(0, 19) }}
            </NText>
            <NText v-if="b.skipped_host_only?.length" depth="3" class="sb-row__meta">
              (跳过: {{ b.skipped_host_only.join(', ') }})
            </NText>
          </li>
        </ul>
      </NSpin>
    </section>

    <!-- Quick-check panel -->
    <section class="sb-block">
      <header class="sb-block__head">
        <NIcon :component="ShieldCheckmarkOutline" :size="16" />
        <h4 class="sb-block__title">SQLite 完整性巡检</h4>
        <NTag
          v-if="quickCheck.results.length"
          :type="quickCheck.fail_count === 0 ? 'success' : 'error'"
          size="small"
        >
          {{ quickCheck.ok_count }} ok / {{ quickCheck.fail_count }} 异常
        </NTag>
        <span class="sb-block__meta">上次执行：{{ lastQuickCheckLabel }}</span>
        <NButton size="tiny" :loading="probing" @click="runQuickCheck">
          立即巡检
        </NButton>
      </header>
      <NSpin :show="quickCheckLoading">
        <p v-if="!quickCheck.results.length" class="sb-empty">
          尚无巡检数据；调度器开启后会按配置周期自动巡检。
        </p>
        <ul v-else class="sb-list">
          <li
            v-for="r in quickCheck.results"
            :key="r.db_id"
            class="sb-row"
          >
            <NTag :type="r.ok ? 'success' : 'error'" size="small">
              {{ r.ok ? 'ok' : r.quick_check }}
            </NTag>
            <span class="sb-row__id">{{ r.db_id }}</span>
            <NText depth="3" class="sb-row__meta">
              journal_mode={{ r.journal_mode || '?' }}
            </NText>
            <NText v-if="r.error" depth="3" class="sb-row__error">
              {{ r.error }}
            </NText>
          </li>
        </ul>
      </NSpin>
    </section>

    <!-- Settings -->
    <section class="sb-block">
      <header class="sb-block__head">
        <NIcon :component="SettingsOutline" :size="16" />
        <h4 class="sb-block__title">备份调度配置</h4>
      </header>
      <NSpin :show="settingsLoading">
        <div class="sb-form">
          <label class="sb-field">
            <span>启用自动备份</span>
            <NSwitch v-model:value="settings.enabled" size="small" />
          </label>
          <label class="sb-field">
            <span>执行时间</span>
            <NInput
              v-model:value="settings.daily_time"
              size="small"
              placeholder="HH:MM"
              class="sb-field__short"
            />
          </label>
          <label class="sb-field">
            <span>保留天数</span>
            <NInputNumber
              v-model:value="settings.keep_days"
              size="small"
              :min="1"
              :max="90"
              class="sb-field__short"
            />
          </label>
          <label class="sb-field">
            <span>默认 Profile</span>
            <NSelect
              v-model:value="settings.default_profile"
              :options="profileOptions"
              size="small"
              class="sb-field__select"
            />
          </label>
          <label class="sb-field">
            <span>完整性巡检</span>
            <NSwitch v-model:value="settings.quick_check_enabled" size="small" />
          </label>
          <label class="sb-field">
            <span>巡检间隔（分钟）</span>
            <NInputNumber
              v-model:value="settings.quick_check_interval_minutes"
              size="small"
              :min="15"
              :max="1440"
              class="sb-field__medium"
            />
          </label>
        </div>
        <div class="sb-actions">
          <NButton
            type="primary"
            size="small"
            :loading="saving"
            @click="saveSettings"
          >
            保存配置
          </NButton>
        </div>
      </NSpin>
    </section>
  </AppPanelSection>
</template>

<style scoped>
.system-backup {
  margin-top: 16px;
}

.sb-profile-select {
  width: 180px;
}

.sb-block {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--om-border);
}

.sb-block--first {
  margin-top: 16px;
  padding-top: 0;
  border-top: 0;
}

.sb-block__head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}

.sb-block__title {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--om-text-1);
}

.sb-block__meta {
  margin-left: auto;
  color: var(--om-text-3);
  font-size: 12px;
}

.sb-empty {
  margin: 0;
  color: var(--om-text-3);
  font-size: 13px;
}

.sb-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.sb-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 13px;
}

.sb-row__id {
  font-weight: 500;
  color: var(--om-text-1);
}

.sb-row__meta {
  font-size: 12px;
}

.sb-row__error {
  font-size: 12px;
  color: var(--om-error);
}

.sb-form {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.sb-field {
  display: flex;
  align-items: center;
  gap: 12px;
}

.sb-field > span {
  width: 120px;
  flex-shrink: 0;
  font-size: 13px;
  color: var(--om-text-2);
}

.sb-field__short {
  width: 100px;
}

.sb-field__medium {
  width: 120px;
}

.sb-field__select {
  width: 180px;
}

.sb-actions {
  margin-top: 12px;
}
</style>
