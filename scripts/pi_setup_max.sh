#!/usr/bin/env bash

set -euo pipefail

if [[ "${EUID}" -eq 0 ]]; then
  echo "Run this script as your normal Raspberry Pi user, not root."
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAMP_ID="${LAMP_ID:-lelamp}"
LAMP_PORT="${LAMP_PORT:-/dev/ttyACM0}"
MODE_SCRIPT="${MODE_SCRIPT:-smooth_animation.py}"
LED_COUNT="${LED_COUNT:-40}"
LED_PIN="${LED_PIN:-12}"
INSTALL_SERVICE="${INSTALL_SERVICE:-0}"
SERVICE_NAME="${SERVICE_NAME:-lelamp}"
SERVICE_USER="$(id -un)"
RESPEAKER_VARIANT="${RESPEAKER_VARIANT:-auto}"
ALLOW_UNSUPPORTED_RESPEAKER_V1="${ALLOW_UNSUPPORTED_RESPEAKER_V1:-0}"
OVERLAY_URL="https://raw.githubusercontent.com/Seeed-Studio/seeed-linux-dtoverlays/refs/heads/master/overlays/rpi/respeaker-2mic-v2_0-overlay.dts"
PI_MODEL="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)"
OS_CODENAME="$(. /etc/os-release && printf '%s' "${VERSION_CODENAME:-unknown}")"
ARCH="$(uname -m)"

log() {
  printf '\n==> %s\n' "$*"
}

append_if_missing() {
  local line="$1"
  local file="$2"

  if ! sudo grep -qxF "$line" "$file" 2>/dev/null; then
    printf '%s\n' "$line" | sudo tee -a "$file" >/dev/null
  fi
}

upsert_boot_overlay() {
  local overlay_name="$1"
  local line="$2"
  local file="$3"

  if sudo grep -q "^dtoverlay=${overlay_name}" "$file" 2>/dev/null; then
    sudo sed -i "s|^dtoverlay=${overlay_name}.*|${line}|" "$file"
  else
    printf '%s\n' "$line" | sudo tee -a "$file" >/dev/null
  fi
}

upsert_env() {
  local key="$1"
  local value="$2"
  local file="$3"

  if grep -q "^${key}=" "$file"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$file"
  else
    printf '%s=%s\n' "$key" "$value" >> "$file"
  fi
}

resolve_respeaker_variant() {
  case "$RESPEAKER_VARIANT" in
    auto)
      # On Pi 5 and current Raspberry Pi OS releases, the safe default is the V2 path.
      printf 'v2'
      ;;
    v2|skip)
      printf '%s' "$RESPEAKER_VARIANT"
      ;;
    v1)
      if [[ "$PI_MODEL" == *"Raspberry Pi 5"* || "$OS_CODENAME" == "bookworm" || "$OS_CODENAME" == "trixie" ]]; then
        if [[ "$ALLOW_UNSUPPORTED_RESPEAKER_V1" != "1" ]]; then
          echo "ReSpeaker V1 / WM8960 is not enabled on ${PI_MODEL:-this Pi} with ${OS_CODENAME}." >&2
          echo "Use RESPEAKER_VARIANT=v2 for ReSpeaker 2-Mics Pi HAT V2.0, or RESPEAKER_VARIANT=skip to leave audio untouched." >&2
          echo "If you really need to continue on an unsupported path, rerun with ALLOW_UNSUPPORTED_RESPEAKER_V1=1." >&2
          exit 1
        fi
      fi
      printf 'v1'
      ;;
    *)
      echo "Unsupported RESPEAKER_VARIANT: ${RESPEAKER_VARIANT}" >&2
      echo "Expected one of: auto, v2, v1, skip" >&2
      exit 1
      ;;
  esac
}

configure_respeaker_v2() {
  local tmp_dir="$1"

  log "Installing ReSpeaker V2 device-tree overlay"
  curl -L -sS "$OVERLAY_URL" -o "$tmp_dir/respeaker-2mic-v2_0-overlay.dts"
  dtc -I dts "$tmp_dir/respeaker-2mic-v2_0-overlay.dts" -o "$tmp_dir/respeaker-2mic-v2_0.dtbo"
  sudo cp "$tmp_dir/respeaker-2mic-v2_0.dtbo" /boot/firmware/overlays/
  append_if_missing "dtoverlay=respeaker-2mic-v2_0" "/boot/firmware/config.txt"

  log "Writing /etc/asound.conf for the ReSpeaker V2 HAT with 24 kHz realtime-compatible defaults"
  sudo tee /etc/asound.conf >/dev/null <<'EOF'
pcm.dmixer_raw {
    type dmix
    ipc_key 1024
    slave {
        pcm "hw:CARD=seeed2micvoicec,DEV=0"
        period_time 0
        period_size 1024
        buffer_size 8192
        rate 48000
    }
}

pcm.dsnoop0_raw {
    type dsnoop
    ipc_key 2048
    slave {
        pcm "hw:CARD=seeed2micvoicec,DEV=0"
        period_time 0
        period_size 1024
        buffer_size 8192
        rate 48000
    }
}

pcm.dmixer {
    type plug
    slave.pcm "dmixer_raw"
}

pcm.dsnoop0 {
    type plug
    slave.pcm "dsnoop0_raw"
}

pcm.!default {
    type asym
    playback.pcm "dmixer"
    capture.pcm "dsnoop0"
}

ctl.!default {
    type hw
    card seeed2micvoicec
}
EOF
}

configure_respeaker_v1() {
  log "RESPEAKER_VARIANT=v1 was requested"
  log "This path is intentionally not automated in this workspace because it is not a safe default on modern Pi setups."
  log "Skipping audio overlay changes. Use the legacy WM8960 guide only if you have verified the exact HAT revision and OS compatibility."
}

