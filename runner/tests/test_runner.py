"""Integration test of the runner with a fake GitHub API and a fake web site (no root, no systemd, no MySQL needed).

    python runner/tests/test_runner.py            (Linux, or Windows with symlink rights; bash in PATH or ROLLOUT_TEST_SHELL)

Covers: signed API, site validation, one read-only GitHub token per site (write-only, used for checks and downloads,
replaced, removed, deleted with the site, moved over from the single token of 1.0.0), backup before an adoption, adoption
of an existing installation (with aaPanel's .user.ini in the document root), deployment, automatic rollback after a failed
health check, manual rollback, backups and restore of shared files, release pruning, unsafe archives."""
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer, SimpleHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arsika_rollout_manager.backup import Backups  # noqa: E402
from arsika_rollout_manager.client import Client  # noqa: E402
from arsika_rollout_manager.config import Config  # noqa: E402
from arsika_rollout_manager.deploy import Deployer, DeployError, merge_user_ini  # noqa: E402
from arsika_rollout_manager.server import Api, serve  # noqa: E402
from arsika_rollout_manager.store import Store  # noqa: E402
from arsika_rollout_manager.worker import Worker  # noqa: E402

SHELL = os.environ.get("ROLLOUT_TEST_SHELL") or shutil.which("bash") or "/bin/bash"
SITE_TOKEN = "github_pat_" + "s" * 40                    # the demo site's own (fake) read-only token


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


class FakeGitHub(object):
    """/repos/o/demo/releases, /releases/tags/<tag>, /assets/<id> (redirect to /storage/<id>, which must come without token)."""

    def __init__(self):
        self.assets = {}
        self.releases = []
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
                if path == "/repos/o/demo/releases":
                    return self.send(200, json.dumps(github.releases).encode())
                if path.startswith("/repos/o/demo/releases/tags/"):
                    tag = path.rsplit("/", 1)[1]
                    found = [r for r in github.releases if r["tag_name"] == tag]
                    return self.send(200, json.dumps(found[0]).encode()) if found else self.send(404, b'{"message":"Not Found"}')
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

    def publish(self, version, files, digest=True):
        data = make_zip(files)
        asset_id = len(self.assets) + 1
        self.assets[asset_id] = data
        self.releases.insert(0, {
            "tag_name": "v" + version, "name": "v" + version, "draft": False, "prerelease": False, "body": "notes " + version,
            "published_at": "2026-10-31T00:00:00Z", "html_url": "",
            "assets": [{"id": asset_id, "name": "demo_v%s.zip" % version, "size": len(data),
                        "url": "http://127.0.0.1:%d/assets/%d" % (self.port, asset_id),
                        "digest": ("sha256:" + hashlib.sha256(data).hexdigest()) if digest else None}]})


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

        # fake web server whose document root is the site's "public" link (follows current/ on every request)
        handler = partial(SimpleHTTPRequestHandler, directory=os.path.join(cls.site_dir, "public"))
        handler.log_message = lambda *a: None
        SimpleHTTPRequestHandler.log_message = lambda *a: None
        cls.web = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.web.serve_forever, daemon=True).start()

        config_path = os.path.join(cls.tmp, "etc", "runner.json")
        os.makedirs(os.path.dirname(config_path))
        cls.config = Config.create(config_path, state_dir=os.path.join(cls.tmp, "state"), backup_dir=os.path.join(cls.tmp, "backup"),
                                   github_api="http://127.0.0.1:%d" % cls.github.port,
                                   site_roots=[cls.www], shell=SHELL, port=0, health_attempts=3, health_delay=0.3)
        cls.config.prepare_dirs()
        store = Store(cls.config.state_path("runner.db"))
        backups = Backups(cls.config, store)
        cls.deployer = Deployer(cls.config, store, backups)
        cls.worker = Worker(cls.config, store, cls.deployer)
        cls.worker.start()
        cls.api = Api(cls.config, store, cls.deployer, cls.worker)
        cls.server = serve(cls.api)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.client = Client("http://127.0.0.1:%d" % cls.server.server_address[1], cls.config["secret"])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.worker.stopping = True
        cls.worker.wake.set()
        cls.web.shutdown()
        cls.github.server.shutdown()
        time.sleep(0.3)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ---------- helpers ----------
    def call(self, method, path, data=None, expect=200):
        status, doc = self.client.request(method, path, data)
        self.assertEqual(status, expect, json.dumps(doc)[:500])
        return doc.get("data", doc)

    def job(self, job_type, version=None, params=None, expect="succeeded"):
        job = self.call("POST", "/v1/sites/demo/jobs", {"type": job_type, "version": version, "params": params or {}, "actor": "test"})
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
