'use strict';
(async () => {
  const workspace = await workspaceReady;
  if(!workspace || page !== 'home')return;
  const status = $('#push-state'), enable = $('#push-enable'), disable = $('#push-disable'), test = $('#push-test');
  const primary = $('#push-primary'), delivery = $('#push-delivery');
  const ios = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const standalone = matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  const apple = ios || (/Safari/.test(navigator.userAgent) && !/Chrome|Chromium|Edg|OPR/.test(navigator.userAgent) && /Mac/.test(navigator.platform));
  let config = null, busy = false, synced = "";
  const registration = await window.pwaRegistration;
  const supported = window.isSecureContext && registration && 'PushManager' in window && 'Notification' in window;
  const pause = value => {busy=value;for(const button of [enable,disable,test])button.disabled=value;};
  function message(text) {status.textContent=text;}
  async function refresh() {
    if(busy)return;
    try {
      config=await api('push/config');
      const sub=supported ? await registration.pushManager.getSubscription() : null;
      const oldKey=sub?.options.applicationServerKey;
      const oldEncoded=oldKey ? btoa(String.fromCharCode(...new Uint8Array(oldKey))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'') : '';
      const keyMatches=!oldEncoded || oldEncoded===config.public_key;
      const active=!!(sub && keyMatches && config.subscription_id && Notification.permission === 'granted');
      if(active && config.enabled && synced!==JSON.stringify(sub.toJSON())) {
        await api('push/subscriptions','POST',sub.toJSON());synced=JSON.stringify(sub.toJSON());
      }
      primary.textContent=config.primary ? `主推送：${config.primary.document_name} · ${config.primary.title}` : '尚未选择主推送，可在邮件模板中设置。';
      const states={pending:'等待投递',sending:'正在投递',accepted:'Apple 已接受通知',failed:'投递失败，请重试测试或检查服务器配置',expired:'通知已过期'};
      delivery.textContent=config.delivery ? '最近投递：'+(states[config.delivery.state] || config.delivery.state) : '';
      enable.hidden=active;disable.hidden=!sub;test.hidden=!active;
      enable.disabled=!config.enabled || !supported || !apple || (ios && !standalone) || Notification.permission === 'denied';
      test.disabled=!config.enabled;disable.disabled=false;
      if(!config.enabled)message('服务器尚未开启设备推送。');
      else if(ios && !standalone)message('请在 Safari 分享菜单中选择“添加到主屏幕”，再从桌面图标打开并开启通知。');
      else if(!apple)message('本版本设备推送支持 iPhone 主屏幕应用；此设备仍可正常使用网页。');
      else if(!supported)message('当前环境不支持推送。请使用 HTTPS，并将 iPhone 更新至 iOS 16.4 或以上。');
      else if(Notification.permission === 'denied')message('通知权限已关闭，请在系统设置的通知中允许“周报检查”。');
      else message(active ? '本设备已开启通知。' : '本设备尚未开启通知。');
    } catch(error) {message(error.message);enable.disabled=true;}
  }
  enable.onclick=async()=>{
    if(busy || !supported || !config)return;
    // Permission prompt must originate directly from the user gesture on iOS.
    const permission=Notification.permission === 'default' ? Notification.requestPermission() : Promise.resolve(Notification.permission);
    pause(true);
    let newSub=null;
    try {
      if(await permission !== 'granted')throw new Error('未获得通知权限，请在系统设置中允许通知。');
      await navigator.serviceWorker.ready;
      let sub=await registration.pushManager.getSubscription();
      const key=Uint8Array.from(atob(config.public_key.replace(/-/g,'+').replace(/_/g,'/')+'='.repeat((4-config.public_key.length%4)%4)),c=>c.charCodeAt(0));
      const oldKey=sub?.options.applicationServerKey;
      if(sub && oldKey && (oldKey.byteLength!==key.length || new Uint8Array(oldKey).some((v,i)=>v!==key[i]))) {
        await sub.unsubscribe();sub=null;
      }
      if(!sub){sub=await registration.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:key});newSub=sub;}
      await api('push/subscriptions','POST',sub.toJSON());
      toast('本设备通知已开启');
    } catch(error) {if(newSub)await newSub.unsubscribe().catch(()=>{});toast(error.message);}
    finally {pause(false);await refresh();}
  };
  disable.onclick=async()=>{
    if(busy)return;pause(true);
    try {
      if(config?.subscription_id)await api('push/subscriptions/'+config.subscription_id,'DELETE',{});
      const sub=await registration.pushManager.getSubscription();
      if(sub && !await sub.unsubscribe())throw new Error('系统订阅尚未取消，请再次尝试。');
      toast('本设备通知已关闭');
    } catch(error){toast(error.message);}
    finally {pause(false);await refresh();}
  };
  test.onclick=async()=>{
    if(busy)return;pause(true);
    try {await api('push/test','POST',{});toast('测试通知已排队，请留意系统通知。');}
    catch(error){toast(error.message);}
    finally {pause(false);await refresh();}
  };
  await refresh();
  setInterval(()=>{if(!document.hidden)refresh();},15000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
})().catch(error=>toast(error.message));
