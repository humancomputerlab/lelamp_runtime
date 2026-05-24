#!/usr/bin/env bash
#
# Install auto-discovery wrapper for lelamp-fluxchi-listener.service.
#
# What this does:
#   1) Copies find_fluxchi_mac.py + fluxchi_listener_wrap.sh to
#      /usr/local/lib/lelamp/ (owned by root, executable).
#   2) Drops a systemd override at
#      /etc/systemd/system/lelamp-fluxchi-listener.service.d/
#      fluxchi-ws.conf that replaces ExecStart with the wrapper.
#   3) daemon-reload + restart the listener service.
#
# After install:
#   * Listener auto-discovers the FluxChi Mac backend on the local
#     network on every start (cached IP fast-path; falls back to
#     a /16 scan ~3 min).
#   * Cache is stored at $HOME/.cache/fluxchi/mac_ip.
#   * Service auto-restarts on crash via systemd Restart=always.
#
# Prereq:
#   * lelamp-fluxchi-listener.service already installed (run
#     install_fluxchi_sidecars.sh first).
#   * Mac IP not pinned in /etc/default/lelamp-fluxchi-listener
#     via FLUXCHI_WS (this script overrides ExecStart, so the var
#     is no longer consumed).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="${SCRIPT_DIR}/auto_discover"
DEST_DIR="/usr/local/lib/lelamp"
SERVICE_NAME="lelamp-fluxchi-listener"
DROPIN_DIR="/etc/systemd/system/${SERVICE_NAME}.service.d"

require_sudo() {
  if [ "$(id -u)" -ne 0 ]; then
    exec sudo --preserve-env=PATH "$0" "$@"
  fi
}

log() {
  printf '\n==> %s\n' "$*"
}

main() {
  require_sudo "$@"

  if [ ! -d "$SOURCE_DIR" ]; then
    echo "ERROR: $SOURCE_DIR not found. Are you running from a fresh checkout?" >&2
    exit 1
  fi

  log "Installing scripts to $DEST_DIR"
  install -d -m 0755 "$DEST_DIR"
  install -m 0755 "$SOURCE_DIR/find_fluxchi_mac.py"      "$DEST_DIR/"
  install -m 0755 "$SOURCE_DIR/fluxchi_listener_wrap.sh" "$DEST_DIR/"

  log "Writing systemd drop-in to $DROPIN_DIR/fluxchi-ws.conf"
  install -d -m 0755 "$DROPIN_DIR"
  cat > "$DROPIN_DIR/fluxchi-ws.conf" << 'EOF'
[Service]
# Override the hardcoded FLUXCHI_WS path; let the wrapper discover the
# Mac backend at runtime via LAN scan + cached IP fast-path.
ExecStart=
ExecStart=/usr/local/lib/lelamp/fluxchi_listener_wrap.sh
EOF

  log "Reloading systemd and restarting $SERVICE_NAME"
  systemctl daemon-reload
  systemctl restart "${SERVICE_NAME}.service"

  log "Done. Tail the log to verify discovery:"
  printf '  sudo journalctl -u %s -f\n' "$SERVICE_NAME"
}

main "$@"
