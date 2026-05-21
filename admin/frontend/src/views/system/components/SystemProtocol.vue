<script setup lang="ts">
import { computed } from 'vue'

import AppPanelSection from '../../../components/common/AppPanelSection.vue'
import {
  compatibilityLabel,
  compatibilityTagType,
  protocolConnectionEventLabel,
  protocolConnectionLabel,
  protocolConnectionType,
  protocolTagType,
  protocolTraceType,
} from '../helpers/badges'
import { formatDuration, formatMs, formatTimestamp } from '../helpers/formatters'
import type {
  ProtocolConnectionPayload,
  ProtocolHealth,
  ProtocolTracePayload,
} from '../helpers/types'

interface Props {
  protocol: ProtocolHealth | null
  traces: ProtocolTracePayload | null
  connections: ProtocolConnectionPayload | null
  probing: boolean
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'probe'): void
}>()

const okCount = computed(() =>
  (props.protocol?.capabilities || []).filter(item => item.status === 'ok' || item.status === 'configured').length,
)

const traceSummary = computed(() => props.traces?.summary || props.protocol?.trace_summary || null)

const connectionSummary = computed(() =>
  props.connections?.summary || props.protocol?.connection || null,
)
</script>

<template>
  <AppPanelSection
    class="system-panel"
    eyebrow="Protocol Probe"
    title="协议能力"
  >
    <template #aside>
      <NButton size="small" secondary :loading="probing" @click="emit('probe')">
        探测
      </NButton>
    </template>

    <div class="system-protocol-summary">
      <div>
        <span>适配器</span>
        <strong>{{ protocol?.adapter || 'napcat' }}</strong>
      </div>
      <div>
        <span>连接 Bot</span>
        <strong>{{ protocol?.connected_bots ?? 0 }}</strong>
      </div>
      <div>
        <span>可用能力</span>
        <strong>{{ okCount }}/{{ protocol?.capabilities.length || 0 }}</strong>
      </div>
    </div>

    <div class="system-connection-panel">
      <div class="system-connection-panel__head">
        <div>
          <span>连接历史</span>
          <strong>
            {{ protocolConnectionLabel(connectionSummary?.current_status || 'unknown') }}
            · {{ connectionSummary?.connected_bots ?? 0 }} 个 Bot
          </strong>
        </div>
        <NTag
          size="small"
          round
          :type="protocolConnectionType(connectionSummary?.current_status || 'unknown')"
        >
          {{ connectionSummary?.event_count || 0 }} 条记录
        </NTag>
      </div>
      <div class="system-connection-meta">
        <span>最近变化 {{ formatTimestamp(connectionSummary?.changed_at) }}</span>
        <span>最近确认 {{ formatTimestamp(connectionSummary?.last_seen_at) }}</span>
        <span v-if="connectionSummary?.last_recovery_seconds != null">
          上次恢复 {{ formatDuration(connectionSummary.last_recovery_seconds) }}
        </span>
      </div>
      <p v-if="connectionSummary?.last_error" class="system-connection-error">
        最近错误：{{ connectionSummary.last_error }}
      </p>
      <div v-if="connections?.events?.length" class="system-connection-list">
        <div
          v-for="event in connections.events"
          :key="event.event_id"
          class="system-connection-row"
        >
          <div>
            <strong>{{ protocolConnectionEventLabel(event) }}</strong>
            <span>
              {{ formatTimestamp(event.occurred_at) }}
              · {{ event.source }}
              <template v-if="event.self_ids?.length">
                · self_id={{ event.self_ids.join(', ') }}
              </template>
            </span>
            <small v-if="event.error">{{ event.error }}</small>
          </div>
          <NTag size="small" round :type="protocolConnectionType(event.status)">
            {{ protocolConnectionLabel(event.status) }}
          </NTag>
        </div>
      </div>
      <p v-else class="system-connection-empty">
        暂无连接状态变化；Bot 首次连接、断开或探测异常后会在这里留下记录。
      </p>
    </div>

    <div class="system-trace-panel">
      <div class="system-trace-panel__head">
        <div>
          <span>请求 Echo 追踪</span>
          <strong>{{ traceSummary?.ok || 0 }} 成功 / {{ traceSummary?.failed || 0 }} 失败</strong>
        </div>
        <NTag size="small" round :type="(traceSummary?.failed || 0) > 0 ? 'warning' : 'success'">
          平均 {{ formatMs(traceSummary?.avg_elapsed_ms) }}
        </NTag>
      </div>
      <div v-if="traces?.traces?.length" class="system-trace-list">
        <div
          v-for="trace in traces.traces"
          :key="trace.trace_id"
          class="system-trace-row"
        >
          <div>
            <strong>{{ trace.action }}</strong>
            <span>{{ trace.trace_id }} · {{ formatMs(trace.elapsed_ms) }}</span>
          </div>
          <NTag size="small" round :type="protocolTraceType(trace.status)">
            {{ trace.status }}
          </NTag>
        </div>
      </div>
      <p v-else class="system-trace-empty">
        Bot 连接后，OneBot API 调用会在这里留下最近追踪记录。
      </p>
    </div>

    <div class="system-capability-list">
      <div
        v-for="capability in protocol?.capabilities || []"
        :key="capability.key"
        class="system-capability-row"
      >
        <div>
          <strong>{{ capability.label }}</strong>
          <span>{{ capability.detail }}</span>
        </div>
        <NTag size="small" round :type="protocolTagType(capability.status)">
          {{ capability.status }}
        </NTag>
      </div>
    </div>

    <div class="system-compatibility-panel">
      <div class="system-compatibility-panel__head">
        <div>
          <span>兼容清单</span>
          <strong>NapCat 默认 · LLOneBot 备用目标</strong>
        </div>
        <NTag size="small" round>
          只读检查
        </NTag>
      </div>
      <div class="system-compatibility-list">
        <div
          v-for="item in protocol?.compatibility || []"
          :key="item.key"
          class="system-compatibility-row"
        >
          <div class="system-compatibility-row__main">
            <strong>{{ item.label }}</strong>
            <span>{{ item.detail }}</span>
          </div>
          <div class="system-compatibility-row__tags">
            <NTag size="small" round :type="compatibilityTagType(item.napcat)">
              NapCat {{ compatibilityLabel(item.napcat) }}
            </NTag>
            <NTag size="small" round :type="compatibilityTagType(item.llonebot)">
              LLOneBot {{ compatibilityLabel(item.llonebot) }}
            </NTag>
          </div>
        </div>
      </div>
    </div>
  </AppPanelSection>
