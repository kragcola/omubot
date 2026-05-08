<script setup lang="ts">
import { useAppStore } from '../../stores/app'
import TheLogo from '../../components/common/TheLogo.vue'

const app = useAppStore()
</script>

<template>
  <router-link class="h-60 f-c-c flex-shrink-0" to="/">
    <TheLogo />
    <h2
      v-show="!app.collapsed"
      class="ml-10 max-w-140 flex-shrink-0 text-16 font-bold overflow-hidden whitespace-nowrap"
      style="color: var(--n-color-target)"
    >
      Omubot
    </h2>
  </router-link>
</template>
