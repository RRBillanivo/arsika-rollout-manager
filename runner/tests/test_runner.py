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
restore. HTTPS runner: public key pin (same as openssl computes it), wrong pin, plain HTTP refused.
Hosting without a terminal (PHP in PATH or ROLLOUT_TEST_PHP, ini ROLLOUT_TEST_PHP_INI; needs zip and pdo_sqlite): the
generated agent file served by PHP's built-in server, connecting, adoption of files made by hand, database changes run
or marked as done by hand, automatic rollback, rollback, backups kept on the runner and restore, a published subfolder,
signatures, replays and unsafe archives refused.
Uploaded versions (no GitHub): a zip in pieces with junk that is removed, a tar.gz, refused uploads, pruning, archives
unpacked by bsdtar (when there is one). Laravel on a hosting (fake Laravel application that records what the agent asks):
the .env prepared in the dashboard and sent when the agent connects, only public/ in the website folder, APP_KEY,
php artisan migrate inside PHP, the plan of migrations, artisan jobs, rollback, editing the .env, composer on the runner
when vendor/ is missing, the PHP version check, the agent replacing itself, adopting a Laravel website, the hidden data
folder."""
import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer, SimpleHTTPRequestHandler
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arsika_rollout_manager import __main__ as cli  # noqa: E402
from arsika_rollout_manager.backup import Backups, settings_from_env  # noqa: E402
from arsika_rollout_manager.client import Client, PinError  # noqa: E402
from arsika_rollout_manager.compose import ComposeError  # noqa: E402
from arsika_rollout_manager.config import Config  # noqa: E402
from arsika_rollout_manager.deploy import Deployer, DeployError, merge_user_ini  # noqa: E402
from arsika_rollout_manager import hosting as hosting_module  # noqa: E402
from arsika_rollout_manager.hosting import AgentClient, AgentError, artisan_command, pair_secret, valid_agent_url  # noqa: E402
from arsika_rollout_manager.server import Api, ThreadingServer, serve  # noqa: E402
from arsika_rollout_manager import system  # noqa: E402
from arsika_rollout_manager.store import Store  # noqa: E402
from arsika_rollout_manager.uploads import (UploadError, excluded, php_satisfies, suggest_version,  # noqa: E402
                                            unpack_with_tool)
from arsika_rollout_manager.util import certificate_pin  # noqa: E402
from arsika_rollout_manager.worker import Worker  # noqa: E402

SHELL = os.environ.get("ROLLOUT_TEST_SHELL") or shutil.which("bash") or "/bin/bash"
OPENSSL = os.environ.get("ROLLOUT_TEST_OPENSSL") or shutil.which("openssl")
PHP = os.environ.get("ROLLOUT_TEST_PHP") or shutil.which("php")
PHP_INI = os.environ.get("ROLLOUT_TEST_PHP_INI") or ""
FAKE_DOCKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_docker.py")
FAKE_COMPOSER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_composer.py")
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


def free_port():
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# the functions aaPanel disables in PHP by default (the agent and Laravel must work without them)
AAPANEL_PHP = ["-d", "disable_functions=passthru,exec,system,putenv,chroot,chgrp,chown,shell_exec,popen,proc_open,pcntl_exec,"
                     "ini_alter,ini_restore,dl,openlog,syslog,readlink,symlink,popepassthru,imap_open,apache_setenv"]


def php_server(folder, extra=()):
    port = free_port()
    proc = subprocess.Popen([PHP] + (["-c", PHP_INI] if PHP_INI else []) + list(extra) + ["-S", "127.0.0.1:%d" % port, "-t", folder],
                            cwd=folder, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(80):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=1).close()
            break
        except urllib.error.HTTPError as e:
            e.close()
            break
        except Exception:
            time.sleep(0.1)
    return proc, port


def hosting_files(version, page=None, migration=None, extra=None):
    files = {"index.html": "<h1>%s</h1>" % version, "version.txt": page or ("version " + version), "uploads/.keep": "",
             "database/migrations/001_create_notes.sql": "-- notes of the site\nCREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT);\n"
                                                         "INSERT INTO notes (body) VALUES ('first; with a semicolon');\n"}
    if migration:
        files["database/migrations/002_add_created.sql"] = migration
    files.update(extra or {})
    return files


@unittest.skipUnless(PHP, "PHP is needed to run the hosting agent")
class HostingTest(unittest.TestCase):
    """A website on a "hosting": a folder served by PHP's built-in server that only the agent file changes."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="rollout-hosting-")
        cls.home = os.path.join(cls.tmp, "home")
        cls.docroot = os.path.join(cls.home, "public_html")
        cls.db = os.path.join(cls.tmp, "site.sqlite").replace("\\", "/")
        os.makedirs(os.path.join(cls.docroot, "uploads"))
        os.makedirs(os.path.join(cls.docroot, ".well-known"))
        for name, text in (("index.html", "old site made by hand"), ("version.txt", "old"), ("uploads/photo.txt", "photo"),
                           (".htaccess", "# my own rules\n"), (".well-known/acme.txt", "acme"),
                           (".env", "DB_CONNECTION=sqlite\nDB_DATABASE=%s\n" % cls.db)):
            with open(os.path.join(cls.docroot, name), "w", encoding="utf-8") as f:
                f.write(text)
        cls.github = FakeGitHub()
        cls.github.publish("1.0.0", hosting_files("1.0.0"), repo="o/host")
        cls.github.publish("1.1.0", hosting_files("1.1.0", migration="ALTER TABLE notes ADD COLUMN created TEXT;\n"), repo="o/host")
        cls.github.publish("1.2.0", hosting_files("1.2.0", page="broken build"), repo="o/host")
        cls.github.publish("1.3.0", hosting_files("1.3.0", extra={"../evil.txt": "x"}), repo="o/host")
        cls.github.publish_source("o/spa", "2.0.0", {"src/app.js": "source", "dist/index.html": "spa 2.0.0", "dist/version.txt": "version 2.0.0"})
        cls.github.publish_source("o/spa", "2.1.0", {"src/app.js": "source", "dist/index.html": "spa 2.1.0", "dist/version.txt": "version 2.1.0"})
        cls.php, cls.port = php_server(cls.docroot)
        config_path = os.path.join(cls.tmp, "etc", "runner.json")
        os.makedirs(os.path.dirname(config_path))
        cls.config = Config.create(config_path, state_dir=os.path.join(cls.tmp, "state"), backup_dir=os.path.join(cls.tmp, "backup"),
                                   github_api="http://127.0.0.1:%d" % cls.github.port, site_roots=[cls.tmp], shell=SHELL, port=0,
                                   health_attempts=2, health_delay=0.3)
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
        stop(cls.server, cls.github.server)
        cls.worker.stopping = True
        cls.worker.wake.set()
        for proc in [cls.php] + getattr(cls, "extra_php", []):
            proc.terminate()
            proc.wait(10)
        time.sleep(0.3)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    call = RunnerTest.call
    wait_job = RunnerTest.wait_job

    def job(self, job_type, version=None, params=None, expect="succeeded", site="shop"):
        job = self.call("POST", "/v1/sites/%s/jobs" % site, {"type": job_type, "version": version, "params": params or {}, "actor": "test"})
        return self.wait_job(job, expect)

    def live(self, name):
        with open(os.path.join(self.docroot, name), encoding="utf-8") as f:
            return f.read()

    def notes(self):
        import sqlite3
        with sqlite3.connect(self.db) as db:
            return [r[0] for r in db.execute("SELECT body FROM notes ORDER BY id")], [r[1] for r in db.execute("PRAGMA table_info(notes)")]

    def agent_url(self):
        return "http://127.0.0.1:%d/%s" % (self.port, self.agent_name)

    # ---------- tests (run in order) ----------
    def test_00_register_and_download_the_agent(self):
        created = self.call("POST", "/v1/sites", {
            "preset": "hosting-php", "id": "shop", "domain": "shop.example.com", "repo": "o/host", "asset_pattern": "demo_v*.zip",
            "shared": [".env", "uploads/"], "backup_paths": [".env", "uploads/"], "backup_env_file": ".env", "backup_db": "auto",
            "health_url": "http://127.0.0.1:%d/version.txt" % self.port, "health_contains": "version {version}"})
        self.assertEqual((created["state"], created["deploy_mode"], created["hosting"]["connected"]), ("unpaired", "hosting", False))
        status, doc = self.client.post("/v1/sites/shop/jobs", {"type": "deploy", "version": "1.0.0"})
        self.assertEqual((status, doc["error"]["code"]), (409, "not_connected"))
        agent = self.call("POST", "/v1/sites/shop/agent-file", {"dashboard": "https://dash.example.com"})
        self.assertIn("const RA_SITE = 'shop';", agent["content"])
        self.assertNotIn("__RA_PAIRING_KEY__", agent["content"])
        type(self).agent_name = agent["filename"]
        with open(os.path.join(self.docroot, agent["filename"]), "w", encoding="utf-8", newline="\n") as f:
            f.write(agent["content"])
        with urllib.request.urlopen(self.agent_url(), timeout=10) as response:
            page = response.read().decode("utf-8")
        self.assertIn("https://dash.example.com/?connect=shop&amp;agent=" + urllib.parse.quote(self.agent_url(), safe=""), page)
        self.assertNotIn(self.docroot, page)                                                   # no paths on the page
        public = urllib.request.Request(self.agent_url(), headers={"Host": "31.41.59.26"})    # not a private address
        with urllib.request.urlopen(public, timeout=10) as response:
            page = response.read().decode("utf-8")
        self.assertIn('<td>HTTPS</td><td class="warn">!</td>', page)                           # plain http: allowed, marked
        self.assertIn('id="ra-connect"', page)
        self.assertIn(urllib.parse.quote("http://31.41.59.26/" + self.agent_name, safe=""), page)

    def test_01_connect(self):
        status, doc = self.client.post("/v1/sites/shop/agent/connect", {"url": self.agent_url() + "?x=1"})
        self.assertEqual(status, 422)                                                          # not the address of the file
        key = self.config.secret_of("agent_keys", "shop")
        sent, call = [], AgentClient.call

        def record(client, action, data=None, *args, **kwargs):
            sent.append((action, json.dumps(data or {})))
            return call(client, action, data, *args, **kwargs)

        with mock.patch.object(AgentClient, "call", record):
            details = self.call("POST", "/v1/sites/shop/agent/connect", {"url": self.agent_url()})
        self.assertEqual((details["state"], details["hosting"]["connected"], details["hosting"]["data_outside"]), ("classic", True, True))
        self.assertTrue(details["hosting"]["insecure"])                                        # plain http: marked "No HTTPS"
        secret = self.config.secret_of("agent_secrets", "shop")
        pair = [json.loads(data) for action, data in sent if action == "pair"]
        self.assertEqual([sorted(p) for p in pair], [["salt"]])                                # no secret on the wire
        self.assertEqual(secret, pair_secret(key, pair[0]["salt"]))                            # the agent derives the same one
        self.assertFalse([action for action, data in sent if secret in data or key in data])
        self.assertTrue(os.path.isdir(os.path.join(self.home, ".rollout-agent-shop")))       # outside the website folder
        status, doc = self.client.post("/v1/sites/shop/agent/connect", {"url": self.agent_url()})
        self.assertIn("download the agent file first", doc["error"]["message"])               # the key works once
        with self.assertRaises(AgentError) as error:
            AgentClient(self.agent_url(), "0" * 64).call("status")
        self.assertEqual(error.exception.code, "bad_signature")
        with urllib.request.urlopen(self.agent_url(), timeout=10) as response:
            page = response.read().decode("utf-8")
        self.assertIn("Terhubung", page)
        self.assertNotIn("connect?agent", page)

    def test_02_adopt_files_made_by_hand(self):
        status, doc = self.client.post("/v1/sites/shop/jobs", {"type": "deploy", "version": "1.0.0"})
        self.assertEqual((status, doc["error"]["code"]), (409, "needs_adopt"))
        job = self.job("adopt", "1.0.0", params={"migrations": "auto"})               # here the database is still empty
        log = job["log"]["text"]
        self.assertIn("Upload the release to the hosting", log)
        self.assertIn("Database changes run on the hosting: 001_create_notes.sql", log)
        self.assertEqual(self.live("version.txt"), "version 1.0.0")
        self.assertEqual(self.live("uploads/photo.txt"), "photo")                              # shared data stays
        self.assertEqual(self.live(".htaccess"), "# my own rules\n")                          # the release has none
        self.assertEqual(self.live(".well-known/acme.txt"), "acme")
        self.assertTrue(os.path.isfile(os.path.join(self.docroot, self.agent_name)))
        self.assertIn("DB_CONNECTION=sqlite", self.live(".env"))
        with open(os.path.join(self.home, ".rollout-agent-shop", "releases", "_pre_rollout", "index.html"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "old site made by hand")
        self.assertEqual(self.notes()[0], ["first; with a semicolon"])
        details = self.call("GET", "/v1/sites/shop")
        self.assertEqual((details["state"], details["current_version"], details["pre_rollout"]), ("ready", "1.0.0", True))
        backups = self.call("GET", "/v1/sites/shop/backups")
        self.assertEqual((len(backups), backups[0]["has_database"], backups[0]["has_files"]), (1, 1, 1))   # before adopting

    def test_03_database_changes_shown_and_marked_as_done_by_hand(self):
        plan = self.call("GET", "/v1/sites/shop/plan?version=1.1.0")
        self.assertEqual([(m["name"], m["status"]) for m in plan["migrations"]], [("002_add_created.sql", "pending")])
        self.assertIn("ALTER TABLE notes ADD COLUMN created", plan["migrations"][0]["sql"])
        job = self.job("deploy", "1.1.0", params={"migrations": "manual", "backup": False})
        self.assertIn("marked as done by hand: 002_add_created.sql", job["log"]["text"])
        self.assertNotIn("created", self.notes()[1])                                           # not run: the user does it
        self.assertEqual(self.call("GET", "/v1/sites/shop/plan?version=1.1.0")["migrations"], [])
        self.assertEqual(self.live("version.txt"), "version 1.1.0")

    def test_04_failed_health_check_rolls_back(self):
        job = self.job("deploy", "1.2.0", params={"backup": False}, expect="failed")
        self.assertIn("rolled back to 1.1.0", job["message"])
        self.assertEqual(self.live("version.txt"), "version 1.1.0")

    def test_05_rollback(self):
        details = self.call("GET", "/v1/sites/shop")
        self.assertEqual(details["rollback_version"], "1.0.0")                                 # not the failed 1.2.0
        self.assertNotIn("1.2.0", details["installed"])                                        # pruned: never live and healthy
        job = self.job("rollback", "1.0.0")
        self.assertEqual((job["from_version"], job["to_version"]), ("1.1.0", "1.0.0"))
        self.assertEqual(self.live("version.txt"), "version 1.0.0")
        self.assertEqual(self.live("uploads/photo.txt"), "photo")
        self.job("rollback", "1.1.0")
        self.assertEqual(self.live("version.txt"), "version 1.1.0")

    def test_06_backup_on_the_runner_and_restore(self):
        backup_id = self.job("backup")["message"].split()[-1]
        self.assertTrue(os.path.isfile(os.path.join(self.config["backup_dir"], "shop", backup_id, "database.sql.gz")))
        self.assertEqual(os.listdir(os.path.join(self.home, ".rollout-agent-shop", "backups")), [])        # nothing left there
        with open(os.path.join(self.docroot, "uploads", "photo.txt"), "w", encoding="utf-8") as f:
            f.write("changed")
        import sqlite3
        with sqlite3.connect(self.db) as db:
            db.execute("INSERT INTO notes (body) VALUES ('after the backup')")
        self.job("restore", params={"backup_id": backup_id, "safety_backup": False})
        self.assertEqual(self.live("uploads/photo.txt"), "photo")
        self.assertEqual(self.notes()[0], ["first; with a semicolon"])

    def test_07_prune_and_cleanup(self):
        installed = self.call("GET", "/v1/sites/shop")["installed"]
        self.assertLessEqual(len(installed), 2)                                                # keep_releases 2 of the preset
        self.job("cleanup-adopt")
        self.assertFalse(self.call("GET", "/v1/sites/shop")["pre_rollout"])

    def test_08_refused_requests(self):
        secret = self.config.secret_of("agent_secrets", "shop")
        client = AgentClient(self.agent_url(), secret)
        with self.assertRaises(AgentError) as error:
            client.call("download", {"path": "state.php"})
        self.assertEqual(error.exception.status, 403)
        job = self.job("deploy", "1.3.0", params={"backup": False}, expect="failed")          # ../evil.txt in the archive
        self.assertIn("unsafe path", job["message"])
        self.assertFalse(os.path.exists(os.path.join(self.home, "evil.txt")))
        body = b"{}"
        url = self.agent_url() + "?action=status"
        ts, nonce = str(int(time.time())), "a" * 32
        message = "POST\n/%s?action=status\n%s\n%s\n%s" % (self.agent_name, ts, nonce, hashlib.sha256(body).hexdigest())
        import hmac as hmac_module
        signature = hmac_module.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
        headers = {"X-Rollout-Timestamp": ts, "X-Rollout-Nonce": nonce, "X-Rollout-Signature": signature}
        with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers, method="POST"), timeout=10) as first:
            self.assertEqual(first.status, 200)
        with self.assertRaises(urllib.error.HTTPError) as replay:
            urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers, method="POST"), timeout=10)
        self.assertIn(b"replayed", replay.exception.read())

    def test_09_published_subfolder(self):
        docroot = os.path.join(self.tmp, "home2", "public_html")
        os.makedirs(docroot)
        proc, port = php_server(docroot)
        type(self).extra_php = [proc]
        self.call("POST", "/v1/sites", {"preset": "hosting-static", "id": "spa", "repo": "o/spa", "source": "source",
                                        "docroot_target": "dist", "health_url": "http://127.0.0.1:%d/version.txt" % port,
                                        "health_contains": "version {version}"})
        agent = self.call("POST", "/v1/sites/spa/agent-file", {"dashboard": "https://dash.example.com"})
        with open(os.path.join(docroot, agent["filename"]), "w", encoding="utf-8", newline="\n") as f:
            f.write(agent["content"])
        self.call("POST", "/v1/sites/spa/agent/connect", {"url": "http://127.0.0.1:%d/%s" % (port, agent["filename"])})
        self.job("deploy", "2.0.0", site="spa")
        self.job("deploy", "2.1.0", site="spa")
        self.assertEqual(sorted(n for n in os.listdir(docroot) if not n.startswith(".")), sorted(["index.html", "version.txt", agent["filename"]]))
        with open(os.path.join(docroot, "index.html"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "spa 2.1.0")                                            # only dist/ is published
        self.job("rollback", site="spa")
        with open(os.path.join(docroot, "index.html"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "spa 2.0.0")

    def test_10_removing_the_site_disconnects_the_agent(self):
        secret = self.config.secret_of("agent_secrets", "spa")
        url = self.call("GET", "/v1/sites/spa")["hosting"]["url"]
        self.call("DELETE", "/v1/sites/spa")
        self.assertEqual(self.config.secret_of("agent_secrets", "spa"), "")
        with self.assertRaises(AgentError) as error:
            AgentClient(url, secret).call("status")
        self.assertEqual(error.exception.code, "bad_signature")

    def test_11_plain_http_needs_an_agent_that_derives_its_secret(self):
        docroot = os.path.join(self.tmp, "home3", "public_html")
        os.makedirs(docroot)
        proc, port = php_server(docroot)
        type(self).extra_php = getattr(type(self), "extra_php", []) + [proc]
        self.call("POST", "/v1/sites", {"preset": "hosting-static", "id": "lan", "repo": "o/spa", "source": "source",
                                        "health_url": "http://127.0.0.1:%d/" % port})
        agent = self.call("POST", "/v1/sites/lan/agent-file", {"dashboard": "https://dash.example.com"})
        key = re.search(r"const RA_PAIRING_KEY = '([0-9a-f]{64})';", agent["content"]).group(1)

        def drop(name, content):                                                               # a new name: no stale opcache
            with open(os.path.join(docroot, name), "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
            return "http://127.0.0.1:%d/%s" % (port, name)

        url = drop(agent["filename"], agent["content"])
        with self.assertRaises(AgentError) as error:                                           # a secret from an old runner
            AgentClient(url, key).call("pair", {"secret": "a" * 64})
        self.assertEqual((error.exception.code, error.exception.status), ("bad_salt", 422))
        old = drop("rollout-agent-old000.php", re.sub(r"const RA_VERSION = '[^']+';", "const RA_VERSION = '1.5.4';", agent["content"]))
        status, doc = self.client.post("/v1/sites/lan/agent/connect", {"url": old})            # an agent file of 1.5.4
        self.assertEqual(status, 422)
        self.assertIn("download the agent file again", doc["error"]["message"])
        self.assertFalse(AgentClient(old, key).call("hello")["paired"])                         # nothing sent, the key still works
        details = self.call("POST", "/v1/sites/lan/agent/connect", {"url": url})
        self.assertTrue(details["hosting"]["connected"] and details["hosting"]["insecure"])
        secret = self.config.secret_of("agent_secrets", "lan")
        self.assertEqual(AgentClient(url, secret).call("status")["agent"], hosting_module.template_version())
        with self.assertRaises(AgentError) as error:
            AgentClient(url, key).call("hello")                                                 # the key worked once
        self.assertEqual(error.exception.code, "bad_signature")
        self.call("DELETE", "/v1/sites/lan")


def make_tar(files, top=""):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as t:
        for name, content in files.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            info = tarfile.TarInfo(top + name)
            info.size, info.mode = len(data), 0o644
            t.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class LogLines(object):
    def __init__(self):
        self.lines = []

    def line(self, text):
        self.lines.append(text)
    step = output = error = line


class UploadMixin(object):
    call = RunnerTest.call
    wait_job = RunnerTest.wait_job

    def upload(self, site, name, data, version=None, chunk=None, expect="succeeded"):
        """Send an archive like the dashboard does: start, pieces, finish (-> the upload job)."""
        start = self.call("POST", "/v1/sites/%s/uploads" % site, {"name": name, "size": len(data), "version": version, "actor": "test"})
        step, offset = chunk or start["chunk_size"], 0
        while offset < len(data):
            status, doc = self.client.request("PUT", "/v1/sites/%s/uploads/%s?offset=%d" % (site, start["id"], offset),
                                              raw=data[offset:offset + step])
            self.assertEqual(status, 200, doc)
            offset = doc["data"]["received"]
        return self.wait_job(self.call("POST", "/v1/sites/%s/uploads/%s/finish" % (site, start["id"]), {"actor": "test"}), expect)

    def runner(self, prefix, **options):
        cls = type(self)
        config_path = os.path.join(cls.tmp, "etc", "runner.json")
        os.makedirs(os.path.dirname(config_path))
        cls.config = Config.create(config_path, state_dir=os.path.join(cls.tmp, "state"), backup_dir=os.path.join(cls.tmp, "backup"),
                                   github_api="http://127.0.0.1:9", shell=SHELL, port=0, health_attempts=2, health_delay=0.3,
                                   **options)
        cls.config.prepare_dirs()
        store = Store(cls.config.state_path("runner.db"))
        cls.deployer = Deployer(cls.config, store, Backups(cls.config, store))
        cls.worker = Worker(cls.config, store, cls.deployer)
        cls.worker.start()
        cls.server = serve(Api(cls.config, store, cls.deployer, cls.worker))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.client = Client("http://127.0.0.1:%d" % cls.server.server_address[1], cls.config["secret"])


class UploadTest(UploadMixin, unittest.TestCase):
    """Versions uploaded through the dashboard for a site on this server (no GitHub)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="rollout-upload-")
        cls.www = os.path.join(cls.tmp, "wwwroot")
        cls.site_dir = os.path.join(cls.www, "up.example.com")
        os.makedirs(cls.site_dir)
        cls.web = HTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=os.path.join(cls.site_dir, "public")))
        threading.Thread(target=cls.web.serve_forever, daemon=True).start()
        UploadMixin.runner(cls(), "upload", site_roots=[cls.www])

    @classmethod
    def tearDownClass(cls):
        stop(cls.server, cls.web)
        cls.worker.stopping = True
        cls.worker.wake.set()
        time.sleep(0.3)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def job(self, job_type, version=None, params=None, expect="succeeded", site="up"):
        job = self.call("POST", "/v1/sites/%s/jobs" % site, {"type": job_type, "version": version, "params": params or {}, "actor": "test"})
        return self.wait_job(job, expect)

    def page(self):
        with open(os.path.join(self.site_dir, "public", "version.txt"), encoding="utf-8") as f:
            return f.read()

    def test_00_register_without_a_repository(self):
        status, doc = self.client.post("/v1/sites", {"preset": "static", "id": "nogit", "path": os.path.join(self.www, "x"), "repo": ""})
        self.assertEqual((status, set(doc["error"]["fields"])), (422, {"repo"}))                 # GitHub sites need one
        created = self.call("POST", "/v1/sites", {
            "preset": "static", "id": "up", "path": self.site_dir, "source": "upload", "repo": "", "web_user": "", "keep_releases": 2,
            "upload_keep": 2, "health_url": "http://127.0.0.1:%d/version.txt" % self.web.server_address[1], "health_contains": "version {version}"})
        self.assertEqual((created["state"], created["latest_version"], created["source"]), ("empty", None, "upload"))
        status, doc = self.client.post("/v1/sites/up/jobs", {"type": "deploy", "version": "003"})
        self.assertEqual((status, doc["error"]["fields"].get("version")), (422, "Upload this version first."))

    def test_01_upload_a_zip_in_pieces(self):
        files = {"index.html": "<h1>up</h1>", "version.txt": "version 003", ".env": "APP_KEY=local-secret", "node_modules/a/i.js": "x",
                 ".git/HEAD": "ref", "sub/.DS_Store": "x", "storage/logs/laravel.log": "log", "storage/logs/.gitignore": "*\n",
                 "bootstrap/cache/packages.php": "<?php", "deploy/run.sh": "#!/bin/sh\necho hi\n", ".env.example": "APP_KEY=\n"}
        job = self.upload("up", "site#003.zip", make_zip({"site#003/" + k: v for k, v in files.items()}), chunk=700)
        self.assertEqual(job["to_version"], "003")                                             # from the file name
        removed = [l for l in job["log"]["text"].splitlines() if "removed (not deployed)" in l][0]
        self.assertTrue({".env", "node_modules/", ".git/", "sub/.DS_Store"} <= set(removed.split(": ", 2)[-1].split(", ")), removed)
        releases = self.call("GET", "/v1/sites/up/releases")["releases"]
        self.assertEqual([(r["version"], r["uploaded"], r["name"]) for r in releases], [("003", True, "site#003.zip")])
        with zipfile.ZipFile(self.deployer.uploads.archive("up", "003")) as z:
            names = set(z.namelist())
            mode = z.getinfo("deploy/run.sh").external_attr >> 16
        self.assertTrue({"index.html", ".env.example", "storage/logs/.gitignore", "bootstrap/cache/"} <= names, names)
        self.assertFalse({".env", "node_modules/a/i.js", ".git/HEAD", "sub/.DS_Store", "storage/logs/laravel.log",
                          "bootstrap/cache/packages.php"} & names)
        if os.name == "posix":
            self.assertTrue(mode & 0o111)
        self.assertEqual(self.call("GET", "/v1/sites/up")["latest_version"], "003")

    def test_02_deploy_uploads(self):
        job = self.job("deploy", "003")
        self.assertIn("Take the uploaded version 003", job["log"]["text"])
        self.assertEqual(self.page(), "version 003")
        self.upload("up", "site-004.tar.gz", make_tar({"index.html": "<h1>up</h1>", "version.txt": "version 004"}, top="site-004/"))
        details = self.call("GET", "/v1/sites/up")
        self.assertEqual((details["latest_version"], details["update_available"]), ("004", True))
        self.job("deploy", "004")
        self.assertEqual(self.page(), "version 004")
        self.job("rollback")
        self.assertEqual(self.page(), "version 003")

    def test_03_refused_uploads(self):
        status, doc = self.client.post("/v1/sites/up/uploads", {"name": "notes.txt", "size": 10})
        self.assertEqual(status, 422)
        status, doc = self.client.post("/v1/sites/up/uploads", {"name": "again.zip", "size": 10, "version": "003"})
        self.assertIn("uploaded already", doc["error"]["message"])
        start = self.call("POST", "/v1/sites/up/uploads", {"name": "half.zip", "size": 100, "version": "half"})
        self.client.request("PUT", "/v1/sites/up/uploads/%s?offset=0" % start["id"], raw=b"x" * 10)
        status, doc = self.client.post("/v1/sites/up/uploads/%s/finish" % start["id"])
        self.assertIn("incomplete", doc["error"]["message"])
        job = self.upload("up", "evil.zip", make_zip({"../evil.txt": "x"}), version="evil", expect="failed")
        self.assertIn("unsafe path", job["message"])
        job = self.upload("up", "junk.zip", b"this is not an archive at all", version="junk", expect="failed")
        self.assertIn("not an archive", job["message"])
        self.assertEqual(sorted(v["version"] for v in self.deployer.uploads.versions("up")), ["003", "004"])
        self.call("POST", "/v1/sites", {"preset": "static", "id": "gh", "path": os.path.join(self.www, "gh"), "repo": "o/gh"})
        status, doc = self.client.post("/v1/sites/gh/uploads", {"name": "a.zip", "size": 10})
        self.assertEqual((status, doc["error"]["code"]), (409, "not_upload"))
        status, doc = self.client.request("DELETE", "/v1/sites/up/uploads/003")                # live
        self.assertEqual(status, 409)

    def test_04_prune_keeps_the_newest_uploads(self):
        for version in ("005", "006"):
            self.upload("up", "site-%s.zip" % version, make_zip({"version.txt": "version " + version}))
        self.job("deploy", "005")
        self.job("deploy", "006")
        self.assertEqual([v["version"] for v in self.deployer.uploads.versions("up")], ["006", "005"])   # upload_keep 2

    def test_05_unpack_with_a_program(self):
        tool = shutil.which("bsdtar") or (r"C:\Windows\System32\tar.exe" if os.path.isfile(r"C:\Windows\System32\tar.exe") else None)
        if not tool:
            self.skipTest("no bsdtar")
        archive = os.path.join(self.tmp, "tool.tar.gz")
        with open(archive, "wb") as f:
            f.write(make_tar({"index.html": "tool", "css/app.css": "body{}"}, top="app/"))
        dest = os.path.join(self.tmp, "tool-out")
        os.makedirs(dest)
        log = LogLines()
        files, skipped = unpack_with_tool({"bsdtar": tool}, "rar", archive, dest, log)
        self.assertEqual((files, skipped), (2, 0))
        with open(os.path.join(dest, "app", "index.html"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "tool")
        with mock.patch("arsika_rollout_manager.uploads.archive_tools", return_value=[]):
            with self.assertRaises(UploadError) as error:
                unpack_with_tool({}, "rar", archive, dest, log)
        self.assertIn("libarchive-tools", str(error.exception))

    def test_06_helpers(self):
        self.assertEqual(suggest_version("absensi-finger#003.rar"), "003")
        self.assertEqual(suggest_version("shop-v1.2.0.tar.gz"), "1.2.0")
        self.assertRegex(suggest_version("backup.zip"), r"^\d{8}-\d{4}$")
        self.assertFalse(php_satisfies("^8.2", "8.1.27"))
        self.assertTrue(php_satisfies("^8.2", "8.3.1"))
        self.assertTrue(php_satisfies("^7.4|^8.0", "8.1.0"))
        self.assertFalse(php_satisfies(">=8.3", "8.2.9"))
        self.assertTrue(excluded("storage/logs/laravel.log", False, ["storage/logs/*"]))
        self.assertFalse(excluded("storage/logs/.gitignore", False, ["storage/logs/*"]))
        self.assertTrue(excluded("a/b/.DS_Store", False, ["**/.DS_Store"]))
        self.assertFalse(excluded("config/.env", False, [".env"]))                              # only the top level
        for url in ("http://192.168.1.20/rollout-agent-abc123.php", "http://nas.local/site/rollout-agent-abc123.php",
                    "http://10.0.0.5:8080/a.php", "https://example.com/a.php",
                    "http://31.41.59.26/rollout-agent-abc123.php", "http://example.com/a.php"):  # plain http: any address
            self.assertEqual(valid_agent_url(url), url)
        for url in ("ftp://example.com/a.php", "http://example.com/a.txt", "http://example.com/a.php?x=1",
                    "https://example.com/a.php#x", "http://exa mple.com/a.php", "http://user@example.com/a.php", ""):
            self.assertIsNone(valid_agent_url(url))
        self.assertEqual(pair_secret("k" * 64, "s" * 64), pair_secret("k" * 64, "s" * 64))
        self.assertNotEqual(pair_secret("k" * 64, "s" * 64), pair_secret("k" * 64, "t" * 64))
        self.assertRegex(pair_secret("k" * 64, "s" * 64), r"^[0-9a-f]{64}$")
        self.assertEqual(artisan_command("php artisan migrate:status"), "migrate:status")
        for refused in ("serve", "tinker", "queue:work", "a; rm -rf /", "migrate\nstatus"):
            with self.assertRaises(DeployError):
                artisan_command(refused)
        self.assertEqual(artisan_command("queue:work --stop-when-empty"), "queue:work --stop-when-empty")
        self.assertEqual(artisan_command('tinker --execute="echo 1;"'), 'tinker --execute="echo 1;"')         # no input needed
        fatal = "<br />\n<b>Warning</b>:  file_exists(): open_basedir restriction in effect<br />\n<b>Fatal error</b>:  Uncaught Error"
        self.assertEqual(system.php_error(fatal), "Fatal error")                              # status 200 after the warnings
        self.assertEqual(system.php_error("PHP Parse error:  syntax error, unexpected"), "Parse error")
        self.assertIsNone(system.php_error("<h1>Fatal error handling in our docs</h1><p>a fatal error: what to do</p>"))


FAKE_LARAVEL_AUTOLOAD = r"""<?php
// Test double of a Laravel application (vendor/autoload.php): the page and artisan show what the agent set up.
namespace Illuminate\Http {
    class Request { public static function capture() { return new self(); } }
}
namespace Symfony\Component\Console\Output {
    class BufferedOutput {
        private $text = '';
        public function write($message, $newline = false) { $this->text .= $message . ($newline ? "\n" : ''); }
        public function writeln($message) { $this->write($message, true); }
        public function fetch() { $text = $this->text; $this->text = ''; return $text; }
    }
}
namespace FakeLaravel {
    class Application {
        public $base; public $env; public $storage; public $public;
        public function __construct($base) { $this->base = $base; $this->env = $base; $this->storage = $base . '/storage'; $this->public = $base . '/public'; }
        public function useEnvironmentPath($path) { $this->env = $path; return $this; }
        public function useStoragePath($path) { $this->storage = $path; return $this; }
        public function usePublicPath($path) { $this->public = $path; return $this; }
        public function values() {
            $values = [];
            foreach (@file($this->env . '/.env', FILE_IGNORE_NEW_LINES) ?: [] as $line) {
                if (strpos($line, '=') !== false && $line[0] !== '#') { list($k, $v) = explode('=', $line, 2); $values[trim($k)] = trim($v); }
            }
            return $values;
        }
        public function make($abstract) { return new ConsoleKernel($this); }
        public function handleRequest($request) {
            $env = $this->values();
            header('Content-Type: text/plain');
            echo 'app ' . trim((string)@file_get_contents($this->base . '/VERSION')) . ' env=' . ($env['APP_NAME'] ?? '-')
                . ' key=' . (($env['APP_KEY'] ?? '') !== '' ? 'set' : 'empty')
                . ' public=' . (realpath($this->public) === realpath(dirname($_SERVER['SCRIPT_FILENAME'])) ? 'web' : 'other')
                . ' storage=' . (is_dir($this->storage . '/framework/views') ? 'ok' : 'missing');
        }
    }
    class ConsoleKernel {
        private $app;
        public function __construct($app) { $this->app = $app; }
        public function bootstrap() { $this->app->booted = true; }
        public function call($command, array $parameters = [], $output = null) {
            @file_put_contents($this->app->storage . '/logs/artisan.log', $command . "\n", FILE_APPEND);
            $name = strtok($command, ' ');
            if ($name === 'fail') { $output->writeln('boom'); return 1; }
            if ($name === 'explode') { throw new \RuntimeException('kaput'); }
            if ($name === 'migrate' || $name === 'migrate:status') {
                $env = $this->app->values();
                $pdo = new \PDO('sqlite:' . $env['DB_DATABASE']);
                $pdo->exec('CREATE TABLE IF NOT EXISTS migrations (id INTEGER PRIMARY KEY, migration TEXT, batch INTEGER)');
                $done = $pdo->query('SELECT migration FROM migrations')->fetchAll(\PDO::FETCH_COLUMN);
                foreach (glob($this->app->base . '/database/migrations/*.php') as $file) {
                    $migration = basename($file, '.php');
                    if ($name === 'migrate' && !in_array($migration, $done, true)) {
                        $pdo->prepare('INSERT INTO migrations (migration, batch) VALUES (?, 1)')->execute([$migration]);
                        $output->writeln('Migrated: ' . $migration);
                    } elseif ($name === 'migrate:status') {
                        $output->writeln((in_array($migration, $done, true) ? 'Ran ' : 'Pending ') . $migration);
                    }
                }
                return 0;
            }
            $output->writeln('ran ' . $command);
            return 0;
        }
    }
}
"""


def laravel_files(version, migrations=1, vendor=True, php=">=7.4", junk=True):
    files = {
        "artisan": "#!/usr/bin/env php\n<?php // fake artisan\n", "VERSION": version, "composer.json": json.dumps({"require": {"php": php}}),
        "bootstrap/app.php": "<?php\nreturn new FakeLaravel\\Application(dirname(__DIR__));\n", "bootstrap/cache/.gitignore": "*\n",
        "public/index.php": "<?php\nrequire __DIR__.'/../vendor/autoload.php';\n$app = require_once __DIR__.'/../bootstrap/app.php';\n"
                            "$app->handleRequest(Illuminate\\Http\\Request::capture());\n",
        "public/.htaccess": "# the front controller of laravel\n", "public/robots.txt": "robots " + version, "public/css/app.css": "body{}",
        "storage/app/public/.gitignore": "*\n", "storage/framework/views/.gitignore": "*\n", "storage/logs/.gitignore": "*\n",
        ".env.example": "APP_NAME=Fake\nAPP_ENV=local\nAPP_KEY=\nAPP_DEBUG=true\nAPP_URL=http://localhost\nDB_CONNECTION=sqlite\n",
        "database/migrations/2026_01_01_000000_create_notes.php": "<?php // notes\n",
    }
    if migrations > 1:
        files["database/migrations/2026_02_01_000000_add_title.php"] = "<?php // title\n"
    if vendor:
        files["vendor/autoload.php"] = FAKE_LARAVEL_AUTOLOAD
    if junk:
        files.update({".env": "APP_NAME=Local\nDB_PASSWORD=local-secret\n", "storage/logs/laravel.log": "local log",
                      "node_modules/x/index.js": "x", "bootstrap/cache/packages.php": "<?php return [];"})
    return files


@unittest.skipUnless(PHP, "PHP is needed to run the hosting agent")
class LaravelHostingTest(UploadMixin, unittest.TestCase):
    """A Laravel site on a "hosting" (a folder served by PHP's built-in server), installed from uploaded versions."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="rollout-laravel-")
        cls.home = os.path.join(cls.tmp, "home")
        cls.docroot = os.path.join(cls.home, "public_html")
        os.makedirs(cls.docroot)
        cls.db = os.path.join(cls.tmp, "lara.sqlite").replace("\\", "/")
        autoload = os.path.join(cls.tmp, "fake-autoload.php")
        with open(autoload, "w", encoding="utf-8", newline="\n") as f:
            f.write(FAKE_LARAVEL_AUTOLOAD)
        os.environ["FAKE_COMPOSER_AUTOLOAD"] = autoload
        cls.php, cls.port = php_server(cls.docroot, AAPANEL_PHP)                       # as strict as aaPanel
        cls.extra_php = []
        UploadMixin.runner(cls(), "laravel", site_roots=[cls.tmp], composer_command=[sys.executable, FAKE_COMPOSER])

    @classmethod
    def tearDownClass(cls):
        stop(cls.server)
        cls.worker.stopping = True
        cls.worker.wake.set()
        for proc in [cls.php] + cls.extra_php:
            proc.terminate()
            proc.wait(10)
        time.sleep(0.3)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def job(self, job_type, version=None, params=None, expect="succeeded", site="lara"):
        job = self.call("POST", "/v1/sites/%s/jobs" % site, {"type": job_type, "version": version, "params": params or {}, "actor": "test"})
        return self.wait_job(job, expect)

    def live(self, name, docroot=None):
        with open(os.path.join(docroot or self.docroot, name), encoding="utf-8") as f:
            return f.read()

    def page(self, port=None):
        with urllib.request.urlopen("http://127.0.0.1:%d/" % (port or self.port), timeout=10) as response:
            return response.read().decode("utf-8")

    def agent_url(self):
        return "http://127.0.0.1:%d/%s" % (self.port, self.agent_name)

    def shared(self, *parts):
        return os.path.join(self.home, ".rollout-agent-lara", "shared", *parts)

    def migrations(self):
        import sqlite3
        with sqlite3.connect(self.db) as db:
            return [r[0] for r in db.execute("SELECT migration FROM migrations ORDER BY id")]

    def test_00_register_upload_and_prepare_the_env(self):
        created = self.call("POST", "/v1/sites", {"preset": "hosting-laravel", "id": "lara", "source": "upload", "repo": "",
                                                  "health_contains": "app {version}"})
        settings = created["settings"]
        self.assertEqual((settings["docroot_target"], settings["hosting_app"], settings["health_url"]), ("public", "laravel", "{site_url}/"))
        self.assertIsNone(created["health"])                                                    # no address before connecting
        job = self.upload("lara", "lara#1.0.0.zip", make_zip({"lara/" + k: v for k, v in laravel_files("1.0.0").items()}))
        self.assertEqual(job["to_version"], "1.0.0")
        env = self.call("GET", "/v1/sites/lara/env")
        self.assertEqual((env["exists"], env["where"], env["laravel"]), (False, "dashboard", True))
        self.assertIn("APP_ENV=production", env["template"])
        self.assertIn("APP_DEBUG=false", env["template"])
        self.assertRegex(env["template"], r"APP_KEY=base64:\S{40,}")
        saved = self.call("PUT", "/v1/sites/lara/env", {"content": "APP_NAME=Lara\nAPP_KEY=\nDB_CONNECTION=sqlite\nDB_DATABASE=%s\n" % self.db})
        self.assertEqual(saved["where"], "dashboard")
        self.assertTrue(self.call("GET", "/v1/sites/lara")["env"]["pending"])

    def test_01_connect_sends_the_env(self):
        agent = self.call("POST", "/v1/sites/lara/agent-file", {"dashboard": "https://dash.example.com"})
        type(self).agent_name = agent["filename"]
        with open(os.path.join(self.docroot, agent["filename"]), "w", encoding="utf-8", newline="\n") as f:
            f.write(agent["content"])
        with urllib.request.urlopen(self.agent_url(), timeout=10) as response:
            page = response.read().decode("utf-8")
        self.assertIn('id="ra-dash"', page)                                                     # the address can be changed
        self.assertIn('value="https://dash.example.com"', page)
        self.call("POST", "/v1/sites/lara/agent/connect", {"url": self.agent_url()})
        self.assertIn("APP_NAME=Lara", self.live(self.shared(".env")))
        details = self.call("GET", "/v1/sites/lara")
        self.assertEqual((details["env"]["pending"], details["env"]["on_hosting"], details["state"]), (False, True, "empty"))

    def test_02_deploy(self):
        job = self.job("deploy", "1.0.0")
        log = job["log"]["text"]
        self.assertIn("APP_KEY was empty", log)
        self.assertIn("$ php artisan migrate --force", log)
        self.assertIn("Migrated: 2026_01_01_000000_create_notes", log)
        names = set(os.listdir(self.docroot))
        self.assertTrue({"index.php", "robots.txt", "css", ".htaccess", self.agent_name} <= names, names)
        self.assertFalse({"vendor", "app", "artisan", ".env", "bootstrap", "VERSION", "composer.json", "database"} & names, names)
        self.assertEqual(self.page(), "app 1.0.0 env=Lara key=set public=web storage=ok")
        self.assertIn("Arsika Rollout Manager", self.live("index.php"))
        self.assertRegex(self.live(self.shared(".env")), r"APP_KEY=base64:\S{40,}")
        self.assertEqual(self.migrations(), ["2026_01_01_000000_create_notes"])
        self.assertTrue(self.call("GET", "/v1/sites/lara")["health"]["ok"])                    # {site_url}/

    def test_03_plan_and_update(self):
        self.upload("lara", "lara-1.1.0.zip", make_zip(laravel_files("1.1.0", migrations=2)))
        plan = self.call("GET", "/v1/sites/lara/plan?version=1.1.0")
        self.assertEqual((plan["kind"], [m["name"] for m in plan["migrations"]]), ("laravel", ["2026_02_01_000000_add_title.php"]))
        self.job("deploy", "1.1.0")
        self.assertTrue(self.page().startswith("app 1.1.0 env=Lara"))
        self.assertEqual(self.call("GET", "/v1/sites/lara/plan?version=1.1.0")["migrations"], [])

    def test_04_artisan_jobs(self):
        job = self.job("artisan", params={"command": "php artisan migrate:status"})
        self.assertIn("Ran 2026_02_01_000000_add_title", job["log"]["text"])
        status, doc = self.client.post("/v1/sites/lara/jobs", {"type": "artisan", "params": {"command": "serve"}})
        self.assertEqual(status, 422)
        job = self.job("artisan", params={"command": "fail"}, expect="failed")
        self.assertIn("boom", job["log"]["text"])
        self.assertIn("exit code 1", job["message"])
        job = self.job("artisan", params={"command": "explode"}, expect="failed")
        self.assertIn("kaput", job["message"])
        self.assertIn("migrate:status --no-interaction", self.live(self.shared("storage", "logs", "artisan.log")))
        job = self.job("artisan", params={"command": 'tinker --execute="echo \'users=\' . count([1, 2]); $secret = \'s3cret\';"'})
        self.assertIn("users=2", job["log"]["text"])                                           # run inside the application
        self.assertNotIn("s3cret", job["log"]["text"] + job["message"])                        # the code is not logged
        self.assertNotIn("s3cret", json.dumps(job["params"]) + json.dumps(self.worker.store.job(job["id"])["params"]))
        self.assertNotIn("s3cret", json.dumps(self.call("GET", "/v1/jobs?site=lara")))  # nor kept, nor handed out
        self.assertIn("tinker --execute=…", job["log"]["text"])
        job = self.job("artisan", params={"command": 'tinker --execute="return [\'a\' => 1];"'})
        self.assertIn('= {"a":1}', job["log"]["text"])                                          # the value of "return"
        job = self.job("artisan", params={"command": 'tinker --execute="throw new RuntimeException(\'nope\');"'}, expect="failed")
        self.assertIn("nope", job["message"])

    def test_05_rollback_and_edit_the_env(self):
        self.job("rollback")
        self.assertTrue(self.page().startswith("app 1.0.0 env=Lara"))
        env = self.call("GET", "/v1/sites/lara/env")
        self.assertEqual((env["exists"], env["where"]), (True, "hosting"))
        self.call("PUT", "/v1/sites/lara/env", {"content": env["content"].replace("APP_NAME=Lara", "APP_NAME=Lara2")})
        self.assertIn("env=Lara2", self.page())                                                # no deployment needed
        self.assertEqual(len(os.listdir(os.path.join(self.home, ".rollout-agent-lara", "env-previous"))), 1)

    def test_05b_a_newer_runner_with_the_same_agent_keeps_the_agent(self):
        before = self.live(self.agent_name)
        with mock.patch.object(hosting_module, "__version__", "9.9.9"):                     # the template is older than the runner
            job = self.job("artisan", params={"command": "migrate:status"})
        self.assertNotIn("Update the agent file", job["log"]["text"])
        self.assertEqual(self.live(self.agent_name), before)

    def test_06_vendor_from_composer_and_the_php_check(self):
        job = self.upload("lara", "lara-1.2.0.zip", make_zip(laravel_files("1.2.0", vendor=False)))
        self.assertIn("Install the PHP dependencies with composer", job["log"]["text"])
        with zipfile.ZipFile(self.deployer.uploads.archive("lara", "1.2.0")) as z:
            self.assertIn("vendor/autoload.php", z.namelist())
            self.assertIn("--no-dev", z.read("vendor/composer-arguments.txt").decode())
        self.upload("lara", "lara-9.0.0.zip", make_zip(laravel_files("9.0.0", php=">=99.0")))
        job = self.job("deploy", "9.0.0", expect="failed")
        self.assertIn("needs PHP >=99.0", job["message"])
        self.assertNotIn("Backup", job["log"]["text"])                                        # stopped before anything else

    def test_07_the_agent_updates_itself(self):
        template = os.path.join(self.tmp, "agent-1.5.6.php")
        with open(hosting_module.TEMPLATE, encoding="utf-8") as f:
            text = f.read().replace("const RA_VERSION = '1.5.5';", "const RA_VERSION = '1.5.6';")
        with open(template, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        before = self.live(self.agent_name)
        with mock.patch.object(hosting_module, "TEMPLATE", template):                          # a newer agent template
            job = self.job("deploy", "1.2.0")
        self.assertIn("Update the agent file on the hosting (1.5.5 -> 1.5.6)", job["log"]["text"])
        after = self.live(self.agent_name)
        self.assertIn("const RA_VERSION = '1.5.6';", after)
        key = re.search(r"const RA_PAIRING_KEY = '([0-9a-f]{64})';", after).group(1)
        self.assertNotIn(key, before)
        with open(os.path.join(self.home, ".rollout-agent-lara", "state.php"), encoding="utf-8") as f:
            state = json.loads(f.read().split("\n", 1)[1])
        self.assertEqual(state["pairing_hash"], hashlib.sha256(key.encode()).hexdigest())     # the new key connects nobody
        self.assertTrue(self.page().startswith("app 1.2.0 env=Lara2"))

    def test_08_adopt_a_laravel_website_in_the_folder(self):
        home = os.path.join(self.tmp, "home2")
        docroot = os.path.join(home, "public_html")
        db = os.path.join(self.tmp, "old.sqlite").replace("\\", "/")
        for name, text in (("artisan", "<?php // old"), ("index.php", "<?php echo 'old site';"), ("public/index.php", "<?php // old"),
                           (".env", "APP_NAME=Old\nAPP_KEY=base64:b2xkb2xkb2xkb2xkb2xkb2xkb2xkb2xkb2xkb2xkb2s=\nDB_CONNECTION=sqlite\nDB_DATABASE=%s\n" % db),
                           ("storage/app/public/photo.txt", "photo"), ("storage/framework/views/.keep", "")):
            os.makedirs(os.path.dirname(os.path.join(docroot, name)), exist_ok=True)
            with open(os.path.join(docroot, name), "w", encoding="utf-8") as f:
                f.write(text)
        proc, port = php_server(docroot, AAPANEL_PHP)
        self.extra_php.append(proc)
        self.call("POST", "/v1/sites", {"preset": "hosting-laravel", "id": "lara2", "source": "upload", "repo": "",
                                        "health_url": "http://127.0.0.1:%d/" % port, "health_contains": "app {version}"})
        self.upload("lara2", "lara.zip", make_zip(laravel_files("1.0.0")), version="1.0.0")
        agent = self.call("POST", "/v1/sites/lara2/agent-file", {"dashboard": "https://dash.example.com"})
        with open(os.path.join(docroot, agent["filename"]), "w", encoding="utf-8", newline="\n") as f:
            f.write(agent["content"])
        self.assertEqual(self.call("POST", "/v1/sites/lara2/agent/connect",
                                   {"url": "http://127.0.0.1:%d/%s" % (port, agent["filename"])})["state"], "classic")
        job = self.job("adopt", "1.0.0", site="lara2")
        self.assertIn(".env taken over from the website folder", job["log"]["text"])
        self.assertIn("database migrations not run", job["log"]["text"])                       # the running site has them
        self.assertEqual(self.page(port), "app 1.0.0 env=Old key=set public=web storage=ok")
        data = os.path.join(home, ".rollout-agent-lara2")
        self.assertEqual(self.live(os.path.join(data, "shared", "storage", "app", "public", "photo.txt")), "photo")
        self.assertTrue(os.path.isfile(os.path.join(data, "releases", "_pre_rollout", "artisan")))
        self.assertFalse(os.path.exists(os.path.join(docroot, ".env")))

    def test_10_a_stopped_deployment_is_never_made_live_by_a_switch(self):
        home = os.path.join(self.tmp, "home4")
        docroot = os.path.join(home, "public_html")
        os.makedirs(docroot)
        proc, port = php_server(docroot, AAPANEL_PHP)
        self.extra_php.append(proc)
        type(self).lara3 = (home, docroot, port)
        self.call("POST", "/v1/sites", {"preset": "hosting-laravel", "id": "lara3", "source": "upload", "repo": "",
                                        "health_contains": "app {version}"})
        self.upload("lara3", "lara3.zip", make_zip(laravel_files("1.0.0")), version="1.0.0")
        agent = self.call("POST", "/v1/sites/lara3/agent-file", {"dashboard": "https://dash.example.com"})
        with open(os.path.join(docroot, agent["filename"]), "w", encoding="utf-8", newline="\n") as f:
            f.write(agent["content"])
        self.call("POST", "/v1/sites/lara3/agent/connect", {"url": "http://127.0.0.1:%d/%s" % (port, agent["filename"])})
        job = self.job("deploy", "1.0.0", site="lara3", expect="failed")                        # no .env yet
        self.assertIn("the .env of the site is not set yet", job["message"])
        releases = {r["version"]: r for r in self.call("GET", "/v1/sites/lara3/releases")["releases"]}
        self.assertTrue(releases["1.0.0"]["installed"] and releases["1.0.0"]["never_live"])    # the interface offers Deploy
        job = self.job("rollback", "1.0.0", site="lara3", expect="failed")                     # "Activate" of 1.4/1.5.1
        self.assertIn("never went live", job["message"])
        self.assertFalse(os.path.exists(os.path.join(docroot, "index.php")))                   # nothing switched
        self.call("PUT", "/v1/sites/lara3/env", {"content": "APP_NAME=Three\nAPP_KEY=\nDB_CONNECTION=sqlite\nDB_DATABASE=%s\n"
                                                             % os.path.join(self.tmp, "lara3.sqlite").replace("\\", "/")})
        job = self.job("deploy", "1.0.0", site="lara3")
        self.assertIn("still on the hosting; reusing it", job["log"]["text"])
        self.assertEqual(self.page(port), "app 1.0.0 env=Three key=set public=web storage=ok")

    def test_11_a_broken_front_controller_fails_the_health_check_and_deploy_again_repairs_it(self):
        home, docroot, port = self.lara3
        with open(os.path.join(docroot, "index.php"), "w", encoding="utf-8") as f:             # Laravel's own public/index.php
            f.write("<?php\nrequire __DIR__.'/../vendor/autoload.php';\n$app = require_once __DIR__.'/../bootstrap/app.php';\n")
        self.assertFalse(self.call("GET", "/v1/sites/lara3/health")["ok"])
        status, doc = self.client.post("/v1/sites/lara3/jobs", {"type": "deploy", "version": "1.0.0"})
        job = self.wait_job(doc["data"], "failed")
        self.assertIn("Deploy again", job["message"])                                         # explains how
        job = self.job("deploy", "1.0.0", params={"redeploy": True, "migrations": "auto"}, site="lara3")
        self.assertIn("Deploy 1.0.0 again (it stays live)", job["log"]["text"])
        self.assertEqual(job["message"], "deployed 1.0.0 again")
        self.assertEqual(self.page(port), "app 1.0.0 env=Three key=set public=web storage=ok")
        self.assertIn("Arsika Rollout Manager", self.live("index.php", docroot))

    def test_09_an_old_data_folder_in_the_website_folder_gets_a_hidden_name(self):
        docroot = os.path.join(self.tmp, "home3", "public_html")
        old = os.path.join(docroot, ".rollout-agent-shop3")
        os.makedirs(os.path.join(old, "releases"))
        with open(os.path.join(old, "state.php"), "w", encoding="utf-8") as f:
            f.write("<?php exit; ?>\n{}")
        self.call("POST", "/v1/sites", {"preset": "hosting-static", "id": "shop3", "source": "upload", "repo": ""})
        agent = self.call("POST", "/v1/sites/shop3/agent-file", {"dashboard": "https://dash.example.com"})
        with open(os.path.join(docroot, agent["filename"]), "w", encoding="utf-8", newline="\n") as f:
            f.write(agent["content"])
        proc, port = php_server(docroot)
        self.extra_php.append(proc)
        key = re.search(r"const RA_PAIRING_KEY = '([0-9a-f]{64})';", agent["content"]).group(1)
        AgentClient("http://127.0.0.1:%d/%s" % (port, agent["filename"]), key).call("hello")
        hidden = [n for n in os.listdir(docroot) if n.startswith(".rollout-agent-shop3")]
        self.assertEqual(len(hidden), 1)
        self.assertRegex(hidden[0], r"^\.rollout-agent-shop3-[0-9a-f]{16}$")                    # not guessable on nginx
        self.assertTrue(os.path.isdir(os.path.join(docroot, hidden[0], "releases")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
