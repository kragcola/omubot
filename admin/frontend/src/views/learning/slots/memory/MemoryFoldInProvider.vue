<script setup lang="ts">
import { provide, watch } from 'vue'
import type { LearningStageKey } from '../../types'
import {
  MEMORY_CONSOLE_KEY,
  createMemoryConsole,
  stageToCandidateState,
} from './state'
import MemoryToolbarContent from './MemoryToolbarContent.vue'
import MemorySidePanelContent from './MemorySidePanelContent.vue'
import MemoryDrawerContent from './MemoryDrawerContent.vue'
import MemoryCardDrawerContent from './MemoryCardDrawerContent.vue'

const props = defineProps<{
  stage: LearningStageKey
  group: string
  mainPaneTarget: string
  toolbarTarget: string
  sideTarget: string
}>()

void props.mainPaneTarget

const console_ = createMemoryConsole()
provide(MEMORY_CONSOLE_KEY, console_)

defineExpose({
  openCardDetail: console_.openCardDetail,
})

console_.filterState.value = stageToCandidateState(props.stage)

watch(
  () => props.stage,
  (stage) => {
    const next = stageToCandidateState(stage)
    if (console_.filterState.value !== next) {
      console_.filterState.value = next
    }
  },
)

watch(
  () => props.group,
  (group) => {
    if (console_.filterGroup.value !== group) {
      console_.filterGroup.value = group
    }
  },
  { immediate: true },
)

watch(
  () => console_.filterState.value,
  () => {
    void console_.fetchCandidates()
  },
)

onMounted(() => {
  void console_.fetchCandidates()
})
</script>

<template>
  <Teleport :to="props.toolbarTarget" defer>
    <MemoryToolbarContent />
  </Teleport>
  <Teleport :to="props.sideTarget" defer>
    <MemorySidePanelContent />
  </Teleport>
  <MemoryDrawerContent />
  <MemoryCardDrawerContent />
</template>
