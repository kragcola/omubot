<script setup lang="ts">
import { RefreshOutline } from '@vicons/ionicons5'
import { useMemoryConsoleInject } from './state'

const console_ = useMemoryConsoleInject()
const { loading, fetchCandidates, filterDomain } = console_

const domainOptions = [
  { value: 'all', label: '全部域' },
  { value: 'fact', label: 'fact' },
  { value: 'slang', label: 'slang' },
  { value: 'style', label: 'style' },
  { value: 'episode', label: 'episode' },
  { value: 'graph_relation', label: 'graph' },
]
</script>

<template>
  <NSpace align="center" :size="6">
    <div class="memory-fold-toolbar__chips">
      <NTag
        v-for="opt in domainOptions"
        :key="opt.value"
        size="small"
        round
        :checkable="true"
        :checked="filterDomain === opt.value"
        :type="filterDomain === opt.value ? 'primary' : 'default'"
        @update:checked="(v: boolean) => { if (v) filterDomain = opt.value }"
      >
        {{ opt.label }}
      </NTag>
    </div>
    <NButton secondary size="small" :loading="loading" @click="fetchCandidates">
      <template #icon>
        <NIcon :component="RefreshOutline" />
      </template>
      刷新
    </NButton>
  </NSpace>
</template>

<style scoped>
.memory-fold-toolbar__chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
}
</style>
