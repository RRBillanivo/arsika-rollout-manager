# Sites

## Requirements for a site

- The site's folder is inside the site folders of its server's runner (aaPanel: `/www/wwwroot`; plain servers:
  `/var/www`, `/srv`; changeable with `install.sh --site-root=`).
- Every version is a **GitHub release** (tag `vX.Y.Z` or `X.Y.Z`) and the runner takes either
  - a **zip asset** of the release that contains the application at its root (e.g.
    `git archive --format=zip -o app_vX.Y.Z.zip vX.Y.Z`), or
  - the release's **source code** (GitHub's archive of the tag; no asset needed).
- Data that must survive updates lives in paths that can be shared between versions (`.env`, `storage/`, uploads …)
  or, for Docker Compose, in named volumes.
- Database changes of a new version stay compatible with the previous version (only add tables and columns), so a
  rollback keeps working.

## Adding a site

**Sites → Add site**: choose the server (when the dashboard controls more than one) and the kind of site:

| Preset | Runs as | Source | Shared | Document root | Hooks / health check |
|---|---|---|---|---|---|
| Anemia Web 3.x | files | asset `anemia_web_v*.zip` | `.env`, `storage/` | `public` → `current/public` | `preset:anemia-web` (Python environment, `db:check`, model self-test; systemd unit `anemia-engine`); `https://{domain}/api/v1/app` contains `"version":"{version}"` |
| PHP application | files | asset | `.env`, `storage/` | `public` → `current/public` | `https://{domain}/` |
| Static site | files | asset | – | `public` → `current` | `https://{domain}/` |
| Docker Compose: build | compose | source code | `.env` | – | images built from the release; `https://{domain}/` |
| Docker Compose: images | compose | source code | `.env` | – | images pulled from a registry; `https://{domain}/` |

