"""Runner state in SQLite (state_dir/runner.db): registered sites, installed releases, jobs and backups.
One connection shared by the API threads and the worker, serialised by a lock."""
import json
import sqlite3
import threading

from .sites import DEFAULTS
from .util import now

SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
    id TEXT PRIMARY KEY,
    config TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS releases (
    site_id TEXT NOT NULL,
    version TEXT NOT NULL,
    installed_at TEXT NOT NULL,
    activated_at TEXT,
    asset TEXT,
    size_bytes INTEGER,
    sha256 TEXT,
    PRIMARY KEY (site_id, version)
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT NOT NULL,
    type TEXT NOT NULL,
    version TEXT,
    params TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    message TEXT,
    from_version TEXT,
    to_version TEXT,
    actor TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS jobs_site_index ON jobs (site_id, id);
CREATE INDEX IF NOT EXISTS jobs_status_index ON jobs (status, id);
CREATE TABLE IF NOT EXISTS backups (
    id TEXT NOT NULL,
    site_id TEXT NOT NULL,
    version TEXT,
    created_at TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    has_database INTEGER NOT NULL DEFAULT 0,
    has_files INTEGER NOT NULL DEFAULT 0,
    volumes INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    job_id INTEGER,
    PRIMARY KEY (site_id, id)
);
"""

ACTIVE = ("queued", "running")


class Store(object):
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.execute("PRAGMA busy_timeout = 5000")
        self.db.executescript(SCHEMA)
        columns = [r["name"] for r in self.db.execute("PRAGMA table_info(backups)").fetchall()]
        if "volumes" not in columns:                                 # runner 1.1.0 and older
            self.db.execute("ALTER TABLE backups ADD COLUMN volumes INTEGER NOT NULL DEFAULT 0")

    def query(self, sql, params=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql, params=()):
        with self.lock:
            return self.db.execute(sql, params)

    # ---------- sites ----------
    def sites(self):
        return [self._site(r) for r in self.query("SELECT * FROM sites ORDER BY id")]

    def site(self, site_id):
        row = self.one("SELECT * FROM sites WHERE id = ?", (site_id,))
        return self._site(row) if row else None

    def save_site(self, site):
        data = json.dumps({k: v for k, v in site.items() if k not in ("id", "created_at", "updated_at")}, sort_keys=True)
        with self.lock:
            if self.one("SELECT id FROM sites WHERE id = ?", (site["id"],)):
                self.execute("UPDATE sites SET config = ?, updated_at = ? WHERE id = ?", (data, now(), site["id"]))
            else:
                self.execute("INSERT INTO sites (id, config, created_at, updated_at) VALUES (?, ?, ?, ?)", (site["id"], data, now(), now()))

    def delete_site(self, site_id):
        with self.lock:
            self.execute("DELETE FROM sites WHERE id = ?", (site_id,))
            self.execute("DELETE FROM releases WHERE site_id = ?", (site_id,))

    @staticmethod
    def _site(row):
        site = dict(DEFAULTS)                                        # settings added by later versions get their default
        site.update(json.loads(row["config"]))
        site.update(id=row["id"], created_at=row["created_at"], updated_at=row["updated_at"])
        return site

    # ---------- releases ----------
    def releases(self, site_id):
        return self.query("SELECT * FROM releases WHERE site_id = ? ORDER BY installed_at DESC", (site_id,))

    def save_release(self, site_id, version, asset=None, size=None, sha256=None):
        with self.lock:
            self.execute("DELETE FROM releases WHERE site_id = ? AND version = ?", (site_id, version))
            self.execute("INSERT INTO releases (site_id, version, installed_at, asset, size_bytes, sha256) VALUES (?, ?, ?, ?, ?, ?)",
                         (site_id, version, now(), asset, size, sha256))

    def mark_activated(self, site_id, version):
        self.execute("UPDATE releases SET activated_at = ? WHERE site_id = ? AND version = ?", (now(), site_id, version))

    def forget_release(self, site_id, version):
        self.execute("DELETE FROM releases WHERE site_id = ? AND version = ?", (site_id, version))

    # ---------- jobs ----------
    def add_job(self, site_id, job_type, version=None, params=None, actor=None):
        cur = self.execute("INSERT INTO jobs (site_id, type, version, params, status, actor, created_at) VALUES (?, ?, ?, ?, 'queued', ?, ?)",
                           (site_id, job_type, version, json.dumps(params or {}), actor, now()))
        return cur.lastrowid

    def job(self, job_id):
        row = self.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if row:
            row["params"] = json.loads(row["params"] or "{}")
        return row

    def jobs(self, site_id=None, limit=50):
        sql = "SELECT * FROM jobs" + (" WHERE site_id = ?" if site_id else "") + " ORDER BY id DESC LIMIT %d" % int(limit)
        rows = self.query(sql, (site_id,) if site_id else ())
        for row in rows:
            row["params"] = json.loads(row["params"] or "{}")
        return rows

    def active_job(self, site_id=None):
        sql = "SELECT * FROM jobs WHERE status IN ('queued', 'running')" + (" AND site_id = ?" if site_id else "") + " ORDER BY id LIMIT 1"
        return self.one(sql, (site_id,) if site_id else ())

    def next_job(self):
        return self.one("SELECT id FROM jobs WHERE status = 'queued' ORDER BY id LIMIT 1")

    def update_job(self, job_id, **fields):
        if not fields:
            return
        names = sorted(fields)
        self.execute("UPDATE jobs SET " + ", ".join(n + " = ?" for n in names) + " WHERE id = ?",
                     [fields[n] for n in names] + [job_id])

    def fail_interrupted_jobs(self):
        """Jobs left 'running' by a stopped runner."""
        self.execute("UPDATE jobs SET status = 'failed', message = 'interrupted: the runner was restarted', finished_at = ? "
                     "WHERE status = 'running'", (now(),))

    def last_success(self, site_id, types=("deploy", "rollback", "adopt")):
        marks = ",".join("?" * len(types))
        return self.one("SELECT * FROM jobs WHERE site_id = ? AND status = 'succeeded' AND type IN (%s) ORDER BY id DESC LIMIT 1" % marks,
                        (site_id,) + tuple(types))

    # ---------- backups ----------
    def backups(self, site_id):
        return self.query("SELECT * FROM backups WHERE site_id = ? ORDER BY created_at DESC, id DESC", (site_id,))

    def backup(self, site_id, backup_id):
        return self.one("SELECT * FROM backups WHERE site_id = ? AND id = ?", (site_id, backup_id))

    def save_backup(self, site_id, backup_id, **fields):
        self.execute("INSERT INTO backups (id, site_id, version, created_at, size_bytes, has_database, has_files, volumes, reason, "
                     "job_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (backup_id, site_id, fields.get("version"), fields.get("created_at") or now(), fields.get("size_bytes", 0),
                      int(bool(fields.get("has_database"))), int(bool(fields.get("has_files"))), int(fields.get("volumes") or 0),
                      fields.get("reason"), fields.get("job_id")))

    def delete_backup(self, site_id, backup_id):
        self.execute("DELETE FROM backups WHERE site_id = ? AND id = ?", (site_id, backup_id))
