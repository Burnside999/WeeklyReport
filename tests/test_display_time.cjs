const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('app/static/time.js', 'utf8'), sandbox);
for(const value of ['2026-09-24T18:50:00+08:00','2026-09-24T10:50:00Z','2026-09-24T18:50:00','2026-09-24 18:50:00']) {
  assert.equal(sandbox.formatDateTime(value),'2026-09-24 18:50:00');
}
assert.equal(sandbox.formatDateTime('2026-12-31T16:00:00Z'),'2027-01-01 00:00:00');
assert.equal(sandbox.formatDateTime(0),'1970-01-01 08:00:00');
assert.equal(sandbox.formatDateTime(null,'尚未查询'),'尚未查询');
assert.equal(sandbox.formatDateTime('invalid'),'—');
assert.equal(sandbox.formatClock('09:00'),'09:00:00');
assert.equal(sandbox.formatEventText('等待到达触发时间：2026-09-24T10:50:00+00:00'),'等待到达触发时间：2026-09-24 18:50:00');
console.log('Beijing timestamp formats, legacy notes and midnight boundaries passed');
