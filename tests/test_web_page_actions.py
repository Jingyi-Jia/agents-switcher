"""Run the shipped page script against a small DOM contract double in Node.

These checks exercise rendering and action payloads without adding a browser
dependency to the Python package. Browser layout and native dialog behavior are
verified separately; this double does not pretend to implement those features.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from html.parser import HTMLParser

import pytest

from claude_swap.web.page import PAGE_HTML


class PageTree(HTMLParser):
    def __init__(self):
        super().__init__()
        self.root = {"tag": "document", "attrs": [], "children": []}
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "attrs": attrs, "children": []}
        self.stack[-1]["children"].append(node)
        if tag not in {"meta", "input", "br", "hr", "link"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        if len(self.stack) > 1 and self.stack[-1]["tag"] == tag:
            self.stack.pop()

    def handle_data(self, data):
        if self.stack[-1]["tag"] not in {"script", "style"}:
            self.stack[-1]["children"].append(data)


SCRIPT = PAGE_HTML.split("<script>", 1)[1].split("</script>", 1)[0]

DOM = r"""
const assert = require('node:assert/strict');
const walk = node => [node, ...node.children.flatMap(walk)];
class Element {
  constructor(tag, text = '') {
    this.tagName = tag.toUpperCase(); this.children = []; this.parent = null;
    this.dataset = {}; this.attributes = {}; this.listeners = {};
    this._text = text; this.value = ''; this.className = '';
    this.hidden = false; this.disabled = false; this.checked = false;
    this.classList = {add: name => { this.className += ' ' + name; }};
  }
  get textContent() { return this._text + this.children.map(n => n.textContent).join(''); }
  set textContent(text) { this.replaceChildren(); this._text = String(text); }
  appendChild(child) { child.parent = this; this.children.push(child); return child; }
  append(...children) { children.forEach(child => this.appendChild(child)); }
  replaceChildren(...children) {
    this.children.forEach(child => {child.parent = null;});
    this.children = []; this._text = ''; this.append(...children);
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name.startsWith('data-')) this.dataset[name.slice(5)] = String(value);
    else if (name === 'class') this.className = String(value);
    else if (['checked', 'hidden', 'required', 'disabled'].includes(name)) this[name] = true;
    else this[name] = String(value);
  }
  contains(node) { return walk(this).includes(node); }
  get isConnected() { return root.contains(this); }
  querySelectorAll(selector) {
    const key = selector.slice(6, -1);
    return walk(this).slice(1).filter(node => key in node.dataset);
  }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  dispatch(name) { (this.listeners[name] || []).forEach(callback => callback({preventDefault(){}})); }
  focus() { document.activeElement = this; }
  click() { if (!this.disabled && this.onclick) return this.onclick(); }
  showModal() { this.open = true; }
  close() { this.open = false; queueMicrotask(() => this.dispatch('close')); }
  reportValidity() {
    if (this.tagName === 'FORM') return walk(this).slice(1).every(node => node.reportValidity());
    if (this.disabled) return true;
    if (this.required && !(this.type === 'checkbox' ? this.checked : this.value)) return false;
    if (this.type === 'number' && this.value) {
      const n = Number(this.value);
      return Number.isFinite(n) && (!this.min || n >= Number(this.min)) && (!this.max || n <= Number(this.max));
    }
    return true;
  }
}
function build(spec) {
  if (typeof spec === 'string') return new Element('#text', spec);
  const node = new Element(spec.tag);
  spec.attrs.forEach(([name, value]) => node.setAttribute(name, value || ''));
  spec.children.forEach(child => node.appendChild(build(child)));
  return node;
}
const root = build(PAGE_TREE);
const document = {
  getElementById: id => walk(root).find(node => node.id === id),
  createElement: tag => new Element(tag),
  createTextNode: text => new Element('#text', text),
  querySelectorAll: selector => root.querySelectorAll(selector),
  documentElement: walk(root).find(node => node.tagName === 'HTML'),
  activeElement: null,
};
const location = {search: '?token=dashboard-test-secret'};
const stored = {};
const localStorage = {getItem: key => stored[key], setItem: (key, value) => {stored[key] = value;}};
const intervals = [];
const setInterval = (callback, ms) => {intervals.push({callback, ms});};
const setTimeout = () => 1;
const clearTimeout = () => {};
const settle = () => new Promise(resolve => setImmediate(resolve));
const common = ['switch', 'add', 'remove', 'disable', 'switch-best', 'auto'];
const fixture = () => ({
  claude: {available: true, capabilities: [...common, 'token'], accounts: [
    {number: '1', email: 'claude@example.com', active: true},
    {number: '2', email: 'other@example.com', active: false}],
    liveLogin: {email: 'claude@example.com', managed: true},
    auto: {mode: 'stopped', threshold: 90, events: []}},
  codex: {available: true, capabilities: common, accounts: [
    {number: '1', email: 'codex@example.com', active: true},
    {number: '2', email: 'next@example.com', active: false}],
    auto: {mode: 'stopped', threshold: 90, events: []}},
});
let apiState = fixture(), response = {ok: true, message: 'Saved.'};
const calls = [];
let fetchHandler = async (path, options) => ({
  ok: true, json: async () => JSON.parse(JSON.stringify(path.startsWith('/api/state') ? apiState : response)),
});
const fetch = async (path, options) => {
  calls.push({path, options, payload: options.body ? JSON.parse(options.body) : null});
  return fetchHandler(path, options);
};
const nodes = host => walk(typeof host === 'string' ? document.getElementById(host) : host);
const button = (host, label) => nodes(host).find(node => node.tagName === 'BUTTON' && node.textContent === label);
const posts = () => calls.filter(call => call.options.method === 'POST');
const submitDialog = () => document.getElementById('dialog-form').onsubmit({preventDefault(){}});
"""


@pytest.fixture(scope="module")
def node():
    executable = shutil.which("node")
    if not executable:
        pytest.skip("Node is required to execute the dashboard JavaScript checks")
    return executable


def run_page(node, scenario):
    tree = PageTree()
    tree.feed(PAGE_HTML)
    source = (
        "const PAGE_TREE = " + json.dumps(tree.root) + ";\n" + DOM + SCRIPT
        + "\n(async () => { await settle();\n" + scenario
        + "\n})().catch(error => {console.error(error); process.exitCode = 1;});"
    )
    result = subprocess.run(
        [node, "-"], input=source, text=True, capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_dashboard_javascript_syntax(node):
    result = subprocess.run(
        [node, "--check"], input=SCRIPT, text=True, capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_api_key_account_is_not_reported_as_a_usage_error(node):
    run_page(node, r"""
