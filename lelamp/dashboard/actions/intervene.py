"""Manual fallback button — `/api/actions/intervene?style=xxx`.

DEMO_PLAN §1.2 calls this mandatory demo-safety wiring: if the FluxChi
backend, the camera, or the EMG wristband silently dies mid-demo, the
presenter can still trigger the lamp response by hand through this button.
The styles map 1:1 onto what the automatic Scene A / Scene B paths do,
so the lamp behaves identically to an auto-trigger:

    shy             → motion: play "shy"
    headshake       → motion: play "headshake"
    sad_nod         → motion: play "sad" → play "nod" (chained)
    breath_moderate → breath: BreathPlan(90s, 12→4bpm, 3000→2400K)
    breath_mild     → breath: BreathPlan(60s, 10→6bpm, 3200→2700K)

Motion styles go through the existing ``DashboardActionExecutor`` so they
honor the dashboard busy-lock (409 Conflict if another action is already
running). Breath styles bypass the executor and speak through the
motor_bus ``ProxyRGBService`` — the same path ``fluxchi_listener._start_breath``
uses — because 10 Hz RGB writes don't fit the executor's single-slot
busy lock and would dogpile 409s otherwise (see
``breath_orchestrator.py`` module docstring for the long version).

Preemption semantics:
    - Starting any intervene (motion or breath) stops a currently-running
      breath first. A motion-playback that is mid-flight (executor busy)
      causes the new request to return 409 normally; we don't try to
      preempt motion.
    - Starting a new breath stops the previous breath (idempotent stop +
      fade tail is handled by BreathOrchestrator itself).

The module exports:
    - ``INTERVENE_STYLES``: the style registry
    - ``BreathSlot``: thread-safe holder for the current orchestrator +
      the lazy ProxyRGBService
    - ``build_intervene_action(executor, bridge, breath_slot)`` → the
      callable wired into ``POST /api/actions/intervene``
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional

from lelamp.breath_orchestrator import BreathOrchestrator, BreathPlan
from lelamp.dashboard.actions.executor import (
    DashboardActionExecutor,
    DashboardActionReceipt,
)
from lelamp.dashboard.runtime_bridge import DashboardActionResult

log = logging.getLogger("dashboard.intervene")


# ─── Style registry ───────────────────────────────────────────

# Each spec is plain data so tests can import and assert on it without
# needing the full dashboard stack alive. Motion specs are lists of
# recording names to chain sequentially inside the executor callback.
# Breath specs carry a frozen BreathPlan; callers clone via `replace` if
# they need to tweak (orchestrator owns it for the run).
MOTION = "motion"
BREATH = "breath"


@dataclass(frozen=True)
class _MotionSpec:
    recordings: List[str]


@dataclass(frozen=True)
class _BreathSpec:
    plan: BreathPlan


INTERVENE_STYLES: Dict[str, Any] = {
    "shy": _MotionSpec(recordings=["shy"]),
    "headshake": _MotionSpec(recordings=["headshake"]),
    "sad_nod": _MotionSpec(recordings=["sad", "nod"]),
    "breath_moderate": _BreathSpec(
        plan=BreathPlan(
            duration_sec=90.0,
            bpm_start=12.0,
            bpm_end=4.0,
            kelvin_start=3000.0,
            kelvin_end=2400.0,
            intensity_base=0.35,
            intensity_peak=1.0,
            tick_hz=10.0,
            name="moderate_breath",
        )
    ),
    "breath_mild": _BreathSpec(
        plan=BreathPlan(
            duration_sec=60.0,
            bpm_start=10.0,
            bpm_end=6.0,
            kelvin_start=3200.0,
            kelvin_end=2700.0,
            intensity_base=0.5,
            intensity_peak=0.95,
            tick_hz=10.0,
            name="mild_breath",
        )
    ),
}


def available_styles() -> List[str]:
    """Enumerate style names in a stable order (for UI / tests)."""
    # dict preserves insertion order in Python 3.7+; we rely on that so the
    # UI button layout and any serialized snapshot are deterministic.
    return list(INTERVENE_STYLES.keys())


def style_metadata() -> List[Dict[str, Any]]:
    """JSON-friendly view of the registry (exposed via /api/actions).

    The dashboard JS uses this to render buttons without hardcoding style
    names twice.
    """
    meta: List[Dict[str, Any]] = []
    for name, spec in INTERVENE_STYLES.items():
        if isinstance(spec, _MotionSpec):
            meta.append(
                {
                    "style": name,
                    "type": MOTION,
                    "recordings": list(spec.recordings),
                }
            )
        elif isinstance(spec, _BreathSpec):
            plan = spec.plan
            meta.append(
                {
                    "style": name,
                    "type": BREATH,
                    "duration_sec": plan.duration_sec,
                    "bpm_start": plan.bpm_start,
                    "bpm_end": plan.bpm_end,
                    "kelvin_start": plan.kelvin_start,
                    "kelvin_end": plan.kelvin_end,
                }
            )
        else:
            # Defensive — should never happen unless someone adds a new
            # spec type without updating the serializer.
            meta.append({"style": name, "type": "unknown"})
    return meta


# ─── Breath slot ──────────────────────────────────────────────


RgbServiceFactory = Callable[[], Any]


class BreathSlot:
    """Owns the dashboard's single in-flight BreathOrchestrator.

    Why a slot (not a pool): only one breath should be "active" on the
    lamp at a time — running two concurrent orchestrators would fight over
    the LED strip at 10 Hz. New triggers preempt the old one.

    The RGB service is built lazily by ``rgb_service_factory`` the first
    time a breath is started. Typical factory:

        def factory():
            from lelamp.motor_bus.client import (
                build_rgb_service, current_sentinel, REQUIRE_RGB,
            )
            sentinel = current_sentinel(require=REQUIRE_RGB, probe_timeout=1.0)
            if sentinel is None:
                raise RuntimeError(
                    "breath requires motor_bus sentinel — start the agent first"
                )
            return build_rgb_service(
                lambda: (_ for _ in ()).throw(
                    RuntimeError("breath refuses to bypass motor_bus")
                )
            )

    The factory is called inside the slot lock; if it raises, the error
    bubbles up to the caller (who surfaces it as a 500 receipt). The
    service is cached after a successful build so subsequent triggers
    don't re-probe the sentinel on every click.
    """

    def __init__(self, rgb_service_factory: RgbServiceFactory) -> None:
        self._factory = rgb_service_factory
        self._lock = threading.Lock()
        self._service: Any = None
        self._current: Optional[BreathOrchestrator] = None
        self._should_continue: Optional[Callable[[], Optional[str]]] = None

    def set_should_continue(self, hook: Optional[Callable[[], Optional[str]]]) -> None:
        """Install a voice-gate-style should_continue hook.

        The dashboard process may not have a voice gate (listener owns
        that), so this defaults to None. If the caller supplies one, it
        gets passed to every orchestrator spawned here.
        """
        with self._lock:
            self._should_continue = hook

    def is_running(self) -> bool:
        with self._lock:
            return self._current is not None and self._current.running

    def stop(self) -> bool:
        """Stop any running breath. Returns True if one was active."""
        with self._lock:
            current = self._current
        if current is None or not current.running:
            return False
        current.stop()
        return True

    def start(self, plan: BreathPlan, *, name: Optional[str] = None) -> BreathOrchestrator:
        """Preempt the current breath (if any) and start a fresh one.

        Returns the orchestrator so the caller can attach to stats /
        wait_until_done for tests. In production the caller just drops it.
        """
        with self._lock:
            if self._service is None:
                self._service = self._factory()
            service = self._service
            previous = self._current
            hook = self._should_continue

        if previous is not None and previous.running:
            log.info("intervene breath preempt: stopping previous %s", previous.plan.name)
            previous.stop()

        orchestrator = BreathOrchestrator(
            plan,
            service.handle_event,
            stop_clears=True,
            name=name or plan.name,
            should_continue=hook,
        )
        orchestrator.start()

        with self._lock:
            self._current = orchestrator
        return orchestrator

    def current(self) -> Optional[BreathOrchestrator]:
        """Mostly for tests — returns the tracked orchestrator."""
        with self._lock:
            return self._current

    def shutdown(self) -> None:
        """Lifespan hook: stop any in-flight breath on app shutdown."""
        self.stop()


# ─── Dispatcher ───────────────────────────────────────────────


def _chain_plays(
    bridge, recordings: Iterable[str]
) -> DashboardActionResult:
    """Synchronous chained playback for multi-step motion styles.

    Runs inside the executor worker thread, so each ``bridge.play`` blocks
    until the recording finishes. If any step fails we short-circuit and
    return that step's failure — mirrors how ``_play_when_idle`` aborts on
    non-retryable errors.
    """
    last: Optional[DashboardActionResult] = None
    for name in recordings:
        result = bridge.play(name)
        last = result
        if not result.ok:
            return result
    # Defensive: recordings iterable might be empty (shouldn't be, but).
    if last is None:
        return DashboardActionResult(False, "No recordings to play")
    return last


def build_intervene_action(
    executor: DashboardActionExecutor,
    bridge,
    breath_slot: BreathSlot,
) -> Callable[[str], DashboardActionReceipt]:
    """Returns the callable that POST /api/actions/intervene invokes.

    Raises ``KeyError`` for unknown styles (the route maps that to 400).
    """

    def _intervene(style: str) -> DashboardActionReceipt:
        spec = INTERVENE_STYLES[style]  # may KeyError — route handles it

        # Any intervene (motion or breath) preempts the current breath
        # first. The listener's _handle does the same thing; doing it here
        # too means the manual button works consistently whether or not
        # the listener is alive.
        breath_slot.stop()

        if isinstance(spec, _MotionSpec):
            recordings = list(spec.recordings)
            action_id = f"intervene:{style}"
            # "second" recording would be the followup; for simplicity we
            # just track the leading recording as the "current" one in
            # dashboard state. If chaining is single-step (shy/headshake)
            # this is indistinguishable from a normal play.
            success_patch = {
                "status": "idle",
                "current_recording": bridge.settings.home_recording,
                "last_completed_recording": recordings[-1],
            }
            return executor.submit(
                action_id,
                lambda recs=recordings: _chain_plays(bridge, recs),
                section="motion",
                success_patch=success_patch,
            )

        if isinstance(spec, _BreathSpec):
            try:
                breath_slot.start(spec.plan, name=f"intervene_{style}")
            except Exception as exc:  # noqa: BLE001
                log.error("intervene breath start failed style=%s err=%s", style, exc)
                return DashboardActionReceipt(
                    ok=False,
                    action_id=f"intervene:{style}",
                    state="error",
                    message=f"breath failed: {exc}",
                    error="breath_start_failed",
                )
            # Breath doesn't hold the executor busy-lock, so we return a
            # synthetic "running" receipt shaped like the executor would
            # emit. Status code mapping in _receipt_response treats ok=True
            # as 202 Accepted, which is what we want.
            return DashboardActionReceipt(
                ok=True,
                action_id=f"intervene:{style}",
                state="running",
                message=f"breath {style} started",
                active_action=None,
            )

        # Unreachable unless registry grows a new spec type.
        return DashboardActionReceipt(
            ok=False,
            action_id=f"intervene:{style}",
            state="error",
            message=f"unknown spec type for style {style}",
            error="unknown_spec",
        )

    return _intervene


__all__ = [
    "BREATH",
    "BreathSlot",
    "INTERVENE_STYLES",
    "MOTION",
    "available_styles",
    "build_intervene_action",
    "style_metadata",
]
