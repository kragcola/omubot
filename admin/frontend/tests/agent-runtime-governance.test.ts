import assert from 'node:assert/strict'
import { readFile, readdir } from 'node:fs/promises'
import test from 'node:test'

type SourceEntry = Readonly<{
  path: string
  source: string
}>

async function loadAgentRuntimeApi() {
  try {
    return await import('../src/api/agentRuntime.ts')
  }
  catch (error) {
    assert.fail(
      'agentRuntime.ts must load under Node without resolving the browser client; '
      + `move the client behind a dynamic import: ${String(error)}`,
    )
  }
}

async function readRequiredSource(relativePath: string, label: string) {
  try {
    return await readFile(new URL(relativePath, import.meta.url), 'utf8')
  }
  catch (error) {
    assert.fail(`${label} source is required: ${String(error)}`)
  }
}

async function readAgentRuntimeVueSources(): Promise<readonly SourceEntry[]> {
  const root = new URL('../src/views/agent-runtime/', import.meta.url)
  let paths: string[]
  try {
    paths = (await readdir(root, { recursive: true }))
      .map(path => String(path))
      .filter(path => path.endsWith('.vue'))
      .sort()
  }
  catch (error) {
    assert.fail(`Agent Runtime view directory is required: ${String(error)}`)
  }
  assert.ok(paths.length > 0, 'Agent Runtime must have at least one Vue view source')
  return Promise.all(paths.map(async path => ({
    path,
    source: await readFile(new URL(path, root), 'utf8'),
  })))
}

function exportBlock(source: string, kind: 'interface' | 'function', name: string) {
  const marker = kind === 'interface'
    ? new RegExp(`export\\s+(?:interface|type)\\s+${name}\\b`)
    : new RegExp(`export\\s+(?:async\\s+)?function\\s+${name}\\b`)
  const start = source.search(marker)
  assert.notEqual(start, -1, `agentRuntime.ts must export ${name}`)
  const following = source.slice(start + 1).search(/\nexport\s+/)
  return following === -1
    ? source.slice(start)
    : source.slice(start, start + 1 + following)
}

function interfaceFields(source: string, name: string) {
  const block = exportBlock(source, 'interface', name)
  return [...block.matchAll(/^\s*([A-Za-z_][A-Za-z0-9_]*)\??\s*:/gm)]
    .map(match => match[1])
}

function assertInOrder(source: string, tokens: readonly string[], label: string) {
  let cursor = 0
  for (const token of tokens) {
    const index = source.indexOf(token, cursor)
    assert.notEqual(index, -1, `${label} must contain ${token} after the prior step`)
    cursor = index + token.length
  }
}

function assertSourceMatches(source: string, pattern: RegExp, message: string) {
  pattern.lastIndex = 0
  assert.ok(pattern.test(source), message)
}

function assertSourceExcludes(source: string, pattern: RegExp, message: string) {
  pattern.lastIndex = 0
  assert.ok(!pattern.test(source), message)
}

test('agent runtime pure helpers load under Node and expose canonical paths', async () => {
  const subject = await loadAgentRuntimeApi()
  assert.equal(typeof subject.AGENT_RUNTIME_GOVERNANCE_PATHS, 'object')
  assert.equal(typeof subject.classifyReadinessGate, 'function')

  const paths = subject.AGENT_RUNTIME_GOVERNANCE_PATHS
  assert.equal(
    paths.toolApprovalContext('call id/7'),
    '/api/admin/agent-runtime/tool-calls/call%20id%2F7/approval/context',
  )
  assert.equal(
    paths.toolApprovalAction('call id/7'),
    '/api/admin/agent-runtime/tool-calls/call%20id%2F7/approval',
  )
  assert.equal(
    paths.reconciliationContext('call id/8'),
    '/api/admin/agent-runtime/tool-calls/call%20id%2F8/reconciliation/context',
  )
  assert.equal(
    paths.reconciliationAction('call id/8'),
    '/api/admin/agent-runtime/tool-calls/call%20id%2F8/reconciliation',
  )
  assert.equal(
    paths.memoryCandidateContext('mcand id/9'),
    '/api/admin/memory-governance/candidates/mcand%20id%2F9/decision/context',
  )
  assert.equal(
    paths.memoryCandidateAction('mcand id/9'),
    '/api/admin/memory-governance/candidates/mcand%20id%2F9/decision',
  )
  assert.equal(
    paths.worldbookSummary,
    '/api/admin/worldbook-governance/summary',
  )
  assert.equal(
    paths.worldbookList,
    '/api/admin/worldbook-governance/proposals',
  )
  assert.equal(
    paths.worldbookDetail('world proposal/10'),
    '/api/admin/worldbook-governance/proposals/world%20proposal%2F10',
  )
})

