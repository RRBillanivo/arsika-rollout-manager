"""Backups of a site's data: the MySQL database (mysqldump, gzip) and the shared files (.env, storage/ ...).

    <backup_dir>/<site>/<YYYYmmdd-HHMMSS>_<version>/
        manifest.json  database.sql.gz  files.tar.gz

Restore replaces the database and the shared files; the replaced files are kept in shared/.replaced-<stamp>/ until
the site answers its health check again."""
import gzip
import json
import os
import shutil
import subprocess
import tarfile
import tempfile

from .sites import layout
from .util import POSIX, ensure_dir, exists, human_size, parse_env, remove, sha256_file, stamp, now


class BackupError(Exception):
    pass


def _find(binary):
    for candidate in (shutil.which(binary), "/www/server/mysql/bin/" + binary, "/usr/bin/" + binary):
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def _option_value(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


class Backups(object):
    def __init__(self, config, store):
        self.config = config
        self.store = store

    def site_dir(self, site_id):
        return os.path.join(self.config["backup_dir"], site_id)

    # ---------- database settings from the site's .env ----------
    def database(self, site, base=None):
        """MySQL settings from <base or shared/>/<backup_env_file>, or None (no database, or not MySQL)."""
        if not site.get("backup_env_file"):
            return None
        path = os.path.join(base or layout(site)["shared"], site["backup_env_file"])
        if not os.path.isfile(path):
            raise BackupError("environment file not found: " + path)
        with open(path, encoding="utf-8") as f:
            env = parse_env(f.read())
        if env.get("DB_CONNECTION", "mysql") != "mysql" or not env.get("DB_DATABASE"):
            return None
        return {"host": env.get("DB_HOST", "localhost"), "port": env.get("DB_PORT", "3306"), "database": env["DB_DATABASE"],
                "user": env.get("DB_USERNAME", ""), "password": env.get("DB_PASSWORD", "")}

    def _defaults_file(self, db):
        fd, path = tempfile.mkstemp(prefix="rollout-my-", suffix=".cnf", dir=self.config.state_path())
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("[client]\nhost=%s\nport=%s\nuser=%s\npassword=%s\n" % (
                _option_value(db["host"]), _option_value(db["port"]), _option_value(db["user"]), _option_value(db["password"])))
        if POSIX:
            os.chmod(path, 0o600)
        return path

    # ---------- create ----------
    def create(self, site, version, reason, log, job_id=None, base=None):
        """Back up the database and the shared files. ``base`` = folder holding the backup paths (default shared/;
        the site folder itself before an adoption). -> backup id"""
        base = base or layout(site)["shared"]
        has_db_settings = self.database(site, base)
        paths = [p for p in site.get("backup_paths", []) if exists(os.path.join(base, p.rstrip("/")))]
        if not has_db_settings and not paths:
            log.line("nothing to back up for this site (no database settings and no backup paths)")
            return None
        base_id = stamp() + ("_" + version if version else "")
        backup_id, n = base_id, 1
        while exists(os.path.join(self.site_dir(site["id"]), backup_id)):      # two backups within one second
            n += 1
            backup_id = "%s-%d" % (base_id, n)
        target = os.path.join(self.site_dir(site["id"]), backup_id)
        ensure_dir(target, 0o700)
        log.step("Backup %s (%s)" % (backup_id, reason))
        manifest = {"site": site["id"], "version": version, "created_at": now(), "reason": reason}
        try:
            if has_db_settings:
                manifest["database"] = self._dump(has_db_settings, os.path.join(target, "database.sql.gz"), log)
            if paths:
                manifest["files"] = self._archive(site, base, paths, os.path.join(target, "files.tar.gz"), log)
            with open(os.path.join(target, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2)
        except Exception:
            remove(target)
            raise
        size = sum(os.path.getsize(os.path.join(target, n)) for n in os.listdir(target))
        self.store.save_backup(site["id"], backup_id, version=version, created_at=manifest["created_at"], size_bytes=size,
                               has_database="database" in manifest, has_files="files" in manifest, reason=reason, job_id=job_id)
        log.line("backup saved: %s (%s)" % (target, human_size(size)))
        self.prune(site, log)
        return backup_id

    def _dump(self, db, target, log):
        mysqldump = _find("mysqldump")
        if not mysqldump:
            raise BackupError("mysqldump not found (aaPanel: /www/server/mysql/bin/mysqldump)")
        defaults = self._defaults_file(db)
        log.line("dumping database %s with %s" % (db["database"], mysqldump))
        try:
            proc = subprocess.Popen([mysqldump, "--defaults-extra-file=" + defaults, "--single-transaction", "--quick", "--routines",
                                     "--triggers", "--no-tablespaces", "--default-character-set=utf8mb4", db["database"]],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            with gzip.open(target, "wb", compresslevel=6) as out:
                for block in iter(lambda: proc.stdout.read(1 << 20), b""):
                    out.write(block)
            err = proc.stderr.read().decode("utf-8", "replace").strip()
            if proc.wait() != 0:
                raise BackupError("mysqldump failed: " + (err.splitlines()[-1] if err else "exit code %d" % proc.returncode))
        finally:
            os.unlink(defaults)
        return {"name": db["database"], "file": "database.sql.gz", "size": os.path.getsize(target), "sha256": sha256_file(target)}

    def _archive(self, site, base, paths, target, log):
        excluded = [e.rstrip("/") for e in site.get("backup_exclude", [])]

        def keep(info):
            name = info.name
            if any(name == e or name.startswith(e + "/") for e in excluded):
                return None
            return info

        log.line("archiving " + ", ".join(paths))
        with tarfile.open(target, "w:gz", compresslevel=6) as tar:
            for path in paths:
                tar.add(os.path.join(base, path.rstrip("/")), arcname=path.rstrip("/"), filter=keep)
        return {"paths": [p.rstrip("/") for p in paths], "file": "files.tar.gz", "size": os.path.getsize(target),
                "sha256": sha256_file(target)}

    # ---------- restore ----------
    def manifest(self, site_id, backup_id):
        path = os.path.join(self.site_dir(site_id), backup_id, "manifest.json")
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            raise BackupError("backup %s not found or damaged" % backup_id)

    def restore(self, site, backup_id, log):
        """Put the database and the shared files of a backup back. -> folder with the replaced files (or None)"""
        folder = os.path.join(self.site_dir(site["id"]), backup_id)
        manifest = self.manifest(site["id"], backup_id)
        for part in ("database", "files"):
            if part in manifest and sha256_file(os.path.join(folder, manifest[part]["file"])) != manifest[part]["sha256"]:
                raise BackupError("%s of backup %s is damaged (checksum mismatch)" % (manifest[part]["file"], backup_id))
        replaced = None
        if "files" in manifest:
            replaced = self._restore_files(site, os.path.join(folder, "files.tar.gz"), manifest["files"]["paths"], log)
        if "database" in manifest:
            self._restore_database(site, os.path.join(folder, "database.sql.gz"), log)
        return replaced

    def _restore_files(self, site, archive, paths, log):
        shared = layout(site)["shared"]
        tag = stamp()
        staging = os.path.join(shared, ".restore-" + tag)
        replaced = os.path.join(shared, ".replaced-" + tag)
        log.step("Restore files: " + ", ".join(paths))
        ensure_dir(staging, 0o700)
        with tarfile.open(archive, "r:gz") as tar:
            members = []
            for m in tar.getmembers():
                name = m.name
                if name.startswith("/") or ".." in name.split("/") or (m.islnk() or m.issym()) and (
                        m.linkname.startswith("/") or ".." in m.linkname.split("/")):
                    raise BackupError("unsafe path in archive: " + name)
                members.append(m)
            tar.extractall(staging, members=members)
        ensure_dir(replaced, 0o700)
        for path in paths:
            current, restored = os.path.join(shared, path), os.path.join(staging, path)
            if exists(current):
                ensure_dir(os.path.dirname(os.path.join(replaced, path)))
                os.rename(current, os.path.join(replaced, path))
            if exists(restored):
                ensure_dir(os.path.dirname(current))
                os.rename(restored, current)
        remove(staging)
        log.line("previous files kept in " + replaced)
        return replaced

    def _restore_database(self, site, dump, log):
        db = self.database(site)
        if not db:
            raise BackupError("the site has no MySQL settings (backup_env_file) to restore the database into")
        mysql = _find("mysql")
        if not mysql:
            raise BackupError("mysql client not found (aaPanel: /www/server/mysql/bin/mysql)")
        log.step("Restore database " + db["database"])
        defaults = self._defaults_file(db)
        try:
            proc = subprocess.Popen([mysql, "--defaults-extra-file=" + defaults, "--default-character-set=utf8mb4", db["database"]],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            with gzip.open(dump, "rb") as data:
                for block in iter(lambda: data.read(1 << 20), b""):
                    proc.stdin.write(block)
            proc.stdin.close()
            out = proc.stdout.read().decode("utf-8", "replace").strip()
            if proc.wait() != 0:
                raise BackupError("mysql failed: " + (out.splitlines()[-1] if out else "exit code %d" % proc.returncode))
        finally:
            os.unlink(defaults)
        log.line("database restored")

    # ---------- housekeeping ----------
    def delete(self, site_id, backup_id):
        folder = os.path.join(self.site_dir(site_id), backup_id)
        if exists(folder):
            remove(folder)
        self.store.delete_backup(site_id, backup_id)

    def prune(self, site, log):
        rows = self.store.backups(site["id"])
        for row in rows[int(site.get("backup_keep", 5)):]:
            self.delete(site["id"], row["id"])
            log.line("old backup removed: " + row["id"])
