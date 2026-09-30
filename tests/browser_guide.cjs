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
    const context = await browser.newContext({viewport:{width:1280,height:1000},timezoneId:'America/Los_Angeles',locale:'en-US',reducedMotion:'reduce'});
    const page = await context.newPage(), errors = [];
    page.on('pageerror',error=>errors.push(String(error)));
    await page.goto(base+'/help');
    await page.locator('#username').fill('admin');await page.locator('#password').fill('browser-test-password');
    await page.locator('#login button').click();await page.waitForURL(u=>u.pathname==='/help');
    await page.locator('#help').waitFor();
    assert.deepEqual(await page.locator('.bottom-nav a:visible span').allTextContents(),['填写情况','监听管理','邮件模板','设置','变量表','帮助手册','管理']);
    assert.equal(await page.locator('.help-author a').getAttribute('href'),'https://github.com/Burnside999');
    assert(await page.locator('.help-author').evaluate(el=>el.previousElementSibling.tagName === 'H1'));
    assert.equal(await page.locator('link[rel=icon]').getAttribute('href'),'/static/icon-192.png');
    assert(await page.locator('.brand img').evaluate(el=>el.complete && el.naturalWidth>0));
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
    assert.equal(await page.locator('#help a[href*="/actions/"]').count(),0);
    assert((await page.locator('#help a[href="https://github.com/Burnside999/WeeklyReport/releases"]').count())>=1);
    assert.equal(await page.locator('#help').getByText('作为网页 App 打开',{exact:false}).count(),0);
    assert(await page.locator('.apple-guide-icon').evaluateAll(items=>items.every(el=>el.complete&&el.naturalWidth>0)));
    await page.locator('.guide-index a[href="#guide-credentials"]').click();
    assert(await page.locator('#guide-credentials').evaluate(el=>el.open));
    assert(await page.locator('#guide-credentials').getByRole('link',{name:'腾讯文档开放平台 ↗',exact:true}).count());
    // All 20 steps work without a document, network writes or cross-page reloads.
    const mutations=[];page.on('request',request=>{if(request.url().includes('/api/')&&!['GET','HEAD'].includes(request.method()))mutations.push(request.url());});
    const tutorialURL=page.url();
    for (const width of [320,390,1280]) {
      await page.setViewportSize({width,height:740});
      await page.emulateMedia({reducedMotion:'no-preference'});
      await page.locator('#start-tour').click();
      for (let i=0;i<20;i++) {
        await page.locator('.tour-progress').filter({hasText:`${i+1} / 20 ·`}).waitFor();
        await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
        const geometry = await page.locator('.tour-card').evaluate(el=>{
          const r=el.getBoundingClientRect(), s=document.querySelector('.tour-spot').getBoundingClientRect();
          const t=document.querySelector('[data-tour-target]').getBoundingClientRect(),p=document.querySelector('.tour-preview').getBoundingClientRect();
          return {inside:r.left>=0 && r.top>=0 && r.right<=innerWidth+1 && r.bottom<=innerHeight+1,
            overlap:Math.max(0,Math.min(r.right,s.right)-Math.max(r.left,s.left))*Math.max(0,Math.min(r.bottom,s.bottom)-Math.max(r.top,s.top)),
            aligned:Math.abs(s.left-Math.max(p.left,t.left-5))<2 && Math.abs(s.top-Math.max(p.top,t.top-5))<2,
            visible:s.height>20 && s.width>20};
        });
        assert(geometry.inside,`tour card inside ${width} step ${i}`);
        assert.equal(geometry.overlap,0,`tour target remains visible ${width} step ${i}`);
        assert(geometry.aligned&&geometry.visible,`spotlight aligned ${width} step ${i}`);
        assert.equal(page.url(),tutorialURL,'tutorial never reloads or navigates the live page');
        if(i===1) {
          await page.getByRole('button',{name:'上一步',exact:true}).click();
          await page.locator('.tour-progress').filter({hasText:'1 / 20 ·'}).waitFor();
          await page.getByRole('button',{name:'下一步',exact:true}).click();
        }
        if(shots && [5,16,19].includes(i) && width!==320)await page.screenshot({path:shots+`/tour-${width}-${i+1}.png`});
        await page.getByRole('button',{name:i===19?'完成引导':'下一步',exact:true}).click();
      }
      await page.locator('#tour-dialog').waitFor({state:'detached'});
      await page.locator('#start-tour').click();await page.locator('#tour-dialog').waitFor();
      await page.getByLabel('跳转教程步骤').selectOption('16');await page.locator('.tour-progress').filter({hasText:'17 / 20 ·'}).waitFor();
      await page.keyboard.press('Tab');assert(await page.evaluate(()=>!!document.activeElement.closest('#tour-dialog')));
      await page.keyboard.press('Escape');await page.locator('#tour-dialog').waitFor({state:'detached'});
      assert(await page.locator('#start-tour').evaluate(el=>document.activeElement===el));
    }
    assert.deepEqual(mutations,[],'viewing a tutorial must not save configuration or send mail');
    await page.emulateMedia({reducedMotion:'reduce'});
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
    await page.locator('#connection-settings > summary').click();
    const tip=page.locator('#settings-form').getByRole('button',{name:'Access Token说明',exact:true});
    const bubble = page.locator('#field-tooltip');
    assert.equal(await tip.evaluate(el=>el.getBoundingClientRect().width),18);
    await tip.dispatchEvent('pointerenter',{pointerType:'mouse'});
    await tip.dispatchEvent('pointerleave',{pointerType:'mouse'});
    await page.waitForTimeout(350);assert(await bubble.isHidden(),'passing over a hint does not open it later');
    await tip.hover();await bubble.filter({hasText:'清空后保存'}).waitFor();
    assert(await bubble.evaluate(el=>el.getBoundingClientRect().width<=240));
    await page.mouse.move(0,0);assert(await bubble.isHidden(),'mouse leave closes immediately');
    await tip.focus();await bubble.waitFor();
    await page.keyboard.press('Escape');assert(await bubble.isHidden());
    await tip.click();await bubble.waitFor();await tip.click();assert(await bubble.isHidden());
    assert.equal(await page.locator('#settings-form [name=access_token]').getAttribute('type'),'password');
    assert.equal(await page.locator('#settings-form [name=access_token]').getAttribute('autocomplete'),'off');
    for(const width of [320,390,1280]) {
      await page.setViewportSize({width,height:740});await tip.click();await page.locator('#field-tooltip').waitFor();
      assert(await page.locator('#field-tooltip').evaluate(el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight;}));
      if(shots&&width===390)await page.screenshot({path:shots+'/tooltip-390.png'});
      await page.keyboard.press('Escape');
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
    await reader.emulateMedia({reducedMotion:'reduce'});
    await reader.locator('#start-tour').click();await reader.locator('.tour-progress').filter({hasText:'1 / 20 ·'}).waitFor();
    await reader.getByRole('button',{name:'下一步',exact:true}).click();await reader.locator('.tour-progress').filter({hasText:'2 / 20 ·'}).waitFor();
    await reader.getByRole('button',{name:'退出引导',exact:true}).click();assert.equal(await reader.locator('#tour-dialog').count(),0);
    await reader.close();assert.deepEqual(errors,[]);
    console.log('Guide navigation/search, no-document access, responsive weekdays, settings switch and Beijing timestamps passed');
  } finally {if(browser)await browser.close();server.kill('SIGTERM');}
})().catch(error=>{console.error(error);process.exitCode=1;});
