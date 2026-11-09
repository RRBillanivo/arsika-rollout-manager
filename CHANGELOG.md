# Changelog

All notable changes are documented here. The project follows [Semantic Versioning](https://semver.org); every version
has a git tag and a GitHub release with the upload archive (`arsika-rollout-manager-vX.Y.Z.zip`).

## [1.5.5] — 2026-11-09

### Changed
- Hosting sites may be connected over plain `http://` at any address, e.g. a local network whose IP addresses are not
  in a private range. Before, plain http was refused unless the address looked local (a private IP range,
  `localhost`, `.local` …). The agent page shows "!" instead of "✗" for http, the dashboard marks the site
  **No HTTPS** and says what travels unencrypted (the release, the `.env`, backups).

### Security
- Connecting a hosting site no longer sends a secret: the runner and the agent derive the site's secret from the one-time
  key in the agent file and a random salt. An agent file downloaded before 1.5.5 still receives its secret, so it is
  connected only over HTTPS; over plain http the dashboard asks to download the agent file again. Agent 1.5.5 (connected
  sites are updated automatically).

## [1.5.4] — 2026-11-08

### Fixed
- `tinker --execute="…"` on aaPanel stopped with "open_basedir restriction … /usr/share/terminfo": PsySH looks for a
  terminal and for folders outside the site. The agent now runs the code itself inside the booted application (the same
  result, no PsySH). The code is no longer written to the job log or the job message, the runner forgets it when the
  job ends and does not hand it to the dashboard (it may hold passwords). Agent 1.5.4 (replaced automatically).

## [1.5.3] — 2026-11-07

### Fixed
- Laravel on aaPanel (and hostings that disable PHP functions): `php artisan migrate` stopped with "Call to undefined
  function putenv()". The agent no longer needs `putenv()`, `set_time_limit()` or `chdir()`, and an error of an artisan
  command is reported as itself instead of "the command ended the PHP request early". The tests run the agent with
  aaPanel's list of disabled functions. Agent 1.5.3 (replaced automatically).
- `tinker --execute` under open_basedir (aaPanel allows PHP only the site's folder): PsySH gets its folders in
  `storage/framework/psysh` instead of the home folder, `/etc/xdg` and `/usr/share`.

## [1.5.2] — 2026-11-07

### Added
- **Deploy again** for the live release of a hosting site: its preparation (Laravel: `.env`, `storage/`,
  `public/index.php`) and database changes run once more, without switching files.

### Fixed
- A hosting release whose deployment stopped before the switch (e.g. Laravel without a `.env` yet) could be made live
  with **Activate** (a rollback) without being prepared: a Laravel site then served its original `public/index.php`
  (open_basedir errors on aaPanel). Such a release now offers **Deploy** instead, a rollback to it is refused, and a
  rollback of a Laravel site prepares the release first.
- Health checks fail when the page is PHP's own fatal or parse error output (with warnings before it, its status can
  still be 200).
- Browsers no longer fill the site id of "Add site" with the saved user name.

## [1.5.1] — 2026-11-07

### Changed
- "Add site" starts with two choices as two buttons each: **where the website runs** (Hosting / VM, where you only drop
  files, or a full server) and **where the versions come from** (Upload zip / rar, or GitHub). The GitHub fields
  appear only for GitHub. A new site starts with Hosting / VM and Upload.

### Fixed
- A runner that is newer than the agent it ships no longer tries to replace an agent of the same version (deployments
  of hosting sites would wait and then stop with "the hosting still runs the old agent"): the agent is compared with
  the agent template of the runner.

## [1.5.0] — 2026-11-07

### Added
- **Versions uploaded in the dashboard** (release source "Upload"): a zip, rar, 7z or tar.gz of the project is sent in
  pieces (1 MB through the dashboard), unpacked and checked on the runner's server (unsafe paths, links, size and
  number of files), a single top folder is stripped and what must not go live is removed (`upload_exclude`: the local
  `.env`, `.git/`, `node_modules/`, logs, compiled views, caches …). The version name comes from the file name
  (`absensi#003.rar` → `003`). Uploaded versions are deployed, rolled back and pruned like releases (`upload_keep`), for
  every kind of site; no GitHub repository needed. rar and 7z need bsdtar (`libarchive-tools`), 7-Zip or unrar on the
  runner's server.
