#!/usr/bin/env bash

set -euo pipefail

if [[ "${EUID}" -eq 0 ]]; then
  echo "Run this script as your normal Raspberry Pi user, not root."
  exit 1
fi

INSTALL_TAILSCALE="${INSTALL_TAILSCALE:-0}"
RUN_ONBOARD="${RUN_ONBOARD:-0}"
OPENCLAW_INSTALL_MODE="${OPENCLAW_INSTALL_MODE:-standard}"
OPENCLAW_PREFIX="${OPENCLAW_PREFIX:-$HOME/.openclaw}"
OPENCLAW_FORCE_REINSTALL="${OPENCLAW_FORCE_REINSTALL:-0}"
PI_MODEL="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)"

log() {
  printf '\n==> %s\n' "$*"
}

ensure_path_line() {
  local line="$1"
  local rc_file="$2"

  touch "$rc_file"
  if ! grep -qxF "$line" "$rc_file" 2>/dev/null; then
    printf '\n%s\n' "$line" >> "$rc_file"
  fi
}

openclaw_installed() {
  command -v openclaw >/dev/null 2>&1
}

log "Refreshing sudo credentials"
sudo -v

if [[ "$PI_MODEL" == *"Raspberry Pi 5"* ]]; then
  log "Detected ${PI_MODEL}. This script is tuned for the Pi 5 path."
fi

log "Installing base packages"
sudo apt-get update
sudo apt-get install -y build-essential curl git

if openclaw_installed && [[ "$OPENCLAW_FORCE_REINSTALL" != "1" ]]; then
  log "OpenClaw is already installed at $(command -v openclaw); skipping reinstall"
else
  case "$OPENCLAW_INSTALL_MODE" in
    standard)
      log "Installing OpenClaw with the official standard installer"
      curl -fsSL --proto '=https' --tlsv1.2 https://openclaw.ai/install.sh | bash -s -- --no-onboard
      ;;
    local)
      log "Installing OpenClaw into local prefix ${OPENCLAW_PREFIX}"
      curl -fsSL --proto '=https' --tlsv1.2 https://openclaw.ai/install-cli.sh | bash -s -- --no-onboard --prefix "${OPENCLAW_PREFIX}"
      export PATH="${OPENCLAW_PREFIX}/bin:${PATH}"
      ensure_path_line "export PATH=\"${OPENCLAW_PREFIX}/bin:\$PATH\"" "$HOME/.bashrc"
      ensure_path_line "export PATH=\"${OPENCLAW_PREFIX}/bin:\$PATH\"" "$HOME/.zshrc"
      ;;
    git)
      log "Installing OpenClaw with the official git mode"
      OPENCLAW_INSTALL_METHOD=git OPENCLAW_NO_PROMPT=1 \
        curl -fsSL --proto '=https' --tlsv1.2 https://openclaw.ai/install.sh | bash -s -- --no-onboard
      ;;
    *)
      echo "Unsupported OPENCLAW_INSTALL_MODE: ${OPENCLAW_INSTALL_MODE}" >&2
      echo "Expected one of: standard, local, git" >&2
      exit 1
      ;;
  esac
fi

if [[ "$INSTALL_TAILSCALE" == "1" ]]; then
  log "Installing Tailscale"
  curl -fsSL https://tailscale.com/install.sh | sh
fi

if [[ "$RUN_ONBOARD" == "1" ]]; then
  log "Starting OpenClaw onboarding"
  openclaw onboard --install-daemon
fi

log "OpenClaw setup complete"
cat <<'EOF'

Recommended next steps:
1. Run 'openclaw onboard --install-daemon' if you did not set RUN_ONBOARD=1.
2. During onboarding, choose Local mode for this Pi.
3. If you use GLM for OpenClaw, prefer the Z.AI / Coding Plan route with base URL:
   https://open.bigmodel.cn/api/coding/paas/v4
4. For OpenClaw model selection, start with glm-4.7 or glm-5 according to your quota.
5. If you want phone-first control, add Telegram first. It is the lightest remote path.
6. Verify with 'openclaw doctor' and 'openclaw status'.
7. Optionally install Tailscale for private dashboard access instead of exposing ports publicly.
EOF
