<script setup lang="ts">
import type { NounSlotContext } from './types'

defineProps<{ ctx: NounSlotContext }>()
</script>

<template>
  <div class="noun-coming-soon">
    <span class="noun-coming-soon__eyebrow">{{ ctx.noun.toUpperCase() }}</span>
    <p>{{ '即将到来' }}</p>
    <small>该名词的专属操作与统计正在迁入学习管道。</small>
  </div>
</template>

<style scoped>
.noun-coming-soon {
  display: grid;
  gap: 6px;
  padding: 16px;
  border: 1px dashed var(--om-border);
  border-radius: 12px;
  background: var(--om-surface-2);
}

.noun-coming-soon__eyebrow {
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.12em;
}

.noun-coming-soon p {
  margin: 0;
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 600;
}

.noun-coming-soon small {
  color: var(--om-text-3);
  font-size: 12px;
}
</style>
