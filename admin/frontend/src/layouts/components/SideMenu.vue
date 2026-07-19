<script setup lang="ts">
import type { MenuOption } from 'naive-ui'
import {
  SpeedometerOutline,
  LayersOutline,
  HappyOutline,
  LibraryOutline,
  PeopleOutline,
  SettingsOutline,
  ServerOutline,
  NewspaperOutline,
  CubeOutline,
  TerminalOutline,
  AnalyticsOutline,
  SparklesOutline,
  GiftOutline,
  ScanOutline,
  JournalOutline,
  BookOutline,
} from '@vicons/ionicons5'
import { api } from '../../api/client'
import { useAppStore } from '../../stores/app'
import {
  PLUGIN_MENU_VISIBILITY_CHANGED,
  enabledPluginNamesFromPayload,
  isPluginMenuRouteVisible,
} from '../pluginMenuVisibility'

const router = useRouter()
const route = useRoute()
const app = useAppStore()

function renderIcon(icon: Component) {
  return () => h('span', { class: 'flex-center' }, h(icon))
}

const baseMenuOptions: MenuOption[] = [
  {
    type: 'group',
    label: '日常',
    key: 'daily',
    children: [
      { label: '仪表盘', key: '/', icon: renderIcon(SpeedometerOutline) },
      { label: '人设管理', key: '/persona-importer', icon: renderIcon(SparklesOutline) },
      { label: '群管理', key: '/groups', icon: renderIcon(PeopleOutline) },
      { label: '群聊记忆', key: '/memory', icon: renderIcon(LayersOutline) },
      { label: '表情包', key: '/stickers', icon: renderIcon(HappyOutline) },
      { label: '角色识别', key: '/characters', icon: renderIcon(ScanOutline) },
      { label: '生日祝福', key: '/birthday', icon: renderIcon(GiftOutline) },
      { label: '空间日志', key: '/qzone-journal', icon: renderIcon(JournalOutline) },
    ],
  },
  {
    type: 'group',
    label: '学习与记忆',
    key: 'learning',
    children: [
      { label: '学习管道', key: '/learning', icon: renderIcon(AnalyticsOutline) },
      { label: '知识库', key: '/knowledge', icon: renderIcon(LibraryOutline) },
      { label: 'BlockTrace', key: '/block-trace', icon: renderIcon(AnalyticsOutline) },
      { label: '世界书', key: '/worldbook', icon: renderIcon(BookOutline) },
      { label: '反事实重放', key: '/replay/weekly', icon: renderIcon(AnalyticsOutline) },
    ],
  },
  {
    type: 'group',
    label: '设置与维护',
    key: 'ops',
    children: [
      { label: '配置', key: '/config', icon: renderIcon(SettingsOutline) },
      { label: '插件', key: '/plugins', icon: renderIcon(CubeOutline) },
      { label: '沙盒', key: '/sandbox', icon: renderIcon(TerminalOutline) },
      { label: '系统', key: '/system', icon: renderIcon(ServerOutline) },
      { label: '日志', key: '/logs', icon: renderIcon(NewspaperOutline) },
    ],
  },
]

const enabledPlugins = ref<ReadonlySet<string>>(new Set())
let pluginVisibilityRequestId = 0

const menuOptions = computed<MenuOption[]>(() => baseMenuOptions.map(option => ({
  ...option,
  children: option.children?.filter(child =>
    isPluginMenuRouteVisible(child.key, enabledPlugins.value),
  ),
})))

async function refreshPluginMenuVisibility() {
  const requestId = ++pluginVisibilityRequestId
  try {
    const payload = await api<unknown>('/api/admin/plugins?include_system=true')
    if (requestId !== pluginVisibilityRequestId) return
    enabledPlugins.value = enabledPluginNamesFromPayload(payload)
  }
  catch {
    if (requestId !== pluginVisibilityRequestId) return
    enabledPlugins.value = new Set()
  }
}

function onPluginMenuVisibilityChanged() {
  void refreshPluginMenuVisibility()
}

onMounted(() => {
  window.addEventListener(PLUGIN_MENU_VISIBILITY_CHANGED, onPluginMenuVisibilityChanged)
})

onBeforeUnmount(() => {
  window.removeEventListener(PLUGIN_MENU_VISIBILITY_CHANGED, onPluginMenuVisibilityChanged)
})

watch(
  () => route.fullPath,
  () => { void refreshPluginMenuVisibility() },
  { immediate: true },
)

const activeKey = computed(() => {
  if (route.path.startsWith('/soul')) return '/persona-importer'
  if (route.path.startsWith('/plugins')) return '/plugins'
  if (route.path === '/affection') return '/memory'
  if (route.path === '/sandbox') return '/sandbox'
  if (['/usage', '/schedule', '/scheduler'].includes(route.path)) return '/system'
  return route.path
})

function handleMenuSelect(key: string) {
  if (!key.startsWith('/')) return

  const target = router.resolve({ path: key })
  if (target.fullPath === route.fullPath) return

  void router.push({ path: key }).catch(() => {})
}
</script>

<template>
  <NMenu
    class="side-menu"
    accordion
    :indent="18"
    :collapsed-icon-size="22"
    :collapsed-width="64"
    :collapsed="app.collapsed"
    :options="menuOptions"
    :value="activeKey"
    @update:value="handleMenuSelect"
  />
</template>

<style>
.side-menu:not(.n-menu--collapsed) {
  .n-menu-item-content::before {
    left: 8px;
    right: 8px;
  }
  .n-menu-item-content.n-menu-item-content--selected::before {
    border-left: 4px solid rgb(var(--primary-color));
  }
}
</style>
