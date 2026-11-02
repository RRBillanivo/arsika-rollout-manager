# API

## Dashboard API (`/api/v1`, used by the interface)

JSON envelope `{"data": …}` / `{"error": {"code", "message", "fields"}}`; session cookie; header `X-CSRF-Token` on
POST/PUT/DELETE (token from `GET /app`); `X-Locale: id|en` for the message language. Endpoints marked 🔒 also need
`password` in the body.

| Method | Path | |
|---|---|---|
| GET | `/app` | version, CSRF token, signed-in user, state of this server's runner |
| POST | `/auth/login` · `/auth/logout` | `{username, password}` |
| GET / PUT | `/account` · `/account/password` | `{current_password, password, password_confirmation}` |
| GET | `/servers` | this server (`id: "local"`) and the other servers, each with `status` (runner `/v1/status`) or `error` |
| POST | `/servers/test` | `{url, secret, tls_pin, server?}` → the runner's status, or field errors (`url`, `secret`, `tls_pin`) with the reason; nothing is saved |
| POST 🔒 | `/servers` | `{name, url, secret, tls_pin}`; the connection is tested first |
| PUT 🔒 / DELETE 🔒 | `/servers/{server}` | change (empty `secret` keeps it) / forget the server (nothing changes on it) |
| GET | `/presets?server=` | presets of that server's runner (its web server user) |
| POST | `/github/check` | `{repo, asset_pattern, source, github_token?, site? \| server?}` → can the runner read the releases? Uses the typed token, else the saved token of `site` |
| GET | `/sites` | `{sites: [… with server], servers: [{id, name, local, ok, error}]}` (all runners asked at once) |
| POST 🔒 | `/sites` | register on `server` (default `local`); `github_token` optional: the site's read-only token |
| GET / PUT 🔒 / DELETE 🔒 | `/sites/{site}` | details with `server` (`github_token_set`, `github_token_hint`; never the token) / settings (`github_token` replaces the token, `remove_github_token: true` removes it) / unregister (files kept, token deleted) |
| GET | `/sites/{site}/releases?refresh=1` · `/sites/{site}/health` | GitHub releases with installed/current flags · health now |
| POST | `/sites/{site}/jobs` | `{type, version, params}`; 🔒 for `adopt`, `restore`, `delete-release`, `cleanup-adopt` |
| GET | `/sites/{site}/jobs/{job}?offset=` | the job with its log from byte `offset` |
| GET / DELETE 🔒 | `/sites/{site}/backups` · `/sites/{site}/backups/{backup}` | |
| GET | `/jobs?site=&limit=` · `/jobs/{job}?offset=` | history of a site · a job of this server's runner |
| GET | `/settings` | this server's runner (`github_tokens`: number of sites with their own token), versions |
| GET | `/activity` | sign-ins and administrator actions |

Site settings: `deploy_mode` (`files` \| `compose`), `source` (`asset` \| `source`), `compose_files`,
`compose_project`, `compose_build`, `backup_db` (`auto` \| `mysql` \| `postgres` \| `none`), `backup_db_service`,
`backup_volumes`, plus the settings of 1.1 (see [SITES.md](SITES.md)).

Job types: `deploy` (`version`, `params.backup`), `rollback` (`version` optional: the previous release),
`adopt` (`version`, `params.backup`), `backup` (`params.reason`), `restore` (`params.backup_id`, `params.safety_backup`),
`delete-release` (`version`), `cleanup-adopt`.

## Runner API (`/v1`, used by the dashboard and `python3 -m arsika_rollout_manager`)

`http://127.0.0.1:8790` for the dashboard's own server; `https://<address>:8790` (pinned certificate) for another
server. Headers on every request:

```
X-Rollout-Timestamp: <unix seconds>
X-Rollout-Nonce: <16-64 lowercase hex characters, new for every request>
X-Rollout-Signature: hex HMAC-SHA256(secret, METHOD "\n" PATH_WITH_QUERY "\n" TIMESTAMP "\n" NONCE "\n" hex SHA-256(body))
```

| Method | Path |
|---|---|
| GET | `/v1/status` (version, Python, host name, platform, web server user, TLS, `allow_from`, Docker Compose version, disk, site folders), `/v1/presets` |
| POST | `/v1/github/check` `{repo, asset_pattern, source, github_token?, site?}` |
| GET / POST | `/v1/sites` (`?health=0` skips the health checks; POST takes the optional `github_token` of the site) |
| GET / PUT / DELETE | `/v1/sites/{site}` (PUT: `github_token` or `remove_github_token`) |
| GET | `/v1/sites/{site}/releases`, `/v1/sites/{site}/health`, `/v1/sites/{site}/backups` |
| POST | `/v1/sites/{site}/jobs` `{type, version, params, actor}` (409 `busy` while a job of the site is queued or running) |
| DELETE | `/v1/sites/{site}/backups/{backup}` |
| GET | `/v1/jobs?site=&limit=`, `/v1/jobs/{id}?offset=` |
