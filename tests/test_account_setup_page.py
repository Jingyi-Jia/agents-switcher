"""Execute the new account setup flows using synthetic local API responses."""

from __future__ import annotations

import shutil

import pytest

from tests.test_web_page_actions import run_page


@pytest.fixture(scope="module")
def node():
    executable = shutil.which("node")
    if not executable:
        pytest.skip("Node is required for dashboard interaction checks")
    return executable


ENROLLMENT = r"""
apiState.codex.capabilities = [...apiState.codex.capabilities, 'login'];
await load();
let prepared = {ok: true, sessionId: 'synthetic-session', method: 'browser', status: 'waiting'};
let loginStatus = {ok: true, status: 'waiting', message: 'Waiting for browser sign-in.'};
let saveResult = {ok: true, account: {number: '4', email: 'new@example.com', plan: 'plus'}, activationRequired: true};
let openResult = {ok: true}, cancelResult = {ok: true};
const enrollmentFetch = async path => {
  if (path.endsWith('/complete') && saveResult.activationRequired === true) {
    const existing = apiState.codex.accounts.find(account => account.number === saveResult.account.number);
    if (existing) Object.assign(existing, saveResult.account, {activationRequired: true});
    else apiState.codex.accounts.push({...saveResult.account, activationRequired: true, active: false});
  }
  const body = path.startsWith('/api/state') ? apiState : path === '/api/codex/status' ? codexStatus
    : path.endsWith('/prepare') ? prepared : path.endsWith('/open') ? openResult
      : path.endsWith('/status') ? loginStatus : path.endsWith('/complete') ? saveResult
        : path.endsWith('/cancel') ? cancelResult : response;
  return {ok: body.ok !== false, json: async () => body};
};
fetchHandler = enrollmentFetch;
const opener = button('codex-actions', 'Add another account');
"""


@pytest.mark.parametrize("active", [True, False])
def test_pending_saved_login_has_a_session_independent_activation_action(node, active):
    run_page(node, "apiState.codex.accounts[0].active = " + str(active).lower() + r""";
apiState.codex.accounts[0].activationRequired = true;
await load();
const active = apiState.codex.accounts[0].active;
const apply = button('codex', active ? 'Use saved login' : 'Switch');
assert.ok(apply);
assert.equal(apply.disabled, false);
assert.equal(apply.attributes['aria-label'], active ? 'Use saved login for account 1' : 'Switch to account 1');
assert.match($('codex').textContent, /Saved login awaiting activation/);
assert.equal(codexEnrollmentFlow, null);
await apply.click();
assert.equal($('codex-dialog').open, true);
assert.match($('codex-dialog-title').textContent, active ? /Use saved login/ : /Switch to/);
assert.match($('codex-dialog-description').textContent, /already saved.*No new sign-in/);
assert.equal(posts().length, 0);
fetchHandler = async path => {
  if (path === '/api/switch') {
    apiState.codex.accounts[0].activationRequired = false;
    apiState.codex.accounts[0].active = true;
  }
  return {ok: true, json: async () => path.startsWith('/api/state') ? apiState : path === '/api/codex/status' ? codexStatus : response};
};
await $('codex-continue').click(); await settle();
assert.deepEqual(posts().map(call => [call.path, call.payload]), [
  ['/api/switch', {provider: 'codex', number: '1', useSavedLogin: true}],
]);
assert.equal($('codex-dialog').open, false);
assert.equal(button('codex', 'Use saved login'), undefined);
assert.equal(button('codex', 'Current').disabled, true);
""")


def test_pending_saved_login_cannot_bypass_unknown_process_state_and_can_be_cancelled(node):
    run_page(node, r"""
apiState.codex.accounts[0].activationRequired = true;
codexStatus = {available: false, running: null};
await load();
const apply = button('codex', 'Use saved login');
apply.focus(); await apply.click();
assert.equal($('codex-continue').disabled, true);
await $('codex-continue').click();
assert.equal(posts().length, 0);
await $('codex-cancel').click(); await settle();
assert.equal($('codex-dialog').open, false);
assert.equal(document.activeElement, apply);
assert.equal(apiState.codex.accounts[0].activationRequired, true);
""")


def test_browser_login_requires_explicit_auto_save_consent_and_never_a_command(node):
    run_page(node, ENROLLMENT + r"""
opener.focus(); opener.click();
assert.equal($('codex-login-dialog').open, true);
assert.equal(posts().length, 0);
assert.match($('codex-login-description').textContent, /automatically save.*does not switch accounts/);
assert.match($('codex-login-dialog').textContent, /Sign in with ChatGPT/);
assert.doesNotMatch($('codex-login-dialog').textContent, /terminal|command|folder|Save & switch|01 ·/);
assert.equal($('codex-login-command'), undefined);
assert.equal($('codex-login-copy'), undefined);
assert.equal($('codex-login-next').textContent, 'Continue in browser');
assert.equal(document.activeElement, $('codex-login-next'));
await $('codex-login-next').click();
assert.deepEqual(posts().map(call => [call.path, call.payload]), [
  ['/api/codex/login/prepare', {confirm: true}],
  ['/api/codex/login/open', {sessionId: 'synthetic-session', confirm: true}],
]);
assert.ok(posts().every(call => call.options.headers['X-Auth-Token'] === TOKEN));
assert.equal($('codex-login-next').textContent, 'Reopen browser');
assert.equal(document.activeElement, $('codex-login-next'));
assert.match($('codex-login-status').textContent, /Waiting.*automatically.*does not switch accounts/);
assert.equal(apiState.codex.accounts[0].active, true);
assert.equal($('nav-settings').disabled, true);
assert.equal($('codex-login-cancel').disabled, false);
""")


