"""Job queue: one background thread runs the queued jobs one after another (deployments never overlap).

A job (its hook, or a self-update) may ask the runner to restart with new code by creating the file
ROLLOUT_RESTART_FILE; the runner then stops when the job has ended, and systemd starts it again."""
import json
import os
import threading
import traceback

from . import __version__, system
from .backup import BackupError
from .deploy import DeployError
from .github import GitHubError
from .hosting import Hosting, shown_command
from .joblog import JobLog
from .uploads import UploadError
from .util import exists, now, remove

JOB_TYPES = ("deploy", "rollback", "adopt", "backup", "restore", "delete-release", "cleanup-adopt", "upload", "artisan")
RUNNER_SITE = "_runner"                  # jobs of the runner itself ("self-update"), not of a site
EXPECTED = (DeployError, BackupError, GitHubError, system.CommandError, UploadError)


class Worker(object):
    def __init__(self, config, store, deployer):
        self.config = config
        self.store = store
        self.deployer = deployer
        self.backups = deployer.backups
        self.wake = threading.Event()
        self.stopping = False
        self.thread = None
        self.on_restart = None                                       # set by serve(): stops the service (systemd restarts it)
        self.hosting = Hosting(config, store, deployer)               # sites on a hosting without a terminal

    def log_path(self, job_id):
        return self.config.state_path("logs", "%d.log" % int(job_id))

    def restart_file(self):
        return self.config.state_path("restart-requested")

    def start(self):
        if os.path.exists(self.restart_file()):
            os.unlink(self.restart_file())                           # from a job before the last start
        self.store.fail_interrupted_jobs()
        self.thread = threading.Thread(target=self.loop, name="rollout-worker", daemon=True)
        self.thread.start()

    def submit(self, site_id, job_type, version=None, params=None, actor=None):
        job_id = self.store.add_job(site_id, job_type, version, params, actor)
        self.wake.set()
        return job_id

    def loop(self):
        while not self.stopping:
            row = self.store.next_job()
            if row is None:
                self.wake.wait(5)
                self.wake.clear()
                continue
            self.run(row["id"])

    def run(self, job_id):
        job = self.store.job(job_id)
        runner_job = job["site_id"] == RUNNER_SITE
        site = None if runner_job else self.store.site(job["site_id"])
        self.store.update_job(job_id, status="running", started_at=now())
        log = JobLog(self.log_path(job_id))
        log.line("Arsika Rollout Manager runner %s - job #%d: %s%s on %s, requested by %s" % (
            __version__, job_id, job["type"], " " + job["version"] if job["version"] else "", job["site_id"], job["actor"] or "?"))
        outcome = {}
        try:
            if site is None and not runner_job:
                raise DeployError("site %s is not registered" % job["site_id"])
            handler = getattr(self, "job_" + job["type"].replace("-", "_"))
            before, after, message = handler(job, site, log)
            log.step("Finished: " + message)
            outcome = {"status": "succeeded", "message": message, "from_version": before, "to_version": after}
        except EXPECTED as error:
            log.error(str(error))
            outcome = {"status": "failed", "message": str(error)[:500]}
        except Exception as error:                                   # a bug: keep the traceback in the log
            log.error("unexpected error: %s" % error)
            for line in traceback.format_exc().splitlines():
                log.error(line)
            outcome = {"status": "failed", "message": ("unexpected error: %s" % error)[:500]}
        finally:
            if site is not None and job["type"] in ("deploy", "rollback", "adopt"):
                try:                                                     # also after a failed deployment (its release stays only if needed)
                    self.ops(site).prune(site, log)
                except Exception as error:
                    log.error("cleaning up old releases failed: %s" % error)
            if site is not None and self.hosting.is_hosting(site):
                self.hosting.forget_cache(site)
            log.close()
            if outcome:                                                  # the job ends after its clean-up (and its last log line)
                self.store.update_job(job_id, finished_at=now(), **outcome)
            self.restart_if_requested(job_id)

    def restart_if_requested(self, job_id):
        """Stop the runner after a job that installed new runner code (systemd starts the new code)."""
        path = self.restart_file()
        if not os.path.exists(path):
            return False
        try:
            os.unlink(path)
        except OSError:
            pass
        print("job #%d asked for a restart: the runner stops now and starts again with its new code" % job_id, flush=True)
        if self.on_restart:
            self.on_restart()
        return True

    def ops(self, site):
        """What installs the site: the deployer (this server) or the agent of its hosting."""
        return self.hosting if self.hosting.is_hosting(site) else self.deployer

    # ---------- job types ----------
    def job_deploy(self, job, site, log):
        before, after = self.ops(site).deploy(job, site, log)
        if job["params"].get("redeploy") and before == after:
            return before, after, "deployed %s again" % after
        return before, after, "deployed %s%s" % (after, " (was %s)" % before if before and before != after else "")

    def job_rollback(self, job, site, log):
        before, after = self.ops(site).rollback(job, site, log)
        return before, after, ("rolled back from %s to %s" % (before, after)) if before else "switched the website to %s" % after

    def job_adopt(self, job, site, log):
        if self.hosting.is_hosting(site):
            before, after = self.hosting.deploy(job, site, log, adopt=True)
        else:
            before, after = self.deployer.adopt(job, site, log)
        return before, after, "adopted the existing installation as release %s" % after

    def job_backup(self, job, site, log):
        if self.hosting.is_hosting(site):
            version = self.hosting.current_version(site)
            backup_id = self.hosting.backup(site, version, job["params"].get("reason") or "manual backup", log, job["id"])
            if not backup_id:
                raise BackupError("nothing to back up: set the database (environment file) or the backup paths of the site")
            return version, version, "backup " + backup_id
        version = self.deployer.current_version(site)
        # before an adoption the data is still in the site folder itself (shared/ does not exist yet)
        base = site["path"] if self.deployer.state(site) == "classic" else None
        backup_id = self.backups.create(site, version, job["params"].get("reason") or "manual backup", log, job["id"], base=base)
        if not backup_id:
            raise BackupError("nothing to back up: set the database (environment file or database service), the backup paths "
                              "or the volumes of the site")
        return version, version, "backup " + backup_id

    def job_restore(self, job, site, log):
        backup_id = str(job["params"].get("backup_id", ""))
        if not self.store.backup(site["id"], backup_id):
            raise BackupError("backup %s not found" % backup_id)
        if self.hosting.is_hosting(site):
            version = self.hosting.current_version(site)
            if job["params"].get("safety_backup", True):
                self.hosting.backup(site, version, "before restoring " + backup_id, log, job["id"])
            replaced = self.hosting.restore(site, backup_id, log)
            if not self.deployer.healthy(site, version, log):
                raise DeployError("data restored, but the health check failed; the replaced files are kept on the hosting in %s" % replaced)
            self.hosting.clear_replaced(site, replaced)
            return version, version, "restored backup " + backup_id
        version = self.deployer.current_version(site)
        if job["params"].get("safety_backup", True):
            self.backups.create(site, version, "before restoring " + backup_id, log, job["id"])
        replaced = self.backups.restore(site, backup_id, log)
        self.deployer.activate(site, version, log)
        if not self.deployer.healthy(site, version, log):
            raise DeployError("data restored, but the health check failed; the replaced files are kept in %s" % replaced)
        if replaced and exists(replaced):
            remove(replaced)
        return version, version, "restored backup " + backup_id

    def job_delete_release(self, job, site, log):
        self.ops(site).delete_release(site, job["version"], log)
        return None, None, "deleted release " + job["version"]

    def job_self_update(self, job, site, log):
        self.deployer.update_runner(job, log)
        return __version__, job["version"], "runner %s installed; it restarts with the new code now" % job["version"]

    def job_cleanup_adopt(self, job, site, log):
        self.ops(site).delete_pre_rollout(site, log)
        return None, None, "deleted the files of the installation before Arsika Rollout Manager (_pre_rollout)"

    def job_upload(self, job, site, log):
        """An archive uploaded through the dashboard becomes a version of the site."""
        version = self.deployer.uploads.process(site, str(job["params"].get("upload_id") or ""), log)
        return None, version, "version %s uploaded" % version

    def job_artisan(self, job, site, log):
        if not self.hosting.is_laravel(site):
            raise DeployError("artisan commands are run for Laravel sites on a hosting")
        command = str(job["params"].get("command") or "")
        try:
            version = self.hosting.run_artisan(site, command, log)
        finally:
            if shown_command(command) != command:                     # tinker code may hold passwords: not kept
                self.store.update_job(job["id"], params=json.dumps(dict(job["params"], command=shown_command(command))))
        return version, version, "php artisan %s" % shown_command(command.strip())[:120]
