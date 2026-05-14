import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const router = createRouter({
  history: createWebHistory('/admin/'),
  routes: [
    {
      path: '/',
      name: 'dashboard',
      meta: { title: '仪表盘', keepAlive: true },
      component: () => import('../views/dashboard/DashboardView.vue'),
    },
    {
      path: '/usage',
      name: 'usage',
      meta: { title: '用量统计', keepAlive: true },
      component: () => import('../views/usage/UsageView.vue'),
    },
    {
      path: '/sandbox',
      name: 'sandbox',
      meta: { title: '沙盒' },
      component: () => import('../views/sandbox/SandboxView.vue'),
    },
    {
      path: '/soul',
      name: 'soul',
      meta: { title: '人设编辑', keepAlive: true },
      component: () => import('../views/soul/SoulView.vue'),
    },
    {
      path: '/soul/persona-guide',
      name: 'soul-persona-guide',
      meta: { title: 'AI 人设规则' },
      component: () => import('../views/soul/SoulPersonaGuideView.vue'),
    },
    {
      path: '/schedule',
      name: 'schedule',
      meta: { title: '日程心情', keepAlive: true },
      component: () => import('../views/schedule/ScheduleView.vue'),
    },
    {
      path: '/memory',
      name: 'memory',
      meta: { title: '记忆管理', keepAlive: true },
      component: () => import('../views/memory/MemoryConsoleView.vue'),
    },
    {
      path: '/affection',
      name: 'affection',
      redirect: () => ({
        path: '/memory',
        query: { view: 'browse' },
      }),
    },
    {
      path: '/stickers',
      name: 'stickers',
      meta: { title: '表情包', keepAlive: true },
      component: () => import('../views/stickers/StickersView.vue'),
    },
    {
      path: '/knowledge',
      name: 'knowledge',
      meta: { title: '知识库' },
      component: () => import('../views/knowledge/KnowledgeView.vue'),
    },
    {
      path: '/slang',
      name: 'slang',
      meta: { title: '群内黑话', keepAlive: true },
      component: () => import('../views/slang/SlangView.vue'),
    },
    {
      path: '/style',
      name: 'style',
      meta: { title: '表达学习', keepAlive: true },
      component: () => import('../views/style/StyleView.vue'),
    },
    {
      path: '/memos',
      name: 'memos',
      redirect: () => ({
        path: '/memory',
        query: { view: 'browse' },
      }),
    },
    {
      path: '/groups',
      name: 'groups',
      meta: { title: '群管理', keepAlive: true },
      component: () => import('../views/groups/GroupsView.vue'),
    },
    {
      path: '/plugins',
      name: 'plugins',
      meta: { title: '插件', keepAlive: true },
      component: () => import('../views/plugins/PluginsView.vue'),
    },
    {
      path: '/plugins/:name',
      name: 'plugin-detail',
      meta: { title: '插件详情', keepAlive: false },
      component: () => import('../views/plugins/PluginsView.vue'),
    },
    {
      path: '/scheduler',
      name: 'scheduler',
      meta: { title: '调度器', keepAlive: true },
      component: () => import('../views/scheduler/SchedulerView.vue'),
    },
    {
      path: '/config',
      name: 'config',
      meta: { title: '配置', keepAlive: true },
      component: () => import('../views/config/ConfigView.vue'),
    },
    {
      path: '/system',
      name: 'system',
      meta: { title: '系统', keepAlive: true },
      component: () => import('../views/system/SystemView.vue'),
    },
    {
      path: '/logs',
      name: 'logs',
      meta: { title: '日志', keepAlive: true },
      component: () => import('../views/logs/LogsView.vue'),
    },
    {
      path: '/design-playground',
      name: 'design-playground',
      meta: { title: '设计系统验收', keepAlive: false },
      component: () => import('../views/playground/DesignPlaygroundView.vue'),
    },
  ],
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  if (!auth.loading && !auth.authenticated && to.name !== 'dashboard') {
    // Auth check complete, not authenticated — redirect to dashboard (which shows login)
    return { name: 'dashboard' }
  }
})

export default router
