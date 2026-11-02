"""Signed client for the runner API (used by the command line and the tests; the dashboard has its own PHP client)."""
import hashlib
import hmac
import json
import secrets
import time
import urllib.error
import urllib.request


class Client(object):
    def __init__(self, url, secret):
        self.url = url.rstrip("/")
        self.secret = secret

    def request(self, method, path, data=None):
        body = json.dumps(data).encode("utf-8") if data is not None else b""
        ts = int(time.time())
        nonce = secrets.token_hex(16)
        message = "%s\n%s\n%d\n%s\n%s" % (method, path, ts, nonce, hashlib.sha256(body).hexdigest())
        signature = hmac.new(self.secret.encode(), message.encode(), hashlib.sha256).hexdigest()
        headers = {"X-Rollout-Timestamp": str(ts), "X-Rollout-Nonce": nonce, "X-Rollout-Signature": signature,
                   "Content-Type": "application/json"}
        request = urllib.request.Request(self.url + path, data=body if method != "GET" else None, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, data=None):
        return self.request("POST", path, data or {})

    def put(self, path, data=None):
        return self.request("PUT", path, data or {})

    def delete(self, path):
        return self.request("DELETE", path)
