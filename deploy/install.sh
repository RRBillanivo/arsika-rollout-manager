#!/usr/bin/env bash
# Installation of Arsika Rollout Manager on a Linux server (aaPanel, or a plain server/VM without a panel).
#
# The dashboard (PHP, this folder) is an ordinary PHP site. The work is done by the runner, a small Python service
# that runs as root:
#   /opt/arsika-rollout-manager/                 runner code (copied from runner/, root only)
#   /etc/arsika-rollout-manager/runner.json      settings + shared secret + GitHub token of each site (root, 0600)
#   /var/lib/arsika-rollout-manager/             runner database and job logs
#   /www/backup/arsika-rollout-manager/          site backups (aaPanel; other servers: /var/backups/arsika-rollout-manager)
#   systemd unit arsika-rollout-manager-runner   starts the runner at boot
#
# Run as root from the folder of the release (the one containing app/, public/, runner/):
#   bash deploy/install.sh               dashboard + runner on this server (first time, or an update after uploading a
#                                        new release); the runner listens on 127.0.0.1 only
#   bash deploy/install.sh --agent       runner only, controlled by a dashboard on another server: HTTPS on port 8790
#                                        with a self-signed certificate; prints the address, secret and key pin to
#                                        enter in that dashboard (Servers > Add server)
#   bash deploy/install.sh --db          change the dashboard database settings only
#   bash deploy/install.sh --admin-link  new one-time administrator setup link (e.g. forgotten password)
# Options (new installations get defaults that suit the server; an existing runner keeps its settings unless an
# option is given):
#   --site-root=/var/www,/srv     folders that may contain sites       (aaPanel: /www/wwwroot; else /var/www,/srv)
#   --backup-dir=DIR              where backups are kept
#   --web-user=USER               owner of the release files            (aaPanel: www; Debian/Ubuntu: www-data)
#   --port=8790                   port of the runner
#   --listen=ADDRESS              --agent only: address to listen on    (default 0.0.0.0, every network)
#   --allow-from=IP[,NETWORK]     --agent only: addresses allowed to connect, e.g. the dashboard server (default: any;
#                                 a firewall rule that only lets the dashboard in is recommended in any case)
# Unattended dashboard installation: set APP_URL, DB_DATABASE, DB_USERNAME, DB_PASSWORD, [DB_HOST], [DB_PORT].
set -euo pipefail
cd "$(dirname "$0")/.."
WEB_DIR="$(pwd)"

RUNNER_DIR="/opt/arsika-rollout-manager"
CONFIG="/etc/arsika-rollout-manager/runner.json"
TLS_DIR="/etc/arsika-rollout-manager/tls"
SERVICE="arsika-rollout-manager-runner"
DB_ONLY=0
ADMIN_LINK_ONLY=0
AGENT=0
OPT_SITE_ROOT="" OPT_BACKUP_DIR="" OPT_WEB_USER="" OPT_PORT="" OPT_LISTEN="" OPT_ALLOW_FROM=""
for arg in "$@"; do
    case "$arg" in
        --db)            DB_ONLY=1 ;;
        --admin-link)    ADMIN_LINK_ONLY=1 ;;
        --agent)         AGENT=1 ;;
        --site-root=*)   OPT_SITE_ROOT="${arg#*=}" ;;
        --backup-dir=*)  OPT_BACKUP_DIR="${arg#*=}" ;;
        --web-user=*)    OPT_WEB_USER="${arg#*=}" ;;
        --port=*)        OPT_PORT="${arg#*=}" ;;
        --listen=*)      OPT_LISTEN="${arg#*=}" ;;
        --allow-from=*)  OPT_ALLOW_FROM="${arg#*=}" ;;
        *) echo "Unknown option: $arg (see the top of this script)"; exit 1 ;;
    esac
done

info() { echo; echo "== $*"; }
fail() { echo; echo "!! $*" >&2; exit 1; }

CAN_ASK=0
if [ -r /dev/tty ] && { : </dev/tty; } 2>/dev/null; then CAN_ASK=1; fi

