<script setup lang="ts">
import {
  PricetagsOutline,
  SettingsOutline,
  SparklesOutline,
} from '@vicons/ionicons5'
import { useSlangConsoleInject } from './injection'

const console_ = useSlangConsoleInject()
const {
  extracting,
  runningAiReview,
  settingsDrawerVisible,
  runExtract,
  runForceAiReview,
  openCreateDrawer,
} = console_
</script>

<template>
  <NSpace align="center" :size="6">
    <NButton secondary size="small" :loading="extracting" @click="runExtract">
      <template #icon>
        <NIcon :component="SparklesOutline" />
      </template>
      抽取
    </NButton>
    <NPopconfirm
      :positive-text="'确认执行'"
      :negative-text="'取消'"
      @positive-click="runForceAiReview"
    >
      <template #trigger>
        <NButton secondary size="small" :loading="runningAiReview">
          <template #icon>
            <NIcon :component="SparklesOutline" />
          </template>
          AI 清池
        </NButton>
      </template>
      将立即对所有启用的群跑一次 AI 审核（force=true，跳过当日去重），可能消耗较多 LLM 配额。是否继续？
    </NPopconfirm>
    <NButton secondary size="small" @click="openCreateDrawer">
      <template #icon>
        <NIcon :component="PricetagsOutline" />
      </template>
      新建
    </NButton>
  </NSpace>

  <Teleport to="#learning-action-extra" defer>
    <NButton secondary size="small" @click="settingsDrawerVisible = true">
      <template #icon>
        <NIcon :component="SettingsOutline" />
      </template>
      黑话设置
    </NButton>
  </Teleport>
</template>
