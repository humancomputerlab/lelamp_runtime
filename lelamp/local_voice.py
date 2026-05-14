from __future__ import annotations

import logging
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from livekit.agents.voice import chat_cli as livekit_chat_cli

from lelamp.voice_telemetry import configure_voice_telemetry


logger = logging.getLogger(__name__)

DEFAULT_DEVICE_KEYWORDS = ("seeed", "respeaker", "dmixer", "dsnoop0")
FALLBACK_INPUT_DEVICE_KEYWORDS = ("pulse", "default")
FALLBACK_OUTPUT_DEVICE_KEYWORDS = ("default", "pulse")
GENERIC_DEVICE_KEYWORDS = ("default", "pulse")
UNSTABLE_INPUT_DEVICE_KEYWORDS = ("dsnoop0_raw", "raw")
UNSTABLE_OUTPUT_DEVICE_KEYWORDS = ("dmixer_raw",)
CONSOLE_MODEL_SAMPLE_RATE = 24000
CONSOLE_DEVICE_SAMPLE_RATE = 48000
CONSOLE_MODEL_FRAME_SAMPLES = CONSOLE_MODEL_SAMPLE_RATE // 100
CONSOLE_DEVICE_FRAME_SAMPLES = CONSOLE_DEVICE_SAMPLE_RATE // 100
CONSOLE_DEVICE_BLOCK_SIZE = CONSOLE_DEVICE_FRAME_SAMPLES * 10
_CONSOLE_ENABLE_APM = False
_CONSOLE_OUTPUT_SUPPRESSION_SECONDS = 0.35
_CONSOLE_AUTO_CALIBRATE = True
_CONSOLE_CALIBRATION_DURATION_SECONDS = 1.6
_CONSOLE_CALIBRATION_MARGIN_DB = 8.0
_CONSOLE_VOICE_STATE_PATH = "/tmp/lelamp-voice-state.json"


@dataclass(frozen=True)
class LocalTurnDetectorConfig:
    speech_threshold_db: float = -54.0
    silence_duration_s: float = 0.4
    min_speech_duration_s: float = 0.2
    commit_cooldown_s: float = 0.75
    speech_start_duration_s: float = 0.08


_CONSOLE_TURN_DETECTOR_CONFIG = LocalTurnDetectorConfig()


@dataclass
class OutputSuppressionGate:
    suppression_seconds: float = 0.35
    _suppress_until: float = 0.0

    def mark_output(self, now: float) -> None:
        self._suppress_until = max(self._suppress_until, now + self.suppression_seconds)

    def should_suppress(self, now: float) -> bool:
        return now < self._suppress_until


def should_suppress_console_input(
    *,
    playback_in_progress: bool,
    output_gate: OutputSuppressionGate,
    now: float,
) -> bool:
    return playback_in_progress or output_gate.should_suppress(now)


@dataclass
class AmbientNoiseCalibrator:
    baseline_threshold_db: float
    calibration_duration_s: float = 1.6
    calibration_margin_db: float = 8.0
    min_threshold_db: float = -66.0
    max_threshold_db: float = -42.0
    enabled: bool = True
    _started_at: float | None = None
    _samples: list[float] = field(default_factory=list)
    _completed: bool = False

    @property
    def completed(self) -> bool:
        return self._completed or not self.enabled

    @property
    def progress(self) -> float:
        if not self.enabled or self._completed:
            return 1.0
        if self._started_at is None or self.calibration_duration_s <= 0:
            return 0.0
        return min(max((time.monotonic() - self._started_at) / self.calibration_duration_s, 0.0), 1.0)

    def observe(self, level_db: float, now: float) -> dict[str, float | str] | None:
        if not self.enabled or self._completed:
            return None

        if self._started_at is None:
            self._started_at = now

        self._samples.append(level_db)

        if now - self._started_at < self.calibration_duration_s:
            return None

        baseline_threshold_db = min(
            max(self.baseline_threshold_db, self.min_threshold_db),
            self.max_threshold_db,
        )
        noise_floor_db = statistics.median(self._samples) if self._samples else baseline_threshold_db
        threshold_candidate = noise_floor_db + self.calibration_margin_db
        speech_threshold_db = min(max(threshold_candidate, self.min_threshold_db), self.max_threshold_db)
        self._completed = True

        return {
            "status": "ready",
            "noise_floor_db": float(noise_floor_db),
            "speech_threshold_db": float(speech_threshold_db),
        }


