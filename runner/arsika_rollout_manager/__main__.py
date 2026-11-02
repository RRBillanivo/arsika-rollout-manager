"""Command line of the runner (run as root on the server):

    python3 -m arsika_rollout_manager serve         the service (systemd unit arsika-rollout-manager-runner)
    python3 -m arsika_rollout_manager init [--site-root=/www/wwwroot] [--backup-dir=...]
                                                    create /etc/arsika-rollout-manager/runner.json
    python3 -m arsika_rollout_manager secret        print the shared secret (for the dashboard's .env)
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
import sys
import time

from . import __version__
from .config import Config


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
    host = config["listen"] if config["listen"] != "localhost" else "127.0.0.1"
    return Client("http://%s:%d" % (host, int(config["port"])), config["secret"])


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
        overrides = {}
        if isinstance(options.get("backup-dir"), str):
            overrides["backup_dir"] = options["backup-dir"]
        if isinstance(options.get("state-dir"), str):
            overrides["state_dir"] = options["state-dir"]
        if isinstance(options.get("site-root"), str):
            overrides["site_roots"] = [r for r in options["site-root"].split(",") if r]
        config = Config.create(config_path, **overrides)
        config.prepare_dirs()
        print("Configuration: " + config.path)
        return 0

    config = Config(config_path)
    if command == "secret":
        print(config["secret"])
        return 0
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
    print("Arsika Rollout Manager runner %s listening on http://%s:%d" % (__version__, config["listen"], int(config["port"])), flush=True)

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
