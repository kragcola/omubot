<script setup lang="ts">
import { LogOutOutline } from '@vicons/ionicons5'
import MenuCollapse from '../../components/MenuCollapse.vue'
import Fullscreen from '../../components/Fullscreen.vue'
import ToggleTheme from '../../../components/common/ToggleTheme.vue'
import AppCard from '../../../components/common/AppCard.vue'
import { useAuthStore } from '../../../stores/auth'

const auth = useAuthStore()
const router = useRouter()

function handleLogout() {
  auth.logout()
  router.push('/')
}
</script>

<template>
  <AppCard class="flex items-center px-12" border-b="1px solid light_border dark:dark_border">
    <MenuCollapse />
    <div class="w-0 flex-1" />
    <div class="flex flex-shrink-0 items-center px-12 text-18">
      <ToggleTheme />
      <Fullscreen />
      <button
        type="button"
        class="om-iconbtn"
        aria-label="退出登录"
        @click="handleLogout"
      >
        <NIcon :size="20">
          <LogOutOutline />
        </NIcon>
      </button>
    </div>
  </AppCard>
</template>
