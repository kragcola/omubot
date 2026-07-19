// Keep every first-level menu explicit: null means core/aggregate UI and stays visible.
export const PLUGIN_MENU_VISIBILITY_CHANGED = 'omubot:plugin-menu-visibility-changed'

export const MENU_PLUGIN_REQUIREMENTS = {
  '/': null,
  '/persona-importer': null,
  '/groups': null,
  '/memory': null,
  '/stickers': 'sticker',
  '/characters': null,
  '/birthday': 'calendar_context',
  '/qzone-journal': 'qzone_journal',
  '/learning': null,
  '/knowledge': 'knowledge',
  '/block-trace': null,
  '/worldbook': 'worldbook',
  '/replay/weekly': null,
  '/config': null,
  '/plugins': null,
  '/sandbox': null,
  '/system': null,
  '/logs': null,
} as const satisfies Record<string, string | null>

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

export function enabledPluginNamesFromPayload(payload: unknown): Set<string> {
  if (!isRecord(payload) || !Array.isArray(payload.plugins)) return new Set()

  const enabledPlugins = new Set<string>()
  for (const plugin of payload.plugins) {
    if (!isRecord(plugin) || plugin.enabled !== true || typeof plugin.name !== 'string') continue
    const name = plugin.name.trim()
    if (name) enabledPlugins.add(name)
  }
  return enabledPlugins
}

export function isPluginMenuRouteVisible(
  routeKey: unknown,
  enabledPlugins: ReadonlySet<string>,
): boolean {
  if (typeof routeKey !== 'string' || !routeKey.startsWith('/')) return true

  const requiredPlugin = MENU_PLUGIN_REQUIREMENTS[
    routeKey as keyof typeof MENU_PLUGIN_REQUIREMENTS
  ]
  if (requiredPlugin === null) return true
  return typeof requiredPlugin === 'string' && enabledPlugins.has(requiredPlugin)
}
