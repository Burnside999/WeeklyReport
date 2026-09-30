'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {EventEmitter} = require('node:events');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname,'../main.cjs'),'utf8');
const settle = () => new Promise(resolve=>setImmediate(resolve));
async function launch(saved = {}) {
  const windows=[], deadlines=[], writes=[], prompts=[], handlers={};
  const app = Object.assign(new EventEmitter(),{setAppUserModelId(){},requestSingleInstanceLock:()=>true,whenReady:()=>Promise.resolve(),getPath:()=>'/tmp/test-data',getVersion:()=> '1.0.0',quit(){this.emit('before-quit');}});
  class Window extends EventEmitter {
    constructor() { super(); this.visible=false; this.webContents=Object.assign(new EventEmitter(),{setWindowOpenHandler(){},getURL:()=>this.url,send(){}}); windows.push(this); }
    removeMenu(){} isDestroyed(){return !!this.destroyed;} destroy(){this.destroyed=true;this.emit('closed');}
    show(){this.visible=true;} hide(){this.visible=false;} focus(){} reload(){} isMinimized(){return false;}
    loadURL(url){this.url=url;return new Promise((resolve,reject)=>{this.loaded=resolve;this.failed=reject;});}
    loadFile(file){this.file=file;return Promise.resolve();}
  }
  class Tray extends EventEmitter {isDestroyed(){return false;}setToolTip(){}setContextMenu(){} }
  class Client { constructor(){this.state={};} disable(){} status(){return {};} }
  const electron={app,BrowserWindow:Window,Tray,Menu:{setApplicationMenu(){},buildFromTemplate:x=>x},Notification:{},ipcMain:{handle:(key,fn)=>{handlers[key]=fn;}},
    session:{fromPartition:()=>({setPermissionRequestHandler(){},setPermissionCheckHandler(){},on(){}})},nativeImage:{createFromPath:()=>({resize(){return {};}})},
    dialog:{showMessageBox:async(_w,options)=>{prompts.push(options);return {response:0};},showErrorBox(){throw new Error('unexpected startup failure');}},powerMonitor:new EventEmitter(),shell:{openExternal:()=>Promise.resolve()}};
  vm.runInNewContext(source, {require(name){
    if(name==='electron')return electron;
    if(name==='node:fs')return {readFileSync:()=>JSON.stringify(saved),writeFileSync:(_p,data)=>writes.push(JSON.parse(data)),renameSync(){}};
    if(name==='./notifications.cjs')return {NotificationClient:Client};
    if(name==='./security.cjs')return require('../security.cjs');
    return require(name);
  },__dirname:path.resolve(__dirname,'..'),process:{platform:'win32'},URL,AbortSignal,
    setTimeout(fn,ms){const token={fn,ms};deadlines.push(token);return token;},clearTimeout(token){if(token)token.cleared=true;},setInterval(){},clearInterval(){}});
  await settle();
  return {windows,deadlines,writes,prompts,app,handlers};
}
test('clean install tries the default directly and shows the page on success',async()=>{
  const env=await launch();assert.equal(env.windows.length,1);assert.equal(env.windows[0].url,'https://wrret.images.city/');
  assert.equal(env.writes[0].server,'https://wrret.images.city');
  env.windows[0].loaded();await settle();assert(env.windows[0].visible);assert(env.deadlines.every(t=>t.cleared));
});
test('saved server is retained; connection errors, HTTP failures and stalls allow manual setup',async()=>{
  for(const failure of ['network','http','timeout']) {
    const env=await launch({server:'https://custom.example'}), window=env.windows[0];
    assert.equal(window.url,'https://custom.example/');
    if(failure==='network')window.webContents.emit('did-fail-load',{},-105,'not found',window.url,true);
    if(failure==='http')window.webContents.emit('did-navigate',{},window.url,503);
    if(failure==='timeout')env.deadlines.find(t=>t.ms===15000).fn();
    assert.equal(env.windows.length,2);assert.match(env.windows[1].file,/settings\.html$/);assert(!window.visible);
    window.loaded();await settle();assert(!window.visible,'late success does not cover manual setup');
  }
});
test('closing explains tray exit and hides; explicit quit does not intercept closing',async()=>{
  const env=await launch(), window=env.windows[0];window.loaded();await settle();
  let prevented=false;window.emit('close',{preventDefault(){prevented=true;}});await settle();
  assert(prevented);assert(!window.visible);assert.match(env.prompts[0].detail,/右键.*退出/);
  env.app.quit();window.emit('close',{preventDefault(){assert.fail('explicit quit must be allowed');}});assert.equal(env.prompts.length,1);
});

test('installed version is returned only to the configured main frame', async () => {
  const env=await launch(), contents=env.windows[0].webContents;
  contents.mainFrame={url:'https://wrret.images.city/'};
  const info=env.handlers['client:info']({sender:contents,senderFrame:contents.mainFrame});
  assert.equal(info.id,'windows');assert.equal(info.version,'1.0.0');
  assert.throws(()=>env.handlers['client:info']({sender:contents,senderFrame:{url:'https://wrret.images.city/'}}));
  contents.mainFrame.url='https://evil.example/';
  assert.throws(()=>env.handlers['client:info']({sender:contents,senderFrame:contents.mainFrame}));
});
