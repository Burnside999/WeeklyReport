'use strict';
const $ = s => document.querySelector(s);
const page = ({'/mail':'mail','/variables':'variables','/settings':'settings','/manage':'manage','/admin':'admin'})[location.pathname] || 'home';
let documentId = '', account = null, documentList = [], toastTimer;
function el(tag,text,cls) {const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
function toast(message) {clearTimeout(toastTimer);$('#toast').textContent=message;$('#toast').hidden=false;toastTimer=setTimeout(()=>$('#toast').hidden=true,4500);}
async function api(path,method='GET',body) {
  const response=await fetch('/api/'+path,{method,headers:{'Content-Type':'application/json','X-Requested-With':'WeeklyReport',...(documentId?{'X-Document-ID':documentId}:{})},...(body===undefined?{}:{body:JSON.stringify(body)})});
  if(response.status===401){location.replace('/login');throw new Error('请重新登录');}
  const text=await response.text();let data;try{data=JSON.parse(text);}catch{data={error:text};}
  if(!response.ok){const error=new Error(data.error || '请求失败');error.data=data;error.status=response.status;throw error;}
  return data;
}
function documentLocation(id,path=location.pathname) {return path+(id?'?doc='+encodeURIComponent(id):'');}
function showDocuments(data) {
  documentList=data.documents;
  const select=$('#document-select');select.replaceChildren();
  for(const d of documentList)select.append(new Option(`${d.name} · ${d.enabled_rule_count || 0}个规则`,d.id));
  select.append(new Option('＋ 新建文档管理器','__new__'));select.value=documentId;
  $('#document-bar').hidden=!documentList.length;
  for(const link of document.querySelectorAll('.bottom-nav a,.brand')) {
    const path=new URL(link.href,location.origin).pathname;link.href=documentLocation(documentId,path);
  }
  const current=documentList.find(d=>d.id===documentId);
  document.title=(current?current.name+' · ':'')+'周报填写检查';
}
async function refreshDocumentNames() {const data=await api('me');showDocuments(data);return data;}
const workspaceReady=(async()=>{
  const data=await api('me');account=data.user;
  const requested=new URLSearchParams(location.search).get('doc');
  const key='wr_document:'+account.id;
  const selected=requested || sessionStorage.getItem(key);
  documentId=data.documents.find(d=>d.id===selected)?.id || data.documents[0]?.id || '';
  if(documentId){sessionStorage.setItem(key,documentId);history.replaceState(null,'',documentLocation(documentId));}
  showDocuments(data);
  const isAdmin=account.role!=='user';
  $('#nav-admin').hidden=!isAdmin;
  if(page==='admin' && !isAdmin){location.replace('/');return null;}
  $('.bottom-nav').hidden=!documentId && !isAdmin;
  for(const name of ['home','manage','mail','settings','variables','admin']) {
    const visible=name===page && (name==='admin'?isAdmin:!!documentId);
    $('#'+name).hidden=!visible;
    $('#nav-'+name).hidden=name==='admin'?!isAdmin:!documentId;
    $('#nav-'+name).classList.toggle('active',visible);
    if(visible)$('#nav-'+name).setAttribute('aria-current','page');
  }
  $('#no-documents').hidden=!!documentId || page==='admin';
  const openCreate=async()=>{
    try {
      const fresh=await api('me'),form=$('#document-form');form.reset();form.elements.name.value=fresh.next_name;
      $('#document-error').textContent='';
      const fields=$('#document-credentials');fields.hidden=fields.disabled=fresh.credentials_configured;
      fields.querySelectorAll('input').forEach(i=>i.required=!fresh.credentials_configured);
      $('#document-dialog').showModal();
    }catch(error){toast(error.message);}
  };
  $('#create-first-document').onclick=openCreate;
  $('#document-select').onchange=e=>{
    if(e.target.value==='__new__'){e.target.value=documentId;openCreate();}
    else location.assign(documentLocation(e.target.value));
  };
  $('#cancel-document').onclick=()=>$('#document-dialog').close();
  $('#document-form').onsubmit=async event=>{
    event.preventDefault();const form=event.target,button=form.querySelector('[type=submit]');button.disabled=true;
    try {const d=await api('documents','POST',Object.fromEntries(new FormData(form)));sessionStorage.setItem(key,d.id);location.assign(documentLocation(d.id,'/'));}
    catch(error){$('#document-error').textContent=error.message;}finally{button.disabled=false;}
  };
  $('#delete-document').onclick=async()=>{
    if(!confirm('删除该文档管理器及其全部监听器、邮件模板和记录？'))return;
    const button=$('#delete-document');button.disabled=true;
    try {await api('documents/'+documentId,'DELETE',{});sessionStorage.removeItem(key);location.assign('/');}
    catch(error){toast(error.message);button.disabled=false;}
  };
  return {document:documentList.find(d=>d.id===documentId),user:account};
})().catch(error=>{$('#workspace-error').hidden=false;$('#workspace-error').textContent=error.message;return null;});
$('#logout').onclick=async()=>{try{await api('logout','POST',{});location.replace('/login');}catch(error){toast(error.message);}};
