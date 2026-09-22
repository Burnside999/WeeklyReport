"""One-use encrypted password envelopes and revocable remembered devices."""
import base64
import hashlib
import secrets
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class BrowserAuth:
    def __init__(self, accounts, secure):
        self.accounts, self.db, self.secure = accounts, accounts.db, secure
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.challenges = {}
        self.db.execute('''CREATE TABLE IF NOT EXISTS remembered_devices (
            token TEXT PRIMARY KEY, uid TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            version INTEGER NOT NULL, expires REAL NOT NULL, automatic INTEGER NOT NULL)''')
        self.db.commit()

    def challenge(self):
        now = time.time()
        self.challenges = {k:v for k,v in self.challenges.items() if v > now}
        if len(self.challenges) >= 1000:
            self.challenges.pop(next(iter(self.challenges)))
        nonce = secrets.token_urlsafe(32)
        self.challenges[nonce] = now + 120
        public = self.key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        return dict(nonce=nonce, public_key=base64.b64encode(public).decode())

    def decrypt(self, envelope):
        try:
            nonce = envelope['nonce']
            if self.challenges.pop(nonce, 0) <= time.time():
                raise ValueError()
            decode = lambda name: base64.b64decode(envelope[name], validate=True)
            key = self.key.decrypt(decode('key'), padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
            raw = AESGCM(key).decrypt(decode('iv'), decode('ciphertext'), nonce.encode())
            password = raw.decode('utf-8')
            if len(password) > 1024:
                raise ValueError()
            return password
        except Exception:
            raise ValueError('密码加密凭据无效或已过期，请重试') from None

    @staticmethod
    def token_hash(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def remembered(self, request):
        token = request.cookies.get('wr_remember', '')
        row = self.db.execute('SELECT uid,version,expires,automatic FROM remembered_devices WHERE token=?', (self.token_hash(token),)).fetchone() if token else None
        user = self.accounts.user(uid=row[0]) if row else None
        if not user or row[1] != user['version'] or row[2] <= time.time():
            return None
        return dict(user=user, automatic=bool(row[3]))

    def forget(self, request, response):
        with self.db:
            self.db.execute('DELETE FROM remembered_devices WHERE token=? OR expires<=?', (self.token_hash(request.cookies.get('wr_remember','')), time.time()))
        response.del_cookie('wr_remember', path='/')

    def remember(self, request, response, user, automatic):
        self.forget(request, response)
        token = secrets.token_urlsafe(32)
        with self.db:
            # Bound persistent storage without extending any existing credential.
            self.db.execute('DELETE FROM remembered_devices WHERE uid=? AND token NOT IN (SELECT token FROM remembered_devices WHERE uid=? ORDER BY expires DESC LIMIT 19)', (user['id'],user['id']))
            self.db.execute('INSERT INTO remembered_devices VALUES (?,?,?,?,?)', (self.token_hash(token), user['id'], user['version'], time.time()+30*86400, int(automatic)))
        response.set_cookie('wr_remember',token,max_age=30*86400,httponly=True,secure=self.secure,samesite='Strict',path='/')
