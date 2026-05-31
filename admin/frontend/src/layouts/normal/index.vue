<script setup lang="ts">
import { useAppStore } from '../../stores/app'
import AppHeader from './header/index.vue'
import SideBar from './sidebar/index.vue'

const app = useAppStore()
</script>

<template>
  <div class="wh-full flex">
    <a href="#main-content" class="om-skip-link">跳到主内容</a>
    <nav
      aria-label="主导航"
      class="flex-col flex-shrink-0 transition-width-300"
      :class="app.collapsed ? 'w-64' : 'w-220'"
      border-r="1px solid light_border dark:dark_border"
    >
      <SideBar />
    </nav>

    <article class="w-0 flex-col flex-1">
      <AppHeader class="h-60 flex-shrink-0" />
      <div id="main-content" tabindex="-1" class="om-main flex-col flex-1 h-0">
        <slot />
      </div>
    </article>
  </div>
</template>

<style scoped>
.om-main:focus {
  outline: none;
}
</style>
