# LeLamp Local Dashboard Design

Date: 2026-04-13
Status: Approved for planning
Scope: `lelamp_runtime` local dashboard for Raspberry Pi and same-network operators

## 1. Problem

LeLamp now has runtime control paths for motion, RGB, audio, and voice, but there is no single local surface that shows the lamp's current state in real time and lets an operator trigger safe actions from a phone, laptop, or the Raspberry Pi's own screen.

The dashboard should be a practical operator console first:

- show the lamp's current runtime state honestly
- allow safe local control of startup, playback, light, and shutdown-related actions
- work on the Pi local display and on devices connected through the same local network or hotspot
- stay easy to extend as new motions, light actions, and diagnostics are added

## 2. Goals

- Provide one local web dashboard for status, controls, and diagnostics.
- Support these access modes with the same server:
  - Raspberry Pi local browser
  - same LAN clients
  - phone hotspot clients
  - Raspberry Pi hotspot clients
- Poll state every 300-500 ms. Recommendation: 400 ms default.
- Expose the current state as structured JSON plus a single-page HTML UI.
- Keep module boundaries explicit so future motion or light features can be added without rewriting the dashboard.
- Follow the visual language of `FluxChi/reference/harward-gesture/web/` while adapting it to LeLamp's warmer personality.

## 3. Non-Goals

- No cloud-first control surface.
- No WebSocket or streaming transport in v1.
- No Bluetooth web transport in v1.
- No camera/video dashboard in v1.
- No fake derived state when the runtime cannot know the truth. Unknown should stay unknown.
- No attempt to replace the existing motion/runtime stack. The dashboard is a local operator layer on top of it.

## 4. User Scenarios

### 4.1 Pi local screen

The lamp is connected to a Raspberry Pi with a local display. An operator opens the dashboard fullscreen on the Pi and uses it as the primary control and monitoring surface during demos.

### 4.2 Same-network operator

The Pi and an operator's phone or laptop are on the same Wi-Fi network. The operator opens the dashboard by Pi IP address and controls the lamp remotely.

### 4.3 Phone hotspot

The operator enables phone hotspot, connects the Pi and a second device to that hotspot, and uses the same dashboard URL.

### 4.4 Pi hotspot

The Pi exposes its own hotspot. Nearby devices join that hotspot and access the dashboard locally.

## 5. Product Decisions

### 5.1 Local-only serving model

The dashboard is served from the Raspberry Pi as a small local web app plus JSON API. It is designed for trusted local demo environments, not public internet exposure.

### 5.2 Polling over WebSocket

State freshness matters, but the complexity budget should stay low. A polling model is enough for a lamp operator console and reduces moving parts:

- simpler server lifecycle
- easier recovery after temporary disconnects
- easier browser and Pi compatibility
- easier debugging with plain HTTP

### 5.3 One combined console

The UI should include all three information layers in one place:

- demo-ready high-level state
- hardware/runtime detail
- debug and error detail

This avoids splitting the operator workflow across separate tools.

### 5.4 Service shape

Implementation should use one small Python web service, with FastAPI as the preferred framework for v1:

- serve the single-page dashboard
- expose JSON API endpoints
- run background samplers on the Pi
- keep the control and read APIs in one process

This keeps the runtime observable without introducing a separate frontend build or a second backend service.

## 6. System Architecture

The dashboard should live inside `lelamp_runtime` as a small, modular subsystem:

```text
lelamp/dashboard/
  api.py
  runtime_bridge.py
  state_store.py
  actions/
    __init__.py
    executor.py
    motion.py
    lights.py
  samplers/
    __init__.py
    runtime.py
    motors.py
    audio.py
    network.py
  web/
    index.html
    dashboard.css
    dashboard.js
```

### 6.1 `state_store.py`

Single in-memory source of truth for the dashboard. Responsibilities:

- hold the current dashboard state snapshot
- merge updates from samplers and action execution
- timestamp updates
- preserve last-known-good values where appropriate
- preserve explicit `unknown` when a subsystem cannot be observed

The store should present one normalized state object to both the API layer and the frontend.

### 6.2 `samplers/`

Read-only collectors that periodically inspect runtime facts and write them into the state store. Each sampler owns exactly one concern so it can be tested independently.

Initial sampler responsibilities:

