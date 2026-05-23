<script setup lang="ts">
import MemoryManageView from './MemoryView.vue'
import MemoryBrowseView from '../memos/MemosView.vue'

const route = useRoute()
const router = useRouter()

type MemoryViewMode = 'manage' | 'browse'

const activeView = computed<MemoryViewMode>(() =>
  route.query.view === 'manage' ? 'manage' : 'browse',
)

watchEffect(() => {
  if (route.name !== 'memory') return

  const queryView = route.query.view
  if (queryView === 'manage' || queryView === 'browse') return

  void router.replace({
    name: 'memory',
    query: { ...route.query, view: 'browse' },
  })
})

function setView(view: MemoryViewMode) {
  if (activeView.value === view) return

  void router.replace({
    name: 'memory',
    query: { ...route.query, view },
  })
}
</script>

<template>
  <MemoryBrowseView
    v-if="activeView === 'browse'"
    :active-view="activeView"
    @change-view="setView"
  />
  <MemoryManageView
    v-else
    :active-view="activeView"
    @change-view="setView"
  />
</template>
