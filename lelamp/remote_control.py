import argparse
import json
import time
from pathlib import Path

from dotenv import load_dotenv

from .motion_profiles import (
    SHUTDOWN_RELEASE_ORDER,
    build_dynamic_startup_actions,
    build_staged_shutdown_actions,
)
from .motor_bus.client import (
    MotorBusClientError,
    REQUIRE_MOTOR,
    build_animation_service as _build_animation_service_with_proxy,
    build_rgb_service as _build_rgb_service_with_proxy,
    current_sentinel,
)
from .pose_presets import write_pose_recordings
from .pose_snapshot import upsert_env_value, write_static_recording
from .runtime_config import load_runtime_settings
from .memory.runtime import record_standalone_playback
from .service.motors.animation_service import AnimationService
from .service.motors.motors_service import MotorsService
from .service.rgb.rgb_service import RGBService


STARTUP_WARM_RGB = (255, 170, 70)
TORQUE_ENABLE_JOINTS = ("base_yaw", "base_pitch", "elbow_pitch", "wrist_roll", "wrist_pitch")
DEFAULT_STARTUP_SETTLE_FRAMES = 18
DEFAULT_STARTUP_HOLD_FRAMES = 10
DEFAULT_STARTUP_FPS = 15
DEFAULT_WAKE_FPS = 30
DEFAULT_POST_WAKE_HOLD_SECONDS = 0.8
DEFAULT_SHUTDOWN_PREPARE_FRACTION = 0.22
DEFAULT_SHUTDOWN_PREPARE_FRAMES = 10
DEFAULT_SHUTDOWN_SETTLE_FRAMES = 16
DEFAULT_SHUTDOWN_HOLD_FRAMES = 8
DEFAULT_SHUTDOWN_FPS = 12
DEFAULT_SHUTDOWN_FINAL_HOLD_SECONDS = 1.0
DEFAULT_RELEASE_PAUSE_SECONDS = 0.8
HANDLES_PLAYBACK_RECORDING = True


def _recordings_dir() -> Path:
    return Path(__file__).resolve().parent / "recordings"


def _elapsed_ms(started_at: float) -> int:
    return int((time.monotonic() - started_at) * 1000)


def _fail_motor_bus_uncertain(
    *,
    action: str,
    recording_name: str | None,
    rgb,
    error: str,
) -> int:
    print(error)
    record_standalone_playback(
        source="remote_control",
        initiator="remote_control",
        action=action,
        recording_name=recording_name,
        rgb=rgb,
        duration_ms=None,
        ok=False,
        error=error,
    )
    return 2


def _write_home_defaults(env_path: Path) -> None:
    upsert_env_value(env_path, "LELAMP_IDLE_RECORDING", "home_safe")
    upsert_env_value(env_path, "LELAMP_HOME_RECORDING", "home_safe")
    upsert_env_value(env_path, "LELAMP_STARTUP_RECORDING", "wake_up")
    upsert_env_value(env_path, "LELAMP_USE_HOME_POSE_RELATIVE", "true")


def _build_rgb_service(args) -> RGBService:
    return RGBService(
        led_count=args.led_count,
        led_pin=args.led_pin,
        led_freq_hz=args.led_freq_hz,
        led_dma=args.led_dma,
        led_brightness=args.led_brightness,
        led_invert=args.led_invert,
        led_channel=args.led_channel,
    )


def _build_animation_service(args) -> AnimationService:
    settings = load_runtime_settings()
    return AnimationService(
        port=args.port,
        lamp_id=args.id,
        fps=args.fps,
        duration=settings.interpolation_duration,
        idle_recording=settings.idle_recording,
        home_recording=settings.home_recording,
        use_home_pose_relative=settings.use_home_pose_relative,
    )


def _load_recording_actions(name: str) -> list[dict[str, float]]:
    recording_path = _recordings_dir() / f"{name}.csv"
    if not recording_path.exists():
        raise FileNotFoundError(f"Recording not found: {recording_path}")

    import csv

    with recording_path.open("r", encoding="utf-8", newline="") as handle:
        return [
            {key: float(value) for key, value in row.items() if key != "timestamp"}
            for row in csv.DictReader(handle)
        ]


