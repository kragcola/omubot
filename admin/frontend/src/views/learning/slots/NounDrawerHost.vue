<script setup lang="ts">
import type { NounSlotContext } from './types'

defineProps<{ ctx: NounSlotContext }>()
</script>

<template>
  <Teleport to="body">
    <slot />
  </Teleport>
</template>
