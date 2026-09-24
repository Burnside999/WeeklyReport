const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require((process.env.PLAYWRIGHT_NODE_MODULES || process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES)+'/playwright');
const base='http://127.0.0.1:18081';
const server=spawn('python',['tests/browser_server.py'],{env:{...process.env,PYTHONPATH:process.cwd(),WEB_PUSH_ENABLED:'true',VAPID_SUBJECT:'mailto:test@example.com'},stdio:'inherit'});
(async()=>{
  let browser;
  try {
    let ready=false;
    for(let i=0;i<50;i++){try{if((await fetch(base+'/healthz')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,100));}
    assert(ready);
    browser=await chromium.launch({headless:true});
    const context=await browser.newContext({viewport:{width:390,height:844}});
    const page=await context.newPage(),errors=[];
    page.on('pageerror',e=>errors.push(String(e)));
    await page.goto(base+'/login');await page.locator('#username').fill('admin');await page.locator('#password').fill('browser-test-password');
    await page.locator('#login button').click();await page.waitForURL(u=>u.pathname==='/');
    await page.locator('#document-select').waitFor();const did=await page.locator('#document-select').inputValue();
    await page.locator('#device-notifications summary').click();
    await page.locator('#push-state').filter({hasText:'本版本设备推送支持'}).waitFor();
    assert(await page.locator('#push-enable').isDisabled(),'do not use Google push on desktop Chromium');
    await page.evaluate(()=>navigator.serviceWorker.ready);
    const manifest=await (await context.request.get(base+'/manifest.webmanifest')).json();assert.equal(manifest.display,'standalone');
    const items=[];
    for(const title of ['第一条主推送','第二条主推送']) {
      const response=await context.request.post(base+'/api/templates',{headers:{'X-Requested-With':'WeeklyReport','X-Document-ID':did},data:{recipients:['test@example.com'],subject:title,body:'正文',mode:'manual'}});
      assert(response.ok());items.push(await response.json());
    }
    await page.goto(base+'/mail?doc='+did);
    let card=page.locator('.rule-card').filter({hasText:'第一条主推送'});
    await card.getByRole('button',{name:'设为主推送',exact:true}).click();await card.locator('.primary-push-badge').waitFor();
    page.once('dialog',dialog=>dialog.accept());
    await page.locator('.rule-card').filter({hasText:'第二条主推送'}).getByRole('button',{name:'设为主推送',exact:true}).click();
    await page.locator('.rule-card').filter({hasText:'第二条主推送'}).locator('.primary-push-badge').waitFor();
    assert.equal(await page.locator('.primary-push-badge').count(),1);
    await page.reload();await page.locator('.primary-push-badge').waitFor();
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'mail layout fits mobile');
    await page.locator('.rule-card').filter({hasText:'第二条主推送'}).getByRole('button',{name:'取消主推送',exact:true}).click();
    await page.waitForFunction(()=>document.querySelectorAll('.primary-push-badge').length===0);
    await page.goto(base+'/?doc='+did);await page.locator('#device-notifications summary').click();
    await page.locator('#push-state').filter({hasText:'本版本设备推送支持'}).waitFor();
    if(process.env.PWA_SCREENSHOT)await page.screenshot({path:process.env.PWA_SCREENSHOT,fullPage:true});
    // iPhone install instructions, without mocking a successful Apple delivery.
    const iphone=await browser.newContext({viewport:{width:390,height:844},userAgent:'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1'});
    await iphone.addCookies(await context.cookies());const phone=await iphone.newPage();
    await phone.goto(base+'/?doc='+did);await phone.locator('#device-notifications summary').click();
    await phone.locator('#push-state').filter({hasText:'添加到主屏幕'}).waitFor();assert(await phone.locator('#push-enable').isDisabled());
    if(process.env.PWA_SCREENSHOT)await phone.screenshot({path:process.env.PWA_SCREENSHOT.replace('.png','-iphone.png'),fullPage:true});
    await iphone.close();
    // Deep link survives an expired login; no open redirect.
    await page.locator('#logout').click();await page.waitForURL(u=>u.pathname==='/login');
    await page.goto(base+'/?doc='+did);await page.locator('#username').fill('admin');await page.locator('#password').fill('browser-test-password');
    await page.locator('#login button').click();await page.waitForURL(u=>u.pathname==='/' && u.searchParams.get('doc')===did);
    await page.evaluate(()=>navigator.serviceWorker.ready);
    await page.waitForFunction(()=>!!navigator.serviceWorker.controller);
    // Playwright's context offline toggle does not reliably affect worker-owned
    // network requests. Stop the fixture to test a real network failure instead.
    const stopped=new Promise(resolve=>server.once('exit',resolve));server.kill('SIGTERM');await stopped;
    await page.reload();await page.getByRole('heading',{name:'暂时无法连接'}).waitFor();
    assert.deepEqual(errors,[]);console.log('PWA install guidance, primary selection, login deep link, mobile layout and offline browser tests passed');
  } finally {if(browser)await browser.close();server.kill('SIGTERM');}
})().catch(error=>{console.error(error);process.exitCode=1;});
