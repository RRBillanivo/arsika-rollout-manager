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
| POST | `/github/check` | `{repo, asset_pattern}` → can the runner read the releases? |
| GET / POST 🔒 | `/sites` | list with health and versions / register |
| GET / PUT 🔒 / DELETE 🔒 | `/sites/{site}` | details / settings / unregister (files kept) |
| GET | `/sites/{site}/releases?refresh=1` · `/sites/{site}/health` | GitHub releases with installed/current flags · health now |
| POST | `/sites/{site}/jobs` | `{type, version, params}`; 🔒 for `adopt`, `restore`, `delete-release`, `cleanup-adopt` |
| GET / DELETE 🔒 | `/sites/{site}/backups` · `/sites/{site}/backups/{backup}` | |
| GET | `/jobs?site=&limit=` · `/jobs/{job}?offset=` | history · job with the log from byte `offset` |
| GET / PUT 🔒 | `/settings` | runner state / `{github_token}` (empty removes it) |
| GET | `/activity` | sign-ins and administrator actions |

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
| GET | `/v1/status`, `/v1/presets`, `/v1/settings` |
| PUT | `/v1/settings` `{github_token}` |
| POST | `/v1/github/check` |
| GET / POST | `/v1/sites` (`?health=0` skips the health checks) |
| GET / PUT / DELETE | `/v1/sites/{site}` |
| GET | `/v1/sites/{site}/releases`, `/v1/sites/{site}/health`, `/v1/sites/{site}/backups` |
| POST | `/v1/sites/{site}/jobs` `{type, version, params, actor}` (409 `busy` while a job of the site is queued or running) |
| DELETE | `/v1/sites/{site}/backups/{backup}` |
| GET | `/v1/jobs?site=&limit=`, `/v1/jobs/{id}?offset=` |
