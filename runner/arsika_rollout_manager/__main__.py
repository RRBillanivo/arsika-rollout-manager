"""Command line of the runner (run as root on the server):

    python3 -m arsika_rollout_manager serve         the service (systemd unit arsika-rollout-manager-runner)
    python3 -m arsika_rollout_manager init [--site-root=/www/wwwroot,...] [--backup-dir=...] [--state-dir=...]
                 [--web-user=www] [--platform=aapanel|linux] [--listen=0.0.0.0] [--port=8790]
                 [--tls-cert=FILE --tls-key=FILE] [--allow-from=192.168.1.10,10.8.0.0/24]
                                                    create /etc/arsika-rollout-manager/runner.json (an existing file
                                                    is kept; the given options are changed in it)
    python3 -m arsika_rollout_manager secret        print the shared secret (for the dashboard's .env)
    python3 -m arsika_rollout_manager connection-info
                                                    address, secret and key pin to add this server in a dashboard
                                                    on another server (Servers > Add server)
    python3 -m arsika_rollout_manager status        runner and site overview
    python3 -m arsika_rollout_manager deploy <site> <version> [--no-backup]
    python3 -m arsika_rollout_manager rollback <site> [<version>]
    python3 -m arsika_rollout_manager backup <site>
    python3 -m arsika_rollout_manager log <job id>  show (and follow) a job log

Every command except serve/init/secret talks to the running service, so jobs never run twice at the same time.
Options: --config=/etc/arsika-rollout-manager/runner.json
"""
import os
import signal
import socket
import sys
import time

from . import __version__
from .config import LOOPBACK, Config
from .util import certificate_pin

INIT_OPTIONS = {"backup-dir": "backup_dir", "state-dir": "state_dir", "web-user": "web_user", "platform": "platform",
                "listen": "listen", "tls-cert": "tls_cert", "tls-key": "tls_key"}


def parse(argv):
    args, options = [], {}
    for item in argv:
        if item.startswith("--"):
            key, _, value = item[2:].partition("=")
            options[key] = value if value else True
        else:
            args.append(item)
    return args, options


def client(config):
    from .client import Client
    host = "127.0.0.1" if config["listen"] in ("localhost", "0.0.0.0") else "[::1]" if config["listen"] == "::" else config["listen"]
    if ":" in host and not host.startswith("["):
        host = "[%s]" % host
    if config.tls_enabled():
        return Client("https://%s:%d" % (host, int(config["port"])), config["secret"], pin=certificate_pin(config["tls_cert"]))
    return Client("http://%s:%d" % (host, int(config["port"])), config["secret"])


