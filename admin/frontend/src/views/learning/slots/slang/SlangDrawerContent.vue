<script setup lang="ts">
import SlangCreateDrawer from '../../../slang/components/SlangCreateDrawer.vue'
import SlangDetailDrawer from '../../../slang/components/SlangDetailDrawer.vue'
import SlangSettingsDrawer from '../../../slang/components/SlangSettingsDrawer.vue'
import { useSlangConsoleInject } from './injection'

const console_ = useSlangConsoleInject()
const {
  createDrawerVisible,
  createDraft,
  creatingTerm,
  saveCreateTerm,
  drawerVisible,
  detailLoading,
  detailTerm,
  observations,
  revisions,
  editAliases,
  mergeTargetId,
  mergeSearchText,
  mergeOptions,
  mergeLoading,
  saveDetail,
  recomputeConfidence,
  mergeCurrentIntoTarget,
  reviewAiTerm,
  loadMergeCandidates,
  settingsDrawerVisible,
  settings,
  allowlistText,
  stoplistText,
  savingSettings,
  saveSettings,
} = console_
</script>

<template>
  <SlangCreateDrawer
    v-model:visible="createDrawerVisible"
    v-model:draft="createDraft"
    :creating-term="creatingTerm"
    @save="saveCreateTerm"
  />

  <SlangDetailDrawer
    v-model:visible="drawerVisible"
    v-model:detail-term="detailTerm"
    v-model:edit-aliases="editAliases"
    v-model:merge-target-id="mergeTargetId"
    v-model:merge-search-text="mergeSearchText"
    :detail-loading="detailLoading"
    :observations="observations"
    :revisions="revisions"
    :merge-options="mergeOptions"
    :merge-loading="mergeLoading"
    @save="saveDetail"
    @recompute-confidence="recomputeConfidence"
    @merge="mergeCurrentIntoTarget"
    @review-ai="reviewAiTerm"
    @search-merge="loadMergeCandidates"
  />

  <SlangSettingsDrawer
    v-model:visible="settingsDrawerVisible"
    v-model:settings="settings"
    v-model:allowlist-text="allowlistText"
    v-model:stoplist-text="stoplistText"
    :saving-settings="savingSettings"
    @save="saveSettings"
  />
</template>
