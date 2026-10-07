import { createRouter, createWebHashHistory, type RouteRecordRaw } from 'vue-router'

import AppShell from '@/components/AppShell.vue'
import ChatView from '@/features/chat/ChatView.vue'
import CharactersView from '@/features/characters/CharactersView.vue'
import DiagnosticsView from '@/features/diagnostics/DiagnosticsView.vue'
import DreamView from '@/features/dream/DreamView.vue'
import JournalView from '@/features/journal/JournalView.vue'
import KnowledgeView from '@/features/knowledge/KnowledgeView.vue'
import GraphView from '@/features/graph/GraphView.vue'
import MemoryView from '@/features/memory/MemoryView.vue'
import NapcatView from '@/features/napcat/NapcatView.vue'
import PersonaView from '@/features/persona/PersonaView.vue'
import PolicyView from '@/features/policy/PolicyView.vue'
import RuntimeView from '@/features/runtime/RuntimeView.vue'
import SettingsView from '@/features/settings/SettingsView.vue'
import StatusView from '@/features/status/StatusView.vue'
import StickersView from '@/features/stickers/StickersView.vue'

export interface RouteMeta {
  label: string
  description: string
  requiresAdmin?: boolean
}

export const routes: RouteRecordRaw[] = [
  {
    path: '/',
    component: AppShell,
    children: [
      { path: '', name: 'status', component: StatusView, meta: { label: '运行概览', description: '查看实例状态、队列和最近执行。' } satisfies RouteMeta },
      { path: 'diagnostics', name: 'diagnostics', component: DiagnosticsView, meta: { label: '用量诊断', description: '查看模型调用覆盖、模型token用量和最近请求追踪。' } satisfies RouteMeta },
      { path: 'runtime', name: 'runtime', component: RuntimeView, meta: { label: '运行管理', description: '查看核心运行标识与配置版本，并在空闲时重启核心。', requiresAdmin: true } satisfies RouteMeta },
      { path: 'settings', name: 'settings', component: SettingsView, meta: { label: '模型配置', description: '管理命名模型、绑定和运行参数。', requiresAdmin: true } satisfies RouteMeta },
      { path: 'persona', name: 'persona', component: PersonaView, meta: { label: '人格设定', description: '定义普通回复和工具续写使用的身份与风格。', requiresAdmin: true } satisfies RouteMeta },
      { path: 'policy', name: 'policy', component: PolicyView, meta: { label: '权限策略', description: '为明确的群和用户维护运行权限。', requiresAdmin: true } satisfies RouteMeta },
      { path: 'memory', name: 'memory', component: MemoryView, meta: { label: '记忆审核', description: '按准确群 ID 查看结构化候选，并分步审核与应用。', requiresAdmin: true } satisfies RouteMeta },
      { path: 'knowledge', name: 'knowledge', component: KnowledgeView, meta: { label: '文档知识库', description: '在当前群导入非个人 Markdown 文档，审核并选择可检索版本。', requiresAdmin: true } satisfies RouteMeta },
      { path: 'graph', name: 'graph', component: GraphView, meta: { label: '概念关系与别名', description: '按准确群 ID 手工维护非个人概念的有证据关系与别名。', requiresAdmin: true } satisfies RouteMeta },
      { path: 'stickers', name: 'stickers', component: StickersView, meta: { label: '表情资源', description: '按群导入、审核和撤销表情图片。', requiresAdmin: true } satisfies RouteMeta },
      { path: 'characters', name: 'characters', component: CharactersView, meta: { label: '角色资源', description: '维护当前 bot 的公开角色元数据，核对保存与运行版本及备份回执。', requiresAdmin: true } satisfies RouteMeta },
      { path: 'dream', name: 'dream', component: DreamView, meta: { label: 'Dream 故事审核', description: '管理群故事 Arc，并人工审核模型提出的虚构事件。', requiresAdmin: true } satisfies RouteMeta },
      { path: 'journal', name: 'journal', component: JournalView, meta: { label: 'Journal 草稿', description: '按群修订和审核虚构故事草稿，执行本地预演。', requiresAdmin: true } satisfies RouteMeta },
      { path: 'chat', name: 'chat', component: ChatView, meta: { label: '离线试聊', description: '用当前离线运行快照验证一条对话。', requiresAdmin: true } satisfies RouteMeta },
      { path: 'napcat', name: 'napcat', component: NapcatView, meta: { label: 'QQ 接入', description: '连接 NapCat WebUI，确认 QQ 登录并采样日志。', requiresAdmin: true } satisfies RouteMeta },
    ],
  },
]

export const router = createRouter({
  history: createWebHashHistory('/'),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

export const navigation = routes[0]?.children?.map((route) => ({
  key: route.path === '' ? '/' : `/${route.path}`,
  label: String((route.meta as RouteMeta | undefined)?.label ?? route.path),
  description: String((route.meta as RouteMeta | undefined)?.description ?? ''),
})) ?? []