const account = card('claude', {number: '3', email: 'key@example.com', sentinel: 'api key'}, apiState.claude);
const state = nodes(account).find(node => node.className === 'state');
assert(state.textContent.includes('subscription quota is not available'));
assert(!nodes(account).some(node => node.className === 'state out'));
""")


def test_provider_actions_are_equally_available_for_managed_accounts(node):
    run_page(node, r"""
for (const provider of ['claude', 'codex']) {
  for (const label of ['Add current', 'Switch best', 'Refresh usage']) {
    assert.equal(button(provider + '-actions', label).disabled, false, provider + ': ' + label);
  }
  assert.equal(button(provider, 'Disable').disabled, false);
  assert.equal(button(provider, 'Remove').disabled, false);
  assert.equal(button(provider, 'switch').disabled, false);
  assert.equal(button(provider, 'active').disabled, true);
  await button(provider + '-actions', 'Add current').click();
  assert.equal(posts().at(-1).path, '/api/add');
  assert.deepEqual(posts().at(-1).payload, {provider});
}
assert(button('claude-actions', 'Add token / API key'));
assert.equal(button('codex-actions', 'Add token / API key'), undefined);
""")


def test_old_fixtures_and_unavailable_claude_preserve_the_desktop_notice(node):
    run_page(node, r"""
apiState = {claude: {available: false, error: 'not configured'}, codex: {available: true, accounts: []}};
await load();
assert.equal(button('codex-actions', 'Add current').disabled, false);
assert.equal(button('codex-actions', 'Switch best').disabled, true);
assert.equal(button('claude-actions', 'Add current').disabled, true);
assert.match($('codex-auto').textContent, /unavailable/);
assert.match($('claude-switch-notice').textContent, /CLI only.*desktop.*Code tab.*separate sign-in/);
assert.equal($('claude-switch-notice').hidden, false);
""")


def test_remove_requires_confirmation_and_disable_can_be_reversed(node):
    run_page(node, r"""
const remove = button('codex', 'Remove');
remove.click();
assert.equal(posts().length, 0);
assert.equal($('action-dialog').open, true);
assert.match($('dialog-description').textContent, /codex@example.com.*slot 1/);
$('dialog-cancel').click();
await settle();
assert.equal(posts().length, 0);
remove.click();
await submitDialog();
assert.deepEqual(posts().at(-1).payload, {provider: 'codex', number: '1', confirm: true});
assert.equal(posts().at(-1).path, '/api/remove');
await settle();
apiState.codex.accounts[0].disabled = true;
const disable = button('codex', 'Disable');
disable.focus();
await disable.click();
assert.deepEqual(posts().at(-1).payload, {provider: 'codex', number: '1', disabled: true});
assert.equal(posts().at(-1).path, '/api/disabled');
assert.equal(document.activeElement, button('codex', 'Enable'));
await button('codex', 'Enable').click();
assert.equal(posts().at(-1).payload.disabled, false);
""")


def test_switch_best_displays_no_switch_reason_and_restart_warning(node):
    run_page(node, r"""
