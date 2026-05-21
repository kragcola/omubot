<script setup lang="ts">
import AppPanelSection from '../../../components/common/AppPanelSection.vue'

interface ToolLink {
  label: string
  path: string
  note: string
}

interface Props {
  expanded: boolean
  tools: ToolLink[]
}

defineProps<Props>()

const emit = defineEmits<{
  (e: 'toggle'): void
  (e: 'navigate', path: string): void
}>()
</script>

<template>
  <AppPanelSection
    class="system-advanced-entry"
    eyebrow="Advanced Tools"
    title="低频工具与深度排查"
    description="LLM Provider 切换 / 协议探测 / 一键备份等不常用的运维入口默认折叠，需要时点开。"
  >
    <template #aside>
      <NButton secondary @click="emit('toggle')">
        {{ expanded ? '收起高级区' : '打开高级区' }}
      </NButton>
    </template>

    <div v-if="tools.length" class="system-advanced-entry__tools">
      <button
        v-for="tool in tools"
        :key="tool.path"
        type="button"
        class="system-advanced-entry__tool"
        @click="emit('navigate', tool.path)"
      >
        <strong>{{ tool.label }}</strong>
        <span>{{ tool.note }}</span>
      </button>
    </div>
  </AppPanelSection>
</template>

<style scoped>
.system-advanced-entry {
  margin-top: 16px;
}

.system-advanced-entry__tools {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.system-advanced-entry__tool {
  display: grid;
  gap: 6px;
  width: 100%;
  padding: 14px 16px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: var(--om-surface-2);
  color: inherit;
  cursor: pointer;
  text-align: left;
  transition:
    border-color 0.18s ease,
    transform 0.18s ease,
    background-color 0.18s ease;
}

.system-advanced-entry__tool:hover {
  transform: translateY(-1px);
  border-color: var(--om-border-strong);
}

.system-advanced-entry__tool strong {
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 700;
}

.system-advanced-entry__tool span {
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.6;
}

@media (max-width: 760px) {
  .system-advanced-entry__tools {
    grid-template-columns: 1fr;
  }
}
</style>
