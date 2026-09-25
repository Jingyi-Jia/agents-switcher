"""Routing, reported analytics, and deliberate quit-first handoffs in the shipped UI."""

import pytest

from claude_swap.web.page import PAGE_HTML
from tests.test_web_page_actions import node, run_page


ANALYTICS = r"""
const today = new Date().toISOString().slice(0, 10);
const yesterday = dayShift(today, -1), before = dayShift(today, -2);
const analytics = (provider = 'codex') => ({provider, scope: provider === 'codex' ? 'account' : 'local', source: 'Synthetic activity', fetchedAt: '2026-09-25T12:00:00Z', error: null,
  accounts: [{number: provider === 'codex' ? '1' : null, label: 'Work', available: true, stale: false, error: null, dataThrough: yesterday,
    summary: {lifetimeTokens: 90000, peakDailyTokens: 10000, currentStreakDays: 2, longestStreakDays: 5, totalSessions: 8, totalMessages: 20, totalThreads: null},
    daily: [{date: before, tokens: 200, messages: 2, sessions: 1}, {date: yesterday, tokens: 0, messages: 0, sessions: null}],
    models: [{name: 'model-a', inputTokens: 100, outputTokens: 50, cacheReadTokens: 60, cacheWriteTokens: null}],
    dailyModels: [{date: before, tokensByModel: {'model-a': 200}}], insights: {fastModePercent: 0, topReasoningEffort: null, topInvocations: []}},
  ...(provider === 'codex' ? [{number: '2', label: 'Personal', available: false, stale: true, error: 'Fresh login needed', dataThrough: before, summary: {}, daily: [], models: [], dailyModels: []}] : [])]});
fetchHandler = async path => ({ok: true, json: async () => path.startsWith('/api/analytics') ? analytics(path.includes('provider=claude') ? 'claude' : 'codex') : path.startsWith('/api/state') ? apiState : response});
"""


def test_routes_keep_usage_off_state_ticks_and_load_on_entry_only(node):
    run_page(node, ANALYTICS + r"""
assert.equal(calls.filter(c => c.path.startsWith('/api/analytics')).length, 0);
await navigate('usage');
assert.equal($('view-accounts').hidden, true);
assert.equal($('view-usage').hidden, false);
assert.equal($('nav-usage').attributes['aria-current'], 'page');
assert.equal(calls.filter(c => c.path.startsWith('/api/analytics')).length, 1);
await load(); intervals[0].callback(); await settle();
assert.equal(calls.filter(c => c.path.startsWith('/api/analytics')).length, 1);
$('usage-range').value = '7'; $('usage-range').onchange();
$('usage-account').value = '1'; $('usage-account').onchange();
assert.equal(calls.filter(c => c.path.startsWith('/api/analytics')).length, 1);
await $('usage-refresh').click();
assert.equal(calls.at(-1).path, '/api/analytics?provider=codex&force=1');
assert.equal(calls.at(-1).options.headers['X-Auth-Token'], 'dashboard-test-secret');
assert(calls.every(c => !c.path.includes('token=')));
await navigate('settings');
assert.equal($('view-settings').hidden, false);
assert.equal($('view-usage').hidden, true);
await navigate('usage');
assert.equal(calls.filter(c => c.path.startsWith('/api/analytics')).length, 3);
""")


def test_native_hash_routes_initialize_and_change_views(node):
    run_page(node, r"""
assert.equal(activeView, 'settings');
assert.equal($('view-settings').hidden, false);
location.hash = '#usage'; window.listeners.hashchange[0](); await settle();
assert.equal(activeView, 'usage');
assert(calls.some(c => c.path === '/api/analytics?provider=codex&force=0'));
location.hash = '#not-a-route'; window.listeners.hashchange[0]();
assert.equal(activeView, 'accounts');
""", setup="location.hash = '#settings';")


def test_period_totals_gaps_zeros_partial_coverage_and_numerical_alternatives(node):
    run_page(node, ANALYTICS + r"""
await navigate('usage');
$('usage-range').value = '7'; $('usage-range').onchange();
assert.equal(nodes('usage-content').find(n => n.attributes['aria-label'] === 'Tokens: 200').textContent, '200');
assert.match($('usage-context').textContent, /1 of 2 accounts available.*Partial coverage.*Cached data is stale.*Gaps are not zero/);
const days = periodDays(usageAccounts());
assert.equal(days.length, 7);
assert.equal(days.at(-1).tokens, null);
assert.equal(days.at(-2).tokens, 0);
assert.equal(days.at(-3).tokens, 200);
assert.equal(days.at(-3).count, 1);
const heat = nodes('usage-content').filter(n => n.className.startsWith('heat ') || n.className === 'heat');
assert.equal(heat.length, 7);
assert.match(heat.at(-1).className, /missing/);
assert.doesNotMatch(heat.at(-2).className, /missing/);
assert.equal(heat.at(-2).dataset.level, '0');
assert.match(heat.at(-3).attributes['aria-label'], /200 tokens.*partial, 1 of 2 accounts/);
assert(nodes('usage-content').some(n => n.tagName === 'TABLE' && n.attributes['aria-label'] === 'Daily activity numerical data'));
assert.match($('usage-content').textContent, /Not reported/);
assert.match($('usage-content').textContent, /All reported history · not filtered by date/);
""")


