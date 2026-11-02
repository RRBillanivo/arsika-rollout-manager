# Installation on aaPanel

## Requirements

| | |
|---|---|
| Server | Linux with systemd and root access (aaPanel **Terminal** or SSH) |
| aaPanel | Nginx, MySQL 5.7+ / MariaDB 10.3+, PHP 7.4 to 8.3 with `curl`, `pdo_mysql`, `openssl`, `mbstring` |
| Python | 3.6 or newer (`/usr/bin/python3`; aaPanel's own Python is used when there is no other) |
| Domain | a sub-domain for the dashboard, e.g. `rollout.example.com` |

The default aaPanel PHP restrictions (`disable_functions`, `open_basedir`) can stay on: PHP only talks to the runner
over HTTP.

## 1. Site and database

1. **Website → Add site**: `rollout.example.com`, PHP 7.4 or newer.
2. **Databases → Add database**: note the name, user and password.

## 2. Upload

Download `arsika-rollout-manager-vX.Y.Z.zip` from the repository's Releases page, upload it into
`/www/wwwroot/rollout.example.com/` (**Files**), unzip it there and delete the zip. The folder must contain `app/`,
`public/`, `runner/`, `deploy/` directly.

## 3. Install script

```sh
cd /www/wwwroot/rollout.example.com
bash deploy/install.sh
```

The script asks for the dashboard address and the database, then installs the runner
(`/opt/arsika-rollout-manager`, settings in `/etc/arsika-rollout-manager/runner.json`, systemd unit
`arsika-rollout-manager-runner`), writes `.env` (with `RUNNER_SECRET`), checks the connection dashboard → runner and
prints a **one-time administrator setup link**.

## 4. Site settings in aaPanel

1. **Website → site → Site directory → Running directory** = `/public` → Save.
2. **Website → site → URL rewrite**: replace the content with [`deploy/nginx/aapanel.conf`](../deploy/nginx/aapanel.conf) → Save.
3. **SSL → Let's Encrypt** → apply, then **Force HTTPS**. The dashboard can deploy code as root: always use HTTPS.
   Restricting access by IP (aaPanel **Site → Access restriction**) adds another layer.

## 5. Administrator and GitHub token

1. Open the setup link, choose a password (at least 12 characters), sign in as `admin`.
2. **Settings → GitHub token**: create a fine-grained token on GitHub (Repository access: only the web repositories;
   Permissions: Contents → Read-only) and paste it. It is stored by the runner (root only) and never shown again.

## 6. Register the sites

See [SITES.md](SITES.md).

## Updating the dashboard

Upload and unzip the new release over the old files (keep `.env` and `storage/`), then run `bash deploy/install.sh`
again and answer **N** to "Change the database settings?". The runner code is replaced and restarted; its settings,
sites, history and backups are kept.

## Commands

```sh
systemctl status arsika-rollout-manager-runner         # runner state
journalctl -u arsika-rollout-manager-runner -n 50      # runner messages

cd /opt/arsika-rollout-manager                         # the runner's command line, from its folder:
python3 -m arsika_rollout_manager status               # sites, versions
python3 -m arsika_rollout_manager deploy example 3.2.0 # deploy without the dashboard
python3 -m arsika_rollout_manager rollback example

cd /www/wwwroot/rollout.example.com                    # from the dashboard's folder:
bash deploy/install.sh --admin-link                    # forgotten administrator password
bash deploy/install.sh --db                            # change the dashboard database
```

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Banner "The runner does not answer" | `systemctl status arsika-rollout-manager-runner`; `journalctl -u arsika-rollout-manager-runner -n 50` |
| "The runner refused the request" | `RUNNER_SECRET` in `.env` differs from `/etc/arsika-rollout-manager/runner.json`: run `bash deploy/install.sh` again |
| GitHub: repository or release not found | Private repository without a token, or the token has no access to it (Settings → GitHub token) |
| Blank page or 403 | Running directory is not `/public` |
| "API not found" | URL rewrite rules missing |
