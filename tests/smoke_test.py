"""End-to-end smoke test: dashboard (PHP built-in server, SQLite) + runner + a fake GitHub + a fake web site.
Everything runs on 127.0.0.1 in a temporary folder; nothing on the machine is changed (except the one-time setup page
public/install/, which deletes itself).

    python tests/smoke_test.py --php php [--php-ini php.ini] [--shell bash]

Covers: setup page, sign-in and lockout messages, CSRF, password confirmation, site registration and validation,
the site's own GitHub token (write-only, access check, removal), deploy, rollback, backups, settings, activity log,
sign-out. With openssl (PATH or ROLLOUT_TEST_OPENSSL): a second runner on HTTPS as "another server" - connection test
with a wrong key pin, without a pin and with a wrong secret, a site on that server (presets, access check, deploy, job
log, history), site ids unique across servers, "Update runner" (the runner installs a release and restarts, as systemd
would start it again), a server that is down, removing the server. The dashboard writes a linked .env through the link.
Hosting without a terminal (needs the zip extension for --agent-php-ini, default --php-ini): a hosting site, its agent
file (password), the agent on a second PHP server, connecting, the plan of database changes, a deployment. A hosting in
another network: its agent calls the dashboard's public route /api/v1/agent/ (connecting, a deployment with the health
check made by the agent, calling on by itself, removal). A Laravel site
on a hosting whose versions are uploaded through the dashboard: the upload in pieces (CSRF), the .env prepared before
connecting (password), connecting, the plan of migrations, a deployment with php artisan migrate, an artisan job
(password), the activity log."""
import argparse
import base64
import hashlib
import hmac
import http.cookiejar
import json
import os
import re
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
from arsika_rollout_manager.util import certificate_pin  # noqa: E402
from test_runner import AGENT_UPDATE_SCRIPT, FakeGitHub, laravel_files, make_zip, release_files  # noqa: E402

OPENSSL = os.environ.get("ROLLOUT_TEST_OPENSSL") or shutil.which("openssl")

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