def choose_preferred_device(
    devices: Sequence[Mapping[str, Any]],
    *,
    output: bool,
    preferred_keywords: Sequence[str] = DEFAULT_DEVICE_KEYWORDS,
    avoid_keywords: Sequence[str] = (),
) -> int | None:
    capability_key = "max_output_channels" if output else "max_input_channels"
    normalized_keywords = tuple(keyword.lower() for keyword in preferred_keywords if keyword)
    normalized_avoid_keywords = tuple(keyword.lower() for keyword in avoid_keywords if keyword)

    for index, device in enumerate(devices):
        channel_count = int(device.get(capability_key, 0) or 0)
        if channel_count <= 0:
            continue

        name = str(device.get("name", "")).lower()
        if any(keyword in name for keyword in normalized_avoid_keywords):
            continue
        if any(keyword in name for keyword in normalized_keywords):
            return index

    return None


def choose_first_available_device(
    devices: Sequence[Mapping[str, Any]],
    *,
    output: bool,
    avoid_keywords: Sequence[str] = (),
) -> int | None:
    capability_key = "max_output_channels" if output else "max_input_channels"
    normalized_avoid_keywords = tuple(keyword.lower() for keyword in avoid_keywords if keyword)

    for index, device in enumerate(devices):
        channel_count = int(device.get(capability_key, 0) or 0)
        if channel_count <= 0:
            continue

        name = str(device.get("name", "")).lower()
        if any(keyword in name for keyword in normalized_avoid_keywords):
            continue

        return index

    return None


def is_resolved_device_usable(
    devices: Sequence[Mapping[str, Any]],
    *,
    index: int | None,
    output: bool,
    avoid_keywords: Sequence[str] = (),
) -> bool:
    if index is None or index < 0 or index >= len(devices):
        return False

    capability_key = "max_output_channels" if output else "max_input_channels"
    channel_count = int(devices[index].get(capability_key, 0) or 0)
    if channel_count <= 0:
        return False

    name = str(devices[index].get("name", "")).lower()
    normalized_avoid_keywords = tuple(keyword.lower() for keyword in avoid_keywords if keyword)
    return not any(keyword in name for keyword in normalized_avoid_keywords)


def is_generic_device(
    devices: Sequence[Mapping[str, Any]],
    *,
    index: int | None,
) -> bool:
    if index is None or index < 0 or index >= len(devices):
        return False

    name = str(devices[index].get("name", "")).lower()
    return any(keyword in name for keyword in GENERIC_DEVICE_KEYWORDS)


def validate_sounddevice_candidate(
    sd_module: Any,
    *,
    index: int | None,
    output: bool,
    sample_rate: int = CONSOLE_DEVICE_SAMPLE_RATE,
) -> bool:
    if index is None or index < 0:
        return False

    try:
        if output:
            sd_module.check_output_settings(
                device=index,
                channels=1,
                samplerate=sample_rate,
                dtype="int16",
            )
        else:
            sd_module.check_input_settings(
                device=index,
                channels=1,
                samplerate=sample_rate,
                dtype="int16",
            )
    except Exception:
        return False

    return True


