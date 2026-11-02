# API

## Dashboard API (`/api/v1`, used by the interface)

JSON envelope `{"data": …}` / `{"error": {"code", "message", "fields"}}`; session cookie; header `X-CSRF-Token` on
POST/PUT/DELETE (token from `GET /app`); `X-Locale: id|en` for the message language. Endpoints marked 🔒 also need
`password` in the body.

| Method | Path | |
|---|---|---|
| GET | `/app` | version, CSRF token, signed-in user, runner state |
| POST | `/auth/login` · `/auth/logout` | `{username, password}` |
| GET / PUT | `/account` · `/account/password` | `{current_password, password, password_confirmation}` |
| GET | `/presets` | presets with their default settings |
| POST | `/github/check` | `{repo, asset_pattern, github_token?, site?}` → can the runner read the releases? Uses the typed token, else the saved token of `site` |
| GET / POST 🔒 | `/sites` | list with health and versions / register (`github_token` optional: the site's read-only token) |
| GET / PUT 🔒 / DELETE 🔒 | `/sites/{site}` | details (`github_token_set`, `github_token_hint`; never the token) / settings (`github_token` replaces the token, `remove_github_token: true` removes it) / unregister (files kept, token deleted) |
| GET | `/sites/{site}/releases?refresh=1` · `/sites/{site}/health` | GitHub releases with installed/current flags · health now |
| POST | `/sites/{site}/jobs` | `{type, version, params}`; 🔒 for `adopt`, `restore`, `delete-release`, `cleanup-adopt` |
| GET / DELETE 🔒 | `/sites/{site}/backups` · `/sites/{site}/backups/{backup}` | |
| GET | `/jobs?site=&limit=` · `/jobs/{job}?offset=` | history · job with the log from byte `offset` |
| GET | `/settings` | runner state (`github_tokens`: number of sites with their own token), versions |
| GET | `/activity` | sign-ins and administrator actions |

GitHub tokens belong to the sites: each site has its own read-only token (or none for a public repository). Tokens are
write-only: answers only say whether one is saved and show a hint such as `github_pat_…a1b2`.

Job types: `deploy` (`version`, `params.backup`), `rollback` (`version` optional: the previous release),
`adopt` (`version`, `params.backup`), `backup` (`params.reason`), `restore` (`params.backup_id`, `params.safety_backup`),
`delete-release` (`version`), `cleanup-adopt`.

## Runner API (`127.0.0.1:8790/v1`, used by the dashboard and `python3 -m arsika_rollout_manager`)

Headers on every request:

```
X-Rollout-Timestamp: <unix seconds>
X-Rollout-Nonce: <16-64 lowercase hex characters, new for every request>
X-Rollout-Signature: hex HMAC-SHA256(secret, METHOD "\n" PATH_WITH_QUERY "\n" TIMESTAMP "\n" NONCE "\n" hex SHA-256(body))
```

| Method | Path |
|---|---|
| GET | `/v1/status`, `/v1/presets` |
| POST | `/v1/github/check` `{repo, asset_pattern, github_token?, site?}` |
| GET / POST | `/v1/sites` (`?health=0` skips the health checks; POST takes the optional `github_token` of the site) |
| GET / PUT / DELETE | `/v1/sites/{site}` (PUT: `github_token` or `remove_github_token`) |
| GET | `/v1/sites/{site}/releases`, `/v1/sites/{site}/health`, `/v1/sites/{site}/backups` |
| POST | `/v1/sites/{site}/jobs` `{type, version, params, actor}` (409 `busy` while a job of the site is queued or running) |
| DELETE | `/v1/sites/{site}/backups/{backup}` |
| GET | `/v1/jobs?site=&limit=`, `/v1/jobs/{id}?offset=` |
