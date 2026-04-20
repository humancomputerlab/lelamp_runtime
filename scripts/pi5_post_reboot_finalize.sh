#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOOT_ENV_FILE="${REPO_ROOT}/.pi5_post_boot.env"
REPORT_FILE="${REPO_ROOT}/POST_BOOT_REPORT.md"
SERVICE_NAME="lelamp-post-bootstrap.service"
MODE_SCRIPT="smooth_animation.py"
RUN_DOWNLOAD_FILES_POSTBOOT="1"
CHECK_OPENCLAW="0"
UV_BIN=""
EXIT_CODE=0

if [[ -f "$BOOT_ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  . "$BOOT_ENV_FILE"
fi

PATH="$HOME/.local/bin:$HOME/.openclaw/bin:$PATH"
UV_BIN="${UV_BIN:-$(command -v uv || true)}"

run_section() {
  local title="$1"
  shift

  {
    printf '\n## %s\n\n' "$title"
    printf '```bash\n'
    printf '%s\n' "$*"
    printf '```\n\n'
    if "$@" >"$tmp_out" 2>&1; then
      printf '```\n'
      cat "$tmp_out"
      printf '\n```\n'
    else
      EXIT_CODE=1
      printf 'Command exited non-zero.\n\n```\n'
      cat "$tmp_out"
      printf '\n```\n'
    fi
  } >>"$REPORT_FILE"
}

tmp_out="$(mktemp)"
trap 'rm -f "$tmp_out"' EXIT

{
  printf '# LeLamp Pi 5 Post-Boot Report\n\n'
  printf 'Generated at: %s\n\n' "$(date -u '+%Y-%m-%d %H:%M:%S UTC')"
} >"$REPORT_FILE"

if [[ "$RUN_DOWNLOAD_FILES_POSTBOOT" == "1" && -n "$UV_BIN" ]]; then
  run_section "Download Files" "$UV_BIN" run "$MODE_SCRIPT" download-files
fi

run_section "Audio Playback Devices" aplay -l
run_section "Audio Capture Devices" arecord -l
run_section "LED Overlay Status" bash -lc 'dtoverlay -l 2>/dev/null | grep -F ws2812-pio || echo "ws2812-pio overlay not loaded"'
run_section "LED Device Node" bash -lc 'ls -l /dev/leds0 2>/dev/null || echo "/dev/leds0 not found"'
run_section "USB Servo Devices" bash -lc 'ls /dev/ttyACM* 2>/dev/null || echo "No /dev/ttyACM devices found"'
run_section "LeLamp Doctor" bash -lc "cd ${REPO_ROOT} && ./scripts/lelamp_doctor.sh"
run_section "LeLamp Bootstrap Service Status" systemctl status lelamp-bootstrap.service --no-pager

if [[ "$CHECK_OPENCLAW" == "1" ]] && command -v openclaw >/dev/null 2>&1; then
  run_section "OpenClaw Version" openclaw --version
  run_section "OpenClaw Doctor" openclaw doctor
  run_section "OpenClaw Status" openclaw status
fi

rm -f "$BOOT_ENV_FILE"
systemctl disable "$SERVICE_NAME" >/dev/null 2>&1 || true

echo "Post-boot finalize complete. Report written to ${REPORT_FILE}"
exit "$EXIT_CODE"
