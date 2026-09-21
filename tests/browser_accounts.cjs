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
    assert(ready);
    browser=await chromium.launch({headless:true});
    const admin=await browser.newPage({viewport:{width:390,height:844}}), errors=[];
    admin.on('pageerror',e=>errors.push(String(e)));
    async function login(page,name){await page.goto(base+'/login');await page.locator('#username').fill(name);await page.locator('#password').fill('browser-test-password');await page.locator('#login button').click();await page.waitForURL(url=>url.pathname==='/');}
    await login(admin,'admin');await admin.locator('#nav-admin').waitFor();
    await admin.locator('#nav-admin').click();await admin.locator('#add-user').click();
    await admin.locator('#user-form [name=username]').fill('alice');await admin.locator('#user-form [name=password]').fill('browser-test-password');
    await admin.locator('#user-form button[type=submit]').click();await admin.locator('#user-form').waitFor({state:'hidden'});
    const card=admin.locator('#user-list article').filter({has:admin.getByRole('heading',{name:'alice',exact:true})});await card.waitFor();
    await admin.locator('#admin details summary').click();await admin.locator('#smtp-form [name=smtp_password]').fill('shared-smtp-password');
    await admin.locator('#smtp-form button[type=submit]').click();await admin.locator('#toast').filter({hasText:'SMTP 设置已保存'}).waitFor();
    for(const width of [320,390,768,1280]){await admin.setViewportSize({width,height:844});assert(await admin.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`admin overflow ${width}`);}
    const context=await browser.newContext({viewport:{width:390,height:844}}), alice=await context.newPage();alice.on('pageerror',e=>errors.push(String(e)));
    await login(alice,'alice');await alice.locator('#create-first-document').waitFor();
    assert(await alice.locator('.bottom-nav').isHidden());assert(await alice.locator('#nav-admin').isHidden());
    await alice.locator('#create-first-document').click();
    assert.equal(await alice.locator('#document-form [name=name]').inputValue(),'文档1');
    assert(await alice.locator('#document-credentials').isVisible());
    await alice.locator('#document-form [name=url]').fill('https://docs.qq.com/sheet/NOTSHEET');
    for(const [name,value] of Object.entries({client_id:'client',open_id:'openid',access_token:'token'}))await alice.locator(`#document-form [name=${name}]`).fill(value);
    await alice.locator('#document-form button[type=submit]').click();await alice.locator('#document-error').filter({hasText:'不是在线表格'}).waitFor();
    await alice.locator('#document-form [name=url]').fill('https://docs.qq.com/sheet/FIRST');
    await alice.locator('#document-form button[type=submit]').click();await alice.locator('#document-select').waitFor();
    const first=await alice.locator('#document-select').inputValue();assert(first);
    await alice.locator('#nav-manage').click();await alice.locator('#add-rule').click();
    await alice.locator('#rule-form [name=name]').fill('Alice rule');await alice.locator('#rule-form [name=sheet_id]').fill('tab1');
    await alice.locator('#rule-form button[type=submit]').click();await alice.locator('#rule-form').waitFor({state:'hidden'});
    assert.equal(await alice.locator('#rules-list article').count(),1);
    // New-document option is the final option; credentials are inherited.
    await alice.locator('#document-select').selectOption('__new__');await alice.locator('#document-dialog').waitFor();
    assert.equal(await alice.locator('#document-form [name=name]').inputValue(),'文档2');
    assert(await alice.locator('#document-credentials').isHidden());
    await alice.locator('#document-form [name=url]').fill('https://docs.qq.com/sheet/SECOND');
    await alice.locator('#document-form button[type=submit]').click();await alice.waitForURL(url=>url.pathname==='/' && url.searchParams.get('doc')!==first);
    await alice.locator('#document-select').waitFor();const second=await alice.locator('#document-select').inputValue();assert.notEqual(first,second);
    await alice.locator('#nav-manage').click();await alice.locator('#rules-list .empty').waitFor();
    const otherTab=await context.newPage();await otherTab.goto(base+'/manage?doc='+first);await otherTab.locator('#rules-list article').waitFor();
    assert.equal(await otherTab.locator('#document-select').inputValue(),first);
    assert.equal(await alice.locator('#document-select').inputValue(),second);
    await alice.locator('#nav-settings').click();await alice.locator('#settings-form [name=interval_seconds]').waitFor({state:'attached'});
    assert(await alice.locator('#settings [name=document_url]').evaluate(e=>e.readOnly));
    assert.equal(await alice.locator('#settings [name=smtp_password]').count(),0);
    await alice.locator('#settings [name=document_name]').fill('个人文档');await alice.getByRole('button',{name:'保存设置',exact:true}).click();
    await alice.locator('#toast').filter({hasText:'设置已保存'}).waitFor();assert.equal(await alice.locator('#document-select option:checked').textContent(),'个人文档');
    await alice.locator('#settings details.advanced summary').click();await alice.locator('#settings [name=access_token]').fill('updated-token');
    await alice.getByRole('button',{name:'保存高级设置',exact:true}).click();await alice.locator('#toast').filter({hasText:'设置已保存'}).waitFor();
    await otherTab.goto(base+'/settings?doc='+first);await otherTab.locator('#settings details.advanced summary').click();
    await otherTab.waitForFunction(()=>document.querySelector('#settings [name=access_token]').value==='updated-token');
    // Document-local templates are empty despite admin having its own data.
    await alice.locator('#nav-mail').click();await alice.getByText('还没有邮件模板',{exact:true}).waitFor();
    // Duplicate name creation fails and leaves the selected manager intact.
    await alice.locator('#document-select').selectOption('__new__');await alice.locator('#document-dialog').waitFor();
    await alice.locator('#document-form [name=name]').fill('文档1');await alice.locator('#document-form [name=url]').fill('https://docs.qq.com/sheet/THIRD');
    await alice.locator('#document-form button[type=submit]').click();await alice.locator('#document-error').filter({hasText:'名称重复'}).waitFor();
    await alice.locator('#cancel-document').click();
    await alice.locator('#nav-settings').click();await alice.locator('#delete-document').waitFor();
    alice.once('dialog',d=>d.accept());await alice.locator('#delete-document').click();await alice.waitForURL(url=>url.pathname==='/');
    await alice.locator('#document-select').waitFor();assert.equal(await alice.locator('#document-select').inputValue(),first);
    await alice.locator('#nav-manage').click();await alice.locator('#rules-list article').waitFor();
    // Promotion updates role access after reauthentication, ordinary user never sees admin.
    await card.getByRole('button',{name:'编辑',exact:true}).click();await admin.locator('#user-form [name=role]').selectOption('admin');
    await admin.locator('#user-form button[type=submit]').click();await admin.locator('#user-form').waitFor({state:'hidden'});
    await alice.reload();await alice.waitForURL(url=>url.pathname==='/login');await login(alice,'alice');await alice.locator('#nav-admin').waitFor();
    await alice.locator('#nav-admin').click();await alice.locator('#add-user').click();
    assert.deepEqual(await alice.locator('#user-form [name=role] option').allTextContents(),['普通用户']);
    assert.deepEqual(errors,[]);console.log('Accounts, document isolation, inherited credentials, role access and document lifecycle: PASS');
    await context.close();
  } finally {if(browser)await browser.close();server.kill('SIGTERM');}
})().catch(error=>{console.error(error);process.exitCode=1;});
