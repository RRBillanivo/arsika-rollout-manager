#!/usr/bin/env bash
# Installation of Arsika Rollout Manager on a Linux server with aaPanel.
#
# The dashboard (PHP, this folder) is an ordinary aaPanel site. The work is done by the runner, a small Python service
# that runs as root and listens on 127.0.0.1 only:
#   /opt/arsika-rollout-manager/                 runner code (copied from runner/, root only)
#   /etc/arsika-rollout-manager/runner.json      settings + shared secret + GitHub token of each site (root, 0600)
#   /var/lib/arsika-rollout-manager/             runner database and job logs
#   /www/backup/arsika-rollout-manager/          site backups
#   systemd unit arsika-rollout-manager-runner   starts the runner at boot
# The dashboard's .env receives the database settings, APP_KEY and the same secret (RUNNER_SECRET).
#
# Run as root from the dashboard's site folder (the one containing app/, public/, runner/):
#   bash deploy/install.sh               full installation (first time) or update after uploading a new release
#   bash deploy/install.sh --db          change the dashboard database settings only
#   bash deploy/install.sh --admin-link  new one-time administrator setup link (e.g. forgotten password)
# Unattended: set APP_URL, DB_DATABASE, DB_USERNAME, DB_PASSWORD, [DB_HOST], [DB_PORT] in the environment.
set -euo pipefail
cd "$(dirname "$0")/.."
WEB_DIR="$(pwd)"

RUNNER_DIR="/opt/arsika-rollout-manager"
CONFIG="/etc/arsika-rollout-manager/runner.json"
SERVICE="arsika-rollout-manager-runner"
SITE_ROOT="/www/wwwroot"
BACKUP_DIR="/www/backup/arsika-rollout-manager"
DB_ONLY=0
ADMIN_LINK_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --db)         DB_ONLY=1 ;;
        --admin-link) ADMIN_LINK_ONLY=1 ;;
        *) echo "Unknown option: $arg (choose from --db, --admin-link)"; exit 1 ;;
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
    for user in www www-data apache nginx; do
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
        echo "   Database created in aaPanel > Databases:"
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
        echo "   The database settings look wrong (see the message above). Check them in aaPanel > Databases."
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

# ---------- 0) checks ----------
[ "$(uname -s)" = "Linux" ] || fail "This script is for Linux servers."
[ "$(id -u)" = "0" ] || fail "Run as root (type: sudo -i, then repeat)."
for f in bootstrap/app.php bin/console runner/arsika_rollout_manager/__main__.py runner/presets/anemia-web.sh; do
    [ -f "$f" ] || fail "Missing $f. Run this from the dashboard's site folder (now: $WEB_DIR) and make sure the upload is complete."
done
PHP="$(find_php || true)"
[ -n "$PHP" ] || fail "PHP command line not found (aaPanel: App Store > PHP)."
PYTHON="$(find_python || true)"
[ -n "$PYTHON" ] || fail "Python 3.6 or newer not found. Install it (Ubuntu/Debian: apt install python3; CentOS: yum install python3)."
command -v systemctl >/dev/null 2>&1 || fail "systemd is required for the runner service."

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
echo "== Dashboard folder : $WEB_DIR"
echo "== Server           : $(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-Linux}") · PHP $("$PHP" -r 'echo PHP_VERSION;') · Python $("$PYTHON" -c 'import platform; print(platform.python_version())')"

# ---------- 1) runner code ----------
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

# ---------- 2) runner settings (an existing configuration is kept) ----------
cd "$RUNNER_DIR"
"$PYTHON" -m arsika_rollout_manager init --config="$CONFIG" --site-root="$SITE_ROOT" --backup-dir="$BACKUP_DIR"
chmod 700 "$(dirname "$CONFIG")"
chmod 600 "$CONFIG"
SECRET="$("$PYTHON" -m arsika_rollout_manager secret --config="$CONFIG")"
cd "$WEB_DIR"

# ---------- 3) systemd service ----------
info "Installing the systemd service $SERVICE (127.0.0.1:8790, user root)"
cat > "/etc/systemd/system/$SERVICE.service" <<EOF
[Unit]
Description=Arsika Rollout Manager runner (deploys, rollbacks and backups of the registered web sites)
After=network-online.target
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
    if "$PYTHON" -c "import urllib.request,urllib.error
try:
    urllib.request.urlopen('http://127.0.0.1:8790/v1/status', timeout=2)
except urllib.error.HTTPError:
    pass" 2>/dev/null; then
        echo "   runner is running"
        break
    fi
    [ "$i" = "30" ] && fail "The runner does not respond. Check: systemctl status $SERVICE  /  journalctl -u $SERVICE -n 50"
    sleep 1
done

# ---------- 4) dashboard .env ----------
if [ -f .env ] && grep -q '^DB_CONNECTION=mysql' .env && grep -q '^DB_DATABASE=.' .env; then
    info "The dashboard database is already configured in .env"
    if confirm "Change the database settings?" N; then configure_database; else "$PHP" bin/console key:generate; "$PHP" bin/console db:check || true; fi
else
    info "Dashboard database (MySQL)"
    configure_database
fi
printf 'RUNNER_URL=http://127.0.0.1:8790\nRUNNER_SECRET=%s\n' "$SECRET" | "$PHP" bin/console env:set >/dev/null
secure_env
WEB_USER="$(find_web_user || true)"
if [ -n "$WEB_USER" ]; then
    chown -R "$WEB_USER":"$WEB_USER" storage
fi
"$PHP" bin/console runner:check || fail "The dashboard cannot reach the runner (see the message above)."

# ---------- 5) administrator ----------
admin_link --if-no-admin
echo "   Forgot the administrator password later? Run: bash deploy/install.sh --admin-link"

cat <<EOF

======================================================================
DONE. Arsika Rollout Manager is installed.
Next steps:
  1) aaPanel > Website > this site > Site directory: Running directory = /public
  2) aaPanel > Website > this site > URL rewrite: paste deploy/nginx/aapanel.conf
  3) Open the administrator setup link above (once), choose the password, sign in as "admin"
  4) Sites > Add site, with the site's own read-only GitHub token (private repositories), then "Adopt"
     the existing installation of that site
  IMPORTANT: keep a backup of .env and $CONFIG somewhere safe.
======================================================================
EOF
