'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const { BackendError } = require('../src/backend.cjs');

const main = path.resolve(__dirname, '../src/main.cjs');

function harness({ singleInstance = true, startError } = {}) {
  const state = { windows: [], backends: [], dialogs: [], external: [], exits: [], partitions: [] };
  const app = new EventEmitter();
  Object.assign(app, {
    isPackaged: true,
    name: '',
    setName: (name) => { app.name = name; },
    requestSingleInstanceLock: () => singleInstance,
    whenReady: () => Promise.resolve(),
    getVersion: () => '1.0.0',
    setAboutPanelOptions: () => {},
    quit: () => app.emit('before-quit', { preventDefault() {} }),
    exit: (code) => state.exits.push(code),
  });
  class Backend extends EventEmitter {
    constructor(options) {
      super();
      this.options = options;
      this.state = 'idle';
      this.stops = 0;
      state.backends.push(this);
    }
    async start() {
      if (startError) throw startError;
      this.state = 'running';
      return `http://127.0.0.1:12345/?token=${'a'.repeat(64)}&desktop=1`;
    }
    async stop() { this.state = 'stopped'; this.stops += 1; }
    forceKill() { this.killed = true; }
  }
  class BrowserWindow extends EventEmitter {
    constructor(options) {
      super();
      this.options = options;
      this.destroyed = false;
      this.webContents = new EventEmitter();
      this.webContents.setWindowOpenHandler = () => {};
      state.windows.push(this);
    }
    async loadURL() {}
    isDestroyed() { return this.destroyed; }
    isMinimized() { return false; }
    show() { this.shown = true; }
    focus() { this.focused = true; }
    destroy() { this.destroyed = true; this.emit('closed'); app.emit('window-all-closed'); }
  }
  class Tray extends EventEmitter {
    setToolTip(value) { state.tooltip = value; }
    setContextMenu(value) { state.trayMenu = value; }
    destroy() { state.trayDestroyed = true; }
  }
  const electron = {
    app, BrowserWindow, Tray,
    Menu: { buildFromTemplate: (value) => value, setApplicationMenu: (value) => { state.menu = value; } },
    nativeImage: { createFromPath: () => ({ setTemplateImage() {} }) },
    dialog: {
      showMessageBox: async (options) => { state.dialogs.push(options); return { response: 1 }; },
      showErrorBox: (title, message) => state.dialogs.push({ title, message }),
    },
    shell: { openExternal: async (url) => state.external.push(url) },
    session: { fromPartition: (partition, options) => {
      state.partitions.push({ partition, options });
      return Object.assign(new EventEmitter(), {
        setPermissionRequestHandler() {}, setPermissionCheckHandler() {}, setDevicePermissionHandler() {},
        webRequest: { onBeforeRequest() {} },
      });
    } },
  };
  const process = Object.assign(new EventEmitter(), { platform: 'linux', resourcesPath: '/packaged/resources' });
  const localRequire = createRequire(main);
  vm.runInNewContext(fs.readFileSync(main, 'utf8'), {
    require: (name) => name === 'electron' ? electron : name === './backend.cjs' ? { Backend, BackendError } : localRequire(name),
    process, __dirname: path.dirname(main), setTimeout, clearTimeout, AbortController, URL,
  });
  return { ...state, app, state };
}

async function settle() {
  for (let index = 0; index < 12; index += 1) await Promise.resolve();
}

test('packaged main creates only a sandboxed, isolated, non-privileged renderer', async () => {
  const { state, app } = harness();
  await settle();
  assert.equal(state.windows.length, 1);
  const window = state.windows[0];
  const prefs = window.options.webPreferences;
  for (const key of ['contextIsolation', 'sandbox', 'webSecurity']) assert.equal(prefs[key], true, key);
  for (const key of ['nodeIntegration', 'allowRunningInsecureContent', 'webviewTag', 'devTools']) assert.equal(prefs[key], false, key);
  assert.equal(prefs.preload, undefined);
  assert.equal(state.partitions[0].partition.startsWith('persist:'), false);
  assert.equal(state.partitions[0].options.cache, false);
  assert.equal(state.backends[0].options.isPackaged, true);
  assert.equal(window.shown, true);
  window.focused = false;
  app.emit('second-instance');
  assert.equal(window.focused, true);
  app.quit();
  await settle();
  assert.equal(state.backends[0].stops, 1);
  assert.equal(state.trayDestroyed, true);
  assert.deepEqual(state.exits, [0]);
});

test('closing the last window quits and stops the backend', async () => {
  const { state } = harness();
  await settle();
  state.windows[0].destroy();
  await settle();
  assert.equal(state.backends[0].stops, 1);
  assert.deepEqual(state.exits, [0]);
});

test('backend failure destroys the stale window before presenting recovery', async () => {
  const { state } = harness();
  await settle();
  state.backends[0].emit('failure', new BackendError('unexpected_exit'));
  assert.equal(state.windows[0].destroyed, true);
  await settle();
  assert.equal(state.dialogs.length, 1);
  assert.equal(state.dialogs[0].buttons[0], 'Try again');
  assert.equal(state.dialogs[0].buttons[1], 'Quit');
  assert.equal(state.dialogs[0].detail.includes('stopped unexpectedly'), true);
  assert.deepEqual(state.exits, [0]);
});

test('renderer crash destroys the stale window and stops the backend', async () => {
  const { state } = harness();
  await settle();
  state.windows[0].webContents.emit('render-process-gone', {}, { reason: 'crashed' });
  assert.equal(state.windows[0].destroyed, true);
  await settle();
  assert.equal(state.dialogs.length, 1);
  assert.deepEqual(state.exits, [0]);
});

test('startup recovery does not expose raw exceptions or create an interactive window', async () => {
  const { state } = harness({ startError: new Error('SECRET token URL credential path') });
  await settle();
  assert.equal(state.windows.length, 0);
  assert.equal(state.dialogs.length, 1);
  assert.equal(JSON.stringify(state.dialogs).includes('SECRET'), false);
  assert.deepEqual(state.exits, [0]);
});

test('a second instance creates no window or backend', async () => {
  const { state } = harness({ singleInstance: false });
  await settle();
  assert.equal(state.windows.length, 0);
  assert.equal(state.backends.length, 0);
});
