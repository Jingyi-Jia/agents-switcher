'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const http = require('node:http');
const { EventEmitter } = require('node:events');
const { isolatedEnvironment, validateRenderer, verifyWindowState, closeNativeWindow, closeFullScreenWindow, quitApplication, readJSON } = require('../scripts/smoke_app.cjs');

function localRequests(t) {
  const requests = [];
  t.mock.method(http, 'get', (options, respond) => {
    const request = new EventEmitter();
    request.destroy = () => { request.destroyed = true; };
    const response = Object.assign(new EventEmitter(), {statusCode: 200, setEncoding() {}});
    requests.push({request, response, options, respond: () => respond(response)});
    return request;
  });
  return requests;
}

test('state collection outlasts short debugger discovery and a full Windows process-probe budget', async t => {
  t.mock.timers.enable({apis: ['setTimeout']});
  const requests = localRequests(t);
  const discovery = readJSON(1234, '/json/list');
  const state = readJSON(5678, '/api/state', {'X-Auth-Token': 'SYNTHETIC_PRIVATE'});
  const expired = assert.rejects(discovery, /^Error: Debugger discovery request timed out after 3 seconds$/);
  assert.equal(requests[0].options.timeout, undefined);
  assert.equal(requests[1].options.timeout, undefined);
  assert.equal(requests[1].options.headers['X-Auth-Token'], 'SYNTHETIC_PRIVATE');
  t.mock.timers.tick(3000);
  await expired;
  assert.equal(requests[0].request.destroyed, true);
  assert.equal(requests[1].request.destroyed, undefined);
  t.mock.timers.tick(17000);
  assert.equal(requests[1].request.destroyed, undefined);
  requests[1].respond();
  requests[1].response.emit('data', '{"ok":true}');
  requests[1].response.emit('end');
  assert.deepEqual(await state, {ok: true});
  t.mock.timers.tick(30000);
  assert.equal(requests[1].request.destroyed, undefined);
});

test('both local request budgets are hard deadlines, including trickling response bodies', async t => {
  t.mock.timers.enable({apis: ['setTimeout']});
  const requests = localRequests(t);
  for (const [pathname, budget, phase] of [['/json/list', 3000, 'Debugger discovery'], ['/api/state', 30000, 'Backend state collection']]) {
    for (const bodyStarted of [false, true]) {
      const pending = readJSON(1234, pathname, {'X-Auth-Token': 'SYNTHETIC_PRIVATE'});
      const expired = assert.rejects(pending, new RegExp(`^Error: ${phase} request timed out after ${budget / 1000} seconds$`));
      const local = requests.at(-1);
      if (bodyStarted) local.respond();
      t.mock.timers.tick(budget - 1);
      assert.equal(local.request.destroyed, undefined);
      if (bodyStarted) local.response.emit('data', '{');
      t.mock.timers.tick(1);
      await expired;
      assert.equal(local.request.destroyed, true);
    }
  }
});

test('local request failures identify their phase without exposing credentials or debug diagnostics', async t => {
  const requests = localRequests(t);
  for (const [pathname, phase] of [['/json/list', 'Debugger discovery'], ['/api/state', 'Backend state collection']]) {
    for (const failure of ['request', 'response', 'aborted', 'invalid', 'http', 'oversized']) {
      const pending = readJSON(1234, pathname, {'X-Auth-Token': 'SYNTHETIC_PRIVATE'});
      const rejected = assert.rejects(pending, error => {
        assert.equal(error.message.startsWith(phase + ' '), true);
        assert.doesNotMatch(error.message, /SYNTHETIC_PRIVATE|ws:\/\/|1234/);
        return true;
      });
      const local = requests.at(-1);
      const diagnostic = new Error('SYNTHETIC_PRIVATE ws://127.0.0.1:1234/private');
      if (failure === 'request') local.request.emit('error', diagnostic);
      else {
        local.respond();
        if (failure === 'response') local.response.emit('error', diagnostic);
        else if (failure === 'aborted') local.response.emit('aborted');
        else {
          if (failure === 'http') local.response.statusCode = 500;
          local.response.emit('data', failure === 'oversized' ? 'x'.repeat(65537) : diagnostic.message);
          local.response.emit('end');
        }
      }
      await rejected;
      assert.equal(local.request.destroyed, true);
    }
  }
});

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

