'use strict';
// Register on both login and workspace pages; never force-reload an open editor.
window.pwaRegistration = ('serviceWorker' in navigator && window.isSecureContext)
  ? navigator.serviceWorker.register('/sw.js', {scope:'/', updateViaCache:'none'}).catch(() => null)
  : Promise.resolve(null);
window.addEventListener('online', () => document.querySelector('#offline-state')?.setAttribute('hidden',''));
window.addEventListener('offline', () => document.querySelector('#offline-state')?.removeAttribute('hidden'));

if(!navigator.onLine)document.querySelector('#offline-state')?.removeAttribute('hidden');
