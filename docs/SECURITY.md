# Security

Whoever controls the dashboard can run code as root on the server (that is what deployments are). The design keeps that
power behind one well-protected door.

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
  changing site settings, including a site's GitHub token (10 wrong passwords lock it for 15 minutes).
- Every action is recorded in the audit log with the administrator and IP address.
- The page is `noindex` and must not be framed (CSP `frame-ancestors 'none'`, `X-Frame-Options: DENY`).

## Runner

- Listens on `127.0.0.1` only; a different address is refused at start.
- Every request carries a timestamp (±120 s), a random nonce (never accepted twice) and an HMAC-SHA256 signature over
  method, path, timestamp, nonce and body hash, with the 256-bit secret shared with the dashboard's `.env`.
- Settings, secret and GitHub tokens: `/etc/arsika-rollout-manager/runner.json`, root only (0600). Every site has its
  own token, so a leaked token exposes one repository only. Tokens are write-only from the dashboard (answers carry a
  hint, never the token, and the activity log never records them) and are only sent to `api.github.com` (never to the
  storage host the downloads redirect to). Removing a site deletes its token.
- Site folders must be inside `/www/wwwroot/`; paths in settings are relative and may not contain `..`.
- Archives: absolute paths, `..` and links leaving the release folder are refused; SHA-256 checked when GitHub provides it.
- Database passwords for backups go through a temporary 0600 option file, never the command line.

## Recommendations

- HTTPS only (Let's Encrypt in aaPanel) and, if possible, restrict the dashboard to your IP addresses.
- One fine-grained GitHub token per site, with read-only Contents access to that site's repository only.
- Keep copies of `.env` (dashboard) and `/etc/arsika-rollout-manager/runner.json` outside the server.