def _load_first_pose(name: str) -> dict[str, float]:
    actions = _load_recording_actions(name)
    if not actions:
        raise ValueError(f"Recording has no frames: {name}")
    return actions[0]


def _set_torque_enabled(robot, enabled: bool) -> None:
    value = 1 if enabled else 0
    for joint in TORQUE_ENABLE_JOINTS:
        robot.bus.write("Torque_Enable", joint, value)


def _play_frames(robot, frames: list[dict[str, float]], *, fps: int) -> None:
    if fps <= 0:
        raise ValueError("fps must be positive")

    for frame in frames:
        robot.send_action(frame)
        time.sleep(1.0 / fps)


def _handle_show_config(args) -> int:
    payload = {
        "lamp_id": args.id,
        "port": args.port,
        "fps": args.fps,
        "model_provider": args.model_provider,
        "model_base_url": args.model_base_url,
        "model_name": args.model_name,
        "model_voice": args.model_voice,
        "led_count": args.led_count,
        "led_pin": args.led_pin,
        "enable_rgb": args.enable_rgb,
        "audio_user": args.audio_user,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _handle_list_recordings(args) -> int:
    service = MotorsService(port=args.port, lamp_id=args.id, fps=args.fps)
    recordings = service.get_available_recordings()

    if not recordings:
        print("No recordings found.")
        return 1

    for recording in recordings:
        print(recording)
    return 0


def _handle_play(args) -> int:
    try:
        service = _build_animation_service_with_proxy(lambda: _build_animation_service(args))
    except MotorBusClientError as exc:
        return _fail_motor_bus_uncertain(
            action="play",
            recording_name=args.name,
            rgb=None,
            error=str(exc),
        )
    recordings = set(service.get_available_recordings())
    started_at = time.monotonic()

    if args.name not in recordings:
        print(f"Recording not found: {args.name}")
        record_standalone_playback(
            source="remote_control",
            initiator="remote_control",
            action="play",
            recording_name=args.name,
            rgb=None,
            duration_ms=None,
            ok=False,
            error=f"Recording not found: {args.name}",
        )
        return 1

    service.start()
    try:
        service.dispatch("play", args.name)
        if not service.wait_until_playback_complete(timeout=args.timeout):
            print(f"Timed out waiting for recording to finish: {args.name}")
            record_standalone_playback(
                source="remote_control",
                initiator="remote_control",
                action="play",
                recording_name=args.name,
                rgb=None,
                duration_ms=_elapsed_ms(started_at),
                ok=False,
                error=f"Timed out waiting for recording to finish: {args.name}",
            )
            return 1
    finally:
        service.stop()

    record_standalone_playback(
        source="remote_control",
        initiator="remote_control",
        action="play",
        recording_name=args.name,
        rgb=None,
        duration_ms=_elapsed_ms(started_at),
        ok=True,
        error=None,
    )
    print(f"Finished playing recording: {args.name}")
    return 0


def _handle_solid(args) -> int:
    if not args.enable_rgb:
        print("RGB is disabled via LELAMP_ENABLE_RGB")
        record_standalone_playback(
            source="remote_control",
            initiator="remote_control",
            action="light_solid",
            recording_name=None,
            rgb=(args.red, args.green, args.blue),
            duration_ms=None,
            ok=False,
            error="RGB is disabled via LELAMP_ENABLE_RGB",
        )
        return 1

    try:
        service = _build_rgb_service_with_proxy(lambda: _build_rgb_service(args))
    except MotorBusClientError as exc:
        return _fail_motor_bus_uncertain(
            action="light_solid",
            recording_name=None,
            rgb=(args.red, args.green, args.blue),
            error=str(exc),
        )
    service.handle_event("solid", (args.red, args.green, args.blue))
    record_standalone_playback(
        source="remote_control",
        initiator="remote_control",
        action="light_solid",
        recording_name=None,
        rgb=(args.red, args.green, args.blue),
        duration_ms=None,
        ok=True,
        error=None,
    )
    print(f"Set RGB solid color to ({args.red}, {args.green}, {args.blue})")
    return 0


def _handle_clear(args) -> int:
    if not args.enable_rgb:
        print("RGB is disabled via LELAMP_ENABLE_RGB")
        record_standalone_playback(
            source="remote_control",
            initiator="remote_control",
            action="light_clear",
            recording_name=None,
            rgb=None,
            duration_ms=None,
            ok=False,
            error="RGB is disabled via LELAMP_ENABLE_RGB",
        )
        return 1

    try:
        service = _build_rgb_service_with_proxy(lambda: _build_rgb_service(args))
    except MotorBusClientError as exc:
        return _fail_motor_bus_uncertain(
            action="light_clear",
            recording_name=None,
            rgb=None,
            error=str(exc),
        )
    service.clear()
    record_standalone_playback(
        source="remote_control",
        initiator="remote_control",
        action="light_clear",
        recording_name=None,
        rgb=None,
        duration_ms=None,
        ok=True,
        error=None,
    )
    print("Cleared RGB LEDs")
    return 0


def _handle_capture_pose(args) -> int:
    from .follower import LeLampFollower, LeLampFollowerConfig

    robot = LeLampFollower(
        LeLampFollowerConfig(
            port=args.port,
            id=args.id,
            disable_torque_on_disconnect=False,
        )
    )

    try:
        robot.bus.connect()
        pose = robot.bus.sync_read("Present_Position", normalize=True, num_retry=2)
    finally:
        if robot.bus.is_connected:
            robot.bus.disconnect(disable_torque=False)

    recording_path = _recordings_dir() / f"{args.name}.csv"
    write_static_recording(
        recording_path,
        pose,
        fps=args.fps,
        frame_count=args.frame_count,
    )

    print(f"Captured current pose to {recording_path}")

    if args.set_defaults:
        env_path = Path(args.env_file)
        upsert_env_value(env_path, "LELAMP_IDLE_RECORDING", args.name)
        upsert_env_value(env_path, "LELAMP_STARTUP_RECORDING", args.name)
        upsert_env_value(env_path, "LELAMP_HOME_RECORDING", args.name)
        upsert_env_value(env_path, "LELAMP_USE_HOME_POSE_RELATIVE", "true")
        print(f"Updated defaults in {env_path}")

    return 0


def _handle_sync_pose_recordings(args) -> int:
    recording_paths = write_pose_recordings(
        _recordings_dir(),
        fps=args.fps,
        frame_count=args.frame_count,
    )

    for path in recording_paths:
        print(f"Wrote pose recording: {path}")

    if args.set_defaults:
        env_path = Path(args.env_file)
        _write_home_defaults(env_path)
        print(f"Updated defaults in {env_path}")

    return 0


def _handle_startup(args) -> int:
    startup_recording = args.recording
    started_at = time.monotonic()
    # Only block direct-hardware startup when the agent actually owns the
    # serial port (motor_ok == True). If the agent is alive but its
    # AnimationService never came up, we let the caller try a direct
    # recovery — matching the motor-domain fallback used by build_animation_service.
    try:
        motor_sentinel = current_sentinel(require=REQUIRE_MOTOR)
    except MotorBusClientError as exc:
        return _fail_motor_bus_uncertain(
            action="startup",
            recording_name=startup_recording,
            rgb=None,
            error=str(exc),
        )
    if motor_sentinel is not None:
        print(
            "Voice agent is running and already owns the serial port; "
            "staged startup via remote_control is not available. "
            "Use the dashboard 'startup' action (routes via motor bus) or "
            "stop the agent first."
        )
        record_standalone_playback(
            source="remote_control",
            initiator="remote_control",
            action="startup",
            recording_name=startup_recording,
            rgb=None,
            duration_ms=None,
            ok=False,
            error="Voice agent is running and already owns the serial port",
        )
        return 2

    from .follower import LeLampFollower, LeLampFollowerConfig

    home_pose = _load_first_pose(args.home_recording)
    wake_up_actions = _load_recording_actions(startup_recording)

    robot = LeLampFollower(
        LeLampFollowerConfig(
            port=args.port,
            id=args.id,
            disable_torque_on_disconnect=False,
        )
    )

    rgb_service: RGBService | None = _build_rgb_service(args) if args.enable_rgb else None

    try:
        robot.connect(calibrate=False)
        _set_torque_enabled(robot, True)

        current_raw = robot.bus.sync_read("Present_Position")
        current_pose = {f"{joint}.pos": value for joint, value in current_raw.items()}
        startup_frames = build_dynamic_startup_actions(
            current_pose,
            home_pose,
            wake_up_actions,
            settle_frame_count=args.settle_frames,
            settle_hold_frames=args.settle_hold_frames,
        )

        if rgb_service is not None:
            rgb_service.handle_event("solid", STARTUP_WARM_RGB)

        settle_count = args.settle_frames + args.settle_hold_frames
        for index, frame in enumerate(startup_frames):
            robot.send_action(frame)
            if index < settle_count:
                time.sleep(1.0 / args.settle_fps)
            else:
                time.sleep(1.0 / args.wake_fps)

        time.sleep(args.post_wake_hold)
    except Exception as exc:
        record_standalone_playback(
            source="remote_control",
            initiator="remote_control",
            action="startup",
            recording_name=startup_recording,
            rgb=None,
            duration_ms=_elapsed_ms(started_at),
            ok=False,
            error=str(exc),
        )
        raise
    finally:
        if robot.is_connected:
            robot.disconnect()
        if rgb_service is not None:
            rgb_service.stop()

    record_standalone_playback(
        source="remote_control",
        initiator="remote_control",
        action="startup",
        recording_name=startup_recording,
        rgb=None,
        duration_ms=_elapsed_ms(started_at),
        ok=True,
        error=None,
    )
    print(f"Finished startup choreography: {startup_recording}")
    return 0


def _handle_shutdown(args) -> int:
    started_at = time.monotonic()
    # Same motor-domain rule as _handle_startup: only refuse when the agent's
    # motor path is actually live. motor_ok=False means the agent never
    # acquired the bus, so direct torque-release can still run.
    try:
        motor_sentinel = current_sentinel(require=REQUIRE_MOTOR)
    except MotorBusClientError as exc:
        return _fail_motor_bus_uncertain(
            action="shutdown_pose",
            recording_name=args.recording,
            rgb=None,
            error=str(exc),
        )
    if motor_sentinel is not None:
        print(
            "Voice agent is running and already owns the serial port; "
            "staged shutdown with torque release is not available via "
            "remote_control. Stop the agent first, or use the dashboard "
            "'shutdown_pose' action (plays power_off via motor bus but "
            "keeps torque enabled)."
        )
        record_standalone_playback(
            source="remote_control",
            initiator="remote_control",
            action="shutdown_pose",
            recording_name=args.recording,
            rgb=None,
            duration_ms=None,
            ok=False,
            error="Voice agent is running and already owns the serial port",
        )
        return 2

    from .follower import LeLampFollower, LeLampFollowerConfig

    power_off_pose = _load_first_pose(args.recording)
    robot = LeLampFollower(
        LeLampFollowerConfig(
            port=args.port,
            id=args.id,
            disable_torque_on_disconnect=False,
        )
    )
    rgb_service: RGBService | None = _build_rgb_service(args) if args.enable_rgb else None

    try:
        robot.connect(calibrate=False)
        _set_torque_enabled(robot, True)

        current_raw = robot.bus.sync_read("Present_Position")
        current_pose = {f"{joint}.pos": value for joint, value in current_raw.items()}
        shutdown_frames = build_staged_shutdown_actions(
            current_pose,
            power_off_pose,
            prepare_fraction=args.prepare_fraction,
            prepare_frames_per_joint=args.prepare_frames,
            settle_frames_per_joint=args.settle_frames,
            hold_frames=args.hold_frames,
        )

        _play_frames(robot, shutdown_frames, fps=args.fps)
        if shutdown_frames:
            robot.send_action(shutdown_frames[-1])
        time.sleep(args.final_hold)

        for joint in SHUTDOWN_RELEASE_ORDER:
            robot.bus.write("Torque_Enable", joint, 0)
            time.sleep(args.release_pause)

        if rgb_service is not None and not args.keep_led_on:
            rgb_service.clear()
    except Exception as exc:
        record_standalone_playback(
            source="remote_control",
            initiator="remote_control",
            action="shutdown_pose",
            recording_name=args.recording,
            rgb=None,
            duration_ms=_elapsed_ms(started_at),
            ok=False,
            error=str(exc),
        )
        raise
    finally:
        if robot.bus.is_connected:
            robot.bus.disconnect(disable_torque=False)
        if rgb_service is not None:
            rgb_service.stop()

    record_standalone_playback(
        source="remote_control",
        initiator="remote_control",
        action="shutdown_pose",
        recording_name=args.recording,
        rgb=None,
        duration_ms=_elapsed_ms(started_at),
        ok=True,
        error=None,
    )
    print(f"Finished shutdown choreography: {args.recording}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    load_dotenv(dotenv_path=".env")
    settings = load_runtime_settings()

    parser = argparse.ArgumentParser(description="High-level LeLamp control helpers for automation and OpenClaw")
    parser.add_argument("--id", default=settings.lamp_id, help="Lamp ID")
    parser.add_argument("--port", default=settings.port, help="Serial port for the lamp")
    parser.add_argument("--fps", type=int, default=settings.fps, help="Motor playback FPS")
    parser.add_argument("--audio-user", default=settings.audio_user, help="Current audio control user")
    parser.add_argument("--model-provider", default=settings.model_provider, help="Realtime model provider")
    parser.add_argument("--model-base-url", default=settings.model_base_url, help="Realtime model base URL")
    parser.add_argument("--model-name", default=settings.model_name, help="Realtime model name")
    parser.add_argument("--model-voice", default=settings.model_voice, help="Realtime voice name")
    parser.add_argument("--led-count", type=int, default=settings.led_count, help="LED count")
    parser.add_argument("--led-pin", type=int, default=settings.led_pin, help="LED GPIO pin")
    parser.add_argument("--led-freq-hz", type=int, default=settings.led_freq_hz, help="LED frequency")
    parser.add_argument("--led-dma", type=int, default=settings.led_dma, help="LED DMA channel")
    parser.add_argument("--led-brightness", type=int, default=settings.led_brightness, help="LED brightness")
    parser.add_argument("--led-invert", action="store_true", default=settings.led_invert, help="Invert LED signal")
    parser.add_argument("--led-channel", type=int, default=settings.led_channel, help="LED channel")
    parser.add_argument("--enable-rgb", dest="enable_rgb", action="store_true", default=settings.enable_rgb, help="Enable RGB output")
    parser.add_argument("--disable-rgb", dest="enable_rgb", action="store_false", help="Disable RGB output")

    subparsers = parser.add_subparsers(dest="command", required=True)

    show_config = subparsers.add_parser("show-config", help="Print the resolved control configuration")
    show_config.set_defaults(handler=_handle_show_config)

    list_recordings = subparsers.add_parser("list-recordings", help="List available motion recordings")
    list_recordings.set_defaults(handler=_handle_list_recordings)

    play = subparsers.add_parser("play", help="Play a named motion recording")
    play.add_argument("name", help="Recording name")
    play.add_argument("--timeout", type=float, default=120.0, help="Playback timeout in seconds")
    play.set_defaults(handler=_handle_play)

    solid = subparsers.add_parser("solid", help="Set the LEDs to a solid RGB color")
    solid.add_argument("red", type=int)
    solid.add_argument("green", type=int)
    solid.add_argument("blue", type=int)
    solid.set_defaults(handler=_handle_solid)

    clear = subparsers.add_parser("clear", help="Turn off all LEDs")
    clear.set_defaults(handler=_handle_clear)

    capture_pose = subparsers.add_parser("capture-pose", help="Capture the current joint pose into a static recording")
    capture_pose.add_argument("name", help="Recording name to write")
    capture_pose.add_argument("--frame-count", type=int, default=30, help="Number of identical frames to write")
    capture_pose.add_argument("--env-file", default=".env", help="Env file to update when --set-defaults is used")
    capture_pose.add_argument("--set-defaults", action="store_true", help="Also set idle/startup recording defaults")
    capture_pose.set_defaults(handler=_handle_capture_pose)

    sync_pose_recordings = subparsers.add_parser(
        "sync-pose-recordings",
        help="Regenerate the static home/sleep/shutdown pose recordings from the checked-in presets",
    )
    sync_pose_recordings.add_argument("--frame-count", type=int, default=30, help="Number of identical frames to write")
    sync_pose_recordings.add_argument("--env-file", default=".env", help="Env file to update when --set-defaults is used")
    sync_pose_recordings.add_argument("--set-defaults", action="store_true", help="Also set the home-safe runtime defaults")
    sync_pose_recordings.set_defaults(handler=_handle_sync_pose_recordings)

    startup = subparsers.add_parser("startup", help="Run the formal startup choreography")
    startup.add_argument("--recording", default="wake_up", help="Recording name to use as the wake-up body motion")
    startup.add_argument("--home-recording", default=settings.home_recording, help="Static pose used as the startup anchor")
    startup.add_argument("--settle-frames", type=int, default=DEFAULT_STARTUP_SETTLE_FRAMES, help="Frame count for the slow startup settle")
    startup.add_argument("--settle-hold-frames", type=int, default=DEFAULT_STARTUP_HOLD_FRAMES, help="How many startup anchor frames to hold before wake-up")
    startup.add_argument("--settle-fps", type=int, default=DEFAULT_STARTUP_FPS, help="FPS used during the settle phase")
    startup.add_argument("--wake-fps", type=int, default=DEFAULT_WAKE_FPS, help="FPS used while replaying the wake-up recording")
    startup.add_argument("--post-wake-hold", type=float, default=DEFAULT_POST_WAKE_HOLD_SECONDS, help="Seconds to hold after wake-up")
    startup.set_defaults(handler=_handle_startup)

    shutdown = subparsers.add_parser("shutdown", help="Run the formal shutdown choreography and release torque")
    shutdown.add_argument("--recording", default="power_off", help="Static pose used as the final shutdown pose")
    shutdown.add_argument("--prepare-fraction", type=float, default=DEFAULT_SHUTDOWN_PREPARE_FRACTION, help="How far to move during the first small-amplitude shutdown phase")
    shutdown.add_argument("--prepare-frames", type=int, default=DEFAULT_SHUTDOWN_PREPARE_FRAMES, help="Frames per joint for the first shutdown phase")
    shutdown.add_argument("--settle-frames", type=int, default=DEFAULT_SHUTDOWN_SETTLE_FRAMES, help="Frames per joint for the final settle into shutdown pose")
    shutdown.add_argument("--hold-frames", type=int, default=DEFAULT_SHUTDOWN_HOLD_FRAMES, help="Frames to hold the intermediate shutdown pose")
    shutdown.add_argument("--final-hold", type=float, default=DEFAULT_SHUTDOWN_FINAL_HOLD_SECONDS, help="Seconds to hold the final shutdown pose before torque release")
    shutdown.add_argument("--release-pause", type=float, default=DEFAULT_RELEASE_PAUSE_SECONDS, help="Seconds to wait between releasing joints")
    shutdown.add_argument("--keep-led-on", action="store_true", help="Do not clear the LEDs after the shutdown choreography")
    shutdown.set_defaults(handler=_handle_shutdown)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
