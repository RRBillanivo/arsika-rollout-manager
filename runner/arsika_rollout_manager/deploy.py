"""Deployment operations: install a release, switch "current", roll back, adopt an existing installation, and clean up.

Order of a deployment (any failure before step 6 leaves the site untouched):
  1. backup of the database, shared files and volumes (if enabled)
  2. download the release asset (or the release's source code) from GitHub and verify its size / SHA-256
  3. extract into releases/<version>/ and link the shared files; Docker Compose sites: build or pull the images
  4. build hook (e.g. Python environment, database migrations, self-test)
  5. mark the release complete (.rollout-release.json)
  6. switch current -> releases/<version>; Docker Compose sites: docker compose up -d; activate hook, restart
     services, reload PHP-FPM
  7. health check; on failure the previous release is switched back automatically
"""
import json
import os
import shutil
import stat
import sys
import zipfile

from . import system
from .compose import DEFAULT_FILES, OVERRIDE, Compose, ComposeError
from .github import GitHub
from .sites import expand, layout, release_pattern
from .util import (POSIX, chown_tree, ensure_dir, exists, human_size, link_target, lock_file, make_link, remove,
                   sha256_file, switch_link, version_key, VERSION)

MARKER = ".rollout-release.json"
RUNNER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # /opt/arsika-rollout-manager
RESERVED = ("releases", "shared", "current", "_pre_rollout")
USER_INI = ".user.ini"


def _ini_key(line):
    text = line.strip()
    if not text or text[0] in ";#" or "=" not in text:
        return None
    return text.split("=", 1)[0].strip()


def merge_user_ini(release_text, live_text):
    """The release's own .user.ini, with the open_basedir line of the running version (the panel manages that limit)."""
    limit = [line for line in live_text.splitlines() if _ini_key(line) == "open_basedir"]
    if not limit:
        return release_text
    kept = [line for line in release_text.splitlines() if _ini_key(line) != "open_basedir"]
    return "\n".join(kept + limit) + "\n"


class DeployError(Exception):
    pass


