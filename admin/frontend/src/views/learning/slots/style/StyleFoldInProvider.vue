<script setup lang="ts">
import { provide, watch } from 'vue'
import type { LearningStageKey } from '../../types'
import {
  STYLE_CONSOLE_KEY,
  createStyleConsole,
  stageToStyleStatus,
} from './state'
import StyleToolbarContent from './StyleToolbarContent.vue'
import StyleSidePanelContent from './StyleSidePanelContent.vue'
import StyleMainPane from './StyleMainPane.vue'
import StyleDrawerContent from './StyleDrawerContent.vue'

const props = defineProps<{
  stage: LearningStageKey
  group: string
  mainPaneTarget: string
  toolbarTarget: string
  sideTarget: string
}>()

const console_ = createStyleConsole()
provide(STYLE_CONSOLE_KEY, console_)

console_.stageStatusFilter.value = stageToStyleStatus(props.stage)

const showMainPane = computed(() => props.stage !== 'hits')

watch(
  () => props.stage,
  (stage) => {
    const next = stageToStyleStatus(stage)
    if (console_.stageStatusFilter.value !== next) {
      console_.stageStatusFilter.value = next
    }
  },
)

watch(
  () => props.group,
  (group) => {
    if (console_.groupId.value !== group) {
      console_.groupId.value = group
    }
  },
  { immediate: true },
)

watch(
  [
    () => console_.stageStatusFilter.value,
    () => console_.scopeFilter.value,
    () => console_.sortMode.value,
    () => console_.groupId.value,
  ],
  () => {
    void console_.loadAll()
  },
)

onMounted(() => {
  void console_.loadAll()
})
</script>

<template>
  <Teleport :to="props.toolbarTarget" defer>
    <StyleToolbarContent />
  </Teleport>
  <Teleport :to="props.sideTarget" defer>
    <StyleSidePanelContent />
  </Teleport>
  <Teleport v-if="showMainPane" :to="props.mainPaneTarget" defer>
    <StyleMainPane />
  </Teleport>
  <StyleDrawerContent />
</template>
