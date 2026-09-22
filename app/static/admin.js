'use strict';
(async()=>{
  const workspace=await workspaceReady;
  if(!workspace || page!=='admin')return;
  const form=$('#user-form'), smtp=$('#smtp-form'), roles={user:'普通用户',admin:'管理员',superadmin:'超级管理员'};
  let editing=null;
  if(account.role!=='superadmin')for(const option of [...form.elements.role.options])if(option.value!=='user')option.remove();
  async function load() {
    const users=await api('admin/users');$('#user-list').replaceChildren();
    for(const user of users){
      const card=el('article',undefined,'rule-card'),heading=el('div',undefined,'rule-top'),actions=el('div',undefined,'rule-actions');
      heading.append(el('h3',user.username),el('span',roles[user.role],'badge'));
      const edit=el('button','编辑','quiet'),remove=el('button','删除','quiet danger');
      edit.onclick=()=>open(user);remove.disabled=user.id===account.id;
      remove.onclick=async()=>{if(!confirm('删除该用户及其所有文档管理器？'))return;try{await api('admin/users/'+user.id,'DELETE',{});await load();}catch(error){toast(error.message);}};
      actions.append(edit,remove);card.append(heading,actions);$('#user-list').append(card);
    }
  }
  function open(user=null){editing=user;form.reset();form.hidden=false;$('#user-form-title').textContent=user?'编辑用户':'新建用户';form.elements.username.value=user?.username || '';form.elements.role.value=user?.role || 'user';form.elements.password.required=!user;form.elements.password.placeholder=user?'留空保持不变':'';form.scrollIntoView({behavior:'smooth',block:'start'});}
  $('#add-user').onclick=()=>open();$('#cancel-user').onclick=()=>form.hidden=true;
  form.onsubmit=async event=>{event.preventDefault();const button=form.querySelector('[type=submit]');button.disabled=true;try{const data=Object.fromEntries(new FormData(form));if(data.password)data.encrypted_password=await encryptPassword(data.password);delete data.password;await api('admin/users'+(editing?'/'+editing.id:''),editing?'PUT':'POST',data);if(editing?.id===account.id){location.reload();return;}form.hidden=true;await load();toast('用户已保存');}catch(error){toast(error.message);}finally{button.disabled=false;}};
  const settings=await api('admin/smtp');for(const [key,value] of Object.entries(settings))smtp.elements[key].value=value;
  smtp.onsubmit=async event=>{event.preventDefault();const button=smtp.querySelector('[type=submit]');button.disabled=true;try{await api('admin/smtp','PUT',Object.fromEntries(new FormData(smtp)));toast('SMTP 设置已保存');}catch(error){toast(error.message);}finally{button.disabled=false;}};
  await load();
})().catch(error=>toast(error.message));
