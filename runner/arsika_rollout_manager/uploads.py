"""Versions uploaded through the dashboard (release source "upload") instead of GitHub releases.

The dashboard sends an archive of the website (zip, rar, 7z, tar.gz ...) in pieces. A job ("upload") unpacks it on
this server, refuses unsafe archives, strips a single top folder, removes what must not go live (the site's
"upload_exclude" list: the local .env, logs, caches ...), installs the PHP dependencies with composer when a Laravel
site on a hosting comes without vendor/, and keeps the result as a normal zip. That zip is deployed exactly like the
asset of a GitHub release:

    state_dir/uploads/<site>/incoming/<id>.part, <id>.json    an upload in progress
    state_dir/uploads/<site>/<version>.zip, <version>.json    a version ready to deploy

Zip and tar archives are unpacked by Python itself; rar and 7z archives need bsdtar (package libarchive-tools),
7-Zip or unrar on this server.
"""
import fnmatch
import json
import os
import re
import secrets
import shutil
import stat
import tarfile
import time
import zipfile

from . import system
from .util import POSIX, VERSION, ensure_dir, exists, human_size, now, remove, sha256_file

CHUNK = 8 << 20                          # largest piece of an upload the runner accepts in one request
MAX_ARCHIVE = 4 << 30                    # largest archive
MAX_FILES = 300000
MAX_UNPACKED = 12 << 30
UPLOAD_ID = re.compile(r"^[0-9a-f]{16}$")
NAME = re.compile(r"^[^/\\\x00]{1,200}$")
EXTENSIONS = (".zip", ".rar", ".7z", ".tar", ".tgz", ".tbz2", ".txz", ".tar.gz", ".tar.bz2", ".tar.xz")
KEEP_NAMES = (".gitignore", ".gitkeep")  # keep the folders of the release even when their content is excluded
STALE_SECONDS = 2 * 86400
EXTENDED = "\\\\?\\"                     # Windows prefix of an extended-length path


class UploadError(Exception):
    pass


def archive_extension(name):
    lower = name.lower()
    return next((ext for ext in sorted(EXTENSIONS, key=len, reverse=True) if lower.endswith(ext)), None)


def suggest_version(name):
    """A version name from the file name: absensi#003.rar -> 003, site-v1.2.0.zip -> 1.2.0, else the date and time."""
    ext = archive_extension(name) or ""
    base = name[:len(name) - len(ext)] if ext else name
    match = re.search(r"(?:^|[^0-9A-Za-z])v?(\d+(?:\.\d+)*)$", base)
    if match and VERSION.match(match.group(1)):
        return match.group(1)
    return time.strftime("%Y%m%d-%H%M")


def detect(path):
    """zip | rar | 7z | tar (also compressed) | None"""
    with open(path, "rb") as f:
        head = f.read(512)
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return "zip"
    if head[:7] == b"Rar!\x1a\x07\x00" or head[:8] == b"Rar!\x1a\x07\x01\x00":
        return "rar"
    if head[:6] == b"7z\xbc\xaf\x27\x1c":
        return "7z"
    try:
        if tarfile.is_tarfile(path):
            return "tar"
    except (OSError, EOFError):
        pass
    return None


def long_path(path):
    """Windows (tests): an extended-length path, so deep trees (vendor/, more than 260 characters) can be handled."""
    if os.name != "nt":
        return path
    path = os.path.abspath(path)
    return path if path.startswith(EXTENDED) else EXTENDED + path


def short_path(path):
    return path[len(EXTENDED):] if os.name == "nt" and path.startswith(EXTENDED) else path


def _safe_parts(name, original=None):
    name = name.replace("\\", "/")
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if name.startswith("/") or ".." in parts or (parts and ":" in parts[0]):
        raise UploadError("unsafe path in the archive: %s" % (original or name))
    return parts


class Limits(object):
    def __init__(self):
        self.files = 0
        self.size = 0

    def add(self, size):
        self.files += 1
        self.size += max(0, int(size or 0))
        if self.files > MAX_FILES:
            raise UploadError("the archive has more than %d files" % MAX_FILES)
        if self.size > MAX_UNPACKED:
            raise UploadError("the archive unpacks to more than %s" % human_size(MAX_UNPACKED))


def _file_mode(path, executable):
    if POSIX:
        os.chmod(path, 0o755 if executable else 0o644)


