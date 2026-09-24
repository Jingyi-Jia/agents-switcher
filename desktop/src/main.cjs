'use strict';

const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { app, BrowserWindow, Menu, Tray, nativeImage, dialog, session, shell } = require('electron');
const { Backend, BackendError } = require('./backend.cjs');
const { HELP_LINKS, secureSession, secureWebContents } = require('./security.cjs');

app.setName('Agent Switch');
let window = null;
let tray = null;
let backend = null;
let quitting = false;
let recovering = false;
let starting = false;

function showWindow() {
  if (!window || window.isDestroyed() || recovering || quitting) return;
  if (window.isMinimized()) window.restore();
  window.show();
  window.focus();
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
    { label: 'View', submenu: [{ role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' }, { type: 'separator' }, { role: 'togglefullscreen' }] },
    { label: 'Help', submenu: [
      { label: 'Claude Code setup', click: () => openHelp(HELP_LINKS.claude) },
      { label: 'Codex setup', click: () => openHelp(HELP_LINKS.codex) },
      { label: 'Download updates', click: () => openHelp(HELP_LINKS.releases) },
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
    { label: 'Show app', click: showWindow },
    { type: 'separator' },
    { label: 'Quit Agent Switch', click: () => app.quit() },
  ]));
  tray.on('click', showWindow);
}

async function recover(error) {
  if (quitting || recovering) return;
  recovering = true;
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
  if (quitting || starting) return;
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
    const origin = new URL(url).origin;
    const isolatedSession = session.fromPartition(`agent-switch-${randomUUID()}`, { cache: false });
    secureSession(isolatedSession, origin);
    window = new BrowserWindow({
      width: 1040, height: 820, minWidth: 520, minHeight: 480,
      title: 'Agent Switch', show: false, backgroundColor: '#f5f5f5',
      icon: path.join(__dirname, '..', 'assets', 'icon.png'),
      webPreferences: {
        nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true,
        allowRunningInsecureContent: false, webviewTag: false, devTools: !app.isPackaged,
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
    if (!recovering) app.quit();
  });
  app.on('before-quit', (event) => {
    event.preventDefault();
    if (quitting) return;
    quitting = true;
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
    installMenu();
    installTray();
    await startDashboard();
  }).catch(() => {
    dialog.showErrorBox('Agent Switch could not start', new BackendError('launch_failed').message);
    app.quit();
  });
}
