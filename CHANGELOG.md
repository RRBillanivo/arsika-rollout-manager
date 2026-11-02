# Changelog

All notable changes are documented here. The project follows [Semantic Versioning](https://semver.org); every version
has a git tag and a GitHub release with the upload archive (`arsika-rollout-manager-vX.Y.Z.zip`).

## [1.2.0] — 2026-11-02

### Added
- **Other servers.** One dashboard controls the runners of several servers (local network, VPN or internet).
  `deploy/install.sh --agent` installs only the runner, on HTTPS with a self-signed certificate, and prints the address,
  secret and key pin (`python3 -m arsika_rollout_manager connection-info`). The dashboard pins the certificate's public
  key, signs every request with that server's secret (stored encrypted with `APP_KEY`) and tests the connection before
  saving. New **Servers** page; a server choice in **Add site**; server badges on the overview; a server that is down
  is reported without hiding the other sites. Optional address allowlist in the runner (`allow_from`).
- **Plain servers without a panel**: the installer detects aaPanel and otherwise uses `/var/www`, `/srv`,
  `/var/backups/arsika-rollout-manager` and `www-data`; options `--site-root`, `--backup-dir`, `--web-user`, `--port`;
  nginx example `deploy/nginx/server.conf`; presets use the web server user of their server.
- **Docker Compose sites** (deploy mode `compose`): compose files checked before anything changes (a bind mount into the
  release must be a shared path), images built and tagged with the version (rollback without a rebuild) or pulled,
  `docker compose up -d --remove-orphans` from the release folder, a fixed project name (an adoption keeps the name of
  the running project), `ROLLOUT_VERSION`, `COMPOSE_PROJECT_NAME` and `COMPOSE_FILE` in hooks, images removed with
  their release. Presets "Docker Compose: build" and "Docker Compose: images".
- **Release source code**: a site can install GitHub's source code archive of the release tag instead of an asset.
- **Backups**: PostgreSQL (pg_dump/psql) besides MySQL/MariaDB (also `mariadb-dump`); settings from `DB_*`,
  `DATABASE_URL` or `MYSQL_*`/`POSTGRES_*`; databases inside a container, dumped with the container's own credentials;
  Docker volumes, archived by a throw-away busybox container and restored with their containers stopped.

### Changed
- Job pages are addressed through their site (`#/sites/<site>/jobs/<n>`); `#/jobs/<n>` still opens jobs of the
  dashboard's own server. A job's state changes after its clean-up, so its log is complete.
- `GET /api/v1/sites` answers `{sites, servers}`.
- Documentation for PHP 8.4 and Python 3.13 (tested on Debian 13 in production); new guide `docs/SERVERS.md`.

### Fixed
- A site that is not adopted yet no longer shows "Problem" when its expected health text contains `{version}`.

## [1.1.0] — 2026-11-02

### Changed
- Every site has its own read-only GitHub token, entered in the site form (**Add site** and the site's **Settings**
  tab) and tried by **Check access** before saving. Tokens stay write-only: the interface shows a hint such as
  `github_pat_…a1b2`, the activity log never records them, and removing a site deletes its token.
- The single GitHub token for all sites in **Settings** is gone. A token saved by 1.0.0 is given to the existing sites
  that have none when the runner starts.
- Neutral examples in the site form (`example.com`, `owner/repository`) instead of values that looked filled in.

### Removed
- Runner `GET`/`PUT /v1/settings` and dashboard `PUT /api/v1/settings` (the single GitHub token).

## [1.0.0] — 2026-11-02

First version, released under the MIT license.

### Added
- Dashboard (PHP 7.4+, MySQL): one-time administrator setup page, sign-in with lockout, password confirmation for
  sensitive actions, audit log, Indonesian / English interface, light and dark theme, phone layout.
- Runner (Python 3.6+, standard library only, systemd service as root on 127.0.0.1, HMAC-signed API with nonces).
- Sites from GitHub releases: overview with health checks and update notices, deploy with automatic rollback after a
  failed health check, rollback to any installed version, release pruning, live job logs and history.
- Release layout `releases/<version>` + `shared/` + `current` + document root link (aaPanel Running directory unchanged).
- Backups (mysqldump + shared files, SHA-256 manifest) before updates and on demand, also of a site that is not adopted
  yet; restore with a safety backup.
- Adoption of installations made by hand, without downtime, with automatic revert on failure.
- aaPanel's `.user.ini` (PHP `open_basedir`) in the document root is carried into every release and locked again with
  `chattr +i`; locked files do not block the deletion of releases or `_pre_rollout/`.
- Presets: Anemia Web 3.x (shared Python environment per requirements, `db:check`, model self-test, `anemia-engine`
  unit pointing at `current/`), PHP application, static site.
- `deploy/install.sh` (runner in `/opt/arsika-rollout-manager`, settings in `/etc/arsika-rollout-manager`, systemd
  unit without `PrivateTmp` so backups reach aaPanel's MySQL socket in `/tmp`, dashboard `.env`, setup link), nginx
  rules for aaPanel.
- Tests: runner integration test and end-to-end smoke test with a fake GitHub (PHP 7.4 and 8.3).