test('readiness classifier treats unknown evidence as not assessed', async () => {
  const { classifyReadinessGate } = await loadAgentRuntimeApi()
  assert.deepEqual(classifyReadinessGate('ready'), {
    label: '就绪',
    tone: 'success',
  })
  assert.deepEqual(classifyReadinessGate('not_ready'), {
    label: '未就绪',
    tone: 'error',
  })
  for (const status of ['not_assessed', 'unknown', '', undefined, false]) {
    assert.deepEqual(classifyReadinessGate(status), {
      label: '未评估',
      tone: 'warning',
    })
  }
})

test('decision, readiness, and Worldbook DTOs freeze the bounded surface', async () => {
  const source = await readRequiredSource(
    '../src/api/agentRuntime.ts',
    'Agent Runtime API',
  )
  const requiredFields: Readonly<Record<string, readonly string[]>> = {
    DecisionContextResource: ['kind', 'id'],
    DecisionContextPreview: [
      'tool_name',
      'effect',
      'projection_kind',
      'operation',
      'conflict_count',
      'conflict_ids',
    ],
    DecisionContext: [
      'contract_version',
      'schema_version',
      'mode',
      'report_only',
      'resource',
      'state',
      'updated_at',
      'preview',
      'preview_digest',
      'expected_token',
    ],
    ReadinessGate: ['status', 'reason', 'evidence_at'],
    ReadinessReport: [
      'contract_version',
      'schema_version',
      'mode',
      'status',
      'report_only',
      'activation_authorized',
      'gates',
    ],
    WorldbookGovernanceSummary: [
      'proposal_count',
      'pending_count',
      'approved_count',
      'rejected_count',
      'committed_count',
    ],
    WorldbookGovernanceProvenance: [
      'source_binding_sha256',
      'evidence_count',
      'time_basis',
    ],
    WorldbookGovernanceProposal: [
      'contract_version',
      'proposal_id',
      'proposal_sha256',
      'event_id',
      'world_id',
      'source_kind',
      'status',
      'decision_present',
      'receipt_present',
      'occurred_at',
      'proposed_at',
      'provenance',
    ],
    WorldbookGovernanceList: ['items', 'next_cursor'],
    WorldbookGovernanceDetail: ['item'],
  }

  for (const [name, fields] of Object.entries(requiredFields)) {
    const block = exportBlock(source, 'interface', name)
    for (const field of fields) {
      assertSourceMatches(
        block,
        new RegExp(`\\b${field}\\??\\s*:`),
        `${name}.${field} is required`,
      )
    }
  }
  for (const name of [
    'WorldbookGovernanceSummary',
    'WorldbookGovernanceList',
    'WorldbookGovernanceDetail',
  ]) {
    assertSourceMatches(
      exportBlock(source, 'interface', name),
      /extends\s+AdminEnvelope\b/,
      `${name} must retain the public Admin envelope`,
    )
  }
  assertSourceMatches(
    exportBlock(source, 'interface', 'WorldbookGovernanceList'),
    /items\??\s*:\s*WorldbookGovernanceProposal\[\]/,
    'Worldbook list items must use the proposal DTO',
  )
  assertSourceMatches(
    exportBlock(source, 'interface', 'WorldbookGovernanceDetail'),
    /item\??\s*:\s*WorldbookGovernanceProposal\s*\|\s*null/,
    'Worldbook detail item must use the proposal DTO',
  )

  for (const name of [
    'DecisionContext',
    'WorldbookGovernanceSummary',
    'WorldbookGovernanceProvenance',
    'WorldbookGovernanceProposal',
    'WorldbookGovernanceList',
    'WorldbookGovernanceDetail',
  ]) {
    const block = exportBlock(source, 'interface', name)
    for (const forbidden of [
      'args_digest',
      'target_ref',
      'principal_id',
      'metadata',
      'raw_evidence',
      'payload',
      'notes',
      'arc_id',
      'consequences',
      'db_path',
      'event',
      'event_payload',
      'evidence',
      'evidence_message_id',
      'evidence_ref',
      'group_id',
      'name',
      'operator_ref',
      'quote',
      'reason_code',
      'source_binding',
      'source_ref',
      'summary',
      'user_id',
      'variable_deltas',
    ]) {
      assertSourceExcludes(
        block,
        new RegExp(`\\b${forbidden}\\b`),
        `${name} must not expose ${forbidden}`,
      )
    }
  }
})

