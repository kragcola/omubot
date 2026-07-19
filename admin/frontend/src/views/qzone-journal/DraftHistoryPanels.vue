<script setup lang="ts">
import AppPanelSection from '../../components/common/AppPanelSection.vue'
import EmptyState from '../../components/common/EmptyState.vue'
import type { QzoneDraftAuditResponse, QzoneDraftRevision } from './types'
import { statusLabel } from './types'

defineProps<{
  revisions: QzoneDraftRevision[]
  audit: QzoneDraftAuditResponse | null
}>()

function formatTime(value: string | null | undefined): string {
  if (!value) return '—'
  return value.replace('T', ' ').replace(/\+00:00$/, ' UTC').replace(/Z$/, ' UTC')
}
</script>

<template>
  <div class="qzone-history">
    <AppPanelSection
      eyebrow="History"
      title="版本历史"
      description="同一逻辑事件的 append-only 修订链；旧版本正文不可改。"
    >
      <EmptyState
        v-if="!revisions.length"
        compact
        title="暂无版本记录"
        description="加载完成后将显示 revision 列表。"
      />
      <ul v-else class="qzone-history__list">
        <li
          v-for="item in revisions"
          :key="item.draft_id"
          class="qzone-history__item"
        >
          <div class="qzone-history__head">
            <strong>r{{ item.revision }} · {{ statusLabel(item.status) }}</strong>
            <span>{{ formatTime(item.created_at) }}</span>
          </div>
          <p class="qzone-history__path qzone-history__mono">
            {{ item.draft_id }}
            <template v-if="item.supersedes_draft_id">
              · 后继自 {{ item.supersedes_draft_id }}
            </template>
          </p>
          <p class="qzone-history__note">
            {{ item.content || '（无正文）' }}
          </p>
        </li>
      </ul>
    </AppPanelSection>

    <AppPanelSection
      eyebrow="Audit"
      title="审核决策"
      description="approve / reject 记录"
    >
      <EmptyState
        v-if="!audit?.review_decisions?.length"
        compact
        title="暂无审核决策"
        description="通过或拒绝后会写入此列表。"
      />
      <ul v-else class="qzone-history__list">
        <li
          v-for="item in audit.review_decisions"
          :key="item.decision_id"
          class="qzone-history__item"
        >
          <div class="qzone-history__head">
            <strong>{{ item.decision }}</strong>
            <span>{{ formatTime(item.created_at) }}</span>
          </div>
          <p class="qzone-history__path">
            {{ statusLabel(item.previous_status) }} → {{ statusLabel(item.new_status) }}
          </p>
          <p class="qzone-history__note">
            {{ item.note || '（无备注）' }}
          </p>
        </li>
      </ul>
    </AppPanelSection>

    <AppPanelSection
      eyebrow="Audit"
      title="人工处置"
      description="confirm-published / confirm-not-published 记录"
    >
      <EmptyState
        v-if="!audit?.manual_resolutions?.length"
        compact
        title="暂无人工处置"
        description="对 unknown 状态的确认会写入此列表。"
      />
      <ul v-else class="qzone-history__list">
        <li
          v-for="item in audit.manual_resolutions"
          :key="item.resolution_id"
          class="qzone-history__item"
        >
          <div class="qzone-history__head">
            <strong>{{ item.decision }}</strong>
            <span>{{ formatTime(item.created_at) }}</span>
          </div>
          <p class="qzone-history__path">
            {{ statusLabel(item.previous_status) }} → {{ statusLabel(item.new_status) }}
          </p>
          <p class="qzone-history__note">
            {{ item.note || '（无备注）' }}
          </p>
          <p v-if="item.external_post_id" class="qzone-history__note qzone-history__mono">
            外部 ID：{{ item.external_post_id }}
          </p>
        </li>
      </ul>
    </AppPanelSection>
  </div>
</template>

<style scoped>
.qzone-history {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 16px;
}

.qzone-history__list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.qzone-history__item {
  padding: 12px;
  border: 1px solid var(--om-border);
  border-radius: 12px;
  background: var(--om-surface-2);
}

.qzone-history__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: var(--om-text-1);
  font-size: 13px;
}

.qzone-history__head span {
  color: var(--om-text-3);
  font-size: 12px;
}

.qzone-history__path {
  margin: 8px 0 0;
  color: var(--om-text-2);
  font-size: 12px;
}

.qzone-history__note {
  margin: 8px 0 0;
  color: var(--om-text-1);
  font-size: 13px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}

.qzone-history__mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
}
</style>
