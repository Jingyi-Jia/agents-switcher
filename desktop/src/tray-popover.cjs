'use strict';

const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { randomUUID } = require('node:crypto');
const { securePopoverSession, securePopoverContents } = require('./security.cjs');
const { quotaModel } = require('./tray-dashboard.cjs');

const CHANNEL = 'agent-switch:tray';
const DOCUMENT = path.join(__dirname, 'tray-popover.html');
const DOCUMENT_URL = pathToFileURL(DOCUMENT).href;

function popoverBounds(anchor, area) {
  const margin = Math.min(8, Math.floor(Math.min(area.width, area.height) / 4));
  const width = Math.min(396, area.width - 2 * margin);
  const height = Math.min(610, area.height - 2 * margin);
  const x = Math.min(area.x + area.width - width - margin,
    Math.max(area.x + margin, anchor.x + anchor.width / 2 - width / 2));
  const below = anchor.y + anchor.height + margin;
  const y = Math.min(area.y + area.height - height - margin,
    Math.max(area.y + margin, below + height <= area.y + area.height - margin ? below : anchor.y - height - margin));
  return { x: Math.round(x), y: Math.round(y), width, height };
}

class TrayPopover {
  constructor({ BrowserWindow, session, screen, ipcMain, navigate, fallback, canOpen }) {
    Object.assign(this, { BrowserWindow, session, screen, navigate, fallback, canOpen });
    this.window = null;
    this.model = quotaModel(null);
    this.ready = false;
    ipcMain.handle(`${CHANNEL}:ready`, (event, ...args) => {
      if (args.length || !this.trusted(event)) return;
      this.ready = true;
      this.publish();
    });
    ipcMain.handle(`${CHANNEL}:action`, (event, action, ...args) => {
      if (args.length || !this.trusted(event) || !['app', 'accounts', 'usage', 'settings', 'dismiss'].includes(action)) return;
      this.dismiss();
      if (action !== 'dismiss') this.navigate(action);
    });
  }

  trusted(event) {
    try {
      const contents = this.window?.webContents;
      return Boolean(this.canOpen() && contents && !contents.isDestroyed()
        && event.sender === contents && event.senderFrame === contents.mainFrame
        && event.senderFrame.url === DOCUMENT_URL && contents.getURL() === DOCUMENT_URL);
    } catch { return false; }
  }

  publish() {
    if (!this.ready || !this.window || !this.trusted({ sender: this.window.webContents,
      senderFrame: this.window.webContents.mainFrame })) return;
    try { this.window.webContents.mainFrame.send(`${CHANNEL}:model`, this.model); }
    catch { this.dismiss(); }
  }

  update(model) {
    if (!model) return;
    this.model = model;
    this.publish();
  }

  clear() {
    this.model = quotaModel(null);
    this.dismiss();
  }

  dismiss() {
    const window = this.window;
    this.window = null;
    this.ready = false;
    if (window && !window.isDestroyed()) window.destroy();
  }

  async toggle(trayBounds) {
    if (!this.canOpen()) return;
    if (this.window) { this.dismiss(); return; }
    let window;
    try {
      const cursor = this.screen.getCursorScreenPoint();
      const anchor = trayBounds?.width > 0 && trayBounds?.height > 0 ? trayBounds : { ...cursor, width: 0, height: 0 };
      const area = this.screen.getDisplayNearestPoint({ x: Math.round(anchor.x + anchor.width / 2),
        y: Math.round(anchor.y + anchor.height / 2) }).workArea;
      if (!this.isolatedSession) {
        this.isolatedSession = this.session.fromPartition(`agent-switch-tray-${randomUUID()}`, { cache: false });
        securePopoverSession(this.isolatedSession, [DOCUMENT_URL,
          pathToFileURL(path.join(__dirname, 'tray-popover.css')).href,
          pathToFileURL(path.join(__dirname, 'tray-popover.js')).href]);
      }
      window = new this.BrowserWindow({
        ...popoverBounds(anchor, area), show: false, frame: false, resizable: false, movable: false,
        minimizable: false, maximizable: false, fullscreenable: false, skipTaskbar: true,
        alwaysOnTop: true, title: 'Account quota — Agent Switch', backgroundColor: '#edf1f7',
        webPreferences: { preload: path.join(__dirname, 'tray-preload.cjs'), session: this.isolatedSession,
          sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true,
          allowRunningInsecureContent: false, webviewTag: false, devTools: false },
      });
      this.window = window;
      securePopoverContents(window.webContents);
      window.setMenu(null);
      window.on('blur', () => { if (this.window === window) this.dismiss(); });
      window.on('closed', () => { if (this.window === window) { this.window = null; this.ready = false; } });
      window.webContents.on('before-input-event', (event, input) => {
        if (input.key === 'Escape') { event.preventDefault(); if (this.window === window) this.dismiss(); }
      });
      window.webContents.on('render-process-gone', () => { if (this.window === window) this.dismiss(); });
      await window.loadFile(DOCUMENT);
      if (this.window !== window || window.isDestroyed()) return;
      if (!this.canOpen()) { this.dismiss(); return; }
      window.show();
      window.focus();
    } catch {
      if (window ? this.window !== window : this.window) return;
      this.dismiss();
      if (this.canOpen()) this.fallback();
    }
  }
}

module.exports = { TrayPopover, popoverBounds, DOCUMENT_URL };
