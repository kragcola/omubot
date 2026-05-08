<script setup lang="ts">
import {
  DocumentTextOutline,
  LayersOutline,
  PencilOutline,
  RefreshOutline,
  SaveOutline,
  SparklesOutline,
} from '@vicons/ionicons5'
import {
  NAlert,
  NButton,
  NEmpty,
  NIcon,
  NInput,
  NSkeleton,
  NSwitch,
  NTag,
  useMessage,
} from 'naive-ui'

import { api } from '../../api/client'
import AppCard from '../../components/common/AppCard.vue'
import AppEditorShell from '../../components/common/AppEditorShell.vue'
import AppPage from '../../components/common/AppPage.vue'
import AppPanelSection from '../../components/common/AppPanelSection.vue'
import EmptyState from '../../components/common/EmptyState.vue'
import MetricCard from '../../components/common/MetricCard.vue'
import RestartBotButton from '../../components/common/RestartBotButton.vue'

type SoulBlockType = 'paragraph' | 'bullet_list' | 'numbered_list' | 'kv_table' | 'free_text'
type SoulNodeKind = 'meta' | 'proactive' | 'persona' | 'instruction'

interface SoulTableRow {
  key: string
  value: string
}

interface SoulBlock {
  id: string
  type: SoulBlockType
  heading: string
  text?: string
  items?: string[]
  columns?: string[]
  rows?: SoulTableRow[]
}

interface SoulSection {
  id: string
  title: string
  blocks: SoulBlock[]
}

interface SoulEditorPayload {
  meta: {
    name: string
    description: string
    display_title: string
  }
  persona_sections: SoulSection[]
  instruction_sections: SoulSection[]
  proactive: {
    enabled: boolean
    text: string
  }
}

interface SoulEditorResponse {
  format_mode: 'legacy'
  migration_pending: boolean
  editor: SoulEditorPayload
}

interface LegacySoulPageResponse {
  identity?: string
  instruction?: string
  files?: unknown
}

interface SoulNodeSection {
  group: 'persona' | 'instruction'
  section: SoulSection
  blocks: SoulBlock[]
  sliceId: string
  label: string
  partial: boolean
  weight: number
}

interface SoulEditorNode {
  id: string
  kind: SoulNodeKind
  eyebrow: string
  title: string
  description: string
  sections: SoulNodeSection[]
  sectionCount: number
  blockCount: number
  weight: number
  includeMeta?: boolean
  includeProactive?: boolean
}

interface SoulBlockGroup {
  id: string
  heading: string
  blocks: SoulBlock[]
}

interface SoulNodeBucket {
  key: string
  title: string
  keywords: string[]
}

const loading = ref(true)
const refreshing = ref(false)
const saving = ref(false)
const originalEditor = ref<SoulEditorPayload | null>(null)
const editor = ref<SoulEditorPayload | null>(null)
const activeNodeId = ref('meta')
const syncWarning = ref('')
const loadError = ref('')

const message = useMessage()
const router = useRouter()

const modified = computed(() =>
  JSON.stringify(editor.value || null) !== JSON.stringify(originalEditor.value || null),
)

const personaCount = computed(() => loadError.value ? '—' : (editor.value?.persona_sections.length || 0))
const instructionCount = computed(() => loadError.value ? '—' : (editor.value?.instruction_sections.length || 0))
const formatLabel = computed(() => {
  if (loadError.value) return '接口异常'
  return '双文件结构'
})
const formatHint = computed(() =>
  loadError.value
    ? loadError.value
    : '当前页面以结构化方式编辑 identity.md 与 instruction.md，保存后同步运行时身份。',
)

const metaDirty = computed(() =>
  JSON.stringify(editor.value?.meta || {}) !== JSON.stringify(originalEditor.value?.meta || {}),
)
const proactiveDirty = computed(() =>
  JSON.stringify(editor.value?.proactive || {}) !== JSON.stringify(originalEditor.value?.proactive || {}),
)
const editorNodes = computed<SoulEditorNode[]>(() => {
  if (!editor.value) return []
  const personaNodes = buildSectionNodes('persona', editor.value.persona_sections)
  const identityNode = personaNodes.find(node => node.id.startsWith('persona-identity-'))
  const remainingPersonaNodes = personaNodes.filter(node => !node.id.startsWith('persona-identity-'))

  return [
    {
      id: 'base-identity',
      kind: 'persona',
      eyebrow: 'Profile',
      title: '基础与身份',
      description: identityNode?.description || '基础信息 / 插话方式 / 身份概览',
      sections: identityNode?.sections || [],
      sectionCount: identityNode?.sectionCount || 0,
      blockCount: identityNode?.blockCount || 0,
      weight: 14 + (identityNode?.weight || 0),
      includeMeta: true,
      includeProactive: true,
    },
    ...remainingPersonaNodes,
    ...buildSectionNodes('instruction', editor.value.instruction_sections),
  ]
})
const nodeCount = computed(() => loadError.value ? '—' : editorNodes.value.length)
const currentNode = computed(() =>
  editorNodes.value.find(node => node.id === activeNodeId.value) || editorNodes.value[0] || null,
)