def resolve_console_devices(
    *,
    current_devices: tuple[int | None, int | None],
    devices: Sequence[Mapping[str, Any]],
    preferred_keywords: Sequence[str] = DEFAULT_DEVICE_KEYWORDS,
) -> tuple[int | None, int | None]:
    current_input, current_output = current_devices
    preferred_input = choose_preferred_device(
        devices,
        output=False,
        preferred_keywords=preferred_keywords,
        avoid_keywords=UNSTABLE_INPUT_DEVICE_KEYWORDS,
    )
    preferred_output = choose_preferred_device(
        devices,
        output=True,
        preferred_keywords=preferred_keywords,
        avoid_keywords=UNSTABLE_OUTPUT_DEVICE_KEYWORDS,
    )

    resolved_input = (
        current_input
        if is_resolved_device_usable(
            devices,
            index=current_input,
            output=False,
            avoid_keywords=UNSTABLE_INPUT_DEVICE_KEYWORDS,
        )
        else None
    )
    if (
        resolved_input is not None
        and is_generic_device(devices, index=resolved_input)
        and preferred_input is not None
        and preferred_input != resolved_input
    ):
        resolved_input = None
    resolved_output = (
        current_output
        if is_resolved_device_usable(
            devices,
            index=current_output,
            output=True,
            avoid_keywords=UNSTABLE_OUTPUT_DEVICE_KEYWORDS,
        )
        else None
    )
    if (
        resolved_output is not None
        and is_generic_device(devices, index=resolved_output)
        and preferred_output is not None
        and preferred_output != resolved_output
    ):
        resolved_output = None

    if resolved_input is None or resolved_input < 0:
        resolved_input = preferred_input
    if resolved_input is None or resolved_input < 0:
        resolved_input = choose_preferred_device(
            devices,
            output=False,
            preferred_keywords=FALLBACK_INPUT_DEVICE_KEYWORDS,
            avoid_keywords=UNSTABLE_INPUT_DEVICE_KEYWORDS,
        )
    if resolved_input is None or resolved_input < 0:
        resolved_input = choose_first_available_device(
            devices,
            output=False,
            avoid_keywords=UNSTABLE_INPUT_DEVICE_KEYWORDS,
        )

    if resolved_output is None or resolved_output < 0:
        resolved_output = preferred_output
    if resolved_output is None or resolved_output < 0:
        resolved_output = choose_preferred_device(
            devices,
            output=True,
            preferred_keywords=FALLBACK_OUTPUT_DEVICE_KEYWORDS,
            avoid_keywords=UNSTABLE_OUTPUT_DEVICE_KEYWORDS,
        )
    if resolved_output is None or resolved_output < 0:
        resolved_output = choose_first_available_device(
            devices,
            output=True,
            avoid_keywords=UNSTABLE_OUTPUT_DEVICE_KEYWORDS,
        )

    return resolved_input, resolved_output


@dataclass
class LocalTurnDetector:
    speech_threshold_db: float = -54.0
    silence_duration_s: float = 0.4
    min_speech_duration_s: float = 0.2
    commit_cooldown_s: float = 0.75
    speech_start_duration_s: float = 0.08
    _speech_started_at: float | None = None
    _last_voice_at: float | None = None
    _cooldown_until: float = 0.0
    _candidate_started_at: float | None = None

    @property
    def speaking(self) -> bool:
        return self._speech_started_at is not None

    def update(self, level_db: float, now: float) -> str | None:
        speaking = level_db >= self.speech_threshold_db

        if self._speech_started_at is None:
            if not speaking or now < self._cooldown_until:
                self._candidate_started_at = None
                return None

            if self._candidate_started_at is None:
                self._candidate_started_at = now

            if now - self._candidate_started_at < self.speech_start_duration_s:
                return None

            self._speech_started_at = self._candidate_started_at
            self._last_voice_at = now
            self._candidate_started_at = None
            logger.debug(
                "Local speech started",
                extra={
                    "level_db": round(level_db, 2),
                    "speech_threshold_db": self.speech_threshold_db,
                },
            )
            return None

        if speaking:
            self._last_voice_at = now
            return None

        if self._last_voice_at is None:
            self._reset(now)
            return None

        silence_elapsed = now - self._last_voice_at
        if silence_elapsed < self.silence_duration_s:
            return None

        speech_duration = self._last_voice_at - self._speech_started_at
        action = "commit" if speech_duration >= self.min_speech_duration_s else "clear"
        logger.debug(
            "Local speech finished",
            extra={
                "action": action,
                "speech_duration_s": round(speech_duration, 3),
                "silence_duration_s": self.silence_duration_s,
                "level_db": round(level_db, 2),
            },
        )
        self._reset(now)
        return action

    def _reset(self, now: float) -> None:
        self._speech_started_at = None
        self._last_voice_at = None
        self._candidate_started_at = None
        self._cooldown_until = now + self.commit_cooldown_s


