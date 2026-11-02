#!/usr/bin/env bash
# Preset hooks for Anemia Web 3.x (github.com/RRBillanivo/Anemia_Web); a site uses them with build/activate = preset:anemia-web.
#
#   build         in releases/<version>, before the switch:
#                   - Python 3.10 environment for the engine, shared by every release with the same engine/requirements.txt
#                     (shared/.rollout-cache/anemia-web/venv-<hash>; torch is downloaded only when the
#                     requirements change)
#                   - load the three models once (self-test)
#                   - php bin/console db:check (creates missing tables; schema changes must stay compatible with the previous release)
#   activate      after the switch: systemd unit anemia-engine pointing at current/ and shared/storage (the runner restarts it)
#   adopt-revert  after a failed adoption: put back the unit that existed before
#
# Variables from the runner: ROLLOUT_SITE_DIR ROLLOUT_RELEASE_DIR ROLLOUT_SHARED_DIR ROLLOUT_CURRENT_DIR
#                            ROLLOUT_CACHE_DIR ROLLOUT_WEB_USER ROLLOUT_VERSION
# The uv version and checksums are the ones of deploy/setup-server.sh in Anemia Web 3.1.0.
set -euo pipefail

PHASE="${1:-}"
CACHE="$ROLLOUT_CACHE_DIR/anemia-web"
UNIT=/etc/systemd/system/anemia-engine.service
SAVED_UNIT="$CACHE/anemia-engine.service.before-rollout"
UV_VERSION="0.12.24"
PYTHON_VERSION="3.10"
PORT=8767
MARK="# Managed by Arsika Rollout Manager (preset anemia-web)"

find_php() {
    if command -v php >/dev/null 2>&1; then command -v php; return; fi
    ls -1d /www/server/php/*/bin/php 2>/dev/null | sort -V | tail -n1
}

as_web_user() {   # run a command as the web server user (files it creates stay writable for PHP)
    if [ -n "${ROLLOUT_WEB_USER:-}" ] && id -u "$ROLLOUT_WEB_USER" >/dev/null 2>&1 && [ "$(id -u)" = "0" ]; then
        if command -v runuser >/dev/null 2>&1; then runuser -u "$ROLLOUT_WEB_USER" -- "$@"; else su -s /bin/sh "$ROLLOUT_WEB_USER" -c "$(printf '%q ' "$@")"; fi
    else
        "$@"
    fi
}

download() {
    if command -v curl >/dev/null 2>&1; then curl -fsSL --retry 3 -o "$2" "$1"; else wget -q -O "$2" "$1"; fi
}

build() {
    cd "$ROLLOUT_RELEASE_DIR"
    mkdir -p "$CACHE"
    case "$(uname -m)" in
        x86_64|amd64)  UV_ARCH="x86_64-unknown-linux-gnu";  UV_SHA="b4dfaef47d491a7296981f8374a4595f55dbf84e8937c8ecd2983574d8bb3da6" ;;
        aarch64|arm64) UV_ARCH="aarch64-unknown-linux-gnu"; UV_SHA="5231be65f496304623895dacdbf1de8504fec90303684bdf05805aa34414dd21" ;;
        *) echo "Architecture $(uname -m) is not supported (x86_64 or aarch64)."; exit 1 ;;
    esac

    key="$(sha256sum engine/requirements.txt | cut -c1-12)"
    venv="$CACHE/venv-$key"
    if [ -f "$venv/.complete" ]; then
        echo "Python environment venv-$key reused (requirements unchanged)"
    else
        uv="$CACHE/uv-$UV_VERSION/uv"
        if [ ! -x "$uv" ]; then
            echo "Downloading uv $UV_VERSION"
            mkdir -p "$CACHE/uv-$UV_VERSION"
            download "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$UV_ARCH.tar.gz" "$CACHE/uv.tar.gz"
            echo "$UV_SHA  $CACHE/uv.tar.gz" | sha256sum -c --quiet -
            tar -xzf "$CACHE/uv.tar.gz" -C "$CACHE/uv-$UV_VERSION" --strip-components=1
            rm -f "$CACHE/uv.tar.gz"
        fi
        rm -rf "$venv"
        export UV_PYTHON_INSTALL_DIR="$CACHE/python" UV_CACHE_DIR="$CACHE/uv-cache" UV_PYTHON_BIN_DIR="$CACHE/uv-bin" UV_NO_CONFIG=1
        echo "Creating the Python $PYTHON_VERSION environment venv-$key"
        "$uv" venv --seed --python "$PYTHON_VERSION" --python-preference only-managed "$venv"
        requirements="engine/requirements.txt"
        if [ "$UV_ARCH" = "aarch64-unknown-linux-gnu" ]; then
            sed 's/+cpu//' engine/requirements.txt > "$CACHE/requirements-arm.txt"
            requirements="$CACHE/requirements-arm.txt"
        fi
        echo "Installing torch (CPU), OpenCV, numpy - about 250 MB, a few minutes"
        "$venv/bin/python" -m pip install --no-cache-dir --disable-pip-version-check -r "$requirements"
        rm -rf "$UV_CACHE_DIR"
        touch "$venv/.complete"
    fi
    chmod -R a+rX "$CACHE"
    rm -rf engine/.venv engine/.python
    ln -s "$venv" engine/.venv

    echo "Loading the three models (segmentation, CNN, ANN)"
    (cd engine && "$venv/bin/python" -E -s -X utf8 -m anemia_engine check >/dev/null)
    echo "models OK"

    php="$(find_php)"
    [ -n "$php" ] || { echo "PHP command line not found"; exit 1; }
    as_web_user "$php" bin/console db:check
}

activate() {
    mkdir -p "$CACHE"
    desired="$MARK
[Unit]
Description=Anemia Web inference engine ($ROLLOUT_SITE_DIR/current)
After=network.target

[Service]
Type=simple
User=${ROLLOUT_WEB_USER:-www}
WorkingDirectory=$ROLLOUT_CURRENT_DIR/engine
ExecStart=$ROLLOUT_CURRENT_DIR/engine/.venv/bin/python -E -s -X utf8 -m anemia_engine serve --host 127.0.0.1 --port $PORT --storage-dir $ROLLOUT_SHARED_DIR/storage/app/examinations
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target"
    if [ -f "$UNIT" ] && [ ! -f "$SAVED_UNIT" ] && ! grep -qF "$MARK" "$UNIT"; then
        cp -p "$UNIT" "$SAVED_UNIT"
        echo "previous unit saved to $SAVED_UNIT"
    fi
    if [ ! -f "$UNIT" ] || [ "$(cat "$UNIT")" != "$desired" ]; then
        printf '%s\n' "$desired" > "$UNIT"
        systemctl daemon-reload
        systemctl enable anemia-engine >/dev/null 2>&1 || true
        echo "unit anemia-engine now runs from current/"
    else
        echo "unit anemia-engine unchanged"
    fi
}

adopt_revert() {
    if [ -f "$SAVED_UNIT" ]; then
        cp -p "$SAVED_UNIT" "$UNIT"
        systemctl daemon-reload
        echo "previous unit anemia-engine restored"
    fi
}

case "$PHASE" in
    build) build ;;
    activate) activate ;;
    adopt-revert) adopt_revert ;;
    *) echo "usage: anemia-web.sh build|activate|adopt-revert"; exit 2 ;;
esac