ask() {   # ask VAR "Question" [default] [1=secret]   (skipped when VAR is already set in the environment)
    local __var="$1" question="$2" default="${3:-}" secret="${4:-0}" answer=""
    if [ -n "${!__var:-}" ]; then return 0; fi
    [ "$CAN_ASK" = "1" ] || fail "Cannot ask questions in this terminal. Set the variable $__var (see the top of this script)."
    while :; do
        if [ "$secret" = "1" ]; then
            read -r -s -p "   $question: " answer </dev/tty; echo >/dev/tty
        else
            read -r -p "   $question${default:+ [$default]}: " answer </dev/tty
        fi
        answer="${answer:-$default}"
        [ -n "$answer" ] && break
        echo "   (required)" >/dev/tty
    done
    printf -v "$__var" '%s' "$answer"
}

confirm() {   # confirm "Question" Y|N  -> 0 = yes
    local answer=""
    [ "$CAN_ASK" = "1" ] || { [ "$2" = "Y" ]; return; }
    read -r -p "   $1 $( [ "$2" = "Y" ] && echo '[Y/n]' || echo '[y/N]' ): " answer </dev/tty
    answer="${answer:-$2}"
    [[ "$answer" =~ ^[Yy] ]]
}

find_php() {   # PHP command line (aaPanel: /www/server/php/<version>/bin/php)
    if command -v php >/dev/null 2>&1; then command -v php; return; fi
    ls -1d /www/server/php/*/bin/php 2>/dev/null | sort -V | tail -n1
}

find_python() {   # Python 3.6+ for the runner: the system's, or aaPanel's own
    local candidate
    for candidate in "$(command -v python3 || true)" /usr/bin/python3 /www/server/panel/pyenv/bin/python3; do
        if [ -n "$candidate" ] && [ -x "$candidate" ] && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 6) else 1)' 2>/dev/null; then
            echo "$candidate"; return
        fi
    done
}

find_web_user() {
    if [ -n "${WEB_USER:-}" ]; then echo "$WEB_USER"; return; fi
    for user in www www-data nginx apache; do
        if id -u "$user" >/dev/null 2>&1; then echo "$user"; return; fi
    done
}

secure_env() {   # .env readable by root and the web server user only
    local user
    user="$(find_web_user || true)"
    if [ -n "$user" ]; then
        chown root:"$user" .env
        chmod 640 .env
    fi
}

configure_database() {
    while :; do
        ask APP_URL "Dashboard address" "https://$(basename "$WEB_DIR")"
        if [ "$PLATFORM" = "aapanel" ]; then echo "   Database created in aaPanel > Databases:"; else echo "   MySQL/MariaDB database and user for the dashboard (create them first):"; fi
        ask DB_DATABASE "Database name"
        ask DB_USERNAME "Database user" "$DB_DATABASE"
        ask DB_PASSWORD "Database password (hidden)" "" 1
        printf 'APP_ENV=production\nAPP_DEBUG=false\nAPP_URL=%s\nDB_CONNECTION=mysql\nDB_HOST=%s\nDB_PORT=%s\nDB_DATABASE=%s\nDB_USERNAME=%s\nDB_PASSWORD=%s\n' \
            "${APP_URL%/}" "${DB_HOST:-localhost}" "${DB_PORT:-3306}" "$DB_DATABASE" "$DB_USERNAME" "$DB_PASSWORD" | "$PHP" bin/console env:set
        "$PHP" bin/console key:generate
        secure_env
        if "$PHP" bin/console db:check; then
            return 0
        fi
        echo "   The database settings look wrong (see the message above)."
        if confirm "Enter the database settings again?" Y; then
            unset DB_DATABASE DB_USERNAME DB_PASSWORD
            continue
        fi
        echo "   Continuing. Fix it later with: bash deploy/install.sh --db"
        return 0
    done
}

admin_link() {   # admin_link [--if-no-admin]
    local user
    info "One-time administrator setup link"
    "$PHP" bin/console install:token "$@"
    user="$(find_web_user || true)"
    if [ -n "$user" ] && [ -d public/install ]; then
        chown -R "$user":"$user" public/install      # the page must be able to delete itself
    fi
}

install_runner_code() {
    info "Installing the runner in $RUNNER_DIR"
    mkdir -p "$RUNNER_DIR"
    rm -rf "$RUNNER_DIR/arsika_rollout_manager.new" "$RUNNER_DIR/presets.new"
    cp -r runner/arsika_rollout_manager "$RUNNER_DIR/arsika_rollout_manager.new"
    cp -r runner/presets "$RUNNER_DIR/presets.new"
    rm -rf "$RUNNER_DIR/arsika_rollout_manager" "$RUNNER_DIR/presets"
    mv "$RUNNER_DIR/arsika_rollout_manager.new" "$RUNNER_DIR/arsika_rollout_manager"
    mv "$RUNNER_DIR/presets.new" "$RUNNER_DIR/presets"
    find "$RUNNER_DIR" -name '__pycache__' -prune -exec rm -rf {} +
    chown -R root:root "$RUNNER_DIR"
    chmod -R u=rwX,go=rX "$RUNNER_DIR"
    chmod 755 "$RUNNER_DIR"/presets/*.sh
}

make_certificate() {   # self-signed certificate of the HTTPS runner (an existing one is kept, so its key pin stays)
    mkdir -p "$TLS_DIR"
    chmod 700 "$TLS_DIR"
    if [ -s "$TLS_DIR/cert.pem" ] && [ -s "$TLS_DIR/key.pem" ]; then
        echo "   keeping the certificate in $TLS_DIR"
        return
    fi
    command -v openssl >/dev/null 2>&1 || fail "openssl is needed to create the certificate (Debian/Ubuntu: apt install openssl)."
    local subject="/CN=arsika-rollout-manager $(hostname -s 2>/dev/null || echo runner)"
    if ! openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 3650 -subj "$subject" \
            -keyout "$TLS_DIR/key.pem" -out "$TLS_DIR/cert.pem" >/dev/null 2>&1; then
        openssl req -x509 -newkey rsa:3072 -nodes -days 3650 -subj "$subject" \
            -keyout "$TLS_DIR/key.pem" -out "$TLS_DIR/cert.pem" >/dev/null 2>&1 || fail "openssl could not create the certificate."
    fi
    chmod 600 "$TLS_DIR/key.pem"
    chmod 644 "$TLS_DIR/cert.pem"
    echo "   new certificate in $TLS_DIR (valid 10 years)"
}

configure_runner() {   # runner.json: defaults for a new installation; the options given are applied to an existing one
    local options=()
    if [ ! -f "$CONFIG" ]; then
        options+=("--site-root=${OPT_SITE_ROOT:-$DEFAULT_SITE_ROOT}" "--backup-dir=${OPT_BACKUP_DIR:-$DEFAULT_BACKUP_DIR}" "--platform=$PLATFORM")
        local user="${OPT_WEB_USER:-$(find_web_user || true)}"
        [ -n "$user" ] && options+=("--web-user=$user")
    else
        [ -n "$OPT_SITE_ROOT" ] && options+=("--site-root=$OPT_SITE_ROOT")
        [ -n "$OPT_BACKUP_DIR" ] && options+=("--backup-dir=$OPT_BACKUP_DIR")
        [ -n "$OPT_WEB_USER" ] && options+=("--web-user=$OPT_WEB_USER")
        options+=("--platform=$PLATFORM")
    fi
    [ -n "$OPT_PORT" ] && options+=("--port=$OPT_PORT")
    if [ "$AGENT" = "1" ]; then
        options+=("--listen=${OPT_LISTEN:-0.0.0.0}" "--tls-cert=$TLS_DIR/cert.pem" "--tls-key=$TLS_DIR/key.pem")
        [ -n "$OPT_ALLOW_FROM" ] && options+=("--allow-from=$OPT_ALLOW_FROM")
    fi
    (cd "$RUNNER_DIR" && "$PYTHON" -m arsika_rollout_manager init --config="$CONFIG" "${options[@]}")
    chmod 700 "$(dirname "$CONFIG")"
    chmod 600 "$CONFIG"
    SECRET="$(cd "$RUNNER_DIR" && "$PYTHON" -m arsika_rollout_manager secret --config="$CONFIG")"
    PORT="$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("port", 8790))' "$CONFIG")"
}

install_service() {
    info "Installing the systemd service $SERVICE (user root)"
    cat > "/etc/systemd/system/$SERVICE.service" <<EOF
[Unit]
Description=Arsika Rollout Manager runner (deploys, rollbacks and backups of the registered web sites)
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$RUNNER_DIR
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=$PYTHON -m arsika_rollout_manager serve --config=$CONFIG
Restart=always
RestartSec=3
# No PrivateTmp: MySQL in aaPanel listens on /tmp/mysql.sock, which backups (mysqldump) and build hooks must reach.

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable "$SERVICE" >/dev/null 2>&1
    systemctl restart "$SERVICE"
    for i in $(seq 1 30); do
        if (cd "$RUNNER_DIR" && "$PYTHON" -m arsika_rollout_manager status --config="$CONFIG" >/dev/null 2>&1); then
            echo "   runner is running"
            return
        fi
        [ "$i" = "30" ] && fail "The runner does not respond. Check: systemctl status $SERVICE  /  journalctl -u $SERVICE -n 50"
        sleep 1
    done
}

# ---------- 0) checks ----------
[ "$(uname -s)" = "Linux" ] || fail "This script is for Linux servers."
[ "$(id -u)" = "0" ] || fail "Run as root (type: sudo -i, then repeat)."
for f in runner/arsika_rollout_manager/__main__.py runner/presets/anemia-web.sh; do
    [ -f "$f" ] || fail "Missing $f. Run this from the folder of the release (now: $WEB_DIR) and make sure the upload is complete."
done
PYTHON="$(find_python || true)"
[ -n "$PYTHON" ] || fail "Python 3.6 or newer not found. Install it (Debian/Ubuntu: apt install python3; RHEL/Rocky: dnf install python3)."
command -v systemctl >/dev/null 2>&1 || fail "systemd is required for the runner service."
if [ -d /www/server/panel ]; then
    PLATFORM="aapanel"; DEFAULT_SITE_ROOT="/www/wwwroot"; DEFAULT_BACKUP_DIR="/www/backup/arsika-rollout-manager"
else
    PLATFORM="linux"; DEFAULT_SITE_ROOT="/var/www,/srv"; DEFAULT_BACKUP_DIR="/var/backups/arsika-rollout-manager"
fi

# ---------- agent: runner only, for a dashboard on another server ----------
if [ "$AGENT" = "1" ]; then
    if [ -f .env ] && grep -q '^RUNNER_SECRET=' .env; then
        fail "This folder holds a dashboard (.env with RUNNER_SECRET). --agent is for a server without its own dashboard; run it from an extracted release folder on that server."
    fi
    echo "== Agent (runner only) : $(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-Linux}") · $PLATFORM · Python $("$PYTHON" -c 'import platform; print(platform.python_version())')"
    install_runner_code
    info "HTTPS certificate"
    make_certificate
    configure_runner
    install_service
    if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
        echo "   Docker Compose: $(docker compose version --short 2>/dev/null || echo found)"
    else
        echo "   Docker Compose v2 not found: needed only for sites with deploy mode \"Docker Compose\"."
    fi
    cat <<EOF

======================================================================
DONE. The runner of this server listens on HTTPS port $PORT.
1) Firewall: let ONLY the dashboard server in, for example
     ufw:        ufw allow from <DASHBOARD-IP> to any port $PORT proto tcp
     firewalld:  firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="<DASHBOARD-IP>" port port="$PORT" protocol="tcp" accept' && firewall-cmd --reload
     aaPanel:    Security > Firewall > add a port rule for $PORT limited to the dashboard's IP
   Outside the local network: use a VPN (WireGuard, Tailscale) or allow only the dashboard's public IP.
2) In the dashboard: Servers > Add server, with these values:
EOF
    (cd "$RUNNER_DIR" && "$PYTHON" -m arsika_rollout_manager connection-info --config="$CONFIG" | sed 's/^/   /')
    cat <<EOF
   Show them again later: cd $RUNNER_DIR && sudo $PYTHON -m arsika_rollout_manager connection-info
   IMPORTANT: keep a backup of $CONFIG and $TLS_DIR somewhere safe.
======================================================================
EOF
    exit 0
fi

# ---------- dashboard + runner ----------
for f in bootstrap/app.php bin/console; do
    [ -f "$f" ] || fail "Missing $f. Run this from the dashboard's site folder (now: $WEB_DIR) and make sure the upload is complete."
done
PHP="$(find_php || true)"
[ -n "$PHP" ] || fail "PHP command line not found (aaPanel: App Store > PHP; Debian/Ubuntu: apt install php-cli php-fpm php-mysql php-curl php-mbstring)."

if [ "$ADMIN_LINK_ONLY" = "1" ]; then
    [ -f .env ] || fail ".env does not exist yet. Run first: bash deploy/install.sh"
    admin_link
    exit 0
fi
if [ "$DB_ONLY" = "1" ]; then
    info "Dashboard database"
    configure_database
    exit 0
fi
[ -z "$OPT_LISTEN$OPT_ALLOW_FROM" ] || fail "--listen and --allow-from are for --agent: the runner of the dashboard's own server listens on 127.0.0.1 only."
echo "== Dashboard folder : $WEB_DIR"
echo "== Server           : $(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-Linux}") · $PLATFORM · PHP $("$PHP" -r 'echo PHP_VERSION;') · Python $("$PYTHON" -c 'import platform; print(platform.python_version())')"

# ---------- 1) runner code, 2) settings (an existing configuration is kept), 3) service ----------
install_runner_code
configure_runner
install_service

# ---------- 4) dashboard .env ----------
if [ -f .env ] && grep -q '^DB_CONNECTION=mysql' .env && grep -q '^DB_DATABASE=.' .env; then
    info "The dashboard database is already configured in .env"
    if confirm "Change the database settings?" N; then configure_database; else "$PHP" bin/console key:generate; "$PHP" bin/console db:check || true; fi
else
    info "Dashboard database (MySQL/MariaDB)"
    configure_database
fi
printf 'RUNNER_URL=http://127.0.0.1:%s\nRUNNER_SECRET=%s\n' "$PORT" "$SECRET" | "$PHP" bin/console env:set >/dev/null
secure_env
WEB_USER="$(find_web_user || true)"
if [ -n "$WEB_USER" ]; then
    chown -R "$WEB_USER":"$WEB_USER" storage
fi
"$PHP" bin/console runner:check || fail "The dashboard cannot reach the runner (see the message above)."

# ---------- 5) administrator ----------
admin_link --if-no-admin
echo "   Forgot the administrator password later? Run: bash deploy/install.sh --admin-link"

if [ "$PLATFORM" = "aapanel" ]; then
    STEPS="  1) aaPanel > Website > this site > Site directory: Running directory = /public
  2) aaPanel > Website > this site > URL rewrite: paste deploy/nginx/aapanel.conf"
else
    STEPS="  1) Web server: serve $WEB_DIR/public with PHP-FPM, HTTPS only; nginx example in
     deploy/nginx/server.conf (set server_name, the certificate and the PHP-FPM socket)
  2) The folder must belong to root, storage/ and .env readable by ${WEB_USER:-the web server user} (done)"
fi
cat <<EOF

======================================================================
DONE. Arsika Rollout Manager is installed.
Next steps:
$STEPS
  3) Open the administrator setup link above (once), choose the password, sign in as "admin"
  4) Sites > Add site, with the site's own read-only GitHub token (private repositories), then "Adopt"
     the existing installation of that site
  5) Other servers: run "bash deploy/install.sh --agent" there, then Servers > Add server
  IMPORTANT: keep a backup of .env and $CONFIG somewhere safe.
======================================================================
EOF