response = {ok: true, switched: false, reason: 'No eligible account has more headroom.'};
await button('claude-actions', 'Switch best').click();
assert.equal(posts().at(-1).path, '/api/switch-best');
assert.equal($('toast').textContent, response.reason);
response = {ok: true, message: 'Switched. Restart Codex.', restartRequired: true};
await button('codex', 'switch').click();
assert.deepEqual(posts().at(-1).payload, {provider: 'codex', number: '2'});
assert.equal(posts().at(-1).path, '/api/switch');
assert.match($('toast').textContent, /Restart Codex/);
assert.match($('toast').className, /ember/);
""")


@pytest.mark.parametrize("failure", ["http", "body", "network"])
def test_failed_actions_restore_controls_and_show_failure(node, failure):
    run_page(node, "const failure = " + json.dumps(failure) + r""";
fetchHandler = async (path, options) => {
  if (path.startsWith('/api/state')) return {ok: true, json: async () => apiState};
  if (failure === 'network') throw new Error('private network detail');
  return {ok: failure !== 'http', json: async () => ({ok: false, message: 'No current login.'})};
};
const add = button('claude-actions', 'Add current');
await add.click();
assert.equal(add.disabled, false);
assert.equal(add.textContent, 'Add current');
assert.equal(button('codex-actions', 'Add current').disabled, false);
assert.match($('toast').className, /ember/);
assert.match($('toast').textContent, failure === 'network' ? /Could not complete/ : /No current login/);
assert.doesNotMatch($('toast').textContent, /private network detail/);
""")


def test_pending_actions_block_duplicate_requests_and_restore_after_refresh_failure(node):
    run_page(node, r"""
let finish;
fetchHandler = (path, options) => {
  if (path.startsWith('/api/state')) return Promise.reject(new Error('offline'));
  return new Promise(resolve => {finish = () => resolve({ok: true, json: async () => response});});
};
const add = button('claude-actions', 'Add current');
const first = add.click();
assert.equal(add.disabled, true);
await act('/api/add', {provider: 'claude'}, add, 'adding…');
assert.equal(posts().length, 1);
finish();
await first;
assert.equal(add.disabled, false);
assert.match($('toast').textContent, /State could not be refreshed/);
""")


def test_auto_controls_require_live_confirmation_and_bound_event_history(node):
    run_page(node, r"""
const ui = providerUI.claude;
ui.threshold.value = '92.5'; ui.threshold.oninput();
ui.modes.live.click();
assert.equal(posts().length, 0);
assert.match($('dialog-description').textContent, /92.5%.*local dashboard server/);
await submitDialog();
assert.equal(posts().at(-1).path, '/api/auto');
assert.deepEqual(posts().at(-1).payload, {provider: 'claude', mode: 'live', threshold: 92.5, confirm: true});
await settle();
apiState.claude.auto = {mode: 'live', threshold: 92.5, events: Array.from({length: 30}, (_, i) => ({kind: 'poll', message: 'Reason ' + i, at: 1700000000 + i}))};
await load();
assert.equal(ui.events.children.length, 20);
assert.match(ui.events.children[0].textContent, /Reason 10/);
assert.equal(ui.reason.textContent, 'Reason 29');
ui.threshold.value = '';
ui.modes.stopped.click(); await settle();
assert.deepEqual(posts().at(-1).payload, {provider: 'claude', mode: 'stopped'});
assert.match($('claude-auto').textContent, /server session only/);
""")


def test_polling_preserves_edited_threshold_and_focused_account_controls(node):
    run_page(node, r"""
const threshold = providerUI.codex.threshold;
threshold.value = '93'; threshold.oninput(); threshold.focus();
apiState.codex.auto.threshold = 85;
await load();
assert.equal(providerUI.codex.threshold, threshold);
assert.equal(threshold.value, '93');
assert.equal(document.activeElement, threshold);
const switchButton = button('codex', 'switch');
switchButton.focus();
await load();
assert.equal(button('codex', 'switch'), switchButton);
assert.equal(document.activeElement, switchButton);
assert.equal(intervals[0].ms, 20000);
$('watch').checked = false; $('watch').onchange();
let before = calls.length;
intervals[0].callback(); await settle();
assert.equal(calls.length, before);
assert.match($('watch-status').textContent, /auto-switch is not stopped/);
$('watch').checked = true; $('watch').onchange(); await settle();
assert(calls.length > before);
""")


def test_token_dialog_posts_secret_only_in_body_and_confirms_explicit_slot(node):
    run_page(node, r"""
