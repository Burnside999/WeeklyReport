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
// Help links open in the system browser; remote content gains no arbitrary URL access.
function externalLink(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.username || url.password || url.port) return false;
    return (url.hostname === 'github.com' &&
      (url.pathname === '/Burnside999' || url.pathname === '/Burnside999/WeeklyReport' || url.pathname.startsWith('/Burnside999/WeeklyReport/'))) ||
      (url.hostname === 'support.apple.com' && url.pathname === '/zh-cn/guide/iphone/iphea86e5236/ios');
  } catch { return false; }
}
module.exports = { serverOrigin, sameOrigin, documentURL, trustedSender, externalLink };
