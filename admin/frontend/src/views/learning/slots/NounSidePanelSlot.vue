<script setup lang="ts">
import type { NounSlotContext } from './types'

defineProps<{ ctx: NounSlotContext }>()
</script>

<template>
  <aside v-if="$slots.default" class="noun-side-panel">
    <slot />
  </aside>
</template>

<style scoped>
.noun-side-panel {
  display: grid;
  gap: 12px;
}
</style>