def _install_console_audio_patch_impl() -> None:
    global _CONSOLE_ENABLE_APM

    if getattr(livekit_chat_cli.ChatCLI, "_lelamp_local_voice_patch", False):
        return

    base_cls = livekit_chat_cli.ChatCLI

    class LeLampChatCLI(base_cls):
        _lelamp_local_voice_patch = True

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._lelamp_turn_detector = LocalTurnDetector(
                speech_threshold_db=_CONSOLE_TURN_DETECTOR_CONFIG.speech_threshold_db,
                silence_duration_s=_CONSOLE_TURN_DETECTOR_CONFIG.silence_duration_s,
                min_speech_duration_s=_CONSOLE_TURN_DETECTOR_CONFIG.min_speech_duration_s,
                commit_cooldown_s=_CONSOLE_TURN_DETECTOR_CONFIG.commit_cooldown_s,
                speech_start_duration_s=_CONSOLE_TURN_DETECTOR_CONFIG.speech_start_duration_s,
            )
            self._lelamp_enable_apm = _CONSOLE_ENABLE_APM
            self._lelamp_output_gate = OutputSuppressionGate(
                suppression_seconds=_CONSOLE_OUTPUT_SUPPRESSION_SECONDS,
            )
            self._lelamp_calibrator = AmbientNoiseCalibrator(
                baseline_threshold_db=self._lelamp_turn_detector.speech_threshold_db,
                calibration_duration_s=_CONSOLE_CALIBRATION_DURATION_SECONDS,
                calibration_margin_db=_CONSOLE_CALIBRATION_MARGIN_DB,
                enabled=_CONSOLE_AUTO_CALIBRATE,
            )
            self._lelamp_voice_telemetry = configure_voice_telemetry(_CONSOLE_VOICE_STATE_PATH)
            self._lelamp_input_suppressed = False
            self._lelamp_voice_telemetry.update(
                status="running" if _CONSOLE_AUTO_CALIBRATE else "ready",
                local_state="calibrating" if _CONSOLE_AUTO_CALIBRATE else "idle",
                speech_threshold_db=self._lelamp_turn_detector.speech_threshold_db,
                calibration_enabled=_CONSOLE_AUTO_CALIBRATE,
                calibration_progress=0.0,
                last_result="voice telemetry initialized",
                force=True,
            )

        async def start(self) -> None:
            self._configure_sounddevice_defaults()
            await super().start()

        def _configure_sounddevice_defaults(self) -> None:
            try:
                import sounddevice as sd
            except ImportError:
                logger.warning("sounddevice is unavailable, skipping preferred device selection")
                return

            devices = sd.query_devices()
            previous_devices = tuple(sd.default.device)
            input_index, output_index = resolve_console_devices(
                current_devices=previous_devices,
                devices=devices,
            )

            input_candidates = [input_index]
            if input_index != previous_devices[0]:
                input_candidates.append(previous_devices[0])
            input_candidates.extend(
                [
                    choose_preferred_device(
                        devices,
                        output=False,
                        preferred_keywords=FALLBACK_INPUT_DEVICE_KEYWORDS,
                        avoid_keywords=UNSTABLE_INPUT_DEVICE_KEYWORDS,
                    ),
                    choose_preferred_device(
                        devices,
                        output=False,
                        preferred_keywords=DEFAULT_DEVICE_KEYWORDS,
                        avoid_keywords=UNSTABLE_INPUT_DEVICE_KEYWORDS,
                    ),
                    choose_first_available_device(
                        devices,
                        output=False,
                        avoid_keywords=UNSTABLE_INPUT_DEVICE_KEYWORDS,
                    ),
                ]
            )

            output_candidates = [output_index]
            if output_index != previous_devices[1]:
                output_candidates.append(previous_devices[1])
            output_candidates.extend(
                [
                    choose_preferred_device(
                        devices,
                        output=True,
                        preferred_keywords=FALLBACK_OUTPUT_DEVICE_KEYWORDS,
                        avoid_keywords=UNSTABLE_OUTPUT_DEVICE_KEYWORDS,
                    ),
                    choose_preferred_device(
                        devices,
                        output=True,
                        preferred_keywords=DEFAULT_DEVICE_KEYWORDS,
                        avoid_keywords=UNSTABLE_OUTPUT_DEVICE_KEYWORDS,
                    ),
                    choose_first_available_device(
                        devices,
                        output=True,
                        avoid_keywords=UNSTABLE_OUTPUT_DEVICE_KEYWORDS,
                    ),
                ]
            )

            input_index = next(
                (
                    candidate
                    for candidate in input_candidates
                    if validate_sounddevice_candidate(
                        sd,
                        index=candidate,
                        output=False,
                    )
                ),
                None,
            )
            output_index = next(
                (
                    candidate
                    for candidate in output_candidates
                    if validate_sounddevice_candidate(
                        sd,
                        index=candidate,
                        output=True,
                    )
                ),
                None,
            )

            if (input_index, output_index) != previous_devices:
                sd.default.device = (input_index, output_index)

            logger.info(
                "Console audio devices resolved",
                extra={
                    "input_device": input_index,
                    "output_device": output_index,
                    "input_name": _device_name(devices, input_index),
                    "output_name": _device_name(devices, output_index),
                    "previous_input_device": previous_devices[0],
                    "previous_output_device": previous_devices[1],
                    "apm_enabled": self._lelamp_enable_apm,
                    "speech_threshold_db": self._lelamp_turn_detector.speech_threshold_db,
                    "silence_duration_s": self._lelamp_turn_detector.silence_duration_s,
                    "min_speech_duration_s": self._lelamp_turn_detector.min_speech_duration_s,
                    "commit_cooldown_s": self._lelamp_turn_detector.commit_cooldown_s,
                    "speech_start_duration_s": self._lelamp_turn_detector.speech_start_duration_s,
                    "output_suppression_s": self._lelamp_output_gate.suppression_seconds,
                },
            )

        def _update_microphone(self, *, enable: bool) -> None:  # type: ignore[override]
            if not enable:
                if self._input_stream is not None:
                    self._input_stream.stop()
                    self._input_stream.close()
                    self._input_stream = None
                self._session.input.audio = None
                return

            try:
                import sounddevice as sd
            except ImportError:
                logger.warning("sounddevice is unavailable, microphone disabled")
                self._update_voice_telemetry(
                    status="error",
                    local_state="mic_unavailable",
                    last_result="sounddevice unavailable",
                    force=True,
                )
                return

            input_device, _ = sd.default.device
            if input_device is None or input_device < 0:
                logger.warning("No input audio device resolved for console microphone")
                self._update_voice_telemetry(
                    status="error",
                    local_state="mic_unavailable",
                    last_result="no input audio device resolved",
                    force=True,
                )
                self._session.input.audio = None
                return

            try:
                device_info = sd.query_devices(input_device)
                assert isinstance(device_info, dict)

                self._input_device_name = device_info.get("name", "Microphone")
                self._input_stream = sd.InputStream(
                    callback=self._sd_input_callback,
                    dtype="int16",
                    channels=1,
                    device=input_device,
                    samplerate=CONSOLE_DEVICE_SAMPLE_RATE,
                    blocksize=CONSOLE_DEVICE_BLOCK_SIZE,
                )
                self._input_stream.start()
                self._session.input.audio = self._input_audio
            except Exception as exc:
                logger.exception("Failed to open console microphone")
                self._update_voice_telemetry(
                    status="error",
                    local_state="mic_unavailable",
                    last_result=f"microphone unavailable: {exc}",
                    force=True,
                )
                self._session.input.audio = None
                if self._input_stream is not None:
                    try:
                        self._input_stream.close()
                    except Exception:
                        pass
                    self._input_stream = None

        def _update_speaker(self, *, enable: bool) -> None:  # type: ignore[override]
            if not enable:
                if self._output_stream is not None:
                    self._output_stream.stop()
                    self._output_stream.close()
                    self._output_stream = None
                self._session.output.audio = None
                return

            try:
                import sounddevice as sd
            except ImportError:
                logger.warning("sounddevice is unavailable, speaker disabled")
                self._session.output.audio = None
                return

            _, output_device = sd.default.device
            if output_device is None or output_device < 0:
                logger.warning("No output audio device resolved for console speaker")
                self._session.output.audio = None
                return

            try:
                self._output_stream = sd.OutputStream(
                    callback=self._sd_output_callback,
                    dtype="int16",
                    channels=1,
                    device=output_device,
                    samplerate=CONSOLE_DEVICE_SAMPLE_RATE,
                    blocksize=CONSOLE_DEVICE_BLOCK_SIZE,
                )
                self._output_stream.start()
                self._session.output.audio = self._output_audio
            except Exception as exc:
                logger.exception("Failed to open console speaker")
                self._session.output.audio = None
                if self._output_stream is not None:
                    try:
                        self._output_stream.close()
                    except Exception:
                        pass
                    self._output_stream = None
                self._update_voice_telemetry(
                    last_result=f"speaker unavailable: {exc}",
                    force=True,
                )

        def _sd_input_callback(self, indata, frame_count, time_info, *args) -> None:  # type: ignore[override]
            now = time.monotonic()
            total_delay = self._output_delay + (time_info.currentTime - time_info.inputBufferAdcTime)
            if self._lelamp_enable_apm:
                try:
                    self._apm.set_stream_delay_ms(int(total_delay * 1000))
                except RuntimeError:
                    pass

            num_frames = frame_count // CONSOLE_DEVICE_FRAME_SAMPLES

            for i in range(num_frames):
                start = i * CONSOLE_DEVICE_FRAME_SAMPLES
                end = start + CONSOLE_DEVICE_FRAME_SAMPLES
                capture_chunk = livekit_chat_cli.np.asarray(indata[start:end]).reshape(-1)
                resampled_chunk = _resample_int16(
                    capture_chunk,
                    src_rate=CONSOLE_DEVICE_SAMPLE_RATE,
                    dst_rate=CONSOLE_MODEL_SAMPLE_RATE,
                )
                raw_frame = livekit_chat_cli.rtc.AudioFrame(
                    data=resampled_chunk.tobytes(),
                    samples_per_channel=len(resampled_chunk),
                    sample_rate=CONSOLE_MODEL_SAMPLE_RATE,
                    num_channels=1,
                )

                frame_to_send = raw_frame
                if self._lelamp_enable_apm:
                    try:
                        self._apm.process_stream(raw_frame)
                    except RuntimeError:
                        logger.debug("Console APM failed, falling back to raw capture", exc_info=True)
                    else:
                        frame_to_send = raw_frame

                rms = _frame_rms_db(frame_to_send)
                self._micro_db = rms

                playback_in_progress = not self._audio_sink._flush_complete.is_set()
                if should_suppress_console_input(
                    playback_in_progress=playback_in_progress,
                    output_gate=self._lelamp_output_gate,
                    now=now,
                ):
                    if not self._lelamp_input_suppressed:
                        logger.debug("Suppressing local mic input during lamp playback")
                        self._lelamp_turn_detector._reset(now)
                        self._loop.call_soon_threadsafe(self._clear_user_turn_safe)
                    self._update_voice_telemetry(
                        status="running",
                        local_state="suppressed",
                        last_level_db=rms,
                        speech_threshold_db=self._lelamp_turn_detector.speech_threshold_db,
                        calibration_progress=self._lelamp_calibrator.progress,
                        force=not self._lelamp_input_suppressed,
                    )
                    self._lelamp_input_suppressed = True
                    continue

                self._lelamp_input_suppressed = False

                if not self._lelamp_calibrator.completed:
                    calibration = self._lelamp_calibrator.observe(rms, now)
                    if calibration is None:
                        self._update_voice_telemetry(
                            status="running",
                            local_state="calibrating",
                            last_level_db=rms,
                            speech_threshold_db=self._lelamp_turn_detector.speech_threshold_db,
                            calibration_progress=self._lelamp_calibrator.progress,
                        )
                    else:
                        self._lelamp_turn_detector.speech_threshold_db = float(
                            calibration["speech_threshold_db"]
                        )
                        self._update_voice_telemetry(
                            status="ready",
                            local_state="idle",
                            last_level_db=rms,
                            speech_threshold_db=self._lelamp_turn_detector.speech_threshold_db,
                            noise_floor_db=float(calibration["noise_floor_db"]),
                            calibration_progress=1.0,
                            last_result="voice calibration ready",
                            force=True,
                        )
                    continue

                self._loop.call_soon_threadsafe(self._audio_input_ch.send_nowait, frame_to_send)

            if self._lelamp_input_suppressed:
                return

            was_speaking = self._lelamp_turn_detector.speaking
            action = self._lelamp_turn_detector.update(self._micro_db, now)
            is_speaking = self._lelamp_turn_detector.speaking
            if not was_speaking and is_speaking:
                self._update_voice_telemetry(
                    status="running",
                    local_state="listening",
                    last_level_db=self._micro_db,
                    speech_threshold_db=self._lelamp_turn_detector.speech_threshold_db,
                    last_speech_started_at_ms=_wall_time_ms(),
                    force=True,
                )
            elif was_speaking and not is_speaking:
                self._update_voice_telemetry(
                    last_speech_finished_at_ms=_wall_time_ms(),
                    force=True,
                )

            if action == "commit":
                self._update_voice_telemetry(
                    status="running",
                    local_state="committing",
                    last_level_db=self._micro_db,
                    speech_threshold_db=self._lelamp_turn_detector.speech_threshold_db,
                    last_commit_at_ms=_wall_time_ms(),
                    last_result="local user turn committed",
                    force=True,
                )
                self._loop.call_soon_threadsafe(self._commit_user_turn_safe)
            elif action == "clear":
                self._update_voice_telemetry(
                    status="ready",
                    local_state="idle",
                    last_level_db=self._micro_db,
                    speech_threshold_db=self._lelamp_turn_detector.speech_threshold_db,
                    last_clear_at_ms=_wall_time_ms(),
                    last_result="short noise cleared",
                    force=True,
                )
                self._loop.call_soon_threadsafe(self._clear_user_turn_safe)
            elif is_speaking:
                self._update_voice_telemetry(
                    status="running",
                    local_state="listening",
                    last_level_db=self._micro_db,
                    speech_threshold_db=self._lelamp_turn_detector.speech_threshold_db,
                )
            else:
                self._update_voice_telemetry(
                    status="ready",
                    local_state="idle",
                    last_level_db=self._micro_db,
                    speech_threshold_db=self._lelamp_turn_detector.speech_threshold_db,
                )

        def _sd_output_callback(self, outdata, frames, time_info, *args) -> None:  # type: ignore[override]
            self._output_delay = time_info.outputBufferDacTime - time_info.currentTime

            with self._audio_sink.lock:
                model_frames_needed = max(
                    int(round(frames * CONSOLE_MODEL_SAMPLE_RATE / CONSOLE_DEVICE_SAMPLE_RATE)),
                    1,
                )
                bytes_needed = model_frames_needed * 2
                wrote_audio = False
                if len(self._audio_sink.audio_buffer) < bytes_needed:
                    available_bytes = len(self._audio_sink.audio_buffer)
                    wrote_audio = available_bytes > 0
                    model_samples = livekit_chat_cli.np.frombuffer(
                        self._audio_sink.audio_buffer,
                        dtype=livekit_chat_cli.np.int16,
                        count=available_bytes // 2,
                    ).copy()
                    del self._audio_sink.audio_buffer[:available_bytes]
                else:
                    chunk = self._audio_sink.audio_buffer[:bytes_needed]
                    wrote_audio = True
                    model_samples = livekit_chat_cli.np.frombuffer(
                        chunk,
                        dtype=livekit_chat_cli.np.int16,
                        count=model_frames_needed,
                    ).copy()
                    del self._audio_sink.audio_buffer[:bytes_needed]

            if wrote_audio:
                device_samples = _resample_int16(
                    model_samples,
                    src_rate=CONSOLE_MODEL_SAMPLE_RATE,
                    dst_rate=CONSOLE_DEVICE_SAMPLE_RATE,
                )
                outdata[:, 0] = 0
                outdata[: min(len(device_samples), frames), 0] = device_samples[:frames]
            else:
                outdata[:, 0] = 0

            if wrote_audio:
                self._lelamp_output_gate.mark_output(time.monotonic())

            if not self._lelamp_enable_apm:
                return

            num_chunks = frames // CONSOLE_DEVICE_FRAME_SAMPLES
            for i in range(num_chunks):
                start = i * CONSOLE_DEVICE_FRAME_SAMPLES
                end = start + CONSOLE_DEVICE_FRAME_SAMPLES
                render_chunk = outdata[start:end, 0]
                render_chunk_24k = _resample_int16(
                    render_chunk,
                    src_rate=CONSOLE_DEVICE_SAMPLE_RATE,
                    dst_rate=CONSOLE_MODEL_SAMPLE_RATE,
                )
                render_frame_for_aec = livekit_chat_cli.rtc.AudioFrame(
                    data=render_chunk_24k.tobytes(),
                    samples_per_channel=len(render_chunk_24k),
                    sample_rate=CONSOLE_MODEL_SAMPLE_RATE,
                    num_channels=1,
                )
                self._apm.process_reverse_stream(render_frame_for_aec)

        def _commit_user_turn_safe(self) -> None:
            try:
                self._session.commit_user_turn()
                logger.info("Committed local console user turn")
            except RuntimeError:
                logger.exception("Failed to commit local console user turn")

        def _clear_user_turn_safe(self) -> None:
            try:
                self._session.clear_user_turn()
                logger.debug("Cleared local console user turn after short noise")
            except RuntimeError:
                logger.exception("Failed to clear local console user turn")

        def _update_voice_telemetry(self, *, force: bool = False, **values: Any) -> None:
            self._lelamp_voice_telemetry.update(force=force, **values)

    livekit_chat_cli.ChatCLI = LeLampChatCLI


