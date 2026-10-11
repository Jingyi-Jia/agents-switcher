"""Visibility and independent profile readiness checks in the dashboard DOM."""

import pytest

from tests.test_web_page_actions import node, run_page
from tests.test_account_setup_page import ENROLLMENT


PROFILES = r"""
apiState.claudeDesktop = {supported: true, installed: true, available: true, canCreate: true,
  canManage: true, running: false, profiles: [{id: 'a'.repeat(32), name: 'Work'}]};
await load();
const profileTick = () => intervals.find(i => i.ms === 5000).callback();
const dashboardTick = () => intervals.find(i => i.ms === 20000).callback();
const visibility = hidden => { document.hidden = hidden; document.dispatch('visibilitychange'); };
const reads = () => calls.filter(c => c.path.startsWith('/api/state') || c.path === '/api/claude-desktop/status');
"""


def test_profile_readiness_and_refresh_use_only_header_authenticated_status(node):
    run_page(node, PROFILES + r"""
calls.length = 0;
apiState.claudeDesktop.running = true;
profileTick(); await settle();
assert.deepEqual(reads().map(c => c.path), ['/api/claude-desktop/status']);
assert.equal(button('claude-desktop-profiles', 'Open').disabled, true);
apiState.claudeDesktop.running = false;
await button('claude-desktop-actions', 'Refresh').click();
assert.equal(button('claude-desktop-profiles', 'Open').disabled, false);
assert.deepEqual(reads().map(c => c.path), ['/api/claude-desktop/status', '/api/claude-desktop/status']);
assert(reads().every(c => c.options.headers['X-Auth-Token'] === TOKEN));
dashboardTick(); await settle();
assert.equal(reads().at(-1).path, '/api/state');
assert.equal(posts().length, 0);
""")


def test_hidden_polling_stops_and_resume_refreshes_without_forcing_quota(node):
    run_page(node, PROFILES + r"""
state.codex.auto.mode = 'live'; state.claude.auto.mode = 'live';
visibility(true); calls.length = 0;
for (let i = 0; i < 3; i++) { dashboardTick(); profileTick(); }
await settle();
assert.equal(reads().length, 0);
assert.equal(state.codex.auto.mode, 'live'); assert.equal(state.claude.auto.mode, 'live');
visibility(false); await settle();
assert.deepEqual(reads().map(c => c.path), ['/api/state', '/api/claude-desktop/status']);
assert.equal(posts().length, 0);
assert.equal(intervals.filter(i => i.ms === 20000).length, 1);
assert.equal(intervals.filter(i => i.ms === 5000).length, 1);
""")


def test_hidden_document_does_not_pause_enrollment_status_or_save_only_completion(node):
    run_page(node, PROFILES + ENROLLMENT + r"""
apiState.codex.auto.mode = 'live'; apiState.claude.auto.mode = 'live';
await load();
opener.click(); await $('codex-login-next').click();
visibility(true); calls.length = 0;
await runTimers(1000);
assert.deepEqual(posts().map(c => c.path), ['/api/codex/login/status']);
dashboardTick(); profileTick(); await settle();
assert.equal(reads().length, 0);
loginStatus = {ok: true, status: 'ready'};
await runTimers(1000);
assert.deepEqual(posts().map(c => c.path), ['/api/codex/login/status', '/api/codex/login/status', '/api/codex/login/complete']);
assert.equal(posts().at(-1).payload.activate, false);
assert.equal(codexEnrollmentFlow.phase, 'saved');
assert.equal(state.codex.auto.mode, 'live'); assert.equal(state.claude.auto.mode, 'live');
assert.equal(reads().at(-1).path, '/api/state?force=1');
calls.length = 0; visibility(false); await settle();
assert.equal(reads().length, 0);
assert.equal(posts().length, 0);
""")


@pytest.mark.parametrize("guard", ["paused", "busy", "dialog", "switch", "enrollment", "usage", "settings"])
def test_resume_and_ticks_respect_ui_guards(node, guard):
    run_page(node, PROFILES + "const guard = " + repr(guard) + ";" + r"""
visibility(true);
if (guard === 'paused') $('watch').checked = false;
if (guard === 'busy') busy = true;
if (guard === 'dialog') button('claude', 'Remove').click();
if (guard === 'switch') codexFlow = {};
if (guard === 'enrollment') codexEnrollmentFlow = {};
if (['usage', 'settings'].includes(guard)) await navigate(guard);
calls.length = 0;
visibility(false); await settle(); dashboardTick(); profileTick(); await settle();
if (['usage', 'settings'].includes(guard)) {
  assert(reads().every(c => c.path === '/api/state'));
} else assert.equal(reads().length, 0);
assert.equal(posts().length, 0);
""")


