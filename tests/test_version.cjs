'use strict';
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('app/static/version.js', 'utf8');
async function render({ua='', platform='', touch=0, bridge, legacy=false, version='1.0.0'} = {}) {
  const footer = {};
  const context = {
    document: {querySelector: selector => selector === '#app-version' ? footer : {content:version}},
    navigator: {userAgent:ua, platform, maxTouchPoints:touch},
    window: {weeklyReportClient:bridge, weeklyReportDesktop:legacy || undefined}
  };
  vm.runInNewContext(source, context);
  await new Promise(resolve => setImmediate(resolve));
  return footer.textContent;
}
(async () => {
  assert.equal(await render(), 'weeklyreport v1.0.0');
  assert.equal(await render({ua:'iPhone'}), 'weeklyreport (iOS version) v1.0.0');
  assert.equal(await render({platform:'MacIntel',touch:5}), 'weeklyreport (iOS version) v1.0.0');
  assert.equal(await render({platform:'MacIntel'}), 'weeklyreport v1.0.0');
  assert.equal(await render({ua:'Android'}), 'weeklyreport v1.0.0', 'Android browser is not an installed native shell');
  const bridge = {protocolVersion:1, getInfo: async () => ({id:'windows', name:'Windows', version:'1.2.3'})};
  assert.equal(await render({bridge,version:'2.0.0'}), 'weeklyreport v2.0.0 (Windows client v1.2.3)', 'application and installed shell evolve independently');
  assert.equal(await render({bridge:{...bridge,getInfo:() => ({id:'linux',name:'Linux',version:'3.0.0-beta.1'})}}), 'weeklyreport v1.0.0 (Linux client v3.0.0-beta.1)');
  assert.equal(await render({legacy:true}), 'weeklyreport v1.0.0 (Windows client 版本未知)');
  for (const bad of [null, {id:'windows',name:'<img>',version:'1.0.0'}, {id:'windows',name:'Windows',version:'latest'}]) {
    assert.equal(await render({bridge:{...bridge,getInfo:async () => bad},legacy:true}), 'weeklyreport v1.0.0 (Windows client 版本未知)');
  }
  assert.equal(await render({bridge:{...bridge,getInfo:async () => {throw Error('unavailable');}}}), 'weeklyreport v1.0.0 (Native client 版本未知)');
  console.log('Independent versions, iOS detection, extensible native bridge and legacy fallbacks passed');
})().catch(error => {console.error(error); process.exitCode=1;});
