# H1 Memory C1 Continuation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the remaining H1 memory C1 runtime integration by recording manual `startup` / `shutdown_pose` playback events, keeping agent lifecycle shutdown safe, and landing the branch in a pushable state.

**Architecture:** Keep all memory writes behind `lelamp.memory.runtime` so runtime hot paths stay no-throw. Extend the existing manual playback seam instead of inventing a second logging path: dashboard and CLI actions should emit `playback.action = startup|shutdown_pose` from their top-level control layer, while agent-side lifecycle continues to use the shutdown callback path already added in `smooth_animation.py`.

**Tech Stack:** Python 3.12, pytest, LiveKit agents, existing `lelamp.memory` JSONL/session library

---

### Task 1: Lock In Remaining Playback Contracts With Tests

**Files:**
- Modify: `lelamp/test/test_remote_control.py`
- Modify: `lelamp/test/test_dashboard_runtime_bridge.py`
- Test: `lelamp/test/test_remote_control.py`
- Test: `lelamp/test/test_dashboard_runtime_bridge.py`

- [ ] **Step 1: Write the failing CLI startup/shutdown playback tests**

```python
def test_handle_startup_records_playback_success(self) -> None:
    with patch.object(remote_control, "record_standalone_playback") as record_playback:
        result = remote_control._handle_startup(args)
    self.assertEqual(result, 0)
    record_playback.assert_called_once_with(
        source="remote_control",
        initiator="remote_control",
        action="startup",
        recording_name="wake_up",
        rgb=None,
        duration_ms=2034,
        ok=True,
        error=None,
    )

def test_handle_shutdown_records_playback_when_motor_live_refuses(self) -> None:
    with patch.object(remote_control, "record_standalone_playback") as record_playback:
        result = remote_control._handle_shutdown(args)
    self.assertEqual(result, 2)
    record_playback.assert_called_once()
    assert record_playback.call_args.kwargs["action"] == "shutdown_pose"
    assert record_playback.call_args.kwargs["ok"] is False
```

- [ ] **Step 2: Run the CLI tests to verify RED**

Run: `PYTHONPATH=. uv run --with pytest python -m pytest lelamp/test/test_remote_control.py -v`
Expected: FAIL on missing `record_standalone_playback(... action="startup" | "shutdown_pose" ...)` assertions

- [ ] **Step 3: Write the failing dashboard startup/shutdown playback tests**

```python
def test_startup_via_motor_bus_records_startup_playback(self) -> None:
    with patch.object(runtime_bridge_mod, "record_standalone_playback") as record_playback:
        result = bridge.startup()
    self.assertTrue(result.ok)
    record_playback.assert_called_once_with(
        source="dashboard",
        initiator="dashboard",
        action="startup",
        recording_name="wake_up",
        rgb=None,
        duration_ms=2034,
        ok=True,
        error=None,
    )

def test_shutdown_pose_via_motor_bus_records_shutdown_pose(self) -> None:
    with patch.object(runtime_bridge_mod, "record_standalone_playback") as record_playback:
        result = bridge.shutdown_pose()
    self.assertTrue(result.ok)
    assert record_playback.call_args.kwargs["action"] == "shutdown_pose"
    assert record_playback.call_args.kwargs["recording_name"] == "power_off"
```

- [ ] **Step 4: Run the dashboard tests to verify RED**

Run: `PYTHONPATH=. uv run --with pytest python -m pytest lelamp/test/test_dashboard_runtime_bridge.py -v`
Expected: FAIL on missing startup / shutdown playback logging assertions

- [ ] **Step 5: Commit the red tests**

```bash
git add lelamp/test/test_remote_control.py lelamp/test/test_dashboard_runtime_bridge.py
git commit -m "test(memory): cover startup and shutdown playback logging"
```

### Task 2: Implement Startup And Shutdown Playback Logging

**Files:**
- Modify: `lelamp/remote_control.py`
- Modify: `lelamp/dashboard/runtime_bridge.py`
- Test: `lelamp/test/test_remote_control.py`
- Test: `lelamp/test/test_dashboard_runtime_bridge.py`

- [ ] **Step 1: Route CLI startup/shutdown through the existing standalone playback seam**

```python
def _handle_startup(args) -> int:
    started_at = time.monotonic()
    if current_sentinel(require=REQUIRE_MOTOR) is not None:
        error = "Voice agent is running and already owns the serial port"
        record_standalone_playback(
            source="remote_control",
            initiator="remote_control",
            action="startup",
            recording_name=args.recording,
            rgb=None,
            duration_ms=None,
            ok=False,
            error=error,
        )
        return 2
    try:
        ...
    except Exception as exc:
        record_standalone_playback(... action="startup", ok=False, error=str(exc))
        raise
    record_standalone_playback(... action="startup", duration_ms=_elapsed_ms(started_at), ok=True)
    return 0
```

