<script setup lang="ts">
import {
  ChatbubblesOutline,
  ChevronForwardOutline,
  CloudUploadOutline,
  CodeSlashOutline,
  CutOutline,
  DocumentTextOutline,
  FolderOpenOutline,
  HardwareChipOutline,
  KeyOutline,
  PulseOutline,
  RefreshOutline,
  SaveOutline,
  ServerOutline,
  SettingsOutline,
  SparklesOutline,
  TimeOutline,
} from '@vicons/ionicons5'
import {
  NAlert,
  NButton,
  NIcon,
  NInput,
  NSpace,
  NSkeleton,
  NSwitch,
  NTag,
  useMessage,
} from 'naive-ui'
import type { Component } from 'vue'

import { api } from '../../api/client'
import AppPage from '../../components/common/AppPage.vue'
import AppPanelSection from '../../components/common/AppPanelSection.vue'
import EmptyState from '../../components/common/EmptyState.vue'
import PageToolbar from '../../components/common/PageToolbar.vue'
import RestartBotButton from '../../components/common/RestartBotButton.vue'
import StateBadge from '../../components/common/StateBadge.vue'
import ConfigField from './ConfigField.vue'
import ConfigStatusStrip, { type ConfigStatusItem } from './ConfigStatusStrip.vue'
import ConfigSystemBackup from './components/ConfigSystemBackup.vue'
import { bucketFields, CONFIG_SECTION_LABELS, type ConfigSectionBucket } from './section-labels'
import type {
  ConfigAuditEntry,
  ConfigAuditPayload,
  ConfigBackupEntry,
  ConfigBackupPayload,
  ConfigDiffChange,
  ConfigEditorPayload,
  ConfigFieldSchema,
  ConfigPreviewResult,
  ConfigRestoreResult,
  ConfigSaveResult,
} from './types'

type ConfigTaskId = 'model' | 'chat' | 'rhythm' | 'humanization' | 'segmentation' | 'connection' | 'access'

interface LegacyConfigResponse {
  path?: string
  content?: string
}

interface ConfigTaskDefinition {
  id: ConfigTaskId
  title: string
  eyebrow: string
  description: string
  audience: string
  impact: string
  restart: string
  paths: string[]
}

interface FieldUiHint {
  display_label?: string
  help?: string
  example?: string
  recommended?: string
  risk_level?: 'normal' | 'careful' | 'danger'
  restart_hint?: 'none' | 'recommended' | 'required'
}

