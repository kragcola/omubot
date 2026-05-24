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
      path: '/persona-importer',
      name: 'persona-importer',
      meta: { title: '人设导入', keepAlive: true },
      component: () => import('../views/persona/PersonaImporterView.vue'),
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
      redirect: to => ({
        path: '/learning',
        query: { ...to.query, noun: 'slang' },
      }),
    },
    {
      path: '/learning',
      name: 'learning',
      meta: { title: '学习管道总览', keepAlive: true },
      component: () => import('../views/learning/LearningView.vue'),
    },
    {
      path: '/style',
      name: 'style',
      redirect: to => ({
        path: '/learning',
        query: { ...to.query, noun: 'style' },
      }),
    },
    {
      path: '/cross-group',
      name: 'cross-group',
      redirect: to => ({
        path: '/learning',
        query: { ...to.query, noun: 'slang', scope: 'cross' },
      }),
    },
    {
      path: '/episodes',
      name: 'episodes',
      redirect: to => ({
        path: '/learning',
        query: { ...to.query, noun: 'episode' },
      }),
    },
    {
      path: '/memory-consolidator',
      name: 'memory-consolidator',
      redirect: to => ({
        path: '/learning',
        query: { ...to.query, noun: 'memory' },
      }),
    },
    {
      path: '/block-trace',
      name: 'block-trace',
      meta: { title: 'BlockTrace', keepAlive: true },
      component: () => import('../views/block-trace/BlockTraceView.vue'),
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
