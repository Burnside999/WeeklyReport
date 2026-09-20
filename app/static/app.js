'use strict';
const $ = s => document.querySelector(s);
const page = location.pathname === '/mail' ? 'mail' : location.pathname === '/variables' ? 'variables' : location.pathname === '/settings' ? 'settings' : location.pathname === '/manage' ? 'manage' : 'home';
for (const name of ['home', 'manage', 'mail', 'settings', 'variables']) {
  $('#' + name).hidden = page !== name;
  $('#nav-' + name).classList.toggle('active', page === name);
  if (page === name) $('#nav-' + name).setAttribute('aria-current', 'page');
}
let allRules = [], toastTimer, checking = false, togglingAuto = false, refreshVersion = 0;
async function api(path, method = 'GET', body) {
  const response = await fetch('/api/' + path, {method, headers:{'Content-Type':'application/json','X-Requested-With':'WeeklyReport'}, ...(body === undefined ? {} : {body:JSON.stringify(body)})});
  if (response.status === 401) { location.replace('/login'); throw new Error('请重新登录'); }
  const text = await response.text();
  let data; try { data = JSON.parse(text); } catch { data = {error:text}; }
  if (!response.ok) {const error = new Error(data.error || '请求失败，请重试');error.data=data;error.status=response.status;throw error;}
  return data;
}
function toast(message) { clearTimeout(toastTimer); $('#toast').textContent = message; $('#toast').hidden = false; toastTimer = setTimeout(() => $('#toast').hidden = true, 4500); }
function el(tag, text, cls) { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; }
function date(value) { return value ? new Date(value).toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}) : '尚未查询'; }
function col(n) { let s=''; while(n) { n--; s=String.fromCharCode(65+n%26)+s; n=Math.floor(n/26); } return s; }
async function refresh() {
  if(page !== 'home') return;
  const version = ++refreshVersion;
  try {
    const s = await api('status');
    if(version !== refreshVersion) return;
    if(!togglingAuto) {$('#auto-query').checked=s.auto_query_enabled;$('#auto-query').disabled=false;}
    checking = s.running; $('#check').disabled = checking; $('#check').textContent = checking ? '查询中…' : '立即查询 ↻';
    $('#people').textContent = s.people_count ?? '—';
    const records = s.records || [];
    $('#record-count').textContent = s.last_success ? `${records.length} 条待填写 · ${s.rule_count || 0} 条启用规则${s.stale ? ' · 上次成功结果' : ''}` : '等待首次成功查询';
    $('#last-time').textContent = date(s.last_attempt);
    $('#schedule').textContent = !s.auto_query_enabled ? '自动查询已关闭' : `每 ${Math.round(s.interval_seconds / 60)} 分钟自动检查${s.next_check ? ' · 下次 ' + date(s.next_check * 1000) : ''}`;
    $('#health').textContent = checking ? '正在查询' : s.stale ? '检查异常' : s.last_success ? (s.rule_count ? '已更新' : '未设置规则') : '等待查询';
    $('#health').className = 'badge ' + (s.stale ? 'bad' : s.last_success && s.rule_count ? 'good' : '');
    $('#errors').replaceChildren(); $('#errors').hidden = !s.stale;
    if(s.stale) {
      $('#errors').append(el('p', s.last_success ? `本次未能完成检查，下方为 ${date(s.last_success)} 的旧结果。` : '尚未获取完整数据，当前人数未知。'));
      for(const error of s.errors || []) $('#errors').append(el('p', `${error.rule}：${error.message}`));
    }
    $('#records').replaceChildren();
    for(const r of records) {
      const tr = el('tr'), owner = el('td', r.person), item = el('td',r.item), sheet = el('td');
      item.append(el('small',`${r.column} 列 · 第 ${r.row}${r.end_row && r.end_row!==r.row?'–'+r.end_row:''} 行 · 未填写`));
      const link = el('a',r.sheet); link.href = s.document_url.split('?')[0] + '?tab=' + encodeURIComponent(r.sheet_id); link.target='_blank'; link.rel='noopener noreferrer'; sheet.append(link);
      tr.append(owner,item,sheet); $('#records').append(tr);
    }
    $('#detail-count').textContent = records.length ? `${records.length} 条` : '';
    $('#empty').hidden = records.length > 0;
    if(!records.length) {
      $('#empty').replaceChildren(el('span',s.stale?'!':s.rule_count?'✓':'—','empty-icon'),
        el('h3',s.stale?'暂时无法确认填写情况':s.last_success && s.rule_count?'当前项目均已填写':'还没有监听规则'),
        el('p',s.stale?'检查连接设置后，再试一次。':s.rule_count?'已检查的责任人行没有缺失项。':'到管理页添加一条监听规则，即可开始。'));
      if(!s.rule_count || s.stale) { const a=el('a','前往监听管理 →');a.href='/manage';$('#empty').append(a); }
    }
  } catch(err) { $('#health').textContent='连接失败'; $('#health').className='badge bad'; $('#errors').hidden=false; $('#errors').textContent='无法连接服务器，当前显示可能为旧结果。'+err.message; }
}
$('#check').onclick = async () => { $('#check').disabled=true; try { await api('check','POST',{}); toast('已开始查询'); await refresh(); } catch(err) {toast(err.message);} finally {$('#check').disabled=checking;} };
$('#auto-query').onchange = async event => {
  const toggle=event.target, enabled=toggle.checked;
  togglingAuto=true;toggle.disabled=true;++refreshVersion;
  try {await api('auto-query','PUT',{enabled});}
  catch(err) {toggle.checked=!enabled;toast(err.message);}
  finally {togglingAuto=false;toggle.disabled=false;await refresh();}
};
$('#logout').onclick = async () => {try {await api('logout','POST',{});location.replace('/login');}catch(err){toast(err.message);}};
const form=$('#rule-form');
function edit(rule) {
  form.reset(); form.hidden=false; $('#form-title').textContent=rule?'编辑监听规则':'添加监听规则';
  for(const key of ['id','name','variable_name','sheet_id','sheet_name','owner_column','start_row','end_row']) if(rule) form.elements[key].value = key.endsWith('column') ? col(rule[key]) : rule[key];
  form.elements.target_columns.value=rule?(rule.target_columns || [rule.target_column]).map(col).join(','):'B';
  if(!rule) form.elements.id.value='';
  form.elements.enabled.checked=rule ? rule.enabled : true;
  $('#sheet-select').value=''; form.scrollIntoView({behavior:'smooth',block:'start'});
}
$('#add-rule').onclick=()=>edit(); $('#cancel-rule').onclick=()=>form.hidden=true;
async function renderRules() {
  allRules=await api('rules'); $('#rules-list').replaceChildren();
  if(!allRules.length) {const box=el('div',undefined,'empty panel');box.append(el('h3','从第一条规则开始'),el('p','选择工作表、责任人列和需要填写的列。'));$('#rules-list').append(box);}
  for(const r of allRules) {
    const card=el('article',undefined,'rule-card'), top=el('div',undefined,'rule-top');
    top.append(el('h3',r.name),el('span',r.enabled?'监听中':'已暂停','badge '+(r.enabled?'good':'')));
    card.append(top,el('code',r.variable_name,'variable-code'),el('p',`${r.sheet_name} · 责任人 ${col(r.owner_column)} 列 → 检查 ${(r.target_columns || [r.target_column]).map(col).join('、')} 列 · 第 ${r.start_row}–${r.end_row} 行`,'rule-meta'));
    const actions=el('div',undefined,'rule-actions'), label=el('label',undefined,'checkbox'), toggle=el('input');toggle.type='checkbox';toggle.checked=r.enabled;label.append(toggle,document.createTextNode('启用'));
    toggle.onchange=async()=>{toggle.disabled=true;try{await api('rules/'+r.id,'PUT',{...r,enabled:toggle.checked});await renderRules();}catch(err){toggle.checked=r.enabled;toggle.disabled=false;toast(err.message);}};
    const buttons=el('div'), editButton=el('button','编辑','quiet'), del=el('button','删除','quiet danger');
    editButton.onclick=()=>edit(r);del.onclick=async()=>{if(!confirm(`删除“${r.name}”规则？`))return;try{await api('rules/'+r.id,'DELETE',{});await renderRules();toast('规则已删除');}catch(err){toast(err.message);}};
    buttons.append(editButton,del);actions.append(label,buttons);card.append(actions);$('#rules-list').append(card);
  }
}
form.onsubmit=async e=>{e.preventDefault();const body=Object.fromEntries(new FormData(form));body.enabled=form.elements.enabled.checked;const id=body.id;delete body.id;const button=form.querySelector('[type=submit]');button.disabled=true;try{await api('rules'+(id?'/'+id:''),id?'PUT':'POST',body);form.hidden=true;await renderRules();toast('规则已保存');}catch(err){toast(err.message);}finally{button.disabled=false;}};
$('#load-sheets').onclick=async()=>{const b=$('#load-sheets');b.disabled=true;b.textContent='读取中…';try{const sheets=await api('sheets');$('#sheet-select').replaceChildren(new Option('请选择工作表',''));for(const s of sheets){const o=new Option(s.title,s.sheetId);$('#sheet-select').append(o);}toast(`已读取 ${sheets.length} 个工作表`);}catch(err){toast(err.message);}finally{b.disabled=false;b.textContent='从腾讯文档读取工作表';}};
$('#sheet-select').onchange=e=>{if(e.target.value){form.elements.sheet_id.value=e.target.value;form.elements.sheet_name.value=e.target.selectedOptions[0].textContent;}};
async function loadSettings(){
  const s=await api('settings'),f=$('#settings-form');
  for(const key of ['document_url','interval_seconds','timeout_seconds','client_id','open_id','access_token','smtp_host','smtp_port','smtp_security','smtp_sender','smtp_sender_name','smtp_password']) f.elements[key].value=s[key];
}
$('#settings-form').onsubmit=async e=>{e.preventDefault();const f=e.target,buttons=[...f.elements].filter(el=>el.type==='submit');buttons.forEach(b=>b.disabled=true);const data=Object.fromEntries(new FormData(f));try{await api('settings','PUT',data);await loadSettings();await loadRoster();toast('设置已保存');}catch(err){toast(err.message);}finally{buttons.forEach(b=>b.disabled=false);}};
if(page === 'manage') renderRules().catch(err=>toast(err.message));
else if(page === 'variables') {loadVariables();setInterval(()=>{if(!document.hidden)loadVariables();},30000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)loadVariables();});}
else if(page === 'settings') Promise.all([loadSettings(),loadRoster()]).catch(err=>toast(err.message));
else if(page === 'home'){refresh();setInterval(refresh,5000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});}

