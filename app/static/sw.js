'use strict';
const CACHE = 'weeklyreport-public-v1';
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(['/offline','/static/style.css'])).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('weeklyreport-public-') && key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if(url.origin !== self.location.origin || event.request.method !== 'GET')return;
  if(event.request.mode === 'navigate') {
    // Network only for every authenticated page. Cache contains no account data.
    event.respondWith(fetch(event.request).catch(async () => (await caches.match('/offline')) || Response.error()));
  } else if(url.pathname === '/static/style.css') {
    event.respondWith(fetch(event.request).catch(async () => (await caches.match('/static/style.css')) || Response.error()));
  }
});
self.addEventListener('push', event => {
  event.waitUntil((async () => {
    let data = {};
    try { data = event.data?.json() || {}; } catch { /* Always show a visible notification. */ }
    await self.registration.showNotification('周报提醒', {
      body: typeof data.body === 'string' ? data.body : '你有一条新的周报提醒',
      icon:'/static/icon-192.png', badge:'/static/icon-192.png',
      tag:'weeklyreport-' + String(data.id || 'message'), renotify:false,
      data:{url:typeof data.url === 'string' ? data.url : '/'}
    });
  })());
});
self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil((async () => {
    let target = new URL('/',self.location.origin);
    try {
      const candidate = new URL(event.notification.data?.url || '/',self.location.origin);
      if(candidate.origin === self.location.origin && candidate.pathname === '/')target = candidate;
    } catch { /* Only same-origin document links can be opened. */ }
    // Do not navigate an existing editor and discard unsaved work.
    const windows = await self.clients.matchAll({type:'window',includeUncontrolled:true});
    const existing = windows.find(client => client.url === target.href);
    if(existing)await existing.focus();
    else await self.clients.openWindow(target.href);
  })());
});
