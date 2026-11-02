"""Commands on the server: site hooks, systemd services, PHP-FPM reload and HTTP health checks."""
import glob
import os
import shutil
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request

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


def http_get(url, timeout=10):
    """GET url -> (status, body text). Certificate errors are reported, not ignored."""
    request = urllib.request.Request(url, headers={"User-Agent": "arsika-rollout-manager/" + __version__, "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
            return response.status, response.read(1 << 20).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(1 << 16).decode("utf-8", "replace")


def health_check(url, contains, log=None, attempts=10, delay=3.0):
    """True when url answers 200 (and contains the given text) within the given attempts."""
    last = ""
    for attempt in range(1, attempts + 1):
        try:
            status, body = http_get(url)
            if status == 200 and (not contains or contains in body):
                if log:
                    log.line("health check OK: %s (attempt %d)" % (url, attempt))
                return True
            last = "HTTP %d" % status + ("" if status != 200 else ', text "%s" not found' % contains)
        except Exception as e:                                    # network errors, timeouts, TLS
            last = "%s: %s" % (type(e).__name__, e)
        if log:
            log.line("health check attempt %d/%d: %s" % (attempt, attempts, last))
        if attempt < attempts:
            time.sleep(delay)
    return False


def quick_health(url, contains, timeout=8):
    """One request for the dashboard overview. -> dict(ok, status, ms, error)"""
    t0 = time.time()
    try:
        status, body = http_get(url, timeout=timeout)
        ok = status == 200 and (not contains or contains in body)
        return {"ok": ok, "status": status, "ms": int((time.time() - t0) * 1000), "error": "" if ok else
                ("HTTP %d" % status if status != 200 else "expected text not found")}
    except Exception as e:
        return {"ok": False, "status": 0, "ms": int((time.time() - t0) * 1000), "error": "%s: %s" % (type(e).__name__, str(e)[:150])}


def disk_free(path):
    try:
        usage = shutil.disk_usage(path)
        return {"total": usage.total, "free": usage.free}
    except OSError:
        return None