const FIELD_UI_HINTS: Record<string, FieldUiHint> = {
  'llm.api_format': {
    display_label: '模型接口类型',
    help: '选择当前模型服务按哪种协议通信。DeepSeek V4 原生模式请选择 deepseek。',
    recommended: 'deepseek / anthropic / openai 按实际服务选择',
    restart_hint: 'recommended',
  },
  'llm.base_url': {
    display_label: '模型服务地址',
    help: 'LLM API 的基础地址，填错会导致 Bot 无法回复。',
    example: 'https://api.deepseek.com',
    risk_level: 'careful',
    restart_hint: 'recommended',
  },
  'llm.api_key': {
    display_label: '模型 API Key',
    help: '用于调用模型服务的密钥。保存时不会在审计记录中明文展示。',
    example: 'sk-...',
    risk_level: 'danger',
    restart_hint: 'recommended',
  },
  'llm.model': {
    display_label: '默认模型',
    help: '主聊天默认使用的模型名。不同 provider 的模型名格式不同。',
    example: 'deepseek-v4-flash',
    restart_hint: 'recommended',
  },
  'llm.default_profile': {
    display_label: '默认模型配置',
    help: '没有单独指定任务模型时使用的 profile。',
    recommended: 'main',
    restart_hint: 'recommended',
  },
  'llm.task_profiles': {
    display_label: '任务模型分配',
    help: '为 main、thinker、compact、slang、vision 等任务选择不同模型配置。',
    risk_level: 'careful',
    restart_hint: 'recommended',
  },
  'group.allowed_groups': {
    display_label: '允许群号兜底',
    help: '群门禁的真理来源是 config/group-policy.json，请到「群管理」编辑；这里只在 group-policy.json 缺失时作为兜底使用。',
    risk_level: 'careful',
    restart_hint: 'recommended',
  },
  'group.access.mode': {
    display_label: '群门禁模式',
    help: '门禁推荐到「群管理」页编辑（写入 config/group-policy.json）。这里的值仅在 group-policy.json 缺失时作为兜底。',
    recommended: 'whitelist 更安全；默认 whitelist',
    risk_level: 'careful',
    restart_hint: 'recommended',
  },
  'group.access.whitelist': {
    display_label: '群门禁白名单',
    help: '同上：白/黑名单的真理来源是「群管理」页 + group-policy.json。这里仅作为 fallback。',
    risk_level: 'careful',
    restart_hint: 'recommended',
  },
  'group.access.blacklist': {
    display_label: '群门禁黑名单',
    help: '同上：白/黑名单的真理来源是「群管理」页 + group-policy.json。这里仅作为 fallback。',
    risk_level: 'careful',
    restart_hint: 'recommended',
  },
  'group.presence.default_mode': {
    display_label: '默认参与模式',
    help: '门禁通过后，未在发言白名单中的群的默认参与态。要让某群学习黑话请到「群管理」对应群单独打开 silent_learn。',
    recommended: 'active 或 silent_learn',
    risk_level: 'careful',
    restart_hint: 'recommended',
  },
  'group.at_only': {
    display_label: '只在被 @ 时回复',
    help: '开启后 Bot 会更安静，只在被明确呼唤时回复。',
    recommended: '新群试运行时建议开启',
  },
  'group.talk_value': {
    display_label: '主动回复强度',
    help: '数值越高越容易自然插话，越低越克制。',
    recommended: '0.2 - 0.5',
  },
  'group.reply_style': {
    display_label: '默认回复风格',
    help: '控制全局回复语气。单群个性化建议在群管理页里设置。',
  },
  'group.tools_enabled': {
    display_label: '允许工具调用',
    help: '关闭后 Bot 不再主动调用联网、群管理等工具。',
    risk_level: 'careful',
  },
  'group.slang_enabled': {
    display_label: '启用群内黑话',
    help: '开启后会把已审核黑话注入当前群语境。',
  },
  'group.sticker_mode': {
    display_label: '表情包倾向',
    help: '控制默认表情包发送倾向。更细配置可去表情包页面。',
  },
  'anti_detect.enabled': {
    display_label: '启用拟人延迟',
    help: '让回复更像人在输入，降低机械感。',
  },
  'anti_detect.min_delay': {
    display_label: '最短回复延迟',
    help: '每次回复前至少等待的秒数。',
    recommended: '0.5 - 1.0 秒',
  },
  'anti_detect.max_delay': {
    display_label: '最长回复延迟',
    help: '每次回复前最多等待的秒数。',
    recommended: '2 - 5 秒',
  },
  'anti_detect.char_delay': {
    display_label: '按字数增加延迟',
    help: '回复越长，等待越久。数值过高会显得太慢。',
    recommended: '0.01 - 0.04',
  },
  'thinker.enabled': {
    display_label: '启用回复前思考',
    help: '开启后会多一次思考步骤，回复可能更稳但更慢、更耗 token。',
    risk_level: 'careful',
    restart_hint: 'recommended',
  },
  'napcat.api_url': {
    display_label: 'NapCat API 地址',
    help: 'QQ 协议端的 HTTP API 地址。填错会影响发消息、取群信息等能力。',
    example: 'http://localhost:29300',
    risk_level: 'danger',
    restart_hint: 'recommended',
  },
  'vision.enabled': {
    display_label: '启用图片理解',
    help: '开启后 Bot 可以读取图片内容，但需要视觉模型配置可用。',
    restart_hint: 'recommended',
  },
  'vision.qwen.api_key': {
    display_label: '视觉模型 API Key',
    help: '用于图片理解的小模型密钥。',
    risk_level: 'danger',
    restart_hint: 'recommended',
  },
  'vision.qwen.base_url': {
    display_label: '视觉模型地址',
    help: 'Qwen VL 兼容接口地址。',
    risk_level: 'careful',
    restart_hint: 'recommended',
  },
  'vision.qwen.model': {
    display_label: '视觉模型名',
    help: '图片理解所使用的模型。',
    example: 'qwen2.5-vl-7b-instruct',
    restart_hint: 'recommended',
  },
  admins: {
    display_label: '管理员映射',
    help: '管理员 QQ 号与备注映射。错误配置会影响管理指令权限。',
    risk_level: 'danger',
    restart_hint: 'recommended',
  },
  admin_token: {
    display_label: 'Web 管理 Token',
    help: '登录管理端使用的令牌。请不要泄露。',
    risk_level: 'danger',
    restart_hint: 'required',
  },
  allowed_private_users: {
    display_label: '允许私聊用户',
    help: '允许与 Bot 私聊的 QQ 用户列表。为空时按当前后端规则处理。',
    risk_level: 'careful',
    restart_hint: 'recommended',
  },
  'group.blocked_users': {
    display_label: '屏蔽用户',
    help: '这些用户的消息会被忽略。',
    risk_level: 'careful',
  },
  'reply_segmentation.enabled': {
    display_label: '启用回复分段',
    help: '开启后长回复会按自然断点逐段发送；关闭后会尽量作为一条回复发送。',
    restart_hint: 'recommended',
  },
  'reply_segmentation.max_segment_chars': {
    display_label: '单段目标长度',
    help: '分段器会围绕这个长度寻找句末、短句或 token 边界，不是硬截断。',
    recommended: '18 - 32',
    restart_hint: 'recommended',
  },
  'reply_segmentation.min_segment_chars': {
    display_label: '短尾合并阈值',
    help: '尾段太短时会优先并回上一段，避免一两个字单独成条。',
    recommended: '4 - 8',
    restart_hint: 'recommended',
  },
  'reply_segmentation.max_send_segments': {
    display_label: '硬性段数上限',
    help: '0 表示不限制正常动态长度；设为正数会把超出的内容并回最后一段。',
    recommended: '保持 0，除非需要强制防刷屏',
    risk_level: 'careful',
    restart_hint: 'recommended',
  },
  'reply_segmentation.soft_max_send_segments': {
    display_label: '软性段数上限',
    help: '超过该段数时会截断并追加自然收尾，用来兜住极端长回复。',
    recommended: '10 - 16，0 为关闭',
    risk_level: 'careful',
    restart_hint: 'recommended',
  },
  'reply_segmentation.soft_limit_notice': {
    display_label: '软上限收尾文案',
    help: '只在极端长回复触发软上限时追加。',
    restart_hint: 'recommended',
  },
  'reply_segmentation.prefer_sentence_break': {
    display_label: '优先句末断点',
    help: '开启后优先在句号、问号、感叹号等自然位置切段。',
    restart_hint: 'recommended',
  },
  'reply_segmentation.preserve_ascii_tokens': {
    display_label: '保护链接与 CQ 码',
    help: '开启后尽量不切断 URL、CQ 码、英文 token 和编号。',
    restart_hint: 'recommended',
  },
  'reply_segmentation.merge_short_tail': {
    display_label: '合并过短尾段',
    help: '开启后减少最后一个短词或标点单独成条。',
    restart_hint: 'recommended',
  },
  'reply_segmentation.first_segment_humanize': {
    display_label: '首段延迟策略',
    help: 'skip 表示首段尽快发出；normal 表示按拟人延迟等待。',
    recommended: 'skip / normal',
    restart_hint: 'recommended',
  },
  'reply_segmentation.later_segment_humanize': {
    display_label: '后续段延迟策略',
    help: 'normal 表示后续段按拟人节奏发送；skip 会让所有段更快排出。',
    recommended: 'normal / skip',
    restart_hint: 'recommended',
  },
  'reply_segmentation.inter_segment_delay_s': {
    display_label: '分段固定间隔',
    help: '每两段之间额外等待的秒数；过大可能让长回复显得拖沓。',
    recommended: '0.5 - 1.2 秒',
    restart_hint: 'recommended',
  },
  'humanization.profile': {
    display_label: '拟人化生成档位',
    help: 'custom 保持显式开关；economy 只做 cache 稳定；balanced/performance 会由后续 Part 6 能力逐步接管生成形态。',
    recommended: 'custom / economy / balanced / performance',
    risk_level: 'careful',
    restart_hint: 'required',
  },
  'humanization.runtime_groups': {
    display_label: '限定群（空=全部）',
    help: '非空时仅这些群启用拟人化子能力；空列表表示全部群生效。',
    risk_level: 'careful',
    restart_hint: 'recommended',
  },
  'humanization.state_board.layout': {
    display_label: '状态板位置',
    help: 'head 保持旧 prompt 顺序；tail 把 state_board 后置以降低前缀缓存抖动。',
    recommended: 'head / tail',
    risk_level: 'careful',
    restart_hint: 'required',
  },
  'humanization.state_board.granularity': {
    display_label: '状态板粒度',
    help: 'fine 保留分钟/计数；coarse 使用粗粒度时间和频率，提升 prompt 稳定性。',
    recommended: 'fine / coarse',
    risk_level: 'careful',
    restart_hint: 'required',
  },
  'humanization.streaming_segment.enabled': {
    display_label: '流式分段',
    help: '开启后 LLM 输出在 SSE 流上实时切段发送，不等生成完毕。',
    risk_level: 'careful',
    restart_hint: 'required',
  },
  'humanization.pause_then_extend.enabled': {
    display_label: '追发（Pause-then-Extend）',
    help: '发完第一段后等待观察，决定是否追发后续内容。',
    risk_level: 'careful',
    restart_hint: 'required',
  },
  'humanization.plan_then_utter.enabled': {
    display_label: '计划式生成（Plan-then-Utter）',
    help: '先生成段大纲，再逐段独立生成。增加 LLM 调用次数但提升自然度。',
    risk_level: 'careful',
    restart_hint: 'required',
  },
  'humanization.rws_primary': {
    display_label: 'RWS 接管调度',
    help: '开启后 RWS 概率评分接管群聊回复决策（替代旧逻辑）。',
    risk_level: 'careful',
    restart_hint: 'required',
  },
  'humanization.qq_interactions.poke_outbound_enabled': {
    display_label: '戳一戳（发出）',
    help: 'bot 可以主动戳群友。',
    risk_level: 'normal',
    restart_hint: 'recommended',
  },
  'humanization.qq_interactions.reaction_outbound_enabled': {
    display_label: '表情回应（发出）',
    help: 'bot 可以给消息贴表情回应。',
    risk_level: 'normal',
    restart_hint: 'recommended',
  },
  'humanization.qq_interactions.quote_reply_enabled': {
    display_label: '引用回复',
    help: 'bot 回复时引用原消息。',
    risk_level: 'normal',
    restart_hint: 'recommended',
  },
  'humanization.qq_interactions.poke_inbound_response_enabled': {
    display_label: '戳一戳触发回复',
    help: '被戳时触发 bot 回复。',
    risk_level: 'normal',
    restart_hint: 'recommended',
  },
  'humanization.qq_interactions.reaction_inbound_response_enabled': {
    display_label: '表情回应触发回复',
    help: '收到表情回应时触发 bot 回复。',
    risk_level: 'normal',
    restart_hint: 'recommended',
  },
}

