'use strict';
(async () => {
  const workspace=await workspaceReady;
  if(!workspace?.document)return;
  if (page !== 'mail') return;
  const form = $('#template-form'), subject = $('#mail-subject'), body = $('#mail-body');
  let catalog = {rows: [], values: {}}, items = [], editing = null, loading = false, actionBusy = false;
  const sentThisPage = new Set();
  let primary = null;
  const token = /{{\s*([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*)\s*}}/g;
  let validationTimer, validationVersion = 0, validTokens = {}, validatedText = {};
  const comparable = new Set(['integer','boolean']);
  function highlight(input, mirror) {
    const text=input.value, fragment=document.createDocumentFragment();
    let start=0;
    for(const match of text.matchAll(/{{[\s\S]*?}}|{%[\s\S]*?%}/g)) {
      fragment.append(document.createTextNode(text.slice(start,match.index)));
      if(match[0].startsWith('{{')) {
        const checked=validatedText[input.id]===text;
        const cls=checked?(validTokens[input.id]?.has(match.index)?'valid-variable':'invalid-variable'):'pending-variable';
        fragment.append(el('span',match[0],cls));
      } else {
        const wrapper=el('span',undefined,'control-statement');let offset=0;
        const lex=/{%|%}|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\b(?:for|in|with|endfor|and|or)\b|\b(?:true|false)\b|-?\d+(?:\.\d+)?|[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*/g;
        for(const part of match[0].matchAll(lex)) {
          wrapper.append(document.createTextNode(match[0].slice(offset,part.index)));
          const value=part[0], cls=/^(for|in|with|endfor|and|or)$/.test(value)?'syntax-keyword':/^(true|false|["'\d-])/.test(value)?'syntax-literal':value==='{%' || value==='%}'?'syntax-delimiter':'syntax-path';
          wrapper.append(el('span',value,cls));offset=part.index+value.length;
        }
        wrapper.append(document.createTextNode(match[0].slice(offset)));fragment.append(wrapper);
      }
      start=match.index+match[0].length;
    }
    fragment.append(document.createTextNode(text.slice(start)+'\n'));mirror.replaceChildren(fragment);
    mirror.scrollTop=input.scrollTop;mirror.scrollLeft=input.scrollLeft;
  }
  function errorText(errors) {
    return errors.map(e=>`${{subject:'标题',body:'正文',condition:'触发规则'}[e.field] || ''} 第 ${e.line} 行，第 ${e.column} 列：${e.message}`).join('\n');
  }
  function paintEditors() {
    highlight(subject, $('#subject-highlight')); highlight(body, $('#body-highlight'));
  }
  function updateEditors() {
    paintEditors();
    clearTimeout(validationTimer);
    const version = ++validationVersion;
    validationTimer = setTimeout(()=>validateEditors(version),250);
  }
  async function validateEditors(version) {
    const subjectText=subject.value, bodyText=body.value;
    try {
      const result=await api('templates/validate','POST',{subject:subjectText,body:bodyText});
      if(version!==validationVersion || subjectText!==subject.value || bodyText!==body.value || form.hidden)return;
      validatedText={'mail-subject':subjectText,'mail-body':bodyText};
      validTokens={'mail-subject':new Set((result.tokens.subject || []).map(t=>t.start)),
        'mail-body':new Set((result.tokens.body || []).map(t=>t.start))};
      const invalid=result.syntax_errors.length>0, state=$('#mail-editor-state');
      form.classList.toggle('syntax-invalid',invalid);
      state.textContent=invalid?'❗存在语法错误':'';
      state.title=errorText(result.syntax_errors);state.classList.toggle('error',invalid);
      paintEditors();
    } catch(error) {
      if(version===validationVersion && !form.hidden)$('#mail-editor-state').textContent='校验失败：'+error.message;
    }
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
    for(const row of catalog.rows) {
      if(comparable.has(row.type)) $('#condition-variable').append(new Option(row.name+' · '+row.description,row.name));
    }
    $('#condition-variable').value=selected;
  }
  function conditionInput() {
    const row=catalog.rows.find(r=>r.name===$('#condition-variable').value), input=$('#condition-value');
    const types={integer:'number'};
    input.type=types[row?.type] || 'text';input.step=row?.type==='integer'?'1':'any';
    input.placeholder=row?.type==='boolean'?'true 或 false':'';
  }
  const weekdayNames=['周一','周二','周三','周四','周五','周六','周日'];
  const selectedDays=()=>[...form.querySelectorAll('[name=schedule_weekday]:checked')].map(input=>Number(input.value));
  function scheduleFields() {
    const weekly=$('#schedule-kind').value==='weekly', days=selectedDays();
    $('#schedule-weekly-fields').hidden=!weekly;$('#schedule-weekly-fields').disabled=!weekly;
    $('#schedule-fixed-fields').hidden=weekly;$('#schedule-fixed').disabled=weekly;
    form.querySelector('[name=schedule_weekday]').setCustomValidity(weekly && !days.length?'请至少选择一个检查日':'');
    $('#schedule-preview').textContent=weekly ? (days.length?'每逢'+days.map(d=>weekdayNames[d]).join('、')+' '+$('#schedule-clock').value+' 开始检查，当天有效':'请至少选择一个检查日') : ($('#schedule-fixed').value?'固定时刻：'+$('#schedule-fixed').value+'（北京时间）':'请选择固定日期和时间');
  }
  function modeFields() {
    const auto=form.elements.mail_mode.value==='auto';$('#automatic-fields').hidden=!auto;$('#automatic-fields').disabled=!auto;scheduleFields();
  }
  form.querySelectorAll('[name=mail_mode]').forEach(input=>input.onchange=modeFields);
  for(const id of ['schedule-kind','schedule-clock','schedule-fixed']) $('#'+id).addEventListener('input',scheduleFields);
  form.querySelectorAll('[name=schedule_weekday]').forEach(input=>input.onchange=scheduleFields);
  $('#condition-variable').onchange=conditionInput;
  async function edit(item=null) {
    try {catalog=await api('variables');populateVariables();} catch(error){toast(error.message);return;}
    editing=item;form.reset();form.hidden=false;form.classList.remove('syntax-invalid');$('#mail-editor-state').textContent='';
    $('#template-form-title').textContent=item?'编辑邮件模板':'新建邮件模板';
    $('#mail-recipients').replaceChildren();for(const address of item?.recipients || ['']) recipient(address);
    subject.value=item?.subject || '';body.value=item?.body || '';form.elements.mail_mode.value=item?.mode || 'manual';
    $('#schedule-kind').value=item?.schedule?.kind==='fixed'?'fixed':'weekly';
    form.querySelectorAll('[name=schedule_weekday]').forEach(input=>input.checked=(item?.schedule?.weekdays || [4]).includes(Number(input.value)));
    $('#schedule-clock').value=item?.schedule?.clock || '09:00';
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
      data.schedule=kind==='fixed'?{kind,value:$('#schedule-fixed').value}:{kind,weekdays:selectedDays(),clock:$('#schedule-clock').value};
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
      const syntaxErrors=item.syntax_errors || [];
      card.classList.toggle('syntax-invalid',syntaxErrors.length>0);
      if(syntaxErrors.length) {
        const warning=el('p','❗存在语法错误','error syntax-warning');
        warning.title=errorText(syntaxErrors);warning.setAttribute('role','status');card.append(warning);
      }
      const isPrimary=primary?.document_id===documentId && primary?.trigger_id===item.id;
      if(isPrimary)card.append(el('span','主推送','badge primary-push-badge'));
      heading.append(el('h3',item.subject),el('span',item.mode==='auto'?'自动触发':'手动触发','badge'));card.append(heading);
      card.append(el('p',item.recipients.join('、'),'rule-meta'),el('p','上次触发：'+mailTime(item.last_trigger),'help'));
      if(item.last_success)card.append(el('p','上次发送成功：'+mailTime(item.last_success),'help'));
      if(item.mode==='auto')card.append(el('p',`${item.schedule.kind==='weekly'?'每逢 '+item.schedule.weekdays.map(d=>weekdayNames[d]).join('、')+' '+item.schedule.clock+'（仅当天）':item.schedule.kind==='fixed'?item.schedule.value+' 后':'旧时间配置，请重新编辑'} · ${item.condition.variable} ${{gt:'>',lt:'<',eq:'='}[item.condition.operator]} ${item.condition.value}`,'rule-meta'));
      if(item.error)card.append(el('p',item.error,'error'));
      else if(item.note && item.mode==='auto')card.append(el('p',item.note,'help'));
      const actions=el('div',undefined,'rule-actions'), tools=el('div'), editButton=el('button','编辑','quiet'), remove=el('button','删除','quiet danger');
      editButton.onclick=()=>edit(item);remove.onclick=async()=>{if(!confirm('删除此邮件模板？'))return;try{await api('templates/'+item.id,'DELETE',{revision:item.revision});if(editing?.id===item.id)form.hidden=true;await load();}catch(error){toast(error.message);}};
      const choose=el('button',isPrimary?'取消主推送':'设为主推送','quiet');
      choose.disabled=actionBusy || item.status==='sending';
      choose.onclick=async()=>{
        if(actionBusy)return;
        if(!isPrimary && primary && !confirm(`将主推送从“${primary.document_name} · ${primary.title}”切换到这条模板？`))return;
        actionBusy=true;renderList();
        try {const result=await api('me/primary-trigger','PUT',isPrimary?{document_id:null,trigger_id:null}:{document_id:documentId,trigger_id:item.id});primary=result.primary;toast(isPrimary?'已取消主推送':'已设为主推送');}
        catch(error){toast(error.message);}finally{actionBusy=false;await load();renderList();}
      };
      tools.append(editButton,remove,choose);
      const completed=item.status==='sent' || item.status==='error';
      const label=item.status==='sending'?'发送中…':item.mode==='auto'?(completed?'重置自动触发':'等待自动触发'):(sentThisPage.has(item.id)?'发送成功':'发送邮件');
      const send=el('button',label,'primary');
      if(syntaxErrors.length)send.title=errorText(syntaxErrors);
      send.disabled=syntaxErrors.length>0 || actionBusy || item.status==='sending' || (item.mode==='auto'?!completed:sentThisPage.has(item.id));
      editButton.disabled=remove.disabled=actionBusy || item.status==='sending';
      send.onclick=()=>trigger(item);actions.append(tools,send);card.append(actions);list.append(card);
    }
  }
  async function trigger(item) {
    if(actionBusy || item.syntax_errors?.length)return;
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
    try {const result=await Promise.all([api('templates'),api('me/primary-trigger')]);items=result[0];primary=result[1].primary;$('#mail-load-state').textContent='';renderList();if(!form.hidden)updateEditors();}
    catch(error){$('#mail-load-state').textContent='模板读取失败：'+error.message;toast(error.message);}
    finally{loading=false;}
  }
  load();setInterval(()=>{if(!document.hidden && !actionBusy)load();},5000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)load();});
})().catch(error=>toast(error.message));

