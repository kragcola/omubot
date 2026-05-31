<script setup lang="ts">
import { useAppStore } from '../../stores/app'
const app = useAppStore()
</script>

<template>
  <button
    type="button"
    class="om-iconbtn"
    :aria-label="app.collapsed ? '展开菜单' : '折叠菜单'"
    :aria-expanded="!app.collapsed"
    @click="app.toggleSidebar()"
  >
    <NIcon :size="20">
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <path fill="currentColor" d="M3 18h18v-2H3v2zm0-5h18v-2H3v2zm0-7v2h18V6H3z" />
      </svg>
    </NIcon>
  </button>
</template>