def unpack_zip(archive, dest):
    limits, skipped = Limits(), 0
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            parts = _safe_parts(info.filename)
            if not parts:
                continue
            target = os.path.join(dest, *parts)
            mode = (info.external_attr >> 16) & 0xFFFF
            if info.filename.endswith(("/", "\\")) or stat.S_ISDIR(mode):
                ensure_dir(target)
                continue
            if stat.S_ISLNK(mode):
                skipped += 1
                continue
            limits.add(info.file_size)
            ensure_dir(os.path.dirname(target))
            with z.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            _file_mode(target, mode & 0o111)
    return limits.files, skipped


def unpack_tar(archive, dest):
    limits, skipped = Limits(), 0
    with tarfile.open(archive) as t:
        for member in t:
            parts = _safe_parts(member.name)
            if not parts:
                continue
            target = os.path.join(dest, *parts)
            if member.isdir():
                ensure_dir(target)
            elif member.isfile():
                limits.add(member.size)
                ensure_dir(os.path.dirname(target))
                with t.extractfile(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
                _file_mode(target, member.mode & 0o111)
            else:
                skipped += 1                                             # links, devices
    return limits.files, skipped


def archive_tools(config):
    """Programs that unpack rar and 7z archives, best first: [(name, path)]."""
    found = []
    custom = str(config.get("bsdtar") or "").strip()
    if custom and os.path.isfile(custom):
        found.append(("bsdtar", custom))
    for name in ("bsdtar", "7zz", "7z", "unrar", "unar"):
        path = shutil.which(name)
        if path and (name, path) not in found:
            found.append((name, path))
    return found


def _tool_argv(name, path, archive, dest):
    if name == "bsdtar":
        return [path, "-x", "-o", "-f", archive, "-C", dest]
    if name in ("7zz", "7z"):
        return [path, "x", "-y", "-bd", "-o" + dest, archive]
    if name == "unrar":
        return [path, "x", "-o+", "-y", "-idq", archive, dest + os.sep]
    return [path, "-q", "-f", "-o", dest, archive]


def unpack_with_tool(config, kind, archive, dest, log):
    tools = archive_tools(config)
    if kind == "7z":
        tools = [t for t in tools if t[0] != "unrar"]
    if not tools:
        raise UploadError("%s archives are unpacked with bsdtar (package libarchive-tools), 7-Zip or unrar on the dashboard's "
                          "server, and none of them is installed: install one (e.g. apt install libarchive-tools) or upload "
                          "a .zip" % kind)
    name, path = tools[0]
    log.line("unpacking the %s archive with %s" % (kind, name))
    try:
        system.run(_tool_argv(name, path, short_path(archive), short_path(dest)), log,
                   timeout=int(config.get("hook_timeout_seconds", 3600)))
    except system.CommandError as e:
        raise UploadError("%s could not unpack the archive: %s" % (name, e))
    limits, skipped = Limits(), 0
    root = os.path.realpath(dest)
    for folder, dirs, files in os.walk(dest):
        for entry in dirs + files:
            full = os.path.join(folder, entry)
            if os.path.islink(full) or not (os.path.isdir(full) or os.path.isfile(full)):
                os.unlink(full)                                          # links and special files are not deployed
                skipped += 1
                if entry in dirs:
                    dirs.remove(entry)
            elif not os.path.realpath(full).startswith(root):
                raise UploadError("unsafe path in the archive: %s" % entry)
            elif os.path.isfile(full):
                limits.add(os.path.getsize(full))
    return limits.files, skipped


def unpack(config, archive, dest, log):
    """Unpack any supported archive into the empty folder ``dest``. -> (files, links skipped)"""
    kind = detect(archive)
    if kind is None:
        raise UploadError("this file is not an archive that can be unpacked (zip, rar, 7z, tar.gz, tar.bz2, tar.xz)")
    ensure_dir(dest, 0o700)
    try:
        if kind == "zip":
            return unpack_zip(archive, dest)
        if kind == "tar":
            return unpack_tar(archive, dest)
        return unpack_with_tool(config, kind, archive, dest, log)
    except (zipfile.BadZipFile, tarfile.TarError, EOFError) as e:
        raise UploadError("the archive is damaged: %s" % e)


def project_root(folder):
    """A single top folder (with nothing else next to it but junk of macOS) is the project itself."""
    names = [n for n in os.listdir(folder) if n not in ("__MACOSX", ".DS_Store")]
    if len(names) == 1 and os.path.isdir(os.path.join(folder, names[0])):
        return os.path.join(folder, names[0])
    return folder


def excluded(rel, is_dir, patterns):
    name = rel.rsplit("/", 1)[-1]
    if name in KEEP_NAMES:
        return False
    for raw in patterns:
        pattern = str(raw).strip()
        if not pattern or pattern.startswith("#"):
            continue
        folder_only = pattern.endswith("/")
        pattern = pattern.rstrip("/")
        if folder_only and not is_dir:
            continue
        if pattern.startswith("**/"):
            if fnmatch.fnmatchcase(name, pattern[3:]):
                return True
        elif fnmatch.fnmatchcase(rel, pattern):
            return True
    return False


def clean(root, patterns):
    """Remove the files and folders matching ``patterns`` (relative to the release). -> removed paths"""
    removed = []
    for folder, dirs, files in os.walk(root):
        rel_folder = os.path.relpath(folder, root).replace(os.sep, "/")
        rel_folder = "" if rel_folder == "." else rel_folder + "/"
        for name in list(dirs):
            if excluded(rel_folder + name, True, patterns):
                remove(os.path.join(folder, name))
                dirs.remove(name)
                removed.append(rel_folder + name + "/")
        for name in files:
            if excluded(rel_folder + name, False, patterns):
                os.unlink(os.path.join(folder, name))
                removed.append(rel_folder + name)
    return removed


def pack(root, target):
    """Zip the release folder (with its empty folders and executable bits). -> number of files"""
    count = 0
    temporary = target + ".tmp"
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        for folder, dirs, files in os.walk(root):
            dirs.sort()
            rel_folder = os.path.relpath(folder, root).replace(os.sep, "/")
            if rel_folder != ".":
                info = zipfile.ZipInfo(rel_folder + "/")
                info.external_attr = (0o40755 << 16) | 0x10
                z.writestr(info, b"")
            for name in sorted(files):
                full = os.path.join(folder, name)
                rel = name if rel_folder == "." else rel_folder + "/" + name
                info = zipfile.ZipInfo.from_file(full, rel)
                executable = POSIX and os.stat(full).st_mode & 0o111
                info.external_attr = ((0o100755 if executable else 0o100644) << 16)
                info.compress_type = zipfile.ZIP_DEFLATED
                with open(full, "rb") as src, z.open(info, "w") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
                count += 1
    os.replace(temporary, target)
    return count


def needs_composer(site, root):
    return (site.get("hosting_app") == "laravel" and os.path.isfile(os.path.join(root, "composer.json"))
            and not os.path.isfile(os.path.join(root, "vendor", "autoload.php")))


def composer_install(config, root, log):
    """Install the PHP dependencies of a Laravel site on this server (a hosting without a terminal cannot run composer):
    without dev packages, scripts and plugins (nothing of the project runs here)."""
    command = list(config.get("composer_command") or ["composer"])
    if not os.path.isabs(command[0]) and shutil.which(command[0]) is None:
        raise UploadError("the release has no vendor/ folder and composer is not installed on the dashboard's server: upload "
                          "the project with its vendor/ folder, or install composer on this server")
    if not os.path.isfile(os.path.join(root, "composer.lock")):
        log.line("note: the project has no composer.lock; composer resolves the newest allowed versions")
    log.step("Install the PHP dependencies with composer on this server (vendor/ is not in the release)")
    env = dict(os.environ, COMPOSER_ALLOW_SUPERUSER="1", COMPOSER_NO_INTERACTION="1",
               COMPOSER_HOME=config.state_path("composer"))
    ensure_dir(env["COMPOSER_HOME"], 0o700)
    try:
        system.run(command + ["install", "--no-dev", "--no-interaction", "--no-progress", "--prefer-dist", "--optimize-autoloader",
                              "--no-scripts", "--no-plugins", "--ignore-platform-reqs"], log, cwd=root, env=env,
                   timeout=int(config.get("hook_timeout_seconds", 3600)))
    except system.CommandError as e:
        raise UploadError("composer install failed: %s" % e)
    if not os.path.isfile(os.path.join(root, "vendor", "autoload.php")):
        raise UploadError("composer did not create vendor/autoload.php")


def prepare_tree(config, site, root, log):
    """Clean an unpacked release and complete it for its site. -> removed paths"""
    removed = clean(root, site.get("upload_exclude") or [])
    if removed:
        shown = ", ".join(removed[:8]) + (" (+%d more)" % (len(removed) - 8) if len(removed) > 8 else "")
        log.line("removed (not deployed): " + shown)
    if needs_composer(site, root):
        composer_install(config, root, log)
    return removed


def php_requirement(root):
    """The PHP version that composer.json of the release asks for (e.g. ^8.2), or ''."""
    try:
        with open(os.path.join(root, "composer.json"), encoding="utf-8") as f:
            return str((json.load(f).get("require") or {}).get("php") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def php_satisfies(constraint, version):
    """Rough check of a composer PHP constraint (^8.2, >=8.1, ~8.2.0, 8.2.*, alternatives with |): False only when the
    PHP version is clearly lower than every alternative allows."""
    if not constraint or not version:
        return True

    def numbers(text):
        return [int(n) for n in re.findall(r"\d+", text)[:3]] + [0, 0, 0]

    have = numbers(version)[:3]
    lowest = []
    for alternative in re.split(r"\s*\|\|?\s*", constraint):
        match = re.search(r"(?:\^|>=|~|=)?\s*v?(\d+(?:\.\d+){0,2})", alternative)
        if match:
            lowest.append(numbers(match.group(1))[:3])
    return not lowest or any(have >= low for low in lowest)


class Uploads(object):
    def __init__(self, config):
        self.config = config

    def folder(self, site_id, *parts):
        return self.config.state_path("uploads", site_id, *parts)

    # ------------------------------------------------------------------ receiving
    def _meta_path(self, site_id, upload_id):
        if not UPLOAD_ID.match(str(upload_id or "")):
            raise UploadError("invalid upload id")
        return self.folder(site_id, "incoming", upload_id + ".json")

    def _load(self, path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _save(self, path, data):
        ensure_dir(os.path.dirname(path), 0o700)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(path + ".tmp", path)

    def incoming(self, site_id, upload_id):
        meta = self._load(self._meta_path(site_id, upload_id))
        if not meta:
            raise UploadError("this upload is not known (expired?): start it again")
        return meta

    def start(self, site, name, size, version, actor=None):
        name = os.path.basename(str(name or "").replace("\\", "/")).strip()
        if not NAME.match(name) or not archive_extension(name):
            raise UploadError("upload a .zip, .rar, .7z, .tar.gz, .tar.bz2 or .tar.xz file")
        try:
            size = int(size)
        except (TypeError, ValueError):
            size = -1
        if not 0 < size <= MAX_ARCHIVE:
            raise UploadError("the file must be between 1 byte and %s" % human_size(MAX_ARCHIVE))
        version = str(version or "").strip() or suggest_version(name)
        if not VERSION.match(version) or version == "_pre_rollout":
            raise UploadError("invalid version name: letters, digits, dot, -, _ and + (e.g. 1.0.3 or 003)")
        if self.get(site["id"], version):
            raise UploadError("version %s was uploaded already: delete it first or choose another name" % version)
        self.forget_stale(site["id"])
        upload_id = secrets.token_hex(8)
        meta = {"id": upload_id, "name": name, "size": size, "version": version, "actor": actor, "started_at": now()}
        self._save(self._meta_path(site["id"], upload_id), meta)
        open(self.folder(site["id"], "incoming", upload_id + ".part"), "wb").close()
        return {"id": upload_id, "version": version, "chunk_size": CHUNK, "received": 0}

    def append(self, site_id, upload_id, offset, data):
        meta = self.incoming(site_id, upload_id)
        part = self.folder(site_id, "incoming", upload_id + ".part")
        size = os.path.getsize(part) if os.path.isfile(part) else 0
        offset = int(offset)
        if offset < 0 or offset > size:
            raise UploadError("offset %d is past the received size %d" % (offset, size))
        if offset + len(data) > meta["size"]:
            raise UploadError("more data than the announced size of the file")
        with open(part, "r+b" if os.path.isfile(part) else "wb") as f:
            f.seek(offset)
            f.write(data)
            f.truncate()
        return {"received": offset + len(data), "size": meta["size"]}

    def finish(self, site_id, upload_id):
        meta = self.incoming(site_id, upload_id)
        part = self.folder(site_id, "incoming", upload_id + ".part")
        received = os.path.getsize(part) if os.path.isfile(part) else 0
        if received != meta["size"]:
            raise UploadError("the upload is incomplete (%s of %s): send the rest" % (human_size(received), human_size(meta["size"])))
        meta["received_at"] = now()
        self._save(self._meta_path(site_id, upload_id), meta)
        return meta

    def forget_stale(self, site_id):
        folder = self.folder(site_id, "incoming")
        if not os.path.isdir(folder):
            return
        limit = time.time() - STALE_SECONDS
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            try:
                if os.path.getmtime(path) < limit:
                    remove(path)
            except OSError:
                pass

    # ------------------------------------------------------------------ the job
    def process(self, site, upload_id, log):
        """Unpack, check, clean and pack an upload as a version of the site. -> version"""
        meta = self.incoming(site["id"], upload_id)
        if not meta.get("received_at"):
            raise UploadError("the upload is not complete")
        version = meta["version"]
        if self.get(site["id"], version):
            raise UploadError("version %s was uploaded already" % version)
        part = self.folder(site["id"], "incoming", upload_id + ".part")
        work = long_path(self.folder(site["id"], "incoming", upload_id + ".dir"))
        log.step("Unpack %s (%s) as version %s" % (meta["name"], human_size(meta["size"]), version))
        digest_in = sha256_file(part)
        log.line("SHA-256 of the uploaded file: " + digest_in)
        try:
            if exists(work):
                remove(work)
            files, skipped = unpack(self.config, part, work, log)
            if skipped:
                log.line("%d symbolic link(s) or special file(s) skipped" % skipped)
            root = project_root(work)
            if root != work:
                log.line("the archive holds one folder (%s): its content is the release" % os.path.basename(root))
            if not os.listdir(root):
                raise UploadError("the archive is empty")
            log.line("%d files unpacked" % files)
            removed = prepare_tree(self.config, site, root, log)
            requirement = php_requirement(root)
            target = self.folder(site["id"], self.file_name(version))
            log.step("Store version %s on this server" % version)
            count = pack(root, target)
            size, digest = os.path.getsize(target), sha256_file(target)
            info = {"version": version, "name": meta["name"], "uploaded_at": now(), "uploaded_ts": time.time(), "actor": meta.get("actor"),
                    "original_size": meta["size"], "original_sha256": digest_in, "size": size, "sha256": digest,
                    "files": count, "removed": removed[:200], "removed_count": len(removed), "php": requirement}
            self._save(self.folder(site["id"], self.file_name(version, ".json")), info)
            log.line("version %s: %d files, %s (SHA-256 %s)" % (version, count, human_size(size), digest))
        finally:
            for path in (work, part, self._meta_path(site["id"], upload_id)):
                if exists(path):
                    remove(path)
        return version

    # ------------------------------------------------------------------ stored versions
    @staticmethod
    def file_name(version, ext=".zip"):
        if not VERSION.match(version or ""):
            raise UploadError("invalid version: %s" % version)
        return version + ext

    def versions(self, site_id):
        """Uploaded versions, newest upload first."""
        folder = self.folder(site_id)
        found = []
        if os.path.isdir(folder):
            for name in os.listdir(folder):
                if name.endswith(".json") and os.path.isfile(os.path.join(folder, name[:-5] + ".zip")):
                    info = self._load(os.path.join(folder, name))
                    if info and info.get("version") == name[:-5]:
                        found.append(info)
        return sorted(found, key=lambda v: (v.get("uploaded_ts") or 0, v.get("uploaded_at") or ""), reverse=True)

    def get(self, site_id, version):
        if not VERSION.match(version or ""):
            return None
        path = self.folder(site_id, version + ".json")
        return self._load(path) if os.path.isfile(self.folder(site_id, version + ".zip")) else None

    def archive(self, site_id, version):
        info = self.get(site_id, version)
        return self.folder(site_id, version + ".zip") if info else None

    def delete(self, site_id, version):
        if not self.get(site_id, version):
            raise UploadError("version %s is not among the uploads" % version)
        for ext in (".zip", ".json"):
            path = self.folder(site_id, version + ext)
            if exists(path):
                os.unlink(path)

    def prune(self, site, keep_versions, log=None):
        """Keep the newest ``upload_keep`` uploads and the versions in ``keep_versions`` (live, rollback, on the hosting)."""
        keep = int(site.get("upload_keep") or 5)
        for info in self.versions(site["id"])[keep:]:
            if info["version"] in keep_versions:
                continue
            self.delete(site["id"], info["version"])
            if log:
                log.line("old upload removed from this server: " + info["version"])

    def remove_site(self, site_id):
        folder = self.folder(site_id)
        if exists(folder):
            remove(folder)
