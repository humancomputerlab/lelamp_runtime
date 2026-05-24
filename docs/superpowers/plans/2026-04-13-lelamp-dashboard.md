# LeLamp Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local FastAPI dashboard on the Raspberry Pi that shows live LeLamp state and safely controls motion and light actions from the Pi screen or same-network devices.

**Architecture:** Add a focused `lelamp.dashboard` subsystem inside `lelamp_runtime`. FastAPI serves the HTML/CSS/JS dashboard and JSON endpoints, background samplers feed a single `DashboardStateStore`, and a serialized `DashboardActionExecutor` routes all operator commands through a `DashboardRuntimeBridge` that reuses the existing motion and RGB stack.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, unittest, vanilla HTML/CSS/JS, existing `RuntimeSettings`, `AnimationService`, `RGBService`, and `remote_control` helpers.

---

## File Map

- Modify: `pyproject.toml`
  - add dashboard runtime dependencies and test-only extras
- Modify: `lelamp/runtime_config.py`
  - add dashboard host, port, and poll interval settings
- Create: `lelamp/dashboard/__init__.py`
  - expose the dashboard package
- Create: `lelamp/dashboard/state_store.py`
  - normalized in-memory dashboard state
- Create: `lelamp/dashboard/runtime_bridge.py`
  - typed adapter over motion and RGB runtime behavior
- Create: `lelamp/dashboard/actions/__init__.py`
  - export action helpers
- Create: `lelamp/dashboard/actions/executor.py`
  - one-at-a-time action executor with background worker threads
- Create: `lelamp/dashboard/actions/motion.py`
  - motion action registration
- Create: `lelamp/dashboard/actions/lights.py`
  - light action registration
- Create: `lelamp/dashboard/samplers/__init__.py`
  - export sampler helpers
- Create: `lelamp/dashboard/samplers/runtime.py`
  - background polling loop and runtime snapshot
- Create: `lelamp/dashboard/samplers/motors.py`
  - motor availability and recording snapshot
- Create: `lelamp/dashboard/samplers/audio.py`
  - best-effort audio snapshot with honest `unknown`
- Create: `lelamp/dashboard/samplers/network.py`
  - reachable local URLs for Pi/LAN/hotspot
- Create: `lelamp/dashboard/api.py`
  - FastAPI app, static asset serving, and JSON endpoints
- Create: `lelamp/dashboard/web/index.html`
  - dashboard markup and panel layout
- Create: `lelamp/dashboard/web/dashboard.css`
  - harward-gesture-inspired dashboard styles adapted to LeLamp
- Create: `lelamp/dashboard/web/dashboard.js`
  - polling client, state renderer, and action button locking
- Create: `lelamp/test/test_dashboard_runtime_config.py`
  - runtime config defaults for dashboard settings
- Create: `lelamp/test/test_dashboard_state_store.py`
  - state store behavior
- Create: `lelamp/test/test_dashboard_runtime_bridge.py`
  - bridge contract tests
- Create: `lelamp/test/test_dashboard_actions.py`
  - executor serialization and action state transitions
- Create: `lelamp/test/test_dashboard_samplers.py`
  - sampler output and honest `unknown` behavior
- Create: `lelamp/test/test_dashboard_api.py`
  - API endpoints and HTTP responses
- Create: `lelamp/test/test_dashboard_web.py`
  - dashboard asset contract and polling behavior
- Modify: `README.md`
  - local dashboard launch and access instructions
- Modify: `.env.example`
  - dashboard configuration examples

### Task 1: Add Dashboard Runtime Settings And Dependencies

**Files:**
- Modify: `pyproject.toml`
- Modify: `lelamp/runtime_config.py`
- Create: `lelamp/test/test_dashboard_runtime_config.py`

- [ ] **Step 1: Write the failing dashboard runtime settings test**

```python
import os
import unittest
from unittest.mock import patch

from lelamp.runtime_config import load_runtime_settings


class DashboardRuntimeConfigTests(unittest.TestCase):
    def test_load_runtime_settings_includes_dashboard_defaults(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = load_runtime_settings()

        self.assertEqual(settings.dashboard_host, "0.0.0.0")
        self.assertEqual(settings.dashboard_port, 8765)
        self.assertEqual(settings.dashboard_poll_ms, 400)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the new test to verify it fails**

Run: `uv run python -m unittest lelamp.test.test_dashboard_runtime_config -v`
Expected: FAIL with `AttributeError: 'RuntimeSettings' object has no attribute 'dashboard_host'`

- [ ] **Step 3: Add FastAPI/Uvicorn dependencies and dashboard settings**

```toml
[project]
dependencies = [
    "feetech-servo-sdk>=1.0.0",
    "fastapi>=0.115,<1.0",
    "lerobot>=0.3.3",
    "livekit-agents[openai]~=1.2",
    "livekit-plugins-noise-cancellation~=0.2",
    "numpy>=2.2.6",
    "pvporcupine>=3.0.5",
    "pvrecorder>=1.2.7",
    "pyarrow==20.0.0",
    "pyaudio>=0.2.14",
    "python-dotenv",
    "sounddevice>=0.5.2",
    "uvicorn[standard]>=0.34,<1.0",
]

[project.optional-dependencies]
hardware = [
    "adafruit-circuitpython-neopixel",
    "rpi-ws281x",
]
dev = [
    "httpx>=0.28,<1.0",
    "js2py>=0.74,<1.0",
]
```

```python
@dataclass(frozen=True)
class RuntimeSettings:
    port: str
    lamp_id: str
    fps: int
    model_provider: str
    model_api_key: str | None
    model_base_url: str | None
    model_name: str | None
    model_voice: str
    led_count: int
    led_pin: int
    led_freq_hz: int
    led_dma: int
    led_brightness: int
    led_invert: bool
    led_channel: int
    enable_rgb: bool
    startup_volume: int
    startup_recording: str
    idle_recording: str
    home_recording: str
    use_home_pose_relative: bool
    interpolation_duration: float
    audio_user: str
    dashboard_host: str
    dashboard_port: int
    dashboard_poll_ms: int


def load_runtime_settings() -> RuntimeSettings:
    model_provider = _get_model_provider()
    idle_recording = _get_str("LELAMP_IDLE_RECORDING", "idle")

    return RuntimeSettings(
        port=_get_str("LELAMP_PORT", "/dev/ttyACM0"),
        lamp_id=_get_str("LELAMP_ID", "lelamp"),
        fps=_get_int("LELAMP_FPS", 30),
        model_provider=model_provider,
        model_api_key=_get_model_api_key(),
        model_base_url=_get_optional_str("MODEL_BASE_URL") or _default_model_base_url(model_provider),
        model_name=_get_optional_str("MODEL_NAME") or _default_model_name(model_provider),
        model_voice=_get_str("MODEL_VOICE", _default_model_voice(model_provider)),
        led_count=_get_int("LELAMP_LED_COUNT", 40),
        led_pin=_get_int("LELAMP_LED_PIN", 12),
        led_freq_hz=_get_int("LELAMP_LED_FREQ_HZ", 800000),
        led_dma=_get_int("LELAMP_LED_DMA", 10),
        led_brightness=_get_int("LELAMP_LED_BRIGHTNESS", 255),
        led_invert=_get_bool("LELAMP_LED_INVERT", False),
        led_channel=_get_int("LELAMP_LED_CHANNEL", 0),
        enable_rgb=_get_bool("LELAMP_ENABLE_RGB", True),
        startup_volume=_get_int("LELAMP_STARTUP_VOLUME", 100),
        startup_recording=_get_str("LELAMP_STARTUP_RECORDING", "wake_up"),
        idle_recording=idle_recording,
        home_recording=_get_str("LELAMP_HOME_RECORDING", idle_recording),
        use_home_pose_relative=_get_bool("LELAMP_USE_HOME_POSE_RELATIVE", False),
        interpolation_duration=_get_float("LELAMP_INTERPOLATION_DURATION", 3.0),
        audio_user=_get_str("LELAMP_AUDIO_USER", os.getenv("SUDO_USER") or os.getenv("USER") or "pi"),
        dashboard_host=_get_str("LELAMP_DASHBOARD_HOST", "0.0.0.0"),
        dashboard_port=_get_int("LELAMP_DASHBOARD_PORT", 8765),
        dashboard_poll_ms=_get_int("LELAMP_DASHBOARD_POLL_MS", 400),
    )
