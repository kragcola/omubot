import { formatCooldown, formatMs } from './formatters'
import type {
  ProtocolConnectionEvent,
  ProviderRateLimit,
  ProviderTestResult,
  ServiceHealthItem,
} from './types'

export function protocolTagType(status: string) {
  if (status === 'ok') return 'success'
  if (status === 'configured') return 'info'
  if (status === 'failed') return 'error'
  return 'default'
}

export function compatibilityTagType(status: string) {
  if (status === 'supported' || status === 'compatible') return 'success'
  if (status === 'conditional') return 'warning'
  if (status === 'manual') return 'info'
  return 'default'
}

export function compatibilityLabel(status: string) {
  if (status === 'supported') return '支持'
  if (status === 'compatible') return '兼容'
  if (status === 'conditional') return '条件支持'
  if (status === 'manual') return '手动确认'
  if (status === 'unchecked') return '未探测'
  return status
}

export function protocolTraceType(status: string) {
  if (status === 'ok') return 'success'
  if (status === 'failed') return 'error'
  if (status === 'pending') return 'warning'
  return 'default'
}

export function runtimeErrorLevelType(level: string) {
  if (level === 'CRITICAL' || level === 'ERROR') return 'error'
  if (level === 'WARNING') return 'warning'
  return 'default'
}

export function runtimeErrorLevelLabel(level: string) {
  if (level === 'CRITICAL') return 'Critical'
  if (level === 'ERROR') return 'Error'
  if (level === 'WARNING') return 'Warning'
  return level || 'Unknown'
}

export function protocolConnectionType(status: string) {
  if (status === 'connected') return 'success'
  if (status === 'disconnected') return 'error'
  return 'warning'
}

export function protocolConnectionLabel(status: string) {
  if (status === 'connected') return '已连接'
  if (status === 'disconnected') return '已断开'
  return '未知'
}

export function protocolConnectionEventLabel(event: ProtocolConnectionEvent) {
  if (event.kind === 'error') return '探测异常'
  if (event.status === 'connected') return '连接恢复'
  if (event.status === 'disconnected') return '连接断开'
  return '状态变化'
}

export function serviceTagType(status: string) {
  if (status === 'ok') return 'success'
  if (status === 'warning' || status === 'degraded') return 'warning'
  if (status === 'error') return 'error'
  return 'default'
}

export function serviceStatusLabel(status: string) {
  if (status === 'ok') return '健康'
  if (status === 'warning') return '留意'
  if (status === 'degraded') return '降级'
  if (status === 'error') return '异常'
  return '未知'
}

export function providerResultType(result?: ProviderTestResult) {
  if (!result) return 'default'
  return result.ok ? 'success' : 'error'
}

export function providerResultLabel(result?: ProviderTestResult) {
  if (!result) return '未测试'
  if (result.ok) return `通过 · ${formatMs(result.elapsed_ms)}`
  return `失败 · ${formatMs(result.elapsed_ms)}`
}

export function providerRateLimitType(rateLimit?: ProviderRateLimit) {
  if (!rateLimit) return 'default'
  if (rateLimit.status === 'cooldown') return 'warning'
  if (rateLimit.rate_limited || rateLimit.failures) return 'info'
  return 'default'
}

export function providerRateLimitLabel(rateLimit?: ProviderRateLimit) {
  if (!rateLimit) return 'ready'
  if (rateLimit.status === 'cooldown') {
    return `冷却 ${formatCooldown(rateLimit.cooldown_remaining_seconds)}`
  }
  if (rateLimit.rate_limited) return `${rateLimit.rate_limited} 次限流`
  return 'ready'
}

export function providerModeLabel(mode?: string) {
  if (!mode) return '--'
  if (mode === 'native') return 'DeepSeek 原生'
  if (mode === 'native-beta') return 'DeepSeek Beta'
  if (mode === 'anthropic-compat') return 'Anthropic 兼容'
  if (mode === 'openai-compat') return 'OpenAI 兼容'
  return mode
}

export function providerCacheHitLabel(value?: number | null) {
  if (typeof value !== 'number' || Number.isNaN(value)) return '--'
  return `${value.toFixed(1)}%`
}

export function alertTagType(severity: string) {
  if (severity === 'error') return 'error'
  if (severity === 'warning') return 'warning'
  return 'info'
}

export function alertSeverityLabel(severity: string) {
  if (severity === 'error') return '异常'
  if (severity === 'warning') return '留意'
  return '提示'
}

export function serviceMetaTags(service: ServiceHealthItem) {
  if (service.id !== 'memory' || !service.meta) return []
  const semantic = (service.meta.semantic || {}) as Record<string, any>
  const tags: string[] = []
  if (typeof service.meta.card_count === 'number') tags.push(`${service.meta.card_count} cards`)
  if (typeof service.meta.message_count === 'number') tags.push(`${service.meta.message_count} messages`)
  if (typeof service.meta.active_sessions === 'number') tags.push(`${service.meta.active_sessions} sessions`)
  if (semantic.enabled) {
    tags.push(`semantic ${semantic.active_backend || semantic.requested_backend || 'ngram'}`)
    tags.push(`${semantic.hits || 0}/${semantic.queries || 0} hits`)
    if (semantic.fallbacks) tags.push(`${semantic.fallbacks} fallback`)
    if (semantic.errors) tags.push(`${semantic.errors} errors`)
  }
  return tags
}

export function memorySemanticLastError(service: ServiceHealthItem) {
  if (service.id !== 'memory' || !service.meta?.semantic?.last_error) return ''
  return String(service.meta.semantic.last_error)
}
