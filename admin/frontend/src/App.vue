<script setup lang="ts">
import { NConfigProvider, NMessageProvider, NDialogProvider, darkTheme, zhCN, dateZhCN } from 'naive-ui'
import { useAppStore } from './stores/app'
import { useAuthStore } from './stores/auth'
import LoginView from './views/login/LoginView.vue'

const appStore = useAppStore()
const auth = useAuthStore()

// Check auth status on startup
auth.checkAuth()

const NormalLayout = markRaw(defineAsyncComponent(() => import('./layouts/normal/index.vue')))
const EmptyLayout = markRaw(defineAsyncComponent(() => import('./layouts/empty/index.vue')))

const isLogin = computed(() => !auth.authenticated)
const layoutComponent = computed(() => (isLogin.value ? EmptyLayout : NormalLayout))

watchEffect(() => {
  document.documentElement.classList.toggle('dark', appStore.isDark)
})
</script>

<template>
  <NConfigProvider
    :theme="appStore.isDark ? darkTheme : undefined"
    :theme-overrides="appStore.naiveThemeOverrides"
    :locale="zhCN"
    :date-locale="dateZhCN"
  >
    <NMessageProvider>
      <NDialogProvider>
        <component :is="layoutComponent">
          <div v-if="auth.loading" class="wh-full f-c-c">
            <NSpin size="large" />
          </div>
          <LoginView v-else-if="isLogin" />
          <router-view v-else v-slot="{ Component: RoutedComponent, route: curRoute }">
            <transition name="fade-slide" mode="out-in" appear>
              <template v-if="curRoute.meta.keepAlive">
                <KeepAlive>
                  <component
                    :is="RoutedComponent"
                    :key="String(curRoute.name ?? curRoute.path)"
                  />
                </KeepAlive>
              </template>
              <component
                :is="RoutedComponent"
                v-else
                :key="curRoute.fullPath"
              />
            </transition>
          </router-view>
        </component>
      </NDialogProvider>
    </NMessageProvider>
  </NConfigProvider>
</template>

<style>
.fade-slide-leave-active,
.fade-slide-enter-active {
  transition: all 0.25s ease;
}
.fade-slide-enter-from {
  opacity: 0;
  transform: translateX(-20px);
}
.fade-slide-leave-to {
  opacity: 0;
  transform: translateX(20px);
}
</style>
