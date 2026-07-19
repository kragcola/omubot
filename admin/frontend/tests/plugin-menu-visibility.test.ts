import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

async function loadSubject() {
  try {
    return await import('../src/layouts/pluginMenuVisibility.ts')
  }
  catch (error) {
    assert.fail(`plugin menu visibility helper must load: ${String(error)}`)
  }
}

test('plugin routes are fail-closed while core routes stay visible', async () => {
  const { isPluginMenuRouteVisible } = await loadSubject()
  const noEnabledPlugins = new Set<string>()

  assert.equal(isPluginMenuRouteVisible('/qzone-journal', noEnabledPlugins), false)
  assert.equal(isPluginMenuRouteVisible('/future-plugin-console', noEnabledPlugins), false)
  assert.equal(isPluginMenuRouteVisible('/', noEnabledPlugins), true)
})

test('every first-level sidebar route has an explicit plugin requirement', async () => {
  const { MENU_PLUGIN_REQUIREMENTS } = await loadSubject()
  const sideMenuSource = await readFile(
    new URL('../src/layouts/components/SideMenu.vue', import.meta.url),
    'utf8',
  )

  assert.deepEqual(MENU_PLUGIN_REQUIREMENTS, {
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
  })

  const baseMenuSource = sideMenuSource.match(
    /const baseMenuOptions:[\s\S]+?\n\]\n\nconst enabledPlugins/,
  )?.[0] ?? ''
  const menuRoutes = [...baseMenuSource.matchAll(/key: '([^']+)'/g)]
    .map(match => match[1])
    .filter(key => key.startsWith('/'))
    .sort()
  assert.deepEqual(menuRoutes, Object.keys(MENU_PLUGIN_REQUIREMENTS).sort())
})

test('only plugins explicitly enabled by the admin payload reveal their menus', async () => {
  const subject = await loadSubject()
  assert.equal(typeof subject.enabledPluginNamesFromPayload, 'function')

  const enabledPlugins = subject.enabledPluginNamesFromPayload({
    plugins: [
      { name: 'qzone_journal', enabled: true },
      { name: 'sticker', enabled: true },
      { name: 'calendar_context', enabled: false },
      { name: 'knowledge', enabled: 1 },
      { name: '', enabled: true },
    ],
  })

  assert.deepEqual([...enabledPlugins].sort(), ['qzone_journal', 'sticker'])
  assert.equal(subject.isPluginMenuRouteVisible('/qzone-journal', enabledPlugins), true)
  assert.equal(subject.isPluginMenuRouteVisible('/stickers', enabledPlugins), true)
  assert.equal(subject.isPluginMenuRouteVisible('/birthday', enabledPlugins), false)
  assert.equal(subject.isPluginMenuRouteVisible('/knowledge', enabledPlugins), false)
  assert.deepEqual([...subject.enabledPluginNamesFromPayload(undefined)], [])
  assert.deepEqual([...subject.enabledPluginNamesFromPayload({ plugins: null })], [])
})

test('SideMenu loads runtime plugin state without optimistic plugin menus', async () => {
  const sideMenuSource = await readFile(
    new URL('../src/layouts/components/SideMenu.vue', import.meta.url),
    'utf8',
  )

  assert.match(sideMenuSource, /api<unknown>\('\/api\/admin\/plugins\?include_system=true'\)/)
  assert.match(sideMenuSource, /ref<ReadonlySet<string>>\(new Set\(\)\)/)
  assert.match(sideMenuSource, /enabledPluginNamesFromPayload/)
  assert.match(sideMenuSource, /isPluginMenuRouteVisible/)
})

test('successful plugin toggles refresh sidebar visibility without navigation', async () => {
  const sideMenuSource = await readFile(
    new URL('../src/layouts/components/SideMenu.vue', import.meta.url),
    'utf8',
  )
  const pluginsViewSource = await readFile(
    new URL('../src/views/plugins/PluginsView.vue', import.meta.url),
    'utf8',
  )

  assert.match(sideMenuSource, /PLUGIN_MENU_VISIBILITY_CHANGED/)
  assert.match(sideMenuSource, /addEventListener\(PLUGIN_MENU_VISIBILITY_CHANGED/)
  assert.match(sideMenuSource, /removeEventListener\(PLUGIN_MENU_VISIBILITY_CHANGED/)
  assert.match(pluginsViewSource, /dispatchEvent\(new Event\(PLUGIN_MENU_VISIBILITY_CHANGED\)\)/)
})
