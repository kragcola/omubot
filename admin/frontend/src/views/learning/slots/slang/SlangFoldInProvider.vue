<script setup lang="ts">
import { provide, watch } from 'vue'
import type { LearningStageKey } from '../../types'
import { useSlangConsole } from '../../../slang/composables/useSlangConsole'
import type { SlangQueueMode } from '../../../slang/helpers/types'
import { SLANG_CONSOLE_KEY } from './injection'
import SlangToolbarContent from './SlangToolbarContent.vue'
import SlangSidePanelContent from './SlangSidePanelContent.vue'
import SlangMainPane from './SlangMainPane.vue'
import SlangDrawerContent from './SlangDrawerContent.vue'

const props = defineProps<{
  stage: LearningStageKey
  group: string
  mainPaneTarget: string
  toolbarTarget: string
  sideTarget: string
}>()

function stageToQueueMode(stage: LearningStageKey): SlangQueueMode | null {
  switch (stage) {
    case 'candidate':
      return 'candidate'
    case 'review':
      return 'pending_human_review'
    case 'approved':
      return 'approved'
    case 'archived':
      return 'archived'
    case 'hits':
    default:
      return null
  }
}

const slangConsole = useSlangConsole({
  initialQueueMode: stageToQueueMode(props.stage) ?? 'candidate',
})

provide(SLANG_CONSOLE_KEY, slangConsole)

const showMainPane = computed(() => props.stage !== 'hits')

watch(
  () => props.stage,
  (stage) => {
    const next = stageToQueueMode(stage)
    if (next && slangConsole.queueMode.value !== next) {
      slangConsole.queueMode.value = next
    }
  },
)

watch(
  () => props.group,
  (group) => {
    if (slangConsole.groupFilter.value !== group) {
      slangConsole.groupFilter.value = group
    }
  },
  { immediate: true },
)

onMounted(() => {
  void slangConsole.loadAll()
})
</script>

<template>
  <Teleport :to="props.toolbarTarget" defer>
    <SlangToolbarContent />
  </Teleport>
  <Teleport :to="props.sideTarget" defer>
    <SlangSidePanelContent />
  </Teleport>
  <Teleport v-if="showMainPane" :to="props.mainPaneTarget" defer>
    <SlangMainPane />
  </Teleport>
  <SlangDrawerContent />
</template>
