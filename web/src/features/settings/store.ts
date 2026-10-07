import { reactive, watch } from 'vue'

import { apiErrorMessage, apiRequest, isApiError } from '@/api/client'
import { cloneJson, isRecord } from '@/api/guards'
import { currentSessionEpoch, expireAdminSession, isCurrentSessionEpoch, sessionState } from '@/app/session'
import type { EditableConfig, EditableModelProfile, ModelProfile, SettingsSnapshot, TaskName } from '@/api/types'
import { isContactSettings } from './contactRules'

export interface SettingsState {
  snapshot: SettingsSnapshot | null
  draft: EditableConfig | null
  baseRevision: number | null
  advancedText: string
  advancedBaseline: string
  advancedEdited: boolean
  selectedProfile: string
  loading: boolean
  saving: boolean
  error: string
  notice: string
  pendingDeleteProfile: string | null
  reloadPending: boolean
}

export const settingsState = reactive<SettingsState>({
  snapshot: null,
  draft: null,
  baseRevision: null,
  advancedText: '',
  advancedBaseline: '',
  advancedEdited: false,
  selectedProfile: '',
  loading: false,
  saving: false,
  error: '',
  notice: '',
  pendingDeleteProfile: null,
  reloadPending: false,
})

let loadSequence = 0
let saveSequence = 0

function serialized(config: EditableConfig): string {
  return JSON.stringify(config, null, 2)
}