def test_cancel_before_preparation_has_no_api_side_effect_and_restores_focus(node):
    run_page(node, ENROLLMENT + r"""
opener.focus(); opener.click();
await $('codex-login-cancel').click(); await settle();
assert.equal(posts().length, 0);
assert.equal($('codex-login-dialog').open, false);
assert.equal(document.activeElement, opener);
assert.equal($('nav-settings').disabled, false);
""")


@pytest.mark.parametrize("mode", ["live", "dry-run", "stopped"])
def test_signin_discloses_existing_live_automation_without_changing_it(node, mode):
    run_page(node, ENROLLMENT + "const mode = " + repr(mode) + r""";
apiState.codex.auto.mode = mode;
await load();
opener.click();
assert.equal($('codex-login-auto-notice').hidden, mode !== 'live');
assert.match($('codex-login-auto-notice').textContent, /Automatic switching remains enabled under your existing rules/);
assert.match($('codex-login-description').textContent, /does not switch accounts/);
await $('codex-login-next').click();
loginStatus = {ok: true, status: 'ready'};
await runTimers(1000);
assert.equal($('codex-login-auto-notice').hidden, mode !== 'live');
assert.equal(apiState.codex.auto.mode, mode);
assert.match($('codex-login-description').textContent, /does not switch accounts/);
assert.ok(posts().every(call => call.path !== '/api/auto' && call.path !== '/api/switch'));
""")


@pytest.mark.parametrize("cancel", ["button", "escape", "close"])
def test_cancel_after_preparation_only_discards_its_owned_session(node, cancel):
    run_page(node, ENROLLMENT + "const cancel = " + repr(cancel) + r""";
opener.focus(); opener.click(); await $('codex-login-next').click();
if (cancel === 'button') await $('codex-login-cancel').click();
if (cancel === 'escape') $('codex-login-dialog').dispatch('cancel');
if (cancel === 'close') $('codex-login-dialog').close();
await settle(); await runTimers(1000);
assert.deepEqual(posts().map(call => [call.path, call.payload]), [
  ['/api/codex/login/prepare', {confirm: true}],
  ['/api/codex/login/open', {sessionId: 'synthetic-session', confirm: true}],
  ['/api/codex/login/cancel', {sessionId: 'synthetic-session', confirm: true}],
]);
assert.equal($('codex-login-dialog').open, false);
assert.equal(document.activeElement, opener);
assert.equal(codexEnrollmentFlow, null);
assert.equal($('nav-settings').disabled, false);
""")


def test_enrollment_repairs_the_selected_slot_without_accepting_an_auth_payload(node):
    run_page(node, ENROLLMENT + r"""
saveResult.account = {number: '1', email: 'codex@example.com'};
button('codex', 'Sign in again…').click();
assert.match($('codex-login-description').textContent, /automatically replace the saved login/);
assert.match($('codex-login-description').textContent, /codex@example.com.*slot 1.*exact account/);
await $('codex-login-next').click();
assert.deepEqual(posts()[0].payload, {number: '1', confirm: true});
loginStatus = {ok: true, status: 'ready', account: {email: 'codex@example.com'}};
await runTimers(1000); await settle();
assert.deepEqual(posts().map(call => [call.path, call.payload]), [
  ['/api/codex/login/prepare', {number: '1', confirm: true}],
  ['/api/codex/login/open', {sessionId: 'synthetic-session', confirm: true}],
  ['/api/codex/login/status', {sessionId: 'synthetic-session', confirm: true}],
  ['/api/codex/login/complete', {sessionId: 'synthetic-session', activate: false, confirm: true}],
]);
assert.equal($('codex-login-dialog').open, true);
assert.match($('codex-login-status').textContent, /Account saved: codex@example.com.*Use saved login/);
assert.match($('codex-login-description').textContent, /Signing in saves this account; it does not switch accounts/);
assert.equal($('codex-login-next').textContent, 'Use saved login');
assert.equal($('codex-login-cancel').textContent, 'Close');
assert.equal(apiState.codex.accounts[0].active, true);
assert.equal(apiState.codex.accounts[0].activationRequired, true);
assert.equal(button('codex', 'Use saved login').disabled, false);
assert.equal(calls.at(-1).path, '/api/state?force=1');
await $('codex-login-cancel').click(); await settle();
assert.equal(apiState.codex.accounts[0].activationRequired, true);
assert.equal(document.activeElement, opener);
""")


