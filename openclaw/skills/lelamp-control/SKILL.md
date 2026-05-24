---
name: lelamp_control
description: Safe high-level control of a LeLamp attached to this Raspberry Pi.
---

# LeLamp Control

Use this skill when the human wants to control LeLamp from OpenClaw.

## Scope

This skill is for high-level commands only:

- list recordings
- play a named recording
- set a solid LED color
- clear LEDs
- inspect the resolved lamp config

This skill is not for first-time bring-up, motor ID setup, calibration, or raw teleoperation.

## Assumptions

- The LeLamp runtime repo is cloned at `~/lelamp_runtime`
- `.env` is already configured there
- The lamp is powered, connected, and already calibrated

## Commands

Run these commands from `~/lelamp_runtime`:

```bash
uv run -m lelamp.remote_control show-config
uv run -m lelamp.remote_control list-recordings
uv run -m lelamp.remote_control play curious
uv run -m lelamp.remote_control solid 255 160 32
uv run -m lelamp.remote_control clear
```

## Rules

- Always list recordings before choosing a movement you are not sure exists.
- Never run `setup_motors`, `calibrate`, `record`, or `replay` unless the human explicitly asks.
- Never invent recording names.
- Treat OpenClaw as a high-level remote control layer, not a low-latency joystick loop.
- If a command fails, tell the human the exact failing command and suggest checking power, `LELAMP_PORT`, and `aplay -l` or `ls /dev/ttyACM*` when relevant.
