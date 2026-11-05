"""Sites on a hosting without a terminal (deploy mode "hosting"): only a file manager, no SSH, no root.

The dashboard generates a PHP agent for the site (runner/agent/rollout-agent.php with the site id, the dashboard's
address and a one-time key), the user uploads it with the hosting's file manager, opens it in the browser and clicks
"Connect"; the runner then exchanges a long secret with it. From then on this runner does what it can do itself
(GitHub with the site's token, checksums, health checks, keeping the backups) and drives the agent with signed
requests: upload the release in pieces, unpack it next to the live files, run the release's database changes
(database/migrations/*.sql, or marked as done by hand), swap the files, keep the previous version for a rollback.
Long work is split into short requests, so the hosting's PHP time limit is never reached.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import ssl
import time
import urllib.error
import urllib.request
import zipfile
from urllib.parse import urlencode, urlsplit

from . import __version__
from .deploy import RUNNER_DIR, DeployError
from .sites import release_pattern
from .util import VERSION, ensure_dir, exists, human_size, now, remove, sha256_file, stamp

PRE = "_pre_rollout"
TEMPLATE = os.path.join(RUNNER_DIR, "agent", "rollout-agent.php")
CHUNK = 4 << 20
AGENT_URL = re.compile(r"^https?://[A-Za-z0-9.-]+(:\d{1,5})?(/[A-Za-z0-9._~%/-]*)?\.php$")
STATUS_SECONDS = 30


class AgentError(DeployError):
    def __init__(self, message, code="", status=0):
        DeployError.__init__(self, message)
        self.code, self.status = code, status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def valid_agent_url(url):
    """https:// address of a .php file; plain http only on this machine (tests)."""
    url = str(url or "").strip()
    parts = urlsplit(url)
    if not AGENT_URL.match(url) or parts.query or parts.fragment:
        return None
    if parts.scheme == "http" and parts.hostname not in ("127.0.0.1", "localhost", "::1"):
        return None
    return url


class AgentClient(object):
    def __init__(self, url, secret, timeout=120):
        self.url = url
        self.secret = secret
        self.timeout = timeout
        self.opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=ssl.create_default_context()))

    def call(self, action, data=None, raw=None, query=None, binary=False):
        params = dict(query or {})
        params["action"] = action
        url = self.url + "?" + urlencode(sorted(params.items()))
        body = raw if raw is not None else json.dumps(data or {}).encode("utf-8")
        parts = urlsplit(url)
        ts, nonce = int(time.time()), secrets.token_hex(16)
        message = "POST\n%s?%s\n%d\n%s\n%s" % (parts.path, parts.query, ts, nonce, hashlib.sha256(body).hexdigest())
        headers = {"X-Rollout-Timestamp": str(ts), "X-Rollout-Nonce": nonce, "User-Agent": "arsika-rollout-manager/" + __version__,
                   "X-Rollout-Signature": hmac.new(self.secret.encode(), message.encode(), hashlib.sha256).hexdigest(),
                   "Content-Type": "application/octet-stream" if raw is not None else "application/json"}
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                content = response.read()
                kind = response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                raise AgentError("the agent address redirects to %s: use that address" % e.headers.get("Location"), "redirect", e.code)
            content = e.read(4000)
            try:
                error = json.loads(content.decode("utf-8")).get("error") or {}
            except ValueError:
                raise AgentError("the hosting answered HTTP %d instead of the agent (is the file there? a firewall of the hosting?)"
                                 % e.code, "http", e.code)
            raise AgentError(str(error.get("message") or "agent error"), str(error.get("code") or ""), e.code)
        except (urllib.error.URLError, OSError) as e:
            raise AgentError("the hosting does not answer: %s" % getattr(e, "reason", e), "unreachable")
        if binary:
            return content
        if "json" not in kind:
            raise AgentError("the address does not answer like the agent (is it the right file?)", "not_agent")
        doc = json.loads(content.decode("utf-8"))
        if not doc.get("ok"):
            error = doc.get("error") or {}
            raise AgentError(str(error.get("message") or "agent error"), str(error.get("code") or ""))
        return doc.get("data")

    def repeat(self, action, data, log=None, describe=None, key="done", limit=2000):
        """Call an action until it reports done (long work goes on in the next request)."""
        data = dict(data)
        for _ in range(limit):
            result = self.call(action, data)
            if result.get(key, True):
                return result
            if log and describe:
                log.line(describe(result))
            data.update({k: result[k] for k in ("start", "offset") if k in result})
            if "next" in result:
                data["start"] = result["next"]
        raise AgentError("the hosting did not finish %s" % action)