@pytest.mark.parametrize("running", [True, None])
def test_saving_never_requires_quitting_or_activates_even_if_codex_is_running(node, running):
    run_page(node, ENROLLMENT + "codexStatus.running = " + str(running).lower().replace("none", "null") + r""";
opener.click(); await $('codex-login-next').click();
const focused = document.activeElement;
const reads = calls.filter(call => call.path.startsWith('/api/state')).length;
await runTimers(1000);
loginStatus = {ok: true, status: 'exchanging'};
await runTimers(1000);
assert.equal(document.activeElement, focused);
assert.equal($('codex-login-cancel').disabled, false);
assert.equal($('codex-login-next').disabled, true);
assert.equal(calls.filter(call => call.path.startsWith('/api/state')).length, reads);
loginStatus = {ok: true, status: 'ready'};
await runTimers(1000);
await runTimers(1000);
assert.equal($('codex-login-dialog').open, true);
assert.equal(posts().filter(call => call.path.endsWith('/complete')).length, 1);
assert.equal(posts().at(-1).payload.activate, false);
assert.ok(calls.every(call => !['/api/codex/status', '/api/codex/quit', '/api/codex/open', '/api/switch'].includes(call.path)));
assert.equal(calls.filter(call => call.path.startsWith('/api/state')).length, reads + 1);
assert.equal(apiState.codex.accounts[0].active, true);
assert.equal(apiState.codex.accounts.at(-1).active, false);
assert.equal(button('codex', 'Use saved login'), undefined);
""")


def test_saved_login_closes_enrollment_before_separate_quit_first_activation(node):
    run_page(node, ENROLLMENT + r"""
opener.click(); await $('codex-login-next').click();
loginStatus = {ok: true, status: 'ready'};
await runTimers(1000);
codexStatus = {available: false, running: null};
await $('codex-login-next').click();
assert.equal(posts().at(-1).path, '/api/codex/login/cancel');
assert.equal($('codex-login-dialog').open, false);
assert.equal(codexEnrollmentFlow, null);
assert.equal($('codex-dialog').open, true);
assert.equal($('codex-continue').disabled, true);
await $('codex-continue').click();
assert.equal(posts().filter(call => call.path === '/api/switch').length, 0);
codexStatus = {available: true, running: false, desktopRunning: false, terminalCount: 0, backgroundCount: 0};
await $('codex-check').click();
await $('codex-continue').click();
assert.deepEqual(posts().at(-1).payload, {provider: 'codex', number: '4', useSavedLogin: true});
assert.equal(posts().at(-1).path, '/api/switch');
""")


@pytest.mark.parametrize("stage", ["prepare", "open", "complete"])
def test_short_enrollment_requests_and_saving_cannot_be_cancelled_halfway_or_duplicated(node, stage):
    run_page(node, ENROLLMENT + "const stage = " + repr(stage) + r""";
opener.click();
if (stage === 'complete') { await $('codex-login-next').click(); loginStatus = {ok: true, status: 'ready'}; }
let finish;
fetchHandler = path => path.endsWith('/' + stage) ? new Promise(resolve => { finish = () => resolve(enrollmentFetch(path)); }) : enrollmentFetch(path);
const pending = stage === 'complete' ? runTimers(1000) : $('codex-login-next').click();
await settle();
assert.equal($('codex-login-next').disabled, true);
assert.equal($('codex-login-cancel').disabled, true);
const requests = posts().length;
$('codex-login-next').click();
$('codex-login-dialog').dispatch('cancel');
await continueCodexEnrollment();
await cancelCodexEnrollment();
assert.equal(posts().length, requests);
assert.equal($('codex-login-dialog').open, true);
finish(); await pending;
assert.equal($('codex-login-cancel').disabled, false);
assert.equal($('codex-login-next').disabled, false);
assert.equal(posts().filter(call => call.path.endsWith('/' + stage)).length, 1);
""")


def test_cleanup_failure_does_not_trap_the_user_or_claim_credentials_were_deleted(node):
    run_page(node, ENROLLMENT + r"""
opener.click(); await $('codex-login-next').click();
fetchHandler = async () => ({ok: false, json: async () => ({ok: false, message: 'Private temporary files remain.'})});
await $('codex-login-cancel').click(); await settle();
assert.equal($('codex-login-dialog').open, false);
assert.equal(codexEnrollmentFlow, null);
assert.equal($('nav-settings').disabled, false);
assert.match($('toast').textContent, /temporary files remain.*Quit Agent Switch/);
assert.match($('toast').textContent, /Saved accounts are not removed/);
assert.doesNotMatch($('toast').textContent, /terminal/);
""")


def test_partial_cleanup_after_success_has_a_cleanup_only_followup(node):
    run_page(node, ENROLLMENT + r"""
opener.click(); await $('codex-login-next').click();
saveResult = {...saveResult, warning: true, cleanupWarning: 'Sign-in cleanup needs attention.'};
loginStatus = {ok: true, status: 'ready'};
await runTimers(1000);
assert.equal($('codex-login-dialog').open, true);
assert.equal($('codex-login-cancel').textContent, 'Close');
assert.match($('codex-login-status').textContent, /Account saved.*cleanup needs attention/);
assert.match($('toast').textContent, /Account saved.*cleanup needs attention/);
await runTimers(1000);
await $('codex-login-cancel').click();
assert.equal(posts().at(-1).path, '/api/codex/login/cancel');
assert.equal($('codex-login-dialog').open, false);
assert.equal(posts().filter(call => call.path.endsWith('/complete')).length, 1);
assert.equal(apiState.codex.accounts.at(-1).activationRequired, true);
""")


