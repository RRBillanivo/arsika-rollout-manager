#!/usr/bin/env bash
# Preset "rollout-manager": Arsika Rollout Manager updating itself. The dashboard is registered as a site of the runner
# on its own server; a new version is installed like any other release, and the steps that belong to Arsika Rollout
# Manager are done by deploy/install.sh of the release being installed (so every version brings its own steps):
#   build     (new release folder, before the switch)  install.sh --self-update=build: PHP syntax, the dashboard
#             database (new tables are added), the new runner code with this server's Python and current settings
#   activate  (after the switch)  install.sh --self-update=activate: installs that release's runner code and systemd
#             unit; the runner restarts with it when the job has ended (also when an automatic rollback ran this step
#             for the previous release, which puts the previous runner code back)
# Variables from the runner: ROLLOUT_RELEASE_DIR, ROLLOUT_VERSION, ROLLOUT_RESTART_FILE, ROLLOUT_PYTHON ...
set -euo pipefail
phase="${1:-${ROLLOUT_PHASE:-}}"

case "$phase" in
    build|activate)
        script="${ROLLOUT_RELEASE_DIR:?}/deploy/install.sh"
        if ! grep -q -- '--self-update' "$script" 2>/dev/null; then
            echo "Release ${ROLLOUT_VERSION:-?} cannot update itself: Arsika Rollout Manager 1.3.0 or newer is needed." >&2
            echo "Install it by hand (unzip + bash deploy/install.sh), see docs/INSTALL.md." >&2
            exit 1
        fi
        exec bash "$script" --self-update="$phase"
        ;;
    adopt-revert)
        echo "nothing to put back: the adopted version is the running one, so its runner code was already installed"
        ;;
    *)
        echo "unknown phase: $phase (build, activate or adopt-revert)" >&2
        exit 2
        ;;
esac
