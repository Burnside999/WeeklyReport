// Exercise worker behavior without a real external push subscription.
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const listeners={},notifications=[],opened=[],cached=[];
let offline=false;
const self={location:{origin:'https://weekly-test.example.com'},
  addEventListener:(name,fn)=>listeners[name]=fn,
  skipWaiting:async()=>{},registration:{showNotification:async(...args)=>notifications.push(args)},
  clients:{claim:async()=>{},matchAll:async()=>[],openWindow:async url=>opened.push(url)}};
const context={self,URL,Response,caches:{open:async()=>({addAll:async urls=>cached.push(...urls)}),keys:async()=>[],match:async()=>new Response('offline')},
  fetch:async()=>{if(offline)throw Error('offline');return new Response('network');}};
vm.runInNewContext(fs.readFileSync('app/static/sw.js','utf8'),context);
async function event(name,data={}){let promise;listeners[name]({...data,waitUntil:p=>promise=p});await promise;}
(async()=>{
  await event('install');assert.deepEqual(cached,['/offline','/static/style.css']);
  await event('push',{data:{json:()=>({id:42,body:'本周还有 5 人未填写',url:'/?doc=abc'})}});
  assert.equal(notifications[0][1].body,'本周还有 5 人未填写');assert.equal(notifications[0][1].tag,'weeklyreport-42');
  await event('push',{data:{json:()=>({id:42,body:'本周还有 5 人未填写'})}});
  assert.equal(notifications[1][1].tag,notifications[0][1].tag);
  await event('notificationclick',{notification:{close(){},data:{url:'https://evil.test'}}});
  assert.equal(opened.pop(),'https://weekly-test.example.com/');
  await event('notificationclick',{notification:{close(){},data:{url:'/?doc=abc'}}});
  assert.equal(opened.pop(),'https://weekly-test.example.com/?doc=abc');
  let intercept=false;
  listeners.fetch({request:{url:'https://weekly-test.example.com/api/settings',method:'GET',mode:'cors'},respondWith(){intercept=true;}});
  assert.equal(intercept,false,'sensitive APIs bypass all caching');
  offline=true;let response;
  listeners.fetch({request:{url:'https://weekly-test.example.com/',method:'GET',mode:'navigate'},respondWith:p=>response=p});
  assert.equal(await (await response).text(),'offline');
  console.log('Service worker visibility, dedup tag, safe navigation and offline tests passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