- **Laravel on a hosting** (preset "Hosting: Laravel"): only `public/` goes into the website folder; the application of
  every release, the `.env` and `storage/` stay in the agent's data folder (outside the website folder when possible),
  so nothing of the application can be opened in a browser, whatever the web server. The agent writes a
  `public/index.php` that points at them, runs **`php artisan migrate --force` inside PHP** before the switch (pending
  migrations shown first, from the database's `migrations` table), generates an empty `APP_KEY`, creates the public
  storage link when links are allowed and clears compiled views. `vendor/` missing (a GitHub repository) → the runner
  runs `composer install --no-dev --no-scripts --no-plugins` on its server. A release whose `composer.json` needs a
  newer PHP than the hosting has stops before anything changes. Adopting a Laravel website that was uploaded by hand
  takes over its `.env` and `storage/`.
- **Artisan commands** for Laravel hosting sites (password): run inside PHP on the live release, output in the job log;
  commands that wait for input or keep running are refused (`tinker` works with `--execute`).
- **The .env of a hosting site in the dashboard** (password to open and to save; the content never goes into the
  activity log): prepared before the hosting is connected (a template from the release's `.env.example` with
  production values and a new `APP_KEY`) and sent when it connects, or edited on the hosting later (the last five
  versions are kept there).
- **Connect and deploy at once:** the agent page has the address of the dashboard filled in (it can be changed), and
  the confirmation in the dashboard can deploy the newest version right away.
- Hosting agents **update themselves** from 1.5.0 on (the runner replaces the file with its own version when needed;
  agents of 1.4.0 are replaced once by hand). Plain `http://` agent addresses are accepted on local networks (a VM;
  marked "No HTTPS"). Health checks may use `{site_url}` (the address of the agent file's folder); a domain may be an
  IPv4 address.

### Changed
- The data folder of an agent inside the website folder gets a random name (`.rollout-agent-<site>-<random>`), because
  nginx does not read `.htaccess`; an existing one is renamed.

### Fixed
- The site page added its click handlers again after reloading itself (after connecting or saving), so one click
  could open two dialogs.

## [1.4.0] — 2026-11-06

### Added
- **Websites on a hosting without a terminal** (only a file manager, no SSH): "Add site" first asks where the website
  runs, on a full server (VPS / VM) or on a hosting. For a hosting the dashboard generates a PHP agent file with a
  one-time key; it is uploaded with the file manager, opened in the browser and connected with one click ("Connect to
  the dashboard", then the password). The runner of the dashboard's server then downloads the releases from GitHub,
  uploads them to the agent in pieces, and the agent unpacks them next to the live files, swaps them in, keeps the
  previous version for a rollback and the files from before the first installation (`_pre_rollout`). Work is split
  into short requests (PHP time limits). Any kind of website: static, a build (`dist/` published), PHP.
- **Database changes shown before an update** (hosting sites): numbered SQL files of the release
  (`database/migrations/*.sql`) that have not run yet, with their SQL; run automatically with PHP on the hosting, or
  marked as done after running them by hand. Database settings from `.env` (`DB_*`, `DATABASE_URL`) or `wp-config.php`.
- Backups of hosting sites (SQL dump by PHP for MySQL/MariaDB and SQLite, zip of the backup paths) kept on the
  runner's server and restored through the agent.
- Presets "Hosting: static site or built files" and "Hosting: PHP website"; new site states "not connected" and
  "hosting not answering"; guide `docs/HOSTING.md`.

## [1.3.0] — 2026-11-02

### Added
- **Arsika Rollout Manager updates itself.** Preset "Arsika Rollout Manager (this dashboard)": the dashboard is adopted
  once as a site of its own server; afterwards **Update to X.Y.Z** backs up its database and `.env`, checks the new
  release (`deploy/install.sh --self-update=build`: PHP syntax, database, the new runner code with the server's Python
  and settings), switches, installs that release's runner and systemd unit (`--self-update=activate`) and checks the
  health; an automatic rollback puts the previous runner code back. A release brings its own update steps.
- **Update runner** for other servers (Servers page, or `python3 -m arsika_rollout_manager update-runner X.Y.Z`): the
  runner downloads that release from GitHub (`update_repo`, SHA-256 checked), runs its `install.sh --agent
  --self-update` and restarts. Job pages for jobs of a runner (`#/servers/<id>/jobs/<n>`).
- The runner restarts with new code when a job asks for it (`ROLLOUT_RESTART_FILE`), after the job's state is saved.
- **Health check through this server** (`health_local`): 127.0.0.1 with the site's host name (for https also SNI; one
  redirect to the same host followed), for sites behind Cloudflare Access, a CDN or a firewall.

### Fixed
- `.env` behind a link (an installed release) is written through the link instead of being replaced by a file.
- Running `install.sh --agent` again no longer resets a runner address set with `--listen` to `0.0.0.0`.
- `install.sh` leaves no `__pycache__` folders behind and gives the content of a linked `storage/` to the web server user.

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
