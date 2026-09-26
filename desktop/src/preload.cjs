'use strict';

const { contextBridge, ipcRenderer } = require('electron');

if (process.isMainFrame) {
  contextBridge.exposeInMainWorld('agentSwitchUpdater', Object.freeze({
    getState: () => ipcRenderer.invoke('agent-switch:update:state'),
    check: () => ipcRenderer.invoke('agent-switch:update:check'),
    download: () => ipcRenderer.invoke('agent-switch:update:download'),
    cancel: () => ipcRenderer.invoke('agent-switch:update:cancel'),
    install: () => ipcRenderer.invoke('agent-switch:update:install'),
    onState: callback => {
      if (typeof callback !== 'function') throw new TypeError('A state callback is required.');
      const listener = (_event, state) => callback(state);
      ipcRenderer.on('agent-switch:update:changed', listener);
      return () => ipcRenderer.removeListener('agent-switch:update:changed', listener);
    },
  }));
}