class Deployer(object):
    def __init__(self, config, store, backups):
        self.config = config
        self.store = store
        self.backups = backups
        self.compose = getattr(backups, "compose", None) or Compose(config)

    def github(self, site):
        """GitHub client with the site's own read-only token (none for a public repository)."""
        return GitHub(self.config["github_api"], self.config.site_token(site["id"]))

    # ------------------------------------------------------------------ state
    @staticmethod
    def current_version(site):
        target = link_target(layout(site)["current"])
        return os.path.basename(target) if target else None

    @staticmethod
    def installed(site):
        """Complete release folders on disk, newest installation first."""
        folder = layout(site)["releases"]
        found = []
        if os.path.isdir(folder):
            for name in os.listdir(folder):
                marker = os.path.join(folder, name, MARKER)
                if os.path.isfile(marker):
                    try:
                        with open(marker, encoding="utf-8") as f:
                            info = json.load(f)
                    except (OSError, ValueError):
                        info = {}
                    found.append({"version": name, "installed_at": info.get("installed_at", ""), "asset": info.get("asset"),
                                  "size_bytes": info.get("size_bytes"), "sha256": info.get("sha256")})
        return sorted(found, key=lambda r: (r["installed_at"], version_key(r["version"])), reverse=True)

    def state(self, site):
        """ready | classic (an installation without releases/: adopt it first) | empty | missing"""
        path = site["path"]
        if not os.path.isdir(path):
            return "missing"
        if os.path.islink(layout(site)["current"]):
            return "ready"
        keep = set(site.get("adopt_keep", [])) | set(RESERVED) | {site.get("docroot_link") or ""}
        others = [n for n in os.listdir(path) if n not in keep]
        return "classic" if others else "empty"

    def variables(self, site, version):
        lay = layout(site)
        release_dir = os.path.join(lay["releases"], version)
        values = {
            "ROLLOUT_SITE": site["id"], "ROLLOUT_VERSION": version, "ROLLOUT_SITE_DIR": site["path"],
            "ROLLOUT_RELEASE_DIR": release_dir, "ROLLOUT_SHARED_DIR": lay["shared"],
            "ROLLOUT_CURRENT_DIR": lay["current"], "ROLLOUT_WEB_USER": site.get("web_user", ""), "ROLLOUT_DOMAIN": site.get("domain", ""),
            "ROLLOUT_CACHE_DIR": os.path.join(lay["shared"], ".rollout-cache"), "ROLLOUT_DEPLOY_MODE": site.get("deploy_mode", "files"),
        }
        values.update(self.runner_variables())
        if self.is_compose(site):
            # a plain "docker compose run/exec ..." in a hook uses the project and the files (with the images) of this release
            values["COMPOSE_PROJECT_NAME"] = values["ROLLOUT_COMPOSE_PROJECT"] = self.compose.project(site)
            try:
                files = self.compose.files(site, release_dir)
                if os.path.isfile(os.path.join(release_dir, OVERRIDE)):
                    files.append(OVERRIDE)
                values["COMPOSE_FILE"] = ":".join(os.path.join(release_dir, name) for name in files)
            except ComposeError:
                pass
        return values

    def runner_variables(self):
        """For hooks that update Arsika Rollout Manager itself: where the runner lives, and the file that asks it to
        restart (with its new code) when the job has ended."""
        return {"ROLLOUT_RUNNER_DIR": RUNNER_DIR, "ROLLOUT_RUNNER_CONFIG": os.path.abspath(self.config.path),
                "ROLLOUT_RESTART_FILE": self.config.state_path("restart-requested"), "ROLLOUT_PYTHON": sys.executable}

    @staticmethod
    def is_compose(site):
        return site.get("deploy_mode") == "compose"

    def keep_project(self, site, name, log):
        """Save the compose project of the site: its containers and volumes belong to that name from now on."""
        if site.get("compose_project") != name:
            site["compose_project"] = name
            self.store.save_site({k: v for k, v in site.items() if k not in ("created_at", "updated_at")})
            log.line("compose project of the site: " + name)

    # ------------------------------------------------------------------ release preparation
    def prepare_release(self, site, version, log, job_id):
        if not VERSION.match(version):
            raise DeployError("invalid version: " + version)
        lay = layout(site)
        target = os.path.join(lay["releases"], version)
        if os.path.isfile(os.path.join(target, MARKER)):
            log.step("Release %s is already installed; reusing releases/%s" % (version, version))
            return target
        if exists(target):
            log.line("removing an incomplete releases/%s from an earlier attempt" % version)
            remove(target)

        log.step("Find release %s in %s" % (version, site["repo"]))
        if self.is_compose(site):
            self.compose.require()                                   # before anything is downloaded
        github = self.github(site)
        release = github.release(site["repo"], version, release_pattern(site))
        asset = release["asset"]
        if not asset:
            raise DeployError("release %s has no asset matching %s (assets: %s)" % (
                version, site["asset_pattern"], ", ".join(release["assets"]) or "none"))
        archive = self.config.state_path("downloads", "%s-%s-%d.zip" % (site["id"], version, job_id))
        if asset.get("source"):
            log.step("Download the source code of %s (%s)" % (release["tag"], asset["name"]))
        else:
            log.step("Download %s (%s)" % (asset["name"], human_size(asset["size"])))
        try:
            size, digest = self.fetch(github, asset, archive, log)
            log.step("Extract into releases/" + version)
            ensure_dir(lay["releases"])
            ensure_dir(target)
            count = self.extract(archive, target)
            log.line("%d files extracted" % count)
        finally:
            if exists(archive):
                os.unlink(archive)

        self.link_shared(site, target, log)
        if site.get("web_user") and chown_tree(target, site["web_user"]):
            log.line("release files given to " + site["web_user"])
        if self.is_compose(site):
            self.keep_project(site, self.compose.project(site), log)
            self.compose.prepare(site, version, target, log, system.run, self.config["hook_timeout_seconds"])
        system.run_hook(site, "build", site.get("build", ""), target, self.variables(site, version), log,
                        self.config["shell"], self.config["hook_timeout_seconds"])
        info = {"version": version, "installed_at": system_now(), "asset": asset["name"], "size_bytes": size, "sha256": digest,
                "job_id": job_id}
        with open(os.path.join(target, MARKER), "w", encoding="utf-8") as f:
            json.dump(info, f, indent=2)
        self.store.save_release(site["id"], version, asset["name"], size, digest)
        log.line("release %s is ready" % version)
        return target

    @staticmethod
    def fetch(github, asset, archive, log):
        """Download an asset and check its size and, when GitHub knows it, its SHA-256. -> (size, sha256)"""
        size = github.download(asset, archive, lambda done, total: log.line(
            "downloaded %s" % human_size(done) + (" of %s" % human_size(total) if total else "")))
        if asset["size"] and size != asset["size"]:
            raise DeployError("download incomplete: %d of %d bytes" % (size, asset["size"]))
        digest = sha256_file(archive)
        if asset.get("digest", "").startswith("sha256:"):
            if asset["digest"][7:] != digest:
                raise DeployError("SHA-256 of the download does not match GitHub's digest")
            log.line("SHA-256 verified: " + digest)
        else:
            log.line("SHA-256: " + digest)
        return size, digest

    def update_runner(self, job, log):
        """Install the runner of another release of Arsika Rollout Manager on this server: its deploy/install.sh
        --agent --self-update copies the runner code and asks for a restart when the job has ended."""
        version = job["version"]
        if not VERSION.match(version or ""):
            raise DeployError("invalid version: %s" % version)
        repo = self.config.get("update_repo") or "RRBillanivo/arsika-rollout-manager"
        log.step("Find Arsika Rollout Manager %s in %s" % (version, repo))
        github = GitHub(self.config["github_api"])
        release = github.release(repo, version, self.config.get("update_asset") or "arsika-rollout-manager-v*.zip")
        asset = release["asset"]
        if not asset:
            raise DeployError("release %s has no %s file" % (version, self.config.get("update_asset")))
        archive = self.config.state_path("downloads", "runner-%s-%d.zip" % (version, job["id"]))
        folder = self.config.state_path("update-%s-%d" % (version, job["id"]))
        log.step("Download %s (%s)" % (asset["name"], human_size(asset["size"])))
        try:
            self.fetch(github, asset, archive, log)
            ensure_dir(folder, 0o700)
            self.extract(archive, folder)
            script = os.path.join(folder, "deploy", "install.sh")
            if not os.path.isfile(script):
                raise DeployError("the release has no deploy/install.sh")
            log.step("Install the runner of %s (deploy/install.sh --agent --self-update)" % version)
            env = dict(os.environ)
            env.update(self.runner_variables())
            system.run([self.config["shell"], script, "--agent", "--self-update"], log, cwd=folder, env=env,
                       timeout=self.config["hook_timeout_seconds"])
        finally:
            for path in (archive, folder):
                if exists(path):
                    remove(path)

    @staticmethod
    def extract(archive, target):
        """Extract a zip safely (no absolute paths, no ..). A single top-level folder is stripped. Unix modes and links kept."""
        with zipfile.ZipFile(archive) as z:
            names = [i.filename for i in z.infolist()]
            for name in names:                                        # check before any top-level folder is stripped
                parts = name.replace("\\", "/").split("/")
                if name.startswith(("/", "\\")) or ".." in parts or ":" in parts[0]:
                    raise DeployError("unsafe path in the archive: " + name)
            tops = {n.split("/", 1)[0] for n in names if n.strip("/")}
            strip = len(tops) == 1 and all(n.startswith(next(iter(tops)) + "/") or n == next(iter(tops)) for n in names) \
                and any("/" in n.strip("/") for n in names)
            prefix = next(iter(tops)) + "/" if strip else ""
            count = 0
            root = os.path.realpath(target)
            for info in z.infolist():
                name = info.filename[len(prefix):] if prefix else info.filename
                if not name or name.endswith("/") and not name.strip("/"):
                    continue
                parts = name.replace("\\", "/").split("/")
                if name.startswith("/") or ".." in parts or ":" in parts[0]:
                    raise DeployError("unsafe path in the archive: " + info.filename)
                path = os.path.join(target, *[p for p in parts if p])
                if not os.path.realpath(path).startswith(root):
                    raise DeployError("unsafe path in the archive: " + info.filename)
                mode = (info.external_attr >> 16) & 0xFFFF
                if name.endswith("/"):
                    ensure_dir(path)
                    continue
                ensure_dir(os.path.dirname(path))
                if POSIX and stat.S_ISLNK(mode):
                    link = z.read(info).decode("utf-8")
                    if link.startswith("/") or ".." in link.split("/"):
                        raise DeployError("unsafe link in the archive: " + info.filename)
                    os.symlink(link, path)
                else:
                    with z.open(info) as src, open(path, "wb") as dst:
                        shutil.copyfileobj(src, dst, 1 << 20)
                    if POSIX and mode & 0o777:
                        os.chmod(path, mode & 0o777)
                count += 1
            return count

    def link_shared(self, site, release_dir, log):
        """Replace the shared paths inside the release with links to shared/ (seeding shared/ on the first install)."""
        shared = layout(site)["shared"]
        if site.get("shared"):
            log.step("Link shared files: " + ", ".join(site["shared"]))
            ensure_dir(shared, 0o755)
        for entry in site.get("shared", []):
            rel = entry.rstrip("/")
            in_shared, in_release = os.path.join(shared, rel), os.path.join(release_dir, rel)
            if not exists(in_shared):
                if exists(in_release) and not os.path.islink(in_release):
                    ensure_dir(os.path.dirname(in_shared))
                    shutil.move(in_release, in_shared)
                    log.line("shared/%s created from the release" % rel)
                    if site.get("web_user"):
                        chown_tree(in_shared, site["web_user"])
                elif entry.endswith("/"):
                    ensure_dir(in_shared)
                    if site.get("web_user"):
                        chown_tree(in_shared, site["web_user"])
                    log.line("shared/%s created (empty)" % rel)
                else:
                    raise DeployError("shared file %s is missing: create %s first" % (rel, in_shared))
            if exists(in_release):
                remove(in_release)
            ensure_dir(os.path.dirname(in_release))
            make_link(in_shared, in_release)

    # ------------------------------------------------------------------ switching
    def ensure_docroot(self, site, log):
        lay = layout(site)
        if not lay["docroot"]:
            return
        target = lay["current"] if site["docroot_target"] == "." else os.path.join(lay["current"], site["docroot_target"])
        docroot = lay["docroot"]
        if os.path.islink(docroot):
            if os.path.normcase(link_target(docroot)) != os.path.normcase(os.path.normpath(target)):
                switch_link(docroot, target)
                log.line("%s -> %s" % (site["docroot_link"], target))
        elif exists(docroot):
            raise DeployError("%s is a real folder, not a link: adopt the existing installation first" % docroot)
        else:
            make_link(target, docroot)
            log.line("%s -> %s" % (site["docroot_link"], target))

    def carry_user_ini(self, site, release_dir, log):
        """aaPanel keeps PHP's open_basedir limit in <document root>/.user.ini, locked with chattr +i. The document root
        is a link into the running release, so that file is carried into the release that goes live: copied when the
        release has none, otherwise only its open_basedir line is taken over. The copy is locked again."""
        docroot = layout(site)["docroot"]
        if not docroot:
            return
        source = os.path.join(docroot, USER_INI)
        target_dir = release_dir if site["docroot_target"] == "." else os.path.join(release_dir, site["docroot_target"])
        target = os.path.join(target_dir, USER_INI)
        if not os.path.isfile(source) or not os.path.isdir(target_dir):
            return
        if os.path.isfile(target) and os.path.realpath(source) == os.path.realpath(target):
            return                                                   # the release is already the live one
        with open(source, encoding="utf-8", errors="replace") as f:
            live = f.read()
        own = None
        if os.path.isfile(target):
            with open(target, encoding="utf-8", errors="replace") as f:
                own = f.read()
        wanted = live if own is None else merge_user_ini(own, live)
        if wanted == own:
            return
        if own is not None:
            remove(target)                                           # also unlocks an earlier copy
        with open(target, "w", encoding="utf-8", newline="\n") as f:
            f.write(wanted)
        locked = lock_file(target)
        log.line("%s/%s of the running version carried over (PHP open_basedir)%s" % (
            site["docroot_link"], USER_INI, {True: ", locked with chattr +i", False: "; chattr +i failed, the copy is not locked",
                                             None: ""}[locked]))

    def switch(self, site, version, log):
        lay = layout(site)
        target = os.path.join(lay["releases"], version)
        if not os.path.isfile(os.path.join(target, MARKER)):
            raise DeployError("release %s is not installed" % version)
        log.step("Switch current -> releases/" + version)
        self.carry_user_ini(site, target, log)
        switch_link(lay["current"], target)
        self.ensure_docroot(site, log)

    def activate(self, site, version, log):
        release_dir = os.path.join(layout(site)["releases"], version)
        if self.is_compose(site):
            # the release folder itself (not current/) is the project directory: its bind mounts and images differ
            # from the previous release, so compose recreates exactly the containers that changed
            self.compose.up(site, version, release_dir, log, system.run, self.config["hook_timeout_seconds"])
        system.run_hook(site, "activate", site.get("activate", ""), release_dir,
                        self.variables(site, version), log, self.config["shell"], self.config["hook_timeout_seconds"])
        system.restart_services(site.get("services", []), log)
        if site.get("reload_php_fpm"):
            log.step("Reload PHP-FPM")
            system.reload_php_fpm(log)

    def healthy(self, site, version, log):
        if not site.get("health_url"):
            log.line("no health check configured")
            return True
        url, contains = expand(site, site["health_url"], version), expand(site, site.get("health_contains", ""), version)
        local = bool(site.get("health_local"))
        log.step("Health check " + url + (" on this server (127.0.0.1)" if local else "") + (' (expects "%s")' % contains if contains else ""))
        return system.health_check(url, contains, log, attempts=int(self.config.get("health_attempts", 10)),
                                   delay=float(self.config.get("health_delay", 3)), local=local)

    def go_live(self, site, version, previous, log):
        """Switch + activate + health check; switches back to ``previous`` when anything fails."""
        try:
            self.switch(site, version, log)
            self.activate(site, version, log)
            if not self.healthy(site, version, log):
                raise DeployError("health check failed for " + version)
        except Exception as error:
            log.error(str(error))
            if previous and previous != version:
                log.step("Automatic rollback to " + previous)
                try:
                    self.switch(site, previous, log)
                    self.activate(site, previous, log)
                    ok = self.healthy(site, previous, log)
                    log.line("rollback done; " + ("site healthy again" if ok else "the site is still NOT healthy"))
                except Exception as again:
                    log.error("automatic rollback failed: %s" % again)
                raise DeployError("%s; rolled back to %s" % (error, previous))
            raise
        self.store.mark_activated(site["id"], version)

    # ------------------------------------------------------------------ jobs
    def deploy(self, job, site, log):
        version = job["version"]
        state = self.state(site)
        if state == "missing":
            raise DeployError("site folder does not exist: " + site["path"])
        if state == "classic":
            raise DeployError("the site folder holds an installation without releases/; use Adopt first")
        previous = self.current_version(site)
        if job["params"].get("backup", site.get("backup_before_deploy")) and state == "ready":
            self.backups.create(site, previous, "before deploying " + version, log, job["id"])
        self.prepare_release(site, version, log, job["id"])
        self.go_live(site, version, previous, log)
        return previous, version

    def rollback_target(self, site):
        current = self.current_version(site)
        rows = [r for r in self.store.releases(site["id"]) if r["activated_at"] and r["version"] != current]
        on_disk = {r["version"] for r in self.installed(site)}
        rows = [r for r in sorted(rows, key=lambda r: r["activated_at"], reverse=True) if r["version"] in on_disk]
        return rows[0]["version"] if rows else None

    def rollback(self, job, site, log):
        current = self.current_version(site)
        target = job["version"] or self.rollback_target(site)
        if not target:
            raise DeployError("no earlier release on the server to roll back to")
        if target == current:
            raise DeployError("%s is already the current release" % target)
        self.go_live(site, target, current, log)
        return current, target

    def adopt(self, job, site, log):
        """Convert an installation that lives directly in the site folder into releases/ + shared/ + current."""
        version = job["version"]
        if self.state(site) != "classic":
            raise DeployError("nothing to adopt: the site folder is %s" % self.state(site))
        lay = layout(site)
        root = site["path"]
        old_compose = False
        if self.is_compose(site):
            self.compose.require()
            names = site.get("compose_files") or DEFAULT_FILES
            old_compose = any(os.path.isfile(os.path.join(root, name)) for name in names)
            if old_compose:
                # the new release takes over the containers and volumes of the running installation: same project
                log.step("Find the compose project of the running installation")
                self.keep_project(site, self.compose.detect_project(site, root), log)
            else:
                log.line("the old installation has no compose file; the containers of release %s replace it" % version)
        if job["params"].get("backup", True):
            # shared/ does not exist yet: the data is still in the site folder itself
            self.backups.create(site, version, "before adopting " + version, log, job["id"], base=root)

        log.step("Move shared files to shared/ (links stay in place, so the running site is not interrupted)")
        ensure_dir(lay["shared"])
        for entry in site.get("shared", []):
            rel = entry.rstrip("/")
            old, new = os.path.join(root, rel), os.path.join(lay["shared"], rel)
            if exists(old) and not os.path.islink(old) and not exists(new):
                ensure_dir(os.path.dirname(new))
                os.rename(old, new)
                make_link(new, old)
                log.line("%s -> shared/%s" % (rel, rel))
        release_dir = self.prepare_release(site, version, log, job["id"])
        self.carry_user_ini(site, release_dir, log)                  # the old document root folder is still in place

        keep = set(site.get("adopt_keep", [])) | set(RESERVED)
        moved = []
        log.step("Move the old installation to _pre_rollout/")
        ensure_dir(lay["pre_rollout"], 0o750)
        try:
            for name in sorted(os.listdir(root)):
                if name in keep:
                    continue
                os.rename(os.path.join(root, name), os.path.join(lay["pre_rollout"], name))
                moved.append(name)
            log.line("moved: " + ", ".join(moved))
            self.go_live(site, version, None, log)
        except Exception as error:
            log.error("adoption failed: %s; restoring the old installation" % error)
            for link in (lay["current"], lay["docroot"]):
                if link and os.path.islink(link):
                    os.unlink(link)
            for name in moved:
                if not exists(os.path.join(root, name)):
                    os.rename(os.path.join(lay["pre_rollout"], name), os.path.join(root, name))
            try:
                if old_compose:                                      # the old containers, from the old compose files
                    self.compose.up(site, "", root, log, system.run, self.config["hook_timeout_seconds"])
                # presets put back what their activate step changed (e.g. the old systemd unit); custom hooks are not re-run
                if site.get("activate", "").startswith("preset:"):
                    system.run_hook(site, "adopt-revert", site["activate"], root, self.variables(site, version), log,
                                    self.config["shell"], self.config["hook_timeout_seconds"])
                system.restart_services(site.get("services", []), log)
                if site.get("reload_php_fpm"):
                    system.reload_php_fpm(log)
            except Exception as again:
                log.error("restart after restoring failed: %s" % again)
            raise
        log.line("adoption complete; the old files are in %s (delete them from the dashboard when all is well)" % lay["pre_rollout"])
        return None, version

    def prune(self, site, log, keep=None):
        keep = int(keep or site.get("keep_releases", 5))
        current = self.current_version(site)
        protected = {current, self.rollback_target(site)}
        releases = self.installed(site)
        removable = [r for r in releases if r["version"] not in protected]
        excess = len(releases) - keep
        for release in sorted(removable, key=lambda r: r["installed_at"])[:max(0, excess)]:
            path = os.path.join(layout(site)["releases"], release["version"])
            self.remove_images(path, log)
            remove(path)
            self.store.forget_release(site["id"], release["version"])
            log.line("old release removed: " + release["version"])

    def remove_images(self, release_dir, log):
        """Docker Compose sites: the images built for a release are removed with it (an image still in use stays)."""
        if self.compose.images(release_dir):
            try:
                self.compose.remove_images(release_dir, log, system.run)
            except system.CommandError as error:
                log.line("images of %s not removed: %s" % (os.path.basename(release_dir), error))

    def delete_release(self, site, version, log):
        if version == self.current_version(site):
            raise DeployError("the current release cannot be deleted")
        path = os.path.join(layout(site)["releases"], version)
        if not VERSION.match(version) or not exists(path):
            raise DeployError("release %s is not installed" % version)
        self.remove_images(path, log)
        remove(path)
        self.store.forget_release(site["id"], version)
        log.line("release removed: " + version)

    def delete_pre_rollout(self, site, log):
        path = layout(site)["pre_rollout"]
        if not exists(path):
            raise DeployError("there is no _pre_rollout folder")
        log.step("Delete %s" % path)
        remove(path)


def system_now():
    from .util import now
    return now()
