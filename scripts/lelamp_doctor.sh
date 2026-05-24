#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"
OPENCLAW_SKILL_PATH="${HOME}/.openclaw/skills/lelamp-control/SKILL.md"
BOOTSTRAP_ENV_SYSTEM="/etc/lelamp/lelamp-bootstrap.env"
BOOTSTRAP_DONE_MARKER="/var/lib/lelamp-bootstrap.done"
EXIT_CODE=0

ok() {
  printf '[OK] %s\n' "$*"
}

warn() {
  printf '[WARN] %s\n' "$*"
}

miss() {
  printf '[MISS] %s\n' "$*"
  EXIT_CODE=1
}

header() {
  printf '\n== %s ==\n' "$*"
}

check_command() {
  local cmd="$1"
  local label="${2:-$1}"
  if command -v "$cmd" >/dev/null 2>&1; then
    ok "${label}: $(command -v "$cmd")"
  else
    miss "${label}: not installed"
  fi
}

check_optional_command() {
  local cmd="$1"
  local label="${2:-$1}"
  if command -v "$cmd" >/dev/null 2>&1; then
    ok "${label}: $(command -v "$cmd")"
  else
    warn "${label}: not installed"
  fi
}

check_file() {
  local path="$1"
  local label="$2"
  if [[ -e "$path" ]]; then
    ok "${label}: ${path}"
  else
    miss "${label}: missing (${path})"
  fi
}

check_optional_file() {
  local path="$1"
  local label="$2"
  if [[ -e "$path" ]]; then
    ok "${label}: ${path}"
  else
    warn "${label}: missing (${path})"
  fi
}

check_env_key() {
  local key="$1"
  if [[ ! -f "$ENV_FILE" ]]; then
    miss ".env file is missing; cannot verify ${key}"
    return
  fi

  local value
  value="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- || true)"
  if [[ -n "$value" ]]; then
    ok ".env ${key}: present"
  else
    warn ".env ${key}: missing"
  fi
}

check_optional_env_key() {
  local key="$1"
  if [[ ! -f "$ENV_FILE" ]]; then
    warn ".env file is missing; cannot verify ${key}"
    return
  fi

  local value
  value="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- || true)"
  if [[ -n "$value" ]]; then
    ok ".env ${key}: present"
  else
    warn ".env ${key}: missing"
  fi
}

service_state() {
  local service_name="$1"

  if ! command -v systemctl >/dev/null 2>&1; then
    warn "systemctl not found; cannot inspect ${service_name}"
    return
  fi

  local enabled_state active_state
  enabled_state="$(systemctl is-enabled "$service_name" 2>/dev/null || true)"
  active_state="$(systemctl is-active "$service_name" 2>/dev/null || true)"

  if [[ "$enabled_state" == "enabled" || "$active_state" == "active" ]]; then
    ok "${service_name}: enabled=${enabled_state:-unknown}, active=${active_state:-unknown}"
  elif [[ -n "$enabled_state" || -n "$active_state" ]]; then
    warn "${service_name}: enabled=${enabled_state:-unknown}, active=${active_state:-unknown}"
  else
    warn "${service_name}: not installed"
  fi
}

print_device_tree_model() {
  tr -d '\0' </proc/device-tree/model 2>/dev/null || printf 'unknown'
}

