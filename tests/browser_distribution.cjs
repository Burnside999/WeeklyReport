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
    assert(ready);browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:390,height:844}}),errors=[],bodies=[];
    page.on('pageerror',e=>errors.push(String(e)));page.on('request',r=>{if(r.method()==='PUT' || r.method()==='POST')bodies.push([new URL(r.url()).pathname,r.postDataJSON()]);});
    await page.goto(base+'/login');await page.locator('#username').fill('admin');await page.locator('#password').fill('browser-test-password');await page.locator('#login button').click();await page.waitForURL(u=>u.pathname==='/');await page.locator('#document-select').waitFor();
    const did=await page.locator('#document-select').inputValue();
    await page.locator('#nav-settings').click();await page.locator('#settings details').first().locator('summary').click();
    const source=page.locator('#source-form');
    assert.equal(await source.locator('input[name=sheet_id],input[name=sheet_name]').count(),0);
    assert(await source.locator('[name=distribution][value=column]').isChecked());
    await source.locator('[name=distribution][value=row]').check();
    assert(await source.getByText('姓名所在行',{exact:true}).isVisible());assert(await source.getByText('起始列',{exact:true}).isVisible());
    assert(await source.getByText('姓名所在列',{exact:true}).isHidden());
    await source.locator('[name=sheet_id]').selectOption('tab1');await source.locator('[name=row]').fill('1');await source.locator('[name=start_column]').fill('B');await source.locator('[name=end_column]').fill('C');
    await source.locator('[type=submit]').click();await page.locator('#toast').filter({hasText:'名单已更新'}).waitFor();assert.equal(await page.locator('#roster-names label').count(),2);
    const sourceBody=bodies.findLast(([path])=>path==='/api/roster')[1];assert.equal(sourceBody.distribution,'row');assert(!('column' in sourceBody));assert(!('start_row' in sourceBody));assert.equal(sourceBody.sheet_name,'研发');
    await page.reload();await page.locator('#settings details').first().locator('summary').click();
    await source.locator('[name=distribution][value=row]:checked').waitFor();await source.locator('option:checked').filter({hasText:'研发'}).waitFor({state:'attached'});
    assert.equal(await source.locator('[name=end_column]').inputValue(),'C');
    await page.locator('#nav-manage').click();await page.locator('#add-rule').click();const form=page.locator('#rule-form');
    assert(await form.locator('[name=distribution][value=column]').isChecked());assert.equal(await form.locator('input[name=sheet_id],input[name=sheet_name]').count(),0);
    await form.locator('[name=name]').fill('横向周报');await form.locator('[name=variable_name]').fill('weekly');await form.locator('[name=sheet_id]').selectOption('tab1');
    await form.locator('[name=distribution][value=row]').check();assert(await form.getByText('责任人所在行',{exact:true}).isVisible());assert(await form.getByText('需要检查的行',{exact:true}).isVisible());assert(await form.getByText('责任人所在列',{exact:true}).isHidden());
    await form.locator('[name=owner_row]').fill('1');await form.locator('[name=target_rows]').fill('2');await form.locator('[name=start_column]').fill('B');await form.locator('[name=end_column]').fill('C');
    for(const width of [320,390,768,1280]){await page.setViewportSize({width,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`overflow ${width}`);}
    await form.locator('[type=submit]').click();await form.waitFor({state:'hidden'});await page.locator('.rule-meta').filter({hasText:'行分布 · 责任人第 1 行 → 检查第 2 行 · B–C 列'}).waitFor();
    const ruleBody=bodies.findLast(([path])=>path==='/api/rules')[1];assert.equal(ruleBody.distribution,'row');assert(!('owner_column' in ruleBody));assert(!('start_row' in ruleBody));
    await page.locator('#rules-list').getByRole('button',{name:'编辑',exact:true}).click();await form.locator('[name=distribution][value=row]:checked').waitFor();assert.equal(await form.locator('[name=target_rows]').inputValue(),'2');await form.locator('option:checked').filter({hasText:'研发'}).waitFor({state:'attached'});
    await form.locator('[name=distribution][value=column]').check();await form.locator('[name=distribution][value=row]').check();assert.equal(await form.locator('[name=end_column]').inputValue(),'C');await page.locator('#cancel-rule').click();
    const check=await page.request.post(base+'/test/check?doc='+did,{data:{}});assert(check.ok());
    await page.locator('#nav-home').click();await page.locator('#records td').filter({hasText:'张三'}).waitFor();assert(await page.locator('#records').innerText().then(t=>t.includes('第 2 行 · B 列')));
    await page.locator('#nav-variables').click();await page.locator('#variable-rows').getByText('weekly.personrow',{exact:true}).waitFor();assert.equal(await page.locator('#variable-rows').getByText('weekly.personcol',{exact:true}).count(),0);assert(await page.locator('#variable-rows').innerText().then(t=>t.includes('责任人所在行')));
    // Returning to column mode submits only column fields and updates all labels.
    await page.locator('#nav-manage').click();await page.locator('#rules-list').getByRole('button',{name:'编辑',exact:true}).click();await form.locator('option:checked').filter({hasText:'研发'}).waitFor({state:'attached'});await form.locator('[name=distribution][value=column]').check();await form.locator('[name=end_row]').fill('3');await form.locator('[type=submit]').click();await form.waitFor({state:'hidden'});await page.locator('.rule-meta').filter({hasText:'列分布'}).waitFor();
    const edited=bodies.findLast(([path])=>path.startsWith('/api/rules/'))[1];assert(!('owner_row' in edited));assert.equal(edited.distribution,'column');
    // A failed list refresh cannot leave a manually editable or stale sheet selection.
    await page.route('**/api/sheets',route=>route.fulfill({status:502,contentType:'application/json',body:JSON.stringify({error:'测试读取失败'})}));
    await page.locator('#rules-list').getByRole('button',{name:'编辑',exact:true}).click();await form.locator('option').filter({hasText:'读取失败'}).waitFor({state:'attached'});assert.equal(await form.locator('[name=sheet_id]').inputValue(),'');assert.equal(await form.locator('[name=sheet_id]').evaluate(e=>e.validity.valid),false);
    assert.deepEqual(errors,[]);console.log('Sheet dropdowns, row/column forms, persistence, row statistics, variable labels and responsive layouts: PASS');
  } finally {if(browser)await browser.close();server.kill('SIGTERM');}
})().catch(error=>{console.error(error);process.exitCode=1;});
