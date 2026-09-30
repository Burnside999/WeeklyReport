'use strict';
(async () => {
  const workspace = await workspaceReady;
  if (!workspace || page !== 'help') return;
  const key = 'wr_tutorial_v2:' + workspace.user.id;
  // Read-only copies of the real forms keep the whole tutorial available before
  // the first document exists. No API writes, navigation, or changes to drafts.
  const steps = [
    ['准备','credentials','platform','先准备腾讯文档帐号','点击下方链接注册腾讯文档开放平台，个人使用选择个人类型。完成邮箱验证，并绑定能打开周报表格的 QQ 或微信帐号。','完成后应看到首页的“开发者信息”。这与本系统的登录帐号不同。','腾讯平台与凭据详解','/help#guide-credentials'],
    ['准备','credentials','fields','复制三项凭据，逐一对应','在“开发者信息”中分别复制 client_id、open_id、access_token，再填到同名输入框。Open ID 不是 QQ 号，Token 不是邮箱密码。','不要复制页面上的星号。三项必须来自同一套授权；个人帐号无需创建应用。','打开腾讯文档开放平台','https://docs.qq.com/open/developers/'],
    ['文档','document','identity','新建第一个文档管理器','在首页点“＋ 新建文档管理器”，填一个易认的名字，再粘贴腾讯在线表格的分享链接。首次创建时还要填上刚才的三项凭据。','例如：名称“研发周报”。创建前确保绑定帐号能打开这张表；文档 URL 创建后不能更换。'],
    ['名单','source','sheet','选择存放同事名单的工作表','进入“设置 → 选择数据源”。先选姓名名单所在的工作表；它不一定是实际填写周报的工作表。列表为空或刚新增表时，点“刷新工作表列表”。','下面都用一个例子：姓名在 A2–A20，周报填写内容在 C、D 两列。'],
    ['名单','source','direction','看姓名往哪个方向排列','姓名从 A2、A3 一直向下排，选“列分布”；姓名从 B1、C1 向右排，才选“行分布”。选择后，下面的输入项会跟着切换。','当前例子选列分布；行分布则填写姓名所在行，以及起止列。'],
    ['名单','source','range','圈定姓名范围，再读取名单','例子中的姓名列填 A，起始行填 2，结束行填 20，避开标题。检查后点“保存数据源并读取名单”，下方会列出姓名。','起止位置都包含在内。姓名中的备注不会自动删除，请让它与责任人写法一致。'],
    ['名单','roster','selection','勾选需要统计的人，并保存','从读入的名单里勾选参与统计的同事。可以用“全选 / 全不选”批量调整，最后必须点“保存统计范围”。','没勾选的人不会参与统计。只改勾选却不保存，下次仍会使用原来的范围。'],
    ['监听','rule','name','新建规则：说明检查什么','进入“监听管理 → 添加规则”。条目名称填“本周工作总结”等易懂文字；变量名可以留空，系统会自动分配 listener1 等名称。','以后邮件需要单独引用这条规则时会用到变量名；改名后要同步修改模板。'],
    ['监听','rule','sheet','选择真正填写周报的位置','在规则里选择周报工作表，按责任人的排列方向选择行分布或列分布。这里配置的是要检查的内容，和前面的名单数据源各司其职。','同一文档可以添加多条规则，分别检查不同工作表或不同任务。'],
    ['监听','rule','owner','分别填写责任人和检查区域','沿用例子：责任人所在列填 A，需要检查的列填 C,D。用英文逗号分隔多列；如果选行分布，则填写行号，如 3,4。','一项任务中，任意一个检查格有内容就算已填，并不是每格都要填写。'],
    ['监听','rule','range','检查范围，保存并启用','填起止行，例如 2 到 20；行分布则填起止列。保持“启用这条规则”勾选，然后点“保存规则”。','范围要包含完整合并区域。同一个人在别处还有空任务，仍会计入未填。'],
    ['查询','merge','refresh','有合并单元格？先同步一次','进入设置，点“刷新合并结构”，等待上次同步时间更新。系统据此把合并责任人对应的多行或多列识别为一项任务。','新增、拆分或移动合并区域后再刷新；只是改文字，不需要每次刷新。'],
    ['查询','home','check','先手动查询，核对人数与明细','进入“填写情况”，点“立即查询”。查询完成后，把未填人数、责任人和工作表与原表核对，确认规则设置正确。','人数按姓名去重。“—”或 null 表示还不能确定结果，不等于 0；异常时先看页面提示。'],
    ['查询','home','auto','确认无误后，开启自动查询','打开“开启自动查询”，服务器就会按设置的间隔持续检查，即使你关闭网页也会继续。间隔可在设置的高级设置中调整。','自动查询负责更新结果。邮件何时发、发给谁，需要接下来单独配置。'],
    ['邮件','template','content','新建邮件模板，先写一封提醒','进入“邮件模板 → 新建模板”，添加 1–5 个接收邮箱，再写标题和正文。标题里可用 {{global.personcount}} 自动填入未填人数。','先请管理员配置 SMTP 发件邮箱。邮件按纯文本发送，标题不能换行。','查看可复制的邮件示例','/help#guide-syntax'],
    ['邮件','variables','catalog','用变量表查名称、类型和值','变量表里的名称可复制进模板。personcount 是人数，personlist 是姓名文本，people 是供循环逐个输出的列表；不要混用文本和列表。','“刷新变量”只读取当前值，不会重新检查腾讯表格。未知值需先成功查询再使用。','变量与循环语法','/help#guide-variables'],
    ['邮件','schedule','clock','选择每周检查日，或固定时刻','模板选“自动触发”后，可以选多个星期和当天开始时刻；也可选固定日期时间。所有时间按北京时间理解。','每周检查到当天结束就停止，不跨天补发；固定时刻到点后持续等待条件满足。'],
    ['邮件','schedule','condition','加上条件，再保存模板','例如：变量选 global.personcount，关系选“大于”，值填 0，意思是还有人没填才提醒。涉及未填数据时，要有到点后的一次成功查询。','保存后由系统自动等待。手动模板则点“发送邮件”；重置自动模板可能再次发信，请先核实邮箱。'],
    ['通知','primary','primary','选一个模板作为主推送','在已保存的模板卡片上点“设为主推送”。每个文档最多一个，也可以取消；不同文档独立选择。','这个模板触发时，设备通知显示替换变量后的邮件标题。选择主推送不会更改邮件接收人。'],
    ['通知','notifications','toggle','在每台设备开启通知，完成配置','进入设置打开“允许通知”，同意系统权限。收到“消息推送启动成功！”说明这台设备已开启；其他设备需分别操作。','Windows 留在托盘，电脑网页保持打开；iPhone 从主屏幕图标进入。设备通知不等于邮件投递成功。','查看安装方法与常见问题','/help#guide-install']
  ];
  let dialog, scene, spotlight, preview, card, target, index=0, currentScene='', frame, observer, savedScroll=0, savedOverflow='';
  const points=new Map();
  const textNode=(tag,text,cls)=>el(tag,text,cls);
  function copy(selector) {
    const node=$(selector).cloneNode(true);node.hidden=false;
    for(const extra of node.querySelectorAll('.tip-trigger,.context-links,.error,[role=status]'))extra.remove();
    for(const input of node.querySelectorAll('input,textarea')) {
      if(input.type==='checkbox'||input.type==='radio')input.checked=false;
      else input.value='';
      input.removeAttribute('form');input.autocomplete='off';
    }
    for(const item of [node,...node.querySelectorAll('[id],[for]')]) {
      if(item.id){item.dataset.originalId=item.id;item.id='demo-'+item.id;}
      if(item.htmlFor)item.htmlFor='demo-'+item.htmlFor;
    }
    return node;
  }
  function point(name,node){points.set(name,node);return node;}
  function fill(root,selector,value){const control=root.querySelector(selector);if(control)control.value=value;return control;}
  function select(root,selector,options,value){const control=root.querySelector(selector);control.replaceChildren(...options.map(([text,value])=>new Option(text,value)));control.value=value;return control;}
  function field(root,name){return root.querySelector(`[name=${name}]`).closest('label');}
  function build(name) {
    points.clear();scene.replaceChildren();
    const heading=textNode('h2','', 'tour-page-title');scene.append(heading);
    const add=node=>{scene.append(node);return node;};
    if(name==='credentials') {
      heading.textContent='准备腾讯文档凭据';
      point('platform',add(textNode('div','腾讯文档开放平台 → 注册 / 登录 → 验证并绑定 → 首页 · 开发者信息','tour-demo-note')));
      const fields=copy('#document-credentials');fields.querySelectorAll('input').forEach(input=>input.value=input.name==='access_token'?'example-token':'从腾讯平台复制');
      add(fields);point('fields',field(fields,'access_token'));
    } else if(name==='document') {
      heading.textContent='新建文档管理器';const form=add(copy('#document-form'));
      form.querySelector('.section-heading').remove();form.querySelector('fieldset').remove();
      fill(form,'[name=name]','研发周报');fill(form,'[name=url]','https://docs.qq.com/sheet/示例表格链接');point('identity',field(form,'url'));
    } else if(name==='source') {
      heading.textContent='设置 / 选择数据源';const form=add(copy('#source-form'));
      select(form,'select',[['同事名单','demo-roster']],'demo-roster');form.querySelector('[value=column]').checked=true;
      fill(form,'[name=column]','A');fill(form,'[name=start_row]','2');fill(form,'[name=end_row]','20');
      point('sheet',form.querySelector('select').closest('label'));point('direction',form.querySelector('.distribution-options'));point('range',field(form,'end_row'));
    } else if(name==='roster') {
      heading.textContent='设置 / 保存统计范围';const box=add(textNode('div',undefined,'panel'));
      box.append(textNode('p','名单已读取 · 以下为示例姓名'));
      for(const name of ['张三','李四','王五']){const label=textNode('label',undefined,'checkbox'),input=el('input');input.type='checkbox';input.checked=name!=='王五';label.append(input,document.createTextNode(name));box.append(label);}
      const button=textNode('button','保存统计范围','secondary full');button.type='button';box.append(button);point('selection',button);
    } else if(name==='rule') {
      heading.textContent='监听管理 / 添加规则';const form=add(copy('#rule-form'));form.querySelector('.section-heading').remove();
      fill(form,'[name=variable_name]','listener1');fill(form,'[name=name]','本周工作总结');select(form,'select',[['周报','demo-report']],'demo-report');
      form.querySelector('[value=column]').checked=true;fill(form,'[name=owner_column]','A');fill(form,'[name=target_columns]','C,D');fill(form,'[name=start_row]','2');fill(form,'[name=end_row]','20');form.querySelector('[name=enabled]').checked=true;
      point('name',field(form,'name'));point('sheet',form.querySelector('select').closest('label'));point('owner',field(form,'target_columns'));point('range',field(form,'end_row'));
    } else if(name==='merge') {
      heading.textContent='设置 / 合并单元格结构';const box=add(copy('#merge-settings'));point('refresh',box.querySelector('button'));
    } else if(name==='home') {
      heading.textContent='填写情况';const summary=add(copy('#home .summary'));summary.querySelector('#demo-people').textContent='2';summary.querySelector('#demo-record-count').textContent='2 条待填写 · 1 条启用规则';summary.querySelector('time').textContent='2026-09-30 09:00:00';
      point('check',summary.querySelector('button'));const toggle=add(copy('#home .auto-query-toggle'));toggle.querySelector('input').disabled=false;point('auto',toggle);
    } else if(name==='template') {
      heading.textContent='邮件模板 / 新建模板';const box=add(textNode('div',undefined,'panel'));
      const recipient=textNode('label','接收人');const input=el('input');input.type='email';input.value='colleague@example.com';recipient.append(input);box.append(recipient);
      const title=textNode('label','邮件标题');const titleInput=el('input');titleInput.value='周报提醒：还有 {{global.personcount}} 人未填写';title.append(titleInput);box.append(title);
      const body=textNode('label','邮件内容');const content=el('textarea');content.rows=3;content.value='尚未填写：{{global.personlist}}\n请前往填写：{{global.url}}';body.append(content);box.append(body);point('content',title);
    } else if(name==='variables') {
      heading.textContent='变量表';const box=add(textNode('div',undefined,'panel'));
      for(const [variable,value] of [['global.personcount','2（整数）'],['global.personlist','张三,李四（文本）'],['global.people','[张三,李四]（列表）']]){const row=textNode('div',undefined,'tour-demo-variable');row.append(textNode('code',variable),textNode('p',value));box.append(row);}point('catalog',box.children[1]);
    } else if(name==='schedule') {
      heading.textContent='邮件模板 / 自动触发配置';const box=add(copy('#automatic-fields'));box.disabled=false;
      box.querySelectorAll('[name=schedule_weekday]').forEach(input=>input.checked=input.value==='4');box.querySelector('#demo-schedule-clock').value='18:00:00';
      select(box,'#demo-condition-variable',[['global.personcount · 未填人数','global.personcount']],'global.personcount');box.querySelector('#demo-condition-value').value='0';
      point('clock',box.querySelector('#demo-schedule-clock').closest('label'));point('condition',box.querySelector('#demo-condition-variable').closest('label'));
    } else if(name==='primary') {
      heading.textContent='邮件模板 / 已保存的模板';const box=add(textNode('article',undefined,'rule-card'));
      box.append(textNode('h3','周五周报提醒'),textNode('p','每周五 18:00:00 后 · 未填人数大于 0','rule-meta'));
      const actions=textNode('div',undefined,'rule-actions'),button=textNode('button','设为主推送','quiet');button.type='button';actions.append(button);box.append(actions);point('primary',button);
    } else if(name==='notifications') {
      heading.textContent='设置 / 允许通知';const box=add(copy('#device-notifications'));box.querySelector('input').disabled=false;point('toggle',box);
      add(textNode('p','开启成功：消息推送启动成功！','tour-demo-note'));
    }
    // Marked copies must never submit, steal focus, or access live form handlers.
    for(const element of scene.querySelectorAll('[form]'))element.removeAttribute('form');
    scene.inert=true;scene.setAttribute('aria-hidden','true');
    currentScene=name;
  }
  function position() {
    frame=0;if(!dialog || !target)return;
    const r=target.getBoundingClientRect(),p=preview.getBoundingClientRect();
    const top=Math.max(p.top,r.top-5),left=Math.max(p.left,r.left-5),bottom=Math.min(p.bottom,r.bottom+5),right=Math.min(p.right,r.right+5);
    Object.assign(spotlight.style,{top:top-p.top+'px',left:left-p.left+'px',width:Math.max(0,right-left)+'px',height:Math.max(0,bottom-top)+'px'});
  }
  function schedulePosition(){if(!frame)frame=requestAnimationFrame(position);}
  function remember(){try{sessionStorage.setItem(key,String(index));}catch{/* The tour still works without browser storage. */}}
  function show(nextIndex) {
    index=Math.max(0,Math.min(nextIndex,steps.length-1));remember();
    const [chapter,name,pointName,title,body,note,linkLabel,href]=steps[index];
    if(currentScene!==name)build(name);
    target?.removeAttribute('data-tour-target');target=points.get(pointName);target.dataset.tourTarget='true';dialog.dataset.step=String(index+1);
    dialog.querySelector('.tour-progress').textContent=`${index+1} / ${steps.length} · ${chapter}`;
    dialog.querySelector('#tour-title').textContent=title;dialog.querySelector('#tour-text').textContent=body;dialog.querySelector('.tour-note').textContent=note;
    const link=dialog.querySelector('.tour-link');link.hidden=!href;if(href){link.textContent=linkLabel+' ↗';link.href=href;}
    dialog.querySelector('.tour-back').disabled=index===0;dialog.querySelector('.tour-next').textContent=index===steps.length-1?'完成引导':'下一步';
    dialog.querySelector('.tour-jump').value=String(index);
    // Measure after the new scene's layout. Scroll inside the preview only, then
    // paint the spotlight before the next frame; no page navigation or smooth-scroll race.
    const scroller=scene.parentElement;
    const r=target.getBoundingClientRect(),v=scroller.getBoundingClientRect();
    scroller.scrollTop+=r.top-v.top-(v.height-r.height)/2;
    card.scrollTop=0;position();schedulePosition();
    dialog.querySelector('.tour-next').focus({preventScroll:true});
  }
  function finish() {
    if(!dialog)return;cancelAnimationFrame(frame);frame=0;observer?.disconnect();
    dialog.close();dialog.remove();dialog=null;currentScene='';points.clear();
    document.documentElement.style.overflow=savedOverflow;window.scrollTo({top:savedScroll,behavior:'instant'});
    try{sessionStorage.removeItem(key);}catch{}
    $('#start-tour').focus({preventScroll:true});
  }
  function start() {
    savedScroll=scrollY;savedOverflow=document.documentElement.style.overflow;
    document.documentElement.style.overflow='hidden';
    dialog=el('dialog');dialog.id='tour-dialog';dialog.setAttribute('aria-labelledby','tour-title');dialog.setAttribute('aria-describedby','tour-text');
    const top=el('div',undefined,'tour-top'),progress=el('span','','tour-progress'),close=el('button','退出引导','quiet tour-close');close.type='button';close.onclick=finish;top.append(progress,close);
    const layout=el('div',undefined,'tour-layout');
    preview=el('div',undefined,'tour-preview');const scroller=el('div',undefined,'tour-scroller');scene=el('div',undefined,'tour-scene');scroller.append(scene);preview.append(scroller);spotlight=el('div',undefined,'tour-spot');spotlight.setAttribute('aria-hidden','true');preview.append(spotlight);scroller.addEventListener('scroll',schedulePosition,{passive:true});
    card=el('div',undefined,'tour-card');const title=el('h2');title.id='tour-title';title.setAttribute('aria-live','polite');const body=el('p');body.id='tour-text';const note=el('p','','tour-note'),link=el('a','','tour-link');link.target='_blank';link.rel='noopener noreferrer';
    const label=el('label','跳转步骤'),jump=el('select',undefined,'tour-jump');jump.setAttribute('aria-label','跳转教程步骤');steps.forEach((step,i)=>jump.append(new Option(`${i+1}. ${step[3]}`,String(i))));jump.onchange=()=>show(Number(jump.value));label.append(jump);
    const actions=el('div',undefined,'tour-actions'),back=el('button','上一步','secondary tour-back'),next=el('button','下一步','primary tour-next');back.type=next.type='button';back.onclick=()=>show(index-1);next.onclick=()=>index===steps.length-1?finish():show(index+1);actions.append(back,next);card.append(title,body,note,link,label,actions);layout.append(preview,card);
    const caption=el('p','界面演示 · 示例数据 · 不会保存设置或发送邮件','tour-demo-caption');dialog.append(top,caption,layout);document.body.append(dialog);
    dialog.addEventListener('cancel',event=>{event.preventDefault();finish();});
    dialog.addEventListener('keydown',event=>{
      if(event.key!=='Tab')return;
      const focusable=[...dialog.querySelectorAll('.tour-top button,.tour-card a[href]:not([hidden]),.tour-card select,.tour-card button:not(:disabled)')];
      const first=focusable[0],last=focusable.at(-1);
      if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
      else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
    });dialog.showModal();
    observer=new ResizeObserver(()=>{
      if(!target)return;const r=target.getBoundingClientRect(),v=scroller.getBoundingClientRect();scroller.scrollTop+=r.top-v.top-(v.height-r.height)/2;schedulePosition();
    });observer.observe(preview);
    let remembered=0;try{remembered=Number(sessionStorage.getItem(key))||0;}catch{}
    show(remembered);
  }
  $('#start-tour').onclick=start;
  addEventListener('resize',schedulePosition);window.visualViewport?.addEventListener('resize',schedulePosition);window.visualViewport?.addEventListener('scroll',schedulePosition);
})().catch(error=>toast(error.message));
