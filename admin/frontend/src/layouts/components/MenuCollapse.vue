<script setup lang="ts">
import { useAppStore } from '../../stores/app'
const app = useAppStore()
</script>

<template>
  <NIcon
    :size="20"
    :title="app.collapsed ? '展开菜单' : '折叠菜单'"
    class="cursor-pointer"
    @click="app.toggleSidebar()"
  >
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
      <path fill="currentColor" d="M3 18h18v-2H3v2zm0-5h18v-2H3v2zm0-7v2h18V6H3z" />
    </svg>
  </NIcon>
</template>
