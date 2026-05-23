<script setup lang="ts">
import type { NounSlotContext } from './types'

defineProps<{ ctx: NounSlotContext }>()
</script>

<template>
  <div class="noun-toolbar-slot">
    <slot />
  </div>
</template>

<style scoped>
.noun-toolbar-slot {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.noun-toolbar-slot:empty {
  display: none;
}
</style>