configure_ws2812_overlay() {
  local overlay_line="dtoverlay=ws2812-pio,gpio=${LED_PIN},num_leds=${LED_COUNT}"

  log "Persisting ws2812-pio overlay for ${LED_COUNT} LEDs on GPIO ${LED_PIN}"
  upsert_boot_overlay "ws2812-pio" "$overlay_line" "/boot/firmware/config.txt"
}

log "Refreshing sudo credentials"
sudo -v

if [[ "$PI_MODEL" == *"Raspberry Pi 5"* ]]; then
  log "Detected ${PI_MODEL}. Use a dedicated 5V/5A USB-C PD supply for stable Pi 5 bring-up."
fi

log "Detected architecture: ${ARCH}"
log "Detected Raspberry Pi OS codename: ${OS_CODENAME}"

log "Installing OS packages required by LeLamp"
sudo apt-get update
sudo apt-get install -y \
  alsa-utils \
  build-essential \
  curl \
  device-tree-compiler \
  git \
  libasound2-dev \
  pkg-config \
  portaudio19-dev \
  python3-dev

if ! command -v uv >/dev/null 2>&1; then
  log "Installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

UV_BIN="$(command -v uv)"

log "Syncing Python dependencies with hardware extras"
cd "$REPO_ROOT"
"$UV_BIN" sync --extra hardware

if [[ ! -f "$REPO_ROOT/.env" ]]; then
  log "Creating .env from .env.example"
  cp "$REPO_ROOT/.env.example" "$REPO_ROOT/.env"
fi
chmod 600 "$REPO_ROOT/.env" 2>/dev/null || true

upsert_env "LELAMP_ID" "$LAMP_ID" "$REPO_ROOT/.env"
upsert_env "LELAMP_PORT" "$LAMP_PORT" "$REPO_ROOT/.env"
upsert_env "LELAMP_AUDIO_USER" "$USER" "$REPO_ROOT/.env"
upsert_env "HF_LEROBOT_CALIBRATION" "$HOME/.cache/huggingface/lerobot/calibration" "$REPO_ROOT/.env"
upsert_env "LELAMP_LED_COUNT" "$LED_COUNT" "$REPO_ROOT/.env"
upsert_env "LELAMP_LED_PIN" "$LED_PIN" "$REPO_ROOT/.env"
chmod 600 "$REPO_ROOT/.env" 2>/dev/null || true

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

resolved_respeaker_variant="$(resolve_respeaker_variant)"
log "Resolved ReSpeaker path: ${resolved_respeaker_variant}"

case "$resolved_respeaker_variant" in
  v2)
    configure_respeaker_v2 "$tmp_dir"
    ;;
  v1)
    configure_respeaker_v1
    ;;
  skip)
    log "Skipping ReSpeaker configuration"
    ;;
esac

configure_ws2812_overlay

install_led_device_permissions() {
  sudo tee /etc/udev/rules.d/99-lelamp-leds0.rules >/dev/null <<'EOF'
KERNEL=="leds0", MODE="0660", GROUP="gpio"
EOF
  sudo udevadm control --reload-rules || true
  sudo udevadm trigger --name-match=leds0 || true
  sudo chgrp gpio /dev/leds0 2>/dev/null || true
  sudo chmod 0660 /dev/leds0 2>/dev/null || true
}

install_led_device_permissions

if [[ "$INSTALL_SERVICE" == "1" ]]; then
  log "Installing ${SERVICE_NAME}.service"
  sudo tee "/etc/systemd/system/${SERVICE_NAME}.service" >/dev/null <<EOF
[Unit]
Description=LeLamp Runtime
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${REPO_ROOT}
EnvironmentFile=${REPO_ROOT}/.env
ExecStart=/usr/bin/env bash -lc 'cd ${REPO_ROOT} && exec /usr/bin/script -qefc "${UV_BIN} run ${MODE_SCRIPT} console" /dev/null'
Restart=always
RestartSec=5
User=${SERVICE_USER}
Group=${SERVICE_USER}

[Install]
WantedBy=multi-user.target
EOF
  sudo systemctl daemon-reload
  sudo systemctl enable "${SERVICE_NAME}"
fi

log "Pi bootstrap complete"
cat <<EOF

Next steps:
1. Reboot the Pi so the ReSpeaker and ws2812 overlays are loaded.
2. Fill in ${REPO_ROOT}/.env with your MODEL provider settings. LiveKit secrets are only needed if you later use room mode.
3. Confirm 'aplay -l' shows the expected audio device and 'ls -l /dev/leds0' succeeds.
4. Run '${UV_BIN} run ${MODE_SCRIPT} download-files' once after networking is ready.
5. Verify audio with '${UV_BIN} run -m lelamp.test.test_audio' if audio was configured.
6. Verify LEDs with 'sudo ${UV_BIN} run -m lelamp.remote_control solid 255 160 32'.
7. Find the servo port with '${UV_BIN} run lerobot-find-port'.
8. Configure motors, calibrate, and test before starting the local voice agent.

Optional:
- Install the boot service in one shot:
  INSTALL_SERVICE=1 MODE_SCRIPT=${MODE_SCRIPT} ${REPO_ROOT}/scripts/pi_setup_max.sh
- Override audio path selection:
  RESPEAKER_VARIANT=auto|v2|v1|skip ${REPO_ROOT}/scripts/pi_setup_max.sh
- Override LED overlay defaults:
  LED_PIN=${LED_PIN} LED_COUNT=${LED_COUNT} ${REPO_ROOT}/scripts/pi_setup_max.sh
EOF
