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
    await page.goto(base+'/mail');await page.locator('#username').fill('admin');await page.locator('#password').fill('browser-test-password');await page.locator('#login button').click();await page.waitForURL(url=>url.pathname==='/' );await page.locator('#document-select').waitFor();
    const documentId=await page.locator('#document-select').inputValue();
    await page.context().setExtraHTTPHeaders({'X-Document-ID':documentId});
    const autoQuery=page.locator('#auto-query');
    await autoQuery.waitFor();await page.locator('#auto-query:not(:disabled)').waitFor();
    assert(await autoQuery.isChecked());await autoQuery.uncheck();
    await page.locator('#schedule').filter({hasText:'自动查询已关闭'}).waitFor();
    await page.reload();await page.locator('#auto-query:not(:disabled)').waitFor();
    assert(!(await autoQuery.isChecked()));assert(!(await page.locator('#check').isDisabled()));
    await autoQuery.check();await page.locator('#schedule').filter({hasText:'自动检查'}).waitFor();
    // The same person is grouped within one rule, even when records interleave.
    // Identically named rules must remain separate; totals still count tasks.
    await page.route('**/api/status', async route=>{
      const response=await route.fetch(), data=await response.json();
      const record={person:'张三',item:'周报',sheet:'研发',sheet_id:'tab1',column:'C',row:2,end_row:2,rule_id:'r1'};
      Object.assign(data,{last_success:'2026-09-18T10:00:00+08:00',stale:false,people_count:2,rule_count:2,
        document_url:'https://docs.qq.com/sheet/test',records:[record,{...record,person:'李四',row:3,end_row:3},
        {...record,row:4,end_row:6,column:'C,D'}, {...record,rule_id:'r2',row:7,end_row:7}]});
      await route.fulfill({response,json:data});
    });
    await page.reload();await page.locator('#records tr').nth(2).waitFor();
    assert.equal(await page.locator('#records tr').count(),3);
    assert.equal(await page.locator('#records tr').first().locator('td').first().textContent(),'张三2 项未填');
    assert.deepEqual(await page.locator('#records tr').first().locator('td').nth(1).locator('small').allTextContents(),['C 列 · 第 2 行','C,D 列 · 第 4–6 行']);
    assert.equal(await page.locator('#records tr').nth(2).locator('td').first().textContent(),'张三1 项未填');
    assert.equal(await page.locator('#records a').first().getAttribute('href'),'https://docs.qq.com/sheet/test?tab=tab1');
    assert.equal(await page.locator('#people').textContent(),'2');
    assert.equal(await page.locator('#detail-count').textContent(),'4 条');
    for(const width of [320,390,1280]) {
      await page.setViewportSize({width,height:844});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`grouped records overflow at ${width}`);
    }
    await page.setViewportSize({width:390,height:844});
    await page.unroute('**/api/status');
    await page.locator('#nav-mail').click();await page.getByText('还没有邮件模板',{exact:true}).waitFor();
    assert.deepEqual(await page.locator('.bottom-nav span').allTextContents(),['填写情况','监听管理','邮件模板','设置','变量表','管理']);
    await page.locator('#add-template').click();await page.locator('#template-form').waitFor({state:'visible'});
    for(let i=0;i<4;i++)await page.locator('#add-recipient').click();
    assert(await page.locator('#add-recipient').isDisabled());
    for(let i=0;i<5;i++)await page.locator('#mail-recipients input').nth(i).fill(`user${i}@example.com`);
    await page.locator('#mail-subject').fill('报告 {{global.date}}');
    await page.locator('#mail-body').fill('人数 {{global.listencount}}\n{{global.invalid}}');
    await page.locator('#subject-highlight .valid-variable').waitFor();
    await page.locator('#body-highlight .invalid-variable').waitFor();
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
    await page.locator('input[name=mail_mode][value=auto]').check();assert.equal(await page.locator('#schedule-variable').count(),0);await page.locator('[name=schedule_weekday][value="0"]').check();await page.locator('[name=schedule_weekday][value="4"]').check();await page.locator('#schedule-clock').fill('09:00');
    await page.locator('[name=schedule_weekday][value="0"]').uncheck();
    await page.locator('[name=schedule_weekday][value="4"]').uncheck();
    assert.equal(await page.locator('[name=schedule_weekday]').first().evaluate(e=>e.validity.valid),false);
    await page.locator('#schedule-kind').selectOption('fixed');
    assert(await page.locator('#schedule-clock').isDisabled());
    assert(!(await page.locator('#schedule-fixed').isDisabled()));
    await page.locator('#schedule-kind').selectOption('weekly');
    await page.locator('[name=schedule_weekday][value="0"]').check();
    await page.locator('[name=schedule_weekday][value="4"]').check();
    for(const width of [320,390,600,1280]) {
      await page.setViewportSize({width,height:844});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`mail form overflow at ${width}`);
      assert(await page.locator('#template-form .trigger-mode input').evaluateAll(inputs=>inputs.every(e=>{
        const r=e.getBoundingClientRect();return r.width===18 && r.height===18;
      })),`radio geometry at ${width}`);
    }
    await page.setViewportSize({width:390,height:844});
    for(const name of ['global.personlist','global.date','global.time','global.lastquery'])
      assert.equal(await page.locator(`#condition-variable option[value="${name}"]`).count(),0);
    assert.equal(await page.locator('#mail-variable, #insert-variable, #condition-hint').count(),0);
    assert.equal(await page.locator('#condition-variable option[value="global.healthy"]').count(),1);
    await page.locator('#condition-variable').selectOption('global.listencount');await page.locator('#condition-value').fill('0');await page.locator('#save-template').click();await page.locator('#template-form').waitFor({state:'hidden'});
    const templates=await (await page.request.get(base+'/api/templates')).json();
    assert.deepEqual(templates.find(t=>t.mode==='auto').schedule.weekdays,[0,4]);
    const waiting=page.getByRole('button',{name:'等待自动触发',exact:true});await waiting.waitFor();assert(await waiting.isDisabled());assert.equal(await waiting.evaluate(e=>getComputedStyle(e).cursor),'not-allowed');
    await page.request.post(base+'/test/tick',{data:{},headers:{'X-Requested-With':'WeeklyReport'}});
    const reset=page.getByRole('button',{name:'重置自动触发',exact:true});await reset.waitFor({timeout:10000});assert(!(await reset.isDisabled()));
    page.once('dialog',d=>d.accept());await reset.click();await waiting.waitFor();
    await page.request.post(base+'/test/tick',{data:{},headers:{'X-Requested-With':'WeeklyReport'}});await reset.waitFor({timeout:10000});
    sent=await (await page.request.get(base+'/test/deliveries')).json();assert.equal(sent.length,4);
    await page.locator('#nav-settings').click();assert.equal(await page.locator('[name=smtp_recipient]').count(),0);
    await page.locator('#settings details.advanced summary').click();
    await page.locator('#settings details:not(.advanced) summary').click();
    assert.equal(await page.locator('[name=file_id],[name=refresh_token],[name=client_secret],[name=clear_secrets],[name=clear_smtp_password]').count(),0);
    await page.locator('#settings [name=access_token]').fill('ui-test-token');
    await page.getByRole('button',{name:'保存高级设置',exact:true}).click();
    await page.locator('#toast').filter({hasText:'设置已保存'}).waitFor();
    await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/settings')),page.reload()]);
    await page.locator('#settings [name=access_token]').waitFor({state:'attached'});
    await page.locator('#settings details.advanced summary').click();
    await page.locator('#settings details:not(.advanced) summary').click();
    assert.equal(await page.locator('#settings [name=access_token]').inputValue(),'ui-test-token');
    assert.equal(await page.locator('#settings [name=access_token]').getAttribute('type'),'password');
    for(const width of [320,390,600,768,1280]) {
      await page.setViewportSize({width,height:844});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`settings overflow at ${width}`);
      assert(await page.locator('#settings input,#settings select').evaluateAll(inputs=>inputs.every(e=>{
        const r=e.getBoundingClientRect();return !r.width || (r.left>=0 && r.right<=innerWidth);
      })),`settings controls overflow at ${width}`);
      assert(await page.locator('#settings .panel').first().evaluate(e=>{
        const input=e.querySelector('input').getBoundingClientRect(), button=e.querySelector('button').getBoundingClientRect();
        return button.top-input.bottom>=16;
      }),`save button spacing at ${width}`);
    }
    await page.locator('#settings [name=access_token]').fill('');
    await page.getByRole('button',{name:'保存高级设置',exact:true}).click();
    await page.locator('#toast').filter({hasText:'设置已保存'}).waitFor();
    const cleared=await (await page.request.get(base+'/api/settings',{headers:{'X-Document-ID':documentId}})).json();
    assert.equal(cleared.access_token,'');assert(!('smtp_password' in cleared));
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
    // List loops: syntax diagnostics, local-variable highlighting and send gates.
    await page.unroute('**/api/variables');
    const headers={'X-Requested-With':'WeeklyReport','X-Document-ID':documentId};
    const rule={name:'研发',variable_name:'listener1',sheet_id:'tab1',sheet_name:'研发',owner_column:1,target_column:3,start_row:2,end_row:5,enabled:true};
    const created=await (await page.request.post(base+'/api/rules',{headers,data:rule})).json();
    const listener=created.find(r=>r.variable_name==='listener1');assert(listener);
    await page.locator('#nav-mail').click();
    await page.locator('#add-template').click();await page.locator('#template-form').waitFor({state:'visible'});
    await page.locator('#mail-recipients input').fill('loops@example.com');
    await page.locator('#mail-subject').fill('循环测试');
    await page.locator('#mail-body').fill('{% for l in global.alllistener %}{{l.typo}}{% endfor %}');
    await page.locator('#template-form.syntax-invalid').waitFor();
    assert.equal(await page.locator('#mail-editor-state').textContent(),'❗存在语法错误');
    await page.locator('#save-template').click();await page.locator('#template-form').waitFor({state:'hidden'});
    const loopCard=page.locator('#template-list article').filter({has:page.getByRole('heading',{name:'循环测试',exact:true})});
    await loopCard.locator('.syntax-warning').waitFor();
    assert(await loopCard.getByRole('button',{name:'发送邮件',exact:true}).isDisabled());
    assert.equal(await loopCard.evaluate(e=>getComputedStyle(e).borderTopColor),'rgb(198, 40, 40)');
    await loopCard.getByRole('button',{name:'编辑',exact:true}).click();
    await page.locator('#mail-body').fill('📬{% for l in [listener1] with l.enable == true %}{{l.name}}{% endfor %}');
    await page.locator('#body-highlight .valid-variable').waitFor();
    await page.locator('#template-form:not(.syntax-invalid)').waitFor();
    assert.equal(await page.locator('#mail-editor-state').textContent(),'');
    assert((await page.locator('#body-highlight .syntax-keyword').count())>=4);
    // Observe a full polling interval: no temporary red or pending local variable.
    await page.evaluate(()=>{
      window.highlightGlitch=false;
      const mirror=document.querySelector('#body-highlight');
      window.highlightObserver=new MutationObserver(()=>{if(mirror.querySelector('.invalid-variable,.pending-variable'))window.highlightGlitch=true;});
      window.highlightObserver.observe(mirror,{subtree:true,childList:true,attributes:true});
    });
    await page.waitForTimeout(5600);
    assert.equal(await page.evaluate(()=>{window.highlightObserver.disconnect();return window.highlightGlitch;}),false);
    await page.locator('#save-template').click();await page.locator('#template-form').waitFor({state:'hidden'});
    await loopCard.getByRole('button',{name:'发送邮件',exact:true}).click();
    await loopCard.getByRole('button',{name:'发送成功',exact:true}).waitFor();
    sent=await (await page.request.get(base+'/test/deliveries')).json();
    assert.equal(sent.at(-1).text,'📬研发');
    const count=sent.length;
    await page.request.put(base+'/api/rules/'+listener.id,{headers,data:{...rule,variable_name:'Renamed'}});
    await loopCard.locator('.syntax-warning').waitFor({timeout:10000});
    assert(await loopCard.getByRole('button',{name:'发送成功',exact:true}).isDisabled());
    const invalidated=(await (await page.request.get(base+'/api/templates',{headers:{'X-Document-ID':documentId}})).json()).find(t=>t.subject==='循环测试');
    const blocked=await page.request.post(base+'/api/templates/'+invalidated.id+'/send',{headers,data:{revision:invalidated.revision}});
    assert.equal(blocked.status(),400);
    assert.equal((await (await page.request.get(base+'/test/deliveries')).json()).length,count);
    await loopCard.getByRole('button',{name:'编辑',exact:true}).click();
    await page.locator('#mail-body').fill('{% for l in global.alllistener %}{{l.name}}{% endfor %}');
    await page.locator('#body-highlight .valid-variable').waitFor();
    await page.locator('#save-template').click();await page.locator('#template-form').waitFor({state:'hidden'});
    await page.request.delete(base+'/api/rules/'+listener.id,{headers,data:{}});
    await page.reload();await loopCard.waitFor();
    assert.equal(await loopCard.locator('.syntax-warning').count(),0);
    assert(!(await loopCard.getByRole('button',{name:'发送邮件',exact:true}).isDisabled()));
    assert.deepEqual(errors,[]);console.log('Mobile mail editor, highlight, manual confirmation and automatic reset: PASS');
  }finally{if(browser)await browser.close();server.kill('SIGTERM');}
})().catch(error=>{console.error(error);process.exitCode=1;});