function setAdvancedFromDraft(): void {
  if (!settingsState.draft) return
  const value = serialized(settingsState.draft)
  settingsState.advancedText = value
  settingsState.advancedBaseline = value
  settingsState.advancedEdited = false
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function isOptionalBoundedString(value: unknown, maxLength: number): boolean {
  return value === undefined || (typeof value === 'string' && value.length <= maxLength)
}

function isGroupModeMap(value: unknown): value is EditableConfig['group_modes'] {
  if (!isRecord(value)) return false
  const entries = Object.entries(value)
  if (entries.length > 128) return false
  const exactGroupId = /^[A-Za-z0-9_-]{1,64}$/
  const modes = ['active', 'silent', 'off']
  return entries.every(([groupId, mode]) => exactGroupId.test(groupId) && typeof mode === 'string' && modes.includes(mode))
}

function isGroupProfileMap(value: unknown): value is EditableConfig['group_profiles'] {
  if (!isRecord(value)) return false
  const entries = Object.entries(value)
  if (entries.length > 128) return false
  const exactGroupId = /^[A-Za-z0-9_-]{1,64}$/
  const replyStyles = ['default', 'gentle', 'playful', 'concise', 'energetic', 'steady']
  return entries.every(([groupId, profile]) => {
    if (!exactGroupId.test(groupId) || !isRecord(profile)) return false
    if (Object.keys(profile).some((key) => key !== 'reply_style' && key !== 'custom_prompt')) return false
    if (profile.reply_style !== undefined && profile.reply_style !== null
      && (typeof profile.reply_style !== 'string' || !replyStyles.includes(profile.reply_style))) return false
    return profile.custom_prompt === undefined || profile.custom_prompt === null
      || (typeof profile.custom_prompt === 'string' && profile.custom_prompt.length <= 6000)
  })
}

export function isGregorianMonthDay(value: unknown): value is string {
  if (typeof value !== 'string' || !/^\d{2}-\d{2}$/.test(value)) return false
  const month = Number(value.slice(0, 2))
  const day = Number(value.slice(3, 5))
  return new Date(Date.UTC(2000, month - 1, day)).toISOString().slice(5, 10) === value
}

function isCalendarWeekdayRule(value: unknown): value is {month: number; ordinal: number; weekday: number} {
  if (!isRecord(value)
    || Object.keys(value).length !== 3
    || Object.keys(value).some((key) => !['month', 'ordinal', 'weekday'].includes(key))) return false
  const { month, ordinal, weekday } = value
  return typeof month === 'number' && Number.isInteger(month) && month >= 1 && month <= 12
    && typeof ordinal === 'number' && Number.isInteger(ordinal) && ordinal >= -5 && ordinal <= 5 && ordinal !== 0
    && typeof weekday === 'number' && Number.isInteger(weekday) && weekday >= 0 && weekday <= 6
}

type CalendarLunarRule =
  | {kind: 'fixed'; month: number; day: number; leap_month: boolean}
  | {kind: 'year_eve'}

function isCalendarLunarRule(value: unknown): value is CalendarLunarRule {
  if (!isRecord(value)) return false
  if (value.kind === 'year_eve') return Object.keys(value).length === 1
  return value.kind === 'fixed'
    && Object.keys(value).length === 4
    && typeof value.month === 'number' && Number.isInteger(value.month) && value.month >= 1 && value.month <= 12
    && typeof value.day === 'number' && Number.isInteger(value.day) && value.day >= 1 && value.day <= 30
    && typeof value.leap_month === 'boolean'
}

function isGroupCalendarEvents(value: unknown): value is EditableConfig['group_calendar_events'] {
  if (!isRecord(value)) return false
  const entries = Object.entries(value)
  if (entries.length > 128) return false
  const exactId = /^[A-Za-z0-9_-]{1,64}$/
  const categories = ['birthday', 'anniversary', 'special_day']
  const subjectKinds = ['bot', 'member', 'group']
  return entries.every(([groupId, events]) => {
    if (!exactId.test(groupId) || !Array.isArray(events) || events.length > 64) return false
    return events.every((event) => {
      if (!isRecord(event)) return false
      if (Object.keys(event).some((key) => ![
        'date', 'weekday', 'lunar', 'name', 'category', 'subject_kind', 'subject_id',
      ].includes(key))) return false
      const hasDate = event.date !== undefined && event.date !== null
      const hasWeekday = event.weekday !== undefined && event.weekday !== null
      const hasLunar = event.lunar !== undefined && event.lunar !== null
      if (Number(hasDate) + Number(hasWeekday) + Number(hasLunar) !== 1
        || (hasDate && !isGregorianMonthDay(event.date))
        || (hasWeekday && !isCalendarWeekdayRule(event.weekday))
        || (hasLunar && !isCalendarLunarRule(event.lunar))
        || typeof event.name !== 'string' || !event.name.trim() || event.name.length > 80
        || typeof event.category !== 'string' || !categories.includes(event.category)
        || typeof event.subject_kind !== 'string' || !subjectKinds.includes(event.subject_kind)) return false
      const hasSubjectId = typeof event.subject_id === 'string'
        && exactId.test(event.subject_id)
      if (event.subject_kind === 'member') return hasSubjectId
      return event.subject_id === undefined || event.subject_id === null
    })
  })
}

function isKnownBotIds(value: unknown): value is string[] {
  if (!Array.isArray(value) || value.length > 128) return false
  const exactUserId = /^[A-Za-z0-9_-]{1,64}$/
  return value.every((item) => typeof item === 'string' && exactUserId.test(item))
    && new Set(value).size === value.length
}

function isExactGroupIds(value: unknown): value is string[] {
  if (!Array.isArray(value) || value.length > 128) return false
  const exactGroupId = /^[A-Za-z0-9_-]{1,64}$/
  return value.every((item) => typeof item === 'string' && exactGroupId.test(item))
    && new Set(value).size === value.length
}

function isEditableModelProfile(value: unknown): value is EditableModelProfile {
  if (!isRecord(value)) return false
  const formats = ['openai_chat', 'openai_responses', 'anthropic', 'deepseek']
  const reasoning = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']
  const tokenParameters = ['max_tokens', 'max_completion_tokens']
  return formats.includes(String(value.api_format))
    && typeof value.api_key_env === 'string'
    && typeof value.endpoint === 'string'
    && isFiniteNumber(value.max_output_tokens)
    && typeof value.model === 'string'
    && (value.reasoning_effort === null || reasoning.includes(String(value.reasoning_effort)))
    && typeof value.send_history === 'boolean'
    && (value.temperature === null || isFiniteNumber(value.temperature))
    && typeof value.thinking === 'boolean'
    && typeof value.vision_enabled === 'boolean'
    && (value.token_parameter === null || tokenParameters.includes(String(value.token_parameter)))
}

// The server exports the field for current snapshots. Keep older saved or
// pasted documents usable by treating an omitted opt-in as the safe default.
function addLegacyVisionDefaults(value: unknown): unknown {
  if (!isRecord(value) || !isRecord(value.models)) return value
  const models = Object.fromEntries(Object.entries(value.models).map(([name, model]) => [
    name,
    isRecord(model) && !Object.hasOwn(model, 'vision_enabled')
      ? { ...model, vision_enabled: false }
      : model,
  ]))
  return { ...value, models }
}

// Worldbook is an additive, opt-in settings family. Older snapshots and
// pasted advanced documents must remain closed and scoped to no groups.
function addLegacyWorldbookDefaults(value: unknown): unknown {
  if (!isRecord(value)) return value
  return {
    journal_enabled: false,
    journal_allowed_groups: [],
    journal_allow_live_publish: false,
    journal_allowed_live_uins: [],
    worldbook_enabled: false,
    worldbook_chat_projection_enabled: false,
    worldbook_schedule_projection_enabled: false,
    worldbook_storylet_enabled: false,
    worldbook_dream_proposal_enabled: false,
    worldbook_social_evidence_enabled: false,
    episode_query_rerank_enabled: false,
    worldbook_allowed_groups: [],
    ...value,
  }
}

function addLegacyStreamReplyDefault(value: unknown): unknown {
  if (!isRecord(value)) return value
  return { stream_reply_enabled: false, planned_reply_enabled: false, planned_reply_groups: [], followup_reply_enabled: false, followup_reply_groups: [], graph_extraction_enabled: false, graph_extraction_groups: [], video_metadata_enabled: false, video_metadata_groups: [], url_titles_enabled: false, url_titles_groups: [], element_rules_enabled: false, element_rules_groups: [], element_custom_rules: [], model_prices: [], food_search_group_overrides: {}, ...value }
}

function addLegacyMentionForceReplyDefault(value: unknown): unknown {
  if (!isRecord(value)) return value
  return { mention_force_reply_enabled: true, ...value }
}

function addLegacyMemoryDefaults(value: unknown): unknown {
  if (!isRecord(value)) return value
  return {
    character_recognition_enabled: false,
    character_recognition_groups: [],
    ccip_endpoint: '',
    character_reference_path: '',
    animetrace_endpoint: '',
    animetrace_model: '',
    character_teaching_enabled: false,
    character_teaching_groups: [],
    willingness_enabled: false,
    willingness_groups: [],
    diagnostic_commands_enabled: false,
    diagnostic_commands_groups: [],
    context_observation_enabled: false,
    graph_observation_enabled: false,
    cross_group_sharing_enabled: false,
    private_conversation_enabled: false,
    private_conversation_peers: [],
    echo_enabled: false,
    echo_groups: [],
    food_enabled: false,
    food_groups: [],
    affection_enabled: false,
    affection_groups: [],
    rws_hawkes_enabled: false,
    rws_feedback_enabled: false,
    rws_bandit_enabled: false,
    slang_machine_review_enabled: false,
    self_nickname_enabled: false,
    self_nickname_groups: [],
    learning_auto_apply_enabled: false,
    learning_auto_apply_groups: [],
    learning_auto_apply_domains: ['fact', 'slang', 'style'],
    retrieval_query_planner_enabled: false,
    memory_capture_enabled: false,
    memory_capture_groups: [],
    memory_extraction_domains: ['fact', 'slang', 'style', 'episode'],
    memory_spool_dir: '',
    memory_key_file: '',
    search_endpoint: '',
    web_fetch_hosts: [],
    http_api_hosts: [],
    visual_url_hosts: [],
    ...value,
  }
}

function addLegacyCalendarDefaults(value: unknown): unknown {
  if (!isRecord(value)) return value
  return { group_calendar_events: {}, ...value }
}

function normalizeLegacyEditableConfig(value: unknown): unknown {
  const result = addLegacyCalendarDefaults(addLegacyMemoryDefaults(
    addLegacyMentionForceReplyDefault(addLegacyStreamReplyDefault(
      addLegacyWorldbookDefaults(addLegacyVisionDefaults(value)),
    )),
  ))
  return isRecord(result) && !Object.hasOwn(result, 'qq_delivery_limits')
    ? { ...result, qq_delivery_limits: { account_min_interval: 5, account_hour_limit: 60, account_day_limit: 180,
      target_min_interval: 8, target_hour_limit: 30, target_day_limit: 90,
      admission_wait_seconds: 30, account_queue_limit: 8, target_queue_limit: 2 } }
    : result
}

function isEditableConfig(value: unknown): value is EditableConfig {
  if (!isRecord(value) || !isRecord(value.models) || Object.keys(value.models).length === 0) return false
  if (value.proactive_contact !== undefined && value.proactive_contact !== null
    && (!isContactSettings(value.proactive_contact) || (value.proactive_contact.enabled && !value.thinker_enabled))) return false
  if (typeof value.active_model !== 'string' || !value.active_model || !isEditableModelProfile(value.models[value.active_model])) return false
  if (!isOptionalBoundedString(value.persona_name, 80) || !isOptionalBoundedString(value.persona_instructions, 6000)) return false
  if (value.persona_mode !== undefined && value.persona_mode !== 'simple' && value.persona_mode !== 'source') return false
  if (!isOptionalBoundedString(value.persona_source_markdown, 24000)) return false
  if (!isGroupModeMap(value.group_modes)) return false
  if (!isGroupProfileMap(value.group_profiles)) return false
  if (!isGroupCalendarEvents(value.group_calendar_events)) return false
  if (!isRecord(value.qq_delivery_limits)) return false
  const qq = value.qq_delivery_limits
  const qqFields = ['account_min_interval', 'account_hour_limit', 'account_day_limit', 'target_min_interval',
    'target_hour_limit', 'target_day_limit', 'admission_wait_seconds', 'account_queue_limit', 'target_queue_limit']
  if (Object.keys(qq).some(key => !qqFields.includes(key)) || qqFields.some(key => !isFiniteNumber(qq[key]))) return false
  if (Number(qq.account_min_interval) < 5 || Number(qq.target_min_interval) < 8) return false
  for (const [key, maximum] of [['account_hour_limit', 60], ['account_day_limit', 180],
    ['target_hour_limit', 30], ['target_day_limit', 90], ['account_queue_limit', 8], ['target_queue_limit', 2]] as const) {
    if (!Number.isInteger(qq[key]) || Number(qq[key]) < 1 || Number(qq[key]) > maximum) return false
  }
  if (Number(qq.admission_wait_seconds) <= 0 || Number(qq.admission_wait_seconds) > 30) return false
  if (!Array.isArray(value.element_custom_rules) || value.element_custom_rules.length > 16
    || !value.element_custom_rules.every(rule => isRecord(rule) && typeof rule.id === 'string'
      && typeof rule.pattern === 'string' && typeof rule.reply === 'string'
      && typeof rule.use_llm === 'boolean')) return false
  if (!Array.isArray(value.model_prices) || value.model_prices.length > 64
    || !value.model_prices.every(price => isRecord(price) && typeof price.provider === 'string'
      && typeof price.model === 'string' && typeof price.version === 'string'
      && typeof price.currency === 'string'
      && ['input_per_million', 'output_per_million', 'cached_input_per_million', 'cache_write_per_million']
        .every(field => price[field] === null || isFiniteNumber(price[field])))) return false
  if (!isRecord(value.food_search_group_overrides)
    || Object.entries(value.food_search_group_overrides).some(([group, enabled]) =>
      !/^[A-Za-z0-9_-]{1,64}$/.test(group) || typeof enabled !== 'boolean')) return false
  if (!isKnownBotIds(value.known_bot_ids)) return false
  if (typeof value.worldbook_enabled !== 'boolean'
    || typeof value.worldbook_chat_projection_enabled !== 'boolean'
    || typeof value.worldbook_schedule_projection_enabled !== 'boolean'
    || typeof value.worldbook_storylet_enabled !== 'boolean'
    || typeof value.worldbook_dream_proposal_enabled !== 'boolean'
    || typeof value.worldbook_social_evidence_enabled !== 'boolean'
    || !isExactGroupIds(value.worldbook_allowed_groups)) return false
  if (typeof value.journal_enabled !== 'boolean' || !isExactGroupIds(value.journal_allowed_groups)
    || (value.journal_enabled && !value.journal_allowed_groups.length)
    || typeof value.journal_allow_live_publish !== 'boolean'
    || !Array.isArray(value.journal_allowed_live_uins) || value.journal_allowed_live_uins.length > 8
    || new Set(value.journal_allowed_live_uins).size !== value.journal_allowed_live_uins.length
    || value.journal_allowed_live_uins.some(id => typeof id !== 'string' || !/^[1-9][0-9]{0,19}$/.test(id))
    || (value.journal_allow_live_publish && (!value.journal_enabled || !value.journal_allowed_live_uins.length))) return false
  if (value.climate_mode !== undefined
    && value.climate_mode !== 'off'
    && value.climate_mode !== 'observe'
    && value.climate_mode !== 'active') return false
  for (const field of ['bot_pair_loop_alt_threshold', 'bot_pair_known_alt_threshold']) {
    const threshold = value[field]
    if (!Number.isInteger(threshold) || Number(threshold) < 1 || Number(threshold) > 127) return false
  }
  if (!['off', 'shadow', 'primary'].includes(String(value.rws_mode))) return false
  if (!isFiniteNumber(value.rws_threshold) || value.rws_threshold < 0 || value.rws_threshold > 1) return false
  const numberFields = [
    'bot_pair_cooldown_seconds', 'bot_pair_known_alt_threshold', 'bot_pair_loop_alt_threshold',
    'history_ttl', 'max_active_sessions', 'max_interruptions', 'max_reply_segments',
    'max_sessions', 'model_concurrency', 'model_timeout', 'queue_capacity',
    'reply_segment_chars', 'send_timeout', 'thinker_reserve', 'total_timeout',
    'reply_generation_timeout', 'reply_admission_timeout', 'reply_delivery_timeout',
  ]
  if (numberFields.some((field) => !isFiniteNumber(value[field]))) return false
  for (const [field, maximum] of [
    ['total_timeout', 105], ['reply_generation_timeout', 30],
    ['reply_admission_timeout', 30], ['reply_delivery_timeout', 45],
  ] as const) {
    if (Number(value[field]) <= 0 || Number(value[field]) > maximum) return false
  }
  if (!Number.isInteger(value.max_reply_segments)
    || Number(value.max_reply_segments) < 1 || Number(value.max_reply_segments) > 5) return false
  if (typeof value.onebot_endpoint !== 'string' || typeof value.onebot_token_env !== 'string' || typeof value.timezone !== 'string') return false
  if (typeof value.thinker_enabled !== 'boolean' || typeof value.mention_force_reply_enabled !== 'boolean'
    || typeof value.rws_hawkes_enabled !== 'boolean'
    || typeof value.rws_feedback_enabled !== 'boolean'
    || typeof value.rws_bandit_enabled !== 'boolean'
    || typeof value.slang_machine_review_enabled !== 'boolean'
    || typeof value.bot_pair_guard_enabled !== 'boolean'
    || typeof value.stream_reply_enabled !== 'boolean'
    || typeof value.planned_reply_enabled !== 'boolean'
    || !isExactGroupIds(value.planned_reply_groups)
    || (value.planned_reply_enabled && !value.planned_reply_groups.length)
    || typeof value.followup_reply_enabled !== 'boolean'
    || !isExactGroupIds(value.followup_reply_groups)
    || (value.followup_reply_enabled && !value.followup_reply_groups.length)
    || typeof value.graph_extraction_enabled !== 'boolean'
    || !isExactGroupIds(value.graph_extraction_groups)
    || (value.graph_extraction_enabled && !value.graph_extraction_groups.length)
    || typeof value.video_metadata_enabled !== 'boolean'
    || !isExactGroupIds(value.video_metadata_groups)
    || (value.video_metadata_enabled && !value.video_metadata_groups.length)
    || typeof value.url_titles_enabled !== 'boolean'
    || !isExactGroupIds(value.url_titles_groups)
    || (value.url_titles_enabled && !value.url_titles_groups.length)
    || typeof value.element_rules_enabled !== 'boolean'
    || !isExactGroupIds(value.element_rules_groups)
    || (value.element_rules_enabled && !value.element_rules_groups.length)
    || typeof value.episode_query_rerank_enabled !== 'boolean'
    || !Array.isArray(value.tool_capabilities) || !value.tool_capabilities.every((item) => typeof item === 'string')) return false
  if (typeof value.character_recognition_enabled !== 'boolean'
    || !isExactGroupIds(value.character_recognition_groups)
    || (value.character_recognition_enabled && !value.character_recognition_groups.length)) return false
  for (const [field, limit] of [['ccip_endpoint', 2048], ['character_reference_path', 4096],
    ['animetrace_endpoint', 2048], ['animetrace_model', 128]] as const) {
    if (typeof value[field] !== 'string' || value[field].length > limit) return false
  }
  if (typeof value.character_teaching_enabled !== 'boolean'
    || !isExactGroupIds(value.character_teaching_groups)
    || (value.character_teaching_enabled && !value.character_teaching_groups.length)) return false
  if (typeof value.willingness_enabled !== 'boolean'
    || !isExactGroupIds(value.willingness_groups)
    || (value.willingness_enabled && (!value.thinker_enabled || !value.willingness_groups.length))) return false
  if (typeof value.diagnostic_commands_enabled !== 'boolean'
    || !isExactGroupIds(value.diagnostic_commands_groups)
    || (value.diagnostic_commands_enabled && !value.diagnostic_commands_groups.length)) return false
  if (typeof value.context_observation_enabled !== 'boolean') return false
  if (typeof value.graph_observation_enabled !== 'boolean') return false
  if (typeof value.cross_group_sharing_enabled !== 'boolean') return false
  if (typeof value.private_conversation_enabled !== 'boolean'
    || !isExactGroupIds(value.private_conversation_peers)
    || (value.private_conversation_enabled && !value.private_conversation_peers.length)) return false
  if (typeof value.echo_enabled !== 'boolean'
    || !isExactGroupIds(value.echo_groups)
    || (value.echo_enabled && !value.echo_groups.length)) return false
  if (typeof value.food_enabled !== 'boolean'
    || !isExactGroupIds(value.food_groups)
    || (value.food_enabled && !value.food_groups.length)) return false
  if (typeof value.affection_enabled !== 'boolean'
    || !isExactGroupIds(value.affection_groups)
    || (value.affection_enabled && !value.affection_groups.length)) return false
  if (typeof value.self_nickname_enabled !== 'boolean' || !isExactGroupIds(value.self_nickname_groups)) return false
  if (value.self_nickname_enabled && (!value.memory_capture_enabled
    || !value.self_nickname_groups.length || !Array.isArray(value.memory_capture_groups)
    || !value.self_nickname_groups.every((group) => (value.memory_capture_groups as unknown[]).includes(group)))) return false
  if (value.slang_machine_review_enabled && (!value.memory_capture_enabled
    || !Array.isArray(value.memory_capture_groups) || !value.memory_capture_groups.length)) return false
  if (typeof value.learning_auto_apply_enabled !== 'boolean'
    || !isExactGroupIds(value.learning_auto_apply_groups)
    || typeof value.retrieval_query_planner_enabled !== 'boolean'
    || !Array.isArray(value.learning_auto_apply_domains) || value.learning_auto_apply_domains.length > 3
    || !value.learning_auto_apply_domains.every((domain) => ['fact', 'slang', 'style'].includes(domain))
    || new Set(value.learning_auto_apply_domains).size !== value.learning_auto_apply_domains.length) return false
  if (value.learning_auto_apply_enabled && (!value.memory_capture_enabled
    || !value.learning_auto_apply_groups.length || !value.learning_auto_apply_domains.length
    || !Array.isArray(value.memory_capture_groups)
    || !value.learning_auto_apply_groups.every((group) => (value.memory_capture_groups as unknown[]).includes(group)))) return false
  if (typeof value.memory_capture_enabled !== 'boolean' 
    || !isExactGroupIds(value.memory_capture_groups)
    || !Array.isArray(value.memory_extraction_domains)
    || value.memory_extraction_domains.length < 1 || value.memory_extraction_domains.length > 4
    || !value.memory_extraction_domains.every((domain) => ['fact', 'slang', 'style', 'episode'].includes(domain))
    || new Set(value.memory_extraction_domains).size !== value.memory_extraction_domains.length
    || typeof value.memory_spool_dir !== 'string' || value.memory_spool_dir.length > 4096
    || typeof value.memory_key_file !== 'string' || value.memory_key_file.length > 4096) return false
  if (value.memory_capture_enabled
    && (!value.memory_capture_groups.length || !value.memory_spool_dir.trim() || !value.memory_key_file.trim())) return false
  if (typeof value.search_endpoint !== 'string' || value.search_endpoint.length > 2048
    || !Array.isArray(value.web_fetch_hosts) || value.web_fetch_hosts.length > 32
    || !value.web_fetch_hosts.every((host) => typeof host === 'string')
    || !Array.isArray(value.http_api_hosts) || value.http_api_hosts.length > 32
    || !value.http_api_hosts.every((host) => typeof host === 'string')
    || !Array.isArray(value.visual_url_hosts) || value.visual_url_hosts.length > 32
    || !value.visual_url_hosts.every((host) => typeof host === 'string')) return false
  if (!isRecord(value.task_models)) return false
  const tasks = ['reply', 'thinker', 'vision', 'schedule', 'dream', 'memory', 'journal']
  if (Object.entries(value.task_models).some(([task, model]) => !tasks.includes(task) || typeof model !== 'string')) return false
  return Object.values(value.models).every((model) => isEditableModelProfile(model))
}

function selectFirstExistingProfile(): void {
  const draft = settingsState.draft
  if (!draft) return
  const names = Object.keys(draft.models)
  if (!names.includes(settingsState.selectedProfile)) {
    settingsState.selectedProfile = names.includes(draft.active_model) ? draft.active_model : (names[0] ?? '')
  }
}

function acceptSnapshot(snapshot: SettingsSnapshot): void {
  const normalized = {
    ...snapshot,
    config: normalizeLegacyEditableConfig(snapshot.config) as EditableConfig,
    effective_config: normalizeLegacyEditableConfig(snapshot.effective_config) as EditableConfig,
  }
  settingsState.snapshot = normalized
  settingsState.draft = cloneJson(normalized.config)
  settingsState.baseRevision = snapshot.revision
  selectFirstExistingProfile()
  settingsState.error = ''
  settingsState.notice = ''
  settingsState.pendingDeleteProfile = null
  settingsState.reloadPending = false
  setAdvancedFromDraft()
}

export function resetSettingsState(): void {
  loadSequence += 1
  saveSequence += 1
  settingsState.snapshot = null
  settingsState.draft = null
  settingsState.baseRevision = null
  settingsState.advancedText = ''
  settingsState.advancedBaseline = ''
  settingsState.advancedEdited = false
  settingsState.selectedProfile = ''
  settingsState.loading = false
  settingsState.saving = false
  settingsState.error = ''
  settingsState.notice = ''
  settingsState.pendingDeleteProfile = null
  settingsState.reloadPending = false
}

export function hasSettingsDraftChanges(): boolean {
  if (!settingsState.snapshot || !settingsState.draft) return false
  return settingsState.advancedEdited || serialized(settingsState.draft) !== serialized(settingsState.snapshot.config)
}

export function profileNames(): string[] {
  return settingsState.draft ? Object.keys(settingsState.draft.models) : []
}

export function profile(name: string): ModelProfile | undefined {
  return settingsState.draft?.models[name]
}

export function selectProfile(name: string): boolean {
  if (!canEdit() || !settingsState.draft?.models[name]) return false
  settingsState.pendingDeleteProfile = null
  settingsState.selectedProfile = name
  return true
}

export async function loadSettings(force = false): Promise<void> {
  if (settingsState.saving || settingsState.loading) return
  if (!force && settingsState.snapshot && settingsState.draft) return
  const epoch = currentSessionEpoch()
  const sequence = ++loadSequence
  settingsState.loading = true
  settingsState.error = ''
  try {
    const snapshot = await apiRequest<SettingsSnapshot>('/api/admin/config')
    if (!isCurrentSessionEpoch(epoch) || sequence !== loadSequence) return
    acceptSnapshot(snapshot)
  } catch (error: unknown) {
    if (!isCurrentSessionEpoch(epoch) || sequence !== loadSequence) return
    if (isApiError(error) && error.status === 401) expireAdminSession()
    settingsState.error = apiErrorMessage(error)
  } finally {
    if (sequence === loadSequence) settingsState.loading = false
  }
}

export function markAdvancedText(value: string): void {
  if (settingsState.saving || settingsState.loading) {
    settingsState.error = settingsState.saving ? '配置正在保存，请等待结果返回后再编辑。' : '配置正在读取，请等待结果返回后再编辑。'
    return
  }
  settingsState.advancedText = value
  settingsState.advancedEdited = value !== settingsState.advancedBaseline
  if (settingsState.advancedEdited) settingsState.notice = '高级 JSON 尚未应用；请先应用到字段，再保存或离开。'
}

export function applyAdvancedText(): boolean {
  if (settingsState.saving || settingsState.loading) {
    settingsState.error = settingsState.saving ? '配置正在保存，请等待结果返回后再编辑。' : '配置正在读取，请等待结果返回后再编辑。'
    return false
  }
  try {
    const parsed: unknown = JSON.parse(settingsState.advancedText)
    const normalized = normalizeLegacyEditableConfig(parsed)
    if (!isEditableConfig(normalized)) throw new Error('invalid_config_shape')
    const candidate = normalized
    settingsState.draft = cloneJson(candidate)
    settingsState.pendingDeleteProfile = null
    selectFirstExistingProfile()
    settingsState.notice = '高级 JSON 已应用到字段；点击保存配置才会提交。'
    setAdvancedFromDraft()
    return true
  } catch {
    settingsState.error = 'JSON 无法应用：需要包含至少一个命名模型配置的完整对象。'
    return false
  }
}

function canEdit(): boolean {
  if (settingsState.saving || settingsState.loading) {
    settingsState.error = settingsState.saving ? '配置正在保存，请等待结果返回后再编辑。' : '配置正在读取，请等待结果返回后再编辑。'
    return false
  }
  if (settingsState.advancedEdited) {
    settingsState.error = '高级 JSON 有未应用修改，请先应用到字段。'
    return false
  }
  return Boolean(settingsState.draft)
}

function commitBasicEdit(): void {
  settingsState.error = ''
  settingsState.notice = '有未保存的配置草稿。'
  setAdvancedFromDraft()
}

export function updateConfigField<K extends keyof EditableConfig>(key: K, value: EditableConfig[K]): boolean {
  if (!canEdit() || !settingsState.draft) return false
  settingsState.draft = { ...settingsState.draft, [key]: value }
  commitBasicEdit()
  return true
}

export function updateProfileField<K extends keyof EditableModelProfile>(name: string, key: K, value: EditableModelProfile[K]): boolean {
  if (!canEdit() || !settingsState.draft) return false
  const current = settingsState.draft.models[name]
  if (!current) return false
  settingsState.draft.models[name] = { ...current, [key]: value }
  commitBasicEdit()
  return true
}

export function updateTaskBinding(task: TaskName, value: string | null): boolean {
  if (!canEdit() || !settingsState.draft) return false
  const taskModels = { ...settingsState.draft.task_models }
  if (value) taskModels[task] = value
  else delete taskModels[task]
  settingsState.draft = { ...settingsState.draft, task_models: taskModels }
  commitBasicEdit()
  return true
}

function defaultProfile(): EditableModelProfile {
  return {
    api_format: 'anthropic',
    api_key_env: 'OMUBOT_MODEL_KEY',
    endpoint: 'https://api.anthropic.com/v1/messages',
    max_output_tokens: 1024,
    model: 'offline-model',
    reasoning_effort: null,
    send_history: true,
    temperature: null,
    thinking: false,
    token_parameter: null,
    vision_enabled: false,
  }
}

export function addProfile(requestedName = ''): string | null {
  if (!canEdit() || !settingsState.draft) return null
  const provided = requestedName.trim()
  if (provided && !/^[A-Za-z0-9_-]{1,64}$/.test(provided)) {
    settingsState.error = '模型配置名只能使用字母、数字、下划线和短横线。'
    return null
  }
  if (Object.keys(settingsState.draft.models).length >= 16) {
    settingsState.error = '最多保留 16 个命名模型配置。'
    return null
  }
  let name = provided
  if (!name) {
    let index = 1
    name = `profile-${index}`
    while (settingsState.draft.models[name]) {
      index += 1
      name = `profile-${index}`
    }
  }
  if (settingsState.draft.models[name]) {
    settingsState.error = `模型配置名“${name}”已存在。`
    return null
  }
  settingsState.draft.models[name] = defaultProfile()
  settingsState.selectedProfile = name
  commitBasicEdit()
  return name
}

export function requestDeleteProfile(): void {
  if (!canEdit() || !settingsState.draft) return
  if (profileNames().length <= 1) {
    settingsState.error = '至少需要保留一个模型配置。'
    return
  }
  settingsState.pendingDeleteProfile = settingsState.selectedProfile
}

export function cancelDeleteProfile(): void {
  settingsState.pendingDeleteProfile = null
}

export function confirmDeleteProfile(): void {
  if (!canEdit() || !settingsState.draft || !settingsState.pendingDeleteProfile) return
  const name = settingsState.pendingDeleteProfile
  if (!Object.hasOwn(settingsState.draft.models, name) || profileNames().length <= 1) {
    settingsState.pendingDeleteProfile = null
    settingsState.error = '模型已变化；至少需要保留一个模型配置。'
    return
  }
  delete settingsState.draft.models[name]
  if (settingsState.draft.active_model === name) settingsState.draft.active_model = Object.keys(settingsState.draft.models)[0] ?? ''
  const taskModels = { ...settingsState.draft.task_models }
  for (const task of Object.keys(taskModels) as TaskName[]) {
    if (taskModels[task] === name) delete taskModels[task]
  }
  settingsState.draft = { ...settingsState.draft, task_models: taskModels }
  selectFirstExistingProfile()
  settingsState.pendingDeleteProfile = null
  commitBasicEdit()
}

export function requestReload(): void {
  if (settingsState.saving) {
    settingsState.error = '配置正在保存，请等待结果返回后再刷新。'
    return
  }
  settingsState.reloadPending = hasSettingsDraftChanges()
  if (!settingsState.reloadPending) void loadSettings(true)
}

export function cancelReload(): void {
  settingsState.reloadPending = false
}

export async function confirmReload(): Promise<void> {
  if (settingsState.saving) return
  settingsState.reloadPending = false
  await loadSettings(true)
}

export async function saveSettings(): Promise<boolean> {
  if (!settingsState.draft || settingsState.baseRevision === null || settingsState.saving || settingsState.loading) return false
  if (settingsState.advancedEdited) {
    settingsState.error = '高级 JSON 有未应用修改，请先应用到字段。'
    return false
  }
  const epoch = currentSessionEpoch()
  const sequence = ++saveSequence
  settingsState.saving = true
  settingsState.error = ''
  try {
    const snapshot = await apiRequest<SettingsSnapshot>('/api/admin/config', {
      method: 'PUT',
      adminMutation: true,
      body: { expected_revision: settingsState.baseRevision, config: cloneJson(settingsState.draft) },
    })
    if (!isCurrentSessionEpoch(epoch)) return false
    acceptSnapshot(snapshot)
    settingsState.notice = '配置已保存。运行模式和生效版本请查看上方提示。'
    return true
  } catch (error: unknown) {
    if (!isCurrentSessionEpoch(epoch)) return false
    if (isApiError(error) && error.status === 401) expireAdminSession()
    settingsState.error = apiErrorMessage(error)
    return false
  } finally {
    if (sequence === saveSequence) settingsState.saving = false
  }
}

export async function rollbackSettings(targetRevision: number): Promise<boolean> {
  if (settingsState.baseRevision === null || settingsState.saving || settingsState.loading) return false
  if (hasSettingsDraftChanges()) {
    settingsState.error = '请先保存或重新读取以放弃当前草稿，再回滚。'
    return false
  }
  const epoch = currentSessionEpoch()
  const sequence = ++saveSequence
  settingsState.saving = true
  settingsState.error = ''
  try {
    const snapshot = await apiRequest<SettingsSnapshot>('/api/admin/config/rollback', {
      method: 'POST',
      adminMutation: true,
      body: { expected_revision: settingsState.baseRevision, target_revision: targetRevision },
    })
    if (!isCurrentSessionEpoch(epoch)) return false
    acceptSnapshot(snapshot)
    settingsState.notice = `已回滚到版本 ${targetRevision}。页面不会自动重启。`
    return true
  } catch (error: unknown) {
    if (!isCurrentSessionEpoch(epoch)) return false
    if (isApiError(error) && error.status === 401) expireAdminSession()
    settingsState.error = apiErrorMessage(error)
    return false
  } finally {
    if (sequence === saveSequence) settingsState.saving = false
  }
}

export function sessionStillAdmin(): boolean {
  return sessionState.adminAuthenticated
}

watch(() => sessionState.generation, () => {
  if (!sessionState.adminAuthenticated) resetSettingsState()
})
