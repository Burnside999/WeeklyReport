'use strict';
const form = document.querySelector('form'), input = document.querySelector('input'), error = document.querySelector('#error'), button = document.querySelector('button');
window.desktopSettings.read().then(value => { input.value = value.server || 'https://wrret.images.city'; error.textContent = value.error || ''; document.querySelector('#client-version').textContent = `Windows client v${value.client.version}`; }).catch(() => { error.textContent = '无法读取设置，请重新打开应用。'; });
form.onsubmit = async event => {
  event.preventDefault(); button.disabled = true; error.textContent = '';
  try { await window.desktopSettings.save(input.value.trim()); }
  catch (e) { error.textContent = e.message; }
  finally { button.disabled = false; }
};
