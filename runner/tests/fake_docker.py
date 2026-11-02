"""Stand-in for the docker command in the runner tests (no Docker needed). It understands the commands the runner uses
and keeps what happened in a JSON state file (FAKE_DOCKER_STATE):

    docker compose version --short
    docker compose [-p P] --project-directory DIR -f FILE... config --format json | build SVC... | pull ... SVC... |
                   up -d --remove-orphans | exec -T [-e K=V] SVC sh -c SCRIPT
    docker image rm IMAGE         docker volume inspect NAME        docker ps -q --filter volume=NAME
    docker stop|start ID...       docker run --rm -v VOL:/volume -v DIR:/backup busybox:1.36 sh -c "tar ..."

Compose files must be written as JSON (which is also YAML). "up" copies DIR/public/version.txt into FAKE_DOCKER_WEBROOT,
as if the web container now served the release. Named volumes are folders in <state dir>/volumes/<name>.
"""
import json
import os
import re
import shutil
import sys
import tarfile

STATE = os.environ["FAKE_DOCKER_STATE"]
VOLUMES = os.path.join(os.path.dirname(STATE), "volumes")


def load():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(state):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)


def record(state, key, value):
    state.setdefault(key, []).append(value)


def project_name(text):
    return re.sub(r"[^a-z0-9_-]", "", text.lower()).lstrip("_-")


def env_file(directory):
    values = {}
    path = os.path.join(directory, ".env")
    if os.path.isfile(path):
        for line in open(path, encoding="utf-8").read().splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return values


def resolved(project, directory, files):
    """The merged compose configuration, as docker compose config --format json shows it."""
    doc = {"services": {}, "volumes": {}}
    for name in files:
        if name.endswith(".rollout-compose.yml"):
            continue
        text = open(name, encoding="utf-8").read().replace("${ROLLOUT_VERSION}", os.environ.get("ROLLOUT_VERSION", ""))
        part = json.loads(text)
        for svc, spec in part.get("services", {}).items():
            doc["services"].setdefault(svc, {}).update(spec)
        doc["volumes"].update(part.get("volumes", {}))
        if part.get("name"):
            doc["name"] = part["name"]
    name = project or doc.get("name") or env_file(directory).get("COMPOSE_PROJECT_NAME") or project_name(os.path.basename(directory))
    services = {}
    for svc, spec in doc["services"].items():
        spec = dict(spec)
        mounts = []
        for item in spec.get("volumes", []):
            source, target = item.split(":")[:2]
            if source.startswith((".", "/")):
                mounts.append({"type": "bind", "source": os.path.normpath(os.path.join(directory, source)), "target": target})
            else:
                mounts.append({"type": "volume", "source": source, "target": target})
        spec["volumes"] = mounts
        services[svc] = spec
    volumes = {key: {"name": (spec or {}).get("name") or "%s_%s" % (name, key)} for key, spec in doc["volumes"].items()}
    return {"name": name, "services": services, "volumes": volumes}


def override_images(files):
    images = {}
    for name in files:
        if name.endswith(".rollout-compose.yml"):
            text = open(name, encoding="utf-8").read()
            images.update(dict(re.findall(r'^  "([^"]+)":\n    image: "([^"]+)"$', text, re.M)))
    return images


