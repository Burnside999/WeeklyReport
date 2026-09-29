'use strict';
const { app, BrowserWindow, Menu, Tray, Notification, ipcMain, session, nativeImage, dialog, powerMonitor, shell } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { pathToFileURL } = require('node:url');
const { serverOrigin, sameOrigin, documentURL, trustedSender, externalLink } = require('./security.cjs');
const { NotificationClient } = require('./notifications.cjs');

app.setAppUserModelId('city.images.weeklyreport');
const locked = app.requestSingleInstanceLock();
if (!locked) app.quit();
let window, settings, tray, client, config, configPath, webSession, origin, timer, quitting = false, settingsError = '';
const DEFAULT_SERVER = 'https://wrret.images.city';
const visibleNotifications = new Set();
const settingsURL = pathToFileURL(path.join(__dirname, 'ui/settings.html')).href;
const iconPath = path.join(__dirname, 'assets/icon.png');
function writeConfig() {
  const temporary = configPath + '.tmp';
  fs.writeFileSync(temporary, JSON.stringify(config), { mode: 0o600 });
  fs.renameSync(temporary, configPath);
}
function showWindow() {
  if (window && !window.isDestroyed()) { if (window.isMinimized()) window.restore(); window.show(); window.focus(); }
  else openSettings();
}
function closeNotifications() {
  for (const notification of visibleNotifications) notification.close();
  visibleNotifications.clear();
}
function sendState(state) {
  if (window && !window.isDestroyed() && sameOrigin(window.webContents.getURL(), origin)) window.webContents.send('notifications:changed', state);
  if (tray) tray.setToolTip('周报填写检查 · ' + (state.error || (state.enabled ? '正在接收通知' : '通知已关闭')));
}
function showNotification(item) {
  if (!Notification.isSupported()) return Promise.reject(new Error('当前系统不支持通知。'));
  const activeOrigin = origin, activeClient = client;
  return new Promise((resolve, reject) => {
    const notification = new Notification({ title: '周报提醒', body: item.title.slice(0, 1000), icon: iconPath });
    visibleNotifications.add(notification);
    const timeout = setTimeout(() => { notification.close(); visibleNotifications.delete(notification); reject(new Error('系统未确认通知，请检查 Windows 通知设置。')); }, 10000);
    notification.once('show', () => { clearTimeout(timeout); resolve(); });
    notification.once('failed', () => { clearTimeout(timeout); visibleNotifications.delete(notification); reject(new Error('通知显示失败，请检查 Windows 通知设置。')); });
    notification.once('close', () => { visibleNotifications.delete(notification); });
    notification.once('click', () => {
      if (origin !== activeOrigin || client !== activeClient) return;
      showWindow();
      if (item.document_id && window && !window.isDestroyed()) {
        const target = documentURL(origin, item.document_id);
        if (window.webContents.getURL() !== target) {
          // Let the user protect unsaved edits before switching documents.
          dialog.showMessageBox(window, { type: 'question', message: '打开这条提醒对应的文档？', detail: '当前页面中尚未保存的编辑会丢失。', buttons: ['打开文档', '留在当前页'], defaultId: 0, cancelId: 1 })
            .then(result => { if (result.response === 0 && origin === activeOrigin && window && !window.isDestroyed()) window.loadURL(target).catch(() => {}); });
        }
      }
    });
    notification.show();
  });
}
function requestFor(ses, base) {
  let resuming;
  async function raw(route, body) {
    const response = await ses.fetch(base + route, { method: body === undefined ? 'GET' : 'POST',
      credentials: 'include', cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(12000),
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'WeeklyReport' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    if (!response.ok) { const error = new Error('服务器请求失败'); error.status = response.status; throw error; }
    return response.json();
  }
  async function resume() {
    const allowed = () => client?.state.enabled && origin === base && !quitting;
    if (!allowed()) { const error = new Error('需要重新登录'); error.status = 401; throw error; }
    const options = await raw('/api/auth/options');
    if (!options.automatic || !allowed()) { const error = new Error('需要重新登录'); error.status = 401; throw error; }
    await raw('/api/auth/resume', { username: options.username, auto: true, remember: true, automatic: true });
  }
  return async route => {
    try { return await raw(route); }
    catch (error) {
      if (error.status !== 401) throw error;
      if (!resuming) resuming = resume().finally(() => { resuming = null; });
      await resuming;
      return raw(route);
    }
  };
}
function configureServer(value) {
  origin = serverOrigin(value);
  if (client) client.disable();
  closeNotifications();
  const partition = 'persist:weeklyreport-' + createHash('sha256').update(origin).digest('hex').slice(0, 24);
  webSession = session.fromPartition(partition);
  // Only native notifications are used. Remote pages receive no privileged permissions.
  webSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  webSession.setPermissionCheckHandler(() => false);
  webSession.on('will-download', event => event.preventDefault());
  client = new NotificationClient({ request: requestFor(webSession, origin), notify: showNotification,
    state: config.notifications, persist: state => { config.notifications = state; writeConfig(); }, changed: sendState });
  if (window && !window.isDestroyed()) window.destroy();
  window = new BrowserWindow({ width: 1180, height: 820, minWidth: 420, minHeight: 600, title: '周报填写检查', icon: iconPath,
    backgroundColor: '#f3f6fb', show: false,
    webPreferences: { session: webSession, preload: path.join(__dirname, 'preload.cjs'), nodeIntegration: false,
      contextIsolation: true, sandbox: true, webSecurity: true, webviewTag: false, backgroundThrottling: false } });
  const current = window;
  current.removeMenu();
  current.webContents.setWindowOpenHandler(({ url }) => {
    if (externalLink(url)) shell.openExternal(url).catch(() => {});
    return { action: 'deny' };
  });
  current.webContents.on('will-attach-webview', event => event.preventDefault());
  current.webContents.on('will-navigate', (event, url) => {
    const target = event.url || url;
    if (!sameOrigin(target, origin)) { event.preventDefault(); if (externalLink(target)) shell.openExternal(target).catch(() => {}); }
  });
  current.webContents.on('will-redirect', (event, url) => { if (!sameOrigin(event.url || url, origin)) event.preventDefault(); });
  current.webContents.on('render-process-gone', () => { if (!quitting && !current.isDestroyed()) current.reload(); });

  let closePrompt = false;
  current.on('close', event => {
    if (quitting || !tray || tray.isDestroyed()) return;
    event.preventDefault();
    if (closePrompt) return;
    closePrompt = true;
    dialog.showMessageBox(current, { type: 'info', title: '继续在后台运行',
      message: '关闭窗口后，应用会隐藏到系统托盘',
      detail: '应用仍会接收通知。要完全关闭，请在任务栏右下角（可能收在“∧”中）找到周报图标，右键选择“退出”。',
      buttons: ['隐藏到托盘', '取消'], defaultId: 0, cancelId: 1 })
      .then(result => { if (result.response === 0 && !current.isDestroyed()) current.hide(); })
      .catch(() => {}).finally(() => { closePrompt = false; });
  });
  current.on('closed', () => { if (window === current) window = null; });
  // A stalled connection or an HTTP error also needs an escape to manual setup.
  let connecting = true;
  const failed = () => {
    clearTimeout(connectionTimer);
    if (!connecting || quitting || current.isDestroyed() || window !== current) return;
    connecting = false;
    current.hide();
    openSettings('无法连接 ' + origin + '。请检查网络后重试，或修改网站地址。');
  };
  const connectionTimer = setTimeout(failed, 15000);
  current.on('closed', () => clearTimeout(connectionTimer));
  current.webContents.on('did-fail-load', (_event, code, _description, _url, isMainFrame) => {
    if (isMainFrame && code !== -3) failed();
  });
  current.webContents.on('did-navigate', (_event, _url, responseCode) => {
    if (responseCode >= 400) failed();
  });
  current.loadURL(origin + '/').then(() => {
    clearTimeout(connectionTimer);
    if (!connecting || current.isDestroyed()) return;
    connecting = false;
    current.show();
  }).catch(failed);
  sendState(client.status());
}
function openSettings(error = '') {
  settingsError = error;
  if (settings && !settings.isDestroyed()) { settings.reload(); settings.show(); settings.focus(); return; }
  settings = new BrowserWindow({ width: 610, height: 600, resizable: false, title: '连接周报服务', icon: iconPath,
    webPreferences: { preload: path.join(__dirname, 'settings-preload.cjs'), sandbox: true, contextIsolation: true, nodeIntegration: false } });
  settings.removeMenu();
  settings.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  settings.webContents.on('will-navigate', event => event.preventDefault());
  settings.on('closed', () => { settings = null; });
  settings.loadFile(path.join(__dirname, 'ui/settings.html'));
}
function settingsSender(event) {
  return settings && !settings.isDestroyed() && event.sender === settings.webContents && event.senderFrame === settings.webContents.mainFrame && event.senderFrame.url === settingsURL;
}
function requireRemote(event) {
  if (!trustedSender(event, window, origin)) throw new Error('不允许的请求来源');
}
ipcMain.handle('notifications:state', async (event, uid) => { requireRemote(event); return client.bind(uid); });
ipcMain.handle('notifications:toggle', async (event, uid, enabled) => {
  requireRemote(event);
  if (typeof enabled !== 'boolean') throw new Error('通知开关格式错误');
  if (enabled) return client.enable(uid);
  closeNotifications(); return client.disable();
});
ipcMain.handle('notifications:stop', event => { requireRemote(event); closeNotifications(); return client.disable(); });
ipcMain.handle('settings:read', event => {
  if (!settingsSender(event)) throw new Error('不允许的请求来源');
  return { server: config.server || DEFAULT_SERVER, error: settingsError };
});
ipcMain.handle('settings:save', (event, value) => {
  if (!settingsSender(event)) throw new Error('不允许的请求来源');
  const next = serverOrigin(value);
  if (next !== config.server) { client?.disable(); config.notifications = {}; }
  config.server = next; writeConfig(); configureServer(next);
  settings.close();
});
function trayMenu() {
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: '打开周报', click: showWindow },
    { label: '重新加载页面', click: () => { showWindow(); window?.reload(); } },
    { label: '更换网站', click: () => openSettings() },
    ...(process.platform === 'win32' && app.isPackaged ? [{ label: '开机启动', type: 'checkbox', checked: app.getLoginItemSettings().openAtLogin,
      click: item => { app.setLoginItemSettings({ openAtLogin: item.checked }); trayMenu(); } }] : []),
    { type: 'separator' }, { label: '退出', click: () => app.quit() }
  ]));
}
if (locked) app.whenReady().then(() => {
  configPath = path.join(app.getPath('userData'), 'settings.json');
  try { config = JSON.parse(fs.readFileSync(configPath, 'utf8')); } catch { config = {}; }
  if (!config || typeof config !== 'object' || Array.isArray(config)) config = {};
  Menu.setApplicationMenu(null);
  tray = new Tray(nativeImage.createFromPath(iconPath).resize({ width: 32, height: 32 }));
  tray.setToolTip('周报填写检查'); tray.on('double-click', showWindow); trayMenu();
  if (!config.server) { config.server = DEFAULT_SERVER; writeConfig(); }
  try { configureServer(config.server); } catch { openSettings('保存的网站地址无效，请重新填写。'); }
  timer = setInterval(() => client?.poll(), 15000);
  powerMonitor.on('resume', () => client?.poll());
  app.on('activate', showWindow);
}).catch(() => { dialog.showErrorBox('周报填写检查', '应用启动失败，请重新安装或联系维护者。'); app.quit(); });
app.on('second-instance', showWindow);
app.on('window-all-closed', () => { /* Explicit Quit in the tray ends the process. */ });
app.on('before-quit', () => { quitting = true; clearInterval(timer); if (client) client.generation++; closeNotifications(); });