onMounted(() => {
  void loadSoul()
})

watch(editorNodes, () => {
  ensureActiveNode()
})

function cloneEditor<T>(value: T): T {
  return JSON.parse(JSON.stringify(value))
}

function normalizeBlock(block: SoulBlock, fallbackId: string): SoulBlock {
  return {
    id: block.id || fallbackId,
    type: block.type,
    heading: block.heading || '',
    text: block.text || '',
    items: block.items ? [...block.items] : [],
    columns: block.columns ? [...block.columns] : ['项', '内容'],
    rows: block.rows ? block.rows.map(row => ({ ...row })) : [],
  }
}

function normalizeSection(section: SoulSection, index: number): SoulSection {
  return {
    id: section.id || `section-${index}`,
    title: section.title || `章节 ${index + 1}`,
    blocks: (section.blocks || []).map((block, blockIndex) =>
      normalizeBlock(block, `${section.id || `section-${index}`}-block-${blockIndex}`),
    ),
  }
}

function normalizeEditor(payload: SoulEditorPayload): SoulEditorPayload {
  return {
    meta: {
      name: payload.meta?.name || '',
      description: payload.meta?.description || '',
      display_title: payload.meta?.display_title || '',
    },
    persona_sections: (payload.persona_sections || []).map(normalizeSection),
    instruction_sections: (payload.instruction_sections || []).map(normalizeSection),
    proactive: {
      enabled: Boolean(payload.proactive?.enabled),
      text: payload.proactive?.text || '',
    },
  }
}

function isSoulEditorResponse(payload: unknown): payload is SoulEditorResponse {
  if (!payload || typeof payload !== 'object') return false
  const data = payload as Record<string, unknown>
  return typeof data.format_mode === 'string' && typeof data.editor === 'object' && data.editor !== null
}

function isLegacySoulPageResponse(payload: unknown): payload is LegacySoulPageResponse {
  if (!payload || typeof payload !== 'object') return false
  const data = payload as Record<string, unknown>
  return 'identity' in data || 'instruction' in data || 'files' in data
}

function explainSoulLoadFailure(error: any) {
  const messageText = String(error?.message || '')
  const status = Number(error?.response?.status || 0)
  const data = error?.response?._data

  if (messageText === 'legacy-soul-api-payload') {
    return '检测到旧版 Soul 接口响应。前端已经升级为结构化编辑器，但运行中的 bot 还没有加载新版 `/api/admin/soul`，请重启 bot 后再试。'
  }
  if (messageText === 'invalid-soul-api-payload') {
    return 'Soul 接口返回了无法识别的结构。请检查 `/api/admin/soul` 是否仍是旧版，或重启 bot 让前后端版本一致。'
  }
  if (isLegacySoulPageResponse(data)) {
    return '检测到旧版 Soul 页面接口。前端已经升级为结构化编辑器，但运行中的 bot 还没有加载新版 `/api/admin/soul`，请重启 bot 后再试。'
  }
  if (status === 404) {
    return '当前运行中的 bot 还没有提供新版 `/api/admin/soul` 接口。通常是前端已更新、后端未重启，请重启 bot 后再试。'
  }
  if (status === 500) {
    return 'Soul 结构接口在服务端执行失败。请先看 bot 日志中的 `/api/admin/soul` 报错。'
  }
  if (status === 401) {
    return '登录状态已失效，请重新登录后再试。'
  }
  return '人设结构加载失败，请检查 `/api/admin/soul` 返回值或重启 bot 让前后端版本一致。'
}

function firstSectionId() {
  return 'base-identity'
}

function estimateBlockWeight(block: SoulBlock) {
  if (block.type === 'kv_table') return Math.max(4, Math.ceil((block.rows?.length || 0) * 1.4))
  if (block.type === 'bullet_list' || block.type === 'numbered_list') return Math.max(4, (block.items?.length || 0) * 2)
  return Math.max(4, Math.ceil((block.text || '').length / 160) + 2)
}

function estimateSectionWeight(section: SoulSection) {
  return 3 + section.blocks.reduce((total, block) => total + estimateBlockWeight(block), 0)
}

const PERSONA_BUCKETS: SoulNodeBucket[] = [
  { key: 'identity', title: '身份概览', keywords: ['概述', '基础身份', '一句话'] },
  { key: 'personality', title: '性格与成长', keywords: ['性格', '成长'] },
  { key: 'relationship', title: '关系与边界', keywords: ['人际', '关系', '像与不像', '不像'] },
  { key: 'voice', title: '语气表达', keywords: ['语气', '说话', '表达'] },
]

const INSTRUCTION_BUCKETS: SoulNodeBucket[] = [
  { key: 'response-rules', title: '回复规则', keywords: ['底线', '必须避免', '污染', '回复风格', '分段发送'] },
  { key: 'expression', title: '表达素材', keywords: ['表情包', '场景'] },
  { key: 'group-stability', title: '群聊与人格', keywords: ['稳固人格', '拒绝', '主动参与群聊', '群聊上下文'] },
  { key: 'tools-memory', title: '日常工具记忆', keywords: ['日常', '心情', '主动搜索', '工具', '记忆'] },
]

