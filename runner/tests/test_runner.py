"""Integration test of the runner with a fake GitHub API, a fake web site and a fake docker command (no root, no
systemd, no MySQL, no Docker needed).

    python runner/tests/test_runner.py            (Linux, or Windows with symlink rights; bash in PATH or ROLLOUT_TEST_SHELL;
                                                   openssl in PATH or ROLLOUT_TEST_OPENSSL for the HTTPS tests)

Covers: signed API, site validation, one read-only GitHub token per site (write-only, used for checks and downloads,
replaced, removed, deleted with the site, moved over from the single token of 1.0.0), backup before an adoption, adoption
of an existing installation (with aaPanel's .user.ini in the document root), deployment, automatic rollback after a failed
health check, manual rollback, backups and restore of shared files, release pruning, unsafe archives, the health check of
a site that is not adopted yet, database settings from environment files, the init options, the address allowlist,
health checks through this server (127.0.0.1 with the site's host name), the restart after a job that installed new
runner code, Arsika Rollout Manager updating itself (preset rollout-manager) and the runner of a server (self-update).
Docker Compose sites (fake docker), installed from release source code archives: adoption of a compose installation
made by hand (its project name is kept), version-tagged images, rollback without a rebuild, a bind mount outside the
release, automatic rollback, database dumps inside the container (no password on the command line), volume backups and
restore. HTTPS runner: public key pin (same as openssl computes it), wrong pin, plain HTTP refused."""
import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer, SimpleHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arsika_rollout_manager import __main__ as cli  # noqa: E402
from arsika_rollout_manager.backup import Backups, settings_from_env  # noqa: E402
from arsika_rollout_manager.client import Client, PinError  # noqa: E402
from arsika_rollout_manager.compose import ComposeError  # noqa: E402
from arsika_rollout_manager.config import Config  # noqa: E402
from arsika_rollout_manager.deploy import Deployer, DeployError, merge_user_ini  # noqa: E402
from arsika_rollout_manager.server import Api, ThreadingServer, serve  # noqa: E402
from arsika_rollout_manager import system  # noqa: E402
from arsika_rollout_manager.store import Store  # noqa: E402
from arsika_rollout_manager.util import certificate_pin  # noqa: E402
from arsika_rollout_manager.worker import Worker  # noqa: E402

SHELL = os.environ.get("ROLLOUT_TEST_SHELL") or shutil.which("bash") or "/bin/bash"
OPENSSL = os.environ.get("ROLLOUT_TEST_OPENSSL") or shutil.which("openssl")
FAKE_DOCKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_docker.py")
SITE_TOKEN = "github_pat_" + "s" * 40                    # the demo site's own (fake) read-only token


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def stop(*servers):
    for server in servers:
        server.shutdown()
        server.server_close()


