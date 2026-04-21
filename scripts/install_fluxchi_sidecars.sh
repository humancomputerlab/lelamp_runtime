#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_ROOT="${RUNTIME_ROOT:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
SERVICE_USER="${SERVICE_USER:-$(id -un)}"
SERVICE_GROUP="${SERVICE_GROUP:-$(id -gn)}"
PYTHON_BIN="${PYTHON_BIN:-${RUNTIME_ROOT}/.venv/bin/python}"

DASHBOARD_SERVICE_NAME="${DASHBOARD_SERVICE_NAME:-lelamp-dashboard}"
LISTENER_SERVICE_NAME="${LISTENER_SERVICE_NAME:-lelamp-fluxchi-listener}"
DASHBOARD_ENV_FILE="${DASHBOARD_ENV_FILE:-/etc/default/${DASHBOARD_SERVICE_NAME}}"
LISTENER_ENV_FILE="${LISTENER_ENV_FILE:-/etc/default/${LISTENER_SERVICE_NAME}}"

LELAMP_DASHBOARD_HOST="${LELAMP_DASHBOARD_HOST:-0.0.0.0}"
LELAMP_DASHBOARD_PORT="${LELAMP_DASHBOARD_PORT:-8765}"
LELAMP_DASHBOARD_POLL_MS="${LELAMP_DASHBOARD_POLL_MS:-400}"
LELAMP_DASHBOARD_EXPOSE_TRANSCRIPTS="${LELAMP_DASHBOARD_EXPOSE_TRANSCRIPTS:-false}"

FLUXCHI_WS="${FLUXCHI_WS:-ws://127.0.0.1:18000/ws/harness}"
LELAMP_DASHBOARD="${LELAMP_DASHBOARD:-http://127.0.0.1:8765}"
FLUXCHI_PROFILE_PATH="${FLUXCHI_PROFILE_PATH:-${RUNTIME_ROOT}/lelamp/integrations/profiles/scene_a.yaml}"
FLUXCHI_LOG_LEVEL="${FLUXCHI_LOG_LEVEL:-INFO}"
FLUXCHI_DISABLE_VOICE_GATE="${FLUXCHI_DISABLE_VOICE_GATE:-1}"

log() {
  printf '\n==> %s\n' "$*"
}

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

require_file() {
  local path="$1"
  [[ -f "$path" ]] || die "Required file not found: $path"
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

require_cmd sudo
require_cmd systemctl
require_file "${PYTHON_BIN}"
require_file "${FLUXCHI_PROFILE_PATH}"
require_file "${RUNTIME_ROOT}/lelamp/dashboard/api.py"
require_file "${RUNTIME_ROOT}/lelamp/integrations/fluxchi_listener.py"

log "Writing ${DASHBOARD_ENV_FILE}"
sudo tee "${DASHBOARD_ENV_FILE}" >/dev/null <<EOF
LELAMP_DASHBOARD_HOST=${LELAMP_DASHBOARD_HOST}
LELAMP_DASHBOARD_PORT=${LELAMP_DASHBOARD_PORT}
LELAMP_DASHBOARD_POLL_MS=${LELAMP_DASHBOARD_POLL_MS}
LELAMP_DASHBOARD_EXPOSE_TRANSCRIPTS=${LELAMP_DASHBOARD_EXPOSE_TRANSCRIPTS}
EOF

log "Writing ${LISTENER_ENV_FILE}"
sudo tee "${LISTENER_ENV_FILE}" >/dev/null <<EOF
FLUXCHI_WS=${FLUXCHI_WS}
LELAMP_DASHBOARD=${LELAMP_DASHBOARD}
FLUXCHI_PROFILE_PATH=${FLUXCHI_PROFILE_PATH}
FLUXCHI_LOG_LEVEL=${FLUXCHI_LOG_LEVEL}
FLUXCHI_DISABLE_VOICE_GATE=${FLUXCHI_DISABLE_VOICE_GATE}
EOF

log "Installing ${DASHBOARD_SERVICE_NAME}.service"
sudo tee "/etc/systemd/system/${DASHBOARD_SERVICE_NAME}.service" >/dev/null <<EOF
[Unit]
Description=LeLamp Dashboard
After=network-online.target lelamp.service
Wants=network-online.target lelamp.service

[Service]
Type=simple
WorkingDirectory=${RUNTIME_ROOT}
EnvironmentFile=-${DASHBOARD_ENV_FILE}
ExecStart=${PYTHON_BIN} -m lelamp.dashboard.api
Restart=always
RestartSec=3
User=${SERVICE_USER}
Group=${SERVICE_GROUP}

[Install]
WantedBy=multi-user.target
EOF

log "Installing ${LISTENER_SERVICE_NAME}.service"
sudo tee "/etc/systemd/system/${LISTENER_SERVICE_NAME}.service" >/dev/null <<EOF
[Unit]
Description=LeLamp FluxChi Listener
After=network-online.target ${DASHBOARD_SERVICE_NAME}.service lelamp.service
Wants=network-online.target ${DASHBOARD_SERVICE_NAME}.service lelamp.service

[Service]
Type=simple
WorkingDirectory=${RUNTIME_ROOT}
EnvironmentFile=-${LISTENER_ENV_FILE}
ExecStart=/usr/bin/env bash -lc 'cd "${RUNTIME_ROOT}" && exec "${PYTHON_BIN}" -m lelamp.integrations.fluxchi_listener --ws "\${FLUXCHI_WS}" --profile "\${FLUXCHI_PROFILE_PATH}" --dashboard "\${LELAMP_DASHBOARD}" --log-level "\${FLUXCHI_LOG_LEVEL}" \${FLUXCHI_DISABLE_VOICE_GATE:+--no-voice-gate}'
Restart=always
RestartSec=3
User=${SERVICE_USER}
Group=${SERVICE_GROUP}

[Install]
WantedBy=multi-user.target
EOF

log "Reloading systemd and enabling services"
sudo systemctl daemon-reload
sudo systemctl enable --now "${DASHBOARD_SERVICE_NAME}.service"
sudo systemctl enable --now "${LISTENER_SERVICE_NAME}.service"

log "Done"
cat <<EOF
Installed:
  - ${DASHBOARD_SERVICE_NAME}.service
  - ${LISTENER_SERVICE_NAME}.service

Inspect:
  systemctl status ${DASHBOARD_SERVICE_NAME}.service --no-pager
  systemctl status ${LISTENER_SERVICE_NAME}.service --no-pager
  journalctl -u ${DASHBOARD_SERVICE_NAME}.service -n 50 --no-pager
  journalctl -u ${LISTENER_SERVICE_NAME}.service -n 50 --no-pager
EOF
