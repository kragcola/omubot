export function formatDuration(seconds: number | null | undefined) {
  if (!seconds || seconds <= 0) return '--'
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (days > 0) return `${days}天 ${hours}小时`
  if (hours > 0) return `${hours}小时 ${minutes}分钟`
  return `${minutes}分钟`
}

export function formatCooldown(seconds: number | null | undefined) {
  if (!seconds || seconds <= 0) return '0s'
  if (seconds < 60) return `${Math.ceil(seconds)}s`
  return formatDuration(seconds)
}

export function formatPercent(value: number | null | undefined) {
  if (typeof value !== 'number' || Number.isNaN(value)) return 0
  return Math.max(0, Math.min(100, Math.round(value)))
}

export function meterColor(value: number | null | undefined) {
  const percent = formatPercent(value)
  if (percent >= 85) return '#B84C5C'
  if (percent >= 70) return '#C58A2B'
  return '#2E8F6B'
}

/**
 * Cache hit rate color — opposite polarity from meterColor (high = good).
 * Input is a fraction in [0, 1] from the dashboard cache-pipelines endpoint;
 * null means no data (returns neutral grey so empty pipelines don't look like
 * a 0% emergency).
 */
export function cacheHitColor(pct: number | null | undefined) {
  if (typeof pct !== 'number' || Number.isNaN(pct)) return '#c8d1d3'
  if (pct >= 0.85) return '#2E8F6B'   // success
  if (pct >= 0.60) return '#316C72'   // primary (calm — within band)
  if (pct >= 0.40) return '#C58A2B'   // warning
  return '#B84C5C'                    // danger
}

/**
 * Format a [0, 1] fraction as a percentage string. Returns "--" for null/NaN
 * so dashboards can show a placeholder without inventing a 0% reading.
 */
export function formatHitPct(pct: number | null | undefined) {
  if (typeof pct !== 'number' || Number.isNaN(pct)) return '--'
  return `${Math.round(pct * 100)}%`
}

export function formatTimestamp(value?: number | null) {
  if (!value) return '--'
  return new Date(value * 1000).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function formatMs(value?: number | null) {
  if (typeof value !== 'number' || Number.isNaN(value)) return '--'
  if (value >= 1000) return `${(value / 1000).toFixed(1)}s`
  return `${Math.round(value)}ms`
}

export function formatTokenCount(value?: number | null) {
  if (typeof value !== 'number' || Number.isNaN(value) || value <= 0) return '--'
  return Number(value).toLocaleString('zh-CN')
}
