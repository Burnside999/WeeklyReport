'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { serverOrigin, documentURL, trustedSender } = require('../security.cjs');
test('only HTTPS origins or loopback HTTP can be configured', () => {
  assert.equal(serverOrigin('https://dev.images.city/'), 'https://dev.images.city');
  assert.equal(serverOrigin('http://127.0.0.1:18081'), 'http://127.0.0.1:18081');
  for (const value of ['http://example.com', 'https://user:pass@example.com', 'https://example.com/path', 'https://example.com/?token=1', 'file:///etc/passwd', 'javascript:alert(1)']) assert.throws(() => serverOrigin(value));
});
test('notification document ids cannot become external links', () => {
  const url = new URL(documentURL('https://dev.images.city', 'https://evil.example/#a'));
  assert.equal(url.origin, 'https://dev.images.city'); assert.equal(url.pathname, '/');
  assert.equal(url.searchParams.get('doc'), 'https://evil.example/#a');
});
test('IPC requires exact webContents, top frame and configured origin', () => {
  const frame = { url: 'https://dev.images.city/mail' }, contents = { mainFrame: null }; contents.mainFrame = frame;
  const window = { isDestroyed: () => false, webContents: contents };
  assert(trustedSender({ sender: contents, senderFrame: frame }, window, 'https://dev.images.city'));
  assert(!trustedSender({ sender: contents, senderFrame: { ...frame } }, window, 'https://dev.images.city'));
  assert(!trustedSender({ sender: {}, senderFrame: frame }, window, 'https://dev.images.city'));
  frame.url = 'https://evil.example/';
  assert(!trustedSender({ sender: contents, senderFrame: frame }, window, 'https://dev.images.city'));
});