function uniqueSectionCount(sections: SoulNodeSection[]) {
  return new Set(sections.map(item => `${item.group}-${item.section.id}`)).size
}

function sectionBlockCount(sections: SoulNodeSection[]) {
  return sections.reduce((total, item) => total + item.blocks.length, 0)
}

function compactTitles(sections: SoulNodeSection[]) {
  const titles = Array.from(new Set(sections.map(item => item.section.title)))
  if (titles.length <= 2) return titles.join(' / ')
  return `${titles[0]} 等 ${titles.length} 章`
}

function blockHeading(block: SoulBlock, index: number) {
  return block.heading || blockLabel(block, index)
}

function makeSliceLabel(section: SoulSection, blocks: SoulBlock[], index: number, total: number) {
  if (total <= 1) return section.title
  const firstBlock = blocks[0]
  const heading = firstBlock ? blockHeading(firstBlock, section.blocks.indexOf(firstBlock)) : ''
  return heading ? `${section.title}：${heading}` : `${section.title} ${index + 1}/${total}`
}

function splitSectionForNode(group: 'persona' | 'instruction', section: SoulSection): SoulNodeSection[] {
  const maxSliceWeight = group === 'persona' ? 52 : 72
  const sectionWeight = estimateSectionWeight(section)

  if (sectionWeight <= maxSliceWeight || section.blocks.length <= 1) {
    return [{
      group,
      section,
      blocks: section.blocks,
      sliceId: `${section.id}-all`,
      label: section.title,
      partial: false,
      weight: sectionWeight,
    }]
  }

  const chunks: Array<{ blocks: SoulBlock[], weight: number }> = []
  let currentBlocks: SoulBlock[] = []
  let currentWeight = 3

  function flushChunk() {
    if (currentBlocks.length === 0) return
    chunks.push({ blocks: currentBlocks, weight: currentWeight })
    currentBlocks = []
    currentWeight = 3
  }

  section.blocks.forEach((block) => {
    const blockWeight = estimateBlockWeight(block)
    if (currentBlocks.length > 0 && currentWeight + blockWeight > maxSliceWeight) {
      flushChunk()
    }
    currentBlocks.push(block)
    currentWeight += blockWeight
  })

  flushChunk()

  return chunks.map((chunk, index) => ({
    group,
    section,
    blocks: chunk.blocks,
    sliceId: `${section.id}-part-${index + 1}`,
    label: makeSliceLabel(section, chunk.blocks, index, chunks.length),
    partial: chunks.length > 1,
    weight: chunk.weight,
  }))
}

function bucketForSection(group: 'persona' | 'instruction', title: string) {
  const buckets = group === 'persona' ? PERSONA_BUCKETS : INSTRUCTION_BUCKETS
  return buckets.find(bucket => bucket.keywords.some(keyword => title.includes(keyword))) || {
    key: `misc-${title}`,
    title,
    keywords: [title],
  }
}

function buildSectionNodes(group: 'persona' | 'instruction', sections: SoulSection[]): SoulEditorNode[] {
  const maxNodeWeight = group === 'persona' ? 66 : 132
  const nodes: SoulEditorNode[] = []
  const buckets = new Map<string, { bucket: SoulNodeBucket, slices: SoulNodeSection[] }>()

  sections.forEach((section) => {
    const bucket = bucketForSection(group, section.title)
    const current = buckets.get(bucket.key) || { bucket, slices: [] }
    current.slices.push(...splitSectionForNode(group, section))
    buckets.set(bucket.key, current)
  })

  function pushNode(bucket: SoulNodeBucket, currentSections: SoulNodeSection[], currentWeight: number, nodeIndex: number, bucketNodeCount: number) {
    if (currentSections.length === 0) return
    const hasMultipleNodes = bucketNodeCount > 1
    const suffix = hasMultipleNodes ? ` ${nodeIndex + 1}` : ''
    const sectionCount = uniqueSectionCount(currentSections)
    const blockCount = sectionBlockCount(currentSections)

    nodes.push({
      id: `${group}-${bucket.key}-${nodeIndex + 1}`,
      kind: group,
      eyebrow: group === 'persona' ? 'Persona' : 'Instruction',
      title: `${bucket.title}${suffix}`,
      description: compactTitles(currentSections),
      sections: currentSections,
      sectionCount,
      blockCount,
      weight: currentWeight,
    })
  }

  buckets.forEach(({ bucket, slices }) => {
    const packed: Array<{ sections: SoulNodeSection[], weight: number }> = []
    let currentSections: SoulNodeSection[] = []
    let currentWeight = 0

    function flushPacked() {
      if (currentSections.length === 0) return
      packed.push({ sections: currentSections, weight: currentWeight })
      currentSections = []
      currentWeight = 0
    }

    slices.forEach((slice) => {
      const shouldStartNext = currentSections.length > 0 && currentWeight + slice.weight > maxNodeWeight
      if (shouldStartNext) flushPacked()
      currentSections.push(slice)
      currentWeight += slice.weight
    })

    flushPacked()
    packed.forEach((item, index) => pushNode(bucket, item.sections, item.weight, index, packed.length))
  })

  return nodes
}

