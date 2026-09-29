'use strict';
(async () => {
  const workspace = await workspaceReady;
  if (!workspace) return;
  const key = 'wr_tour:' + workspace.user.id;
  const steps = documentId ? [
    {path:'/help', target:'#document-select', title:'先选对文档', text:'这里切换文档，也可以新建。之后看到的规则、邮件和结果，都属于当前文档。'},
    {path:'/settings', target:'#settings details.panel > summary', title:'第一步：选出同事', text:'展开“选择数据源”，读入名单并勾选同事，最后点“保存统计范围”。'},
    {path:'/manage', target:'#add-rule', title:'第二步：告诉系统检查哪里', text:'用“添加规则”指定工作表、责任人位置和填写范围。保存后记得启用规则。'},
    {path:'/', target:'#check', title:'第三步：核对查询结果', text:'点“立即查询”检查一次。结果正确后，打开下方的自动查询开关，让服务器持续检查。'},
    {path:'/mail', target:'#add-template', title:'第四步：设置邮件提醒', text:'新建模板，填收件人、标题和正文。可手动发送，也可选择时间和条件自动触发。'},
    {path:'/settings', target:'#device-notifications', title:'第五步：在本设备接收通知', text:'打开“允许通知”，成功后会收到启动提示。再将邮件模板设为主推送；每个文档最多一个。'},
    {path:'/variables', target:'#refresh-variables', title:'需要实时内容？来变量表', text:'这里查变量名称、含义和当前值，再复制进邮件模板。刷新变量不会重新检查腾讯表格。'},
    {path:'/help', target:'#guide-syntax > summary', title:'示例和答案随时可查', text:'展开模板语法可复制示例；遇到问题先看 FAQ。现在可以结束引导，开始配置了。'}
  ] : [
    {path:'/', target:'#create-first-document', title:'从第一个文档开始', text:'点这里新建文档管理器，填写腾讯表格地址和凭据。此引导只介绍入口，结束后再填写。'},
    {path:'/help', target:'#guide-start > summary', title:'接下来照着五步配置', text:'创建文档后，按这里的步骤选同事、加规则、查询和设置提醒。你也可以再次开始完整引导。'}
  ];
  let state;
  try { state = JSON.parse(sessionStorage.getItem(key)); } catch { sessionStorage.removeItem(key); }
  if (state && (state.doc !== documentId || !Number.isInteger(state.index) || !steps[state.index] || steps[state.index].path !== location.pathname)) {
    sessionStorage.removeItem(key); state = null;
  }
  let dialog, target, resizeObserver;
  function finish(complete = false) {
    sessionStorage.removeItem(key); state = null;
    resizeObserver?.disconnect();
    if (dialog) { dialog.close(); dialog.remove(); dialog = null; }
    if (complete && location.pathname !== '/help') location.assign(documentLocation(documentId, '/help'));
    else ($('#start-tour')?.getClientRects().length ? $('#start-tour') : target)?.focus({preventScroll:true});
  }
  function advance(index) {
    if (index >= steps.length) return finish(true);
    state = {doc:documentId, index};
    try { sessionStorage.setItem(key, JSON.stringify(state)); }
    catch { toast('浏览器无法保存引导进度，请允许网站存储后重试。'); return; }
    const step = steps[index];
    if (location.pathname !== step.path) location.assign(documentLocation(documentId, step.path));
    else show();
  }
  function position() {
    if (!dialog || !target) return;
    const r = target.getBoundingClientRect(), spot = dialog.querySelector('.tour-spot'), card = dialog.querySelector('.tour-card');
    const width = innerWidth, height = innerHeight;
    Object.assign(spot.style, {left:Math.max(4,r.left-5)+'px',top:Math.max(4,r.top-5)+'px',width:Math.min(r.width+10,width-8)+'px',height:r.height+10+'px'});
    const box = card.getBoundingClientRect();
    const below = height-r.bottom, above = r.top;
    const y = below >= box.height+24 ? r.bottom+18 : above >= box.height+24 ? r.top-box.height-18 : (below>=above ? height-box.height-12 : 12);
    Object.assign(card.style, {left:Math.max(12,Math.min(r.left,width-box.width-12))+'px',top:Math.max(12,Math.min(y,height-box.height-12))+'px'});
  }
  function show() {
    const step = steps[state.index];
    target = $(step.target);
    if (!target || !target.getClientRects().length) { finish(); toast('当前入口暂不可用，可稍后从帮助手册重新开始。'); return; }
    resizeObserver?.disconnect();
    if (dialog) { dialog.close(); dialog.remove(); }
    dialog = el('dialog'); dialog.id = 'tour-dialog';
    dialog.setAttribute('aria-labelledby','tour-title'); dialog.setAttribute('aria-describedby','tour-text');
    const spot = el('div',undefined,'tour-spot'); spot.setAttribute('aria-hidden','true');
    const card = el('div',undefined,'tour-card'), top = el('div',undefined,'tour-top');
    const progress = el('span',`${state.index+1} / ${steps.length}`,'tour-progress');
    const close = el('button','退出引导','quiet tour-close'); close.type = 'button'; close.onclick = () => finish();
    top.append(progress,close);
    const title = el('h2',step.title); title.id = 'tour-title';
    const text = el('p',step.text); text.id = 'tour-text';
    const actions = el('div',undefined,'tour-actions');
    const back = el('button','上一步','secondary'); back.type = 'button'; back.disabled = state.index === 0; back.onclick = () => advance(state.index-1);
    const next = el('button',state.index === steps.length-1 ? '完成引导' : '下一步','primary'); next.type = 'button'; next.onclick = () => advance(state.index+1);
    actions.append(back,next); card.append(top,title,text,actions); dialog.append(spot,card); document.body.append(dialog);
    dialog.addEventListener('cancel', event => {event.preventDefault(); finish();});
    target.scrollIntoView({block:'center',behavior:'instant'});
    dialog.showModal(); next.focus({preventScroll:true}); position();
    resizeObserver = new ResizeObserver(position); resizeObserver.observe(card); resizeObserver.observe(target);
    requestAnimationFrame(position);
  }
  $('#start-tour').onclick = () => advance(0);
  addEventListener('resize',position); addEventListener('scroll',position,{passive:true});
  if (state) show();
})().catch(error => toast(error.message));
