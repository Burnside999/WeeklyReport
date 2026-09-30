'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { NotificationClient } = require('../notifications.cjs');
function fixture(state = {}) {
  const shown = [], persisted = [], calls = [];
  let feed = { user_id: 'alice', items: [], next_cursor: 10 }, user = 'alice';
  const client = new NotificationClient({ state,
    request: async route => { calls.push(route); return route === '/api/me' ? { user: { id: user } } : feed; },
    notify: async item => { shown.push(item); }, persist: s => persisted.push({ ...s }) });
  return { client, shown, persisted, calls, feed: value => { feed = value; }, user: value => { user = value; } };
}
function item(id, title = '本周周报') { return { id, title, document_id: 'doc-1', expires_at: Date.now() / 1000 + 3600 }; }
test('enable starts at latest, sends one local welcome, and re-enabling is idempotent', async () => {
  const f = fixture(); await f.client.enable('alice'); await f.client.enable('alice');
  assert.equal(f.shown.length, 1); assert.equal(f.shown[0].title, '消息推送启动成功！');
  assert.equal(f.client.state.cursor, 10);
  assert(f.calls.includes('/api/notifications?after=latest'));
});
test('persisted cursor survives restart; duplicates and expired items are skipped', async () => {
  const f = fixture({ enabled: true, userId: 'alice', cursor: 10 });
  f.feed({ user_id: 'alice', items: [item(10), { ...item(11), expires_at: 1 }, item(12)] });
  await f.client.poll(); await f.client.poll();
  assert.deepEqual(f.shown.map(x => x.id), [12]); assert.equal(f.client.state.cursor, 12);
});
test('switching off suppresses a response already in flight', async () => {
  const f = fixture({ enabled: true, userId: 'alice', cursor: 10 });
  let resolve; f.client.request = () => new Promise(r => { resolve = r; });
  const pending = f.client.poll(); f.client.disable(); resolve({ user_id: 'alice', items: [item(11)] });
  await pending; assert.equal(f.shown.length, 0); assert.equal(f.client.state.enabled, false);
});
test('network error keeps cursor and retries successfully', async () => {
  const f = fixture({ enabled: true, userId: 'alice', cursor: 10 });
  const request = f.client.request; f.client.request = async () => { throw new Error('offline'); };
  await f.client.poll(); assert.equal(f.client.state.enabled, true); assert(f.client.error);
  f.client.request = request; f.feed({ user_id: 'alice', items: [item(11)] }); await f.client.poll();
  assert.equal(f.client.state.cursor, 11); assert.equal(f.client.error, '');
});
test('expired authentication and account mismatch disable notifications', async () => {
  for (const unauthorized of [true, false]) {
    const f = fixture({ enabled: true, userId: 'alice', cursor: 10 });
    f.client.request = async () => { if (unauthorized) throw Object.assign(new Error(), { status: 401 }); return { user_id: 'bob', items: [item(11)] }; };
    await f.client.poll(); assert.equal(f.client.state.enabled, false); assert.equal(f.shown.length, 0);
  }
});
test('failed system display does not acknowledge the message', async () => {
  const f = fixture({ enabled: true, userId: 'alice', cursor: 10 });
  f.feed({ user_id: 'alice', items: [item(11)] }); f.client.notify = async () => { throw new Error('display failed'); };
  await f.client.poll(); assert.equal(f.client.state.cursor, 10); assert(f.client.error);
});
test('failed welcome leaves the switch off', async () => {
  const f = fixture(); f.client.notify = async () => { throw new Error('display failed'); };
  await assert.rejects(f.client.enable('alice')); assert.equal(f.client.state.enabled, false);
});
test('account rebinding drops old preferences and old feed', async () => {
  const f = fixture({ enabled: true, userId: 'alice', cursor: 10 });
  f.user('bob'); await f.client.bind('bob'); assert.equal(f.client.state.enabled, false);
  await assert.rejects(f.client.bind('alice')); assert.equal(f.shown.length, 0);
});
test('disable during enable never re-enables after the request completes', async () => {
  const f = fixture(); let release;
  f.client.request = () => new Promise(r => { release = r; });
  const enable = f.client.enable('alice'); f.client.disable(); release({ user: { id: 'alice' } });
  await assert.rejects(enable); assert.equal(f.client.state.enabled, false); assert.equal(f.shown.length, 0);
});
