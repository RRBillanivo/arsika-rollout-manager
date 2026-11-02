# Sites

## Requirements for a site

- The site is an aaPanel site in `/www/wwwroot/` (other folders are refused).
- Every version is published as a **GitHub release** with a **zip asset** that contains the application at its root
  (e.g. built with `git archive --format=zip -o app_vX.Y.Z.zip vX.Y.Z`). Tags `vX.Y.Z` or `X.Y.Z`.
- Data that must survive updates lives in paths that can be shared between versions (`.env`, `storage/`, uploads ...).
- Database changes of a new version stay compatible with the previous version (only add tables and columns), so a
  rollback keeps working.

## Adding a site

**Sites → Add site**, choose the kind of site:

| Preset | Shared | Document root | Hooks | Health check |
|---|---|---|---|---|
| Anemia Web 3.x | `.env`, `storage/` | `public` → `current/public` | `preset:anemia-web` (Python environment, `db:check`, model self-test; systemd unit `anemia-engine`) | `https://{domain}/api/v1/app` contains `"version":"{version}"` |
| PHP application | `.env`, `storage/` | `public` → `current/public` | – | `https://{domain}/` |
| Static site | – | `public` → `current` | – | `https://{domain}/` |

Then fill in the domain (the folder `/www/wwwroot/<domain>` is suggested), the repository (`owner/name`) and the
release file pattern. For a private repository, paste the site's own **GitHub token**: a fine-grained token with access
to that repository only and the permission Contents: Read-only (a public repository needs none). Use **Check access**
to confirm that the runner can read the releases with it. The token is stored by the runner, never shown again, and
can be replaced or removed later in the site's **Settings** tab.

Settings under **Advanced**:

| Setting | Meaning |
|---|---|
| Document root link / target | Link in the site folder that aaPanel serves (Running directory), and the folder of the release it points to |
| Shared files | Paths kept between versions; folders end with `/` |
| Web server user | Owner of the release files (aaPanel: `www`) |
| Versions kept | Old releases beyond this number are removed (never the current one or the rollback target) |
| Build / activation command | Shell commands run as root (see [ARCHITECTURE.md](ARCHITECTURE.md#hooks)) |
| systemd services | Restarted after every switch |
| Reload PHP-FPM | Needed for PHP sites (OPcache) |
| Health check | Address that must answer 200 (and contain the text); `{domain}` and `{version}` are replaced |
| Backups | `.env` file with the MySQL settings (inside `shared/`), paths to archive, exclusions, number kept |

## First installation

- **Empty folder**: create `shared/.env` (and the database) by hand or by the application's installer, then deploy a
  version from the **Versions** tab.
- **Existing installation** (uploaded by hand, files directly in the site folder): the site shows **Needs adoption**.
  In the **Versions** tab choose the version that is running now and click **Adopt now**. The site keeps running during
  the conversion; afterwards the old files are in `_pre_rollout/` and can be deleted from the dashboard.

### Example: Anemia Web (anemia.arsika.my.id)

1. Add site → preset **Anemia Web 3.x**, domain `anemia.arsika.my.id`, repository `RRBillanivo/Anemia_Web` and its
   fine-grained GitHub token (the repository is private).
2. Versions tab → **Adopt now** with version `3.1.2` (backup first stays on).
   The preset builds a new Python environment in `shared/.rollout-cache/anemia-web/` (torch download, a few minutes),
   checks the models and the database, then points the `anemia-engine` service to `current/`.
3. Check the site, then delete `_pre_rollout/` (it holds the old Python environment, about 1.5 GB).
4. From now on: publish a release on GitHub → **Update to X.Y.Z** on the overview.

The Running directory stays `/public`; it is now a link into `current/`.
