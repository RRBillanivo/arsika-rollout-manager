"""Commands on the server: site hooks, systemd services, PHP-FPM reload and HTTP health checks."""
import glob
import http.client
import os
import re
import shutil
import socket
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlsplit

from . import __version__
from .util import POSIX

PRESET_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "presets")


class CommandError(Exception):
    pass


def run(argv, log, cwd=None, env=None, timeout=3600, check=True):
    """Run a command, copying its output line by line into the job log. -> exit code"""
    log.line("$ " + " ".join(argv) if len(" ".join(argv)) < 300 else "$ " + argv[0] + " ...")
    try:
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    except OSError as e:
        raise CommandError("cannot start %s: %s" % (argv[0], e))
    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    try:
        for raw in iter(proc.stdout.readline, b""):
            log.output(raw.decode("utf-8", "replace").rstrip("\r\n"))
        code = proc.wait()
    finally:
        timer.cancel()
        proc.stdout.close()
    if check and code != 0:
        raise CommandError("%s exited with code %d" % (os.path.basename(argv[0]), code) + (" (timeout)" if code in (-9, 137) else ""))
    return code


def hook_command(command, phase, shell):
    """Shell command of a hook: "preset:<name>" runs runner/presets/<name>.sh <phase>."""
    if command.startswith("preset:"):
        script = os.path.join(PRESET_DIR, command[7:] + ".sh")
        if not os.path.isfile(script):
            raise CommandError("preset script not found: " + script)
        return [shell, script, phase]
    return [shell, "-c", command]


def run_hook(site, phase, command, cwd, variables, log, shell, timeout):
    if not command:
        return
    log.step("Hook %s: %s" % (phase, command if len(command) < 120 else command[:117] + "..."))
    env = dict(os.environ)
    env.update({k: str(v) for k, v in variables.items()})
    env["ROLLOUT_PHASE"] = phase
    run(hook_command(command, phase, shell), log, cwd=cwd, env=env, timeout=timeout)


def restart_services(services, log):
    for name in services:
        if not POSIX or not shutil.which("systemctl"):
            log.line("systemctl is not available; skipped restart of " + name)
            continue
        log.step("Restart service " + name)
        run(["systemctl", "restart", name], log, timeout=180)
        time.sleep(1)
        code = run(["systemctl", "is-active", "--quiet", name], log, timeout=30, check=False)
        if code != 0:
            run(["systemctl", "status", "--no-pager", "--lines=20", name], log, timeout=30, check=False)
            raise CommandError("service %s is not running after the restart" % name)


def service_states(services):
    states = {}
    for name in services:
        if not POSIX or not shutil.which("systemctl"):
            states[name] = "unknown"
            continue
        try:
            out = subprocess.run(["systemctl", "is-active", name], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 timeout=10).stdout.decode().strip()
        except (OSError, subprocess.SubprocessError):
            out = "unknown"
        states[name] = out or "unknown"
    return states


def reload_php_fpm(log):
    """Graceful reload of every PHP-FPM version, so OPcache and the realpath cache see the new 'current' at once."""
    if not POSIX:
        log.line("PHP-FPM reload skipped (not Linux)")
        return
    scripts = sorted(glob.glob("/etc/init.d/php-fpm-*"))            # aaPanel
    if scripts:
        for script in scripts:
            run([script, "reload"], log, timeout=60, check=False)
        return
    if shutil.which("systemctl"):
        try:
            units = subprocess.run(["systemctl", "list-units", "--type=service", "--state=running", "--no-legend", "--plain"],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15).stdout.decode()
        except (OSError, subprocess.SubprocessError):
            units = ""
        names = [line.split()[0] for line in units.splitlines() if line.strip() and "php" in line.split()[0] and "fpm" in line.split()[0]]
        for name in names:
            run(["systemctl", "reload", name], log, timeout=60, check=False)
        if names:
            return
    log.line("No PHP-FPM service found to reload")