@pytest.mark.parametrize("failure", ["save", "network", "invalid"])
def test_failed_or_uncertain_save_can_refresh_and_retry_same_session_without_signin(node, failure):
    run_page(node, ENROLLMENT + "const failure = " + repr(failure) + r""";
opener.click(); await $('codex-login-next').click();
const success = saveResult;
saveResult = failure === 'save' ? {ok: false, message: 'The account store could not be written.'} : {ok: true};
fetchHandler = path => {
  if (failure === 'network' && path.endsWith('/complete')) throw new Error('private network failure');
  return enrollmentFetch(path);
};
loginStatus = {ok: true, status: 'ready'};
await runTimers(1000);
await runTimers(1000);
assert.equal($('codex-login-dialog').open, true);
assert.equal($('codex-login-next').textContent, 'Retry save');
assert.equal($('codex-login-refresh').hidden, false);
assert.match($('codex-login-status').textContent, /same sign-in.*do not sign in again/);
assert.doesNotMatch($('codex-login-status').textContent, /Account saved|private network failure/);
assert.equal(posts().filter(call => call.path.endsWith('/complete')).length, 1);
assert.equal(posts().filter(call => call.path.endsWith('/status')).length, 1);
await $('codex-login-refresh').click();
assert.equal(calls.at(-1).path, '/api/state?force=1');
assert.equal($('codex-login-next').textContent, 'Retry save');
saveResult = success; fetchHandler = enrollmentFetch;
await $('codex-login-next').click();
assert.equal(codexEnrollmentFlow.phase, 'saved');
assert.equal(posts().filter(call => call.path.endsWith('/prepare')).length, 1);
assert.equal(posts().filter(call => call.path.endsWith('/complete')).length, 2);
assert.ok(posts().filter(call => call.path.endsWith('/complete')).every(call => call.payload.activate === false));
""")


def test_partial_save_preserves_saved_account_and_does_not_repeat_signin(node):
    run_page(node, ENROLLMENT + r"""
opener.click(); await $('codex-login-next').click();
saveResult = {...saveResult, ok: false, message: 'Login saved, but sign-in cleanup was not confirmed.'};
loginStatus = {ok: true, status: 'ready'};
await runTimers(1000);
assert.equal($('codex-login-next').textContent, 'Use saved login');
assert.match($('codex-login-status').textContent, /Account saved.*cleanup was not confirmed/);
await $('codex-login-cancel').click();
assert.equal(apiState.codex.accounts.at(-1).activationRequired, true);
assert.equal(posts().filter(call => call.path.endsWith('/complete')).length, 1);
assert.ok(posts().every(call => call.path !== '/api/switch'));
""")


def test_browser_open_failure_can_reopen_without_repreparing_or_losing_session(node):
    run_page(node, ENROLLMENT + r"""
openResult = {ok: false, message: 'The default browser could not be opened.'};
opener.click(); await $('codex-login-next').click();
assert.match($('codex-login-status').textContent, /default browser/);
assert.equal($('codex-login-next').textContent, 'Retry opening browser');
await runTimers(1000);
assert.match($('codex-login-status').textContent, /default browser/);
openResult = {ok: true};
await $('codex-login-next').click();
assert.equal($('codex-login-next').textContent, 'Reopen browser');
assert.equal($('codex-login-cancel').disabled, false);
assert.equal(posts().filter(call => call.path.endsWith('/prepare')).length, 1);
assert.equal(posts().filter(call => call.path.endsWith('/open')).length, 2);
assert.equal(posts().filter(call => call.path.endsWith('/complete')).length, 0);
""")


@pytest.mark.parametrize("status", ["exchanging", "ready"])
def test_reused_preparation_resumes_same_session_without_reopening_browser(node, status):
    run_page(node, ENROLLMENT + "const reusedStatus = " + repr(status) + r""";
prepared.status = reusedStatus;
loginStatus = {ok: true, status: reusedStatus};
opener.click(); await $('codex-login-next').click();
assert.deepEqual(posts().map(call => [call.path, call.payload]), [
  ['/api/codex/login/prepare', {confirm: true}],
]);
assert.equal(codexEnrollmentFlow.sessionId, 'synthetic-session');
assert.match($('codex-login-status').textContent, /Resuming your existing browser sign-in/);
assert.equal($('codex-login-next').disabled, true);
assert.equal($('codex-login-cancel').disabled, false);
await $('codex-login-next').click();
await runTimers(1000);
if (reusedStatus === 'exchanging') {
  assert.equal(posts().filter(call => call.path.endsWith('/complete')).length, 0);
  loginStatus = {ok: true, status: 'ready'};
  await runTimers(1000);
}
await runTimers(1000);
assert.equal(codexEnrollmentFlow.phase, 'saved');
assert.equal(posts().filter(call => call.path.endsWith('/prepare')).length, 1);
assert.equal(posts().filter(call => call.path.endsWith('/open') || call.path.endsWith('/cancel')).length, 0);
const saves = posts().filter(call => call.path.endsWith('/complete'));
assert.equal(saves.length, 1);
assert.deepEqual(saves[0].payload, {sessionId: 'synthetic-session', activate: false, confirm: true});
""")