```

- [ ] **Step 4: Run the runtime config tests**

Run: `uv run python -m unittest lelamp.test.test_runtime_config lelamp.test.test_dashboard_runtime_config -v`
Expected: PASS with both test modules green

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml lelamp/runtime_config.py lelamp/test/test_dashboard_runtime_config.py
git commit -m "feat: add dashboard runtime settings"
```

### Task 2: Implement The Dashboard State Store

**Files:**
- Create: `lelamp/dashboard/__init__.py`
- Create: `lelamp/dashboard/state_store.py`
- Create: `lelamp/test/test_dashboard_state_store.py`

- [ ] **Step 1: Write the failing state store tests**

```python
import unittest

from lelamp.dashboard.state_store import DashboardStateStore


class DashboardStateStoreTests(unittest.TestCase):
    def test_snapshot_starts_with_unknown_sections(self) -> None:
        store = DashboardStateStore()
        snapshot = store.snapshot()

        self.assertEqual(snapshot["system"]["status"], "unknown")
        self.assertEqual(snapshot["motion"]["status"], "unknown")
        self.assertEqual(snapshot["light"]["status"], "unknown")
        self.assertEqual(snapshot["audio"]["status"], "unknown")
        self.assertEqual(snapshot["errors"], [])

    def test_patch_merges_nested_state_and_updates_timestamp(self) -> None:
        store = DashboardStateStore()
        first_update = store.snapshot()["system"]["last_update_ms"]

        snapshot = store.patch("motion", {"status": "idle", "current_recording": "home_safe"})

        self.assertEqual(snapshot["motion"]["status"], "idle")
        self.assertEqual(snapshot["motion"]["current_recording"], "home_safe")
        self.assertGreaterEqual(snapshot["system"]["last_update_ms"], first_update)

    def test_record_error_deduplicates_by_code_and_source(self) -> None:
        store = DashboardStateStore()

        store.record_error("motor.read_failed", "read timeout", "motors", "warning")
        snapshot = store.record_error("motor.read_failed", "read timeout", "motors", "warning")

        self.assertEqual(len(snapshot["errors"]), 1)
        self.assertTrue(snapshot["errors"][0]["active"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the new state store tests**

Run: `uv run python -m unittest lelamp.test.test_dashboard_state_store -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lelamp.dashboard'`

- [ ] **Step 3: Implement the package and in-memory store**

```python
# lelamp/dashboard/__init__.py
"""Local operator dashboard for LeLamp."""
```

```python
from __future__ import annotations

from copy import deepcopy
from threading import RLock
import time


def _now_ms() -> int:
    return int(time.time() * 1000)


DEFAULT_STATE = {
    "system": {
        "status": "unknown",
        "active_action": None,
        "last_update_ms": 0,
        "uptime_s": 0,
        "server_started_at": 0,
        "reachable_urls": [],
    },
    "motion": {
        "status": "unknown",
        "current_recording": None,
        "last_completed_recording": None,
        "home_recording": None,
        "startup_recording": None,
        "last_result": None,
        "motors_connected": "unknown",
        "calibration_state": "unknown",
        "available_recordings": [],
    },
    "light": {
        "status": "unknown",
        "color": None,
        "effect": None,
        "brightness": None,
        "last_result": None,
    },
    "audio": {
        "status": "unknown",
        "output_device": None,
        "volume_percent": None,
        "last_result": None,
    },
    "errors": [],
}


class DashboardStateStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._state = deepcopy(DEFAULT_STATE)
        self._state["system"]["last_update_ms"] = _now_ms()

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return deepcopy(self._state)

    def patch(self, section: str, values: dict[str, object]) -> dict[str, object]:
        with self._lock:
            self._state[section].update(values)
            self._state["system"]["last_update_ms"] = _now_ms()
            return deepcopy(self._state)

    def set_system(self, **values: object) -> dict[str, object]:
        return self.patch("system", values)

    def record_error(self, code: str, message: str, source: str, severity: str) -> dict[str, object]:
        with self._lock:
            now_ms = _now_ms()
            for item in self._state["errors"]:
                if item["code"] == code and item["source"] == source:
                    item["message"] = message
                    item["severity"] = severity
                    item["last_seen_ms"] = now_ms
                    item["active"] = True
                    self._state["system"]["last_update_ms"] = now_ms
                    return deepcopy(self._state)

            self._state["errors"].insert(
                0,
                {
                    "code": code,
                    "message": message,
                    "source": source,
                    "severity": severity,
                    "first_seen_ms": now_ms,
                    "last_seen_ms": now_ms,
                    "active": True,
                },
            )
            self._state["system"]["last_update_ms"] = now_ms
            return deepcopy(self._state)
```

- [ ] **Step 4: Run the state store tests**

Run: `uv run python -m unittest lelamp.test.test_dashboard_state_store -v`
Expected: PASS with 3 tests green

- [ ] **Step 5: Commit**

```bash
git add lelamp/dashboard/__init__.py lelamp/dashboard/state_store.py lelamp/test/test_dashboard_state_store.py
git commit -m "feat: add dashboard state store"
```

### Task 3: Implement The Runtime Bridge

**Files:**
- Create: `lelamp/dashboard/runtime_bridge.py`
- Create: `lelamp/test/test_dashboard_runtime_bridge.py`

- [ ] **Step 1: Write the failing runtime bridge tests**

