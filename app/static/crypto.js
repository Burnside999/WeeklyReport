'use strict';
async function encryptPassword(password) {
  if(!globalThis.crypto?.subtle)throw new Error('请使用 HTTPS 访问后登录');
  const response=await fetch('/api/auth/challenge');
  if(!response.ok)throw new Error('无法获取密码加密凭据，请重试');
  const challenge=await response.json(),subtle=crypto.subtle;
  const decode=value=>Uint8Array.from(atob(value),c=>c.charCodeAt(0));
  const encode=value=>btoa(String.fromCharCode(...new Uint8Array(value)));
  const publicKey=await subtle.importKey('spki',decode(challenge.public_key),{name:'RSA-OAEP',hash:'SHA-256'},false,['encrypt']);
  const key=await subtle.generateKey({name:'AES-GCM',length:256},true,['encrypt']);
  const iv=crypto.getRandomValues(new Uint8Array(12));
  const ciphertext=await subtle.encrypt({name:'AES-GCM',iv,additionalData:new TextEncoder().encode(challenge.nonce)},key,new TextEncoder().encode(password));
  const wrapped=await subtle.encrypt({name:'RSA-OAEP'},publicKey,await subtle.exportKey('raw',key));
  return {nonce:challenge.nonce,key:encode(wrapped),iv:encode(iv),ciphertext:encode(ciphertext)};
}