- [ ] **Step 2: Teach dashboard runtime_bridge to log semantic startup/shutdown actions instead of generic `play`**

```python
def startup(self) -> DashboardActionResult:
    started_at = time.monotonic()
    if current_sentinel(require=REQUIRE_MOTOR) is not None:
        return self._dispatch_recording_action(
            action="startup",
            service_event="startup",
            recording_name=self.settings.startup_recording,
            success_message="Re-dispatched startup via motor bus",
            timeout_message="Timed out waiting for startup to finish",
            failure_message="Failed to replay startup via motor bus",
            started_at=started_at,
        )
    return self._run_remote(..., playback_action="startup", playback_recording=self.settings.startup_recording)

def shutdown_pose(self) -> DashboardActionResult:
    if current_sentinel(require=REQUIRE_MOTOR) is not None:
        return self._dispatch_recording_action(
            action="shutdown_pose",
            service_event="play",
            recording_name="power_off",
            ...
        )
    return self._run_remote(..., playback_action="shutdown_pose", playback_recording="power_off")
```

- [ ] **Step 3: Keep logging isolated behind one helper so tests only assert behavior**

```python
def _run_remote(self, handler, *, playback_action=None, playback_recording=None, **overrides):
    try:
        exit_code = handler(args)
    except Exception as exc:
        if playback_action is not None:
            self._record_playback(action=playback_action, recording_name=playback_recording, ...)
        return DashboardActionResult(False, "Runtime action failed", detail=str(exc))
    if exit_code != 0:
        if playback_action is not None:
            self._record_playback(action=playback_action, recording_name=playback_recording, ...)
        return DashboardActionResult(False, "Runtime action failed", detail=str(exit_code))
    if playback_action is not None:
        self._record_playback(action=playback_action, recording_name=playback_recording, ...)
    return DashboardActionResult(True, "Runtime action completed")
```

- [ ] **Step 4: Run the focused tests to verify GREEN**

Run: `PYTHONPATH=. uv run --with pytest python -m pytest lelamp/test/test_remote_control.py lelamp/test/test_dashboard_runtime_bridge.py -v`
Expected: PASS

- [ ] **Step 5: Commit the implementation**

```bash
git add lelamp/remote_control.py lelamp/dashboard/runtime_bridge.py lelamp/test/test_remote_control.py lelamp/test/test_dashboard_runtime_bridge.py
git commit -m "feat(memory): record startup and shutdown playback actions"
```

### Task 3: Re-verify The C1 Branch And Publish It

**Files:**
- Modify: `smooth_animation.py` (only if verification reveals lifecycle regressions)
- Modify: `lelamp/memory/runtime.py` (only if verification reveals lifecycle regressions)
- Test: `lelamp/test/test_memory_*.py`
- Test: `lelamp/test/test_voice_profile.py`
- Test: `lelamp/test/test_auto_expression.py`
- Test: `lelamp/test/test_qwen_realtime.py`
- Test: `lelamp/test/test_remote_control.py`
- Test: `lelamp/test/test_dashboard_runtime_bridge.py`
- Test: `lelamp/test/test_motor_bus_*.py`

- [ ] **Step 1: Run the full relevant regression suite**

Run:

```bash
PYTHONPATH=. uv run --with pytest python -m pytest \
  lelamp/test/test_memory_*.py \
  lelamp/test/test_voice_profile.py \
  lelamp/test/test_auto_expression.py \
  lelamp/test/test_qwen_realtime.py \
  lelamp/test/test_remote_control.py \
  lelamp/test/test_dashboard_runtime_bridge.py \
  lelamp/test/test_motor_bus_*.py
```

Expected: PASS for the complete C0+C1 memory/runtime surface

- [ ] **Step 2: If lifecycle regressions appear, add the smallest missing test first, then patch `smooth_animation.py` / `lelamp/memory/runtime.py`**

```python
async def test_entrypoint_shutdown_callback_stops_auto_expression_before_memory_close():
    ...
    await callbacks[0]("room_disconnect")
    assert events == ["auto.stop", "memory.close"]
```

- [ ] **Step 3: Re-run the full regression suite and verify branch cleanliness**

Run:

```bash
git status --short --branch
PYTHONPATH=. uv run --with pytest python -m pytest ...
```

Expected: clean or intentional diff only; tests green

- [ ] **Step 4: Push the finished C1 continuation branch**

```bash
git push origin feature/h1-memory-v0-c1-bootstrap
```

- [ ] **Step 5: Hand off the next stack step**

```text
Next after push:
1. Decide whether to stack a CI-only branch (.github/workflows/pytest.yml)
2. Decide whether to open/update the C1 PR stack now or wait for upstream base movement
3. Schedule Pi canary with LELAMP_MEMORY_DISABLE toggled off against a shadow memory root
```