def test_repair_resumed_after_lost_prepare_response_keeps_pinned_slot(node):
    run_page(node, ENROLLMENT + r"""
saveResult.account = {number: '1', email: 'codex@example.com'};
button('codex', 'Sign in again…').click();
fetchHandler = () => { throw new Error('lost response'); };
await $('codex-login-next').click();
assert.equal($('codex-login-next').textContent, 'Start again');
assert.equal(codexEnrollmentFlow.sessionId, null);
prepared.status = 'ready'; loginStatus = {ok: true, status: 'ready'};
fetchHandler = enrollmentFetch;
await $('codex-login-next').click();
await runTimers(1000);
assert.equal(codexEnrollmentFlow.phase, 'saved');
const preparations = posts().filter(call => call.path.endsWith('/prepare'));
assert.equal(preparations.length, 2);
assert.ok(preparations.every(call => call.payload.number === '1' && call.payload.confirm === true));
assert.equal(posts().filter(call => call.path.endsWith('/open') || call.path.endsWith('/cancel')).length, 0);
assert.equal(posts().filter(call => call.path.endsWith('/complete')).length, 1);
await $('codex-login-next').click();
assert.equal($('codex-login-dialog').open, false);
assert.match($('codex-dialog-title').textContent, /Use saved login for codex@example.com/);
assert.equal(posts().at(-1).path, '/api/codex/login/cancel');
""")


@pytest.mark.parametrize("status", ["error", "expired"])
def test_failed_reused_preparation_requires_cancel_before_fresh_session(node, status):
    run_page(node, ENROLLMENT + "const failedStatus = " + repr(status) + r""";
prepared = {...prepared, ok: false, status: failedStatus, message: 'Previous sign-in cannot continue.'};
opener.click(); await $('codex-login-next').click();
assert.equal($('codex-login-next').textContent, 'Start again');
assert.equal(codexEnrollmentFlow.sessionId, 'synthetic-session');
await runTimers(1000);
assert.equal(posts().length, 1);
prepared = {...prepared, ok: true, status: 'waiting', sessionId: 'fresh-session'};
await $('codex-login-next').click();
assert.deepEqual(posts().slice(-3).map(call => [call.path, call.payload]), [
  ['/api/codex/login/cancel', {sessionId: 'synthetic-session', confirm: true}],
  ['/api/codex/login/prepare', {confirm: true}],
  ['/api/codex/login/open', {sessionId: 'fresh-session', confirm: true}],
]);
assert.equal(posts().filter(call => call.path.endsWith('/complete')).length, 0);
""")


@pytest.mark.parametrize("status", ["error", "expired"])
def test_terminal_signin_error_stops_polling_and_only_restarts_deliberately(node, status):
    run_page(node, ENROLLMENT + "const failedStatus = " + repr(status) + r""";
opener.click(); await $('codex-login-next').click();
loginStatus = {ok: false, status: failedStatus, message: 'Sign-in could not finish. Start again.'};
await runTimers(1000); await runTimers(1000);
assert.equal($('codex-login-next').textContent, 'Start again');
assert.match($('codex-login-status').textContent, /could not finish/);
assert.equal(posts().filter(call => call.path.endsWith('/status')).length, 1);
assert.equal(posts().filter(call => call.path.endsWith('/complete')).length, 0);
prepared.sessionId = 'new-session';
loginStatus = {ok: true, status: 'waiting'};
await $('codex-login-next').click();
assert.deepEqual(posts().slice(-3).map(call => [call.path, call.payload]), [
  ['/api/codex/login/cancel', {sessionId: 'synthetic-session', confirm: true}],
  ['/api/codex/login/prepare', {confirm: true}],
  ['/api/codex/login/open', {sessionId: 'new-session', confirm: true}],
]);
assert.equal(codexEnrollmentFlow.sessionId, 'new-session');
""")


def test_wrong_repair_identity_offers_start_again_without_claiming_a_save(node):
    run_page(node, ENROLLMENT + r"""
button('codex', 'Sign in again…').click(); await $('codex-login-next').click();
saveResult = {ok: false, code: 'wrong-account', message: 'The signed-in account does not match slot 1.'};
loginStatus = {ok: true, status: 'ready'};
await runTimers(1000);
assert.equal($('codex-login-next').textContent, 'Start again');
assert.match($('codex-login-status').textContent, /does not match slot 1/);
assert.doesNotMatch($('codex-login-status').textContent, /Account saved/);
assert.equal(apiState.codex.accounts[0].activationRequired, undefined);
await $('codex-login-next').click();
assert.deepEqual(posts().at(-2).payload, {number: '1', confirm: true});
""")