def test_data_through_blocks_unreported_future_rows_and_all_range_does_not_fill_them(node):
    run_page(node, ANALYTICS + r"""
await navigate('usage');
analyticsBody.accounts[0].daily.push({date: today, tokens: 99999, messages: 9, sessions: 9});
analyticsAccount = '1'; analyticsRange = '7'; renderUsage();
assert.equal(periodDays(usageAccounts()).at(-1).tokens, null);
assert.equal(sumKnown(periodDays(usageAccounts()).map(d => d.tokens)), 200);
analyticsRange = 'all'; renderUsage();
assert.equal(periodDays(usageAccounts())[0].date, before);
assert.equal(periodDays(usageAccounts()).at(-1).tokens, null);
analyticsBody.accounts[0].daily = [{date: yesterday, tokens: null, sessions: null, messages: null}];
renderUsage();
assert.match($('usage-content').textContent, /No reported tokens in this period/);
assert.equal(nodes('usage-content').find(n => n.attributes['aria-label'] === 'Tokens: Not reported').textContent, '—');
""")


def test_provider_response_races_do_not_replace_current_scope_or_filters(node):
    run_page(node, ANALYTICS + r"""
let releaseCodex;
fetchHandler = async path => path.includes('provider=codex') ? new Promise(resolve => { releaseCodex = resolve; }) : {ok: true, json: async () => analytics('claude')};
const pending = navigate('usage');
$('usage-provider').value = 'claude'; $('usage-provider').onchange(); await settle();
assert.equal(analyticsBody.provider, 'claude');
assert.match($('usage-subtitle').textContent, /Claude Code activity · This device/);
assert.equal($('usage-account-field').hidden, true);
$('usage-range').value = '7'; $('usage-range').onchange();
releaseCodex({ok: true, json: async () => analytics('codex')}); await pending;
assert.equal(analyticsBody.provider, 'claude');
assert.equal(analyticsRange, '7');
assert.doesNotMatch($('usage-content').textContent, /Account comparison/);
""")


def test_navigation_invalidates_pending_analytics_and_late_refresh_uses_current_account(node):
    run_page(node, ANALYTICS + r"""
await navigate('usage');
let finish;
fetchHandler = async () => new Promise(resolve => { finish = resolve; });
const refresh = loadAnalytics(true);
$('usage-account').value = '2'; $('usage-account').onchange();
finish({ok: true, json: async () => analytics()}); await refresh;
assert.equal(analyticsAccount, '2');
assert.match($('usage-content').textContent, /Fresh login needed/);
const late = loadAnalytics(true);
await navigate('settings');
finish({ok: true, json: async () => analytics()}); await late;
assert.equal(activeView, 'settings');
assert.equal($('view-usage').hidden, true);
assert.equal(analyticsLoading, false);
""")


def test_models_preserve_components_nulls_scope_and_safe_text(node):
    run_page(node, ANALYTICS + r"""
await navigate('usage');
const attack = '<img src=x onerror=alert(1)>';
analyticsBody.accounts[0].models[0].name = attack;
analyticsBody.accounts[0].dailyModels[0].tokensByModel = {[attack]: 200};
$('usage-models').click();
assert.equal(nodes('usage-content').some(n => n.tagName === 'IMG'), false);
assert.match($('usage-content').textContent, /<img src=x/);
const table = nodes('usage-content').find(n => n.attributes['aria-label'] === 'Model token components');
assert.match(table.textContent, /InputOutputCache readCache write/);
assert.match(table.textContent, /1005060Not reported/);
assert.doesNotMatch(table.textContent, /210/);
assert.match($('usage-content').textContent, /not added into a second, potentially double-counted total/);
assert.match($('usage-content').textContent, /not filtered by date/);
""")