def test_profile_dialog_resumes_readiness_without_replacing_unsaved_fields(node):
    run_page(node, PROFILES + r"""
button('claude-desktop-profiles', 'New profile').click();
const fields = nodes('dialog-fields');
const name = fields.find(n => n.type === 'text'), email = fields.find(n => n.type === 'email');
const consent = fields.find(n => n.type === 'checkbox');
name.value = 'Unsaved name'; email.value = 'label@example.test'; consent.checked = true;
$('watch').checked = false;
visibility(true); calls.length = 0; profileTick(); await settle();
assert.equal(reads().length, 0);
apiState.claudeDesktop.running = true;
visibility(false); await settle();
assert.deepEqual(reads().map(c => c.path), ['/api/claude-desktop/status']);
assert.equal(nodes('dialog-fields').find(n => n.type === 'text'), name);
assert.equal(name.value, 'Unsaved name'); assert.equal(email.value, 'label@example.test');
assert.equal(consent.checked, true); assert.equal($('action-dialog').open, true);
assert.equal(button('claude-desktop-profiles', 'Open').disabled, true);
assert.equal(posts().length, 0);
""")


@pytest.mark.parametrize("failure", ["network", "http", "json", "malformed"])
def test_profile_status_failure_blocks_launch_keeps_forms_and_can_recover(node, failure):
    run_page(node, PROFILES + "const failure = " + repr(failure) + ";" + r"""
button('claude-desktop-profiles', 'Open').click();
nodes('dialog-fields').find(n => n.type === 'checkbox').checked = true;
fetchHandler = async () => {
  if (failure === 'network') throw new Error('offline');
  return {ok: failure !== 'http', json: async () => {
    if (failure === 'json') throw new Error('invalid JSON');
    return failure === 'malformed' ? {available: true, running: false} : {ok: false, message: 'failed'};
  }};
};
profileTick(); await settle();
assert.equal(state.claudeDesktop.running, null);
assert.equal(button('claude-desktop-profiles', 'Open').disabled, true);
assert.equal(button('claude-desktop-actions', 'Open Claude').disabled, true);
assert.match($('claude-desktop-status').textContent, /couldn't be checked.*blocked/);
await submitDialog();
assert.equal(posts().length, 0); assert.equal($('action-dialog').open, true);
assert.equal(nodes('dialog-fields').find(n => n.type === 'checkbox').checked, true);
fetchHandler = async () => ({ok: true, json: async () => apiState.claudeDesktop});
profileTick(); await settle();
assert.equal(state.claudeDesktop.running, false);
assert.equal(button('claude-desktop-profiles', 'Open').disabled, false);
""")


def test_stale_profile_response_cannot_overwrite_newer_full_state_or_action(node):
    run_page(node, PROFILES + r"""
let finish;
fetchHandler = async path => path === '/api/claude-desktop/status'
  ? new Promise(resolve => {finish = resolve;}) : {ok: true, json: async () => apiState};
profileTick(); await settle(); profileTick(); await settle();
assert.equal(calls.filter(c => c.path === '/api/claude-desktop/status').length, 1);
apiState.claudeDesktop.running = true;
await load();
finish({ok: true, json: async () => ({...apiState.claudeDesktop, running: false})}); await settle();
assert.equal(state.claudeDesktop.running, true);
profileTick(); await settle();
await button('claude-actions', 'Add current').click();
finish({ok: true, json: async () => ({...apiState.claudeDesktop, running: false})}); await settle();
assert.equal(state.claudeDesktop.running, true);
""")


def test_stale_full_state_cannot_overwrite_newer_profile_status(node):
    run_page(node, PROFILES + r"""
let finish;
fetchHandler = async path => path.startsWith('/api/state')
  ? new Promise(resolve => {finish = resolve;})
  : {ok: true, json: async () => ({...apiState.claudeDesktop, running: true})};
const pending = load(); await settle();
profileTick(); await settle();
finish({ok: true, json: async () => apiState}); await pending;
assert.equal(state.claudeDesktop.running, true);
assert.equal(button('claude-desktop-profiles', 'Open').disabled, true);
""")


