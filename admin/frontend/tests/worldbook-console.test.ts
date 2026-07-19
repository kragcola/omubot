import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

async function loadWorldbookApi() {
  try {
    return await import('../src/api/worldbook.ts')
  }
  catch (error) {
    assert.fail(`worldbook api module must load: ${String(error)}`)
  }
}

async function loadPluginVisibility() {
  try {
    return await import('../src/layouts/pluginMenuVisibility.ts')
  }
  catch (error) {
    assert.fail(`plugin menu visibility helper must load: ${String(error)}`)
  }
}

test('worldbook route and menu requirement are wired for fail-closed visibility', async () => {
  const routerSource = await readFile(
    new URL('../src/router/index.ts', import.meta.url),
    'utf8',
  )
  const sideMenuSource = await readFile(
    new URL('../src/layouts/components/SideMenu.vue', import.meta.url),
    'utf8',
  )
  const { MENU_PLUGIN_REQUIREMENTS, isPluginMenuRouteVisible } = await loadPluginVisibility()
  const {
    WORLDBOOK_PLUGIN_NAME,
    WORLDBOOK_ROUTE_PATH,
    WORLDBOOK_SNAPSHOT_PATH,
  } = await loadWorldbookApi()

  assert.equal(WORLDBOOK_ROUTE_PATH, '/worldbook')
  assert.equal(WORLDBOOK_PLUGIN_NAME, 'worldbook')
  assert.equal(WORLDBOOK_SNAPSHOT_PATH, '/api/admin/worldbook/snapshot')

  assert.match(routerSource, /path:\s*['"]\/worldbook['"]/)
  assert.match(routerSource, /title:\s*['"]世界书 \/ Living Story['"]/)
  assert.match(routerSource, /keepAlive:\s*true/)
  assert.match(routerSource, /views\/worldbook\/WorldbookView\.vue/)

  assert.match(sideMenuSource, /key:\s*['"]\/worldbook['"]/)
  assert.match(sideMenuSource, /BookOutline/)
  assert.match(sideMenuSource, /学习与记忆/)

  assert.equal(MENU_PLUGIN_REQUIREMENTS['/worldbook'], 'worldbook')
  assert.equal(isPluginMenuRouteVisible('/worldbook', new Set()), false)
  assert.equal(isPluginMenuRouteVisible('/worldbook', new Set(['worldbook'])), true)
  assert.equal(
    isPluginMenuRouteVisible('/worldbook', new Set(['sticker', 'knowledge'])),
    false,
  )
})

test('worldbook client is GET-only against the snapshot endpoint', async () => {
  const clientSource = await readFile(
    new URL('../src/api/worldbook.ts', import.meta.url),
    'utf8',
  )
  const viewSource = await readFile(
    new URL('../src/views/worldbook/WorldbookView.vue', import.meta.url),
    'utf8',
  )
  const { fetchWorldbookSnapshot, WORLDBOOK_SNAPSHOT_PATH } = await loadWorldbookApi()

  assert.equal(typeof fetchWorldbookSnapshot, 'function')
  assert.match(clientSource, /export async function fetchWorldbookSnapshot/)
  assert.match(clientSource, new RegExp(WORLDBOOK_SNAPSHOT_PATH.replace(/\//g, '\\/')))
  assert.match(clientSource, /api<WorldbookSnapshot>\(WORLDBOOK_SNAPSHOT_PATH\)/)

  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE']) {
    assert.doesNotMatch(
      clientSource,
      new RegExp(`method:\\s*['"]${method}['"]`, 'i'),
    )
    assert.doesNotMatch(
      viewSource,
      new RegExp(`method:\\s*['"]${method}['"]`, 'i'),
    )
  }
  assert.doesNotMatch(clientSource, /api\([^)]*\{\s*method/)
  assert.doesNotMatch(viewSource, /api\([^)]*\{\s*method/)
  assert.match(viewSource, /fetchWorldbookSnapshot/)
})

test('worldbook helper mapping covers gates, unavailable copy, and lifecycle tones', async () => {
  const {
    gateMetricRows,
    unavailableCopy,
    reasonLabel,
    lifecycleStatusTone,
    formatTimestamp,
  } = await loadWorldbookApi()

  const emptyGates = gateMetricRows(null)
  assert.equal(emptyGates.length, 4)
  assert.equal(emptyGates[0]?.value, '—')

  const gated = gateMetricRows({
    enabled: true,
    chat_projection_enabled: true,
    schedule_projection_enabled: false,
    storylet_enabled: false,
    dream_proposal_enabled: true,
    social_evidence_enabled: false,
    allowlist_count: 2,
    total_budget_chars: 1200,
    max_setbacks_per_arc: 1,
    max_events_per_tick: 3,
    state_dir: 'storage/worldbook',
    canon_dir: 'config/worldbook/canon',
    storylet_dir: 'config/worldbook/storylets',
  })
  assert.equal(gated[0]?.value, '开')
  assert.equal(gated[0]?.accent, 'success')
  assert.match(String(gated[0]?.hint), /allowlist 2/)
  assert.equal(gated[2]?.value, '关')

  const disabled = unavailableCopy('worldbook_disabled')
  assert.match(disabled.title, /不可用/)
  assert.match(disabled.description, /不会自动跳转|直达/)

  const unmounted = unavailableCopy('runtime_not_mounted')
  assert.match(unmounted.title, /未挂载/)

  assert.equal(reasonLabel('ok'), '正常')
  assert.match(reasonLabel('snapshot_failed:ValueError'), /快照构建失败/)
  assert.equal(lifecycleStatusTone('rejected'), 'error')
  assert.equal(lifecycleStatusTone('committed'), 'success')
  assert.equal(lifecycleStatusTone('validated'), 'success')
  assert.equal(lifecycleStatusTone('pass'), 'success')
  assert.equal(lifecycleStatusTone('fail'), 'error')
  assert.equal(lifecycleStatusTone('missing'), 'warning')
  assert.equal(formatTimestamp(''), '—')
  assert.notEqual(formatTimestamp('2026-07-18T12:00:00Z'), '—')
})

test('worldbook view is read-only Calm Ops console with required sections and states', async () => {
  const viewSource = await readFile(
    new URL('../src/views/worldbook/WorldbookView.vue', import.meta.url),
    'utf8',
  )

  for (const token of [
    'AppPage',
    'MetricCard',
    'PageToolbar',
    'AppPanelSection',
    'EmptyState',
    'AppCard',
  ]) {
    assert.match(viewSource, new RegExp(token))
  }

  assert.match(viewSource, /刷新/)
  assert.match(viewSource, /@click="refresh"/)
  assert.doesNotMatch(viewSource, /NSwitch|v-model:value|NForm\b|NModal\b/)
  assert.doesNotMatch(viewSource, /commit proposal|shadow run|handleCommit|handleSave|handleDelete/i)
  assert.doesNotMatch(viewSource, /@click=".*(commit|save|delete|propose)/i)
  // Only action binding should be refresh
  const clickHandlers = [...viewSource.matchAll(/@click="([^"]+)"/g)].map(m => m[1])
  assert.deepEqual(clickHandlers, ['refresh'])

  assert.match(viewSource, /活跃故事栈|Main|Side|Ambient/)
  assert.match(viewSource, /Life TTL|已过期/)
  assert.match(viewSource, /提案|决策|提交|Proposals|Decisions|Commits/)
  assert.match(viewSource, /Traces|Shadow Verdict|注册表/)
  assert.match(viewSource, /世界书插件不可用|无法读取世界书快照|正在拉取快照/)
  assert.match(viewSource, /暂无 worldbook trace|Shadow 不可用|故事栈为空/)

  assert.doesNotMatch(viewSource, /!important/)
  assert.doesNotMatch(viewSource, /linear-gradient|radial-gradient/)
  assert.doesNotMatch(viewSource, /#[0-9a-fA-F]{3,8}\b/)
  assert.doesNotMatch(viewSource, /rgb\(\s*\d/)
  assert.doesNotMatch(viewSource, /style="/)

  assert.match(viewSource, /@media \(max-width: 900px\)/)
  assert.match(viewSource, /var\(--om-text-1\)|var\(--om-text-2\)|var\(--om-surface-2\)/)
  assert.match(viewSource, /@vicons\/ionicons5/)
})

test('worldbook operational text and actions use readable local contracts', async () => {
  const viewSource = await readFile(
    new URL('../src/views/worldbook/WorldbookView.vue', import.meta.url),
    'utf8',
  )

  assert.doesNotMatch(viewSource, /var\(--om-text-3\)/)
  assert.match(viewSource, /const readableTagColor\s*=\s*\{[\s\S]*textColor:\s*'var\(--om-text-1\)'/)
  assert.match(viewSource, /class="wb-refresh"/)
  assert.match(viewSource, /\.wb-refresh\s*\{[\s\S]*min-width:\s*44px;[\s\S]*min-height:\s*44px;/)
  assert.match(viewSource, /<AppPage[\s\S]*class="wb-console"/)
  assert.match(viewSource, /<NTag size="small" :bordered="false" :color="readableTagColor">/)
  assert.match(
    viewSource,
    /\.wb-console\s+:deep\(\.om-panel-section__eyebrow\)\s*\{[\s\S]*color:\s*var\(--om-text-2\);/,
  )
})

test('worldbook snapshot DTO fields match backend contract surface', async () => {
  const clientSource = await readFile(
    new URL('../src/api/worldbook.ts', import.meta.url),
    'utf8',
  )

  for (const field of [
    'available',
    'reason',
    'gates',
    'registry',
    'ledger',
    'life',
    'lifecycle',
    'block_traces',
    'shadow',
    'chat_projection_enabled',
    'dream_proposal_enabled',
    'allowlist_count',
    'runtime_loaded',
    'main_count',
    'expired_count',
    'proposal_count',
    'overall_verdict',
    'payload_keys',
    'reason_code',
    'severity',
  ]) {
    assert.match(clientSource, new RegExp(field))
  }

  // Read-only: never invent mutation endpoints
  assert.doesNotMatch(clientSource, /\/worldbook\/(commit|propose|process|shadow)/)
})
