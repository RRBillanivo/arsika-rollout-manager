"""Small helpers shared by the runner modules: time stamps, names, files, links and environment files."""
import hashlib
import json
import os
import re
import shutil
import stat
from datetime import datetime, timezone

SITE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$")
REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
SERVICE = re.compile(r"^[A-Za-z0-9@_.-]{1,80}$")
USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
POSIX = os.name == "posix"


def now():
    """Current time as ISO 8601 in UTC, e.g. 2026-10-31T09:15:02+00:00."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def stamp():
    """Local time for folder names, e.g. 20261031-161502."""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def version_of(tag):
    """Release tag -> version: "v3.1.0" -> "3.1.0"."""
    tag = str(tag or "").strip()
    return tag[1:] if len(tag) > 1 and tag[0] in "vV" and tag[1].isdigit() else tag


def version_key(version):
    """Sort key that orders 3.10.0 after 3.9.2 (numbers compared as numbers)."""
    return [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"[.+-]", str(version))]


def ensure_dir(path, mode=0o755):
    os.makedirs(path, mode=mode, exist_ok=True)
    return path


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_private(path, text):
    """Write a file readable by root only (0600), atomically."""
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
    if POSIX:
        os.chmod(path, 0o600)


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def human_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024


def exists(path):
    """True for files, folders and links (also broken links)."""
    return os.path.lexists(path)


def remove(path):
    """Delete a file, a link (never its target) or a folder tree."""
    if os.path.islink(path) or (os.path.lexists(path) and not os.path.isdir(path)):
        os.unlink(path)
    elif os.path.isdir(path):
        def onerror(func, p, _exc):
            os.chmod(p, stat.S_IWRITE)
            func(p)
        shutil.rmtree(path, onerror=onerror)


def make_link(target, link):
    """Symbolic link ``link`` -> ``target`` (absolute target)."""
    os.symlink(target, link, target_is_directory=os.path.isdir(target))


def switch_link(link, target):
    """Point ``link`` at ``target`` atomically (rename of a new link over the old one)."""
    tmp = link + ".new"
    if exists(tmp):
        remove(tmp)
    make_link(target, tmp)
    if POSIX:
        os.replace(tmp, link)
    else:                                       # Windows (tests only): a directory link cannot be replaced by rename
        if exists(link):
            remove(link)
        os.rename(tmp, link)


def link_target(link):
    """Absolute target of a link, or None."""
    if not os.path.islink(link):
        return None
    target = os.readlink(link)
    if target.startswith("\\\\?\\"):
        target = target[4:]
    return os.path.normpath(os.path.join(os.path.dirname(link), target))


ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _env_value(raw):
    if raw == "":
        return ""
    quote = raw[0]
    if quote in "\"'" and len(raw) >= 2 and raw[-1] == quote:
        inner = raw[1:-1]
        if quote == "'":
            return inner
        out, i = [], 0                                  # \\ -> \   \" -> "   \n -> newline (same as App\Core\Env)
        while i < len(inner):
            pair = inner[i:i + 2]
            if pair in ("\\\\", '\\"', "\\n"):
                out.append({"\\\\": "\\", '\\"': '"', "\\n": "\n"}[pair])
                i += 2
            else:
                out.append(inner[i])
                i += 1
        return "".join(out)
    comment = raw.find(" #")
    return (raw if comment < 0 else raw[:comment]).rstrip()


def parse_env(text):
    """KEY=VALUE lines of a .env file, read exactly like the PHP class App\\Core\\Env of the web apps."""
    values = {}
    for line in re.split(r"\r\n|\r|\n", text):
        line = line.strip()
        if not line or line[0] == "#":
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, raw = line.split("=", 1)
        key = key.strip()
        if ENV_KEY.match(key):
            values[key] = _env_value(raw.strip())
    return values


def chown_tree(path, user):
    """Give ``path`` (recursively, links not followed) to ``user`` and its group. Only as root on Linux."""
    if not POSIX or not user or os.geteuid() != 0:
        return False
    import pwd
    try:
        pw = pwd.getpwnam(user)
    except KeyError:
        return False
    uid, gid = pw.pw_uid, pw.pw_gid
    os.lchown(path, uid, gid)
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            os.lchown(os.path.join(root, name), uid, gid)
    return True


def user_exists(user):
    if not POSIX or not user:
        return False
    import pwd
    try:
        pwd.getpwnam(user)
        return True
    except KeyError:
        return False