def compose(args, state):
    if args[:1] == ["version"]:
        if state.get("no_compose"):
            return 1
        print("2.29.1")
        return 0
    project, directory, files = None, None, []
    while args and args[0] in ("-p", "--project-directory", "-f"):
        option, value, args = args[0], args[1], args[2:]
        if option == "-p":
            project = value
        elif option == "--project-directory":
            directory = value
        else:
            files.append(value)
    directory = directory or os.path.dirname(files[0])
    config = resolved(project, directory, files)
    name, command, rest = config["name"], args[0], args[1:]
    record(state, "compose", {"command": command, "project": name, "directory": directory, "args": rest,
                              "version": os.environ.get("ROLLOUT_VERSION", ""), "files": [os.path.basename(f) for f in files]})
    if command == "config":
        print(json.dumps(config))
    elif command == "build":
        record(state, "built", {"project": name, "services": rest, "images": override_images(files)})
    elif command == "pull":
        record(state, "pulled", {"project": name, "services": [s for s in rest if not s.startswith("-")]})
    elif command == "up":
        images = override_images(files)
        running = {}
        for svc, spec in config["services"].items():
            running[svc] = images.get(svc) or spec.get("image") or "%s-%s" % (name, svc)
            for mount in spec["volumes"]:
                if mount["type"] == "volume":
                    volume = config["volumes"][mount["source"]]["name"]
                    os.makedirs(os.path.join(VOLUMES, volume), exist_ok=True)
                    users = state.setdefault("volume_users", {}).setdefault(volume, [])
                    if "%s-%s" % (name, svc) not in users:
                        users.append("%s-%s" % (name, svc))
        state.setdefault("containers", {})[name] = running
        page = os.path.join(directory, "public", "version.txt")
        if os.environ.get("FAKE_DOCKER_WEBROOT") and os.path.isfile(page):
            shutil.copyfile(page, os.path.join(os.environ["FAKE_DOCKER_WEBROOT"], "version.txt"))
    elif command == "exec":
        env, rest = {}, [a for a in rest if a != "-T"]
        while rest[0] == "-e":
            key, _, value = rest[1].partition("=")
            env[key], rest = value, rest[2:]
        service, script = rest[0], rest[-1]
        record(state, "exec", {"service": service, "env": env, "argv": sys.argv[1:]})
        engine = state.get("db_engine", "mysql")
        if "ping" in script or "pg_isready" in script:
            return 0
        if "echo postgres" in script:
            print(engine)
        elif '"$D"' in script or "pg_dump -U" in script:
            sys.stdout.write("-- fake %s dump of %s\n%s" % (engine, env.get("ROLLOUT_DB", "?"), state.get("db", "")))
        elif '"$C"' in script or "psql -U" in script:
            state["db_restored"] = sys.stdin.read()
        else:
            return 9
    return 0


def run(args, state):
    mounts, i = {}, 0
    while i < len(args):
        if args[i] == "-v":
            spec = args[i + 1][:-3] if args[i + 1].endswith((":ro", ":rw")) else args[i + 1]
            host, target = spec.rsplit(":", 1)                     # a Windows host path has a colon of its own
            mounts[target] = host
            i += 2
        elif args[i] == "--rm":
            i += 1
        else:
            break
    image, script = args[i], args[-1]
    volume, folder = os.path.join(VOLUMES, mounts["/volume"]), mounts["/backup"]
    record(state, "runs", {"image": image, "volume": mounts["/volume"], "script": script})
    archive = re.search(r"/backup/(\S+)", script).group(1)
    if "tar czf" in script:
        with tarfile.open(os.path.join(folder, archive), "w:gz") as tar:
            tar.add(volume, arcname=".")
    elif "tar xzf" in script:
        shutil.rmtree(volume, ignore_errors=True)
        os.makedirs(volume)
        with tarfile.open(os.path.join(folder, archive), "r:gz") as tar:
            tar.extractall(volume)
    return 0


def main(args):
    state = load()
    before = json.dumps(state, sort_keys=True)
    try:
        if args[0] == "compose":
            return compose(args[1:], state)
        if args[:2] == ["image", "rm"]:
            record(state, "removed", args[2])
            return 0
        if args[:2] == ["volume", "inspect"]:
            return 0 if os.path.isdir(os.path.join(VOLUMES, args[2])) else 1
        if args[:2] == ["ps", "-q"]:
            print("\n".join(state.get("volume_users", {}).get(args[3].split("=", 1)[1], [])))
            return 0
        if args[0] in ("stop", "start"):
            record(state, args[0] + "ped" if args[0] == "stop" else "started", args[1:])
            return 0
        if args[0] == "run":
            return run(args[1:], state)
        print("fake docker: unknown command %s" % args, file=sys.stderr)
        return 2
    finally:
        if json.dumps(state, sort_keys=True) != before:          # read-only commands (version, inspect, ps) write nothing
            save(state)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