```python
import unittest
from types import SimpleNamespace

from lelamp.dashboard.runtime_bridge import DashboardRuntimeBridge


class FakeAnimationService:
    instances = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.started = False
        self.dispatched = []
        self.stopped = False
        FakeAnimationService.instances.append(self)

    def get_available_recordings(self) -> list[str]:
        return ["curious", "wake_up"]

    def start(self) -> None:
        self.started = True

    def dispatch(self, event_type: str, payload: str) -> None:
        self.dispatched.append((event_type, payload))

    def wait_until_playback_complete(self, timeout: float | None = None) -> bool:
        return True

    def stop(self) -> None:
        self.stopped = True


class FakeRGBService:
    instances = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.actions = []
        FakeRGBService.instances.append(self)

    def handle_event(self, event_type: str, payload) -> None:
        self.actions.append((event_type, payload))

    def clear(self) -> None:
        self.actions.append(("clear", None))

    def stop(self) -> None:
        self.actions.append(("stop", None))


class DashboardRuntimeBridgeTests(unittest.TestCase):
    def test_play_uses_home_recording_as_idle_target(self) -> None:
        settings = SimpleNamespace(
            port="/dev/ttyACM0",
            lamp_id="lelamp",
            fps=30,
            interpolation_duration=3.0,
            startup_recording="wake_up",
            home_recording="home_safe",
            use_home_pose_relative=True,
            enable_rgb=True,
            led_count=40,
            led_pin=12,
            led_freq_hz=800000,
            led_dma=10,
            led_brightness=255,
            led_invert=False,
            led_channel=0,
        )

        bridge = DashboardRuntimeBridge(
            settings,
            animation_factory=FakeAnimationService,
            rgb_factory=FakeRGBService,
            remote_module=SimpleNamespace(),
        )

        result = bridge.play("curious")

        service = FakeAnimationService.instances[-1]
        self.assertTrue(result.ok)
        self.assertEqual(service.kwargs["idle_recording"], "home_safe")
        self.assertEqual(service.kwargs["home_recording"], "home_safe")
        self.assertEqual(service.dispatched, [("play", "curious")])

    def test_set_light_solid_dispatches_rgb_event(self) -> None:
        settings = SimpleNamespace(
            port="/dev/ttyACM0",
            lamp_id="lelamp",
            fps=30,
            interpolation_duration=3.0,
            startup_recording="wake_up",
            home_recording="home_safe",
            use_home_pose_relative=True,
            enable_rgb=True,
            led_count=40,
            led_pin=12,
            led_freq_hz=800000,
            led_dma=10,
            led_brightness=255,
            led_invert=False,
            led_channel=0,
        )

        bridge = DashboardRuntimeBridge(
            settings,
            animation_factory=FakeAnimationService,
            rgb_factory=FakeRGBService,
            remote_module=SimpleNamespace(),
        )

        result = bridge.set_light_solid((255, 170, 70))

        self.assertTrue(result.ok)
        self.assertEqual(FakeRGBService.instances[-1].actions[0], ("solid", (255, 170, 70)))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the runtime bridge tests**

Run: `uv run python -m unittest lelamp.test.test_dashboard_runtime_bridge -v`
Expected: FAIL with `ModuleNotFoundError` or missing `DashboardRuntimeBridge`

- [ ] **Step 3: Implement the typed bridge over motion and RGB runtime**

```python
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from lelamp import remote_control
from lelamp.service.motors.animation_service import AnimationService
from lelamp.service.rgb.rgb_service import RGBService


@dataclass(frozen=True)
class DashboardActionResult:
    ok: bool
    message: str
    detail: str | None = None


class DashboardRuntimeBridge:
    def __init__(
        self,
        settings,
        *,
        animation_factory=AnimationService,
        rgb_factory=RGBService,
        remote_module=remote_control,
    ) -> None:
        self.settings = settings
        self._animation_factory = animation_factory
        self._rgb_factory = rgb_factory
        self._remote = remote_module

    def list_recordings(self) -> list[str]:
        service = self._animation_factory(
            port=self.settings.port,
            lamp_id=self.settings.lamp_id,
            fps=self.settings.fps,
            duration=self.settings.interpolation_duration,
            idle_recording=self.settings.home_recording,
            home_recording=self.settings.home_recording,
            use_home_pose_relative=self.settings.use_home_pose_relative,
        )
        return service.get_available_recordings()

    def startup(self) -> DashboardActionResult:
        return self._run_remote(
            self._remote._handle_startup,
            recording=self.settings.startup_recording,
            home_recording=self.settings.home_recording,
            settle_frames=self._remote.DEFAULT_STARTUP_SETTLE_FRAMES,
            settle_hold_frames=self._remote.DEFAULT_STARTUP_HOLD_FRAMES,
            settle_fps=self._remote.DEFAULT_STARTUP_FPS,
            wake_fps=self._remote.DEFAULT_WAKE_FPS,
            post_wake_hold=self._remote.DEFAULT_POST_WAKE_HOLD_SECONDS,
        )

    def play(self, recording_name: str) -> DashboardActionResult:
        service = self._animation_factory(
            port=self.settings.port,
            lamp_id=self.settings.lamp_id,
            fps=self.settings.fps,
            duration=self.settings.interpolation_duration,
            idle_recording=self.settings.home_recording,
            home_recording=self.settings.home_recording,
            use_home_pose_relative=self.settings.use_home_pose_relative,
        )
        service.start()
        try:
            service.dispatch("play", recording_name)
            if not service.wait_until_playback_complete(timeout=120.0):
                return DashboardActionResult(False, f"Playback timed out: {recording_name}")
            return DashboardActionResult(True, f"Finished recording: {recording_name}")
        finally:
            service.stop()

    def shutdown_pose(self) -> DashboardActionResult:
        return self._run_remote(
            self._remote._handle_shutdown,
            recording="power_off",
            prepare_fraction=self._remote.DEFAULT_SHUTDOWN_PREPARE_FRACTION,
            prepare_frames=self._remote.DEFAULT_SHUTDOWN_PREPARE_FRAMES,
            settle_frames=self._remote.DEFAULT_SHUTDOWN_SETTLE_FRAMES,
            hold_frames=self._remote.DEFAULT_SHUTDOWN_HOLD_FRAMES,
            final_hold=self._remote.DEFAULT_SHUTDOWN_FINAL_HOLD_SECONDS,
            release_pause=self._remote.DEFAULT_RELEASE_PAUSE_SECONDS,
            keep_led_on=False,
        )

    def stop(self) -> DashboardActionResult:
        return self.play(self.settings.home_recording)

    def set_light_solid(self, rgb: tuple[int, int, int]) -> DashboardActionResult:
        service = self._rgb_factory(
            led_count=self.settings.led_count,
            led_pin=self.settings.led_pin,
            led_freq_hz=self.settings.led_freq_hz,
            led_dma=self.settings.led_dma,
            led_brightness=self.settings.led_brightness,
            led_invert=self.settings.led_invert,
            led_channel=self.settings.led_channel,
        )
        service.handle_event("solid", rgb)
        service.stop()
        return DashboardActionResult(True, f"Set RGB to {rgb}")

    def clear_light(self) -> DashboardActionResult:
        service = self._rgb_factory(
            led_count=self.settings.led_count,
            led_pin=self.settings.led_pin,
            led_freq_hz=self.settings.led_freq_hz,
            led_dma=self.settings.led_dma,
            led_brightness=self.settings.led_brightness,
            led_invert=self.settings.led_invert,
            led_channel=self.settings.led_channel,
        )
        service.clear()
        service.stop()
        return DashboardActionResult(True, "Cleared RGB LEDs")

    def _run_remote(self, handler, **overrides) -> DashboardActionResult:
        args = SimpleNamespace(
            id=self.settings.lamp_id,
            port=self.settings.port,
            fps=self.settings.fps,
            enable_rgb=self.settings.enable_rgb,
            led_count=self.settings.led_count,
            led_pin=self.settings.led_pin,
            led_freq_hz=self.settings.led_freq_hz,
            led_dma=self.settings.led_dma,
            led_brightness=self.settings.led_brightness,
            led_invert=self.settings.led_invert,
            led_channel=self.settings.led_channel,
            **overrides,
        )
        exit_code = handler(args)
        if exit_code != 0:
            return DashboardActionResult(False, f"Action failed with exit code {exit_code}")
        return DashboardActionResult(True, "Action completed")
