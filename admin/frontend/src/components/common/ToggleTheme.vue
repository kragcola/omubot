<script setup lang="ts">
import { SunnyOutline, MoonOutline } from '@vicons/ionicons5'
import { useAppStore } from '../../stores/app'
const app = useAppStore()
</script>

<template>
  <button
    type="button"
    class="om-iconbtn mr-16"
    :aria-label="app.isDark ? '切换到浅色主题' : '切换到深色主题'"
    @click="app.toggleTheme()"
  >
    <NIcon :size="20" :component="app.isDark ? SunnyOutline : MoonOutline" />
  </button>
</template>
