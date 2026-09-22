'use strict';
const form=document.querySelector('#login'),username=document.querySelector('#username'),password=document.querySelector('#password');
const remember=document.querySelector('#remember'),automatic=document.querySelector('#automatic'),button=form.querySelector('button');
let savedName='',busy=false;
async function request(path,body) {
  const response=await fetch('/api/'+path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json','X-Requested-With':'WeeklyReport'},...(body===undefined?{}:{body:JSON.stringify(body)})});
  const data=await response.json();if(!response.ok)throw new Error(data.error || '登录失败，请重试');return data;
}
function syncPassword() {
  const saved=remember.checked && username.value.trim()===savedName && !!savedName;
  password.required=!saved;password.placeholder=saved?'已记住密码，点击即可登录':'';
}
username.addEventListener('input',syncPassword);
automatic.onchange=()=>{if(automatic.checked)remember.checked=true;syncPassword();};
remember.onchange=async()=>{
  if(!remember.checked){
    automatic.checked=false;savedName='';syncPassword();button.disabled=true;
    try{await request('auth/forget',{});}catch(error){document.querySelector('#error').textContent=error.message;}
    finally{button.disabled=busy;}
  }
  syncPassword();
};
async function login(auto=false) {
  if(busy)return;busy=true;button.disabled=true;document.querySelector('#error').textContent='';
  try {
    const body={username:username.value.trim(),remember:remember.checked,automatic:automatic.checked};
    if(!password.value && savedName===body.username && remember.checked)await request('auth/resume',{...body,auto});
    else await request('login',{...body,encrypted_password:await encryptPassword(password.value)});
    password.value='';location.replace('/');
  }catch(error){document.querySelector('#error').textContent=error.message;if(auto || !password.value){savedName='';automatic.checked=false;syncPassword();}}
  finally{busy=false;button.disabled=false;}
}
form.addEventListener('submit',event=>{event.preventDefault();login();});
button.disabled=true;
request('auth/options').then(async data=>{
  // Do not overwrite input the user has already started typing.
  if(username.value || password.value)return;
  savedName=data.username;username.value=savedName;remember.checked=data.remember;automatic.checked=data.automatic;syncPassword();
  if(data.automatic)await login(true);
}).catch(error=>{document.querySelector('#error').textContent=error.message;}).finally(()=>{button.disabled=busy;});
