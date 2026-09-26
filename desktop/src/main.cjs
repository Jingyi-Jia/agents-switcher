'use strict';

const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { app, BrowserWindow, Menu, Tray, nativeImage, dialog, session, shell, ipcMain } = require('electron');
const { Backend, BackendError } = require('./backend.cjs');
const { HELP_LINKS, secureSession, secureWebContents } = require('./security.cjs');
const { UpdateController, registerUpdaterIPC } = require('./updater.cjs');
const { createUpdateRuntime } = require('./updater-runtime.cjs');

app.setName('Agent Switch');
let window = null;
let tray = null;
let backend = null;
let quitting = false;
let recovering = false;
let starting = false;
let dashboardAddress = null;
let updates = null;
let installing = false;

function showWindow() {
  if (!window || window.isDestroyed() || recovering || quitting || installing) return;
  if (window.isMinimized()) window.restore();
  window.show();
  window.focus();
}

function showView(view) {
  if (!['accounts', 'usage', 'settings'].includes(view) || !dashboardAddress
    || !window || window.isDestroyed() || recovering || quitting || installing) return;
  const target = new URL(dashboardAddress);
  target.hash = view;
  void window.loadURL(target.href).then(showWindow).catch(() => recover(new BackendError('backend_error')));
}

function openHelp(url) {
  return shell.openExternal(url).catch(() => {});
}

function installMenu() {
  const template = [];
  if (process.platform === 'darwin') {
    template.push({ label: app.name, submenu: [
      { role: 'about' }, { type: 'separator' }, { role: 'services' },
      { type: 'separator' }, { role: 'hide' }, { role: 'hideOthers' }, { role: 'unhide' },
      { type: 'separator' }, { label: 'Quit Agent Switch', accelerator: 'Command+Q', click: () => app.quit() },
    ] });
  }
  template.push(
    { label: 'File', submenu: [
      { label: 'Show app', click: showWindow },
      { type: 'separator' },
      { label: 'Quit Agent Switch', accelerator: process.platform === 'darwin' ? undefined : 'Alt+F4', click: () => app.quit() },
    ] },
    { role: 'editMenu' },
    { label: 'View', submenu: [
      { label: 'Accounts', accelerator: 'CommandOrControl+1', click: () => showView('accounts') },
      { label: 'Usage', accelerator: 'CommandOrControl+2', click: () => showView('usage') },
      { label: 'Settings', accelerator: 'CommandOrControl+,', click: () => showView('settings') },
      { type: 'separator' }, { role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' },
      { type: 'separator' }, { role: 'togglefullscreen' },
    ] },
    { label: 'Help', submenu: [
      { label: 'Claude Code setup', click: () => openHelp(HELP_LINKS.claude) },
      { label: 'Codex setup', click: () => openHelp(HELP_LINKS.codex) },
      { label: 'Check for Updates…', click: () => { showView('settings'); void updates?.check(); } },
      { label: 'Download updates manually', click: () => openHelp(HELP_LINKS.releases) },
      { label: 'Agent Switch on GitHub', click: () => openHelp(HELP_LINKS.project) },
    ] },
  );
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
  app.setAboutPanelOptions({ applicationName: 'Agent Switch', applicationVersion: app.getVersion(), copyright: 'MIT · agents-switcher contributors' });
}

function installTray() {
  const icon = nativeImage.createFromPath(path.join(__dirname, '..', 'assets', process.platform === 'darwin' ? 'trayTemplate.png' : 'tray.png'));
  if (process.platform === 'darwin') icon.setTemplateImage(true);
  tray = new Tray(icon);
  tray.setToolTip('Agent Switch — close the window to quit');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Accounts', click: () => showView('accounts') },
    { label: 'Usage dashboard', click: () => showView('usage') },
    { label: 'Settings', click: () => showView('settings') },
    { type: 'separator' },
    { label: 'Quit Agent Switch', click: () => app.quit() },
  ]));
  tray.on('click', showWindow);
}

async function recover(error) {
  if (quitting || recovering || installing) return;
  recovering = true;
  dashboardAddress = null;
  if (window && !window.isDestroyed()) {
    window.destroy();
    window = null;
  }
  try {
    await backend?.stop();
  } catch {
    app.quit();
    return;
  }
  if (quitting) return;
  const safeError = error instanceof BackendError ? error : new BackendError('backend_error');
  const { response } = await dialog.showMessageBox({
    type: 'error', title: 'Agent Switch could not connect',
    message: 'The local dashboard is unavailable.', detail: safeError.message,
    buttons: ['Try again', 'Quit'], defaultId: 0, cancelId: 1, noLink: true,
  });
  recovering = false;
  if (quitting) return;
  if (response === 0) await startDashboard();
  else app.quit();
}

