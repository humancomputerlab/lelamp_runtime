#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL_SRC="${REPO_ROOT}/openclaw/skills/lelamp-control/SKILL.md"
OPENCLAW_SKILL_HOME="${OPENCLAW_SKILL_HOME:-$HOME/.openclaw/skills}"
SKILL_DEST="${OPENCLAW_SKILL_HOME}/lelamp-control"

if [[ ! -f "$SKILL_SRC" ]]; then
  echo "Skill source file not found: ${SKILL_SRC}" >&2
  exit 1
fi

mkdir -p "${SKILL_DEST}"

if [[ -f "${SKILL_DEST}/SKILL.md" ]] && cmp -s "${SKILL_SRC}" "${SKILL_DEST}/SKILL.md"; then
  printf 'LeLamp OpenClaw skill already up to date at %s\n' "${SKILL_DEST}"
  exit 0
fi

cp "${SKILL_SRC}" "${SKILL_DEST}/SKILL.md"

printf 'Installed LeLamp OpenClaw skill to %s\n' "${SKILL_DEST}"