def test_status_polls_never_overlap_and_cancel_blocks_ready_before_cleanup_returns(node):
    run_page(node, ENROLLMENT + r"""
opener.click(); await $('codex-login-next').click();
let finishStatus, finishCancel;
fetchHandler = path => path.endsWith('/status') ? new Promise(resolve => {finishStatus = resolve;})
  : path.endsWith('/cancel') ? new Promise(resolve => {finishCancel = resolve;}) : enrollmentFetch(path);
const flow = codexEnrollmentFlow;
const polling = runTimers(1000); await settle();
await pollCodexEnrollment(flow); await runTimers(1000);
assert.equal(posts().filter(call => call.path.endsWith('/status')).length, 1);
assert.equal($('codex-login-cancel').disabled, false);
const cancelling = $('codex-login-cancel').click();
assert.equal(flow.phase, 'cancelling');
assert.equal(posts().find(call => call.path.endsWith('/status')).options.signal.aborted, true);
assert.equal([...timeouts.values()].filter(timer => [1000, 10000].includes(timer.ms)).length, 0);
finishStatus({ok: true, json: async () => ({ok: true, status: 'ready'})});
await polling; await runTimers(1000);
assert.equal(posts().filter(call => call.path.endsWith('/complete')).length, 0);
assert.equal($('codex-login-next').disabled, true);
finishCancel({ok: true, json: async () => ({ok: true})}); await cancelling;
assert.equal(codexEnrollmentFlow, null);
""")


@pytest.mark.parametrize("late", ["ready", "error", "network"])
def test_late_old_status_response_cannot_touch_a_later_flow(node, late):
    run_page(node, ENROLLMENT + "const late = " + repr(late) + r""";
opener.click(); await $('codex-login-next').click();
let finish;
fetchHandler = path => path.endsWith('/status') ? new Promise((resolve, reject) => {finish = () => late === 'network' ? reject(new Error('late failure')) : resolve({ok: true, json: async () => ({ok: true, status: late, message: 'stale response'})});}) : enrollmentFetch(path);
const polling = runTimers(1000); await settle();
await $('codex-login-cancel').click(); await settle();
prepared.sessionId = 'later-session';
opener.click(); await $('codex-login-next').click();
const current = codexEnrollmentFlow;
const message = $('codex-login-status').textContent;
finish(); await polling;
assert.equal(codexEnrollmentFlow, current);
assert.equal(codexEnrollmentFlow.phase, 'waiting');
assert.equal($('codex-login-status').textContent, message);
assert.equal(posts().filter(call => call.path.endsWith('/complete')).length, 0);
assert.equal([...timeouts.values()].filter(timer => timer.ms === 1000).length, 1);
""")


def test_reopening_browser_discards_the_previous_inflight_status_response(node):
    run_page(node, ENROLLMENT + r"""
opener.click(); await $('codex-login-next').click();
let finish;
fetchHandler = path => path.endsWith('/status') ? new Promise(resolve => {finish = resolve;}) : enrollmentFetch(path);
const polling = runTimers(1000); await settle();
await $('codex-login-next').click();
finish({ok: true, json: async () => ({ok: true, status: 'error', message: 'stale'})});
await polling;
assert.equal(codexEnrollmentFlow.phase, 'waiting');
assert.doesNotMatch($('codex-login-status').textContent, /stale/);
assert.equal([...timeouts.values()].filter(timer => timer.ms === 1000).length, 1);
""")


def test_read_only_status_failure_checks_same_session_without_repeating_signin(node):
    run_page(node, ENROLLMENT + r"""
opener.click(); await $('codex-login-next').click();
fetchHandler = path => {
  if (path.endsWith('/status')) throw new Error('private response');
  return enrollmentFetch(path);
};
await runTimers(1000);
assert.equal($('codex-login-next').textContent, 'Check again');
assert.doesNotMatch($('codex-login-status').textContent, /private response/);
fetchHandler = enrollmentFetch;
await $('codex-login-next').click();
assert.equal($('codex-login-next').textContent, 'Reopen browser');
assert.equal(posts().filter(call => call.path.endsWith('/prepare')).length, 1);
assert.equal(posts().filter(call => call.path.endsWith('/status')).length, 2);
""")


def test_read_only_status_timeout_is_cancelable_and_retries_the_same_session(node):
    run_page(node, ENROLLMENT + r"""
opener.click(); await $('codex-login-next').click();
fetchHandler = (path, options) => path.endsWith('/status') ? new Promise((resolve, reject) => {
  options.signal.addEventListener('abort', () => reject(new Error('aborted')));
}) : enrollmentFetch(path);
const polling = runTimers(1000); await settle();
assert.equal($('codex-login-cancel').disabled, false);
await runTimers(10000); await polling;
assert.equal($('codex-login-next').textContent, 'Check again');
assert.equal([...timeouts.values()].filter(timer => [1000, 10000].includes(timer.ms)).length, 0);
fetchHandler = enrollmentFetch;
await $('codex-login-next').click();
assert.equal($('codex-login-next').textContent, 'Reopen browser');
assert.equal(posts().filter(call => call.path.endsWith('/prepare')).length, 1);
""")


