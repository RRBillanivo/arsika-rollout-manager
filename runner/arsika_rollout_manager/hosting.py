"""Sites on a hosting without a terminal (deploy mode "hosting"): only a way to drop files into the website folder (a
file manager, FTP), no SSH, no root. A virtual machine where one may only drop files is treated the same way.

The dashboard generates a PHP agent for the site (runner/agent/rollout-agent.php with the site id, the dashboard's
address and a one-time key), the user drops it into the website folder, opens it in the browser and clicks "Connect";
the runner then exchanges a long secret with it. From then on this runner does what it can do itself (GitHub with the
site's token, uploaded versions, composer, checksums, health checks, keeping the backups) and drives the agent with
signed requests: upload the release in pieces, unpack it next to the live files, run the release's database changes
(database/migrations/*.sql, or marked as done by hand; Laravel: php artisan migrate inside PHP), swap the files, keep
the previous version for a rollback. Long work is split into short requests, so the hosting's PHP time limit is never
reached.

Laravel sites (hosting_app "laravel"): only public/ goes into the website folder; the application of every release,
the .env and storage/ stay in the agent's data folder (outside the website folder when the hosting allows it), so they
are never served, whatever the web server (Apache, LiteSpeed or nginx).
"""
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import shlex
import ssl
import time
import urllib.error
import urllib.request
import zipfile
from urllib.parse import urlencode, urlsplit

from . import __version__
from .deploy import RUNNER_DIR, DeployError
from .sites import release_pattern, site_url
from .uploads import UploadError, long_path, pack, php_satisfies, prepare_tree, project_root, unpack
from .util import VERSION, ensure_dir, exists, human_size, now, remove, sha256_file, stamp, version_key, write_private

PRE = "_pre_rollout"
TEMPLATE = os.path.join(RUNNER_DIR, "agent", "rollout-agent.php")
CHUNK = 4 << 20
AGENT_URL = re.compile(r"^https?://[A-Za-z0-9.-]+(:\d{1,5})?(/[A-Za-z0-9._~%/-]*)?\.php$")
STATUS_SECONDS = 30
LARAVEL_AGENT = "1.5.0"                  # the first agent that runs Laravel sites, edits .env, runs artisan, updates itself
LAN_SUFFIXES = (".local", ".lan", ".internal", ".home.arpa", ".localdomain")
ENV_LIMIT = 256 << 10
ARTISAN_SECONDS = 300
# artisan commands that wait for input or run until they are stopped: not possible in a request of a web server
ARTISAN_REFUSED = ("serve", "tinker", "pail", "schedule:work", "queue:listen", "horizon", "octane:start", "reverb:start",
                   "inertia:start-ssr", "dev")


class AgentError(DeployError):
    def __init__(self, message, code="", status=0, details=None):
        DeployError.__init__(self, message)
        self.code, self.status, self.details = code, status, details or {}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def local_host(host):
    """An address of this machine or of a local network (a virtual machine without a certificate)."""
    host = str(host or "").lower()
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return host == "localhost" or "." not in host or host.endswith(LAN_SUFFIXES)


def valid_agent_url(url):
    """https:// address of a .php file; plain http only on this machine or a local network."""
    url = str(url or "").strip()
    parts = urlsplit(url)
    if not AGENT_URL.match(url) or parts.query or parts.fragment:
        return None
    if parts.scheme == "http" and not local_host(parts.hostname):
        return None
    return url


def agent_outdated(agent, wanted):
    return version_key(str(agent or "0")) < version_key(wanted)


_TEMPLATE_VERSION = {}


def template_version():
    """RA_VERSION of the agent template this runner ships (a newer runner may ship the same agent)."""
    try:
        mtime = os.path.getmtime(TEMPLATE)
    except OSError:
        return __version__
    cached = _TEMPLATE_VERSION.get(TEMPLATE)
    if cached and cached[0] == mtime:
        return cached[1]
    with open(TEMPLATE, encoding="utf-8") as f:
        match = re.search(r"const RA_VERSION = '([0-9A-Za-z.+-]{1,32})';", f.read(4000))
    version = match.group(1) if match else __version__
    _TEMPLATE_VERSION[TEMPLATE] = (mtime, version)
    return version