function ensureActiveNode(preferredId = activeNodeId.value) {
  if (editorNodes.value.some(node => node.id === preferredId)) {
    activeNodeId.value = preferredId
    return
  }
  activeNodeId.value = editorNodes.value[0]?.id || firstSectionId()
}

function selectNode(id: string) {
  activeNodeId.value = id
}

function nodeDirty(node: SoulEditorNode) {
  return Boolean(node.includeMeta && metaDirty.value)
    || Boolean(node.includeProactive && proactiveDirty.value)
    || node.sections.some(item => sectionDirty(item.group, item.section.id))
}

function ensureSafeToDiscard() {
  if (!modified.value) return true
  return window.confirm('当前有人设修改尚未保存，确定继续吗？')
}

function compareSection(group: 'persona' | 'instruction', sectionId: string) {
  const currentSections = group === 'persona'
    ? editor.value?.persona_sections || []
    : editor.value?.instruction_sections || []
  const originalSections = group === 'persona'
    ? originalEditor.value?.persona_sections || []
    : originalEditor.value?.instruction_sections || []
  const currentSection = currentSections.find(section => section.id === sectionId) || null
  const originalSection = originalSections.find(section => section.id === sectionId) || null
  return JSON.stringify(currentSection) !== JSON.stringify(originalSection)
}

function sectionDirty(group: 'persona' | 'instruction', sectionId: string) {
  return compareSection(group, sectionId)
}

function nodeSectionDescription(nodeSection: SoulNodeSection) {
  if (!nodeSection.partial) return ''
  return `“${nodeSection.section.title}”内容较长，当前只显示其中一组配置块；保存时仍会写回原章节顺序。`
}

function nodeMetaText(node: SoulEditorNode) {
  const parts: string[] = []
  if (node.includeMeta) parts.push('基础')
  if (node.includeProactive) parts.push('插话')
  if (node.sectionCount > 0) parts.push(`${node.sectionCount} 章`)
  if (node.blockCount > 0) parts.push(`${node.blockCount} 块`)
  return parts.join(' · ') || '配置节点'
}

