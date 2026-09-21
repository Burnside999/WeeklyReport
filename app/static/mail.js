'use strict';
(() => {
  if (page !== 'mail') return;
  const form = $('#template-form'), subject = $('#mail-subject'), body = $('#mail-body');
  let catalog = {rows: [], values: {}}, items = [], editing = null, loading = false, actionBusy = false;
  const sentThisPage = new Set();
  const token = /{{\s*([A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z][A-Za-z0-9]*)+)\s*}}/g;
  const known = name => Object.prototype.hasOwnProperty.call(catalog.values, name);
  const comparable = new Set(['integer','boolean']);
  function highlight(input, mirror) {
    const text = input.value, fragment = document.createDocumentFragment();
    let start = 0;
    for (const match of text.matchAll(token)) {
      fragment.append(document.createTextNode(text.slice(start, match.index)));
      fragment.append(el('span', match[0], known(match[1]) ? 'valid-variable' : 'invalid-variable'));
      start = match.index + match[0].length;
    }
    fragment.append(document.createTextNode(text.slice(start) + '\n'));
    mirror.replaceChildren(fragment);
    mirror.scrollTop = input.scrollTop; mirror.scrollLeft = input.scrollLeft;
  }
  function updateEditors() {
    highlight(subject, $('#subject-highlight')); highlight(body, $('#body-highlight'));
    const text = subject.value + '\n' + body.value;
    const names = [...text.matchAll(token)].map(m => m[1]);
    const invalid = [...new Set(names.filter(n => !known(n)))];
    const incomplete = /{{|}}/.test(text.replace(token, ''));
    $('#mail-editor-state').textContent = invalid.length ? '未知变量：' + invalid.join('、') : incomplete ? '变量格式未完成，请使用 {{变量名}}。' : '';
  }
  for (const [input, mirror] of [[subject,$('#subject-highlight')],[body,$('#body-highlight')]]) {
    input.addEventListener('input', updateEditors);
    input.addEventListener('scroll', () => {mirror.scrollTop=input.scrollTop;mirror.scrollLeft=input.scrollLeft;});
  }
  function recipient(value = '') {
    const row = el('div', undefined, 'recipient-row'), input = el('input'), remove = el('button', '移除', 'quiet danger');
    input.type='email'; input.required=true; input.value=value; input.placeholder='name@example.com';input.setAttribute('aria-label','接收邮箱');
    remove.type='button'; remove.onclick=()=>{row.remove();recipientButtons();};
    row.append(input, remove); $('#mail-recipients').append(row);recipientButtons();
  }
  function recipientButtons() {
    const rows=$('#mail-recipients').children;
    $('#add-recipient').disabled=rows.length>=5;
    for(const row of rows) row.querySelector('button').disabled=rows.length<=1;
  }
  $('#add-recipient').onclick=()=>recipient();
  function populateVariables() {
    const selected=$('#condition-variable').value;
    $('#condition-variable').replaceChildren(new Option('请选择变量',''));
    $('#schedule-variables').replaceChildren();
    for(const row of catalog.rows) {
      if(comparable.has(row.type)) $('#condition-variable').append(new Option(row.name+' · '+row.description,row.name));
      if(['date','datetime'].includes(row.type)) $('#schedule-variables').append(new Option(row.description,row.name));
    }
    $('#condition-variable').value=selected;
  }
  function conditionInput() {
    const row=catalog.rows.find(r=>r.name===$('#condition-variable').value), input=$('#condition-value');
    const types={integer:'number'};
    input.type=types[row?.type] || 'text';input.step=row?.type==='integer'?'1':'any';
    input.placeholder=row?.type==='boolean'?'true 或 false':'';
  }
  function scheduleFields() {
    const variable=$('#schedule-kind').value==='variable';
    $('#schedule-variable-fields').hidden=!variable;$('#schedule-fixed-fields').hidden=variable;
    $('#schedule-variable').disabled=!variable;$('#schedule-fixed').disabled=variable;
    const name=$('#schedule-variable').value.trim().replace(/^{{\s*|\s*}}$/g,'');
    const row=catalog.rows.find(r=>r.name===name);
    $('#schedule-clock').disabled=!variable || row?.type==='datetime';
    const value=variable?catalog.values[name]:$('#schedule-fixed').value;
    $('#schedule-preview').textContent=value ? '本轮开始时间：'+value+(variable && row?.type==='date'?' '+$('#schedule-clock').value:'') : '请选择时间';
  }
  function modeFields() {
    const auto=form.elements.mail_mode.value==='auto';$('#automatic-fields').hidden=!auto;$('#automatic-fields').disabled=!auto;scheduleFields();
  }
  form.querySelectorAll('[name=mail_mode]').forEach(input=>input.onchange=modeFields);
  for(const id of ['schedule-kind','schedule-variable','schedule-clock','schedule-fixed']) $('#'+id).addEventListener('input',scheduleFields);
  $('#condition-variable').onchange=conditionInput;
  async function edit(item=null) {
    try {catalog=await api('variables');populateVariables();} catch(error){toast(error.message);return;}
    editing=item;form.reset();form.hidden=false;
    $('#template-form-title').textContent=item?'编辑邮件模板':'新建邮件模板';
    $('#mail-recipients').replaceChildren();for(const address of item?.recipients || ['']) recipient(address);
    subject.value=item?.subject || '';body.value=item?.body || '';form.elements.mail_mode.value=item?.mode || 'manual';
    $('#schedule-kind').value=item?.schedule?.kind || 'variable';
    $('#schedule-variable').value=item?.schedule?.kind==='variable'?item.schedule.value:'global.week.friday';
    $('#schedule-clock').value=item?.schedule?.clock || '00:00';
    $('#schedule-fixed').value=item?.schedule?.kind==='fixed'?item.schedule.value.slice(0,19):'';
    const variable=item?.condition?.variable || 'global.personcount';
    if(!catalog.rows.some(r=>r.name===variable && comparable.has(r.type))) {
      const option=new Option(variable+'（请重新选择）',variable);option.disabled=true;$('#condition-variable').append(option);
    }
    $('#condition-variable').value=variable;conditionInput();
    $('#condition-value').value=item?.condition?.value || '0';
    $('#condition-operator').value=item?.condition?.operator || 'eq';
    updateEditors();modeFields();form.scrollIntoView({behavior:'smooth',block:'start'});
  }
  $('#add-template').onclick=()=>edit();$('#cancel-template').onclick=()=>form.hidden=true;
  form.onsubmit=async event=>{
    event.preventDefault();const button=$('#save-template');button.disabled=true;
    const mode=form.elements.mail_mode.value, kind=$('#schedule-kind').value;
    const data={};
    data.recipients=[...$('#mail-recipients').querySelectorAll('input')].map(i=>i.value.trim());
    Object.assign(data,{subject:subject.value,body:body.value,mode,revision:editing?.revision});
    if(mode==='auto') {
      data.schedule={kind,value:$(kind==='fixed'?'#schedule-fixed':'#schedule-variable').value,clock:$('#schedule-clock').value};
      data.condition={variable:$('#condition-variable').value,operator:$('#condition-operator').value,value:$('#condition-value').value};
    }
    try {const saved=await api('templates'+(editing?'/'+editing.id:''),editing?'PUT':'POST',data);if(editing && saved.revision!==editing.revision)sentThisPage.delete(editing.id);form.hidden=true;toast('邮件模板已保存');await load();}catch(error){toast(error.message);}finally{button.disabled=false;}
  };
  const mailTime = value => value ? new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}) : '尚未触发';
  function renderList() {
    const list=$('#template-list');list.replaceChildren();
    if(!items.length) {const empty=el('div',undefined,'empty panel');empty.append(el('h3','还没有邮件模板'),el('p','新建模板，选择接收人并编写邮件。'));list.append(empty);}
    for(const item of items) {
      const card=el('article',undefined,'rule-card'), heading=el('div',undefined,'rule-top');
      heading.append(el('h3',item.subject),el('span',item.mode==='auto'?'自动触发':'手动触发','badge'));card.append(heading);
      card.append(el('p',item.recipients.join('、'),'rule-meta'),el('p','上次触发：'+mailTime(item.last_trigger),'help'));
      if(item.last_success)card.append(el('p','上次发送成功：'+mailTime(item.last_success),'help'));
      if(item.mode==='auto')card.append(el('p',`${item.schedule.value}${item.schedule.kind==='variable'?' '+item.schedule.clock:''} 后 · ${item.condition.variable} ${{gt:'>',lt:'<',eq:'='}[item.condition.operator]} ${item.condition.value}`,'rule-meta'));
      if(item.error)card.append(el('p',item.error,'error'));
      else if(item.note && item.mode==='auto')card.append(el('p',item.note,'help'));
      const actions=el('div',undefined,'rule-actions'), tools=el('div'), editButton=el('button','编辑','quiet'), remove=el('button','删除','quiet danger');
      editButton.onclick=()=>edit(item);remove.onclick=async()=>{if(!confirm('删除此邮件模板？'))return;try{await api('templates/'+item.id,'DELETE',{revision:item.revision});if(editing?.id===item.id)form.hidden=true;await load();}catch(error){toast(error.message);}};
      tools.append(editButton,remove);
      const completed=item.status==='sent' || item.status==='error';
      const label=item.status==='sending'?'发送中…':item.mode==='auto'?(completed?'重置自动触发':'等待自动触发'):(sentThisPage.has(item.id)?'发送成功':'发送邮件');
      const send=el('button',label,'primary');
      send.disabled=actionBusy || item.status==='sending' || (item.mode==='auto'?!completed:sentThisPage.has(item.id));
      editButton.disabled=remove.disabled=actionBusy || item.status==='sending';
      send.onclick=()=>trigger(item);actions.append(tools,send);card.append(actions);list.append(card);
    }
  }
  async function trigger(item) {
    if(actionBusy)return;
    actionBusy=true;renderList();
    try {
      if(item.mode==='auto') {
        if(!confirm('重置后，若时间和条件已满足，将再次发送。确定重置吗？'))return;
        await api('templates/'+item.id+'/reset','POST',{revision:item.revision});toast('已重置，等待自动触发');
      } else {
        try {await api('templates/'+item.id+'/send','POST',{revision:item.revision});}
        catch(error) {
          if(!error.data?.confirmation_required)throw error;
          if(!confirm(`你已在 ${mailTime(error.data.last_trigger)} 触发过此规则，确定要再次触发吗？`))return;
          await api('templates/'+item.id+'/send','POST',{revision:item.revision,confirm_attempt:error.data.confirm_attempt});
        }
        sentThisPage.add(item.id);toast('发送成功，SMTP 服务器已接受邮件');
      }
    }catch(error){toast(error.message);}finally{actionBusy=false;await load();renderList();}
  }
  async function load() {
    if(loading)return;loading=true;
    try {items=await api('templates');$('#mail-load-state').textContent='';renderList();}
    catch(error){$('#mail-load-state').textContent='模板读取失败：'+error.message;toast(error.message);}
    finally{loading=false;}
  }
  load();setInterval(()=>{if(!document.hidden && !actionBusy)load();},5000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)load();});
})();

