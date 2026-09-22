"""Exercise the browser password envelope in existing HTTP regression fixtures."""
import base64
import secrets
from aiohttp.test_utils import TestClient as BaseTestClient
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def encrypt(challenge, password):
    key, iv = secrets.token_bytes(32), secrets.token_bytes(12)
    public = serialization.load_der_public_key(base64.b64decode(challenge['public_key']))
    encode = lambda raw: base64.b64encode(raw).decode()
    return dict(nonce=challenge['nonce'],iv=encode(iv),
        key=encode(public.encrypt(key,padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))),
        ciphertext=encode(AESGCM(key).encrypt(iv,password.encode(),challenge['nonce'].encode())))


class EncryptedTestClient(BaseTestClient):
    async def _request(self, method, path, **kwargs):
        raw = kwargs.get('json')
        if (path == '/api/login' or path.startswith('/api/admin/users')) and isinstance(raw,dict) and isinstance(raw.get('password'),str):
            challenge = await (await super()._request('GET','/api/auth/challenge')).json()
            raw = dict(raw)
            raw['encrypted_password'] = encrypt(challenge,raw.pop('password'))
            kwargs['json'] = raw
        return await super()._request(method,path,**kwargs)
