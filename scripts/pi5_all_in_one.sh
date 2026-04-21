#!/usr/bin/env bash

set -euo pipefail

if [[ "${EUID}" -eq 0 ]]; then
  echo "Run this script as your normal Raspberry Pi user, not root."
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AUTO_ACCEPT_DEFAULTS="${AUTO_ACCEPT_DEFAULTS:-0}"
AUTO_REBOOT="${AUTO_REBOOT:-0}"
ALLOW_NON_PI5="${ALLOW_NON_PI5:-0}"

LAMP_ID_WAS_SET="${LAMP_ID+x}"
LAMP_PORT_WAS_SET="${LAMP_PORT+x}"
MODE_SCRIPT_WAS_SET="${MODE_SCRIPT+x}"
LED_COUNT_WAS_SET="${LED_COUNT+x}"
LED_PIN_WAS_SET="${LED_PIN+x}"
RESPEAKER_VARIANT_WAS_SET="${RESPEAKER_VARIANT+x}"
INSTALL_LELAMP_SERVICE_WAS_SET="${INSTALL_LELAMP_SERVICE+x}"
INSTALL_OPENCLAW_WAS_SET="${INSTALL_OPENCLAW+x}"
OPENCLAW_INSTALL_MODE_WAS_SET="${OPENCLAW_INSTALL_MODE+x}"
INSTALL_TAILSCALE_WAS_SET="${INSTALL_TAILSCALE+x}"
RUN_OPENCLAW_ONBOARD_WAS_SET="${RUN_OPENCLAW_ONBOARD+x}"
RUN_DOWNLOAD_FILES_POSTBOOT_WAS_SET="${RUN_DOWNLOAD_FILES_POSTBOOT+x}"
MODEL_PROVIDER_WAS_SET="${MODEL_PROVIDER+x}"
MODEL_API_KEY_WAS_SET="${MODEL_API_KEY+x}"
MODEL_BASE_URL_WAS_SET="${MODEL_BASE_URL+x}"
MODEL_NAME_WAS_SET="${MODEL_NAME+x}"
MODEL_VOICE_WAS_SET="${MODEL_VOICE+x}"
LIVEKIT_URL_WAS_SET="${LIVEKIT_URL+x}"
LIVEKIT_API_KEY_WAS_SET="${LIVEKIT_API_KEY+x}"
LIVEKIT_API_SECRET_WAS_SET="${LIVEKIT_API_SECRET+x}"

LAMP_ID="${LAMP_ID:-lelamp}"
LAMP_PORT="${LAMP_PORT:-auto}"
MODE_SCRIPT="${MODE_SCRIPT:-smooth_animation.py}"
LED_COUNT="${LED_COUNT:-40}"
LED_PIN="${LED_PIN:-12}"
RESPEAKER_VARIANT="${RESPEAKER_VARIANT:-auto}"

INSTALL_LELAMP_SERVICE="${INSTALL_LELAMP_SERVICE:-auto}"
INSTALL_OPENCLAW="${INSTALL_OPENCLAW:-auto}"
OPENCLAW_INSTALL_MODE="${OPENCLAW_INSTALL_MODE:-standard}"
INSTALL_TAILSCALE="${INSTALL_TAILSCALE:-auto}"
RUN_OPENCLAW_ONBOARD="${RUN_OPENCLAW_ONBOARD:-auto}"
RUN_DOWNLOAD_FILES_POSTBOOT="${RUN_DOWNLOAD_FILES_POSTBOOT:-1}"

MODEL_PROVIDER="${MODEL_PROVIDER:-qwen}"
MODEL_API_KEY="${MODEL_API_KEY:-${DASHSCOPE_API_KEY:-${QWEN_API_KEY:-${ZAI_API_KEY:-${OPENAI_API_KEY:-}}}}}"
MODEL_BASE_URL="${MODEL_BASE_URL:-}"
MODEL_NAME="${MODEL_NAME:-}"
MODEL_VOICE="${MODEL_VOICE:-}"
LIVEKIT_URL="${LIVEKIT_URL:-}"
LIVEKIT_API_KEY="${LIVEKIT_API_KEY:-}"
LIVEKIT_API_SECRET="${LIVEKIT_API_SECRET:-}"

PI_MODEL="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)"
ARCH="$(uname -m)"
OS_CODENAME="$(. /etc/os-release && printf '%s' "${VERSION_CODENAME:-unknown}")"
ENV_FILE="${REPO_ROOT}/.env"
POST_BOOT_ENV_FILE="${REPO_ROOT}/.pi5_post_boot.env"
POST_BOOT_SERVICE_PATH="/etc/systemd/system/lelamp-post-bootstrap.service"

