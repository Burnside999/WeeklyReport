const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const {chromium} = require((process.env.PLAYWRIGHT_NODE_MODULES || process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES)+'/playwright');
const base = 'http://127.0.0.1:18081';
const server = spawn('python',['tests/browser_server.py'],{env:{...process.env,PYTHONPATH:process.cwd()},stdio:'inherit'});
(async () => {
  let browser;
  try {
    let ready = false;
    for(let i=0;i<60;i++){try{if((await fetch(base+'/healthz')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,100));}
    assert(ready);
    browser = await chromium.launch({headless:true});
    const context = await browser.newContext({viewport:{width:1280,height:1000},timezoneId:'America/Los_Angeles',locale:'en-US'});
    const page = await context.newPage(), errors = [];
    page.on('pageerror',error=>errors.push(String(error)));
    await page.goto(base+'/help');
    await page.locator('#username').fill('admin');await page.locator('#password').fill('browser-test-password');
    await page.locator('#login button').click();await page.waitForURL(u=>u.pathname==='/help');
    await page.locator('#help').waitFor();
    assert.deepEqual(await page.locator('.bottom-nav a:visible span').allTextContents(),['填写情况','监听管理','邮件模板','设置','变量表','帮助手册','管理']);
    assert.equal(await page.locator('.help-author a').getAttribute('href'),'https://github.com/Burnside999');
    const did = await page.locator('#document-select').inputValue();
    const shots = process.env.UI_SCREENSHOT_DIR;
    if(shots)fs.mkdirSync(shots,{recursive:true});
    for(const width of [320,390,600,768,1280]) {
      await page.setViewportSize({width,height:1000});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`guide overflow ${width}`);
      assert(await page.locator('.bottom-nav').evaluate(el=>el.scrollWidth<=el.clientWidth),`navigation overflow ${width}`);
      await page.locator('.guide-index a[href="#guide-syntax"]').click();
      assert(await page.locator('#guide-syntax').evaluate(el=>el.open));
      assert(await page.locator('.guide-example').evaluateAll(items=>items.every(el=>el.scrollWidth<=el.clientWidth)),`code wrapping ${width}`);
      if(shots && [390,1280].includes(width)) {
        await page.locator('#guide-syntax > summary').click();await page.evaluate(()=>window.scrollTo(0,0));
        await page.screenshot({path:shots+`/help-${width}.png`,fullPage:true});
      }
    }
    await page.locator('#help-search').fill('不存在的测试词');
    await page.locator('#help-search-state').filter({hasText:'没有找到'}).waitFor();
    assert.equal(await page.locator('.guide-section:visible').count(),0);
    await page.locator('#help-search').fill('null');
    assert((await page.locator('.guide-section:visible').count())>0);
    await page.locator('.guide-index a[href="#guide-mail"]').click();
    assert.equal(await page.locator('#help-search').inputValue(),'');
    assert(await page.locator('#guide-mail').evaluate(el=>el.open));
    await page.reload();await page.locator('#guide-mail[open]').waitFor();
    // The shared formatter uses Beijing even when the device is in Los Angeles.
    assert.equal(await page.evaluate(()=>formatDateTime('2026-09-24T10:50:00Z')),'2026-09-24 18:50:00');
    await page.route('**/api/status', async route=>{
      const response=await route.fetch(), data=await response.json();
      Object.assign(data,{last_attempt:'2026-09-24T10:50:00Z',last_success:'2026-09-24T10:50:00Z',layout_updated_at:'2026-09-24T10:50:00Z'});
      await route.fulfill({response,json:data});
    });
    await page.locator('#nav-home').click();await page.locator('#last-time').filter({hasText:'2026-09-24 18:50:00'}).waitFor();
    assert(await page.locator('#push-toggle').isHidden());
    const autoStyle = await page.locator('.auto-query-toggle').first().evaluate(el=>{
      const css=getComputedStyle(el);return [css.borderWidth,css.borderRadius,css.backgroundColor,css.padding];
    });
    await page.locator('#nav-settings').click();await page.locator('#push-toggle:not(:disabled)').waitFor();
    assert.deepEqual(await page.locator('#device-notifications .auto-query-toggle').evaluate(el=>{
      const css=getComputedStyle(el);return [css.borderWidth,css.borderRadius,css.backgroundColor,css.padding];
    }),autoStyle);
    assert.equal(await page.locator('#device-notifications').evaluate(el=>el.previousElementSibling.id),'merge-settings');
    assert.equal(await page.locator('#device-notifications').evaluate(el=>getComputedStyle(el).borderWidth),'0px');
    await page.locator('#layout-state').filter({hasText:'2026-09-24 18:50:00'}).waitFor();
    for(const width of [320,390,1280]) {
      await page.setViewportSize({width,height:1000});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`settings overflow ${width}`);
      if(shots && width!==320)await page.screenshot({path:shots+`/settings-${width}.png`,fullPage:true});
    }
    await page.locator('#nav-mail').click();await page.locator('#add-template').click();
    await page.locator('#mail-recipients input').fill('test@example.com');
    await page.locator('#mail-subject').fill('时间格式测试');await page.locator('#mail-body').fill('正文');
    await page.locator('[name=mail_mode][value=auto]').check();
    for(const width of [320,390,600,768,1280]) {
      await page.setViewportSize({width,height:1000});
      const boxes=await page.locator('.weekday-choice').evaluateAll(elements=>elements.map(el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};}));
      assert.equal(boxes.length,7);assert(boxes.every(b=>Math.abs(b.y-boxes[0].y)<1 && Math.abs(b.width-boxes[0].width)<1 && b.height>=44),`weekday alignment ${width}`);
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`schedule overflow ${width}`);
      if(shots && [390,1280].includes(width))await page.locator('#automatic-fields').screenshot({path:shots+`/schedule-${width}.png`,style:".bottom-nav{visibility:hidden}"});
    }
    await page.locator('[name=schedule_weekday][value="0"]').check();
    await page.locator('[name=schedule_weekday][value="4"]').check();
    assert.equal(await page.locator('[name=schedule_weekday]:checked').count(),2);
    await page.locator('#schedule-kind').selectOption('fixed');
    await page.locator('#schedule-fixed').fill('2026-10-09 18:00:00');
    await page.locator('#condition-variable').selectOption('global.listencount');
    await page.locator('#save-template').click();await page.locator('#template-form').waitFor({state:'hidden'});
    await page.locator('.rule-meta').filter({hasText:'2026-10-09 18:00:00 后'}).waitFor();
    await page.getByRole('button',{name:'编辑',exact:true}).click();
    assert.equal(await page.locator('#schedule-fixed').inputValue(),'2026-10-09 18:00:00');
    // Help stays available to ordinary users who have not created any documents.
    const encrypted_password=await page.evaluate(()=>encryptPassword('guide-test-password'));
    const response=await context.request.post(base+'/api/admin/users',{headers:{'X-Requested-With':'WeeklyReport'},data:{username:'reader',encrypted_password,role:'user'}});
    assert(response.ok(),await response.text());
    const reader=await browser.newPage();await reader.goto(base+'/help');
    await reader.locator('#username').fill('reader');await reader.locator('#password').fill('guide-test-password');await reader.locator('#login button').click();
    await reader.locator('#help').waitFor();assert(await reader.locator('#no-documents').isHidden());assert(await reader.locator('#nav-admin').isHidden());
    assert.deepEqual(await reader.locator('.bottom-nav a:visible span').allTextContents(),['帮助手册']);
    await reader.close();assert.deepEqual(errors,[]);
    console.log('Guide navigation/search, no-document access, responsive weekdays, settings switch and Beijing timestamps passed');
  } finally {if(browser)await browser.close();server.kill('SIGTERM');}
})().catch(error=>{console.error(error);process.exitCode=1;});