async function startDashboard() {
  if (quitting || starting || installing) return;
  starting = true;
  backend = new Backend({
    isPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    repoPath: path.resolve(__dirname, '..', '..'),
  });
  const currentBackend = backend;
  backend.on('failure', (error) => {
    if (backend === currentBackend) void recover(error);
  });
  try {
    const url = await backend.start();
    if (quitting || recovering || backend.state !== 'running') return;
    dashboardAddress = url;
    const origin = new URL(url).origin;
    const isolatedSession = session.fromPartition(`agent-switch-${randomUUID()}`, { cache: false });
    secureSession(isolatedSession, origin);
    window = new BrowserWindow({
      width: 1180, height: 860, minWidth: 520, minHeight: 480,
      title: 'Agent Switch', show: false, backgroundColor: '#f5f5f5',
      icon: path.join(__dirname, '..', 'assets', 'icon.png'),
      webPreferences: {
        nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true,
        allowRunningInsecureContent: false, webviewTag: false, devTools: !app.isPackaged,
        preload: path.join(__dirname, 'preload.cjs'),
        session: isolatedSession,
      },
    });
    secureWebContents(window.webContents, origin, openHelp);
    window.webContents.on('render-process-gone', () => void recover(new BackendError('backend_error')));
    window.on('closed', () => { window = null; });
    await window.loadURL(url);
    showWindow();
  } catch (error) {
    starting = false;
    await recover(error);
  } finally {
    starting = false;
  }
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', showWindow);
  app.on('activate', showWindow);
  app.on('window-all-closed', () => {
    if (!recovering && !quitting) app.quit();
  });
  app.on('before-quit', (event) => {
    if (installing && updates?.installStarted && backend?.cleanExit) {
      quitting = true;
      updates.close();
      if (tray) tray.destroy();
      return;
    }
    event.preventDefault();
    if (quitting) return;
    quitting = true;
    updates?.close();
    if (window && !window.isDestroyed()) window.destroy();
    if (tray) {
      tray.setToolTip('Agent Switch — stopping the local service');
      tray.setContextMenu(Menu.buildFromTemplate([
        { label: 'Stopping local service…', enabled: false },
        { label: 'Force quit', click: () => backend?.forceKill() },
      ]));
    }
    void (async () => {
      let exitCode = 0;
      const stoppingDialog = new AbortController();
      const stoppingTimer = setTimeout(() => {
        if (process.platform === 'darwin') return;
        void dialog.showMessageBox({
          type: 'info', title: 'Quitting Agent Switch',
          message: 'Stopping the local service…',
          detail: 'Waiting for in-progress provider requests before quitting. This can take up to 35 seconds. Auto switching stops with the local service.',
          buttons: ['Force quit'], noLink: true, signal: stoppingDialog.signal,
        }).then(() => {
          if (!stoppingDialog.signal.aborted) backend?.forceKill();
        }).catch(() => {});
      }, 1000);
      try {
        await backend?.stop();
      } catch {
        backend?.forceKill();
        exitCode = 1;
        dialog.showErrorBox('Agent Switch shutdown', new BackendError('shutdown_failed').message);
      } finally {
        clearTimeout(stoppingTimer);
        stoppingDialog.abort();
      }
      if (tray) tray.destroy();
      app.exit(exitCode);
    })();
  });
  process.on('exit', () => backend?.forceKill());
  process.on('SIGINT', () => app.quit());
  process.on('SIGTERM', () => app.quit());
  void app.whenReady().then(async () => {
    const runtime = await createUpdateRuntime({ app, releaseBuild: require('../package.json').agentSwitchRelease });
    if (quitting) return;
    updates = new UpdateController({
      ...runtime, currentVersion: app.getVersion(), platform: process.platform, arch: process.arch,
      confirmInstall: async () => {
        if (quitting || recovering || !window || window.isDestroyed()) return false;
        const { response } = await dialog.showMessageBox({
          type: 'question', title: 'Install Agent Switch update',
          message: 'Install the downloaded update and restart Agent Switch?',
          detail: 'The local service and this app’s auto-switching will stop first. Saved accounts and provider credentials are not changed. Independently started CLI automation is not stopped.',
          buttons: ['Install and restart', 'Cancel'], defaultId: 1, cancelId: 1, noLink: true,
        });
        return response === 0 && !quitting && !recovering && Boolean(window && !window.isDestroyed());
      },
      prepareInstall: async () => {
        if (quitting || recovering || !backend) throw new Error();
        installing = true;
        await backend.stopForUpdate();
        if (quitting) throw new Error();
      },
      installFailed: () => {
        installing = false;
        dialog.showErrorBox('Agent Switch update', 'The update could not finish safely. Agent Switch will close. Reopen it and check the version before retrying; no successful update has been confirmed.');
        app.quit();
      },
    });
    registerUpdaterIPC(ipcMain, updates, () => ({
      contents: !quitting && !recovering && window && !window.isDestroyed() ? window.webContents : null,
      origin: dashboardAddress ? new URL(dashboardAddress).origin : null,
    }));
    installMenu();
    installTray();
    await startDashboard();
  }).catch(() => {
    dialog.showErrorBox('Agent Switch could not start', new BackendError('launch_failed').message);
    app.quit();
  });
}
