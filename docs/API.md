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
| POST 🔒 | `/servers/{server}/update` | the runner of that server installs the runner of the dashboard's version and restarts (a job of `_runner`) |
| GET | `/servers/{server}/jobs/{job}?offset=` | a job of that runner itself, with its log |
| GET | `/presets?server=` | presets of that server's runner (its web server user) |
| POST | `/github/check` | `{repo, asset_pattern, source, github_token?, site? \| server?}` → can the runner read the releases? Uses the typed token, else the saved token of `site` |
| GET | `/sites` | `{sites: [… with server], servers: [{id, name, local, ok, error}]}` (all runners asked at once) |
| POST 🔒 | `/sites` | register on `server` (default `local`); `github_token` optional: the site's read-only token |
| GET / PUT 🔒 / DELETE 🔒 | `/sites/{site}` | details with `server` (`github_token_set`, `github_token_hint`; never the token) / settings (`github_token` replaces the token, `remove_github_token: true` removes it) / unregister (files kept, token deleted) |
| GET | `/sites/{site}/releases?refresh=1` · `/sites/{site}/health` | GitHub releases with installed/current flags · health now |
| POST | `/sites/{site}/jobs` | `{type, version, params}`; 🔒 for `adopt`, `restore`, `delete-release`, `cleanup-adopt`, `artisan` |
| GET | `/sites/{site}/jobs/{job}?offset=` | the job with its log from byte `offset` |
| POST 🔒 | `/sites/{site}/agent-file` | hosting site: a new agent file `{filename, content}` with a new one-time key (the button of the file opens `APP_URL`) |
| POST 🔒 | `/sites/{site}/agent/connect` | `{url}`: connect the uploaded agent file (directly, or through the dashboard when the runner cannot open `url`) |
| GET | `/sites/{site}/plan?version=` | hosting site: the database changes of a release (`kind` `sql` or `laravel`; `migrations`: name, content, `pending` or `changed`) |
| POST | `/sites/{site}/uploads` | `{name, size, version?}` → `{id, version, chunk_size}`: start uploading an archive (release source `upload`) |
| POST | `/sites/{site}/uploads/{id}?offset=` | body: the bytes of one piece (`Content-Type: application/octet-stream`, with `X-CSRF-Token`) → `{received}` |
| POST | `/sites/{site}/uploads/{id}/finish` | the upload job (unpack, clean, store) |
| DELETE 🔒 | `/sites/{site}/uploads/{version}` | an uploaded version that is not live and not the rollback version |
| POST 🔒 · PUT 🔒 | `/sites/{site}/env/read` · `/sites/{site}/env` | hosting site: the `.env` (`{exists, content, where, file, template?}`) · save `{content}` (to the hosting, or kept until it connects) |
| GET / DELETE 🔒 | `/sites/{site}/backups` · `/sites/{site}/backups/{backup}` | |
| GET | `/jobs?site=&limit=` · `/jobs/{job}?offset=` | history of a site · a job of this server's runner |
| GET | `/settings` | this server's runner (`github_tokens`: number of sites with their own token), versions |
| GET | `/activity` | sign-ins and administrator actions |

Site settings: `deploy_mode` (`files` \| `compose`), `source` (`asset` \| `source`), `compose_files`,
`compose_project`, `compose_build`, `backup_db` (`auto` \| `mysql` \| `postgres` \| `none`), `backup_db_service`,
`backup_volumes`, `health_local` (check through this server), `migrations_dir` (hosting), plus the settings of 1.1
(see [SITES.md](SITES.md)). A hosting site has `deploy_mode` `hosting`; its states add `unpaired` (no agent connected)
and `unreachable`. Job parameter `migrations`: `auto` (run on the hosting) or `manual` (marked as done; default for an
adoption).

Job types: `deploy` (`version`, `params.backup`), `rollback` (`version` optional: the previous release),
`adopt` (`version`, `params.backup`), `backup` (`params.reason`), `restore` (`params.backup_id`, `params.safety_backup`),
`delete-release` (`version`), `cleanup-adopt`, `artisan` (`params.command`: an artisan command line for a Laravel
site on a hosting), `upload` (started by `/uploads/{id}/finish`).

