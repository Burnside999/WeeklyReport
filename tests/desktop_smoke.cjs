'use strict';
// Run against the installed EXE on Windows. OS toast display is mocked; IPC,
// persistent Chromium cookies, server API, tray lifetime and polling are real.
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const path = require('node:path');
const { _electron } = require('../desktop/node_modules/playwright');
const base = 'http://127.0.0.1:18081';
const server = spawn('python', ['tests/browser_server.py'], { env: { ...process.env, PYTHONPATH: process.cwd() }, stdio: 'inherit' });
async function until(check, timeout = 30000) {
  const start = Date.now();
  while (Date.now() - start < timeout) { const result = await check(); if (result) return result; await new Promise(r => setTimeout(r, 150)); }
  throw new Error('Timed out');
}
(async () => {
  let desktop;
  try {
    await until(async () => { try { return (await fetch(base + '/healthz')).ok; } catch { return false; } });
    desktop = await _electron.launch({
      executablePath: process.env.DESKTOP_EXE || require('../desktop/node_modules/electron'),
      args: process.env.DESKTOP_EXE ? [] : [path.resolve('desktop'), ...(process.platform === 'linux' ? ['--no-sandbox'] : [])],
      env: { ...process.env, ELECTRON_DISABLE_SECURITY_WARNINGS: 'true' }
    });
    await desktop.evaluate(({ Notification }) => {
      globalThis.smokeNotices = [];
      Notification.isSupported = () => true;
      Notification.prototype.show = function () { globalThis.smokeNotices.push({ title: this.title, body: this.body }); this.emit('show'); };
      Notification.prototype.close = function () { this.emit('close'); };
    });
    const setup = await desktop.firstWindow();
    await setup.locator('#server').fill(base); await setup.locator('button').click();
    const page = await until(async () => desktop.windows().find(p => p.url().startsWith(base)));
    await page.locator('#username').fill('admin'); await page.locator('#password').fill('browser-test-password');
    await page.locator('#login button').click(); await page.waitForURL(u => u.pathname === '/');
    await page.waitForFunction(() => !document.querySelector('#push-toggle').disabled);
    assert.equal(await page.evaluate(() => typeof require), 'undefined', 'remote content cannot access Node');
    assert.equal(await page.evaluate(() => window.weeklyReportDesktop.platform), 'windows');
    await page.locator('#push-toggle').check();
    await page.locator('#push-state').filter({ hasText: '托盘' }).waitFor();
    assert.deepEqual(await desktop.evaluate(() => globalThis.smokeNotices.map(x => x.body)), ['消息推送启动成功！']);
    await page.reload(); await page.locator('#push-state').filter({ hasText: '托盘' }).waitFor();
    assert.equal(await desktop.evaluate(() => globalThis.smokeNotices.length), 1, 'refresh does not send welcome');
    const did = await page.locator('#document-select').inputValue();
    const title = 'Windows 后台通知验证';
    const result = await page.evaluate(async ({ did, title }) => {
      const write = async (route, body) => {
        const response = await fetch('/api/' + route, { method: route === 'me/primary-trigger' ? 'PUT' : 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'WeeklyReport', 'X-Document-ID': did }, body: JSON.stringify(body) });
        if (!response.ok) throw new Error(await response.text()); return response.json();
      };
      const item = await write('templates', { recipients: ['test@example.com'], subject: title, body: '正文', mode: 'manual' });
      await write('me/primary-trigger', { document_id: did, trigger_id: item.id });
      return item;
    }, { did, title });
    // The close action must hide, keeping the real main-process poller alive.
    await desktop.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().find(w => w.webContents.getURL().startsWith('http://127.0.0.1')).close());
    assert.equal(await desktop.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].isVisible()), false);
    await page.evaluate(async ({ did, item }) => {
      const response = await fetch('/api/templates/' + item.id + '/send', { method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'WeeklyReport', 'X-Document-ID': did }, body: JSON.stringify({ revision: item.revision }) });
      if (!response.ok) throw new Error(await response.text());
    }, { did, item: result });
    await until(async () => (await desktop.evaluate(() => globalThis.smokeNotices)).some(n => n.body === title));
    assert.equal(await desktop.evaluate(() => globalThis.smokeNotices.filter(n => n.body === 'Windows 后台通知验证').length), 1);
    await desktop.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].show());
    await page.locator('#push-toggle').uncheck();
    await page.locator('#push-state').filter({ hasText: '仅控制本设备' }).waitFor();
    await page.locator('#push-toggle').check(); await page.locator('#push-state').filter({ hasText: '托盘' }).waitFor();
    await page.locator('#logout').click(); await page.waitForURL(u => u.pathname === '/login');
    await page.locator('#username').fill('admin'); await page.locator('#password').fill('browser-test-password');
    await page.locator('#login button').click(); await page.waitForURL(u => u.pathname === '/');
    await page.waitForFunction(() => !document.querySelector('#push-toggle').disabled);
    assert.equal(await page.locator('#push-toggle').isChecked(), false, 'logout disables native notifications');
    if (process.env.DESKTOP_SCREENSHOT) await page.screenshot({ path: process.env.DESKTOP_SCREENSHOT });
    console.log('Installed desktop smoke passed: login, cookies, native bridge, single welcome, hidden-window delivery, toggle, logout');
  } finally { if (desktop) await desktop.close(); server.kill(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
