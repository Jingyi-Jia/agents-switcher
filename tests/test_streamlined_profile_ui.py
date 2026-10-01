from __future__ import annotations

import pytest

from tests.test_web_page_actions import node, run_page


def test_quit_codex_switches_from_the_original_click_with_two_readiness_checks(node):
    run_page(node, r"""
assert.equal(posts().length, 0);
await button('codex', 'Switch').click();
assert.deepEqual(posts().map(call => [call.path, call.payload]), [
  ['/api/switch', {provider: 'codex', number: '2'}],
]);
assert.equal(calls.filter(call => call.path === '/api/codex/status').length, 2);
assert.equal($('codex-dialog').open, false);
assert.equal(codexFlow, null);
assert.equal(nodes('codex-dialog').some(node => /codex-step-/.test(node.id || '')), false);
assert.match($('toast').className, /show/);
await runTimers(5000);
assert.equal($('toast').className, '');
""")


@pytest.mark.parametrize("readiness", ["running", "unknown"])
def test_one_click_codex_switch_still_refuses_a_changed_second_readiness_check(node, readiness):
    run_page(node, "const readiness = '" + readiness + "';" + r"""
let checks = 0;
fetchHandler = async path => {
  if (path === '/api/codex/status') {
    ++checks;
    return {ok: true, json: async () => checks === 1 ? codexStatus : {
      ...codexStatus, running: readiness === 'running' ? true : null,
      backgroundCount: readiness === 'running' ? 1 : 0,
    }};
  }
  return {ok: true, json: async () => apiState};
};
await button('codex', 'Switch').click();
assert.equal(checks, 2);
assert.equal(posts().length, 0);
assert.equal($('codex-dialog').open, true);
assert.equal($('codex-continue').disabled, true);
assert.match($('codex-dialog-status').textContent, /no longer confirmed stopped/);
""")


def test_running_codex_desktop_shows_only_the_assisted_primary_action(node):
    run_page(node, r"""
codexStatus = {...codexStatus, running: true, desktopRunning: true, canAssist: true};
await button('codex', 'Switch').click();
assert.equal(posts().length, 0);
assert.equal($('codex-continue').hidden, true);
assert.equal($('codex-assist').hidden, false);
assert.equal($('codex-assist').disabled, false);
assert.match($('codex-dialog-status').textContent, /Save your work.*reopen Codex/);
$('codex-cancel').click();
assert.equal(posts().length, 0);
""")


@pytest.mark.parametrize("cancel", ["button", "escape", "navigation"])
def test_cancelling_the_initial_codex_check_discards_late_clear_status(node, cancel):
    run_page(node, "const cancel = '" + cancel + "';" + r"""
let finish;
fetchHandler = async () => new Promise(resolve => { finish = resolve; });
const pending = button('codex', 'Switch').click();
await settle();
assert.equal($('codex-cancel').disabled, false);
if (cancel === 'button') $('codex-cancel').click();
if (cancel === 'escape') $('codex-dialog').dispatch('cancel');
if (cancel === 'navigation') navigate('settings');
await pending;
finish({ok: true, json: async () => codexStatus});
await settle();
assert.equal(posts().length, 0);
assert.equal(codexFlow, null);
assert.equal($('codex-dialog').open, false);
""")


def test_profile_first_use_is_compact_but_limits_and_explicit_consent_remain(node):
    run_page(node, r"""
apiState.claudeDesktop = {available: true, canCreate: true, supported: true, installed: true,
  running: false, profiles: [{id: 'a'.repeat(32), name: 'Work'}]};
await load();
assert.doesNotMatch($('claude-desktop-heading').textContent, /Beta/);
assert.equal($('claude-desktop-status').hidden, true);
await button('claude-desktop-profiles', 'Open').click();
assert.equal(posts().length, 0);
assert.equal($('action-dialog').open, true);
assert($('dialog-description').textContent.length < 90);
const details = nodes('dialog-fields').find(node => node.tagName === 'DETAILS');
assert.notEqual(details.open, true);
assert.match(details.textContent, /Claude-in-Chrome pairing/);
assert.match(details.textContent, /not verified identities/);
assert.match(details.textContent, /Mac.*Code\/Cowork.*unverified/);
const consent = nodes('dialog-fields').find(node => node.type === 'checkbox');
assert.equal(consent.required, true);
await submitDialog();
assert.equal(posts().length, 0);
consent.checked = true;
await submitDialog();
assert.deepEqual(posts().map(call => [call.path, call.payload]), [
  ['/api/preferences', {profileNoticeVersion: 1, confirm: true}],
  ['/api/claude-desktop/open', {profileId: 'a'.repeat(32), confirm: true}],
]);
""")


@pytest.mark.parametrize("running", [True, None])
def test_remembered_profile_launch_stays_blocked_when_claude_is_running_or_unknown(node, running):
    run_page(node, "const running = " + ("null" if running is None else "true") + ";" + r"""
apiState.preferences = {theme: 'system', profileNoticeVersion: 1};
apiState.claudeDesktop = {available: true, canCreate: true, supported: true, installed: true,
  running, profiles: [{id: 'a'.repeat(32), name: 'Work'}]};
await load();
assert.equal(button('claude-desktop-profiles', 'Open').disabled, true);
assert.equal($('claude-desktop-status').hidden, false);
await openDesktopProfile('a'.repeat(32), 'Work', button('claude-desktop-profiles', 'Open'));
assert.equal(posts().length, 0);
assert.notEqual($('action-dialog').open, true);
""")


def test_profiles_alone_suppress_account_setup_scaffolding_and_do_not_launch_on_read(node):
    run_page(node, r"""
apiState.desktop = {providers: {claude: {installed: true}, codex: {installed: true}}};
apiState.claude.accounts = [];
apiState.codex.accounts = [];
apiState.preferences = {theme: 'system', profileNoticeVersion: 1};
apiState.claudeDesktop = {available: true, canCreate: true, supported: true, installed: true,
  running: false, profiles: [{id: 'a'.repeat(32), name: 'Work'}]};
await load();
assert.equal($('desktop-guide').hidden, true);
assert.equal(posts().length, 0);
await button('claude-desktop-profiles', 'Open').click();
assert.notEqual($('action-dialog').open, true);
assert.deepEqual(posts().map(call => [call.path, call.payload]), [
  ['/api/claude-desktop/open', {profileId: 'a'.repeat(32), confirm: true}],
]);
""")