def hosting_tests(admin, password, tmp, github, agent_php, base, run_job):
    """A site on a "hosting": a folder served by a second PHP server where only the agent file changes anything."""
    docroot = tmp / "hosting" / "public_html"
    docroot.mkdir(parents=True)
    github.publish_source("o/hosted", "1.0.0", {"index.html": "<h1>hosted 1.0.0</h1>", "version.txt": "version 1.0.0"})
    port = free_port()
    server = subprocess.Popen(agent_php + ["-S", "127.0.0.1:%d" % port, "-t", str(docroot)], cwd=str(docroot),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        site = {"preset": "hosting-static", "id": "hosted", "name": "Hosted", "repo": "o/hosted", "source": "source",
                "deploy_mode": "hosting", "health_url": "http://127.0.0.1:%d/version.txt" % port, "health_contains": "version {version}",
                "password": password}
        status, doc = admin.post("sites", site)
        check("hosting site created, waiting for its agent", status == 201 and doc["data"]["state"] == "unpaired"
              and doc["data"]["deploy_mode"] == "hosting", json.dumps(doc)[:300])
        status, doc = admin.post("sites/hosted/agent-file", {})
        check("the agent file needs the password", status == 422 and "password" in doc["error"]["fields"])
        status, doc = admin.post("sites/hosted/agent-file", {"password": password})
        agent = doc.get("data", {})
        check("agent file for the site, pointing at this dashboard", status == 200 and "const RA_DASHBOARD = '%s';" % base in agent.get("content", "")
              and agent.get("filename", "").startswith("rollout-agent-"), json.dumps(doc)[:200])
        (docroot / agent["filename"]).write_text(agent["content"], encoding="utf-8")
        url = "http://127.0.0.1:%d/%s" % (port, agent["filename"])
        wait_for(url)
        with urllib.request.urlopen(url, timeout=10) as response:
            page = response.read().decode("utf-8")
        check("the agent page links back to the dashboard", base + "/?connect=hosted&amp;agent=" in page)
        status, doc = admin.post("sites/hosted/agent/connect", {"url": url, "password": password})
        check("hosting connected through the dashboard", status == 200 and doc["data"]["hosting"]["connected"]
              and doc["data"]["state"] == "empty", json.dumps(doc)[:300])
        status, doc = admin.get("sites/hosted/plan?version=1.0.0")
        check("plan of database changes", status == 200 and doc["data"]["migrations"] == [], json.dumps(doc)[:200])
        status, doc = run_job({"type": "deploy", "version": "1.0.0"}, site_id="hosted")
        check("deploy on the hosting", doc["data"]["status"] == "succeeded" and (docroot / "version.txt").read_text() == "version 1.0.0",
              doc["data"].get("message", "") + doc["data"]["log"]["text"][-500:])
        status, doc = admin.get("activity")
        actions = {a["action"] for a in doc["data"]["actions"]}
        check("agent file and connection in the activity log", {"agent_file", "agent_connected"} <= actions, str(actions))
        status, doc = admin.delete("sites/hosted", {"password": password})
        check("hosting site removed (the agent forgets its secret)", status == 200)
    finally:
        server.terminate()
        server.wait(10)


def laravel_tests(admin, password, tmp, agent_php, run_job):
    """A Laravel site (a fake Laravel application) on a "hosting", its versions uploaded through the dashboard."""
    docroot = tmp / "laravel" / "public_html"
    docroot.mkdir(parents=True)
    db = (tmp / "laravel.sqlite").as_posix()
    port = free_port()
    server = subprocess.Popen(agent_php + ["-S", "127.0.0.1:%d" % port, "-t", str(docroot)], cwd=str(docroot),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def finished(job_id):
        for _ in range(240):
            status, doc = admin.get("sites/absen/jobs/%d" % job_id)
            if doc["data"]["status"] in ("succeeded", "failed"):
                return doc["data"]
            time.sleep(0.25)
        return doc["data"]

    try:
        status, doc = admin.post("sites", {"preset": "hosting-laravel", "id": "absen", "name": "Absen", "source": "upload", "repo": "",
                                           "health_contains": "app {version}", "password": password})
        check("Laravel hosting site without a repository (its versions are uploaded)", status == 201
              and doc["data"]["settings"]["hosting_app"] == "laravel" and doc["data"]["settings"]["source"] == "upload", json.dumps(doc)[:300])
        archive = make_zip({"absen/" + k: v for k, v in laravel_files("1.0.0").items()})
        status, doc = admin.post("sites/absen/uploads", {"name": "absen#1.0.0.zip", "size": len(archive)})
        start = doc.get("data") or {}
        check("upload started: version from the file name, pieces of at most 1 MB", status == 201 and start.get("version") == "1.0.0"
              and 0 < start.get("chunk_size", 0) <= 1 << 20, json.dumps(doc)[:300])
        status, _, raw = admin.raw("/api/v1/sites/absen/uploads/%s?offset=0" % start["id"], archive[:100],
                                   {"Accept": "application/json", "Content-Type": "application/octet-stream"}, "POST")
        check("a piece without the CSRF token is refused", status == 403)
        offset = 0
        while offset < len(archive):
            status, _, raw = admin.raw("/api/v1/sites/absen/uploads/%s?offset=%d" % (start["id"], offset), archive[offset:offset + 1500],
                                       {"Accept": "application/json", "Content-Type": "application/octet-stream", "X-CSRF-Token": admin.token}, "POST")
            if status != 200:
                break
            offset = json.loads(raw)["data"]["received"]
        check("the archive arrived in pieces", offset == len(archive), raw[:300])
        status, doc = admin.post("sites/absen/uploads/%s/finish" % start["id"])
        job = finished(doc["data"]["id"]) if status == 201 else {}
        check("the upload job stored version 1.0.0 (local .env removed)", job.get("status") == "succeeded" and ".env" in job["log"]["text"],
              json.dumps(doc)[:300] + str(job.get("message")))
        status, doc = admin.get("sites/absen/releases")
        check("uploaded versions are listed like releases", status == 200 and [r["version"] for r in doc["data"]["releases"]] == ["1.0.0"]
              and doc["data"]["releases"][0]["uploaded"], json.dumps(doc)[:300])
        status, doc = admin.post("sites/absen/env/read", {})
        check("reading the .env needs the password", status == 422 and "password" in doc["error"]["fields"])
        status, doc = admin.post("sites/absen/env/read", {"password": password})
        check(".env template from .env.example (production, new APP_KEY)", status == 200 and not doc["data"]["exists"]
              and "APP_ENV=production" in doc["data"]["template"] and "APP_KEY=base64:" in doc["data"]["template"], json.dumps(doc)[:300])
        content = "APP_NAME=Absen\nAPP_KEY=\nDB_CONNECTION=sqlite\nDB_DATABASE=%s\n" % db
        status, doc = admin.put("sites/absen/env", {"content": content, "password": password})
        check(".env prepared in the dashboard before connecting", status == 200 and doc["data"]["where"] == "dashboard", json.dumps(doc)[:300])
        status, doc = admin.post("sites/absen/agent-file", {"password": password})
        agent = doc["data"]
        (docroot / agent["filename"]).write_text(agent["content"], encoding="utf-8")
        url = "http://127.0.0.1:%d/%s" % (port, agent["filename"])
        wait_for(url)
        with urllib.request.urlopen(url, timeout=10) as response:
            page = response.read().decode("utf-8")
        check("the agent page has the address of the dashboard filled in", 'id="ra-dash"' in page)
        status, doc = admin.post("sites/absen/agent/connect", {"url": url, "password": password})
        check("connected: the prepared .env went to the hosting", status == 200 and doc["data"]["env"]["on_hosting"]
              and not doc["data"]["env"]["pending"] and doc["data"]["state"] == "empty", json.dumps(doc)[:300])
        status, doc = admin.get("sites/absen/plan?version=1.0.0")
        check("plan: the Laravel migrations of the release", status == 200 and doc["data"]["kind"] == "laravel"
              and [m["name"] for m in doc["data"]["migrations"]] == ["2026_01_01_000000_create_notes.php"], json.dumps(doc)[:300])
        status, doc = run_job({"type": "deploy", "version": "1.0.0", "params": {"migrations": "auto"}}, site_id="absen")
        with urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=10) as response:
            served = response.read().decode("utf-8")
        check("deploy: php artisan migrate inside PHP, only public/ in the website folder", doc["data"]["status"] == "succeeded"
              and "php artisan migrate --force" in doc["data"]["log"]["text"] and served == "app 1.0.0 env=Absen key=set public=web storage=ok"
              and not (docroot / "vendor").exists() and not (docroot / ".env").exists(), doc["data"].get("message", "") + served)
        status, doc = admin.post("sites/absen/jobs", {"type": "artisan", "params": {"command": "migrate:status"}})
        check("an artisan command needs the password", status == 422 and "password" in doc["error"]["fields"])
        status, doc = run_job({"type": "artisan", "params": {"command": "migrate:status"}, "password": password}, site_id="absen")
        check("artisan job through the dashboard", doc["data"]["status"] == "succeeded" and "Ran 2026_01_01_000000_create_notes" in doc["data"]["log"]["text"],
              doc["data"].get("message", ""))
        status, doc = admin.get("activity")
        actions = {a["action"] for a in doc["data"]["actions"]}
        check("upload, .env and artisan in the activity log", {"version_uploaded", "env_viewed", "env_saved", "job_artisan"} <= actions, str(actions))
        check("the .env content is not in the activity log", "DB_DATABASE" not in json.dumps(doc["data"]))
        status, doc = admin.delete("sites/absen", {"password": password})
        check("Laravel hosting site removed", status == 200)
    finally:
        server.terminate()
        server.wait(10)


def remote_tests(admin, password, tmp, github, args, logs, run_job):
    """A second runner on HTTPS (as install.sh --agent sets it up) plays the other server."""
    cert, key = tmp / "agent-cert.pem", tmp / "agent-key.pem"
    subprocess.run([OPENSSL, "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-days", "2",
                    "-subj", "/CN=smoke-agent", "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True)
    pin = certificate_pin(str(cert))
    port, secret = free_port(), secrets.token_hex(32)
    www = tmp / "agent-www"
    (www / "remote.test" / "shared").mkdir(parents=True)
    (www / "remote.test" / "shared" / ".env").write_text("APP_KEY=remote\n")
    config = tmp / "agent.json"
    config.write_text(json.dumps({
        "listen": "127.0.0.1", "port": port, "secret": secret, "state_dir": str(tmp / "agent-state"), "backup_dir": str(tmp / "agent-backup"),
        "github_api": "http://127.0.0.1:%d" % github.port, "shell": args.shell, "site_roots": [str(www)], "tls_cert": str(cert),
        "tls_key": str(key), "web_user": "www-data", "platform": "linux", "update_repo": "o/rollout"}), encoding="utf-8")

    def start_agent():
        return subprocess.Popen([sys.executable, "-m", "arsika_rollout_manager", "serve", "--config=" + str(config)], cwd=ROOT / "runner",
                                stdout=logs, stderr=logs)
    agent = start_agent()
    url = "https://127.0.0.1:%d" % port
    try:
        for _ in range(60):
            status, doc = admin.post("servers/test", {"url": url, "secret": secret, "tls_pin": pin})
            if status == 200:
                break
            time.sleep(0.25)
        check("connection test with the key pin", status == 200 and doc["data"]["status"]["tls"], json.dumps(doc)[:300])
        wrong_pin = "sha256//" + "A" * 43 + "="
        status, doc = admin.post("servers/test", {"url": url, "secret": secret, "tls_pin": wrong_pin})
        check("a wrong key pin is refused", status == 422 and "tls_pin" in doc["error"]["fields"], json.dumps(doc)[:300])
        status, doc = admin.post("servers/test", {"url": url, "secret": secret, "tls_pin": ""})
        check("a self-signed certificate without a pin is refused", status == 422 and "tls_pin" in doc["error"]["fields"], json.dumps(doc)[:300])
        status, doc = admin.post("servers/test", {"url": url, "secret": "f" * 64, "tls_pin": pin})
        check("a wrong secret is refused", status == 422 and "secret" in doc["error"]["fields"], json.dumps(doc)[:300])
        status, doc = admin.post("servers/test", {"url": "http://192.168.1.20:8790", "secret": secret})
        check("plain http to another machine is refused", status == 422 and "url" in doc["error"]["fields"])
        status, doc = admin.post("servers", {"name": "VM2", "url": url, "secret": secret, "tls_pin": pin})
        check("adding a server needs the password", status == 422 and "password" in doc["error"]["fields"])
        status, doc = admin.post("servers", {"name": "VM2", "url": url + "/", "secret": secret, "tls_pin": pin, "password": password})
        server_id = str(doc.get("data", {}).get("id", ""))
        check("server added", status == 201 and server_id.isdigit() and doc["data"]["status"]["platform"] == "linux", json.dumps(doc)[:300])
        status, doc = admin.get("servers")
        names = [(s["id"], s["local"], bool(s["status"])) for s in doc["data"]]
        check("servers list: this server and VM2, both answer", names == [("local", True, True), (server_id, False, True)], str(names))
        check("the secret is never sent back", secret not in json.dumps(doc))
        status, doc = admin.get("presets?server=" + server_id)
        presets = {p["id"]: p["settings"] for p in doc.get("data", [])}
        check("presets of VM2 use its web server user", presets.get("php-app", {}).get("web_user") == "www-data", json.dumps(doc)[:200])
        status, doc = admin.post("github/check", {"repo": "o/demo", "asset_pattern": "demo_v*.zip", "server": server_id})
        check("access check through VM2", status == 200 and doc["data"]["ok"])
        site = {"server": server_id, "id": "demo", "domain": "remote.test", "path": str(www / "remote.test"), "repo": "o/demo",
                "asset_pattern": "demo_v*.zip", "web_user": "", "shared": [".env"], "reload_php_fpm": False, "password": password}
        status, doc = admin.post("sites", site)
        check("site ids are unique across servers", status == 422 and "id" in doc["error"]["fields"], json.dumps(doc)[:300])
        status, doc = admin.post("sites", dict(site, id="remote"))
        check("site created on VM2", status == 201 and doc["data"]["server"]["id"] == server_id, json.dumps(doc)[:300])
        status, doc = run_job({"type": "deploy", "version": "1.1.0"}, site_id="remote")
        check("deploy on VM2", doc["data"]["status"] == "succeeded" and (www / "remote.test" / "current").exists(),
              doc["data"].get("message", "") + doc["data"]["log"]["text"][-500:])
        status, doc = admin.get("jobs?site=remote")
        check("job history of the VM2 site", status == 200 and len(doc["data"]) == 1 and doc["data"][0]["site_id"] == "remote")
        status, doc = admin.get("sites/remote")
        check("site page names its server", status == 200 and doc["data"]["server"]["name"] == "VM2" and doc["data"]["current_version"] == "1.1.0")
        status, doc = admin.get("sites")
        ids = {s["id"]: s["server"]["id"] for s in doc["data"]["sites"]}
        check("overview lists the sites of both servers", ids == {"demo": "local", "remote": server_id}, str(ids))

        version = admin.get("app")[1]["data"]["app"]["version"]
        github.publish(version, {"deploy/install.sh": AGENT_UPDATE_SCRIPT, "README.md": "release"}, repo="o/rollout",
                       asset="arsika-rollout-manager-v%s.zip" % version)
        status, doc = admin.post("servers/%s/update" % server_id, {})
        check("updating a runner needs the password", status == 422 and "password" in doc["error"]["fields"])
        status, doc = admin.post("servers/local/update", {"password": password})
        check("this server's runner is updated with the dashboard, not here", status == 400)
        status, doc = admin.post("servers/%s/update" % server_id, {"password": password})
        update_id = doc.get("data", {}).get("id")
        check("update runner of VM2 started", status == 201 and doc["data"]["site_id"] == "_runner", json.dumps(doc)[:300])
        try:
            agent.wait(30)                                         # the runner stops after the job (systemd starts it again)
            stopped = True
        except subprocess.TimeoutExpired:
            stopped = False
        check("the runner of VM2 stopped by itself after the update", stopped)
        agent = start_agent()                                      # what systemd does (Restart=always)
        for _ in range(60):
            status, doc = admin.get("servers/%s/jobs/%d" % (server_id, update_id))
            if status == 200:
                break
            time.sleep(0.25)
        check("the update job succeeded and its log is kept", status == 200 and doc["data"]["status"] == "succeeded"
              and "--agent --self-update" in (www.parent / "update.log").read_text(), json.dumps(doc)[:400])
        status, doc = admin.get("sites/remote/jobs/%d" % update_id)
        check("a runner job is not a job of a site", status == 404)

        agent.terminate()
        agent.wait(10)
        status, doc = admin.get("sites")
        down = [s for s in doc["data"]["servers"] if not s["ok"]]
        check("a server that is down: the other sites are still listed", status == 200 and [s["id"] for s in doc["data"]["sites"]] == ["demo"]
              and len(down) == 1 and down[0]["name"] == "VM2" and "VM2" in down[0]["error"], json.dumps(doc["data"]["servers"]))
        status, doc = admin.get("sites/remote")
        check("its site answers with the reason", status == 503 and "VM2" in doc["error"]["message"], json.dumps(doc)[:200])
        status, doc = admin.delete("servers/" + server_id, {"password": password})
        check("server removed (its sites stay on it)", status == 200 and doc["data"]["sites"] == 1)
        status, doc = admin.get("servers")
        check("only this server is left", [s["id"] for s in doc["data"]] == ["local"])
        status, doc = admin.get("activity")
        actions = {a["action"] for a in doc["data"]["actions"]}
        check("server changes in the activity log", {"server_added", "server_removed"} <= actions, str(actions))
        check("the server secret never appears in the activity log", secret not in json.dumps(doc["data"]))
    finally:
        if agent.poll() is None:
            agent.terminate()


def poll_call(relay, site, key, ts, nonce, url):
    """One call of an agent to the public route of the dashboard, signed with ``key`` (None: a wrong signature)."""
    message = "\n".join(["POLL", site, str(ts), nonce, "1.6.1", url, "", "0", "", hashlib.sha256(b"").hexdigest()])
    headers = {"X-Rollout-Timestamp": str(ts), "X-Rollout-Nonce": nonce, "X-Rollout-Agent": "1.6.1", "X-Rollout-Agent-Url": url,
               "X-Rollout-Result": "", "X-Rollout-Result-Status": "0", "X-Rollout-Result-Type": "", "X-Rollout-Wait": "0",
               "X-Rollout-Signature": hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest() if key else "0" * 64}
    request = urllib.request.Request(relay + "/api/v1/agent/%s/poll" % site, data=b"", headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


def pull_tests(admin, password, tmp, github, agent_php, php, env, logs, base, run_job):
    """A hosting in another network (no address the dashboard's server can open): its agent calls the dashboard (the
    public route /api/v1/agent/<site>/poll, passed on to the runner) and takes the runner's requests that way."""
    docroot = tmp / "office" / "public_html"
    docroot.mkdir(parents=True)
    github.publish_source("o/office", "1.0.0", {"index.html": "<h1>office 1.0.0</h1>", "version.txt": "version 1.0.0"})
    relay_port, port, web_port = free_port(), free_port(), free_port()
    # PHP's built-in server answers one request at a time: the agent calls a second server of the same dashboard, its
    # rounds run in one server and the website (for the health checks the agent makes) is served by another
    servers = [subprocess.Popen(php + ["-S", "127.0.0.1:%d" % relay_port, "-t", "public", "scripts/dev-server.php"], cwd=ROOT, env=env,
                                stdout=logs, stderr=logs),
               subprocess.Popen(agent_php + ["-d", "max_execution_time=15", "-S", "127.0.0.1:%d" % port, "-t", str(docroot)],
                                cwd=str(docroot), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
               subprocess.Popen(agent_php + ["-S", "127.0.0.1:%d" % web_port, "-t", str(docroot)], cwd=str(docroot),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
    halt = threading.Event()
    try:
        relay = "http://127.0.0.1:%d" % relay_port
        status, doc = None, None
        check("public route for agents: /api/v1/agent/ping", wait_for(relay + "/api/v1/agent/ping"))
        with urllib.request.urlopen(relay + "/api/v1/agent/ping", timeout=30) as response:
            ping = json.loads(response.read().decode("utf-8"))["data"]
        check("the ping tells the dashboard's time (the agent page compares the clocks)", abs(ping.get("time", 0) - time.time()) < 10,
              json.dumps(ping))
        site = {"preset": "hosting-static", "id": "office", "name": "Office", "repo": "o/office", "source": "source",
                "deploy_mode": "hosting", "health_url": "http://127.0.0.1:%d/version.txt" % web_port, "health_contains": "version {version}",
                "password": password}
        status, doc = admin.post("sites", site)
        check("hosting site in another network created", status == 201, json.dumps(doc)[:300])
        status, doc = admin.post("sites/office/agent-file", {"password": password})
        agent = doc["data"]
        content = agent["content"].replace("const RA_DASHBOARD = '%s';" % base, "const RA_DASHBOARD = '%s';" % relay)
        (docroot / agent["filename"]).write_text(content, encoding="utf-8")
        url = "http://127.0.0.1:%d/%s" % (port, agent["filename"])
        wait_for(url)
        with urllib.request.urlopen(url, timeout=30) as response:
            page = response.read().decode("utf-8")
        check("the agent page: the dashboard answers its call", '<td>Menghubungi dasbor / Calls the dashboard</td><td class="ok">' in page)
        # an agent whose clock is 7 hours off: the right key, another time -> the runner's time with a proof of that key
        key = re.search(r"const RA_PAIRING_KEY = '([0-9a-f]{64})';", content).group(1)
        status, answer = poll_call(relay, "office", key, int(time.time()) - 7 * 3600, "f" * 32, url)
        fields = (answer.get("error") or {}).get("fields") or {}
        proof = hmac.new(key.encode(), ("arsika-rollout-manager clock\n%s\n%s" % (fields.get("time"), "f" * 32)).encode(),
                         hashlib.sha256).hexdigest()
        check("another clock: the dashboard answers 403 clock with its time and the proof", status == 403
              and answer["error"]["code"] == "clock" and abs(fields.get("time", 0) - time.time()) < 10 and fields.get("proof") == proof,
              "%s %s" % (status, json.dumps(answer)[:300]))
        status, answer = poll_call(relay, "office", None, int(time.time()), "e" * 32, url)
        check("a refused agent hears 403 bad_signature (not 503 'the runner refused the dashboard')",
              status == 403 and answer["error"]["code"] == "bad_signature", "%s %s" % (status, json.dumps(answer)[:300]))

        def keep_open():                                             # the agent's page stays open in a browser
            while not halt.is_set():
                try:
                    urllib.request.urlopen(url + "?poll=run&page=1", timeout=90).read()
                except Exception:
                    pass
                halt.wait(0.3)

        threading.Thread(target=keep_open, daemon=True).start()

        def agent_state():
            with urllib.request.urlopen(url + "?poll=status", timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))

        for _ in range(60):
            if agent_state()["contact"] is not None:
                break
            time.sleep(0.5)
        status, doc = admin.post("sites/office/agent/connect", {"url": "http://127.0.0.1:1/" + agent["filename"], "password": password})
        hosting = (doc.get("data") or {}).get("hosting") or {}
        check("connected although the dashboard's server cannot open its address: the agent calls the dashboard",
              status == 200 and hosting.get("connected") and hosting.get("mode") == "pull" and doc["data"]["state"] == "empty",
              json.dumps(doc)[:300])
        status, doc = run_job({"type": "deploy", "version": "1.0.0"}, site_id="office")
        check("deploy through the dashboard, health check by the agent", doc["data"]["status"] == "succeeded"
              and (docroot / "version.txt").read_text() == "version 1.0.0" and "from the hosting (its agent)" in doc["data"]["log"]["text"],
              doc["data"].get("message", "") + doc["data"]["log"]["text"][-500:])
        halt.set()                                                   # the page is closed: the agent keeps calling itself
        time.sleep(12)
        status, doc = admin.get("sites/office")
        hosting = (doc.get("data") or {}).get("hosting") or {}
        check("the agent keeps calling by itself", status == 200 and hosting.get("online") and hosting.get("last_seen") is not None
              and hosting["last_seen"] < 8,
              json.dumps({k: hosting.get(k) for k in ("online", "last_seen", "error", "mode")}) + " " + json.dumps(agent_state()))
        status, doc = admin.delete("sites/office", {"password": password})
        check("site in another network removed (its agent forgets its secret)", status == 200 and not agent_state()["paired"])
    finally:
        halt.set()
        for server in servers:
            server.terminate()
            server.wait(10)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--php", default="php")
    parser.add_argument("--php-ini", default="")
    parser.add_argument("--agent-php-ini", default="", help="php.ini with the zip extension for the hosting agent (default: --php-ini)")
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
        "github_api": "http://127.0.0.1:%d" % github.port, "shell": args.shell, "site_roots": [str(www)],
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
        site_token = "github_pat_" + "a" * 40
        status, doc = admin.post("sites", dict(site, password=admin_password, github_token=site_token))
        check("site registered with its own GitHub token", status == 201 and doc["data"]["state"] == "empty"
              and doc["data"]["github_token_set"] and doc["data"]["github_token_hint"] == "github_pat_…aaaa", json.dumps(doc)[:300])
        status, doc = admin.get("sites/demo")
        check("the token is never sent back", status == 200 and site_token not in json.dumps(doc))
        status, doc = admin.post("github/check", {"repo": "o/demo", "asset_pattern": "demo_v*.zip", "site": "demo"})
        check("GitHub access check with the site's token", status == 200 and doc["data"]["ok"] and doc["data"]["latest"] == "1.1.0")
        status, doc = admin.post("github/check", {"repo": "o/demo", "github_token": "not a token"})
        check("access check refuses a malformed token", status == 200 and not doc["data"]["ok"])

        def run_job(payload, expect="succeeded", site_id="demo"):
            status, doc = admin.post("sites/%s/jobs" % site_id, payload)
            if status != 201:
                return status, doc
            job_id = doc["data"]["id"]
            for _ in range(240):
                status, doc = admin.get("sites/%s/jobs/%d" % (site_id, job_id))
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
        demo = doc["data"]["sites"][0]
        check("overview: current 1.1.0, healthy, rollback to 1.0.0", demo["current_version"] == "1.1.0" and demo["health"]["ok"]
              and demo["rollback_version"] == "1.0.0" and demo["server"]["local"], json.dumps(demo)[:300])
        check("overview: this server answered", doc["data"]["servers"] == [{"id": "local", "name": "", "local": True, "ok": True, "error": ""}],
              json.dumps(doc["data"]["servers"]))
        status, doc = admin.get("jobs/%d" % 1)
        check("job link of version 1.1 (this server)", status == 200 and doc["data"]["site_id"] == "demo")
        status, doc = admin.get("sites/demo/jobs/99999")
        check("unknown job of a site -> 404", status == 404)
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
        status, doc = admin.get("settings")
        check("settings show the runner and the sites with a token", status == 200 and doc["data"]["runner"]["version"]
              and doc["data"]["runner"]["github_tokens"] == 1 and "settings" not in doc["data"], json.dumps(doc)[:300])
        status, doc = admin.put("settings", {"github_token": "github_pat_" + "c" * 40, "password": admin_password})
        check("no single GitHub token for every site any more", status in (404, 405), str(status))
        status, doc = admin.put("sites/demo", {"remove_github_token": True, "password": admin_password})
        check("site token removed in the site settings", status == 200 and not doc["data"]["github_token_set"], json.dumps(doc)[:300])
        status, doc = admin.get("activity")
        actions = {a["action"] for a in doc["data"]["actions"]}
        check("activity log", {"admin_created", "site_created", "site_updated", "job_deploy", "job_rollback", "job_restore",
                               "backup_deleted"} <= actions, str(actions))
        check("tokens never appear in the activity log", site_token not in json.dumps(doc["data"]))

        print("== other servers")
        if not OPENSSL:
            print("  (skipped: openssl not found)")
        else:
            remote_tests(admin, admin_password, tmp, github, args, logs, run_job)

        print("== hosting without a terminal")
        agent_php = [args.php] + (["-c", args.agent_php_ini or args.php_ini] if (args.agent_php_ini or args.php_ini) else [])
        modules = subprocess.run(agent_php + ["-m"], capture_output=True, text=True).stdout.lower().split()
        if "zip" not in modules:
            print("  (skipped: the PHP for the agent has no zip extension; pass --agent-php-ini)")
        else:
            hosting_tests(admin, admin_password, tmp, github, agent_php, base, run_job)
            print("== hosting in another network: the agent calls the dashboard")
            pull_tests(admin, admin_password, tmp, github, agent_php, php, env, logs, base, run_job)
            print("== Laravel on a hosting, uploaded versions")
            laravel_tests(admin, admin_password, tmp, agent_php, run_job)

        print("== .env behind a link")
        envdir = tmp / "envlink"
        envdir.mkdir()
        (envdir / "real.env").write_text("APP_KEY=keep\n")
        try:
            os.symlink(envdir / "real.env", envdir / ".env")
        except OSError as e:
            print("  (skipped: no symlink support: %s)" % e)
        else:
            code = "require 'app/Core/Env.php'; App\\Core\\Env::write($argv[1], ['RUNNER_SECRET' => 'x']);"
            subprocess.run(php + ["-r", code, str(envdir / ".env")], cwd=ROOT, check=True)
            check("a linked .env is written through the link (shared .env of an installed release)",
                  os.path.islink(envdir / ".env") and "RUNNER_SECRET=x" in (envdir / "real.env").read_text()
                  and "APP_KEY=keep" in (envdir / "real.env").read_text())

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