async function loadRoster() {
  const roster=await api('roster'), f=$('#source-form');
  if(roster.source) for(const key of ['sheet_id','sheet_name','column','start_row','end_row']) f.elements[key].value=key==='column'?col(roster.source[key]):roster.source[key];
  $('#roster-names').replaceChildren();
  for(const name of roster.names || []) {
    const label=el('label',undefined,'checkbox'), check=el('input');check.type='checkbox';check.value=name;check.checked=!(roster.excluded || []).includes(name);label.append(check,document.createTextNode(name));$('#roster-names').append(label);
  }
  $('#save-selection').disabled=!roster.source;
  $('#roster-state').textContent=roster.source?`${roster.names.length} 位同事 · 已选择 ${roster.names.filter(n=>!roster.excluded.includes(n)).length} 人 · 名单更新 ${date(roster.updated_at)}`:'请先配置同事名单，现有监听规则会在配置后继续检查。';
  const status=await api('status');$('#layout-state').textContent='上次同步：'+date(status.layout_updated_at);
}
$('#source-load-sheets').onclick=async()=>{
  const b=$('#source-load-sheets');b.disabled=true;
  try{const sheets=await api('sheets');$('#source-sheet-select').replaceChildren(new Option('请选择名单工作表',''));for(const s of sheets)$('#source-sheet-select').append(new Option(s.title,s.sheetId));toast(`已读取 ${sheets.length} 个工作表`);}catch(e){toast(e.message);}finally{b.disabled=false;}
};
$('#source-sheet-select').onchange=e=>{if(e.target.value){$('#source-form').elements.sheet_id.value=e.target.value;$('#source-form').elements.sheet_name.value=e.target.selectedOptions[0].textContent;}};
$('#source-form').onsubmit=async e=>{e.preventDefault();const b=e.target.querySelector('[type=submit]');b.disabled=true;try{await api('roster','PUT',Object.fromEntries(new FormData(e.target)));await loadRoster();toast('名单已更新，默认统计新同事');}catch(e){toast(e.message);}finally{b.disabled=false;}};
$('#select-all').onclick=()=>$('#roster-names').querySelectorAll('input').forEach(c=>c.checked=true);
$('#select-none').onclick=()=>$('#roster-names').querySelectorAll('input').forEach(c=>c.checked=false);
$('#save-selection').onclick=async()=>{const b=$('#save-selection');b.disabled=true;try{const selected=[...$('#roster-names').querySelectorAll('input:checked')].map(c=>c.value);await api('roster/selection','PUT',{selected});await loadRoster();toast('统计范围已保存');}catch(e){toast(e.message);}finally{b.disabled=false;}};
$('#refresh-layout').onclick=async()=>{const b=$('#refresh-layout');b.disabled=true;b.textContent='正在导出并同步…';try{const result=await api('layout/refresh','POST',{});$('#layout-state').textContent='上次同步：'+date(result.updated_at);toast('合并结构已更新');}catch(e){toast(e.message);}finally{b.disabled=false;b.textContent='刷新合并结构';}};


async function loadVariables() {
  const button = $('#refresh-variables');
  if (button.disabled) return;
  button.disabled = true;
  try {
    const data = await api('variables');
    const fragment = document.createDocumentFragment();
    for (const row of data.rows) {
      const tr = el('tr'), name = el('td'), value = el('td');
      name.append(el('code', row.name, 'variable-code'));
      if (row.value === null) value.append(el('span', 'null', 'muted'));
      else if (row.value === '') value.append(el('span', '（空）', 'muted'));
      else value.textContent = String(row.value);
      tr.append(name, el('td', row.type), el('td', row.description), value);
      ['变量名', '类型', '描述', '值'].forEach((label, i) => tr.children[i].dataset.label = label);
      fragment.append(tr);
    }
    $('#variable-rows').replaceChildren(fragment);
    $('#variables-state').textContent = `共 ${data.rows.length} 个变量 · 值更新于 ${data.generated_at.replace('T', ' ')} · 每 30 秒刷新${data.query_running ? ' · 表格查询中' : ''}`;
  } catch (error) {
    $('#variables-state').textContent = '变量刷新失败，下方可能是旧值：' + error.message;
    toast(error.message);
  } finally { button.disabled = false; }
}
$('#refresh-variables').onclick = loadVariables;

