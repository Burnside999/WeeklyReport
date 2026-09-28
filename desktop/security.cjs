'use strict';
function serverOrigin(value) {
  const url = new URL(value);
  const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  if (!(url.protocol === 'https:' || (url.protocol === 'http:' && loopback)) ||
      url.username || url.password || url.search || url.hash || url.pathname !== '/') {
    throw new Error('请输入 HTTPS 网站地址，不包含路径、帐号或参数。');
  }
  return url.origin;
}
function sameOrigin(value, origin) {
  try { return new URL(value).origin === origin; } catch { return false; }
}
function documentURL(origin, id) {
  const url = new URL('/', origin);
  if (typeof id === 'string' && id) url.searchParams.set('doc', id);
  return url.href;
}
function trustedSender(event, window, origin) {
  return !!window && !window.isDestroyed() && event.sender === window.webContents &&
    event.senderFrame === window.webContents.mainFrame && sameOrigin(event.senderFrame.url, origin);
}
module.exports = { serverOrigin, sameOrigin, documentURL, trustedSender };