def test_exchanging_signin_can_be_cancelled_without_saving(node):
    run_page(node, ENROLLMENT + r"""
opener.click(); await $('codex-login-next').click();
loginStatus = {ok: true, status: 'exchanging'};
await runTimers(1000);
assert.equal($('codex-login-next').disabled, true);
assert.equal($('codex-login-cancel').disabled, false);
$('codex-login-dialog').dispatch('cancel'); await settle();
await runTimers(1000);
assert.equal(codexEnrollmentFlow, null);
assert.equal(posts().at(-1).path, '/api/codex/login/cancel');
assert.equal(posts().filter(call => call.path.endsWith('/complete')).length, 0);
""")


def test_enrollment_preserves_other_modal_and_background_refresh_guards(node):
    run_page(node, ENROLLMENT + r"""
button('codex', 'Remove').click();
opener.click();
assert.equal(codexEnrollmentFlow, null);
$('dialog-cancel').click(); await settle();
await button('codex', 'Switch').click();
opener.click();
assert.equal(codexEnrollmentFlow, null);
$('codex-cancel').click(); await settle();
opener.click();
const flow = codexEnrollmentFlow;
await button('codex', 'Switch').click();
button('codex', 'Remove').click();
opener.click();
assert.equal(codexEnrollmentFlow, flow);
assert.equal(codexFlow, null);
assert.equal($('action-dialog').open, false);
const requestCount = calls.length;
for (const timer of intervals) await timer.callback();
assert.equal(calls.length, requestCount);
assert.equal(posts().length, 0);
""")


def test_status_saved_is_displayed_without_repeating_completion(node):
    run_page(node, ENROLLMENT + r"""
opener.click(); await $('codex-login-next').click();
loginStatus = {...saveResult, status: 'saved'};
await runTimers(1000); await runTimers(1000);
assert.match($('codex-login-status').textContent, /Account saved: new@example.com/);
assert.equal(posts().filter(call => call.path.endsWith('/complete')).length, 0);
assert.equal($('codex-login-next').textContent, 'Use saved login');
""")


def test_saved_success_keeps_refresh_failure_actionable_without_resaving(node):
    run_page(node, ENROLLMENT + r"""
opener.click(); await $('codex-login-next').click();
loginStatus = {ok: true, status: 'ready'};
fetchHandler = path => {
  if (path.startsWith('/api/state')) throw new Error('unavailable');
  return enrollmentFetch(path);
};
await runTimers(1000);
assert.match($('codex-login-status').textContent, /Account saved/);
assert.match($('toast').textContent, /Account saved.*could not be refreshed/);
assert.equal($('codex-login-refresh').hidden, false);
assert.equal($('codex-login-next').disabled, true);
fetchHandler = enrollmentFetch;
await $('codex-login-refresh').click();
assert.equal($('codex-login-refresh').hidden, true);
assert.equal($('codex-login-next').disabled, false);
assert.equal(posts().filter(call => call.path.endsWith('/complete')).length, 1);
""")


def test_saved_login_handoff_cannot_bypass_provider_capabilities(node):
    run_page(node, ENROLLMENT + r"""
opener.click(); await $('codex-login-next').click();
apiState.codex.capabilities = ['login'];
loginStatus = {ok: true, status: 'ready'};
await runTimers(1000);
assert.equal($('codex-login-next').disabled, true);
assert.equal($('codex-login-refresh').hidden, false);
await continueCodexEnrollment();
assert.equal($('codex-login-dialog').open, true);
assert.equal(codexFlow, null);
assert.equal(posts().filter(call => call.path.endsWith('/cancel')).length, 0);
assert.equal(posts().filter(call => call.path === '/api/switch').length, 0);
""")


@pytest.mark.parametrize("failure", ["missing-session", "wrong-method", "rejected", "network"])
def test_invalid_preparation_never_opens_browser_or_claims_success(node, failure):
    run_page(node, ENROLLMENT + "const failure = " + repr(failure) + r""";
if (failure === 'missing-session') delete prepared.sessionId;
if (failure === 'wrong-method') prepared.method = 'terminal';
if (failure === 'rejected') prepared = {ok: false, message: 'The callback port is already in use.'};
if (failure === 'network') fetchHandler = () => {throw new Error('private error');};
opener.click(); await $('codex-login-next').click();
assert.equal($('codex-login-next').textContent, 'Start again');
assert.equal($('codex-login-cancel').disabled, false);
assert.equal(posts().length, 1);
assert.doesNotMatch($('codex-login-status').textContent, /Account saved|private error/);
await runTimers(1000);
assert.equal(posts().length, 1);
""")


