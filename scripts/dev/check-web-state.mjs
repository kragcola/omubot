// Exercise actual TypeScript state modules without a browser or external network.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import test from 'node:test';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const webRequire = createRequire(resolve(root, 'web/package.json'));
const ts = webRequire('typescript');

function modules(fetch, hooks = {}) {
  const cache = new Map();
  function load(name) {
    if (name === 'vue') {
      const vue = webRequire('vue');
      return {
        ...vue,
        onMounted() {},
        onBeforeUnmount(callback) { hooks.cleanup = callback; },
        watch: hooks.watch ?? vue.watch,
      };
    }
    if (name === 'naive-ui') return {};
    const file = resolve(root, 'web/src', name.replace(/^@\//, '') + (name.endsWith('.vue') ? '' : '.ts'));
    if (cache.has(file)) return cache.get(file).exports;
    const module = { exports: {} };
    cache.set(file, module);
    let source = readFileSync(file, 'utf8');
    if (file.endsWith('.vue')) {
      const names = file.endsWith('/ContactConsentPanel.vue')
        ? 'load, change, snapshot, userId, groupId, error, notice, loading, saving'
        : file.endsWith('/ContactSettingsPanel.vue')
        ? 'enabled, identity, windows, sendInterval, sendLimit, decisionInterval, decisionLimit, saveEndpoint'
        : file.endsWith('/AutoLearningManagement.vue')
        ? 'group, loadedGroup, status, page, busy, error, canWrite, load, mutate'
        : file.endsWith('/StyleManagement.vue')
        ? 'group, loadedGroup, snapshot, busy, error, canWrite, load, profile, disable, feedback'
        : file.endsWith('/MattersManagement.vue')
        ? 'groupInput, subjectFilter, stateFilter, loadedQuery, page, selected, matterIdInput, busy, needsReload, error, notice, cancelReason, replacing, draft, loadedForCurrentQuery, canLoad, canWrite, canLoadMore, canRead, draftValid, canPropose, load, read, propose, canTransition, transition, startReplacement, clearDraft, clear, timeLabel, reasonLabel'
        : file.endsWith('/ObservationManagement.vue')
        ? 'groupInput, loadedGroup, observationPage, jobPage, slangStatus, governancePage, reviewDrafts, busy, jobsFresh, governanceFresh, error, actionError, notice, loadedForCurrentGroup, load, setReviewDraft, reviewDraft, canReviewJob, submitReview, disableSlangReview, canDisableSlang, canRevokeSuggestion, revokeSuggestion'
        : file.endsWith('/MemoryIdentityEpisodeManagement.vue')
        ? 'groupInput, surfaceInput, aliasAtInput, alias, aliasBusy, aliasError, subjectInput, familiarity, familiarityBusy, familiarityError, familiarityScore, familiarityNeedsReload, canAdjustFamiliarity, adjustFamiliarity, candidateInput, episode, loadedGroup, loadedCandidate, episodeBusy, episodeError, episodeNotice, needsReload, decayDraft, reasonDraft, canResolve, canLoadFamiliarity, canLoadEpisode, episodeDirty, loadedForCurrentQuery, canWriteEpisode, decayValid, resolveAlias, loadFamiliarity, loadEpisode, canTransition, mutateEpisode'
        : file.includes('/journal/')
        ? 'group, loadedGroup, heads, status, consents, consentIds, deliveries, decisions, selected, sourceEvent, body, dryRun, approvalScope, publishMode, resolutionId, resolutionOutcome, resolutionReceipt, busy, needsReload, draftCurrent, error, notice, ready, dirty, editable, canCreate, canRevise, canReview, canApprove, approved, canDryRun, canPublish, canPreviewFiction, canPreviewFactual, canResolve, modeOptions, load, loadMoreHeads, loadMoreConsents, loadMoreDeliveries, select, chooseConsent, previewFiction, previewFactual, create, act, publish, chooseResolution, resolve, deliveryLabel, clearDraft'
        : file.includes('/diagnostics/')
        ? 'boardGroup, groupBoard, boardLoading, boardError, loadGroupBoard, diagnostics, isLoading, error, stale, topicEdges, topicEdgeError, boundedTopicEdges, loadDiagnostics, participation, contextObservations, contextObservationError, contextObservationStale, recentContextObservations, contextPathTotals, observationCount, contextPathLabel, aggregateHealth, healthError, healthStale'
        : file.includes('/memory/')
        ? 'groupIdInput, statusFilter, loadedGroupId, loadedStatus, candidates, nextCursor, loading, loadError, actionError, notice, busyCandidateId, searchCandidates, loadMore, reviewCandidate, applyCandidate, retrySocialExperience, canRetrySocial, canReview, canApply, socialCaptureStatusLabel, socialStoryStatusLabel, socialProgressNotice, hotGroupIdInput, hotSubjectIdInput, hotFacts, hotTruncated, hotLoading, hotError, hotQueried, searchHotFacts, factGroupIdInput, factSubjectIdInput, loadedFactGroupId, loadedFactSubjectId, facts, nextFactCursor, factsLoading, factsLoadError, factsActionError, factsNotice, busyFactId, correctionDrafts, correctionDraft, submitCorrection, searchFacts, requestDisableFact, confirmDisableFact, inspectConflictTarget, conflictCandidateId, conflictSourceVerified, conflictTargetFact, conflictLoading, conflictError, resolveConflict, retrievalGroupId, retrievalSubjectId, retrievalQuery, retrievalLoading, retrievalError, retrievalResult, runRetrievalDiagnostic, domainFailures, nextFailureCursor, failureLoading, failureLoadError, canLoadMoreFailures, readDomainFailures, loadMoreDomainFailures, failureEmptyDescription, extractionRuns, nextRunCursor, runLoading, runLoadError, canLoadMoreRuns, readExtractionRunDiagnostics, loadMoreExtractionRuns, runEmptyDescription, cardGroupInput, cardQueryInput, cardFilter, cardFactIdInput, cardPage, cardMetadata, cardCategoryDraft, cardBusy, cardError, cardNotice, canWriteCard, loadCards, mutateCard'
        : file.includes('/stickers/')
        ? 'group, snapshot, selected, description, metadataDirty, busy, error, needsReload, load, select, describe, saveMetadata, mutate'
        : file.includes('/knowledge/')
        ? 'group, loadedGroup, page, selectedSourceId, source, currentDocument, busy, needsReload, error, notice, confirmNonPersonal, draft, canWrite, canImport, canApprove, canActivate, load, selectSource, review, activation, removeSource, saveDocument, resetToNewImport'
        : file.includes('/dream/')
        ? 'groupIdInput, loadedGroupId, arcs, selectedArcId, proposals, nextCursor, loading, loadError, proposalError, actionError, notice, arcIdInput, arcTitleInput, arcStageInput, busyAction, decisionProposal, decisionKind, activeArcs, currentArc, canCreateArc, canGenerateDream, dreamModelReady, searchGroup, loadMore, createArc, generateDream, askDecision, confirmDecision, proposalStatusLabel'
        : file.includes('/runtime/')
        ? 'runtime, loading, error, notice, healthDetail, runtimeStale, confirmOpen, restartPhase, configDraftDirty, policyDraftDirty, editorBusy, hasUncommittedWork, isRestarting, canRestart, loadRuntime, restartCore, requestRestart, cancelPending'
        : file.includes('/persona/')
        ? 'draftName, draftInstructions, draftMode, draftSource, runningName, runningInstructions, runningMode, preview, previewBusy, previewError, hasOtherDraftChanges, canSave, updatePersonaField, updatePersonaMode, updatePersonaSource, previewPersona, savePersona, requestReload, confirmReload, cancelReload'
        : file.includes('/settings/')
        ? 'catalogKey, catalogModels, fetchCatalog, changeProfileFormat, changeProfileBoolean, changeConfigBoolean, runSave, modelAction, modelActionMessage, canSave, newGroupId, newGroupMode, groupModeError, addGroupMode, changeGroupMode, removeGroupMode, newGroupProfileId, groupProfileError, addGroupProfile, changeGroupReplyStyle, changeGroupCustomPrompt, removeGroupProfile, newCalendarGroupId, newCalendarName, newCalendarDate, newCalendarRuleKind, newCalendarMonth, newCalendarOrdinal, newCalendarWeekday, newCalendarLunarMonth, newCalendarLunarDay, newCalendarLunarLeapMonth, newCalendarCategory, newCalendarSubjectKind, newCalendarSubjectId, calendarCategoryOptions, calendarRuleKindOptions, calendarMonthOptions, calendarOrdinalOptions, calendarWeekdayOptions, calendarLunarMonthOptions, calendarLunarDayOptions, groupCalendarError, addGroupCalendarEvent, changeGroupCalendarEvent, changeGroupCalendarWeekday, changeGroupCalendarLunar, removeGroupCalendarEvent, changeWorldbookGate, changeJournalGroups, changeJournalLiveAccounts, newWorldbookGroupId, draftWorldbookGroups, addWorldbookGroup, removeWorldbookGroup, worldbookGroupError, draftWorldbookScheduleReady, runningWorldbookScheduleReady, draftStreamReplyStatus, runningStreamReplyStatus, changePlannedReplyGroups, draftPlannedReplyStatus, runningPlannedReplyStatus, scopedFeatures, changeScopedFeatureGroups, scopedFeatureStatus'
        : file.includes('/napcat/')
        ? 'status, token, logLines, logSampling, runtime, actionBusy, statusBusy, statusError, actionError, actionMessage, logBusy, connectNapcat, disconnectNapcat, sampleLogs, startLogSampling, stopLogSampling, canStartRuntime, canStopRuntime, startNapcat, stopNapcat, loadStatus'
        : 'catalogKey, catalogModels, fetchCatalog, changeProfileFormat, runSave, modelAction, modelActionMessage, canSave';
      source = source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1] + `\nexport {${names}};`;
    }
    const text = ts.transpileModule(source, {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    const localRequire = (dependency) => load(dependency.startsWith('.')
      ? '@/' + resolve(dirname(file), dependency).slice(resolve(root, 'web/src').length + 1)
      : dependency);
    vm.runInNewContext(text, { module, exports: module.exports, require: localRequire,
      fetch, Headers, Response, AbortController, console,
      defineProps: () => hooks.props ?? { disabled: false },
      defineEmits: () => hooks.emit ?? (() => {}),
      setTimeout: hooks.setTimeout ?? setTimeout,
      clearTimeout: hooks.clearTimeout ?? clearTimeout,
      URL, URLSearchParams, performance: hooks.performance ?? globalThis.performance, crypto: globalThis.crypto }, { filename: file });
    return module.exports;
  }
  return load;
}
const response = (data, status = 200) => new Response(JSON.stringify(data), { status });
function deferred() {
  let resolve;
  const promise = new Promise(r => { resolve = r; });
  return { promise, resolve };
}

function immediateTimeout(callback, ...args) {
  const handle = { cancelled: false };
  queueMicrotask(() => {
    if (!handle.cancelled) callback(...args);
  });
  return handle;
}

function immediateClearTimeout(handle) {
  if (handle) handle.cancelled = true;
}

function runtimeFixture(generation = 'old') {
  return {
    active_sessions: 0,
    effective_revision: 1,
    generation,
    instance_id: 'instance-test',
    last_restart_error: '',
    managed: true,
    pending: false,
    queue_depth: 0,
    restart_required: false,
    revision: 1,
    uptime_seconds: 10,
  };
}

async function nextHostTurn() {
  await new Promise(resolve => setImmediate(resolve));
}

test('Dream review stays group-scoped and requires explicit decisions before fiction commits', async () => {
  const calls = [];
  let arcRecords = [];
  const pending = {
    proposal_id: 'proposal-pending', group_id: 'group-31', target_arc_id: 'arc-main', target_arc_revision: 2,
    created_at: 100, kind: 'scene', summary: '待审核摘要', payload: { event_type: 'scene', details: 'fiction only' },
    decision_status: 'pending', reason: null, committed_event_id: null, committed_at: null,
  };
  const validated = { ...pending, proposal_id: 'proposal-validated', decision_status: 'validated' };
  const rejected = { ...pending, proposal_id: 'proposal-rejected', decision_status: 'rejected', reason: '管理员拒绝' };
  const load = modules(async (path, options = {}) => {
    const url = new URL(String(path), 'http://local');
    const method = options.method ?? 'GET';
    calls.push({ path: String(path), method, body: options.body ? JSON.parse(options.body) : null });
    if (url.pathname === '/api/admin/story/arcs' && method === 'GET') {
      return response({ items: arcRecords });
    }
    if (url.pathname === '/api/admin/dream/proposals' && method === 'GET') {
      assert.equal(url.searchParams.get('group_id'), 'group-31');
      assert.equal(url.searchParams.get('limit'), '32');
      return response({ items: [pending, validated, rejected], next_cursor: null });
    }
    if (url.pathname === '/api/admin/story/arcs' && method === 'POST') {
      const created = { arc_id: 'arc-new', role: 'main', title: '新 Arc', stage: 'active', status: 'active', revision: 1, group_ids: ['group-31'] };
      arcRecords = [created];
      return response(created);
    }
    if (url.pathname === '/api/admin/dream/accept') {
      return response({ ...pending, decision_status: 'validated', committed_event_id: 'event-fiction', committed_at: 101 });
    }
    if (url.pathname === '/api/admin/dream/reject') return response(rejected);
    if (url.pathname === '/api/admin/dream/propose') return response({ ...pending, proposal_id: 'proposal-generated' });
    return response({ error: 'unexpected_request' }, 404);
  });
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  session.sessionState.status = { mode: 'offline', model_config: { task_bindings: {} } };
  const view = load('@/features/dream/DreamView.vue');

  view.groupIdInput.value = '  group-31  ';
  await view.searchGroup();
  assert.equal(view.loadedGroupId.value, 'group-31');
  assert.equal(view.currentArc.value, null);
  assert.equal(view.canGenerateDream.value, false);
  assert.equal(view.proposalStatusLabel(validated), '管理员已接受 · 待提交重试');
  assert.equal(view.proposalStatusLabel({ ...validated, committed_event_id: 'event-fiction' }), '管理员已接受并提交');
  assert.match(readFileSync(resolve(root, 'web/src/app/router.ts'), 'utf8'), /path: 'dream',[^\n]*requiresAdmin: true/);

  view.arcIdInput.value = 'arc-new';
  view.arcTitleInput.value = '新 Arc';
  view.arcStageInput.value = 'active';
  assert.equal(view.canCreateArc.value, true);
  await view.createArc();
  assert.equal(calls.find(call => call.method === 'POST' && call.path === '/api/admin/story/arcs').body.group_id, 'group-31');
  assert.equal(view.currentArc.value.arc_id, 'arc-new');
  assert.equal(view.canCreateArc.value, false);
  await view.searchGroup();
  assert.equal(view.currentArc.value.arc_id, 'arc-new');
  assert.equal(view.canCreateArc.value, false);

  view.askDecision(pending, 'accept');
  assert.equal(view.decisionKind.value, 'accept');
  await view.confirmDecision();
  assert.equal(calls.find(call => call.path === '/api/admin/dream/accept').body.proposal_id, 'proposal-pending');
  assert.match(view.notice.value, /已接受并提交虚构事件/);

  view.askDecision(validated, 'reject');
  assert.equal(view.decisionKind.value, 'reject');
  await view.confirmDecision();
  assert.equal(calls.find(call => call.path === '/api/admin/dream/reject').body.proposal_id, 'proposal-validated');

  session.sessionState.status = { mode: 'live', model_config: { task_bindings: {} } };
  assert.equal(view.canGenerateDream.value, true);
  await view.generateDream();
  assert.equal(calls.find(call => call.path === '/api/admin/dream/propose').body.arc_id, 'arc-new');

  const source = readFileSync(resolve(root, 'web/src/features/dream/DreamView.vue'), 'utf8');
  assert.match(source, /接受会提交虚构事件/);
  assert.match(source, /确认拒绝/);
});

test('late Dream reads cannot restore group data after administrator logout', async t => {
  const arcWaiting = deferred();
  const proposalsWaiting = deferred();
  const hooks = { watch: (_getter, callback) => { hooks.adminLogoutWatch = callback; } };
  const load = modules(path => String(path).includes('/story/arcs') ? arcWaiting.promise : proposalsWaiting.promise, hooks);
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const view = load('@/features/dream/DreamView.vue');
  view.groupIdInput.value = 'group-32';
  const reading = view.searchGroup();

  session.sessionState.adminAuthenticated = false;
  hooks.adminLogoutWatch(false);
  arcWaiting.resolve(response({ items: [{ arc_id: 'stale-arc', role: 'main', title: 'stale', stage: 'active', status: 'active', revision: 1, group_ids: ['group-32'] }] }));
  proposalsWaiting.resolve(response({ items: [], next_cursor: null }));
  await reading;

  assert.equal(view.loadedGroupId.value, '');
  assert.deepEqual([...view.arcs.value], []);
  assert.deepEqual([...view.proposals.value], []);
  assert.equal(view.loading.value, false);
  t.after(() => hooks.cleanup?.());
});

test('admin and read-only login both release busy state', async () => {
  for (const kind of ['Admin', 'Status']) {
    const load = modules(async path => response(path === '/api/status' ? { mode: 'offline' } : { authenticated: true }));
    const session = load('@/app/session');
    await session['login' + kind]('synthetic-token');
    assert.equal(session.sessionState.authBusy, null);
    assert.equal(session.sessionState[kind === 'Admin' ? 'adminAuthenticated' : 'statusAuthenticated'], true);
  }
});

test('late status response cannot repopulate after either logout', async () => {
  for (const kind of ['Status', 'Admin']) {
    const waiting = deferred();
    const load = modules(path => path === '/api/status' ? waiting.promise : Promise.resolve(response({ authenticated: false })));
    const session = load('@/app/session');
    const refresh = session.refreshStatus();
    await session['logout' + kind]();
    waiting.resolve(response({ mode: 'live', secretSentinel: 'stale' }));
    await refresh;
    assert.equal(session.sessionState.status, null, kind);
    assert.equal(session.sessionState.statusAuthenticated, false, kind);
    assert.equal(session.sessionState.statusLoading, false, kind);
  }
});

test('settings conflict preserves original revision and draft', async () => {
  const config = JSON.parse(execFileSync('uv', ['run', 'python', '-c',
    'import json; from omubot_new.config import Config; from omubot_new.settings import editable; print(json.dumps(editable(Config())))'],
    { cwd: root, encoding: 'utf8' }));
  const snapshot = { revision: 1, config, effective_revision: 1, effective_config: config,
    versions: [1], changed_fields: [], restart_required: false };
  const load = modules(async (_path, options) => options?.method === 'PUT'
    ? response({ error: 'revision_conflict' }, 409) : response(snapshot));
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const settings = load('@/features/settings/store');
  await settings.loadSettings();
  settings.updateConfigField('timezone', 'UTC');
  await settings.saveSettings();
  assert.equal(settings.settingsState.baseRevision, 1);
  assert.equal(settings.settingsState.draft.timezone, 'UTC');
  assert.equal(settings.settingsState.saving, false);
  assert.match(settings.settingsState.error, /冲突/);
});

test('invalid advanced JSON leaves the usable draft unchanged', async () => {
  const config = JSON.parse(execFileSync('uv', ['run', 'python', '-c',
    'import json; from omubot_new.config import Config; from omubot_new.settings import editable; print(json.dumps(editable(Config())))'],
    { cwd: root, encoding: 'utf8' }));
  const load = modules(async () => response({ revision: 1, config, effective_revision: 1,
    effective_config: config, versions: [1], changed_fields: [], restart_required: false }));
  const settings = load('@/features/settings/store');
  await settings.loadSettings();
  const before = JSON.stringify(settings.settingsState.draft);
  settings.markAdvancedText('{"models":{"broken":null}}');
  assert.equal(settings.applyAdvancedText(), false);
  assert.equal(JSON.stringify(settings.settingsState.draft), before);
});

test('reply phase settings preserve saved versus running versions and reject wider clocks', async () => {
  const snapshot = settingsFixture();
  snapshot.config.total_timeout = 30;
  snapshot.config.max_reply_segments = 4;
  snapshot.effective_config = structuredClone(snapshot.config);
  const writes = [];
  const load = modules(async (_path, options) => {
    if (options?.method === 'PUT') {
      const body = JSON.parse(options.body);
      writes.push(body);
      return response({ ...snapshot, revision: 2, config: body.config,
        versions: [1, 2], changed_fields: ['max_reply_segments', 'total_timeout'], restart_required: true });
    }
    return response(snapshot);
  });
  const settings = load('@/features/settings/store');
  await settings.loadSettings();
  assert.equal(settings.settingsState.draft.total_timeout, 30);
  assert.equal(settings.settingsState.draft.max_reply_segments, 4);
  const candidate = { ...settings.settingsState.draft, total_timeout: 105, max_reply_segments: 5,
    reply_generation_timeout: 30, reply_admission_timeout: 30, reply_delivery_timeout: 45 };
  settings.markAdvancedText(JSON.stringify(candidate));
  assert.equal(settings.applyAdvancedText(), true);
  assert.equal(await settings.saveSettings(), true);
  assert.equal(writes[0].expected_revision, 1);
  assert.equal(writes[0].config.reply_delivery_timeout, 45);
  assert.equal(settings.settingsState.snapshot.effective_config.total_timeout, 30);
  assert.equal(settings.settingsState.snapshot.restart_required, true);
  const before = JSON.stringify(settings.settingsState.draft);
  settings.markAdvancedText(JSON.stringify({ ...candidate, reply_delivery_timeout: 46 }));
  assert.equal(settings.applyAdvancedText(), false);
  assert.equal(JSON.stringify(settings.settingsState.draft), before);
});

test('Bot loop guard and RWS fields survive advanced settings validation and save', async () => {
  const snapshot = settingsFixture();
  const writes = [];
  const load = modules(async (path, options) => {
    if (path === '/api/admin/config' && options?.method === 'PUT') {
      const body = JSON.parse(options.body);
      writes.push(body);
      return response({ ...snapshot, revision: 2, config: body.config,
        versions: [1, 2], changed_fields: ['known_bot_ids', 'rws_mode'], restart_required: true });
    }
    return response(snapshot);
  });
  const settings = load('@/features/settings/store');
  await settings.loadSettings();
  const valid = JSON.parse(settings.settingsState.advancedText);
  valid.bot_pair_guard_enabled = true;
  valid.bot_pair_loop_alt_threshold = 10;
  valid.bot_pair_known_alt_threshold = 6;
  valid.bot_pair_cooldown_seconds = 60;
  valid.known_bot_ids = ['bot-peer-17'];
  valid.rws_mode = 'shadow';
  valid.rws_threshold = 0.6;
  settings.markAdvancedText(JSON.stringify(valid));
  assert.equal(settings.applyAdvancedText(), true);
  assert.equal(await settings.saveSettings(), true);
  assert.deepEqual(writes[0].config.known_bot_ids, ['bot-peer-17']);
  assert.equal(writes[0].config.bot_pair_loop_alt_threshold, 10);
  assert.equal(writes[0].config.bot_pair_known_alt_threshold, 6);
  assert.equal(writes[0].config.rws_mode, 'shadow');
  assert.equal(writes[0].config.rws_threshold, 0.6);

  const maxThresholds = JSON.parse(settings.settingsState.advancedText);
  maxThresholds.bot_pair_loop_alt_threshold = 127;
  maxThresholds.bot_pair_known_alt_threshold = 127;
  settings.markAdvancedText(JSON.stringify(maxThresholds));
  assert.equal(settings.applyAdvancedText(), true);
  assert.equal(settings.settingsState.draft.bot_pair_loop_alt_threshold, 127);
  assert.equal(settings.settingsState.draft.bot_pair_known_alt_threshold, 127);
  settings.markAdvancedText(JSON.stringify(valid));
  assert.equal(settings.applyAdvancedText(), true);

  const beforeInvalid = JSON.stringify(settings.settingsState.draft);
  const invalid = JSON.parse(settings.settingsState.advancedText);
  invalid.known_bot_ids = ['peer', 'peer'];
  settings.markAdvancedText(JSON.stringify(invalid));
  assert.equal(settings.applyAdvancedText(), false);
  assert.equal(JSON.stringify(settings.settingsState.draft), beforeInvalid);

  invalid.known_bot_ids = ['peer'];
  invalid.rws_mode = 'always';
  settings.markAdvancedText(JSON.stringify(invalid));
  assert.equal(settings.applyAdvancedText(), false);
  assert.equal(JSON.stringify(settings.settingsState.draft), beforeInvalid);

  const validAdvanced = JSON.stringify(settings.settingsState.draft);
  for (const field of ['bot_pair_loop_alt_threshold', 'bot_pair_known_alt_threshold']) {
    const unreachable = JSON.parse(validAdvanced);
    unreachable[field] = 128;
    settings.markAdvancedText(JSON.stringify(unreachable));
    assert.equal(settings.applyAdvancedText(), false, `${field} must not exceed the retained flip window`);
    assert.equal(JSON.stringify(settings.settingsState.draft), beforeInvalid);
  }
});

test('diagnostics keeps the last snapshot and marks it stale after a failed refresh', async () => {
  const calls = [];
  let fail = false;
  const snapshot = {
    usage: { reported_calls: 1, model_calls: 2, input_tokens: 11, output_tokens: 3, cached_input_tokens: 2, cache_write_tokens: 0 },
    traces: [{ request_id: 'request-1', state: 'failed', code: 'invalid_protocol', actions: [{
      key: 'action-1', action: 'model.invoke', state: 'failed', code: 'invalid_protocol', task: 'reply', elapsed_ms: 42,
      usage: null,
    }] }],
  };
  const load = modules(async (path) => {
    if (path === '/api/diagnostics') {
      calls.push(path);
      return fail ? response({ error: 'upstream_unavailable' }, 502) : response(snapshot);
    }
    return response({ mode: 'offline' });
  });
  const session = load('@/app/session');
  session.sessionState.statusAuthenticated = true;
  const view = load('@/features/diagnostics/DiagnosticsView.vue');
  await view.loadDiagnostics();
  assert.equal(view.diagnostics.value.usage.reported_calls, 1);
  assert.equal(view.stale.value, false);
  fail = true;
  await view.loadDiagnostics();
  assert.equal(view.diagnostics.value.usage.input_tokens, 11);
  assert.equal(view.stale.value, true);
  assert.match(view.error.value, /暂不可达/);
  assert.deepEqual(calls, ['/api/diagnostics', '/api/diagnostics']);
});

test('diagnostics view exposes bounded participation decisions from its current snapshot', async () => {
  const snapshot = {
    usage: { reported_calls: 0, model_calls: 0, input_tokens: 0, output_tokens: 0, cached_input_tokens: 0, cache_write_tokens: 0 },
    traces: [],
    participation: [{
      instance_id: 'instance-a', bot_id: '10001', group_id: 'group-a', event_id: 'event-a',
      outcome: 'gray', reason: 'legacy_default', rws_mode: 'shadow', rws_score: 0.5,
      rws_threshold: 0.5, rws_state: 'neutral_no_weighted_signals',
      signal_availability: [{ name: 'eot_probability', status: 'missing' }],
      loop_fuse_reason: null,
    }],
  };
  const load = modules(async () => response(snapshot));
  const session = load('@/app/session');
  session.sessionState.statusAuthenticated = true;
  const view = load('@/features/diagnostics/DiagnosticsView.vue');
  await view.loadDiagnostics();
  assert.equal(view.participation.value.length, 1);
  assert.equal(view.participation.value[0].event_id, 'event-a');
  const source = readFileSync(resolve(root, 'web/src/features/diagnostics/DiagnosticsView.vue'), 'utf8');
  assert.match(source, /参与裁决/);
  assert.match(source, /signal_availability/);
});

test('diagnostics reads topic edges only for an admin session and clears them on admin expiry', async () => {
  const calls = [];
  const snapshot = {
    usage: { reported_calls: 0, model_calls: 0, input_tokens: 0, output_tokens: 0, cached_input_tokens: 0, cache_write_tokens: 0 },
    traces: [],
    participation: [],
  };
  const topicEdges = {
    topic_edges: [{
      instance_id: 'instance-a', bot_id: '10001', group_id: 'group-a', event_id: 'event-a',
      author_id: 'user-a', message_id: 'message-a', mention_targets: ['10001'], mention_routes: [{
        target_id: '10001', source_event_ids: ['event-a'], topic_ids: ['topic-a'], truncated: false,
      }], topic_id: 'topic-a', topic_parent_event_id: null, topic_edge_kind: 'root', bot_involved: false,
    }],
  };
  const load = modules(async (path) => {
    calls.push(path);
    if (path === '/api/admin/diagnostics/topic-edges') return response(topicEdges);
    if (path === '/api/diagnostics') return response(snapshot);
    return response({ mode: 'offline' });
  });
  const session = load('@/app/session');
  session.sessionState.statusAuthenticated = true;
  session.sessionState.adminAuthenticated = true;
  const view = load('@/features/diagnostics/DiagnosticsView.vue');
  await view.loadDiagnostics();
  assert.equal(view.topicEdges.value.length, 1);
  assert.ok(calls.includes('/api/admin/diagnostics/topic-edges'));
  const source = readFileSync(resolve(root, 'web/src/features/diagnostics/DiagnosticsView.vue'), 'utf8');
  assert.match(source, /话题边诊断/);
  assert.match(source, /真实 @ 目标/);
  session.sessionState.adminAuthenticated = false;
  await webRequire('vue').nextTick();
  assert.equal(view.topicEdges.value.length, 0);
});

test('diagnostics does not repopulate after status logout cancels a late response', async () => {
  const pending = deferred();
  const load = modules(path => path === '/api/diagnostics' ? pending.promise : Promise.resolve(response({ mode: 'offline' })));
  const session = load('@/app/session');
  session.sessionState.statusAuthenticated = true;
  const view = load('@/features/diagnostics/DiagnosticsView.vue');
  const request = view.loadDiagnostics();
  session.sessionState.statusAuthenticated = false;
  await webRequire('vue').nextTick();
  pending.resolve(response({ usage: { reported_calls: 1, model_calls: 1, input_tokens: 1, output_tokens: 1, cached_input_tokens: 0, cache_write_tokens: 0 }, traces: [] }));
  await request;
  assert.equal(view.diagnostics.value, null);
  assert.equal(view.isLoading.value, false);
});

test('diagnostics cancels an old generation before reloading the same status session', async () => {
  const first = deferred();
  const second = deferred();
  let reads = 0;
  const load = modules(path => path === '/api/diagnostics'
    ? (++reads === 1 ? first.promise : second.promise)
    : Promise.resolve(response({ mode: 'offline' })));
  const session = load('@/app/session');
  session.sessionState.statusAuthenticated = true;
  const view = load('@/features/diagnostics/DiagnosticsView.vue');
  const oldRequest = view.loadDiagnostics();
  session.sessionState.generation += 1;
  await webRequire('vue').nextTick();
  first.resolve(response({ usage: { reported_calls: 1, model_calls: 1, input_tokens: 1, output_tokens: 1, cached_input_tokens: 0, cache_write_tokens: 0 }, traces: [] }));
  await oldRequest;
  assert.equal(view.diagnostics.value, null);
  assert.equal(view.isLoading.value, true);
  second.resolve(response({ usage: { reported_calls: 1, model_calls: 1, input_tokens: 2, output_tokens: 1, cached_input_tokens: 0, cache_write_tokens: 0 }, traces: [] }));
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(view.diagnostics.value.usage.input_tokens, 2);
});

test('request context observations distinguish off and unknown, retain stale counts, and discard expired admin responses', async () => {
  const calls = [];
  let mode = 'off';
  let finishLate;
  let finishHealthLate;
  const diagnostics = { usage: { reported_calls: 0, model_calls: 0, input_tokens: 0, output_tokens: 0,
    cached_input_tokens: 0, cache_write_tokens: 0 }, traces: [], participation: [] };
  const roles = ['context_main', 'temporal_trace', 'context_constrained', 'social_episode'];
  const off = { enabled: false, sample_count: 0, recent: [], pack_state_counts: {
    skip: 0, empty: 0, nonempty: 0, omit_only: 0, unknown: 0,
  }, path_totals: roles.map(role => ({ role, accepted: 0, trimmed: 0, rejected: 0, missing: 0, unknown: 0 })) };
  const active = { ...off, enabled: true, sample_count: 1, pack_state_counts: {
    skip: 0, empty: 0, nonempty: 1, omit_only: 0, unknown: 0,
  }, path_totals: roles.map(role => ({ role, accepted: role === 'context_main' ? 1 : 0,
    trimmed: 0, rejected: 0, missing: role === 'social_episode' ? 1 : 0, unknown: role === 'temporal_trace' ? 1 : 0 })),
  recent: [{ plan: null, pack: { state: 'nonempty', hot_count: 1, cold_count: 0, card_count: 0,
    document_count: 0, graph_count: 0, temporal_count: null, total_budget: 256, used_budget: 32, cold_used_budget: 0 },
    paths: roles.map(role => ({ role, availability: role === 'context_main' ? 'available' : 'unknown',
      item_count: role === 'context_main' ? 1 : null, decision: role === 'context_main' ? 'accepted' : null })),
    budget: { outcome: 'accepted', character_limit: 1024, built_characters: 48, final_characters: 48, removed_messages: 0 } }] };
  const load = modules(async path => {
    calls.push(path);
    if (path === '/api/diagnostics') return response(diagnostics);
    if (path === '/api/admin/diagnostics/topic-edges') return response({ topic_edges: [] });
    if (path === '/api/admin/diagnostics/health') {
      if (mode === 'late') return new Promise(resolve => { finishHealthLate = resolve; });
      if (mode === 'fail') return response({ error: 'upstream_unavailable' }, 503);
      return response({ state: 'unknown', observed_at: '2026-10-01T09:00:00Z', components: [] });
    }
    if (path === '/api/admin/diagnostics/context-observations') {
      if (mode === 'late') return new Promise(resolve => { finishLate = resolve; });
      if (mode === 'fail') return response({ error: 'upstream_unavailable' }, 503);
      if (mode === 'unauthorized') return response({ error: 'unauthorized' }, 401);
      return response(mode === 'off' ? off : active);
    }
    throw new Error(`unexpected request ${path}`);
  });
  const session = load('@/app/session');
  session.sessionState.statusAuthenticated = true;
  const view = load('@/features/diagnostics/DiagnosticsView.vue');
  await view.loadDiagnostics();
  assert.equal(calls.some(path => path.includes('/api/admin/')), false);
  assert.equal(view.contextObservations.value, null);
  session.sessionState.adminAuthenticated = true;
  await webRequire('vue').nextTick();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(view.contextObservations.value.enabled, false);
  assert.equal(view.aggregateHealth.value.state, 'unknown');
  assert.equal(view.recentContextObservations.value.length, 0);
  mode = 'on';
  await view.loadDiagnostics();
  assert.equal(view.contextObservations.value.sample_count, 1);
  assert.equal(view.recentContextObservations.value[0].pack.temporal_count, null);
  assert.equal(view.observationCount(null), '未知');
  assert.equal(view.observationCount(undefined), '未知');
  assert.equal(view.observationCount(0), '0');
  assert.deepEqual(Array.from(view.contextPathTotals.value, row => row.role), roles);
  assert.equal(view.contextPathTotals.value[1].unknown, 1);
  assert.equal(view.contextPathLabel(active.recent[0].paths[1]), '未知');
  mode = 'fail';
  await view.loadDiagnostics();
  assert.equal(view.contextObservations.value.sample_count, 1);
  assert.equal(view.contextObservationStale.value, true);
  assert.equal(view.healthStale.value, true);
  assert.ok(view.contextObservationError.value);
  mode = 'late';
  const pending = view.loadDiagnostics();
  session.expireAdminSession();
  await webRequire('vue').nextTick();
  assert.equal(view.contextObservations.value, null);
  assert.equal(view.aggregateHealth.value, null);
  finishLate(response(active));
  finishHealthLate(response({ state: 'unknown', observed_at: '2026-10-01T09:00:00Z', components: [] }));
  await pending;
  assert.equal(view.contextObservations.value, null);
  assert.equal(view.aggregateHealth.value, null);
  mode = 'unauthorized';
  session.sessionState.adminAuthenticated = true;
  await webRequire('vue').nextTick();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(session.sessionState.adminAuthenticated, false);
  assert.equal(view.contextObservations.value, null);
  assert.equal(view.contextObservationStale.value, false);
  const source = readFileSync(resolve(root, 'web/src/features/diagnostics/DiagnosticsView.vue'), 'utf8');
  const observationTemplate = source.split('<p class="eyebrow">请求上下文观测</p>')[1].split('<p class="eyebrow">话题边诊断</p>')[0];
  assert.doesNotMatch(observationTemplate, /request_id|event_id|source_id|hash|\.body|\.content/);
  assert.match(observationTemplate, /观测未开启/);
});

test('persona editor shares the settings draft and saves the complete config with CAS', async () => {
  const snapshot = settingsFixture();
  snapshot.config = { ...snapshot.config, persona_name: '', persona_instructions: '' };
  snapshot.effective_config = { ...snapshot.effective_config, persona_name: '运行中的助手', persona_instructions: '当前运行风格' };
  const calls = [];
  const load = modules(async (path, options) => {
    if (options?.method === 'PUT') {
      const body = JSON.parse(options.body);
      calls.push({ path, body });
      return response({ ...snapshot, revision: 2, config: body.config, restart_required: true,
        changed_fields: ['persona_name', 'persona_instructions'] });
    }
    return response(snapshot);
  });
  const settings = load('@/features/settings/store');
  await settings.loadSettings();
  const view = load('@/features/persona/PersonaView.vue');
  assert.equal(view.runningName.value, '运行中的助手');
  assert.equal(view.runningInstructions.value, '当前运行风格');
  settings.updateConfigField('timezone', 'UTC');
  assert.equal(view.hasOtherDraftChanges.value, true);
  assert.equal(view.updatePersonaField('persona_name', '小木'), true);
  assert.equal(view.updatePersonaField('persona_instructions', '简洁、清楚地回答。'), true);
  assert.equal(view.canSave.value, true);
  assert.equal(await view.savePersona(), true);
  assert.deepEqual(calls.map(call => call.path), ['/api/admin/config']);
  assert.equal(calls[0].body.expected_revision, 1);
  assert.equal(calls[0].body.config.timezone, 'UTC');
  assert.equal(calls[0].body.config.persona_name, '小木');
  assert.equal(calls[0].body.config.persona_instructions, '简洁、清楚地回答。');
  assert.equal(view.runningName.value, '运行中的助手');
  assert.equal(settings.settingsState.snapshot.restart_required, true);
});

test('persona source mode previews with admin mutation headers and saves separately', async () => {
  const snapshot = settingsFixture();
  const source = `---
persona_id: web-persona
canonical_name: Web 人格
version_hint: web-v1
language: zh-CN
---
# 1. 身份
保持清晰。
# 3. 表达
先说结论。
# 4. 知识边界
未知就说明未知。
# 7. 例子
给出可核对的下一步。
`;
  const calls = [];
  let invalidPreview = false;
  let pendingPreview = null;
  const load = modules(async (path, options) => {
    if (path === '/api/admin/persona/preview') {
      if (pendingPreview) return pendingPreview.promise;
      calls.push({ path, method: options?.method, body: JSON.parse(options.body), header: options.headers.get('X-Omubot-Request') });
      return invalidPreview
        ? response({ valid: false, source_ref: 'config.persona_source_markdown', source_hash: 'hash', version: null,
          system: '', issues: [{ code: 'missing_required_section', line: 7, message: 'required section is missing' }] })
        : response({ valid: true, source_ref: 'config.persona_source_markdown', source_hash: 'hash', version: 'persona-v1:sha256:web',
          system: 'compiled web persona', issues: [] });
    }
    if (options?.method === 'PUT') {
      const body = JSON.parse(options.body);
      return response({ ...snapshot, revision: 2, config: body.config, changed_fields: ['persona_mode', 'persona_source_markdown'], restart_required: true });
    }
    return response(snapshot);
  });
  const settings = load('@/features/settings/store');
  await settings.loadSettings();
  const view = load('@/features/persona/PersonaView.vue');
  assert.equal(view.draftMode.value, 'simple');
  assert.equal(view.updatePersonaMode('source'), true);
  assert.equal(view.updatePersonaSource(source), true);
  assert.equal(view.preview.value, null);
  assert.equal(await view.previewPersona(), true);
  assert.equal(view.preview.value.version, 'persona-v1:sha256:web');
  assert.deepEqual(calls[0], {
    path: '/api/admin/persona/preview', method: 'POST',
    body: { source_markdown: source }, header: '1',
  });

  invalidPreview = true;
  assert.equal(view.updatePersonaSource(`${source}\n# 4. duplicate`), true);
  assert.equal(await view.previewPersona(), false);
  assert.equal(view.preview.value.valid, false);
  assert.equal(view.preview.value.issues[0].line, 7);

  invalidPreview = false;
  assert.equal(view.updatePersonaSource(source), true);
  assert.equal(await view.previewPersona(), true);
  pendingPreview = deferred();
  const latePreview = view.previewPersona();
  await Promise.resolve();
  const replaced = JSON.parse(settings.settingsState.advancedText);
  replaced.persona_mode = 'source';
  replaced.persona_source_markdown = `${source}\nchanged after reload`;
  settings.markAdvancedText(JSON.stringify(replaced));
  assert.equal(settings.applyAdvancedText(), true);
  assert.equal(view.preview.value, null);
  assert.equal(view.previewBusy.value, false);
  pendingPreview.resolve(response({ valid: true, source_ref: 'config.persona_source_markdown', source_hash: 'late',
    version: 'persona-v1:sha256:late', system: 'stale', issues: [] }));
  await latePreview;
  assert.equal(view.preview.value, null);
  pendingPreview = null;
  assert.equal(view.updatePersonaSource(source), true);
  assert.equal(await view.savePersona(), true);
  assert.equal(calls.filter(call => call.path === '/api/admin/persona/preview').length, 3);
  assert.equal(settings.settingsState.snapshot.config.persona_mode, 'source');
  assert.equal(settings.settingsState.snapshot.config.persona_source_markdown, source);
});

test('persona editor blocks edits while advanced JSON is not applied', async () => {
  const snapshot = settingsFixture();
  const load = modules(async () => response(snapshot));
  const settings = load('@/features/settings/store');
  await settings.loadSettings();
  const view = load('@/features/persona/PersonaView.vue');
  settings.markAdvancedText(`${settings.settingsState.advancedText} `);
  assert.equal(settings.settingsState.advancedEdited, true);
  assert.equal(view.updatePersonaField('persona_name', 'blocked'), false);
  assert.equal(view.canSave.value, false);
});

test('persona retries a failed initial read and does not discard a draft on reload or conflict', async () => {
  const snapshot = settingsFixture();
  let reads = 0;
  const load = modules(async (path, options) => {
    if (options?.method === 'PUT') return response({ error: 'revision_conflict' }, 409);
    if (path === '/api/admin/config') return ++reads === 1
      ? response({ error: 'upstream_unavailable' }, 502)
      : response(snapshot);
    return response({ mode: 'offline' });
  });
  const settings = load('@/features/settings/store');
  await settings.loadSettings();
  const view = load('@/features/persona/PersonaView.vue');
  view.requestReload();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(settings.settingsState.snapshot.revision, 1);
  settings.updateConfigField('persona_name', '保留草稿');
  view.requestReload();
  assert.equal(settings.settingsState.reloadPending, true);
  assert.equal(settings.settingsState.draft.persona_name, '保留草稿');
  view.cancelReload();
  assert.equal(settings.settingsState.reloadPending, false);
  assert.equal(await view.savePersona(), false);
  assert.equal(settings.settingsState.draft.persona_name, '保留草稿');
  assert.equal(settings.settingsState.baseRevision, 1);
});

test('persona fields accept legacy omissions but reject values over their limits', async () => {
  const snapshot = settingsFixture();
  delete snapshot.config.persona_name;
  delete snapshot.config.persona_instructions;
  const load = modules(async () => response(snapshot));
  const settings = load('@/features/settings/store');
  await settings.loadSettings();
  const legacy = JSON.parse(settings.settingsState.advancedText);
  legacy.persona_name = '兼容旧配置';
  legacy.persona_instructions = '可应用的新字段';
  settings.markAdvancedText(JSON.stringify(legacy));
  assert.equal(settings.applyAdvancedText(), true);
  const tooLong = JSON.parse(settings.settingsState.advancedText);
  tooLong.persona_name = 'x'.repeat(81);
  settings.markAdvancedText(JSON.stringify(tooLong));
  assert.equal(settings.applyAdvancedText(), false);
});

test('read-only logout does not abandon an in-flight admin save', async () => {
  const config = JSON.parse(execFileSync('uv', ['run', 'python', '-c',
    'import json; from omubot_new.config import Config; from omubot_new.settings import editable; print(json.dumps(editable(Config())))'],
    { cwd: root, encoding: 'utf8' }));
  const snapshot = { revision: 1, config, effective_revision: 1, effective_config: config,
    versions: [1], changed_fields: [], restart_required: false };
  const pending = deferred();
  const load = modules((path, options) => options?.method === 'PUT' ? pending.promise
    : Promise.resolve(response(path === '/api/admin/config' ? snapshot : { authenticated: false })));
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const settings = load('@/features/settings/store');
  await settings.loadSettings();
  settings.updateConfigField('timezone', 'UTC');
  const save = settings.saveSettings();
  await session.logoutStatus();
  pending.resolve(response({ ...snapshot, revision: 2, config: { ...config, timezone: 'UTC' } }));
  await save;
  assert.equal(settings.settingsState.saving, false);
  assert.equal(settings.settingsState.baseRevision, 2);
});

test('reloading configuration excludes edits and writes until the snapshot arrives', async () => {
  const config = JSON.parse(execFileSync('uv', ['run', 'python', '-c',
    'import json; from omubot_new.config import Config; from omubot_new.settings import editable; print(json.dumps(editable(Config())))'],
    { cwd: root, encoding: 'utf8' }));
  const snapshot = { revision: 1, config, effective_revision: 1, effective_config: config,
    versions: [1], changed_fields: [], restart_required: false };
  const pending = deferred();
  let reads = 0;
  let writes = 0;
  const load = modules((_path, options) => {
    if (options?.method === 'PUT') { writes++; return Promise.resolve(response(snapshot)); }
    return ++reads === 1 ? Promise.resolve(response(snapshot)) : pending.promise;
  });
  const settings = load('@/features/settings/store');
  await settings.loadSettings();
  const reload = settings.loadSettings(true);
  assert.equal(settings.updateConfigField('timezone', 'UTC'), false);
  assert.equal(await settings.saveSettings(), false);
  assert.equal(writes, 0);
  pending.resolve(response(snapshot));
  await reload;
  assert.equal(settings.settingsState.loading, false);
});

test('admin logout clears an unsubmitted permission form', async () => {
  const load = modules(async () => response({ authenticated: false }));
  const session = load('@/app/session');
  const policy = load('@/features/policy/store');
  session.sessionState.adminAuthenticated = true;
  policy.policyState.groupId = 'private-group';
  policy.policyState.userId = 'private-user';
  policy.policyState.expiryHours = 100;
  await session.logoutAdmin();
  await webRequire('vue').nextTick();
  assert.equal(policy.policyState.groupId, '');
  assert.equal(policy.policyState.userId, '');
  assert.equal(policy.policyState.draft, null);
});

test('policy media capabilities are opt-in, bounded, and saved with the CAS grant draft', async () => {
  const snapshot = {
    revision: 1,
    grants: [],
    bot_id: 'bot',
    thinker_enabled: false,
    mode: 'offline',
    model_config: {
      profile: 'default',
      api_format: 'openai_chat',
      model: 'offline-model',
      policy_provider: 'offline',
      task_bindings: {
        reply: {
          profile: 'default', api_format: 'openai_chat', model: 'offline-model', policy_provider: 'offline',
        },
      },
    },
  };
  const writes = [];
  const load = modules(async (path, options) => {
    if (path === '/api/admin/policy' && options?.method === 'PUT') {
      writes.push(JSON.parse(options.body));
      return response({ revision: 2 });
    }
    return response(snapshot);
  });
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const policy = load('@/features/policy/store');
  await policy.loadPolicy();

  assert.equal(policy.policyState.allowCurrentImage, false);
  assert.equal(policy.policyState.allowImageUpload, false);
  assert.equal(policy.policyState.allowStickerSend, false);
  policy.policyState.groupId = 'text-group';
  policy.policyState.userId = 'text-user';
  assert.equal(policy.addUserGrants(), true);
  const textGrant = policy.policyState.draft[0];
  assert.equal(textGrant.allow_images, false);
  assert.equal(textGrant.actions.includes('media.read'), false);
  assert.equal(textGrant.actions.includes('message.sticker'), false);
  assert.equal(textGrant.actions.includes('media.send'), false);

  policy.policyState.groupId = 'media-group';
  policy.policyState.userId = 'media-user';
  policy.policyState.allowImageUpload = true;
  assert.equal(policy.addUserGrants(), false);
  assert.match(policy.policyState.error, /本轮当前图片/);
  policy.policyState.allowImageUpload = false;
  policy.policyState.allowStickerSend = true;
  policy.policyState.groupId = 'sticker-group';
  policy.policyState.userId = 'sticker-user';
  assert.equal(policy.addUserGrants(), true);
  const stickerGrant = policy.policyState.draft[1];
  assert.equal(stickerGrant.allow_images, false);
  assert.equal(stickerGrant.actions.includes('media.read'), false);
  assert.equal(stickerGrant.actions.includes('message.sticker'), true);
  assert.equal(stickerGrant.actions.includes('media.send'), true);

  policy.policyState.allowCurrentImage = true;
  policy.policyState.allowStickerSend = false;
  policy.policyState.groupId = 'media-group';
  policy.policyState.userId = 'media-user';
  policy.policyState.allowImageUpload = true;
  assert.equal(policy.addUserGrants(), true);
  const mediaGrant = policy.policyState.draft[2];
  assert.equal(mediaGrant.allow_images, true);
  assert.equal(mediaGrant.actions.includes('media.read'), true);
  assert.equal(mediaGrant.actions.includes('message.sticker'), false);
  assert.equal(mediaGrant.actions.includes('media.send'), false);
  assert.equal(mediaGrant.allow_history, true);

  assert.equal(await policy.savePolicy(), true);
  assert.equal(writes.length, 1);
  assert.equal(writes[0].expected_revision, 1);
  assert.equal(writes[0].grants[0].allow_images, false);
  assert.equal(writes[0].grants[1].allow_images, false);
  assert.equal(writes[0].grants[2].allow_images, true);
  assert.equal(policy.policyState.baseRevision, 2);

  // Search grants bind the running tool destination, not a model destination.
  assert.equal(policy.addSearchGrant(), false);
  policy.policyState.snapshot.tool_destinations = [{ tool_id: 'web.search', destination: 'https://search.example/search' }];
  policy.policyState.groupId = 'search-group';
  policy.policyState.userId = 'search-user';
  policy.policyState.allowHistory = false;
  policy.policyState.allowImageUpload = false;
  assert.equal(policy.addSearchGrant(), true);
  const searchGrant = policy.policyState.draft.at(-1);
  assert.equal(searchGrant.provider, 'https://search.example/search');
  assert.equal(searchGrant.model, 'web.search');
  assert.deepEqual(Array.from(searchGrant.actions), ['tool.invoke:web.search']);
  assert.equal(searchGrant.allow_history, false);
  assert.equal(searchGrant.allow_images, false);
  assert.equal(await policy.savePolicy(), true);
  assert.equal(writes.length, 2);
  assert.equal(writes[1].expected_revision, 2);
  assert.equal(writes[1].grants.at(-1).provider, 'https://search.example/search');
});

test('web fetch grants require the running tool and bind one canonical HTTPS URL', async () => {
  const snapshot = {
    revision: 1,
    grants: [],
    bot_id: 'bot',
    thinker_enabled: false,
    mode: 'offline',
    request_destination_tools: [],
    tool_destinations: [{ tool_id: 'other', destination: 'https://other.example/' }],
    model_config: {
      profile: 'default',
      api_format: 'openai_chat',
      model: 'offline-model',
      policy_provider: 'offline',
      task_bindings: {
        reply: {
          profile: 'default', api_format: 'openai_chat', model: 'offline-model', policy_provider: 'offline',
        },
      },
    },
  };
  const load = modules(async () => response(snapshot));
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const policy = load('@/features/policy/store');
  await policy.loadPolicy();
  policy.policyState.groupId = 'fetch-group';
  policy.policyState.userId = 'fetch-user';
  policy.policyState.fetchUrl = 'https://example.com/data?id=7';
  assert.equal(policy.addFetchGrant(), false, 'an unrelated registered destination cannot enable web.fetch');

  policy.policyState.snapshot.request_destination_tools = ['web.fetch'];
  policy.policyState.fetchUrl = '';
  assert.equal(policy.addFetchGrant(), false);
  policy.policyState.fetchUrl = 'http://example.com/data?id=7';
  assert.equal(policy.addFetchGrant(), false);
  policy.policyState.fetchUrl = 'HTTPS://Example.COM/data?id=7';
  policy.policyState.allowHistory = false;
  assert.equal(policy.addFetchGrant(), true);
  const grant = policy.policyState.draft[0];
  assert.equal(grant.provider, 'https://example.com/data?id=7');
  assert.equal(grant.model, 'web.fetch');
  assert.equal(grant.effect, 'allow');
  assert.deepEqual(Array.from(grant.actions), ['tool.invoke:web.fetch']);
  assert.equal(grant.allow_history, false);
  assert.equal(grant.allow_images, false);
  assert.equal(grant.actions.some(action => /post|model\.invoke|image/i.test(action)), false);

  policy.policyState.allowHistory = true;
  assert.equal(policy.addFetchGrant(), true);
  assert.equal(policy.policyState.draft.length, 1, 'the same canonical target updates its existing grant');
  assert.equal(policy.policyState.draft[0].allow_history, true);
  policy.resetPolicyState();
  assert.equal(policy.policyState.fetchUrl, '');
});

test('HTTP method grants stay bound to one exact URL and the running method', async () => {
  const snapshot = {
    revision: 1,
    grants: [],
    bot_id: 'bot',
    thinker_enabled: false,
    mode: 'offline',
    request_destination_tools: ['http.get'],
    model_config: {
      profile: 'default',
      api_format: 'openai_chat',
      model: 'offline-model',
      policy_provider: 'offline',
      task_bindings: {
        reply: {
          profile: 'default', api_format: 'openai_chat', model: 'offline-model', policy_provider: 'offline',
        },
      },
    },
  };
  const load = modules(async () => response(snapshot));
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const policy = load('@/features/policy/store');
  await policy.loadPolicy();
  policy.policyState.groupId = 'http-group';
  policy.policyState.userId = 'http-user';
  policy.policyState.httpUrl = 'https://Example.COM/api/items?id=3';
  policy.policyState.httpMethod = 'POST';
  assert.equal(policy.canAddHttpGrant(), false);
  assert.equal(policy.addHttpGrant(), false, 'POST requires its own running registration');

  policy.policyState.httpMethod = 'GET';
  policy.policyState.httpUrl = 'ftp://example.com/api/items';
  assert.equal(policy.addHttpGrant(), false);
  policy.policyState.httpUrl = 'https://Example.COM/api/items?id=3';
  assert.equal(policy.addHttpGrant(), true);
  const getGrant = policy.policyState.draft[0];
  assert.equal(getGrant.scope.bot_id, 'bot');
  assert.equal(getGrant.scope.group_id, 'http-group');
  assert.equal(getGrant.subject, 'http-user');
  assert.equal(getGrant.provider, 'https://example.com/api/items?id=3');
  assert.equal(getGrant.model, 'http.get');
  assert.deepEqual(Array.from(getGrant.actions), ['tool.invoke:http.get']);
  assert.equal(getGrant.allow_images, false);

  policy.policyState.snapshot.request_destination_tools.push('http.post');
  policy.policyState.httpMethod = 'POST';
  assert.equal(policy.addHttpGrant(), true);
  assert.equal(policy.policyState.draft.length, 2);
  assert.equal(policy.policyState.draft[1].provider, getGrant.provider);
  assert.equal(policy.policyState.draft[1].model, 'http.post');
  assert.deepEqual(Array.from(policy.policyState.draft[1].actions), ['tool.invoke:http.post']);
  policy.resetPolicyState();
  assert.equal(policy.policyState.httpUrl, '');
  assert.equal(policy.policyState.httpMethod, 'GET');
  assert.equal(policy.policyState.draft, null);
});

test('chat diagnostics grant is limited to its running group and preserves unrelated model grants', async () => {
  const existingGrant = {
    subject: 'diag-user',
    scope: { bot_id: 'bot', group_id: 'diag-group' },
    actions: ['message.read', 'message.reply', 'model.invoke'],
    effect: 'allow',
    provider: 'offline',
    model: 'offline-model',
    allow_history: true,
    allow_images: false,
    expires_at: 4102444800,
  };
  const snapshot = {
    revision: 1,
    grants: [existingGrant],
    bot_id: 'bot',
    thinker_enabled: false,
    mode: 'offline',
    diagnostic_commands_enabled: false,
    diagnostic_commands_groups: ['diag-group'],
    model_config: {
      profile: 'default',
      api_format: 'openai_chat',
      model: 'offline-model',
      policy_provider: 'offline',
      task_bindings: {
        reply: {
          profile: 'default', api_format: 'openai_chat', model: 'offline-model', policy_provider: 'offline',
        },
      },
    },
  };
  const load = modules(async () => response(snapshot));
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const policy = load('@/features/policy/store');
  await policy.loadPolicy();
  policy.policyState.groupId = 'diag-group';
  policy.policyState.userId = 'diag-user';
  assert.equal(policy.canAddDiagnosticGrant(), false);
  assert.equal(policy.addDiagnosticGrant(), false);
  assert.equal(policy.policyState.draft.length, 1);

  policy.policyState.snapshot.diagnostic_commands_enabled = true;
  policy.policyState.snapshot.diagnostic_commands_groups = ['other-group'];
  assert.equal(policy.canAddDiagnosticGrant(), false);
  assert.equal(policy.addDiagnosticGrant(), false);
  assert.equal(policy.policyState.draft.length, 1);

  policy.policyState.snapshot.diagnostic_commands_groups = ['diag-group'];
  assert.equal(policy.canAddDiagnosticGrant(), true);
  assert.equal(policy.addDiagnosticGrant(), true);
  assert.equal(policy.policyState.draft.length, 2);
  const diagnostic = policy.policyState.draft[1];
  assert.equal(diagnostic.subject, 'diag-user');
  assert.equal(diagnostic.scope.bot_id, 'bot');
  assert.equal(diagnostic.scope.group_id, 'diag-group');
  assert.deepEqual(Array.from(diagnostic.actions), ['message.read', 'message.reply', 'status.read']);
  assert.equal(diagnostic.provider, '');
  assert.equal(diagnostic.model, '');
  assert.equal(diagnostic.allow_history, false);
  assert.equal(diagnostic.allow_images, false);

  const firstExpiry = diagnostic.expires_at;
  policy.policyState.expiryHours = 48;
  assert.equal(policy.addDiagnosticGrant(), true);
  assert.equal(policy.policyState.draft.length, 2);
  assert.ok(policy.policyState.draft[1].expires_at >= firstExpiry + 86000);
  assert.equal(policy.policyState.draft[0].model, 'offline-model');
  assert.equal(policy.policyState.draft[0].actions.includes('model.invoke'), true);
});

test('policy memory capabilities stay independently off by default, preserve existing grants, and roll back with CAS', async () => {
  const existingGrant = {
    subject: 'existing-user',
    scope: { bot_id: 'bot', group_id: 'existing-group' },
    actions: ['message.read', 'model.invoke', 'message.reply', 'tool.invoke:time.now',
      'media.read', 'message.sticker', 'media.send', 'memory.apply', 'custom.audit'],
    effect: 'allow',
    provider: 'offline',
    model: 'offline-model',
    allow_history: false,
    allow_images: true,
    expires_at: 4102444800,
  };
  const snapshot = {
    revision: 4,
    grants: [existingGrant],
    bot_id: 'bot',
    thinker_enabled: false,
    mode: 'offline',
    model_config: {
      profile: 'default',
      api_format: 'openai_chat',
      model: 'offline-model',
      policy_provider: 'offline',
      task_bindings: {
        reply: {
          profile: 'default', api_format: 'openai_chat', model: 'offline-model', policy_provider: 'offline',
        },
      },
    },
  };
  const writes = [];
  const load = modules(async (path, options) => {
    if (path === '/api/admin/policy' && options?.method === 'PUT') {
      writes.push(JSON.parse(options.body));
      return response({ revision: 5 });
    }
    return response(snapshot);
  });
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const policy = load('@/features/policy/store');
  await policy.loadPolicy();

  const memoryFields = [
    ['allowMemoryArchive', 'memory.archive'],
    ['allowMemoryLearn', 'memory.learn'],
    ['allowMemoryRetrieve', 'memory.retrieve'],
    ['allowMemoryReview', 'memory.review'],
    ['allowMemoryApply', 'memory.apply'],
  ];
  for (const [field, action] of memoryFields) {
    assert.equal(policy.policyState[field], false, `${field} starts disabled`);
  }
  for (const [index, [field, action]] of memoryFields.entries()) {
    policy.policyState[field] = true;
    policy.policyState.groupId = `memory-group-${index}`;
    policy.policyState.userId = `memory-user-${index}`;
    assert.equal(policy.addUserGrants(), true);
    const added = policy.policyState.draft.find(grant => grant.scope.group_id === `memory-group-${index}`);
    assert.ok(added);
    assert.equal(added.actions.includes(action), true);
    for (const [, otherAction] of memoryFields) {
      if (otherAction !== action) assert.equal(added.actions.includes(otherAction), false);
    }
    policy.policyState[field] = false;
  }

  policy.policyState.groupId = 'existing-group';
  policy.policyState.userId = 'existing-user';
  assert.equal(policy.addUserGrants(), true);
  const preserved = policy.policyState.draft.find(grant => grant.scope.group_id === 'existing-group');
  assert.ok(preserved);
  assert.equal(preserved.actions.includes('custom.audit'), true);
  assert.equal(preserved.actions.includes('media.read'), true);
  assert.equal(preserved.actions.includes('message.sticker'), true);
  assert.equal(preserved.actions.includes('media.send'), true);
  assert.equal(preserved.actions.includes('memory.apply'), true);
  assert.equal(preserved.allow_history, false);
  assert.equal(preserved.allow_images, true);

  for (const [field] of memoryFields) policy.policyState[field] = true;
  policy.policyState.groupId = 'saved-memory-group';
  policy.policyState.userId = 'saved-memory-user';
  assert.equal(policy.addUserGrants(), true);
  assert.equal(await policy.savePolicy(), true);
  assert.equal(writes.length, 1);
  assert.equal(writes[0].expected_revision, 4);
  const saved = writes[0].grants.find(grant => grant.scope.group_id === 'saved-memory-group');
  assert.ok(saved);
  for (const [, action] of memoryFields) assert.equal(saved.actions.includes(action), true);
  assert.equal(policy.policyState.baseRevision, 5);

  for (const [field] of memoryFields) policy.policyState[field] = false;
  policy.policyState.allowMemoryArchive = true;
  policy.policyState.groupId = 'rollback-group';
  policy.policyState.userId = 'rollback-user';
  assert.equal(policy.addUserGrants(), true);
  policy.requestReload();
  assert.equal(policy.policyState.reloadPending, true);
  await policy.confirmReload();
  assert.equal(policy.policyState.reloadPending, false);
  assert.equal(policy.policyState.allowMemoryArchive, false);
  assert.equal(policy.policyState.draft.some(grant => grant.scope.group_id === 'rollback-group'), false);


});

test('policy memory capability CAS conflict keeps the unsaved draft', async () => {
  const snapshot = {
    revision: 7,
    grants: [],
    bot_id: 'bot',
    thinker_enabled: false,
    mode: 'offline',
    model_config: {
      profile: 'default',
      api_format: 'openai_chat',
      model: 'offline-model',
      policy_provider: 'offline',
      task_bindings: {
        reply: {
          profile: 'default', api_format: 'openai_chat', model: 'offline-model', policy_provider: 'offline',
        },
      },
    },
  };
  const load = modules(async (path, options) => options?.method === 'PUT'
    ? response({ error: 'revision_conflict' }, 409)
    : response(snapshot));
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const policy = load('@/features/policy/store');
  await policy.loadPolicy();
  policy.policyState.allowMemoryReview = true;
  policy.policyState.groupId = 'cas-group';
  policy.policyState.userId = 'cas-user';
  assert.equal(policy.addUserGrants(), true);
  const before = JSON.stringify(policy.policyState.draft);
  assert.equal(await policy.savePolicy(), false);
  assert.equal(policy.policyState.baseRevision, 7);
  assert.equal(JSON.stringify(policy.policyState.draft), before);
  assert.equal(policy.policyState.saving, false);
  assert.match(policy.policyState.error, /冲突/);
});


test('background model grants are exact, separate, and off by default', async () => {
  const snapshot = {
    revision: 1, grants: [], bot_id: 'bot', thinker_enabled: false, mode: 'offline',
    model_config: {
      profile: 'default', api_format: 'openai_chat', model: 'reply', policy_provider: 'reply-id',
      task_bindings: {
        reply: { profile: 'default', api_format: 'openai_chat', model: 'reply', policy_provider: 'reply-id' },
        schedule: { profile: 'schedule', api_format: 'openai_chat', model: 'daily', policy_provider: 'daily-id' },
        dream: { profile: 'dream', api_format: 'openai_chat', model: 'dreamer', policy_provider: 'dream-id' },
      },
    },
  };
  const load = modules(async () => response(snapshot));
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const policy = load('@/features/policy/store');
  await policy.loadPolicy();
  assert.equal(policy.policyState.draft.length, 0);
  policy.policyState.groupId = 'group-a';
  assert.equal(policy.addBackgroundModelGrant('schedule'), true);
  assert.equal(policy.addBackgroundModelGrant('dream'), true);
  assert.deepEqual(JSON.parse(JSON.stringify(policy.policyState.draft.map(grant => grant.actions))), [['model.schedule'], ['model.dream']]);
  assert.deepEqual(JSON.parse(JSON.stringify(policy.policyState.draft.map(grant => grant.subject))), ['system:schedule', 'system:dream']);
  assert.deepEqual(JSON.parse(JSON.stringify(policy.policyState.draft.map(grant => grant.provider))), ['daily-id', 'dream-id']);
  assert.ok(policy.policyState.draft.every(grant => !grant.allow_history && !grant.allow_images));
  assert.equal(policy.addBackgroundModelGrant('schedule'), true);
  assert.equal(policy.policyState.draft.length, 2);
  policy.policyState.userId = 'system:schedule';
  assert.equal(policy.addUserGrants(), false);
  assert.equal(policy.policyState.draft.length, 2);
  policy.policyState.groupId = '*';
  assert.equal(policy.addBackgroundModelGrant('schedule'), false);
  assert.equal(policy.policyState.draft.length, 2);
});

test('catalog query and profile switching preserve inputs and selection', async () => {
  const load = modules(async () => response({models: ['fixture-model']}));
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const settings = load('@/features/settings/store');
  const profile = {api_format: 'deepseek', endpoint: 'https://example.test', api_key_env: 'TEST_KEY', model: 'chosen', max_output_tokens:1024, send_history:true, thinking:false, temperature:null, reasoning_effort:null, token_parameter:null};
  settings.settingsState.draft = {models: {default: {...profile}, second: {...profile}}, active_model:'default', task_models:{}};
  settings.settingsState.selectedProfile = 'default';
  const view = load('@/features/settings/SettingsView.vue');
  const {nextTick} = webRequire('vue');
  view.catalogKey.value = 'synthetic-key';
  await view.fetchCatalog();
  assert.equal(view.catalogKey.value, 'synthetic-key');
  assert.deepEqual([...view.catalogModels.value], ['fixture-model']);
  assert.equal(settings.settingsState.draft.models.default.model, 'chosen');
  settings.selectProfile('second'); await nextTick();
  assert.equal(view.catalogKey.value, '');
  settings.selectProfile('default'); await nextTick();
  assert.equal(view.catalogKey.value, 'synthetic-key');
  assert.deepEqual([...view.catalogModels.value], ['fixture-model']);
  settings.updateProfileField('default', 'model', 'fixture-model'); await nextTick();
  assert.equal(view.catalogKey.value, 'synthetic-key');
  session.sessionState.adminAuthenticated = false; await nextTick();
  assert.equal(view.catalogKey.value, '');
  assert.equal(view.catalogModels.value.length, 0);
});


test('protocol presets replace official endpoints and preserve custom routes', () => {
  const load = modules(async () => response({}));
  const settings = load('@/features/settings/store');
  settings.settingsState.draft = {models: {default: {api_format:'anthropic', endpoint:'https://api.anthropic.com/v1/messages'}}, active_model:'default', task_models:{}};
  settings.settingsState.selectedProfile = 'default';
  const view = load('@/features/settings/SettingsView.vue');
  view.changeProfileFormat('deepseek');
  assert.equal(settings.settingsState.draft.models.default.endpoint, 'https://api.deepseek.com/chat/completions');
  settings.updateProfileField('default','endpoint','https://custom.test/exact/');
  view.changeProfileFormat('openai_chat');
  assert.equal(settings.settingsState.draft.models.default.endpoint, 'https://custom.test/exact/');
});


test('NapCat late connection response cannot restore QR or token after logout', async t => {
  const waiting = deferred();
  const hooks = {};
  let connects = 0;
  const load = modules(path => {
    if (path === '/api/admin/napcat/connect') { connects++; return waiting.promise; }
    return Promise.resolve(response({ connected: false }));
  }, hooks);
  const session = load('@/app/session');
  const view = load('@/features/napcat/NapcatView.vue');
  t.after(() => hooks.cleanup?.());
  session.sessionState.adminAuthenticated = true;
  await new Promise(resolve => setTimeout(resolve, 0));
  view.token.value = 'synthetic';
  const connection = view.connectNapcat();
  await view.connectNapcat();
  assert.equal(connects, 1);
  await session.logoutAdmin();
  waiting.resolve(response({ connected: true, qr_url: 'https://qq.com/late' }));
  await connection;
  assert.equal(view.status.value, null);
  assert.equal(view.token.value, '');
  assert.equal(view.logLines.value.length, 0);
});

test('NapCat stopping sampling discards a late log response', async t => {
  const waiting = deferred();
  const hooks = {};
  const load = modules(path => path.endsWith('/logs') ? waiting.promise : Promise.resolve(response({ connected: true })), hooks);
  const session = load('@/app/session');
  const view = load('@/features/napcat/NapcatView.vue');
  t.after(() => hooks.cleanup?.());
  session.sessionState.adminAuthenticated = true;
  await new Promise(resolve => setTimeout(resolve, 0));
  view.logSampling.value = true;
  const sample = view.sampleLogs();
  view.stopLogSampling();
  waiting.resolve(response({ lines: ['stale secret'], detail: 'sample' }));
  await sample;
  assert.equal(view.logSampling.value, false);
  assert.equal(view.logLines.value.length, 0);
});

test('NapCat sampling replaces cached batches but preserves duplicates within one batch', async t => {
  const hooks = {};
  const batch = ['cached line', 'real line', 'real line'];
  const load = modules(async path => {
    if (path === '/api/admin/napcat/status') return response({ connected: true });
    if (path === '/api/admin/napcat/runtime') return response({ available: true, phase: 'running', oom_killed: false });
    if (path.endsWith('/logs')) return response({ lines: batch, detail: 'latest batch' });
    return response({ authenticated: false });
  }, hooks);
  const session = load('@/app/session');
  const view = load('@/features/napcat/NapcatView.vue');
  t.after(() => hooks.cleanup?.());
  session.sessionState.adminAuthenticated = true;
  await new Promise(resolve => setTimeout(resolve, 0));

  view.logSampling.value = true;
  await view.sampleLogs();
  view.stopLogSampling();
  assert.equal(JSON.stringify(view.logLines.value), JSON.stringify(batch));

  view.logSampling.value = true;
  await view.sampleLogs();
  view.stopLogSampling();
  assert.equal(JSON.stringify(view.logLines.value), JSON.stringify(batch));
  assert.equal(view.logLines.value.filter(line => line === 'real line').length, 2);
});


test('NapCat reconnect and disconnect clear logs from the previous connection', async t => {
  const hooks = {};
  const load = modules(async () => response({ connected: true }), hooks);
  const session = load('@/app/session');
  const view = load('@/features/napcat/NapcatView.vue');
  t.after(() => hooks.cleanup?.());
  session.sessionState.adminAuthenticated = true;
  await new Promise(resolve => setTimeout(resolve, 0));
  view.logLines.value = ['previous instance'];
  view.token.value = 'synthetic';
  await view.connectNapcat();
  assert.equal(view.logLines.value.length, 0);
  view.logLines.value = ['current instance'];
  await view.disconnectNapcat();
  assert.equal(view.logLines.value.length, 0);
});

test('NapCat runtime remains usable when QQ status fails', async t => {
  const hooks = {};
  const calls = [];
  const load = modules(async (path, options) => {
    calls.push({ path, method: options?.method, body: options?.body });
    if (path === '/api/admin/napcat/status') return response({ error: 'status_down' }, 502);
    if (path === '/api/admin/napcat/runtime') return response({ available: true, phase: 'running', oom_killed: false });
    if (path === '/api/admin/napcat/start') return response({ connected: true, logged_in: false, qr_url: 'https://qq.com/reconnect' });
    if (path === '/api/admin/napcat/stop') return response({ available: true, phase: 'stopped', oom_killed: false });
    return response({ authenticated: false });
  }, hooks);
  const session = load('@/app/session');
  const view = load('@/features/napcat/NapcatView.vue');
  t.after(() => hooks.cleanup?.());
  session.sessionState.adminAuthenticated = true;
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(view.runtime.value.available, true);
  assert.equal(view.runtime.value.phase, 'running');
  assert.equal(view.runtime.value.oom_killed, false);
  assert.match(view.statusError.value, /QQ 状态读取失败/);
  assert.equal(view.canStartRuntime.value, true);
  assert.equal(view.canStopRuntime.value, true);

  await view.startNapcat();
  assert.equal(view.status.value.connected, true);
  await view.stopNapcat();
  assert.equal(view.runtime.value.available, true);
  assert.equal(view.runtime.value.phase, 'stopped');
  assert.equal(view.runtime.value.oom_killed, false);
  assert.equal(view.status.value, null);
  assert.equal(view.canStartRuntime.value, true);
  assert.deepEqual(calls.filter(call => call.path.endsWith('/start') || call.path.endsWith('/stop')),
    [
      { path: '/api/admin/napcat/start', method: 'POST', body: undefined },
      { path: '/api/admin/napcat/stop', method: 'POST', body: undefined },
    ]);
});

test('late NapCat start response cannot restore state after admin logout', async t => {
  const hooks = {};
  const waiting = deferred();
  const load = modules(path => {
    if (path === '/api/admin/napcat/status') return Promise.resolve(response({ connected: false }));
    if (path === '/api/admin/napcat/runtime') return Promise.resolve(response({ available: true, phase: 'stopped', oom_killed: false }));
    if (path === '/api/admin/napcat/start') return waiting.promise;
    return Promise.resolve(response({ authenticated: false }));
  }, hooks);
  const session = load('@/app/session');
  const view = load('@/features/napcat/NapcatView.vue');
  t.after(() => hooks.cleanup?.());
  session.sessionState.adminAuthenticated = true;
  await new Promise(resolve => setTimeout(resolve, 0));
  const start = view.startNapcat();
  await Promise.resolve();
  await session.logoutAdmin();
  await new Promise(resolve => setTimeout(resolve, 0));
  waiting.resolve(response({ connected: true, qr_url: 'https://qq.com/late-start' }));
  await start;
  assert.equal(view.status.value, null);
  assert.equal(view.runtime.value, null);
  assert.equal(view.token.value, '');
  assert.equal(view.logLines.value.length, 0);
  assert.equal(view.actionBusy.value, null);
});

test('NapCat stop clears QR and logs while making start available', async t => {
  const hooks = {};
  const load = modules(async path => {
    if (path === '/api/admin/napcat/status') return response({ connected: true, logged_in: false, qr_url: 'https://qq.com/stop-qr' });
    if (path === '/api/admin/napcat/runtime') return response({ available: true, phase: 'running', oom_killed: false });
    if (path === '/api/admin/napcat/stop') return response({ available: true, phase: 'stopped', oom_killed: false });
    return response({ authenticated: false });
  }, hooks);
  const session = load('@/app/session');
  const view = load('@/features/napcat/NapcatView.vue');
  t.after(() => hooks.cleanup?.());
  session.sessionState.adminAuthenticated = true;
  await new Promise(resolve => setTimeout(resolve, 0));
  view.logLines.value = ['stale log'];
  view.logSampling.value = true;
  await view.stopNapcat();
  assert.equal(view.status.value, null);
  assert.equal(view.logLines.value.length, 0);
  assert.equal(view.logSampling.value, false);
  assert.equal(view.runtime.value.available, true);
  assert.equal(view.runtime.value.phase, 'stopped');
  assert.equal(view.runtime.value.oom_killed, false);
  assert.equal(view.canStartRuntime.value, true);
  assert.equal(view.actionBusy.value, null);
});

test('disabled NapCat runtime keeps manual connection available', async t => {
  const hooks = {};
  const calls = [];
  const load = modules(async (path, options) => {
    calls.push({ path, method: options?.method });
    if (path === '/api/admin/napcat/status') return response({ connected: false });
    if (path === '/api/admin/napcat/runtime') return response({ available: false, phase: 'disabled', oom_killed: false });
    if (path === '/api/admin/napcat/connect') return response({ connected: true, logged_in: false, qr_url: 'https://qq.com/manual-qr' });
    return response({ authenticated: false });
  }, hooks);
  const session = load('@/app/session');
  const view = load('@/features/napcat/NapcatView.vue');
  t.after(() => hooks.cleanup?.());
  session.sessionState.adminAuthenticated = true;
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(view.canStartRuntime.value, false);
  assert.equal(view.canStopRuntime.value, false);
  view.token.value = 'manual-token';
  await view.connectNapcat();
  assert.equal(view.status.value.connected, true);
  assert.equal(view.token.value, '');
  assert.equal(view.runtime.value.available, false);
  assert.deepEqual(calls.filter(call => call.path.endsWith('/connect')), [
    { path: '/api/admin/napcat/connect', method: 'POST' },
  ]);
});


function settingsFixture() {
  const config = { ...JSON.parse(execFileSync('uv', ['run', 'python', '-c',
    'import json; from omubot_new.config import Config; from omubot_new.settings import editable; print(json.dumps(editable(Config())))'],
    { cwd: root, encoding: 'utf8' })), persona_name: '', persona_instructions: '' };
  return { revision: 1, config, effective_revision: 1, effective_config: { ...config },
    versions: [1], changed_fields: [], restart_required: false };
}

test('group mode editor saves the selected rule while running config stays unchanged until restart, then deletes only from draft', async () => {
  const snapshot = settingsFixture();
  const runningConfig = JSON.parse(JSON.stringify(snapshot.effective_config));
  const writes = [];
  const load = modules(async (path, options) => {
    if (path === '/api/admin/config' && options?.method === 'PUT') {
      const body = JSON.parse(options.body);
      writes.push({ path, body });
      return response({ ...snapshot, revision: 2, config: body.config,
        effective_revision: 1, effective_config: runningConfig,
        versions: [1, 2], changed_fields: ['group_modes'], restart_required: true });
    }
    return response(snapshot);
  });
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const settings = load('@/features/settings/store');
  await settings.loadSettings();
  const view = load('@/features/settings/SettingsView.vue');

  const before = JSON.stringify(settings.settingsState.draft.group_modes);
  view.newGroupId.value = 'group_17';
  view.newGroupMode.value = 'silent';
  view.addGroupMode();
  assert.deepEqual(JSON.parse(JSON.stringify(settings.settingsState.draft.group_modes)), { group_17: 'silent' });

  view.newGroupId.value = 'group_17';
  view.newGroupMode.value = 'off';
  view.addGroupMode();
  assert.match(view.groupModeError.value, /已有规则/);
  assert.deepEqual(JSON.parse(JSON.stringify(settings.settingsState.draft.group_modes)), { group_17: 'silent' });

  view.newGroupId.value = 'group 17';
  view.addGroupMode();
  assert.match(view.groupModeError.value, /精确群 ID/);
  assert.deepEqual(JSON.parse(JSON.stringify(settings.settingsState.draft.group_modes)), { group_17: 'silent' });
  assert.equal(JSON.stringify(settings.settingsState.snapshot.config.group_modes), before);
  assert.equal(JSON.stringify(settings.settingsState.snapshot.effective_config.group_modes), JSON.stringify(runningConfig.group_modes));

  view.changeGroupMode('group_17', 'off');
  assert.deepEqual(JSON.parse(JSON.stringify(settings.settingsState.draft.group_modes)), { group_17: 'off' });
  assert.equal(settings.settingsState.snapshot.config.group_modes.group_17, undefined);
  assert.equal(settings.settingsState.snapshot.effective_config.group_modes.group_17, undefined);
  assert.equal(view.canSave.value, true);

  await view.runSave();
  assert.equal(writes.length, 1);
  assert.equal(writes[0].path, '/api/admin/config');
  assert.equal(writes[0].body.expected_revision, 1);
  assert.deepEqual(writes[0].body.config.group_modes, { group_17: 'off' });
  assert.equal(settings.settingsState.snapshot.revision, 2);
  assert.equal(settings.settingsState.snapshot.config.group_modes.group_17, 'off');
  assert.equal(settings.settingsState.snapshot.effective_revision, 1);
  assert.equal(settings.settingsState.snapshot.effective_config.group_modes.group_17, undefined);
  assert.equal(settings.settingsState.snapshot.restart_required, true);
  assert.equal(settings.hasSettingsDraftChanges(), false);

  view.removeGroupMode('group_17');
  assert.deepEqual(JSON.parse(JSON.stringify(settings.settingsState.draft.group_modes)), {});
  assert.equal(settings.settingsState.snapshot.config.group_modes.group_17, 'off');
  assert.equal(settings.settingsState.snapshot.effective_config.group_modes.group_17, undefined);
  assert.equal(settings.hasSettingsDraftChanges(), true);
});

test('group profile editor keeps instance config running until restart and stores only persona additions', async () => {
  const snapshot = settingsFixture();
  const runningConfig = JSON.parse(JSON.stringify(snapshot.effective_config));
  const writes = [];
  const load = modules(async (path, options) => {
    if (path === '/api/admin/config' && options?.method === 'PUT') {
      const body = JSON.parse(options.body);
      writes.push({ path, body });
      return response({ ...snapshot, revision: 2, config: body.config,
        effective_revision: 1, effective_config: runningConfig,
        versions: [1, 2], changed_fields: ['group_profiles'], restart_required: true });
    }
    return response(snapshot);
  });
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const settings = load('@/features/settings/store');
  await settings.loadSettings();
  const view = load('@/features/settings/SettingsView.vue');

  view.newGroupProfileId.value = 'group_17';
  view.addGroupProfile();
  assert.deepEqual(JSON.parse(JSON.stringify(settings.settingsState.draft.group_profiles)), {
    group_17: { reply_style: null, custom_prompt: null },
  });

  view.changeGroupReplyStyle('group_17', 'playful');
  view.changeGroupCustomPrompt('group_17', '多接群内的话题。');
  assert.deepEqual(JSON.parse(JSON.stringify(settings.settingsState.draft.group_profiles)), {
    group_17: { reply_style: 'playful', custom_prompt: '多接群内的话题。' },
  });
  view.newGroupProfileId.value = 'group 18';
  view.addGroupProfile();
  assert.match(view.groupProfileError.value, /精确群 ID/);
  assert.equal(settings.settingsState.snapshot.config.group_profiles.group_17, undefined);
  assert.equal(settings.settingsState.snapshot.effective_config.group_profiles.group_17, undefined);
  assert.equal(view.canSave.value, true);

  await view.runSave();
  assert.equal(writes.length, 1);
  assert.equal(writes[0].body.expected_revision, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(writes[0].body.config.group_profiles.group_17)),
    { reply_style: 'playful', custom_prompt: '多接群内的话题。' });
  assert.equal(settings.settingsState.snapshot.effective_revision, 1);
  assert.equal(settings.settingsState.snapshot.restart_required, true);
  assert.equal(settings.settingsState.snapshot.effective_config.group_profiles.group_17, undefined);

  view.changeGroupCustomPrompt('group_17', '');
  assert.equal(settings.settingsState.draft.group_profiles.group_17.custom_prompt, null);
  view.removeGroupProfile('group_17');
  assert.deepEqual(JSON.parse(JSON.stringify(settings.settingsState.draft.group_profiles)), {});
  assert.deepEqual(JSON.parse(JSON.stringify(settings.settingsState.snapshot.config.group_profiles.group_17)),
    { reply_style: 'playful', custom_prompt: '多接群内的话题。' });

  const beforeInvalidImport = JSON.stringify(settings.settingsState.draft);
  const invalid = JSON.parse(beforeInvalidImport);
  invalid.group_profiles.group_17 = { allowed_tools: ['clock.read'] };
  settings.markAdvancedText(JSON.stringify(invalid));
  assert.equal(settings.applyAdvancedText(), false);
  assert.equal(JSON.stringify(settings.settingsState.draft), beforeInvalidImport);
});

test('group calendar editor keeps exact group and lunar rules in the revisioned settings draft', async () => {
  const snapshot = settingsFixture();
  const runningConfig = JSON.parse(JSON.stringify(snapshot.effective_config));
  const writes = [];
  const load = modules(async (path, options) => {
    if (path === '/api/admin/config' && options?.method === 'PUT') {
      const body = JSON.parse(options.body);
      writes.push({ path, body });
      return response({ ...snapshot, revision: 2, config: body.config,
        effective_revision: 1, effective_config: runningConfig,
        versions: [1, 2], changed_fields: ['group_calendar_events'], restart_required: true });
    }
    return response(snapshot);
  });
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const settings = load('@/features/settings/store');
  await settings.loadSettings();
  const view = load('@/features/settings/SettingsView.vue');

  view.newCalendarGroupId.value = 'group_17';
  view.newCalendarName.value = '林';
  view.newCalendarDate.value = '02-29';
  view.newCalendarSubjectId.value = '';
  view.addGroupCalendarEvent();
  assert.match(view.groupCalendarError.value, /精确成员 ID/);
  assert.deepEqual(JSON.parse(JSON.stringify(settings.settingsState.draft.group_calendar_events)), {});

  view.newCalendarSubjectId.value = 'member_17';
  assert.ok(view.calendarCategoryOptions.some((option) => option.label === '特殊日' && option.value === 'special_day'));
  assert.ok(view.calendarRuleKindOptions.some((option) => option.value === 'weekday'));
  assert.ok(view.calendarRuleKindOptions.some((option) => option.value === 'lunar_fixed'));
  assert.ok(view.calendarRuleKindOptions.some((option) => option.value === 'lunar_year_eve'));
  assert.deepEqual(JSON.parse(JSON.stringify(view.calendarLunarMonthOptions.map((option) => option.value))),
    Array.from({ length: 12 }, (_, index) => index + 1));
  assert.ok(view.calendarLunarMonthOptions.some((option) => option.label === '冬月' && option.value === 11));
  assert.equal(view.calendarLunarDayOptions.length, 30);
  view.addGroupCalendarEvent();
  assert.deepEqual(JSON.parse(JSON.stringify(settings.settingsState.draft.group_calendar_events.group_17)), [
    { date: '02-29', name: '林', category: 'birthday', subject_kind: 'member', subject_id: 'member_17' },
  ]);
  view.changeGroupCalendarEvent('group_17', 0, 'date', '');
  assert.equal(settings.settingsState.draft.group_calendar_events.group_17[0].date, '');
  view.changeGroupCalendarEvent('group_17', 0, 'date', '02-29');
  assert.equal(settings.settingsState.draft.group_calendar_events.group_17[0].date, '02-29');
  view.changeGroupCalendarEvent('group_17', 0, 'name', '林同学');
  view.newCalendarName.value = 'Omubot';
  view.newCalendarDate.value = '10-12';
  view.newCalendarSubjectKind.value = 'bot';
  view.newCalendarSubjectId.value = '';
  view.addGroupCalendarEvent();
  view.newCalendarName.value = '群特殊日';
  view.newCalendarDate.value = '12-31';
  view.newCalendarCategory.value = 'special_day';
  view.newCalendarSubjectKind.value = 'group';
  view.addGroupCalendarEvent();
  view.newCalendarName.value = '每年九月第二个星期四';
  view.newCalendarCategory.value = 'anniversary';
  view.newCalendarRuleKind.value = 'weekday';
  view.newCalendarMonth.value = 9;
  view.newCalendarOrdinal.value = 2;
  view.newCalendarWeekday.value = 3;
  view.addGroupCalendarEvent();
  view.newCalendarName.value = '闰二月初一';
  view.newCalendarRuleKind.value = 'lunar_fixed';
  view.newCalendarLunarMonth.value = 2;
  view.newCalendarLunarDay.value = 1;
  view.newCalendarLunarLeapMonth.value = true;
  view.newCalendarSubjectKind.value = 'member';
  view.newCalendarSubjectId.value = 'member_23';
  view.addGroupCalendarEvent();
  view.newCalendarName.value = '除夕';
  view.newCalendarRuleKind.value = 'lunar_year_eve';
  view.newCalendarCategory.value = 'special_day';
  view.newCalendarSubjectKind.value = 'group';
  view.newCalendarSubjectId.value = '';
  view.addGroupCalendarEvent();
  assert.deepEqual(JSON.parse(JSON.stringify(settings.settingsState.draft.group_calendar_events.group_17)), [
    { date: '02-29', name: '林同学', category: 'birthday', subject_kind: 'member', subject_id: 'member_17' },
    { date: '10-12', name: 'Omubot', category: 'birthday', subject_kind: 'bot', subject_id: null },
    { date: '12-31', name: '群特殊日', category: 'special_day', subject_kind: 'group', subject_id: null },
    { weekday: { month: 9, ordinal: 2, weekday: 3 }, name: '每年九月第二个星期四', category: 'anniversary', subject_kind: 'group', subject_id: null },
    { lunar: { kind: 'fixed', month: 2, day: 1, leap_month: true }, name: '闰二月初一', category: 'anniversary', subject_kind: 'member', subject_id: 'member_23' },
    { lunar: { kind: 'year_eve' }, name: '除夕', category: 'special_day', subject_kind: 'group', subject_id: null },
  ]);
  view.changeGroupCalendarWeekday('group_17', 3, 'ordinal', -1);
  view.changeGroupCalendarEvent('group_17', 3, 'name', '每年九月最后一个星期四');

  await view.runSave();
  assert.equal(writes.length, 1);
  assert.equal(writes[0].path, '/api/admin/config');
  assert.equal(writes[0].body.expected_revision, 1);
  assert.equal(settings.settingsState.snapshot.effective_revision, 1);
  assert.equal(settings.settingsState.snapshot.restart_required, true);
  assert.deepEqual(writes[0].body.config.group_calendar_events.group_17,
    JSON.parse(JSON.stringify(settings.settingsState.snapshot.config.group_calendar_events.group_17)));
  assert.deepEqual(writes[0].body.config.group_calendar_events.group_17[3].weekday,
    { month: 9, ordinal: -1, weekday: 3 });
  assert.deepEqual(writes[0].body.config.group_calendar_events.group_17[4].lunar,
    { kind: 'fixed', month: 2, day: 1, leap_month: true });
  assert.deepEqual(writes[0].body.config.group_calendar_events.group_17[5].lunar,
    { kind: 'year_eve' });

  view.changeGroupCalendarLunar('group_17', 4, 'month', 3);
  view.changeGroupCalendarLunar('group_17', 4, 'day', 2);
  view.changeGroupCalendarLunar('group_17', 4, 'leap_month', false);
  assert.deepEqual(JSON.parse(JSON.stringify(settings.settingsState.draft.group_calendar_events.group_17[4].lunar)),
    { kind: 'fixed', month: 3, day: 2, leap_month: false });
  assert.deepEqual(JSON.parse(JSON.stringify(settings.settingsState.snapshot.config.group_calendar_events.group_17[4].lunar)),
    { kind: 'fixed', month: 2, day: 1, leap_month: true });

  view.removeGroupCalendarEvent('group_17', 0);
  assert.deepEqual(JSON.parse(JSON.stringify(settings.settingsState.draft.group_calendar_events.group_17)), [
    { date: '10-12', name: 'Omubot', category: 'birthday', subject_kind: 'bot', subject_id: null },
    { date: '12-31', name: '群特殊日', category: 'special_day', subject_kind: 'group', subject_id: null },
    { weekday: { month: 9, ordinal: -1, weekday: 3 }, name: '每年九月最后一个星期四', category: 'anniversary', subject_kind: 'group', subject_id: null },
    { lunar: { kind: 'fixed', month: 3, day: 2, leap_month: false }, name: '闰二月初一', category: 'anniversary', subject_kind: 'member', subject_id: 'member_23' },
    { lunar: { kind: 'year_eve' }, name: '除夕', category: 'special_day', subject_kind: 'group', subject_id: null },
  ]);
  assert.equal(settings.settingsState.snapshot.config.group_calendar_events.group_17.length, 6);
});

test('settings draft accepts one typed calendar rule and rejects ambiguous or malformed rules', async () => {
  const snapshot = settingsFixture();
  const load = modules(async () => response(snapshot));
  const settings = load('@/features/settings/store');
  await settings.loadSettings();

  const recurring = {
    ...settings.settingsState.draft,
    group_calendar_events: {
      group_17: [{
        weekday: { month: 9, ordinal: 2, weekday: 3 },
        name: '每年九月第二个星期四',
        category: 'anniversary',
        subject_kind: 'group',
        subject_id: null,
      }],
    },
  };
  settings.markAdvancedText(JSON.stringify(recurring));
  assert.equal(settings.applyAdvancedText(), true);
  assert.deepEqual(JSON.parse(JSON.stringify(settings.settingsState.draft.group_calendar_events.group_17[0].weekday)),
    { month: 9, ordinal: 2, weekday: 3 });

  recurring.group_calendar_events.group_17[0].date = '09-10';
  settings.markAdvancedText(JSON.stringify(recurring));
  assert.equal(settings.applyAdvancedText(), false);

  const lunar = {
    ...settings.settingsState.draft,
    group_calendar_events: {
      group_17: [
        {
          lunar: { kind: 'fixed', month: 2, day: 1, leap_month: true },
          name: '闰二月初一',
          category: 'birthday',
          subject_kind: 'member',
          subject_id: 'member_17',
        },
        {
          lunar: { kind: 'year_eve' },
          name: '除夕',
          category: 'special_day',
          subject_kind: 'group',
          subject_id: null,
        },
      ],
    },
  };
  settings.markAdvancedText(JSON.stringify(lunar));
  assert.equal(settings.applyAdvancedText(), true);
  assert.deepEqual(JSON.parse(JSON.stringify(settings.settingsState.draft.group_calendar_events.group_17)),
    lunar.group_calendar_events.group_17);

  const invalidLunar = JSON.parse(JSON.stringify(lunar));
  invalidLunar.group_calendar_events.group_17[0].lunar.day = 31;
  settings.markAdvancedText(JSON.stringify(invalidLunar));
  assert.equal(settings.applyAdvancedText(), false);

  const mixedLunar = JSON.parse(JSON.stringify(lunar));
  mixedLunar.group_calendar_events.group_17[0].date = '09-10';
  settings.markAdvancedText(JSON.stringify(mixedLunar));
  assert.equal(settings.applyAdvancedText(), false);
});

test('Worldbook gates default closed, require an exact allowlist, and remain pending restart', async () => {
  const snapshot = settingsFixture();
  for (const key of [
    'worldbook_enabled', 'worldbook_chat_projection_enabled', 'worldbook_schedule_projection_enabled',
    'worldbook_storylet_enabled', 'worldbook_dream_proposal_enabled', 'worldbook_social_evidence_enabled',
    'worldbook_allowed_groups',
  ]) {
    delete snapshot.config[key];
    delete snapshot.effective_config[key];
  }
  const runningConfig = JSON.parse(JSON.stringify(snapshot.effective_config));
  const writes = [];
  const load = modules(async (path, options) => {
    if (path === '/api/admin/config' && options?.method === 'PUT') {
      const body = JSON.parse(options.body);
      writes.push(body);
      return response({ ...snapshot, revision: 2, config: body.config,
        effective_revision: 1, effective_config: runningConfig,
        versions: [1, 2], changed_fields: ['worldbook_enabled', 'worldbook_schedule_projection_enabled', 'worldbook_allowed_groups'], restart_required: true });
    }
    return response(snapshot);
  });
  const settings = load('@/features/settings/store');
  await settings.loadSettings();
  const view = load('@/features/settings/SettingsView.vue');

  assert.equal(settings.settingsState.draft.worldbook_enabled, false);
  assert.deepEqual(JSON.parse(JSON.stringify(settings.settingsState.draft.worldbook_allowed_groups)), []);
  view.changeWorldbookGate('worldbook_schedule_projection_enabled', true);
  assert.equal(view.draftWorldbookScheduleReady.value, false);
  view.newWorldbookGroupId.value = 'group 17';
  view.addWorldbookGroup();
  assert.match(view.worldbookGroupError.value, /精确群 ID/);
  view.newWorldbookGroupId.value = 'group_17';
  view.addWorldbookGroup();
  assert.deepEqual(JSON.parse(JSON.stringify(settings.settingsState.draft.worldbook_allowed_groups)), ['group_17']);
  view.newWorldbookGroupId.value = 'group_17';
  view.addWorldbookGroup();
  assert.match(view.worldbookGroupError.value, /已在/);
  assert.equal(view.draftWorldbookScheduleReady.value, false);

  view.changeWorldbookGate('worldbook_enabled', true);
  assert.equal(view.draftWorldbookScheduleReady.value, true);
  await view.runSave();
  assert.equal(writes.length, 1);
  assert.equal(writes[0].config.worldbook_enabled, true);
  assert.equal(writes[0].config.worldbook_schedule_projection_enabled, true);
  assert.deepEqual(writes[0].config.worldbook_allowed_groups, ['group_17']);
  assert.equal(settings.settingsState.snapshot.effective_config.worldbook_enabled, false);
  assert.deepEqual(JSON.parse(JSON.stringify(settings.settingsState.snapshot.effective_config.worldbook_allowed_groups)), []);
  assert.equal(settings.settingsState.snapshot.restart_required, true);
});

test('stream reply gate normalizes legacy drafts, preserves CAS, and reports tool fallback', async () => {
  const snapshot = settingsFixture();
  delete snapshot.config.stream_reply_enabled;
  delete snapshot.effective_config.stream_reply_enabled;
  delete snapshot.config.planned_reply_enabled;
  delete snapshot.config.planned_reply_groups;
  delete snapshot.effective_config.planned_reply_enabled;
  delete snapshot.effective_config.planned_reply_groups;
  const runningConfig = JSON.parse(JSON.stringify(snapshot.effective_config));
  const writes = [];
  const load = modules(async (path, options) => {
    if (path === '/api/admin/config' && options?.method === 'PUT') {
      const body = JSON.parse(options.body);
      writes.push(body);
      return response({ ...snapshot, revision: 2, config: body.config,
        effective_revision: 1, effective_config: runningConfig,
        versions: [1, 2], changed_fields: ['stream_reply_enabled'], restart_required: true });
    }
    return response(snapshot);
  });
  const settings = load('@/features/settings/store');
  await settings.loadSettings();
  const view = load('@/features/settings/SettingsView.vue');
  assert.equal(settings.settingsState.draft.stream_reply_enabled, false);
  assert.equal(view.draftStreamReplyStatus.value, '关闭');
  assert.equal(view.draftPlannedReplyStatus.value, '关闭');
  assert.deepEqual(Array.from(settings.settingsState.draft.planned_reply_groups), []);

  const legacyDraft = JSON.parse(settings.settingsState.advancedText);
  delete legacyDraft.stream_reply_enabled;
  delete legacyDraft.planned_reply_enabled;
  delete legacyDraft.planned_reply_groups;
  settings.markAdvancedText(JSON.stringify(legacyDraft));
  assert.equal(settings.applyAdvancedText(), true);
  assert.equal(settings.settingsState.draft.stream_reply_enabled, false);

  view.changeConfigBoolean('stream_reply_enabled', true);
  view.changePlannedReplyGroups('test-group');
  view.changeConfigBoolean('planned_reply_enabled', true);
  assert.match(view.draftStreamReplyStatus.value, /配置了工具能力/);
  for (const feature of view.scopedFeatures) {
    view.changeConfigBoolean(feature.enabled, true);
    assert.equal(settings.applyAdvancedText(), false);
    view.changeScopedFeatureGroups(feature.groups, 'test-group');
    assert.equal(settings.applyAdvancedText(), true);
    assert.match(view.scopedFeatureStatus(settings.settingsState.draft, feature), /test-group/);
  }
  assert.equal(await settings.saveSettings(), true);
  assert.equal(writes[0].expected_revision, 1);
  assert.equal(writes[0].config.stream_reply_enabled, true);
  assert.equal(writes[0].config.planned_reply_enabled, true);
  assert.deepEqual(writes[0].config.planned_reply_groups, ['test-group']);
  assert.equal(view.runningPlannedReplyStatus.value, '关闭');
  for (const feature of view.scopedFeatures) {
    assert.equal(writes[0].config[feature.enabled], true);
    assert.deepEqual(writes[0].config[feature.groups], ['test-group']);
    assert.equal(view.scopedFeatureStatus(settings.settingsState.snapshot.effective_config, feature), '关闭');
  }
  assert.equal(settings.settingsState.snapshot.effective_config.stream_reply_enabled, false);
  assert.equal(settings.settingsState.snapshot.restart_required, true);

  settings.updateConfigField('tool_capabilities', []);
  for (const apiFormat of ['openai_responses', 'anthropic']) {
    settings.settingsState.draft.models.default.api_format = apiFormat;
    assert.match(view.draftStreamReplyStatus.value, /无工具、无图片的群文字回复适用/);
  }
  settings.settingsState.draft.models.default.api_format = 'deepseek';
  assert.match(view.draftStreamReplyStatus.value, /无工具、无图片的群文字回复适用/);
});

test('mention force reply gate defaults on and explains the independent bot-reply rule', async () => {
  const snapshot = settingsFixture();
  delete snapshot.config.mention_force_reply_enabled;
  delete snapshot.effective_config.mention_force_reply_enabled;
  const runningConfig = JSON.parse(JSON.stringify(snapshot.effective_config));
  const writes = [];
  const load = modules(async (path, options) => {
    if (path === '/api/admin/config' && options?.method === 'PUT') {
      const body = JSON.parse(options.body);
      writes.push(body);
      return response({ ...snapshot, revision: 2, config: body.config,
        effective_revision: 1, effective_config: runningConfig,
        versions: [1, 2], changed_fields: ['mention_force_reply_enabled'], restart_required: true });
    }
    return response(snapshot);
  });
  const settings = load('@/features/settings/store');
  await settings.loadSettings();
  const view = load('@/features/settings/SettingsView.vue');

  assert.equal(settings.settingsState.draft.mention_force_reply_enabled, true);
  const legacyDraft = JSON.parse(settings.settingsState.advancedText);
  delete legacyDraft.mention_force_reply_enabled;
  settings.markAdvancedText(JSON.stringify(legacyDraft));
  assert.equal(settings.applyAdvancedText(), true);
  assert.equal(settings.settingsState.draft.mention_force_reply_enabled, true);

  view.changeConfigBoolean('mention_force_reply_enabled', false);
  assert.equal(settings.settingsState.draft.mention_force_reply_enabled, false);
  assert.equal(await settings.saveSettings(), true);
  assert.equal(writes[0].config.mention_force_reply_enabled, false);
  assert.equal(settings.settingsState.snapshot.effective_config.mention_force_reply_enabled, true);
  const source = readFileSync(resolve(root, 'web/src/features/settings/SettingsView.vue'), 'utf8');
  assert.match(source, /关闭后进入普通群聊参与规则/);
  assert.match(source, /引用 Bot 回复仍独立使用强规则/);
});

test('memory capture normalizes legacy settings and saves its task binding through CAS', async () => {
  const snapshot = settingsFixture();
  for (const config of [snapshot.config, snapshot.effective_config]) {
    delete config.memory_capture_enabled;
    delete config.memory_capture_groups;
    delete config.memory_extraction_domains;
    delete config.memory_spool_dir;
    delete config.memory_key_file;
  }
  const runningConfig = JSON.parse(JSON.stringify(snapshot.effective_config));
  const writes = [];
  const load = modules(async (path, options) => {
    if (path === '/api/admin/config' && options?.method === 'PUT') {
      const body = JSON.parse(options.body);
      writes.push(body);
      return response({ ...snapshot, revision: 2, config: body.config,
        effective_revision: 1, effective_config: runningConfig,
        versions: [1, 2], changed_fields: ['memory_capture_enabled', 'memory_capture_groups', 'task_models'],
        restart_required: true });
    }
    return response(snapshot);
  });
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const settings = load('@/features/settings/store');
  await settings.loadSettings();

  assert.equal(settings.settingsState.draft.memory_capture_enabled, false);
  assert.deepEqual(JSON.parse(JSON.stringify(settings.settingsState.draft.memory_capture_groups)), []);
  assert.deepEqual(JSON.parse(JSON.stringify(settings.settingsState.draft.memory_extraction_domains)), ['fact', 'slang', 'style', 'episode']);
  assert.equal(settings.settingsState.draft.memory_spool_dir, '');
  assert.equal(settings.settingsState.draft.memory_key_file, '');

  const legacy = JSON.parse(settings.settingsState.advancedText);
  delete legacy.memory_capture_enabled;
  delete legacy.memory_capture_groups;
  delete legacy.memory_extraction_domains;
  delete legacy.memory_spool_dir;
  delete legacy.memory_key_file;
  settings.markAdvancedText(JSON.stringify(legacy));
  assert.equal(settings.applyAdvancedText(), true);
  assert.equal(settings.settingsState.draft.memory_capture_enabled, false);

  const domainsDraft = JSON.parse(settings.settingsState.advancedText);
  domainsDraft.memory_extraction_domains = ['fact', 'slang', 'style'];
  settings.markAdvancedText(JSON.stringify(domainsDraft));
  assert.equal(settings.applyAdvancedText(), true);
  assert.deepEqual(JSON.parse(JSON.stringify(settings.settingsState.draft.memory_extraction_domains)), ['fact', 'slang', 'style']);
  domainsDraft.memory_extraction_domains = ['fact', 'episode', 'episode'];
  settings.markAdvancedText(JSON.stringify(domainsDraft));
  assert.equal(settings.applyAdvancedText(), false);
  assert.deepEqual(JSON.parse(JSON.stringify(settings.settingsState.draft.memory_extraction_domains)), ['fact', 'slang', 'style']);

  const invalid = JSON.parse(settings.settingsState.advancedText);
  invalid.memory_capture_groups = ['group 17'];
  settings.markAdvancedText(JSON.stringify(invalid));
  assert.equal(settings.applyAdvancedText(), false);
  assert.equal(settings.settingsState.draft.memory_capture_groups.length, 0);
  settings.markAdvancedText(settings.settingsState.advancedBaseline);

  settings.updateTaskBinding('memory', 'default');
  settings.updateConfigField('memory_capture_groups', ['group_17']);
  settings.updateConfigField('memory_spool_dir', '../memory-spool');
  settings.updateConfigField('memory_key_file', '../memory-key/archive.key');
  settings.updateConfigField('memory_capture_enabled', true);
  assert.equal(await settings.saveSettings(), true);

  assert.equal(writes.length, 1);
  assert.equal(writes[0].expected_revision, 1);
  assert.equal(writes[0].config.task_models.memory, 'default');
  assert.equal(writes[0].config.memory_capture_enabled, true);
  assert.deepEqual(writes[0].config.memory_capture_groups, ['group_17']);
  assert.deepEqual(writes[0].config.memory_extraction_domains, ['fact', 'slang', 'style']);
  assert.equal(writes[0].config.memory_spool_dir, '../memory-spool');
  assert.equal(writes[0].config.memory_key_file, '../memory-key/archive.key');
  assert.equal(settings.settingsState.snapshot.effective_config.memory_capture_enabled, false);
  assert.equal(settings.settingsState.snapshot.restart_required, true);
});

test('memory correction ignores duplicate clicks, keeps a failed draft, and saves after retry', async () => {
  const firstPost = deferred();
  const fact = {
    fact_id: 'fact-17', subject_id: 'user-17', predicate: 'likes.game', value: '旧值',
    source_ids: ['source-17'], fact_revision: 4, status: 'active', observed_at: 100,
    valid_from: null, valid_to: null, applied_at: 110,
  };
  const created = {
    candidate_id: 'candidate-correction-17', subject_id: 'user-17', predicate: 'likes.game',
    value: '新值', action: 'supersede', target_fact_id: 'fact-17', source_ids: ['source-17'],
    candidate_revision: 1, status: 'conflict_pending', conflict_set_id: 'conflict-17',
    skip_reason: null, suggestion_reason: null, observed_at: 120, valid_from: null, valid_to: null,
  };
  const calls = [];
  let correctionPosts = 0;
  const load = modules(async (path, options = {}) => {
    const call = { path: String(path), method: options.method ?? 'GET', headers: new Headers(options.headers), body: options.body };
    calls.push(call);
    if (call.path === '/api/admin/memory/correction') {
      correctionPosts += 1;
      return correctionPosts === 1 ? firstPost.promise : response(created);
    }
    if (call.path.startsWith('/api/admin/memory/facts?')) return response({ items: [fact], next_cursor: null });
    if (call.path.startsWith('/api/admin/memory/candidates?')) return response({ items: [], next_cursor: null });
    return response({});
  });
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const view = load('@/features/memory/MemoryView.vue');
  view.factGroupIdInput.value = 'group-17';
  view.loadedFactGroupId.value = 'group-17';
  view.groupIdInput.value = 'group-17';
  view.loadedGroupId.value = 'group-17';
  view.loadedStatus.value = 'all';
  view.facts.value = [fact];
  view.correctionDrafts[fact.fact_id] = { sourceId: 'source-17', value: '新值', verified: true };

  const pending = view.submitCorrection(fact);
  const duplicate = view.submitCorrection(fact);
  assert.equal(correctionPosts, 1);
  const mutation = calls.find(call => call.path === '/api/admin/memory/correction');
  assert.equal(mutation.headers.get('X-Omubot-Request'), '1');
  assert.equal(JSON.parse(mutation.body).expected_fact_revision, 4);

  firstPost.resolve(response({ error: 'revision_conflict' }, 409));
  await Promise.all([pending, duplicate]);
  assert.deepEqual(JSON.parse(JSON.stringify(view.correctionDrafts[fact.fact_id])), {
    sourceId: 'source-17', value: '新值', verified: true,
  });
  assert.match(view.factsActionError.value, /版本已变化/);
  assert.equal(view.facts.value.length, 1);
  assert.equal(calls.some(call => call.path.startsWith('/api/admin/memory/candidates?')), true, JSON.stringify(calls.map(call => call.path)));

  await view.submitCorrection(fact);
  assert.equal(correctionPosts, 2);
  assert.equal(view.correctionDrafts[fact.fact_id], undefined);
  assert.equal(view.candidates.value[0].candidate_id, 'candidate-correction-17');
  assert.match(view.factsNotice.value, /请在候选区按冲突核验、批准和应用分步处理/);
});

test('memory conflicts cannot be approved and approval remains separate from apply', async () => {
  const calls = [];
  const approved = {
    kind: 'fact', candidate_id: 'candidate-18', subject_id: 'user-18', predicate: 'likes.game', value: '值',
    action: 'add', target_fact_id: null, source_ids: ['source-18'], candidate_revision: 2,
    status: 'approved', conflict_set_id: null, skip_reason: null, suggestion_reason: 'stable_preference',
    observed_at: 120, valid_from: null, valid_to: null,
  };
  const applied = { ...approved, candidate_revision: 3, status: 'applied' };
  const load = modules(async (path, options = {}) => {
    calls.push({ path: String(path), method: options.method ?? 'GET', body: options.body });
    if (path === '/api/admin/memory/review') return response(approved);
    if (path === '/api/admin/memory/apply') return response(applied);
    return response({});
  });
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const view = load('@/features/memory/MemoryView.vue');
  view.groupIdInput.value = 'group-18';
  view.loadedGroupId.value = 'group-18';
  view.loadedStatus.value = 'all';
  view.candidates.value = [{ ...approved, status: 'pending', candidate_revision: 1 }];
  const conflict = { ...approved, candidate_id: 'conflict-18', status: 'conflict_pending' };
  await view.reviewCandidate(conflict, 'approved');
  assert.equal(calls.length, 0);

  await view.reviewCandidate({ ...approved, status: 'pending', candidate_revision: 1 }, 'approved');
  assert.deepEqual(calls.map(call => call.path), ['/api/admin/memory/review']);
  assert.equal(view.candidates.value[0].status, 'approved');
  await view.applyCandidate(approved);
  assert.deepEqual(calls.map(call => call.path), ['/api/admin/memory/review', '/api/admin/memory/apply']);
  assert.equal(view.candidates.value[0].status, 'applied');
});

test('memory episode apply and Social retry report N6 and N7 as separate stages', async () => {
  const retryReply = deferred();
  const calls = [];
  const approved = {
    kind: 'episode', candidate_id: 'episode-social-27', candidate_revision: 3,
    created_at: 170, episode_state: 'approved', source_ids: ['archive-source-27'],
    status: 'approved', application_status: 'not_applied',
    value: { situation: 'fixture', observed_context: 'not rendered by this test', action_taken: 'fixture', outcome_signal: 'fixture', reflection: 'fixture' },
    social: { capture_status: 'pending', story_status: 'waiting_for_capture', experience_id: null, error_code: null, retryable: false },
  };
  const blocked = {
    ...approved,
    episode_state: 'enabled_for_prompt',
    application_status: 'applied',
    social: { capture_status: 'captured', story_status: 'pending', experience_id: 'experience-27', error_code: 'story_main_missing', retryable: true },
  };
  const completed = {
    ...blocked,
    social: { capture_status: 'captured', story_status: 'committed', experience_id: 'experience-27', error_code: null, retryable: false },
  };
  const load = modules(async (path, options = {}) => {
    const call = {
      path: String(path),
      method: options.method ?? 'GET',
      headers: new Headers(options.headers),
      body: options.body,
    };
    calls.push(call);
    if (call.path === '/api/admin/memory/apply') return response(blocked);
    if (call.path === '/api/admin/memory/social/retry') return retryReply.promise;
    return response({});
  });
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const view = load('@/features/memory/MemoryView.vue');
  view.groupIdInput.value = 'group-social-27';
  view.loadedGroupId.value = 'group-social-27';
  view.loadedStatus.value = 'all';
  view.candidates.value = [approved];

  await view.applyCandidate(approved);
  assert.equal(view.candidates.value[0].application_status, 'applied');
  assert.match(view.notice.value, /N6 事件经历已应用/);
  assert.match(view.notice.value, /N7 Social 捕获：已记录；Story：等待提交/);
  assert.match(view.notice.value, /当前群没有可用主线/);
  assert.doesNotMatch(view.notice.value, /全部成功|共同经历已完成/);
  assert.equal(view.canRetrySocial(view.candidates.value[0]), true);
  session.sessionState.adminAuthenticated = false;
  assert.equal(view.canRetrySocial(view.candidates.value[0]), false);
  session.sessionState.adminAuthenticated = true;

  const first = view.retrySocialExperience(view.candidates.value[0]);
  const duplicate = view.retrySocialExperience(view.candidates.value[0]);
  assert.equal(calls.filter(call => call.path === '/api/admin/memory/social/retry').length, 1);
  const retry = calls.find(call => call.path === '/api/admin/memory/social/retry');
  assert.equal(retry.method, 'POST');
  assert.equal(retry.headers.get('X-Omubot-Request'), '1');
  assert.deepEqual(JSON.parse(retry.body), { group_id: 'group-social-27', candidate_id: 'episode-social-27' });

  retryReply.resolve(response(completed));
  await Promise.all([first, duplicate]);
  assert.equal(view.candidates.value[0].social.story_status, 'committed');
  assert.match(view.notice.value, /N6 事件经历仍为已应用/);
  assert.match(view.notice.value, /N7 Social 捕获：已记录；Story：已提交通用剧情/);
  assert.equal(view.canRetrySocial(view.candidates.value[0]), false);
  assert.equal(view.socialCaptureStatusLabel('invalidated'), '来源已撤权');
  assert.equal(view.socialStoryStatusLabel('waiting_for_capture'), '等待 Social 捕获');
  const revoked = { ...completed.social, capture_status: 'invalidated' };
  assert.match(view.socialProgressNotice(revoked), /历史通用剧情已保留（不再作为当前共同经历引用）/);
  assert.equal(view.socialStoryStatusLabel('pending', 'invalidated'), '已停止（来源已撤权）');
});

test('memory empty diagnostic pages keep continuation visible', async () => {
  const calls = [];
  const load = modules(path => {
    const url = new URL(String(path), 'http://localhost');
    calls.push(url);
    if (url.pathname === '/api/admin/memory/domain-failures') {
      return response(url.searchParams.has('after')
        ? { items: [{ result_id: 'failure-after-empty' }], next_cursor: null }
        : { items: [], next_cursor: 'failure-after-empty' });
    }
    if (url.pathname === '/api/admin/memory/extraction-run-diagnostics') {
      return response(url.searchParams.has('after')
        ? { items: [{ run_id: 'run-after-empty' }], next_cursor: null }
        : { items: [], next_cursor: 'run-after-empty' });
    }
    return response({});
  });
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const view = load('@/features/memory/MemoryView.vue');
  view.groupIdInput.value = 'group-empty-page';
  view.loadedGroupId.value = 'group-empty-page';
  view.loadedStatus.value = 'all';

  await Promise.all([
    view.readDomainFailures(null, true),
    view.readExtractionRunDiagnostics(null, true),
  ]);

  assert.deepEqual(Array.from(view.domainFailures.value), []);
  assert.deepEqual(Array.from(view.extractionRuns.value), []);
  assert.equal(view.nextFailureCursor.value, 'failure-after-empty');
  assert.equal(view.nextRunCursor.value, 'run-after-empty');
  assert.equal(view.canLoadMoreFailures.value, true);
  assert.equal(view.canLoadMoreRuns.value, true);
  assert.equal(
    view.failureEmptyDescription.value,
    '当前页没有仍可见的逐域提取失败；仍有更多诊断，可继续加载。',
  );
  assert.equal(
    view.runEmptyDescription.value,
    '当前页没有仍缺少回执的终止提取记录；仍有更多诊断，可继续加载。',
  );

  await Promise.all([view.loadMoreDomainFailures(), view.loadMoreExtractionRuns()]);
  assert.equal(view.domainFailures.value[0].result_id, 'failure-after-empty');
  assert.equal(view.extractionRuns.value[0].run_id, 'run-after-empty');
  assert.equal(calls.filter(url => url.searchParams.has('after')).length, 2);
});

test('memory pages clear on admin expiry and late responses cannot repopulate them', async () => {
  const waits = new Map([
    ['/api/admin/memory/candidates', deferred()],
    ['/api/admin/memory/facts', deferred()],
    ['/api/admin/memory/hot', deferred()],
    ['/api/admin/memory/retrieval-diagnostic', deferred()],
  ]);
  const load = modules(path => {
    const match = [...waits.keys()].find(prefix => String(path).startsWith(prefix));
    return match ? waits.get(match).promise : Promise.resolve(response({}));
  });
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const view = load('@/features/memory/MemoryView.vue');
  view.groupIdInput.value = 'group-19';
  view.factGroupIdInput.value = 'group-19';
  view.hotGroupIdInput.value = 'group-19';
  view.hotSubjectIdInput.value = 'user-19';
  view.retrievalGroupId.value = 'group-19';
  view.retrievalSubjectId.value = 'user-19';
  view.retrievalQuery.value = 'temporary query';
  const pending = [view.searchCandidates(), view.searchFacts(), view.searchHotFacts(), view.runRetrievalDiagnostic()];

  session.sessionState.adminAuthenticated = false;
  await webRequire('vue').nextTick();
  for (const [endpoint, wait] of waits) {
    if (endpoint.endsWith('/candidates')) wait.resolve(response({ items: [{ candidate_id: 'stale' }], next_cursor: null }));
    else if (endpoint.endsWith('/facts')) wait.resolve(response({ items: [{ fact_id: 'stale' }], next_cursor: null }));
    else if (endpoint.endsWith('/hot')) wait.resolve(response({ items: [{ fact_id: 'stale' }], truncated: false }));
    else wait.resolve(response({ hot_hit_count: 1, cold_hit_count: 0, cold_pack_state: 'ready', total_budget: 10,
      total_budget_used: 1, cold_budget_used: 0, policy_revision: 1, visibility_revision: 1,
      retrieve_mode: 'hybrid', omitted_reason_codes: [], reason_codes: ['stale'] }));
  }
  await Promise.all(pending);
  assert.deepEqual(Array.from(view.candidates.value), []);
  assert.deepEqual(Array.from(view.facts.value), []);
  assert.deepEqual(Array.from(view.hotFacts.value), []);
  assert.equal(view.retrievalResult.value, null);
  assert.equal(view.loading.value, false);
  assert.equal(view.factsLoading.value, false);
  assert.equal(view.hotLoading.value, false);
  assert.equal(view.retrievalLoading.value, false);
  assert.equal(view.retrievalQuery.value, '');
});

test('memory disable conflicts refresh facts and queue; stale conflict targets require reinspection', async () => {
  const fact = {
    fact_id: 'fact-20', subject_id: 'user-20', predicate: 'likes.game', value: '值',
    source_ids: ['source-20'], fact_revision: 4, status: 'active', observed_at: 100,
    valid_from: null, valid_to: null, applied_at: 110,
  };
  const pendingCandidate = {
    kind: 'fact', candidate_id: 'candidate-20', subject_id: 'user-20', predicate: 'likes.game', value: '新值',
    action: 'add', target_fact_id: null, source_ids: ['source-20'], candidate_revision: 1,
    status: 'pending', conflict_set_id: null, skip_reason: null, suggestion_reason: null,
    observed_at: 120, valid_from: null, valid_to: null,
  };
  const refreshedCandidate = { ...pendingCandidate, candidate_revision: 2 };
  const calls = [];
  let conflictRefresh = false;
  let refreshedConflict = null;
  const load = modules(async (path, options = {}) => {
    calls.push({ path: String(path), method: options.method ?? 'GET' });
    if (path === '/api/admin/memory/disable' || path === '/api/admin/memory/resolve-conflict') {
      return response({ error: 'revision_conflict' }, 409);
    }
    if (String(path).startsWith('/api/admin/memory/facts?')) {
      return response({ items: [{ ...fact, fact_revision: 5 }], next_cursor: null });
    }
    if (String(path).startsWith('/api/admin/memory/candidates?')) {
      return response({ items: [conflictRefresh ? refreshedConflict : refreshedCandidate], next_cursor: null });
    }
    return response({});
  });
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const view = load('@/features/memory/MemoryView.vue');
  view.factGroupIdInput.value = 'group-20';
  view.loadedFactGroupId.value = 'group-20';
  view.groupIdInput.value = 'group-20';
  view.loadedGroupId.value = 'group-20';
  view.loadedStatus.value = 'all';
  view.facts.value = [fact];
  view.candidates.value = [pendingCandidate];
  view.requestDisableFact(fact);
  await view.confirmDisableFact(fact);
  assert.equal(view.facts.value[0].fact_revision, 5);
  assert.equal(view.candidates.value[0].candidate_revision, 2);
  assert.equal(calls.some(call => call.path.startsWith('/api/admin/memory/facts?')), true);
  assert.equal(calls.some(call => call.path.startsWith('/api/admin/memory/candidates?')), true);

  const conflict = {
    ...pendingCandidate, candidate_id: 'conflict-20', action: 'supersede', target_fact_id: fact.fact_id,
    candidate_revision: 3, status: 'conflict_pending',
  };
  view.candidates.value = [conflict];
  view.conflictCandidateId.value = conflict.candidate_id;
  view.conflictTargetFact.value = fact;
  view.conflictSourceVerified.value = true;
  refreshedConflict = { ...conflict, candidate_revision: 4 };
  conflictRefresh = true;
  await view.resolveConflict(conflict);
  assert.equal(view.conflictTargetFact.value, null);
  assert.equal(view.conflictSourceVerified.value, false);
  assert.equal(view.candidates.value[0].candidate_revision, 4);
  assert.equal(view.candidates.value[0].status, 'conflict_pending');
});

test('model deletion revalidates the last profile and clears obsolete confirmation', async () => {
  const snapshot = settingsFixture();
  const load = modules(async () => response(snapshot));
  const settings = load('@/features/settings/store');
  await settings.loadSettings();
  settings.addProfile('second');
  settings.requestDeleteProfile();
  const only = JSON.parse(JSON.stringify(settings.settingsState.draft));
  only.models = { second: only.models.second }; only.active_model = 'second';
  settings.markAdvancedText(JSON.stringify(only));
  assert.equal(settings.applyAdvancedText(), true);
  assert.equal(settings.settingsState.pendingDeleteProfile, null);
  settings.settingsState.pendingDeleteProfile = 'second';
  settings.confirmDeleteProfile();
  assert.equal(settings.profileNames().length, 1);
});

test('vision opt-in stays scoped to each named profile and persists through the full config save', async () => {
  const snapshot = settingsFixture();
  const writes = [];
  const load = modules(async (path, options) => {
    if (path === '/api/admin/config' && options?.method === 'PUT') {
      const body = JSON.parse(options.body);
      writes.push(body);
      return response({ ...snapshot, revision: 2, config: body.config,
        versions: [1, 2], changed_fields: ['models'], restart_required: true });
    }
    return response(snapshot);
  });
  const settings = load('@/features/settings/store');
  await settings.loadSettings();
  const view = load('@/features/settings/SettingsView.vue');

  assert.equal(settings.settingsState.draft.models.default.vision_enabled, false);
  view.changeProfileBoolean('vision_enabled', true);
  assert.equal(settings.settingsState.draft.models.default.vision_enabled, true);

  settings.addProfile('second');
  assert.equal(settings.settingsState.draft.models.second.vision_enabled, false);
  view.changeProfileBoolean('vision_enabled', true);
  settings.selectProfile('default');
  assert.equal(settings.settingsState.draft.models.default.vision_enabled, true);
  settings.selectProfile('second');
  assert.equal(settings.settingsState.draft.models.second.vision_enabled, true);

  await view.runSave();
  assert.equal(writes.length, 1);
  assert.equal(writes[0].config.models.default.vision_enabled, true);
  assert.equal(writes[0].config.models.second.vision_enabled, true);
});

test('legacy snapshots and advanced JSON default vision off without discarding the draft', async () => {
  const snapshot = settingsFixture();
  delete snapshot.config.models.default.vision_enabled;
  delete snapshot.effective_config.models.default.vision_enabled;
  const load = modules(async () => response(snapshot));
  const settings = load('@/features/settings/store');
  await settings.loadSettings();
  assert.equal(settings.settingsState.draft.models.default.vision_enabled, false);

  settings.updateProfileField('default', 'model', 'draft-model');
  const legacy = JSON.parse(settings.settingsState.advancedText);
  delete legacy.models.default.vision_enabled;
  settings.markAdvancedText(JSON.stringify(legacy));
  assert.equal(settings.applyAdvancedText(), true);
  assert.equal(settings.settingsState.draft.models.default.vision_enabled, false);
  assert.equal(settings.settingsState.draft.models.default.model, 'draft-model');
});

test('saving stays on edited model and dirty rollback cannot discard it', async () => {
  const snapshot = settingsFixture();
  let rollbacks = 0;
  const load = modules(async (path, options) => {
    if (path.includes('rollback')) rollbacks++;
    if (options?.method === 'PUT') return response({...snapshot, revision: 2, config: JSON.parse(options.body).config});
    return response(snapshot);
  });
  const settings = load('@/features/settings/store');
  await settings.loadSettings();
  settings.addProfile('second');
  assert.equal(await settings.rollbackSettings(1), false);
  assert.equal(rollbacks, 0);
  await settings.saveSettings();
  assert.equal(settings.settingsState.selectedProfile, 'second');
  settings.updateConfigField('active_model', 'second');
  settings.updateTaskBinding('thinker', 'second');
  settings.requestDeleteProfile(); settings.confirmDeleteProfile();
  assert.equal(settings.settingsState.draft.active_model, 'default');
  assert.equal(settings.settingsState.draft.task_models.thinker, undefined);
});

test('combined save orders config before key and reports partial failure', async () => {
  const snapshot = settingsFixture();
  const calls = [];
  const load = modules(async (path, options) => {
    if (options?.method === 'PUT') {
      calls.push({path, body: JSON.parse(options.body)});
      if (path.endsWith('/model-key')) return response({detail:'invalid_key'}, 422);
      return response({...snapshot, revision: 2, config: JSON.parse(options.body).config});
    }
    return response(snapshot);
  });
  const session = load('@/app/session'); session.sessionState.adminAuthenticated = true;
  const settings = load('@/features/settings/store'); await settings.loadSettings();
  settings.addProfile('second');
  const view = load('@/features/settings/SettingsView.vue');
  view.catalogKey.value = 'synthetic';
  await view.runSave();
  assert.deepEqual(calls.map(x=>x.path), ['/api/admin/config','/api/admin/model-key']);
  assert.equal(calls[1].body.profile, 'second');
  assert.equal(calls[1].body.expected_revision, 2);
  assert.equal(view.catalogKey.value, 'synthetic');
  assert.match(view.modelActionMessage.value, /配置已保存.*密钥保存失败/);
  assert.equal(view.canSave.value, true);
});

test('catalog can use saved credentials and failed refresh preserves prior list', async () => {
  const snapshot = settingsFixture();
  let catalogCalls = 0;
  const load = modules(async (path, options) => {
    if (path === '/api/admin/models') {
      const body = JSON.parse(options.body);
      assert.equal(body.api_key, ''); assert.equal(body.profile,'default');
      assert.equal(body.expected_revision, 1);
      return ++catalogCalls === 1 ? response({models:['actual-id'],source_url:'https://provider.test/models',pages:1,truncated:false,display_names:{}})
        : response({error:'upstream_http'}, 400);
    }
    return response(snapshot);
  });
  const settings = load('@/features/settings/store'); await settings.loadSettings();
  const view = load('@/features/settings/SettingsView.vue');
  await view.fetchCatalog(); await view.fetchCatalog();
  assert.deepEqual([...view.catalogModels.value], ['actual-id']);
});


test('NapCat retains last known login on transient failure and automatically recovers', async t => {
  const hooks = {};
  let failing = false;
  let recoveredReads = 0;
  const load = modules(async path => {
    if (path.endsWith('/runtime')) return response({ available: true, phase: 'running', oom_killed: false });
    if (failing) return response({ error: 'upstream_unavailable' }, 502);
    recoveredReads++;
    return response({ connected: true, logged_in: true, offline: false, phase: 'online' });
  }, hooks);
  const session = load('@/app/session');
  const view = load('@/features/napcat/NapcatView.vue');
  t.after(() => hooks.cleanup?.());
  session.sessionState.adminAuthenticated = true;
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(view.status.value.logged_in, true);
  failing = true;
  await view.loadStatus();
  assert.equal(view.status.value.logged_in, true);
  assert.match(view.statusError.value, /QQ 状态读取失败/);
  failing = false;
  const before = recoveredReads;
  await new Promise(resolve => setTimeout(resolve, 5200));
  assert.ok(recoveredReads > before);
  assert.equal(view.statusError.value, '');
  assert.equal(view.status.value.logged_in, true);
});


test('online NapCat with a retained QR URL never asks for another scan', async t => {
  const hooks = {};
  const load = modules(async path => {
    if (path.endsWith('/runtime')) return response({ available: true, phase: 'running', oom_killed: false });
    if (path.endsWith('/start')) return response({ connected: true, logged_in: true, qr_url: 'https://qq.com/old-qr' });
    return response({ connected: false });
  }, hooks);
  const session = load('@/app/session');
  const view = load('@/features/napcat/NapcatView.vue');
  t.after(() => hooks.cleanup?.());
  session.sessionState.adminAuthenticated = true;
  await new Promise(resolve => setTimeout(resolve, 0));
  await view.startNapcat();
  assert.match(view.actionMessage.value, /QQ 已在线，无需再次扫码/);
});

test('runtime restart is disabled while settings or policy drafts are dirty', async t => {
  const hooks = { setTimeout: immediateTimeout, clearTimeout: immediateClearTimeout };
  const load = modules(async path => path === '/api/admin/runtime'
    ? response(runtimeFixture())
    : response({ mode: 'offline' }), hooks);
  const session = load('@/app/session');
  const settings = load('@/features/settings/store');
  const policy = load('@/features/policy/store');
  const view = load('@/features/runtime/RuntimeView.vue');
  t.after(() => hooks.cleanup?.());
  session.sessionState.adminAuthenticated = true;
  await view.loadRuntime();
  assert.equal(view.canRestart.value, true);

  settings.settingsState.snapshot = { config: { timezone: 'Asia/Shanghai' } };
  settings.settingsState.draft = { timezone: 'UTC' };
  assert.equal(view.canRestart.value, false);
  settings.settingsState.snapshot = null;
  settings.settingsState.draft = null;
  policy.policyState.snapshot = { grants: [] };
  policy.policyState.draft = [{ subject: 'user', scope: { bot_id: 'bot', group_id: 'group' }, actions: [] }];
  assert.equal(view.canRestart.value, false);
});

test('runtime restart waits for a ready generation change and sends one POST', async t => {
  const hooks = { setTimeout: immediateTimeout, clearTimeout: immediateClearTimeout };
  const calls = [];
  let healthReads = 0;
  const load = modules(async (path, options) => {
    calls.push({ path, method: options?.method });
    if (path === '/api/admin/runtime') return response(runtimeFixture('old'));
    if (path === '/api/admin/runtime/restart') return response({ accepted: true, generation: 'old' }, 202);
    if (path === '/health') {
      healthReads += 1;
      return response({ ready: true, generation: healthReads === 1 ? 'old' : 'new' });
    }
    if (path === '/api/admin/config') return response({ error: 'unauthorized' }, 401);
    if (path === '/api/status') return response({ error: 'unauthorized' }, 401);
    return response({});
  }, hooks);
  const session = load('@/app/session');
  const view = load('@/features/runtime/RuntimeView.vue');
  t.after(() => hooks.cleanup?.());
  session.sessionState.adminAuthenticated = true;
  await view.loadRuntime();
  await view.restartCore();
  await nextHostTurn();
  assert.equal(calls.filter(call => call.path === '/api/admin/runtime/restart').length, 1);
  assert.equal(healthReads, 2);
  assert.equal(session.sessionState.adminAuthenticated, false);
});

test('runtime refresh retains the last snapshot while stale and unlocks after recovery', async t => {
  const hooks = { setTimeout: immediateTimeout, clearTimeout: immediateClearTimeout };
  let reads = 0;
  const load = modules(async path => {
    if (path === '/api/admin/runtime') {
      reads += 1;
      if (reads === 2) return response({ error: 'upstream_unavailable' }, 502);
      return response(runtimeFixture(reads === 3 ? 'recovered' : 'stable'));
    }
    return response({ mode: 'offline' });
  }, hooks);
  const session = load('@/app/session');
  const view = load('@/features/runtime/RuntimeView.vue');
  t.after(() => hooks.cleanup?.());
  session.sessionState.adminAuthenticated = true;
  await view.loadRuntime();
  const retained = view.runtime.value;
  await view.loadRuntime();
  assert.strictEqual(view.runtime.value, retained);
  assert.equal(view.runtimeStale.value, true);
  assert.equal(view.canRestart.value, false);
  await view.loadRuntime();
  assert.equal(view.runtimeStale.value, false);
  assert.equal(view.runtime.value.generation, 'recovered');
  assert.equal(view.canRestart.value, true);
});

test('runtime refresh ignores a late response after the session generation changes', async t => {
  const hooks = {};
  const first = deferred();
  const second = deferred();
  const signals = [];
  let reads = 0;
  const load = modules((path, options) => {
    if (path === '/api/admin/runtime') {
      signals.push(options.signal);
      return ++reads === 1 ? first.promise : second.promise;
    }
    return Promise.resolve(response({ mode: 'offline' }));
  }, hooks);
  const session = load('@/app/session');
  const view = load('@/features/runtime/RuntimeView.vue');
  t.after(() => hooks.cleanup?.());
  session.sessionState.adminAuthenticated = true;
  const oldRequest = view.loadRuntime();
  await Promise.resolve();
  session.sessionState.generation += 1;
  await webRequire('vue').nextTick();
  assert.equal(signals.length, 2);
  assert.equal(signals[0].aborted, true);
  first.resolve(response(runtimeFixture('late')));
  await oldRequest;
  assert.equal(view.runtime.value, null);
});

test('runtime unmount aborts an in-flight refresh and ignores its late response', async t => {
  const hooks = {};
  const waiting = deferred();
  let signal;
  const load = modules((path, options) => {
    if (path === '/api/admin/runtime') {
      signal = options.signal;
      return waiting.promise;
    }
    return Promise.resolve(response({ mode: 'offline' }));
  }, hooks);
  const session = load('@/app/session');
  const view = load('@/features/runtime/RuntimeView.vue');
  session.sessionState.adminAuthenticated = true;
  const request = view.loadRuntime();
  await Promise.resolve();
  hooks.cleanup();
  assert.equal(signal.aborted, true);
  waiting.resolve(response(runtimeFixture('late')));
  await request;
  assert.equal(view.runtime.value, null);
  assert.equal(view.loading.value, false);
  t.after(() => hooks.cleanup?.());
});

test('runtime restart polls after a network error without resending the POST', async t => {
  const hooks = { setTimeout: immediateTimeout, clearTimeout: immediateClearTimeout };
  let postCalls = 0;
  let healthCalls = 0;
  const load = modules(async path => {
    if (path === '/api/admin/runtime') return response(runtimeFixture('old'));
    if (path === '/api/admin/runtime/restart') {
      postCalls += 1;
      throw new TypeError('synthetic network failure');
    }
    if (path === '/health') {
      healthCalls += 1;
      return response({ ready: true, generation: 'new' });
    }
    if (path === '/api/admin/config') return response({ error: 'unauthorized' }, 401);
    if (path === '/api/status') return response({ error: 'unauthorized' }, 401);
    return response({});
  }, hooks);
  const session = load('@/app/session');
  const view = load('@/features/runtime/RuntimeView.vue');
  t.after(() => hooks.cleanup?.());
  session.sessionState.adminAuthenticated = true;
  await view.loadRuntime();
  await view.restartCore();
  await nextHostTurn();
  assert.equal(postCalls, 1);
  assert.equal(healthCalls, 1);
  assert.equal(session.sessionState.adminAuthenticated, false);
});

test('style management ignores late results after logout and preserves no prior group data', async () => {
  const waiting = deferred();
  const load = modules(() => waiting.promise);
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const view = load('@/features/memory/StyleManagement.vue');
  view.group.value = 'group-a';
  const pending = view.load();
  await Promise.resolve();
  session.sessionState.adminAuthenticated = false;
  await webRequire('vue').nextTick();
  waiting.resolve(response({ revision: 1, items: [], profiles: [], feedback: [], next_cursor: null }));
  await pending;
  assert.equal(view.snapshot.value, null);
  assert.equal(view.loadedGroup.value, '');
  assert.equal(view.canWrite.value, false);
});

test('style management writes a revision-bound profile only for the loaded group', async () => {
  const calls = [];
  const load = modules(async (path, options) => {
    calls.push({ path, options });
    return response({ revision: 7, items: [], profiles: [], feedback: [], next_cursor: null });
  });
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const view = load('@/features/memory/StyleManagement.vue');
  view.group.value = 'group-a';
  await view.load();
  view.group.value = 'group-b';
  await view.profile('generate');
  assert.equal(calls.length, 1);
  view.group.value = 'group-a';
  await view.profile('generate');
  assert.equal(calls.length, 2);
  assert.equal(calls[1].path, '/api/admin/memory/style/profile');
  assert.equal(calls[1].options.method, 'POST');
  assert.deepEqual(JSON.parse(calls[1].options.body), {
    group_id: 'group-a', expected_revision: 7, action: 'generate', profile_id: null,
  });
});

test('automatic learning management ignores old-session results and binds rollback to loaded receipt', async () => {
  const calls = [];
  let deferredResolve;
  let deferReceipts = true;
  const load = modules(async (path, options) => {
    calls.push({ path, options });
    if (path.endsWith('/status')) return response({ enabled: false, allowed_groups: [], allowed_domains: ['fact'], last_report: null });
    if (path.includes('/receipts')) {
      if (deferReceipts) return new Promise(resolve => { deferredResolve = resolve; });
      return response({ items: [{ receipt_id: 'auto:receipt', object_revision: 7, state: 'applied' }], next_cursor: null });
    }
    if (path.endsWith('/rollback')) return response({ receipt_id: 'auto:receipt', state: 'rolled_back' });
    throw new Error(`unexpected request ${path}`);
  });
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const view = load('@/features/memory/AutoLearningManagement.vue');
  view.group.value = 'group-a';
  const pending = view.load();
  session.sessionState.adminAuthenticated = false;
  deferredResolve(response({ items: [{ receipt_id: 'old-session' }], next_cursor: null }));
  await pending;
  assert.equal(view.page.value, null);
  assert.equal(view.status.value, null);
  session.sessionState.adminAuthenticated = true;
  deferReceipts = false;
  await view.load();
  const receipt = view.page.value.items[0];
  view.group.value = 'group-b';
  await view.mutate(receipt);
  assert.equal(calls.filter(call => call.path.endsWith('/rollback')).length, 0);
  view.group.value = 'group-a';
  await view.mutate(receipt);
  const writes = calls.filter(call => call.path.endsWith('/rollback'));
  assert.equal(writes.length, 1);
  assert.deepEqual(JSON.parse(writes[0].options.body), {
    group_id: 'group-a', receipt_id: 'auto:receipt', expected_object_revision: 7, reason: 'admin_disabled',
  });
  assert.equal(writes[0].options.headers.get('X-Omubot-Request'), '1');
});

test('knowledge review is revision-bound and group or old-session changes discard loaded text', async () => {
  const calls = [];
  let current = knowledgeSource({});
  let groupTwoResolve;
  let rejectReview = true;
  const load = modules(async (path, options = {}) => {
    calls.push({ path, options });
    if (path.startsWith('/api/admin/knowledge/sources?')) {
      if (path.includes('group_id=group-two')) return new Promise(resolve => { groupTwoResolve = resolve; });
      return response({ items: [current], next_cursor: null });
    }
    if (path.endsWith('/document?group_id=group-one&expected_revision=8')) {
      return response({ source: current, document: knowledgeDocument() });
    }
    if (path.endsWith('/document?group_id=group-one&expected_revision=9')) {
      return response({ source: current, document: knowledgeDocument() });
    }
    if (path.includes('/sources/source-1?group_id=group-one')) return response(current);
    if (path === '/api/admin/knowledge/review') {
      if (rejectReview) return response({ error: 'knowledge_revision_conflict' }, 409);
      current = knowledgeSource({ revision: 9, review_status: 'approved', reviewed_content_revision: 3 });
      return response({});
    }
    throw new Error(`unexpected request ${path}`);
  });
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const view = load('@/features/knowledge/KnowledgeView.vue');
  view.group.value = 'group-one';
  await webRequire('vue').nextTick();
  await view.load();
  await view.selectSource(view.page.value.items[0]);
  assert.equal(view.canWrite.value, true);
  assert.equal(view.currentDocument.value.document.content, '# Current text');
  await view.review('approved');
  assert.equal(calls.filter(call => call.path === '/api/admin/knowledge/review').length, 0);
  view.confirmNonPersonal.value = true;
  view.draft.content = '# Preserved edit';
  await view.review('approved');
  assert.equal(view.needsReload.value, true);
  assert.equal(view.canWrite.value, false);
  assert.equal(view.canImport.value, false);
  assert.equal(view.draft.content, '# Preserved edit');
  await view.review('approved');
  assert.equal(calls.filter(call => call.path === '/api/admin/knowledge/review').length, 1);
  rejectReview = false;
  await view.load();
  await view.selectSource(view.page.value.items[0]);
  view.confirmNonPersonal.value = true;
  await view.review('approved');
  const write = calls.find(call => call.path === '/api/admin/knowledge/review');
  assert.equal(write.options.method, 'POST');
  assert.equal(write.options.headers.get('X-Omubot-Request'), '1');
  assert.deepEqual(JSON.parse(write.options.body), {
    group_id: 'group-one', source_id: 'source-1', expected_revision: 8,
    decision: 'approved', non_personal_document: true,
  });
  assert.equal(view.source.value.review_status, 'approved');
  assert.equal(view.source.value.revision, 9);

  view.group.value = 'group-two';
  await webRequire('vue').nextTick();
  assert.equal(view.page.value, null);
  assert.equal(view.source.value, null);
  assert.equal(view.currentDocument.value, null);
  assert.equal(view.draft.content, '');
  view.group.value = 'group-two';
  const pending = view.load();
  session.sessionState.adminAuthenticated = false;
  await webRequire('vue').nextTick();
  groupTwoResolve(response({ items: [knowledgeSource({ source_id: 'stale' })], next_cursor: null }));
  await pending;
  assert.equal(view.page.value, null);
  assert.equal(view.loadedGroup.value, '');
  assert.equal(view.source.value, null);
  assert.equal(view.draft.content, '');
});

function knowledgeSource(overrides) {
  return {
    apply_actor: null, apply_policy_revision: null, content_hash: 'hash', content_revision: 3,
    created_at: 1, index_version: 'index-1', review_actor: null, review_policy_revision: null,
    review_status: 'pending', reviewed_content_revision: null, revision: 8,
    scope: { bot_id: 'bot-1', group_id: 'group-one' }, source_id: 'source-1',
    source_label: 'Manual source', status: 'inactive', title: 'Team notes', updated_at: 1,
    upload_policy_revision: 1, uploader_id: 'admin-1', ...overrides,
  };
}

function knowledgeDocument() {
  return { classification: 'non_personal_document', format: 'markdown', source_id: 'source-1',
    source_label: 'Manual source', title: 'Team notes', content: '# Current text' };
}

test('identity and episode management keeps scoped drafts, dual CAS and explicit recovery across late replies', async () => {
  const calls = [];
  const replies = [];
  const hooks = {};
  const load = modules(async (path, options = {}) => {
    calls.push({ path, options });
    assert.equal(options.credentials, 'same-origin');
    assert.ok(replies.length, `unexpected request ${path}`);
    return replies.shift();
  }, hooks);
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const view = load('@/features/memory/MemoryIdentityEpisodeManagement.vue');
  const episode = (candidateRevision, objectRevision, state, decay = '2026-12-01T00:00:00Z') => ({
    candidate_id: 'episode-a', candidate_revision: candidateRevision,
    object_id: 'object-a', object_revision: objectRevision, state, decay_at: decay,
    last_used_at: 1700000000, prompt_eligible: state === 'enabled_for_prompt',
  });
  const alias = {
    at: 1700000000, reader_id: 'web-admin', scope: { bot_id: 'bot-a', group_id: 'group-a' },
    status: 'resolved', surface: '小木', candidates: [{
      applied_event_id: 'apply-a', evidence_refs: ['event-a'], fact_digest: 'digest-a',
      fact_id: 'fact-a', fact_revision: 2, kind: 'self_nickname_alias',
      scope: { bot_id: 'bot-a', group_id: 'group-a' }, source_ids: ['source-a'],
      source_revisions: [['source-a', 2]], status: 'active', subject_id: 'person-a',
      surface: '小木', window_from: 1600000000, window_to: null,
    }],
  };
  view.groupInput.value = 'group-a';
  view.surfaceInput.value = '小木';
  view.aliasAtInput.value = '1700000000';
  replies.push(response(alias));
  await view.resolveAlias();
  assert.equal(view.alias.value.status, 'resolved');
  assert.equal(view.alias.value.candidates[0].subject_id, 'person-a');
  const aliasQuery = new URL(calls[0].path, 'http://localhost');
  assert.equal(aliasQuery.pathname, '/api/admin/memory/self-alias');
  assert.equal(aliasQuery.searchParams.get('group_id'), 'group-a');
  assert.equal(aliasQuery.searchParams.get('surface'), '小木');
  assert.equal(aliasQuery.searchParams.get('at'), '1700000000');
  assert.equal(view.subjectInput.value, '', 'alias resolution does not silently select a familiarity subject');
  const familiarity = {
    scope: { bot_id: 'bot-a', group_id: 'group-a' }, subject_id: 'person-a',
    revision: 3, score: 0, tier: '陌生人', valid_contributions: 0, enabled_for_chat: false,
  };
  view.subjectInput.value = 'person-a';
  replies.push(response(familiarity));
  await view.loadFamiliarity();
  const familiarityQuery = new URL(calls.at(-1).path, 'http://localhost');
  assert.equal(familiarityQuery.pathname, '/api/admin/memory/familiarity');
  assert.equal(familiarityQuery.searchParams.get('group_id'), 'group-a');
  assert.equal(familiarityQuery.searchParams.get('subject_id'), 'person-a');
  assert.equal(calls.at(-1).options.method ?? 'GET', 'GET');
  assert.equal(calls.at(-1).options.body, undefined);
  assert.equal(view.familiarity.value.score, 0);
  assert.equal(view.familiarity.value.valid_contributions, 0);
  assert.equal(view.alias.value.status, 'resolved', 'the independent metadata read preserves alias results');

  view.candidateInput.value = 'episode-a';
  replies.push(response(episode(7, 12, 'enabled_for_prompt')));
  await view.loadEpisode();
  assert.equal(view.canWriteEpisode.value, false, 'a reason is required before every write');
  view.reasonDraft.value = '旧群草稿';
  view.subjectInput.value = 'person-b';
  assert.equal(view.familiarity.value, null);
  assert.equal(view.episode.value.candidate_revision, 7);
  assert.equal(view.reasonDraft.value, '旧群草稿', 'subject changes preserve the episode draft');
  const oldAlias = deferred();
  replies.push(oldAlias.promise);
  const pendingAlias = view.resolveAlias();
  const oldAliasCall = calls.at(-1);
  const oldFamiliarity = deferred();
  replies.push(oldFamiliarity.promise);
  const pendingFamiliarity = view.loadFamiliarity();
  const oldFamiliarityCall = calls.at(-1);
  view.groupInput.value = 'group-b';
  assert.equal(oldAliasCall.options.signal.aborted, true);
  assert.equal(oldFamiliarityCall.options.signal.aborted, true);
  assert.equal(view.alias.value, null);
  assert.equal(view.episode.value, null);
  assert.equal(view.loadedGroup.value, '');
  assert.equal(view.reasonDraft.value, '');
  oldAlias.resolve(response(alias));
  oldFamiliarity.resolve(response(familiarity));
  await pendingAlias;
  await pendingFamiliarity;
  assert.equal(view.alias.value, null, 'a prior group reply cannot repopulate the view');
  assert.equal(view.familiarity.value, null, 'a prior group metadata reply cannot repopulate the view');

  replies.push(response(episode(8, 13, 'disabled')));
  await view.loadEpisode();
  view.decayDraft.value = '';
  view.reasonDraft.value = '清除期限，不启用';
  replies.push(response({ error: 'revision_conflict' }, 409));
  await view.mutateEpisode('decay');
  const conflictCall = calls.at(-1);
  assert.equal(conflictCall.path, '/api/admin/memory/episode/decay');
  assert.equal(conflictCall.options.headers.get('X-Omubot-Request'), '1');
  assert.deepEqual(JSON.parse(conflictCall.options.body), {
    group_id: 'group-b', candidate_id: 'episode-a', expected_candidate_revision: 8,
    expected_object_revision: 13, reason: '清除期限，不启用', decay_at: '',
  });
  assert.equal(view.needsReload.value, true);
  assert.equal(view.decayDraft.value, '');
  assert.equal(view.reasonDraft.value, '清除期限，不启用');
  assert.match(view.episodeError.value, /不会自动重试/);
  const writesAtConflict = calls.length;
  await view.mutateEpisode('approve_reopen');
  assert.equal(calls.length, writesAtConflict, 'conflict requires an explicit read before another write');
  replies.push(response(episode(9, 14, 'disabled')));
  await view.loadEpisode();
  assert.equal(view.needsReload.value, false);
  assert.equal(view.decayDraft.value, '', 'manual reload preserves the dirty deadline');
  assert.equal(view.reasonDraft.value, '清除期限，不启用');
  replies.push(response(episode(10, 15, 'disabled', '')));
  await view.mutateEpisode('decay');
  assert.equal(JSON.parse(calls.at(-1).options.body).expected_candidate_revision, 9);
  assert.equal(JSON.parse(calls.at(-1).options.body).expected_object_revision, 14);
  assert.equal(view.episode.value.state, 'disabled', 'clearing the deadline does not reopen or enable');
  assert.equal(view.canTransition('enable'), false);

  view.reasonDraft.value = '人工确认恢复批准';
  const recovery = deferred();
  replies.push(recovery.promise);
  const pendingRecovery = view.mutateEpisode('approve_reopen');
  const recoveryCall = calls.at(-1);
  view.reasonDraft.value = '恢复请求期间另写的新理由';
  view.decayDraft.value = '2027-01-01T00:00:00Z';
  recovery.resolve(response(episode(11, 16, 'approved', '')));
  await pendingRecovery;
  assert.equal(JSON.parse(recoveryCall.options.body).action, 'approve_reopen');
  assert.equal(JSON.parse(recoveryCall.options.body).expected_object_revision, 15);
  assert.equal(view.episode.value.state, 'approved');
  assert.equal(view.reasonDraft.value, '恢复请求期间另写的新理由');
  assert.equal(view.decayDraft.value, '2027-01-01T00:00:00Z');
  assert.match(view.episodeNotice.value, /还需单独启用/);
  assert.deepEqual(calls.filter(call => call.path === '/api/admin/memory/episode/state')
    .map(call => JSON.parse(call.options.body).action), ['approve_reopen'], 'approval produces no automatic enable request');
  replies.push(response(episode(12, 17, 'enabled_for_prompt', '')));
  await view.mutateEpisode('enable');
  const enableCall = calls.at(-1);
  assert.equal(enableCall.path, '/api/admin/memory/episode/state');
  assert.deepEqual(JSON.parse(enableCall.options.body), {
    group_id: 'group-b', candidate_id: 'episode-a', expected_candidate_revision: 11,
    expected_object_revision: 16, reason: '恢复请求期间另写的新理由', action: 'enable',
  });
  assert.equal(view.episode.value.state, 'enabled_for_prompt');

  assert.equal(view.decayDraft.value, '2027-01-01T00:00:00Z', 'a state-only action must preserve an unsubmitted deadline draft');

  const loggedOutRead = deferred();
  replies.push(loggedOutRead.promise);
  const pendingRead = view.loadEpisode();
  const oldReadCall = calls.at(-1);
  const loggedOutFamiliarity = deferred();
  replies.push(loggedOutFamiliarity.promise);
  const pendingLoggedOutFamiliarity = view.loadFamiliarity();
  const oldLoggedOutFamiliarityCall = calls.at(-1);
  replies.push(response({ error: 'unauthorized' }, 401));
  await view.resolveAlias();
  assert.equal(session.sessionState.adminAuthenticated, false);
  assert.equal(oldReadCall.options.signal.aborted, true);
  assert.equal(oldLoggedOutFamiliarityCall.options.signal.aborted, true);
  assert.equal(view.groupInput.value, '');
  assert.equal(view.surfaceInput.value, '');
  assert.equal(view.candidateInput.value, '');
  assert.equal(view.subjectInput.value, '');
  assert.equal(view.episode.value, null);
  assert.equal(view.familiarity.value, null);
  session.sessionState.adminAuthenticated = true;
  session.sessionState.generation += 1;
  loggedOutRead.resolve(response(episode(13, 18, 'enabled_for_prompt')));
  loggedOutFamiliarity.resolve(response(familiarity));
  await pendingRead;
  await pendingLoggedOutFamiliarity;
  assert.equal(view.episode.value, null, 'an old session reply cannot repopulate a new session');
  assert.equal(view.familiarity.value, null, 'an old session metadata reply cannot repopulate a new session');

  view.groupInput.value = 'group-c';
  view.candidateInput.value = 'episode-a';
  const unmountedRead = deferred();
  replies.push(unmountedRead.promise);
  const pendingUnmount = view.loadEpisode();
  const unmountedCall = calls.at(-1);
  hooks.cleanup();
  assert.equal(unmountedCall.options.signal.aborted, true);
  unmountedRead.resolve(response(episode(14, 19, 'enabled_for_prompt')));
  await pendingUnmount;
  assert.equal(view.episode.value, null);
  assert.equal(replies.length, 0);
});

test('observation controls preserve CAS drafts and discard old group/session state', async () => {
  const calls = [];
  let currentJob = observationJob(5);
  let currentSuggestion = slangSuggestion(12);
  let observationsForGroupB;
  const load = modules(async (path, options = {}) => {
    calls.push({ path, options });
    if (path.startsWith('/api/admin/memory/observations?')) {
      if (path.includes('group_id=group-b')) return new Promise(resolve => { observationsForGroupB = resolve; });
      return response({ items: [observationPool()], next_cursor: null });
    }
    if (path.startsWith('/api/admin/memory/observation-jobs?')) {
      return response({ items: [currentJob], next_cursor: null });
    }
    if (path === '/api/admin/memory/slang-review/status') {
      return response({ enabled: false, allowed_groups: [], last_report: null });
    }
    if (path.startsWith('/api/admin/memory/slang-governance?')) {
      return response({ items: currentSuggestion ? [currentSuggestion] : [] });
    }
    if (path === '/api/admin/memory/observation-review') {
      const body = JSON.parse(options.body);
      if (body.expected_revision === 5) {
        currentJob = observationJob(6, 'pending');
        return response({ error: 'conflict' }, 409);
      }
      currentJob = observationJob(7, 'reviewed');
      return response(currentJob);
    }
    if (path === '/api/admin/memory/slang-governance/revoke') {
      currentSuggestion = null;
      return response({ revision: 13 });
    }
    throw new Error(`unexpected request ${path}`);
  });
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const view = load('@/features/memory/ObservationManagement.vue');
  view.groupInput.value = 'group-a';
  await webRequire('vue').nextTick();
  await view.load();
  assert.equal(view.loadedForCurrentGroup.value, true);
  assert.equal(view.canReviewJob(view.jobPage.value.items[0]), false);
  view.setReviewDraft('job-a', 'rejected');
  const originalJob = view.jobPage.value.items[0];
  assert.equal(view.canReviewJob(originalJob), true);
  await view.submitReview(originalJob);
  const firstReview = calls.find(call => call.path === '/api/admin/memory/observation-review');
  assert.deepEqual(JSON.parse(firstReview.options.body), {
    group_id: 'group-a', job_id: 'job-a', expected_revision: 5, verdict: 'rejected',
  });
  assert.equal(firstReview.options.headers.get('X-Omubot-Request'), '1');
  assert.equal(view.jobPage.value.items[0].revision, 6);
  assert.equal(view.reviewDrafts['job-a'], 'rejected');
  assert.match(view.actionError.value, /所选人工结果仍保留/);
  await view.submitReview(view.jobPage.value.items[0]);
  const reviewWrites = calls.filter(call => call.path === '/api/admin/memory/observation-review');
  assert.equal(JSON.parse(reviewWrites[1].options.body).expected_revision, 6);
  assert.equal(view.jobPage.value.items[0].revision, 7);
  assert.equal(view.reviewDrafts['job-a'], undefined);
  assert.match(view.notice.value, /不批准候选，也不会应用数据/);

  const suggestion = view.governancePage.value.items[0];
  assert.equal(view.canRevokeSuggestion(suggestion), true);
  await view.revokeSuggestion(suggestion);
  const revoke = calls.find(call => call.path === '/api/admin/memory/slang-governance/revoke');
  assert.deepEqual(JSON.parse(revoke.options.body), {
    group_id: 'group-a', suggestion_id: 44, expected_revision: 12,
  });
  assert.equal(revoke.options.headers.get('X-Omubot-Request'), '1');
  assert.deepEqual([...view.governancePage.value.items], []);
  assert.match(view.notice.value, /已撤销建议并重新读取来源/);

  view.groupInput.value = 'group-b';
  await webRequire('vue').nextTick();
  assert.equal(view.observationPage.value, null);
  assert.equal(view.jobPage.value, null);
  assert.equal(view.governancePage.value, null);
  assert.equal(view.loadedGroup.value, '');
  const pending = view.load();
  session.sessionState.adminAuthenticated = false;
  await webRequire('vue').nextTick();
  observationsForGroupB(response({ items: [observationPool()], next_cursor: null }));
  await pending;
  assert.equal(view.groupInput.value, '');
  assert.equal(view.observationPage.value, null);
  assert.equal(view.jobPage.value, null);
  assert.equal(view.governancePage.value, null);
  assert.equal(view.loadedGroup.value, '');
});

function observationPool() {
  return {
    backlog_checkpoint: 2, candidate_ids: ['candidate-a'], count: 3, domain: 'slang',
    output_policy: 'observe_only', pool_key: 'pool-a', risk_tags: ['manual'], semantic_checkpoint: 4,
    sources: [{ source_id: 'source-a', source_revision: 2, subject_id: 'subject-a' }],
  };
}

function observationJob(revision, status = 'pending') {
  return {
    candidate: { candidate_id: 'candidate-a', candidate_revision: 4 }, chain: 'semantic', count: 3,
    job_id: 'job-a', object_id: 'object-a', object_revision: 6, pool_key: 'pool-a', revision,
    sources: [{ source_id: 'source-a', source_revision: 2, subject_id: 'subject-a' }], status, threshold: 3,
  };
}

function slangSuggestion(revision) {
  return { code: 'muting_suggestion', details: { term: 'typed term', proposed_meaning: 'typed meaning',
    candidate: 'candidate-a', chain: 'semantic', raw_text: 'must not be surfaced' }, revision, suggestion_id: 44 };
}

test('private grants require the running peer and retain same-ID group authorization', async () => {
  const groupGrant = {
    subject: 'peer', scope: { bot_id: 'bot', group_id: 'peer' },
    actions: ['message.read', 'model.invoke', 'message.reply'], effect: 'allow',
    provider: 'offline', model: 'reply-model', allow_history: true, allow_images: false,
    expires_at: 4102444800,
  };
  const snapshot = {
    revision: 1, bot_id: 'bot', mode: 'offline', grants: [groupGrant],
    private_conversation_enabled: false, private_conversation_peers: ['peer'],
    thinker_enabled: true,
    model_config: { task_bindings: {
      reply: { profile: 'reply', api_format: 'openai_chat', model: 'reply-model', policy_provider: 'offline' },
      thinker: { profile: 'thinker', api_format: 'openai_chat', model: 'think-model', policy_provider: 'think-provider' },
    } },
  };
  const load = modules(async () => response(snapshot));
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const policy = load('@/features/policy/store');
  await policy.loadPolicy();
  policy.policyState.privatePeerId = 'peer';
  assert.equal(policy.policyState.allowPrivateHistory, false);
  assert.equal(policy.canAddPrivateConversationGrants(), false);
  assert.equal(policy.addPrivateConversationGrants(), false);
  policy.policyState.snapshot.private_conversation_enabled = true;
  policy.policyState.privatePeerId = 'elsewhere';
  assert.equal(policy.addPrivateConversationGrants(), false);
  policy.policyState.privatePeerId = 'peer';
  assert.equal(policy.canAddPrivateConversationGrants(), true);
  assert.equal(policy.addPrivateConversationGrants(), true);
  assert.equal(policy.policyState.draft.length, 4);
  assert.equal(policy.policyState.draft[0].scope.group_id, 'peer');
  const privateGrants = policy.policyState.draft.filter(grant => grant.scope.kind === 'private');
  assert.equal(privateGrants.length, 3);
  for (const grant of privateGrants) {
    assert.equal(grant.subject, 'peer');
    assert.equal(grant.scope.private_user_id, 'peer');
    assert.equal('group_id' in grant.scope, false);
    assert.equal(grant.allow_images, false);
    assert.equal(grant.allow_history, false);
    assert.equal(grant.actions.some(action => action.startsWith('tool.') || action.startsWith('memory.')), false);
  }
  assert.deepEqual(Array.from(privateGrants[0].actions), ['message.read', 'message.reply']);
  assert.equal(privateGrants[0].provider, '');
  assert.equal(privateGrants[0].model, '');
  assert.equal(privateGrants[1].model, 'reply-model');
  assert.equal(privateGrants[2].model, 'think-model');
  policy.policyState.privatePeerId = 'peer';
  policy.policyState.allowPrivateHistory = true;
  assert.equal(policy.addPrivateConversationGrants(), true);
  assert.equal(policy.policyState.draft.length, 4);
  assert.equal(policy.policyState.draft[0].allow_history, true);
  assert.equal(policy.policyState.draft[1].allow_history, false);
  assert.equal(policy.policyState.draft[2].allow_history, true);
  policy.policyState.privatePeerId = 'peer';
  delete policy.policyState.snapshot.model_config.task_bindings.thinker;
  assert.equal(policy.addPrivateConversationGrants(), false);
  policy.resetPolicyState();
  assert.equal(policy.policyState.privatePeerId, '');
  assert.equal(policy.policyState.allowPrivateHistory, false);
});

test('explicit shared object permission preserves ordinary drafts and blocks late admin data', async () => {
  const calls = [];
  let revision = 40;
  let saved = [];
  let lateResolve;
  let late = false;
  const ref = {material_type: 'knowledge', source_id: 'manual', source_revision: 3, content_revision: 1, index_version: 'index-v1'};
  const snapshot = () => ({revision, bot_id: 'bot', grants: saved, visibility_grants: [], cross_group_sharing_enabled: false});
  const load = modules(async (path, init = {}) => {
    if (path === '/api/admin/policy' && init.method !== 'PUT') return response(snapshot());
    if (path.includes('/visibility/options')) {
      if (late) return new Promise(resolve => { lateResolve = resolve; });
      return response({source_scope: {bot_id: 'bot', group_id: 'source'}, material_type: 'knowledge',
        items: [{ref, label: '手册', summary: '非个人冷却规则'}], partial: false});
    }
    const body = JSON.parse(init.body);
    calls.push({path, body});
    revision += 1;
    if (path === '/api/admin/policy') { saved = body.grants; return response({revision}); }
    const grant = {grant_id: body.grant_id, grant_revision: path.endsWith('/revoke') ? 2 : 1,
      source_scope: {bot_id: 'bot', kind: 'group', group_id: 'source'},
      target_scope: {bot_id: 'bot', kind: 'group', group_id: 'target'}, material_type: 'knowledge',
      object_refs: [ref], status: path.endsWith('/revoke') ? 'revoked' : 'active', expires_at: 9999999999};
    return response({revision, visibility_grant: grant});
  });
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const policy = load('@/features/policy/store');
  const vue = webRequire('vue');
  await policy.loadPolicy();
  policy.policyState.visibilitySourceGroupId = 'source';
  policy.policyState.visibilityTargetGroupId = 'target';
  policy.policyState.visibilityGrantId = 'manual-share';
  await vue.nextTick();
  assert.equal(policy.addVisibilityGovernanceDraft(), true);
  const dirty = JSON.stringify(policy.policyState.draft);
  assert.equal(await policy.grantVisibility(), false);
  assert.equal(calls.length, 0);
  assert.equal(JSON.stringify(policy.policyState.draft), dirty);
  assert.equal(await policy.savePolicy(), true);
  assert.equal(saved.length, 2);
  await policy.loadVisibilityOptions();
  policy.policyState.visibilitySelection = [policy.visibilityOptionKey(policy.policyState.visibilityOptions[0])];
  assert.equal(policy.canGrantVisibility(), false);
  policy.policyState.visibilityConfirmed = true;
  assert.equal(await policy.grantVisibility(), true);
  const write = calls.find(call => call.path.endsWith('/visibility/grant')).body;
  assert.equal(write.expected_policy_revision, 41);
  assert.equal(write.expected_grant_revision, null);
  assert.deepEqual(write.object_refs, [ref]);
  assert.equal(write.non_personal_projection_confirmed, true);
  assert.equal(policy.policyState.snapshot.cross_group_sharing_enabled, false);
  assert.equal(policy.hasPolicyDraftChanges(), false);
  const active = policy.policyState.snapshot.visibility_grants[0];
  assert.equal(await policy.revokeVisibility(active), true);
  assert.equal(policy.policyState.snapshot.visibility_grants[0].status, 'revoked');
  assert.equal(JSON.stringify(policy.policyState.draft), JSON.stringify(saved));
  late = true;
  const pending = policy.loadVisibilityOptions();
  await Promise.resolve();
  session.expireAdminSession();
  lateResolve(response({items: [{ref, label: '旧来源', summary: '已过期会话'}], partial: false}));
  await pending;
  assert.equal(policy.policyState.snapshot, null);
  assert.equal(policy.policyState.visibilityOptions.length, 0);
});


test('sticker administration stages only exact group management and running vision grants', async () => {
  const preserved = { subject: 'user', scope: { bot_id: 'bot', group_id: 'other' },
    actions: ['message.read', 'message.reply'], effect: 'allow', provider: '', model: '',
    allow_history: false, allow_images: false, expires_at: 4102444800 };
  const load = modules(async () => response({ revision: 1, grants: [preserved], bot_id: 'bot',
    sticker_description_available: false, model_config: { task_bindings: { vision: {
      profile: 'vision', api_format: 'openai_chat', model: 'vision-model', policy_provider: 'https://vision.test/v1' } } } }));
  load('@/app/session').sessionState.adminAuthenticated = true;
  const policy = load('@/features/policy/store');
  await policy.loadPolicy();
  policy.policyState.groupId = 'assets';
  assert.equal(policy.addStickerAdministrationGrant(), true);
  assert.deepEqual(Array.from(policy.policyState.draft[1].actions), ['sticker.manage']);
  assert.equal(policy.policyState.draft[1].subject, 'web-admin');
  assert.equal(policy.policyState.draft[1].scope.group_id, 'assets');
  assert.equal(policy.addStickerAdministrationGrant(true), false);
  assert.equal(policy.policyState.draft.length, 2);
  policy.policyState.snapshot.sticker_description_available = true;
  assert.equal(policy.addStickerAdministrationGrant(true), true);
  const vision = policy.policyState.draft[2];
  assert.equal(vision.model, 'vision-model');
  assert.equal(vision.provider, 'https://vision.test/v1');
  assert.deepEqual(Array.from(vision.actions), ['message.read', 'media.read', 'model.invoke']);
  assert.equal(vision.allow_images, true);
  assert.equal(vision.allow_history, false);
  assert.equal(policy.policyState.draft[0].scope.group_id, 'other');
  assert.deepEqual(Array.from(policy.policyState.draft[0].actions), ['message.read', 'message.reply']);
});

test('sticker review requires persisted draft and discards late group or session responses', async () => {
  const entry = { sticker_id: 'cat', content_hash: 'a'.repeat(64), mime_type: 'image/jpeg', byte_size: 24,
    entry_revision: 1, status: 'pending', description: '旧描述', usage_hint: '', ocr_text: '', intent_tags: [], affect_tags: [] };
  let catalog = { group_id: 'g', revision: 1, entries: [entry], description_available: true };
  let approvals = 0;
  let waiting = null;
  const load = modules(async (url, options = {}) => {
    if (url.endsWith('/describe')) return response({ operation_id: 'draft', sticker_id: 'cat', catalog_revision: 1,
      entry_revision: 1, draft_only: true, metadata: { description: '视觉草稿', usage_hint: '', ocr_text: '晚安',
        intent_tags: ['closing'], affect_tags: [] } });
    if (url.endsWith('/metadata')) {
      catalog = { ...catalog, revision: 2, entries: [{ ...entry, ...JSON.parse(options.body).metadata, entry_revision: 2 }] };
      return response({});
    }
    if (url.endsWith('/approve')) { approvals += 1; return response({}); }
    return waiting ? waiting.promise : response(catalog);
  });
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const view = load('@/features/stickers/StickersView.vue');
  view.group.value = 'g';
  await load('vue').nextTick();
  await view.load();
  view.select(view.snapshot.value.entries[0]);
  await view.describe();
  assert.equal(view.metadataDirty.value, true);
  await view.mutate('approve');
  assert.equal(approvals, 0);
  await view.saveMetadata();
  assert.equal(view.metadataDirty.value, false);
  assert.equal(view.selected.value.description, '视觉草稿');
  await view.mutate('approve');
  assert.equal(approvals, 1);
  waiting = deferred();
  const lateGroup = view.load();
  view.group.value = 'other';
  await load('vue').nextTick();
  waiting.resolve(response(catalog));
  await lateGroup;
  assert.equal(view.snapshot.value, null);
  assert.equal(view.selected.value, null);
  waiting = deferred();
  const lateSession = view.load();
  session.expireAdminSession();
  await load('vue').nextTick();
  waiting.resolve(response({ ...catalog, group_id: 'other' }));
  await lateSession;
  assert.equal(view.snapshot.value, null);
  assert.equal(view.description.value, '');
});


test('memory card management binds both revisions and erases dirty group/logout late bodies', async () => {
  const fact = { fact_id: 'fact-card', subject_id: 'author', predicate: 'likes.game', value: '私有卡片正文',
    source_ids: ['source-card'], fact_revision: 4, status: 'active', observed_at: 100,
    valid_from: null, valid_to: null, applied_at: 110 };
  let metadata = { fact, category: null, classification_revision: 2 };
  const lateRead = deferred();
  let holdRead = false;
  let conflict = false;
  const writes = [];
  const load = modules(async (path, options = {}) => {
    if (options.method === 'POST') {
      writes.push({ body: JSON.parse(options.body), headers: new Headers(options.headers) });
      if (conflict) return response({ error: 'revision_conflict' }, 409);
      metadata = { fact, category: 'preference', classification_revision: 3 };
      return response({ revision: 3 });
    }
    if (String(path).startsWith('/api/admin/memory/cards?')) {
      return response({ items: [metadata], total_active: 1, matched_active: 1 });
    }
    if (holdRead) return lateRead.promise;
    return response(metadata);
  });
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const view = load('@/features/memory/MemoryView.vue');
  view.cardGroupInput.value = 'group-a';
  await view.loadCards();
  await view.loadCards(fact.fact_id);
  assert.equal(view.cardMetadata.value.classification_revision, 2);
  view.cardCategoryDraft.value = 'preference';
  await view.mutateCard(false);
  assert.deepEqual(writes[0].body, { group_id: 'group-a', fact_id: fact.fact_id,
    expected_fact_revision: 4, expected_classification_revision: 2, category: 'preference' });
  assert.equal(writes[0].headers.get('X-Omubot-Request'), '1');
  assert.equal(view.cardMetadata.value.classification_revision, 3);
  conflict = true;
  await view.mutateCard(false);
  assert.equal(view.cardMetadata.value, null);
  assert.equal(view.canWriteCard.value, false);
  assert.match(view.cardError.value, /重新读取/);
  await view.loadCards(fact.fact_id);
  view.cardQueryInput.value = '改动查询';
  assert.equal(view.cardMetadata.value, null);
  assert.equal(view.cardPage.value, null);
  await view.loadCards(fact.fact_id);
  view.cardGroupInput.value = 'group-b';
  assert.equal(view.cardMetadata.value, null);
  assert.equal(view.cardPage.value, null);
  holdRead = true;
  const pending = view.loadCards(fact.fact_id);
  session.sessionState.adminAuthenticated = false;
  assert.equal(view.cardMetadata.value, null);
  lateRead.resolve(response(metadata));
  await pending;
  assert.equal(view.cardMetadata.value, null);
  assert.equal(view.cardPage.value, null);
  assert.equal(view.canWriteCard.value, false);
});


test('group board clears on scope/logout, ignores late reads, and expires its source snapshot', async () => {
  const late = deferred();
  const timers = [];
  const snapshot = { bot_id: '10001', group_id: 'g', timeline: [], expires_in_s: 10 };
  let calls = 0;
  let elapsed = 0;
  const load = modules(path => {
    if (path.startsWith('/api/admin/diagnostics/group-state')) {
      calls += 1;
      if (calls === 4) elapsed += 11000;
      return calls === 2 ? late.promise : Promise.resolve(response(snapshot));
    }
    return Promise.resolve(response({ mode: 'offline' }));
  }, {
    performance: { now() { return elapsed; } },
    setTimeout(callback) { timers.push(callback); return timers.length; },
    clearTimeout() {},
  });
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const view = load('@/features/diagnostics/DiagnosticsView.vue');
  const { nextTick } = load('vue');
  view.boardGroup.value = 'g';
  await nextTick();
  await view.loadGroupBoard();
  assert.equal(view.groupBoard.value.group_id, 'g');
  assert.equal(timers.length, 1);
  timers[0]();
  assert.equal(view.groupBoard.value, null);
  assert.match(view.boardError.value, /保留期/);
  const pending = view.loadGroupBoard();
  view.boardGroup.value = 'other';
  await nextTick();
  assert.equal(view.groupBoard.value, null);
  late.resolve(response(snapshot));
  await pending;
  assert.equal(view.groupBoard.value, null);
  await view.loadGroupBoard();
  assert.ok(view.groupBoard.value);
  await view.loadGroupBoard();
  assert.equal(view.groupBoard.value, null);
  assert.match(view.boardError.value, /保留期/);
  assert.equal(timers.length, 2);
  session.sessionState.adminAuthenticated = false;
  await nextTick();
  assert.equal(view.groupBoard.value, null);
});

function journalDraft(overrides = {}) {
  return { draft_id: 'draft-one', root_id: 'root-one', revision: 1, source_event_id: 'story-one',
    source_hash: 'a'.repeat(64), body_hash: 'b'.repeat(64), content_hash: 'c'.repeat(64),
    state: 'pending_review', created_at: 1700000000, group_id: 'group-one',
    body: '虚构故事里，今天在湖边散步。', supersedes_draft_id: null, is_tip: true,
    review: null, content_kind: 'fiction', ...overrides };
}
function journalStatus(overrides = {}) {
  return { enabled: true, fiction_available: true, archive_available: true, storage_error: '',
    allow_live_publish: false, live_available: false, wire_validated: false,
    external_transport: false, validation_profile: null, validated_account_digest: null,
    recovered_deliveries: 0, ...overrides };
}
function journalDelivery(overrides = {}) {
  return { delivery_id: 'delivery-one', draft_id: 'draft-one', mode: 'dry_run', state: 'published',
    payload_hash: 'd'.repeat(64), receipt: '', code: '', wire_validated: false,
    external_transport: false, ...overrides };
}
function journalReview(draft, scope = 'dry_run') {
  return { actor: 'web-admin', decision: 'approve', body_hash: draft.body_hash,
    source_hash: draft.source_hash, content_hash: draft.content_hash, approval_scope: scope, created_at: 1700000100 };
}

// Exercise the real script setup and same-origin client, with only the transport replaced.
test('Journal factual preview uses opaque references with Worldbook unavailable and keeps dry_run honest', async t => {
  const calls = [];
  const consent = { source_id: 'opaque-consent-one', template_id: 'attendance_solo_v1',
    labels: ['一位朋友'], label_index: 0, expires_at: 1900000000, current: true, code: '' };
  let draft = journalDraft({ source_event_id: 'jp_opaque-source', content_kind: 'factual', body: '一位朋友到场了' });
  const load = modules(async (path, options = {}) => {
    assert.equal(options.credentials, 'same-origin');
    const payload = options.body ? JSON.parse(options.body) : null;
    calls.push({ path, options, payload });
    if (options.method === 'POST') {
      assert.equal(options.headers.get('X-Omubot-Request'), '1');
      if (path.endsWith('/preview-factual')) return response(draft);
      if (path.endsWith('/approve')) {
        draft = { ...draft, state: 'approved', review: journalReview(draft) };
        return response(draft);
      }
      if (path.endsWith('/dry-run')) return response({ ...draft, mode: 'dry_run', scope_digest: 'scope-digest' });
      if (path.endsWith('/publish')) return response(journalDelivery());
      assert.fail(`unexpected mutation ${path}`);
    }
    if (path.includes('/status?')) return response(journalStatus({ fiction_available: false, allow_live_publish: true }));
    if (path.includes('/consents?')) return response({ items: [consent], has_more: false });
    if (path.includes('/deliveries?')) return response({ items: [], has_more: false });
    return response({ items: [], has_more: false });
  }, {});
  const session = load('@/app/session');
  session.sessionState.adminAuthenticated = true;
  const view = load('@/features/journal/JournalView.vue');
  view.group.value = 'group-one';
  await view.load();
  assert.equal(view.canPreviewFiction.value, false);
  assert.equal(view.modeOptions.value[1].disabled, true, 'a config toggle is not a validated live transport');
  view.chooseConsent(consent.source_id, true);
  assert.equal(view.canPreviewFactual.value, true, 'factual Journal remains usable without Story/Worldbook');
  await view.previewFactual();
  const preview = calls.find(call => call.path.endsWith('/preview-factual')).payload;
  assert.deepEqual(Object.keys(preview).sort(), ['consent_source_ids', 'group_id', 'operation_id']);
  assert.deepEqual(preview.consent_source_ids, [consent.source_id]);
  assert.equal(view.body.value, '一位朋友到场了');
  assert.equal(view.editable.value, false, 'the fact template cannot be rewritten');
  view.approvalScope.value = 'live';
  await view.act('approve');
  assert.equal(calls.filter(call => call.path.endsWith('/approve')).length, 0);
  view.approvalScope.value = 'dry_run';
  await view.act('approve');
  const approval = calls.find(call => call.path.endsWith('/approve')).payload;
  assert.equal(approval.approval_scope, 'dry_run');
  assert.equal(approval.expected_body_hash, draft.body_hash);
  assert.equal(approval.expected_source_hash, draft.source_hash);
  assert.match(view.notice.value, /尚未执行发布/);
  await view.act('dry-run');
  assert.equal(view.dryRun.value.mode, 'dry_run');
  await view.publish();
  const publish = calls.find(call => call.path.endsWith('/publish')).payload;
  assert.equal(publish.mode, 'dry_run');
  assert.equal(publish.expected_content_hash, draft.content_hash);
  assert.equal(publish.expected_body_hash, draft.body_hash);
  assert.equal(publish.expected_source_hash, draft.source_hash);
  assert.match(view.notice.value, /本地演练（无外发）/);
  assert.equal(view.canPublish.value, false);
  await view.publish();
  assert.equal(calls.filter(call => call.path.endsWith('/publish')).length, 1);
  const source = readFileSync(resolve(root, 'web/src/features/journal/JournalView.vue'), 'utf8');
  assert.match(source, /未真实发布、无外发/);
  assert.doesNotMatch(source, /consent\.author|consent\.text|consent\.raw/);
  t.after(() => { view.group.value = ''; });
});

test('Journal Story preview revises with CAS, explicitly approves live and resolves unknown without resending', async t => {
  const calls = [];
  let draft = journalDraft();
  const unknown = journalDelivery({ mode: 'live', state: 'unknown', code: 'transport_uncertain',
    wire_validated: true, external_transport: true });
  const hooks = {};
  const load = modules(async (path, options = {}) => {
    const payload = options.body ? JSON.parse(options.body) : null;
    calls.push({ path, options, payload });
    if (options.method === 'POST') {
      assert.equal(options.headers.get('X-Omubot-Request'), '1');
      if (path.endsWith('/preview-fiction')) return response({ draft, decisions: [{ source_event_id: 'story-one', reason: 'selected', score: 1 }] });
      if (path.endsWith('/revise')) {
        draft = journalDraft({ draft_id: 'draft-two', revision: 2, body: payload.body,
          supersedes_draft_id: 'draft-one', body_hash: 'e'.repeat(64), content_hash: 'f'.repeat(64) });
        return response(draft);
      }
      if (path.endsWith('/approve')) {
        draft = { ...draft, state: 'approved', review: journalReview(draft, payload.approval_scope) };
        return response(draft);
      }
      if (path.endsWith('/publish')) return response({ ...unknown, draft_id: draft.draft_id });
      if (path.endsWith('/resolve')) return response({ ...unknown, draft_id: draft.draft_id,
        state: payload.outcome, receipt: payload.receipt, code: 'manual_resolution' });
      assert.fail(`unexpected mutation ${path}`);
    }
    if (path.includes('/status?')) return response(journalStatus({ allow_live_publish: true, live_available: true,
      wire_validated: true, external_transport: true, validation_profile: 'controlled', validated_account_digest: 'opaque-account' }));
    return response({ items: [], has_more: false });
  }, hooks);
  t.after(() => hooks.cleanup?.());
  const session = load('@/app/session'); session.sessionState.adminAuthenticated = true;
  const view = load('@/features/journal/JournalView.vue');
  view.group.value = 'group-one'; await view.load(); await view.previewFiction();
  assert.equal(view.decisions.value[0].source_event_id, 'story-one');
  assert.deepEqual(Object.keys(calls.find(call => call.path.endsWith('/preview-fiction')).payload).sort(), ['group_id', 'operation_id']);
  view.body.value = '虚构故事里，今天在桥边散步。';
  assert.equal(view.canApprove.value, false, 'editing invalidates any review of the stored body');
  await view.act('revise');
  const revision = calls.find(call => call.path.endsWith('/revise')).payload;
  assert.equal(revision.expected_body_hash, 'b'.repeat(64));
  assert.equal(revision.expected_source_hash, 'a'.repeat(64));
  assert.equal(view.selected.value.review, null);
  assert.equal(view.canPublish.value, false);
  view.approvalScope.value = 'live'; await view.act('approve');
  assert.equal(view.publishMode.value, 'live');
  assert.equal(view.selected.value.review.body_hash, 'e'.repeat(64));
  await view.publish();
  const publish = calls.find(call => call.path.endsWith('/publish')).payload;
  assert.equal(publish.mode, 'live'); assert.equal(publish.expected_content_hash, 'f'.repeat(64));
  assert.equal(view.deliveries.value.items[0].state, 'unknown');
  assert.equal(view.canPublish.value, false);
  await view.publish();
  assert.equal(calls.filter(call => call.path.endsWith('/publish')).length, 1, 'unknown is never automatically retried');
  view.chooseResolution(view.deliveries.value.items[0]);
  assert.equal(view.resolutionOutcome.value, null, 'the user must choose a verified outcome');
  view.resolutionOutcome.value = 'published';
  assert.equal(view.canResolve.value, false, 'successful manual resolution requires an actual receipt');
  view.resolutionReceipt.value = 'confirmed-qzone-receipt';
  await view.resolve();
  assert.deepEqual(calls.find(call => call.path.endsWith('/resolve')).payload,
    { group_id: 'group-one', delivery_id: 'delivery-one', outcome: 'published', receipt: 'confirmed-qzone-receipt' });
  assert.equal(view.deliveries.value.items[0].state, 'published');
  assert.match(view.notice.value, /不会发送正文/);
  assert.equal(calls.filter(call => call.path.endsWith('/publish')).length, 1);
});

test('Journal keeps conflict drafts but invalidates proofs and clears group/session data before late replies', async t => {
  const calls = [];
  const hooks = {};
  let draftError = null;
  let revisionError = null;
  let pendingDraft = null;
  const draft = journalDraft();
  const consent = { source_id: 'opaque', template_id: 'attendance_solo_v1', labels: ['一位朋友'], label_index: 0,
    expires_at: 1900000000, current: true, code: '' };
  const load = modules(async (path, options = {}) => {
    calls.push({ path, options });
    if (path.endsWith('/revise')) return response({ error: revisionError }, 409);
    if (path.includes('/status?')) return response(journalStatus());
    if (path.includes('/consents?')) return response({ items: [consent], has_more: false });
    if (path.includes('/deliveries?')) return response({ items: [journalDelivery({ state: 'unknown', mode: 'live' })], has_more: false });
    if (path.includes('/journal/draft-one?')) {
      if (pendingDraft) return pendingDraft.promise;
      if (draftError) return response({ error: draftError.code }, draftError.status);
      return response(draft);
    }
    return response({ items: [draft], has_more: false });
  }, hooks);
  t.after(() => hooks.cleanup?.());
  const session = load('@/app/session'); session.sessionState.adminAuthenticated = true;
  const view = load('@/features/journal/JournalView.vue');
  view.group.value = 'group-one'; await view.load(); await view.select('draft-one');
  view.body.value = '保留我的编辑正文'; revisionError = 'journal_revision_conflict';
  await view.act('revise');
  assert.equal(view.body.value, '保留我的编辑正文'); assert.equal(view.selected.value.draft_id, 'draft-one');
  assert.equal(view.needsReload.value, true); assert.equal(view.draftCurrent.value, false);
  assert.equal(view.canRevise.value, false); assert.match(view.error.value, /正文已保留/);
  await view.load();
  assert.equal(view.body.value, '保留我的编辑正文', 'refreshing metadata does not discard the conflict draft');
  assert.equal(view.canApprove.value, false, 'refreshing status does not renew old draft proof');
  draftError = { code: 'journal_source_expired', status: 409 };
  await view.select('draft-one');
  assert.equal(view.body.value, '保留我的编辑正文'); assert.match(view.error.value, /来源已失效/);
  assert.equal(view.canPublish.value, false);
  draftError = null; await view.load(); await view.select('draft-one');
  pendingDraft = deferred();
  const reading = view.select('draft-one'); const signal = calls.at(-1).options.signal;
  view.group.value = 'group-two';
  assert.equal(signal.aborted, true); assert.equal(view.heads.value, null); assert.equal(view.consents.value, null);
  assert.equal(view.deliveries.value, null); assert.equal(view.body.value, '');
  pendingDraft.resolve(response(draft)); await reading;
  assert.equal(view.selected.value, null, 'a prior group response cannot repopulate the view');
  pendingDraft = null; view.group.value = 'group-one'; await view.load(); await view.select('draft-one');
  draftError = { code: 'denied', status: 403 }; await view.select('draft-one');
  assert.equal(view.body.value, ''); assert.equal(view.status.value, null); assert.equal(view.deliveries.value, null);
  assert.equal(session.sessionState.adminAuthenticated, true, 'scope denial does not claim the whole login expired');
  draftError = null; await view.load();
  pendingDraft = deferred(); const loggedOut = view.select('draft-one');
  const logoutSignal = calls.at(-1).options.signal;
  session.sessionState.adminAuthenticated = false;
  assert.equal(logoutSignal.aborted, true);
  session.sessionState.adminAuthenticated = true;
  pendingDraft.resolve(response(draft)); await loggedOut;
  assert.equal(view.selected.value, null); assert.equal(view.consents.value, null);
  assert.equal(view.resolutionId.value, ''); assert.equal(view.resolutionReceipt.value, '');
});

test('Journal settings keep legacy live closed, validate exact lists and save independently of Worldbook activation', async () => {
  const snapshot = settingsFixture();
  for (const config of [snapshot.config, snapshot.effective_config]) {
    delete config.journal_enabled; delete config.journal_allowed_groups;
    delete config.journal_allow_live_publish; delete config.journal_allowed_live_uins;
  }
  const writes = [];
  const load = modules(async (path, options = {}) => {
    if (path === '/api/admin/config' && options.method === 'PUT') {
      const payload = JSON.parse(options.body); writes.push(payload);
      return response({ ...snapshot, revision: 2, config: payload.config, changed_fields: ['journal_enabled'], restart_required: true });
    }
    return response(snapshot);
  });
  const session = load('@/app/session'); session.sessionState.adminAuthenticated = true;
  const settings = load('@/features/settings/store'); await settings.loadSettings();
  assert.equal(settings.settingsState.draft.journal_allow_live_publish, false);
  assert.deepEqual([...settings.settingsState.draft.journal_allowed_live_uins], []);
  const view = load('@/features/settings/SettingsView.vue');
  view.changeWorldbookGate('journal_enabled', true);
  view.changeJournalGroups('group-one');
  view.changeJournalLiveAccounts('30001，30002\n30003');
  settings.updateConfigField('journal_allow_live_publish', true);
  settings.updateConfigField('task_models', { journal: 'default' });
  const valid = JSON.parse(settings.settingsState.advancedText);
  const invalid = { ...valid, journal_allowed_live_uins: ['30001', '30001'] };
  settings.markAdvancedText(JSON.stringify(invalid));
  assert.equal(settings.applyAdvancedText(), false, 'duplicate live identities cannot become a draft');
  assert.deepEqual([...settings.settingsState.draft.journal_allowed_live_uins], ['30001', '30002', '30003']);
  settings.markAdvancedText(JSON.stringify({ ...valid, journal_allowed_live_uins: Array.from({ length: 9 }, (_, i) => String(30001 + i)) }));
  assert.equal(settings.applyAdvancedText(), false, 'the exact account allowlist is bounded at eight');
  settings.markAdvancedText(JSON.stringify(valid)); assert.equal(settings.applyAdvancedText(), true);
  assert.equal(await settings.saveSettings(), true);
  assert.equal(writes[0].config.worldbook_enabled, false);
  assert.equal(writes[0].config.journal_enabled, true);
  assert.deepEqual(writes[0].config.task_models, { journal: 'default' });
  assert.equal(settings.settingsState.snapshot.effective_config.journal_enabled, false);
  assert.equal(settings.settingsState.snapshot.effective_config.journal_allow_live_publish, false);
  assert.equal(settings.settingsState.snapshot.restart_required, true);
});

test('F19 familiarity adjustment sends current CAS and keeps conflict input until explicit reload or scope invalidation', async t => {
  const replies = [];
  const calls = [];
  const hooks = {};
  const load = modules(async (path, options = {}) => {
    calls.push({ path, options }); assert.equal(options.credentials, 'same-origin');
    assert.ok(replies.length, `unexpected request ${path}`); return replies.shift();
  }, hooks);
  t.after(() => hooks.cleanup?.());
  const session = load('@/app/session'); session.sessionState.adminAuthenticated = true;
  const view = load('@/features/memory/MemoryIdentityEpisodeManagement.vue');
  const familiarity = { scope: { bot_id: 'bot-a', group_id: 'group-a' }, subject_id: 'person-a', revision: 3,
    score: 10, tier: '陌生人', valid_contributions: 1, enabled_for_chat: true, admin_adjustment: null };
  view.groupInput.value = 'group-a'; view.subjectInput.value = 'person-a';
  replies.push(response(familiarity)); await view.loadFamiliarity();
  view.familiarityScore.value = '45';
  replies.push(response({ ...familiarity, revision: 4, score: 45, admin_adjustment: 45 }));
  await view.adjustFamiliarity();
  const write = calls.at(-1);
  assert.equal(write.path, '/api/admin/memory/familiarity/adjust');
  assert.equal(write.options.headers.get('X-Omubot-Request'), '1');
  const payload = JSON.parse(write.options.body);
  assert.equal(payload.expected_revision, 3); assert.equal(payload.score, 45);
  assert.equal(payload.group_id, 'group-a'); assert.equal(payload.subject_id, 'person-a');
  assert.ok(payload.operation_id); assert.equal(view.familiarity.value.admin_adjustment, 45);
  view.familiarityScore.value = '60';
  replies.push(response({ error: 'revision_conflict' }, 409)); await view.adjustFamiliarity();
  assert.equal(view.familiarityScore.value, '60'); assert.equal(view.familiarity.value.revision, 4);
  assert.equal(view.familiarityNeedsReload.value, true); assert.equal(view.canAdjustFamiliarity.value, false);
  replies.push(response({ ...familiarity, revision: 5, score: 46, admin_adjustment: 45 })); await view.loadFamiliarity();
  assert.equal(view.familiarityNeedsReload.value, false); assert.equal(view.familiarityScore.value, '46');
  view.familiarityScore.value = '55'; const late = deferred(); replies.push(late.promise);
  const pending = view.adjustFamiliarity(); const signal = calls.at(-1).options.signal;
  view.subjectInput.value = 'person-b';
  assert.equal(signal.aborted, true); assert.equal(view.familiarity.value, null); assert.equal(view.familiarityScore.value, '');
  late.resolve(response({ ...familiarity, revision: 6, score: 55, admin_adjustment: 55 })); await pending;
  assert.equal(view.familiarity.value, null, 'a prior subject adjustment cannot populate the next subject');
});

function journalPagingRows() {
  const heads = Array.from({ length: 65 }, (_, index) => journalDraft({
    draft_id: `draft-${index + 1}`, root_id: `root-${index + 1}`, created_at: 1900000000 - index,
  }));
  const consents = Array.from({ length: 65 }, (_, index) => ({
    source_id: `opaque-consent-${index + 1}`, template_id: 'attendance_solo_v1', labels: ['一位朋友'],
    label_index: 0, current: true, code: '', expires_at: 1900001000,
  }));
  const deliveries = Array.from({ length: 65 }, (_, index) => journalDelivery({
    delivery_id: `delivery-${index + 1}`, draft_id: `draft-${index + 1}`,
    mode: 'live', state: index === 64 ? 'unknown' : 'published', wire_validated: true, external_transport: true,
  }));
  return { heads, consents, deliveries };
}

test('Journal pagination reaches row 65 independently, preserves drafts and CAS, then refreshes only on request', async t => {
  const rows = journalPagingRows();
  const calls = [];
  const hooks = {};
  const load = modules(async (path, options = {}) => {
    const url = new URL(path, 'http://localhost');
    const payload = options.body ? JSON.parse(options.body) : null;
    calls.push({ path, options, payload });
    if (options.method === 'POST') {
      assert.equal(options.headers.get('X-Omubot-Request'), '1');
      if (url.pathname.endsWith('/approve')) {
        const draft = rows.heads[64]; return response({ ...draft, state: 'approved', review: journalReview(draft) });
      }
      if (url.pathname.endsWith('/resolve')) return response({ ...rows.deliveries[64], state: payload.outcome, receipt: payload.receipt });
      if (url.pathname.endsWith('/preview-factual')) return response(journalDraft({
        draft_id: 'factual-65', root_id: 'factual-root', source_event_id: 'jp_65', content_kind: 'factual', body: '一位朋友到场了',
      }));
      assert.fail(`unexpected mutation ${path}`);
    }
    if (url.pathname.endsWith('/status')) return response(journalStatus({ live_available: true }));
    if (/\/journal\/draft-\d+$/.test(url.pathname)) {
      return response(rows.heads.find(item => url.pathname.endsWith(`/${item.draft_id}`)));
    }
    const kind = url.pathname.endsWith('/consents') ? 'consents' : url.pathname.endsWith('/deliveries') ? 'deliveries' : 'heads';
    assert.equal(url.searchParams.get('limit'), '64');
    const cursor = url.searchParams.get('cursor');
    if (cursor) {
      assert.equal(cursor, `${kind}-cursor`, 'each list sends only its own opaque cursor');
      // A repeat at the boundary must update/dedupe in place without changing page order.
      return response({ items: rows[kind].slice(63), has_more: false, next_cursor: null });
    }
    return response({ items: rows[kind].slice(0, 64), has_more: true, next_cursor: `${kind}-cursor` });
  }, hooks);
  t.after(() => hooks.cleanup?.());
  const session = load('@/app/session'); session.sessionState.adminAuthenticated = true;
  const view = load('@/features/journal/JournalView.vue');
  view.group.value = 'group-one'; await view.load(); await view.select('draft-1');
  view.body.value = '保留未保存的编辑正文';
  const draftProof = JSON.stringify(view.selected.value);
  const consentsBefore = view.consents.value, deliveriesBefore = view.deliveries.value, statusBefore = view.status.value;
  const initialCalls = calls.length;
  await view.loadMoreHeads();
  assert.equal(calls.length, initialCalls + 1, 'paging heads does not reload status or the other lists');
  assert.deepEqual([...view.heads.value.items].map(item => item.draft_id), rows.heads.map(item => item.draft_id));
  assert.equal(view.heads.value.next_cursor, null);
  assert.equal(view.consents.value, consentsBefore); assert.equal(view.deliveries.value, deliveriesBefore);
  await view.loadMoreConsents(); await view.loadMoreDeliveries();
  assert.equal(calls.length, initialCalls + 3);
  assert.deepEqual([...view.consents.value.items].map(item => item.source_id), rows.consents.map(item => item.source_id));
  assert.deepEqual([...view.deliveries.value.items].map(item => item.delivery_id), rows.deliveries.map(item => item.delivery_id));
  assert.equal(view.body.value, '保留未保存的编辑正文');
  assert.equal(JSON.stringify(view.selected.value), draftProof); assert.equal(view.draftCurrent.value, true);
  assert.equal(view.status.value, statusBefore); assert.equal(view.canRevise.value, true);
  await view.loadMoreHeads(); await view.loadMoreConsents(); await view.loadMoreDeliveries();
  assert.equal(calls.length, initialCalls + 3, 'no next cursor means no extra or automatic fetch');
  await view.select('draft-65'); await view.act('approve');
  assert.deepEqual([...view.heads.value.items].map(item => item.draft_id), rows.heads.map(item => item.draft_id),
    'reading or reviewing an older page item preserves the list order');
  const approval = calls.find(call => call.path.endsWith('/approve')).payload;
  assert.equal(approval.draft_id, 'draft-65'); assert.equal(approval.expected_body_hash, rows.heads[64].body_hash);
  assert.equal(approval.expected_source_hash, rows.heads[64].source_hash);
  view.chooseResolution(view.deliveries.value.items.find(item => item.delivery_id === 'delivery-65'));
  view.resolutionOutcome.value = 'published'; view.resolutionReceipt.value = 'manual-65'; await view.resolve();
  assert.equal(calls.find(call => call.path.endsWith('/resolve')).payload.delivery_id, 'delivery-65');
  view.chooseConsent('opaque-consent-65', true); await view.previewFactual();
  assert.deepEqual(calls.find(call => call.path.endsWith('/preview-factual')).payload.consent_source_ids, ['opaque-consent-65']);
  assert.equal(calls.filter(call => call.path.endsWith('/publish')).length, 0, 'pagination and manual resolution never publish');
  const savedBody = view.body.value;
  const beforeRefresh = calls.length; await view.load();
  assert.equal(calls.length, beforeRefresh + 4);
  assert.equal(view.heads.value.items.length, 64); assert.equal(view.consents.value.items.length, 64);
  assert.equal(view.deliveries.value.items.length, 64); assert.equal(view.heads.value.next_cursor, 'heads-cursor');
  assert.equal(view.consents.value.next_cursor, 'consents-cursor'); assert.equal(view.deliveries.value.next_cursor, 'deliveries-cursor');
  assert.equal(view.body.value, savedBody, 'returning lists to page one does not overwrite the editor');
  assert.equal(view.draftCurrent.value, false, 'metadata refresh still cannot renew the old draft proof');
  for (const call of calls.slice(-4)) assert.equal(new URL(call.path, 'http://localhost').searchParams.has('cursor'), false);
});

test('Journal pagination clears denied data and aborts late pages across group and administrator changes', async t => {
  const rows = journalPagingRows();
  const calls = [];
  const hooks = {};
  let waiting = null;
  let denied = false;
  const load = modules(async (path, options = {}) => {
    calls.push({ path, options });
    const url = new URL(path, 'http://localhost');
    if (url.searchParams.has('cursor')) {
      if (denied) return response({ error: 'denied' }, 403);
      assert.ok(waiting); return waiting.promise;
    }
    if (url.pathname.endsWith('/status')) return response(journalStatus());
    if (url.pathname.endsWith('/draft-1')) return response(rows.heads[0]);
    const kind = url.pathname.endsWith('/consents') ? 'consents' : url.pathname.endsWith('/deliveries') ? 'deliveries' : 'heads';
    return response({ items: rows[kind].slice(0, 64), has_more: true, next_cursor: `${kind}-cursor` });
  }, hooks);
  t.after(() => hooks.cleanup?.());
  const session = load('@/app/session'); session.sessionState.adminAuthenticated = true;
  const view = load('@/features/journal/JournalView.vue');
  view.group.value = 'group-one'; await view.load(); await view.select('draft-1');
  waiting = deferred(); const previousGroup = view.loadMoreHeads(); const groupSignal = calls.at(-1).options.signal;
  view.group.value = 'group-two';
  assert.equal(groupSignal.aborted, true); assert.equal(view.heads.value, null); assert.equal(view.body.value, '');
  waiting.resolve(response({ items: [rows.heads[64]], has_more: false, next_cursor: null })); await previousGroup;
  assert.equal(view.heads.value, null); assert.equal(view.loadedGroup.value, '');
  await view.load(); await view.select('draft-1');
  denied = true; await view.loadMoreDeliveries();
  assert.equal(view.heads.value, null); assert.equal(view.consents.value, null); assert.equal(view.deliveries.value, null);
  assert.equal(view.selected.value, null); assert.equal(view.body.value, '');
  assert.match(view.error.value, /授权不可用/); assert.equal(session.sessionState.adminAuthenticated, true);
  denied = false; await view.load(); waiting = deferred();
  const previousSession = view.loadMoreConsents(); const logoutSignal = calls.at(-1).options.signal;
  session.sessionState.adminAuthenticated = false;
  assert.equal(logoutSignal.aborted, true);
  session.sessionState.adminAuthenticated = true;
  waiting.resolve(response({ items: [rows.consents[64]], has_more: false, next_cursor: null })); await previousSession;
  assert.equal(view.consents.value, null); assert.equal(view.heads.value, null); assert.equal(view.deliveries.value, null);
  assert.equal(view.resolutionId.value, ''); assert.equal(view.busy.value, false);
  assert.equal(calls.filter(call => call.options.method === 'POST').length, 0);
});

test('reviewed matters preserve conditions and unknown time through separate native CAS transitions', async () => {
  const record = {
    matter_id: 'matter-1', scope: { bot_id: 'bot', kind: 'group', group_id: 'g' }, subject_id: 'self',
    source_id: 'source-self', source_revision: 2, summary: '准备演出', condition: '只有场地确认后才排练',
    state: 'candidate', revision: 1, observed_at: null, due_at: 1900000000, expires_at: 2000000000,
    replaces_matter_id: '', reason: '', actor: 'admin', created_at: 1, updated_at: 2, purpose: 'inbound_reply',
  };
  const calls = [];
  let conflict = true;
  const load = modules(async (path, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ path, body });
    if ((options.method ?? 'GET') === 'GET') {
      return String(path).includes('/matters/matter-1?')
        ? response({ ...record, state: 'approved', revision: 2 })
        : response({ items: [], next_cursor: null });
    }
    assert.equal(options.headers.get('X-Omubot-Request'), '1');
    if (String(path).endsWith('/propose')) return response(record);
    if (String(path).endsWith('/review')) return response({ ...record, state: 'approved', revision: 2 });
    if (String(path).endsWith('/apply')) {
      if (conflict) return response({ error: 'revision_conflict' }, 409);
      return response({ ...record, state: 'active', revision: 3 });
    }
    throw new Error('Unexpected matter request');
  });
  load('@/app/session').sessionState.adminAuthenticated = true;
  const view = load('@/features/memory/MattersManagement.vue');
  view.groupInput.value = 'g';
  await view.load();
  Object.assign(view.draft, { subject: 'self', source: 'source-self', sourceRevision: '2',
    summary: '准备演出', condition: record.condition, observed: '', due: '1900000000', expiry: '2000000000' });
  await view.propose();
  const proposed = calls.find(call => String(call.path).endsWith('/propose')).body;
  assert.equal(proposed.observed_at, null);
  assert.equal(proposed.condition, record.condition);
  assert.equal(proposed.expected_source_revision, 2);
  assert.equal(view.canTransition('apply'), false);
  await view.transition('review', 'approved');
  assert.equal(calls.at(-1).body.expected_revision, 1);
  await view.transition('apply');
  assert.equal(calls.at(-1).body.expected_revision, 2);
  assert.equal(view.selected.value.state, 'approved');
  assert.equal(view.needsReload.value, true);
  assert.equal(view.canWrite.value, false);
  conflict = false;
  await view.read('matter-1');
  await view.transition('apply');
  assert.equal(view.selected.value.state, 'active');
  assert.equal(view.selected.value.condition, record.condition);
});