log() {
  printf '\n==> %s\n' "$*"
}

was_explicitly_set() {
  local flag_name="${1}_WAS_SET"
  [[ -n "${!flag_name:-}" ]]
}

read_env_value() {
  local key="$1"

  if [[ ! -f "$ENV_FILE" ]]; then
    return 0
  fi

  grep -E "^${key}=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- || true
}

prompt_default() {
  local var_name="$1"
  local prompt_text="$2"
  local default_value="$3"
  local response

  if was_explicitly_set "$var_name"; then
    return
  fi

  if [[ "$AUTO_ACCEPT_DEFAULTS" == "1" || ! -t 0 ]]; then
    printf -v "$var_name" '%s' "$default_value"
    return
  fi

  read -r -p "${prompt_text} [${default_value}]: " response
  printf -v "$var_name" '%s' "${response:-$default_value}"
}

prompt_yes_no() {
  local var_name="$1"
  local prompt_text="$2"
  local default_value="$3"
  local response

  if was_explicitly_set "$var_name" && [[ "${!var_name:-}" == "0" || "${!var_name:-}" == "1" ]]; then
    return
  fi

  if [[ "$AUTO_ACCEPT_DEFAULTS" == "1" || ! -t 0 ]]; then
    printf -v "$var_name" '%s' "$default_value"
    return
  fi

  local default_hint="y/N"
  if [[ "$default_value" == "1" ]]; then
    default_hint="Y/n"
  fi

  while true; do
    read -r -p "${prompt_text} [${default_hint}]: " response
    response="${response:-}"
    if [[ -z "$response" ]]; then
      printf -v "$var_name" '%s' "$default_value"
      return
    fi
    case "${response,,}" in
      y|yes) printf -v "$var_name" '1'; return ;;
      n|no) printf -v "$var_name" '0'; return ;;
    esac
  done
}

prompt_secret() {
  local var_name="$1"
  local prompt_text="$2"
  local response

  if [[ -n "${!var_name:-}" ]]; then
    return
  fi

  if [[ "$AUTO_ACCEPT_DEFAULTS" == "1" || ! -t 0 ]]; then
    return
  fi

  read -r -s -p "${prompt_text}: " response
  printf '\n'
  printf -v "$var_name" '%s' "$response"
}

upsert_env() {
  local key="$1"
  local value="$2"
  local escaped_value

  escaped_value="$(printf '%s' "$value" | sed 's/[&|]/\\&/g')"

  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${escaped_value}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

command_present() {
  command -v "$1" >/dev/null 2>&1
}

normalize_model_provider() {
  case "${MODEL_PROVIDER,,}" in
    qwen|dashscope|tongyi)
      MODEL_PROVIDER="qwen"
      ;;
    glm|zhipu|bigmodel|z.ai)
      MODEL_PROVIDER="glm"
      ;;
    "")
      MODEL_PROVIDER="qwen"
      ;;
    *)
      MODEL_PROVIDER="${MODEL_PROVIDER,,}"
      ;;
  esac
}

default_model_base_url() {
  case "$1" in
    qwen) printf '%s' "https://dashscope.aliyuncs.com/api-ws/v1/realtime" ;;
    glm) printf '%s' "https://open.bigmodel.cn/api/paas/v4/realtime" ;;
    openai) printf '%s' "https://api.openai.com/v1" ;;
    *) printf '%s' "" ;;
  esac
}

default_model_name() {
  case "$1" in
    qwen) printf '%s' "qwen3.5-omni-flash-realtime" ;;
    glm) printf '%s' "glm-realtime" ;;
    *) printf '%s' "" ;;
  esac
}

default_model_voice() {
  case "$1" in
    qwen) printf '%s' "Tina" ;;
    glm) printf '%s' "tongtong" ;;
    openai) printf '%s' "ballad" ;;
    *) printf '%s' "Tina" ;;
  esac
}

apply_model_provider_defaults() {
  normalize_model_provider

  if ! was_explicitly_set MODEL_BASE_URL && [[ -z "$MODEL_BASE_URL" ]]; then
    MODEL_BASE_URL="$(default_model_base_url "$MODEL_PROVIDER")"
  fi

  if ! was_explicitly_set MODEL_NAME && [[ -z "$MODEL_NAME" ]]; then
    MODEL_NAME="$(default_model_name "$MODEL_PROVIDER")"
  fi

  if ! was_explicitly_set MODEL_VOICE && [[ -z "$MODEL_VOICE" ]]; then
    MODEL_VOICE="$(default_model_voice "$MODEL_PROVIDER")"
  fi
}

