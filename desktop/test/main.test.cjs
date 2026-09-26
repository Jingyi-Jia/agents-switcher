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

function harness({ singleInstance = true, startError, runtime = {}, confirm = 1, stopForUpdate } = {}) {
  const state = { windows: [], backends: [], dialogs: [], external: [], exits: [], partitions: [], ipc: new Map(), updates: [], nativeQuits: 0 };
  const app = new EventEmitter();
  Object.assign(app, {
    isPackaged: true,
    name: '',
    setName: (name) => { app.name = name; },
    requestSingleInstanceLock: () => singleInstance,
    whenReady: () => Promise.resolve(),
    getVersion: () => '1.0.0',
    setAboutPanelOptions: () => {},
    quit: () => {
      let prevented = false;
      app.emit('before-quit', { preventDefault() { prevented = true; } });
      if (!prevented) state.nativeQuits += 1;
    },
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
    async stopForUpdate() {
      this.updateStopCalled = true;
      if (stopForUpdate) await stopForUpdate();
      await this.stop();
      this.cleanExit = true;
    }
    forceKill() { this.killed = true; }
  }
  class BrowserWindow extends EventEmitter {
    constructor(options) {
      super();
      this.options = options;
      this.destroyed = false;
      this.webContents = new EventEmitter();
      this.webContents.setWindowOpenHandler = () => {};
      this.webContents.isDestroyed = () => this.destroyed;
      this.webContents.getURL = () => this.loadedURL;
      this.webContents.mainFrame = { url: '', send: (channel, value) => state.updates.push({ channel, value }) };
      state.windows.push(this);
    }
    async loadURL(url) { this.loadedURL = url; this.webContents.mainFrame.url = url; }
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
      showMessageBox: async (options) => { state.dialogs.push(options); return { response: options.title === 'Install Agent Switch update' ? confirm : 1 }; },
      showErrorBox: (title, message) => state.dialogs.push({ title, message }),
    },
    shell: { openExternal: async (url) => state.external.push(url) },
    ipcMain: { handle: (channel, listener) => state.ipc.set(channel, listener) },
    session: { fromPartition: (partition, options) => {
      state.partitions.push({ partition, options });
      return Object.assign(new EventEmitter(), {
        setPermissionRequestHandler() {}, setPermissionCheckHandler() {}, setDevicePermissionHandler() {},
        webRequest: { onBeforeRequest() {} },
      });
    } },
  };
  const process = Object.assign(new EventEmitter(), { platform: 'linux', arch: 'x64', resourcesPath: '/packaged/resources' });
  const localRequire = createRequire(main);
  vm.runInNewContext(fs.readFileSync(main, 'utf8'), {
    require: (name) => name === 'electron' ? electron : name === './backend.cjs' ? { Backend, BackendError }
      : name === './updater-runtime.cjs' ? { createUpdateRuntime: async () => runtime } : localRequire(name),
    process, __dirname: path.dirname(main), setTimeout, clearTimeout, AbortController, URL,
  });
  const invoke = method => {
    const contents = state.windows.at(-1).webContents;
    return state.ipc.get(`agent-switch:update:${method}`)({ sender: contents, senderFrame: contents.mainFrame });
  };
  return { ...state, app, state, invoke };
}

async function settle() {
  await new Promise(resolve => setImmediate(resolve));
}