header "System"
ok "Model: $(print_device_tree_model)"
ok "Architecture: $(uname -m)"
ok "Kernel: $(uname -r)"
ok "Hostname: $(hostname)"
if [[ -f /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  ok "OS: ${PRETTY_NAME:-unknown}"
fi
if [[ "$(print_device_tree_model)" != *"Raspberry Pi 5"* ]]; then
  warn "This workspace is tuned for Raspberry Pi 5, but detected a different board"
fi

header "Core tooling"
check_command python3 "python3"
check_optional_command uv "uv"
check_command git "git"
check_command curl "curl"
check_optional_command dtc "device-tree-compiler"
check_optional_command aplay "alsa aplay"
check_optional_command arecord "alsa arecord"
check_optional_command amixer "alsa amixer"
check_optional_command libcamera-hello "libcamera-hello"
check_optional_command raspi-config "raspi-config"
check_optional_command nmcli "NetworkManager nmcli"
check_optional_command vcgencmd "vcgencmd"
check_optional_command node "node"
check_optional_command npm "npm"
check_optional_command openclaw "openclaw"

header "LeLamp workspace"
check_file "${REPO_ROOT}/pyproject.toml" "pyproject.toml"
check_file "${REPO_ROOT}/scripts/pi5_all_in_one.sh" "all-in-one installer"
check_file "${REPO_ROOT}/scripts/pi_setup_max.sh" "LeLamp Pi setup"
check_file "${REPO_ROOT}/scripts/lelamp_doctor.sh" "doctor script"
check_optional_file "${ENV_FILE}" ".env"

header "Environment"
check_env_key MODEL_PROVIDER
check_env_key MODEL_API_KEY
check_env_key MODEL_BASE_URL
check_env_key MODEL_NAME
check_env_key MODEL_VOICE
check_optional_env_key ZAI_API_KEY
check_optional_env_key OPENAI_API_KEY
check_env_key LIVEKIT_URL
check_env_key LIVEKIT_API_KEY
check_env_key LIVEKIT_API_SECRET
check_env_key LELAMP_ID
check_env_key LELAMP_PORT
check_env_key LELAMP_AUDIO_USER

header "Runtime assets"
if [[ -d "${REPO_ROOT}/lelamp/recordings" ]]; then
  recording_count="$(find "${REPO_ROOT}/lelamp/recordings" -maxdepth 1 -name '*.csv' | wc -l | tr -d ' ')"
  ok "Recordings directory present with ${recording_count} csv files"
else
  miss "Recordings directory missing"
fi
check_optional_file "/etc/asound.conf" "/etc/asound.conf"
check_optional_file "${REPO_ROOT}/POST_BOOT_REPORT.md" "post-boot report"

header "Devices"
tty_devices="$(ls /dev/ttyACM* 2>/dev/null || true)"
if [[ -n "$tty_devices" ]]; then
  ok "USB servo devices detected:"
  printf '%s\n' "$tty_devices"
else
  warn "No /dev/ttyACM* devices detected"
fi

if command -v aplay >/dev/null 2>&1; then
  audio_playback="$(aplay -l 2>/dev/null || true)"
  if [[ -n "$audio_playback" ]]; then
    ok "Playback devices detected"
  else
    warn "No playback devices listed by aplay -l"
  fi
fi

if command -v arecord >/dev/null 2>&1; then
  audio_capture="$(arecord -l 2>/dev/null || true)"
  if [[ -n "$audio_capture" ]]; then
    ok "Capture devices detected"
  else
    warn "No capture devices listed by arecord -l"
  fi
fi

header "Services"
service_state lelamp.service
service_state lelamp-post-bootstrap.service
service_state lelamp-bootstrap.service

header "Bootstrap"
check_optional_file "${BOOTSTRAP_ENV_SYSTEM}" "system bootstrap env"
check_optional_file "${BOOTSTRAP_DONE_MARKER}" "zero-touch completion marker"

header "OpenClaw integration"
check_optional_file "${OPENCLAW_SKILL_PATH}" "OpenClaw LeLamp skill"
if command -v openclaw >/dev/null 2>&1; then
  status_out="$(openclaw status 2>/dev/null || true)"
  if [[ -n "$status_out" ]]; then
    ok "openclaw status returned output"
  else
    warn "openclaw status returned no output"
  fi
fi

header "Summary"
if [[ "$EXIT_CODE" -eq 0 ]]; then
  ok "Doctor finished without critical missing items"
else
  miss "Doctor found critical missing items"
fi

exit "$EXIT_CODE"