### For agents (no session, no CSRF token)

The agent of a hosting that the runner cannot open (another network, no public address) calls the dashboard instead.
A sign-in in front of the dashboard (e.g. Cloudflare Access) must let `/api/v1/agent/` through: every call is signed.

| Method | Path | |
|---|---|---|
| GET | `/agent/ping` | `{"data": {"app": …}}`: the agent's page checks that the hosting can reach the dashboard |
| POST | `/agent/{site}/poll` | raw body: the agent's answer to the runner's last request; headers `X-Rollout-Timestamp`, `X-Rollout-Nonce`, `X-Rollout-Signature`, `X-Rollout-Agent`, `X-Rollout-Agent-Url`, `X-Rollout-Result`, `X-Rollout-Result-Status`, `X-Rollout-Result-Type`, `X-Rollout-Wait`. Passed on to the runner of the site → `{"command": request or null}` or `{"stop": "direct"}` |

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
| POST | `/v1/runner/update` `{version, actor}`: install the runner of that release (`update_repo`); restarts when the job has ended (409 `busy` while any job is queued or running) |
| POST | `/v1/sites/{site}/agent-file` `{dashboard}`, `/v1/sites/{site}/agent/connect` `{url}` · GET `/v1/sites/{site}/plan?version=` |
| POST | `/v1/sites/{site}/uploads` `{name, size, version, actor}` → `{id, version, chunk_size}` |
| PUT | `/v1/sites/{site}/uploads/{id}?offset=` raw body (one piece, up to 8 MB) → `{received, size}` |
| POST · DELETE | `/v1/sites/{site}/uploads/{id}/finish` `{actor}` (→ the `upload` job) · `/v1/sites/{site}/uploads/{version}` |
| GET · PUT | `/v1/sites/{site}/env` · `{content}`: the `.env` of a hosting site (a copy kept by the runner until the agent connects) |
| POST | `/v1/sites/{site}/agent/poll?ts=&nonce=&sig=&agent=&url=&result=&status=&type=&wait=` raw body: a call of an agent that calls the dashboard (from the headers above). The agent signs hex HMAC-SHA256(site secret, or the one-time key while it connects, `POLL` \n site \n ts \n nonce \n agent version \n agent address \n answered nonce \n answer status \n answer content type \n hex SHA-256(body)); ±300 s, one-time nonce, 30 invalid calls in 10 minutes lock the site for a while. The runner holds the call up to `wait` seconds (`agent_poll_wait`, 20) while it has no request. A request is `{uri, ts, nonce, signature, type, body (base64)}`: signed exactly as a request sent to the agent's address |

## Hosting agent (`https://<site>/rollout-agent-….php` or `http://<local address>/…`, used by the runner)

`POST ?action=<name>[&…]`, signed like the runner API (`X-Rollout-Timestamp` ±300 s, `X-Rollout-Nonce`,
`X-Rollout-Signature` over `POST`, the path with the query, timestamp, nonce and the body's SHA-256) with the site's
secret; `hello` and `pair` also with the file's one-time key until it is used. Actions: `hello`, `pair`, `status`,
`upload` (raw body, `name`, `offset`), `finish-upload`, `extract` (in steps), `migrate`, `activate`, `delete-release`,
`backup` (in steps), `download`, `clear`, `restore-files`, `restore-db`, `forget`; from 1.5.0 `self-update` (`code`:
the agent of the runner's version), `env-read` / `env-write` (`file`, `content`), `laravel-prepare`, `laravel-finish`,
`laravel-migrations`, `artisan` (`version`, `command`); from 1.6.0 `fetch` (`url`, `timeout`: a GET from the hosting, the
health check of a site that only its own network can open). Paths starting with `@shared/` are in the agent's data
folder. A `GET` shows the page for the browser; `GET ?poll=run` runs one round of calls to the dashboard (an agent that
the runner cannot open: from its open page, a cron task or the round before), `GET ?poll=status` tells how it goes.
