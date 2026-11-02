"""Job queue: one background thread runs the queued jobs one after another (deployments never overlap)."""
import threading
import traceback

from . import __version__, system
from .backup import BackupError
from .deploy import DeployError
from .github import GitHubError
from .joblog import JobLog
from .util import exists, now, remove

JOB_TYPES = ("deploy", "rollback", "adopt", "backup", "restore", "delete-release", "cleanup-adopt")
EXPECTED = (DeployError, BackupError, GitHubError, system.CommandError)


class Worker(object):
    def __init__(self, config, store, deployer):
        self.config = config
        self.store = store
        self.deployer = deployer
        self.backups = deployer.backups
        self.wake = threading.Event()
        self.stopping = False
        self.thread = None

    def log_path(self, job_id):
        return self.config.state_path("logs", "%d.log" % int(job_id))

    def start(self):
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
        site = self.store.site(job["site_id"])
        self.store.update_job(job_id, status="running", started_at=now())
        log = JobLog(self.log_path(job_id))
        log.line("Arsika Rollout Manager runner %s - job #%d: %s%s on %s, requested by %s" % (
            __version__, job_id, job["type"], " " + job["version"] if job["version"] else "", job["site_id"], job["actor"] or "?"))
        try:
            if site is None:
                raise DeployError("site %s is not registered" % job["site_id"])
            handler = getattr(self, "job_" + job["type"].replace("-", "_"))
            before, after, message = handler(job, site, log)
            log.step("Finished: " + message)
            self.store.update_job(job_id, status="succeeded", message=message, from_version=before, to_version=after, finished_at=now())
        except EXPECTED as error:
            log.error(str(error))
            self.store.update_job(job_id, status="failed", message=str(error)[:500], finished_at=now())
        except Exception as error:                                   # a bug: keep the traceback in the log
            log.error("unexpected error: %s" % error)
            for line in traceback.format_exc().splitlines():
                log.error(line)
            self.store.update_job(job_id, status="failed", message=("unexpected error: %s" % error)[:500], finished_at=now())
        finally:
            if site is not None and job["type"] in ("deploy", "rollback"):
                try:                                                     # also after a failed deployment (its release stays only if needed)
                    self.deployer.prune(site, log)
                except Exception as error:
                    log.error("cleaning up old releases failed: %s" % error)
            log.close()

    # ---------- job types ----------
    def job_deploy(self, job, site, log):
        before, after = self.deployer.deploy(job, site, log)
        return before, after, "deployed %s%s" % (after, " (was %s)" % before if before and before != after else "")

    def job_rollback(self, job, site, log):
        before, after = self.deployer.rollback(job, site, log)
        return before, after, "rolled back from %s to %s" % (before, after)

    def job_adopt(self, job, site, log):
        before, after = self.deployer.adopt(job, site, log)
        return before, after, "adopted the existing installation as release %s" % after

    def job_backup(self, job, site, log):
        version = self.deployer.current_version(site)
        # before an adoption the data is still in the site folder itself (shared/ does not exist yet)
        base = site["path"] if self.deployer.state(site) == "classic" else None
        backup_id = self.backups.create(site, version, job["params"].get("reason") or "manual backup", log, job["id"], base=base)
        if not backup_id:
            raise BackupError("nothing to back up: set the backup paths or the database environment file of the site")
        return version, version, "backup " + backup_id

    def job_restore(self, job, site, log):
        backup_id = str(job["params"].get("backup_id", ""))
        if not self.store.backup(site["id"], backup_id):
            raise BackupError("backup %s not found" % backup_id)
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
        self.deployer.delete_release(site, job["version"], log)
        return None, None, "deleted release " + job["version"]

    def job_cleanup_adopt(self, job, site, log):
        self.deployer.delete_pre_rollout(site, log)
        return None, None, "deleted the files of the installation before Arsika Rollout Manager (_pre_rollout)"
