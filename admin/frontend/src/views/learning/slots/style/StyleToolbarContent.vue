<script setup lang="ts">
import { FlashOutline, SparklesOutline } from '@vicons/ionicons5'
import { useStyleConsoleInject } from './state'

const console_ = useStyleConsoleInject()
const {
  actionLoading,
  scopeFilter,
  sortMode,
  runExtract,
  generateProfile,
} = console_

const scopeOptions = [
  { label: '全部作用域', value: '' },
  { label: '本群表达', value: 'group' },
  { label: '全局表达', value: 'global' },
]

const sortOptions = [
  { label: '默认排序', value: 'default' },
  { label: '按时间', value: 'time' },
]
</script>

<template>
  <NSpace align="center" :size="6">
    <NSelect
      v-model:value="scopeFilter"
      class="style-fold-toolbar__select"
      size="small"
      :options="scopeOptions"
    />
    <NSelect
      v-model:value="sortMode"
      class="style-fold-toolbar__select"
      size="small"
      :options="sortOptions"
    />
    <NButton secondary size="small" :loading="actionLoading" @click="runExtract">
      <template #icon>
        <NIcon :component="SparklesOutline" />
      </template>
      抽取
    </NButton>
    <NButton type="primary" size="small" :loading="actionLoading" @click="generateProfile">
      <template #icon>
        <NIcon :component="FlashOutline" />
      </template>
      生成档案
    </NButton>
  </NSpace>
</template>

<style scoped>
.style-fold-toolbar__select {
  width: 130px;
}
</style>
