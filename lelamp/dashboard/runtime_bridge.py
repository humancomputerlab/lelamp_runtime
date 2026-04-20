"""Typed adapter over the dashboard runtime services.

When the voice agent is running it owns ``/dev/ttyACM0`` and ``/dev/leds0``
for the lifetime of the process; spinning up a fresh ``AnimationService``
here would fight the agent for the serial port. We therefore probe the
``motor_bus`` sentinel before building services and, when the agent is
alive, return HTTP proxies that forward to the agent's long-lived services.
When the sentinel is missing we fall back to the historical direct-hardware
behaviour so calibration / no-agent workflows keep working.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable

from lelamp.memory.runtime import record_standalone_playback
from lelamp.motor_bus.client import (
    MotorBusClientError,
    REQUIRE_MOTOR,
    build_animation_service as _build_animation_service_with_proxy,
    build_rgb_service as _build_rgb_service_with_proxy,
    current_sentinel,
)


@dataclass(frozen=True)
class DashboardActionResult:
    ok: bool
    message: str
    detail: str | None = None


def _elapsed_ms(started_at: float) -> int:
    return int((time.monotonic() - started_at) * 1000)


def _default_animation_factory(**kwargs):
    from lelamp.service.motors.animation_service import AnimationService

    return AnimationService(**kwargs)


def _default_rgb_factory(**kwargs):
    from lelamp.service.rgb.rgb_service import RGBService

    return RGBService(**kwargs)


def _default_remote_module():
    from lelamp import remote_control

    return remote_control


class DashboardRuntimeBridge:
    def __init__(
        self,
        settings,
        animation_factory: Callable[..., Any] | None = None,
        rgb_factory: Callable[..., Any] | None = None,
        remote_module=None,
    ) -> None:
        self.settings = settings
        self.animation_factory = animation_factory or _default_animation_factory
        self.rgb_factory = rgb_factory or _default_rgb_factory
        self.remote_module = remote_module or _default_remote_module()

    def list_recordings(self) -> list[str]:
        service = self._build_animation_service()
        return service.get_available_recordings()

    def startup(self) -> DashboardActionResult:
        started_at = time.monotonic()
        # When the voice agent is running AND its motor service is live we
        # short-circuit to a proxy dispatch, because re-running the staged
        # startup via remote_control would fight the agent for the serial
        # port. If the agent's motor service never came up (``motor_ok =
        # False``) we intentionally drop through to the direct-hardware path
        # so the fallback code can still try to recover.
        try:
            motor_sentinel = current_sentinel(require=REQUIRE_MOTOR)
        except MotorBusClientError as exc:
            result = DashboardActionResult(False, "Motor bus ownership uncertain", detail=str(exc))
            self._record_playback(
                action="startup",
                recording_name=self.settings.startup_recording,
                rgb=None,
                duration_ms=_elapsed_ms(started_at),
                ok=False,
                error=result.detail,
            )
            return result

        if motor_sentinel is not None:
            # Block the dashboard busy lock until startup choreography is
            # actually done, just like play(). Without the wait, executor
            # would mark the action complete on dispatch-return, letting the
            # next click punch through the startup state machine mid-play.
            service = self._build_animation_service()
            try:
                service.dispatch("startup", self.settings.startup_recording)
                if not service.wait_until_playback_complete(timeout=120.0):
                    result = DashboardActionResult(
                        False,
                        "Timed out waiting for startup to finish",
                        detail=self.settings.startup_recording,
                    )
                    self._record_playback(
                        action="startup",
                        recording_name=self.settings.startup_recording,
                        rgb=None,
                        duration_ms=_elapsed_ms(started_at),
                        ok=False,
                        error=result.message,
                    )
                    return result
            except Exception as exc:
                result = DashboardActionResult(
                    False,
                    "Failed to replay startup via motor bus",
                    detail=str(exc),
                )
                self._record_playback(
                    action="startup",
                    recording_name=self.settings.startup_recording,
                    rgb=None,
                    duration_ms=_elapsed_ms(started_at),
                    ok=False,
                    error=result.detail,
                )
                return result
            result = DashboardActionResult(
                True,
                "Re-dispatched startup via motor bus",
                detail=self.settings.startup_recording,
            )
            self._record_playback(
                action="startup",
                recording_name=self.settings.startup_recording,
                rgb=None,
                duration_ms=_elapsed_ms(started_at),
                ok=True,
                error=None,
            )
            return result
        return self._run_remote(
            self.remote_module._handle_startup,
            playback_action="startup",
            playback_recording=self.settings.startup_recording,
            handler_records_playback=bool(
                getattr(self.remote_module, "HANDLES_PLAYBACK_RECORDING", False)
            ),
            recording=self.settings.startup_recording,
            home_recording=self.settings.home_recording,
            settle_frames=self.remote_module.DEFAULT_STARTUP_SETTLE_FRAMES,
            settle_hold_frames=self.remote_module.DEFAULT_STARTUP_HOLD_FRAMES,
            settle_fps=self.remote_module.DEFAULT_STARTUP_FPS,
            wake_fps=self.remote_module.DEFAULT_WAKE_FPS,
            post_wake_hold=self.remote_module.DEFAULT_POST_WAKE_HOLD_SECONDS,
        )

    def play(self, recording_name: str, *, playback_action: str = "play") -> DashboardActionResult:
        started = False
        started_at = time.monotonic()
        try:
            service = self._build_animation_service()
            recordings = set(service.get_available_recordings())
            if recording_name not in recordings:
                result = DashboardActionResult(
                    False,
                    "Recording not found",
                    detail=recording_name,
                )
                self._record_playback(
                    action=playback_action,
                    recording_name=recording_name,
                    rgb=None,
                    duration_ms=None,
                    ok=False,
                    error=result.detail,
                )
                return result

            service.start()
            started = True
            service.dispatch("play", recording_name)
            if not service.wait_until_playback_complete(timeout=120.0):
                result = DashboardActionResult(
                    False,
                    "Timed out waiting for recording to finish",
                    detail=recording_name,
                )
                self._record_playback(
                    action=playback_action,
                    recording_name=recording_name,
                    rgb=None,
                    duration_ms=_elapsed_ms(started_at),
                    ok=False,
                    error=result.message,
                )
                return result
        except Exception as exc:
            result = DashboardActionResult(
                False,
                "Failed to play recording",
                detail=str(exc),
            )
            self._record_playback(
                action=playback_action,
                recording_name=recording_name,
                rgb=None,
                duration_ms=_elapsed_ms(started_at),
                ok=False,
                error=result.detail,
            )
            return result
        finally:
            if started:
                service.stop()

        result = DashboardActionResult(True, "Finished playing recording", detail=recording_name)
        self._record_playback(
            action=playback_action,
            recording_name=recording_name,
            rgb=None,
            duration_ms=_elapsed_ms(started_at),
            ok=True,
            error=None,
        )
        return result

    def shutdown_pose(self) -> DashboardActionResult:
        # remote_control._handle_shutdown releases torque by writing
        # Torque_Enable directly on the servo bus. That sequence cannot run
        # while the agent owns the serial port, so when the agent's motor
        # service is healthy we degrade to "play the power_off recording" —
        # the lamp reaches the rest pose but torque stays enabled. Full
        # torque-off requires stopping the agent first (see known
        # limitations). When motor_ok is False we fall through to the staged
        # shutdown; the port is unlikely to be held in that case.
        try:
            motor_sentinel = current_sentinel(require=REQUIRE_MOTOR)
        except MotorBusClientError as exc:
            return DashboardActionResult(False, "Motor bus ownership uncertain", detail=str(exc))

        if motor_sentinel is not None:
            return self.play("power_off", playback_action="shutdown_pose")
        return self._run_remote(
            self.remote_module._handle_shutdown,
            playback_action="shutdown_pose",
            playback_recording="power_off",
            handler_records_playback=bool(
                getattr(self.remote_module, "HANDLES_PLAYBACK_RECORDING", False)
            ),
            recording="power_off",
            prepare_fraction=self.remote_module.DEFAULT_SHUTDOWN_PREPARE_FRACTION,
            prepare_frames=self.remote_module.DEFAULT_SHUTDOWN_PREPARE_FRAMES,
            settle_frames=self.remote_module.DEFAULT_SHUTDOWN_SETTLE_FRAMES,
            hold_frames=self.remote_module.DEFAULT_SHUTDOWN_HOLD_FRAMES,
            fps=self.remote_module.DEFAULT_SHUTDOWN_FPS,
            final_hold=self.remote_module.DEFAULT_SHUTDOWN_FINAL_HOLD_SECONDS,
            release_pause=self.remote_module.DEFAULT_RELEASE_PAUSE_SECONDS,
            keep_led_on=False,
        )

    def stop(self) -> DashboardActionResult:
        return self.play(self.settings.home_recording)

    def set_light_solid(self, rgb: tuple[int, int, int]) -> DashboardActionResult:
        if not self.settings.enable_rgb:
            result = DashboardActionResult(False, "RGB is disabled via LELAMP_ENABLE_RGB")
            self._record_playback(
                action="light_solid",
                recording_name=None,
                rgb=rgb,
                duration_ms=None,
                ok=False,
                error=result.message,
            )
            return result

        try:
            service = self._build_rgb_service()
            service.handle_event("solid", rgb)
        except Exception as exc:
            result = DashboardActionResult(
                False,
                "Failed to set RGB solid color",
                detail=str(exc),
            )
            self._record_playback(
                action="light_solid",
                recording_name=None,
                rgb=rgb,
                duration_ms=None,
                ok=False,
                error=result.detail,
            )
            return result

        result = DashboardActionResult(True, "Set RGB solid color", detail=str(rgb))
        self._record_playback(
            action="light_solid",
            recording_name=None,
            rgb=rgb,
            duration_ms=None,
            ok=True,
            error=None,
        )
        return result

    def clear_light(self) -> DashboardActionResult:
        if not self.settings.enable_rgb:
            result = DashboardActionResult(False, "RGB is disabled via LELAMP_ENABLE_RGB")
            self._record_playback(
                action="light_clear",
                recording_name=None,
                rgb=None,
                duration_ms=None,
                ok=False,
                error=result.message,
            )
            return result

        try:
            service = self._build_rgb_service()
            service.clear()
        except Exception as exc:
            result = DashboardActionResult(
                False,
                "Failed to clear RGB LEDs",
                detail=str(exc),
            )
            self._record_playback(
                action="light_clear",
                recording_name=None,
                rgb=None,
                duration_ms=None,
                ok=False,
                error=result.detail,
            )
            return result

        result = DashboardActionResult(True, "Cleared RGB LEDs")
        self._record_playback(
            action="light_clear",
            recording_name=None,
            rgb=None,
            duration_ms=None,
            ok=True,
            error=None,
        )
        return result

    def _run_remote(
        self,
        handler,
        *,
        playback_action: str | None = None,
        playback_recording: str | None = None,
        handler_records_playback: bool = False,
        **overrides: Any,
    ) -> DashboardActionResult:
        payload = {
            "id": self.settings.lamp_id,
            "port": self.settings.port,
            "fps": self.settings.fps,
            "enable_rgb": self.settings.enable_rgb,
            "led_count": self.settings.led_count,
            "led_pin": self.settings.led_pin,
            "led_freq_hz": self.settings.led_freq_hz,
            "led_dma": self.settings.led_dma,
            "led_brightness": self.settings.led_brightness,
            "led_invert": self.settings.led_invert,
            "led_channel": self.settings.led_channel,
        }
        payload.update(overrides)
        args = SimpleNamespace(**payload)

        try:
            exit_code = handler(args)
        except Exception as exc:
            if playback_action is not None and not handler_records_playback:
                self._record_playback(
                    action=playback_action,
                    recording_name=playback_recording,
                    rgb=None,
                    duration_ms=None,
                    ok=False,
                    error=str(exc),
                )
            return DashboardActionResult(
                False,
                "Runtime action failed",
                detail=str(exc),
            )
        if exit_code != 0:
            if playback_action is not None and not handler_records_playback:
                self._record_playback(
                    action=playback_action,
                    recording_name=playback_recording,
                    rgb=None,
                    duration_ms=None,
                    ok=False,
                    error=str(exit_code),
                )
            return DashboardActionResult(
                False,
                "Runtime action failed",
                detail=str(exit_code),
            )

        if playback_action is not None and not handler_records_playback:
            self._record_playback(
                action=playback_action,
                recording_name=playback_recording,
                rgb=None,
                duration_ms=None,
                ok=True,
                error=None,
            )
        return DashboardActionResult(True, "Runtime action completed")

    def _build_animation_service(self):
        def _fallback():
            return self.animation_factory(
                port=self.settings.port,
                lamp_id=self.settings.lamp_id,
                fps=self.settings.fps,
                duration=self.settings.interpolation_duration,
                idle_recording=self.settings.home_recording,
                home_recording=self.settings.home_recording,
                use_home_pose_relative=self.settings.use_home_pose_relative,
            )

        return _build_animation_service_with_proxy(_fallback)

    def _build_rgb_service(self):
        def _fallback():
            return self.rgb_factory(
                led_count=self.settings.led_count,
                led_pin=self.settings.led_pin,
                led_freq_hz=self.settings.led_freq_hz,
                led_dma=self.settings.led_dma,
                led_brightness=self.settings.led_brightness,
                led_invert=self.settings.led_invert,
                led_channel=self.settings.led_channel,
            )

        return _build_rgb_service_with_proxy(_fallback)

    def _record_playback(
        self,
        *,
        action: str,
        recording_name: str | None,
        rgb: tuple[int, int, int] | None,
        duration_ms: int | None,
        ok: bool,
        error: str | None,
    ) -> None:
        record_standalone_playback(
            source="dashboard",
            initiator="dashboard",
            action=action,
            recording_name=recording_name,
            rgb=rgb,
            duration_ms=duration_ms,
            ok=ok,
            error=error,
        )
