import { isRecord } from '@/api/guards'
import type { ContactSettings } from '@/api/generated'

/** Advanced JSON enters the same typed draft as the structured editor. */
export function isContactSettings(value: unknown): value is ContactSettings {
  if (!isRecord(value) || typeof value.enabled !== 'boolean'
    || Object.keys(value).some(key => !['enabled', 'users', 'groups'].includes(key))) return false
  for (const kind of ['users', 'groups']) {
    const targets = value[kind]
    if (!isRecord(targets) || Object.keys(targets).length > 128) return false
    for (const [id, rule] of Object.entries(targets)) {
      if (!/^[A-Za-z0-9_-]{1,64}$/.test(id) || !isRecord(rule)
        || Object.keys(rule).some(key => !['windows', 'minimum_interval_seconds', 'day_limit',
          'decision_minimum_interval_seconds', 'decision_day_limit'].includes(key))
        || !Array.isArray(rule.windows) || rule.windows.length < 1 || rule.windows.length > 16) return false
      for (const name of ['minimum_interval_seconds', 'decision_minimum_interval_seconds']) {
        const interval = rule[name]
        if (typeof interval !== 'number' || !Number.isFinite(interval) || interval <= 0) return false
      }
      for (const name of ['day_limit', 'decision_day_limit']) {
        const limit = rule[name]
        if (typeof limit !== 'number' || !Number.isInteger(limit) || limit < 1 || limit > 180) return false
      }
      const windows: Array<{ start: number; end: number }> = []
      for (const window of rule.windows) {
        if (!isRecord(window) || Object.keys(window).some(key => !['start_minute', 'end_minute'].includes(key))
          || typeof window.start_minute !== 'number' || !Number.isInteger(window.start_minute)
          || typeof window.end_minute !== 'number' || !Number.isInteger(window.end_minute)
          || window.start_minute < 0 || window.end_minute > 1440
          || window.start_minute >= window.end_minute) return false
        windows.push({ start: window.start_minute, end: window.end_minute })
      }
      windows.sort((left, right) => left.start - right.start)
      if (windows.some((window, index) => index > 0 && (windows[index - 1]?.end ?? 0) > window.start)) return false
    }
  }
  return true
}
