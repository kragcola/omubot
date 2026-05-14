import { defineStore } from 'pinia'
import type { GlobalThemeOverrides } from 'naive-ui'

const PRIMARY_COLOR = '#316C72'

function buildThemeOverrides(primary: string, isDark: boolean): GlobalThemeOverrides {
  const palette = isDark
    ? {
        textColorBase: '#E7F0F2',
        textColor1: '#E7F0F2',
        textColor2: '#9CB0B8',
        textColor3: '#768B92',
        bodyColor: '#10171A',
        cardColor: '#1A262C',
        modalColor: '#1A262C',
        popoverColor: '#1A262C',
        tableColor: '#1A262C',
        tableHeaderColor: 'rgba(255, 255, 255, 0.04)',
        tableColorHover: 'rgba(99, 178, 186, 0.08)',
        tableColorStriped: 'rgba(99, 178, 186, 0.05)',
        hoverColor: 'rgba(255, 255, 255, 0.05)',
        pressedColor: 'rgba(255, 255, 255, 0.04)',
        actionColor: 'rgba(255, 255, 255, 0.04)',
        borderColor: 'rgba(123, 149, 157, 0.22)',
        dividerColor: 'rgba(123, 149, 157, 0.22)',
        inputColor: 'rgba(255, 255, 255, 0.05)',
        codeColor: '#22323A',
        tabColor: 'rgba(255, 255, 255, 0.04)',
        scrollbarColor: 'rgba(255, 255, 255, 0.18)',
        scrollbarColorHover: 'rgba(255, 255, 255, 0.28)',
        boxShadow1: '0 8px 24px rgba(0, 0, 0, 0.18)',
        boxShadow2: '0 16px 40px rgba(0, 0, 0, 0.28)',
        boxShadow3: '0 24px 70px rgba(0, 0, 0, 0.36)',
      }
    : {
        textColorBase: '#1F2A30',
        textColor1: '#1F2A30',
        textColor2: '#607078',
        textColor3: '#8A979D',
        bodyColor: '#EEF2F4',
        cardColor: '#FFFFFF',
        modalColor: '#FFFFFF',
        popoverColor: '#FFFFFF',
        tableColor: '#FFFFFF',
        tableHeaderColor: '#F4F8F9',
        tableColorHover: 'rgba(49, 108, 114, 0.05)',
        tableColorStriped: 'rgba(49, 108, 114, 0.03)',
        hoverColor: 'rgba(49, 108, 114, 0.06)',
        pressedColor: 'rgba(49, 108, 114, 0.08)',
        actionColor: 'rgba(49, 108, 114, 0.04)',
        borderColor: 'rgba(111, 137, 146, 0.22)',
        dividerColor: 'rgba(111, 137, 146, 0.22)',
        inputColor: '#F8FBFB',
        codeColor: '#F2F7F8',
        tabColor: '#F7FAFB',
        scrollbarColor: 'rgba(96, 112, 120, 0.18)',
        scrollbarColorHover: 'rgba(96, 112, 120, 0.3)',
        boxShadow1: '0 8px 24px rgba(23, 42, 48, 0.06)',
        boxShadow2: '0 16px 40px rgba(23, 42, 48, 0.12)',
        boxShadow3: '0 24px 70px rgba(23, 42, 48, 0.18)',
      }

  return {
    common: {
      primaryColor: primary,
      primaryColorHover: '#3C7B82',
      primaryColorPressed: '#274E53',
      primaryColorSuppl: '#3C7B82',
      infoColor: '#4D7892',
      infoColorHover: '#5B879F',
      infoColorPressed: '#3C6278',
      infoColorSuppl: '#5B879F',
      successColor: '#2E8F6B',
      successColorHover: '#3A9B76',
      successColorPressed: '#26775A',
      successColorSuppl: '#3A9B76',
      warningColor: '#C58A2B',
      warningColorHover: '#D09835',
      warningColorPressed: '#A87322',
      warningColorSuppl: '#D09835',
      errorColor: '#B84C5C',
      errorColorHover: '#C65A69',
      errorColorPressed: '#9E3E4E',
      errorColorSuppl: '#C65A69',
      borderRadius: '16px',
      borderRadiusSmall: '12px',
      fontWeightStrong: '600',
      placeholderColor: isDark ? '#768B92' : '#8A979D',
      placeholderColorDisabled: isDark ? '#5A6E76' : '#A9B5BA',
      iconColor: isDark ? '#9CB0B8' : '#607078',
      closeIconColor: isDark ? '#9CB0B8' : '#607078',
      ...palette,
    },
    Tag: {
      colorBordered: isDark ? 'rgba(255, 255, 255, 0.04)' : '#F4F8F9',
      textColor: isDark ? '#E7F0F2' : '#1F2A30',
      borderColor: isDark ? 'rgba(123, 149, 157, 0.22)' : 'rgba(111, 137, 146, 0.22)',
    },
    DataTable: {
      thColor: isDark ? 'rgba(255, 255, 255, 0.04)' : '#F4F8F9',
      thColorHover: isDark ? 'rgba(255, 255, 255, 0.06)' : '#EAF1F2',
      thTextColor: isDark ? '#E7F0F2' : '#1F2A30',
      tdColor: 'transparent',
      tdColorHover: isDark ? 'rgba(99, 178, 186, 0.06)' : 'rgba(49, 108, 114, 0.05)',
      tdColorStriped: isDark ? 'rgba(99, 178, 186, 0.04)' : 'rgba(49, 108, 114, 0.03)',
      tdTextColor: isDark ? '#E7F0F2' : '#1F2A30',
      borderColor: isDark ? 'rgba(123, 149, 157, 0.22)' : 'rgba(111, 137, 146, 0.22)',
    },
  }
}

function loadDark(): boolean {
  const v = localStorage.getItem('app:isDark')
  return v !== null ? v === 'true' : false
}

function loadCollapsed(): boolean {
  return localStorage.getItem('app:collapsed') === 'true'
}

export const useAppStore = defineStore('app', () => {
  const isDark = ref(loadDark())
  const collapsed = ref(loadCollapsed())
  const primaryColor = ref(PRIMARY_COLOR)
  const layout = ref('normal')

  const naiveThemeOverrides = computed(() =>
    buildThemeOverrides(primaryColor.value, isDark.value),
  )

  const sidebarCollapsed = computed(() => collapsed.value)
  const toggleSidebar = () => {
    collapsed.value = !collapsed.value
    localStorage.setItem('app:collapsed', String(collapsed.value))
  }

  function toggleTheme() {
    isDark.value = !isDark.value
    localStorage.setItem('app:isDark', String(isDark.value))
  }

  return {
    isDark,
    collapsed,
    primaryColor,
    layout,
    naiveThemeOverrides,
    sidebarCollapsed,
    toggleSidebar,
    toggleTheme,
  }
})
