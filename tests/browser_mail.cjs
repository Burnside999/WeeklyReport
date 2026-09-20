const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {chromium} = require((process.env.PLAYWRIGHT_NODE_MODULES || process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES)+'/playwright');
const base = 'http://127.0.0.1:18081';
const server=spawn('python',['tests/browser_server.py'],{env:{...process.env,PYTHONPATH:process.cwd()},stdio:'inherit'});
(async()=>{
  let browser;
  try {
    let ready=false;
    for(let i=0;i<50;i++){try{if((await fetch(base+'/healthz')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,100));}
    assert(ready,'test server started');
    browser=await chromium.launch({headless:true});
    const page=await browser.newPage({viewport:{width:390,height:844}}), errors=[];
    page.on('pageerror',error=>errors.push(String(error)));
    await page.goto(base+'/mail');await page.locator('#password').fill('browser-test-password');await page.locator('#login button').click();await page.waitForURL(base+'/');
    const autoQuery=page.locator('#auto-query');
    await autoQuery.waitFor();await page.waitForFunction(()=>!document.querySelector('#auto-query').disabled);
    assert(await autoQuery.isChecked());await autoQuery.uncheck();
    await page.waitForFunction(()=>document.querySelector('#schedule').textContent==='自动查询已关闭');
    await page.reload();await page.waitForFunction(()=>!document.querySelector('#auto-query').disabled);
    assert(!(await autoQuery.isChecked()));assert(!(await page.locator('#check').isDisabled()));
    await autoQuery.check();await page.waitForFunction(()=>document.querySelector('#schedule').textContent.includes('自动检查'));
    await page.locator('#nav-mail').click();await page.getByText('还没有邮件模板',{exact:true}).waitFor();
    assert.deepEqual(await page.locator('.bottom-nav span').allTextContents(),['填写情况','监听管理','邮件模板','设置','变量表']);
    await page.locator('#add-template').click();await page.locator('#template-form').waitFor({state:'visible'});
    for(let i=0;i<4;i++)await page.locator('#add-recipient').click();
    assert(await page.locator('#add-recipient').isDisabled());
    for(let i=0;i<5;i++)await page.locator('#mail-recipients input').nth(i).fill(`user${i}@example.com`);
    await page.locator('#mail-subject').fill('报告 {{global.date}}');
    await page.locator('#mail-body').fill('人数 {{global.listencount}}\n{{global.invalid}}');
    assert.equal(await page.locator('#subject-highlight .valid-variable').count(),1);
    assert.equal(await page.locator('#body-highlight .invalid-variable').count(),1);
    assert.equal(await page.locator('#subject-highlight .valid-variable').evaluate(e=>getComputedStyle(e).color),'rgb(21, 101, 192)');
    await page.locator('#mail-body').fill('人数 {{global.listencount}}\n<img src=x onerror=alert(1)>');
    await page.screenshot({path:'/tmp/mail-editor-mobile.png',fullPage:true});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'mobile has no page overflow');
    await page.locator('#save-template').click();await page.locator('#template-form').waitFor({state:'hidden'});
    await page.getByRole('button',{name:'发送邮件',exact:true}).click();
    await page.getByRole('button',{name:'发送成功',exact:true}).waitFor();assert(await page.getByRole('button',{name:'发送成功',exact:true}).isDisabled());
    let sent=await (await page.request.get(base+'/test/deliveries')).json();assert.equal(sent.length,1);assert.equal(sent[0].subject,'报告 2026-09-18');assert.equal(sent[0].recipients.length,5);
    await page.reload();const send=page.getByRole('button',{name:'发送邮件',exact:true});await send.waitFor();assert(!(await send.isDisabled()));
    let confirmation='';page.once('dialog',async dialog=>{confirmation=dialog.message();await dialog.accept();});await send.click();await page.getByRole('button',{name:'发送成功',exact:true}).waitFor();assert(confirmation.includes('确定要再次触发'));
    await page.locator('#add-template').click();await page.locator('#template-form').waitFor({state:'visible'});
    await page.locator('#mail-recipients input').fill('auto@example.com');await page.locator('#mail-subject').fill('自动邮件');await page.locator('#mail-body').fill('当前日期 {{global.date}}');
    await page.locator('input[name=mail_mode][value=auto]').check();await page.locator('#schedule-variable').fill('global.week.friday');await page.locator('#schedule-clock').fill('09:00');
    for(const name of ['global.personlist','global.date','global.time','global.lastquery'])
      assert.equal(await page.locator(`#condition-variable option[value="${name}"]`).count(),0);
    assert.equal(await page.locator('#mail-variable, #insert-variable, #condition-hint').count(),0);
    assert.equal(await page.locator('#condition-variable option[value="global.healthy"]').count(),1);
    await page.locator('#condition-variable').selectOption('global.listencount');await page.locator('#condition-value').fill('0');await page.locator('#save-template').click();await page.locator('#template-form').waitFor({state:'hidden'});
    const waiting=page.getByRole('button',{name:'等待自动触发',exact:true});await waiting.waitFor();assert(await waiting.isDisabled());
    await page.request.post(base+'/test/tick',{data:{},headers:{'X-Requested-With':'WeeklyReport'}});
    const reset=page.getByRole('button',{name:'重置自动触发',exact:true});await reset.waitFor({timeout:10000});assert(!(await reset.isDisabled()));
    page.once('dialog',d=>d.accept());await reset.click();await waiting.waitFor();
    await page.request.post(base+'/test/tick',{data:{},headers:{'X-Requested-With':'WeeklyReport'}});await reset.waitFor({timeout:10000});
    sent=await (await page.request.get(base+'/test/deliveries')).json();assert.equal(sent.length,4);
    await page.locator('#nav-settings').click();assert.equal(await page.locator('[name=smtp_recipient]').count(),0);
    await page.setViewportSize({width:320,height:740});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    // Long URLs/names must remain readable without horizontal scrolling at every breakpoint.
    await page.route('**/api/variables', async route=>{
      const response=await route.fetch(), data=await response.json();
      data.rows.push({name:'listener'+'X'.repeat(55)+'.personlist',type:'string',description:'很长的未交名单',value:'张三、李四、'.repeat(80)});
      data.rows.push({name:'global.testurl',type:'url',description:'文档地址',value:'https://docs.qq.com/sheet/'+'A'.repeat(180)});
      await route.fulfill({response,json:data});
    });
    await page.locator('#nav-variables').click();await page.locator('#variable-rows tr').first().waitFor();
    assert.equal(await page.locator('#variable-rows tr').filter({hasText:'global.personcount'}).locator('td').last().textContent(),'null');
    for(const width of [320,390,600,768,1280]) {
      await page.setViewportSize({width,height:844});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`page overflow at ${width}`);
      assert(await page.locator('.variables-wrap').evaluate(e=>e.scrollWidth<=e.clientWidth),`table overflow at ${width}`);
      assert(await page.locator('.variables-table td').evaluateAll(cells=>cells.every(e=>e.scrollWidth<=e.clientWidth)),`cell overflow at ${width}`);
      if(width===390 || width===1280) await page.screenshot({path:`/tmp/variables-${width}.png`,fullPage:true});
    }
    assert.deepEqual(errors,[]);console.log('Mobile mail editor, highlight, manual confirmation and automatic reset: PASS');
  }finally{if(browser)await browser.close();server.kill('SIGTERM');}
})().catch(error=>{console.error(error);process.exitCode=1;});

