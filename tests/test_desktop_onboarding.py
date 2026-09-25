"""Exercise desktop onboarding with the shipped page's Node DOM contract runner."""

from __future__ import annotations

import json
import shutil

import pytest

from claude_swap.web.page import PAGE_HTML
from tests.test_web_page_actions import run_page


@pytest.fixture(scope="module")
def node():
    executable = shutil.which("node")
    if not executable:
        pytest.skip("Node is required to execute the dashboard JavaScript checks")
    return executable


DESKTOP = r"""
const desktopFixture = (platform = 'darwin') => {
  const data = fixture();
  data.desktop = {
    version: '1.0.0', platform, windowClose: 'quit',
    providers: {claude: {installed: true}, codex: {installed: true}},
  };
  for (const id of ['claude', 'codex']) {
    data[id].accounts = [];
    data[id].liveLogin = null;
  }
  return data;
};
"""


def test_browser_without_desktop_metadata_keeps_existing_behavior(node):
    run_page(node, r"""
Object.defineProperty(globalThis, 'navigator', {value: {userAgent: 'Electron/38.0.0'}, configurable: true});
apiState = {claude: {available: true, accounts: []}, codex: {available: true, accounts: []}};
await load();
assert.equal($('help-toggle').hidden, true);
assert.equal($('desktop-guide').hidden, true);
assert.equal($('app-title').textContent, 'Agent Switch');
assert.match($('claude').textContent, /agent-switch add/);
assert.match($('codex').textContent, /agent-switch codex add/);
for (const id of ['claude', 'codex']) {
  assert(button(id + '-actions', 'Add current'));
  assert.match(providerUI[id].lifecycle.textContent, /Closing this tab.*does not stop it/);
  assert.match(providerUI[id].lifecycle.textContent, /server session only/);
}
assert.equal(posts().length, 0);
""")


@pytest.mark.parametrize("platform,label", [("darwin", "macOS"), ("win32", "Windows"), ("linux", "Linux")])
def test_desktop_metadata_updates_layout_after_async_fetch(node, platform, label):
    run_page(node, DESKTOP + "const platform = " + json.dumps(platform) + ";\n"
             + "const label = " + json.dumps(label) + r""";
assert.equal($('desktop-guide').hidden, true);
assert.equal($('help-toggle').hidden, true);
let finish;
fetchHandler = () => new Promise(resolve => {finish = () => resolve({ok: true, json: async () => desktopFixture(platform)});});
const loading = button('claude-actions', 'Refresh usage').click();
assert.equal($('desktop-guide').hidden, true);
finish(); await loading;
assert.equal($('desktop-guide').hidden, false);
assert.equal($('help-toggle').hidden, false);
assert.equal($('help-toggle').attributes['aria-expanded'], 'true');
assert.equal($('app-title').textContent, 'Agent Switch');
assert.equal(document.title, 'Agent Switch');
assert.equal($('desktop-meta').textContent, 'Agent Switch 1.0.0 · ' + label);
assert.equal($('guide-label').textContent, 'Getting started');
assert.match($('claude').textContent, /Sign in through this provider's CLI/);
assert.doesNotMatch($('claude').textContent, /agent-switch add/);
for (const id of ['claude', 'codex']) assert(button(id + '-actions', 'Add existing login'));
assert.equal(posts().length, 0);
""")


