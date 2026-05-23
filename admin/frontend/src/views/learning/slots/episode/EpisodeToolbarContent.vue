<script setup lang="ts">
import { RefreshOutline } from '@vicons/ionicons5'
import { useEpisodeConsoleInject } from './state'

const console_ = useEpisodeConsoleInject()
const { loading, refresh } = console_
</script>

<template>
  <NSpace align="center" :size="6">
    <NButton secondary size="small" :loading="loading" @click="refresh">
      <template #icon>
        <NIcon :component="RefreshOutline" />
      </template>
      刷新
    </NButton>
  </NSpace>
</template>