const CONFIG_TASKS: ConfigTaskDefinition[] = [
  {
    id: 'model',
    title: '模型与 API',
    eyebrow: 'Model Setup',
    description: '让 Bot 能正确调用大模型，是新手第一优先级。',
    audience: '第一次部署、切换 DeepSeek/OpenAI/Anthropic、换模型时修改。',
    impact: '影响主聊天、后台任务、压缩、黑话审核等模型调用。',
    restart: '通常建议保存后在线重启 Bot，让 provider 与 profile 状态重新加载。',
    paths: [
      'llm.api_format',
      'llm.base_url',
      'llm.api_key',
      'llm.model',
      'llm.default_profile',
      'llm.task_profiles',
      'vision.enabled',
      'vision.max_images_per_message',
      'vision.qwen.api_key',
      'vision.qwen.base_url',
      'vision.qwen.model',
    ],
  },
  {
    id: 'chat',
    title: '群聊回复',
    eyebrow: 'Chat Behavior',
    description: '决定 Bot 在群里多主动、什么风格、是否使用工具和黑话。群门禁请到「群管理」页编辑。',
    audience: '想调回复频率、只 @ 回复、默认语气和工具开关时修改。',
    impact: '影响所有未单独覆盖的群；白/黑名单门禁请去「群管理」页。',
    restart: '多数行为可即时读取，但保存后重启能确保所有长期任务同步。',
    paths: [
      'group.at_only',
      'group.talk_value',
      'group.reply_style',
      'group.tools_enabled',
      'group.slang_enabled',
      'group.sticker_mode',
    ],
  },
  {
    id: 'rhythm',
    title: '回复节奏',
    eyebrow: 'Human Rhythm',
    description: '调节拟人延迟、回复前思考等"像不像人"的细节。',
    audience: '觉得回复太快、太慢、太机械，或想开启 thinker 时修改。',
    impact: '影响回复延迟、消耗和响应速度。',
    restart: '建议重启以确保异步任务和缓存状态都使用新节奏。',
    paths: ['anti_detect.enabled', 'anti_detect.min_delay', 'anti_detect.max_delay', 'anti_detect.char_delay', 'thinker.enabled', 'thinker.max_tokens'],
  },
  {
    id: 'humanization',
    title: '拟人化生成',
    eyebrow: 'Humanization',
    description: '控制 Part 6 的生成档位、调度策略与 QQ 交互能力开关。',
    audience: '调整拟人化生成行为、开关 QQ 特殊交互时修改。',
    impact: '影响 state_board prompt 稳定性，以及 streaming / pause / plan 生成路径和 QQ 交互行为。',
    restart: '保存后建议在线重启，让 LLMClient、health guard 与长驻任务统一读取新配置。',
    paths: [
      'humanization.profile',
      'humanization.runtime_groups',
      'humanization.rws_primary',
      'humanization.state_board.layout',
      'humanization.state_board.granularity',
      'humanization.streaming_segment.enabled',
      'humanization.pause_then_extend.enabled',
      'humanization.plan_then_utter.enabled',
      'humanization.qq_interactions.poke_outbound_enabled',
      'humanization.qq_interactions.reaction_outbound_enabled',
      'humanization.qq_interactions.quote_reply_enabled',
      'humanization.qq_interactions.poke_inbound_response_enabled',
      'humanization.qq_interactions.reaction_inbound_response_enabled',
    ],
  },
  {
    id: 'segmentation',
    title: '回复分段',
    eyebrow: 'Segmentation',
    description: '控制长回复如何切段、排队和逐段发出。',
    audience: '验收分段效果、调长回复节奏、防极端刷屏时修改。',
    impact: '影响群聊可见回复的段落数量、段间延迟和软硬上限，不改变模型生成意图。',
    restart: '建议保存后重启 Bot，让调度器和发送队列统一加载新参数。',
    paths: [
      'reply_segmentation.enabled',
      'reply_segmentation.max_segment_chars',
      'reply_segmentation.min_segment_chars',
      'reply_segmentation.max_send_segments',
      'reply_segmentation.soft_max_send_segments',
      'reply_segmentation.soft_limit_notice',
      'reply_segmentation.prefer_sentence_break',
      'reply_segmentation.preserve_ascii_tokens',
      'reply_segmentation.merge_short_tail',
      'reply_segmentation.first_segment_humanize',
      'reply_segmentation.later_segment_humanize',
      'reply_segmentation.inter_segment_delay_s',
    ],
  },
  {
    id: 'connection',
    title: '协议端连接',
    eyebrow: 'Connection',
    description: '维护 Bot 与 NapCat 之间的 HTTP API 链路。视觉模型相关字段已并入「模型与 API」。',
    audience: '协议端地址或端口变化时修改。',
    impact: '影响 QQ 收发消息、群操作等基础链路。',
    restart: '建议保存后重启，避免旧连接继续驻留。',
    paths: ['napcat.api_url'],
  },
  {
    id: 'access',
    title: '权限与私聊',
    eyebrow: 'Access',
    description: '控制谁能登录后台、谁能私聊 Bot、谁被屏蔽。',
    audience: '换管理员、开放私聊、封禁用户时修改。',
    impact: '影响后台登录、管理权限、私聊入口和消息过滤。',
    restart: '涉及 token 或管理员时建议重启，并重新登录确认。',
    paths: ['admins', 'admin_token', 'allowed_private_users', 'group.blocked_users'],
  },
]

const TASK_ICONS: Record<ConfigTaskId, Component> = {
  model: KeyOutline,
  chat: ChatbubblesOutline,
  rhythm: PulseOutline,
  humanization: SparklesOutline,
  segmentation: CutOutline,
  connection: ServerOutline,
  access: HardwareChipOutline,
}

type AdvancedNavId = 'full' | 'json' | 'system_backup' | 'backup' | 'audit'
type NavId = ConfigTaskId | AdvancedNavId

interface AdvancedNavDefinition {
  id: AdvancedNavId
  label: string
  eyebrow: string
  description: string
  icon: Component
}

const ADVANCED_NAVS: AdvancedNavDefinition[] = [
  {
    id: 'full',
    label: '完整配置',
    eyebrow: 'Full Config',
    description: '未放进上方任务卡的低频字段，通常用于迁移、联调和深度维护。',
    icon: SettingsOutline,
  },
  {
    id: 'json',
    label: 'Raw JSON',
    eyebrow: 'Advanced JSON',
    description: '对未映射控件的字段做兜底批量编辑，保存前仍会进行结构校验。',
    icon: CodeSlashOutline,
  },
  {
    id: 'system_backup',
    label: '系统备份',
    eyebrow: 'System Backup',
    description: '管理 SQLite 数据库与配置文件的自动备份策略和历史记录。',
    icon: ServerOutline,
  },
  {
    id: 'backup',
    label: '配置快照',
    eyebrow: 'Config Snapshot',
    description: '查看最近几次可恢复快照，必要时回滚配置。',
    icon: CloudUploadOutline,
  },
  {
    id: 'audit',
    label: '保存审计',
    eyebrow: 'Audit Trail',
    description: '最近几次配置落盘摘要，看清谁动了哪些模块。',
    icon: TimeOutline,
  },
]

const path = ref('')
const formatMode = ref<'json' | 'legacy'>('json')
const migrationPending = ref(false)
const schema = ref<ConfigFieldSchema[]>([])
const values = ref<Record<string, any>>({})
const originalValues = ref<Record<string, any>>({})
const secretMasks = ref<Record<string, string>>({})
const rawJson = ref('')
const originalRawJson = ref('')
const showAdvanced = ref(false)
const fieldErrors = ref<Record<string, string>>({})
const compatibilityWarning = ref('')
const schemaWarning = ref('')
const loading = ref(true)
const refreshing = ref(false)
const saving = ref(false)
const previewLoading = ref(false)
const historyLoading = ref(false)
const backupLoading = ref(false)
const previewError = ref('')
const diffPreview = ref<{ summary: ConfigPreviewResult['summary'], changes: ConfigDiffChange[] } | null>(null)
const auditEntries = ref<ConfigAuditEntry[]>([])
const backupEntries = ref<ConfigBackupEntry[]>([])
const restoringBackupId = ref('')
const message = useMessage()

const modified = computed(() => (
  JSON.stringify(values.value) !== JSON.stringify(originalValues.value)
  || rawJson.value !== originalRawJson.value
))
const groupedSchema = computed(() => schema.value.filter(item => item.kind === 'object'))
const topLevelSchema = computed(() => schema.value.filter(item => item.kind !== 'object'))
const hasStructuredSchema = computed(() => schema.value.length > 0)
const parseStateLabel = computed(() => {
  if (compatibilityWarning.value) return 'Legacy API'
  return formatMode.value === 'legacy' ? 'Legacy TOML' : 'JSON'
})
const syncStateLabel = computed(() => (modified.value ? '未保存' : '已同步'))
const activeNav = ref<NavId>('model')
const taskPathSet = computed(() => new Set(CONFIG_TASKS.flatMap(task => task.paths)))
const allFields = computed(() => schema.value.flatMap(section => flattenFields(section)))

const isTaskNav = (id: NavId): id is ConfigTaskId =>
  CONFIG_TASKS.some(task => task.id === id)

const activeTask = computed(() =>
  isTaskNav(activeNav.value)
    ? (CONFIG_TASKS.find(task => task.id === activeNav.value) || CONFIG_TASKS[0])
    : null,
)
const activeAdvanced = computed(() =>
  isTaskNav(activeNav.value)
    ? null
    : ADVANCED_NAVS.find(item => item.id === activeNav.value) || null,
)
const humanizationSummaryCards = computed(() => {
  const humanization = values.value.humanization || {}
  const stateBoard = humanization.state_board || {}
  const streaming = humanization.streaming_segment || {}
  const pause = humanization.pause_then_extend || {}
  const plan = humanization.plan_then_utter || {}
  return [
    {
      label: '状态板',
      value: `${stateBoard.layout || 'head'} / ${stateBoard.granularity || 'fine'}`,
    },
    {
      label: 'Streaming',
      value: streaming.enabled ? '已显式开启' : '随档位决议',
    },
    {
      label: '追发',
      value: pause.enabled ? '已显式开启' : '随档位决议',
    },
    {
      label: 'Plan',
      value: plan.enabled
        ? `已开启${Array.isArray(plan.group_whitelist) && plan.group_whitelist.length ? ` · ${plan.group_whitelist.length} 群` : ''}`
        : '随档位决议',
    },
  ]
})