```

- [ ] **Step 4: Run the runtime bridge tests**

Run: `uv run python -m unittest lelamp.test.test_dashboard_runtime_bridge -v`
Expected: PASS with both bridge tests green

- [ ] **Step 5: Commit**

```bash
git add lelamp/dashboard/runtime_bridge.py lelamp/test/test_dashboard_runtime_bridge.py
git commit -m "feat: add dashboard runtime bridge"
```

### Task 4: Implement Serialized Dashboard Actions

**Files:**
- Create: `lelamp/dashboard/actions/__init__.py`
- Create: `lelamp/dashboard/actions/executor.py`
- Create: `lelamp/dashboard/actions/motion.py`
- Create: `lelamp/dashboard/actions/lights.py`
- Create: `lelamp/test/test_dashboard_actions.py`

- [ ] **Step 1: Write the failing action executor tests**

```python
import threading
import time
import unittest

from lelamp.dashboard.actions.executor import DashboardActionExecutor
from lelamp.dashboard.runtime_bridge import DashboardActionResult
from lelamp.dashboard.state_store import DashboardStateStore


class DashboardActionExecutorTests(unittest.TestCase):
    def test_submit_rejects_overlapping_actions(self) -> None:
        store = DashboardStateStore()
        gate = threading.Event()

        def slow_action() -> DashboardActionResult:
            gate.wait(timeout=1.0)
            return DashboardActionResult(True, "done")

        executor = DashboardActionExecutor(store)

        first = executor.submit("startup", slow_action, section="motion", success_patch={"status": "idle"})
        second = executor.submit("shutdown_pose", slow_action, section="motion", success_patch={"status": "idle"})

        self.assertTrue(first.ok)
        self.assertFalse(second.ok)
        self.assertEqual(second.error, "busy")
        gate.set()
        executor.wait_for_idle(timeout=1.0)

    def test_submit_updates_store_during_and_after_action(self) -> None:
        store = DashboardStateStore()
        executor = DashboardActionExecutor(store)

        receipt = executor.submit(
            "clear_light",
            lambda: DashboardActionResult(True, "cleared"),
            section="light",
            success_patch={"status": "off", "color": None, "last_result": "cleared"},
        )
        executor.wait_for_idle(timeout=1.0)

        snapshot = store.snapshot()
        self.assertTrue(receipt.ok)
        self.assertEqual(snapshot["system"]["status"], "ready")
        self.assertEqual(snapshot["system"]["active_action"], None)
        self.assertEqual(snapshot["light"]["status"], "off")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the failing action executor tests**

Run: `uv run python -m unittest lelamp.test.test_dashboard_actions -v`
Expected: FAIL with `ModuleNotFoundError` for `lelamp.dashboard.actions`

- [ ] **Step 3: Implement the action executor and action maps**

```python
# lelamp/dashboard/actions/__init__.py
from .executor import DashboardActionExecutor, DashboardActionReceipt
from .lights import build_light_actions
from .motion import build_motion_actions
```

```python
from __future__ import annotations

from dataclasses import dataclass
from threading import Lock, Thread

from lelamp.dashboard.runtime_bridge import DashboardActionResult


@dataclass(frozen=True)
class DashboardActionReceipt:
    ok: bool
    action_id: str
    state: str
    message: str
    error: str | None = None


class DashboardActionExecutor:
    def __init__(self, store) -> None:
        self._store = store
        self._lock = Lock()
        self._worker: Thread | None = None
        self._active_action: str | None = None

    def submit(self, action_id: str, callback, *, section: str, success_patch: dict[str, object]) -> DashboardActionReceipt:
        with self._lock:
            if self._worker and self._worker.is_alive():
                return DashboardActionReceipt(False, action_id, "busy", "Another action is already running.", error="busy")

            self._active_action = action_id
            self._store.set_system(status="running", active_action=action_id)
            self._store.patch(section, {"status": "running", "last_result": None})

            self._worker = Thread(
                target=self._run_action,
                args=(action_id, callback, section, success_patch),
                daemon=True,
            )
            self._worker.start()
            return DashboardActionReceipt(True, action_id, "running", f"{action_id} started.")

    def current_action(self) -> str | None:
        with self._lock:
            return self._active_action

    def is_busy(self) -> bool:
        with self._lock:
            return bool(self._worker and self._worker.is_alive())

    def wait_for_idle(self, timeout: float | None = None) -> bool:
        worker = self._worker
        if not worker:
            return True
        worker.join(timeout=timeout)
        return not worker.is_alive()

    def _run_action(self, action_id: str, callback, section: str, success_patch: dict[str, object]) -> None:
        try:
            result: DashboardActionResult = callback()
            if result.ok:
                self._store.patch(section, dict(success_patch, last_result=result.message))
                self._store.set_system(status="ready", active_action=None)
            else:
                self._store.patch(section, {"status": "error", "last_result": result.message})
                self._store.record_error(f"action.{action_id}", result.message, section, "error")
                self._store.set_system(status="error", active_action=None)
        except Exception as exc:
            self._store.patch(section, {"status": "error", "last_result": str(exc)})
            self._store.record_error(f"action.{action_id}", str(exc), section, "error")
            self._store.set_system(status="error", active_action=None)
        finally:
            with self._lock:
                self._active_action = None
```

```python
# lelamp/dashboard/actions/motion.py
def build_motion_actions(executor, bridge) -> dict[str, object]:
    return {
        "startup": lambda: executor.submit(
            "startup",
            bridge.startup,
            section="motion",
            success_patch={"status": "idle", "current_recording": bridge.settings.home_recording},
        ),
        "play": lambda name: executor.submit(
            f"play:{name}",
            lambda: bridge.play(name),
            section="motion",
            success_patch={
                "status": "idle",
                "current_recording": bridge.settings.home_recording,
                "last_completed_recording": name,
            },
        ),
        "shutdown_pose": lambda: executor.submit(
            "shutdown_pose",
            bridge.shutdown_pose,
            section="motion",
            success_patch={"status": "idle", "current_recording": "power_off"},
        ),
        "stop": lambda: executor.submit(
            "stop",
            bridge.stop,
            section="motion",
            success_patch={"status": "idle", "current_recording": bridge.settings.home_recording},
        ),
    }
```

```python
# lelamp/dashboard/actions/lights.py
def build_light_actions(executor, bridge) -> dict[str, object]:
    return {
        "solid": lambda red, green, blue: executor.submit(
            "light:solid",
            lambda: bridge.set_light_solid((red, green, blue)),
            section="light",
            success_patch={"status": "solid", "color": {"red": red, "green": green, "blue": blue}},
        ),
        "clear": lambda: executor.submit(
            "light:clear",
            bridge.clear_light,
            section="light",
            success_patch={"status": "off", "color": None},
        ),
    }
```

- [ ] **Step 4: Run the action executor tests**

Run: `uv run python -m unittest lelamp.test.test_dashboard_actions -v`
Expected: PASS with executor busy-lock and state-update coverage green

- [ ] **Step 5: Commit**

```bash
git add lelamp/dashboard/actions/__init__.py lelamp/dashboard/actions/executor.py lelamp/dashboard/actions/motion.py lelamp/dashboard/actions/lights.py lelamp/test/test_dashboard_actions.py
git commit -m "feat: add serialized dashboard actions"
```

### Task 5: Implement Samplers And Reachable URL Discovery

**Files:**
- Create: `lelamp/dashboard/samplers/__init__.py`
- Create: `lelamp/dashboard/samplers/runtime.py`
- Create: `lelamp/dashboard/samplers/motors.py`
- Create: `lelamp/dashboard/samplers/audio.py`
- Create: `lelamp/dashboard/samplers/network.py`
- Create: `lelamp/test/test_dashboard_samplers.py`

- [ ] **Step 1: Write the failing sampler tests**

