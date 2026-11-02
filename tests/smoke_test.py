"""End-to-end smoke test: dashboard (PHP built-in server, SQLite) + runner + a fake GitHub + a fake web site.
Everything runs on 127.0.0.1 in a temporary folder; nothing on the machine is changed (except the one-time setup page
public/install/, which deletes itself).

    python tests/smoke_test.py --php php [--php-ini php.ini] [--shell bash]

Covers: setup page, sign-in and lockout messages, CSRF, password confirmation, site registration and validation,
GitHub access check, deploy, rollback, backups, settings (GitHub token), activity log, sign-out."""
import argparse
import base64
import http.cookiejar
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "runner"))
sys.path.insert(0, str(ROOT / "runner" / "tests"))
from test_runner import FakeGitHub, release_files  # noqa: E402

passed = failed = 0


def check(name, condition, detail=""):
    global passed, failed
    passed, failed = (passed + 1, failed) if condition else (passed, failed + 1)
    print(f"  {'ok  ' if condition else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not condition else ""))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Browser:
    def __init__(self, base):
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), NoRedirect())
        self.token = ""

    def raw(self, path, data=None, headers=None, method=None):
        request = urllib.request.Request(self.base + path, data=data, headers=headers or {}, method=method)
        try:
            with self.opener.open(request, timeout=120) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def call(self, method, path, data=None, csrf=True):
        headers = {"Accept": "application/json", "X-Locale": "en"}
        body = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(data).encode()
        if csrf and method != "GET":
            headers["X-CSRF-Token"] = self.token
        status, _, raw = self.raw("/api/v1/" + path, body, headers, method)
        try:
            doc = json.loads(raw)
        except ValueError:
            doc = {"raw": raw[:300]}
        if isinstance(doc.get("data"), dict) and doc["data"].get("csrf_token"):
            self.token = doc["data"]["csrf_token"]
        return status, doc

    def get(self, path):
        return self.call("GET", path)

    def post(self, path, data=None, csrf=True):
        return self.call("POST", path, data or {}, csrf)

    def put(self, path, data):
        return self.call("PUT", path, data)

    def delete(self, path, data=None):
        return self.call("DELETE", path, data)

    def form(self, path, fields):
        status, headers, raw = self.raw(path, urllib.parse.urlencode(fields).encode(), {"Content-Type": "application/x-www-form-urlencoded"}, "POST")
        return status, headers, raw.decode("utf-8", "replace")


