'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { quotaModel } = require('../src/tray-dashboard.cjs');

function page() {
  class Node {
    constructor(tag) { this.tag = tag; this.childNodes = []; this.attributes = {}; this.dataset = {}; this.events = {}; this.scrollTop = 0; }
    set textContent(value) { this.text = String(value); this.childNodes = []; }
    get textContent() { return (this.text || '') + this.childNodes.map(node => node.textContent).join(''); }
    append(...nodes) { this.childNodes.push(...nodes); }
    replaceChildren(...nodes) { this.childNodes = nodes; this.text = ''; }
    setAttribute(name, value) { this.attributes[name] = value; }
    addEventListener(name, handler) { this.events[name] = handler; }
    focus() { this.focused = true; }
  }
  const overview = new Node('main'), close = new Node('button'), root = new Node('html');
  const buttons = ['app', 'accounts', 'usage', 'settings'].map(view => { const node = new Node('button'); node.dataset.view = view; return node; });
  const document = { documentElement: root, events: {}, createElement: tag => new Node(tag),
    getElementById: id => id === 'overview' ? overview : close,
    querySelectorAll: () => buttons,
    addEventListener: (name, handler) => { document.events[name] = handler; } };
  let render;
  const actions = [];
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/tray-popover.js'), 'utf8'), {
    document, window: { agentSwitchTray: { onModel: callback => { render = callback; }, navigate: action => actions.push(action) } },
  });
  const all = () => {
    const visit = node => [node, ...node.childNodes.flatMap(visit)];
    return visit(overview);
  };
  return { overview, close, root, buttons, document, actions, render, all };
}

const now = 1800000000;
const window = (usedPercent, extra = {}) => ({ label: '5h', usedPercent, observedAt: now - 60,
  resetAt: now + 3600, windowSeconds: 18000, ...extra });

test('renderer draws provider cards, numeric remaining bars, model separation and explicit account badges', () => {
  const { overview, all, render, root } = page();
  overview.scrollTop = 120;
  render(quotaModel({ preferences: { theme: 'dark' }, claude: { available: true, accounts: [
    { alias: 'Research', active: true, windows: [window(0), window(100, { label: 'Fable · 7d', scope: 'model' })] },
  ] }, codex: { available: true, accounts: [{ alias: 'Work', disabled: true, activationRequired: true,
    windows: [window(35.123456789)] }] } }, now));
  assert.equal(root.dataset.theme, 'dark');
  assert.equal(overview.focused, true);
  assert.equal(overview.scrollTop, 120);
  for (const text of ['Claude Code', 'Codex', 'Research', 'Work', 'Active', 'Excluded from auto', 'Saved login pending',
    '100% left', '0% left', '65% left', 'model limit', 'resets 1h 0m']) assert.ok(overview.textContent.includes(text), text);
  const bars = all().filter(node => node.tag === 'progress');
  assert.deepEqual(bars.map(bar => bar.value), [100, 0, 64.876543211]);
  assert.ok(bars.every(bar => bar.max === 100 && bar.attributes['aria-label'].includes('% remaining')));
  assert.ok(all().some(node => node.className?.includes('model')));
  assert.ok(all().some(node => node.className?.includes('low')));
  assert.equal(overview.textContent.includes('64.876543211%'), false);
});

test('renderer distinguishes failed last-good bars from fresh quota and never draws false healthy bars for errors or credits', () => {
  const { overview, all, render } = page();
  render(quotaModel({ claude: { available: true, accounts: [
    { usageFailed: true, windows: [window(60)] },
    { error: 'SECRET', windows: [window(0)] },
    { loginRequired: true, windows: [window(0)] },
    { usageFailed: true }, { onCredits: true, windows: [window(0)] },
    { sentinel: 'api key', windows: [window(0)] },
    { windows: [window(null), window(200), window(-1), window(NaN)] },
  ] } }, now));
  for (const text of ['Update failed · last successful report', 'Usage unavailable', 'Sign-in required',
    'Update failed · no quota report available', 'On paid credits', 'API key · metered usage', 'Unknown']) {
    assert.ok(overview.textContent.includes(text), text);
  }
  const bars = all().filter(node => node.tag === 'progress');
  assert.equal(bars.length, 1);
  assert.equal(bars[0].value, 40);
  assert.match(bars[0].attributes['aria-label'], /last successful report/);
  assert.ok(all().some(node => node.className === 'last-good'));
  assert.equal(overview.textContent.includes('100%'), false);
  assert.equal(overview.textContent.includes('SECRET'), false);
});

