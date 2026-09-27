'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const { isolatedEnvironment, validateRenderer, verifyWindowState, closeNativeWindow, quitApplication } = require('../scripts/smoke_app.cjs');

test('packaged app smoke isolates all account paths and strips ambient credentials and overrides', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-switch-smoke-env-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const env = isolatedEnvironment(root, { PATH: '/usr/bin', DISPLAY: ':99', HOME: '/real-home', USERPROFILE: '/real-home',
    OPENAI_API_KEY: 'synthetic', CLAUDE_CONFIG_DIR: '/real-claude', CODEX_HOME: '/real-codex',
    CLAUDE_CODE_OAUTH_TOKEN: 'synthetic', GH_TOKEN: 'synthetic', PYTHONPATH: '/unsafe',
    ELECTRON_RUN_AS_NODE: '1', AGENT_SWITCH_BACKEND: '/unsafe/backend' });
  assert.equal(env.PATH, '/usr/bin');
  assert.equal(env.DISPLAY, ':99');
  for (const name of ['OPENAI_API_KEY', 'CLAUDE_CODE_OAUTH_TOKEN', 'GH_TOKEN', 'PYTHONPATH', 'ELECTRON_RUN_AS_NODE', 'AGENT_SWITCH_BACKEND']) {
    assert.equal(env[name], undefined);
  }
  for (const name of ['HOME', 'USERPROFILE', 'CLAUDE_CONFIG_DIR', 'CODEX_HOME', 'APPDATA', 'LOCALAPPDATA',
    'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME', 'XDG_RUNTIME_DIR', 'XDG_CACHE_HOME', 'TMPDIR', 'TEMP', 'TMP']) {
    assert.equal(path.dirname(env[name]), root);
    assert.equal(fs.statSync(env[name]).isDirectory(), true);
    if (process.platform !== 'win32') assert.equal(fs.statSync(env[name]).mode & 0o777, 0o700);
  }
  assert.equal(env.PYTHON_KEYRING_BACKEND, 'keyring.backends.null.Keyring');
});

test('native app smoke demands real manual controls, empty accounts, and no executable actions', () => {
  const result = {version: '1.2.1', mode: 'manual', status: 'idle', supported: true, checkEnabled: true,
    updatesVisible: true, settingsVisible: true, executableActionsHidden: true, releaseHidden: true, accountsIsolated: true, windowClose: 'hide'};
  validateRenderer(result, '1.2.1', 'community');
  for (const mutation of [{version: '1.2.0'}, {mode: 'install'}, {status: 'checking'}, {supported: false},
    {checkEnabled: false}, {updatesVisible: false}, {settingsVisible: false}, {executableActionsHidden: false},
    {releaseHidden: false}, {accountsIsolated: false}, {windowClose: 'quit'}, {windowClose: undefined}]) {
    assert.throws(() => validateRenderer({...result, ...mutation}, '1.2.1', 'community'));
  }
  for (const distribution of ['preview', 'signed']) {
    validateRenderer({...result, mode: 'unsupported', status: 'unsupported', supported: false, checkEnabled: false}, '1.2.1', distribution);
  }
});

test('window smoke requires the same isolated renderer, backend and automation while hidden and reopened', async () => {
  const state = {desktop: {windowClose: 'hide'},
    claude: {available: true, accounts: [], auto: {mode: 'dry-run'}},
    codex: {available: true, accounts: [], auto: {mode: 'dry-run'}}};
  const hidden = async () => ({result: {value: {visibility: 'hidden', sameDocument: true}}});
  for (const visibility of ['hidden', 'visible']) {
    await verifyWindowState(async (method, params) => {
      assert.equal(method, 'Runtime.evaluate');
      assert.equal(params.awaitPromise, undefined);
      assert.equal(params.returnByValue, true);
      return {result: {value: {sameDocument: true, visibility}}};
    }, async () => state, visibility);
  }
  await assert.rejects(verifyWindowState(async () => ({result: {value: {sameDocument: false}}}), async () => state, 'hidden'), /did not retain/);
  for (const mutation of [{desktop: {windowClose: 'quit'}}, {claude: {...state.claude, available: false}},
    {codex: {...state.codex, accounts: [{}]}}, {claude: {...state.claude, auto: {mode: 'stopped'}}}]) {
    await assert.rejects(verifyWindowState(hidden, async () => ({...state, ...mutation}), 'hidden'), /did not retain/);
  }
  await assert.rejects(verifyWindowState(async () => ({exceptionDetails: {text: 'SECRET'}}), async () => state, 'hidden'), /did not retain/);
});

test('smoke quits through native Browser.close without waiting for its nonexistent protocol response', async t => {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => server.close());
  const port = server.address().port;
  let finish;
  const exited = new Promise(resolve => { finish = resolve; });
  let sent = false;
  const socket = {send(message) {
    assert.deepEqual(JSON.parse(message), {id: 0, method: 'Browser.close', params: {}});
    sent = true;
    server.close(() => finish({code: 0, signal: null}));
  }};
  await quitApplication(socket, exited, port);
  assert.equal(sent, true);
});

test('smoke uses the native cancellable close and demands the existing window stay alive but hidden', async () => {
  const vm = require('node:vm');
  const window = {close() { this.hidden = true; }, isDestroyed() { return false; }, isVisible() { return !this.hidden; }};
  await closeNativeWindow(async (method, params) => {
    assert.equal(method, 'Runtime.evaluate');
    assert.equal(params.returnByValue, true);
    const value = vm.runInNewContext(params.expression, {process: {mainModule: {require(name) {
      assert.equal(name, 'electron');
      return {BrowserWindow: {getAllWindows: () => [window]}};
    }}}});
    return {result: {value}};
  });
  assert.equal(window.hidden, true);
  for (const result of [{result: {value: false}}, {exceptionDetails: {text: 'SECRET'}}]) {
    await assert.rejects(closeNativeWindow(async () => result), /Native window close did not hide/);
  }
});

test('smoke rejects a failed exit or a backend left listening after native quit', async t => {
  const socket = {send() {}};
  for (const exit of [{code: 1, signal: null}, {code: null, signal: 'SIGTERM'}]) {
    await assert.rejects(quitApplication(socket, Promise.resolve(exit), 1), /did not quit cleanly/);
  }
  const server = net.createServer(connection => connection.end());
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => server.close());
  await assert.rejects(quitApplication(socket, Promise.resolve({code: 0, signal: null}), server.address().port), /remained reachable/);
});
