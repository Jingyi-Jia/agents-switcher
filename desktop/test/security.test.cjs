'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { dashboardURL, externalHelpURL, isOwnedURL, isNavigationAllowed, secureSession, secureWebContents } = require('../src/security.cjs');

const origin = 'http://127.0.0.1:32123';

test('owned network scope is exactly the authenticated loopback origin', () => {
  for (const url of [`${origin}/`, `${origin}/api/state`, `${origin}/api/action?value=1`]) {
    assert.equal(isOwnedURL(url, origin), true, url);
  }
  for (const url of [
    'https://127.0.0.1:32123/', 'http://localhost:32123/', 'http://127.0.0.1:32124/',
    'http://127.0.0.1/', 'http://127.0.0.1:32123.evil.com/', `${origin}@evil.com/`,
    'http://user:password@127.0.0.1:32123/', 'https://evil.com/', 'file:///etc/passwd',
    'data:text/html,hello', 'javascript:alert(1)', 'blob:http://127.0.0.1:32123/id',
    'ws://127.0.0.1:32123/', 'not a url',
  ]) assert.equal(isOwnedURL(url, origin), false, url);
});

test('navigation is restricted to the dashboard document', () => {
  assert.equal(isNavigationAllowed(`${origin}/?token=abc&desktop=1#settings`, origin), true);
  assert.equal(isNavigationAllowed(`${origin}/api/state`, origin), false);
  assert.equal(isNavigationAllowed('https://code.claude.com/docs/en/setup', origin), false);
});

test('official help links use strict HTTPS host and path boundaries', () => {
  for (const url of [
    'https://code.claude.com/docs/en/setup', 'https://docs.anthropic.com/en/docs/claude-code/setup',
    'https://developers.openai.com/codex/cli', 'https://github.com/Jingyi-Jia/agents-switcher',
    'https://github.com/Jingyi-Jia/agents-switcher/issues',
  ]) assert.equal(externalHelpURL(url), url);
  assert.equal(externalHelpURL('https://code.claude.com/docs/en/setup?token=SECRET#SECRET'), 'https://code.claude.com/docs/en/setup');
  for (const url of [
    'http://code.claude.com/docs/en/setup', 'https://code.claude.com:8443/docs/en/setup',
    'https://code.claude.com.evil.com/docs/en/setup', 'https://code.claude.com@evil.com/docs',
    'https://user:secret@code.claude.com/docs', 'https://code.claude.com/redirect',
    'https://code.claude.com/docs-evil', 'https://developers.openai.com/codex-evil',
    'https://github.com/Jingyi-Jia/agents-switcher-evil', 'https://github.com/Other/repository',
    'https://github.com/Jingyi-Jia/agents-switcher/../../Other/repo',
    'https://code.claude.com/docs/%2f%2fevil.com', 'mailto:user@example.com',
    'javascript:alert(1)', 'file:///etc/passwd', 'not a url',
  ]) assert.equal(externalHelpURL(url), null, url);
});

test('dashboard URL rejects invalid port and token values without echoing them', () => {
  const token = 'a'.repeat(64);
  assert.equal(dashboardURL(12345, token), `http://127.0.0.1:12345/?token=${token}&desktop=1`);
  for (const port of [0, -1, 65536, 1.5, '12345', NaN]) assert.throws(() => dashboardURL(port, token), /Invalid desktop connection/);
  for (const value of ['', 'SECRET', 'a'.repeat(63), `${token}&inject=true`, undefined]) {
    assert.throws(() => dashboardURL(12345, value), (error) => !error.message.includes('SECRET'));
  }
});

test('session denies permissions, devices, downloads, and non-owned requests', () => {
  const session = new EventEmitter();
  session.setPermissionRequestHandler = (handler) => { session.request = handler; };
  session.setPermissionCheckHandler = (handler) => { session.check = handler; };
  session.setDevicePermissionHandler = (handler) => { session.device = handler; };
  session.webRequest = { onBeforeRequest: (filter, handler) => {
    assert.deepEqual(filter.urls, ['<all_urls>']);
    session.network = handler;
  } };
  secureSession(session, origin);
  session.request(null, 'clipboard-read', (allowed) => assert.equal(allowed, false));
  assert.equal(session.check(), false);
  assert.equal(session.device(), false);
  let prevented = false;
  session.emit('will-download', { preventDefault: () => { prevented = true; } });
  assert.equal(prevented, true);
  session.network({ url: `${origin}/api/state` }, ({ cancel }) => assert.equal(cancel, false));
  session.network({ url: 'https://code.claude.com/docs/en/setup' }, ({ cancel }) => assert.equal(cancel, true));
});

test('web contents denies new windows and webviews and opens only validated help', () => {
  const contents = new EventEmitter();
  contents.setWindowOpenHandler = (handler) => { contents.open = handler; };
  const opened = [];
  secureWebContents(contents, origin, async (url) => { opened.push(url); });
  assert.deepEqual(contents.open({ url: 'https://evil.com/' }), { action: 'deny' });
  assert.deepEqual(contents.open({ url: 'https://code.claude.com/docs/en/setup?token=PRIVATE' }), { action: 'deny' });
  assert.deepEqual(opened, ['https://code.claude.com/docs/en/setup']);
  for (const [eventName, details] of [
    ['will-attach-webview', {}],
    ['will-frame-navigate', { url: 'https://evil.com/', isMainFrame: true }],
    ['will-redirect', { url: 'https://code.claude.com/docs/en/setup', isMainFrame: true }],
    ['will-frame-navigate', { url: `${origin}/`, isMainFrame: false }],
  ]) {
    let prevented = false;
    contents.emit(eventName, { ...details, preventDefault: () => { prevented = true; } });
    assert.equal(prevented, true, eventName);
  }
  let prevented = false;
  contents.emit('will-frame-navigate', { url: `${origin}/`, isMainFrame: true, preventDefault: () => { prevented = true; } });
  assert.equal(prevented, false);
  contents.emit('will-frame-navigate', { url: 'https://developers.openai.com/codex/cli', isMainFrame: true, preventDefault: () => { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(opened.at(-1), 'https://developers.openai.com/codex/cli');
});
