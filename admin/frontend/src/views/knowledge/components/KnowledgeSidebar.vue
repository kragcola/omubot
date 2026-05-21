<script setup lang="ts">
import {
  AlertCircleOutline,
  DocumentTextOutline,
  FlashOutline,
  FolderOpenOutline,
  GitNetworkOutline,
  HourglassOutline,
  RefreshOutline,
  SparklesOutline,
} from '@vicons/ionicons5'

interface Props {
  available: boolean
  entryCount: number
  sourceCount: number
  relationshipCount: number
  skippedCount: number
  pendingCount: number
  scopeRiskCount: number
  refreshing: boolean
  reindexing: boolean
}

defineProps<Props>()

const emit = defineEmits<{
  (e: 'refresh'): void
  (e: 'reindex'): void
  (e: 'open-admin', tab: 'candidates' | 'graph' | 'graph_nodes'): void
}>()
</script>

<template>
  <aside class="knowledge-sidebar">
    <div class="knowledge-sidebar__group">
      <p class="knowledge-sidebar__eyebrow">
        Index
      </p>
      <div class="knowledge-sidebar__stat">
        <span class="knowledge-sidebar__stat-icon">
          <NIcon :component="DocumentTextOutline" />
        </span>
        <div class="knowledge-sidebar__stat-body">
          <span>文档片段</span>
          <strong>{{ entryCount }}</strong>
        </div>
      </div>
      <div class="knowledge-sidebar__stat">
        <span class="knowledge-sidebar__stat-icon">
          <NIcon :component="FolderOpenOutline" />
        </span>
        <div class="knowledge-sidebar__stat-body">
          <span>文档源</span>
          <strong>{{ sourceCount }}</strong>
        </div>
      </div>
      <div class="knowledge-sidebar__stat">
        <span class="knowledge-sidebar__stat-icon">
          <NIcon :component="FlashOutline" />
        </span>
        <div class="knowledge-sidebar__stat-body">
          <span>图谱事实</span>
          <strong>{{ relationshipCount }}</strong>
        </div>
      </div>
    </div>

    <div class="knowledge-sidebar__group">
      <p class="knowledge-sidebar__eyebrow">
        Backlog
      </p>
      <NTooltip placement="left" :style="{ maxWidth: '260px' }">
        <template #trigger>
          <button
            type="button"
            class="knowledge-chip"
            :class="{ 'knowledge-chip--warn': pendingCount > 0 }"
            @click="emit('open-admin', 'candidates')"
          >
            <span class="knowledge-chip__icon">
              <NIcon :component="AlertCircleOutline" />
            </span>
            <span class="knowledge-chip__label">候选待审</span>
            <strong class="knowledge-chip__value">{{ pendingCount }}</strong>
          </button>
        </template>
        中置信度的图谱候选事实，等待人工审核入图谱。点击打开「候选队列」管理抽屉。
      </NTooltip>
      <NTooltip placement="left" :style="{ maxWidth: '260px' }">
        <template #trigger>
          <button
            type="button"
            class="knowledge-chip"
            :class="{ 'knowledge-chip--warn': scopeRiskCount > 0 }"
            @click="emit('open-admin', 'graph')"
          >
            <span class="knowledge-chip__icon">
              <NIcon :component="GitNetworkOutline" />
            </span>
            <span class="knowledge-chip__label">作用域待查</span>
            <strong class="knowledge-chip__value">{{ scopeRiskCount }}</strong>
          </button>
        </template>
        图谱事实存在跨群可见性 / 作用域风险，需要人工治理。点击打开「图谱关系」管理抽屉。
      </NTooltip>
      <NTooltip placement="left" :style="{ maxWidth: '260px' }">
        <template #trigger>
          <button
            type="button"
            class="knowledge-chip"
            :class="{ 'knowledge-chip--warn': skippedCount > 0 }"
            @click="emit('open-admin', 'graph_nodes')"
          >
            <span class="knowledge-chip__icon">
              <NIcon :component="HourglassOutline" />
            </span>
            <span class="knowledge-chip__label">跳过源</span>
            <strong class="knowledge-chip__value">{{ skippedCount }}</strong>
          </button>
        </template>
        未进入索引的文档源 / 节点状态。点击打开「图谱节点」管理抽屉查看明细。
      </NTooltip>
    </div>

    <div class="knowledge-sidebar__group">
      <p class="knowledge-sidebar__eyebrow">
        Actions
      </p>
      <NButton
        secondary
        block
        size="small"
        :loading="refreshing"
        @click="emit('refresh')"
      >
        <template #icon>
          <NIcon :component="RefreshOutline" />
        </template>
        刷新数据
      </NButton>
      <NPopconfirm
        :positive-text="'确认重建'"
        :negative-text="'取消'"
        @positive-click="emit('reindex')"
      >
        <template #trigger>
          <NButton
            type="primary"
            secondary
            block
            size="small"
            :disabled="!available"
            :loading="reindexing"
          >
            <template #icon>
              <NIcon :component="SparklesOutline" />
            </template>
            重建索引
          </NButton>
        </template>
        重建索引会重新读取所有文档源并重新切片，过程中可能短暂占用 CPU。确认继续？
      </NPopconfirm>
    </div>
  </aside>
</template>

<style scoped>
.knowledge-sidebar {
  position: sticky;
  top: 16px;
  display: grid;
  gap: 14px;
}

.knowledge-sidebar__group {
  display: grid;
  gap: 8px;
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: var(--om-surface);
}

.knowledge-sidebar__eyebrow {
  margin: 0 0 4px;
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}

.knowledge-sidebar__stat {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: var(--om-surface-2);
}

.knowledge-sidebar__stat-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--om-primary) 12%, transparent);
  color: var(--om-primary);
  font-size: 16px;
}

.knowledge-sidebar__stat-body {
  display: flex;
  flex: 1;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
}

.knowledge-sidebar__stat-body span {
  color: var(--om-text-3);
  font-size: 12px;
}

.knowledge-sidebar__stat-body strong {
  color: var(--om-text-1);
  font-size: 18px;
  font-weight: 700;
  line-height: 1;
}

.knowledge-chip {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 9px 12px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: var(--om-surface-2);
  color: var(--om-text-2);
  cursor: pointer;
  text-align: left;
  font: inherit;
  transition:
    border-color 0.18s ease,
    background-color 0.18s ease,
    color 0.18s ease,
    transform 0.18s ease;
}

.knowledge-chip:hover {
  transform: translateY(-1px);
  border-color: var(--om-border-strong);
  color: var(--om-text-1);
}

.knowledge-chip__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  color: var(--om-text-3);
  font-size: 14px;
}

.knowledge-chip__label {
  flex: 1;
  font-size: 13px;
}

.knowledge-chip__value {
  color: var(--om-text-1);
  font-size: 16px;
  font-weight: 700;
  line-height: 1;
}

.knowledge-chip--warn {
  border-color: rgba(197, 138, 43, 0.35);
  background: rgba(197, 138, 43, 0.08);
  color: var(--om-text-1);
}

.knowledge-chip--warn .knowledge-chip__icon {
  color: rgba(197, 138, 43, 0.95);
}

.knowledge-chip--warn .knowledge-chip__value {
  color: rgba(176, 116, 21, 1);
}

@media (max-width: 1180px) {
  .knowledge-sidebar {
    position: static;
  }
}
</style>
