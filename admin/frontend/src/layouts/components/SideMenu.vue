<script setup lang="ts">
import type { MenuOption } from 'naive-ui'
import {
  SpeedometerOutline,
  DocumentTextOutline,
  LayersOutline,
  HappyOutline,
  LibraryOutline,
  PeopleOutline,
  SettingsOutline,
  ServerOutline,
  NewspaperOutline,
  PricetagsOutline,
  CubeOutline,
  ChatbubbleEllipsesOutline,
  TerminalOutline,
  GlobeOutline,
  BulbOutline,
  AnalyticsOutline,
  FunnelOutline,
} from '@vicons/ionicons5'
import { useAppStore } from '../../stores/app'

const router = useRouter()
const route = useRoute()
const app = useAppStore()

function renderIcon(icon: Component) {
  return () => h('span', { class: 'flex-center' }, h(icon))
}

const menuOptions: MenuOption[] = [
  {
    type: 'group',
    label: '日常',
    key: 'daily',
    children: [
      { label: '仪表盘', key: '/', icon: renderIcon(SpeedometerOutline) },
      { label: '人设编辑', key: '/soul', icon: renderIcon(DocumentTextOutline) },
      { label: '群管理', key: '/groups', icon: renderIcon(PeopleOutline) },
      { label: '记忆', key: '/memory', icon: renderIcon(LayersOutline) },
      { label: '表情包', key: '/stickers', icon: renderIcon(HappyOutline) },
      { label: '学习管道', key: '/learning', icon: renderIcon(AnalyticsOutline) },
      { label: '群内黑话', key: '/slang', icon: renderIcon(PricetagsOutline) },
      { label: '表达方式', key: '/style', icon: renderIcon(ChatbubbleEllipsesOutline) },
      { label: '跨群可见', key: '/cross-group', icon: renderIcon(GlobeOutline) },
      { label: '经验反思', key: '/episodes', icon: renderIcon(BulbOutline) },
      { label: '记忆候选', key: '/memory-consolidator', icon: renderIcon(FunnelOutline) },
      { label: '知识库', key: '/knowledge', icon: renderIcon(LibraryOutline) },
      { label: 'BlockTrace', key: '/block-trace', icon: renderIcon(AnalyticsOutline) },
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

const activeKey = computed(() => {
  if (route.path.startsWith('/soul')) return '/soul'
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
