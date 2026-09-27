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
const prepared = {ok: true, sessionId: 'synthetic-session', shell: 'posix', command: "CODEX_HOME='/private/new-login' codex -c 'cli_auth_credentials_store=\"file\"' login"};
let loginError = null;
fetchHandler = async (path, options) => ({
  ok: !loginError || !path.endsWith('/complete'),
  json: async () => path.startsWith('/api/state') ? apiState
    : path.endsWith('/prepare') ? prepared
      : path.endsWith('/complete') ? loginError || {ok: true, account: {number: '4'}, switched: true, message: 'Login saved and switched.'}
        : {ok: true},
});
const opener = button('codex-actions', 'Add another account');
"""


@pytest.mark.parametrize("active", [True, False])
def test_pending_saved_login_has_a_session_independent_activation_action(node, active):
    run_page(node, "apiState.codex.accounts[0].active = " + str(active).lower() + r""";
apiState.codex.accounts[0].activationRequired = true;
await load();
const apply = button('codex', 'Use saved login');
assert.ok(apply);
assert.equal(apply.disabled, false);
assert.equal(apply.attributes['aria-label'], 'Use saved login for account 1');
assert.match($('codex').textContent, /Saved login awaiting activation/);
assert.equal(codexEnrollmentFlow, null);
await apply.click();
assert.equal($('codex-dialog').open, true);
assert.match($('codex-dialog-title').textContent, /Use saved login/);
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