const activeTaskFields = computed(() =>
  activeTask.value
    ? activeTask.value.paths
        .map(taskPath => findFieldByPath(taskPath))
        .filter((field): field is ConfigFieldSchema => Boolean(field))
    : [],
)
const activeTaskBuckets = computed<ConfigSectionBucket[]>(() =>
  activeTask.value ? bucketFields(activeTaskFields.value) : [],
)

const advancedGroupedSchema = computed(() =>
  groupedSchema.value
    .map(section => pruneTaskFields(section))
    .filter((section): section is ConfigFieldSchema => Boolean(section)),
)
const advancedTopLevelSchema = computed(() =>
  topLevelSchema.value.filter(field => !taskPathSet.value.has(field.path)),
)
const advancedTopLevelBuckets = computed<ConfigSectionBucket[]>(() =>
  bucketFields(advancedTopLevelSchema.value),
)
const hiddenAdvancedModuleCount = computed(() =>
  advancedGroupedSchema.value.length + advancedTopLevelSchema.value.length,
)

const restartSummaryLabel = computed(() => {
  if (fieldsChangedWithRestartHint('required')) return '需要重启'
  if (fieldsChangedWithRestartHint('recommended')) return '建议重启'
  return '无需特别重启'
})
const restartSummaryType = computed<'success' | 'warning' | 'error'>(() => {
  if (fieldsChangedWithRestartHint('required')) return 'error'
  if (fieldsChangedWithRestartHint('recommended')) return 'warning'
  return 'success'
})

const configStatusItems = computed<ConfigStatusItem[]>(() => [
  { label: '保存状态', value: syncStateLabel.value, type: modified.value ? 'warning' : 'success' },
  { label: '配置路径', value: path.value || 'config/config.json', type: 'info' },
  { label: '解析模式', value: parseStateLabel.value, type: migrationPending.value || compatibilityWarning.value ? 'warning' : 'success' },
  { label: '生效提示', value: restartSummaryLabel.value, type: restartSummaryType.value },
])

const taskNavs = computed(() => CONFIG_TASKS.map((task) => {
  const fields = task.paths
    .map(taskPath => findFieldByPath(taskPath))
    .filter((field): field is ConfigFieldSchema => Boolean(field))
  const changedCount = fields.filter(field => fieldChanged(field)).length
  return {
    id: task.id,
    title: task.title,
    eyebrow: task.eyebrow,
    icon: TASK_ICONS[task.id],
    fieldCount: fields.length,
    changedCount,
  }
}))

const advancedNavs = computed(() => ADVANCED_NAVS.map((nav) => {
  let count = 0
  let changedCount = 0
  if (nav.id === 'full') {
    count = hiddenAdvancedModuleCount.value
    changedCount = advancedGroupedSchema.value.reduce((acc, section) => acc + countChangedFields(section), 0)
      + advancedTopLevelSchema.value.filter(field => fieldChanged(field)).length
  } else if (nav.id === 'json') {
    count = rawJson.value && rawJson.value !== originalRawJson.value ? 1 : 0
    changedCount = count
  } else if (nav.id === 'backup') {
    count = backupEntries.value.length
  } else if (nav.id === 'audit') {
    count = auditEntries.value.length
  }
  return {
    id: nav.id,
    title: nav.label,
    eyebrow: nav.eyebrow,
    icon: nav.icon,
    fieldCount: count,
    changedCount,
  }
}))

const totalChangedCount = computed(() => changedPaths().length)
const highRiskChangedPaths = computed(() =>
  changedPaths().filter(p => {
    const field = findFieldByPath(p)
    return field?.risk_level === 'danger'
  }),
)

onMounted(() => {
  applyRouteQueryNav()
  void loadConfig()
  void loadAuditHistory()
  void loadConfigBackups()
})

const route = useRoute()

watch(
  () => route.query.task,
  () => {
    if (route.path === '/config') applyRouteQueryNav()
  },
)

function applyRouteQueryNav() {
  const task = route.query.task
  if (typeof task !== 'string' || !task) return
  if (CONFIG_TASKS.some(item => item.id === task) || ADVANCED_NAVS.some(item => item.id === task)) {
    activeNav.value = task as NavId
  }
}

function normalizeConfigPath(rawPath: string | undefined): string {
  if (!rawPath) return 'config/config.json'
  return rawPath.replace(/\.toml$/i, '.json')
}

function isStructuredPayload(payload: unknown): payload is ConfigEditorPayload {
  if (!payload || typeof payload !== 'object') return false
  const data = payload as Record<string, unknown>
  return typeof data.path === 'string'
    && typeof data.editor === 'object'
    && data.editor !== null
}

function isLegacyPayload(payload: unknown): payload is LegacyConfigResponse {
  if (!payload || typeof payload !== 'object') return false
  const data = payload as Record<string, unknown>
  return typeof data.content === 'string'
}

function deepClone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value))
}

function decorateConfigSchema(items: ConfigFieldSchema[]): ConfigFieldSchema[] {
  return items.map((item) => {
    const hint = FIELD_UI_HINTS[item.path] || {}
    return {
      ...item,
      ...hint,
      label: hint.display_label || item.display_label || item.label,
      display_label: hint.display_label || item.display_label,
      description: item.description,
      help: hint.help || item.help || item.description,
      children: item.children ? decorateConfigSchema(item.children) : undefined,
    }
  })
}

function flattenFields(field: ConfigFieldSchema): ConfigFieldSchema[] {
  if (!field.children?.length) return [field]
  return [field, ...field.children.flatMap(child => flattenFields(child))]
}

function pruneTaskFields(field: ConfigFieldSchema): ConfigFieldSchema | null {
  if (taskPathSet.value.has(field.path)) return null
  if (field.kind !== 'object') return field
  const children = (field.children || [])
    .map(child => pruneTaskFields(child))
    .filter((child): child is ConfigFieldSchema => Boolean(child))
  if (!children.length) return null
  return { ...field, children }
}

function findFieldByPath(pathToFind: string): ConfigFieldSchema | undefined {
  return allFields.value.find(field => field.path === pathToFind)
}

function getValueByPath(source: Record<string, any>, dottedPath: string): any {
  const segments = dottedPath.split('.')
  let node: any = source
  for (const segment of segments) {
    if (!node || typeof node !== 'object') return undefined
    node = node[segment]
  }
  return node
}

function sameValue(left: any, right: any): boolean {
  return JSON.stringify(left ?? null) === JSON.stringify(right ?? null)
}

function changedPaths(): string[] {
  return allFields.value
    .filter(field => field.kind !== 'object')
    .map(field => field.path)
    .filter(fieldPath => !sameValue(getValueByPath(values.value, fieldPath), getValueByPath(originalValues.value, fieldPath)))
}

function fieldsChangedWithRestartHint(hint: 'recommended' | 'required'): boolean {
  return changedPaths().some((fieldPath) => {
    const field = findFieldByPath(fieldPath)
    return field?.restart_hint === hint
  })
}

function selectNav(id: NavId) {
  activeNav.value = id
}

function fieldChanged(field: ConfigFieldSchema): boolean {
  return !sameValue(getValueByPath(values.value, field.path), getValueByPath(originalValues.value, field.path))
}

function countChangedFields(field: ConfigFieldSchema): number {
  if (field.kind !== 'object') return fieldChanged(field) ? 1 : 0
  return (field.children || []).reduce((acc, child) => acc + countChangedFields(child), 0)
}

function setValueByPath(target: Record<string, any>, dottedPath: string, value: any) {
  const segments = dottedPath.split('.')
  let node: Record<string, any> = target
  for (let i = 0; i < segments.length - 1; i += 1) {
    const segment = segments[i]
    const current = node[segment]
    if (!current || typeof current !== 'object' || Array.isArray(current)) {
      node[segment] = {}
    }
    node = node[segment]
  }
  node[segments[segments.length - 1]] = value
}

