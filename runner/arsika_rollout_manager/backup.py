"""Backups of a site's data: its database, its shared files (.env, storage/ ...) and Docker volumes.

    <backup_dir>/<site>/<YYYYmmdd-HHMMSS>_<version>/
        manifest.json  database.sql.gz  files.tar.gz  volume-<name>.tar.gz

The database is MySQL/MariaDB or PostgreSQL (backup_db: auto, mysql, postgres or none), on this server or in a container:
  - on this server: settings from the site's environment file (DB_CONNECTION, DB_HOST, DB_PORT, DB_DATABASE,
    DB_USERNAME, DB_PASSWORD; or DATABASE_URL; or the MYSQL_* / POSTGRES_* variables of a compose .env file)
  - in a container (backup_db_service): the dump runs inside it (docker compose exec, or docker exec for a container
    of another site) with the credentials of the container's own environment (MYSQL_ROOT_PASSWORD, POSTGRES_USER, ...,
    also *_FILE secrets), so no password leaves the container
Volumes (backup_volumes) are archived by a throw-away busybox container; their containers keep running during a backup
and are stopped while a volume is restored.

Restore replaces the database, the shared files and the volumes; the replaced files are kept in shared/.replaced-<stamp>/
until the site answers its health check again."""
import gzip
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
from urllib.parse import unquote, urlsplit

from . import system
from .compose import Compose, ComposeError
from .sites import layout
from .util import POSIX, ensure_dir, exists, human_size, now, parse_env, remove, sha256_file, stamp

ENGINES = {"mysql": "MySQL/MariaDB", "postgres": "PostgreSQL"}
PORTS = {"mysql": "3306", "postgres": "5432"}
SCHEMES = {"mysql": "mysql", "mysql2": "mysql", "mariadb": "mysql", "postgres": "postgres", "postgresql": "postgres",
           "pgsql": "postgres"}

# ---------- scripts run inside a database container (POSIX sh; credentials from the container's environment) ----------
_SECRET = r'''set -e
secret() { eval "f=\${$1_FILE:-}"; if [ -n "$f" ] && [ -r "$f" ]; then eval "$1=\$(cat \"\$f\")"; fi; }
'''
_MYSQL = _SECRET + r'''for v in MARIADB_ROOT_PASSWORD MYSQL_ROOT_PASSWORD MARIADB_USER MYSQL_USER MARIADB_PASSWORD MYSQL_PASSWORD MARIADB_DATABASE MYSQL_DATABASE; do secret $v; done
U=root
P="${MARIADB_ROOT_PASSWORD:-${MYSQL_ROOT_PASSWORD:-}}"
if [ -z "$P" ] && [ -z "${MARIADB_ALLOW_EMPTY_ROOT_PASSWORD:-${MYSQL_ALLOW_EMPTY_PASSWORD:-}}" ]; then
  U="${MARIADB_USER:-${MYSQL_USER:-root}}"; P="${MARIADB_PASSWORD:-${MYSQL_PASSWORD:-}}"
fi
DB="${ROLLOUT_DB:-${MARIADB_DATABASE:-${MYSQL_DATABASE:-}}}"
[ -n "$DB" ] || { echo "database name unknown: set MYSQL_DATABASE in the container or DB_DATABASE in the environment file of the site" >&2; exit 3; }
CNF=$(mktemp)
trap 'rm -f "$CNF"' EXIT
esc() { printf '%s' "$1" | sed 's/[\\"]/\\&/g'; }
printf '[client]\nuser="%s"\npassword="%s"\n' "$(esc "$U")" "$(esc "$P")" > "$CNF"
'''
_POSTGRES = _SECRET + r'''for v in POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB; do secret $v; done
U="${POSTGRES_USER:-postgres}"
DB="${ROLLOUT_DB:-${POSTGRES_DB:-$U}}"
export PGPASSWORD="${POSTGRES_PASSWORD:-}"
'''
CONTAINER_SCRIPTS = {
    ("mysql", "dump"): _MYSQL + r'''if command -v mariadb-dump >/dev/null 2>&1; then D=mariadb-dump; else D=mysqldump; fi
"$D" --defaults-extra-file="$CNF" --single-transaction --quick --routines --triggers --no-tablespaces --default-character-set=utf8mb4 "$DB"
''',
    ("mysql", "restore"): _MYSQL + r'''if command -v mariadb >/dev/null 2>&1; then C=mariadb; else C=mysql; fi
"$C" --defaults-extra-file="$CNF" --default-character-set=utf8mb4 "$DB"
''',
    ("mysql", "ready"): r'''if command -v mariadb-admin >/dev/null 2>&1; then A=mariadb-admin; else A=mysqladmin; fi
"$A" ping --silent >/dev/null 2>&1
''',
    ("postgres", "dump"): _POSTGRES + r'''pg_dump -U "$U" --no-password --clean --if-exists "$DB"
''',
    ("postgres", "restore"): _POSTGRES + r'''psql -U "$U" -d "$DB" --no-password -q -v ON_ERROR_STOP=1 --single-transaction
''',
    ("postgres", "ready"): r'''pg_isready -q
''',
}
DETECT = r'''if command -v pg_dump >/dev/null 2>&1; then echo postgres
elif command -v mariadb-dump >/dev/null 2>&1 || command -v mysqldump >/dev/null 2>&1; then echo mysql
else echo none; fi
'''