```python
import unittest
from types import SimpleNamespace

from lelamp.dashboard.samplers.audio import collect_audio_snapshot
from lelamp.dashboard.samplers.motors import collect_motor_snapshot
from lelamp.dashboard.samplers.network import build_reachable_urls


class DashboardSamplerTests(unittest.TestCase):
    def test_build_reachable_urls_includes_loopback_and_local_ips(self) -> None:
        urls = build_reachable_urls("0.0.0.0", 8765, ip_list=["192.168.0.15", "172.20.10.3"])

        self.assertIn("http://127.0.0.1:8765", urls)
        self.assertIn("http://192.168.0.15:8765", urls)
        self.assertIn("http://172.20.10.3:8765", urls)

    def test_collect_audio_snapshot_returns_unknown_when_probe_fails(self) -> None:
        snapshot = collect_audio_snapshot(
            SimpleNamespace(audio_user="pi"),
            run_command=lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("amixer")),
        )

        self.assertEqual(snapshot["status"], "unknown")
        self.assertEqual(snapshot["volume_percent"], None)

    def test_collect_motor_snapshot_marks_missing_port(self) -> None:
        settings = SimpleNamespace(port="/dev/tty.missing", home_recording="home_safe", startup_recording="wake_up")
        bridge = SimpleNamespace(list_recordings=lambda: ["curious", "wake_up"])

        snapshot = collect_motor_snapshot(settings, bridge, path_exists=lambda _path: False)

        self.assertEqual(snapshot["motors_connected"], False)
        self.assertEqual(snapshot["home_recording"], "home_safe")
        self.assertEqual(snapshot["available_recordings"], ["curious", "wake_up"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the failing sampler tests**

Run: `uv run python -m unittest lelamp.test.test_dashboard_samplers -v`
Expected: FAIL with missing sampler modules

- [ ] **Step 3: Implement the sampler helpers**

```python
# lelamp/dashboard/samplers/__init__.py
from .audio import collect_audio_snapshot
from .motors import collect_motor_snapshot
from .network import build_reachable_urls
from .runtime import DashboardSamplerLoop, collect_runtime_snapshot
```

```python
# lelamp/dashboard/samplers/network.py
from __future__ import annotations

import socket


def build_reachable_urls(host: str, port: int, *, ip_list: list[str] | None = None) -> list[str]:
    urls = [f"http://127.0.0.1:{port}"]
    candidates = ip_list or _local_ipv4_addresses()

    for ip_addr in candidates:
        urls.append(f"http://{ip_addr}:{port}")

    if host not in {"0.0.0.0", "::"}:
        urls.insert(0, f"http://{host}:{port}")

    deduped = []
    for url in urls:
        if url not in deduped:
            deduped.append(url)
    return deduped


def _local_ipv4_addresses() -> list[str]:
    names = socket.gethostbyname_ex(socket.gethostname())[2]
    return [name for name in names if "." in name and not name.startswith("127.")]
```

```python
# lelamp/dashboard/samplers/audio.py
from __future__ import annotations

import re
import subprocess


def collect_audio_snapshot(settings, *, run_command=subprocess.run) -> dict[str, object]:
    try:
        result = run_command(
            ["sudo", "-u", settings.audio_user, "amixer", "sget", "Line"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return {
            "status": "unknown",
            "output_device": "Line",
            "volume_percent": None,
            "last_result": "amixer unavailable",
        }

    match = re.search(r"\\[(\\d{1,3})%\\]", result.stdout)
    if not match:
        return {
            "status": "unknown",
            "output_device": "Line",
            "volume_percent": None,
            "last_result": "volume parse failed",
        }

    volume = int(match.group(1))
    return {
        "status": "ready" if volume > 0 else "muted",
        "output_device": "Line",
        "volume_percent": volume,
        "last_result": "sampled from amixer",
    }
```

```python
# lelamp/dashboard/samplers/motors.py
from __future__ import annotations

from pathlib import Path


def collect_motor_snapshot(settings, bridge, *, path_exists=Path.exists) -> dict[str, object]:
    port_path = Path(settings.port)
    try:
        recordings = bridge.list_recordings()
    except Exception:
        recordings = []

    return {
        "status": "idle" if path_exists(port_path) else "warning",
        "current_recording": None,
        "last_completed_recording": None,
        "home_recording": settings.home_recording,
        "startup_recording": settings.startup_recording,
        "last_result": None,
        "motors_connected": path_exists(port_path),
        "calibration_state": "unknown",
        "available_recordings": recordings,
    }
```

```python
# lelamp/dashboard/samplers/runtime.py
from __future__ import annotations

from threading import Event, Thread
import time

from .audio import collect_audio_snapshot
from .motors import collect_motor_snapshot
from .network import build_reachable_urls


def collect_runtime_snapshot(settings, executor, started_at: float) -> dict[str, object]:
    return {
        "status": "running" if executor.is_busy() else "ready",
        "active_action": executor.current_action(),
        "uptime_s": int(time.time() - started_at),
        "server_started_at": int(started_at * 1000),
        "reachable_urls": build_reachable_urls(settings.dashboard_host, settings.dashboard_port),
    }


class DashboardSamplerLoop:
    def __init__(self, store, settings, bridge, executor) -> None:
        self._store = store
        self._settings = settings
        self._bridge = bridge
        self._executor = executor
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._started_at = time.time()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        interval = max(self._settings.dashboard_poll_ms / 1000.0, 0.2)
        while not self._stop_event.is_set():
            self._store.patch("system", collect_runtime_snapshot(self._settings, self._executor, self._started_at))
            self._store.patch("motion", collect_motor_snapshot(self._settings, self._bridge))
            self._store.patch("audio", collect_audio_snapshot(self._settings))
            self._stop_event.wait(interval)
```

- [ ] **Step 4: Run the sampler tests**

Run: `uv run python -m unittest lelamp.test.test_dashboard_samplers -v`
Expected: PASS with reachable URLs, audio unknown fallback, and motor snapshot coverage green

- [ ] **Step 5: Commit**

```bash
git add lelamp/dashboard/samplers/__init__.py lelamp/dashboard/samplers/runtime.py lelamp/dashboard/samplers/motors.py lelamp/dashboard/samplers/audio.py lelamp/dashboard/samplers/network.py lelamp/test/test_dashboard_samplers.py
git commit -m "feat: add dashboard samplers"
```

### Task 6: Build The FastAPI Dashboard Server

**Files:**
- Create: `lelamp/dashboard/api.py`
- Create: `lelamp/test/test_dashboard_api.py`

- [ ] **Step 1: Write the failing API tests**

```python
import unittest
from types import SimpleNamespace

from fastapi.testclient import TestClient

from lelamp.dashboard.api import create_app
from lelamp.dashboard.runtime_bridge import DashboardActionResult
from lelamp.dashboard.state_store import DashboardStateStore


class FakeExecutor:
    def __init__(self, *, busy: bool = False) -> None:
        self.busy = busy
        self.active = None

    def is_busy(self) -> bool:
        return self.busy

    def current_action(self) -> str | None:
        return self.active

    def submit(self, action_id, callback, *, section, success_patch):
        if self.busy:
            return SimpleNamespace(ok=False, action_id=action_id, state="busy", message="Another action is already running.", error="busy")
        self.active = action_id
        return SimpleNamespace(ok=True, action_id=action_id, state="running", message=f"{action_id} started.", error=None)


class FakeBridge:
    settings = SimpleNamespace(home_recording="home_safe")

    def list_recordings(self) -> list[str]:
        return ["curious", "wake_up"]

    def startup(self) -> DashboardActionResult:
        return DashboardActionResult(True, "startup complete")

    def stop(self) -> DashboardActionResult:
        return DashboardActionResult(True, "stopped")

    def shutdown_pose(self) -> DashboardActionResult:
        return DashboardActionResult(True, "shutdown complete")

    def play(self, name: str) -> DashboardActionResult:
        return DashboardActionResult(True, f"played {name}")

    def set_light_solid(self, rgb) -> DashboardActionResult:
        return DashboardActionResult(True, f"rgb {rgb}")

    def clear_light(self) -> DashboardActionResult:
        return DashboardActionResult(True, "cleared")


class DashboardApiTests(unittest.TestCase):
    def test_get_state_returns_snapshot(self) -> None:
        settings = SimpleNamespace(dashboard_host="0.0.0.0", dashboard_port=8765, dashboard_poll_ms=400)
        app = create_app(
            settings=settings,
            store=DashboardStateStore(),
            bridge=FakeBridge(),
            executor=FakeExecutor(),
            enable_background=False,
        )
        client = TestClient(app)

        response = client.get("/api/state")

        self.assertEqual(response.status_code, 200)
        self.assertIn("system", response.json())

    def test_post_startup_returns_running_receipt(self) -> None:
        settings = SimpleNamespace(dashboard_host="0.0.0.0", dashboard_port=8765, dashboard_poll_ms=400)
        app = create_app(
            settings=settings,
            store=DashboardStateStore(),
            bridge=FakeBridge(),
            executor=FakeExecutor(),
            enable_background=False,
        )
        client = TestClient(app)

        response = client.post("/api/actions/startup")

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["action_id"], "startup")

    def test_post_startup_returns_busy_when_executor_rejects(self) -> None:
        settings = SimpleNamespace(dashboard_host="0.0.0.0", dashboard_port=8765, dashboard_poll_ms=400)
        app = create_app(
            settings=settings,
            store=DashboardStateStore(),
            bridge=FakeBridge(),
            executor=FakeExecutor(busy=True),
            enable_background=False,
        )
        client = TestClient(app)

        response = client.post("/api/actions/startup")

        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.json()["ok"])
        self.assertEqual(response.json()["error"], "busy")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the failing API tests**