@pytest.mark.parametrize("failure", ["network", "http", "empty"])
def test_usage_errors_and_empty_states_restore_refresh_without_fake_data(node, failure):
    run_page(node, "const failure = '" + failure + "';" + r"""
fetchHandler = async () => {
  if (failure === 'network') throw new Error('private-network-message');
  return {ok: failure !== 'http', json: async () => ({provider: 'codex', accounts: [], error: failure === 'http' ? 'Activity unavailable' : null})};
};
await navigate('usage');
assert.equal($('usage-refresh').disabled, false);
assert.match($('usage-content').textContent, failure === 'empty' ? /A fresh page/ : /Activity couldn't be loaded/);
assert.doesNotMatch($('usage-content').textContent, /private-network-message/);
assert.equal(nodes('usage-content').filter(n => n.className === 'metric').length, 0);
""")


def test_preferences_load_independently_and_late_state_does_not_undo_theme_change(node):
    run_page(node, r"""
assert(calls.some(c => c.path === '/api/preferences' && !c.options.method));
let finishState;
fetchHandler = async (path, options) => {
  if (path.startsWith('/api/state')) return new Promise(resolve => { finishState = resolve; });
  return {ok: true, json: async () => ({ok: true, preferences: {theme: 'dark', profileNoticeVersion: 1}})};
};
const pending = load();
await loadPreferences();
assert.equal(document.documentElement.dataset.theme, 'dark');
$('theme').value = 'light'; await $('theme').onchange();
assert.deepEqual(posts().at(-1).payload, {theme: 'light'});
finishState({ok: true, json: async () => ({...apiState, preferences: {theme: 'dark', profileNoticeVersion: 0}})}); await pending;
assert.equal(document.documentElement.dataset.theme, 'light');
assert(calls.filter(c => c.path === '/api/preferences').every(c => c.options.headers['X-Auth-Token'] === 'dashboard-test-secret'));
""")


def test_remembered_profile_consent_still_requires_a_deliberate_open_and_poll_is_safe(node):
    run_page(node, r"""
apiState.preferences = {theme: 'system', profileNoticeVersion: 1};
apiState.claudeDesktop = {available: true, canCreate: true, supported: true, installed: true, running: true, profiles: [{id: 'a'.repeat(32), name: 'Work'}]};
await load();
assert.equal(button('claude-desktop-profiles', 'Open').disabled, true);
$('watch').checked = false;
apiState.claudeDesktop.running = null;
intervals.find(i => i.ms === 5000).callback(); await settle();
assert.equal(button('claude-desktop-profiles', 'Open').disabled, true);
apiState.claudeDesktop.running = false;
intervals.find(i => i.ms === 5000).callback(); await settle();
assert.equal(button('claude-desktop-profiles', 'Open').disabled, false);
assert.equal(posts().length, 0);
button('claude-desktop-profiles', 'Open').click();
assert.equal(nodes('dialog-fields').find(n => n.type === 'checkbox'), undefined);
assert.equal(posts().length, 0);
await submitDialog();
assert.deepEqual(posts().map(c => c.path), ['/api/claude-desktop/open']);
assert.deepEqual(posts()[0].payload, {profileId: 'a'.repeat(32), confirm: true});
""")


def test_failed_profile_acknowledgement_never_creates_or_launches(node):
    run_page(node, r"""
apiState.claudeDesktop = {available: true, canCreate: true, supported: true, installed: true, running: false, profiles: []};
await load();
button('claude-desktop-actions', 'New profile').click();
nodes('dialog-fields').find(n => n.type === 'text').value = 'Work';
nodes('dialog-fields').find(n => n.type === 'checkbox').checked = true;
fetchHandler = async () => ({ok: false, json: async () => ({ok: false, error: 'Cannot save preferences'})});
await submitDialog();
assert.deepEqual(posts().map(c => c.path), ['/api/preferences']);
assert.match($('dialog-feedback').textContent, /Nothing was created or opened/);
assert.equal($('action-dialog').open, true);
assert.equal($('dialog-submit').disabled, false);
""")


def test_unknown_and_manual_codex_status_never_switch_without_continue(node):
    run_page(node, r"""
codexStatus.running = null;
await button('codex', 'Switch').click();
assert.equal($('codex-dialog').open, true);
assert.match($('codex-dialog-title').textContent, /next@example.com/);
assert.match($('codex-dialog-status').textContent, /unknown/);
assert.equal($('codex-continue').disabled, true);
assert.equal($('codex-assist').hidden, true);
assert.equal(posts().length, 0);
codexStatus.running = false;
await $('codex-check').click();
assert.equal($('codex-continue').disabled, false);
assert.equal(posts().length, 0);
await $('codex-continue').click();
assert.deepEqual(posts().map(c => c.path), ['/api/switch']);
assert.deepEqual(posts()[0].payload, {provider: 'codex', number: '2'});
""")