class BackupError(Exception):
    pass


def _find(*names):
    for name in names:
        for candidate in (shutil.which(name), "/www/server/mysql/bin/" + name, "/www/server/pgsql/bin/" + name, "/usr/bin/" + name,
                          "/usr/local/bin/" + name):
            if candidate and os.path.isfile(candidate):
                return candidate
    return None


def _option_value(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _first(env, *keys):
    for key in keys:
        if env.get(key):
            return env[key]
    return ""


def _last_line(data, code):
    lines = data.decode("utf-8", "replace").strip().splitlines() if data else []
    return lines[-1] if lines else "exit code %d" % code


def settings_from_env(env):
    """Database settings in an environment file -> dict(engine, host, port, name, user, password) or None.
    engine is None for a database this module cannot dump (SQLite: back its file up with the backup paths)."""
    url = env.get("DATABASE_URL", "")
    if "://" in url:
        parts = urlsplit(url)
        engine = SCHEMES.get(parts.scheme.split("+")[0].lower())
        name = parts.path.strip("/").split("/")[0]
        if engine and name:
            return {"engine": engine, "host": parts.hostname or "localhost", "port": str(parts.port or PORTS[engine]),
                    "name": unquote(name), "user": unquote(parts.username or ""), "password": unquote(parts.password or "")}
    name = _first(env, "DB_DATABASE", "DB_NAME", "MYSQL_DATABASE", "MARIADB_DATABASE", "POSTGRES_DB")
    if not name:
        return None
    connection = env.get("DB_CONNECTION", "").strip().lower()
    if connection in ("mysql", "mariadb"):
        engine = "mysql"
    elif connection in ("pgsql", "postgres", "postgresql"):
        engine = "postgres"
    elif connection:
        engine = None
    elif _first(env, "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD") and not _first(env, "MYSQL_DATABASE", "MARIADB_DATABASE"):
        engine = "postgres"
    else:
        engine = "mysql"
    return {"engine": engine, "name": name,
            "host": env.get("DB_HOST") or ("127.0.0.1" if engine == "postgres" else "localhost"),
            "port": env.get("DB_PORT") or PORTS.get(engine, ""),
            "user": _first(env, "DB_USERNAME", "DB_USER", "MYSQL_USER", "MARIADB_USER", "POSTGRES_USER") or ("postgres" if engine == "postgres" else ""),
            "password": _first(env, "DB_PASSWORD", "DB_PASS", "MYSQL_PASSWORD", "MARIADB_PASSWORD", "POSTGRES_PASSWORD")}


class Backups(object):
    def __init__(self, config, store, compose=None):
        self.config = config
        self.store = store
        self.compose = compose or Compose(config)

    def site_dir(self, site_id):
        return os.path.join(self.config["backup_dir"], site_id)

    # ---------- where the data is ----------
    def environment(self, site, base=None):
        """Variables of the site's environment file (backup_env_file) in ``base`` (default shared/); {} without one."""
        if not site.get("backup_env_file"):
            return {}
        path = os.path.join(base or layout(site)["shared"], site["backup_env_file"])
        if not os.path.isfile(path):
            raise BackupError("environment file not found: " + path)
        with open(path, encoding="utf-8") as f:
            return parse_env(f.read())

    def database(self, site, base=None):
        """How the site's database is reached -> dict (where: host or container) or None (nothing to dump)."""
        engine = site.get("backup_db") or "auto"
        if engine == "none":
            return None
        settings = settings_from_env(self.environment(site, base))
        if site.get("backup_db_service"):
            return {"where": "container", "service": site["backup_db_service"], "engine": None if engine == "auto" else engine,
                    "name": (settings or {}).get("name", "")}
        if not settings:
            if engine != "auto":
                raise BackupError("no %s settings to back up: set the environment file of the site (DB_DATABASE ...) or the "
                                  "database service" % ENGINES[engine])
            return None
        if engine != "auto":
            settings["engine"] = engine
        if not settings["engine"]:
            return None
        settings["where"] = "host"
        return settings

    @staticmethod
    def compose_dir(site, base=None):
        """Folder whose compose files describe the running containers -> (folder, use the site's project name).
        Before an adoption that is the site folder itself, with the project name compose gives it."""
        current = layout(site)["current"]
        if (base and os.path.normpath(base) == os.path.normpath(site["path"])) or not os.path.islink(current):
            return site["path"], False
        return os.path.realpath(current), True

    def _container_argv(self, site, db, script, base=None):
        env = {"ROLLOUT_DB": db["name"]} if db.get("name") else {}
        if site.get("deploy_mode") == "compose":
            directory, project = self.compose_dir(site, base)
            return self.compose.exec_argv(site, directory, db["service"], script, env, project)
        argv = self.compose.docker() + ["exec", "-i"]
        for key, value in sorted(env.items()):
            argv += ["-e", "%s=%s" % (key, value)]
        return argv + [db["service"], "sh", "-c", script]

    def _container_engine(self, site, db, base=None):
        if db.get("engine"):
            return db["engine"]
        try:
            proc = subprocess.run(self._container_argv(site, db, DETECT, base), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  stdin=subprocess.DEVNULL, timeout=120)
        except (OSError, subprocess.SubprocessError, ComposeError) as e:
            raise BackupError("cannot run a command in the database container %s: %s" % (db["service"], e))
        if proc.returncode != 0:
            raise BackupError("cannot run a command in the database container %s: %s" % (db["service"], _last_line(proc.stderr, proc.returncode)))
        found = (proc.stdout.decode("utf-8", "replace").split() or ["none"])[-1]
        if found not in ENGINES:
            raise BackupError("the container %s has neither mysqldump/mariadb-dump nor pg_dump: set Database service to the "
                              "container of the database" % db["service"])
        return found

    # ---------- create ----------
    def create(self, site, version, reason, log, job_id=None, base=None):
        """Back up the database, the shared files and the volumes. ``base`` = folder holding the backup paths (default
        shared/; the site folder itself before an adoption). -> backup id"""
        given = base
        base = base or layout(site)["shared"]
        db = self.database(site, base)
        paths = [p for p in site.get("backup_paths", []) if exists(os.path.join(base, p.rstrip("/")))]
        volumes = list(site.get("backup_volumes") or [])
        if not db and not paths and not volumes:
            log.line("nothing to back up for this site (no database, no backup paths, no volumes)")
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
            if db:
                manifest["database"] = self._dump(site, db, os.path.join(target, "database.sql.gz"), log, given)
            if paths:
                manifest["files"] = self._archive(site, base, paths, os.path.join(target, "files.tar.gz"), log)
            if volumes:
                manifest["volumes"] = self._archive_volumes(site, volumes, target, log, given)
            with open(os.path.join(target, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2)
        except Exception:
            remove(target)
            raise
        size = sum(os.path.getsize(os.path.join(target, n)) for n in os.listdir(target))
        self.store.save_backup(site["id"], backup_id, version=version, created_at=manifest["created_at"], size_bytes=size,
                               has_database="database" in manifest, has_files="files" in manifest,
                               volumes=len(manifest.get("volumes", [])), reason=reason, job_id=job_id)
        log.line("backup saved: %s (%s)" % (target, human_size(size)))
        self.prune(site, log)
        return backup_id

    def _dump(self, site, db, target, log, base=None):
        """Dump the database into a gzip file -> manifest entry."""
        cleanup, env = None, None
        if db["where"] == "container":
            engine = self._container_engine(site, db, base)
            argv = self._container_argv(site, db, CONTAINER_SCRIPTS[(engine, "dump")], base)
            log.line("dumping the %s database%s inside the container %s" % (ENGINES[engine], " " + db["name"] if db["name"] else "",
                                                                         db["service"]))
        elif db["engine"] == "postgres":
            engine = "postgres"
            pg_dump = _find("pg_dump")
            if not pg_dump:
                raise BackupError("pg_dump not found (Debian/Ubuntu: apt install postgresql-client)")
            argv = [pg_dump, "-h", db["host"], "-p", str(db["port"]), "-U", db["user"], "--no-password", "--clean", "--if-exists",
                    db["name"]]
            env = dict(os.environ, PGPASSWORD=db["password"])
            log.line("dumping database %s with %s" % (db["name"], pg_dump))
        else:
            engine = "mysql"
            mysqldump = _find("mysqldump", "mariadb-dump")
            if not mysqldump:
                raise BackupError("mysqldump not found (aaPanel: /www/server/mysql/bin/mysqldump; Debian/Ubuntu: apt install "
                                  "mariadb-client)")
            cleanup = self._defaults_file(db)
            argv = [mysqldump, "--defaults-extra-file=" + cleanup, "--single-transaction", "--quick", "--routines", "--triggers",
                    "--no-tablespaces", "--default-character-set=utf8mb4", db["name"]]
            log.line("dumping database %s with %s" % (db["name"], mysqldump))
        try:
            self._stream_out(argv, target, env, db)
        finally:
            if cleanup:
                os.unlink(cleanup)
        return {"engine": engine, "where": db["where"], "service": db.get("service", ""), "name": db["name"],
                "file": "database.sql.gz", "size": os.path.getsize(target), "sha256": sha256_file(target)}

    def _stream_out(self, argv, target, env, db):
        try:
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, env=env)
        except OSError as e:
            raise BackupError("cannot start %s: %s" % (os.path.basename(argv[0]), e))
        errors = tempfile.TemporaryFile()
        reader = _drain(proc.stderr, errors)
        with gzip.open(target, "wb", compresslevel=6) as out:
            for block in iter(lambda: proc.stdout.read(1 << 20), b""):
                out.write(block)
        code = proc.wait()
        reader.join()
        errors.seek(0)
        if code != 0:
            raise BackupError("database dump failed: " + self._hint(_last_line(errors.read(), code), db))

    @staticmethod
    def _hint(message, db):
        if db.get("where") == "host" and db.get("host") not in ("localhost", "127.0.0.1", "::1", "") and "." not in db.get("host", "."):
            message += " (does the database run in a container? set Database service)"
        return message

    def _defaults_file(self, db):
        fd, path = tempfile.mkstemp(prefix="rollout-my-", suffix=".cnf", dir=self.config.state_path())
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("[client]\nhost=%s\nport=%s\nuser=%s\npassword=%s\n" % (
                _option_value(db["host"]), _option_value(db["port"]), _option_value(db["user"]), _option_value(db["password"])))
        if POSIX:
            os.chmod(path, 0o600)
        return path

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

    def volume_names(self, site, names, base=None):
        """Volumes of the settings -> Docker volume names (a compose site's volume "data" is "<project>_data")."""
        declared = {}
        if site.get("deploy_mode") == "compose":
            directory, project = self.compose_dir(site, base)
            declared = self.compose.volumes(site, directory, "", project)
        return {name: declared.get(name, name) for name in names}

    def _volume_exists(self, volume):
        proc = subprocess.run(self.compose.docker() + ["volume", "inspect", volume], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=60)
        return proc.returncode == 0

    def _archive_volumes(self, site, names, target, log, base=None):
        entries = []
        for name, volume in sorted(self.volume_names(site, names, base).items()):
            if not self._volume_exists(volume):
                raise BackupError("volume %s does not exist (docker volume ls)" % volume)
            archive = "volume-%s.tar.gz" % name
            log.line("archiving volume %s (%s)" % (name, volume))
            system.run(self.compose.volume_argv(volume, target, "tar czf /backup/%s -C /volume ." % archive), log,
                       timeout=self.config["hook_timeout_seconds"])
            path = os.path.join(target, archive)
            entries.append({"name": name, "volume": volume, "file": archive, "size": os.path.getsize(path), "sha256": sha256_file(path)})
        return entries

    # ---------- restore ----------
    def manifest(self, site_id, backup_id):
        path = os.path.join(self.site_dir(site_id), backup_id, "manifest.json")
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            raise BackupError("backup %s not found or damaged" % backup_id)

    def restore(self, site, backup_id, log):
        """Put the shared files, the volumes and the database of a backup back. -> folder with the replaced files (or None)"""
        folder = os.path.join(self.site_dir(site["id"]), backup_id)
        manifest = self.manifest(site["id"], backup_id)
        parts = [manifest[p] for p in ("database", "files") if p in manifest] + list(manifest.get("volumes", []))
        for part in parts:
            if sha256_file(os.path.join(folder, part["file"])) != part["sha256"]:
                raise BackupError("%s of backup %s is damaged (checksum mismatch)" % (part["file"], backup_id))
        replaced = None
        if "files" in manifest:
            replaced = self._restore_files(site, os.path.join(folder, "files.tar.gz"), manifest["files"]["paths"], log)
        if manifest.get("volumes"):
            self._restore_volumes(folder, manifest["volumes"], log)
        if "database" in manifest:
            self._restore_database(site, os.path.join(folder, "database.sql.gz"), manifest["database"], log)
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

    def _restore_volumes(self, folder, entries, log):
        """The containers that use the volumes are stopped while their content is replaced, then started again."""
        docker = self.compose.docker()
        log.step("Restore volumes: " + ", ".join(e["name"] for e in entries))
        users = []
        for entry in entries:
            proc = subprocess.run(docker + ["ps", "-q", "--filter", "volume=" + entry["volume"]], stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=60)
            users += [c for c in proc.stdout.decode("utf-8", "replace").split() if c not in users]
        if users:
            system.run(docker + ["stop"] + users, log, timeout=300)
        try:
            for entry in entries:
                script = "rm -rf /volume/..?* /volume/.[!.]* /volume/* && tar xzf /backup/%s -C /volume" % entry["file"]
                system.run(self.compose.volume_argv(entry["volume"], folder, script, restore=True), log,
                           timeout=self.config["hook_timeout_seconds"])
        finally:
            if users:
                system.run(docker + ["start"] + users, log, timeout=300, check=False)

    def _restore_database(self, site, dump, info, log):
        engine = info.get("engine", "mysql")                         # backups of 1.0/1.1 hold MySQL dumps
        db = self.database(site)
        if not db:
            raise BackupError("the site has no database settings to restore the database into")
        cleanup, env = None, None
        if db["where"] == "container":
            found = self._container_engine(site, dict(db, engine=db["engine"] or None))
            if found != engine:
                raise BackupError("the backup holds a %s dump, the container %s runs %s" % (ENGINES[engine], db["service"], ENGINES[found]))
            self._wait_ready(site, db, engine, log)
            argv = self._container_argv(site, db, CONTAINER_SCRIPTS[(engine, "restore")])
            log.step("Restore the %s database inside the container %s" % (ENGINES[engine], db["service"]))
        else:
            if db["engine"] != engine:
                raise BackupError("the backup holds a %s dump, the site's database is %s" % (ENGINES[engine], ENGINES[db["engine"]]))
            log.step("Restore database " + db["name"])
            if engine == "postgres":
                psql = _find("psql")
                if not psql:
                    raise BackupError("psql not found (Debian/Ubuntu: apt install postgresql-client)")
                argv = [psql, "-h", db["host"], "-p", str(db["port"]), "-U", db["user"], "-d", db["name"], "--no-password", "-q",
                        "-v", "ON_ERROR_STOP=1", "--single-transaction"]
                env = dict(os.environ, PGPASSWORD=db["password"])
            else:
                mysql = _find("mysql", "mariadb")
                if not mysql:
                    raise BackupError("mysql client not found (aaPanel: /www/server/mysql/bin/mysql; Debian/Ubuntu: apt install "
                                      "mariadb-client)")
                cleanup = self._defaults_file(db)
                argv = [mysql, "--defaults-extra-file=" + cleanup, "--default-character-set=utf8mb4", db["name"]]
        try:
            self._stream_in(argv, dump, env)
        finally:
            if cleanup:
                os.unlink(cleanup)
        log.line("database restored")

    def _wait_ready(self, site, db, engine, log, attempts=30, delay=2.0):
        argv = self._container_argv(site, db, CONTAINER_SCRIPTS[(engine, "ready")])
        for attempt in range(attempts):
            try:
                if subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                  timeout=60).returncode == 0:
                    return
            except (OSError, subprocess.SubprocessError):
                pass
            if attempt == 0:
                log.line("waiting for the database in %s to accept connections" % db["service"])
            time.sleep(delay)
        raise BackupError("the database in the container %s does not accept connections" % db["service"])

    @staticmethod
    def _stream_in(argv, dump, env):
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        except OSError as e:
            raise BackupError("cannot start %s: %s" % (os.path.basename(argv[0]), e))
        output = tempfile.TemporaryFile()
        reader = _drain(proc.stdout, output)
        try:
            with gzip.open(dump, "rb") as data:
                for block in iter(lambda: data.read(1 << 20), b""):
                    proc.stdin.write(block)
        except BrokenPipeError:
            pass                                                     # the client stopped early: its exit code tells why
        finally:
            try:
                proc.stdin.close()
            except BrokenPipeError:
                pass
        code = proc.wait()
        reader.join()
        output.seek(0)
        if code != 0:
            raise BackupError("database restore failed: " + _last_line(output.read(), code))

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


def _drain(stream, sink):
    """Copy a pipe into a file in a thread (a full stderr pipe would otherwise block the dump)."""
    import threading

    def copy():
        for block in iter(lambda: stream.read(1 << 16), b""):
            sink.write(block)
        stream.close()

    thread = threading.Thread(target=copy, daemon=True)
    thread.start()
    return thread
