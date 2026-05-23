<script setup lang="ts">
import { useMemoryConsoleInject } from './state'

const console_ = useMemoryConsoleInject()
const { filterDomain } = console_

const domainOptions = [
  { value: 'all', label: '全部 consolidator 域' },
  { value: 'fact', label: 'fact' },
  { value: 'slang', label: 'slang' },
  { value: 'style', label: 'style' },
  { value: 'episode', label: 'episode' },
  { value: 'graph_relation', label: 'graph' },
]
</script>

<template>
  <NSpace align="center" :size="6">
    <span class="memory-fold-toolbar__label">consolidator 候选</span>
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
  </NSpace>
</template>

<style scoped>
.memory-fold-toolbar__label {
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.memory-fold-toolbar__chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
}
</style>