async function loadSoul(silent = false) {
  const previousActiveNodeId = activeNodeId.value
  if (silent) refreshing.value = true
  else loading.value = true

  try {
    const data = await api<unknown>('/api/admin/soul')
    if (!isSoulEditorResponse(data)) {
      if (isLegacySoulPageResponse(data)) {
        throw new Error('legacy-soul-api-payload')
      }
      throw new Error('invalid-soul-api-payload')
    }

    const nextEditor = normalizeEditor(data.editor)
    editor.value = cloneEditor(nextEditor)
    originalEditor.value = cloneEditor(nextEditor)
    syncWarning.value = ''
    loadError.value = ''
    await nextTick()
    ensureActiveNode(previousActiveNodeId)
  } catch (error) {
    console.error('Failed to load soul editor:', error)
    editor.value = null
    originalEditor.value = null
    activeNodeId.value = firstSectionId()
    const failureMessage = explainSoulLoadFailure(error)
    loadError.value = failureMessage
    message.error(failureMessage)
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

function resetDraft() {
  if (!originalEditor.value) return
  if (!ensureSafeToDiscard()) return
  editor.value = cloneEditor(originalEditor.value)
  syncWarning.value = ''
  void nextTick(() => ensureActiveNode())
}

async function refreshEditor() {
  if (!ensureSafeToDiscard()) return
  await loadSoul(true)
}

function openPersonaGuide() {
  void router.push('/soul/persona-guide')
}

function addListItem(block: SoulBlock) {
  if (!block.items) block.items = []
  block.items.push('')
}

function removeListItem(block: SoulBlock, index: number) {
  block.items?.splice(index, 1)
}

function addTableRow(block: SoulBlock) {
  if (!block.rows) block.rows = []
  block.rows.push({ key: '', value: '' })
}

function removeTableRow(block: SoulBlock, index: number) {
  block.rows?.splice(index, 1)
}

function addTextBlock(section: SoulSection, type: SoulBlockType = 'paragraph') {
  section.blocks.push({
    id: `${section.id}-block-${Date.now()}`,
    type,
    heading: '',
    text: '',
    items: [],
    columns: ['项', '内容'],
    rows: [],
  })
}

function textAutosize(block: SoulBlock) {
  if (block.type === 'free_text')
    return { minRows: 6 }
  return { minRows: 4 }
}

function blockLabel(block: SoulBlock, index: number) {
  if (block.heading) return block.heading
  if (block.type === 'paragraph') return `文本块 ${index + 1}`
  if (block.type === 'free_text') return `自由内容 ${index + 1}`
  if (block.type === 'bullet_list') return `无序列表 ${index + 1}`
  if (block.type === 'numbered_list') return `有序列表 ${index + 1}`
  return `键值表 ${index + 1}`
}

function blockTypeLabel(block: SoulBlock) {
  if (block.type === 'paragraph') return 'Paragraph'
  if (block.type === 'free_text') return 'Free Text'
  if (block.type === 'bullet_list') return 'Bullet List'
  if (block.type === 'numbered_list') return 'Numbered List'
  return 'Key Value'
}

function blockGroupLabel(group: SoulBlockGroup, index: number) {
  if (group.heading) return group.heading
  if (group.blocks.length === 1) return blockLabel(group.blocks[0], index)
  return `内容组 ${index + 1}`
}

function blockGroupTypeLabel(group: SoulBlockGroup) {
  if (group.blocks.length === 1) return blockTypeLabel(group.blocks[0])
  return `组合 · ${group.blocks.length} 块`
}

function blockHeadingPlaceholder(block: SoulBlock, index: number) {
  if (block.type === 'paragraph') return `文本块 ${index + 1}，例如：角色名或关系名`
  if (block.type === 'free_text') return `自由内容 ${index + 1}`
  if (block.type === 'bullet_list') return `列表 ${index + 1}`
  if (block.type === 'numbered_list') return `流程 ${index + 1}`
  return `键值表 ${index + 1}`
}

function blockGroups(blocks: SoulBlock[]) {
  const groups: SoulBlockGroup[] = []

  blocks.forEach((block, index) => {
    const heading = (block.heading || '').trim()
    const lastGroup = groups[groups.length - 1]

    if (heading || !lastGroup) {
      groups.push({
        id: `${block.id || index}-group`,
        heading,
        blocks: [block],
      })
      return
    }

    lastGroup.blocks.push(block)
  })

  return groups
}

function updateBlockGroupHeading(group: SoulBlockGroup, value: string) {
  const cleaned = value.replace(/[*_`#>\[\]()]/g, '').trim()
  group.blocks.forEach((block, index) => {
    block.heading = index === 0 ? cleaned : ''
  })
}

function validateEditor() {
  if (!editor.value) return '当前没有可保存的人设内容'
  if (!editor.value.meta.display_title.trim()) return '请先填写展示标题'
  if (!editor.value.meta.name.trim()) return '请先填写人设名'
  if (editor.value.proactive.enabled && !editor.value.proactive.text.trim()) {
    return '启用插话方式时必须填写规则内容'
  }
  return ''
}

async function save() {
  if (!editor.value) return
  const validationError = validateEditor()
  if (validationError) {
    message.warning(validationError)
    return
  }

  saving.value = true
  try {
    const data = await api<{
      ok: boolean
      message?: string
      error?: string
      reload_ok?: boolean
    }>('/api/admin/soul/save', {
      method: 'POST',
      body: {
        editor: editor.value,
      },
    })

    if (!data.ok) {
      message.error(data.error || '保存失败')
      return
    }

    const warningText = data.reload_ok === false
      ? 'config/soul/identity.md 与 config/soul/instruction.md 已保存，但运行时同步失败。'
      : ''

    if (warningText) {
      message.warning(warningText)
    } else {
      message.success('已保存到 config/soul/identity.md 与 config/soul/instruction.md，并已自动重载。')
    }

    await loadSoul(true)
    syncWarning.value = warningText
  } catch (error) {
    console.error('Failed to save soul editor:', error)
    message.error('保存失败')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <AppPage
    title="人设编辑"
    eyebrow="Identity Editor"
    description="通过结构化章节维护人设、行为规则和插话方式，保存后写入 identity.md / instruction.md 并同步运行时身份。"
  >
    <template #action>
      <div class="soul-hero-actions">
        <NTag round size="small" type="success">
          {{ formatLabel }}
        </NTag>
        <NTag round size="small" :type="modified ? 'warning' : 'default'">
          {{ modified ? '存在未保存修改' : '已同步' }}
        </NTag>
        <NButton class="soul-action-button" secondary :loading="refreshing" @click="refreshEditor">
          <template #icon>
            <NIcon :component="RefreshOutline" />
          </template>
          刷新结构
        </NButton>
        <RestartBotButton class="soul-action-button soul-action-button--wide" />
        <NButton class="soul-action-button" secondary :disabled="!modified" @click="resetDraft">
          重置草稿
        </NButton>
        <NButton class="soul-action-button soul-action-button--wide" secondary @click="openPersonaGuide">
          <template #icon>
            <NIcon :component="DocumentTextOutline" />
          </template>
          AI 人设生成规则
        </NButton>
        <NButton class="soul-action-button" type="primary" :loading="saving" :disabled="loading || !editor" @click="save">
          <template #icon>
            <NIcon :component="SaveOutline" />
          </template>
          保存并同步
        </NButton>
      </div>
    </template>

    <div class="soul-metric-grid">
      <MetricCard
        title="编辑格式"
        value="双文件"
        :hint="formatHint"
        :icon="SparklesOutline"
        accent="primary"
      />
      <MetricCard
        title="人设章节"
        :value="personaCount"
        hint="当前可编辑的人设正文章节数"
        :icon="LayersOutline"
        accent="success"
      />
      <MetricCard
        title="规则章节"
        :value="instructionCount"
        hint="当前可编辑的行为规则章节数"
        :icon="DocumentTextOutline"
        accent="info"
      />
      <MetricCard
        title="同步状态"
        :value="loadError ? '加载失败' : (syncWarning ? '需检查' : (modified ? '未保存' : '已同步'))"
        :hint="loadError || syncWarning || (modified ? '当前草稿尚未写入双文件' : '当前结构已与运行时同步')"
        :icon="PencilOutline"
        accent="warning"
      />
    </div>

    <template v-if="loading">
      <NSkeleton :repeat="10" text />
    </template>

    <template v-else-if="editor">
      <NAlert
        v-if="syncWarning"
        type="warning"
        class="mb-16"
        title="运行时同步失败"
      >
        {{ syncWarning }}
      </NAlert>

      <AppCard bordered elevated class="soul-editor">
        <AppEditorShell>
          <template #left>
            <NTag round size="small">
              {{ editor.meta.display_title || '未命名人设' }}
            </NTag>
            <NTag round size="small" type="success">
              identity.md / instruction.md
            </NTag>
          </template>

          <template #right>
            <NTag round size="small" type="info">
              人设 {{ personaCount }}
            </NTag>
            <NTag round size="small" type="success">
              规则 {{ instructionCount }}
            </NTag>
          </template>
        </AppEditorShell>

        <template v-if="editorNodes.length > 0">
          <div class="soul-node-strip">
            <div class="soul-node-strip__head">
              <div>
                <p class="soul-node-strip__eyebrow">
                  Nodes
                </p>
                <h3 class="soul-node-strip__title">
                  配置节点
                </h3>
              </div>
              <NTag round size="small">
                {{ nodeCount }} 个节点
              </NTag>
            </div>

            <div class="soul-node-strip__track" role="tablist" aria-label="人设配置节点">
              <button
                v-for="node in editorNodes"
                :key="node.id"
                type="button"
                class="soul-node-tab"
                :class="{ 'soul-node-tab--active': activeNodeId === node.id }"
                role="tab"
                :aria-selected="activeNodeId === node.id"
                @click="selectNode(node.id)"
              >
                <span class="soul-node-tab__eyebrow">{{ node.eyebrow }}</span>
                <span class="soul-node-tab__title">{{ node.title }}</span>
                <span class="soul-node-tab__meta">
                  {{ nodeMetaText(node) }}
                </span>
                <span v-if="nodeDirty(node)" class="soul-node-tab__dot" />
              </button>
            </div>
          </div>

          <div v-if="currentNode" :key="currentNode.id" class="soul-node-panel">
              <AppPanelSection
                v-if="currentNode.includeMeta"
                eyebrow="Metadata"
                title="基础信息"
                description="这部分会写入 identity.md 的一级标题和标题下方简介。"
              >
                <div class="soul-field-grid">
                  <label class="soul-field">
                    <span>人设名</span>
                    <NInput
                      v-model:value="editor.meta.name"
                      placeholder="用于运行时身份名"
                    />
                  </label>

                  <label class="soul-field">
                    <span>展示标题</span>
                    <NInput
                      v-model:value="editor.meta.display_title"
                      placeholder="写入正文 H1，例如 凤笑梦 (Emu Otori)"
                    />
                  </label>

                  <label class="soul-field soul-field--full">
                    <span>简述</span>
                    <NInput
                      v-model:value="editor.meta.description"
                      type="textarea"
                      :autosize="{ minRows: 3 }"
                      placeholder="写入 identity.md 标题下方的简短说明"
                    />
                  </label>
                </div>
              </AppPanelSection>

              <AppPanelSection
                v-if="currentNode.includeProactive"
                eyebrow="Proactive"
                title="插话方式"
                description="关闭后不会写出 `## 插话方式`，开启后会把下面的规则单独写入该章节。"
              >
                <template #aside>
                  <div class="soul-proactive__toggle">
                    <span>启用主动插话</span>
                    <NSwitch v-model:value="editor.proactive.enabled" />
                  </div>
                </template>

                <NInput
                  v-model:value="editor.proactive.text"
                  type="textarea"
                  :disabled="!editor.proactive.enabled"
                  :autosize="{ minRows: 6 }"
                  placeholder="填写需要落到 `## 插话方式` 下的规则文本"
                />
              </AppPanelSection>

              <div v-if="currentNode.sections.length > 0" class="soul-node-sections">
                <AppPanelSection
                  v-for="nodeSection in currentNode.sections"
                  :key="`${nodeSection.group}-${nodeSection.sliceId}`"
                  :eyebrow="nodeSection.group === 'persona' ? 'Persona' : 'Instruction'"
                  :title="nodeSection.label"
                  :description="nodeSectionDescription(nodeSection)"
                >
                  <div class="soul-block-stack">
                    <template v-if="nodeSection.blocks.length > 0">
                      <div
                        v-for="(group, groupIndex) in blockGroups(nodeSection.blocks)"
                        :key="group.id"
                        class="soul-block-card"
                      >
                        <div class="soul-block-card__head">
                          <div class="soul-block-card__summary">
                            <p class="soul-block-card__eyebrow">
                              {{ blockGroupTypeLabel(group) }}
                            </p>
                            <h4 class="soul-block-card__title">
                              {{ blockGroupLabel(group, groupIndex) }}
                            </h4>
                          </div>
                          <label class="soul-block-card__heading-field">
                            <span>小标题</span>
                            <NInput
                              :value="group.heading"
                              size="small"
                              clearable
                              placeholder="例如：角色名、关系名、场景名或规则名"
                              @update:value="value => updateBlockGroupHeading(group, value)"
                            />
                          </label>
                        </div>

                        <div class="soul-block-card__parts">
                          <div
                            v-for="(block, partIndex) in group.blocks"
                            :key="block.id"
                            class="soul-block-part"
                            :class="{ 'soul-block-part--embedded': group.blocks.length > 1 }"
                          >
                            <div v-if="group.blocks.length > 1" class="soul-block-part__label">
                              {{ blockTypeLabel(block) }} {{ partIndex + 1 }}
                            </div>

                            <template v-if="block.type === 'paragraph' || block.type === 'free_text'">
                              <NInput
                                v-model:value="block.text"
                                type="textarea"
                                :autosize="textAutosize(block)"
                                :placeholder="block.type === 'free_text' ? '保留这段 Markdown 片段的原始结构' : '编辑这段内容'"
                              />
                            </template>

                            <template v-else-if="block.type === 'bullet_list' || block.type === 'numbered_list'">
                              <div class="soul-list-editor">
                                <div
                                  v-for="(listItem, itemIndex) in block.items"
                                  :key="`${block.id}-item-${itemIndex}`"
                                  class="soul-list-editor__row"
                                >
                                  <span class="soul-list-editor__index">
                                    {{ block.type === 'numbered_list' ? `${itemIndex + 1}.` : '•' }}
                                  </span>
                                  <NInput
                                    v-model:value="block.items![itemIndex]"
                                    type="textarea"
                                    :autosize="{ minRows: 2 }"
                                    placeholder="填写这一条列表内容"
                                  />
                                  <NButton secondary size="small" @click="removeListItem(block, itemIndex)">
                                    删除
                                  </NButton>
                                </div>

                                <NButton secondary size="small" @click="addListItem(block)">
                                  添加条目
                                </NButton>
                              </div>
                            </template>

                            <template v-else-if="block.type === 'kv_table'">
                              <div class="soul-table-editor">
                                <div class="soul-table-editor__columns">
                                  <NInput
                                    v-model:value="block.columns![0]"
                                    placeholder="左列表头"
                                  />
                                  <NInput
                                    v-model:value="block.columns![1]"
                                    placeholder="右列表头"
                                  />
                                </div>

                                <div
                                  v-for="(row, rowIndex) in block.rows"
                                  :key="`${block.id}-row-${rowIndex}`"
                                  class="soul-table-editor__row"
                                >
                                  <NInput
                                    v-model:value="row.key"
                                    type="textarea"
                                    :autosize="{ minRows: 1 }"
                                    placeholder="键"
                                  />
                                  <NInput
                                    v-model:value="row.value"
                                    type="textarea"
                                    :autosize="{ minRows: 1 }"
                                    placeholder="值"
                                  />
                                  <NButton secondary size="small" @click="removeTableRow(block, rowIndex)">
                                    删除
                                  </NButton>
                                </div>

                                <NButton secondary size="small" @click="addTableRow(block)">
                                  添加条目
                                </NButton>
                              </div>
                            </template>
                          </div>
                        </div>
                      </div>
                    </template>

                    <div v-else class="soul-section-empty">
                      <NEmpty size="small" description="这个章节目前没有可编辑块">
                        <template #extra>
                          <NButton secondary size="small" @click="addTextBlock(nodeSection.section, 'paragraph')">
                            添加文本块
                          </NButton>
                        </template>
                      </NEmpty>
                    </div>
                  </div>
                </AppPanelSection>
              </div>
            </div>

            <EmptyState
              v-else
              compact
              title="当前节点不可用"
              description="节点索引已经刷新，但当前选中的节点不存在。请点击上方节点或刷新结构。"
              :icon="DocumentTextOutline"
            />
        </template>

        <EmptyState
          v-else
          compact
          title="没有可切换的配置节点"
          description="接口返回了人设结构，但没有生成任何可编辑节点。请刷新结构或检查 Soul API 响应。"
          :icon="DocumentTextOutline"
        />
      </AppCard>
    </template>

    <EmptyState
      v-else
      title="当前没有可编辑的人设结构"
      :description="loadError || '接口没有返回可用的人设编辑模型，请检查 `config/soul` 配置。'"
      :icon="DocumentTextOutline"
    />
  </AppPage>
</template>

<style scoped>
.soul-hero-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  align-items: center;
  gap: 8px;
}

.soul-action-button {
  min-width: 112px;
  height: 34px;
  border-radius: 999px;
}

.soul-action-button--wide {
  min-width: 146px;
}

.soul-hero-actions :deep(.n-button__content) {
  justify-content: center;
  font-size: 13px;
  font-weight: 600;
}

.soul-hero-actions :deep(.n-tag) {
  height: 26px;
}

.soul-metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

.soul-editor {
  display: grid;
  align-content: start;
  gap: 16px;
  min-height: 0;
  padding: 20px;
}

.soul-node-strip {
  display: grid;
  gap: 10px;
  padding: 12px;
  border: 1px solid var(--om-border);
  border-radius: 18px;
  background: color-mix(in srgb, var(--om-surface-solid) 78%, transparent);
}

.soul-node-strip__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.soul-node-strip__eyebrow {
  margin: 0 0 6px;
  color: var(--om-text-3);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}

.soul-node-strip__title {
  margin: 0;
  color: var(--om-text-1);
  font-size: 15px;
  font-weight: 700;
}

.soul-node-strip__track {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(132px, 1fr));
  gap: 8px;
  min-width: 0;
}

.soul-node-tab {
  position: relative;
  display: grid;
  gap: 6px;
  min-height: 62px;
  padding: 10px 12px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: var(--om-surface-solid);
  color: inherit;
  cursor: pointer;
  text-align: left;
  transition:
    border-color 0.18s ease,
    background-color 0.18s ease,
    box-shadow 0.18s ease,
    transform 0.18s ease;
}

.soul-node-tab:hover {
  transform: translateY(-1px);
  border-color: var(--om-border-strong);
  background: var(--om-surface-2);
}

.soul-node-tab--active {
  border-color: rgba(var(--primary-color), 0.46);
  background: rgba(var(--primary-color), 0.1);
  box-shadow: 0 10px 22px rgba(23, 42, 48, 0.08);
}

.soul-node-tab__eyebrow {
  color: var(--om-text-3);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.soul-node-tab__title {
  min-width: 0;
  overflow: hidden;
  color: var(--om-text-1);
  font-size: 13px;
  font-weight: 700;
  line-height: 1.4;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.soul-node-tab__meta {
  color: var(--om-text-2);
  font-size: 11px;
  font-weight: 600;
}

.soul-node-tab__dot {
  position: absolute;
  top: 10px;
  right: 10px;
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: var(--om-warning);
}

.soul-node-panel,
.soul-node-sections {
  display: grid;
  gap: 12px;
}

.soul-node-panel {
  min-height: 0;
}

.soul-field-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.soul-field {
  display: grid;
  gap: 8px;
}

.soul-field span {
  color: var(--om-text-2);
  font-size: 13px;
  font-weight: 600;
}

.soul-field--full {
  grid-column: 1 / -1;
}

.soul-proactive__toggle {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  color: var(--om-text-2);
  font-size: 13px;
  font-weight: 600;
}

.soul-block-stack {
  display: grid;
  gap: 14px;
}

.soul-block-card {
  display: grid;
  gap: 12px;
  padding: 16px;
  border: 1px solid var(--om-border);
  border-radius: 16px;
  background: color-mix(in srgb, var(--om-surface-solid) 72%, transparent);
}

.soul-block-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.soul-block-card__summary {
  min-width: 0;
  flex: 1;
}

.soul-block-card__eyebrow {
  margin: 0 0 8px;
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}

.soul-block-card__title {
  margin: 0;
  color: var(--om-text-1);
  font-size: 15px;
  font-weight: 700;
}

.soul-block-card__heading-field {
  display: grid;
  width: min(360px, 44%);
  gap: 6px;
}

.soul-block-card__heading-field span {
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.soul-block-card__parts {
  display: grid;
  gap: 12px;
}

.soul-block-part {
  display: grid;
  gap: 10px;
}

.soul-block-part--embedded {
  padding: 12px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: color-mix(in srgb, var(--om-surface-solid) 64%, transparent);
}

.soul-block-part__label {
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.soul-list-editor,
.soul-table-editor {
  display: grid;
  gap: 12px;
}

.soul-list-editor__row {
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr) auto;
  gap: 10px;
  align-items: flex-start;
}

.soul-list-editor__index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 34px;
  color: var(--om-text-2);
  font-size: 13px;
  font-weight: 700;
}

.soul-table-editor__columns,
.soul-table-editor__row {
  display: grid;
  grid-template-columns: minmax(0, 0.8fr) minmax(0, 1.2fr) auto;
  gap: 10px;
  align-items: flex-start;
}

.soul-table-editor__columns {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.soul-section-empty {
  padding: 8px 0;
}

@media (max-width: 960px) {
  .soul-metric-grid,
  .soul-field-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .soul-metric-grid,
  .soul-field-grid,
  .soul-table-editor__columns {
    grid-template-columns: 1fr;
  }

  .soul-list-editor__row,
  .soul-table-editor__row,
  .soul-block-card__head {
    grid-template-columns: 1fr;
  }

  .soul-block-card__head {
    display: grid;
  }

  .soul-block-card__heading-field {
    width: 100%;
  }

  .soul-hero-actions {
    justify-content: flex-start;
  }

  .soul-node-strip__track {
    grid-template-columns: repeat(auto-fit, minmax(118px, 1fr));
  }

  .soul-node-strip,
  .soul-editor {
    padding: 14px;
  }
}
</style>