def make_zip(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in files.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (0o100755 if name.endswith(".sh") else 0o100644) << 16
            z.writestr(info, content)
    return buffer.getvalue()


def release_files(version, page=None):
    return {"public/index.html": "<h1>demo</h1>", "public/version.txt": page or ("version " + version),
            "app/main.php": "<?php // " + version, "data/.gitignore": "*\n", "deploy/run.sh": "#!/bin/sh\necho hi\n"}


SELF_UPDATE_SCRIPT = """#!/bin/bash
# test double of deploy/install.sh: supports --self-update
echo "$* $ROLLOUT_VERSION" >> "$ROLLOUT_SITE_DIR/self-update.log"
if [ "$1" = "--self-update=activate" ]; then : > "$ROLLOUT_RESTART_FILE"; fi
"""
AGENT_UPDATE_SCRIPT = """#!/bin/bash
# test double of deploy/install.sh of a new release, run by the runner's self-update
echo "$* python=${ROLLOUT_PYTHON:+yes}" >> "$(dirname "$ROLLOUT_RUNNER_CONFIG")/update.log"
: > "$ROLLOUT_RESTART_FILE"
"""


class HostCheck(BaseHTTPRequestHandler):
    """Answers like a web server with one virtual host: dash.example.com (anything else: 404)."""

    def log_message(self, *args):
        pass

    def do_GET(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        if self.path == "/elsewhere":
            body, status, extra = b"", 301, {"Location": "https://other.example.com/"}
        elif host == "dash.example.com":
            body, status, extra = b'{"version":"1.3.0"}', 200, {}
        else:
            body, status, extra = b"unknown host", 404, {}
        self.send_response(status)
        for key, value in extra.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def shop_files(version, page=None, extra_mount=None):
    """Source code of a Docker Compose application (compose file in JSON, which is also YAML)."""
    web = {"build": {"context": "."}, "volumes": ["./data:/app/data", "uploads:/app/uploads"]}
    if extra_mount:
        web["volumes"].append(extra_mount)
    compose = {"services": {"web": web, "db": {"image": "mariadb:11", "volumes": ["dbdata:/var/lib/mysql"]}},
               "volumes": {"uploads": {}, "dbdata": {}}}
    return {"compose.yaml": json.dumps(compose, indent=2), "Dockerfile": "FROM busybox\n",
            "public/version.txt": page or ("version " + version), "data/.keep": ""}


class FakeGitHub(object):
    """/repos/<owner>/<name>/releases, /releases/tags/<tag>, /zipball/<tag> (redirect to /codeload/...), /assets/<id>
    (redirect to /storage/<id>). Storage and codeload must be requested without the token."""

    def __init__(self):
        self.assets = {}
        self.archives = {}
        self.repos = {}
        self.token_seen_by_storage = False
        self.auth_seen = []                                # Authorization headers of the API requests
        github = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send(self, status, body, ctype="application/json", headers=None):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = self.path.split("?")[0]
                if path.startswith(("/repos/", "/assets/")):
                    github.auth_seen.append(self.headers.get("Authorization") or "")
                match = re.match(r"^/repos/([^/]+/[^/]+)/(releases$|releases/tags/(.+)|zipball/(.+))", path)
                releases = github.repos.get(match.group(1)) if match else None
                if releases is not None and match.group(2) == "releases":
                    return self.send(200, json.dumps(releases).encode())
                if releases is not None and match.group(3):
                    found = [r for r in releases if r["tag_name"] == match.group(3)]
                    return self.send(200, json.dumps(found[0]).encode()) if found else self.send(404, b'{"message":"Not Found"}')
                if releases is not None and match.group(4):
                    return self.send(302, b"", headers={"Location": "http://127.0.0.1:%d/codeload/%s/%s" % (
                        github.port, match.group(1), match.group(4))})
                if path.startswith("/codeload/"):
                    if self.headers.get("Authorization"):
                        github.token_seen_by_storage = True
                    data = github.archives.get(path[len("/codeload/"):])
                    return self.send(200, data, "application/zip") if data else self.send(404, b"not found")
                if path.startswith("/assets/"):
                    return self.send(302, b"", headers={"Location": "http://127.0.0.1:%d/storage/%s" % (github.port, path.rsplit("/", 1)[1])})
                if path.startswith("/storage/"):
                    if self.headers.get("Authorization"):
                        github.token_seen_by_storage = True
                    return self.send(200, github.assets[int(path.rsplit("/", 1)[1])], "application/zip")
                self.send(404, b'{"message":"Not Found"}')

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def publish(self, version, files, digest=True, repo="o/demo", asset=None):
        data = make_zip(files)
        asset_id = len(self.assets) + 1
        self.assets[asset_id] = data
        self.repos.setdefault(repo, []).insert(0, {
            "tag_name": "v" + version, "name": "v" + version, "draft": False, "prerelease": False, "body": "notes " + version,
            "published_at": "2026-10-31T00:00:00Z", "html_url": "",
            "zipball_url": "http://127.0.0.1:%d/repos/%s/zipball/v%s" % (self.port, repo, version),
            "assets": [{"id": asset_id, "name": asset or "demo_v%s.zip" % version, "size": len(data),
                        "url": "http://127.0.0.1:%d/assets/%d" % (self.port, asset_id),
                        "digest": ("sha256:" + hashlib.sha256(data).hexdigest()) if digest else None}]})

    def publish_source(self, repo, version, files):
        """A release without assets: only GitHub's source code archive (one top folder owner-name-<commit>/)."""
        top = "%s-%s/" % (repo.replace("/", "-"), hashlib.sha1(version.encode()).hexdigest()[:7])
        self.archives["%s/v%s" % (repo, version)] = make_zip({top + name: content for name, content in files.items()})
        self.repos.setdefault(repo, []).insert(0, {
            "tag_name": "v" + version, "name": "v" + version, "draft": False, "prerelease": False, "body": "notes " + version,
            "published_at": "2026-10-31T00:00:00Z", "html_url": "", "assets": [],
            "zipball_url": "http://127.0.0.1:%d/repos/%s/zipball/v%s" % (self.port, repo, version)})


class RunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="rollout-test-")
        cls.www = os.path.join(cls.tmp, "wwwroot")
        cls.site_dir = os.path.join(cls.www, "demo.example.com")
        os.makedirs(cls.site_dir)
        cls.github = FakeGitHub()
        for version in ("1.0.0", "1.1.0"):
            cls.github.publish(version, release_files(version))
        cls.github.publish("1.2.0", release_files("1.2.0", page="broken build"))          # fails its health check
        cls.github.publish("2.0.0", dict(release_files("2.0.0"), **{"deploy/install.sh": SELF_UPDATE_SCRIPT}), repo="o/self")
        cls.github.publish("2.1.0", dict(release_files("2.1.0"), **{"deploy/install.sh": "#!/bin/bash\necho old\n"}), repo="o/self")
        cls.github.publish("9.9.9", {"deploy/install.sh": AGENT_UPDATE_SCRIPT, "README.md": "fake release"}, repo="o/rollout",
                           asset="arsika-rollout-manager-v9.9.9.zip")

        # fake web server whose document root is the site's "public" link (follows current/ on every request)
        handler = partial(QuietHandler, directory=os.path.join(cls.site_dir, "public"))
        cls.web = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.web.serve_forever, daemon=True).start()

        config_path = os.path.join(cls.tmp, "etc", "runner.json")
        os.makedirs(os.path.dirname(config_path))
        cls.config = Config.create(config_path, state_dir=os.path.join(cls.tmp, "state"), backup_dir=os.path.join(cls.tmp, "backup"),
                                   github_api="http://127.0.0.1:%d" % cls.github.port,
                                   site_roots=[cls.www], shell=SHELL, port=0, health_attempts=3, health_delay=0.3,
                                   update_repo="o/rollout")
        cls.config.prepare_dirs()
        store = Store(cls.config.state_path("runner.db"))
        backups = Backups(cls.config, store)
        cls.deployer = Deployer(cls.config, store, backups)
        cls.worker = Worker(cls.config, store, cls.deployer)
        cls.restarts = []
        cls.worker.on_restart = lambda: cls.restarts.append(time.time())
        cls.worker.start()
        cls.api = Api(cls.config, store, cls.deployer, cls.worker)
        cls.server = serve(cls.api)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.client = Client("http://127.0.0.1:%d" % cls.server.server_address[1], cls.config["secret"])

    @classmethod
    def tearDownClass(cls):
        stop(cls.server, cls.web, cls.github.server)
        cls.worker.stopping = True
        cls.worker.wake.set()
        time.sleep(0.3)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ---------- helpers ----------
    def call(self, method, path, data=None, expect=200):
        status, doc = self.client.request(method, path, data)
        self.assertEqual(status, expect, json.dumps(doc)[:500])
        return doc.get("data", doc)

    def job(self, job_type, version=None, params=None, expect="succeeded", site="demo"):
        job = self.call("POST", "/v1/sites/%s/jobs" % site, {"type": job_type, "version": version, "params": params or {}, "actor": "test"})
        return self.wait_job(job, expect)

    def wait_job(self, job, expect="succeeded"):
        for _ in range(300):
            current = self.call("GET", "/v1/jobs/%d" % job["id"])
            if current["status"] in ("succeeded", "failed"):
                break
            time.sleep(0.1)
        self.assertEqual(current["status"], expect, current["message"] + "\n" + current["log"]["text"][-3000:])
        return current

    def page(self):
        with open(os.path.join(self.site_dir, "public", "version.txt"), encoding="utf-8") as f:
            return f.read()

    # ---------- tests (run in order) ----------
    def test_00_signature(self):
        bad = Client(self.client.url, "wrong-secret")
        status, doc = bad.get("/v1/status")
        self.assertEqual(status, 401)
        status, doc = self.client.get("/v1/status")
        self.assertEqual(status, 200)
        self.assertEqual(doc["data"]["github_tokens"], 0)

    def test_01_site_validation(self):
        status, doc = self.client.post("/v1/sites", {"id": "Bad Id", "path": "/etc", "repo": "nope", "github_token": "not a token!"})
        self.assertEqual(status, 422)
        self.assertTrue({"id", "path", "repo", "github_token"} <= set(doc["error"]["fields"]))

    def test_02_register_and_check_github(self):
        site = {"id": "demo", "name": "Demo", "domain": "demo.example.com", "path": self.site_dir, "repo": "o/demo",
                "asset_pattern": "demo_v*.zip", "web_user": "", "shared": [".env", "data/"], "docroot_link": "public",
                "docroot_target": "public", "build": "echo \"$ROLLOUT_VERSION\" > built.txt", "activate": "echo $ROLLOUT_VERSION >> \"$ROLLOUT_SHARED_DIR/activations.log\"",
                "reload_php_fpm": False, "health_url": "http://127.0.0.1:%d/version.txt" % self.web.server_address[1],
                "health_contains": "version {version}", "backup_paths": [".env", "data/"], "backup_keep": 3, "keep_releases": 2,
                "github_token": SITE_TOKEN}
        created = self.call("POST", "/v1/sites", site)
        self.assertEqual(created["state"], "empty")
        self.assertEqual(created["latest_version"], "1.2.0")
        self.assertEqual((created["github_token_set"], created["github_token_hint"]), (True, "github_pat_…ssss"))
        self.assertNotIn(SITE_TOKEN, json.dumps(created))                                      # write-only
        self.assertIn("Bearer " + SITE_TOKEN, self.github.auth_seen)                           # the site's own token
        self.github.auth_seen.clear()
        check = self.call("POST", "/v1/github/check", {"repo": "o/demo", "asset_pattern": "demo_v*.zip", "site": "demo"})
        self.assertTrue(check["ok"] and check["releases"] == 3)
        self.assertEqual(self.github.auth_seen, ["Bearer " + SITE_TOKEN])                      # saved token of the site
        typed = "github_pat_" + "t" * 40
        self.call("POST", "/v1/github/check", {"repo": "o/demo", "site": "demo", "github_token": typed})
        self.assertEqual(self.github.auth_seen[-1], "Bearer " + typed)                         # a token typed in the form wins
        self.assertFalse(self.call("POST", "/v1/github/check", {"repo": "o/demo", "github_token": "bad token"})["ok"])
        self.assertEqual(self.call("GET", "/v1/status")["github_tokens"], 1)
        status, doc = self.client.post("/v1/sites", site)
        self.assertEqual(status, 422)

    def test_03_adopt_existing_installation(self):
        # an installation that lives directly in the site folder (like a manual aaPanel upload)
        os.makedirs(os.path.join(self.site_dir, "public"))
        with open(os.path.join(self.site_dir, "public", "version.txt"), "w") as f:
            f.write("version 1.0.0")
        with open(os.path.join(self.site_dir, ".env"), "w") as f:
            f.write("APP_KEY=secret\n")
        os.makedirs(os.path.join(self.site_dir, "data"))
        with open(os.path.join(self.site_dir, "data", "user.txt"), "w") as f:
            f.write("user data")
        with open(os.path.join(self.site_dir, ".user.ini"), "w") as f:
            f.write("open_basedir=...")
        with open(os.path.join(self.site_dir, "public", ".user.ini"), "w") as f:          # aaPanel: Running directory /public
            f.write("open_basedir=/www/wwwroot/demo.example.com/:/tmp/\n")
        self.assertEqual(self.call("GET", "/v1/sites/demo")["state"], "classic")
        health = self.call("GET", "/v1/sites/demo/health")          # health_contains has {version}, unknown before adoption
        self.assertTrue(health["ok"], health)
        status, doc = self.client.post("/v1/sites/demo/jobs", {"type": "deploy", "version": "1.1.0"})
        self.assertEqual((status, doc["error"]["code"]), (409, "needs_adopt"))
        backup_id = self.job("backup", params={"reason": "before adoption"})["message"].split()[-1]   # data still in the site folder
        self.assertEqual(len(self.call("GET", "/v1/sites/demo/backups")), 1)
        status, doc = self.client.post("/v1/sites/demo/jobs", {"type": "restore", "params": {"backup_id": backup_id}})
        self.assertEqual((status, doc["error"]["code"]), (409, "needs_adopt"))
        job = self.job("adopt", "1.0.0")
        self.assertIn("adopted", job["message"])
        details = self.call("GET", "/v1/sites/demo")
        self.assertEqual((details["state"], details["current_version"]), ("ready", "1.0.0"))
        self.assertTrue(details["pre_rollout"])
        self.assertTrue(os.path.islink(os.path.join(self.site_dir, "public")))
        self.assertTrue(os.path.isfile(os.path.join(self.site_dir, ".user.ini")))              # kept in place
        with open(os.path.join(self.site_dir, "public", ".user.ini")) as f:                    # carried into the release
            self.assertEqual(f.read(), "open_basedir=/www/wwwroot/demo.example.com/:/tmp/\n")
        self.assertIn(".user.ini of the running version carried over", job["log"]["text"])
        with open(os.path.join(self.site_dir, "current", "data", "user.txt")) as f:
            self.assertEqual(f.read(), "user data")                                            # shared data linked into the release
        with open(os.path.join(self.site_dir, "current", "built.txt")) as f:
            self.assertEqual(f.read().strip(), "1.0.0")                                        # build hook ran in the release
        self.assertEqual(len(self.call("GET", "/v1/sites/demo/backups")), 2)                   # manual one + before adoption
        self.assertFalse(self.github.token_seen_by_storage)

    def test_04_deploy(self):
        self.github.auth_seen.clear()
        job = self.job("deploy", "1.1.0")
        self.assertTrue(self.github.auth_seen and set(self.github.auth_seen) == {"Bearer " + SITE_TOKEN})   # release + asset
        self.assertEqual((job["from_version"], job["to_version"]), ("1.0.0", "1.1.0"))
        self.assertEqual(self.page(), "version 1.1.0")
        self.assertIn("SHA-256 verified", job["log"]["text"])
        self.assertTrue(os.path.isfile(os.path.join(self.site_dir, "releases", "1.1.0", "public", ".user.ini")))
        details = self.call("GET", "/v1/sites/demo")
        self.assertEqual(details["rollback_version"], "1.0.0")
        self.assertEqual(details["latest_version"], "1.2.0")
        self.assertTrue(details["update_available"])
        self.assertTrue(details["health"]["ok"])
        with open(os.path.join(self.site_dir, "shared", "activations.log")) as f:
            self.assertEqual(f.read().split(), ["1.0.0", "1.1.0"])

    def test_05_failed_health_check_rolls_back(self):
        job = self.job("deploy", "1.2.0", expect="failed")
        self.assertIn("rolled back to 1.1.0", job["message"])
        self.assertEqual(self.page(), "version 1.1.0")
        self.assertEqual(self.call("GET", "/v1/sites/demo")["current_version"], "1.1.0")

    def test_06_manual_rollback(self):
        job = self.job("rollback")
        self.assertEqual((job["from_version"], job["to_version"]), ("1.1.0", "1.0.0"))
        self.assertEqual(self.page(), "version 1.0.0")
        job = self.job("rollback")                                                # back to the most recent other release
        self.assertEqual(job["to_version"], "1.1.0")
        releases = self.call("GET", "/v1/sites/demo/releases")
        flags = {r["version"]: (r["installed"], r["current"]) for r in releases["releases"]}
        self.assertEqual(flags["1.1.0"], (True, True))

    def test_07_backup_and_restore(self):
        user_file = os.path.join(self.site_dir, "shared", "data", "user.txt")
        backup = self.job("backup", params={"reason": "test"})
        backup_id = backup["message"].split()[-1]
        with open(user_file, "w") as f:
            f.write("changed after the backup")
        self.job("restore", params={"backup_id": backup_id, "safety_backup": True})
        with open(user_file) as f:
            self.assertEqual(f.read(), "user data")
        backups = self.call("GET", "/v1/sites/demo/backups")
        self.assertLessEqual(len(backups), 3)                                     # backup_keep
        self.call("DELETE", "/v1/sites/demo/backups/" + backups[-1]["id"])
        self.assertEqual(len(self.call("GET", "/v1/sites/demo/backups")), len(backups) - 1)

    def test_08_prune_and_delete(self):
        installed = self.call("GET", "/v1/sites/demo")["installed"]
        self.assertLessEqual(len(installed), 2)                                  # keep_releases = 2 (current + rollback target)
        job = self.job("delete-release", "1.1.0", expect="failed")
        self.assertIn("current release", job["message"])
        self.job("cleanup-adopt")
        self.assertFalse(self.call("GET", "/v1/sites/demo")["pre_rollout"])

    def test_09_busy_and_unknown(self):
        status, doc = self.client.post("/v1/sites/nope/jobs", {"type": "deploy", "version": "1.0.0"})
        self.assertEqual(status, 404)
        status, doc = self.client.post("/v1/sites/demo/jobs", {"type": "explode"})
        self.assertEqual(status, 422)

    def test_10_unsafe_archive(self):
        path = os.path.join(self.tmp, "evil.zip")
        with open(path, "wb") as f:
            f.write(make_zip({"../escape.txt": "x"}))
        target = os.path.join(self.tmp, "extract")
        os.makedirs(target)
        with self.assertRaises(DeployError):
            Deployer.extract(path, target)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "escape.txt")))

    def test_11_user_ini_merge(self):
        live = "open_basedir=/www/wwwroot/site/:/tmp/\n"
        self.assertEqual(merge_user_ini("upload_max_filesize=20M\n", live),
                         "upload_max_filesize=20M\nopen_basedir=/www/wwwroot/site/:/tmp/\n")      # the release keeps its settings
        self.assertEqual(merge_user_ini("open_basedir=/\nmemory_limit=256M\n", live),
                         "memory_limit=256M\nopen_basedir=/www/wwwroot/site/:/tmp/\n")            # the panel's limit wins
        self.assertEqual(merge_user_ini("memory_limit=256M\n", "; no limit here\n"), "memory_limit=256M\n")

    def test_12_site_token_replace_and_remove(self):
        new = "github_pat_" + "n" * 40
        details = self.call("PUT", "/v1/sites/demo", {"github_token": new})
        self.assertEqual(details["github_token_hint"], "github_pat_…nnnn")
        self.assertEqual(self.config.site_token("demo"), new)
        self.assertTrue(self.call("PUT", "/v1/sites/demo", {"name": "Demo site"})["github_token_set"])   # other changes keep it
        status, doc = self.client.put("/v1/sites/demo", {"github_token": "short"})
        self.assertEqual((status, list(doc["error"]["fields"])), (422, ["github_token"]))
        self.assertFalse(self.call("PUT", "/v1/sites/demo", {"remove_github_token": True})["github_token_set"])
        self.assertEqual(self.config.site_token("demo"), "")
        with open(self.config.path, encoding="utf-8") as f:
            self.assertNotIn(new, f.read())

    def test_13_legacy_token_moves_to_sites(self):
        folder = os.path.join(self.tmp, "legacy")
        os.makedirs(folder)
        legacy, own = "ghp_" + "l" * 36, "github_pat_" + "o" * 40
        config = Config.create(os.path.join(folder, "runner.json"), state_dir=folder, github_token=legacy)   # as 1.0.0 saved it
        store = Store(os.path.join(folder, "runner.db"))
        store.save_site({"id": "old", "repo": "o/old"})
        store.save_site({"id": "own", "repo": "o/own"})
        config.set_site_token("own", own)
        Api(config, store, None, None)
        saved = Config(config.path)
        self.assertNotIn("github_token", saved.values)
        self.assertEqual((saved.site_token("old"), saved.site_token("own")), (legacy, own))   # a site's own token is kept

    def test_14_deleting_a_site_deletes_its_token(self):
        site_dir = os.path.join(self.www, "second.example.com")
        os.makedirs(site_dir)
        self.call("POST", "/v1/sites", {"id": "second", "domain": "second.example.com", "path": site_dir, "repo": "o/demo",
                                        "asset_pattern": "demo_v*.zip", "github_token": "github_pat_" + "d" * 40})
        self.assertTrue(self.config.site_token("second"))
        self.call("DELETE", "/v1/sites/second")
        self.assertEqual(self.config.site_token("second"), "")

    def test_15_allow_from(self):
        server = ThreadingServer.__new__(ThreadingServer)            # verify_request only, no socket
        server.allowed = tuple(Config.networks("10.0.0.0/8, fd00::/8"))
        self.assertTrue(server.verify_request(None, ("10.1.2.3", 5000)))
        self.assertTrue(server.verify_request(None, ("::ffff:10.1.2.3", 5000, 0, 0)))      # IPv4 client of a dual-stack socket
        self.assertTrue(server.verify_request(None, ("127.0.0.1", 5000)))                  # the local command line
        self.assertTrue(server.verify_request(None, ("fd12::5", 5000, 0, 0)))
        self.assertFalse(server.verify_request(None, ("192.168.1.5", 5000)))
        self.assertFalse(server.verify_request(None, ("fe80::1%eth0", 5000, 0, 2)))
        with self.assertRaises(ValueError):
            Config.networks(["300.1.1.1"])

    def test_16_database_settings(self):
        laravel = settings_from_env({"DB_CONNECTION": "mysql", "DB_HOST": "127.0.0.1", "DB_DATABASE": "app", "DB_USERNAME": "u",
                                     "DB_PASSWORD": "p"})
        self.assertEqual((laravel["engine"], laravel["port"], laravel["name"]), ("mysql", "3306", "app"))
        self.assertEqual(settings_from_env({"DB_DATABASE": "app"})["host"], "localhost")          # 1.x default: MySQL socket
        pg = settings_from_env({"DB_CONNECTION": "pgsql", "DB_DATABASE": "app", "DB_USERNAME": "u"})
        self.assertEqual((pg["engine"], pg["host"], pg["port"]), ("postgres", "127.0.0.1", "5432"))
        url = settings_from_env({"DATABASE_URL": "postgresql://shop:p%40ss@db.internal:5433/shop?sslmode=disable"})
        self.assertEqual((url["engine"], url["host"], url["port"], url["name"], url["user"], url["password"]),
                         ("postgres", "db.internal", "5433", "shop", "shop", "p@ss"))
        compose_env = settings_from_env({"POSTGRES_DB": "shop", "POSTGRES_USER": "shop", "POSTGRES_PASSWORD": "x"})
        self.assertEqual((compose_env["engine"], compose_env["user"]), ("postgres", "shop"))
        self.assertEqual(settings_from_env({"MYSQL_DATABASE": "shop", "MYSQL_USER": "shop"})["engine"], "mysql")
        self.assertIsNone(settings_from_env({"DB_CONNECTION": "sqlite", "DB_DATABASE": "/data/app.sqlite"})["engine"])
        self.assertIsNone(settings_from_env({"APP_KEY": "x"}))

    def test_17_init_options(self):
        folder = os.path.join(self.tmp, "init")
        path = os.path.join(folder, "runner.json")
        common = ["init", "--config=" + path, "--state-dir=" + os.path.join(folder, "state"), "--backup-dir=" + os.path.join(folder, "backup")]
        self.assertEqual(cli.main(common + ["--listen=0.0.0.0"]), 2)                    # network address without TLS
        self.assertEqual(cli.main(common + ["--web-user=www-data", "--platform=linux", "--site-root=/var/www,/srv",
                                            "--allow-from=10.0.0.0/8,192.168.1.10"]), 0)
        first = Config(path)
        self.assertEqual((first["web_user"], first["platform"], first["site_roots"], first["allow_from"]),
                         ("www-data", "linux", ["/var/www", "/srv"], ["10.0.0.0/8", "192.168.1.10/32"]))
        self.assertEqual(cli.main(common + ["--port=9000"]), 0)                         # an existing file: only the option changes
        second = Config(path)
        self.assertEqual((second["port"], second["secret"], second["web_user"]), (9000, first["secret"], "www-data"))

    def test_19_health_check_through_this_server(self):
        web = HTTPServer(("127.0.0.1", 0), HostCheck)
        threading.Thread(target=web.serve_forever, daemon=True).start()
        try:
            url = "http://dash.example.com:%d/api/v1/app" % web.server_address[1]
            result = system.quick_health(url, '"version":"1.3.0"', local=True)        # asked on 127.0.0.1 with Host
            self.assertTrue(result["ok"], result)
            self.assertFalse(system.quick_health(url, "", timeout=3)["ok"])            # the name itself does not resolve
            other = system.quick_health("http://dash.example.com:%d/elsewhere" % web.server_address[1], "", local=True)
            self.assertEqual((other["ok"], other["status"]), (False, 301))              # another host is not followed
        finally:
            stop(web)
        details = self.call("PUT", "/v1/sites/demo", {"health_local": True})
        self.assertTrue(details["settings"]["health_local"])
        self.assertTrue(self.call("GET", "/v1/sites/demo/health")["ok"])               # 127.0.0.1 is its own host
        self.call("PUT", "/v1/sites/demo", {"health_local": False})

    def test_20_restart_after_a_job_that_asks_for_it(self):
        before = len(self.restarts)
        old = self.call("GET", "/v1/sites/demo")["settings"]["activate"]
        self.call("PUT", "/v1/sites/demo", {"activate": old + ' && : > "$ROLLOUT_RESTART_FILE"'})
        try:
            self.job("rollback")
            time.sleep(0.3)
            self.assertEqual(len(self.restarts), before + 1)
            self.assertFalse(os.path.exists(self.config.state_path("restart-requested")))
            self.job("rollback")                                     # back to the newest release; also restarts once
            time.sleep(0.3)
            self.assertEqual(len(self.restarts), before + 2)
        finally:
            self.call("PUT", "/v1/sites/demo", {"activate": old})
        self.job("backup")                                            # a job that asks for nothing: no restart
        time.sleep(0.3)
        self.assertEqual(len(self.restarts), before + 2)

    def test_21_arsika_rollout_manager_updates_itself(self):
        site_dir = os.path.join(self.www, "rollout.example.com")
        os.makedirs(site_dir)
        created = self.call("POST", "/v1/sites", {"preset": "rollout-manager", "id": "rollout", "domain": "rollout.example.com",
                                                  "path": site_dir, "repo": "o/self", "asset_pattern": "demo_v*.zip", "shared": [],
                                                  "web_user": "", "backup_env_file": "", "backup_paths": [], "health_url": "",
                                                  "reload_php_fpm": False})
        self.assertEqual((created["settings"]["build"], created["settings"]["health_local"]), ("preset:rollout-manager", True))
        before = len(self.restarts)
        self.job("deploy", "2.0.0", site="rollout")
        time.sleep(0.3)
        with open(os.path.join(site_dir, "self-update.log"), encoding="utf-8") as f:
            self.assertEqual(f.read().split("\n")[:2], ["--self-update=build 2.0.0", "--self-update=activate 2.0.0"])
        self.assertEqual(len(self.restarts), before + 1)                               # new runner code: restart after the job
        job = self.job("deploy", "2.1.0", site="rollout", expect="failed")             # a release without --self-update
        self.assertIn("cannot update itself", job["log"]["text"])
        self.assertEqual(self.call("GET", "/v1/sites/rollout")["current_version"], "2.0.0")
        self.call("DELETE", "/v1/sites/rollout")

    def test_22_update_the_runner_of_this_server(self):
        before = len(self.restarts)
        status, doc = self.client.post("/v1/runner/update", {"version": "not a version!"})
        self.assertEqual(status, 422)
        job = self.wait_job(self.call("POST", "/v1/runner/update", {"version": "9.9.9", "actor": "test"}))
        self.assertEqual((job["site_id"], job["type"], job["to_version"]), ("_runner", "self-update", "9.9.9"))
        self.assertIn("SHA-256 verified", job["log"]["text"])
        time.sleep(0.3)
        with open(os.path.join(os.path.dirname(self.config.path), "update.log"), encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), "--agent --self-update python=yes")
        self.assertEqual(len(self.restarts), before + 1)
        self.assertFalse([n for n in os.listdir(self.config.state_path()) if n.startswith("update-")])   # cleaned up

    def test_18_presets_use_the_server_web_user(self):
        presets = {p["id"]: p["settings"] for p in self.call("GET", "/v1/presets")}
        self.assertEqual(presets["php-app"]["web_user"], self.config["web_user"])
        self.assertEqual((presets["compose-build"]["web_user"], presets["compose-build"]["deploy_mode"]), ("", "compose"))
        self.assertFalse(presets["compose-images"]["compose_build"])
        status, doc = self.client.post("/v1/sites", {"id": "bad", "path": os.path.join(self.www, "bad"), "repo": "o/demo",
                                                     "deploy_mode": "kubernetes", "backup_db": "oracle", "compose_project": "Bad Name"})
        self.assertEqual(status, 422)
        self.assertTrue({"deploy_mode", "backup_db", "compose_project"} <= set(doc["error"]["fields"]))


