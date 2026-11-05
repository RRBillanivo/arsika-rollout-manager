# Hosting without a terminal

Many hostings give only a file manager in a panel (cPanel, hPanel, DirectAdmin, Plesk …): no SSH, no terminal, no root.
Updating a site there usually means zipping it and uploading it by hand. Arsika Rollout Manager can do that for you:
the website gets one PHP file, the **agent**, and every update goes through it.

```
Dashboard ── runner (on the dashboard's server)  ── HTTPS, signed requests ──>  rollout-agent-xxxxxx.php
               GitHub, checksums, health checks,                                 in the website folder of the hosting:
               backups (kept here)                                               unpack, database changes, swap files
```

The runner of the dashboard's server does everything it can do itself (download the release from GitHub with the
site's token, checksums, health checks, keeping the backups) and drives the agent: it uploads the release in pieces,
the agent unpacks it next to the live files, runs the release's database changes, swaps the files and keeps the
previous version for a rollback. Long work is split into short requests, so the hosting's PHP time limit is never
reached: updates take a little longer than on a full server, but they are automatic.

Any kind of website works: plain HTML/CSS/JS, the build of a front-end framework (`dist/`), PHP applications. Sites
that need a background service (a Python engine, a queue worker …) need a full server (VPS / VM) instead.

## Requirements on the hosting

| | |
|---|---|
| PHP | 7.4 or newer, run by the web server (the agent is a normal PHP page) |
| Extensions | `zip` (ZipArchive, required); `zlib` for database backups; `pdo_mysql` (or `pdo_sqlite`) for database changes and backups |
| Folder | the website folder (e.g. `public_html`) must be writable by PHP, as it is on most hostings |
| Address | HTTPS (the dashboard refuses plain http to another machine) |

The hosting does not need to reach GitHub or the dashboard: the runner sends everything.

## Connecting a site

1. **Sites → Add site**, **Where the website runs: Hosting**. Choose a kind:
   - *Hosting: static site or built files*: nothing is shared between versions, no database.
   - *Hosting: PHP website*: `.env` is shared and holds the database settings.

   Fill in the domain and the repository (with its read-only token for a private one). Under **Advanced**:
   **Release folder to publish** (`.` for the whole release, `dist` for a build), **Shared files** (data that stays
   from version to version: `.env`, `uploads/`, `storage/` …), **Never touched** (files of the hosting itself:
   `.well-known`, `cgi-bin`, `.user.ini`), the folder of the **database changes**, the health check and the backups.
2. The site page shows **Connect the hosting**. Click **Download the agent file** (your password is asked): a file
   `rollout-agent-xxxxxx.php` with a one-time key for this site.
3. Upload it with the hosting's file manager into the website folder (usually `public_html`).
4. Open it in the browser (`https://example.com/rollout-agent-xxxxxx.php`). It checks the hosting (PHP, zip, folder,
   HTTPS) and shows **Connect to the dashboard**. The button opens the dashboard (signed in), which asks for your
   password and connects: the runner calls the agent with the one-time key and gives it its own long secret.

The site page changes by itself once it is connected. A newly downloaded file replaces an earlier one that was not
connected yet; uploading a new file later connects again (for example after moving the site).

## First installation

- **Empty website folder** (only the agent file): deploy a version from the **Versions** tab.
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

## What lives where on the hosting

```
public_html/                          the live files of the current version, plus
    rollout-agent-xxxxxx.php          the agent (never replaced by a release)
    .env, uploads/ …                  shared files: stay in place
../.rollout-agent-<site>/             data of the agent, outside the website folder when the hosting allows it
    state.php                         secret, current version, database changes already run
    releases/<version>/               releases that are not live (for a rollback)
    releases/_pre_rollout/            the files from before the first installation
```

When the folder above the website is not writable, the data folder is inside the website folder
(`public_html/.rollout-agent-<site>/`) and protected with an `.htaccess` that denies every request; the state file is
a PHP file that stops before its content.

Folders of other websites inside the website folder (e.g. a subdomain in `public_html/blog` with its own agent file)
are never moved; list other folders that belong to the hosting under **Never touched**.

`.htaccess` is kept as it is unless the release brings its own; then the release's file is used and cPanel's
`BEGIN cPanel-generated handler` lines (the PHP version of the site) are carried over. Symbolic links in a release are
skipped (a hosting usually does not allow them).

## Backups

Backups of a hosting site are made by the agent (a SQL dump written by PHP for MySQL/MariaDB and SQLite, a zip of
the backup paths) and downloaded to the runner's server, then removed from the hosting. Restore uploads them again.
PostgreSQL databases on a hosting are not dumped (use the panel's backups for them); big databases take longer.

## Security

- The agent answers only requests signed with the site's secret: HMAC-SHA256 over method, address, timestamp
  (±5 minutes) and body, with a one-time nonce. After 30 invalid requests in 10 minutes it stops answering for 10
  minutes. The one-time key in the file works once, only to connect.
- The page in the browser shows the checks and the connect button only before the site is connected; afterwards it
  says only that the site is connected.
- The agent only works inside its website folder and its data folder: paths with `..`, absolute paths and unsafe
  archives are refused, and it never runs shell commands. Downloads are limited to its backups.
- Removing the site from the dashboard makes the agent forget its secret; delete the file to remove it completely.
- Keep the random file name, use HTTPS, and keep the dashboard's own access protected (it can change the site).

## Troubleshooting

| Message | Cause and fix |
|---|---|
| The hosting answered HTTP 403 / 406 instead of the agent | A firewall of the hosting (ModSecurity, a WAF) blocks the requests: ask the hosting to allow requests to the agent file |
| The agent address redirects to … | Use the final address (https, with or without www) |
| the PHP extension zip is not enabled | Enable `zip` in the panel (PHP settings / extensions) |
| the file .env with the database settings is not on the hosting | The site has database changes or backups but its environment file is missing: upload it or change the setting |
| Not connected / Hosting not answering | The agent file was deleted or renamed, or the hosting is down: upload a new agent file and connect again |