button('claude-actions', 'Add token / API key').click();
assert.equal($('action-dialog').attributes['aria-labelledby'], 'dialog-title');
assert.equal($('credential').type, 'password');
assert.equal($('credential-email').type, 'email');
assert.equal($('credential-slot').required, false);
$('credential').value = 'test-private-credential';
$('credential-email').value = 'person@example.com';
$('credential-slot').value = '2'; $('credential-slot').oninput();
await submitDialog();
assert.equal(posts().length, 0);
const check = nodes('dialog-fields').find(node => node.type === 'checkbox');
assert.match(check.parent.textContent, /replacing.*slot 2/);
check.checked = true;
response = {ok: false, message: 'Cannot save test-private-credential: slot conflict.'};
await submitDialog();
assert.equal(posts().at(-1).path, '/api/token');
assert.deepEqual(posts().at(-1).payload, {provider: 'claude', token: 'test-private-credential', email: 'person@example.com', slot: 2, confirm: true});
assert.equal($('credential').value, '');
assert.match($('dialog-feedback').textContent, /slot conflict/);
assert.doesNotMatch($('toast').textContent + $('dialog-feedback').textContent, /test-private-credential/);
assert.equal($('dialog-submit').disabled, false);
assert.equal($('action-dialog').open, true);
assert(calls.every(call => !call.path.includes('test-private-credential')));
""")


def test_slow_polls_do_not_overlap_or_replace_a_newer_action_snapshot(node):
    run_page(node, r"""
let finishPoll;
let reads = 0;
fetchHandler = async (path, options) => {
  if (path.startsWith('/api/state')) {
    reads += 1;
    if (reads === 1) return new Promise(resolve => {finishPoll = () => resolve({ok: true, json: async () => fixture()});});
    return {ok: true, json: async () => apiState};
  }
  apiState.claude.accounts[0].email = 'refreshed@example.com';
  return {ok: true, json: async () => response};
};
const poll = load();
intervals[0].callback(); await settle();
assert.equal(reads, 1);
await button('claude-actions', 'Add current').click();
assert.match($('claude').textContent, /refreshed@example.com/);
finishPoll(); await poll;
assert.match($('claude').textContent, /refreshed@example.com/);
""")


def test_token_dialog_uses_null_for_an_automatic_slot_and_clears_on_cancel(node):
    run_page(node, r"""
button('claude-actions', 'Add token / API key').click();
$('credential').value = 'test-only-token'; $('credential-email').value = 'person@example.com';
const before = calls.length;
intervals[0].callback(); await settle();
assert.equal(calls.length, before);
await submitDialog(); await settle();
assert.equal(posts().at(-1).payload.slot, null);
assert.equal(posts().at(-1).payload.confirm, false);
assert.equal($('action-dialog').open, false);
assert.equal($('credential'), undefined);
button('claude-actions', 'Add token / API key').click();
$('credential').value = 'another-private-token';
$('dialog-cancel').click(); await settle();
assert.equal($('credential'), undefined);
assert.equal(posts().length, 1);
button('claude-actions', 'Add token / API key').click();
$('credential').value = 'test-without-email';
assert.equal($('credential-email').required, false);
await submitDialog(); await settle();
assert.equal(posts().at(-1).payload.email, '');
""")


def test_token_email_refresh_requires_explicit_confirmation_without_a_slot(node):
    run_page(node, r"""
button('claude-actions', 'Add token / API key').click();
$('credential').value = 'replacement-test-token';
$('credential-email').value = 'CLAUDE@example.com'; $('credential-email').oninput();
const check = nodes('dialog-fields').find(node => node.type === 'checkbox');
assert.equal(check.required, true);
assert.equal(check.parent.hidden, false);
assert.match(check.parent.textContent, /refreshing.*claude@example.com.*slot 1/);
await submitDialog();
assert.equal(posts().length, 0);
check.checked = true;
await submitDialog();
assert.equal(posts().at(-1).payload.slot, null);
assert.equal(posts().at(-1).payload.confirm, true);
""")


def test_user_content_remains_text_and_theme_supports_all_modes(node):
    run_page(node, r"""
const content = '<img src=x onerror=alert(1)>';
apiState.claude.accounts[0].email = content;
apiState.claude.auto.events = [{message: content, kind: 'poll', at: 1700000000}];
apiState.claude.switchNotice = content;
await load();
assert.match($('claude').textContent, /<img src=x/);
assert.equal(nodes('claude').some(node => node.tagName === 'IMG'), false);
assert.equal(providerUI.claude.reason.textContent, content);
assert.equal($('claude-switch-notice').textContent, content);
for (const theme of ['dark', 'light', 'system']) {
  $('theme').value = theme; $('theme').onchange();
  assert.equal(document.documentElement.dataset.theme, theme);
  assert.equal(stored['agent-switch-theme'], theme);
}
""")
    assert "innerHTML" not in SCRIPT
    assert "@media (prefers-color-scheme: dark)" in PAGE_HTML
    assert "window.prompt" not in SCRIPT
    assert "window.confirm" not in SCRIPT