class ComposeTest(unittest.TestCase):
    """A Docker Compose site run by the fake docker command, installed from release source code archives."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="rollout-compose-")
        cls.www = os.path.join(cls.tmp, "srv")
        cls.site_dir = os.path.join(cls.www, "shop.example.com")
        os.makedirs(cls.site_dir)
        cls.webroot = os.path.join(cls.tmp, "container-web")       # what the fake "web" container serves
        os.makedirs(cls.webroot)
        cls.state = os.path.join(cls.tmp, "docker", "state.json")
        os.makedirs(os.path.join(cls.tmp, "docker", "volumes"))
        os.environ["FAKE_DOCKER_STATE"], os.environ["FAKE_DOCKER_WEBROOT"] = cls.state, cls.webroot
        cls.github = FakeGitHub()
        for version in ("1.0.0", "1.1.0"):
            cls.github.publish_source("o/shop", version, shop_files(version))
        cls.github.publish_source("o/shop", "1.2.0", shop_files("1.2.0", extra_mount="./cache:/cache"))   # not in the release
        cls.github.publish_source("o/shop", "1.3.0", shop_files("1.3.0", page="broken build"))          # fails its health check

        handler = partial(QuietHandler, directory=cls.webroot)
        cls.web = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.web.serve_forever, daemon=True).start()

        config_path = os.path.join(cls.tmp, "etc", "runner.json")
        os.makedirs(os.path.dirname(config_path))
        cls.config = Config.create(config_path, state_dir=os.path.join(cls.tmp, "state"), backup_dir=os.path.join(cls.tmp, "backup"),
                                   github_api="http://127.0.0.1:%d" % cls.github.port, site_roots=[cls.www], shell=SHELL, port=0,
                                   health_attempts=3, health_delay=0.3, docker_command=[sys.executable, FAKE_DOCKER])
        cls.config.prepare_dirs()
        store = Store(cls.config.state_path("runner.db"))
        cls.deployer = Deployer(cls.config, store, Backups(cls.config, store))
        cls.worker = Worker(cls.config, store, cls.deployer)
        cls.worker.start()
        cls.server = serve(Api(cls.config, store, cls.deployer, cls.worker))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.client = Client("http://127.0.0.1:%d" % cls.server.server_address[1], cls.config["secret"])

    @classmethod
    def tearDownClass(cls):
        stop(cls.server, cls.web, cls.github.server)
        cls.worker.stopping = True
        cls.worker.wake.set()
        time.sleep(0.3)
        for key in ("FAKE_DOCKER_STATE", "FAKE_DOCKER_WEBROOT"):
            os.environ.pop(key, None)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    call = RunnerTest.call

    def job(self, job_type, version=None, params=None, expect="succeeded"):
        job = self.call("POST", "/v1/sites/shop/jobs", {"type": job_type, "version": version, "params": params or {}, "actor": "test"})
        for _ in range(300):
            current = self.call("GET", "/v1/jobs/%d" % job["id"])
            if current["status"] in ("succeeded", "failed"):
                break
            time.sleep(0.1)
        self.assertEqual(current["status"], expect, current["message"] + "\n" + current["log"]["text"][-3000:])
        return current

    def docker(self):
        with open(self.state, encoding="utf-8") as f:
            return json.load(f)

    def set_docker(self, **values):
        state = self.docker() if os.path.exists(self.state) else {}
        state.update(values)
        with open(self.state, "w", encoding="utf-8") as f:
            json.dump(state, f)

    def last_up(self):
        return [c for c in self.docker()["compose"] if c["command"] == "up"][-1]

    def page(self):
        with open(os.path.join(self.webroot, "version.txt"), encoding="utf-8") as f:
            return f.read()

    def volume_file(self, *parts):
        return os.path.join(self.tmp, "docker", "volumes", *parts)

    # ---------- tests (run in order) ----------
    def test_00_register(self):
        self.assertEqual(self.call("GET", "/v1/status")["docker_compose"], "2.29.1")
        check = self.call("POST", "/v1/github/check", {"repo": "o/shop", "source": "source"})
        self.assertEqual((check["ok"], check["with_asset"], check["latest"]), (True, 4, "1.3.0"))   # every release has source code
        created = self.call("POST", "/v1/sites", {
            "preset": "compose-build", "id": "shop", "domain": "shop.example.com", "path": self.site_dir, "repo": "o/shop",
            "shared": [".env", "data/"], "backup_env_file": ".env", "backup_db_service": "db", "backup_volumes": ["uploads"],
            "health_url": "http://127.0.0.1:%d/version.txt" % self.web.server_address[1], "health_contains": "version {version}",
            "build": 'echo "$COMPOSE_PROJECT_NAME|$COMPOSE_FILE" > hook.txt', "keep_releases": 2})
        settings = created["settings"]
        self.assertEqual((settings["deploy_mode"], settings["source"], settings["web_user"], settings["docroot_link"]),
                         ("compose", "source", "", ""))
        self.assertEqual((created["deploy_mode"], created["latest_version"]), ("compose", "1.3.0"))

    def test_01_adopt_compose_installation(self):
        # a compose installation made by hand, running as project "shop" (not the folder name "shopexamplecom")
        files = shop_files("1.0.0")
        for name in ("compose.yaml", "public/version.txt", "data/.keep"):
            os.makedirs(os.path.dirname(os.path.join(self.site_dir, name)), exist_ok=True)
            with open(os.path.join(self.site_dir, name), "w", encoding="utf-8") as f:
                f.write(files[name])
        with open(os.path.join(self.site_dir, ".env"), "w", encoding="utf-8") as f:
            f.write("COMPOSE_PROJECT_NAME=shop\nDB_DATABASE=shopdb\nMYSQL_PASSWORD=supersecret\n")
        with open(os.path.join(self.site_dir, "data", "user.txt"), "w", encoding="utf-8") as f:
            f.write("user data")
        for volume in ("shop_uploads", "shop_dbdata"):
            os.makedirs(self.volume_file(volume))
        with open(self.volume_file("shop_uploads", "photo.jpg"), "w", encoding="utf-8") as f:
            f.write("photo")
        self.set_docker(db="INSERT INTO t VALUES (1);\n", volume_users={"shop_uploads": ["shop-web"], "shop_dbdata": ["shop-db"]})
        with open(os.path.join(self.webroot, "version.txt"), "w", encoding="utf-8") as f:
            f.write("version 1.0.0")

        job = self.job("adopt", "1.0.0")
        details = self.call("GET", "/v1/sites/shop")
        self.assertEqual((details["state"], details["current_version"], details["settings"]["compose_project"]), ("ready", "1.0.0", "shop"))
        up = self.last_up()
        self.assertEqual((up["project"], os.path.normcase(up["directory"])),
                         ("shop", os.path.normcase(os.path.join(self.site_dir, "releases", "1.0.0"))))
        self.assertIn(".rollout-compose.yml", up["files"])
        docker = self.docker()
        self.assertEqual(docker["built"][-1]["images"], {"web": "shop-web:1.0.0"})          # tagged with the version
        self.assertEqual(docker["pulled"][-1]["services"], ["db"])
        self.assertEqual(docker["containers"]["shop"]["web"], "shop-web:1.0.0")
        dump = [e for e in docker["exec"] if '"$D"' in e["argv"][-1]][-1]                   # the dump itself
        self.assertEqual((dump["service"], dump["env"]), ("db", {"ROLLOUT_DB": "shopdb"}))
        self.assertFalse(any("supersecret" in " ".join(e["argv"]) for e in docker["exec"]))  # credentials stay in the container
        backup = self.call("GET", "/v1/sites/shop/backups")[0]
        self.assertEqual((backup["has_database"], backup["volumes"]), (1, 1))
        manifest_path = os.path.join(self.config["backup_dir"], "shop", backup["id"], "manifest.json")
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual((manifest["database"]["engine"], manifest["database"]["where"]), ("mysql", "container"))
        self.assertEqual(manifest["volumes"][0]["volume"], "shop_uploads")
        with open(os.path.join(self.site_dir, "current", "data", "user.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "user data")                                          # bind mount data moved to shared/
        with open(os.path.join(self.site_dir, "current", "hook.txt"), encoding="utf-8") as f:
            hook = f.read()
        self.assertTrue(hook.startswith("shop|") and ".rollout-compose.yml" in hook, hook)   # hooks can run docker compose
        self.assertFalse(self.github.token_seen_by_storage)
        self.assertIn("Download the source code of v1.0.0", job["log"]["text"])
        self.assertTrue(details["health"]["ok"])

    def test_02_deploy_and_rollback_without_rebuild(self):
        self.job("deploy", "1.1.0")
        self.assertEqual(self.page(), "version 1.1.0")
        self.assertEqual(self.docker()["containers"]["shop"]["web"], "shop-web:1.1.0")
        builds = len(self.docker()["built"])
        job = self.job("rollback")
        self.assertEqual((job["from_version"], job["to_version"]), ("1.1.0", "1.0.0"))
        self.assertEqual(self.docker()["containers"]["shop"]["web"], "shop-web:1.0.0")       # the image of 1.0.0 again
        self.assertEqual(len(self.docker()["built"]), builds)                                # no rebuild
        self.assertEqual(self.page(), "version 1.0.0")
        self.job("rollback")
        self.assertEqual(self.page(), "version 1.1.0")

    def test_03_mount_outside_the_release_stops_before_the_switch(self):
        ups = len([c for c in self.docker()["compose"] if c["command"] == "up"])
        job = self.job("deploy", "1.2.0", expect="failed")
        self.assertIn("add cache/ to Shared files", job["message"])
        self.assertEqual(len([c for c in self.docker()["compose"] if c["command"] == "up"]), ups)
        self.assertEqual(self.call("GET", "/v1/sites/shop")["current_version"], "1.1.0")

    def test_04_failed_health_check_starts_the_previous_containers(self):
        job = self.job("deploy", "1.3.0", expect="failed")
        self.assertIn("rolled back to 1.1.0", job["message"])
        self.assertEqual(self.docker()["containers"]["shop"]["web"], "shop-web:1.1.0")
        self.assertEqual(self.page(), "version 1.1.0")
        self.assertIn("shop-web:1.3.0", self.docker().get("removed", []))                 # pruned with its release

    def test_05_backup_and_restore_volume_and_database(self):
        backup_id = self.job("backup")["message"].split()[-1]
        with open(self.volume_file("shop_uploads", "photo.jpg"), "w", encoding="utf-8") as f:
            f.write("changed")
        with open(self.volume_file("shop_uploads", "new.jpg"), "w", encoding="utf-8") as f:
            f.write("added after the backup")
        self.job("restore", params={"backup_id": backup_id})
        with open(self.volume_file("shop_uploads", "photo.jpg"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "photo")
        self.assertFalse(os.path.exists(self.volume_file("shop_uploads", "new.jpg")))
        docker = self.docker()
        self.assertEqual((docker["stopped"][-1], docker["started"][-1]), (["shop-web"], ["shop-web"]))
        self.assertIn("-- fake mysql dump of shopdb", docker["db_restored"])
        self.assertIn("INSERT INTO t VALUES (1);", docker["db_restored"])

    def test_06_delete_release_removes_its_image(self):
        self.job("delete-release", "1.0.0")
        self.assertIn("shop-web:1.0.0", self.docker()["removed"])

    def test_07_project_name_is_fixed_once_installed(self):
        status, doc = self.client.put("/v1/sites/shop", {"compose_project": "other"})
        self.assertEqual((status, list(doc["error"]["fields"])), (422, ["compose_project"]))

    def test_08_missing_docker_compose(self):
        self.set_docker(no_compose=True)
        self.deployer.compose._version = (0.0, None)
        try:
            with self.assertRaises(ComposeError):
                self.deployer.compose.require()
        finally:
            self.set_docker(no_compose=False)
            self.deployer.compose._version = (0.0, None)


@unittest.skipUnless(OPENSSL, "openssl is needed to create the test certificate")
class TlsTest(unittest.TestCase):
    """The runner on HTTPS with a self-signed certificate, as install.sh --agent sets it up."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="rollout-tls-")
        cls.cert, key = os.path.join(cls.tmp, "cert.pem"), os.path.join(cls.tmp, "key.pem")
        subprocess.run([OPENSSL, "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-days", "2",
                        "-subj", "/CN=rollout-test", "-keyout", key, "-out", cls.cert], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        config = Config.create(os.path.join(cls.tmp, "runner.json"), state_dir=os.path.join(cls.tmp, "state"),
                               backup_dir=os.path.join(cls.tmp, "backup"), port=0, tls_cert=cls.cert, tls_key=key,
                               allow_from=["10.9.0.0/16"])
        config = Config(config.path)
        config.prepare_dirs()
        store = Store(config.state_path("runner.db"))
        deployer = Deployer(config, store, Backups(config, store))
        cls.server = serve(Api(config, store, deployer, Worker(config, store, deployer)))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = "https://127.0.0.1:%d" % cls.server.server_address[1]
        cls.secret = config["secret"]
        cls.pin = certificate_pin(cls.cert)

    @classmethod
    def tearDownClass(cls):
        stop(cls.server)
        time.sleep(0.2)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_pin_is_the_one_openssl_computes(self):
        pubkey = subprocess.run([OPENSSL, "x509", "-in", self.cert, "-pubkey", "-noout"], stdout=subprocess.PIPE, check=True).stdout
        der = subprocess.run([OPENSSL, "pkey", "-pubin", "-outform", "der"], input=pubkey, stdout=subprocess.PIPE, check=True).stdout
        self.assertEqual(self.pin, "sha256//" + base64.b64encode(hashlib.sha256(der).digest()).decode())

    def test_signed_request_over_https(self):
        status, doc = Client(self.url, self.secret, pin=self.pin).get("/v1/status")
        self.assertEqual(status, 200)
        self.assertEqual((doc["data"]["tls"], doc["data"]["allow_from"]), (True, ["10.9.0.0/16"]))   # loopback is always allowed

    def test_wrong_pin_is_refused(self):
        with self.assertRaises(PinError):
            Client(self.url, self.secret, pin="sha256//" + "A" * 43 + "=").get("/v1/status")

    def test_plain_http_gets_no_answer(self):
        with self.assertRaises(Exception):
            Client(self.url.replace("https:", "http:"), self.secret, timeout=10).get("/v1/status")
        self.assertEqual(Client(self.url, self.secret, pin=self.pin).get("/v1/status")[0], 200)     # the server still serves

    def test_network_address_needs_tls(self):
        path = os.path.join(self.tmp, "plain.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"listen": "0.0.0.0", "secret": "x"}, f)
        with self.assertRaises(ValueError):
            Config(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
