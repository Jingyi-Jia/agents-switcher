'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { TrayPopover, popoverBounds, DOCUMENT_URL } = require('../src/tray-popover.cjs');
const { quotaModel } = require('../src/tray-dashboard.cjs');

function harness({ load, constructError } = {}) {
  const state = { windows: [], sessions: [], messages: [], actions: [], ipc: new Map(), allowed: true, fallbacks: 0 };
  class BrowserWindow extends EventEmitter {
    constructor(options) {
      super();
      if (constructError) throw constructError;
      this.options = options;
      this.webContents = Object.assign(new EventEmitter(), { isDestroyed: () => this.destroyed,
        getURL: () => this.url, setWindowOpenHandler: handler => { this.open = handler; },
        mainFrame: { url: '', send: (channel, model) => state.messages.push({ channel, model }) } });
      state.windows.push(this);
    }
    isDestroyed() { return Boolean(this.destroyed); }
    setMenu(menu) { this.menu = menu; }
    async loadFile(file) {
      this.file = file;
      this.url = DOCUMENT_URL;
      this.webContents.mainFrame.url = DOCUMENT_URL;
      await load?.();
    }
    show() { this.shown = true; }
    focus() { this.focused = true; }
    destroy() { this.destroyed = true; this.emit('closed'); }
  }
  const controller = new TrayPopover({ BrowserWindow,
    session: { fromPartition: (partition, options) => {
      const session = Object.assign(new EventEmitter(), { partition, options,
        setPermissionRequestHandler: fn => { session.request = fn; },
        setPermissionCheckHandler: fn => { session.check = fn; },
        setDevicePermissionHandler: fn => { session.device = fn; },
        webRequest: { onBeforeRequest: (_filter, fn) => { session.network = fn; } } });
      state.sessions.push(session);
      return session;
    } },
    screen: { getCursorScreenPoint: () => ({ x: -800, y: 24 }),
      getDisplayNearestPoint: point => { state.point = point; return { workArea: { x: -1920, y: 24, width: 1920, height: 1056 } }; } },
    ipcMain: { handle: (channel, fn) => state.ipc.set(channel, fn) },
    navigate: action => state.actions.push(action), fallback: () => { state.fallbacks += 1; },
    canOpen: () => state.allowed,
  });
  const event = () => ({ sender: controller.window.webContents, senderFrame: controller.window.webContents.mainFrame });
  return { state, controller, event,
    ready: (...args) => state.ipc.get('agent-switch:tray:ready')(event(), ...args),
    action: (...args) => state.ipc.get('agent-switch:tray:action')(event(), ...args) };
}

test('popover is lazy, sandboxed, focused and destroyed on blur, Escape, toggle and renderer failure', async () => {
  const { controller, state, ready } = harness();
  const model = quotaModel({ claude: { available: true, accounts: [{ alias: 'Synthetic' }] } });
  controller.update(model);
  assert.equal(state.windows.length, 0);
  assert.equal(state.sessions.length, 0);
  for (const dismiss of ['blur', 'Escape', 'toggle', 'render-process-gone']) {
    await controller.toggle({ x: -1000, y: 0, width: 22, height: 22 });
    const window = controller.window;
    assert.equal(window.shown, true);
    assert.equal(window.focused, true);
    assert.equal(window.menu, null);
    const prefs = window.options.webPreferences;
    for (const key of ['sandbox', 'contextIsolation', 'webSecurity']) assert.equal(prefs[key], true);
    for (const key of ['nodeIntegration', 'allowRunningInsecureContent', 'webviewTag', 'devTools']) assert.equal(prefs[key], false);
    assert.equal(prefs.preload, path.resolve(__dirname, '../src/tray-preload.cjs'));
    ready();
    assert.deepEqual(state.messages.at(-1), { channel: 'agent-switch:tray:model', model });
    if (dismiss === 'blur') window.emit('blur');
    if (dismiss === 'Escape') {
      let prevented = false;
      window.webContents.emit('before-input-event', { preventDefault: () => { prevented = true; } }, { key: 'Escape' });
      assert.equal(prevented, true);
    }
    if (dismiss === 'toggle') await controller.toggle();
    if (dismiss === 'render-process-gone') window.webContents.emit('render-process-gone');
    assert.equal(controller.window, null);
    assert.equal(window.destroyed, true);
  }
  assert.equal(state.sessions.length, 1);
  assert.equal(state.sessions[0].partition.startsWith('persist:'), false);
  assert.deepEqual(state.sessions[0].options, { cache: false });
});

