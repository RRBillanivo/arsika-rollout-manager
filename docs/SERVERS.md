# Other servers

One dashboard can manage sites on several servers. Each other server runs only the runner (Python, no PHP, no
database) and is added to the dashboard with its address, its secret and the key pin of its certificate.

```
Dashboard server (VM1)                        Other server (VM2, VM3 ...)
  dashboard (PHP) ── runner of VM1               runner (systemd, root)
        │                (127.0.0.1, HTTP)          ▲
        └──────────── HTTPS :8790 ──────────────────┘   pinned certificate · signed requests · allowlist/firewall
```

- The runner of another server answers on **HTTPS only** with a self-signed certificate created by the installer.
  The dashboard does not trust it through a certificate authority but **pins its public key** (`sha256//…`), so it
  accepts exactly that server, whatever address it is reached at (LAN, VPN, public IP).
- Every request is signed with that server's own secret (HMAC-SHA256, timestamp and one-time nonce).
- The server's secret is stored in the dashboard database **encrypted with `APP_KEY`** and never shown again.
- Optionally the runner only accepts connections from listed addresses (`allow_from`); loopback is always allowed.

## 1. Install the runner on the other server

Requirements: Linux with systemd and root access, Python 3.6+, `openssl`; Docker with Compose v2 for Docker Compose
sites. aaPanel is optional.

```sh
# copy arsika-rollout-manager-vX.Y.Z.zip to the server, then
mkdir -p /root/rollout && cd /root/rollout && unzip /path/to/arsika-rollout-manager-vX.Y.Z.zip
sudo bash deploy/install.sh --agent
# optional: --allow-from=192.168.1.10  --site-root=/srv,/opt/apps  --port=8790  --listen=10.8.0.5
```

The installer copies the runner to `/opt/arsika-rollout-manager`, creates the certificate in
`/etc/arsika-rollout-manager/tls/` (kept on later runs, so the pin stays the same), writes
`/etc/arsika-rollout-manager/runner.json` (listen `0.0.0.0:8790`, HTTPS), starts the systemd service and prints:

```
Add this server in the dashboard (Servers > Add server):
  Address : https://192.168.1.20:8790
  Secret  : 3f9c…(64 hex characters)
  Key pin : sha256//pHfaCR/oXbEVj/irBfuaaNqDm+YFG9Lw1cdBN7d3bnM=
```

Show it again any time: `cd /opt/arsika-rollout-manager && sudo python3 -m arsika_rollout_manager connection-info`.
The extracted release folder is not needed afterwards.

## 2. Firewall

Open the runner port **only for the dashboard server**:

```sh
ufw allow from 192.168.1.10 to any port 8790 proto tcp                    # Ubuntu/Debian with ufw
firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="192.168.1.10" port port="8790" protocol="tcp" accept'
firewall-cmd --reload                                                      # RHEL/Rocky with firewalld
```

aaPanel: **Security → Firewall**, a port rule for 8790 limited to the dashboard's IP. Cloud providers: the security
group of the VM.

Across the internet, prefer a VPN (WireGuard, Tailscale, ZeroTier) and use the VPN address; otherwise allow only the
public IP of the dashboard server. `--allow-from` (`allow_from` in `runner.json`) is a second barrier inside the
runner: other addresses are disconnected before TLS starts.

## 3. Add the server in the dashboard

**Servers → Add server**: a name (e.g. `VM2`), the address, the secret and the key pin. **Test connection** shows the
runner version, host name and number of sites; saving needs your password. Sites already registered on that runner
appear on the overview.

From then on **Add site** offers a choice of server; the site's folder must be inside that server's site folders
(shown under the field). Jobs, logs, backups and GitHub tokens of a site stay on its server.

## Changing or removing

- **Edit**: name, address, pin; leave the secret empty to keep it. The connection is tested before saving.
- **Remove**: the dashboard forgets the server and its sites; nothing changes on the server (sites, releases, backups
  and the runner stay). Add it again later to manage it again.
- **New certificate** (e.g. after a leak): on that server `rm -r /etc/arsika-rollout-manager/tls`, run
  `bash deploy/install.sh --agent` again, then enter the new pin in the dashboard (**Edit**).
- **New secret**: write a new value (`openssl rand -hex 32`) into `"secret"` of `/etc/arsika-rollout-manager/runner.json`,
  `systemctl restart arsika-rollout-manager-runner`, then enter it in the dashboard (**Edit**).
- **Updating the runner**: when the runner of a server is older than the dashboard, its card shows **Update runner
  to X.Y.Z**. The runner downloads that release of Arsika Rollout Manager from GitHub (`update_repo` in `runner.json`,
  the official repository by default; its SHA-256 is checked), runs its `deploy/install.sh --agent --self-update`
  (runner code and systemd unit; settings, certificate, key pin and sites stay) and restarts when the job has ended.
  On the server itself: `cd /opt/arsika-rollout-manager && sudo python3 -m arsika_rollout_manager update-runner X.Y.Z`,
  or unzip the release there and run `bash deploy/install.sh --agent` again.

## A dashboard without its own runner

A dashboard that only controls other servers may leave `RUNNER_SECRET` empty in `.env`; "This server" then shows
"not configured" and is not offered for new sites. Installing it with `deploy/install.sh` (which also installs the
local runner) is simpler and does no harm.

## Troubleshooting

| Message | Cause and fix |
|---|---|
| The server … does not answer | Wrong address or port, firewall, runner stopped (`systemctl status arsika-rollout-manager-runner` on that server), or the dashboard's IP is not in `allow_from` (the runner's journal says "connection … refused") |
| The certificate … does not match the key pin | The pin was mistyped, or the certificate was recreated: copy the pin again from `connection-info` |
| The certificate … cannot be verified | The pin field is empty while the runner uses its self-signed certificate: enter the pin |
| The server … refused the request | Wrong secret, or the clocks of the two servers differ by more than 2 minutes (enable NTP: `timedatectl set-ntp true`) |
| Sites with these ids already exist | Site ids are unique across the dashboard; a server whose sites clash with existing ids cannot be added |