class Hosting(object):
    def __init__(self, config, store, deployer):
        self.config = config
        self.store = store
        self.deployer = deployer
        self.backups = deployer.backups
        self.cache = {}

    @staticmethod
    def is_hosting(site):
        return site.get("deploy_mode") == "hosting"

    def client(self, site, timeout=120):
        secret = self.config.secret_of("agent_secrets", site["id"])
        if not site.get("agent_url") or not secret:
            raise DeployError("the hosting of this site is not connected yet: download the agent file in the dashboard, upload it "
                              "with the file manager of the hosting, open it and click Connect")
        return AgentClient(site["agent_url"], secret, timeout)

    # ------------------------------------------------------------------ state, as the deployer answers it for other sites
    def status(self, site, refresh=False):
        """The agent's view of the site (cached for a short time), None when it is not connected."""
        if not site.get("agent_url") or not self.config.secret_of("agent_secrets", site["id"]):
            return None
        cached = self.cache.get(site["id"])
        if cached and not refresh and time.time() - cached[0] < STATUS_SECONDS:
            return cached[1]
        try:
            result = self.client(site, timeout=20).call("status", {"shared": site.get("shared", []), "protect": site.get("adopt_keep", [])})
        except AgentError as e:
            result = {"error": str(e)}
        self.cache[site["id"]] = (time.time(), result)
        return result

    def forget_cache(self, site):
        self.cache.pop(site["id"], None)

    def state(self, site):
        """unpaired | unreachable | ready | classic (files that were not installed by the runner) | empty"""
        status = self.status(site)
        if status is None:
            return "unpaired"
        if "error" in status:
            return "unreachable"
        if status.get("current"):
            return "ready"
        return "classic" if status.get("live") else "empty"

    def current_version(self, site):
        status = self.status(site)
        return (status or {}).get("current")

    def installed(self, site):
        status = self.status(site) or {}
        releases = status.get("releases") or []
        return sorted([{"version": r["version"], "installed_at": r.get("installed_at") or "", "asset": None, "size_bytes": None,
                        "sha256": r.get("sha256")} for r in releases], key=lambda r: r["installed_at"], reverse=True)

    def rollback_target(self, site):
        """The release that was live (and healthy) most recently before the current one, if it is still on the hosting."""
        status = self.status(site) or {}
        current = status.get("current")
        kept = {r["version"] for r in status.get("releases") or []}
        rows = [r for r in self.store.releases(site["id"]) if r["activated_at"] and r["version"] != current and r["version"] in kept]
        rows.sort(key=lambda r: r["activated_at"], reverse=True)
        return rows[0]["version"] if rows else None

    def pre_rollout(self, site):
        return bool((self.status(site) or {}).get("pre_rollout"))

    def details(self, site):
        status = self.status(site, refresh=True)
        if status is None:
            return {"connected": False}
        if "error" in status:
            return {"connected": True, "error": status["error"], "url": site.get("agent_url")}
        keys = ("agent", "php", "site_dir", "data_outside", "zip", "zlib", "pdo", "max_execution_time", "memory_limit", "disk_free", "writable")
        return dict({k: status.get(k) for k in keys}, connected=True, url=site.get("agent_url"), error="")

    # ------------------------------------------------------------------ connecting
    def agent_file(self, site, dashboard):
        """A new agent file for the site, with a new one-time key (an earlier file that is not connected stops working)."""
        dashboard = str(dashboard or "").strip().rstrip("/")
        if not re.match(r"^https?://[A-Za-z0-9.:\[\]-]+(/[A-Za-z0-9._~%/-]*)?$", dashboard):
            raise DeployError("the dashboard address (APP_URL in the dashboard's .env) is not set or not valid: %s" % dashboard)
        with open(TEMPLATE, encoding="utf-8") as f:
            template = f.read()
        key = secrets.token_hex(32)
        self.config.set_secret_of("agent_keys", site["id"], key)
        content = (template.replace("__RA_SITE__", site["id"]).replace("__RA_DASHBOARD__", dashboard)
                   .replace("__RA_PAIRING_KEY__", key))
        return {"filename": "rollout-agent-%s.php" % secrets.token_hex(3), "content": content, "site": site["id"]}

    def connect(self, site, url):
        url = valid_agent_url(url)
        if not url:
            raise DeployError("not the address of the agent file: https://<domain>/<folder>/rollout-agent-….php")
        key = self.config.secret_of("agent_keys", site["id"])
        if not key:
            raise DeployError("download the agent file first (the key of an earlier file has been used)")
        client = AgentClient(url, key, timeout=30)
        info = client.call("hello")
        if info.get("site") != site["id"]:
            raise DeployError("this agent file belongs to the site %s, not %s" % (info.get("site"), site["id"]))
        missing = [name for name, ok in (("the PHP zip extension (ZipArchive)", info.get("zip")),
                                         ("a writable website folder", info.get("writable"))) if not ok]
        if missing:
            raise DeployError("the hosting is missing " + " and ".join(missing))
        secret = secrets.token_hex(32)
        client.call("pair", {"secret": secret})
        self.config.set_secret_of("agent_secrets", site["id"], secret)
        self.config.set_secret_of("agent_keys", site["id"], "")
        site["agent_url"], site["path"] = url, str(info.get("site_dir") or "")
        self.store.save_site({k: v for k, v in site.items() if k not in ("created_at", "updated_at")})
        self.forget_cache(site)
        return info

    def disconnect(self, site):
        """When a site is removed: the agent forgets its secret (best effort), the runner forgets both keys."""
        try:
            if site.get("agent_url") and self.config.secret_of("agent_secrets", site["id"]):
                self.client(site, timeout=15).call("forget")
        except DeployError:
            pass
        self.config.set_secret_of("agent_secrets", site["id"], "")
        self.config.set_secret_of("agent_keys", site["id"], "")
        self.forget_cache(site)

    # ------------------------------------------------------------------ releases
    def archive_path(self, site, version):
        return self.config.state_path("downloads", "hosting-%s-%s.zip" % (site["id"], re.sub(r"[^A-Za-z0-9._-]", "-", version)))

    def fetch_release(self, site, version, log):
        """The release zip on this server (downloaded once for the plan and the deployment)."""
        github = self.deployer.github(site)
        release = github.release(site["repo"], version, release_pattern(site))
        asset = release["asset"]
        if not asset:
            raise DeployError("release %s has no asset matching %s (assets: %s)" % (
                version, site["asset_pattern"], ", ".join(release["assets"]) or "none"))
        archive = self.archive_path(site, version)
        if exists(archive) and (not asset.get("size") or os.path.getsize(archive) == asset["size"]):
            log.line("release %s already downloaded" % version)
            return archive
        if asset.get("source"):
            log.step("Download the source code of %s" % release["tag"])
        else:
            log.step("Download %s (%s)" % (asset["name"], human_size(asset["size"])))
        try:
            self.deployer.fetch(github, asset, archive, log)
        except Exception:
            if exists(archive):
                os.unlink(archive)
            raise
        return archive

    def plan(self, site, version):
        """Database changes a release brings (for the confirmation before an update): its migration files that the
        hosting has not run yet, and files that changed after they were run."""
        if not VERSION.match(version or ""):
            raise DeployError("invalid version: %s" % version)

        class Quiet(object):
            def line(self, *a):
                pass
            step = error = output = line

        archive = self.fetch_release(site, version, Quiet())
        folder = (site.get("migrations_dir") or "database/migrations").strip("/") + "/"
        found = []
        with zipfile.ZipFile(archive) as z:
            names = [i.filename for i in z.infolist()]
            tops = {n.split("/", 1)[0] for n in names if n.strip("/")}
            prefix = next(iter(tops)) + "/" if len(tops) == 1 and any("/" in n.strip("/") for n in names) else ""
            for name in names:
                rel = name[len(prefix):] if prefix and name.startswith(prefix) else name
                if rel.startswith(folder) and rel.endswith(".sql") and "/" not in rel[len(folder):]:
                    data = z.read(name)
                    found.append({"name": rel[len(folder):], "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                                  "sql": data[:20000].decode("utf-8", "replace"), "truncated": len(data) > 20000})
        applied = ((self.status(site, refresh=True) or {}).get("migrations") or {})
        changes = []
        for item in sorted(found, key=lambda m: _natural(m["name"])):
            done = applied.get(item["name"]) if isinstance(applied, dict) else None
            if not done:
                changes.append(dict(item, status="pending"))
            elif done.get("sha256") != item["sha256"]:
                changes.append(dict(item, status="changed"))
        return {"version": version, "folder": folder.rstrip("/"), "migrations": changes}

    def upload_release(self, site, client, version, log, info):
        archive = self.fetch_release(site, version, log)
        size, digest = os.path.getsize(archive), sha256_file(archive)
        name = "release-%s.zip" % re.sub(r"[^A-Za-z0-9._-]", "-", version)
        limit = int(info.get("post_max_size") or 0)
        chunk = max(256 << 10, min(CHUNK, limit // 2 if limit > 0 else CHUNK))
        log.step("Upload the release to the hosting (%s, in pieces of %s)" % (human_size(size), human_size(chunk)))
        offset, last = 0, time.time()
        with open(archive, "rb") as f:
            while offset < size:
                f.seek(offset)
                piece = f.read(chunk)
                for attempt in range(3):
                    try:
                        offset = int(client.call("upload", raw=piece, query={"name": name, "offset": offset})["size"])
                        break
                    except AgentError as e:
                        if attempt == 2 or e.code in ("bad_signature", "locked", "redirect"):
                            raise
                        log.line("upload interrupted (%s), trying again" % e)
                        time.sleep(2)
                if time.time() - last >= 3 or offset >= size:
                    log.line("uploaded %s of %s" % (human_size(offset), human_size(size)))
                    last = time.time()
        client.call("finish-upload", {"name": name, "size": size, "sha256": digest})
        log.line("SHA-256 on the hosting verified: " + digest)
        log.step("Unpack the release on the hosting")
        result = client.repeat("extract", {"name": name, "version": version, "start": 0}, log,
                               lambda r: "unpacked %s of %s files" % (r.get("next"), r.get("total")))
        if result.get("links_skipped"):
            log.line("%d symbolic link(s) in the release were skipped (a hosting does not create links)" % result["links_skipped"])
        self.store.save_release(site["id"], version, os.path.basename(archive), size, digest)

    def migrate(self, site, client, version, mode, log):
        """Run the release's migration files that the hosting has not run yet ("manual": the user ran them by hand)."""
        data = {"version": version, "dir": site.get("migrations_dir") or "database/migrations", "mode": mode,
                "env_file": site.get("backup_env_file") or "", "engine": site.get("backup_db") or "auto"}
        applied = []
        for _ in range(500):
            result = client.call("migrate", data)
            applied += result.get("applied") or []
            if result.get("done", True):
                break
        if applied:
            log.step(("Database changes run on the hosting: " if mode == "auto" else "Database changes marked as done by hand: ")
                     + ", ".join(applied))
        else:
            log.line("no new database changes in this release")

    def activate(self, site, client, version, log):
        log.step("Switch the website to %s" % ("the files from before Arsika Rollout Manager" if version == PRE else version))
        data = {"version": version, "docroot_target": site.get("docroot_target") or ".", "shared": site.get("shared", []),
                "protect": site.get("adopt_keep", [])}
        try:
            result = client.call("activate", data)
        except AgentError as e:
            if e.code != "busy":
                raise
            for _ in range(200):                                     # a large folder is still being deleted
                status = client.call("status", {"shared": data["shared"], "protect": data["protect"]})
                if not status.get("journal"):
                    result = {"previous": status.get("previous")}
                    break
            else:
                raise
        log.line("live: %s (before: %s)" % (version, result.get("previous") or "nothing"))
        self.forget_cache(site)
        return result

    # ------------------------------------------------------------------ jobs
    def deploy(self, job, site, log, adopt=False):
        version = job["version"]
        client = self.client(site)
        info = client.call("status", {"shared": site.get("shared", []), "protect": site.get("adopt_keep", [])})
        current = info.get("current")
        if version == current:
            raise DeployError("%s is already live on the hosting" % version)
        if adopt and current:
            raise DeployError("nothing to adopt: the hosting already runs release %s" % current)
        if not adopt and not current and info.get("live"):
            raise DeployError("the website folder holds files that were not installed by Arsika Rollout Manager; use Adopt first")
        log.line("hosting: PHP %s, agent %s, website folder %s" % (info.get("php"), info.get("agent"), info.get("site_dir")))
        if job["params"].get("backup", site.get("backup_before_deploy")) and (current or info.get("live")):
            self.backup(site, current, ("before adopting " if adopt else "before deploying ") + version, log, job["id"])
        if version in {r["version"] for r in info.get("releases") or []}:
            log.step("Release %s is still on the hosting; reusing it" % version)
        else:
            self.upload_release(site, client, version, log, info)
        # an adopted website already runs this version, so its database has these changes: mark them as done unless asked
        self.migrate(site, client, version, str(job["params"].get("migrations") or ("manual" if adopt else "auto")), log)
        switched = self.activate(site, client, version, log)
        previous = switched.get("previous")
        if not self.deployer.healthy(site, version, log):
            if previous:
                log.step("Automatic rollback to " + ("the files from before" if previous == PRE else previous))
                self.activate(site, client, previous, log)
                ok = self.deployer.healthy(site, previous, log) if previous != PRE else True
                log.line("rollback done; " + ("site healthy again" if ok else "the site is still NOT healthy"))
                raise DeployError("health check failed for %s; rolled back to %s" % (version, previous))
            raise DeployError("health check failed for " + version)
        self.store.mark_activated(site["id"], version)
        archive = self.archive_path(site, version)
        if exists(archive):
            os.unlink(archive)
        if adopt:
            log.line("adoption complete; the old files are kept on the hosting in _pre_rollout (delete them from the dashboard "
                     "when all is well)")
        return current, version

    def rollback(self, job, site, log):
        client = self.client(site)
        current = self.current_version(site)
        target = job["version"] or self.rollback_target(site)
        if not target:
            raise DeployError("no earlier release on the hosting to roll back to")
        if target == current:
            raise DeployError("%s is already the current release" % target)
        self.activate(site, client, target, log)
        if not self.deployer.healthy(site, target, log):
            if current:
                log.step("Automatic rollback to " + current)
                self.activate(site, client, current, log)
            raise DeployError("health check failed for %s; %s is live again" % (target, current))
        self.store.mark_activated(site["id"], target)
        return current, target

    def prune(self, site, log, keep=None):
        status = self.status(site, refresh=True) or {}
        if "error" in status or not status.get("releases"):
            return
        keep = int(keep or site.get("keep_releases", 2))
        protected = {status.get("current"), self.rollback_target(site)}
        releases = sorted(status["releases"], key=lambda r: r.get("installed_at") or "")
        removable = [r for r in releases if r["version"] not in protected]
        client = self.client(site)
        for release in removable[:max(0, len(releases) - keep)]:
            client.repeat("delete-release", {"version": release["version"]})
            self.store.forget_release(site["id"], release["version"])
            log.line("old release removed from the hosting: " + release["version"])
        self.forget_cache(site)

    def delete_release(self, site, version, log):
        if version == self.current_version(site):
            raise DeployError("the current release cannot be deleted")
        self.client(site).repeat("delete-release", {"version": version})
        self.store.forget_release(site["id"], version)
        self.forget_cache(site)
        log.line("release removed from the hosting: " + version)

    def delete_pre_rollout(self, site, log):
        if not self.pre_rollout(site):
            raise DeployError("there is no _pre_rollout folder on the hosting")
        log.step("Delete the files from before Arsika Rollout Manager (_pre_rollout) on the hosting")
        self.client(site).repeat("delete-release", {"version": PRE})
        self.forget_cache(site)

    # ------------------------------------------------------------------ backups (kept on this server)
    def backup(self, site, version, reason, log, job_id=None):
        client = self.client(site)
        backup_id = stamp() + ("_" + version if version else "")
        log.step("Backup %s on the hosting (%s)" % (backup_id, reason))
        result = client.repeat("backup", {"id": backup_id, "env_file": site.get("backup_env_file") or "",
                                          "engine": site.get("backup_db") or "auto", "paths": site.get("backup_paths", []),
                                          "exclude": site.get("backup_exclude", [])}, log,
                               lambda r: "backing up the %s %s" % (r.get("stage"), r.get("files") or ""))
        if result.get("db_skipped"):
            log.line("the PostgreSQL database is not backed up on a hosting (use the panel's backups for it)")
        parts = result.get("parts") or []
        if not parts:
            client.call("clear", {"path": result["folder"]})
            log.line("nothing to back up for this site (no database settings and no backup paths)")
            return None
        target = os.path.join(self.backups.site_dir(site["id"]), backup_id)
        ensure_dir(target, 0o700)
        manifest = {"site": site["id"], "version": version, "created_at": now(), "reason": reason, "hosting": True}
        try:
            for part in parts:
                self.download(client, part, os.path.join(target, part["file"]), log)
                entry = {"file": part["file"], "size": part["size"], "sha256": part["sha256"]}
                if part["kind"] == "database":
                    manifest["database"] = dict(entry, engine="hosting", where="hosting")
                else:
                    manifest["files"] = dict(entry, paths=result.get("paths") or [])
            with open(os.path.join(target, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2)
        except Exception:
            remove(target)
            raise
        finally:
            client.call("clear", {"path": result["folder"]})            # nothing stays on the hosting
        size = sum(os.path.getsize(os.path.join(target, n)) for n in os.listdir(target))
        self.store.save_backup(site["id"], backup_id, version=version, created_at=manifest["created_at"], size_bytes=size,
                               has_database="database" in manifest, has_files="files" in manifest, reason=reason, job_id=job_id)
        log.line("backup saved on this server: %s (%s)" % (target, human_size(size)))
        self.backups.prune(site, log)
        return backup_id

    def download(self, client, part, target, log):
        offset = 0
        with open(target, "wb") as out:
            while offset < part["size"]:
                data = client.call("download", {"path": part["path"], "offset": offset, "length": CHUNK}, binary=True)
                if not data:
                    raise DeployError("the download of %s from the hosting stopped" % part["file"])
                out.write(data)
                offset += len(data)
        if sha256_file(target) != part["sha256"]:
            raise DeployError("%s changed on the way from the hosting (checksum mismatch)" % part["file"])
        log.line("%s downloaded (%s)" % (part["file"], human_size(part["size"])))

    def upload_file(self, client, path, name):
        size, offset = os.path.getsize(path), 0
        with open(path, "rb") as f:
            while offset < size:
                f.seek(offset)
                offset = int(client.call("upload", raw=f.read(CHUNK), query={"name": name, "offset": offset})["size"])
        if size == 0:
            client.call("upload", raw=b"", query={"name": name, "offset": 0})
        client.call("finish-upload", {"name": name, "size": size, "sha256": sha256_file(path)})

    def restore(self, site, backup_id, log):
        """Put the files and the database of a backup back on the hosting. -> folder of the replaced files there"""
        folder = os.path.join(self.backups.site_dir(site["id"]), backup_id)
        manifest = self.backups.manifest(site["id"], backup_id)
        for part in ("database", "files"):
            if part in manifest and sha256_file(os.path.join(folder, manifest[part]["file"])) != manifest[part]["sha256"]:
                raise DeployError("%s of backup %s is damaged (checksum mismatch)" % (manifest[part]["file"], backup_id))
        if not manifest.get("hosting"):
            raise DeployError("backup %s was not made on a hosting" % backup_id)
        client = self.client(site)
        replaced = None
        if "files" in manifest:
            log.step("Restore files on the hosting: " + ", ".join(manifest["files"]["paths"]))
            name = "restore-%s-files.zip" % re.sub(r"[^A-Za-z0-9._-]", "-", backup_id)
            self.upload_file(client, os.path.join(folder, manifest["files"]["file"]), name)
            replaced = client.repeat("restore-files", {"name": name, "paths": manifest["files"]["paths"], "start": 0}).get("replaced")
            log.line("previous files kept on the hosting in " + str(replaced))
        if "database" in manifest:
            log.step("Restore the database on the hosting")
            name = "restore-%s-database.sql.gz" % re.sub(r"[^A-Za-z0-9._-]", "-", backup_id)
            self.upload_file(client, os.path.join(folder, manifest["database"]["file"]), name)
            client.repeat("restore-db", {"name": name, "env_file": site.get("backup_env_file") or "", "engine": site.get("backup_db") or "auto",
                                         "offset": 0}, log, lambda r: "database restore in progress")
            log.line("database restored")
        self.forget_cache(site)
        return replaced

    def clear_replaced(self, site, replaced):
        if replaced:
            try:
                self.client(site).repeat("clear", {"path": replaced})
            except DeployError:
                pass


def _natural(name):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", name)]