def test_only_classified_login_required_errors_get_direct_repair_controls(node):
    run_page(node, ENROLLMENT + r"""
apiState.codex.accounts[0].error = 'Token rejected. Long CLI instructions: codex login --not-a-gui-command';
apiState.codex.accounts[0].loginRequired = true;
apiState.codex.accounts[1].error = 'Network failure while checking whether sign-in is required.';
apiState.codex.accounts[1].loginRequired = false;
apiState.claude.accounts[0].error = 'Temporary provider error.';
apiState.claude.accounts[0].loginRequired = true;
await load();
const first = $('codex').children[0], second = $('codex').children[1];
const repair = button(first, 'Sign in again');
assert.equal(repair.className, 'primary');
assert.ok(repair);
assert.equal(repair.parent.className, 'account-footer');
assert.ok(button(first, 'Sign in again…'));
assert.match(first.textContent, /Sign-in required/);
assert.doesNotMatch(first.textContent, /Long CLI instructions|codex login/);
assert.equal(button(second, 'Sign in again'), undefined);
assert.match(second.textContent, /Network failure/);
assert.equal(button('claude', 'Sign in again'), undefined);
await repair.click();
assert.match($('codex-login-description').textContent, /slot 1/);
assert.equal(posts().length, 0);
""")


def test_login_required_is_never_inferred_from_error_text_or_truthy_values(node):
    run_page(node, ENROLLMENT + r"""
for (const loginRequired of [undefined, false, 'true', 1]) {
  apiState.codex.accounts[0].loginRequired = loginRequired;
  apiState.codex.accounts[0].error = 'Refresh token rejected. Sign in again.';
  await load(true);
  assert.equal(button('codex', 'Sign in again'), undefined);
  assert.match($('codex').textContent, /Refresh token rejected/);
}
""")


def test_new_login_controls_do_not_bypass_missing_provider_capability(node):
    run_page(node, r"""
assert.equal(button('codex-actions', 'Add another account').disabled, true);
assert.equal(button('codex', 'Sign in again…'), undefined);
startCodexEnrollment(button('codex-actions', 'Add another account'));
assert.equal($('codex-login-dialog').open, undefined);
assert.equal(posts().length, 0);
""")


STORAGE = r"""
let storage = {destination: '/private/agents-switcher', imported: false, canImport: true,
  sources: [{id: 'legacy', label: 'Previous saved accounts'}], warnings: ['Desktop profiles are not imported.']};
fetchHandler = async (path, options) => ({ok: true, json: async () => path === '/api/storage' ? storage
  : path.startsWith('/api/state') ? apiState : {ok: true, message: 'Accounts copied. Original store unchanged.'}});
await loadStorage();
"""


def test_legacy_storage_is_visible_but_never_imported_automatically(node):
    run_page(node, STORAGE + r"""
assert.equal($('storage-notice').hidden, false);
assert.equal($('storage-location').textContent, '/private/agents-switcher');
assert.match($('storage-warnings').textContent, /Desktop profiles are not imported/);
assert.equal(posts().length, 0);
$('storage-review').click();
assert.equal(activeView, 'settings');
assert.equal(document.activeElement, $('storage-import'));
$('storage-import').click();
assert.equal($('action-dialog').open, true);
assert.match($('dialog-description').textContent, /without moving or deleting the original/);
assert.match($('dialog-description').textContent, /Desktop profiles and terminal sessions are excluded/);
await submitDialog();
assert.equal(posts().length, 0);
$('import-consent').checked = true;
storage = {...storage, imported: true, canImport: false};
await submitDialog(); await settle();
assert.deepEqual(posts().map(call => [call.path, call.payload]), [
  ['/api/storage/import', {source: 'legacy', confirm: true}],
]);
assert.equal($('storage-notice').hidden, true);
assert.equal($('storage-import').disabled, true);
assert.equal($('action-dialog').open, false);
""")


def test_multiple_legacy_stores_require_an_explicit_source_choice(node):
    run_page(node, STORAGE + r"""
storage.sources.push({id: 'xdg', label: 'Previous XDG accounts'});
await loadStorage(); $('storage-import').click(); $('import-consent').checked = true;
assert.equal($('import-source').value, '');
await submitDialog(); assert.equal(posts().length, 0);
$('import-source').value = '/untrusted/path';
await submitDialog(); assert.equal(posts().length, 0);
$('import-source').value = 'xdg';
await submitDialog();
assert.deepEqual(posts()[0].payload, {source: 'xdg', confirm: true});
""")


def test_storage_failure_and_occupied_destination_block_import(node):
    run_page(node, STORAGE + r"""
storage.canImport = false;
await loadStorage();
assert.equal($('storage-import').disabled, true);
assert.match($('storage-status').textContent, /will never be overwritten/);
fetchHandler = async () => { throw new Error('Unavailable'); };
await loadStorage();
assert.match($('storage-status').textContent, /could not be checked/);
assert.equal($('storage-import').disabled, true);
assert.equal(posts().length, 0);
""")


def test_invalid_historical_sources_show_the_cause_and_cannot_be_selected(node):
    run_page(node, STORAGE + r"""
storage.sources.push({id: 'xdg', label: 'Previous XDG store', error: 'The roster is malformed.'});
await loadStorage();
assert.match($('storage-warnings').textContent, /Previous XDG store: The roster is malformed/);
$('storage-import').click(); $('import-consent').checked = true;
assert.equal($('import-source').children.at(-1).disabled, true);
$('import-source').value = 'xdg';
await submitDialog();
assert.equal(posts().length, 0);
""")