class _LocalHTTPS(http.client.HTTPSConnection):
    """HTTPS to 127.0.0.1 that presents the site's own host name (SNI), so the web server picks the site. The certificate
    is not checked: the connection never leaves this machine, and origin certificates (e.g. Cloudflare's) are not
    publicly trusted anyway."""

    def __init__(self, port, server_name, timeout):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        http.client.HTTPSConnection.__init__(self, "127.0.0.1", port, timeout=timeout, context=context)
        self.server_name = server_name

    def connect(self):
        sock = socket.create_connection((self.host, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.server_name)


def local_get(url, timeout=10, redirects=1):
    """GET url from the web server of this machine (127.0.0.1) with the url's host name, so a site behind a CDN,
    Cloudflare Access or a firewall is checked without leaving the server. A redirect to the same host (e.g. http ->
    https) is followed on this machine as well. -> (status, body text)"""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("not an http(s) address: " + url)
    secure = parts.scheme == "https"
    port = parts.port or (443 if secure else 80)
    conn = _LocalHTTPS(port, parts.hostname, timeout) if secure else http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", (parts.path or "/") + ("?" + parts.query if parts.query else ""),
                     headers={"Host": parts.netloc.rsplit("@", 1)[-1], "User-Agent": "arsika-rollout-manager/" + __version__,
                              "Cache-Control": "no-cache"})
        response = conn.getresponse()
        location = response.getheader("Location") or ""
        if response.status in (301, 302, 303, 307, 308) and location and redirects > 0:
            target = urljoin(url, location)
            if urlsplit(target).hostname == parts.hostname:
                return local_get(target, timeout, redirects - 1)
        return response.status, response.read(1 << 20).decode("utf-8", "replace")
    finally:
        conn.close()


def http_get(url, timeout=10, local=False):
    """GET url -> (status, body text). Certificate errors are reported, not ignored. ``local``: ask this machine's web
    server (see local_get)."""
    if local:
        return local_get(url, timeout)
    request = urllib.request.Request(url, headers={"User-Agent": "arsika-rollout-manager/" + __version__, "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
            return response.status, response.read(1 << 20).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(1 << 16).decode("utf-8", "replace")


# PHP prints a fatal error after its warnings, so the status of the page can still be 200
PHP_ERROR = re.compile(r"(?:^|<br\s*/?>|\n)\s*(?:<b>)?(?:PHP )?(Fatal error|Parse error)(?:</b>)?:\s", re.I)


def php_error(body):
    """'Fatal error' / 'Parse error' when the page is PHP's own error output, else None."""
    match = PHP_ERROR.search(body or "")
    return match.group(1) if match else None


def health_check(url, contains, log=None, attempts=10, delay=3.0, local=False, fetch=None):
    """True when url answers 200 (and contains the given text) within the given attempts. ``fetch(url)``: another way to
    GET (the agent of a hosting in another network)."""
    last = ""
    for attempt in range(1, attempts + 1):
        try:
            status, body = fetch(url) if fetch else http_get(url, local=local)
            error = php_error(body)
            if status == 200 and not error and (not contains or contains in body):
                if log:
                    log.line("health check OK: %s (attempt %d)" % (url, attempt))
                return True
            last = "HTTP %d" % status + ("" if status != 200 else ", the page shows a PHP %s" % error.lower() if error
                                         else ', text "%s" not found' % contains)
        except Exception as e:                                    # network errors, timeouts, TLS
            last = "%s: %s" % (type(e).__name__, e)
        if log:
            log.line("health check attempt %d/%d: %s" % (attempt, attempts, last))
        if attempt < attempts:
            time.sleep(delay)
    return False


def quick_health(url, contains, timeout=8, local=False, fetch=None):
    """One request for the dashboard overview. -> dict(ok, status, ms, error)"""
    t0 = time.time()
    try:
        status, body = fetch(url, timeout) if fetch else http_get(url, timeout=timeout, local=local)
        error = php_error(body)
        ok = status == 200 and not error and (not contains or contains in body)
        return {"ok": ok, "status": status, "ms": int((time.time() - t0) * 1000), "error": "" if ok else
                ("HTTP %d" % status if status != 200 else "PHP %s on the page" % error.lower() if error else "expected text not found")}
    except Exception as e:
        return {"ok": False, "status": 0, "ms": int((time.time() - t0) * 1000), "error": "%s: %s" % (type(e).__name__, str(e)[:150])}


def platform_name():
    """aapanel when aaPanel is installed on this server, else linux."""
    if os.path.isdir("/www/server/panel"):
        return "aapanel"
    return "linux" if POSIX else os.name


def disk_free(path):
    try:
        usage = shutil.disk_usage(path)
        return {"total": usage.total, "free": usage.free}
    except OSError:
        return None