function applyPayload(payload: ConfigEditorPayload) {
  const nextSchema = decorateConfigSchema(payload.editor?.schema || [])
  let nextValues = deepClone(payload.editor?.values || {})
  const nextRawJson = payload.advanced?.raw_json || JSON.stringify(nextValues, null, 2)

  if (Object.keys(nextValues).length === 0 && nextRawJson.trim()) {
    try {
      const parsed = JSON.parse(nextRawJson)
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        nextValues = deepClone(parsed as Record<string, any>)
      }
    } catch {
      // keep values from payload when raw_json is not valid JSON
    }
  }

  path.value = normalizeConfigPath(payload.path)
  formatMode.value = payload.format_mode || 'json'
  migrationPending.value = Boolean(payload.migration_pending)
  schema.value = nextSchema
  values.value = nextValues
  originalValues.value = deepClone(nextValues)
  secretMasks.value = payload.editor?.secret_masks || {}
  rawJson.value = nextRawJson
  originalRawJson.value = rawJson.value
  fieldErrors.value = {}
  compatibilityWarning.value = ''
  schemaWarning.value = ''
  clearPreview()

  if (nextSchema.length === 0) {
    schemaWarning.value = '当前接口未返回结构化 schema，已自动切换为高级 JSON 模式。'
    showAdvanced.value = true
  }
}

function applyLegacyPayload(payload: LegacyConfigResponse) {
  path.value = normalizeConfigPath(payload.path)
  formatMode.value = 'legacy'
  migrationPending.value = true
  schema.value = []
  values.value = {}
  originalValues.value = {}
  secretMasks.value = {}
  rawJson.value = payload.content || ''
  originalRawJson.value = rawJson.value
  showAdvanced.value = true
  fieldErrors.value = {}
  schemaWarning.value = ''
  compatibilityWarning.value = '检测到旧版 `/api/admin/config` 接口（仅返回文本内容）。请先重启 Bot 加载新版接口，否则无法使用结构化配置编辑。'
  clearPreview()
}

function clearPreview() {
  diffPreview.value = null
  previewError.value = ''
}

function applyFieldErrors(items?: Array<{ path: string, message: string }>) {
  fieldErrors.value = {}
  for (const item of items || []) {
    if (!item?.path) continue
    fieldErrors.value[item.path] = item.message || '字段校验失败'
  }
}

function confirmDiscardIfNeeded(actionLabel: string): boolean {
  if (!modified.value) return true
  return window.confirm(`当前有未保存修改，确认继续${actionLabel}吗？`)
}