- `runtime.py`: server uptime, software mode, current action summary
- `motors.py`: current motion state, last motion result, calibration/read health where available
- `audio.py`: configured audio output state, last audio health, volume if available
- `network.py`: bound host/port, local URLs, connectivity hints

### 6.3 `actions/`

Imperative control layer for operator-triggered commands. Responsibilities:

- validate action availability
- serialize execution so only one action runs at a time
- update `state_store` with running, success, and error transitions
- call the runtime bridge instead of talking directly to low-level services

### 6.4 `runtime_bridge.py`

Single adapter between dashboard code and the existing runtime. This keeps dashboard logic from spreading into motion, RGB, or CLI code.

Responsibilities:

- expose safe, typed methods such as `startup()`, `play(recording)`, `shutdown_pose()`, `stop()`, `set_light_solid(rgb)`, `clear_light()`
- reuse the existing LeLamp runtime behavior and configuration where possible
- hide whether an operation is implemented via existing Python services, helper functions, or safe internal command execution

The dashboard should depend on the bridge, not on `AnimationService`, `RGBService`, or `remote_control` details directly.

## 7. State Model

The top-level state object should have these sections:

```json
{
  "system": {},
  "motion": {},
  "light": {},
  "audio": {},
  "errors": []
}
```

### 7.1 `system`

- `status`: `ready | running | warning | error | unknown`
- `active_action`: action id or `null`
- `last_update_ms`
- `uptime_s`
- `server_started_at`
- `reachable_urls`: array of local URLs for Pi screen, LAN, or hotspot usage

### 7.2 `motion`

- `status`: `idle | running | homing | error | unknown`
- `current_recording`
- `last_completed_recording`
- `home_recording`
- `startup_recording`
- `last_result`
- `motors_connected`: `true | false | unknown`
- `calibration_state`: `ok | suspect | missing | unknown`

### 7.3 `light`

- `status`: `off | solid | transition | error | unknown`
- `color`
- `effect`
- `brightness`
- `last_result`

### 7.4 `audio`

- `status`: `ready | muted | warning | error | unknown`
- `output_device`
- `volume_percent`
- `last_result`

### 7.5 `errors`

Ordered list of recent surfaced issues. Each error item should include:

- `code`
- `message`
- `source`
- `severity`
- `first_seen_ms`
- `last_seen_ms`
- `active`

## 8. Action API

The dashboard server should expose a compact HTTP API.

### 8.1 Read endpoints

- `GET /`
  - return dashboard HTML
- `GET /api/state`
  - return the normalized state snapshot
- `GET /api/actions`
  - return currently supported actions and whether each is enabled

### 8.2 Motion endpoints

- `POST /api/actions/startup`
  - execute startup choreography into wakeup/home-safe ready posture
- `POST /api/actions/play`
  - body: `{ "name": "<recording>" }`
- `POST /api/actions/shutdown_pose`
  - move the lamp from current safe static state into the designed shutdown pose
- `POST /api/actions/stop`
  - request safe stop / return to stable state

### 8.3 Light endpoints

- `POST /api/lights/solid`
  - body: `{ "red": 255, "green": 170, "blue": 70 }`
- `POST /api/lights/clear`

### 8.4 Action responses

All POST endpoints should return a structured response:

```json
{
  "ok": true,
  "action_id": "startup",
  "state": "running",
  "message": "Startup choreography started."
}
```

If rejected because another action is running:

```json
{
  "ok": false,
  "error": "busy",
  "message": "Another action is already running.",
  "active_action": "play:curious"
}
```

## 9. Action Execution Model

This section is intentionally strict because demo reliability matters more than raw flexibility.

### 9.1 Single executor

Only one operator action may execute at a time.

- if an action is running, all other action buttons become disabled
- the API rejects overlapping action requests with `busy`
- the UI shows which action is currently running

### 9.2 Button states

Each control button should expose one of:

- `enabled`
- `running`
- `disabled`
- `error`

The button label should reflect the live state where useful, for example `Starting...`, `Playing curious...`, or `Busy`.

### 9.3 Motion safety rule

Every motion-triggered action should end in a stable known state before the next action is allowed.

For v1, the rule is:

- after `startup`, the lamp ends at `home_safe`
- after `play`, the lamp returns to `home_safe`
- after `stop`, the lamp returns to `home_safe`
- after `shutdown_pose`, the lamp ends at the designed shutdown pose and remains there

This rule is mandatory even if a future motion recording contains expressive overshoot or flourish.

