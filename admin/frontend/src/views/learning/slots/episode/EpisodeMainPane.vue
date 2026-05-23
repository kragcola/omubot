<script setup lang="ts">
import {
  BulbOutline,
  ChevronForwardOutline,
} from '@vicons/ionicons5'
import { computed, ref, watch } from 'vue'
import AppPanelSection from '../../../../components/common/AppPanelSection.vue'
import EmptyState from '../../../../components/common/EmptyState.vue'
import {
  EPISODE_STATE_LABEL,
  type EpisodeItem,
  decayHint,
  useEpisodeConsoleInject,
} from './state'

const console_ = useEpisodeConsoleInject()
const { episodes, loading, openActionDialog, openDetail } = console_

const PAGE_SIZE = 20
const page = ref(1)

const pageCount = computed(() => Math.max(1, Math.ceil(episodes.value.length / PAGE_SIZE)))
const pagedEpisodes = computed(() => {
  const start = (page.value - 1) * PAGE_SIZE
  return episodes.value.slice(start, start + PAGE_SIZE)
})

watch(() => episodes.value.length, () => {
  if (page.value > pageCount.value) page.value = pageCount.value
  if (page.value < 1) page.value = 1
})

type EpisodeTone = 'success' | 'pending' | 'rejected' | 'neutral'

function episodeTone(state: string): EpisodeTone {
  if (state === 'enabled_for_prompt' || state === 'approved') return 'success'
  if (state === 'candidate' || state === 'dry_run') return 'pending'
  if (state === 'disabled') return 'rejected'
  return 'neutral'
}

function formatTime(value: string): string {
  if (!value) return '——'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date).replace(/\//g, '-')
}

function shortGroup(value: string): string {
  if (!value) return '——'
  if (value.length <= 8) return value
  return `…${value.slice(-6)}`
}
</script>

<template>
  <AppPanelSection
    class="episode-list-panel"
    eyebrow="Episodes"
    title="经验反思"
  >
    <template v-if="pageCount > 1" #aside>
      <NPagination
        v-model:page="page"
        :page-count="pageCount"
        :page-slot="5"
        size="small"
      />
    </template>

    <NSkeleton v-if="loading && !episodes.length" :repeat="6" text />

    <EmptyState
      v-else-if="!episodes.length"
      compact
      title="暂无经验反思"
      description="Bot 完成对话后由 Consolidator 写入；置信度达 0.6 后自动晋升 candidate。"
      :icon="BulbOutline"
    />

    <div v-else class="ep-list">
      <div
        v-for="row in pagedEpisodes"
        :key="row.episode_id"
        class="ep-row"
        :class="`ep-row--${episodeTone(row.episode_state)}`"
        tabindex="0"
        role="button"
        @click="openDetail(row.episode_id)"
        @keydown.enter.prevent="openDetail(row.episode_id)"
        @keydown.space.prevent="openDetail(row.episode_id)"
      >
        <span class="ep-row__situation" :title="row.situation || '(未填写)'">
          {{ row.situation || '(未填写)' }}
        </span>
        <span class="ep-row__group" :title="row.group_id">
          {{ shortGroup(row.group_id) }}
        </span>
        <span class="ep-row__time">{{ formatTime(row.updated_at) }}</span>
        <span class="ep-row__conf">{{ Math.round(row.confidence * 100) }}%</span>
        <button
          type="button"
          class="ep-row__config"
          @click.stop="openDetail(row.episode_id)"
        >
          <span class="ep-row__config-icon">
            <NIcon :component="ChevronForwardOutline" />
          </span>
          <span class="ep-row__config-text">配置</span>
        </button>
        <span class="ep-row__decay">{{ decayHint(row.decay_at) }}</span>
        <span class="ep-row__status">{{ EPISODE_STATE_LABEL[row.episode_state] || row.episode_state }}</span>
        <span class="ep-row__actions" @click.stop>
          <button
            v-if="row.episode_state === 'candidate'"
            type="button"
            class="ep-row__act ep-row__act--success"
            @click="openActionDialog(row, 'approve')"
          >批准</button>
          <button
            v-if="row.episode_state !== 'disabled'"
            type="button"
            class="ep-row__act ep-row__act--danger"
            @click="openActionDialog(row, 'disable')"
          >停用</button>
          <button
            v-if="row.episode_state === 'disabled'"
            type="button"
            class="ep-row__act"
            @click="openActionDialog(row, 'restore')"
          >恢复</button>
        </span>
      </div>
    </div>

    <div v-if="pageCount > 1" class="ep-pagination-bottom">
      <NPagination
        v-model:page="page"
        :page-count="pageCount"
        :page-slot="7"
      />
    </div>
  </AppPanelSection>
</template>

<style scoped>
.episode-list-panel {
  font-feature-settings: 'tnum' 1;
}

.ep-list {
  display: grid;
  gap: 8px;
}

