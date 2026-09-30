'use strict';
(async () => {
  const workspace = await workspaceReady;
  if (!workspace || page !== 'help') return;
  const key = 'wr_tutorial_v2:' + workspace.user.id;
  // Read-only copies of the real forms keep the whole tutorial available before
  // the first document exists. No API writes, navigation, or changes to drafts.
  const steps = window.WeeklyReportTourSteps;
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