def test_missing_clients_offer_only_official_setup_help(node):
    run_page(node, DESKTOP + r"""
apiState = desktopFixture();
for (const id of ['claude', 'codex']) apiState.desktop.providers[id].installed = false;
await load();
for (const id of ['claude', 'codex']) {
  assert.equal(helpUI[id].installed.textContent, 'CLI not detected');
  assert.match(helpUI[id].login.textContent, id === 'claude' ? /No CLI login detected/ : /No file-backed login detected/);
  assert.match(helpUI[id].next.textContent, /official setup guide/);
  assert.equal(helpUI[id].add.disabled, true);
  helpUI[id].add.click();
}
const links = nodes('desktop-guide').filter(node => node.tagName === 'A');
assert.deepEqual(links.map(node => node.href), [
  'https://code.claude.com/docs/en/setup',
  'https://developers.openai.com/codex/app',
]);
assert(links.every(node => node.target === '_blank' && node.rel === 'noopener noreferrer'));
assert.match($('desktop-guide').textContent, /includes its own runtime/);
assert.match($('desktop-guide').textContent, /Claude Code needs its CLI and sign-in/);
assert.match($('desktop-guide').textContent, /Codex CLI is not required for a Desktop login/);
assert.match($('desktop-guide').textContent, /Claude Desktop, including its Code tab, has a separate sign-in/);
assert.match($('desktop-guide').textContent, /doesn't install provider CLIs, start sign-in flows, or change Desktop cookies/);
assert.match($('desktop-guide').textContent, /doesn't start a new sign-in/);
assert.equal(posts().length, 0);
""")


def test_installed_cli_without_login_does_not_offer_a_fake_sign_in(node):
    run_page(node, DESKTOP + r"""
apiState = desktopFixture();
await load();
for (const id of ['claude', 'codex']) {
  assert.equal(helpUI[id].installed.textContent, 'CLI detected');
  assert.match(helpUI[id].next.textContent, id === 'claude' ? /Sign in through this provider's CLI/ : /Sign in through Codex Desktop or CLI/);
  assert.equal(helpUI[id].add.disabled, true);
}
assert.equal(posts().length, 0);
apiState.codex.liveLogin = {email: 'codex-new@example.com', managed: false};
await $('guide-check').click();
assert.equal(calls.at(-1).path, '/api/state?force=1');
assert.equal(helpUI.codex.add.disabled, false);
assert.equal(helpUI.claude.add.disabled, true);
assert.match(helpUI.codex.login.textContent, /codex-new@example.com.*not managed yet/);
assert.equal(posts().length, 0);
""")


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_existing_login_adoption_is_provider_local_and_help_remains_available(node, provider):
    run_page(node, DESKTOP + "const provider = " + json.dumps(provider) + r""";
apiState = desktopFixture();
apiState[provider].liveLogin = {email: 'existing@example.com', managed: false};
await load();
assert.equal(helpUI[provider].add.disabled, false);
assert.match(helpUI[provider].next.textContent, /without signing you in again/);
fetchHandler = async (path, options) => {
  if (path === '/api/add') {
    apiState[provider].accounts = [{number: '1', email: 'existing@example.com', active: true}];
    apiState[provider].liveLogin.managed = true;
  }
  return {ok: true, json: async () => path.startsWith('/api/state') ? apiState : response};
};
helpUI[provider].add.focus();
await helpUI[provider].add.click();
assert.equal(posts().length, 1);
assert.equal(posts()[0].path, '/api/add');
assert.deepEqual(posts()[0].payload, {provider});
assert.equal($('desktop-guide').hidden, true);
assert.equal(document.activeElement, $('help-toggle'));
assert.equal($('help-toggle').hidden, false);
assert.match($(provider + '-count').textContent, /1 managed/);
const other = provider === 'claude' ? 'codex' : 'claude';
assert.equal(state[other].accounts.length, 0);
assert.equal(helpUI[other].add.disabled, true);
$('help-toggle').click();
assert.equal($('desktop-guide').hidden, false);
assert.equal(document.activeElement, $('guide-title'));
assert.equal($('guide-label').textContent, 'Help');
assert.match(helpUI[provider].login.textContent, /already managed/);
assert.match(helpUI[provider].next.textContent, /refresh its saved credentials/);
""")


