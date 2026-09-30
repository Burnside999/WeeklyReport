'use strict';
const { contextBridge, ipcRenderer } = require('electron');
if (process.isMainFrame) contextBridge.exposeInMainWorld('weeklyReportDesktop', {
  platform: 'windows',
  getState: userId => ipcRenderer.invoke('notifications:state', userId),
  setEnabled: (userId, enabled) => ipcRenderer.invoke('notifications:toggle', userId, enabled),
  stop: () => ipcRenderer.invoke('notifications:stop'),
  onState: callback => {
    const listener = (_event, state) => callback(state);
    ipcRenderer.on('notifications:changed', listener);
    return () => ipcRenderer.removeListener('notifications:changed', listener);
  }
});

// Version handshake shared with future native shells; no Node access is exposed.
if (process.isMainFrame) contextBridge.exposeInMainWorld('weeklyReportClient', {
  protocolVersion: 1,
  getInfo: () => ipcRenderer.invoke('client:info')
});