def own_address():
    """Address of this server in its network (the one used for outgoing traffic; nothing is sent)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        return probe.getsockname()[0]
    except OSError:
        return "SERVER-ADDRESS"
    finally:
        probe.close()


def connection_info(config):
    if not config.tls_enabled():
        print("This runner only answers on %s (no HTTPS): it is controlled by the dashboard on this server.\n"
              "To control it from a dashboard on another server, run: sudo bash deploy/install.sh --agent" % config["listen"])
        return 1
    try:
        pin = certificate_pin(config["tls_cert"])
    except (OSError, ValueError) as e:
        print("Cannot read the certificate %s: %s" % (config["tls_cert"], e))
        return 1
    listen = config["listen"]
    host = own_address() if listen in ("0.0.0.0", "::") else listen
    print("Add this server in the dashboard (Servers > Add server):")
    print("  Address : https://%s:%d" % ("[%s]" % host if ":" in host else host, int(config["port"])))
    print("  Secret  : %s" % config["secret"])
    print("  Key pin : %s" % pin)
    allowed = ", ".join(str(n) for n in config.allowed) or "any address (set allow_from to limit it)"
    print("Allowed to connect: %s" % allowed)
    if listen in LOOPBACK:
        print("Note: the runner listens on %s only; other servers cannot reach it." % listen)
    return 0


def follow(api, job_id):
    offset = 0
    while True:
        status, doc = api.get("/v1/jobs/%d?offset=%d" % (job_id, offset))
        if status != 200:
            print(doc.get("error", {}).get("message", doc))
            return 1
        job = doc["data"]
        sys.stdout.write(job["log"]["text"])
        sys.stdout.flush()
        offset = job["log"]["offset"]
        if job["status"] in ("succeeded", "failed") and offset >= job["log"]["size"]:
            print("\nJob #%d %s: %s" % (job_id, job["status"], job["message"] or ""))
            return 0 if job["status"] == "succeeded" else 1
        time.sleep(1)


def main(argv=None):
    args, options = parse(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "help"
    config_path = options.get("config") if isinstance(options.get("config"), str) else None

    if command == "init":
        overrides = {key: options[option] for option, key in INIT_OPTIONS.items() if isinstance(options.get(option), str)}
        if isinstance(options.get("site-root"), str):
            overrides["site_roots"] = [r for r in options["site-root"].split(",") if r]
        if isinstance(options.get("allow-from"), str):
            overrides["allow_from"] = [str(n) for n in Config.networks(options["allow-from"])]
        if isinstance(options.get("port"), str):
            overrides["port"] = int(options["port"])
        config = Config.create(config_path)                          # a new file gets a random secret
        merged = dict(config.values, **overrides)                    # options given now win over an existing file
        if merged["listen"] not in LOOPBACK and not (merged.get("tls_cert") and merged.get("tls_key")):
            print("--listen=%s needs --tls-cert and --tls-key: a runner reachable from the network must use HTTPS" % merged["listen"])
            return 2
        if merged != config.values:
            config.values = merged
            config.allowed = Config.networks(merged.get("allow_from") or [])
            config.save()
        config.prepare_dirs()
        print("Configuration: " + config.path)
        return 0

    config = Config(config_path)
    if command == "secret":
        print(config["secret"])
        return 0
    if command == "connection-info":
        return connection_info(config)
    if command == "serve":
        return serve(config)
    if command in ("help", "-h", "--help"):
        print(__doc__)
        return 0

    api = client(config)
    try:
        if command == "status":
            status, doc = api.get("/v1/status")
            if status != 200:
                print(doc)
                return 1
            print("Arsika Rollout Manager runner %(version)s (Python %(python)s), up %(uptime_seconds)ss" % doc["data"])
            _, sites = api.get("/v1/sites?health=0")
            for s in sites.get("data", []):
                print("  %-16s %-10s current %-10s latest %-10s %s" % (s["id"], s["state"], s["current_version"] or "-",
                                                                         s["latest_version"] or "-", s["domain"]))
            return 0
        if command in ("deploy", "rollback", "backup"):
            if len(args) < 2 or (command == "deploy" and len(args) < 3):
                print(__doc__)
                return 2
            payload = {"type": command, "version": args[2] if len(args) > 2 else None, "actor": "console",
                       "params": {"backup": not options.get("no-backup")}}
            status, doc = api.post("/v1/sites/%s/jobs" % args[1], payload)
            if status != 200:
                print(doc.get("error", {}).get("message", doc))
                return 1
            return follow(api, doc["data"]["id"])
        if command == "log" and len(args) > 1:
            return follow(api, int(args[1]))
    except OSError as e:
        print("Cannot reach the runner service (%s). Is it running? systemctl status arsika-rollout-manager-runner" % e)
        return 1
    print(__doc__)
    return 2


def serve(config):
    from .backup import Backups
    from .deploy import Deployer
    from .server import Api, serve as make_server
    from .store import Store
    from .worker import Worker

    if not config["secret"]:
        print("No secret in %s; run: python3 -m arsika_rollout_manager init" % config.path)
        return 1
    config.prepare_dirs()
    os.umask(0o022)
    store = Store(config.state_path("runner.db"))
    backups = Backups(config, store)
    deployer = Deployer(config, store, backups)
    worker = Worker(config, store, deployer)
    worker.start()
    api = Api(config, store, deployer, worker)
    server = make_server(api)
    print("Arsika Rollout Manager runner %s listening on %s://%s:%d%s" % (
        __version__, "https" if config.tls_enabled() else "http", config["listen"], int(config["port"]),
        " (allowed: %s)" % ", ".join(str(n) for n in config.allowed) if config.allowed else ""), flush=True)

    def stop(*_args):
        worker.stopping = True
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
