<script setup lang="ts">
import { useEpisodeConsoleInject } from './state'

useEpisodeConsoleInject()
</script>

<template>
  <span class="episode-fold-toolbar__hint">由 Consolidator 周期写入；置信度 ≥ 0.6 自动晋升 candidate。</span>
</template>

<style scoped>
.episode-fold-toolbar__hint {
  color: var(--om-text-3);
  font-size: 11px;
  letter-spacing: 0.02em;
}
</style>