async function loadConfig(silent = false, force = false) {
  if (!force && !confirmDiscardIfNeeded('刷新')) return

  if (silent) refreshing.value = true
  else loading.value = true

  try {
    const data = await api<unknown>('/api/admin/config')
    if (isStructuredPayload(data)) {
      applyPayload(data)
    } else if (isLegacyPayload(data)) {
      applyLegacyPayload(data)
      message.warning('后端仍是旧版配置接口，请重启 Bot 后再使用结构化编辑。')
    } else {
      throw new Error('invalid-config-api-payload')
    }
  } catch (error) {
    console.error('Failed to load config:', error)
    message.error('加载配置失败')
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

async function loadAuditHistory() {
  historyLoading.value = true
  try {
    const data = await api<ConfigAuditPayload>('/api/admin/config/history')
    auditEntries.value = Array.isArray(data.entries) ? data.entries : []
  } catch (error) {
    console.error('Failed to load config audit history:', error)
  } finally {
    historyLoading.value = false
  }
}

async function loadConfigBackups() {
  backupLoading.value = true
  try {
    const data = await api<ConfigBackupPayload>('/api/admin/config/backups')
    backupEntries.value = Array.isArray(data.entries) ? data.entries : []
  } catch (error) {
    console.error('Failed to load config backups:', error)
  } finally {
    backupLoading.value = false
  }
}

function resetDraft() {
  if (!confirmDiscardIfNeeded('重置草稿')) return
  values.value = deepClone(originalValues.value)
  rawJson.value = originalRawJson.value
  fieldErrors.value = {}
  clearPreview()
}

function handleFieldUpdate(payload: { path: string, value: any }) {
  setValueByPath(values.value, payload.path, payload.value)
  values.value = deepClone(values.value)
  rawJson.value = JSON.stringify(values.value, null, 2)
  delete fieldErrors.value[payload.path]
  clearPreview()
}

function handleFieldRevert(payload: { path: string }) {
  const original = getValueByPath(originalValues.value, payload.path)
  setValueByPath(values.value, payload.path, deepClone(original))
  values.value = deepClone(values.value)
  rawJson.value = JSON.stringify(values.value, null, 2)
  delete fieldErrors.value[payload.path]
  clearPreview()
}

watch(rawJson, (value, oldValue) => {
  if (value !== oldValue) clearPreview()
})

async function loadPreview() {
  if (compatibilityWarning.value) {
    message.warning('当前后端接口版本过旧，请先重启 Bot 后再预览配置变更。')
    return false
  }
  if (!modified.value) {
    clearPreview()
    message.info('当前没有需要预览的配置变更。')
    return false
  }

  previewLoading.value = true
  previewError.value = ''
  try {
    const data = await api<ConfigPreviewResult>('/api/admin/config/preview', {
      method: 'POST',
      body: showAdvanced.value
        ? { mode: 'advanced', raw_json: rawJson.value }
        : { mode: 'structured', values: values.value },
    })

    if (!data.ok) {
      applyFieldErrors(data.field_errors)
      previewError.value = data.error || '变更预览失败'
      message.error(previewError.value)
      diffPreview.value = null
      return false
    }

    fieldErrors.value = {}
    diffPreview.value = {
      summary: data.summary,
      changes: data.changes || [],
    }
    return true
  } catch (error) {
    console.error('Failed to preview config diff:', error)
    previewError.value = '变更预览失败'
    message.error(previewError.value)
    diffPreview.value = null
    return false
  } finally {
    previewLoading.value = false
  }
}

function formatAuditTime(value?: number) {
  if (!value) return '--'
  return new Date(value * 1000).toLocaleString('zh-CN', { hour12: false })
}

function changeTypeLabel(type: string) {
  if (type === 'added') return '新增'
  if (type === 'removed') return '移除'
  return '修改'
}

function changeTypeTag(type: string) {
  if (type === 'added') return 'success'
  if (type === 'removed') return 'error'
  return 'warning'
}

function backupTriggerLabel(trigger: string) {
  if (trigger === 'restore') return '恢复结果'
  if (trigger === 'pre_restore') return '恢复前备份'
  return '保存快照'
}

function backupTriggerTag(trigger: string) {
  if (trigger === 'restore') return 'success'
  if (trigger === 'pre_restore') return 'warning'
  return 'info'
}

function formatSnapshotSize(value?: number) {
  const size = Number(value || 0)
  if (size <= 0) return '--'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

async function save() {
  if (compatibilityWarning.value) {
    message.warning('当前后端接口版本过旧，请先重启 Bot 后再保存结构化配置。')
    return
  }
  const previewOk = await loadPreview()
  if (!previewOk) return
  if (diffPreview.value?.summary.total) {
    const riskText = highRiskChangedPaths.value.length
      ? `\n包含高风险字段：${highRiskChangedPaths.value.join('、')}`
      : ''
    const restartText = restartSummaryLabel.value !== '无需特别重启'
      ? `\n生效提示：${restartSummaryLabel.value}`
      : ''
    const confirmed = window.confirm(
      `本次将写入 ${diffPreview.value.summary.total} 项变更，涉及 ${diffPreview.value.summary.top_level_count} 个模块。${riskText}${restartText}\n确认继续保存吗？`,
    )
    if (!confirmed) return
  }
  const restartAfterSave = restartSummaryLabel.value
  saving.value = true
  fieldErrors.value = {}

  try {
    const data = await api<ConfigSaveResult & {
      field_errors?: Array<{ path: string, message: string }>
      error?: string
      ok?: boolean
    }>('/api/admin/config', {
      method: 'POST',
      body: showAdvanced.value
        ? { mode: 'advanced', raw_json: rawJson.value }
        : { mode: 'structured', values: values.value },
    })

    if (!data.ok) {
      applyFieldErrors(data.field_errors)
      message.error(data.error || '保存失败')
      return
    }

    applyPayload(data)
    if (data.diff) {
      diffPreview.value = {
        summary: data.diff.summary,
        changes: data.diff.changes || [],
      }
    }
    if (data.audit_entry) {
      auditEntries.value = [data.audit_entry, ...auditEntries.value.filter(item => item.id !== data.audit_entry?.id)].slice(0, 8)
    } else {
      void loadAuditHistory()
    }
    if (data.backup_entry) {
      backupEntries.value = [data.backup_entry, ...backupEntries.value.filter(item => item.id !== data.backup_entry?.id)].slice(0, 8)
    } else {
      void loadConfigBackups()
    }
    if (restartAfterSave !== '无需特别重启') {
      message.warning(`${data.message || '已保存'}，${restartAfterSave}后完全生效。`)
    } else {
      message.success(data.message || '已保存')
    }
  } catch (error) {
    console.error('Failed to save config:', error)
    message.error('保存失败')
  } finally {
    saving.value = false
  }
}

async function restoreBackup(entry: ConfigBackupEntry) {
  if (compatibilityWarning.value) {
    message.warning('当前后端接口版本过旧，请先重启 Bot 后再使用配置恢复。')
    return
  }
  if (!confirmDiscardIfNeeded('恢复快照')) return
  const confirmed = window.confirm(
    `确认恢复 ${formatAuditTime(entry.created_at)} 的配置快照吗？这会覆盖当前 config/config.json，并追加新的审计记录。`,
  )
  if (!confirmed) return

  restoringBackupId.value = entry.id
  try {
    const data = await api<ConfigRestoreResult & {
      error?: string
      ok?: boolean
    }>('/api/admin/config/restore', {
      method: 'POST',
      body: { backup_id: entry.id },
    })

    if (!data.ok) {
      message.error(data.error || '恢复快照失败')
      return
    }

    applyPayload(data)
    if (data.diff) {
      diffPreview.value = {
        summary: data.diff.summary,
        changes: data.diff.changes || [],
      }
    }
    if (data.audit_entry) {
      auditEntries.value = [data.audit_entry, ...auditEntries.value.filter(item => item.id !== data.audit_entry?.id)].slice(0, 8)
    } else {
      void loadAuditHistory()
    }
    void loadConfigBackups()
    message.success(data.message || '已恢复配置快照')
  } catch (error) {
    console.error('Failed to restore config backup:', error)
    message.error('恢复快照失败')
  } finally {
    restoringBackupId.value = ''
  }
}
</script>

<template>
  <AppPage
    title="配置"
    eyebrow="Runtime Config"
    description="左侧选择要修改的配置组——日常配置在上、高级维护在下；右侧只显示当前选中项的字段，避免一次铺一整片。"
  >
    <template #action>
      <NSpace align="center" :size="10">
        <NButton secondary :loading="refreshing" @click="loadConfig(true)">
          <template #icon>
            <NIcon :component="RefreshOutline" />
          </template>
          刷新配置
        </NButton>
        <RestartBotButton />
      </NSpace>
    </template>

    <ConfigStatusStrip :items="configStatusItems" />

    <NSkeleton v-if="loading" :repeat="10" text />

    <template v-else-if="path">
      <NAlert
        v-if="compatibilityWarning"
        type="warning"
        title="接口版本不一致"
        class="config-alert"
      >
        {{ compatibilityWarning }}
      </NAlert>

      <NAlert
        v-if="schemaWarning"
        type="info"
        title="结构化模型不可用"
        class="config-alert"
      >
        {{ schemaWarning }}
      </NAlert>

      <NAlert
        v-if="Object.keys(fieldErrors).length > 0"
        type="error"
        title="配置校验未通过"
        class="config-alert"
      >
        请根据字段下方提示修正后再保存。
      </NAlert>

      <div class="config-shell">
        <aside class="config-shell__rail">
          <div class="config-rail-group">
            <div class="config-rail-group__head">
              <span class="config-rail-group__eyebrow">日常配置</span>
              <NTag size="small" round>
                {{ taskNavs.length }}
              </NTag>
            </div>
            <button
              v-for="nav in taskNavs"
              :key="nav.id"
              type="button"
              class="config-nav-item"
              :class="{ 'config-nav-item--active': activeNav === nav.id }"
              @click="selectNav(nav.id)"
            >
              <span class="config-nav-item__icon">
                <NIcon :component="nav.icon" :size="16" />
              </span>
              <span class="config-nav-item__body">
                <span class="config-nav-item__title">{{ nav.title }}</span>
                <span class="config-nav-item__meta">
                  <span>{{ nav.eyebrow }}</span>
                  <span class="config-nav-item__sep">·</span>
                  <span>{{ nav.fieldCount }} 项</span>
                </span>
              </span>
              <span v-if="nav.changedCount" class="config-nav-item__pip">
                {{ nav.changedCount }}
              </span>
              <NIcon :component="ChevronForwardOutline" class="config-nav-item__chevron" :size="14" />
            </button>
          </div>

          <div class="config-rail-group config-rail-group--advanced">
            <div class="config-rail-group__head">
              <span class="config-rail-group__eyebrow">高级维护</span>
              <NTag size="small" round>
                {{ advancedNavs.length }}
              </NTag>
            </div>
            <button
              v-for="nav in advancedNavs"
              :key="nav.id"
              type="button"
              class="config-nav-item config-nav-item--advanced"
              :class="{ 'config-nav-item--active': activeNav === nav.id }"
              @click="selectNav(nav.id)"
            >
              <span class="config-nav-item__icon">
                <NIcon :component="nav.icon" :size="16" />
              </span>
              <span class="config-nav-item__body">
                <span class="config-nav-item__title">{{ nav.title }}</span>
                <span class="config-nav-item__meta">
                  <span>{{ nav.eyebrow }}</span>
                  <span v-if="nav.fieldCount" class="config-nav-item__sep">·</span>
                  <span v-if="nav.fieldCount">{{ nav.fieldCount }} 项</span>
                </span>
              </span>
              <span v-if="nav.changedCount" class="config-nav-item__pip">
                {{ nav.changedCount }}
              </span>
              <NIcon :component="ChevronForwardOutline" class="config-nav-item__chevron" :size="14" />
            </button>
          </div>
        </aside>

        <section class="config-shell__stage">
          <PageToolbar class="config-stage__toolbar">
            <template #left>
              <NTag round size="small">
                {{ path || 'config/config.json' }}
              </NTag>
              <StateBadge
                :status="modified ? 'warning' : 'success'"
                :label="modified ? `未保存 · ${totalChangedCount} 项` : '已同步'"
                compact
              />
              <StateBadge
                v-if="restartSummaryLabel !== '无需特别重启'"
                :status="restartSummaryType"
                :label="restartSummaryLabel"
                compact
              />
              <NTag v-if="migrationPending" round size="small" type="warning">
                首次保存将迁移为 JSON
              </NTag>
            </template>
            <template #right>
              <NButton secondary size="small" :loading="previewLoading" :disabled="!modified || !!compatibilityWarning" @click="loadPreview">
                <template #icon>
                  <NIcon :component="DocumentTextOutline" />
                </template>
                预览变更
              </NButton>
              <NButton secondary size="small" :disabled="!modified" @click="resetDraft">
                撤销修改
              </NButton>
              <NButton type="primary" size="small" :loading="saving" :disabled="loading || !modified || !!compatibilityWarning" @click="save">
                <template #icon>
                  <NIcon :component="SaveOutline" />
                </template>
                保存配置
              </NButton>
            </template>
          </PageToolbar>

          <div class="config-stage__body">
            <template v-if="activeTask">
              <div class="config-stage__hero">
                <span class="config-stage__hero-eyebrow">{{ activeTask.eyebrow }}</span>
                <h2 class="config-stage__hero-title">{{ activeTask.title }}</h2>
                <p class="config-stage__hero-desc">{{ activeTask.description }}</p>
              </div>

              <div class="config-task-guide">
                <div class="config-task-guide__cell">
                  <span>适合谁改</span>
                  <p>{{ activeTask.audience }}</p>
                </div>
                <div class="config-task-guide__cell">
                  <span>改了影响什么</span>
                  <p>{{ activeTask.impact }}</p>
                </div>
                <div class="config-task-guide__cell">
                  <span>生效建议</span>
                  <p>{{ activeTask.restart }}</p>
                </div>
              </div>

              <div v-if="activeTask.id === 'humanization'" class="config-task-guide">
                <div
                  v-for="card in humanizationSummaryCards"
                  :key="card.label"
                  class="config-task-guide__cell"
                >
                  <span>{{ card.label }}</span>
                  <p>{{ card.value }}</p>
                </div>
              </div>

              <template v-if="activeTaskBuckets.length">
                <AppPanelSection
                  v-for="bucket in activeTaskBuckets"
                  :key="bucket.id"
                  :eyebrow="bucket.label.eyebrow"
                  :title="bucket.label.title"
                  :description="bucket.label.description"
                >
                  <div class="config-section-stack">
                    <ConfigField
                      v-for="field in bucket.fields"
                      :key="field.path"
                      :field="field"
                      :values="values"
                      :original-values="originalValues"
                      :errors="fieldErrors"
                      :secret-masks="secretMasks"
                      @update="handleFieldUpdate"
                      @revert="handleFieldRevert"
                    />
                  </div>
                </AppPanelSection>
              </template>

              <EmptyState
                v-else
                compact
                title="当前任务暂无可视化字段"
                description="后端 schema 暂未提供这些字段，可在「完整配置」中查看。"
                :icon="SettingsOutline"
              />
            </template>

            <template v-else-if="activeAdvanced">
              <div class="config-stage__hero">
                <span class="config-stage__hero-eyebrow">{{ activeAdvanced.eyebrow }}</span>
                <h2 class="config-stage__hero-title">{{ activeAdvanced.label }}</h2>
                <p class="config-stage__hero-desc">{{ activeAdvanced.description }}</p>
              </div>

              <template v-if="activeAdvanced.id === 'full'">
                <div v-if="advancedTopLevelBuckets.length || advancedGroupedSchema.length" class="config-full-stack">
                  <AppPanelSection
                    v-for="bucket in advancedTopLevelBuckets"
                    :key="`full-top-${bucket.id}`"
                    :eyebrow="bucket.label.eyebrow"
                    :title="bucket.label.title"
                    :description="bucket.label.description"
                  >
                    <div class="config-section-stack">
                      <ConfigField
                        v-for="field in bucket.fields"
                        :key="field.path"
                        :field="field"
                        :values="values"
                        :original-values="originalValues"
                        :errors="fieldErrors"
                        :secret-masks="secretMasks"
                        @update="handleFieldUpdate"
                        @revert="handleFieldRevert"
                      />
                    </div>
                  </AppPanelSection>

                  <AppPanelSection
                    v-for="section in advancedGroupedSchema"
                    :key="section.path"
                    :eyebrow="section.path.toUpperCase()"
                    :title="section.display_label || section.label"
                    :description="section.help || section.description || '该模块保留完整能力，但默认折叠以减少打扰。'"
                  >
                    <div class="config-section-stack">
                      <ConfigField
                        v-for="child in section.children || []"
                        :key="child.path"
                        :field="child"
                        :values="values"
                        :original-values="originalValues"
                        :errors="fieldErrors"
                        :secret-masks="secretMasks"
                        @update="handleFieldUpdate"
                        @revert="handleFieldRevert"
                      />
                    </div>
                  </AppPanelSection>
                </div>

                <EmptyState
                  v-else
                  compact
                  title="没有额外低频字段"
                  description="当前结构化配置都已经被左侧任务承载。"
                  :icon="DocumentTextOutline"
                />
              </template>

              <template v-else-if="activeAdvanced.id === 'json'">
                <AppPanelSection eyebrow="Raw JSON" title="高级 JSON 兜底" description="只在需要批量迁移或编辑未映射字段时开启；日常建议使用左侧任务卡。">
                  <div class="config-json-toggle">
                    <div>
                      <strong>启用 Raw JSON 编辑</strong>
                      <p>开启后将通过 raw JSON 提交保存；关闭后保存仍走结构化值。</p>
                    </div>
                    <NSwitch v-model:value="showAdvanced" :disabled="!hasStructuredSchema">
                      <template #checked>
                        已启用
                      </template>
                      <template #unchecked>
                        未启用
                      </template>
                    </NSwitch>
                  </div>

                  <NInput
                    v-if="showAdvanced"
                    v-model:value="rawJson"
                    type="textarea"
                    :autosize="{ minRows: 18, maxRows: 36 }"
                    class="config-editor__textarea"
                  />

                  <NTag v-else round size="small" type="info">
                    当前以结构化控件保存，开启上方开关即可显示 JSON
                  </NTag>
                </AppPanelSection>
              </template>

              <template v-else-if="activeAdvanced.id === 'system_backup'">
                <ConfigSystemBackup />
              </template>

              <template v-else-if="activeAdvanced.id === 'backup'">
                <AppPanelSection eyebrow="Backup" title="可恢复快照" description="保存或恢复配置后，最近几次快照会保留在这里，便于回滚。">
                  <template #aside>
                    <NButton tertiary size="small" :loading="backupLoading" @click="loadConfigBackups">
                      刷新快照
                    </NButton>
                  </template>

                  <div v-if="backupEntries.length" class="config-backup-list">
                    <div
                      v-for="entry in backupEntries"
                      :key="entry.id"
                      class="config-backup-item"
                    >
                      <div class="config-backup-item__head">
                        <div>
                          <strong>{{ formatAuditTime(entry.created_at) }}</strong>
                          <p>{{ entry.config_path }} · {{ backupTriggerLabel(entry.trigger) }} · {{ formatSnapshotSize(entry.size_bytes) }}</p>
                        </div>
                        <NSpace align="center" :size="8">
                          <NTag size="small" round :type="backupTriggerTag(entry.trigger)">
                            {{ backupTriggerLabel(entry.trigger) }}
                          </NTag>
                          <NTag size="small" round type="info">
                            {{ entry.summary.total }} 项
                          </NTag>
                          <NButton
                            tertiary
                            size="small"
                            :loading="restoringBackupId === entry.id"
                            @click="restoreBackup(entry)"
                          >
                            恢复此快照
                          </NButton>
                        </NSpace>
                      </div>

                      <p v-if="entry.note" class="config-backup-item__note">
                        {{ entry.note }}
                      </p>

                      <div v-if="entry.summary.top_levels.length" class="config-backup-item__modules">
                        <NTag v-for="group in entry.summary.top_levels" :key="`${entry.id}-${group}`" size="small" round>
                          {{ group }}
                        </NTag>
                      </div>
                    </div>
                  </div>

                  <EmptyState
                    v-else
                    compact
                    title="还没有可恢复快照"
                    description="保存或恢复配置后，这里会保留最近几次快照，便于后续回滚。"
                    :icon="FolderOpenOutline"
                  />
                </AppPanelSection>
              </template>

              <template v-else-if="activeAdvanced.id === 'audit'">
                <AppPanelSection eyebrow="Audit Trail" title="保存审计" description="最近几次配置落盘摘要，看清谁动了哪些模块。">
                  <template #aside>
                    <NButton tertiary size="small" :loading="historyLoading" @click="loadAuditHistory">
                      刷新记录
                    </NButton>
                  </template>

                  <div v-if="auditEntries.length" class="config-audit-list">
                    <div
                      v-for="entry in auditEntries"
                      :key="entry.id"
                      class="config-audit-item"
                    >
                      <div class="config-audit-item__head">
                        <div>
                          <strong>{{ formatAuditTime(entry.saved_at) }}</strong>
                          <p>{{ entry.config_path }} · {{ entry.mode === 'advanced' ? '高级 JSON' : '结构化保存' }}</p>
                        </div>
                        <div class="config-audit-item__tags">
                          <NTag size="small" round type="warning">
                            {{ entry.summary.total }} 项
                          </NTag>
                          <NTag size="small" round type="info">
                            {{ entry.summary.top_level_count }} 个模块
                          </NTag>
                        </div>
                      </div>

                      <div v-if="entry.summary.top_levels.length" class="config-audit-item__modules">
                        <NTag v-for="group in entry.summary.top_levels" :key="`${entry.id}-${group}`" size="small" round>
                          {{ group }}
                        </NTag>
                      </div>

                      <div v-if="entry.changes.length" class="config-audit-item__changes">
                        <div
                          v-for="change in entry.changes.slice(0, 4)"
                          :key="`${entry.id}-${change.path}-${change.change_type}`"
                          class="config-audit-item__change"
                        >
                          <span>{{ change.path }}</span>
                          <span>{{ change.before_display }} → {{ change.after_display }}</span>
                        </div>
                      </div>
                    </div>
                  </div>

                  <EmptyState
                    v-else
                    compact
                    title="还没有保存审计记录"
                    description="首次保存配置后，这里会显示最近几次落盘摘要。"
                    :icon="TimeOutline"
                  />
                </AppPanelSection>
              </template>
            </template>

            <AppPanelSection
              v-if="diffPreview || previewError"
              eyebrow="Diff Preview"
              title="保存前变更预览"
              description="服务端校验后的写盘差异；敏感字段只展示遮罩结果。"
              class="config-diff-block"
            >
              <template #aside>
                <NButton tertiary size="small" :loading="previewLoading" :disabled="!modified || !!compatibilityWarning" @click="loadPreview">
                  刷新预览
                </NButton>
              </template>

              <div v-if="diffPreview" class="config-preview">
                <div class="config-preview__summary">
                  <NTag size="small" round type="warning">
                    {{ diffPreview.summary.total }} 项变更
                  </NTag>
                  <NTag size="small" round type="success">
                    {{ diffPreview.summary.added }} 新增
                  </NTag>
                  <NTag size="small" round type="error">
                    {{ diffPreview.summary.removed }} 移除
                  </NTag>
                  <NTag size="small" round type="info">
                    {{ diffPreview.summary.changed }} 修改
                  </NTag>
                </div>

                <div v-if="diffPreview.summary.top_levels.length" class="config-preview__modules">
                  <span>涉及模块</span>
                  <div class="config-preview__module-tags">
                    <NTag v-for="group in diffPreview.summary.top_levels" :key="group" size="small" round>
                      {{ group }}
                    </NTag>
                  </div>
                </div>

                <div class="config-preview__list">
                  <div
                    v-for="change in diffPreview.changes"
                    :key="`${change.path}-${change.change_type}`"
                    class="config-preview__item"
                  >
                    <div class="config-preview__item-head">
                      <strong>{{ change.path }}</strong>
                      <NTag size="small" round :type="changeTypeTag(change.change_type)">
                        {{ changeTypeLabel(change.change_type) }}
                      </NTag>
                    </div>
                    <div class="config-preview__item-body">
                      <span>{{ change.before_display }}</span>
                      <span class="config-preview__arrow">→</span>
                      <span>{{ change.after_display }}</span>
                    </div>
                  </div>
                </div>
              </div>

              <NAlert
                v-else-if="previewError"
                type="error"
                title="预览失败"
                class="config-alert"
              >
                {{ previewError }}
              </NAlert>
            </AppPanelSection>
          </div>
        </section>
      </div>
    </template>

    <EmptyState
      v-else
      title="没有检测到配置文件路径"
      description="当前接口没有返回有效路径，暂时无法展示编辑器。"
      :icon="FolderOpenOutline"
    />
  </AppPage>
</template>

<style scoped>
.config-alert {
  margin-bottom: 12px;
}

.config-shell {
  display: grid;
  grid-template-columns: 264px minmax(0, 1fr);
  gap: 16px;
  align-items: stretch;
}

.config-shell__rail {
  position: sticky;
  top: 12px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  align-self: flex-start;
  max-height: calc(100vh - 32px);
  padding: 14px;
  overflow-y: auto;
  border: 1px solid var(--om-border);
  border-radius: 18px;
  background: var(--om-surface-2);
  box-shadow: var(--om-shadow-sm);
}

.config-rail-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.config-rail-group--advanced {
  padding-top: 12px;
  border-top: 1px dashed color-mix(in srgb, var(--om-border) 80%, transparent);
}

.config-rail-group__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 4px 4px 6px;
}

.config-rail-group__eyebrow {
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}

.config-nav-item {
  position: relative;
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr) auto auto;
  gap: 10px;
  align-items: center;
  width: 100%;
  padding: 9px 10px;
  border: 1px solid transparent;
  border-radius: 12px;
  color: inherit;
  background: transparent;
  cursor: pointer;
  text-align: left;
  transition:
    background-color 0.16s ease,
    border-color 0.16s ease,
    color 0.16s ease;
}

.config-nav-item:hover {
  background: color-mix(in srgb, var(--om-surface) 70%, transparent);
}

.config-nav-item--active {
  border-color: color-mix(in srgb, var(--om-info) 32%, var(--om-border));
  background: color-mix(in srgb, var(--om-info) 8%, var(--om-surface));
  box-shadow: inset 3px 0 0 0 var(--om-info);
}

.config-nav-item__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 9px;
  background: color-mix(in srgb, var(--om-surface) 60%, transparent);
  color: var(--om-text-2);
}