for (const status of [200, 401]) {
  test(`late matter request ${status} cannot restore the old admin session`, async () => {
    const pending = deferred();
    let count = 0;
    const load = modules(async () => ++count === 1 ? pending.promise
      : response({ items: [], next_cursor: null }));
    const session = load('@/app/session');
    session.sessionState.adminAuthenticated = true;
    const view = load('@/features/memory/MattersManagement.vue');
    view.groupInput.value = 'old';
    const old = view.load();
    session.invalidateAdminRequests();
    session.sessionState.adminAuthenticated = false;
    session.sessionState.generation += 1;
    session.sessionState.adminAuthenticated = true;
    session.sessionState.generation += 1;
    view.groupInput.value = 'new';
    await view.load();
    pending.resolve(status === 200 ? response({ items: [{ matter_id: 'old' }], next_cursor: null })
      : response({ error: 'unauthorized' }, 401));
    await old;
    assert.equal(session.sessionState.adminAuthenticated, true);
    assert.equal(view.groupInput.value, 'new');
    assert.equal(view.page.value.items.length, 0);
    assert.equal(view.error.value, '');
  });
}

// Merge into the existing actual-source modules loader; no browser/network.
// 1. Add a ContactConsentPanel.vue branch before the generic branches:
//    'load,change,snapshot,userId,groupId,error,notice,loading,saving'
// 2. Add VM globals (Vue compiler macros):
//    defineProps: () => hooks.props ?? {disabled:false},
//    defineEmits: () => hooks.emit ?? (() => {})