service_is_enabled() {
  local service_name="$1"

  if ! command_present systemctl; then
    return 1
  fi

  systemctl is-enabled "$service_name" >/dev/null 2>&1
}

load_existing_env_defaults() {
  local existing_value

  existing_value="$(read_env_value LELAMP_ID)"
  if ! was_explicitly_set LAMP_ID && [[ -n "$existing_value" ]]; then
    LAMP_ID="$existing_value"
  fi

  existing_value="$(read_env_value LELAMP_PORT)"
  if ! was_explicitly_set LAMP_PORT && [[ -n "$existing_value" ]]; then
    LAMP_PORT="$existing_value"
  fi

  existing_value="$(read_env_value LELAMP_LED_COUNT)"
  if ! was_explicitly_set LED_COUNT && [[ -n "$existing_value" ]]; then
    LED_COUNT="$existing_value"
  fi

  existing_value="$(read_env_value LELAMP_LED_PIN)"
  if ! was_explicitly_set LED_PIN && [[ -n "$existing_value" ]]; then
    LED_PIN="$existing_value"
  fi

  existing_value="$(read_env_value MODEL_PROVIDER)"
  if ! was_explicitly_set MODEL_PROVIDER && [[ -n "$existing_value" ]]; then
    MODEL_PROVIDER="$existing_value"
  fi

  existing_value="$(read_env_value MODEL_API_KEY)"
  if ! was_explicitly_set MODEL_API_KEY && [[ -z "$MODEL_API_KEY" && -n "$existing_value" ]]; then
    MODEL_API_KEY="$existing_value"
  fi

  if ! was_explicitly_set MODEL_API_KEY && [[ -z "$MODEL_API_KEY" ]]; then
    existing_value="$(read_env_value ZAI_API_KEY)"
    if [[ -n "$existing_value" ]]; then
      MODEL_API_KEY="$existing_value"
    fi
  fi

  if ! was_explicitly_set MODEL_API_KEY && [[ -z "$MODEL_API_KEY" ]]; then
    existing_value="$(read_env_value OPENAI_API_KEY)"
    if [[ -n "$existing_value" ]]; then
      MODEL_API_KEY="$existing_value"
    fi
  fi

  existing_value="$(read_env_value MODEL_BASE_URL)"
  if ! was_explicitly_set MODEL_BASE_URL && [[ -n "$existing_value" ]]; then
    MODEL_BASE_URL="$existing_value"
  fi

  existing_value="$(read_env_value MODEL_NAME)"
  if ! was_explicitly_set MODEL_NAME && [[ -n "$existing_value" ]]; then
    MODEL_NAME="$existing_value"
  fi

  existing_value="$(read_env_value MODEL_VOICE)"
  if ! was_explicitly_set MODEL_VOICE && [[ -n "$existing_value" ]]; then
    MODEL_VOICE="$existing_value"
  fi

  existing_value="$(read_env_value LIVEKIT_URL)"
  if ! was_explicitly_set LIVEKIT_URL && [[ -z "$LIVEKIT_URL" && -n "$existing_value" ]]; then
    LIVEKIT_URL="$existing_value"
  fi

  existing_value="$(read_env_value LIVEKIT_API_KEY)"
  if ! was_explicitly_set LIVEKIT_API_KEY && [[ -z "$LIVEKIT_API_KEY" && -n "$existing_value" ]]; then
    LIVEKIT_API_KEY="$existing_value"
  fi

  existing_value="$(read_env_value LIVEKIT_API_SECRET)"
  if ! was_explicitly_set LIVEKIT_API_SECRET && [[ -z "$LIVEKIT_API_SECRET" && -n "$existing_value" ]]; then
    LIVEKIT_API_SECRET="$existing_value"
  fi
}

