<script setup lang="ts">
import AppDrawerHeader from '../../../components/common/AppDrawerHeader.vue'
import AppDrawerLayout from '../../../components/common/AppDrawerLayout.vue'
import AppPanelSection from '../../../components/common/AppPanelSection.vue'
import { REPEAT_POLICY_OPTIONS, STATUS_OPTIONS } from '../helpers/badges'
import type { SlangCreateDraft } from '../helpers/types'

defineProps<{
  creatingTerm: boolean
}>()

const visible = defineModel<boolean>('visible', { required: true })
const draft = defineModel<SlangCreateDraft>('draft', { required: true })

const emit = defineEmits<{
  (e: 'save'): void
}>()
</script>

<template>
  <NDrawer v-model:show="visible" :width="620">
    <NDrawerContent closable>
      <template #header>
        <AppDrawerHeader
          eyebrow="Manual Slang"
          title="主动构建黑话"
          description="手动录入群内约定词、释义和使用策略；可直接批准进入 Prompt 注入候选。"
        />
      </template>

      <AppDrawerLayout>
        <AppPanelSection eyebrow="Term" title="词条信息">
          <div class="slang-detail-grid">
            <label>
              <span>黑话词</span>
              <NInput v-model:value="draft.term" placeholder="例如：猫饼" />
            </label>
            <label>
              <span>作用域</span>
              <NSelect
                v-model:value="draft.scope"
                :options="[{ label: '当前群', value: 'group' }, { label: '全局', value: 'global' }]"
              />
            </label>
            <label v-if="draft.scope === 'group'">
              <span>群号</span>
              <NInput v-model:value="draft.group_id" placeholder="输入群号；可用当前筛选群自动带入" />
            </label>
            <label>
              <span>状态</span>
              <NSelect v-model:value="draft.status" :options="STATUS_OPTIONS.filter(option => option.value)" />
            </label>
            <label>
              <span>置信度</span>
              <NInputNumber v-model:value="draft.confidence" :min="0" :max="1" :step="0.05" />
            </label>
            <label>
              <span>复述策略</span>
              <NSelect v-model:value="draft.repeat_policy" :options="REPEAT_POLICY_OPTIONS" />
            </label>
            <label class="slang-detail-grid__full">
              <span>释义</span>
              <NInput
                v-model:value="draft.meaning"
                type="textarea"
                :autosize="{ minRows: 3, maxRows: 6 }"
                placeholder="说明这个词在群里的真实含义和适用场景"
              />
            </label>
            <label class="slang-detail-grid__full">
              <span>别名（每行一个）</span>
              <NInput
                v-model:value="draft.aliases"
                type="textarea"
                :autosize="{ minRows: 2, maxRows: 5 }"
                placeholder="可选；例如缩写、同义写法、错别字梗"
              />
            </label>
          </div>
        </AppPanelSection>

        <AppPanelSection eyebrow="Context" title="示例与备注">
          <div class="slang-detail-grid">
            <label class="slang-detail-grid__full">
              <span>示例 / 证据</span>
              <NInput
                v-model:value="draft.evidence"
                type="textarea"
                :autosize="{ minRows: 2, maxRows: 5 }"
                placeholder="可选；写一句典型群聊用法，方便后续审核追溯"
              />
            </label>
            <label class="slang-detail-grid__full">
              <span>备注</span>
              <NInput
                v-model:value="draft.notes"
                type="textarea"
                :autosize="{ minRows: 2, maxRows: 5 }"
                placeholder="可选；记录来源、边界或维护说明"
              />
            </label>
          </div>
        </AppPanelSection>

        <template #footer>
          <NButton secondary @click="visible = false">
            取消
          </NButton>
          <NButton type="primary" :loading="creatingTerm" @click="emit('save')">
            创建黑话
          </NButton>
        </template>
      </AppDrawerLayout>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.slang-detail-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.slang-detail-grid label {
  display: grid;
  gap: 8px;
}

.slang-detail-grid span {
  color: var(--om-text-2);
  font-size: 13px;
}

.slang-detail-grid__full {
  grid-column: 1 / -1;
}

@media (max-width: 640px) {
  .slang-detail-grid {
    grid-template-columns: 1fr;
  }
}
</style>
