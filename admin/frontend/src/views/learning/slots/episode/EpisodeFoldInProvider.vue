<script setup lang="ts">
import { provide, watch } from 'vue'
import type { LearningStageKey } from '../../types'
import {
  EPISODE_CONSOLE_KEY,
  createEpisodeConsole,
  stageToEpisodeState,
} from './state'
import EpisodeToolbarContent from './EpisodeToolbarContent.vue'
import EpisodeSidePanelContent from './EpisodeSidePanelContent.vue'
import EpisodeMainPane from './EpisodeMainPane.vue'
import EpisodeDrawerContent from './EpisodeDrawerContent.vue'

const props = defineProps<{
  stage: LearningStageKey
  group: string
  mainPaneTarget: string
  toolbarTarget: string
  sideTarget: string
}>()

const console_ = createEpisodeConsole()
provide(EPISODE_CONSOLE_KEY, console_)

console_.filterState.value = stageToEpisodeState(props.stage)

const showMainPane = computed(() => props.stage !== 'hits')

watch(
  () => props.stage,
  (stage) => {
    const next = stageToEpisodeState(stage)
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
  [() => console_.filterState.value, () => console_.filterGroup.value],
  () => {
    void console_.fetchEpisodes()
  },
)

onMounted(() => {
  console_.refresh()
})
</script>

<template>
  <Teleport :to="props.toolbarTarget" defer>
    <EpisodeToolbarContent />
  </Teleport>
  <Teleport :to="props.sideTarget" defer>
    <EpisodeSidePanelContent />
  </Teleport>
  <Teleport v-if="showMainPane" :to="props.mainPaneTarget" defer>
    <EpisodeMainPane />
  </Teleport>
  <EpisodeDrawerContent />
</template>