def test_preparing_login_requires_deliberate_confirmation_and_does_not_switch(node):
    run_page(node, ENROLLMENT + r"""
opener.focus(); opener.click();
assert.equal($('codex-login-dialog').open, true);
assert.equal(posts().length, 0);
assert.equal($('codex-login-terminal').hidden, true);
assert.match($('codex-login-description').textContent, /unchanged until.*Save & switch/);
assert.match($('codex-login-dialog').textContent, /Requires the Codex CLI/);
assert.equal(document.activeElement, $('codex-login-cancel'));
await $('codex-login-next').click();
assert.deepEqual(posts().map(call => [call.path, call.payload]), [
  ['/api/codex/login/prepare', {confirm: true}],
]);
assert.equal($('codex-login-command').value, prepared.command);
assert.equal($('codex-login-terminal').hidden, false);
assert.equal($('codex-login-next').textContent, 'Save & switch');
assert.equal(document.activeElement, $('codex-login-command'));
assert.match($('codex-login-status').textContent, /No sign-in has been launched/);
assert.equal(apiState.codex.accounts[0].active, true);
assert.equal($('nav-settings').disabled, true);
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


def test_cancel_after_preparation_only_discards_its_owned_session(node):
    run_page(node, ENROLLMENT + r"""
opener.focus(); opener.click(); await $('codex-login-next').click();
await $('codex-login-cancel').click(); await settle();
assert.deepEqual(posts().map(call => [call.path, call.payload]), [
  ['/api/codex/login/prepare', {confirm: true}],
  ['/api/codex/login/cancel', {sessionId: 'synthetic-session', confirm: true}],
]);
assert.equal($('codex-login-dialog').open, false);
assert.equal($('codex-login-command').value, '');
assert.equal(document.activeElement, opener);
assert.equal(codexEnrollmentFlow, null);
""")


def test_enrollment_repairs_the_selected_slot_without_accepting_an_auth_payload(node):
    run_page(node, ENROLLMENT + r"""
button('codex', 'Sign in again…').click();
assert.match($('codex-login-description').textContent, /codex@example.com.*slot 1.*exact account/);
await $('codex-login-next').click();
assert.deepEqual(posts()[0].payload, {number: '1', confirm: true});
await $('codex-login-next').click(); await settle();
assert.deepEqual(posts()[1].payload, {sessionId: 'synthetic-session', confirm: true});
assert.equal(posts()[1].path, '/api/codex/login/complete');
assert.equal(posts().length, 2);
assert.equal($('codex-login-dialog').open, false);
assert.match($('toast').textContent, /saved and switched.*Open Codex.*confirm the account/);
assert.equal(codexEnrollmentFlow, null);
""")


@pytest.mark.parametrize("code", ["codex-running", "codex-status-unknown"])
def test_blocked_activation_stays_in_the_same_session_and_can_be_retried(node, code):
    run_page(node, ENROLLMENT + "const failureCode = " + repr(code) + r""";
opener.click(); await $('codex-login-next').click();
loginError = {ok: false, kind: 'action-required', code: failureCode, message: 'Quit Codex before switching.'};
await $('codex-login-next').click();
assert.equal($('codex-login-dialog').open, true);
assert.equal(codexEnrollmentFlow.sessionId, 'synthetic-session');
assert.match($('codex-login-status').textContent, /Quit Codex/);
assert.equal($('codex-login-next').textContent, 'Save & switch');
loginError = null;
await $('codex-login-next').click();
assert.equal($('codex-login-dialog').open, false);
assert.equal(posts().filter(call => call.path.endsWith('/prepare')).length, 1);
assert.equal(posts().filter(call => call.path.endsWith('/complete')).length, 2);
""")


def test_failed_signin_and_clipboard_denial_remain_actionable_without_discarding_session(node):
    run_page(node, ENROLLMENT + r"""
opener.click(); await $('codex-login-next').click();
await $('codex-login-copy').click();
assert.match($('codex-login-status').textContent, /Select and copy/);
loginError = {ok: false, message: 'Finish the terminal sign-in first.'};
await $('codex-login-next').click();
assert.equal($('codex-login-dialog').open, true);
assert.match($('codex-login-status').textContent, /Finish the terminal sign-in/);
assert.match($('codex-login-status').className, /dialog-error/);
assert.equal($('codex-login-command').value, prepared.command);
""")


def test_preparing_and_saving_cannot_be_cancelled_halfway_or_duplicated(node):
    run_page(node, ENROLLMENT + r"""
opener.click();
let finish;
fetchHandler = () => new Promise(resolve => { finish = () => resolve({ok: true, json: async () => prepared}); });
const pending = $('codex-login-next').click();
assert.equal($('codex-login-next').disabled, true);
assert.equal($('codex-login-cancel').disabled, true);
$('codex-login-next').click();
$('codex-login-dialog').dispatch('cancel');
assert.equal($('codex-login-dialog').open, true);
assert.equal(posts().length, 1);
finish(); await pending;
assert.equal($('codex-login-cancel').disabled, false);
assert.equal($('codex-login-next').disabled, false);
""")


def test_cleanup_failure_does_not_trap_the_user_or_claim_credentials_were_deleted(node):
    run_page(node, ENROLLMENT + r"""
opener.click(); await $('codex-login-next').click();
fetchHandler = async () => ({ok: false, json: async () => ({ok: false, message: 'Private temporary files remain.'})});
await $('codex-login-cancel').click(); await settle();
assert.equal($('codex-login-dialog').open, false);
assert.equal(codexEnrollmentFlow, null);
assert.equal($('nav-settings').disabled, false);
assert.match($('toast').textContent, /temporary files remain.*Stop the terminal sign-in.*quit Agent Switch/);
assert.match($('toast').textContent, /Saved accounts are not removed/);
""")


def test_partial_cleanup_after_success_has_a_cleanup_only_followup(node):
    run_page(node, ENROLLMENT + r"""
opener.click(); await $('codex-login-next').click();
fetchHandler = async path => ({ok: true, json: async () => path.startsWith('/api/state') ? apiState
  : path.endsWith('/complete') ? {ok: true, warning: true, message: 'Saved login activated.', cleanupWarning: 'Temporary files remain private.'} : {ok: true}});
await $('codex-login-next').click();
assert.equal($('codex-login-dialog').open, true);
assert.equal($('codex-login-terminal').hidden, true);
assert.equal($('codex-login-next').hidden, true);
assert.equal($('codex-login-cancel').textContent, 'Close & clean up');
assert.match($('codex-login-status').textContent, /saved and switched.*cleanup needs attention/);
await $('codex-login-cancel').click();
assert.equal(posts().at(-1).path, '/api/codex/login/cancel');
assert.equal($('codex-login-dialog').open, false);
""")


def test_saved_login_with_failed_activation_retries_without_another_signin(node):
    run_page(node, ENROLLMENT + r"""
opener.click(); await $('codex-login-next').click();
loginError = {ok: false, account: {number: '4'}, activationRequired: true, message: 'The login was saved, but activation was not confirmed.'};
await $('codex-login-next').click();
assert.equal($('codex-login-terminal').hidden, true);
assert.equal($('codex-login-command').value, '');
assert.equal($('codex-login-next').textContent, 'Retry switch');
assert.match($('codex-login-status').textContent, /do not sign in again/);
loginError = null;
await $('codex-login-next').click();
assert.equal(posts().filter(call => call.path.endsWith('/prepare')).length, 1);
assert.equal(posts().filter(call => call.path.endsWith('/complete')).length, 2);
assert.equal($('codex-login-dialog').open, false);
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