test('only the live popover main frame can request a model or fixed navigation, never arbitrary paths or quit', async () => {
  const { controller, state, event, ready, action } = harness();
  await controller.toggle();
  const valid = event();
  for (const bad of [{ ...valid, sender: {} }, { ...valid, senderFrame: { url: DOCUMENT_URL } },
    { ...valid, senderFrame: null }, {}]) {
    state.ipc.get('agent-switch:tray:ready')(bad);
    state.ipc.get('agent-switch:tray:action')(bad, 'app');
  }
  ready('SECRET');
  for (const unsafe of ['quit', 'switch', 'refresh', 'login', 'https://example.test/', { action: 'app' }, undefined]) action(unsafe);
  action('app', 'SECRET');
  assert.deepEqual(state.actions, []);
  assert.deepEqual(state.messages, []);
  controller.window.webContents.mainFrame.url = `${DOCUMENT_URL}?injected`;
  ready(); action('app');
  assert.deepEqual(state.messages, []);
  controller.window.webContents.mainFrame.url = DOCUMENT_URL;
  controller.window.url = 'file:///etc/passwd';
  ready(); action('app');
  assert.deepEqual(state.actions, []);
  controller.window.url = DOCUMENT_URL;
  ready();
  assert.equal(state.messages.length, 1);
  action('settings');
  assert.deepEqual(state.actions, ['settings']);
  assert.equal(controller.window, null);
  state.ipc.get('agent-switch:tray:action')(valid, 'app');
  assert.deepEqual(state.actions, ['settings']);
  for (const destination of ['app', 'accounts', 'usage', 'dismiss']) {
    await controller.toggle();
    action(destination);
    assert.equal(controller.window, null);
  }
  assert.deepEqual(state.actions, ['settings', 'app', 'accounts', 'usage']);
});

test('session denies all network, unrelated files, permissions, downloads, windows and every renderer navigation', async () => {
  const { controller, state } = harness();
  await controller.toggle();
  const session = state.sessions[0];
  session.request(null, 'camera', allowed => assert.equal(allowed, false));
  assert.equal(session.check(), false);
  assert.equal(session.device(), false);
  let prevented = false;
  session.emit('will-download', { preventDefault: () => { prevented = true; } });
  assert.equal(prevented, true);
  for (const value of [DOCUMENT_URL, DOCUMENT_URL.replace('.html', '.css'), DOCUMENT_URL.replace('.html', '.js')]) {
    session.network({ url: value }, result => assert.equal(result.cancel, false));
  }
  for (const value of ['http://127.0.0.1:12345/api/state', 'https://example.test/', 'file:///etc/passwd',
    DOCUMENT_URL + '?token=SECRET', DOCUMENT_URL.replace('tray-popover.html', 'preload.cjs'), 'data:text/html,hello', 'ws://127.0.0.1/']) {
    session.network({ url: value }, result => assert.equal(result.cancel, true));
  }
  const window = controller.window;
  assert.deepEqual(window.open({ url: 'https://example.test/' }), { action: 'deny' });
  for (const name of ['will-attach-webview', 'will-navigate', 'will-frame-navigate', 'will-redirect']) {
    let denied = false;
    window.webContents.emit(name, { preventDefault: () => { denied = true; }, url: DOCUMENT_URL, isMainFrame: true });
    assert.equal(denied, true, name);
  }
  controller.clear();
});

test('clear drops sensitive display state and cancels an in-flight load without showing or falling back', async () => {
  let finish;
  const { controller, state } = harness({ load: () => new Promise(resolve => { finish = resolve; }) });
  controller.update(quotaModel({ claude: { available: true, accounts: [{ alias: 'Private label' }] } }));
  const opening = controller.toggle();
  const window = controller.window;
  state.allowed = false;
  controller.clear();
  finish();
  await opening;
  assert.equal(window.destroyed, true);
  assert.equal(window.shown, undefined);
  assert.equal(JSON.stringify(controller.model).includes('Private label'), false);
  assert.equal(state.fallbacks, 0);
  await controller.toggle();
  assert.equal(state.windows.length, 1);
});

test('construction or loading failure cleans up and leaves the native fallback usable', async () => {
  for (const options of [{ constructError: new Error('SECRET') }, { load: async () => { throw new Error('SECRET'); } }]) {
    const { controller, state } = harness(options);
    await controller.toggle();
    assert.equal(controller.window, null);
    assert.equal(state.fallbacks, 1);
    assert.ok(state.windows.every(window => window.destroyed));
  }
});

