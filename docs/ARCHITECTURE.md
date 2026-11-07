# Architecture

## Components

| Component | Runs as | Stores | Role |
|---|---|---|---|
| Dashboard (`app/`, `public/`) | PHP-FPM user (`www`, `www-data`) | MySQL: `users`, `login_attempts`, `rate_limits`, `audit_logs`, `servers`, `site_servers`, `app_meta` | Sign-in, password confirmation, audit trail, single-page interface; forwards actions to the runner of the site's server |
| Runner (`runner/arsika_rollout_manager`, installed in `/opt/arsika-rollout-manager`) on every server | root (systemd `arsika-rollout-manager-runner`) | `/var/lib/arsika-rollout-manager/runner.db` (SQLite): `sites`, `releases`, `jobs`, `backups`; job logs | Downloads releases, switches versions, runs hooks and Docker Compose, restarts services, backs up and restores |

The runner of the dashboard's server listens on `127.0.0.1:8790` (plain HTTP on loopback). The runner of another
server listens on the network with HTTPS only (self-signed certificate whose public key the dashboard pins) and may
limit the addresses it accepts (`allow_from`). Both answer only requests signed with their own secret (see
[API.md](API.md)). The runner's settings (`/etc/arsika-rollout-manager/runner.json`, mode 0600) hold the secret and the
read-only GitHub token of each of its sites.

The dashboard keeps the other servers in `servers` (address, secret encrypted with `APP_KEY`, key pin) and the server
of each site in `site_servers`; a site without a row belongs to the dashboard's own server. Site ids are unique across
the dashboard. The overview asks all runners at the same time (`curl_multi`), so a server that is down costs one
connection timeout and does not hide the others. Job numbers count per runner, so job pages are addressed through
their site (`/sites/{site}/jobs/{job}`).