resolve_auto_defaults() {
  if [[ "$INSTALL_LELAMP_SERVICE" == "auto" ]]; then
    if service_is_enabled lelamp.service; then
      INSTALL_LELAMP_SERVICE="0"
    else
      INSTALL_LELAMP_SERVICE="1"
    fi
  fi

  if [[ "$INSTALL_OPENCLAW" == "auto" ]]; then
    if command_present openclaw; then
      INSTALL_OPENCLAW="0"
    else
      INSTALL_OPENCLAW="1"
    fi
  fi

  if [[ "$INSTALL_TAILSCALE" == "auto" ]]; then
    if command_present tailscale; then
      INSTALL_TAILSCALE="0"
    else
      INSTALL_TAILSCALE="0"
    fi
  fi

  if [[ "$RUN_OPENCLAW_ONBOARD" == "auto" ]]; then
    RUN_OPENCLAW_ONBOARD="0"
  fi
}

detect_servo_port() {
  local devices=()

  if compgen -G '/dev/ttyACM*' >/dev/null 2>&1; then
    mapfile -t devices < <(printf '%s\n' /dev/ttyACM*)
  fi

  if [[ "$LAMP_PORT" == "auto" || -z "$LAMP_PORT" ]]; then
    if [[ "${#devices[@]}" -gt 0 ]]; then
      LAMP_PORT="${devices[0]}"
      log "Auto-selected servo port: ${LAMP_PORT}"
    else
      LAMP_PORT="/dev/ttyACM0"
      log "No ttyACM device detected yet; defaulting to ${LAMP_PORT}"
    fi
    return
  fi

  if [[ ! -e "$LAMP_PORT" && "${#devices[@]}" -eq 1 ]]; then
    log "Requested servo port ${LAMP_PORT} not found; switching to detected device ${devices[0]}"
    LAMP_PORT="${devices[0]}"
  fi
}

print_detection_summary() {
  printf '\nCurrent defaults after auto-detection:\n'
  printf '  LAMP_ID=%s\n' "$LAMP_ID"
  printf '  LAMP_PORT=%s\n' "$LAMP_PORT"
  printf '  MODE_SCRIPT=%s\n' "$MODE_SCRIPT"
  printf '  LED_COUNT=%s\n' "$LED_COUNT"
  printf '  LED_PIN=%s\n' "$LED_PIN"
  printf '  MODEL_PROVIDER=%s\n' "$MODEL_PROVIDER"
  printf '  MODEL_BASE_URL=%s\n' "$MODEL_BASE_URL"
  printf '  MODEL_NAME=%s\n' "$MODEL_NAME"
  printf '  MODEL_VOICE=%s\n' "$MODEL_VOICE"
  printf '  RESPEAKER_VARIANT=%s\n' "$RESPEAKER_VARIANT"
  printf '  INSTALL_LELAMP_SERVICE=%s\n' "$INSTALL_LELAMP_SERVICE"
  printf '  INSTALL_OPENCLAW=%s\n' "$INSTALL_OPENCLAW"
  if [[ "$INSTALL_OPENCLAW" == "1" ]]; then
    printf '  OPENCLAW_INSTALL_MODE=%s\n' "$OPENCLAW_INSTALL_MODE"
    printf '  INSTALL_TAILSCALE=%s\n' "$INSTALL_TAILSCALE"
    printf '  RUN_OPENCLAW_ONBOARD=%s\n' "$RUN_OPENCLAW_ONBOARD"
  fi
  printf '  RUN_DOWNLOAD_FILES_POSTBOOT=%s\n' "$RUN_DOWNLOAD_FILES_POSTBOOT"
  printf '  AUTO_REBOOT=%s\n' "$AUTO_REBOOT"
}

ensure_pi_profile() {
  if [[ "$PI_MODEL" != *"Raspberry Pi 5"* && "$ALLOW_NON_PI5" != "1" ]]; then
    echo "This installer is tuned for Raspberry Pi 5, but detected: ${PI_MODEL:-unknown}" >&2
    echo "If you really want to continue on another device, rerun with ALLOW_NON_PI5=1." >&2
    exit 1
  fi

  if [[ "$ARCH" != "aarch64" ]]; then
    echo "Expected 64-bit Raspberry Pi OS. Detected architecture: ${ARCH}" >&2
    echo "For the safest path, use Raspberry Pi OS Lite 64-bit on Pi 5." >&2
  fi
}

has_voice_runtime_config() {
  if [[ -n "$MODEL_API_KEY" ]]; then
    return 0
  fi

  if [[ "$MODEL_PROVIDER" == "custom" && -n "$MODEL_BASE_URL" && -n "$MODEL_NAME" ]]; then
    return 0
  fi

  return 1
}

