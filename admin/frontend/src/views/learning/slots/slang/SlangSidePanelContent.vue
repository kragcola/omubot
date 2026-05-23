<script setup lang="ts">
import SlangBacklogProgress from '../../../slang/components/SlangBacklogProgress.vue'
import SlangExtractionProgress from '../../../slang/components/SlangExtractionProgress.vue'
import SlangSnapshotStrip from '../../../slang/components/SlangSnapshotStrip.vue'
import SlangStatsCards from '../../../slang/components/SlangStatsCards.vue'
import { useSlangConsoleInject } from './injection'

const console_ = useSlangConsoleInject()
const { summary, stats, loading, loadSummary } = console_
</script>

<template>
  <SlangSnapshotStrip :summary="summary" />
  <SlangBacklogProgress
    :eligible-count="summary.eligible_backlog_count"
    @progress="loadSummary"
  />
  <SlangExtractionProgress />
  <SlangStatsCards
    v-if="!loading"
    :summary="summary"
    :stats="stats"
  />
</template>