### 9.4 Queueing

There is no action queue in v1. The server executes zero or one action at a time. Re-clicking a busy button should not stack additional actions, and the UI should not invent queued work.

### 9.5 Failure behavior

If an action fails:

- mark the relevant state section as `error` or `warning`
- surface a readable message in `errors`
- unlock the UI once the runtime is back in a stable state or the failure has been finalized
- do not silently claim success

## 10. UI Layout

The UI is a single responsive page with four functional regions.

### 10.1 Top status bar

Shows:

- connection status
- overall system status
- current action
- last state update timestamp

This area should remain glanceable from across the room during demos.

### 10.2 Main control area

Primary operator controls:

- startup
- play selected motion
- shutdown pose
- stop / return safe
- solid light preset
- clear light

Controls should visually communicate which ones are safe to press right now.

### 10.3 Realtime state area

Shows live normalized state in readable cards:

- system
- motion
- light
- audio

This section should make it obvious whether the lamp is ready, moving, in shutdown pose, or degraded.

### 10.4 Diagnostics area

Shows:

- error feed
- hardware notes
- connectivity hints
- available recordings
- configuration snippets useful during demo setup

## 11. Responsive Behavior

The same page must work in three display modes:

- phone portrait
- laptop/tablet landscape
- Raspberry Pi fullscreen kiosk

Rules:

- stack panels vertically on narrow screens
- retain large, readable control buttons on touch devices
- avoid hover-only affordances
- keep critical status visible without scrolling on common Pi display sizes when possible

## 12. Visual System

The dashboard should adapt the design language from `FluxChi/reference/harward-gesture/web/`, not copy it blindly.

### 12.1 Visual principles to preserve

- dark layered background with atmospheric gradients
- `JetBrains Mono` typography
- rounded translucent panels
- strong topbar hierarchy
- pill-shaped status indicators
- restrained but expressive semantic colors

### 12.2 LeLamp-specific adaptation

LeLamp should feel warmer and more alive than the reference dashboard.

Use these semantic directions:

- healthy/ready: green
- warning/transition: yellow
- error/stop: red
- LeLamp motion/light highlight: warm amber

Warm amber should become the lamp's signature accent for startup, wakeup, motion selection, and light-related emphasis. The result should feel like a robotic lamp control surface, not a generic ops console.

### 12.3 Visual tone

Priorities:

1. readable in live demos
2. intentional and polished
3. slightly playful, never toy-like

## 13. Honest Degradation

The dashboard must prefer truthful degraded output over invented certainty.

Examples:

- if motor connectivity cannot be sampled, show `unknown`
- if audio volume cannot be read, show `unknown`
- if the last action result is uncertain, show `warning` plus the best known explanation

The UI should distinguish between:

- healthy
- busy
- warning
- error
- unknown

## 14. Suggested Runtime Integration

Implementation should stay compatible with current runtime patterns in `lelamp_runtime`:

- existing motion control through the animation/motor stack
- existing RGB service behavior
- existing `remote_control` and runtime settings concepts

The dashboard should introduce a new operator-facing layer, not fork business logic into the frontend.

## 15. Testing and Verification

The implementation plan should include tests for:

- state store merge behavior
- sampler-to-store updates
- action executor busy locking
- action API success and busy responses
- serialization of `unknown` values
- frontend polling behavior with mocked API responses
- responsive rendering sanity for phone and Pi fullscreen

Manual verification should include:

- Pi local browser opens and polls correctly
- same-network phone can load and control the lamp
- startup, play, stop, shutdown pose all reflect correct button locking
- light actions reflect real state changes
- action completion returns to `home_safe` where required

## 16. Extension Points

The design should make the following future additions straightforward:

- new motion groups or categorized recordings
- richer light presets and animated light effects
- more detailed hardware health cards
- optional auth if the dashboard later leaves trusted local environments
- optional event push transport if polling later becomes insufficient

## 17. Open Implementation Constraints

These are fixed constraints for planning, not open questions:

- local-only first
- polling first
- one action executor at a time
- explicit return to `home_safe` between motion actions
- Pi screen and phone access both required
- UI and API served from one small Python service
- modular files with clear ownership

## 18. Outcome

The v1 dashboard should feel like a clean local mission console for LeLamp: easy to open, easy to understand, safe to operate during a demo, and structured so future motions and hardware details can be added without turning the runtime into a monolith.