test('a cancelled load rejection never reopens the native menu or destroys a newer popover', async () => {
  const pending = [];
  const { controller, state } = harness({ load: () => new Promise((resolve, reject) => pending.push({ resolve, reject })) });
  const first = controller.toggle();
  await controller.toggle();
  assert.equal(state.windows[0].destroyed, true);
  const second = controller.toggle();
  const current = controller.window;
  pending[0].reject(new Error('load cancelled'));
  await first;
  assert.equal(state.fallbacks, 0);
  assert.equal(controller.window, current);
  pending[1].resolve();
  await second;
  assert.equal(current.shown, true);
  const third = controller.toggle();
  await third;
  assert.equal(current.destroyed, true);
  const fourth = controller.toggle();
  controller.clear();
  pending[2].reject(new Error('load cancelled by clear'));
  await fourth;
  assert.equal(state.fallbacks, 0);
});

test('placement clamps to every display edge, negative coordinates and small work areas', () => {
  for (const area of [{ x: 0, y: 24, width: 1920, height: 1056 },
    { x: -1920, y: -1080, width: 1920, height: 1080 }, { x: 100, y: 100, width: 300, height: 280 }]) {
    for (const [x, y] of [[area.x, area.y], [area.x + area.width, area.y],
      [area.x, area.y + area.height], [area.x + area.width, area.y + area.height]]) {
      const bounds = popoverBounds({ x, y, width: 22, height: 22 }, area);
      assert.ok(bounds.x >= area.x && bounds.y >= area.y);
      assert.ok(bounds.x + bounds.width <= area.x + area.width);
      assert.ok(bounds.y + bounds.height <= area.y + area.height);
      assert.ok(bounds.width <= 396 && bounds.height <= 610);
      assert.ok(Object.values(bounds).every(Number.isInteger));
    }
  }
  assert.equal(popoverBounds({ x: 800, y: 0, width: 22, height: 22 }, { x: 0, y: 24, width: 1920, height: 1056 }).y, 32);
  assert.equal(popoverBounds({ x: 800, y: 1060, width: 22, height: 22 }, { x: 0, y: 24, width: 1920, height: 1036 }).y, 442);
});

test('empty tray bounds use cursor display rather than placing the popover on the primary display', async () => {
  const { controller, state } = harness();
  await controller.toggle({ x: 0, y: 0, width: 0, height: 0 });
  assert.deepEqual(state.point, { x: -800, y: 24 });
  assert.ok(controller.window.options.x < 0);
  controller.dismiss();
});

test('minimal preload exposes no backend address, updater, raw IPC or subframe bridge', () => {
  const script = fs.readFileSync(path.join(__dirname, '../src/tray-preload.cjs'), 'utf8');
  for (const isMainFrame of [false, true]) {
    let api;
    const calls = [], listeners = new Map();
    vm.runInNewContext(script, { process: { isMainFrame }, require: name => {
      assert.equal(name, 'electron');
      return { contextBridge: { exposeInMainWorld: (key, value) => { assert.equal(key, 'agentSwitchTray'); api = value; } },
        ipcRenderer: { invoke: (...args) => { calls.push(args); return Promise.resolve(); },
          on: (key, fn) => listeners.set(key, fn), removeListener: key => listeners.delete(key) } };
    } });
    if (!isMainFrame) { assert.equal(api, undefined); continue; }
    assert.deepEqual(Object.keys(api).sort(), ['navigate', 'onModel']);
    assert.equal(Object.isFrozen(api), true);
    let received;
    const unsubscribe = api.onModel((...args) => { received = args; });
    assert.deepEqual(calls, [['agent-switch:tray:ready']]);
    const model = { status: 'loading' };
    listeners.get('agent-switch:tray:model')({ sender: 'SECRET' }, model);
    assert.deepEqual(received, [model]);
    for (const action of ['quit', 'switch', 'https://example.test/', { token: 'SECRET' }]) api.navigate(action);
    assert.equal(calls.length, 1);
    api.navigate('accounts', 'SECRET');
    assert.deepEqual(calls.at(-1), ['agent-switch:tray:action', 'accounts']);
    unsubscribe();
    assert.equal(listeners.size, 0);
    assert.throws(() => api.onModel(null), /callback/);
  }
});