def install_console_audio_patch(
    *,
    enable_apm: bool = False,
    speech_threshold_db: float = LocalTurnDetectorConfig.speech_threshold_db,
    silence_duration_s: float = LocalTurnDetectorConfig.silence_duration_s,
    min_speech_duration_s: float = LocalTurnDetectorConfig.min_speech_duration_s,
    commit_cooldown_s: float = LocalTurnDetectorConfig.commit_cooldown_s,
    speech_start_duration_s: float = LocalTurnDetectorConfig.speech_start_duration_s,
    output_suppression_s: float = 0.35,
    auto_calibrate: bool = True,
    calibration_duration_s: float = 1.6,
    calibration_margin_db: float = 8.0,
    voice_state_path: str = "/tmp/lelamp-voice-state.json",
) -> None:
    global _CONSOLE_ENABLE_APM
    global _CONSOLE_OUTPUT_SUPPRESSION_SECONDS
    global _CONSOLE_AUTO_CALIBRATE
    global _CONSOLE_CALIBRATION_DURATION_SECONDS
    global _CONSOLE_CALIBRATION_MARGIN_DB
    global _CONSOLE_VOICE_STATE_PATH
    global _CONSOLE_TURN_DETECTOR_CONFIG

    _CONSOLE_ENABLE_APM = enable_apm
    _CONSOLE_OUTPUT_SUPPRESSION_SECONDS = output_suppression_s
    _CONSOLE_AUTO_CALIBRATE = auto_calibrate
    _CONSOLE_CALIBRATION_DURATION_SECONDS = calibration_duration_s
    _CONSOLE_CALIBRATION_MARGIN_DB = calibration_margin_db
    _CONSOLE_VOICE_STATE_PATH = voice_state_path
    _CONSOLE_TURN_DETECTOR_CONFIG = LocalTurnDetectorConfig(
        speech_threshold_db=speech_threshold_db,
        silence_duration_s=silence_duration_s,
        min_speech_duration_s=min_speech_duration_s,
        commit_cooldown_s=commit_cooldown_s,
        speech_start_duration_s=speech_start_duration_s,
    )
    configure_voice_telemetry(voice_state_path)

    if getattr(livekit_chat_cli.ChatCLI, "_lelamp_local_voice_patch", False):
        return

    _install_console_audio_patch_impl()


