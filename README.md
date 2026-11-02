# Arsika Rollout Manager

A self-hosted web dashboard to **deploy, update, roll back and back up web applications on your own Linux servers**,
straight from their **GitHub Releases**: a zip attached to the release, or the release's source code.

![version](https://img.shields.io/badge/version-1.2.0-4f46e5) ![license](https://img.shields.io/badge/license-MIT-16a34a)
![php](https://img.shields.io/badge/PHP-7.4%E2%80%938.4-777bb4) ![python](https://img.shields.io/badge/Python-3.6%E2%80%933.13-3776ab)

> **Status: early release.** Version 1.1 runs in production on an aaPanel server (adoption of an existing site,
> deployments, backups). The features of 1.2 (other servers, servers without a panel, Docker Compose, PostgreSQL and
> container backups) pass the automated tests but are new: try them with a test application first and keep your own
> backups.

## What it is for

Updating a web application by hand usually means: back up the data, upload a zip or pull the code, rebuild, restart
and hope nothing breaks. Arsika Rollout Manager does this for you:

1. You publish a release of your application on GitHub.
2. The dashboard shows that a new version is available.
3. One click backs up the database and the data, installs the new version next to the running one (and builds its
   Docker images, when it runs with Docker Compose), switches over and checks that the site answers.
4. If the check fails, the previous version is switched back automatically. Earlier versions stay on the server, so a
   manual rollback takes seconds, also for Docker Compose applications (their images are tagged with the version).

One dashboard manages many sites on **this server and on other servers** (in the local network, over a VPN or over the
internet): each other server runs only the small runner service.

## Features

- **Overview** of every site of every server: health (HTTP check), current version, latest GitHub release, last job.
- **Deploy** any release in one click; the new version is prepared next to the running one, activated, then checked.
  When the health check fails, the previous version is switched back automatically.
- **Two ways to run a site**
  - *Files* served by the web server: PHP applications, static sites, applications run by a systemd service.
  - *Docker Compose*: the compose files of the release are run with `docker compose up -d`; images are built from the
    release (tagged with its version) or pulled; named volumes and the project name stay the same across versions.
- **Two release sources:** a zip asset attached to the release, or GitHub's source code archive of the release tag.
- **Rollback** to any version still on the server within seconds.
- **Backups** before every update, on demand, and **restore** with a safety backup first:
  - databases: MySQL/MariaDB or PostgreSQL, on the server or inside a container (the dump runs in the container with
    the container's own credentials);
  - shared files (.env, storage/) and Docker volumes.
- **Adoption** of an installation made by hand (files in the site folder, or a running `docker compose` project)
  without downtime.
- **Several servers:** a server is added with its address, secret and certificate key pin; the dashboard talks to it
  over HTTPS and signs every request. A server that is down does not hide the others.
- **aaPanel and plain servers:** paths, web server user and nginx examples for both.
- **One GitHub token per site:** each site has its own read-only token (or none for a public repository). Tokens are
  kept by the runner of the site's server and never shown again.
- **Live job logs**, job history, activity log (sign-ins and every administrator action).
- Interface in Indonesian and English, light and dark theme, works on phones.

## Requirements

- Linux servers with root access and systemd (VPS, virtual machines, dedicated servers), with or without aaPanel.
- Runner (every server): Python 3.6 or newer (standard library only); Docker with Compose v2 for Docker Compose sites.
- Dashboard (one server): PHP 7.4 to 8.4 with `curl`, `pdo_mysql`, `openssl`, `mbstring`; MySQL 5.7+ / MariaDB 10.3+;
  a domain or sub-domain served over HTTPS.

## Installation

On the server that hosts the dashboard (aaPanel site or nginx + PHP-FPM), unzip the release archive
`arsika-rollout-manager-vX.Y.Z.zip` into the dashboard's folder and run as root:

```sh
bash deploy/install.sh              # dashboard + runner of this server
```

On every other server (runner only, no PHP needed):

```sh
bash deploy/install.sh --agent      # HTTPS runner; prints the address, secret and key pin for the dashboard
```

Then add the server in the dashboard under **Servers**. Guides: [docs/INSTALL.md](docs/INSTALL.md) (aaPanel and plain
servers), [docs/SERVERS.md](docs/SERVERS.md) (other servers), [docs/SITES.md](docs/SITES.md) (registering, adopting,
Docker Compose, backups).

## How it works

```
Browser ──HTTPS──> Dashboard (PHP)                                    MySQL: administrators, servers, audit log
                     │ signed requests (HMAC-SHA256)
                     ├──> 127.0.0.1:8790   runner of this server       (plain HTTP on loopback)
                     └──> https://vm2:8790 runner of another server    (HTTPS, pinned certificate, allowlist)
                              │ GitHub API, unzip, symlinks, docker compose, systemctl, dumps
                              ▼
<site>/  releases/<version>/  shared/  current -> releases/<version>  [public -> current/public]
```

The dashboard never runs as root: it authenticates the administrator and forwards signed requests to the runners, which
do the work. Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/SECURITY.md](docs/SECURITY.md),
[docs/API.md](docs/API.md).

## Project layout

```
app/                            dashboard (PHP): Core, Http, Models, Services, Install, Console
config/ routes/ lang/ database/schema/ bootstrap/ bin/console
public/                         web root: index.html (single-page app), index.php (API), assets/
runner/arsika_rollout_manager/  runner (Python, standard library only): API, job queue, deployments, compose, backups
runner/presets/                 build/activation hooks for known applications
runner/tests/                   runner integration test (fake GitHub, fake web site, fake docker)
deploy/                         install.sh, nginx examples (aaPanel and plain servers)
docs/                           architecture, installation, servers, sites, API, security
tests/smoke_test.py             end-to-end test: dashboard + two runners + fake GitHub
```

## Development and tests

```sh
python runner/tests/test_runner.py                              # runner (needs bash and symlink support; openssl for HTTPS)
python tests/smoke_test.py --php php [--php-ini php.ini]        # dashboard + runners end to end
```

Both tests run entirely on 127.0.0.1 with temporary folders, a fake GitHub API and a fake docker command.

## Versioning

[Semantic Versioning](https://semver.org): every version has a git tag and a GitHub release with the upload archive
`arsika-rollout-manager-vX.Y.Z.zip`. Changes: [CHANGELOG.md](CHANGELOG.md).

## Security

Please report security problems privately through GitHub (**Security → Report a vulnerability**), not in a public
issue.

## License

[MIT](LICENSE)