A site on a **hosting without a terminal** (`deploy_mode` `hosting`) belongs to a runner too (normally the one of the
dashboard's server), but its files live on the hosting: the runner drives a generated PHP agent there with signed
requests (upload in pieces, unpack, migrate, swap, back up, in short steps). See [HOSTING.md](HOSTING.md).

Versions of a site with the release source **upload** come from the dashboard instead of GitHub: the archive goes in
pieces through the dashboard to the runner (`state_dir/uploads/<site>/incoming/`), an `upload` job unpacks, checks,
cleans and packs it again (`uploads.py`), and the result (`state_dir/uploads/<site>/<version>.zip`) is installed exactly
like the zip asset of a release.

## Site layout

```
/www/wwwroot/example.com/     (or /var/www/example.com, /srv/shop ...)
  releases/
    3.1.0/            one complete release (extracted zip or source archive); .rollout-release.json marks it complete
    3.2.0/            (Docker Compose: also .rollout-compose.yml, the version-tagged images of this release)
  shared/             kept between releases, linked into every release
    .env
    storage/
    .rollout-cache/   hook caches (e.g. the Python environment of Anemia Web)
  current -> releases/3.2.0
  public  -> current/public       document root link (aaPanel "Running directory" = /public); none for Docker Compose
  _pre_rollout/       the old installation after an adoption (deleted from the dashboard)
  .user.ini           aaPanel (left untouched)
```

Paths inside a release that are listed in the site's `shared` setting are replaced by links to `shared/`
(`storage/` is created from the release on the first install). The web server keeps serving `public`, which always
points into `current`, so no web server setting changes when a version is switched.

aaPanel's protection against cross-site access writes PHP's `open_basedir` limit into `.user.ini` in the running
directory (`public/.user.ini`) and locks it with `chattr +i`. Since `public` points into the running release, the runner
carries that file into every release it switches to (copied when the release has none, otherwise only its
`open_basedir` line is taken over) and locks the copy again. Locked files are unlocked before a release or
`_pre_rollout/` is deleted.

## Deployment

1. Optional backup (database, shared files, volumes).
2. Find the release on GitHub (`v<version>` or `<version>` tag) and its asset (file name pattern), or take the source
   code archive of the tag.
3. Download (the API redirect to storage is followed without the token), check the size and, when GitHub provides it,
   the SHA-256 digest of an asset.
4. Extract into `releases/<version>/` (a single top folder is removed; absolute paths, `..` and links leaving the folder
   are refused; Unix modes kept).
5. Link the shared paths, give the files to the web server user. Docker Compose: check the compose files and bind
   mounts, build the images tagged with the version, pull the others. Run the **build** hook in the new folder.
6. Write `.rollout-release.json`. Until here the running site is untouched; a failure leaves an incomplete folder
   that is removed on the next attempt.
7. Switch `current` atomically (rename of a new link), make sure the document root link exists. Docker Compose:
   `docker compose up -d --remove-orphans` with the release folder as project directory. Run the **activate** hook,
   restart the site's systemd services, reload PHP-FPM (OPcache and the realpath cache).
8. Health check (HTTP 200, optional expected text). On failure the previous release is switched back, activated (its
   containers started again) and checked again; the job is marked failed with the reason.
9. Old releases beyond `keep_releases` are removed with their images (never the current one or the rollback target).

Jobs of one runner run one at a time in a background thread. A job left running by a stopped runner is marked failed at
the next start. A job ends after its clean-up, so its log is complete when its state changes. Jobs of the runner itself
(`self-update`, "Update runner") belong to the pseudo site `_runner`.

Health checks request the address over the network, or, with **check through this server**, from 127.0.0.1 with the
address's host name (`Host` header; for https also SNI, without a certificate check on the loopback connection),
following one redirect to the same host.

## Hooks

`build` and `activate` are shell commands run as root (`bash -c`), or `preset:<name>` for `runner/presets/<name>.sh`.
Environment variables: `ROLLOUT_SITE`, `ROLLOUT_VERSION`, `ROLLOUT_SITE_DIR`, `ROLLOUT_RELEASE_DIR`,
`ROLLOUT_SHARED_DIR`, `ROLLOUT_CURRENT_DIR`, `ROLLOUT_CACHE_DIR`, `ROLLOUT_WEB_USER`, `ROLLOUT_DOMAIN`,
`ROLLOUT_DEPLOY_MODE`, `ROLLOUT_PHASE` (`build`, `activate`, `adopt-revert`); Docker Compose sites also
`COMPOSE_PROJECT_NAME`, `COMPOSE_FILE` and `ROLLOUT_COMPOSE_PROJECT`. For updates of Arsika Rollout Manager itself:
`ROLLOUT_RUNNER_DIR`, `ROLLOUT_RUNNER_CONFIG`, `ROLLOUT_PYTHON` and `ROLLOUT_RESTART_FILE`: a hook that creates that
file makes the runner stop when the job has ended (its state is saved first); systemd (`Restart=always`) starts the new
code.
A non-zero exit code fails the job (before the switch for `build`, with an automatic rollback for `activate`).

## Backups

`<backup_dir>/<site>/<YYYYmmdd-HHMMSS>_<version>/` contains `database.sql.gz`, `files.tar.gz`,
`volume-<name>.tar.gz` and `manifest.json` with SHA-256 checksums (the database entry records the engine and where it
ran, so it is restored the same way).

- MySQL/MariaDB on the server: `mysqldump --single-transaction` (or `mariadb-dump`), credentials from the site's
  environment file through a temporary 0600 option file. PostgreSQL on the server: `pg_dump --clean --if-exists`,
  password in the environment of the process. Restore with `mysql` / `psql --single-transaction -v ON_ERROR_STOP=1`.
- Database in a container: the same tools inside the container (`docker compose exec -T` or `docker exec -i`), with
  the container's own credentials, written to a temporary option file inside the container.
- Volumes: `docker run --rm -v <volume>:/volume:ro -v <backup>:/backup busybox:1.36 tar czf …`; a restore stops the
  containers using the volume, replaces its content and starts them again.

A restore verifies the checksums, replaces the shared files (the replaced ones are kept until the site is healthy),
restores the volumes, imports the database (waiting until a database container accepts connections), then runs the
activation steps and the health check.

A site that is not adopted yet can be backed up too: the data is read from the site folder itself (Docker Compose: from
the running project). Such a backup is restored after the adoption (a restore needs the release layout).

The runner service has no `PrivateTmp`: MySQL in aaPanel listens on `/tmp/mysql.sock`, which mysqldump and the build
hooks must reach.

## Adoption

For a site installed by hand (application files directly in the site folder, or a compose project started there):

1. Docker Compose: the name of the running project is detected (`docker compose config`) and kept for the site.
2. Backup from the old location.
3. Shared paths are moved to `shared/` and replaced by links, so the running site keeps working (running containers
   keep their mounts).
4. The chosen release is downloaded and built as a normal release.
5. The `.user.ini` of the old document root folder is carried into the release. Everything else in the site folder
   (except `.user.ini`, `.well-known`, `.htaccess`) is moved to `_pre_rollout/`; `current` and the document root link
   are created; activation (Docker Compose: the containers are recreated from the release) and health check follow.
6. On failure the files are moved back, the old compose project is started again from its own files and presets restore
   what they changed (e.g. the old systemd unit).
