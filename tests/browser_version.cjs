'use strict';
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {chromium} = require((process.env.PLAYWRIGHT_NODE_MODULES || process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES)+'/playwright');
const {application} = require('../app/release.json');
const base = 'http://127.0.0.1:18081';
const server = spawn('python',['tests/browser_server.py'],{env:{...process.env,PYTHONPATH:process.cwd()},stdio:'inherit'});
(async () => {
  let browser;
  try {
    for (let i=0;i<60;i++) {try {if ((await fetch(base+'/healthz')).ok) break;} catch {} await new Promise(r=>setTimeout(r,100));}
    browser = await chromium.launch({headless:true});
    const context = await browser.newContext({viewport:{width:390,height:844},reducedMotion:'reduce'});
    const page = await context.newPage(), errors=[];
    page.on('pageerror',e=>errors.push(String(e)));
    await page.goto(base+'/login');
    const expected = `weeklyreport v${application.version}`;
    assert.equal(await page.locator('#app-version').textContent(), expected);
    await page.locator('#username').fill('admin'); await page.locator('#password').fill('browser-test-password');
    await page.locator('#login button').click(); await page.waitForURL(u=>u.pathname==='/');
    assert.deepEqual(await (await context.request.get(base+'/api/version')).json(), {...application,client_protocol:1});
    for (const path of ['/', '/manage','/mail','/settings','/variables','/help','/admin','/offline']) {
      await page.goto(base+path);
      const footer = page.locator('#app-version');
      assert.equal(await footer.textContent(),expected,path);
      await footer.scrollIntoViewIfNeeded();
      assert(await footer.evaluate(el=>{
        const r=el.getBoundingClientRect(),nav=document.querySelector('.bottom-nav:not([hidden])');
        return getComputedStyle(el).position!=='fixed' && r.left>=0 && r.right<=innerWidth && (!nav || r.bottom<=nav.getBoundingClientRect().top);
      }), 'footer does not cover navigation '+path);
    }
    const phone = await browser.newContext({viewport:{width:390,height:844},hasTouch:true,isMobile:true,userAgent:'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1',reducedMotion:'reduce'});
    await phone.addCookies(await context.cookies());
    const mobile = await phone.newPage();await mobile.goto(base+'/settings');
    assert.equal(await mobile.locator('#app-version').textContent(),`weeklyreport (iOS version) v${application.version}`);
    await mobile.locator('#connection-settings > summary').click();
    const tip=mobile.locator('#settings-form').getByRole('button',{name:'Access Token说明',exact:true});
    await tip.tap();await mobile.locator('#field-tooltip').waitFor();
    assert.equal(await tip.evaluate(el=>el.getBoundingClientRect().width),28,'touch target remains usable');
    await tip.tap();assert(await mobile.locator('#field-tooltip').isHidden());
    await tip.tap();await mobile.locator('#field-tooltip').waitFor();
    await mobile.locator('#settings-form [name=access_token]').tap();assert(await mobile.locator('#field-tooltip').isHidden(),'touching the field closes hint');
    await phone.close();
    await context.addInitScript(()=>{window.weeklyReportClient={protocolVersion:1,getInfo:async()=>({id:'windows',name:'Windows',version:'9.8.7'})};});
    await page.goto(base+'/login');await page.locator('#app-version').filter({hasText:'Windows client v9.8.7'}).waitFor();
    assert.equal(await page.locator('#app-version').textContent(),`${expected} (Windows client v9.8.7)`);
    assert.deepEqual(errors,[]);
    console.log('Version footer on every page, independent shell version and touch tooltips passed');
  } finally {await browser?.close();server.kill('SIGTERM');}
})().catch(e=>{console.error(e);process.exitCode=1;});