def _device_name(devices: Sequence[Mapping[str, Any]], index: int | None) -> str | None:
    if index is None or index < 0 or index >= len(devices):
        return None
    return str(devices[index].get("name", "")) or None


def _frame_rms_db(frame: Any) -> float:
    samples = livekit_chat_cli.np.frombuffer(frame.data, dtype=livekit_chat_cli.np.int16)
    rms = livekit_chat_cli.np.sqrt(livekit_chat_cli.np.mean(samples.astype(livekit_chat_cli.np.float32) ** 2))
    max_int16 = livekit_chat_cli.np.iinfo(livekit_chat_cli.np.int16).max
    return float(20.0 * livekit_chat_cli.np.log10(rms / max_int16 + 1e-6))


def _resample_int16(
    samples: Any,
    *,
    src_rate: int,
    dst_rate: int,
) -> Any:
    np = livekit_chat_cli.np
    array = np.asarray(samples, dtype=np.int16).reshape(-1)
    if src_rate == dst_rate or len(array) == 0:
        return array

    target_length = max(int(round(len(array) * dst_rate / src_rate)), 1)
    source_positions = np.linspace(0, len(array) - 1, num=len(array), endpoint=True)
    target_positions = np.linspace(0, len(array) - 1, num=target_length, endpoint=True)
    resampled = np.interp(target_positions, source_positions, array.astype(np.float32))
    clipped = np.clip(np.rint(resampled), -32768, 32767)
    return clipped.astype(np.int16)


def _wall_time_ms() -> int:
    return int(time.time() * 1000)