</template>

<style scoped>
.system-panel {
  min-height: 100%;
}

.system-protocol-summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.system-protocol-summary div {
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: var(--om-surface-2);
}

.system-protocol-summary span {
  display: block;
  color: var(--om-text-3);
  font-size: 12px;
}

.system-protocol-summary strong {
  display: block;
  margin-top: 8px;
  color: var(--om-text-1);
  font-size: 18px;
}

.system-trace-panel,
.system-connection-panel {
  margin-top: 14px;
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: color-mix(in srgb, var(--om-surface-solid) 72%, transparent);
}

.system-connection-panel__head,
.system-trace-panel__head,
.system-connection-row,
.system-trace-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.system-connection-panel__head span,
.system-trace-panel__head span,
.system-connection-row span,
.system-trace-row span {
  display: block;
  color: var(--om-text-3);
  font-size: 12px;
}

.system-connection-panel__head strong,
.system-trace-panel__head strong,
.system-connection-row strong,
.system-trace-row strong {
  display: block;
  margin-top: 5px;
  color: var(--om-text-1);
  font-size: 13px;
}

.system-connection-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  margin-top: 12px;
  color: var(--om-text-2);
  font-size: 12px;
}

.system-connection-error {
  margin: 10px 0 0;
  padding: 10px 12px;
  border: 1px solid color-mix(in srgb, var(--om-danger) 30%, var(--om-border));
  border-radius: 12px;
  background: color-mix(in srgb, var(--om-danger) 9%, transparent);
  color: var(--om-danger);
  font-size: 12px;
  line-height: 1.6;
}

.system-connection-list,
.system-trace-list {
  display: grid;
  gap: 8px;
  margin-top: 12px;
}

.system-connection-row,
.system-trace-row {
  padding: 10px 12px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: color-mix(in srgb, var(--om-surface) 35%, transparent);
}

.system-connection-row div {
  min-width: 0;
}

.system-connection-row small {
  display: block;
  overflow: hidden;
  margin-top: 4px;
  color: var(--om-danger);
  font-size: 11px;
  line-height: 1.5;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.system-trace-empty,
.system-connection-empty {
  margin: 12px 0 0;
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.6;
}

.system-capability-list {
  display: grid;
  gap: 10px;
  margin-top: 14px;
}

.system-capability-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: color-mix(in srgb, var(--om-surface) 32%, transparent);
}

.system-capability-row div {
  min-width: 0;
}

.system-capability-row strong {
  display: block;
  color: var(--om-text-1);
  font-size: 13px;
}

.system-capability-row span {
  display: block;
  overflow: hidden;
  margin-top: 4px;
  color: var(--om-text-2);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.system-compatibility-panel {
  margin-top: 14px;
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: color-mix(in srgb, var(--om-surface-solid) 66%, transparent);
}

.system-compatibility-panel__head,
.system-compatibility-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.system-compatibility-panel__head span {
  display: block;
  color: var(--om-text-3);
  font-size: 12px;
}

.system-compatibility-panel__head strong {
  display: block;
  margin-top: 5px;
  color: var(--om-text-1);
  font-size: 13px;
}

.system-compatibility-list {
  display: grid;
  gap: 8px;
  margin-top: 12px;
}

.system-compatibility-row {
  padding: 10px 12px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: color-mix(in srgb, var(--om-surface) 35%, transparent);
}

.system-compatibility-row__main {
  min-width: 0;
}

.system-compatibility-row__main strong {
  display: block;
  color: var(--om-text-1);
  font-size: 13px;
}

.system-compatibility-row__main span {
  display: block;
  margin-top: 5px;
  color: var(--om-text-2);
  font-size: 12px;
  line-height: 1.5;
}

.system-compatibility-row__tags {
  display: flex;
  flex-shrink: 0;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
}

@media (max-width: 760px) {
  .system-protocol-summary {
    grid-template-columns: 1fr;
  }

  .system-compatibility-panel__head,
  .system-compatibility-row,
  .system-connection-panel__head,
  .system-connection-row {
    flex-direction: column;
    align-items: stretch;
  }

  .system-compatibility-row__tags {
    justify-content: flex-start;
  }
}
</style>