.config-nav-item--active .config-nav-item__icon {
  background: color-mix(in srgb, var(--om-info) 14%, transparent);
  color: var(--om-info);
}

.config-nav-item__body {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.config-nav-item__title {
  overflow: hidden;
  color: var(--om-text-1);
  font-size: 13px;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.config-nav-item__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  color: var(--om-text-3);
  font-size: 11px;
}

.config-nav-item__sep {
  opacity: 0.6;
}

.config-nav-item__pip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 6px;
  border-radius: 999px;
  color: #fff;
  background: var(--om-warning);
  font-size: 11px;
  font-weight: 800;
  line-height: 1;
}

.config-nav-item__chevron {
  color: var(--om-text-3);
  opacity: 0.5;
}

.config-nav-item--active .config-nav-item__chevron {
  color: var(--om-info);
  opacity: 1;
}

.config-shell__stage {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
  padding: 18px;
  border: 1px solid var(--om-border);
  border-radius: 18px;
  background: var(--om-surface);
  box-shadow: var(--om-shadow-sm);
}

.config-stage__toolbar {
  margin: 0;
}

.config-stage__body {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.config-stage__hero {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.config-stage__hero-eyebrow {
  color: var(--om-text-3);
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}

.config-stage__hero-title {
  margin: 0;
  color: var(--om-text-1);
  font-size: 20px;
  font-weight: 800;
}

.config-stage__hero-desc {
  margin: 6px 0 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.65;
}

.config-task-guide {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.config-task-guide__cell {
  padding: 12px;
  border: 1px solid color-mix(in srgb, var(--om-info) 14%, var(--om-border));
  border-radius: 14px;
  background: color-mix(in srgb, var(--om-info) 5%, transparent);
}

.config-task-guide__cell span {
  color: var(--om-text-3);
  font-size: 12px;
  font-weight: 750;
}

.config-task-guide__cell p {
  margin: 6px 0 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.65;
}

.config-section-stack {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.config-full-stack {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.config-json-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px;
  border: 1px solid color-mix(in srgb, var(--om-info) 14%, var(--om-border));
  border-radius: 14px;
  background: color-mix(in srgb, var(--om-info) 5%, transparent);
  margin-bottom: 12px;
}

.config-json-toggle strong {
  color: var(--om-text-1);
  font-size: 14px;
}

.config-json-toggle p {
  margin: 6px 0 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.6;
}

.config-editor__textarea:deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Monaco, Consolas, monospace;
  font-size: 13px;
  line-height: 1.75;
}

.config-diff-block :deep(.om-card) {
  border-color: color-mix(in srgb, var(--om-warning) 22%, var(--om-border));
  background: color-mix(in srgb, var(--om-warning) 5%, var(--om-surface));
}

.config-preview,
.config-audit-list,
.config-backup-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.config-preview__summary,
.config-preview__module-tags,
.config-audit-item__tags,
.config-audit-item__modules {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.config-preview__modules {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.config-preview__modules > span {
  color: var(--om-text-3);
  font-size: 12px;
}

.config-preview__list,
.config-audit-item__changes {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.config-preview__item,
.config-audit-item,
.config-backup-item {
  padding: 14px;
  border: 1px solid var(--om-border);
  border-radius: 14px;
  background: var(--om-surface);
}

.config-preview__item {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.config-preview__item-head,
.config-audit-item__head,
.config-backup-item__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.config-preview__item-head strong,
.config-audit-item__head strong,
.config-backup-item__head strong {
  color: var(--om-text-1);
  font-size: 14px;
  font-weight: 700;
}

.config-audit-item__head p,
.config-backup-item__head p {
  margin: 6px 0 0;
  color: var(--om-text-3);
  font-size: 12px;
  line-height: 1.55;
}

.config-backup-item__note {
  margin: 10px 0 0;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.65;
}

.config-backup-item__modules {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.config-preview__item-body,
.config-audit-item__change {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  gap: 10px;
  align-items: flex-start;
  color: var(--om-text-2);
  font-size: 13px;
  line-height: 1.65;
}

.config-audit-item__change {
  grid-template-columns: minmax(180px, 0.8fr) minmax(0, 1.2fr);
}

.config-audit-item__change span:first-child {
  color: var(--om-text-1);
  font-weight: 600;
}

.config-preview__item-body span,
.config-audit-item__change span {
  min-width: 0;
  word-break: break-word;
}

.config-preview__arrow {
  color: var(--om-text-3);
}

@media (max-width: 1180px) {
  .config-shell {
    grid-template-columns: 232px minmax(0, 1fr);
  }

  .config-task-guide {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 960px) {
  .config-shell {
    grid-template-columns: 1fr;
  }

  .config-shell__rail {
    position: static;
    max-height: none;
  }
}

@media (max-width: 760px) {
  .config-shell__stage {
    padding: 14px;
  }

  .config-json-toggle,
  .config-preview__item-head,
  .config-audit-item__head,
  .config-backup-item__head,
  .config-preview__item-body,
  .config-audit-item__change {
    flex-direction: column;
    align-items: stretch;
  }

  .config-preview__item-body,
  .config-audit-item__change {
    display: flex;
  }
}
</style>