install_post_boot_service() {
  local resolved_uv_bin="${UV_BIN:-}"

  if [[ -z "$resolved_uv_bin" ]] && command -v uv >/dev/null 2>&1; then
    resolved_uv_bin="$(command -v uv)"
  fi
  if [[ -z "$resolved_uv_bin" && -x "${HOME}/.local/bin/uv" ]]; then
    resolved_uv_bin="${HOME}/.local/bin/uv"
  fi

  cat >"$POST_BOOT_ENV_FILE" <<EOF
MODE_SCRIPT=${MODE_SCRIPT}
RUN_DOWNLOAD_FILES_POSTBOOT=${RUN_DOWNLOAD_FILES_POSTBOOT}
CHECK_OPENCLAW=${INSTALL_OPENCLAW}
UV_BIN=${resolved_uv_bin}
EOF
  chmod 600 "$POST_BOOT_ENV_FILE" 2>/dev/null || true

  sudo tee "$POST_BOOT_SERVICE_PATH" >/dev/null <<EOF
[Unit]
Description=LeLamp Pi 5 post-boot finalizer
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=${REPO_ROOT}
ExecStart=/usr/bin/env bash -lc 'cd ${REPO_ROOT} && ${REPO_ROOT}/scripts/pi5_post_reboot_finalize.sh'
User=root

[Install]
WantedBy=multi-user.target
EOF

  sudo systemctl daemon-reload
  sudo systemctl enable lelamp-post-bootstrap.service
}

log "Detected Pi model: ${PI_MODEL:-unknown}"
log "Detected architecture: ${ARCH}"
log "Detected OS codename: ${OS_CODENAME}"

ensure_pi_profile
load_existing_env_defaults
apply_model_provider_defaults
detect_servo_port
resolve_auto_defaults
print_detection_summary

prompt_default LAMP_ID "Lamp ID" "$LAMP_ID"
prompt_default LAMP_PORT "Servo serial port" "$LAMP_PORT"
prompt_default MODE_SCRIPT "Runtime mode script (smooth_animation.py or main.py)" "$MODE_SCRIPT"
prompt_default LED_COUNT "LED count" "$LED_COUNT"
prompt_default LED_PIN "LED GPIO pin" "$LED_PIN"
prompt_default MODEL_PROVIDER "Realtime model provider (qwen, glm, openai, custom)" "$MODEL_PROVIDER"
apply_model_provider_defaults
prompt_default MODEL_BASE_URL "Realtime model base URL" "$MODEL_BASE_URL"
prompt_default MODEL_NAME "Realtime model name" "$MODEL_NAME"
prompt_default MODEL_VOICE "Realtime voice name" "$MODEL_VOICE"
prompt_default RESPEAKER_VARIANT "ReSpeaker variant (auto, v2, v1, skip)" "$RESPEAKER_VARIANT"

prompt_yes_no INSTALL_LELAMP_SERVICE "Install and enable LeLamp boot service" "$INSTALL_LELAMP_SERVICE"
prompt_yes_no INSTALL_OPENCLAW "Install OpenClaw on this Pi" "$INSTALL_OPENCLAW"

if [[ "$INSTALL_OPENCLAW" == "1" ]]; then
  prompt_default OPENCLAW_INSTALL_MODE "OpenClaw install mode (standard, local, git)" "$OPENCLAW_INSTALL_MODE"
  prompt_yes_no INSTALL_TAILSCALE "Install Tailscale for private remote dashboard access" "$INSTALL_TAILSCALE"
  prompt_yes_no RUN_OPENCLAW_ONBOARD "Run OpenClaw onboarding now" "$RUN_OPENCLAW_ONBOARD"
fi

prompt_yes_no RUN_DOWNLOAD_FILES_POSTBOOT "Run LiveKit/model download step automatically after reboot" "$RUN_DOWNLOAD_FILES_POSTBOOT"
prompt_yes_no AUTO_REBOOT "Reboot automatically at the end of setup" "$AUTO_REBOOT"

prompt_secret MODEL_API_KEY "Realtime model API key (leave blank to fill later)"
prompt_secret LIVEKIT_URL "LiveKit URL (leave blank to fill later)"
prompt_secret LIVEKIT_API_KEY "LiveKit API key (leave blank to fill later)"
prompt_secret LIVEKIT_API_SECRET "LiveKit API secret (leave blank to fill later)"

if [[ ! -f "$ENV_FILE" ]]; then
  cp "${REPO_ROOT}/.env.example" "$ENV_FILE"
fi
chmod 600 "$ENV_FILE" 2>/dev/null || true