def test_live_login_is_detected_independently_of_cli_installation(node):
    run_page(node, DESKTOP + r"""
apiState = desktopFixture();
apiState.desktop.providers.claude.installed = false;
apiState.claude.liveLogin = {email: 'existing@example.com', managed: false};
await load();
assert.equal(helpUI.claude.installed.textContent, 'CLI not detected');
assert.match(helpUI.claude.login.textContent, /existing@example.com/);
assert.equal(helpUI.claude.add.disabled, false);
await helpUI.claude.add.click();
assert.deepEqual(posts().at(-1).payload, {provider: 'claude'});
""")


def test_managed_accounts_suppress_first_run_but_help_can_be_reopened(node):
    run_page(node, DESKTOP + r"""
apiState = desktopFixture();
apiState.codex.accounts = fixture().codex.accounts;
await load();
assert.equal($('desktop-guide').hidden, true);
assert.equal($('help-toggle').hidden, false);
$('help-toggle').click();
assert.equal($('desktop-guide').hidden, false);
assert.match(helpUI.codex.login.textContent, /2 managed accounts/);
await load();
assert.equal($('desktop-guide').hidden, false);
$('help-close').click();
assert.equal($('desktop-guide').hidden, true);
assert.equal(document.activeElement, $('help-toggle'));
assert.equal($('help-toggle').attributes['aria-expanded'], 'false');
apiState.codex.accounts = [];
await load();
assert.equal($('desktop-guide').hidden, true);
$('help-toggle').click();
assert.equal($('desktop-guide').hidden, false);
assert.equal(posts().length, 0);
""")


def test_unavailable_provider_and_missing_capability_block_adoption(node):
    run_page(node, DESKTOP + r"""
apiState = desktopFixture();
apiState.claude.available = false;
apiState.claude.error = 'Cannot read local credentials.';
apiState.codex.capabilities = ['switch'];
apiState.codex.liveLogin = {email: 'existing@example.com', managed: false};
await load();
assert.match(helpUI.claude.login.textContent, /could not be checked/);
assert.match(helpUI.claude.next.textContent, /Account access is unavailable/);
assert.match($('claude').textContent, /Cannot read local credentials/);
assert.match(providerUI.claude.reason.textContent, /not available for this provider in the app/);
for (const id of ['claude', 'codex']) {
  assert.equal(helpUI[id].add.disabled, true);
  assert.equal(button(id + '-actions', 'Add existing login').disabled, true);
  helpUI[id].add.click();
}
assert.equal(posts().length, 0);
""")


def test_help_actions_show_real_failures_and_restore_controls(node):
    run_page(node, DESKTOP + r"""
apiState = desktopFixture();
apiState.claude.liveLogin = {email: 'existing@example.com', managed: false};
await load();
let finish;
fetchHandler = (path, options) => {
  if (path.startsWith('/api/state')) return Promise.resolve({ok: true, json: async () => apiState});
  return new Promise(resolve => {finish = () => resolve({ok: false, json: async () => ({ok: false, message: 'Login is no longer available.'})});});
};
const pending = helpUI.claude.add.click();
assert.equal(helpUI.claude.add.disabled, true);
assert.equal($('guide-check').disabled, true);
helpUI.claude.add.click();
assert.equal(posts().length, 1);
finish(); await pending;
assert.match($('toast').textContent, /Login is no longer available/);
assert.match($('toast').className, /ember/);
assert.equal($('desktop-guide').hidden, false);
assert.equal(helpUI.claude.add.disabled, false);
assert.equal(helpUI.claude.add.textContent, 'Add existing login');
assert.equal($('guide-check').disabled, false);
assert.equal(state.claude.accounts.length, 0);
""")


