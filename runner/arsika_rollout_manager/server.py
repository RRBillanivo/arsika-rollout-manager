"""HTTP API for the dashboard (JSON): plain HTTP on 127.0.0.1 for a dashboard on the same server, HTTPS with a pinned
certificate (and optionally an address allowlist) for a dashboard on another server. Every request is signed:

    X-Rollout-Timestamp: <unix seconds>
    X-Rollout-Nonce: <16-64 random lowercase hex characters, new for every request>
    X-Rollout-Signature: hex HMAC-SHA256(secret, METHOD \\n PATH_WITH_QUERY \\n TIMESTAMP \\n NONCE \\n hex SHA-256(body))

Requests older than 120 seconds and repeated nonces are refused. Answers: {"data": ...} or {"error": {code, message, fields}}.
"""
import hashlib
import hmac
import ipaddress
import json
import os
import platform
import re
import socket
import socketserver
import ssl
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

from . import __version__, system
from .deploy import DeployError
from .github import GitHub, GitHubError
from .hosting import artisan_command
from .joblog import read_log
from .sites import expand, layout, preset, presets_info, release_pattern, site_url, validate
from .uploads import CHUNK, UploadError
from .util import VERSION, exists, now, version_key
from .worker import JOB_TYPES, RUNNER_SITE

MAX_BODY = 1 << 20
UPLOAD_BODY = CHUNK + 4096                # a piece of an uploaded archive (PUT /v1/sites/<site>/uploads/<id>)
WINDOW = 120
TOKEN = re.compile(r"^[A-Za-z0-9_]{20,255}$")
TOKEN_ERROR = "This does not look like a GitHub token."


class ApiError(Exception):
    def __init__(self, status, code, message, fields=None):
        Exception.__init__(self, message)
        self.status, self.code, self.fields = status, code, fields or {}


def token_hint(token):
    """Recognisable part of a token for the interface, e.g. github_pat_…a1b2 (the token itself is never sent back)."""
    if not token:
        return ""
    return ("github_pat_" if token.startswith("github_pat_") else token[:4]) + "…" + token[-4:]


def read_token(data, errors):
    """The site's GitHub token sent by the dashboard ("" when not given)."""
    token = str(data.get("github_token") or "").strip()
    if token and not TOKEN.match(token):
        errors["github_token"] = TOKEN_ERROR
    return token


class ThreadingServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    allowed = ()                                                     # allow_from networks; empty: any address

    def verify_request(self, request, client_address):
        """Connections from addresses outside allow_from are closed before TLS or HTTP (loopback is always allowed)."""
        if not self.allowed:
            return True
        try:
            ip = ipaddress.ip_address(str(client_address[0]).split("%")[0])
        except ValueError:
            return False
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if ip.is_loopback or any(ip in network for network in self.allowed):
            return True
        refused = self.__dict__.setdefault("refused", {})
        if time.time() - refused.get(str(ip), 0) > 3600:                # one line per address and hour in the journal
            if len(refused) > 1000:
                refused.clear()
            refused[str(ip)] = time.time()
            print("connection from %s refused: not in allow_from" % ip, flush=True)
        return False

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ssl.SSLError, OSError)):  # scanners, plain HTTP on the HTTPS port, dropped clients
            return
        HTTPServer.handle_error(self, request, client_address)


class ThreadingServer6(ThreadingServer):
    address_family = socket.AF_INET6


