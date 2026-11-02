"""Runner settings: /etc/arsika-rollout-manager/runner.json (root only, 0600). Created by deploy/install.sh.

    listen, port      address of the API (always a loopback address)
    secret            shared HMAC key; the same value is RUNNER_SECRET in the dashboard's .env
    state_dir         SQLite database and job logs
    backup_dir        site backups (database dumps and data archives)
    github_api        GitHub REST API base URL (changed only by the tests)
    github_token      read-only token for private repositories (set from the dashboard, never shown again)
    shell             bash used for the site hooks
    site_roots        folders that may contain registered sites (a site path must be inside one of them)
"""
import json
import os
import secrets

from .util import POSIX, ensure_dir, write_private

DEFAULT_PATH = "/etc/arsika-rollout-manager/runner.json"
DEFAULTS = {
    "listen": "127.0.0.1",
    "port": 8790,
    "secret": "",
    "state_dir": "/var/lib/arsika-rollout-manager",
    "backup_dir": "/www/backup/arsika-rollout-manager",
    "github_api": "https://api.github.com",
    "github_token": "",
    "shell": "/bin/bash",
    "hook_timeout_seconds": 3600,
    "site_roots": ["/www/wwwroot"],
    "health_attempts": 10,
    "health_delay": 3,
}


class Config(object):
    def __init__(self, path=None):
        self.path = path or os.environ.get("ARSIKA_ROLLOUT_MANAGER_CONFIG") or DEFAULT_PATH
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

    def state_path(self, *parts):
        return os.path.join(self.values["state_dir"], *parts)

    def prepare_dirs(self):
        for path in (self.values["state_dir"], self.state_path("logs"), self.state_path("downloads"), self.values["backup_dir"]):
            ensure_dir(path, 0o700)
            if POSIX:
                os.chmod(path, 0o700)
