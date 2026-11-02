"""Docker Compose sites. Every release folder holds the application's compose files (from the release's source code or
its zip). A release is prepared by building its images, tagged with the version so that switching back to an earlier
release needs no rebuild, and by pulling the other images; "docker compose up -d" with the release folder as project
directory activates it.

The compose project name never changes between releases, so containers are replaced in place and named volumes (the
data) stay the same. When an installation made by hand is adopted, the name compose already uses for it is kept.

ROLLOUT_VERSION is set for every compose command, so a compose file may use image: registry/app:${ROLLOUT_VERSION}.
"""
import json
import os
import re
import subprocess
import time

from .system import CommandError
from .util import exists, parse_env

DEFAULT_FILES = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")
OVERRIDE_OF = {"compose.yaml": "compose.override.yaml", "compose.yml": "compose.override.yml",
               "docker-compose.yaml": "docker-compose.override.yaml", "docker-compose.yml": "docker-compose.override.yml"}
OVERRIDE = ".rollout-compose.yml"          # written into each release: the version-tagged images of that release
HELPER_IMAGE = "busybox:1.36"              # archives and restores named volumes


class ComposeError(CommandError):
    pass


def project_name(text):
    """Compose's own rule for project names: lowercase letters, digits, _ and -, starting with a letter or digit."""
    return re.sub(r"[^a-z0-9_-]", "", str(text).lower()).lstrip("_-")


def image_tag(version):
    return re.sub(r"[^A-Za-z0-9_.-]", "-", str(version))[:128] or "latest"