class Api(object):
    """Request routing and the answers (kept apart from the HTTP handler so it can be tested directly)."""

    ROUTES = [
        ("GET", r"/v1/status$", "status"),
        ("GET", r"/v1/presets$", "presets"),
        ("POST", r"/v1/github/check$", "github_check"),
        ("GET", r"/v1/sites$", "sites"),
        ("POST", r"/v1/sites$", "create_site"),
        ("GET", r"/v1/sites/(?P<site>[a-z0-9-]+)$", "site"),
        ("PUT", r"/v1/sites/(?P<site>[a-z0-9-]+)$", "update_site"),
        ("DELETE", r"/v1/sites/(?P<site>[a-z0-9-]+)$", "delete_site"),
        ("GET", r"/v1/sites/(?P<site>[a-z0-9-]+)/releases$", "releases"),
        ("GET", r"/v1/sites/(?P<site>[a-z0-9-]+)/health$", "health"),
        ("POST", r"/v1/sites/(?P<site>[a-z0-9-]+)/jobs$", "create_job"),
        ("GET", r"/v1/sites/(?P<site>[a-z0-9-]+)/backups$", "backups"),
        ("DELETE", r"/v1/sites/(?P<site>[a-z0-9-]+)/backups/(?P<backup>[0-9A-Za-z._+-]+)$", "delete_backup"),
        ("GET", r"/v1/jobs$", "jobs"),
        ("GET", r"/v1/jobs/(?P<job>\d+)$", "job"),
        ("POST", r"/v1/runner/update$", "update_runner"),
        ("POST", r"/v1/sites/(?P<site>[a-z0-9-]+)/agent-file$", "agent_file"),
        ("POST", r"/v1/sites/(?P<site>[a-z0-9-]+)/agent/connect$", "agent_connect"),
        ("GET", r"/v1/sites/(?P<site>[a-z0-9-]+)/plan$", "plan"),
        ("POST", r"/v1/sites/(?P<site>[a-z0-9-]+)/uploads$", "upload_start"),
        ("PUT", r"/v1/sites/(?P<site>[a-z0-9-]+)/uploads/(?P<upload>[0-9a-f]{16})$", "upload_chunk"),
        ("POST", r"/v1/sites/(?P<site>[a-z0-9-]+)/uploads/(?P<upload>[0-9a-f]{16})/finish$", "upload_finish"),
        ("DELETE", r"/v1/sites/(?P<site>[a-z0-9-]+)/uploads/(?P<version>[0-9A-Za-z._+-]+)$", "upload_delete"),
        ("GET", r"/v1/sites/(?P<site>[a-z0-9-]+)/env$", "env_read"),
        ("PUT", r"/v1/sites/(?P<site>[a-z0-9-]+)/env$", "env_write"),
    ]
    RAW = ("upload_chunk",)                                          # the body is data, not JSON

    def __init__(self, config, store, deployer, worker):
        self.config = config
        self.store = store
        self.deployer = deployer
        self.worker = worker
        self.hosting = getattr(worker, "hosting", None)
        self.started = time.time()
        self.health_cache = {}
        self.github_clients = {}
        self.seen = {}
        self.seen_lock = threading.Lock()
        moved = self.config.migrate_legacy_token([s["id"] for s in self.store.sites()])
        if moved:
            print("the GitHub token of version 1.0.0 now belongs to %d site(s)" % moved, flush=True)

    # ---------- authentication ----------
    def verify(self, method, target, headers, body):
        secret = self.config["secret"]
        if not secret:
            raise ApiError(503, "not_configured", "runner secret is not set")
        try:
            ts = int(headers.get("X-Rollout-Timestamp", "0"))
        except ValueError:
            ts = 0
        if abs(time.time() - ts) > WINDOW:
            raise ApiError(401, "bad_signature", "request expired or clock mismatch")
        nonce = headers.get("X-Rollout-Nonce", "")
        if not re.match(r"^[0-9a-f]{16,64}$", nonce):
            raise ApiError(401, "bad_signature", "missing nonce")
        message = "%s\n%s\n%d\n%s\n%s" % (method, target, ts, nonce, hashlib.sha256(body).hexdigest())
        expected = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
        signature = headers.get("X-Rollout-Signature", "")
        if not hmac.compare_digest(expected, signature):
            raise ApiError(401, "bad_signature", "invalid signature")
        with self.seen_lock:
            limit = time.time() - WINDOW * 2
            for key in [k for k, v in self.seen.items() if v < limit]:
                del self.seen[key]
            if nonce in self.seen:
                raise ApiError(401, "replayed", "request already used")
            self.seen[nonce] = time.time()

    def handle(self, method, target, headers, body):
        """-> (status, document)"""
        try:
            self.verify(method, target, headers, body)
            parts = urlsplit(target)
            query = {k: v[-1] for k, v in parse_qs(parts.query).items()}
            for route_method, pattern, name in self.ROUTES:
                match = re.match(pattern, parts.path)
                if match and route_method == method:
                    if name in self.RAW:
                        return 200, {"data": getattr(self, name)(query=query, data={}, raw=body, **match.groupdict())}
                    data = json.loads(body.decode("utf-8")) if body else {}
                    if not isinstance(data, dict):
                        raise ApiError(400, "bad_request", "JSON object expected")
                    return 200, {"data": getattr(self, name)(query=query, data=data, **match.groupdict())}
            raise ApiError(404, "not_found", "unknown endpoint " + method + " " + parts.path)
        except ApiError as e:
            return e.status, {"error": {"code": e.code, "message": str(e), "fields": e.fields}}
        except (DeployError, GitHubError, UploadError) as e:
            return 422, {"error": {"code": "failed", "message": str(e), "fields": {}}}
        except ValueError as e:
            return 400, {"error": {"code": "bad_request", "message": str(e), "fields": {}}}

    # ---------- helpers ----------
    def github(self, token=""):
        """GitHub client for one token (each client keeps its own one-minute cache of releases)."""
        client = self.github_clients.get(token)
        if client is None:
            if len(self.github_clients) > 50:
                self.github_clients.clear()
            client = self.github_clients[token] = GitHub(self.config["github_api"], token)
        return client

    def site_github(self, site):
        return self.github(self.config.site_token(site["id"]))

    def ops(self, site):
        """What installs a site: the deployer (this server) or the agent of its hosting."""
        return self.hosting if self.hosting is not None and self.hosting.is_hosting(site) else self.deployer

    def get_site(self, site_id):
        site = self.store.site(site_id)
        if site is None:
            raise ApiError(404, "site_not_found", "site %s is not registered" % site_id)
        return site

    def site_health(self, site, refresh=False):
        if not site.get("health_url") or ("{site_url}" in site["health_url"] and not site_url(site)):
            return None                         # a hosting site that is not connected yet has no address
        version = self.ops(site).current_version(site) or ""
        cached = self.health_cache.get(site["id"])
        if cached and not refresh and time.time() - cached[0] < 30 and cached[2] == version:
            return cached[1]
        contains = site.get("health_contains", "")
        if "{version}" in contains and not version:
            contains = ""                       # an installation that is not adopted yet: its version is not known
        result = system.quick_health(expand(site, site["health_url"], version), expand(site, contains, version),
                                     local=bool(site.get("health_local")))
        result["checked_at"] = now()
        self.health_cache[site["id"]] = (time.time(), result, version)
        return result

    def summary(self, site, with_health=True, with_latest=True):
        ops = self.ops(site)
        current = ops.current_version(site)
        installed = ops.installed(site) if ops is not self.deployer or os.path.isdir(layout(site)["releases"]) else []
        latest, latest_error = None, ""
        uploaded = site.get("source") == "upload"
        if uploaded:
            versions = self.deployer.uploads.versions(site["id"])
            latest = versions[0]["version"] if versions else None      # the newest upload
        elif with_latest and site.get("repo"):
            try:
                releases = [r for r in self.site_github(site).releases(site["repo"], release_pattern(site)) if not r["prerelease"]]
                latest = releases[0]["version"] if releases else None
            except GitHubError as e:
                latest_error = str(e)
        jobs = self.store.jobs(site["id"], 1)
        active = self.store.active_job(site["id"])
        return {
            "id": site["id"], "name": site["name"], "domain": site["domain"], "repo": site["repo"], "path": site["path"],
            "deploy_mode": site.get("deploy_mode", "files"), "state": ops.state(site), "current_version": current, "installed": [r["version"] for r in installed],
            "latest_version": latest, "latest_error": latest_error,
            "update_available": bool(latest and current and latest != current and (uploaded or version_key(latest) > version_key(current))),
            "source": site.get("source", "asset"), "hosting_app": site.get("hosting_app", ""),
            "rollback_version": ops.rollback_target(site),
            "health": self.site_health(site) if with_health else None,
            "services": system.service_states(site.get("services", [])),
            "last_job": jobs[0] if jobs else None, "active_job": active,
        }

    # ---------- endpoints ----------
    def status(self, query, data):
        active = self.store.active_job()
        sites = self.store.sites()
        return {"version": __version__, "python": platform.python_version(), "uptime_seconds": int(time.time() - self.started),
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(self.started)) + "+00:00",
                "sites": len(sites), "github_tokens": sum(1 for s in sites if self.config.site_token(s["id"])), "active_job": active,
                "disk": system.disk_free(self.config["backup_dir"] if os.path.isdir(self.config["backup_dir"]) else "/"),
                "site_roots": self.config.get("site_roots", ["/www/wwwroot"]), "hostname": socket.gethostname(),
                "platform": self.config.get("platform") or system.platform_name(), "web_user": self.config.get("web_user", ""),
                "tls": self.config.tls_enabled(), "allow_from": [str(n) for n in self.config.allowed],
                "docker_compose": self.deployer.compose.version() if self.deployer else None}

    def presets(self, query, data):
        return presets_info(self.config.get("web_user", "www"))

    def github_check(self, query, data):
        """Can this repository be read? Uses the token typed in the form, else the saved token of the site being edited."""
        repo = str(data.get("repo", "")).strip()
        errors = {}
        token = read_token(data, errors)
        if errors:
            return {"ok": False, "message": TOKEN_ERROR, "releases": 0}
        if not token and data.get("site"):
            token = self.config.site_token(str(data["site"]))
        pattern = release_pattern({"source": data.get("source"), "asset_pattern": str(data.get("asset_pattern") or "*.zip")})
        try:
            releases = self.github(token).releases(repo, pattern, refresh=True)
        except GitHubError as e:
            return {"ok": False, "message": str(e), "releases": 0}
        with_asset = [r for r in releases if r["asset"]]
        return {"ok": True, "releases": len(releases), "with_asset": len(with_asset),
                "latest": with_asset[0]["version"] if with_asset else None, "message": ""}

    def sites(self, query, data):
        sites = self.store.sites()
        health = query.get("health", "1") != "0"
        with ThreadPoolExecutor(max_workers=min(8, len(sites) or 1)) as pool:
            return list(pool.map(lambda s: self.summary(s, with_health=health), sites))

    def site(self, site, query, data):
        record = self.get_site(site)
        lay = layout(record)
        details = self.summary(record)
        details["settings"] = record
        token = self.config.site_token(record["id"])
        details["github_token_set"], details["github_token_hint"] = bool(token), token_hint(token)
        if self.ops(record) is self.hosting:
            details["releases_installed"] = self.hosting.installed(record)
            details["pre_rollout"] = self.hosting.pre_rollout(record)
            details["disk"] = None
            details["hosting"] = self.hosting.details(record)
            status = self.hosting.status(record) or {}
            details["env"] = {"file": self.hosting.env_file(record), "pending": self.hosting.pending_env(record) is not None,
                              "on_hosting": bool(status.get("env"))}
            return details
        details["releases_installed"] = self.deployer.installed(record)
        details["pre_rollout"] = exists(lay["pre_rollout"])
        details["disk"] = system.disk_free(record["path"]) if os.path.isdir(record["path"]) else None
        return details

    def create_site(self, query, data):
        base = preset(str(data.get("preset", "")), self.config.get("web_user", "www")) if data.get("preset") else None
        merged = dict(base or {})
        merged.update({k: v for k, v in data.items() if k not in ("preset", "github_token", "remove_github_token")})
        site, errors = validate(merged, self.config.get("site_roots", ["/www/wwwroot"]))
        token = read_token(data, errors)
        if not errors and self.store.site(site["id"]):
            errors["id"] = "A site with this id already exists."
        if not errors and site["deploy_mode"] != "hosting" and any(s["path"] == site["path"] and s.get("deploy_mode") != "hosting"
                                                                    for s in self.store.sites()):
            errors["path"] = "Another site already uses this folder."
        if errors:
            raise ApiError(422, "validation_failed", "check the highlighted fields", errors)
        self.store.save_site(site)
        self.config.set_site_token(site["id"], token)
        return self.site(site["id"], query, {})

    def update_site(self, site, query, data):
        """Settings sent are changed, the others kept. github_token replaces the site's token, remove_github_token removes it."""
        record = self.get_site(site)
        updated, errors = validate(data, self.config.get("site_roots", ["/www/wwwroot"]), existing=record)
        token = read_token(data, errors)
        installed = self.ops(record).state(record) == "ready"
        if not errors and record.get("deploy_mode") == "hosting":
            updated["path"], updated["agent_url"] = record.get("path", ""), record.get("agent_url", "")   # set by the agent
            if updated["deploy_mode"] != "hosting":
                errors["deploy_mode"] = "A hosting site stays a hosting site (remove it and add it again)."
        elif not errors and updated["deploy_mode"] == "hosting":
            errors["deploy_mode"] = "Add a hosting site as a new site."
        if not errors and updated["path"] != record["path"] and installed and record.get("deploy_mode") != "hosting":
            errors["path"] = "The folder of an installed site cannot be changed."
        if not errors and installed and record.get("compose_project") and updated["compose_project"] != record["compose_project"] \
                and record.get("deploy_mode") == "compose":
            errors["compose_project"] = "The containers and volumes of the installed site belong to this name; it cannot be changed."
        if errors:
            raise ApiError(422, "validation_failed", "check the highlighted fields", errors)
        updated["id"] = record["id"]
        self.store.save_site({k: v for k, v in updated.items() if k not in ("created_at", "updated_at")})
        if token or data.get("remove_github_token"):
            self.config.set_site_token(site, token)
        self.health_cache.pop(site, None)
        return self.site(site, query, {})

    def delete_site(self, site, query, data):
        self.get_site(site)
        if self.store.active_job(site):
            raise ApiError(409, "busy", "a job of this site is queued or running")
        record = self.store.site(site)
        if self.hosting is not None and self.hosting.is_hosting(record):
            self.hosting.disconnect(record)
        self.deployer.uploads.remove_site(site)
        self.store.delete_site(site)
        self.config.set_site_token(site, "")
        return {"deleted": site, "files_kept": True}

    def releases(self, site, query, data):
        record = self.get_site(site)
        installed = {r["version"]: r for r in self.ops(record).installed(record)}
        current = self.ops(record).current_version(record)
        if record.get("source") == "upload":
            remote, error = [self.upload_release(u) for u in self.deployer.uploads.versions(site)], ""
        else:
            try:
                remote = self.site_github(record).releases(record["repo"], release_pattern(record), refresh=query.get("refresh") == "1")
                error = ""
            except GitHubError as e:
                remote, error = [], str(e)
        listed = {r["version"] for r in remote}
        for r in remote:
            r["installed"] = r["version"] in installed
            r["current"] = r["version"] == current
        local_only = [{"version": v, "tag": v, "name": v, "published_at": None, "notes": "", "asset": None, "installed": True,
                       "current": v == current, "local_only": True} for v in installed if v not in listed]
        return {"releases": remote + local_only, "error": error, "current": current}

    @staticmethod
    def upload_release(info):
        """An uploaded version, in the shape of a GitHub release for the dashboard."""
        return {"version": info["version"], "tag": info["version"], "name": info.get("name") or info["version"],
                "published_at": info.get("uploaded_at"), "prerelease": False, "draft": False, "notes": "", "html_url": "",
                "asset": {"name": info.get("name"), "size": info.get("size")}, "assets": [info.get("name")], "uploaded": True,
                "actor": info.get("actor"), "files": info.get("files"), "removed_count": info.get("removed_count", 0),
                "removed": (info.get("removed") or [])[:20], "php": info.get("php") or "", "sha256": info.get("sha256")}

    def health(self, site, query, data):
        return self.site_health(self.get_site(site), refresh=True)

    def create_job(self, site, query, data):
        record = self.get_site(site)
        job_type = str(data.get("type", ""))
        if job_type not in JOB_TYPES:
            raise ApiError(422, "validation_failed", "unknown job type", {"type": "Unknown job type."})
        version = str(data.get("version") or "").strip() or None
        if version and not VERSION.match(version):
            raise ApiError(422, "validation_failed", "invalid version", {"version": "Invalid version."})
        if job_type in ("deploy", "adopt", "delete-release") and not version:
            raise ApiError(422, "validation_failed", "version required", {"version": "Choose a version."})
        params = data.get("params") if isinstance(data.get("params"), dict) else {}
        params = {k: params[k] for k in ("backup", "backup_id", "safety_backup", "reason", "migrations", "command") if k in params}
        if params.get("migrations") not in (None, "auto", "manual"):
            raise ApiError(422, "validation_failed", "migrations: auto or manual", {"migrations": "Choose auto or manual."})
        if job_type == "upload":
            raise ApiError(422, "validation_failed", "versions are uploaded with the upload endpoints", {"type": "Use the upload."})
        if job_type == "artisan":
            if self.hosting is None or not self.hosting.is_laravel(record):
                raise ApiError(409, "not_laravel", "artisan commands are run for Laravel sites on a hosting")
            try:
                params["command"] = artisan_command(params.get("command"))
            except DeployError as e:
                raise ApiError(422, "validation_failed", str(e), {"command": str(e)})
        if record.get("source") == "upload" and job_type in ("deploy", "adopt") and not self.deployer.uploads.get(site, version) \
                and version not in {r["version"] for r in self.ops(record).installed(record)}:
            raise ApiError(422, "validation_failed", "version %s is not uploaded" % version, {"version": "Upload this version first."})
        if self.store.active_job(site):
            raise ApiError(409, "busy", "a job of this site is already queued or running")
        if job_type == "restore" and not self.store.backup(site, str(params.get("backup_id", ""))):
            raise ApiError(422, "validation_failed", "backup not found", {"backup_id": "Backup not found."})
        state = self.ops(record).state(record)
        if state in ("unpaired", "unreachable"):
            raise ApiError(409, "not_connected", "the hosting of this site is %s" % ("not connected yet" if state == "unpaired"
                                                                                       else "not answering"))
        if job_type == "adopt" and state != "classic":
            raise ApiError(409, "not_classic", "nothing to adopt: the site folder is %s" % state)
        if job_type in ("deploy", "restore") and state == "classic":
            raise ApiError(409, "needs_adopt", "the site holds an installation without releases/; adopt it first")
        actor = str(data.get("actor") or "")[:80] or None
        job_id = self.worker.submit(site, job_type, version, params, actor)
        return self.store.job(job_id)

    def backups(self, site, query, data):
        self.get_site(site)
        return self.store.backups(site)

    def delete_backup(self, site, backup, query, data):
        self.get_site(site)
        if not self.store.backup(site, backup):
            raise ApiError(404, "backup_not_found", "backup not found")
        if self.store.active_job(site):
            raise ApiError(409, "busy", "a job of this site is queued or running")
        self.deployer.backups.delete(site, backup)
        return {"deleted": backup}

    def update_runner(self, query, data):
        """Install the runner of another Arsika Rollout Manager release on this server (normally the dashboard's own
        version). It restarts with the new code when the job ends."""
        version = str(data.get("version") or "").strip()
        if not VERSION.match(version):
            raise ApiError(422, "validation_failed", "invalid version", {"version": "Invalid version."})
        if self.store.active_job():
            raise ApiError(409, "busy", "a job is queued or running on this server; try again when it has finished")
        actor = str(data.get("actor") or "")[:80] or None
        return self.store.job(self.worker.submit(RUNNER_SITE, "self-update", version, {}, actor))

    def hosting_site(self, site):
        record = self.get_site(site)
        if self.hosting is None or not self.hosting.is_hosting(record):
            raise ApiError(409, "not_hosting", "site %s is not a hosting site" % site)
        return record

    def agent_file(self, site, query, data):
        return self.hosting.agent_file(self.hosting_site(site), str(data.get("dashboard") or ""))

    def agent_connect(self, site, query, data):
        record = self.hosting_site(site)
        self.hosting.connect(record, str(data.get("url") or ""))
        self.health_cache.pop(site, None)
        return self.site(site, query, {})

    def plan(self, site, query, data):
        return self.hosting.plan(self.hosting_site(site), str(query.get("version") or ""))

    # ---------- uploaded versions ----------
    def upload_site(self, site):
        record = self.get_site(site)
        if record.get("source") != "upload":
            raise ApiError(409, "not_upload", "site %s takes its versions from GitHub releases (release source)" % site)
        return record

    def upload_start(self, site, query, data):
        record = self.upload_site(site)
        return self.deployer.uploads.start(record, data.get("name"), data.get("size"), data.get("version"),
                                           str(data.get("actor") or "")[:80] or None)

    def upload_chunk(self, site, upload, query, data, raw):
        self.upload_site(site)
        try:
            offset = int(query.get("offset", "0"))
        except ValueError:
            raise ApiError(400, "bad_request", "offset must be a number")
        return self.deployer.uploads.append(site, upload, offset, raw)

    def upload_finish(self, site, upload, query, data):
        """The upload is complete: a job unpacks, cleans and stores it as a version (see uploads.py)."""
        self.upload_site(site)
        meta = self.deployer.uploads.incoming(site, upload)
        if self.store.active_job(site):
            raise ApiError(409, "busy", "a job of this site is queued or running; finish the upload when it has ended")
        self.deployer.uploads.finish(site, upload)
        actor = str(data.get("actor") or meta.get("actor") or "")[:80] or None
        return self.store.job(self.worker.submit(site, "upload", meta["version"], {"upload_id": upload}, actor))

    def upload_delete(self, site, version, query, data):
        record = self.upload_site(site)
        ops = self.ops(record)
        if version in (ops.current_version(record), ops.rollback_target(record)):
            raise ApiError(409, "in_use", "version %s is live or the rollback version; it stays" % version)
        if self.store.active_job(site):
            raise ApiError(409, "busy", "a job of this site is queued or running")
        self.deployer.uploads.delete(site, version)
        return {"deleted": version}

    # ---------- the .env of a hosting site ----------
    def env_read(self, site, query, data):
        return self.hosting.env_read(self.hosting_site(site))

    def env_write(self, site, query, data):
        record = self.hosting_site(site)
        result = self.hosting.env_write(record, data.get("content"))
        self.hosting.forget_cache(record)
        return result

    def jobs(self, query, data):
        return self.store.jobs(query.get("site") or None, min(200, int(query.get("limit", 50) or 50)))

    def job(self, job, query, data):
        record = self.store.job(int(job))
        if not record:
            raise ApiError(404, "job_not_found", "job not found")
        text, offset, size = read_log(self.worker.log_path(record["id"]), query.get("offset", 0))
        record["log"] = {"text": text, "offset": offset, "size": size}
        return record


