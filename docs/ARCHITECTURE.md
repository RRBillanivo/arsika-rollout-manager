# Architecture

## Components

| Component | Runs as | Stores | Role |
|---|---|---|---|
| Dashboard (`app/`, `public/`) | PHP-FPM user (`www`) | MySQL: `users`, `login_attempts`, `rate_limits`, `audit_logs`, `app_meta` | Sign-in, password confirmation, audit trail, single-page interface; forwards actions to the runner |
| Runner (`runner/arsika_rollout_manager`, installed in `/opt/arsika-rollout-manager`) | root (systemd `arsika-rollout-manager-runner`) | `/var/lib/arsika-rollout-manager/runner.db` (SQLite): `sites`, `releases`, `jobs`, `backups`; job logs | Downloads releases, switches versions, runs hooks, restarts services, backs up and restores |

The runner listens on `127.0.0.1:8790` only and answers requests signed with the shared secret (see [API.md](API.md)).
Its settings (`/etc/arsika-rollout-manager/runner.json`, mode 0600) hold the secret and the GitHub token.

## Site layout

```
/www/wwwroot/example.com/
  releases/
    3.1.0/            one complete release (extracted zip); .rollout-release.json marks it complete
    3.2.0/
  shared/             kept between releases, linked into every release
    .env
    storage/
    .rollout-cache/   hook caches (e.g. the Python environment of Anemia Web)
  current -> releases/3.2.0
  public  -> current/public       aaPanel "Running directory" = /public
  _pre_rollout/       the old installation after an adoption (deleted from the dashboard)
  .user.ini           aaPanel (left untouched)
```

Paths inside a release that are listed in the site's `shared` setting are replaced by links to `shared/`
(`storage/` is created from the release on the first install). The web server keeps serving `public`, which always
points into `current`, so no aaPanel setting changes when a version is switched.

## Deployment

1. Optional backup (database + shared files).
2. Find the release on GitHub (`v<version>` or `<version>` tag) and its asset (file name pattern).
3. Download the asset (the API redirect to storage is followed without the token), check its size and, when GitHub
   provides it, its SHA-256 digest.
4. Extract into `releases/<version>/` (absolute paths, `..` and links leaving the folder are refused; Unix modes kept).
5. Link the shared paths, give the files to the web server user, run the **build** hook in the new folder.
6. Write `.rollout-release.json`. Until here the running site is untouched; a failure leaves an incomplete folder
   that is removed on the next attempt.
7. Switch `current` atomically (rename of a new link), make sure the document root link exists, run the **activate**
   hook, restart the site's systemd services, reload PHP-FPM (OPcache and the realpath cache).
8. Health check (HTTP 200, optional expected text). On failure the previous release is switched back, activated and
   checked again; the job is marked failed with the reason.
9. Old releases beyond `keep_releases` are removed (never the current one or the rollback target).

Jobs run one at a time in a background thread. A job left running by a stopped runner is marked failed at the next start.

## Hooks

`build` and `activate` are shell commands run as root (`bash -c`), or `preset:<name>` for `runner/presets/<name>.sh`.
Environment variables: `ROLLOUT_SITE`, `ROLLOUT_VERSION`, `ROLLOUT_SITE_DIR`, `ROLLOUT_RELEASE_DIR`,
`ROLLOUT_SHARED_DIR`, `ROLLOUT_CURRENT_DIR`, `ROLLOUT_CACHE_DIR`, `ROLLOUT_WEB_USER`, `ROLLOUT_DOMAIN`, `ROLLOUT_PHASE`
(`build`, `activate`, `adopt-revert`).
A non-zero exit code fails the job (before the switch for `build`, with an automatic rollback for `activate`).

## Backups

`/www/backup/arsika-rollout-manager/<site>/<YYYYmmdd-HHMMSS>_<version>/` contains `database.sql.gz` (mysqldump with
`--single-transaction`, credentials from the site's `.env` passed through a temporary 0600 option file),
`files.tar.gz` (the backup paths inside `shared/`, minus the exclusions) and `manifest.json` with SHA-256 checksums.
A restore verifies the checksums, replaces the shared files (the replaced ones are kept until the site is healthy),
imports the database, then runs the activation steps and the health check.

## Adoption

For a site installed by hand (application files directly in the site folder):

1. Backup from the old location.
2. Shared paths are moved to `shared/` and replaced by links, so the running site keeps working.
3. The chosen release is downloaded and built as a normal release.
4. Everything else in the site folder (except `.user.ini`, `.well-known`, `.htaccess`) is moved to `_pre_rollout/`;
   `current` and the document root link are created; activation and health check follow.
5. On failure the files are moved back and presets restore what they changed (e.g. the old systemd unit).
