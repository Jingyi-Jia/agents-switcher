'use strict';

const { contextBridge, ipcRenderer } = require('electron');

if (process.isMainFrame) {
  contextBridge.exposeInMainWorld('agentSwitchTray', Object.freeze({
    onModel: callback => {
      if (typeof callback !== 'function') throw new TypeError('A model callback is required.');
      const listener = (_event, model) => callback(model);
      ipcRenderer.on('agent-switch:tray:model', listener);
      void ipcRenderer.invoke('agent-switch:tray:ready').catch(() => {});
      return () => ipcRenderer.removeListener('agent-switch:tray:model', listener);
    },
    navigate: action => {
      if (!['app', 'accounts', 'usage', 'settings', 'dismiss'].includes(action)) return;
      void ipcRenderer.invoke('agent-switch:tray:action', action).catch(() => {});
    },
  }));
}
