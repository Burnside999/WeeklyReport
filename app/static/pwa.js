'use strict';
(async () => {
  const workspace=await workspaceReady;
  if(!workspace)return;
  const toggle=$('#push-toggle'),status=$('#push-state');
  const key='wr-notifications-'+workspace.user.id;
  const registration=await window.pwaRegistration;
  const ios=/iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform==='MacIntel' && navigator.maxTouchPoints>1);
  const standalone=matchMedia('(display-mode: standalone)').matches || navigator.standalone===true;
  const apple=ios || (/Safari/.test(navigator.userAgent) && !/Chrome|Chromium|Edg|OPR/.test(navigator.userAgent) && /Mac/.test(navigator.platform));
  const system=window.isSecureContext && 'Notification' in window && (!ios || standalone);
  let config=null,busy=false,polling=false;
  function read(){try{return JSON.parse(localStorage.getItem(key)) || {enabled:false};}catch{return {enabled:false};}}
  function save(value){localStorage.setItem(key,JSON.stringify(value));render();}
  function render(error){
    const state=read();
    toggle.checked=!!state.enabled;toggle.disabled=busy || !config;
    status.textContent=error || (state.enabled ? state.mode==='remote' ? '本设备已开启后台通知。' : state.mode==='browser' ? '本设备已开启通知，保持页面打开即可接收。' : '本设备已开启页内提醒，保持页面打开即可接收。' : ios && !standalone ? '添加到主屏幕后可接收后台通知；当前可开启页内提醒。' : '仅控制本设备的通知。');
  }
  async function getFeed(after){
    const response=await fetch('/api/notifications?after='+after,{cache:'no-store'});
    if(response.status===401){save({enabled:false});throw new Error('请重新登录后开启通知。');}
    if(!response.ok)throw new Error('暂时无法读取通知，请稍后重试。');
    const result=await response.json();
    if(result.user_id!==workspace.user.id){save({enabled:false});throw new Error('帐号已变更，请刷新页面。');}
    return result;
  }
  async function show(title,id,did,mode){
    if(mode==='inpage'){toast(title);return;}
    const url=did ? '/?doc='+encodeURIComponent(did) : '/';
    const options={body:title,tag:'weeklyreport-'+id,icon:'/static/icon-192.png',data:{url}};
    if(registration){await navigator.serviceWorker.ready;await registration.showNotification('周报提醒',options);}
    else {
      const notification=new Notification('周报提醒',options);
      notification.onclick=()=>{window.focus();location.assign(url);notification.close();};
    }
  }
  window.stopDeviceNotifications=()=>save({enabled:false});
  async function poll(){
    if(polling || busy)return;
    polling=true;
    const run=async()=>{
      const state=read();
      if(!state.enabled || state.mode==='remote' || (state.mode==='inpage' && document.hidden))return;
      if(state.mode==='browser' && Notification.permission!=='granted'){
        save({enabled:false});render('通知权限已关闭，请在系统或浏览器设置中允许。');return;
      }
      const result=await getFeed(state.cursor || 0);
      // Another tab may have switched off while the request was in flight.
      const current=read();
      if(!current.enabled || current.generation!==state.generation)return;
      for(const item of result.items){
        if(item.id<=(read().cursor || 0))continue;
        if(item.expires_at>Date.now()/1000)await show(item.title,item.id,item.document_id,state.mode);
        const latest=read();
        if(!latest.enabled || latest.generation!==state.generation)return;
        save({...latest,cursor:item.id});
      }
    };
    try {
      // One reader per origin/account prevents duplicate alerts in multiple tabs.
      if(navigator.locks)await navigator.locks.request(key,{ifAvailable:true},lock=>lock ? run() : undefined);
      else if(!document.hidden)await run();
    }catch(error){render(error.message);}finally{polling=false;}
  }
  async function refresh(){
    if(busy)return;
    try{
      config=await api('push/config');
      if(config.user_id!==workspace.user.id){save({enabled:false});config=null;throw new Error('帐号已变更，请刷新页面。');}
      if(busy)return;
      const sub=registration?.pushManager ? await registration.pushManager.getSubscription() : null;
      const oldKey=sub?.options.applicationServerKey;
      const encoded=oldKey ? btoa(String.fromCharCode(...new Uint8Array(oldKey))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'') : '';
      const keyMatches=!encoded || encoded===config.public_key;
      const state=read();
      if(sub && keyMatches && config.enabled && config.subscription_id && system && Notification.permission==='granted'){
        // Preserve subscriptions made by the previous version. No new welcome message.
        if(!state.enabled || state.mode!=='remote')save({enabled:true,mode:'remote'});
      }else if(state.mode==='remote' && state.enabled)save({enabled:false});
      render();
    }catch(error){render(error.message);}
  }
  toggle.onchange=async()=>{
    if(busy)return;
    const enabling=toggle.checked;
    // Request permission immediately within the click, before any network awaits.
    const permission=enabling && system ? (Notification.permission==='default' ? Notification.requestPermission() : Promise.resolve(Notification.permission)) : Promise.resolve('granted');
    busy=true;toggle.disabled=true;
    let created=null;
    try{
      if(enabling){
        if(await permission!=='granted')throw new Error('未获得通知权限，请在系统或浏览器设置中允许。');
        config=await api('push/config');
        if(config.user_id!==workspace.user.id){save({enabled:false});config=null;throw new Error('帐号已变更，请刷新页面。');}
        if(apple && system && registration?.pushManager && config.enabled){
          await navigator.serviceWorker.ready;
          let sub=await registration.pushManager.getSubscription();
          const bytes=Uint8Array.from(atob(config.public_key.replace(/-/g,'+').replace(/_/g,'/')+'='.repeat((4-config.public_key.length%4)%4)),c=>c.charCodeAt(0));
          const old=sub?.options.applicationServerKey;
          if(sub && old && (old.byteLength!==bytes.length || new Uint8Array(old).some((v,i)=>v!==bytes[i]))){await sub.unsubscribe();sub=null;}
          if(!sub){sub=await registration.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:bytes});created=sub;}
          const result=await api('push/subscriptions','POST',sub.toJSON());
          config.subscription_id=result.id;
          save({enabled:true,mode:'remote'});
        }else{
          const feed=await getFeed('latest');
          const mode=system ? 'browser' : 'inpage';
          await show('消息推送启动成功！','enabled-'+Date.now(),null,mode);
          save({enabled:true,mode,cursor:feed.next_cursor,generation:Date.now()});
        }
      }else{
        if(read().mode==='remote' || config?.subscription_id){
          config=await api('push/config');
          if(config.user_id!==workspace.user.id){save({enabled:false});config=null;throw new Error('帐号已变更，请刷新页面。');}
          if(config.subscription_id)await api('push/subscriptions/'+config.subscription_id,'DELETE',{});
          const sub=registration?.pushManager ? await registration.pushManager.getSubscription() : null;
          if(sub && !await sub.unsubscribe())throw new Error('系统订阅尚未取消，请再次尝试。');
          config.subscription_id=null;
        }
        save({enabled:false});
      }
      render();
    }catch(error){
      if(created)await created.unsubscribe().catch(()=>{});
      render(error.message);
    }finally{busy=false;toggle.disabled=!config;toggle.checked=!!read().enabled;}
  };
  addEventListener('storage',event=>{if(event.key===key)render();});
  await refresh();await poll();
  setInterval(poll,15000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden){refresh();poll();}});
})().catch(error=>toast(error.message));