Then fill in the domain (a folder in the server's site folder is suggested), the repository (`owner/name`) and, for a
zip asset, the file name pattern. For a private repository, paste the site's own **GitHub token**: a fine-grained token
with access to that repository only and the permission Contents: Read-only (a public repository needs none). **Check
access** confirms that the runner of the chosen server can read the releases with it. The token is stored by that
runner, never shown again, and can be replaced or removed later in the site's **Settings** tab.

Settings under **Advanced**:

| Setting | Meaning |
|---|---|
| Document root link / target | Link in the site folder that the web server serves (aaPanel: Running directory), and the folder of the release it points to; empty link: none |
| Shared files | Paths kept between versions; folders end with `/` |
| Web server user | Owner of the release files (aaPanel: `www`; Debian/Ubuntu: `www-data`; empty: unchanged, e.g. Docker) |
| Versions kept | Old releases beyond this number are removed (never the current one or the rollback target) |
| Build / activation command | Shell commands run as root (see [ARCHITECTURE.md](ARCHITECTURE.md#hooks)) |
| systemd services | Restarted after every switch |
| Reload PHP-FPM | Needed for PHP sites (OPcache) |
| Docker Compose | Compose files, project name, build or pull (see below) |
| Health check | Address that must answer 200 (and contain the text); `{domain}` and `{version}` are replaced |
| Backups | Database, environment file, paths, exclusions, volumes, number kept (see below) |

## First installation

- **Empty folder**: create `shared/.env` (and the database) by hand or by the application's installer, then deploy a
  version from the **Versions** tab.
- **Existing installation** (uploaded by hand, files directly in the site folder, or a `docker compose` project started
  there by hand): the site shows **Needs adoption**. In the **Versions** tab choose the version that is running now and
  click **Adopt now**. The site keeps running during the conversion; afterwards the old files are in `_pre_rollout/`
  and can be deleted from the dashboard. A site that is not adopted yet can already be backed up.

## Docker Compose

A Docker Compose site keeps the usual layout (`releases/<version>/`, `shared/`, `current`); each release folder holds
the application's compose files. Requirements on the server: Docker with the Compose v2 plugin (`docker compose
version`).

Deployment of version X:

1. The release is downloaded and extracted; shared paths (e.g. `.env`, `data/`) are linked into it.
2. `docker compose config` checks the files. A **bind mount** of a path inside the release that does not exist stops
   the deployment before anything changes ("add data/ to Shared files"): data that must outlive a version belongs in
   `shared/` or in a named volume.
3. Services with a `build:` section are built and tagged `<project>-<service>:X` (file `.rollout-compose.yml` in the
   release); the other images are pulled. With **Build the images from the release** off, nothing is built: use
   ready-made images, ideally tagged with the version, e.g. `image: ghcr.io/owner/app:${ROLLOUT_VERSION}`.
4. Build hook, switch of `current`, then `docker compose up -d --remove-orphans` with the release folder as project
   directory: compose recreates exactly the containers whose image or configuration changed. Activation hook, health
   check; on failure the previous release is started again (its images are still there, no rebuild).

The **project name** stays the same for every version, so containers are replaced in place and named volumes (the data)
are kept. It is the site folder's name unless set; an adoption takes over the name of the running project
(`COMPOSE_PROJECT_NAME`, `name:`, or the folder name). It cannot change once the site is installed.

Compose files: empty setting = `compose.yaml` / `compose.yml` / `docker-compose.yml` (plus its `*.override.*` file), or
`COMPOSE_FILE` from the release's `.env`. `ROLLOUT_VERSION` is set for every compose command. In hooks, a plain
`docker compose …` uses the right project and files (`COMPOSE_PROJECT_NAME` and `COMPOSE_FILE` are set), e.g. a
migration in the activation hook: `docker compose exec -T app php artisan migrate --force`.

When old releases are removed, their version-tagged images are removed too (an image still in use stays).

## Backups

Each backup holds what is configured, with SHA-256 checksums in `manifest.json`:

- **Database** (`backup_db`: automatic, MySQL/MariaDB, PostgreSQL or none):
  - *On the server*: connection from the site's **environment file** inside `shared/` (e.g. `.env`):
    `DB_CONNECTION`, `DB_HOST`, `DB_PORT`, `DB_DATABASE`, `DB_USERNAME`, `DB_PASSWORD` (Laravel style), or
    `DATABASE_URL` (`mysql://…`, `postgres://…`), or the `MYSQL_*` / `POSTGRES_*` variables of a compose `.env`.
    Tools: `mysqldump`/`mariadb-dump` and `mysql`, or `pg_dump` and `psql` (Debian/Ubuntu: `mariadb-client`,
    `postgresql-client`); passwords go through a 0600 option file or the environment, never the command line.
  - *In a container* (**Database container**, e.g. `db`): the dump runs inside that compose service (or, for a site
    that is not a compose site, that container) with the container's own credentials (`MYSQL_ROOT_PASSWORD`,
    `MARIADB_*`, `POSTGRES_USER`/`POSTGRES_PASSWORD`, also `*_FILE` secrets); nothing secret leaves the container.
    The database name comes from the environment file (`DB_DATABASE`) or the container (`MYSQL_DATABASE`,
    `POSTGRES_DB`).
- **Files**: the backup paths inside `shared/`, minus the exclusions.
- **Volumes**: named volumes of the compose file (e.g. `uploads`; other names are taken as Docker volume names),
  archived by a throw-away `busybox` container while the site keeps running. For databases prefer the dump above: a
  running database's files are not a consistent copy.

A restore verifies the checksums, puts the files back (the replaced ones are kept until the site is healthy), stops the
containers using a restored volume while it is replaced, imports the database, then runs the activation steps and the
health check. A safety backup of the current state is made first (on by default).

### Example: Anemia Web

1. Add site → preset **Anemia Web 3.x**, domain `anemia.example.com`, repository `RRBillanivo/Anemia_Web` and its
   fine-grained GitHub token (the repository is private).
2. Versions tab → **Adopt now** with the running version (backup first stays on).
   The preset builds a new Python environment in `shared/.rollout-cache/anemia-web/` (torch download, a few minutes),
   checks the models and the database, then points the `anemia-engine` service to `current/`.
3. Check the site, then delete `_pre_rollout/` (it holds the old Python environment, about 1.5 GB).
4. From now on: publish a release on GitHub → **Update to X.Y.Z** on the overview.

The Running directory stays `/public`; it is now a link into `current/`.

### Example: a Docker Compose application on another server

1. On the server: `bash deploy/install.sh --agent`; in the dashboard: **Servers → Add server** ([SERVERS.md](SERVERS.md)).
2. **Add site** → server `VM2`, preset **Docker Compose: build**, folder `/srv/shop` (the folder of the running
   project), repository with its token, Shared files `.env` (plus bind-mounted data folders such as `data/`),
   Database container `db`, volumes `uploads`.
3. Versions tab → **Adopt now** with the running version: the project name of the running containers is kept, the
   images of that version are built and the containers are recreated from the release folder.
4. Afterwards: **Update to X.Y.Z** builds the new images next to the running containers and switches in seconds.