.ep-row {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto 50px auto auto auto auto;
  align-items: center;
  gap: 10px;
  padding: 10px 14px 10px 18px;
  border: 1px solid var(--om-border);
  border-radius: 10px;
  background: var(--om-surface-solid);
  color: var(--om-text-1);
  cursor: pointer;
  transition:
    border-color 0.16s ease,
    background-color 0.16s ease,
    transform 0.16s ease,
    box-shadow 0.16s ease;
}

.ep-row::before {
  content: '';
  position: absolute;
  top: 10px;
  bottom: 10px;
  left: 0;
  width: 3px;
  border-radius: 0 2px 2px 0;
  background: var(--om-text-3);
  opacity: 0.45;
  transition: background-color 0.16s ease, opacity 0.16s ease;
}

.ep-row--success::before { background: var(--om-success); opacity: 1; }
.ep-row--pending::before { background: var(--om-warning); opacity: 1; }
.ep-row--rejected::before { background: var(--om-danger); opacity: 0.85; }

.ep-row:hover,
.ep-row:focus-visible {
  border-color: var(--om-border-strong);
  background: color-mix(in srgb, var(--om-surface-2) 35%, var(--om-surface-solid));
  outline: none;
  transform: translateY(-1px);
  box-shadow: var(--om-shadow-sm);
}

.ep-row__situation {
  color: var(--om-text-1);
  font-size: 13px;
  font-weight: 500;
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ep-row__group {
  color: var(--om-text-3);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  max-width: 9ch;
  overflow: hidden;
  text-overflow: ellipsis;
}

.ep-row__time {
  color: var(--om-text-3);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.ep-row__conf {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 16px;
  padding: 0 5px;
  border-radius: 2px;
  background: color-mix(in srgb, var(--om-info) 12%, transparent);
  color: var(--om-text-2);
  font-size: 10.5px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  min-width: 36px;
  text-align: center;
}

.ep-row__decay {
  color: var(--om-text-3);
  font-size: 11px;
  white-space: nowrap;
}

.ep-row__config {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 12px 3px 6px;
  border: 1.5px solid color-mix(in srgb, var(--om-info) 32%, transparent);
  border-radius: 18px;
  background: color-mix(in srgb, var(--om-info) 6%, transparent);
  color: var(--om-info);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
  flex-shrink: 0;
}

.ep-row__config-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--om-info);
  color: #fff;
  font-size: 14px;
}

.ep-row__config:hover {
  border-color: var(--om-info);
  background: var(--om-info);
  color: #fff;
}

.ep-row__config:hover .ep-row__config-icon {
  background: rgba(255, 255, 255, 0.25);
  color: #fff;
}

.ep-row__status {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.02em;
  color: var(--om-text-3);
  white-space: nowrap;
}

.ep-row--success .ep-row__status { color: var(--om-success); }
.ep-row--pending .ep-row__status { color: var(--om-warning); }
.ep-row--rejected .ep-row__status { color: var(--om-danger); }

.ep-row__actions {
  display: inline-flex;
  gap: 4px;
  opacity: 0;
  transition: opacity 0.16s ease;
}

.ep-row:hover .ep-row__actions,
.ep-row:focus-visible .ep-row__actions,
.ep-row:focus-within .ep-row__actions {
  opacity: 1;
}

.ep-row__act {
  height: 22px;
  padding: 0 9px;
  border: 1px solid color-mix(in srgb, var(--om-border-strong) 70%, transparent);
  border-radius: 4px;
  background: var(--om-surface-solid);
  color: var(--om-text-2);
  font-size: 11px;
  font-weight: 500;
  line-height: 1;
  cursor: pointer;
  transition: background-color 0.12s ease, color 0.12s ease, border-color 0.12s ease;
}

.ep-row__act:hover {
  background: var(--om-surface-2);
  color: var(--om-text-1);
  border-color: var(--om-border-strong);
}

.ep-row__act--success {
  color: var(--om-success);
  border-color: color-mix(in srgb, var(--om-success) 40%, var(--om-border));
}
.ep-row__act--success:hover {
  background: color-mix(in srgb, var(--om-success) 10%, var(--om-surface-solid));
  color: var(--om-success);
}

.ep-row__act--danger {
  color: var(--om-danger);
  border-color: color-mix(in srgb, var(--om-danger) 40%, var(--om-border));
}
.ep-row__act--danger:hover {
  background: color-mix(in srgb, var(--om-danger) 10%, var(--om-surface-solid));
  color: var(--om-danger);
}

.ep-pagination-bottom {
  display: flex;
  justify-content: center;
  margin-top: 14px;
}

@media (max-width: 1100px) {
  .ep-row {
    grid-template-columns: minmax(0, 1fr) auto 50px auto auto auto auto;
  }
  .ep-row__group {
    display: none;
  }
}

@media (max-width: 720px) {
  .ep-row {
    grid-template-columns: minmax(0, 1fr) auto auto auto;
    gap: 8px;
  }
  .ep-row__time,
  .ep-row__conf,
  .ep-row__decay {
    display: none;
  }
  .ep-row__actions {
    opacity: 1;
  }
}
</style>