test('window visibility settles before spending the independent backend state budget', async () => {
  let commands = 0, reads = 0;
  await verifyWindowState(async () => {
    assert.equal(reads, 0);
    commands += 1;
    return {result: {value: {sameDocument: true, visibility: commands === 1 ? 'visible' : 'hidden'}}};
  }, async () => {
    reads += 1;
    return {desktop: {windowClose: 'hide'},
      claude: {available: true, accounts: [], auto: {mode: 'dry-run'}},
      codex: {available: true, accounts: [], auto: {mode: 'dry-run'}}};
  }, 'hidden');
  assert.equal(commands, 2);
  assert.equal(reads, 1);
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

test('full-screen smoke requires native exit before hide rather than merely eventual invisibility', async () => {
  const vm = require('node:vm');
  for (const hideBeforeExit of [false, true]) {
    const window = Object.assign(new EventEmitter(), {
      shown: true, fullScreen: false, requests: [],
      setFullScreen(value) {
        this.requests.push(value);
        setImmediate(() => { this.fullScreen = value; this.emit('enter-full-screen'); });
      },
      isFullScreen() { return this.fullScreen; },
      isVisible() { return this.shown; },
      isDestroyed() { return false; },
      close() {
        setImmediate(() => {
          if (hideBeforeExit) { this.shown = false; this.emit('hide'); }
          this.fullScreen = false;
          this.emit('leave-full-screen');
          if (!hideBeforeExit) { this.shown = false; this.emit('hide'); }
        });
      },
    });
    const smoke = closeFullScreenWindow(async (method, params) => {
      assert.equal(method, 'Runtime.evaluate');
      assert.equal(params.awaitPromise, true);
      assert.equal(params.returnByValue, true);
      const value = await vm.runInNewContext(params.expression, {setTimeout, clearTimeout, process: {mainModule: {require(name) {
        assert.equal(name, 'electron');
        return {BrowserWindow: {getAllWindows: () => [window]}};
      }}}});
      return {result: {value}};
    });
    if (hideBeforeExit) await assert.rejects(smoke, /did not leave full screen before hiding \(hidden-before-exit\)/);
    else await smoke;
    assert.deepEqual(window.requests, [true]);
    assert.equal(window.shown, false);
    assert.equal(window.eventNames().length, 0);
  }
  for (const result of [{result: {value: false}}, {result: {value: 'SECRET ws://127.0.0.1:1234/private'}}, {exceptionDetails: {text: 'SECRET'}}]) {
    await assert.rejects(closeFullScreenWindow(async () => result), error => {
      assert.match(error.message, /did not leave full screen before hiding \(evaluation-failed\)/);
      assert.doesNotMatch(error.message, /SECRET|ws:\/\/|1234/);
      return true;
    });
  }
});

for (const missing of ['enter-full-screen', 'hide']) {
  test(`full-screen smoke bounds a missing ${missing} event and removes its listeners`, async t => {
    t.mock.timers.enable({apis: ['setTimeout']});
    const vm = require('node:vm');
    const window = Object.assign(new EventEmitter(), {
      setFullScreen() { if (missing !== 'enter-full-screen') this.emit('enter-full-screen'); },
      isFullScreen() { return true; },
      isVisible() { return true; },
      close() {},
    });
    const rejected = assert.rejects(closeFullScreenWindow(async (method, params) => {
      const value = await vm.runInNewContext(params.expression, {setTimeout, clearTimeout, process: {mainModule: {require() {
        return {BrowserWindow: {getAllWindows: () => [window]}};
      }}}});
      return {result: {value}};
    }), new RegExp(`did not leave full screen before hiding \\(${missing === 'hide' ? 'hide' : 'enter'}-timeout\\)`));
    await new Promise(resolve => setImmediate(resolve));
    t.mock.timers.tick(10000);
    await rejected;
    assert.equal(window.eventNames().length, 0);
  });
}

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