test('contact consent mutation carries the native admin write marker', async () => {
  let marker;
  const load = modules(async (_, options = {}) => {
    if (options.method === 'PUT') {
      marker = options.headers.get('x-omubot-request');
      return marker === '1'
        ? response({revision:2,consent:{kind:'user',subject_id:'user',enabled:true}})
        : response({error:'request_header_required'},403);
    }
    return response({revision:1,consents:[],runtime_settings:null});
  });
  load('@/app/session').sessionState.adminAuthenticated = true;
  const panel = load('@/features/policy/ContactConsentPanel.vue');
  await panel.load(); panel.userId.value = 'user'; await panel.change('user',true);
  assert.equal(marker,'1'); assert.equal(panel.snapshot.value.revision,2);
  assert.equal(panel.error.value,'');
});

test('current contact GET or PUT 401 expires the actual admin session and clears old consent data', async () => {
  for (const method of ['GET','PUT']) {
    let expired = false;
    const load = modules(async () => expired
      ? response({error:'unauthorized'},401)
      : response({revision:1,consents:[],runtime_settings:null}));
    const session = load('@/app/session'); session.sessionState.adminAuthenticated = true;
    const panel = load('@/features/policy/ContactConsentPanel.vue');
    await panel.load(); panel.userId.value = 'user'; expired = true;
    if (method === 'GET') await panel.load(); else await panel.change('user',true);
    await webRequire('vue').nextTick();
    assert.equal(session.sessionState.adminAuthenticated,false,method);
    assert.equal(panel.snapshot.value,null,method);
  }
});


test('contact settings keeps an unapplied endpoint when enabling its parent draft', async () => {
  const vue = webRequire('vue');
  const props = vue.reactive({ value: null, disabled: false, baseRevision: 7 });
  const load = modules(async () => response({}), {
    props,
    emit: (name, value) => { assert.equal(name, 'update'); props.value = value; },
  });
  const panel = load('@/features/settings/ContactSettingsPanel.vue');
  panel.identity.value = 'typed-user';
  panel.windows.value = [{ start: '09:00', end: '18:00' }];
  panel.sendInterval.value = 90;
  panel.sendLimit.value = 2;
  panel.decisionInterval.value = 60;
  panel.decisionLimit.value = 3;
  panel.enabled(true);
  await vue.nextTick();
  assert.equal(panel.identity.value, 'typed-user');
  assert.equal(panel.sendInterval.value, 90);
  panel.saveEndpoint();
  assert.equal(props.value.users['typed-user'].minimum_interval_seconds, 90);
  assert.equal(props.value.enabled, true);
  props.baseRevision = 8;
  await vue.nextTick();
  assert.equal(panel.identity.value, '');
});