test('loading, unavailable, empty and invalid resets show honest text; theme updates do not stick', () => {
  const { overview, render, root, all } = page();
  render(quotaModel(null));
  assert.equal(overview.textContent, 'Loading account usage…');
  render(quotaModel(null, now, 'unavailable'));
  assert.match(overview.textContent, /Open the app to reconnect/);
  render(quotaModel({ preferences: { theme: 'light' }, claude: { available: true, accounts: [] } }, now));
  assert.equal(root.dataset.theme, 'light');
  assert.match(overview.textContent, /No saved accounts/);
  assert.match(overview.textContent, /Provider unavailable/);
  assert.deepEqual(all().filter(node => node.tag === 'h2').map(node => node.textContent), ['Claude Code0 saved', 'Codex']);
  render(quotaModel({ preferences: { theme: 'system' }, codex: { available: true, accounts: [
    { windows: [window(40, { observedAt: null })] }, {},
  ] } }, now));
  assert.equal(root.dataset.theme, undefined);
  assert.match(overview.textContent, /Reset time unknown/);
  assert.match(overview.textContent, /Quota unknown/);
});

test('long account names are text nodes, never markup; keyboard and footer actions stay fixed navigation only', () => {
  const { overview, all, render, actions, buttons, close, document } = page();
  const name = '<img src="https://example.test/" onerror="alert(1)">' + 'long'.repeat(100);
  render(quotaModel({ claude: { available: true, accounts: [{ alias: name, windows: [window(20, { label: name })] }] } }, now));
  const heading = all().find(node => node.tag === 'h3');
  assert.equal(heading.textContent, name.slice(0, 320));
  assert.equal(heading.childNodes.length, 0);
  assert.equal(all().some(node => node.tag === 'img' || node.tag === 'script'), false);
  for (const button of buttons) button.events.click();
  close.events.click();
  let prevented = false;
  document.events.keydown({ key: 'Escape', preventDefault: () => { prevented = true; } });
  assert.equal(prevented, true);
  assert.deepEqual(actions, ['app', 'accounts', 'usage', 'settings', 'dismiss', 'dismiss']);
  assert.equal(overview.focused, true);
});

test('static assets forbid network and inline execution while providing scrolling, label wrapping and reduced motion', () => {
  const html = fs.readFileSync(path.join(__dirname, '../src/tray-popover.html'), 'utf8');
  const css = fs.readFileSync(path.join(__dirname, '../src/tray-popover.css'), 'utf8');
  for (const rule of ["default-src 'none'", "script-src 'self'", "style-src 'self'", "connect-src 'none'",
    "frame-src 'none'", "base-uri 'none'", "form-action 'none'"]) assert.ok(html.includes(rule), rule);
  assert.doesNotMatch(html, /unsafe-inline|unsafe-eval|https?:|\son\w+=|<iframe|<webview/);
  assert.match(html, /<main[^>]*tabindex="0"/);
  assert.match(html, /aria-label="Dismiss quota overview"/);
  assert.match(css, /overflow-y:auto/);
  assert.match(css, /min-height:0/);
  assert.match(css, /overflow-wrap:anywhere/);
  assert.match(css, /flex-wrap:wrap/);
  assert.match(css, /prefers-reduced-motion:reduce/);
  assert.match(css, /prefers-color-scheme:dark/);
  assert.match(css, /focus-visible/);
});