def console_line(command):
    """The command line for Symfony's StringInput, which removes backslashes (stripcslashes): every word quoted with its
    backslashes and quotes escaped, so a class name such as App\\Models\\User arrives as typed."""
    words = shlex.split(command)
    return " ".join(words[:1] + ['"%s"' % w.replace("\\", "\\\\").replace('"', '\\"') for w in words[1:]])


def tinker_code(command):
    """The PHP code of "tinker --execute=…" (run by the agent inside the application), or None."""
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    if not words or words[0] != "tinker":
        return None
    for index, word in enumerate(words[1:], 1):
        if word.startswith("--execute="):
            return word[len("--execute="):]
        if word == "--execute" and index + 1 < len(words):
            return words[index + 1]
    return None


def shown_command(command):
    """The command for logs and messages: the code of tinker may hold passwords, so it is left out."""
    return "tinker --execute=…" if tinker_code(command) is not None else command


def artisan_command(command):
    """A command line for artisan from the dashboard: one line, no shell; long-running commands are refused."""
    command = str(command or "").strip()
    if command.startswith("php artisan "):
        command = command[len("php artisan "):].strip()
    elif command.startswith("artisan "):
        command = command[len("artisan "):].strip()
    if not command or len(command) > 500 or re.search(r"[\r\n\x00]", command):
        raise DeployError("enter one artisan command, e.g. migrate:status")
    try:
        words = shlex.split(command)
    except ValueError as e:
        raise DeployError("the command cannot be read: %s" % e)
    name = words[0] if words else ""
    if not re.match(r"^[a-z0-9][a-z0-9:_-]*$", name):
        raise DeployError("not an artisan command: %s" % name)
    options = {w.split("=", 1)[0] for w in words[1:]}
    if (name in ARTISAN_REFUSED and not (name == "tinker" and "--execute" in options))             or (name == "queue:work" and not ({"--once", "--stop-when-empty"} & options)):
        raise DeployError("%s waits for input or runs until it is stopped; a hosting cannot keep a command running"
                          % name + (" (tinker works with --execute=\"...\")" if name == "tinker" else ""))
    return command


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
            raise AgentError(str(error.get("message") or "agent error"), str(error.get("code") or ""), e.code, error)
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
        self.uploads = deployer.uploads
        self.cache = {}

    @staticmethod
    def is_hosting(site):
        return site.get("deploy_mode") == "hosting"

    @staticmethod
    def is_laravel(site):
        return site.get("deploy_mode") == "hosting" and site.get("hosting_app") == "laravel"

    def protect(self, site):
        """Names in the website folder that a release never replaces (Laravel: the storage link)."""
        return list(site.get("adopt_keep", [])) + (["storage"] if self.is_laravel(site) else [])

    def status_params(self, site):
        return {"shared": site.get("shared", []), "protect": self.protect(site), "env_file": self.env_file(site)}

    @staticmethod
    def env_file(site):
        return site.get("backup_env_file") or ("@shared/.env" if site.get("hosting_app") == "laravel" else ".env")

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
            result = self.client(site, timeout=20).call("status", self.status_params(site))
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
                        "sha256": r.get("sha256"), "activated_at": r.get("activated_at")} for r in releases],
                      key=lambda r: r["installed_at"], reverse=True)

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
        keys = ("agent", "php", "site_dir", "data_outside", "zip", "zlib", "pdo", "max_execution_time", "memory_limit", "disk_free",
                "writable", "web_server", "symlink")
        return dict({k: status.get(k) for k in keys}, connected=True, url=site.get("agent_url"), error="",
                    insecure=str(site.get("agent_url") or "").startswith("http://"),
                    outdated=agent_outdated(status.get("agent"), template_version()),
                    self_update=not agent_outdated(status.get("agent"), LARAVEL_AGENT))

    # ------------------------------------------------------------------ the agent file itself
    def require_agent(self, site, client, info, log=None, wanted=LARAVEL_AGENT):
        """Make sure the agent can do what the job needs: an agent from 1.5.0 on is replaced with the agent template of
        this runner when that one is newer; an older one must be downloaded and dropped on the hosting again. -> status"""
        latest = template_version()
        if not agent_outdated(info.get("agent"), latest):
            return info
        if agent_outdated(info.get("agent"), LARAVEL_AGENT):
            if agent_outdated(info.get("agent"), wanted):
                raise DeployError("the agent file on the hosting is version %s; this needs %s or newer: download a new agent "
                                  "file in the dashboard, put it on the hosting in place of the old one and connect again"
                                  % (info.get("agent"), wanted))
            return info
        if log:
            log.step("Update the agent file on the hosting (%s -> %s)" % (info.get("agent"), latest))
        client.call("self-update", {"code": self.agent_code(site, info.get("dashboard") or "")})
        for attempt in range(6):                                     # PHP's opcache may keep the old code for a moment
            info = client.call("status", self.status_params(site))
            if not agent_outdated(info.get("agent"), latest):
                break
            time.sleep(2)
        if agent_outdated(info.get("agent"), wanted):
            raise DeployError("the hosting still runs the old agent (PHP opcache): try again in a minute")
        if log:
            log.line("agent %s on the hosting" % info.get("agent"))
        self.forget_cache(site)
        return info


    # ------------------------------------------------------------------ connecting
    def agent_file(self, site, dashboard):
        """A new agent file for the site, with a new one-time key (an earlier file that is not connected stops working)."""
        dashboard = str(dashboard or "").strip().rstrip("/")
        if not re.match(r"^https?://[A-Za-z0-9.:\[\]-]+(/[A-Za-z0-9._~%/-]*)?$", dashboard):
            raise DeployError("the dashboard address (APP_URL in the dashboard's .env) is not set or not valid: %s" % dashboard)
        key = secrets.token_hex(32)
        self.config.set_secret_of("agent_keys", site["id"], key)
        return {"filename": "rollout-agent-%s.php" % secrets.token_hex(3), "content": self.agent_code(site, dashboard, key),
                "site": site["id"]}

    @staticmethod
    def agent_code(site, dashboard, key=None):
        """The agent for the site. Without ``key`` (an update of a connected agent) the key is random and never given out:
        the agent marks it as used, so the updated file cannot connect anyone."""
        with open(TEMPLATE, encoding="utf-8") as f:
            template = f.read()
        dashboard = re.sub(r"[^A-Za-z0-9.:/\[\]_~%-]", "", str(dashboard or ""))
        return (template.replace("__RA_SITE__", site["id"]).replace("__RA_DASHBOARD__", dashboard)
                .replace("__RA_PAIRING_KEY__", key or secrets.token_hex(32)))

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
        try:                                                         # the .env prepared in the dashboard before connecting
            self.push_pending_env(site, AgentClient(url, secret, timeout=30), None)
        except DeployError:
            pass                                                     # sent again before the first deployment
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
        if exists(self.pending_env_path(site)):
            os.unlink(self.pending_env_path(site))

    # ------------------------------------------------------------------ the .env of the site
    def pending_env_path(self, site):
        return self.config.state_path("env", site["id"] + ".env")

    def pending_env(self, site):
        path = self.pending_env_path(site)
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()

    def push_pending_env(self, site, client, log):
        """Write the .env prepared in the dashboard to the hosting when the hosting has none (the hosting's file wins)."""
        content = self.pending_env(site)
        if content is None:
            return False
        current = client.call("env-read", {"file": self.env_file(site)})
        if not current.get("exists"):
            client.call("env-write", {"file": self.env_file(site), "content": content})
            if log:
                log.line("the .env prepared in the dashboard was written to the hosting")
        elif log:
            log.line("the hosting has its own .env already; the copy prepared in the dashboard is not used")
        os.unlink(self.pending_env_path(site))
        return True

    def env_read(self, site):
        """The .env of the site: from the hosting when it is connected, else the copy prepared in the dashboard; a template
        from the release's .env.example when there is none yet."""
        pending = self.pending_env(site)
        where, exists_now, content = "dashboard", pending is not None, pending or ""
        if self.status(site) is not None:
            client = self.client(site, timeout=30)
            self.require_agent(site, client, client.call("status", self.status_params(site)))
            hosted = client.call("env-read", {"file": self.env_file(site)})
            if hosted.get("exists") or pending is None:
                where, exists_now, content = "hosting", bool(hosted.get("exists")), hosted.get("content") or ""
        result = {"exists": exists_now, "content": content, "where": where, "file": self.env_file(site),
                  "laravel": self.is_laravel(site)}
        if not exists_now:
            result["template"] = self.env_template(site)
        return result

    def env_write(self, site, content):
        content = str(content or "").replace("\r\n", "\n")
        if len(content.encode("utf-8")) > ENV_LIMIT or "\x00" in content:
            raise DeployError("the .env is too large or not a text file")
        if not content.endswith("\n"):
            content += "\n"
        if self.status(site) is None:                                 # not connected yet: sent when it connects
            ensure_dir(os.path.dirname(self.pending_env_path(site)), 0o700)
            write_private(self.pending_env_path(site), content)
            return {"where": "dashboard", "saved": True}
        client = self.client(site, timeout=30)
        self.require_agent(site, client, client.call("status", self.status_params(site)))
        client.call("env-write", {"file": self.env_file(site), "content": content})
        if exists(self.pending_env_path(site)):
            os.unlink(self.pending_env_path(site))
        return {"where": "hosting", "saved": True}

    def newest_archive(self, site):
        """The newest uploaded version, or a GitHub release that is on this server already (no download for a template)."""
        if site.get("source") == "upload":
            versions = self.uploads.versions(site["id"])
            return self.uploads.archive(site["id"], versions[0]["version"]) if versions else None
        folder = os.path.dirname(self.archive_path(site, "x"))
        prefix = "hosting-%s-" % site["id"]
        found = [os.path.join(folder, n) for n in os.listdir(folder) if n.startswith(prefix) and n.endswith(".zip")] \
            if os.path.isdir(folder) else []
        return max(found, key=os.path.getmtime) if found else None

    def env_template(self, site):
        """.env.example of the newest release; for Laravel with production values and a new APP_KEY."""
        text = ""
        archive = self.newest_archive(site)
        if archive:
            with zipfile.ZipFile(archive) as z:
                prefix = _zip_prefix([i.filename for i in z.infolist()])
                for name in (prefix + ".env.example", prefix + ".env.production.example", prefix + ".env.sample"):
                    try:
                        text = z.read(name).decode("utf-8", "replace")
                        break
                    except KeyError:
                        continue
        if not self.is_laravel(site):
            return text
        if not text.strip():
            text = ("APP_NAME=Laravel\nAPP_ENV=production\nAPP_KEY=\nAPP_DEBUG=false\nAPP_URL=\n\nDB_CONNECTION=mysql\nDB_HOST=localhost\n"
                    "DB_PORT=3306\nDB_DATABASE=\nDB_USERNAME=\nDB_PASSWORD=\n\nSESSION_DRIVER=file\nCACHE_STORE=file\nQUEUE_CONNECTION=sync\n")
        url = site_url(site) or ("https://" + site["domain"] if site.get("domain") else "")
        values = {"APP_ENV": "production", "APP_DEBUG": "false", "APP_KEY": "base64:" + base64.b64encode(os.urandom(32)).decode()}
        if url:
            values["APP_URL"] = url
        for key, value in values.items():
            if re.search(r"^%s=.*$" % key, text, re.M):
                text = re.sub(r"^%s=.*$" % key, lambda m: key + "=" + value, text, count=1, flags=re.M)
            else:
                text = key + "=" + value + "\n" + text
        return text

    # ------------------------------------------------------------------ releases
    def archive_path(self, site, version):
        return self.config.state_path("downloads", "hosting-%s-%s.zip" % (site["id"], re.sub(r"[^A-Za-z0-9._-]", "-", version)))

    def fetch_release(self, site, version, log):
        """The release zip on this server: an uploaded version, or the GitHub release downloaded once for the plan and
        the deployment (Laravel: cleaned and completed with composer when vendor/ is missing)."""
        if site.get("source") == "upload":
            archive = self.uploads.archive(site["id"], version)
            if not archive:
                raise DeployError("version %s is not among the uploads of this site: upload it in the dashboard" % version)
            return archive
        github = self.deployer.github(site)
        release = github.release(site["repo"], version, release_pattern(site))
        asset = release["asset"]
        if not asset:
            raise DeployError("release %s has no asset matching %s (assets: %s)" % (
                version, site["asset_pattern"], ", ".join(release["assets"]) or "none"))
        archive = self.archive_path(site, version)
        # a completed Laravel archive differs from the download (cleaned, vendor/ added); it only exists when complete
        if exists(archive) and (self.is_laravel(site) or not asset.get("size") or os.path.getsize(archive) == asset["size"]):
            log.line("release %s already downloaded" % version)
            return archive
        if asset.get("source"):
            log.step("Download the source code of %s" % release["tag"])
        else:
            log.step("Download %s (%s)" % (asset["name"], human_size(asset["size"])))
        download = archive + ".download" if self.is_laravel(site) else archive
        try:
            self.deployer.fetch(github, asset, download, log)
            if download != archive:
                self.complete_archive(site, download, archive, log)
        except Exception:
            for path in (download, archive):
                if exists(path):
                    os.unlink(path)
            raise
        finally:
            if download != archive and exists(download):
                os.unlink(download)
        return archive

    def complete_archive(self, site, source, target, log):
        """A Laravel release from GitHub: unpacked, cleaned like an upload, vendor/ installed with composer, packed again."""
        work = long_path(target + ".work")
        if exists(work):
            remove(work)
        try:
            unpack(self.config, source, work, log)
            root = project_root(work)
            prepare_tree(self.config, site, root, log)
            pack(root, target)
        except UploadError as e:
            raise DeployError(str(e))
        finally:
            if exists(work):
                remove(work)

    def plan(self, site, version):
        """Database changes a release brings (for the confirmation before an update): its migration files that the
        hosting has not run yet, and files that changed after they were run."""
        if not VERSION.match(version or ""):
            raise DeployError("invalid version: %s" % version)

        archive = self.fetch_release(site, version, _Quiet())
        folder = (site.get("migrations_dir") or "database/migrations").strip("/") + "/"
        laravel = self.is_laravel(site)
        found = []
        with zipfile.ZipFile(archive) as z:
            names = [i.filename for i in z.infolist()]
            prefix = _zip_prefix(names)
            for name in names:
                rel = name[len(prefix):] if prefix and name.startswith(prefix) else name
                if rel.startswith(folder) and rel.endswith(".php" if laravel else ".sql") and "/" not in rel[len(folder):]:
                    data = z.read(name)
                    found.append({"name": rel[len(folder):], "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                                  "sql": data[:20000].decode("utf-8", "replace"), "truncated": len(data) > 20000})
        if laravel:
            return self.laravel_plan(site, version, folder, found)
        applied = ((self.status(site, refresh=True) or {}).get("migrations") or {})
        changes = []
        for item in sorted(found, key=lambda m: _natural(m["name"])):
            done = applied.get(item["name"]) if isinstance(applied, dict) else None
            if not done:
                changes.append(dict(item, status="pending"))
            elif done.get("sha256") != item["sha256"]:
                changes.append(dict(item, status="changed"))
        return {"version": version, "folder": folder.rstrip("/"), "migrations": changes, "kind": "sql"}

    def laravel_plan(self, site, version, folder, found):
        """Laravel: the migration files of the release that are not in the database's migrations table yet."""
        plan = {"version": version, "folder": folder.rstrip("/"), "kind": "laravel", "migrations": [], "error": ""}
        applied = None
        if self.status(site) is not None and "error" not in (self.status(site) or {}):
            try:
                client = self.client(site, timeout=30)
                self.require_agent(site, client, client.call("status", self.status_params(site)))
                applied = set(client.call("laravel-migrations", {"env_file": self.env_file(site)}).get("applied") or [])
            except DeployError as e:
                plan["error"] = str(e)
        for item in sorted(found, key=lambda m: _natural(m["name"])):
            if applied is None or item["name"][:-4] not in applied:
                plan["migrations"].append(dict(item, status="pending" if applied is not None else "unknown"))
        return plan

    def check_php(self, site, archive, info, log):
        """A release whose composer.json asks for a newer PHP than the hosting has would only fail on the hosting."""
        with zipfile.ZipFile(archive) as z:
            prefix = _zip_prefix([i.filename for i in z.infolist()])
            try:
                requirement = str((json.loads(z.read(prefix + "composer.json").decode("utf-8")).get("require") or {}).get("php") or "")
            except (KeyError, ValueError, AttributeError):
                return
        if requirement and not php_satisfies(requirement, str(info.get("php") or "")):
            raise DeployError("the release needs PHP %s (composer.json) and the hosting runs PHP %s: choose a newer PHP version "
                              "in the panel of the hosting" % (requirement, info.get("php")))
        if requirement:
            log.line("PHP %s on the hosting, the release asks for %s" % (info.get("php"), requirement))

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
        data = dict(self.status_params(site), version=version, docroot_target=site.get("docroot_target") or ".",
                    laravel=self.is_laravel(site))
        try:
            result = client.call("activate", data)
        except AgentError as e:
            if e.code != "busy":
                raise
            for _ in range(200):                                     # a large folder is still being deleted
                status = client.call("status", self.status_params(site))
                if not status.get("journal"):
                    result = {"previous": status.get("previous")}
                    break
            else:
                raise
        log.line("live: %s (before: %s)" % (version, result.get("previous") or "nothing"))
        if self.is_laravel(site) and version != PRE:
            self.laravel_finish(site, client, version, log)
        self.forget_cache(site)
        return result

    # ------------------------------------------------------------------ Laravel
    def laravel_prepare(self, site, client, version, adopt, log):
        log.step("Prepare the Laravel application of %s (.env, storage/, public/index.php)" % version)
        result = client.call("laravel-prepare", {"version": version, "adopt": bool(adopt)})
        notes = {"copied": ".env taken over from the website folder", "generated": "APP_KEY was empty: a new key was generated",
                 "seeded": "storage/ created from the release", "created": "storage/ created", "website": "storage/ of the running "
                 "website is taken over when the website switches", "shared": "storage/ of the earlier versions is used"}
        for key in ("env", "app_key", "storage"):
            if result.get(key) in notes:
                log.line(notes[result[key]])
        log.line("the application stays in the agent's data folder%s; the website folder gets public/" %
                 ("" if result.get("data_outside") else " (inside the website folder, hidden; keep APP_DEBUG=false)"))

    def artisan(self, site, client, version, command, log, check=True):
        """Run an artisan command of a release inside PHP on the hosting (no terminal needed). -> exit code"""
        log.line("$ php artisan " + shown_command(command))
        code = tinker_code(command)
        data = {"version": version, "timeout": ARTISAN_SECONDS}
        data.update({"code": code} if code is not None else {"command": console_line(command)})
        try:
            result = client.call("artisan", data)
        except AgentError as e:
            for line in str(e.details.get("output") or "").splitlines():
                log.output(line)
            raise
        for line in str(result.get("output") or "").splitlines():
            log.output(line)
        status = int(result.get("status") or 0)
        if check and status != 0:
            raise DeployError("php artisan %s failed (exit code %d)" % (command.split()[0], status))
        return status

    def laravel_migrate(self, site, client, version, mode, log):
        if mode != "auto":
            log.line("database migrations not run (chosen): run them later with php artisan migrate in the dashboard")
            return
        log.step("Run the database migrations of %s (php artisan migrate on the hosting)" % version)
        self.artisan(site, client, version, "migrate --force", log)

    def laravel_finish(self, site, client, version, log):
        result = client.call("laravel-finish", {"version": version})
        link = result.get("storage_link")
        if link == "created":
            log.line("public storage link created (storage -> storage/app/public)")
        elif link and link not in ("exists", "created"):
            log.line("note: the hosting does not allow the storage link (%s): files of the public disk are not served" % link)
        if result.get("views_cleared"):
            log.line("%d compiled views cleared" % result["views_cleared"])

    def run_artisan(self, site, command, log):
        """The artisan job of the dashboard: a command on the live release."""
        command = artisan_command(command)
        client = self.client(site, timeout=ARTISAN_SECONDS + 30)
        info = self.require_agent(site, client, client.call("status", self.status_params(site)), log)
        version = info.get("current")
        if not version:
            raise DeployError("no release is live on the hosting yet: deploy one first")
        self.artisan(site, client, version, command, log)
        return version

    # ------------------------------------------------------------------ jobs
    def deploy(self, job, site, log, adopt=False):
        version = job["version"]
        client = self.client(site)
        info = client.call("status", self.status_params(site))
        laravel = self.is_laravel(site)
        info = self.require_agent(site, client, info, log, LARAVEL_AGENT if laravel else "0")
        current = info.get("current")
        again = bool(job["params"].get("redeploy")) and not adopt
        if version == current and not again:
            raise DeployError("%s is already live on the hosting (Deploy again runs its preparation and database changes "
                              "once more)" % version)
        if again and version != current:
            raise DeployError("only the live release can be deployed again; %s is not live" % version)
        if adopt and current:
            raise DeployError("nothing to adopt: the hosting already runs release %s" % current)
        if not adopt and not current and info.get("live"):
            raise DeployError("the website folder holds files that were not installed by Arsika Rollout Manager; use Adopt first")
        log.line("hosting: PHP %s, agent %s, website folder %s%s" % (info.get("php"), info.get("agent"), info.get("site_dir"),
                                                                   ", " + info["web_server"] if info.get("web_server") else ""))
        if not agent_outdated(info.get("agent"), LARAVEL_AGENT):
            self.push_pending_env(site, client, log)
        on_hosting = version in {r["version"] for r in info.get("releases") or []}
        if not on_hosting:                                           # before the backup: a release that cannot run stops here
            self.check_php(site, self.fetch_release(site, version, log), info, log)
        if job["params"].get("backup", site.get("backup_before_deploy")) and (current or info.get("live")):
            self.backup(site, current, ("before adopting " if adopt else "before deploying ") + version, log, job["id"])
        if again:
            log.step("Deploy %s again (it stays live): preparation and database changes" % version)
        elif on_hosting:
            log.step("Release %s is still on the hosting; reusing it" % version)
        else:
            self.upload_release(site, client, version, log, info)
        # an adopted website already runs this version, so its database has these changes: mark them as done unless asked
        mode = str(job["params"].get("migrations") or ("manual" if adopt else "auto"))
        if laravel:
            self.laravel_prepare(site, client, version, adopt, log)
            self.laravel_migrate(site, client, version, mode, log)
        else:
            self.migrate(site, client, version, mode, log)
        if again:
            if laravel:
                self.laravel_finish(site, client, version, log)
            self.forget_cache(site)
            if not self.deployer.healthy(site, version, log):
                raise DeployError("health check failed for %s" % version)
            self.store.mark_activated(site["id"], version)
            return current, version
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
        info = client.call("status", self.status_params(site))
        if self.is_laravel(site):
            info = self.require_agent(site, client, info, log)
        current = info.get("current")
        target = job["version"] or self.rollback_target(site)
        if not target:
            raise DeployError("no earlier release on the hosting to roll back to")
        if target == current:
            raise DeployError("%s is already the current release" % target)
        kept = {r["version"]: r for r in info.get("releases") or []}
        if target in kept and not kept[target].get("activated_at"):
            # e.g. its deployment stopped before the switch: it was never prepared and its database changes never ran
            raise DeployError("release %s never went live on the hosting: deploy it instead (Deploy prepares it and runs its "
                              "database changes)" % target)
        if self.is_laravel(site):
            self.laravel_prepare(site, client, target, False, log)    # .env, storage/ and its public/index.php
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
        if site.get("source") == "upload":
            kept = {r["version"] for r in releases if r not in removable[:max(0, len(releases) - keep)]}
            self.uploads.prune(site, kept | protected, log)

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


def _zip_prefix(names):
    """The single top folder of a zip (GitHub source archives), or ''."""
    tops = {n.split("/", 1)[0] for n in names if n.strip("/")}
    return next(iter(tops)) + "/" if len(tops) == 1 and any("/" in n.strip("/") for n in names) else ""


class _Quiet(object):
    def line(self, *a):
        pass
    step = error = output = line
