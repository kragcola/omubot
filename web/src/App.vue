<script setup lang="ts">
import { onMounted } from 'vue'
import { NConfigProvider, NDialogProvider, NMessageProvider, NNotificationProvider, type GlobalThemeOverrides } from 'naive-ui'

import { bootstrapSession } from '@/app/session'

const themeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#377d7b',
    primaryColorHover: '#2f706f',
    primaryColorPressed: '#245b5a',
    borderRadius: '12px',
    borderRadiusSmall: '8px',
    bodyColor: '#edf4f3',
    cardColor: '#ffffff',
    textColorBase: '#21363a',
    textColor2: '#526a6d',
    textColor3: '#789093',
    borderColor: '#d5e3e1',
  },
  Card: {
    borderRadius: '12px',
  },
  Button: {
    borderRadiusMedium: '8px',
  },
}

onMounted(() => {
  void bootstrapSession()
})
</script>

<template>
  <n-config-provider :theme-overrides="themeOverrides">
    <n-message-provider>
      <n-dialog-provider>
        <n-notification-provider>
          <router-view />
        </n-notification-provider>
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>