Run: `uv run --extra dev python -m unittest lelamp.test.test_dashboard_api -v`
Expected: FAIL with missing `create_app` or missing API routes

- [ ] **Step 3: Implement the FastAPI app and endpoints**

```python
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from lelamp.dashboard.actions import build_light_actions, build_motion_actions, DashboardActionExecutor
from lelamp.dashboard.runtime_bridge import DashboardRuntimeBridge
from lelamp.dashboard.samplers.runtime import DashboardSamplerLoop
from lelamp.dashboard.state_store import DashboardStateStore
from lelamp.runtime_config import load_runtime_settings


WEB_DIR = Path(__file__).resolve().parent / "web"


def _receipt_response(receipt) -> JSONResponse:
    status_code = 202
    if not receipt.ok and receipt.error == "busy":
        status_code = 409
    elif not receipt.ok:
        status_code = 500
    return JSONResponse(status_code=status_code, content=receipt.__dict__)


def create_app(*, settings=None, store=None, bridge=None, executor=None, enable_background: bool = True) -> FastAPI:
    settings = settings or load_runtime_settings()
    store = store or DashboardStateStore()
    bridge = bridge or DashboardRuntimeBridge(settings)
    executor = executor or DashboardActionExecutor(store)
    sampler = DashboardSamplerLoop(store, settings, bridge, executor) if enable_background else None

    motion_actions = build_motion_actions(executor, bridge)
    light_actions = build_light_actions(executor, bridge)

    app = FastAPI(title="LeLamp Dashboard")
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.on_event("startup")
    def _startup() -> None:
        if sampler:
            sampler.start()

    @app.on_event("shutdown")
    def _shutdown() -> None:
        if sampler:
            sampler.stop()

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/api/state")
    def get_state() -> dict[str, object]:
        return store.snapshot()

    @app.get("/api/actions")
    def get_actions() -> dict[str, object]:
        return {
            "busy": executor.is_busy(),
            "active_action": executor.current_action(),
            "recordings": bridge.list_recordings(),
            "poll_ms": settings.dashboard_poll_ms,
            "actions": {
                "startup": {"enabled": not executor.is_busy()},
                "play": {"enabled": not executor.is_busy()},
                "stop": {"enabled": not executor.is_busy()},
                "shutdown_pose": {"enabled": not executor.is_busy()},
                "light_solid": {"enabled": not executor.is_busy()},
                "light_clear": {"enabled": not executor.is_busy()},
            },
        }

    @app.post("/api/actions/startup", status_code=202)
    def post_startup():
        return _receipt_response(motion_actions["startup"]())

    @app.post("/api/actions/play", status_code=202)
    def post_play(payload: dict[str, str]) -> dict[str, object]:
        name = payload.get("name")
        if not name:
            raise HTTPException(status_code=400, detail="Missing recording name.")
        return _receipt_response(motion_actions["play"](name))

    @app.post("/api/actions/shutdown_pose", status_code=202)
    def post_shutdown_pose():
        return _receipt_response(motion_actions["shutdown_pose"]())

    @app.post("/api/actions/stop", status_code=202)
    def post_stop():
        return _receipt_response(motion_actions["stop"]())

    @app.post("/api/lights/solid", status_code=202)
    def post_solid(payload: dict[str, int]) -> dict[str, object]:
        return _receipt_response(light_actions["solid"](payload["red"], payload["green"], payload["blue"]))

    @app.post("/api/lights/clear", status_code=202)
    def post_clear():
        return _receipt_response(light_actions["clear"]())

    return app


if __name__ == "__main__":
    settings = load_runtime_settings()
    uvicorn.run(
        "lelamp.dashboard.api:create_app",
        factory=True,
        host=settings.dashboard_host,
        port=settings.dashboard_port,
    )
```

- [ ] **Step 4: Run the API tests**

Run: `uv run --extra dev python -m unittest lelamp.test.test_dashboard_api -v`
Expected: PASS with `GET /api/state` and `POST /api/actions/startup` green

- [ ] **Step 5: Commit**

```bash
git add lelamp/dashboard/api.py lelamp/test/test_dashboard_api.py
git commit -m "feat: add dashboard api"
```

### Task 7: Build The Dashboard UI And Document How To Run It

**Files:**
- Create: `lelamp/dashboard/web/index.html`
- Create: `lelamp/dashboard/web/dashboard.css`
- Create: `lelamp/dashboard/web/dashboard.js`
- Create: `lelamp/test/test_dashboard_web.py`
- Modify: `README.md`
- Modify: `.env.example`

- [ ] **Step 1: Write the failing dashboard asset tests**

