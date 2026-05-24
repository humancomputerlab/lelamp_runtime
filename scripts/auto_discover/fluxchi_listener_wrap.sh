#!/bin/bash
# Wrapper：自动发现 Mac 后 exec listener。systemd Restart=always 保证恢复。

set -u
RUNTIME_DIR="/home/wujiajun/lelamp-dev/lelamp_runtime_canary_0fb2450"
PYTHON="${RUNTIME_DIR}/.venv/bin/python"
RESOLVER="/usr/local/lib/lelamp/find_fluxchi_mac.py"

: "${FLUXCHI_PROFILE_PATH:=${RUNTIME_DIR}/lelamp/integrations/profiles/scene_b.yaml}"
: "${LELAMP_DASHBOARD:=http://127.0.0.1:8765}"
: "${FLUXCHI_LOG_LEVEL:=INFO}"
: "${FLUXCHI_PORT:=8000}"

echo "[fluxchi-wrap] resolving Mac..."
MAC_IP="$(/usr/bin/python3 "$RESOLVER" 2>/tmp/fluxchi-resolve.log)"
if [ -z "$MAC_IP" ]; then
    echo "[fluxchi-wrap] not found; sleep 30s then exit (systemd will restart)"
    sleep 30
    exit 1
fi
echo "[fluxchi-wrap] found Mac at $MAC_IP, launching listener"

WS_URL="ws://${MAC_IP}:${FLUXCHI_PORT}/ws/harness"
EXTRA_ARGS=()
[ -n "${FLUXCHI_DISABLE_VOICE_GATE:-}" ] && EXTRA_ARGS+=(--no-voice-gate)

cd "$RUNTIME_DIR"
exec "$PYTHON" -m lelamp.integrations.fluxchi_listener \
    --ws "$WS_URL" \
    --profile "$FLUXCHI_PROFILE_PATH" \
    --dashboard "$LELAMP_DASHBOARD" \
    --log-level "$FLUXCHI_LOG_LEVEL" \
    "${EXTRA_ARGS[@]}"
