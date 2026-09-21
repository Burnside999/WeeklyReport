'use strict';
document.querySelector('#login').addEventListener('submit', async e => {
  e.preventDefault();
  const button = e.target.querySelector('button');
  button.disabled = true;
  document.querySelector('#error').textContent = '';
  try {
    const response = await fetch('/api/login', {method:'POST', headers:{'Content-Type':'application/json','X-Requested-With':'WeeklyReport'}, body:JSON.stringify({username:document.querySelector('#username').value.trim(),password:document.querySelector('#password').value})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '登录失败，请重试');
    location.replace('/');
  } catch (err) { document.querySelector('#error').textContent = err.message; }
  finally { button.disabled = false; }
});

