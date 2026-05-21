<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useMessage } from 'naive-ui'
import {
  ArchiveOutline,
  ChevronDownOutline,
  ChevronUpOutline,
  ShieldCheckmarkOutline,
} from '@vicons/ionicons5'

import AppPanelSection from '../../../components/common/AppPanelSection.vue'

const message = useMessage()

interface BackupListItem {
  backup_id: string
  created_at?: string
  trusted?: boolean
  complete?: boolean
  skipped_host_only?: string[]
  profile?: string
  path?: string
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

const backupLoading = ref(false)
const selectedProfile = ref('daily')
const backups = ref<BackupListItem[]>([])
const listLoading = ref(false)
const expandedId = ref<string | null>(null)

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

const displayBackups = computed(() => backups.value.slice(0, 20))

const lastQuickCheckLabel = computed(() => {
  if (!quickCheck.value.last_run_at) return '尚未执行'
  try {
    const dt = new Date(quickCheck.value.last_run_at)
    return dt.toLocaleString('zh-CN', { hour12: false })
  } catch {
    return quickCheck.value.last_run_at
  }
})

function toggleExpand(id: string) {
  expandedId.value = expandedId.value === id ? null : id
}

function profileTagType(profile: string | undefined): 'info' | 'warning' | 'success' {
  if (profile === 'daily') return 'info'
  if (profile === 'pre-change') return 'warning'
  return 'success'
}

function profileLabel(profile: string | undefined): string {
  if (profile === 'daily') return '每日'
  if (profile === 'pre-change') return '变更前'
  if (profile === 'migration') return '迁移'
  return profile ?? ''
}

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
    message.error(`请求失败: ${e.message || e}`)
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
  } catch (e) {
    console.error('[ConfigSystemBackup] loadBackups error:', e)
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
  } catch (e) {
    console.error('[ConfigSystemBackup] loadSettings error:', e)
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
    message.error(`请求失败: ${e.message || e}`)
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
    message.error(`请求失败: ${e.message || e}`)
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
  <div class="config-system-backup">
    <!-- Schedule Settings -->
    <AppPanelSection
      eyebrow="Schedule"
      title="备份调度配置"
      description="配置每日自动备份与 SQLite 完整性巡检策略。"
    >
      <template #aside>
        <NButton type="primary" size="small" :loading="saving" @click="saveSettings">
          保存配置
        </NButton>
      </template>

      <NSpin :show="settingsLoading">
        <div class="settings-grid">
          <div class="settings-group">
            <div class="settings-group__title">每日自动备份</div>
            <NSpace vertical size="small">
              <NSpace align="center">
                <NText style="width: 120px; font-size: 13px">启用</NText>
                <NSwitch v-model:value="settings.enabled" size="small" />
              </NSpace>
              <NSpace align="center">
                <NText style="width: 120px; font-size: 13px">执行时间</NText>
                <NInput
                  v-model:value="settings.daily_time"
                  size="small"
                  placeholder="HH:MM"
                  style="width: 100px"
                />
              </NSpace>
              <NSpace align="center">
                <NText style="width: 120px; font-size: 13px">保留天数</NText>
                <NInputNumber
                  v-model:value="settings.keep_days"
                  size="small"
                  :min="1"
                  :max="90"
                  style="width: 100px"
                />
              </NSpace>
              <NSpace align="center">
                <NText style="width: 120px; font-size: 13px">默认 Profile</NText>
                <NSelect
                  v-model:value="settings.default_profile"
                  :options="profileOptions"
                  size="small"
                  style="width: 180px"
                />
              </NSpace>
            </NSpace>
          </div>

          <div class="settings-group">
            <div class="settings-group__title">SQLite 完整性巡检</div>
            <NSpace vertical size="small">
              <NSpace align="center">
                <NText style="width: 120px; font-size: 13px">启用</NText>
                <NSwitch v-model:value="settings.quick_check_enabled" size="small" />
              </NSpace>
              <NSpace align="center">
                <NText style="width: 120px; font-size: 13px">间隔（分钟）</NText>
                <NInputNumber
                  v-model:value="settings.quick_check_interval_minutes"
                  size="small"
                  :min="15"
                  :max="1440"
                  style="width: 120px"
                />
              </NSpace>
              <NText depth="3" style="font-size: 12px; line-height: 1.6">
                巡检失败会触发紧急 pre-change 备份并发布运行时告警。
              </NText>
            </NSpace>
          </div>
        </div>
      </NSpin>
    </AppPanelSection>

    <!-- Quick-check status -->
    <AppPanelSection
      eyebrow="Quick-check"
      title="SQLite 巡检状态"
      description="周期性 PRAGMA quick_check / journal_mode 探测，发现损坏立即警报。"
    >
      <template #aside>
        <NSpace>
          <NText depth="3" style="font-size: 12px">
            上次执行：{{ lastQuickCheckLabel }}
          </NText>
          <NButton size="small" :loading="probing" @click="runQuickCheck">
            <template #icon><NIcon :component="ShieldCheckmarkOutline" /></template>
            立即巡检
          </NButton>
        </NSpace>
      </template>

      <NSpin :show="quickCheckLoading">
        <div
          v-if="!quickCheck.results.length"
          style="color: var(--text-color-3); font-size: 13px"
        >
          尚无巡检数据；调度器开启后会按配置周期自动巡检。
        </div>
        <NSpace v-else vertical size="small">
          <div
            v-for="r in quickCheck.results"
            :key="r.db_id"
            class="quick-check-row"
          >
            <NTag :type="r.ok ? 'success' : 'error'" size="small">
              {{ r.ok ? 'ok' : r.quick_check }}
            </NTag>
            <span class="quick-check-row__id">{{ r.db_id }}</span>
            <NText depth="3" style="font-size: 12px">
              journal_mode={{ r.journal_mode || '?' }}
            </NText>
            <NText
              v-if="r.error"
              depth="3"
              style="font-size: 12px; color: var(--error-color)"
            >
              {{ r.error }}
            </NText>
          </div>
        </NSpace>
      </NSpin>
    </AppPanelSection>

    <!-- Manual Backup -->
    <AppPanelSection
      eyebrow="Manual"
      title="手动创建备份"
      description="选择 profile 后立即创建一份备份快照。"
    >
      <NSpace align="center">
        <NSelect
          v-model:value="selectedProfile"
          :options="profileOptions"
          size="small"
          style="width: 200px"
          @update:value="loadBackups"
        />
        <NButton type="primary" :loading="backupLoading" @click="createBackup">
          <template #icon><NIcon :component="ArchiveOutline" /></template>
          立即创建
        </NButton>
      </NSpace>
    </AppPanelSection>

    <!-- Backup History -->
    <AppPanelSection
      eyebrow="History"
      title="备份历史"
      :description="`profile = ${selectedProfile} 的备份记录，按时间倒序排列。`"
    >
      <template #aside>
        <NButton tertiary size="small" :loading="listLoading" @click="loadBackups">
          刷新
        </NButton>
      </template>

      <NSpin :show="listLoading">
        <div
          v-if="displayBackups.length === 0"
          style="color: var(--text-color-3); font-size: 13px"
        >
          暂无备份记录
        </div>
        <div v-else class="backup-list">
          <div
            v-for="b in displayBackups"
            :key="b.backup_id"
            class="backup-item"
            @click="toggleExpand(b.backup_id)"
          >
            <div class="backup-item__row">
              <NTag :type="profileTagType(b.profile)" size="small" round>
                {{ profileLabel(b.profile) }}
              </NTag>
              <NTag :type="b.trusted ? 'success' : 'error'" size="small">
                {{ b.trusted ? '可信' : '不可信' }}
              </NTag>
              <NTag v-if="b.complete === false" type="warning" size="small">
                不完整
              </NTag>
              <span class="backup-item__id">{{ b.backup_id }}</span>
              <NText depth="3" class="backup-item__time">
                {{ b.created_at?.slice(0, 19).replace('T', ' ') }}
              </NText>
              <NIcon
                :component="expandedId === b.backup_id ? ChevronUpOutline : ChevronDownOutline"
                size="14"
                style="margin-left: auto; opacity: 0.5"
              />
            </div>
            <div v-if="expandedId === b.backup_id" class="backup-item__detail">
              <div v-if="b.skipped_host_only?.length" class="backup-item__skipped">
                <NText depth="3" style="font-size: 12px">
                  跳过 (host-only): {{ b.skipped_host_only.join(', ') }}
                </NText>
              </div>
              <div class="backup-item__meta">
                <NText depth="3" style="font-size: 12px">
                  路径: {{ b.path }}
                </NText>
              </div>
            </div>
          </div>
        </div>
      </NSpin>
    </AppPanelSection>
  </div>
</template>

<style scoped>
.config-system-backup {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.settings-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
}

@media (max-width: 640px) {
  .settings-grid {
    grid-template-columns: 1fr;
  }
}

.settings-group__title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 12px;
  color: var(--text-color-2);
}

.quick-check-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  flex-wrap: wrap;
}

.quick-check-row__id {
  font-weight: 500;
}

.backup-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.backup-item {
  padding: 8px 12px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.15s;
}

.backup-item:hover {
  background: var(--hover-color);
}

.backup-item__row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  flex-wrap: wrap;
}

.backup-item__id {
  font-family: var(--font-family-mono, monospace);
  font-size: 12px;
}

.backup-item__time {
  font-size: 12px;
}

.backup-item__detail {
  margin-top: 8px;
  padding: 8px 12px;
  background: var(--card-color);
  border-radius: 4px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.backup-item__skipped {
  color: var(--warning-color);
}
</style>