test('client exports three context GETs, three token POSTs, and Worldbook reads', async () => {
  const source = await readRequiredSource(
    '../src/api/agentRuntime.ts',
    'Agent Runtime API',
  )
  assertSourceExcludes(
    source,
    /^\s*import\s+.*(?:client|request|utils\/api)/m,
    'browser client must not be resolved while pure helpers load in Node',
  )
  assertSourceMatches(
    source,
    /await\s+import\(['"][^'"]*(?:client|request|utils\/api)[^'"]*['"]\)/,
    'network functions must dynamically import the browser client',
  )

  const getFunctions = [
    'fetchToolApprovalContext',
    'fetchReconciliationContext',
    'fetchMemoryCandidateContext',
    'fetchWorldbookGovernanceSummary',
    'fetchWorldbookGovernanceList',
    'fetchWorldbookGovernanceDetail',
  ]
  for (const name of getFunctions) {
    const block = exportBlock(source, 'function', name)
    assertSourceExcludes(
      block,
      /method\s*:\s*['"](?:POST|PUT|PATCH|DELETE)['"]/i,
      `${name} must stay GET-only`,
    )
    if (name === 'fetchWorldbookGovernanceList') {
      for (const filter of ['limit', 'cursor', 'world_id', 'source_kind', 'status']) {
        assertSourceMatches(
          block,
          new RegExp(`\\b${filter}\\b`),
          `Worldbook proposal list must forward ${filter}`,
        )
      }
    }
  }

  const postFunctions = [
    'approveToolCall',
    'reconcileToolCall',
    'decideMemoryCandidate',
  ]
  for (const name of postFunctions) {
    const block = exportBlock(source, 'function', name)
    assertSourceMatches(block, /method\s*:\s*['"]POST['"]/i, `${name} must POST`)
    assertSourceMatches(block, /expected_token/, `${name} must send expected_token`)
    if (name === 'decideMemoryCandidate') {
      assertSourceExcludes(
        block,
        /\bconflict_ids\b/,
        'Memory decision POST must not forward preview conflict_ids',
      )
    }
  }

  const requestFields: Readonly<Record<string, readonly string[]>> = {
    ToolApprovalDecisionRequest: ['expected_token', 'approval_ref'],
    ReconciliationDecisionRequest: [
      'expected_token',
      'decision',
      'evidence_ref',
      'operator_note',
      'external_id',
    ],
    MemoryCandidateDecisionRequest: [
      'expected_token',
      'decision',
      'reason_code',
      'operator_note',
      'occurred_at',
    ],
  }
  const forbiddenTrustFields = [
    'operator',
    'operator_id',
    'principal',
    'principal_id',
    'adapter',
    'adapter_id',
    'target',
    'target_ref',
    'args',
    'args_digest',
  ]
  for (const [name, expected] of Object.entries(requestFields)) {
    assert.deepEqual(interfaceFields(source, name).sort(), [...expected].sort())
    const block = exportBlock(source, 'interface', name)
    for (const forbidden of forbiddenTrustFields) {
      assertSourceExcludes(
        block,
        new RegExp(`\\b${forbidden}\\b`),
        `${name} must not accept browser-supplied ${forbidden}`,
      )
    }
  }
})

test('sensitive governance requests use transient operator credentials', async () => {
  const apiSource = await readRequiredSource(
    '../src/api/agentRuntime.ts',
    'Agent Runtime API',
  )
  const clientSource = await readRequiredSource(
    '../src/api/client.ts',
    'Admin API client',
  )
  const entries = await readAgentRuntimeVueSources()
  const drawer = entries.find(entry => /GovernanceDecisionDrawer\.vue$/.test(entry.path))
  assert.ok(drawer, 'GovernanceDecisionDrawer.vue is required')

  assert.deepEqual(
    interfaceFields(apiSource, 'AgentRuntimeOperatorCredentials').sort(),
    ['credential', 'operatorId'],
  )
  const headerBuilder = exportBlock(apiSource, 'function', 'buildAgentRuntimeOperatorHeaders')
  assertSourceMatches(headerBuilder, /X-Agent-Runtime-Operator-Id/, 'operator ID must travel in a header')
  assertSourceMatches(headerBuilder, /Authorization/, 'operator credential must travel in Authorization')
  assertSourceMatches(headerBuilder, /Bearer/, 'operator credential must use Bearer authorization')

  for (const name of [
    'fetchToolApprovalContext',
    'fetchReconciliationContext',
    'fetchMemoryCandidateContext',
    'approveToolCall',
    'reconcileToolCall',
    'decideMemoryCandidate',
  ]) {
    const block = exportBlock(apiSource, 'function', name)
    assertSourceMatches(
      block,
      /AgentRuntimeOperatorCredentials/,
      `${name} must require transient operator credentials`,
    )
    assertSourceMatches(
      block,
      /headers\s*:\s*buildAgentRuntimeOperatorHeaders\(/,
      `${name} must send operator credentials only as headers`,
    )
  }
  assertSourceExcludes(
    apiSource,
    /(?:body|params)\s*:\s*[^\n]*(?:operatorId|credential)/,
    'operator credentials must not be sent in request bodies or URLs',
  )

  assertSourceMatches(
    clientSource,
    /x-agent-runtime-operator-id/i,
    'the shared client must recognize operator-authenticated requests',
  )
  assertSourceMatches(
    clientSource,
    /response\.status\s*===\s*401[\s\S]{0,500}!(?:isAgentRuntimeOperatorRequest|hasAgentRuntimeOperator)/,
    'operator credential failures must not clear the web session',
  )

  assertSourceMatches(drawer.source, /operatorId/, 'drawer must keep an operator ID in memory')
  assertSourceMatches(drawer.source, /operatorCredential/, 'drawer must keep a credential in memory')
  assertSourceMatches(drawer.source, /type="password"/, 'drawer credential input must be masked')
  assertSourceMatches(drawer.source, /hasOperatorCredentials/, 'empty credentials must block context fetches')
  assertSourceMatches(drawer.source, /operatorAuthenticationFailed/, 'drawer must model credential failures')
  assertSourceMatches(drawer.source, /status\s*===\s*401/, 'drawer must classify credential HTTP 401 errors')
  assertSourceMatches(
    drawer.source,
    /watch\([\s\S]{0,500}(?:operatorId|operatorCredential)[\s\S]{0,800}clearDecisionContext/,
    'credential changes must discard old decision context',
  )
  assertSourceExcludes(
    drawer.source,
    /localStorage|sessionStorage|indexedDB|document\.cookie/,
    'operator credentials must not be persisted by the drawer',
  )
})

test('governance workflow reads context before confirm and refreshes after POST', async () => {
  const entries = await readAgentRuntimeVueSources()
  const source = entries.map(entry => entry.source).join('\n')

  for (const token of [
    'fetchToolApprovalContext',
    'fetchReconciliationContext',
    'fetchMemoryCandidateContext',
    'approveToolCall',
    'reconcileToolCall',
    'decideMemoryCandidate',
    'expected_token',
    'NModal',
    'NForm',
  ]) {
    assertSourceMatches(
      source,
      new RegExp(token),
      `governance workflow requires ${token}`,
    )
  }

  for (const [label, sequence] of Object.entries({
    approval: ['fetchToolApprovalContext', 'approveToolCall', 'refresh'],
    reconciliation: ['fetchReconciliationContext', 'reconcileToolCall', 'refresh'],
    memory: ['fetchMemoryCandidateContext', 'decideMemoryCandidate', 'refresh'],
  })) {
    assertInOrder(source, sequence, label)
  }

  const decisionDrawer = entries.find(entry => /GovernanceDecisionDrawer\.vue$/.test(entry.path))
  assert.ok(decisionDrawer, 'GovernanceDecisionDrawer.vue is required')
  assertSourceMatches(
    decisionDrawer.source,
    /preview\??\.conflict_ids\b/,
    'Memory context preview must display opaque conflict_ids for operator review',
  )
  assertSourceExcludes(
    decisionDrawer.source,
    /\bconflict_ids\s*:/,
    'GovernanceDecisionDrawer POST must not forward preview conflict_ids',
  )
  assertSourceExcludes(
    decisionDrawer.source,
    /v-model(?::value)?[^>]*conflict_ids|conflict_ids[^>]*v-model(?::value)?/,
    'preview conflict_ids must not become browser-editable input',
  )

  assertSourceMatches(source, /exact_retry/, 'workflow must surface exact_retry')
  assertSourceMatches(
    source,
    /重复提交|已经记录|已记录|无需重复/,
    'workflow must explain an exact retry',
  )
  assertSourceMatches(
    source,
    /(?:status|statusCode|response\.status)\s*={0,3}\s*409|\b409\b/,
    'workflow must classify HTTP 409 as stale context',
  )
  assertSourceMatches(
    source,
    /stale_context|上下文已过期|上下文失效|重新获取/,
    'workflow must tell the operator that stale context is being refreshed',
  )
  assertSourceMatches(
    source,
    /(?:\b409\b[\s\S]{0,1200}(?:fetchToolApprovalContext|fetchReconciliationContext|fetchMemoryCandidateContext)|(?:fetchToolApprovalContext|fetchReconciliationContext|fetchMemoryCandidateContext)[\s\S]{0,1200}\b409\b)/,
    'HTTP 409 handling must fetch a fresh decision context',
  )
  assertSourceMatches(
    source,
    /actions_unavailable|actionsUnavailable|actionAvailability/,
    'workflow must model unavailable actions',
  )
  assertSourceMatches(source, /只读|readOnly|readonly/, 'unavailable actions must be read-only')
  assertSourceMatches(
    source,
    /(?:\b503\b[\s\S]{0,800}(?:只读|readOnly|readonly)|(?:只读|readOnly|readonly)[\s\S]{0,800}\b503\b)/,
    'HTTP 503 actions unavailable must enter read-only mode',
  )
})

test('stale context refresh failure clears authority and enters read-only mode', async () => {
  const entries = await readAgentRuntimeVueSources()
  const source = entries.map(entry => entry.source).join('\n')
  const contextFetch = /fetch(?:ToolApproval|Reconciliation|MemoryCandidate)Context\s*\(/
  const stalePositions = [...source.matchAll(/\b409\b/g)].map(match => match.index)
  assert.ok(stalePositions.length > 0, 'governance workflow must handle POST 409')

  const recoveryWindows = stalePositions.map((position) => {
    const start = Math.max(0, position - 800)
    return source.slice(start, position + 4_000)
  })
  const recovery = recoveryWindows.find((window) => {
    contextFetch.lastIndex = 0
    return contextFetch.test(window) && /\bcatch\b|\bclassify[A-Za-z0-9_$]*\s*\(/.test(window)
  })
  assert.ok(
    recovery,
    'POST 409 recovery must classify errors from the refreshed context GET',
  )

  assertSourceMatches(
    recovery,
    /(?:[A-Za-z_$][\w$]*(?:context|Context|token|Token)[\w$]*(?:\.value)?\s*=\s*(?:null|undefined|['"]['"])|delete\s+[A-Za-z_$][\w$]*(?:\.value)?(?:\.preview)?\.expected_token|(?:clear|reset|discard|invalidate)[A-Za-z0-9_$]*(?:Context|Token)\s*\()/,
    'failed stale-context refresh must clear or invalidate the prior context/token',
  )

  assertSourceMatches(
    source,
    /(?:\b503\b[\s\S]{0,600}actions_unavailable|actions_unavailable[\s\S]{0,600}\b503\b)/,
    'refresh HTTP 503 must classify as actions_unavailable',
  )
  assertSourceMatches(
    source,
    /(?:actions_unavailable[\s\S]{0,600}(?:readOnly|readonly|只读)|(?:readOnly|readonly|只读)[\s\S]{0,600}actions_unavailable)/,
    'actions_unavailable must drive the governance UI into read-only mode',
  )
})

test('readiness renders attested gates and never infers absence from false', async () => {
  const entries = await readAgentRuntimeVueSources()
  const source = entries.map(entry => entry.source).join('\n')

  for (const token of [
    'classifyReadinessGate',
    'not_assessed',
    '未评估',
    'evidence_at',
    'reason',
  ]) {
    assertSourceMatches(source, new RegExp(token), `readiness UI requires ${token}`)
  }
  assertSourceExcludes(
    source,
    /production_database_present/,
    'readiness must not infer database absence',
  )
  assertSourceExcludes(
    source,
    /production_migration_present/,
    'readiness must not infer migration absence',
  )
  assertSourceExcludes(
    source,
    /production_runtime_wiring_present/,
    'readiness must not infer wiring absence',
  )
  assertSourceExcludes(
    source,
    /===\s*false[\s\S]{0,80}未创建/,
    'false must never be rendered as an unattested absence claim',
  )
})

test('Worldbook governance panel is bounded and read-only', async () => {
  const entries = await readAgentRuntimeVueSources()
  const panel = entries.find(entry => /WorldbookGovernancePanel\.vue$/.test(entry.path))
  assert.ok(panel, 'WorldbookGovernancePanel.vue is required for the read-only tab')
  const source = panel.source
  const combinedSource = entries.map(entry => entry.source).join('\n')

  assertSourceMatches(
    combinedSource,
    /<NTabPane\b[^>]*(?:name="worldbook"|tab="[^"]*(?:Worldbook|世界书)[^"]*")/,
    'Agent Runtime must expose a Worldbook governance tab',
  )
  assertSourceMatches(
    combinedSource,
    /<WorldbookGovernancePanel\b/,
    'Worldbook governance tab must mount its read-only panel',
  )

  for (const token of [
    'fetchWorldbookGovernanceSummary',
    'fetchWorldbookGovernanceList',
    'fetchWorldbookGovernanceDetail',
    '只读',
  ]) {
    assertSourceMatches(source, new RegExp(token), `Worldbook panel requires ${token}`)
  }
  assertSourceMatches(source, /limit\s*:\s*(?:50|100)\b/, 'Worldbook list must be bounded')
  assertSourceExcludes(
    source,
    /approveToolCall|reconcileToolCall|decideMemoryCandidate/,
    'Worldbook panel must not own mutation actions',
  )
  assertSourceExcludes(
    source,
    /method\s*:\s*['"](?:POST|PUT|PATCH|DELETE)['"]/i,
    'Worldbook panel must remain GET-only',
  )
  assertSourceExcludes(
    source,
    /raw_evidence|args_digest|target_ref|principal_id|metadata|payload|notes|arc_id|consequences|db_path|event_payload|evidence_message_id|evidence_ref|group_id|operator_ref|quote|reason_code|source_ref|user_id|variable_deltas/,
    'Worldbook panel must remain bounded and redacted',
  )
})

test('new governance actions have accessible 44 by 44 pixel targets', async () => {
  const entries = await readAgentRuntimeVueSources()
  const source = entries.map(entry => entry.source).join('\n')
  const actionButtons = [...source.matchAll(/<NButton\b[^>]*class="[^"]*governance-action[^"]*"[^>]*>/g)]
    .map(match => match[0])
  assert.ok(actionButtons.length >= 3, 'at least three governance action buttons are required')
  for (const button of actionButtons) {
    assertSourceMatches(button, /aria-label="[^"]+"/, 'governance button needs aria-label')
    assertSourceMatches(button, /@click=/, 'governance button needs a click command')
  }

  const actionStyle = source.match(
    /(?:\.governance-action|:deep\(\.governance-action\))\s*\{[\s\S]*?\}/,
  )?.[0] ?? ''
  assertSourceMatches(actionStyle, /min-width\s*:\s*44px/, 'action target width must be 44px')
  assertSourceMatches(actionStyle, /min-height\s*:\s*44px/, 'action target height must be 44px')
})
