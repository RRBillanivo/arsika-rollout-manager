# Hosting without a terminal

Many hostings give only a file manager in a panel (cPanel, hPanel, DirectAdmin, Plesk …) or FTP: no SSH, no terminal,
no root. Some virtual machines are the same for you: you may drop files into the website folder, nothing more.
Updating a site there usually means zipping it and uploading it by hand. Arsika Rollout Manager can do that for you:
the website gets one PHP file, the **agent**, and every update goes through it.

```
Dashboard ── runner (on the dashboard's server)  ── HTTPS (or http), signed ──>  rollout-agent-xxxxxx.php
               GitHub or uploaded versions,                                      in the website folder:
               composer, checksums, health checks,                               unpack, database changes,
               backups (kept here)                                               swap files, artisan
```

The runner of the dashboard's server does everything it can do itself (download the release from GitHub with the
site's token, or take a version uploaded in the dashboard, install composer packages, checksums, health checks,
keeping the backups) and drives the agent: it uploads the release in pieces, the agent unpacks it next to the live
files, runs the release's database changes, swaps the files and keeps the previous version for a rollback. Long work
is split into short requests, so PHP's time limit is never reached: updates take a little longer than on a full
server, but they are automatic.

Any kind of website works: plain HTML/CSS/JS, the build of a front-end framework (`dist/`), PHP applications and
**Laravel** ([below](#laravel)). Sites that need a background service (a Python engine, a queue worker, a listener
that runs all the time …) need a full server (VPS / VM with the runner) for that part.

## Requirements

| | |
|---|---|
| PHP | 7.4 or newer, run by the web server (the agent is a normal PHP page); Laravel 11/12 needs PHP 8.2+ |
| Extensions | `zip` (ZipArchive, required); `zlib` for database backups; `pdo_mysql` (or `pdo_sqlite`) for database changes and backups |
| Disabled functions | fine: the agent needs no `exec`, `putenv`, `symlink` … (aaPanel's default `disable_functions` works; without `symlink` there is no public storage link) |
| Folder | the website folder (e.g. `public_html`) must be writable by PHP, as it is on most hostings |
| Address | HTTPS, or plain `http://` (e.g. a local IP address without a certificate), marked **No HTTPS**: the release, the `.env` and backups then travel unencrypted, the connection secret never does |
| Reachable | the **dashboard's server** must be able to open the address of the agent file (the hosting does not need to reach GitHub or the dashboard: the runner sends everything) |

## Connecting a site

1. **Sites → Add site**: **Where the website runs: Hosting / VM** and **Project source: Upload zip / rar** or
   **GitHub** (two buttons each). Choose a kind:
   - *Hosting: static site or built files*: nothing is shared between versions, no database.
   - *Hosting: PHP website*: `.env` is shared and holds the database settings.
   - *Hosting: Laravel*: see [below](#laravel).

   With GitHub, fill in the repository (with its read-only token for a private one); with Upload there is nothing
   to fill in: the versions are uploaded on the site page (zip, rar, 7z, tar.gz; see
   [SITES.md](SITES.md#uploaded-versions)). Under
   **Advanced**: **Release folder to publish** (`.` for the whole release, `dist` for a build), **Shared files** (data
   that stays from version to version: `.env`, `uploads/` …), **Never touched** (files of the hosting itself:
   `.well-known`, `cgi-bin`, `.user.ini`), the folder of the **database changes**, the health check and the backups.
2. Get it ready on the site page (it lists what is done): upload the first version, and for a Laravel site fill in the
   `.env` (**Edit .env**). Both can also be done after connecting.
3. Click **Download the agent file** (your password is asked): a file `rollout-agent-xxxxxx.php` with a one-time key
   for this site.
4. Put it into the website folder (the file manager of the hosting, FTP, or the folder of the VM's web server).
5. Open it in the browser (`https://example.com/rollout-agent-xxxxxx.php`; on a local network
   `http://192.168.1.20/rollout-agent-xxxxxx.php` works too). It checks the place (PHP, zip, folder, HTTPS) and shows
   the **address of the dashboard**, filled in already: change it only when you open the dashboard at another address.
   **Connect to the dashboard** opens the dashboard (signed in), which asks for your password and connects: the runner
   calls the agent with the one-time key, and both derive the site's own long secret from it. With **Deploy right
   after connecting** (when the website folder is empty) the newest version is installed at once and its log opens.

Only the dashboard that holds the file's key can connect it, so the address on the agent page may be changed safely.
The site page changes by itself once it is connected. A newly downloaded file replaces an earlier one that was not
connected yet; uploading a new file later connects again (for example after moving the site).

## First installation

- **Empty website folder** (only the agent file): deploy a version from the **Versions** tab (or right after
  connecting).
- **Files that were uploaded by hand**: the site shows **Needs adoption**. **Adopt now** with the version that runs
  now: the shared files stay in place, the other files move to `_pre_rollout` on the hosting (delete them from the
  dashboard when all is well), the chosen release goes live. Its database changes are marked as done by default,
  because the running website already uses them.

## Updates and database changes

A release may bring numbered SQL files in `database/migrations/` (the folder can be changed):

```
database/migrations/001_create_notes.sql
database/migrations/002_add_phone_to_users.sql
```

Before an update the dashboard shows the files that the hosting has not run yet, with their SQL, and you choose:

- **Run them automatically with PHP on the hosting** (default): the agent connects with the database settings of the
  site's environment file (`DB_*` or `DATABASE_URL` in `.env`, or `define('DB_NAME', …)` in a PHP file such as
  `wp-config.php`) and runs each file, before the new files go live.
- **I ran them myself** (e.g. in phpMyAdmin): they are only marked as done.

Rules for the files: one statement ends with `;` at the end of a line; `--` lines are comments. Keep the changes
additive (new tables, new columns that may be empty or have a default): a rollback swaps the files back, not the
database. A file that changes after it was run is not run again (the dashboard says so).

Each update: backup (if enabled) → upload and unpack the release → database changes → swap → health check. When the
health check fails, the previous files are swapped back at once. **Rollback** swaps back to a release that is still
kept on the hosting (by default 2 releases are kept, because hostings limit the number of files).

## Laravel

The preset **Hosting: Laravel** publishes only the release's `public/` folder in the website folder. The application
of every release (`app/`, `vendor/`, `bootstrap/` …), the `.env` and `storage/` stay in the agent's data folder, so
none of it can be opened in a browser, with Apache, LiteSpeed or nginx alike:

```
public_html/                        public/ of the live release (index.php written by the agent, .htaccess, assets)
    rollout-agent-xxxxxx.php        the agent
    storage -> …/shared/storage/app/public    the public storage link (when the hosting allows links)
../.rollout-agent-<site>/
    releases/<version>/             the application of a release (its public/ is in the website folder while live)
    shared/.env                     the .env of the site
    shared/storage/                 storage/ of the site (logs, sessions, cache, uploaded files)
```

**.env.** Edit it in the dashboard (**Edit .env** on the site page; the password is asked to open and to save it).
Before the first deployment the editor offers the `.env.example` of the newest release with production values
(`APP_ENV=production`, `APP_DEBUG=false`, `APP_URL`) and a new `APP_KEY`; fill in the database (`DB_HOST`,
`DB_DATABASE`, `DB_USERNAME`, `DB_PASSWORD` from the hosting's database page) and save. Saved before the agent is
connected, it is kept by the runner and sent when the agent connects. An empty `APP_KEY` is generated at the next
deployment, like `php artisan key:generate`. SQLite without `DB_DATABASE`: the database file is put into `shared/`.

**Migrations.** Before an update the dashboard lists the migration files of the release that are not in the database's
`migrations` table yet. **Run php artisan migrate** (default) runs `php artisan migrate --force` inside PHP on the
hosting, for the new release, before its files go live; when it fails, the update stops and the running version stays.
**Skip** leaves them for later (the artisan box).

**composer.** A release with `vendor/` (an uploaded project usually has it) is used as it is. Without `vendor/` (a
GitHub repository) the runner runs `composer install --no-dev --no-scripts --no-plugins --optimize-autoloader` on the
dashboard's server before the release is sent: install composer (and the PHP CLI) there. When `composer.json` asks for
a newer PHP than the hosting runs, the update stops before anything changes: choose a newer PHP in the hosting's panel.

**Artisan commands.** The site page has a box for `php artisan …` (password): the command runs inside PHP on the
hosting, on the live release, and its output appears in the job log. Examples: `migrate:status`, `migrate --force`,
`db:seed --force`, `optimize:clear`, `storage:link`, `tinker --execute="echo App\Models\User::count();"` (the agent runs
that code itself in the booted application, without PsySH; it is not written to the job log). Commands that
wait for input or run until they are stopped (`serve`, `tinker` without `--execute`, `queue:listen`, `queue:work`
without `--once` / `--stop-when-empty`, `schedule:work` …) are refused; any command is stopped by PHP's time limit.
Where the hosting disables `proc_open` (aaPanel does by default), commands that start other programs (`about`,
anything that calls composer or a shell) stop with Laravel's message "The Process class relies on proc_open".

**What a hosting cannot do.** Processes that run all the time (queue workers, `schedule:work`, a listener for a device
in the office network) need a server. The scheduler can run from the hosting panel's cron
(`php …/artisan schedule:run` where the panel offers a PHP cron), if the hosting has one.

**Web server.** Apache and LiteSpeed read the `.htaccess` of `public/` (Laravel's own front controller rules). With
nginx, the site's configuration needs `try_files $uri $uri/ /index.php?$query_string;` (aaPanel: **Site → URL rewrite →
laravel5**), otherwise only `/` answers.

**Adopting** a Laravel website that was uploaded by hand into the website folder takes over its `.env` (copied into
`shared/`) and its `storage/` (moved into `shared/` when the website switches).

## What lives where on the hosting

```
public_html/                          the live files of the current version, plus
    rollout-agent-xxxxxx.php          the agent (never replaced by a release)
    .env, uploads/ …                  shared files: stay in place (Laravel: in the data folder)
../.rollout-agent-<site>/             data of the agent, outside the website folder when the hosting allows it
    state.php                         secret, current version, database changes already run
    releases/<version>/               releases that are not live (for a rollback)
    releases/_pre_rollout/            the files from before the first installation
    shared/                           Laravel: .env and storage/
    env-previous/                     the last five versions of the .env saved from the dashboard
```

When the folder above the website cannot be used (not writable, or PHP's `open_basedir` keeps the agent in the website
folder), the data folder is inside the website folder with a random name (`.rollout-agent-<site>-<16 hex digits>`):
nginx does not read `.htaccess`, so the name must not be guessable; for Apache and LiteSpeed an `.htaccess` denies
every request, and the state file is a PHP file that stops before its content. Keep `APP_DEBUG=false` there: an error
page of Laravel shows paths.

Folders of other websites inside the website folder (e.g. a subdomain in `public_html/blog` with its own agent file)
are never moved; list other folders that belong to the hosting under **Never touched**.

`.htaccess` is kept as it is unless the release brings its own; then the release's file is used and cPanel's
`BEGIN cPanel-generated handler` lines (the PHP version of the site) are carried over. Symbolic links in a release are
skipped (a hosting usually does not allow them).

## The agent file updates itself

From 1.5.0 on, an agent that is older than the runner is replaced by the runner with the agent of its own version
before a deployment (same file name, same connection; the job log says so). An agent of 1.4.0 cannot do that yet:
download a new agent file once, put it in place of the old one and connect again.

## Backups

Backups of a hosting site are made by the agent (a SQL dump written by PHP for MySQL/MariaDB and SQLite, a zip of
the backup paths; Laravel: `.env` and `storage/app` from the data folder) and downloaded to the runner's server, then
removed from the hosting. Restore uploads them again. PostgreSQL databases on a hosting are not dumped (use the panel's
backups for them); big databases take longer.

## Security

- The agent answers only requests signed with the site's secret: HMAC-SHA256 over method, address, timestamp
  (±5 minutes) and body, with a one-time nonce. After 30 invalid requests in 10 minutes it stops answering for 10
  minutes. The one-time key in the file works once, only to connect; a file the runner writes when it updates the
  agent has a key that is marked as used.
- The page in the browser shows the checks and the connect form only before the site is connected; afterwards it says
  only that the site is connected.
- The agent only works inside its website folder and its data folder: paths with `..`, absolute paths and unsafe
  archives are refused, and it never runs shell commands. artisan commands run the site's own application inside PHP,
  only on request of the runner (and the dashboard asks for the password). Downloads are limited to its backups.
- Connecting sends no secret: the runner and the agent (1.5.5 and newer) derive the site's secret from the file's
  one-time key and a random salt (HMAC-SHA256). An agent file of an earlier version still receives its secret, so the
  runner connects it only over HTTPS; over plain `http://` download the agent file again.
- Plain `http://` agent addresses are accepted (a local network often has no certificate, and its addresses are not
  always private ones). Requests stay signed, but the release, the `.env` and the backups travel unencrypted, and the
  answers of the agent are not protected: use it only inside a network you trust, and HTTPS for an address on the
  internet.
- Removing the site from the dashboard makes the agent forget its secret; delete the file to remove it completely.
- Keep the random file name, use HTTPS on the internet, and keep the dashboard's own access protected (it can change
  the site).

## Troubleshooting

| Message | Cause and fix |
|---|---|
| The hosting answered HTTP 403 / 406 instead of the agent | A firewall of the hosting (ModSecurity, a WAF) blocks the requests: ask the hosting to allow requests to the agent file |
| The agent address redirects to … | Use the final address (https, with or without www) |
| not the address of the agent file | The address must end in the `.php` file of the agent, without `?…` or `#…` (`https://…` or `http://…`) |
| this agent file is from an earlier version | Plain `http://` with an agent file downloaded before 1.5.5: download the agent file again and replace it |
| the PHP extension zip is not enabled | Enable `zip` in the panel (PHP settings / extensions) |
| the file .env with the database settings is not on the hosting | The site has database changes or backups but its environment file is missing: upload it, or fill it in with **Edit .env** |
| the .env of the site is not set yet | Laravel: **Edit .env** on the site page, then deploy again |
| the release needs PHP … and the hosting runs PHP … | Choose a newer PHP version in the hosting's panel |
| composer is not installed on the dashboard's server | The release has no `vendor/`: upload the project with `vendor/`, or install composer on the dashboard's server |
| rar archives are unpacked with bsdtar … | Install `libarchive-tools` on the dashboard's server, or upload a .zip |
| php artisan migrate failed | The log shows Laravel's message (often the database settings in the `.env`); the running version stays |
| Only `/` answers, other pages 404 (nginx) | Add Laravel's rewrite to the nginx site (see [Laravel](#laravel)) |
| the agent file on the hosting is version 1.4.0 | Download a new agent file, put it in place of the old one, connect again |
| Not connected / Hosting not answering | The agent file was deleted or renamed, or the hosting is down: upload a new agent file and connect again |
