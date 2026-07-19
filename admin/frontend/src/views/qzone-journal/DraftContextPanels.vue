<script setup lang="ts">
import AppPanelSection from '../../components/common/AppPanelSection.vue'
import StateBadge from '../../components/common/StateBadge.vue'
import type { QzoneDraft, QzoneReviewBundle } from './types'
import { statusBadge, statusLabel } from './types'

interface DisplayRow {
  key: string
  value: string
}

defineProps<{
  draft: QzoneDraft
  review: QzoneReviewBundle | null
  provenanceRows: DisplayRow[]
  publicProjectionRows: DisplayRow[]
  dryRunRows: DisplayRow[]
}>()

function formatTime(value: string | null | undefined): string {
  if (!value) return '—'
  return value.replace('T', ' ').replace(/\+00:00$/, ' UTC').replace(/Z$/, ' UTC')
}
</script>

<template>
  <div class="qzone-context">
    <AppPanelSection eyebrow="Content" title="草稿正文">
      <pre class="qzone-context__content">{{ draft.content }}</pre>
    </AppPanelSection>

    <AppPanelSection eyebrow="Review" title="审核溯源">
      <dl class="qzone-context__meta">
        <div class="qzone-context__meta-row">
          <dt>稳定 ID</dt>
          <dd>{{ review?.stable_id || draft.stable_id || '—' }}</dd>
        </div>
        <div class="qzone-context__meta-row">
          <dt>主体类型</dt>
          <dd>{{ review?.subject_kind || draft.subject_kind || '—' }}</dd>
        </div>
        <div class="qzone-context__meta-row">
          <dt>隐私</dt>
          <dd>{{ review?.privacy || draft.privacy || '—' }}</dd>
        </div>
        <div class="qzone-context__meta-row">
          <dt>显著性</dt>
          <dd>{{ review?.salience ?? draft.salience ?? '—' }}</dd>
        </div>
        <div class="qzone-context__meta-row qzone-context__meta-row--full">
          <dt>来源摘要</dt>
          <dd>{{ review?.source_summary || draft.source_summary || '—' }}</dd>
        </div>
        <div class="qzone-context__meta-row">
          <dt>去重键</dt>
          <dd class="qzone-context__mono">{{ draft.dedupe_key }}</dd>
        </div>
      </dl>

      <div v-if="provenanceRows.length" class="qzone-context__subblock">
        <p class="qzone-context__subtitle">
          Provenance
        </p>
        <dl class="qzone-context__meta">
          <div
            v-for="row in provenanceRows"
            :key="row.key"
            class="qzone-context__meta-row"
          >
            <dt>{{ row.key }}</dt>
            <dd class="qzone-context__mono">{{ row.value }}</dd>
          </div>
        </dl>
      </div>

      <div v-if="publicProjectionRows.length" class="qzone-context__subblock">
        <p class="qzone-context__subtitle">
          Public projection
        </p>
        <dl class="qzone-context__meta">
          <div
            v-for="row in publicProjectionRows"
            :key="`pp-${row.key}`"
            class="qzone-context__meta-row"
            :class="{ 'qzone-context__meta-row--full': row.key === 'aliases' }"
          >
            <dt>{{ row.key }}</dt>
            <dd class="qzone-context__mono">{{ row.value }}</dd>
          </div>
        </dl>
      </div>
    </AppPanelSection>

    <AppPanelSection eyebrow="Delivery" title="投递状态">
      <dl class="qzone-context__meta">
        <div class="qzone-context__meta-row">
          <dt>状态</dt>
          <dd>
            <StateBadge
              :status="statusBadge(draft.status)"
              :label="statusLabel(draft.status)"
            />
          </dd>
        </div>
        <div class="qzone-context__meta-row">
          <dt>审批作用域</dt>
          <dd>{{ draft.approval_scope === 'live' ? 'live' : 'dry_run' }}</dd>
        </div>
        <div class="qzone-context__meta-row">
          <dt>发布日</dt>
          <dd>{{ draft.publish_date || '—' }}</dd>
        </div>
        <div class="qzone-context__meta-row">
          <dt>外部 ID</dt>
          <dd class="qzone-context__mono">{{ draft.external_post_id || '—' }}</dd>
        </div>
        <div class="qzone-context__meta-row">
          <dt>错误码</dt>
          <dd>{{ draft.last_error_code || '—' }}</dd>
        </div>
        <div class="qzone-context__meta-row">
          <dt>创建时间</dt>
          <dd>{{ formatTime(draft.created_at) }}</dd>
        </div>
        <div class="qzone-context__meta-row">
          <dt>更新时间</dt>
          <dd>{{ formatTime(draft.updated_at) }}</dd>
        </div>
      </dl>
    </AppPanelSection>

    <AppPanelSection
      v-if="dryRunRows.length"
      eyebrow="Dry-run"
      title="模拟描述符"
      description="仅展示消毒后的线缆描述，不含凭证或原始响应。"
    >
      <dl class="qzone-context__meta">
        <div
          v-for="row in dryRunRows"
          :key="row.key"
          class="qzone-context__meta-row"
        >
          <dt>{{ row.key }}</dt>
          <dd class="qzone-context__mono">{{ row.value }}</dd>
        </div>
      </dl>
    </AppPanelSection>
  </div>
</template>

<style scoped>
.qzone-context {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 16px;
}

.qzone-context__content {
  margin: 0;
  padding: 12px 16px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: var(--om-surface-2);
  color: var(--om-text-1);
  font-family: inherit;
  font-size: 13px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
}

.qzone-context__meta {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin: 0;
}

.qzone-context__meta-row {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
}

.qzone-context__meta-row--full {
  grid-column: 1 / -1;
}

.qzone-context__meta-row dt {
  color: var(--om-text-3);
  font-size: 12px;
  font-weight: 600;
}

.qzone-context__meta-row dd {
  margin: 0;
  color: var(--om-text-1);
  font-size: 13px;
  line-height: 1.5;
  word-break: break-word;
}

.qzone-context__mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
}

.qzone-context__subblock {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--om-border);
}

.qzone-context__subtitle {
  margin: 0 0 12px;
  color: var(--om-text-2);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

@media (max-width: 640px) {
  .qzone-context__meta {
    grid-template-columns: 1fr;
  }
}
</style>
