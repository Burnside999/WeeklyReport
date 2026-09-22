const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require((process.env.PLAYWRIGHT_NODE_MODULES || process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES)+'/playwright');
const base='http://127.0.0.1:18081';
const server=spawn('python',['tests/browser_server.py'],{env:{...process.env,PYTHONPATH:process.cwd()},stdio:'inherit'});
(async()=>{
  let browser;
  try {
    let ready=false;
    for(let i=0;i<50;i++){try{if((await fetch(base+'/healthz')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,100));}
    assert(ready);browser=await chromium.launch({headless:true});
    const context=await browser.newContext({viewport:{width:390,height:844}}),page=await context.newPage(),errors=[],bodies=[];
    page.on('pageerror',e=>errors.push(String(e)));
    page.on('request',r=>{if(r.url().endsWith('/api/login'))bodies.push(r.postDataJSON());});
    await page.goto(base+'/login');await page.locator('#login button').waitFor();
    await page.locator('#automatic').check();assert(await page.locator('#remember').isChecked());
    await page.locator('#remember').uncheck();assert(!(await page.locator('#automatic').isChecked()));
    await page.locator('#username').fill('admin');await page.locator('#password').fill('browser-test-password');
    await page.locator('#automatic').check();await page.locator('#login button').click();await page.waitForURL(u=>u.pathname==='/');
    await page.locator('#document-select').waitFor();
    assert.equal(bodies.length,1);assert(!('password' in bodies[0]));assert(!JSON.stringify(bodies).includes('browser-test-password'));
    for(const width of [320,390,768,1280]) {
      await page.setViewportSize({width,height:844});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`overflow ${width}`);
      const brand=await page.locator('.brand').boundingBox(),select=await page.locator('#document-select').boundingBox(),logout=await page.locator('#logout').boundingBox();
      assert(select.x>=brand.x+brand.width);assert(select.x+select.width<=logout.x);assert(Math.abs(select.y+select.height/2-brand.y-brand.height/2)<2);
    }
    // New browser session retains only the persistent device cookie.
    const cookies=(await context.cookies()).filter(c=>c.name==='wr_remember');assert.equal(cookies.length,1);assert(cookies[0].httpOnly);
    const next=await browser.newContext();await next.addCookies(cookies);const tab=await next.newPage();
    await tab.goto(base+'/');await tab.waitForURL(u=>u.pathname==='/' && u.searchParams.has('doc'));await tab.locator('#logout').click();
    await tab.waitForURL(u=>u.pathname==='/login');await tab.locator('#login button:not(:disabled)').waitFor();assert.equal(await tab.locator('#username').inputValue(),'admin');
    assert(await tab.locator('#remember').isChecked());assert(!(await tab.locator('#automatic').isChecked()));assert.equal(await tab.locator('#password').inputValue(),'');
    await tab.locator('#login button').click();await tab.waitForURL(u=>u.pathname==='/');
    await tab.locator('#logout').click();await tab.waitForURL(u=>u.pathname==='/login');await tab.locator('#remember:checked').waitFor();
    await tab.locator('#remember').uncheck();await tab.locator('#login button:not(:disabled)').waitFor();await tab.reload();
    await tab.locator('#login button:not(:disabled)').waitFor();assert.equal(await tab.locator('#username').inputValue(),'');assert(await tab.locator('#password').evaluate(e=>e.required));
    await tab.emulateMedia({reducedMotion:'reduce'});assert.equal(await tab.locator('.login-box').evaluate(e=>getComputedStyle(e).animationName),'none');
    assert.deepEqual(errors,[]);console.log('Encrypted login, remember/auto dependencies, browser restart, logout, forget and responsive header: PASS');
    await next.close();await context.close();
  } finally {if(browser)await browser.close();server.kill('SIGTERM');}
})().catch(error=>{console.error(error);process.exitCode=1;});