class Compose(object):
    def __init__(self, config):
        self.config = config
        self._version = (0.0, None)

    # ------------------------------------------------------------------ basics
    def docker(self):
        command = self.config.get("docker_command") or ["docker"]
        return [str(c) for c in command] if isinstance(command, (list, tuple)) else [str(command)]

    def version(self):
        """Version of Docker Compose v2 ("2.29.1"), or None when it is missing (checked at most once a minute)."""
        checked, value = self._version
        if time.time() - checked < 60:
            return value
        try:
            proc = subprocess.run(self.docker() + ["compose", "version", "--short"], stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, timeout=20)
            text = proc.stdout.decode("utf-8", "replace").strip()
            value = text if proc.returncode == 0 and text else None
        except (OSError, subprocess.SubprocessError):
            value = None
        self._version = (time.time(), value)
        return value

    def require(self):
        if not self.version():
            self._version = (0.0, None)
            raise ComposeError("Docker Compose v2 is not available on this server (docker compose version)")

    def project(self, site):
        """The site's compose project: its setting, else the name compose gives its folder."""
        return site.get("compose_project") or project_name(os.path.basename(os.path.normpath(site["path"])))

    def files(self, site, directory):
        """The compose files of a release: the site setting, else COMPOSE_FILE of the release's .env, else the
        standard names (compose.yaml + compose.override.yaml ...)."""
        if site.get("compose_files"):
            return list(site["compose_files"])
        env_file = os.path.join(directory, ".env")
        if os.path.isfile(env_file):
            with open(env_file, encoding="utf-8", errors="replace") as f:
                env = parse_env(f.read())
            listed = [n for n in env.get("COMPOSE_FILE", "").split(env.get("COMPOSE_PATH_SEPARATOR") or ":") if n.strip()]
            if listed:
                return listed
        for name in DEFAULT_FILES:
            if os.path.isfile(os.path.join(directory, name)):
                override = OVERRIDE_OF[name]
                return [name, override] if os.path.isfile(os.path.join(directory, override)) else [name]
        raise ComposeError("no compose file in %s (compose.yaml or docker-compose.yml)" % directory)

    def base(self, site, directory, project=True, with_override=True):
        """docker compose [-p project] --project-directory DIR -f FILE ... [-f the release's image override]"""
        argv = self.docker() + ["compose"]
        if project:
            argv += ["-p", self.project(site)]
        argv += ["--project-directory", directory]
        for name in self.files(site, directory):
            argv += ["-f", os.path.join(directory, name)]
        if with_override and os.path.isfile(os.path.join(directory, OVERRIDE)):
            argv += ["-f", os.path.join(directory, OVERRIDE)]
        return argv

    @staticmethod
    def env(version):
        env = dict(os.environ)
        env["ROLLOUT_VERSION"] = version or ""
        return env

    def config_json(self, site, directory, version, project=True):
        """The resolved compose configuration (docker compose config --format json)."""
        argv = self.base(site, directory, project=project, with_override=False) + ["config", "--format", "json"]
        try:
            proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=directory,
                                  env=self.env(version), timeout=120)
        except (OSError, subprocess.SubprocessError) as e:
            raise ComposeError("cannot run docker compose: %s" % e)
        if proc.returncode != 0:
            lines = proc.stderr.decode("utf-8", "replace").strip().splitlines()
            raise ComposeError("docker compose config failed: " + (lines[-1] if lines else "exit code %d" % proc.returncode))
        try:
            return json.loads(proc.stdout.decode("utf-8"))
        except ValueError:
            raise ComposeError("docker compose config did not answer with JSON (Docker Compose v2 is required)")

    # ------------------------------------------------------------------ releases
    @staticmethod
    def check_mounts(services, directory):
        """A bind mount inside the release whose source is missing would make Docker create an empty folder: data that
        must outlive a release has to be a shared path (linked to shared/), so stop before anything changes."""
        root = os.path.normcase(os.path.abspath(directory))
        for name, spec in sorted(services.items()):
            for volume in (spec or {}).get("volumes") or []:
                if not isinstance(volume, dict) or volume.get("type") != "bind":
                    continue
                source = os.path.abspath(str(volume.get("source", "")))
                if os.path.normcase(source).startswith(root + os.sep) and not exists(source):
                    rel = os.path.relpath(source, directory).replace(os.sep, "/")
                    raise ComposeError("service %s mounts ./%s, which is not part of the release: data that must be kept "
                                       "between versions belongs in the shared files (add %s/ to Shared files)" % (name, rel, rel))

    def write_override(self, site, version, directory, built):
        """Tag the images built for this release with its version (rollback then needs no rebuild)."""
        lines = ["# Written by Arsika Rollout Manager: the images of release %s" % version, "services:"]
        for name in built:
            lines += ['  "%s":' % name, '    image: "%s-%s:%s"' % (self.project(site), project_name(name) or "app", image_tag(version))]
        with open(os.path.join(directory, OVERRIDE), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")

    def prepare(self, site, version, directory, log, run, timeout):
        """Check the compose files of a new release, build the images of its services that have a build section
        (tagged with the version; not when compose_build is off) and pull the other images, before anything changes."""
        self.require()
        services = self.config_json(site, directory, version).get("services") or {}
        if not services:
            raise ComposeError("the compose files of release %s define no services" % version)
        self.check_mounts(services, directory)
        log.line("compose project %s, services: %s" % (self.project(site), ", ".join(sorted(services))))
        built = sorted(n for n, spec in services.items() if (spec or {}).get("build")) if site.get("compose_build", True) else []
        if built:
            self.write_override(site, version, directory, built)
            log.step("Build images: " + ", ".join(built))
            run(self.base(site, directory) + ["build"] + built, log, cwd=directory, env=self.env(version), timeout=timeout)
        pulled = sorted(n for n, spec in services.items() if n not in built and (spec or {}).get("image"))
        if pulled:
            log.step("Pull images: " + ", ".join(pulled))
            # images of a "build" site that come from elsewhere (a local tag, a registry that is down) do not stop it
            run(self.base(site, directory) + ["pull", "--quiet"] + (["--ignore-pull-failures"] if built else []) + pulled,
                log, cwd=directory, env=self.env(version), timeout=timeout)

    def up(self, site, version, directory, log, run, timeout):
        """Start (or update) the containers of the release in ``directory``, the release folder itself."""
        self.require()
        log.step("Start containers (docker compose up -d)")
        run(self.base(site, directory) + ["up", "-d", "--remove-orphans"], log, cwd=directory, env=self.env(version),
            timeout=timeout)

    @staticmethod
    def images(directory):
        path = os.path.join(directory, OVERRIDE)
        if not os.path.isfile(path):
            return []
        with open(path, encoding="utf-8") as f:
            return re.findall(r'^\s+image: "([^"]+)"$', f.read(), re.M)

    def remove_images(self, directory, log, run):
        """Remove the version-tagged images of a release folder that is deleted (an image in use stays)."""
        for image in self.images(directory):
            run(self.docker() + ["image", "rm", image], log, timeout=120, check=False)

    def detect_project(self, site, directory):
        """Name compose uses today for the installation in ``directory`` (COMPOSE_PROJECT_NAME, name:, or the folder)."""
        name = self.config_json(site, directory, "", project=False).get("name")
        return project_name(name) if name else project_name(os.path.basename(os.path.normpath(directory)))

    # ------------------------------------------------------------------ backups
    def exec_argv(self, site, directory, service, script, env=None, project=True):
        """docker compose exec -T [-e K=V] SERVICE sh -c SCRIPT (the script reads credentials from the container's
        environment)."""
        argv = self.base(site, directory, project=project) + ["exec", "-T"]
        for key, value in sorted((env or {}).items()):
            argv += ["-e", "%s=%s" % (key, value)]
        return argv + [service, "sh", "-c", script]

    def volumes(self, site, directory, version="", project=True):
        """Named volumes declared in the compose files -> their Docker names."""
        data = self.config_json(site, directory, version, project=project)
        prefix = data.get("name") or self.project(site)
        return {key: (spec or {}).get("name") or "%s_%s" % (prefix, key) for key, spec in (data.get("volumes") or {}).items()}

    def volume_argv(self, volume, folder, script, restore=False):
        """A throw-away container with the volume at /volume and the backup folder at /backup (each read-only where
        it is only read)."""
        return self.docker() + ["run", "--rm", "-v", "%s:/volume%s" % (volume, "" if restore else ":ro"),
                                "-v", "%s:/backup%s" % (folder, ":ro" if restore else ""), HELPER_IMAGE, "sh", "-c", script]
