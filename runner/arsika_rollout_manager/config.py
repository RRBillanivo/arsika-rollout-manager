"""Runner settings: /etc/arsika-rollout-manager/runner.json (root only, 0600). Created by deploy/install.sh.

    listen, port      address of the API (always a loopback address)
    secret            shared HMAC key; the same value is RUNNER_SECRET in the dashboard's .env
    state_dir         SQLite database and job logs
    backup_dir        site backups (database dumps and data archives)
    github_api        GitHub REST API base URL (changed only by the tests)
    site_tokens       read-only GitHub token of each site, by site id (set from the dashboard, never shown again)
    shell             bash used for the site hooks
    site_roots        folders that may contain registered sites (a site path must be inside one of them)
"""
import json
import os
import secrets
import threading

from .util import POSIX, ensure_dir, write_private

DEFAULT_PATH = "/etc/arsika-rollout-manager/runner.json"
DEFAULTS = {
    "listen": "127.0.0.1",
    "port": 8790,
    "secret": "",
    "state_dir": "/var/lib/arsika-rollout-manager",
    "backup_dir": "/www/backup/arsika-rollout-manager",
    "github_api": "https://api.github.com",
    "site_tokens": {},
    "shell": "/bin/bash",
    "hook_timeout_seconds": 3600,
    "site_roots": ["/www/wwwroot"],
    "health_attempts": 10,
    "health_delay": 3,
}


class Config(object):
    def __init__(self, path=None):
        self.path = path or os.environ.get("ARSIKA_ROLLOUT_MANAGER_CONFIG") or DEFAULT_PATH
        self.lock = threading.Lock()
        self.values = dict(DEFAULTS)
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                self.values.update(json.load(f))
        if self.values["listen"] not in ("127.0.0.1", "::1", "localhost"):
            raise ValueError("listen must be a loopback address (127.0.0.1)")

    def __getitem__(self, key):
        return self.values[key]

    def get(self, key, default=None):
        return self.values.get(key, default)

    def update(self, **changes):
        self.values.update(changes)
        self.save()

    def save(self):
        ensure_dir(os.path.dirname(self.path) or ".", 0o700)
        write_private(self.path, json.dumps(self.values, indent=2, sort_keys=True) + "\n")

    @classmethod
    def create(cls, path=None, **overrides):
        """Write a new configuration with a random secret (an existing file is kept)."""
        config = cls(path)
        if not os.path.exists(config.path):
            config.values.update(overrides)
            if not config.values["secret"]:
                config.values["secret"] = secrets.token_hex(32)
            config.save()
        return config

    # ---------- GitHub tokens: one per site ----------
    def site_token(self, site_id):
        return (self.values.get("site_tokens") or {}).get(site_id, "")

    def set_site_token(self, site_id, token):
        """Store the GitHub token of a site; an empty token removes it."""
        with self.lock:
            tokens = dict(self.values.get("site_tokens") or {})
            if token:
                tokens[site_id] = token
            else:
                tokens.pop(site_id, None)
            self.update(site_tokens=tokens)

    def migrate_legacy_token(self, site_ids):
        """Version 1.0.0 kept one GitHub token for every site. Since 1.1.0 each site has its own: the old token is given
        to the sites that have none, then removed. -> number of sites that received it"""
        if "github_token" not in self.values:
            return 0
        with self.lock:
            legacy = self.values.pop("github_token") or ""
            tokens = dict(self.values.get("site_tokens") or {})
            given = 0
            for site_id in site_ids if legacy else []:
                if site_id not in tokens:
                    tokens[site_id] = legacy
                    given += 1
            self.values["site_tokens"] = tokens
            self.save()
        return given

    def state_path(self, *parts):
        return os.path.join(self.values["state_dir"], *parts)

    def prepare_dirs(self):
        for path in (self.values["state_dir"], self.state_path("logs"), self.state_path("downloads"), self.values["backup_dir"]):
            ensure_dir(path, 0o700)
            if POSIX:
                os.chmod(path, 0o700)