```python
import unittest
from pathlib import Path

import js2py


ROOT = Path(__file__).resolve().parents[1] / "dashboard" / "web"


class DashboardWebTests(unittest.TestCase):
    def test_index_contains_primary_control_regions(self) -> None:
        html = (ROOT / "index.html").read_text(encoding="utf-8")

        self.assertIn('id="connectionStatus"', html)
        self.assertIn('id="systemStatus"', html)
        self.assertIn('id="startupButton"', html)
        self.assertIn('id="shutdownPoseButton"', html)
        self.assertIn('id="diagnosticsPanel"', html)
        self.assertIn('id="recordingList"', html)

    def test_dashboard_js_starts_polling_at_400ms(self) -> None:
        source = (ROOT / "dashboard.js").read_text(encoding="utf-8")
        context = js2py.EvalJs({})
        context.execute(
            """
            var pollCalls = [];
            function resolved(value) {
              return {
                then: function (handler) { return resolved(handler(value)); },
                catch: function () { return this; }
              };
            }
            var window = {
              intervalMs: null,
              setInterval: function (fn, ms) { this.intervalMs = ms; },
              addEventListener: function () {}
            };
            var fetch = function (url) {
              pollCalls.push(url);
              if (url === "/api/actions") {
                return resolved({
                  json: function () {
                    return resolved({
                      busy: false,
                      recordings: ["curious"],
                      poll_ms: 400,
                      actions: {
                        startup: { enabled: true },
                        play: { enabled: true },
                        stop: { enabled: true },
                        shutdown_pose: { enabled: true },
                        light_solid: { enabled: true },
                        light_clear: { enabled: true }
                      }
                    });
                  }
                });
              }
              return resolved({
                json: function () {
                  return resolved({
                    system: { status: "ready", active_action: null, reachable_urls: [] },
                    motion: { status: "idle", available_recordings: ["curious"] },
                    light: { status: "off" },
                    audio: { status: "ready" },
                    errors: []
                  });
                }
              });
            };
            var nodes = {};
            var document = {
              getElementById: function (id) {
                if (!nodes[id]) {
                  nodes[id] = {
                    id: id,
                    textContent: "",
                    disabled: false,
                    value: "",
                    innerHTML: "",
                    addEventListener: function () {},
                    appendChild: function (child) { this.value = child.value; }
                  };
                }
                return nodes[id];
              }
            };
            """
        )

        context.execute(source)
        context.DashboardApp.start(context.document, context.window, context.fetch, 400)

        self.assertEqual(context.window.intervalMs, 400)
        self.assertEqual(list(context.pollCalls), ["/api/actions", "/api/state"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the failing dashboard asset tests**

Run: `uv run --extra dev python -m unittest lelamp.test.test_dashboard_web -v`
Expected: FAIL with missing dashboard asset files

- [ ] **Step 3: Build the dashboard HTML, CSS, JS, and docs**

```html
<!-- lelamp/dashboard/web/index.html -->
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <meta name="theme-color" content="#050a10">
  <title>LeLamp Local Dashboard</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700;800&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/static/dashboard.css">
</head>
<body>
  <div class="app-shell">
    <header class="topbar">
      <div>
        <p class="eyebrow">LeLamp</p>
        <h1>Local Dashboard</h1>
        <p class="subtitle">A warm local control surface for startup, motion, lights, and demo diagnostics.</p>
      </div>
      <div class="topbar-meta">
        <div id="connectionStatus" class="conn-pill">offline</div>
        <div id="systemStatus" class="stage-pill">unknown</div>
        <div id="activeAction" class="conn-pill">idle</div>
        <div id="lastUpdateTopbar" class="conn-pill">0</div>
      </div>
    </header>

    <main class="layout">
      <section class="left-column">
        <div class="panel">
          <div class="panel-inner">
            <h2>Main Controls</h2>
            <div class="controls-grid">
              <button id="startupButton" class="primary" type="button">Startup</button>
              <select id="recordingSelect"></select>
              <button id="playButton" type="button">Play Motion</button>
              <button id="stopButton" type="button">Return Home</button>
              <button id="shutdownPoseButton" class="warn" type="button">Shutdown Pose</button>
              <button id="lightAmberButton" type="button">Warm Amber</button>
              <button id="lightClearButton" type="button">Light Off</button>
            </div>
          </div>
        </div>

        <div class="panel">
          <div class="panel-inner">
            <h2>Realtime State</h2>
            <div class="metric-grid">
              <div class="metric-card"><span>Motion</span><strong id="motionStatus">unknown</strong></div>
              <div class="metric-card"><span>Light</span><strong id="lightStatus">unknown</strong></div>
              <div class="metric-card"><span>Audio</span><strong id="audioStatus">unknown</strong></div>
              <div class="metric-card"><span>Updated</span><strong id="lastUpdate">0</strong></div>
            </div>
          </div>
        </div>
      </section>

      <section class="right-column">
        <div id="diagnosticsPanel" class="panel">
          <div class="panel-inner">
            <h2>Diagnostics</h2>
            <div id="reachableUrls"></div>
            <div id="recordingList"></div>
            <div id="errorFeed" class="event-feed"></div>
          </div>
        </div>
      </section>
    </main>
  </div>

  <script src="/static/dashboard.js"></script>
</body>
</html>
```

```css
/* lelamp/dashboard/web/dashboard.css */
:root {
  --bg: #050a10;
  --bg-top: #0a1622;
  --panel: rgba(9, 17, 27, 0.92);
  --line: rgba(148, 163, 184, 0.16);
  --text: #e6edf4;
  --muted: #97a6b6;
  --accent: #8cf0b5;
  --warn: #f4c15d;
  --danger: #ff7474;
  --lamp-amber: #ffb35c;
  --radius-lg: 24px;
  --shadow: 0 24px 80px rgba(0, 0, 0, 0.35);
}