test('packaged main creates only a sandboxed, isolated, non-privileged renderer', async () => {
  const { state, app } = harness();
  await settle();
  assert.equal(state.windows.length, 1);
  const window = state.windows[0];
  const prefs = window.options.webPreferences;
  for (const key of ['contextIsolation', 'sandbox', 'webSecurity']) assert.equal(prefs[key], true, key);
  for (const key of ['nodeIntegration', 'allowRunningInsecureContent', 'webviewTag', 'devTools']) assert.equal(prefs[key], false, key);
  assert.equal(prefs.preload, path.resolve(__dirname, '../src/preload.cjs'));
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

function updateFixture() {
  const updater = new EventEmitter();
  const info = { version: '1.2.0', files: [{ url: 'Agent-Switch-1.2.0-linux-x86_64.AppImage', size: 123, sha512: Buffer.alloc(64).toString('base64') }] };
  updater.checkForUpdates = async () => ({ isUpdateAvailable: true, updateInfo: info });
  updater.downloadUpdate = async () => updater.emit('update-downloaded', info);
  return updater;
}

test('install waits for clean service exit and lets the native updater quit normally', async () => {
  let finishStop;
  const updater = updateFixture();
  const { state, invoke, app } = harness({ runtime: { updater }, confirm: 0,
    stopForUpdate: () => new Promise(resolve => { finishStop = resolve; }) });
  updater.quitAndInstall = (...args) => {
    assert.equal(state.backends[0].cleanExit, true);
    assert.deepEqual(args, [false, true]);
    state.installs = (state.installs || 0) + 1;
    app.quit();
  };
  await settle();
  await invoke('check'); await invoke('download');
  assert.equal(state.installs, undefined);
  const install = invoke('install');
  await settle();
  assert.equal(state.backends[0].updateStopCalled, true);
  assert.equal(state.installs, undefined);
  assert.equal(state.dialogs[0].defaultId, 1);
  finishStop(); await install;
  assert.equal(state.installs, 1);
  assert.equal(state.nativeQuits, 1);
  assert.deepEqual(state.exits, []);
  assert.equal(state.trayDestroyed, true);
});

test('cancelled install and ordinary quit never install a downloaded update', async () => {
  const updater = updateFixture();
  updater.quitAndInstall = () => assert.fail('surprise installation');
  const { state, invoke, app } = harness({ runtime: { updater } });
  await settle(); await invoke('check'); await invoke('download');
  assert.equal((await invoke('install')).status, 'downloaded');
  assert.equal(state.backends[0].stops, 0);
  assert.equal(updater.autoInstallOnAppQuit, false);
  app.quit(); await settle();
  assert.equal(state.backends[0].stops, 1);
  assert.deepEqual(state.exits, [0]);
});

test('failed clean shutdown refuses installation and safely closes the app', async () => {
  const updater = updateFixture();
  updater.quitAndInstall = () => assert.fail('unsafe installation');
  const { state, invoke } = harness({ runtime: { updater }, confirm: 0,
    stopForUpdate: async () => { throw new Error('SECRET shutdown diagnostic'); } });
  await settle(); await invoke('check'); await invoke('download'); await invoke('install'); await settle();
  assert.match(state.dialogs.at(-1).message, /could not finish safely/);
  assert.equal(JSON.stringify(state.dialogs).includes('SECRET'), false);
  assert.deepEqual(state.exits, [0]);
  assert.equal(state.nativeQuits, 0);
});

test('quitting during the clean shutdown cancels the pending install', async () => {
  let finishStop;
  const updater = updateFixture();
  updater.quitAndInstall = () => assert.fail('install after ordinary quit');
  const { state, invoke, app } = harness({ runtime: { updater }, confirm: 0,
    stopForUpdate: () => new Promise(resolve => { finishStop = resolve; }) });
  await settle(); await invoke('check'); await invoke('download');
  const installing = invoke('install');
  await settle(); app.quit(); finishStop(); await installing; await settle();
  assert.equal(state.nativeQuits, 0);
  assert.deepEqual(state.exits, [0]);
});

test('native install errors do not claim an update applied or restart the backend', async () => {
  const updater = updateFixture();
  updater.quitAndInstall = () => updater.emit('error', new Error('SECRET update URL'));
  const { state, invoke } = harness({ runtime: { updater }, confirm: 0 });
  await settle(); await invoke('check'); await invoke('download'); await invoke('install'); await settle();
  assert.equal(state.backends.length, 1);
  assert.equal(state.nativeQuits, 0);
  assert.equal(JSON.stringify(state.dialogs).includes('SECRET'), false);
  assert.match(state.dialogs.at(-1).message, /no successful update has been confirmed/);
  assert.deepEqual(state.exits, [0]);
});

test('the native update menu opens Settings and checks only on deliberate selection', async () => {
  const updater = updateFixture();
  const check = updater.checkForUpdates;
  let checks = 0;
  updater.checkForUpdates = () => { checks += 1; return check(); };
  updater.downloadUpdate = () => assert.fail('menu selection must not download');
  const { state, app } = harness({ runtime: { updater } });
  await settle(); assert.equal(checks, 0);
  state.menu.find(item => item.label === 'Help').submenu.find(item => item.label === 'Check for Updates…').click();
  await settle();
  assert.equal(checks, 1);
  assert.equal(new URL(state.windows[0].loadedURL).hash, '#settings');
  assert.equal(state.external.length, 0);
  assert.equal(state.backends.length, 1);
  app.quit(); await settle();
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

test('tray and application menus navigate only within the owned dashboard', async () => {
  const { state, app } = harness();
  await settle();
  const window = state.windows[0];
  for (const [label, hash] of [['Accounts', '#accounts'], ['Usage dashboard', '#usage'], ['Settings', '#settings']]) {
    state.trayMenu.find(item => item.label === label).click();
    await settle();
    const url = new URL(window.loadedURL);
    assert.equal(url.origin, 'http://127.0.0.1:12345');
    assert.equal(url.pathname, '/');
    assert.equal(url.hash, hash);
    assert.equal(url.searchParams.get('token'), 'a'.repeat(64));
  }
  const view = state.menu.find(item => item.label === 'View');
  for (const label of ['Accounts', 'Usage', 'Settings']) {
    const action = view.submenu.find(item => item.label === label);
    assert.ok(action.accelerator);
    action.click();
    await settle();
    assert.equal(new URL(window.loadedURL).hash, '#' + label.toLowerCase());
  }
  assert.equal(state.external.length, 0);
  assert.equal(state.backends.length, 1);
  app.quit();
  await settle();
});