upsert_env "LELAMP_ID" "$LAMP_ID"
upsert_env "LELAMP_PORT" "$LAMP_PORT"
upsert_env "LELAMP_AUDIO_USER" "$USER"
upsert_env "HF_LEROBOT_CALIBRATION" "$HOME/.cache/huggingface/lerobot/calibration"
upsert_env "LELAMP_LED_COUNT" "$LED_COUNT"
upsert_env "LELAMP_LED_PIN" "$LED_PIN"

upsert_env "MODEL_PROVIDER" "$MODEL_PROVIDER"
if [[ -n "$MODEL_API_KEY" ]]; then upsert_env "MODEL_API_KEY" "$MODEL_API_KEY"; fi
if [[ -n "$MODEL_BASE_URL" ]]; then upsert_env "MODEL_BASE_URL" "$MODEL_BASE_URL"; fi
if [[ -n "$MODEL_NAME" ]]; then upsert_env "MODEL_NAME" "$MODEL_NAME"; fi
if [[ -n "$MODEL_VOICE" ]]; then upsert_env "MODEL_VOICE" "$MODEL_VOICE"; fi
if [[ -n "$LIVEKIT_URL" ]]; then upsert_env "LIVEKIT_URL" "$LIVEKIT_URL"; fi
if [[ -n "$LIVEKIT_API_KEY" ]]; then upsert_env "LIVEKIT_API_KEY" "$LIVEKIT_API_KEY"; fi
if [[ -n "$LIVEKIT_API_SECRET" ]]; then upsert_env "LIVEKIT_API_SECRET" "$LIVEKIT_API_SECRET"; fi

resolved_install_service="$INSTALL_LELAMP_SERVICE"
if [[ "$INSTALL_LELAMP_SERVICE" == "1" ]] && ! has_voice_runtime_config; then
  log "Realtime model config is incomplete, so the LeLamp boot service will not be enabled yet."
  resolved_install_service="0"
fi

log "Running LeLamp Pi setup"
INSTALL_SERVICE="$resolved_install_service" \
MODE_SCRIPT="$MODE_SCRIPT" \
LAMP_ID="$LAMP_ID" \
LAMP_PORT="$LAMP_PORT" \
LED_COUNT="$LED_COUNT" \
LED_PIN="$LED_PIN" \
RESPEAKER_VARIANT="$RESPEAKER_VARIANT" \
  "${REPO_ROOT}/scripts/pi_setup_max.sh"

if [[ "$INSTALL_OPENCLAW" == "1" ]]; then
  log "Running OpenClaw setup"
  INSTALL_TAILSCALE="$INSTALL_TAILSCALE" \
  RUN_ONBOARD="$RUN_OPENCLAW_ONBOARD" \
  OPENCLAW_INSTALL_MODE="$OPENCLAW_INSTALL_MODE" \
    "${REPO_ROOT}/scripts/openclaw_pi5_setup.sh"

  log "Installing LeLamp OpenClaw skill"
  "${REPO_ROOT}/scripts/install_openclaw_skill.sh"
fi

log "Installing post-reboot finalizer"
install_post_boot_service

if [[ -x "${REPO_ROOT}/scripts/lelamp_doctor.sh" ]]; then
  log "Running doctor snapshot after staging"
  (
    cd "$REPO_ROOT"
    ./scripts/lelamp_doctor.sh || true
  )
fi

cat <<EOF

Setup staged successfully.

Repo root: ${REPO_ROOT}
Lamp ID: ${LAMP_ID}
Servo port: ${LAMP_PORT}
Mode script: ${MODE_SCRIPT}
LED count: ${LED_COUNT}
LED pin: ${LED_PIN}
Realtime provider: ${MODEL_PROVIDER}
Realtime base URL: ${MODEL_BASE_URL}
Realtime model: ${MODEL_NAME}
Realtime voice: ${MODEL_VOICE}
ReSpeaker path: ${RESPEAKER_VARIANT}
OpenClaw installed: ${INSTALL_OPENCLAW}
LeLamp boot service enabled now: ${resolved_install_service}

Next reboot will run:
- post-boot audio/device report
- optional download-files step
- optional OpenClaw status checks

After reboot, inspect:
- ${REPO_ROOT}/POST_BOOT_REPORT.md
- ${REPO_ROOT}/.env
EOF

if [[ "$AUTO_REBOOT" == "1" ]]; then
  log "Rebooting now"
  sudo reboot
else
  log "Reboot required. Rerun nothing; just reboot when ready."
fi
