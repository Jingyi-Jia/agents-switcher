'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const { BackendError } = require('../src/backend.cjs');
const { TrayDashboard } = require('../src/tray-dashboard.cjs');

const main = path.resolve(__dirname, '../src/main.cjs');

function harness({ singleInstance = true, startError, runtime = {}, confirm = 1, stopForUpdate,
  platform = 'linux', stop, recoveryResponse = 1, trayError, trayRead = async () => ({}) } = {}) {
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
      if (!prevented) {
        state.nativeQuits += 1;
        for (const window of state.windows) if (!window.isDestroyed()) window.close();
      }
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
    async stop() {
      this.state = 'stopping'; this.stops += 1;
      if (stop) await stop();
      this.state = 'stopped';
    }
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
      this.fullScreenRequests = [];
      this.webContents = new EventEmitter();
      this.webContents.setWindowOpenHandler = () => {};
      this.webContents.isDestroyed = () => this.destroyed;
      this.webContents.getURL = () => this.loadedURL;
      this.webContents.mainFrame = { url: '', send: (channel, value) => state.updates.push({ channel, value }) };
      state.windows.push(this);
    }
    async loadURL(url) { this.loadedURL = url; this.webContents.mainFrame.url = url; }
    isDestroyed() { return this.destroyed; }
    isMinimized() { return Boolean(this.minimized); }
    isFullScreen() { return Boolean(this.fullScreen); }
    setFullScreen(value) { this.fullScreenRequests.push(value); }
    restore() { this.minimized = false; }
    show() { this.shown = true; }
    hide() { this.shown = false; this.focused = false; }
    focus() { this.focused = true; }
    close() {
      let prevented = false;
      this.emit('close', { preventDefault() { prevented = true; } });
      if (!prevented) this.destroy();
    }
    destroy() { this.destroyed = true; this.emit('closed'); app.emit('window-all-closed'); }
  }
  class Tray extends EventEmitter {
    constructor() { super(); if (trayError) throw trayError; state.tray = this; }
    setToolTip(value) { state.tooltip = value; }
    setContextMenu(value) { state.trayMenu = value; }
    popUpContextMenu() { state.trayPoppedUp = true; }
    destroy() { state.trayDestroyed = true; }
  }
  const powerMonitor = new EventEmitter();
  const electron = {
    app, BrowserWindow, Tray,
    powerMonitor,
    Menu: { buildFromTemplate: (value) => {
      const events = new EventEmitter();
      Object.defineProperties(value, { on: { value: events.on.bind(events) }, emit: { value: events.emit.bind(events) } });
      return value;
    }, setApplicationMenu: (value) => { state.menu = value; } },
    nativeImage: { createFromPath: () => ({ setTemplateImage() {} }) },
    dialog: {
      showMessageBox: async (options) => { state.dialogs.push(options); return { response: options.title === 'Install Agent Switch update' ? confirm : recoveryResponse }; },
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
  const process = Object.assign(new EventEmitter(), { platform, arch: 'x64', resourcesPath: '/packaged/resources' });
  const localRequire = createRequire(main);
  vm.runInNewContext(fs.readFileSync(main, 'utf8'), {
    require: (name) => name === 'electron' ? electron : name === './backend.cjs' ? { Backend, BackendError }
      : name === './tray-dashboard.cjs' ? { TrayDashboard: class extends TrayDashboard {
        constructor(render) { super(render, trayRead); state.trayDashboard = this; }
      } }
      : name === './updater-runtime.cjs' ? { createUpdateRuntime: async () => runtime } : localRequire(name),
    process, __dirname: path.dirname(main), setTimeout, clearTimeout, AbortController, URL,
  });
  const invoke = method => {
    const contents = state.windows.at(-1).webContents;
    return state.ipc.get(`agent-switch:update:${method}`)({ sender: contents, senderFrame: contents.mainFrame });
  };
  return { ...state, app, state, invoke, powerMonitor };
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

for (const platform of ['darwin', 'win32', 'linux']) {
  test(`${platform} window close hides without restarting or stopping the backend`, async () => {
    const { state, app } = harness({ platform });
    await settle();
    const window = state.windows[0];
    const address = window.loadedURL;
    for (const reopen of [() => app.emit('activate'), () => app.emit('second-instance'),
      () => {
        state.tray.emit('click');
        assert.equal(window.shown, false);
        state.trayMenu.find(item => item.label === 'Show app').click();
      }, () => state.trayMenu.find(item => item.label === 'Show app').click(),
      () => state.menu.find(item => item.label === 'File').submenu.find(item => item.label === 'Show app').click()]) {
      window.close();
      window.close();
      await settle();
      assert.equal(window.shown, false);
      assert.equal(window.destroyed, false);
      assert.equal(state.backends[0].state, 'running');
      assert.equal(state.backends[0].stops, 0);
      assert.equal(state.trayDestroyed, undefined);
      assert.deepEqual(state.exits, []);
      window.minimized = true;
      reopen();
      assert.equal(window.minimized, false);
      assert.equal(window.shown, true);
      assert.equal(window.focused, true);
      assert.equal(window.loadedURL, address);
      assert.equal(state.windows.length, 1);
      assert.equal(state.backends.length, 1);
      assert.equal(state.partitions.length, 1);
    }
    assert.match(state.tooltip, /close hides the window/);
    assert.match(state.tooltip, /Quit stops automation/);
    app.quit(); await settle();
  });

  for (const source of platform === 'darwin' ? ['tray', 'File', 'Agent Switch'] : ['tray', 'File']) {
    test(`${platform} ${source} Quit drains the hidden app before exiting`, async () => {
      let finish;
      const { state, app } = harness({ platform, stop: () => new Promise(resolve => { finish = resolve; }) });
      await settle();
      const window = state.windows[0];
      window.close();
      const menu = source === 'tray' ? state.trayMenu : state.menu.find(item => item.label === source).submenu;
      const quit = menu.find(item => item.label === 'Quit Agent Switch');
      if (source === 'Agent Switch') assert.equal(quit.accelerator, 'Command+Q');
      if (source === 'File' && platform !== 'darwin') assert.equal(quit.accelerator, 'Control+Q');
      quit.click();
      assert.equal(window.destroyed, true);
      assert.equal(state.backends[0].stops, 1);
      assert.equal(state.backends[0].state, 'stopping');
      assert.deepEqual(state.exits, []);
      app.emit('activate'); app.emit('second-instance'); app.quit();
      assert.equal(state.backends[0].stops, 1);
      finish(); await settle();
      assert.equal(state.backends[0].state, 'stopped');
      assert.equal(state.trayDestroyed, true);
      assert.deepEqual(state.exits, [0]);
    });
  }
}

test('an open tray menu stays stable while new usage waits for the next opening', async () => {
  const { state, app } = harness();
  await settle();
  const openMenu = state.trayMenu;
  openMenu.emit('menu-will-show');
  state.trayDashboard.render([{ label: 'Updated quota', enabled: false }]);
  assert.equal(state.trayMenu, openMenu);
  openMenu.emit('menu-will-close');
  await settle();
  assert.equal(state.trayMenu[0].label, 'Updated quota');
  app.quit(); await settle();
});

for (const newer of ['Fresh quota', 'Account usage unavailable · reconnect in the app']) {
  test(`closing a tray menu cannot overwrite ${newer} with an older queued snapshot`, async () => {
    const { state, app } = harness();
    await settle();
    const openMenu = state.trayMenu;
    openMenu.emit('menu-will-show');
    state.trayDashboard.render([{ label: 'Old quota · 100% left', enabled: false }]);
    openMenu.emit('menu-will-close');
    state.trayDashboard.render([{ label: newer, enabled: false }]);
    await settle();
    assert.equal(state.trayMenu[0].label, newer);
    app.quit(); await settle();
  });
}

test('macOS full-screen close waits for native exit before hiding and retains the backend', async () => {
  const { state, app } = harness({ platform: 'darwin' });
  await settle();
  const window = state.windows[0];
  const address = window.loadedURL;
  window.fullScreen = true;
  window.close();
  window.close();
  assert.equal(window.shown, true);
  assert.deepEqual(window.fullScreenRequests, [false]);
  window.fullScreen = false;
  window.close();
  await settle();
  assert.equal(window.shown, true);
  assert.deepEqual(window.fullScreenRequests, [false]);
  assert.equal(window.listenerCount('leave-full-screen'), 1);
  assert.equal(state.backends[0].stops, 0);
  window.emit('leave-full-screen');
  assert.equal(window.shown, false);
  assert.equal(window.destroyed, false);
  assert.equal(window.listenerCount('leave-full-screen'), 0);
  assert.equal(window.loadedURL, address);
  assert.equal(state.backends[0].state, 'running');
  assert.deepEqual(state.exits, []);
  app.emit('activate');
  assert.equal(window.shown, true);
  assert.equal(window.isFullScreen(), false);
  assert.equal(state.windows.length, 1);
  assert.equal(state.backends.length, 1);
  window.close();
  assert.equal(window.shown, false);
  assert.deepEqual(window.fullScreenRequests, [false]);
  app.quit(); await settle();
});

for (const source of ['activate', 'second-instance', 'tray', 'Show app', 'Settings']) {
  test(`macOS ${source} during full-screen exit cancels the pending hide`, async () => {
    const { state, app } = harness({ platform: 'darwin' });
    await settle();
    const window = state.windows[0];
    window.fullScreen = true;
    window.close();
    if (source === 'tray') {
      state.tray.emit('click');
      window.fullScreen = false;
      window.emit('leave-full-screen');
      assert.equal(window.shown, false);
      state.trayMenu.find(item => item.label === 'Show app').click();
    }
    else if (['Show app', 'Settings'].includes(source)) state.trayMenu.find(item => item.label === source).click();
    else app.emit(source);
    window.fullScreen = false;
    window.emit('leave-full-screen');
    assert.equal(window.shown, true);
    await settle();
    assert.equal(window.shown, true);
    assert.equal(window.focused, true);
    assert.deepEqual(window.fullScreenRequests, [false]);
    assert.equal(state.backends[0].stops, 0);
    window.close();
    assert.equal(window.shown, false);
    app.quit(); await settle();
  });
}

test('macOS close after a reopen request still waits for the in-flight full-screen exit', async () => {
  const { state, app } = harness({ platform: 'darwin' });
  await settle();
  const window = state.windows[0];
  window.fullScreen = true;
  window.close();
  app.emit('activate');
  window.fullScreen = false;
  window.close();
  assert.equal(window.shown, true);
  assert.deepEqual(window.fullScreenRequests, [false]);
  window.emit('leave-full-screen');
  assert.equal(window.shown, false);
  assert.equal(state.backends[0].stops, 0);
  app.quit(); await settle();
});

test('macOS leaving full screen without closing never hides the window', async () => {
  const { state, app } = harness({ platform: 'darwin' });
  await settle();
  const window = state.windows[0];
  window.fullScreen = false;
  window.emit('leave-full-screen');
  assert.equal(window.shown, true);
  assert.deepEqual(window.fullScreenRequests, []);
  app.quit(); await settle();
});

for (const source of ['quit', 'shutdown']) {
  test(`macOS ${source} during full-screen exit drains immediately without a delayed hide`, async () => {
    let finish;
    const { state, app, powerMonitor } = harness({ platform: 'darwin', stop: () => new Promise(resolve => { finish = resolve; }) });
    await settle();
    const window = state.windows[0];
    window.fullScreen = true;
    window.close();
    window.hide = () => assert.fail('must not hide after shutdown starts');
    if (source === 'quit') app.quit();
    else powerMonitor.emit('shutdown', { preventDefault() {} });
    assert.equal(window.destroyed, true);
    assert.equal(state.backends[0].stops, 1);
    assert.deepEqual(state.exits, []);
    window.emit('leave-full-screen');
    finish(); await settle();
    assert.deepEqual(state.exits, [0]);
  });
}

test('a stale macOS full-screen exit cannot hide or cancel closing a recovered window', async () => {
  const { state, app } = harness({ platform: 'darwin', recoveryResponse: 0 });
  await settle();
  const previous = state.windows[0];
  previous.fullScreen = true;
  previous.close();
  state.backends[0].emit('failure', new BackendError('unexpected_exit'));
  await settle();
  const replacement = state.windows[1];
  assert.equal(previous.destroyed, true);
  replacement.fullScreen = true;
  replacement.close();
  previous.hide = () => assert.fail('must not hide a destroyed window');
  previous.emit('leave-full-screen');
  assert.equal(replacement.shown, true);
  replacement.fullScreen = false;
  replacement.emit('leave-full-screen');
  assert.equal(replacement.shown, false);
  assert.equal(state.backends[1].state, 'running');
  assert.equal(state.backends[1].stops, 0);
  app.quit(); await settle();
});

for (const platform of ['win32', 'linux']) {
  test(`${platform} full-screen close keeps the existing immediate hide behavior`, async () => {
    const { state, app } = harness({ platform });
    await settle();
    const window = state.windows[0];
    window.fullScreen = true;
    window.close();
    assert.equal(window.shown, false);
    assert.equal(window.destroyed, false);
    assert.deepEqual(window.fullScreenRequests, []);
    assert.equal(state.backends[0].stops, 0);
    app.quit(); await settle();
  });
}

for (const platform of ['darwin', 'linux']) {
  test(`${platform} OS shutdown quits rather than leaving a hidden service`, async () => {
    const { state, powerMonitor } = harness({ platform });
    await settle();
    state.windows[0].close();
    let delayed = false;
    powerMonitor.emit('shutdown', { preventDefault() { delayed = true; } });
    await settle();
    assert.equal(delayed, true);
    assert.equal(state.backends[0].stops, 1);
    assert.equal(state.windows[0].destroyed, true);
    assert.deepEqual(state.exits, [0]);
  });
}

test('macOS native Quit request drains the hidden app without a window-close request', async () => {
  let finish;
  const { state, app } = harness({ platform: 'darwin', stop: () => new Promise(resolve => { finish = resolve; }) });
  await settle();
  const window = state.windows[0];
  window.close();
  assert.equal(window.isMinimized(), false);
  assert.equal(window.shown, false);
  let prevented = false;
  app.emit('before-quit', { preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(window.destroyed, true);
  assert.equal(state.backends[0].stops, 1);
  assert.deepEqual(state.exits, []);
  finish(); await settle();
  assert.equal(state.trayDestroyed, true);
  assert.deepEqual(state.exits, [0]);
});

for (const event of ['query-session-end', 'session-end']) {
  test(`Windows ${event} starts cleanup without cancelling logout`, async () => {
    const { state } = harness({ platform: 'win32' });
    await settle();
    state.windows[0].close();
    state.windows[0].emit(event, { preventDefault() { assert.fail('must not cancel logout'); } });
    await settle();
    assert.equal(state.backends[0].stops, 1);
    assert.equal(state.windows[0].destroyed, true);
    assert.deepEqual(state.exits, [0]);
  });
}

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
  state.windows[0].close();
  assert.equal(state.windows[0].destroyed, false);
  assert.equal(state.windows[0].shown, false);
  assert.deepEqual(state.exits, []);
  finishStop(); await install;
  assert.equal(state.installs, 1);
  assert.equal(state.nativeQuits, 1);
  assert.deepEqual(state.exits, []);
  assert.equal(state.trayDestroyed, true);
  assert.equal(state.windows[0].destroyed, true);
});

for (const hidden of [false, true]) {
  test(`installer closes the ${hidden ? 'hidden' : 'visible'} window before before-quit without ordinary cleanup`, async () => {
    const updater = updateFixture();
    const { state, invoke, app } = harness({ runtime: { updater }, confirm: 0 });
    await settle();
    const window = state.windows[0];
    if (hidden) window.close();
    const events = [];
    window.on('closed', () => events.push('closed'));
    app.on('window-all-closed', () => events.push('window-all-closed'));
    app.on('before-quit', () => events.push('before-quit'));
    let nativeArgs, closedBeforeQuit = false, quitPrevented = false;
    updater.quitAndInstall = (...args) => {
      nativeArgs = args;
      window.close();
      closedBeforeQuit = window.destroyed;
      app.emit('before-quit', { preventDefault() { quitPrevented = true; } });
    };
    await invoke('check'); await invoke('download'); await invoke('install'); await settle();
    assert.equal(state.backends[0].cleanExit, true);
    assert.deepEqual(nativeArgs, [false, true]);
    assert.equal(closedBeforeQuit, true);
    assert.deepEqual(events, ['closed', 'window-all-closed', 'before-quit']);
    assert.equal(quitPrevented, false);
    assert.equal(state.nativeQuits, 0);
    assert.equal(state.backends[0].stops, 1);
    assert.deepEqual(state.exits, []);
    assert.equal(state.trayDestroyed, true);
  });
}

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

test('community release viewing opens the verified browser link without stopping the backend', async () => {
  const url = 'https://github.com/Jingyi-Jia/agents-switcher/releases/tag/v1.2.0';
  let checks = 0;
  const { state, app, invoke } = harness({ runtime: { checkRelease: async () => {
    checks += 1;
    return { version: '1.2.0', url };
  } } });
  await settle();
  assert.equal(checks, 0);
  assert.equal((await invoke('state')).mode, 'manual');
  await invoke('view-release');
  assert.deepEqual(state.external, []);
  await invoke('check');
  assert.equal(checks, 1);
  assert.deepEqual(state.external, []);
  await invoke('view-release');
  assert.deepEqual(state.external, [url]);
  await invoke('download'); await invoke('install');
  assert.equal(state.backends[0].stops, 0);
  assert.equal(state.backends[0].updateStopCalled, undefined);
  assert.equal(state.dialogs.length, 0);
  assert.deepEqual(state.exits, []);
  app.quit(); await settle();
  assert.equal(state.backends[0].stops, 1);
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

for (const source of ['backend', 'renderer']) {
  test(`${source} failure while hidden recovers to a usable new dashboard`, async () => {
    const { state, app } = harness({ recoveryResponse: 0 });
    await settle();
    const previous = state.windows[0];
    previous.close();
    if (source === 'backend') state.backends[0].emit('failure', new BackendError('unexpected_exit'));
    else previous.webContents.emit('render-process-gone', {}, { reason: 'crashed' });
    await settle();
    assert.equal(previous.destroyed, true);
    assert.equal(state.backends[0].stops, 1);
    assert.equal(state.dialogs.length, 1);
    assert.equal(state.windows.length, 2);
    assert.equal(state.backends.length, 2);
    assert.equal(state.windows[1].shown, true);
    assert.equal(state.backends[1].state, 'running');
    assert.deepEqual(state.exits, []);
    state.windows[1].close();
    state.tray.emit('click');
    assert.equal(state.windows[1].shown, false);
    state.trayMenu.find(item => item.label === 'Show app').click();
    assert.equal(state.windows[1].shown, true);
    app.quit(); await settle();
    assert.equal(state.backends[1].stops, 1);
    assert.deepEqual(state.exits, [0]);
  });
}

test('tray initialization failure cannot leave a background backend', async () => {
  const { state } = harness({ trayError: new Error('SECRET native diagnostic') });
  await settle();
  assert.equal(state.backends.length, 0);
  assert.equal(state.windows.length, 0);
  assert.equal(JSON.stringify(state.dialogs).includes('SECRET'), false);
  assert.deepEqual(state.exits, [0]);
});

for (const platform of ['darwin', 'win32', 'linux']) {
  test(`${platform} tray shows all account windows without opening or switching the hidden app`, async () => {
    let signal;
    const { state, app } = harness({ platform, trayRead: async (_address, readSignal) => {
      signal = readSignal;
      return { claude: { available: true, accounts: [
        { email: 'first@example.test', active: true, windows: [{ label: '5h', usedPercent: 100 }] },
        { email: 'second@example.test', windows: [{ label: '7d', usedPercent: 60 }] },
      ] }, codex: { available: true, accounts: [] } };
    } });
    await settle();
    const window = state.windows[0];
    window.close();
    state.tray.emit('click');
    assert.equal(window.shown, false);
    assert.equal(state.trayPoppedUp, platform !== 'darwin' ? true : undefined);
    for (const expected of ['first@example.test · active', '5h: 0% left', 'second@example.test', '7d: 40% left']) {
      const item = state.trayMenu.find(item => item.label?.includes(expected));
      assert.ok(item, expected);
      assert.equal(item.enabled, false);
      assert.equal(item.click, undefined);
    }
    assert.equal(state.backends[0].stops, 0);
    app.quit(); await settle();
    assert.equal(signal.aborted, true);
    assert.equal(state.trayDashboard.timer, null);
  });
}

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
    window.close();
    state.trayMenu.find(item => item.label === label).click();
    await settle();
    assert.equal(window.shown, true);
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