def wait_for(url, seconds=30):
    for _ in range(seconds * 4):
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except urllib.error.HTTPError:
            return True
        except Exception:
            time.sleep(0.25)
    return False


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--php", default="php")
    parser.add_argument("--php-ini", default="")
    parser.add_argument("--shell", default=shutil.which("bash") or "/bin/bash")
    args = parser.parse_args()
    if (ROOT / "public" / "install").exists():
        sys.exit("public/install exists: remove it first (a setup page from an earlier run).")

    tmp = Path(tempfile.mkdtemp(prefix="rollout-smoke-"))
    www = tmp / "wwwroot"
    site_dir = www / "demo.test"
    site_dir.mkdir(parents=True)
    github = FakeGitHub()
    for version in ("1.0.0", "1.1.0"):
        github.publish(version, release_files(version))
    SimpleHTTPRequestHandler.log_message = lambda *a: None
    web = HTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(site_dir / "public")))
    threading.Thread(target=web.serve_forever, daemon=True).start()

    runner_port, php_port = free_port(), free_port()
    secret = secrets.token_hex(32)
    config = tmp / "runner.json"
    config.write_text(json.dumps({
        "listen": "127.0.0.1", "port": runner_port, "secret": secret, "state_dir": str(tmp / "state"), "backup_dir": str(tmp / "backup"),
        "github_api": "http://127.0.0.1:%d" % github.port, "github_token": "", "shell": args.shell, "site_roots": [str(www)],
        "health_attempts": 3, "health_delay": 0.3}), encoding="utf-8")
    env = dict(os.environ, APP_ENV="local", APP_DEBUG="false", APP_URL="http://127.0.0.1:%d" % php_port,
               APP_KEY="base64:" + base64.b64encode(secrets.token_bytes(32)).decode(), DB_CONNECTION="sqlite",
               DB_SQLITE_PATH=str(tmp / "rollout.sqlite"), RUNNER_URL="http://127.0.0.1:%d" % runner_port, RUNNER_SECRET=secret)
    php = [args.php] + (["-c", args.php_ini] if args.php_ini else [])
    logs = open(tmp / "servers.log", "w")
    runner = subprocess.Popen([sys.executable, "-m", "arsika_rollout_manager", "serve", "--config=" + str(config)], cwd=ROOT / "runner", stdout=logs, stderr=logs)
    dashboard = subprocess.Popen(php + ["-S", "127.0.0.1:%d" % php_port, "-t", "public", "scripts/dev-server.php"], cwd=ROOT, env=env,
                                 stdout=logs, stderr=logs)
    base = "http://127.0.0.1:%d" % php_port
    try:
        if not wait_for(base + "/api/v1/app") or not wait_for("http://127.0.0.1:%d/v1/status" % runner_port):
            sys.exit("servers did not start, see " + str(tmp / "servers.log"))
        admin_password = "Smoke-" + secrets.token_urlsafe(10) + "4x"

        print("== public")
        guest = Browser(base)
        status, _, html = guest.raw("/")
        check("app shell", status == 200 and b"assets/js/main.js" in html)
        status, doc = guest.get("app")
        check("GET /app as guest", status == 200 and doc["data"]["user"] is None and doc["data"]["runner"] is None)
        status, doc = guest.get("sites")
        check("guest cannot list sites", status == 401)
        for private in ("/.env", "/config/runner.php", "/runner/arsika_rollout_manager/server.py"):
            check(private + " not served", guest.raw(private)[0] in (403, 404))

        print("== setup page")
        result = subprocess.run(php + ["bin/console", "install:token", "--quiet", "--url=" + base], cwd=ROOT, env=env, capture_output=True, text=True)
        token = urllib.parse.parse_qs(urllib.parse.urlparse(result.stdout.strip()).query).get("token", [""])[0]
        check("install:token", bool(token), result.stderr)
        status, _, page = guest.form("/install/", {"token": token, "password": "short1", "password_confirmation": "short1"})
        check("password under 12 characters rejected", status == 422 and "field--error" in page)
        status, headers, _ = guest.form("/install/", {"token": token, "password": admin_password, "password_confirmation": admin_password})
        check("administrator created -> sign-in", status == 303 and "installed=1#/login" in headers.get("Location", ""), str(status))

        print("== sign-in")
        admin = Browser(base)
        admin.get("app")
        status, doc = admin.post("auth/login", {"username": "admin", "password": "wrong-password-1"})
        check("wrong password -> 401", status == 401 and doc["error"]["code"] == "invalid_credentials")
        status, doc = admin.post("auth/login", {"username": "admin", "password": admin_password})
        check("admin signs in", status == 200 and doc["data"]["user"]["username"] == "admin")
        status, doc = admin.get("app")
        check("runner reachable with the shared secret", doc["data"]["runner"]["ok"] is True, json.dumps(doc["data"]["runner"]))
        status, doc = admin.post("sites", {}, csrf=False)
        check("POST without CSRF token -> 403", status == 403)

        print("== sites")
        status, doc = admin.get("presets")
        check("presets", status == 200 and {p["id"] for p in doc["data"]} >= {"anemia-web", "php-app", "static"})
        site = {"id": "demo", "name": "Demo", "domain": "demo.test", "path": str(site_dir), "repo": "o/demo", "asset_pattern": "demo_v*.zip",
                "web_user": "", "shared": [".env", "data/"], "docroot_link": "public", "docroot_target": "public", "reload_php_fpm": False,
                "health_url": "http://127.0.0.1:%d/version.txt" % web.server_address[1], "health_contains": "version {version}",
                "backup_paths": [".env", "data/"], "backup_keep": 3, "keep_releases": 3, "backup_before_deploy": True}
        (site_dir.parent / "seed").mkdir()
        status, doc = admin.post("sites", site)
        check("adding a site needs the password", status == 422 and "password" in doc["error"]["fields"])
        status, doc = admin.post("sites", dict(site, id="Bad Id", path="/etc", password=admin_password))
        check("invalid site settings -> field errors", status == 422 and {"id", "path"} <= set(doc["error"]["fields"]), json.dumps(doc)[:300])
        status, doc = admin.post("sites", dict(site, password=admin_password))
        check("site registered", status == 201 and doc["data"]["state"] == "empty", json.dumps(doc)[:300])
        status, doc = admin.post("github/check", {"repo": "o/demo", "asset_pattern": "demo_v*.zip"})
        check("GitHub access check", status == 200 and doc["data"]["ok"] and doc["data"]["latest"] == "1.1.0")

        def run_job(payload, expect="succeeded"):
            status, doc = admin.post("sites/demo/jobs", payload)
            if status != 201:
                return status, doc
            job_id = doc["data"]["id"]
            for _ in range(240):
                status, doc = admin.get("jobs/%d" % job_id)
                if doc["data"]["status"] in ("succeeded", "failed"):
                    break
                time.sleep(0.25)
            return status, doc

        (site_dir / "shared").mkdir()
        (site_dir / "shared" / ".env").write_text("APP_KEY=demo\n")
        status, doc = run_job({"type": "deploy", "version": "1.0.0"})
        check("first deploy 1.0.0", doc["data"]["status"] == "succeeded", doc["data"].get("message", "") + doc["data"]["log"]["text"][-800:])
        status, doc = run_job({"type": "deploy", "version": "1.1.0", "params": {"backup": True}})
        check("deploy 1.1.0 with backup", doc["data"]["status"] == "succeeded" and "Backup" in doc["data"]["log"]["text"], doc["data"].get("message", ""))
        status, doc = admin.get("sites")
        demo = doc["data"][0]
        check("overview: current 1.1.0, healthy, rollback to 1.0.0", demo["current_version"] == "1.1.0" and demo["health"]["ok"]
              and demo["rollback_version"] == "1.0.0", json.dumps(demo)[:300])
        status, doc = admin.get("sites/demo/releases")
        check("releases with installed/current flags", any(r["current"] and r["version"] == "1.1.0" for r in doc["data"]["releases"]))
        status, doc = run_job({"type": "rollback"})
        check("rollback to 1.0.0", doc["data"]["status"] == "succeeded" and doc["data"]["to_version"] == "1.0.0")
        status, doc = admin.get("jobs?site=demo")
        check("job history", status == 200 and len(doc["data"]) == 3)

        print("== backups")
        status, doc = admin.get("sites/demo/backups")
        backup_id = doc["data"][0]["id"] if doc["data"] else ""
        check("backup listed", status == 200 and backup_id)
        status, doc = admin.post("sites/demo/jobs", {"type": "restore", "params": {"backup_id": backup_id}})
        check("restore needs the password", status == 422 and "password" in doc["error"]["fields"])
        status, doc = run_job({"type": "restore", "params": {"backup_id": backup_id, "safety_backup": False}, "password": admin_password})
        check("restore with password", doc["data"]["status"] == "succeeded", doc["data"].get("message", ""))
        status, doc = admin.delete("sites/demo/backups/" + backup_id, {"password": "wrong-password-1"})
        check("deleting a backup with a wrong password -> 422", status == 422)
        status, doc = admin.delete("sites/demo/backups/" + backup_id, {"password": admin_password})
        check("backup deleted", status == 200)

        print("== settings & activity")
        status, doc = admin.put("settings", {"github_token": "github_pat_" + "a" * 40, "password": admin_password})
        check("GitHub token saved (write-only)", status == 200 and doc["data"]["github_token_set"] and doc["data"]["github_token_hint"] == "…aaaa")
        status, doc = admin.get("settings")
        check("settings show the runner", status == 200 and doc["data"]["runner"]["version"] and "github_token" not in json.dumps(doc["data"]["settings"]).replace("github_token_set", "").replace("github_token_hint", ""))
        status, doc = admin.put("settings", {"github_token": "", "password": admin_password})
        check("GitHub token removed", status == 200 and not doc["data"]["github_token_set"])
        status, doc = admin.get("activity")
        actions = {a["action"] for a in doc["data"]["actions"]}
        check("activity log", {"admin_created", "site_created", "job_deploy", "job_rollback", "job_restore", "backup_deleted", "github_token_changed"} <= actions, str(actions))

        print("== account")
        new_password = admin_password + "Z9"
        status, doc = admin.put("account/password", {"current_password": admin_password, "password": new_password, "password_confirmation": new_password})
        check("password changed", status == 200)
        status, doc = admin.delete("sites/demo", {"password": new_password})
        check("site removed from the dashboard (files kept)", status == 200 and (site_dir / "current").exists())
        status, doc = admin.post("auth/logout")
        check("sign out", status == 200 and admin.get("sites")[0] == 401)
    finally:
        dashboard.terminate()
        runner.terminate()
        web.shutdown()
        github.server.shutdown()
        time.sleep(0.5)
        logs.close()
        shutil.rmtree(ROOT / "public" / "install", ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