ASSIST = r"""
codexStatus = {available: true, running: true, desktopRunning: true, terminalCount: 0, backgroundCount: 0, canAssist: true, canOpen: true};
"""


def test_codex_assist_is_never_available_with_terminal_or_background_blockers(node):
    run_page(node, ASSIST + r"""
for (const blocker of ['terminalCount', 'backgroundCount']) {
  codexStatus[blocker] = 1;
  await button('codex', 'Switch').click();
  assert.equal($('codex-assist').hidden, true);
  assert.equal($('codex-continue').disabled, true);
  assert.match($('codex-dialog-status').textContent, /We never stop these/);
  $('codex-cancel').click(); await settle();
  codexStatus[blocker] = 0;
}
assert.equal(posts().length, 0);
""")


def test_codex_assist_quits_then_switches_then_opens_only_after_success(node):
    run_page(node, ASSIST + r"""
await button('codex', 'Switch').click();
assert.equal($('codex-assist').hidden, false);
fetchHandler = async path => {
  if (path === '/api/codex/quit') codexStatus = {...codexStatus, running: false, desktopRunning: false};
  return {ok: true, json: async () => path === '/api/codex/status' ? codexStatus : path.startsWith('/api/state') ? apiState : response};
};
await $('codex-assist').click();
assert.deepEqual(posts().map(c => c.path), ['/api/codex/quit', '/api/switch', '/api/codex/open']);
assert.deepEqual(posts()[0].payload, {confirm: true});
assert.deepEqual(posts()[1].payload, {provider: 'codex', number: '2'});
assert.deepEqual(posts()[2].payload, {confirm: true});
assert.match($('toast').textContent, /Confirm the selected account in Codex/);
assert.equal($('codex-dialog').open, false);
""")


@pytest.mark.parametrize("cancel", ["button", "escape", "close", "navigation"])
def test_cancelling_during_inflight_quit_or_wait_discards_pending_switch(node, cancel):
    run_page(node, ASSIST + "const cancellation = '" + cancel + "';" + r"""
await button('codex', 'Switch').click();
let finish;
fetchHandler = async path => path === '/api/codex/quit' ? new Promise(resolve => { finish = resolve; }) : {ok: true, json: async () => ({...codexStatus, running: false, desktopRunning: false})};
const pending = $('codex-assist').click(); await settle();
assert.equal($('codex-cancel').disabled, false);
if (cancellation === 'button') $('codex-cancel').click();
if (cancellation === 'escape') $('codex-dialog').dispatch('cancel');
if (cancellation === 'close') $('codex-dialog').close();
if (cancellation === 'navigation') { location.hash = '#settings'; window.listeners.hashchange[0](); }
await settle();
finish({ok: true, json: async () => ({ok: true})}); await pending;
await runTimers(1000);
assert.deepEqual(posts().map(c => c.path), ['/api/codex/quit']);
assert.equal(codexFlow, null);
assert.equal($('codex-dialog').open, false);
""")


def test_codex_wait_is_bounded_and_cancel_removes_scheduled_work(node):
    run_page(node, ASSIST + r"""
await button('codex', 'Switch').click();
await $('codex-assist').click();
assert.match($('codex-dialog-status').textContent, /Waiting for Codex/);
for (let i = 0; i < 22; i++) await runTimers(1000);
assert.match($('codex-dialog-status').textContent, /Nothing was switched/);
assert.equal(codexFlow.phase, 'ready');
assert.deepEqual(posts().map(c => c.path), ['/api/codex/quit']);
$('codex-cancel').click(); await settle();
await button('codex', 'Switch').click(); await $('codex-assist').click();
$('codex-cancel').click(); await settle();
codexStatus.running = false; codexStatus.desktopRunning = false;
await runTimers(1000);
assert.deepEqual(posts().map(c => c.path), ['/api/codex/quit', '/api/codex/quit']);
""")


@pytest.mark.parametrize("kind", ["codex-running", "codex-status-unknown", "failure"])
def test_switch_refusal_never_reopens_and_expected_blockers_are_neutral(node, kind):
    run_page(node, ASSIST + "const kind = '" + kind + "';" + r"""
await button('codex', 'Switch').click();
fetchHandler = async path => {
  if (path === '/api/codex/quit') codexStatus = {...codexStatus, running: false, desktopRunning: false};
  return {ok: path !== '/api/switch', json: async () => path === '/api/codex/status' ? codexStatus : path === '/api/switch' ? {ok: false, kind: kind === 'failure' ? 'error' : 'action-required', code: kind, message: 'Switch refused'} : response};
};
await $('codex-assist').click();
assert.deepEqual(posts().map(c => c.path), ['/api/codex/quit', '/api/switch']);
assert.equal($('codex-dialog').open, true);
assert.equal($('codex-dialog-status').className.includes('dialog-error'), kind === 'failure');
assert.equal($('codex-continue').disabled, true);
""")


