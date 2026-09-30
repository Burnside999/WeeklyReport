'use strict';
const { contextBridge, ipcRenderer } = require('electron');
if (process.isMainFrame) contextBridge.exposeInMainWorld('desktopSettings', {
  read: () => ipcRenderer.invoke('settings:read'),
  save: value => ipcRenderer.invoke('settings:save', value)
});
