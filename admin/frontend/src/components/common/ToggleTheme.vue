<script setup lang="ts">
import { SunnyOutline, MoonOutline } from '@vicons/ionicons5'
import { useAppStore } from '../../stores/app'
const app = useAppStore()
</script>

<template>
  <NIcon
    :size="20"
    :component="app.isDark ? SunnyOutline : MoonOutline"
    class="mr-16 cursor-pointer opacity-70 hover:opacity-100 transition-opacity"
    @click="app.toggleTheme()"
  />
</template>
