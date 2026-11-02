# Arsika Rollout Manager

A self-hosted web dashboard to **deploy, update, roll back and back up web applications on your own Linux server**,
straight from the zip assets of their **GitHub Releases**.

![version](https://img.shields.io/badge/version-1.1.0-4f46e5) ![license](https://img.shields.io/badge/license-MIT-16a34a)
![php](https://img.shields.io/badge/PHP-7.4%2B-777bb4) ![python](https://img.shields.io/badge/Python-3.6%2B-3776ab)

> **Status: early release.** Version 1.1.0 passes its automated tests (a runner integration test and an end-to-end
> smoke test on PHP 7.4 and 8.3), but it has not been proven on production servers yet. Try it on a test server first
> and keep your own backups.

## What it is for

Updating a web application by hand usually means: back up the data, upload a zip, unpack it over the running site and
hope nothing breaks. Arsika Rollout Manager does this for you:

1. You publish a release of your application on GitHub, with the application as a zip asset.
2. The dashboard on your server shows that a new version is available.
3. One click backs up the database and the data files, installs the new version next to the running one, runs the
   application's build steps, switches over and checks that the site answers.
4. If the check fails, the previous version is switched back automatically. Earlier versions stay on the server, so a
   manual rollback takes seconds.

One installation manages several sites on the same server.

## Features

- **Overview** of every registered site: health (HTTP check), current version, latest GitHub release, services, last job.
- **Deploy** any release in one click; the new version is prepared next to the running one, activated, then checked.
  When the health check fails, the previous version is switched back automatically.
- **Rollback** to any version still on the server within seconds.
- **Backups** of the database (mysqldump) and the shared files (.env, storage) before every update, on demand, and
  **restore** with a safety backup first.
- **Adoption** of an installation made by hand (files directly in the site folder) without downtime.
- **One GitHub token per site:** each site has its own read-only token (or none for a public repository), so a leaked
  token exposes one repository only. Tokens are kept by the runner and never shown again.
- **Live job logs**, job history, activity log (sign-ins and every administrator action).
- Presets for PHP applications and static sites, plus Anemia Web 3.x as a complete example with a Python service; any
  site can define its own build and activation commands.
- Interface in Indonesian and English, light and dark theme, works on phones.

## Requirements

- A Linux server with root access and systemd (VPS, virtual machine or dedicated server).
- For the dashboard: PHP 7.4 to 8.3 with `curl`, `pdo_mysql`, `openssl`, `mbstring`, and MySQL 5.7+ / MariaDB 10.3+.
- For the runner: Python 3.6 or newer (standard library only).
- A domain or sub-domain for the dashboard, served over HTTPS.

The installer and the installation guide currently target servers managed with **aaPanel**. On other Linux servers
(nginx or Apache without a panel) the paths have to be adjusted by hand for now; built-in support is planned.

## Installation (aaPanel)

Create a site and a MySQL database, upload the release archive `arsika-rollout-manager-vX.Y.Z.zip` into the site
folder and unzip it, then run as root:

```sh
cd /www/wwwroot/rollout.example.com
bash deploy/install.sh
```

Set the site's running directory to `/public`, paste [deploy/nginx/aapanel.conf](deploy/nginx/aapanel.conf) as URL
rewrite rules, and open the one-time administrator setup link printed by the script. Full guide:
[docs/INSTALL.md](docs/INSTALL.md). Registering sites and adopting an existing installation: [docs/SITES.md](docs/SITES.md).

## How it works

```
Browser ──HTTPS──> Dashboard (PHP site, public/)                     MySQL: administrators, audit log
                        │  signed requests (HMAC-SHA256) to 127.0.0.1:8790
                        ▼
                   Runner (Python, systemd service, root)            SQLite: sites, releases, jobs, backups
                        │  GitHub API, unzip, symlinks, systemctl, mysqldump
                        ▼
/www/wwwroot/<site>/  releases/<version>/  shared/  current -> releases/<version>   public -> current/public
```

The dashboard never runs as root: it authenticates the administrator and forwards signed requests to the runner, which
does the work. Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/SECURITY.md](docs/SECURITY.md),
[docs/API.md](docs/API.md).

## Project layout

```
app/                            dashboard (PHP): Core, Http, Models, Services, Install, Console
config/ routes/ lang/ database/schema/ bootstrap/ bin/console
public/                         web root: index.html (single-page app), index.php (API), assets/
runner/arsika_rollout_manager/  runner (Python, standard library only): API, job queue, deployments, backups
runner/presets/                 build/activation hooks for known applications
runner/tests/                   runner integration test (fake GitHub, fake web site)
deploy/                         install.sh, nginx rules
docs/                           architecture, installation, sites, API, security
tests/smoke_test.py             end-to-end test: dashboard + runner + fake GitHub
```

## Development and tests

```sh
python runner/tests/test_runner.py                              # runner (needs bash and symlink support)
python tests/smoke_test.py --php php [--php-ini php.ini]        # dashboard + runner end to end
```

Both tests run entirely on 127.0.0.1 with temporary folders and a fake GitHub API.

## Versioning

[Semantic Versioning](https://semver.org): every version has a git tag and a GitHub release with the upload archive
`arsika-rollout-manager-vX.Y.Z.zip`. Changes: [CHANGELOG.md](CHANGELOG.md).

## Security

Please report security problems privately through GitHub (**Security → Report a vulnerability**), not in a public
issue.

## License

[MIT](LICENSE)