def test_desktop_lifecycle_copy_and_live_confirmation_match_window_quit(node):
    run_page(node, DESKTOP + r"""
apiState = desktopFixture();
await load();
for (const id of ['claude', 'codex']) {
  assert.match(providerUI[id].lifecycle.textContent, /Closing the app stops its automation/);
  assert.match(providerUI[id].lifecycle.textContent, /app session only/);
  assert.doesNotMatch(providerUI[id].lifecycle.textContent, /tab|server session/);
}
providerUI.codex.modes.live.click();
assert.match($('dialog-description').textContent, /Closing the app stops its automation/);
assert.doesNotMatch($('dialog-description').textContent, /Closing the tab|local dashboard server/);
assert.equal(posts().length, 0);
await submitDialog(); await settle();
assert.deepEqual(posts().at(-1).payload, {provider: 'codex', mode: 'live', threshold: 90, confirm: true});
$('watch').checked = false; $('watch').onchange();
assert.match($('watch-status').textContent, /auto-switch is not stopped/);
button('claude-actions', 'Add token / API key').click();
assert.match($('dialog-description').textContent, /this app's local service/);
assert.match($('dialog-description').textContent, /Claude Desktop sign-in, including the Code tab, stays separate/);
$('dialog-cancel').click(); await settle();
delete apiState.desktop;
await load();
assert.equal($('desktop-guide').hidden, true);
assert.equal($('help-toggle').hidden, true);
providerUI.claude.modes.live.click();
assert.match($('dialog-description').textContent, /Closing this tab.*does not stop it/);
assert.match($('dialog-description').textContent, /local dashboard server/);
""")


def test_desktop_connection_failures_do_not_suggest_a_separate_server(node):
    run_page(node, DESKTOP + r"""
apiState = desktopFixture();
await load();
fetchHandler = async () => {throw new Error('private connection detail');};
await $('guide-check').click();
assert.match($('toast').textContent, /Reopen Agent Switch/);
assert.doesNotMatch($('toast').textContent, /dashboard server|private connection detail/);
assert.equal($('guide-check').disabled, false);
await button('codex-actions', 'Add existing login').click();
assert.match($('toast').textContent, /reopen the app/);
assert.doesNotMatch($('toast').textContent, /dashboard server|private connection detail/);
""")


def test_desktop_preserves_paid_credit_manual_only_and_provider_controls(node):
    run_page(node, DESKTOP + r"""
apiState = desktopFixture();
apiState.claude.liveLogin = {email: 'claude@example.com', managed: false};
apiState.codex.accounts = [{number: '3', email: 'credits@example.com', active: false, onCredits: true, percent: 100}];
await load();
assert.match($('codex').textContent, /paid credits/);
assert.match($('codex').textContent, /Auto-switch will never choose this account/);
assert.equal(button('codex', 'Switch').disabled, false);
assert.equal(button('codex', 'Exclude from auto-switch').disabled, false);
assert.equal(button('codex', 'Remove').disabled, false);
$('help-toggle').click();
assert.match($('desktop-guide').textContent, /Paid-credit accounts remain manual-only/);
await button('codex', 'Switch').click();
assert.equal(posts().length, 0);
await $('codex-continue').click();
assert.deepEqual(posts().at(-1).payload, {provider: 'codex', number: '3'});
await button('claude-actions', 'Add existing login').click();
assert.deepEqual(posts().at(-1).payload, {provider: 'claude'});
assert.equal(apiState.codex.accounts[0].email, 'credits@example.com');
""")


def test_help_renders_fetched_content_as_text_and_preserves_focus(node):
    run_page(node, DESKTOP + r"""
apiState = desktopFixture();
const content = '<img src=x onerror=alert(1)>';
apiState.desktop.version = content;
apiState.desktop.providers.claude.url = 'https://untrusted.example/setup';
apiState.claude.liveLogin = {email: content, managed: false};
await load();
const add = helpUI.claude.add;
add.focus();
await load();
assert.equal(helpUI.claude.add, add);
assert.equal(document.activeElement, add);
assert.match($('desktop-meta').textContent, /<img src=x/);
assert.match(helpUI.claude.login.textContent, /<img src=x/);
assert(!nodes('desktop-guide').some(node => node.tagName === 'IMG'));
assert(nodes('desktop-guide').filter(node => node.tagName === 'A').every(node => !node.href.includes('untrusted.example')));
""")
    assert "innerHTML" not in PAGE_HTML