* { box-sizing: border-box; }
html, body { margin: 0; min-height: 100%; }
body {
  font-family: "JetBrains Mono", monospace;
  color: var(--text);
  background:
    radial-gradient(circle at top, rgba(76, 201, 240, 0.12), transparent 30%),
    radial-gradient(circle at 20% 20%, rgba(255, 179, 92, 0.14), transparent 28%),
    linear-gradient(180deg, var(--bg-top) 0%, var(--bg) 55%, #03060a 100%);
}
.app-shell { max-width: 1440px; margin: 0 auto; padding: 20px; }
.topbar, .layout { display: grid; gap: 18px; }
.topbar { grid-template-columns: 1fr auto; align-items: start; }
.layout { grid-template-columns: 1.1fr 0.9fr; }
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius-lg); box-shadow: var(--shadow); backdrop-filter: blur(18px); }
.panel-inner { padding: 18px; }
.controls-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.metric-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.metric-card { padding: 14px; border-radius: 18px; border: 1px solid var(--line); background: rgba(255, 255, 255, 0.03); }
.stage-pill, .conn-pill, button, select {
  border-radius: 999px;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.04);
  color: var(--text);
  padding: 12px 14px;
  font: inherit;
}
button.primary { border-color: rgba(140, 240, 181, 0.32); }
button.warn { border-color: rgba(244, 193, 93, 0.32); }
button[disabled] { opacity: 0.45; cursor: not-allowed; }
@media (max-width: 900px) {
  .layout { grid-template-columns: 1fr; }
  .controls-grid, .metric-grid { grid-template-columns: 1fr; }
}
```

```javascript
/* lelamp/dashboard/web/dashboard.js */
var DashboardApp = (function () {
  function text(node, value) {
    if (node) {
      node.textContent = value;
    }
  }

  function disableButtons(documentRef, isBusy) {
    var ids = [
      "startupButton",
      "playButton",
      "stopButton",
      "shutdownPoseButton",
      "lightAmberButton",
      "lightClearButton"
    ];
    var index;
    for (index = 0; index < ids.length; index += 1) {
      var node = documentRef.getElementById(ids[index]);
      if (node) {
        node.disabled = isBusy;
      }
    }
  }

  function applyActionAvailability(documentRef, actions) {
    var mapping = {
      startupButton: "startup",
      playButton: "play",
      stopButton: "stop",
      shutdownPoseButton: "shutdown_pose",
      lightAmberButton: "light_solid",
      lightClearButton: "light_clear"
    };
    var id;
    for (id in mapping) {
      if (mapping.hasOwnProperty(id)) {
        var node = documentRef.getElementById(id);
        var config = actions ? actions[mapping[id]] : null;
        if (node && config && config.enabled === false) {
          node.disabled = true;
        }
      }
    }
  }

  function applyState(documentRef, state) {
    text(documentRef.getElementById("connectionStatus"), (state.system.reachable_urls || []).length > 0 ? "live" : "offline");
    text(documentRef.getElementById("systemStatus"), state.system.status || "unknown");
    text(documentRef.getElementById("activeAction"), state.system.active_action || "idle");
    text(documentRef.getElementById("motionStatus"), state.motion.status || "unknown");
    text(documentRef.getElementById("lightStatus"), state.light.status || "unknown");
    text(documentRef.getElementById("audioStatus"), state.audio.status || "unknown");
    text(documentRef.getElementById("lastUpdate"), String(state.system.last_update_ms || 0));
    text(documentRef.getElementById("lastUpdateTopbar"), String(state.system.last_update_ms || 0));
    text(documentRef.getElementById("reachableUrls"), (state.system.reachable_urls || []).join(" "));
    text(documentRef.getElementById("recordingList"), (state.motion.available_recordings || []).join(" "));
    text(documentRef.getElementById("errorFeed"), JSON.stringify(state.errors || []));
    disableButtons(documentRef, state.system.status === "running");
  }

  function pollState(fetchRef, onState) {
    return fetchRef("/api/state")
      .then(function (response) { return response.json(); })
      .then(function (state) {
        onState(state);
        return state;
      })
      .catch(function () {
        onState({
          system: { status: "unknown", active_action: null, last_update_ms: 0, reachable_urls: [] },
          motion: { status: "unknown" },
          light: { status: "unknown" },
          audio: { status: "unknown" },
          errors: [{ code: "ui.poll_failed", message: "State polling failed." }]
        });
      });
  }

  function loadActions(documentRef, fetchRef) {
    return fetchRef("/api/actions")
      .then(function (response) { return response.json(); })
      .then(function (payload) {
        var select = documentRef.getElementById("recordingSelect");
        var index;
        applyActionAvailability(documentRef, payload.actions || {});
        if (select) {
          select.innerHTML = "";
          for (index = 0; index < (payload.recordings || []).length; index += 1) {
            var option = { value: payload.recordings[index], textContent: payload.recordings[index] };
            if (select.appendChild) {
              select.appendChild(option);
            }
          }
        }
        return payload;
      });
  }

  function postJson(fetchRef, url, payload) {
    return fetchRef(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: payload ? JSON.stringify(payload) : "{}"
    });
  }

  function wireActions(documentRef, fetchRef) {
    var startupButton = documentRef.getElementById("startupButton");
    var playButton = documentRef.getElementById("playButton");
    var stopButton = documentRef.getElementById("stopButton");
    var shutdownPoseButton = documentRef.getElementById("shutdownPoseButton");
    var lightAmberButton = documentRef.getElementById("lightAmberButton");
    var lightClearButton = documentRef.getElementById("lightClearButton");
    var recordingSelect = documentRef.getElementById("recordingSelect");

    if (startupButton && startupButton.addEventListener) {
      startupButton.addEventListener("click", function () { postJson(fetchRef, "/api/actions/startup"); });
    }
    if (playButton && playButton.addEventListener) {
      playButton.addEventListener("click", function () {
        postJson(fetchRef, "/api/actions/play", { name: recordingSelect ? recordingSelect.value : "" });
      });
    }
    if (stopButton && stopButton.addEventListener) {
      stopButton.addEventListener("click", function () { postJson(fetchRef, "/api/actions/stop"); });
    }
    if (shutdownPoseButton && shutdownPoseButton.addEventListener) {
      shutdownPoseButton.addEventListener("click", function () { postJson(fetchRef, "/api/actions/shutdown_pose"); });
    }
    if (lightAmberButton && lightAmberButton.addEventListener) {
      lightAmberButton.addEventListener("click", function () {
        postJson(fetchRef, "/api/lights/solid", { red: 255, green: 179, blue: 92 });
      });
    }
    if (lightClearButton && lightClearButton.addEventListener) {
      lightClearButton.addEventListener("click", function () { postJson(fetchRef, "/api/lights/clear"); });
    }
  }

  function start(documentRef, windowRef, fetchRef, pollMs) {
    wireActions(documentRef, fetchRef);
    loadActions(documentRef, fetchRef).then(function (payload) {
      var effectivePollMs = payload.poll_ms || pollMs;
      pollState(fetchRef, function (state) { applyState(documentRef, state); });
      windowRef.setInterval(function () {
        pollState(fetchRef, function (state) { applyState(documentRef, state); });
      }, effectivePollMs);
    });
  }

  return {
    applyState: applyState,
    loadActions: loadActions,
    pollState: pollState,
    start: start
  };
}());

window.addEventListener("DOMContentLoaded", function () {
  DashboardApp.start(document, window, window.fetch.bind(window), 400);
});
```

```dotenv
# .env.example
LELAMP_DASHBOARD_HOST=0.0.0.0
LELAMP_DASHBOARD_PORT=8765
LELAMP_DASHBOARD_POLL_MS=400
```

```markdown
<!-- README.md -->
## Local Dashboard

Run the local dashboard on the Raspberry Pi with `uv run python -m lelamp.dashboard.api`.

Open one of the reported URLs from the Pi screen, a laptop on the same LAN, or a device connected through phone/Pi hotspot.
```

- [ ] **Step 4: Run dashboard web tests and a full module sweep**

Run: `uv run --extra dev python -m unittest lelamp.test.test_dashboard_runtime_config lelamp.test.test_dashboard_state_store lelamp.test.test_dashboard_runtime_bridge lelamp.test.test_dashboard_actions lelamp.test.test_dashboard_samplers lelamp.test.test_dashboard_api lelamp.test.test_dashboard_web -v`
Expected: PASS across all dashboard-focused modules

- [ ] **Step 5: Commit**

```bash
git add lelamp/dashboard/web/index.html lelamp/dashboard/web/dashboard.css lelamp/dashboard/web/dashboard.js lelamp/test/test_dashboard_web.py README.md .env.example
git commit -m "feat: add local dashboard ui"
```

### Task 8: Manual Pi Verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Start the dashboard on the Raspberry Pi**

```bash
uv run python -m lelamp.dashboard.api
```

- [ ] **Step 2: Verify the Pi local browser flow**

Run in the Pi browser:

```text
http://127.0.0.1:8765
```

Expected:

- status pills render
- control buttons render
- state cards update roughly every 400 ms
- no blank panel or broken CSS

- [ ] **Step 3: Verify same-network and hotspot access**

Use one LAN IP from `/api/state` or the diagnostics panel, then open:

```text
http://<pi-ip>:8765
```

Expected:

- phone or laptop can load the dashboard
- the same page works without desktop-only hover interactions
- state and button locking match the Pi screen

- [ ] **Step 4: Verify motion and light safety rules**

Manual checklist:

- click `Startup`, confirm buttons disable while motion is running
- click `Play Motion`, confirm selected recording runs and then returns to `home_safe`
- click `Return Home`, confirm the lamp settles at `home_safe`
- click `Shutdown Pose`, confirm the lamp ends at shutdown pose and LEDs clear
- click `Warm Amber`, confirm the light changes without reporting false hardware state

- [ ] **Step 5: Commit the final verification docs update if needed**

```bash
git add README.md
git commit -m "docs: add dashboard verification notes"
```
