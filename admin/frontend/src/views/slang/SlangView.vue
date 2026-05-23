<script setup lang="ts">
import {
  PricetagsOutline,
  RefreshOutline,
  SettingsOutline,
  SparklesOutline,
} from '@vicons/ionicons5'

import AppPage from '../../components/common/AppPage.vue'
import SlangStatsCards from './components/SlangStatsCards.vue'
import SlangBacklogProgress from './components/SlangBacklogProgress.vue'
import SlangExtractionProgress from './components/SlangExtractionProgress.vue'
import SlangCreateDrawer from './components/SlangCreateDrawer.vue'
import SlangDetailDrawer from './components/SlangDetailDrawer.vue'
import SlangQueueToolbar from './components/SlangQueueToolbar.vue'
import SlangSettingsDrawer from './components/SlangSettingsDrawer.vue'
import SlangSnapshotStrip from './components/SlangSnapshotStrip.vue'
import SlangSummaryBar from './components/SlangSummaryBar.vue'
import SlangTermList from './components/SlangTermList.vue'
import { useSlangConsole } from './composables/useSlangConsole'

const slangCacheRevision = 'slang-console-v3-foldin-pr-b'

const console_ = useSlangConsole()
const {
  summary,
  stats,
  terms,
  driftReviews,
  groups,
  loading,
  refreshing,
  extracting,
  scanningGlobal,
  bulkLoading,
  driftBacklogLoading,
  savingSettings,
  settingsDrawerVisible,
  runningAiReview,
  createDrawerVisible,
  creatingTerm,
  page,
  searchText,
  groupFilter,
  queueMode,
  minConfidence,
  sortBy,
  selectedTermIds,
  settings,
  allowlistText,
  stoplistText,
  createDraft,
  drawerVisible,
  detailLoading,
  detailTerm,
  observations,
  revisions,
  editAliases,
  mergeTargetId,
  mergeSearchText,
  mergeLoading,
  displayTotal,
  pageCount,
  mergeOptions,
  loadAll,
  runExtract,
  runForceAiReview,
  runGlobalScan,
  openCreateDrawer,
  saveCreateTerm,
  quickStatus,
  reviewAiTerm,
  openDetail,
  saveDetail,
  recomputeConfidence,
  mergeCurrentIntoTarget,
  saveSettings,
  resetFilters,
  setQueueMode,
  runBulkAction,
  handleDriftAction,
  processDriftBacklog,
  loadMergeCandidates,
  loadSummary,
} = console_

onMounted(() => {
  void loadAll()
})
</script>

<template>
  <AppPage
    title="群内黑话"
    eyebrow="Slang Review"
    description="从群聊中学习候选黑话，人工审核后按群注入语境，帮助 Omubot 理解社群内部约定。"
  >
    <span class="slang-cache-revision" aria-hidden="true">
      {{ slangCacheRevision }}
    </span>

    <template #action>
      <NSpace align="center" :size="10">
        <NButton secondary size="small" :loading="refreshing" @click="loadAll(true)">
          <template #icon>
            <NIcon :component="RefreshOutline" />
          </template>
          刷新
        </NButton>
        <NButton secondary size="small" :loading="extracting" @click="runExtract">
          <template #icon>
            <NIcon :component="SparklesOutline" />
          </template>
          手动抽取
        </NButton>
        <NPopconfirm
          :positive-text="'确认执行'"
          :negative-text="'取消'"
          @positive-click="runForceAiReview"
        >
          <template #trigger>
            <NButton secondary size="small" :loading="runningAiReview">
              <template #icon>
                <NIcon :component="SparklesOutline" />
              </template>
              AI 清池
            </NButton>
          </template>
          将立即对所有启用的群跑一次 AI 审核（force=true，跳过当日去重），可能消耗较多 LLM 配额。是否继续？
        </NPopconfirm>
        <NButton secondary size="small" @click="openCreateDrawer">
          <template #icon>
            <NIcon :component="PricetagsOutline" />
          </template>
          新建黑话
        </NButton>
        <NButton quaternary size="small" @click="settingsDrawerVisible = true">
          <template #icon>
            <NIcon :component="SettingsOutline" />
          </template>
          设置
        </NButton>
      </NSpace>
    </template>

    <SlangSummaryBar :summary="summary" @switch-queue-mode="setQueueMode" />

    <SlangSnapshotStrip :summary="summary" />

    <div class="slang-main-layout">
      <div class="slang-main-layout__main">
        <SlangQueueToolbar
          v-model:search-text="searchText"
          v-model:group-filter="groupFilter"
          v-model:queue-mode="queueMode"
          v-model:min-confidence="minConfidence"
          v-model:sort-by="sortBy"
          :summary="summary"
          :groups="groups"
          :display-total="displayTotal"
          :scanning-global="scanningGlobal"
          @reset="resetFilters"
          @scan-global="runGlobalScan"
        />

        <NSkeleton v-if="loading" :repeat="8" text />

        <SlangTermList
          v-else
          v-model:page="page"
          v-model:selected-term-ids="selectedTermIds"
          :terms="terms"
          :drift-reviews="driftReviews"
          :queue-mode="queueMode"
          :page-count="pageCount"
          :bulk-loading="bulkLoading"
          :drift-backlog-loading="driftBacklogLoading"
          @open-detail="openDetail"
          @quick-status="quickStatus"
          @review-ai="reviewAiTerm"
          @drift-action="handleDriftAction"
          @bulk-action="runBulkAction"
          @drift-process-backlog="processDriftBacklog"
        />
      </div>

      <aside class="slang-main-layout__side">
        <SlangBacklogProgress :eligible-count="summary.eligible_backlog_count" @progress="loadSummary" />

        <SlangExtractionProgress />

        <SlangStatsCards
          v-if="!loading"
          :summary="summary"
          :stats="stats"
        />
      </aside>
    </div>

    <SlangCreateDrawer
      v-model:visible="createDrawerVisible"
      v-model:draft="createDraft"
      :creating-term="creatingTerm"
      @save="saveCreateTerm"
    />

    <SlangDetailDrawer
      v-model:visible="drawerVisible"
      v-model:detail-term="detailTerm"
      v-model:edit-aliases="editAliases"
      v-model:merge-target-id="mergeTargetId"
      v-model:merge-search-text="mergeSearchText"
      :detail-loading="detailLoading"
      :observations="observations"
      :revisions="revisions"
      :merge-options="mergeOptions"
      :merge-loading="mergeLoading"
      @save="saveDetail"
      @recompute-confidence="recomputeConfidence"
      @merge="mergeCurrentIntoTarget"
      @review-ai="reviewAiTerm"
      @search-merge="loadMergeCandidates"
    />

    <SlangSettingsDrawer
      v-model:visible="settingsDrawerVisible"
      v-model:settings="settings"
      v-model:allowlist-text="allowlistText"
      v-model:stoplist-text="stoplistText"
      :saving-settings="savingSettings"
      @save="saveSettings"
    />
  </AppPage>
</template>

<style scoped>
.slang-cache-revision {
  display: none;
}

.slang-main-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 280px;
  gap: 16px;
  align-items: start;
}

.slang-main-layout__main {
  display: grid;
  gap: 16px;
  min-width: 0;
}

.slang-main-layout__side {
  position: sticky;
  top: 16px;
  display: grid;
  gap: 14px;
}

@media (max-width: 1100px) {
  .slang-main-layout {
    grid-template-columns: 1fr;
  }

  .slang-main-layout__side {
    position: static;
  }
}
</style>
