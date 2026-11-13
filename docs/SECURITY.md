# Security

Whoever controls the dashboard can run code as root on every server it manages (that is what deployments are). The
design keeps that power behind one well-protected door, and keeps each runner closed to everything but its dashboard.

## Reporting a vulnerability

Please report security problems privately through GitHub (**Security → Report a vulnerability** in this repository),
not in a public issue.

## Dashboard

- One administrator account (`admin`), created on a one-time setup page (`php bin/console install:token`, token stored as
  SHA-256, valid 24 hours, page deletes itself). No password in any file.
- Passwords: at least 12 characters with letters and digits, Argon2id with a server-side pepper (`APP_KEY`).
- Sign-in lockout: 5 failures per account or 20 per IP within 15 minutes; unknown accounts take the same time.
- Sessions: HttpOnly, `SameSite=Strict`, Secure on HTTPS, 8 hours of inactivity, no "remember me"; the session id
  rotates on sign-in and every password change signs out other devices.
- CSRF token on every state-changing request.
- **The password is asked again** for adoption, restore, deleting a release, `_pre_rollout`, a backup or a site,
  changing site settings (including a site's GitHub token), and adding, changing or removing a server (10 wrong
  passwords lock it for 15 minutes).
- The secrets of other servers are stored encrypted (AES-256-GCM with a key derived from `APP_KEY`) and never sent
  back to the browser; the activity log never records them.
- Every action is recorded in the audit log with the administrator and IP address.
- The page is `noindex` and must not be framed (CSP `frame-ancestors 'none'`, `X-Frame-Options: DENY`).

## Runner

- The runner of the dashboard's server listens on a loopback address only. A runner on any other address refuses to
  start without TLS: it then answers **HTTPS only** (TLS 1.2+) with its own certificate, and the dashboard checks the
  certificate's **public key pin** instead of a certificate authority, so a different server or a man in the middle is
  refused even on the right address.
- `allow_from` (IP addresses / networks) disconnects every other address before TLS starts; loopback is always allowed.
  A firewall rule that only lets the dashboard reach the port is recommended in any case.
- Every request carries a timestamp (±120 s), a random nonce (never accepted twice) and an HMAC-SHA256 signature over
  method, path, timestamp, nonce and body hash, with the 256-bit secret of that runner.
- Settings, secret and GitHub tokens: `/etc/arsika-rollout-manager/runner.json`, root only (0600); the TLS key:
  `/etc/arsika-rollout-manager/tls/key.pem` (0600). Every site has its own token, so a leaked token exposes one
  repository only. Tokens are write-only from the dashboard (answers carry a hint, never the token) and are only sent
  to `api.github.com` (never to the storage host the downloads redirect to). Removing a site deletes its token.
- Site folders must be inside the runner's site folders; paths in settings are relative and may not contain `..`.
- Archives: absolute paths, `..` and links leaving the release folder are refused; SHA-256 checked when GitHub provides it.
- Database passwords for backups go through a temporary 0600 option file or the process environment, never the command
  line. Dumps of a database in a container run inside the container with its own credentials.
- Docker Compose: a bind mount into the release that does not exist stops a deployment (Docker would silently create an
  empty folder in its place).
- Hosting agents: a generated PHP file per site answers only requests signed with that site's secret (timestamp ±5
  minutes, one-time nonce, lockout after repeated invalid requests; a hosting whose clock is further off proves its time
  with the key and the runner signs with it, the window stays the same); the key written into the file works once, to
  connect; the agent works only inside its website and data folders, refuses unsafe archives, never runs shell
  commands, and keeps its secret outside the web root when the hosting allows it (inside it: a random folder name and
  an `.htaccess` that denies access). Backups are moved off the hosting. Laravel applications live outside the website
  folder. The runner may replace the agent with the agent of its own version (`self-update`, signed like every
  request); the key in the new file is marked as used. Connecting sends no secret: both sides derive it from that
  key. Plain `http://` agent addresses are accepted (e.g. a local IP address) and marked "No HTTPS": requests stay
  signed, but the release, the `.env` and backups travel unencrypted. An agent in another network (no address the
  runner can open) calls the dashboard instead: the public route `/api/v1/agent/` takes no session, only calls signed
  with the site's secret (or the file's one-time key while it connects), checked by the runner with a one-time nonce
  and a lockout; the runner's requests keep their own signature. See [HOSTING.md](HOSTING.md#security).
- Uploaded archives are unpacked into an empty folder on the runner's server: absolute paths, `..`, links and special
  files are refused or dropped, the size and the number of files are limited, and the local `.env` and similar files
  are removed before a version is stored. composer (Laravel without `vendor/`) runs with `--no-scripts --no-plugins`,
  so no code of the project or its packages runs on the server.
- artisan commands and the `.env` of hosting sites need the password; the activity log records the command name and
  that the `.env` was opened or saved, never its content. The `.env` kept by the runner before a hosting connects is a
  root-only file (0600) and is deleted once it is on the hosting.
- Self-update: the runner installs only releases of `update_repo` (the official repository unless changed in
  `runner.json`), checks the asset's SHA-256 when GitHub provides it, and is asked to do so only by a signed request;
  the dashboard asks for the password first.

## Recommendations

- HTTPS only for the dashboard (Let's Encrypt) and, if possible, restrict it to your IP addresses or a VPN.
- Other servers: reach them over a VPN, or open the runner port only for the dashboard's IP; keep `allow_from` set.
- One fine-grained GitHub token per site, with read-only Contents access to that site's repository only.
- Keep copies of `.env` (dashboard, contains `APP_KEY`), `/etc/arsika-rollout-manager/runner.json` and
  `/etc/arsika-rollout-manager/tls/` of every server outside the servers. Without `APP_KEY` the saved server secrets
  cannot be decrypted (add the servers again).
