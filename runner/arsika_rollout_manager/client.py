"""Signed client for the runner API (used by the command line and the tests; the dashboard has its own PHP client).

An https:// runner is checked by the public key pin of its certificate (as the dashboard does), not by a certificate
authority: install.sh --agent creates a self-signed certificate."""
import hashlib
import hmac
import http.client
import json
import secrets
import ssl
import time
from urllib.parse import urlsplit

from .util import public_key_pin


class PinError(ssl.SSLError):
    pass


class Client(object):
    def __init__(self, url, secret, pin=None, timeout=120):
        self.url = url.rstrip("/")
        self.secret = secret
        self.pin = pin
        self.timeout = timeout

    def connection(self):
        parts = urlsplit(self.url)
        if parts.scheme != "https":
            return http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=self.timeout)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE                          # replaced by the pin check below
        conn = http.client.HTTPSConnection(parts.hostname, parts.port or 443, timeout=self.timeout, context=context)
        conn.connect()
        found = public_key_pin(conn.sock.getpeercert(binary_form=True))
        if not self.pin or found not in [p.strip() for p in self.pin.split(";")]:
            conn.close()
            raise PinError("the runner's certificate does not match the pin (it has %s)" % found)
        return conn

    def request(self, method, path, data=None, raw=None):
        """-> (status, document). ``raw``: bytes sent as they are (a piece of an uploaded archive) instead of JSON."""
        body = raw if raw is not None else (json.dumps(data).encode("utf-8") if data is not None else b"")
        target = urlsplit(self.url).path.rstrip("/") + path
        ts = int(time.time())
        nonce = secrets.token_hex(16)
        message = "%s\n%s\n%d\n%s\n%s" % (method, target, ts, nonce, hashlib.sha256(body).hexdigest())
        signature = hmac.new(self.secret.encode(), message.encode(), hashlib.sha256).hexdigest()
        headers = {"X-Rollout-Timestamp": str(ts), "X-Rollout-Nonce": nonce, "X-Rollout-Signature": signature,
                   "Content-Type": "application/octet-stream" if raw is not None else "application/json", "Accept": "application/json"}
        conn = self.connection()
        try:
            conn.request(method, target, body=body if method != "GET" else None, headers=headers)
            response = conn.getresponse()
            text = response.read().decode("utf-8")
            return response.status, json.loads(text or "{}")
        finally:
            conn.close()

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, data=None):
        return self.request("POST", path, data or {})

    def put(self, path, data=None):
        return self.request("PUT", path, data or {})

    def delete(self, path):
        return self.request("DELETE", path)
