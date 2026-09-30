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

test('only documented HTTPS help links open externally', () => {
  const {externalLink} = require('../security.cjs');
  for (const url of ['https://github.com/Burnside999','https://github.com/Burnside999/WeeklyReport/actions/workflows/windows.yml','https://support.apple.com/zh-cn/guide/iphone/iphea86e5236/ios']) assert.equal(externalLink(url),true,url);
  for (const url of ['file:///etc/passwd','javascript:alert(1)','https://github.com.evil.test/Burnside999','https://github.com/Burnside999/Other','https://github.com/Burnside999/WeeklyReport-evil','https://user@github.com/Burnside999','https://github.com:444/Burnside999','http://github.com/Burnside999']) assert.equal(externalLink(url),false,url);
});

test('onboarding links allow only the documented Tencent and Apple origins and paths', () => {
  const {externalLink} = require('../security.cjs');
  for(const url of ['https://docs.qq.com/','https://docs.qq.com/open/developers/','https://docs.qq.com/open/document/app/oauth2/','https://support.apple.com/zh-cn/guide/iphone/iph42ab2f3a7/18.0/ios/18.0'])assert.equal(externalLink(url),true,url);
  for(const url of ['https://docs.qq.com.evil.test/open/developers/','https://docs.qq.com/open/../../login','https://user@docs.qq.com/open/','http://docs.qq.com/open/','https://docs.qq.com:444/open/','https://support.apple.com/not-the-guide'])assert.equal(externalLink(url),false,url);
});
