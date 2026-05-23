<script setup lang="ts">
import SlangQueueToolbar from '../../../slang/components/SlangQueueToolbar.vue'
import SlangSummaryBar from '../../../slang/components/SlangSummaryBar.vue'
import SlangTermList from '../../../slang/components/SlangTermList.vue'
import { useSlangConsoleInject } from './injection'

const console_ = useSlangConsoleInject()
const {
  summary,
  groups,
  loading,
  bulkLoading,
  driftBacklogLoading,
  scanningGlobal,
  terms,
  driftReviews,
  page,
  searchText,
  groupFilter,
  queueMode,
  minConfidence,
  sortBy,
  selectedTermIds,
  displayTotal,
  pageCount,
  setQueueMode,
  resetFilters,
  runGlobalScan,
  openDetail,
  quickStatus,
  reviewAiTerm,
  handleDriftAction,
  runBulkAction,
  processDriftBacklog,
} = console_
</script>

<template>
  <section class="slang-fold-pane">
    <SlangSummaryBar :summary="summary" @switch-queue-mode="setQueueMode" />

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

    <NSkeleton v-if="loading" :repeat="6" text />

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
  </section>
</template>

<style scoped>
.slang-fold-pane {
  display: grid;
  gap: 14px;
}
</style>
