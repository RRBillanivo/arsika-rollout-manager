# Installation

One server hosts the **dashboard** and its own runner. Every other server only runs the **runner** (see
[SERVERS.md](SERVERS.md)). This guide installs the dashboard, on aaPanel or on a plain server without a panel.

## Requirements

| | |
|---|---|
| Server | Linux with systemd and root access (SSH, or aaPanel **Terminal**) |
| Web server | aaPanel (Nginx), or Nginx + PHP-FPM on a plain server |
| PHP | 7.4 to 8.4 with `curl`, `pdo_mysql`, `openssl`, `mbstring` |
| Database | MySQL 5.7+ / MariaDB 10.3+ for the dashboard (the sites may use any database) |
| Python | 3.6 or newer (`/usr/bin/python3`; on aaPanel its own Python is used when there is no other) |
| Domain | a sub-domain for the dashboard, e.g. `rollout.example.com`, served over HTTPS |

The installer recognises aaPanel (`/www/server/panel`) and picks the matching defaults:

| | aaPanel | Plain server |
|---|---|---|
| Site folders the runner accepts | `/www/wwwroot` | `/var/www`, `/srv` |
| Backups | `/www/backup/arsika-rollout-manager` | `/var/backups/arsika-rollout-manager` |
| Owner of release files | `www` | `www-data` (or `nginx` / `apache`) |

Other values: `--site-root=/var/www,/opt/apps`, `--backup-dir=...`, `--web-user=...`, `--port=...` (see the top of
`deploy/install.sh`). An existing runner keeps its settings unless such an option is given.

## A. aaPanel

1. **Website → Add site**: `rollout.example.com`, PHP 7.4 or newer. **Databases → Add database**: note the name, user
   and password.
2. Download `arsika-rollout-manager-vX.Y.Z.zip` from the Releases page, upload it into the site folder (**Files**),
   unzip it there and delete the zip. The folder must contain `app/`, `public/`, `runner/`, `deploy/` directly.
3. Run as root:

   ```sh
   cd /www/wwwroot/rollout.example.com
   bash deploy/install.sh
   ```

   The script asks for the dashboard address and the database, installs the runner (`/opt/arsika-rollout-manager`,
   settings in `/etc/arsika-rollout-manager/runner.json`, systemd unit `arsika-rollout-manager-runner`), writes `.env`
   (with `RUNNER_SECRET`), checks the connection dashboard → runner and prints a **one-time administrator setup link**.
4. **Website → site → Site directory → Running directory** = `/public` → Save.
5. **Website → site → URL rewrite**: replace the content with [`deploy/nginx/aapanel.conf`](../deploy/nginx/aapanel.conf) → Save.
6. **SSL → Let's Encrypt** → apply, then **Force HTTPS**. Restricting access by IP (**Site → Access restriction**) adds
   another layer.

The default aaPanel PHP restrictions (`disable_functions`, `open_basedir`) can stay on: PHP only talks to the runners
over HTTP(S).

## B. Plain server (Debian / Ubuntu)

1. Packages: `apt install nginx php-fpm php-cli php-mysql php-curl php-mbstring mariadb-server python3 openssl`
   (and `certbot python3-certbot-nginx` for Let's Encrypt).
2. Database: `mysql -e "CREATE DATABASE rollout; CREATE USER 'rollout'@'localhost' IDENTIFIED BY '<password>';
   GRANT ALL ON rollout.* TO 'rollout'@'localhost';"`
3. Unzip the release into e.g. `/var/www/rollout` (it must contain `app/`, `public/`, `runner/`, `deploy/`), then:

   ```sh
   cd /var/www/rollout
   sudo bash deploy/install.sh
   ```
4. Nginx: copy [`deploy/nginx/server.conf`](../deploy/nginx/server.conf), set `server_name`, `root`
   (`/var/www/rollout/public`), the certificate and the PHP-FPM socket (`ls /run/php/`), enable it, `nginx -t`,
   `systemctl reload nginx`. Let's Encrypt: `certbot --nginx -d rollout.example.com`.

## Administrator

Open the setup link, choose a password (at least 12 characters), sign in as `admin`.

## Next

- Register the sites of this server: **Sites → Add site** ([SITES.md](SITES.md)).
- Other servers: `bash deploy/install.sh --agent` there, then **Servers → Add server** ([SERVERS.md](SERVERS.md)).

## Updating

**From the dashboard (1.3.0 and newer).** Register the dashboard once as a site of its own server: **Sites → Add site**,
kind **Arsika Rollout Manager (this dashboard)**, the dashboard's domain and folder, then **Adopt now** with the
installed version (see [SITES.md](SITES.md#arsika-rollout-manager-itself)). From then on a new release shows
**Update to X.Y.Z** like any site: backup of the dashboard database and `.env`, checks of the new release, switch,
installation of its runner (which restarts when the job has ended), health check, automatic rollback on failure. The
runners of other servers: **Servers → Update runner** ([SERVERS.md](SERVERS.md#changing-or-removing)).

After that adoption the dashboard lives in `releases/`: never unzip a release over its folder again. The commands of
the installer are run from `current/`, e.g. `bash current/deploy/install.sh --admin-link`. Without the dashboard (it
does not load after an update): `cd /opt/arsika-rollout-manager && python3 -m arsika_rollout_manager rollback <id>`.

**By hand** (before the adoption, or from 1.2 and older): upload and unzip the new release over the old files (keep
`.env` and `storage/`), then run `bash deploy/install.sh` again and answer **N** to "Change the database settings?".
The runner code is replaced and restarted; its settings, sites, history and backups are kept. The dashboard database
gets new tables automatically. Other servers: unzip the release there and run `bash deploy/install.sh --agent` again
(their certificate, key pin and settings are kept).

## Commands

```sh
systemctl status arsika-rollout-manager-runner          # runner state
journalctl -u arsika-rollout-manager-runner -n 50       # runner messages

cd /opt/arsika-rollout-manager                          # the runner's command line, from its folder:
python3 -m arsika_rollout_manager status                # sites, versions
python3 -m arsika_rollout_manager deploy example 3.2.0  # deploy without the dashboard
python3 -m arsika_rollout_manager rollback example
python3 -m arsika_rollout_manager connection-info       # address, secret and key pin (servers installed with --agent)
python3 -m arsika_rollout_manager update-runner 1.3.0   # install the runner of that release on this server (--agent)

cd /www/wwwroot/rollout.example.com                     # from the dashboard's folder (after the adoption: current/):
bash deploy/install.sh --admin-link                     # forgotten administrator password
bash deploy/install.sh --db                             # change the dashboard database
```

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Banner "The runner does not answer" | `systemctl status arsika-rollout-manager-runner`; `journalctl -u arsika-rollout-manager-runner -n 50` |
| "The runner refused the request" | `RUNNER_SECRET` in `.env` differs from `/etc/arsika-rollout-manager/runner.json`: run `bash deploy/install.sh` again |
| GitHub: repository or release not found | Private repository without a token, or the site's token has no access to it (site → Settings → GitHub token) |
| Blank page or 403 | The web server's root is not the `public/` folder (aaPanel: Running directory `/public`) |
| "API not found" | Rewrite rules missing (aaPanel: URL rewrite; nginx: `location ^~ /api/`) |
| A server "does not answer" | See [SERVERS.md](SERVERS.md#troubleshooting) |
