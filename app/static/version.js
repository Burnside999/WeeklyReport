'use strict';
// Server version is rendered with the page; installed shell version comes from
// the host bridge. Neither value is inferred from a download/release listing.
(() => {
  const footer = document.querySelector('#app-version');
  if (!footer) return;
  const version = document.querySelector('meta[name="weeklyreport-version"]')?.content;
  if (!version) return;
  const base = `weeklyreport v${version}`;
  const ios = /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  footer.textContent = ios ? `weeklyreport (iOS version) v${version}` : base;
  footer.title = '应用版本（由服务器提供）';

  const legacy = window.weeklyReportDesktop;
  const bridge = window.weeklyReportClient;
  if (!bridge && !legacy) return;
  footer.textContent = `${base} (${legacy ? 'Windows' : 'Native'} client 版本未知)`;
  footer.title = '应用版本 / 当前安装的客户端版本';
  if (!bridge || bridge.protocolVersion !== 1 || typeof bridge.getInfo !== 'function') return;

  const validVersion = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/;
  Promise.resolve().then(() => bridge.getInfo()).then(info => {
    if (!info || !/^[a-z][a-z0-9-]{0,31}$/.test(info.id) ||
        typeof info.name !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9 ._-]{0,31}$/.test(info.name) ||
        typeof info.version !== 'string' || !validVersion.test(info.version)) return;
    footer.textContent = `${base} (${info.name} client v${info.version})`;
  }).catch(() => { /* Old or unavailable bridges must not affect the page. */ });
})();