def test_codex_continue_rechecks_and_duplicate_requests_are_blocked(node):
    run_page(node, r"""
await button('codex', 'Switch').click();
codexStatus.running = true; codexStatus.terminalCount = 1;
await $('codex-continue').click();
assert.equal(posts().length, 0);
assert.match($('codex-dialog-status').textContent, /no longer confirmed stopped/);
codexStatus.running = false; codexStatus.terminalCount = 0;
await $('codex-check').click();
let finish;
fetchHandler = async path => path === '/api/switch' ? new Promise(resolve => { finish = resolve; }) : {ok: true, json: async () => path === '/api/codex/status' ? codexStatus : apiState};
const pending = $('codex-continue').click(); await settle();
await $('codex-continue').click();
assert.equal(posts().length, 1);
assert.equal($('codex-cancel').disabled, true);
assert.equal($('nav-settings').disabled, true);
finish({ok: true, json: async () => response}); await pending;
assert.equal(posts().length, 1);
""")


def test_slow_status_times_out_without_switching_and_cancel_aborts_it(node):
    run_page(node, r"""
let finish;
fetchHandler = async () => new Promise(resolve => { finish = resolve; });
const pending = button('codex', 'Switch').click();
await runTimers(5000); await pending;
assert.match($('codex-dialog-status').textContent, /unknown/);
assert.equal($('codex-continue').disabled, true);
assert.equal(posts().length, 0);
finish({ok: true, json: async () => codexStatus}); await settle();
assert.equal($('codex-continue').disabled, true);
const recheck = $('codex-check').click();
$('codex-cancel').click(); await recheck;
assert.equal(codexFlow, null);
assert.equal([...timeouts.values()].filter(t => t.ms === 5000).length, 0);
assert.equal(posts().length, 0);
""")


def test_account_overflow_preserves_keyboard_focus_after_mutation(node):
    run_page(node, r"""
const menu = nodes('claude').find(n => n.className === 'account-overflow');
menu.open = true;
menu.children[0].focus();
await load();
assert.equal(document.activeElement, menu.children[0]);
const exclude = button('claude', 'Exclude from auto-switch');
exclude.focus();
await exclude.click();
const replacement = nodes('claude').find(n => n.className === 'account-overflow');
assert.equal(replacement.open, true);
assert.equal(document.activeElement, button('claude', 'Exclude from auto-switch'));
""")


def test_claude_followup_and_codex_best_action_required_are_informational(node):
    run_page(node, r"""
response = {ok: true, message: 'Switched.', followUp: 'Keychain may take about 30 seconds to reload.'};
await button('claude', 'Switch').click();
assert.match($('toast').textContent, /Keychain.*30 seconds/);
assert.doesNotMatch($('toast').className, /ember/);
response = {ok: false, kind: 'action-required', code: 'codex-status-unknown', message: 'Confirm Codex is quit.'};
await button('codex-actions', 'Switch best').click();
assert.match($('toast').textContent, /Confirm Codex/);
assert.doesNotMatch($('toast').className, /ember/);
""")


def test_meter_missing_is_not_full_and_overflow_remains_accessible(node):
    run_page(node, r"""
const missing = meter({label: '7d', usedPercent: null});
assert.match(missing.textContent, /Not reported/);
assert.doesNotMatch(missing.textContent, /100%/);
assert.equal(nodes(missing).find(n => n.className.startsWith('fill')).style.width, '0%');
const menu = nodes('codex').find(n => n.className === 'account-overflow');
assert.equal(menu.tagName, 'DETAILS');
assert.match(menu.children[0].attributes['aria-label'], /Manage account 1/);
assert.match(menu.textContent, /Exclude from auto-switch/);
assert.equal($('codex-auto').tagName, 'DETAILS');
""")
    assert "@media (max-width:640px)" in PAGE_HTML
    assert "prefers-reduced-motion: no-preference" in PAGE_HTML
    assert "[hidden] { display:none !important; }" in PAGE_HTML
    assert ":focus-visible" in PAGE_HTML
    assert "innerHTML" not in PAGE_HTML