@pytest.mark.parametrize("stale", ["profile", "dashboard"])
def test_hidden_invalidates_inflight_reads_and_resume_can_start_new_reads(node, stale):
    run_page(node, PROFILES + "const stale = " + repr(stale) + ";" + r"""
let finish;
fetchHandler = async path => new Promise(resolve => {finish = resolve;});
if (stale === 'profile') profileTick(); else dashboardTick();
await settle();
const old = finish;
visibility(true);
fetchHandler = async path => ({ok: true, json: async () => path === '/api/claude-desktop/status'
  ? {...apiState.claudeDesktop, running: true} : {...apiState, claudeDesktop: {...apiState.claudeDesktop, running: true}}});
visibility(false); await settle();
old({ok: true, json: async () => stale === 'profile' ? apiState.claudeDesktop : apiState}); await settle();
assert.equal(state.claudeDesktop.running, true);
assert.equal(button('claude-desktop-profiles', 'Open').disabled, true);
assert.equal(posts().length, 0);
""")


def test_profile_status_error_is_discarded_after_hiding_or_opening_another_dialog(node):
    run_page(node, PROFILES + r"""
let finish;
fetchHandler = async () => new Promise(resolve => {finish = resolve;});
profileTick(); await settle(); visibility(true);
finish({ok: false, json: async () => ({ok: false})}); await settle();
assert.equal(state.claudeDesktop.running, false);
fetchHandler = async path => ({ok: true, json: async () => path === '/api/claude-desktop/status' ? apiState.claudeDesktop : apiState});
visibility(false); await settle();
fetchHandler = async () => new Promise(resolve => {finish = resolve;});
profileTick(); await settle();
button('claude', 'Remove').click();
finish({ok: false, json: async () => ({ok: false})}); await settle();
assert.equal(state.claudeDesktop.running, false);
assert.equal($('action-dialog').open, true);
assert.match($('dialog-title').textContent, /Remove/);
""")


def test_hiding_preserves_an_inflight_forced_dashboard_refresh(node):
    run_page(node, PROFILES + r"""
let finish;
fetchHandler = async () => new Promise(resolve => {finish = resolve;});
const pending = load(true, true); await settle();
visibility(true);
finish({ok: true, json: async () => ({...apiState, claudeDesktop: {...apiState.claudeDesktop, running: true}})});
assert.equal(await pending, true);
assert.equal(state.claudeDesktop.running, true);
assert.equal(button('claude-desktop-profiles', 'Open').disabled, true);
assert.equal(reads().at(-1).path, '/api/state?force=1');
""")


def test_hiding_during_post_action_refresh_does_not_report_a_false_refresh_failure(node):
    run_page(node, PROFILES + r"""
let finish;
fetchHandler = async (path, options) => options.method === 'POST'
  ? {ok: true, json: async () => ({ok: true, message: 'Saved current account.'})}
  : new Promise(resolve => {finish = resolve;});
const pending = button('claude-actions', 'Add current').click(); await settle();
assert.equal(reads().at(-1).path, '/api/state?force=1');
visibility(true);
finish({ok: true, json: async () => ({...apiState, claudeDesktop: {...apiState.claudeDesktop, running: true}})});
await pending;
assert.equal(state.claudeDesktop.running, true);
assert.equal(busy, false);
assert.match($('toast').textContent, /Saved current account/);
assert.doesNotMatch($('toast').textContent, /could not be refreshed|couldn't be reached/);
""")


@pytest.mark.parametrize("success", [True, False])
def test_hiding_preserves_manual_profile_refresh_success_and_failure(node, success):
    run_page(node, PROFILES + "const success = " + str(success).lower() + ";" + r"""
let finish;
fetchHandler = async () => new Promise(resolve => {finish = resolve;});
const pending = button('claude-desktop-actions', 'Refresh').click(); await settle();
visibility(true);
finish({ok: success, json: async () => success
  ? {...apiState.claudeDesktop, running: true} : {ok: false}});
await pending;
assert.equal(state.claudeDesktop.running, success ? true : null);
assert.equal(button('claude-desktop-profiles', 'Open').disabled, true);
assert.equal(busy, false);
if (!success) assert.match($('claude-desktop-status').textContent, /couldn't be checked.*blocked/);
""")


@pytest.mark.parametrize("stale", ["profile", "dashboard"])
def test_hidden_automatic_reads_stay_invalid_when_resume_does_not_start_a_new_read(node, stale):
    run_page(node, PROFILES + "const stale = " + repr(stale) + ";" + r"""
let finish;
fetchHandler = async () => new Promise(resolve => {finish = resolve;});
if (stale === 'profile') profileTick(); else dashboardTick();
await settle(); visibility(true);
$('watch').checked = false;
const count = reads().length;
visibility(false); await settle();
assert.equal(reads().length, count);
const changed = {...apiState.claudeDesktop, running: true};
finish({ok: true, json: async () => stale === 'profile' ? changed : {...apiState, claudeDesktop: changed}});
await settle();
assert.equal(state.claudeDesktop.running, false);
assert.equal(button('claude-desktop-profiles', 'Open').disabled, false);
""")