def make_handler(api):
    class Handler(BaseHTTPRequestHandler):
        server_version = "arsika-rollout-manager/" + __version__
        protocol_version = "HTTP/1.1"
        timeout = 60                                                 # idle or slow connections are closed

        def setup(self):
            BaseHTTPRequestHandler.setup(self)
            if isinstance(self.connection, ssl.SSLSocket):
                self.connection.do_handshake()                       # here, in the request thread, not in accept()

        def _serve(self, method):
            length = int(self.headers.get("Content-Length") or 0)
            if length > (UPLOAD_BODY if method == "PUT" and "/uploads/" in self.path else MAX_BODY):
                status, doc = 413, {"error": {"code": "too_large", "message": "request too large", "fields": {}}}
            else:
                body = self.rfile.read(length) if length else b""
                status, doc = api.handle(method, self.path, self.headers, body)
            payload = json.dumps(doc, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            self._serve("GET")

        def do_POST(self):
            self._serve("POST")

        def do_PUT(self):
            self._serve("PUT")

        def do_DELETE(self):
            self._serve("DELETE")

        def log_message(self, fmt, *args):
            pass                                                     # requests are not logged (the dashboard audits them)

    return Handler


def tls_context(config):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    if hasattr(ssl, "TLSVersion"):
        context.minimum_version = ssl.TLSVersion.TLSv1_2
    else:                                                            # Python 3.6
        context.options |= getattr(ssl, "OP_NO_TLSv1", 0) | getattr(ssl, "OP_NO_TLSv1_1", 0)
    context.load_cert_chain(config["tls_cert"], config["tls_key"])
    return context


def serve(api):
    config = api.config
    server_class = ThreadingServer6 if ":" in config["listen"] else ThreadingServer
    server = server_class((config["listen"], int(config["port"])), make_handler(api))
    server.allowed = tuple(config.allowed)
    if config.tls_enabled():
        server.socket = tls_context(config).wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
    return server
