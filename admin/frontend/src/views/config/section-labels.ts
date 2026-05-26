import type { ConfigFieldSchema } from './types'

export interface ConfigSectionLabel {
  /** Eyebrow text rendered above the section title (uppercase + tracking). */
  eyebrow: string
  /** Visible section title. */
  title: string
  /** Optional one-line description shown beneath the title. */
  description?: string
}

/**
 * Manual mapping from a "bucket id" to its display labels. Bucket ids are not
 * the field path namespace directly; sometimes one task wants to split a single
 * namespace into multiple buckets (e.g. group.access.* vs the rest of group.*).
 */
export const CONFIG_SECTION_LABELS: Record<string, ConfigSectionLabel> = {
  llm: {
    eyebrow: 'LLM',
    title: '模型与 API',
    description: '决定 Bot 调用哪个模型、用哪个 API key、不同任务使用哪份 profile。',
  },
  llm_profiles: {
    eyebrow: 'Profiles',
    title: '任务模型分配',
    description: '为 main / thinker / compact / slang / vision 等任务挑选 profile。',
  },
  group_access: {
    eyebrow: 'Group Access',
    title: '群门禁兜底字段',
    description: '门禁请到「群管理」页编辑（写入 config/group-policy.json）。下面这几项仅在 group-policy.json 缺失时作为兜底。',
  },
  group: {
    eyebrow: 'Chat Behavior',
    title: '群聊行为',
    description: '默认参与态、回复风格、是否启用工具与黑话。',
  },
  group_users: {
    eyebrow: 'Group Users',
    title: '用户白/黑名单',
  },
  anti_detect: {
    eyebrow: 'Human Rhythm',
    title: '拟人延迟',
    description: '让回复更像人在打字，避免机械感。',
  },
  thinker: {
    eyebrow: 'Thinker',
    title: '回复前思考',
  },
  reply_segmentation: {
    eyebrow: 'Segmentation',
    title: '回复分段',
    description: '控制长回复如何切段、排队和逐段发出。',
  },
  humanization: {
    eyebrow: 'Humanization',
    title: '拟人化生成',
    description: '控制 Part 6 生成档位、状态板稳定化与后续生成能力开关。',
  },
  napcat: {
    eyebrow: 'Connection',
    title: 'NapCat 协议端',
  },
  vision: {
    eyebrow: 'Vision',
    title: '图片理解模型',
    description: '让 Bot 能看图。需要单独配置一个视觉模型 provider（与主聊天模型可以不同）。',
  },
  access: {
    eyebrow: 'Access',
    title: '权限与私聊',
    description: '决定谁能登录后台、谁能私聊 Bot、谁被屏蔽。',
  },
  misc: {
    eyebrow: 'Other',
    title: '其它字段',
  },
}

/**
 * Determine which section bucket a path belongs to. Order matters because the
 * first matching predicate wins (e.g. `group.access.*` must be checked before
 * the catch-all `group.*` rule).
 */
export function bucketForPath(path: string): string {
  if (path.startsWith('llm.task_profiles')) return 'llm_profiles'
  if (path.startsWith('llm.')) return 'llm'
  if (path.startsWith('group.access') || path === 'group.allowed_groups' || path === 'group.presence.default_mode') return 'group_access'
  if (path === 'group.blocked_users' || path === 'allowed_private_users') return 'group_users'
  if (path.startsWith('group.')) return 'group'
  if (path.startsWith('anti_detect.')) return 'anti_detect'
  if (path.startsWith('thinker.')) return 'thinker'
  if (path.startsWith('reply_segmentation.')) return 'reply_segmentation'
  if (path.startsWith('humanization.')) return 'humanization'
  if (path.startsWith('napcat.')) return 'napcat'
  if (path.startsWith('vision.')) return 'vision'
  if (path === 'admins' || path === 'admin_token') return 'access'
  return 'misc'
}

export interface ConfigSectionBucket {
  id: string
  label: ConfigSectionLabel
  fields: ConfigFieldSchema[]
}

/**
 * Group ordered fields into named section buckets, preserving the original
 * order both within and across buckets. The bucket order is determined by the
 * first occurrence of each bucket id in the input.
 */
export function bucketFields(fields: ConfigFieldSchema[]): ConfigSectionBucket[] {
  const buckets = new Map<string, ConfigSectionBucket>()
  for (const field of fields) {
    const id = bucketForPath(field.path)
    const label = CONFIG_SECTION_LABELS[id] || CONFIG_SECTION_LABELS.misc
    let bucket = buckets.get(id)
    if (!bucket) {
      bucket = { id, label, fields: [] }
      buckets.set(id, bucket)
    }
    bucket.fields.push(field)
  }
  return Array.from(buckets.values())
}
