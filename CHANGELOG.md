# Changelog

All notable changes are documented here. The project follows [Semantic Versioning](https://semver.org); every version
has a git tag and a GitHub release with the upload archive (`arsika-rollout-manager-vX.Y.Z.zip`).

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
