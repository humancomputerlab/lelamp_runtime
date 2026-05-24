from __future__ import annotations

from dataclasses import dataclass
import os

from livekit.plugins import openai


_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}
_GLM_PROVIDER_ALIASES = {"glm", "zhipu", "bigmodel", "z.ai"}
_QWEN_PROVIDER_ALIASES = {"qwen", "dashscope", "tongyi"}
_OPENAI_BASE_URL = "https://api.openai.com/v1"
_GLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4/realtime"
_QWEN_BASE_URL = "https://dashscope.aliyuncs.com/api-ws/v1/realtime"
_DEFAULT_MODEL_PROVIDER = "qwen"


def _get_str(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value


def _get_optional_str(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or value == "":
        return None
    return value


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {value!r}") from exc


def _get_positive_int(name: str, default: int) -> int:
    value = _get_int(name, default)
    if value <= 0:
        raise ValueError(f"{name} must be greater than 0")
    return value


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a float, got {value!r}") from exc


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default

    normalized = value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False

    raise ValueError(f"{name} must be a boolean, got {value!r}")


def _normalize_model_provider(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized in _QWEN_PROVIDER_ALIASES:
        return "qwen"
    if normalized in _GLM_PROVIDER_ALIASES:
        return "glm"
    if normalized == "":
        return _DEFAULT_MODEL_PROVIDER
    return normalized


def _get_model_provider() -> str:
    provider = _get_optional_str("MODEL_PROVIDER")
    if provider is not None:
        return _normalize_model_provider(provider)

    if os.getenv("DASHSCOPE_API_KEY") or os.getenv("QWEN_API_KEY"):
        return "qwen"
    if os.getenv("ZAI_API_KEY"):
        return "glm"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    return _DEFAULT_MODEL_PROVIDER


def _model_api_key_candidates(provider: str) -> tuple[str, ...]:
    if provider == "qwen":
        return ("MODEL_API_KEY", "DASHSCOPE_API_KEY", "QWEN_API_KEY")
    if provider == "glm":
        return ("MODEL_API_KEY", "ZAI_API_KEY")
    if provider == "openai":
        return ("MODEL_API_KEY", "OPENAI_API_KEY")
    return (
        "MODEL_API_KEY",
        "DASHSCOPE_API_KEY",
        "QWEN_API_KEY",
        "ZAI_API_KEY",
        "OPENAI_API_KEY",
    )


def _get_model_api_key(provider: str) -> str | None:
    for env_name in _model_api_key_candidates(provider):
        value = _get_optional_str(env_name)
        if value:
            return value
    return None


def _default_model_base_url(provider: str) -> str | None:
    if provider == "qwen":
        return _QWEN_BASE_URL
    if provider == "glm":
        return _GLM_BASE_URL
    if provider == "openai":
        return _OPENAI_BASE_URL
    return None


def _default_model_name(provider: str) -> str | None:
    if provider == "qwen":
        return "qwen3.5-omni-flash-realtime"
    if provider == "glm":
        return "glm-realtime"
    return None


def _default_model_voice(provider: str) -> str:
    if provider == "qwen":
        return "Tina"
    if provider == "glm":
        return "tongtong"
    if provider == "openai":
        return "ballad"
    return "Tina"


@dataclass(frozen=True)
class RuntimeSettings:
    port: str
    lamp_id: str
    fps: int
    dashboard_host: str
    dashboard_port: int
    dashboard_poll_ms: int
    dashboard_expose_transcripts: bool
    model_provider: str
    model_api_key: str | None
    model_base_url: str | None
    model_name: str | None
    model_voice: str
    model_temperature: float
    qwen_use_server_vad: bool
    glm_use_server_vad: bool
    agent_language: str
    agent_opening_line: str
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
    audio_card_index: int
    console_enable_apm: bool
    console_speech_threshold_db: float
    console_silence_duration_s: float
    console_min_speech_duration_s: float
    console_commit_cooldown_s: float
    console_output_suppression_s: float
    console_auto_calibrate: bool
    console_calibration_duration_s: float
    console_calibration_margin_db: float
    console_start_trigger_s: float
    voice_state_path: str


def load_runtime_settings() -> RuntimeSettings:
    model_provider = _get_model_provider()

    idle_recording = _get_str("LELAMP_IDLE_RECORDING", "home_safe")

    return RuntimeSettings(
        port=_get_str("LELAMP_PORT", "/dev/ttyACM0"),
        lamp_id=_get_str("LELAMP_ID", "lelamp"),
        fps=_get_int("LELAMP_FPS", 30),
        dashboard_host=_get_str("LELAMP_DASHBOARD_HOST", "127.0.0.1"),
        dashboard_port=_get_positive_int("LELAMP_DASHBOARD_PORT", 8765),
        dashboard_poll_ms=_get_positive_int("LELAMP_DASHBOARD_POLL_MS", 400),
        dashboard_expose_transcripts=_get_bool("LELAMP_DASHBOARD_EXPOSE_TRANSCRIPTS", False),
        model_provider=model_provider,
        model_api_key=_get_model_api_key(model_provider),
        model_base_url=_get_optional_str("MODEL_BASE_URL") or _default_model_base_url(model_provider),
        model_name=_get_optional_str("MODEL_NAME") or _default_model_name(model_provider),
        model_voice=_get_str("MODEL_VOICE", _default_model_voice(model_provider)),
        model_temperature=_get_float("MODEL_TEMPERATURE", 0.8),
        qwen_use_server_vad=_get_bool("LELAMP_QWEN_USE_SERVER_VAD", False),
        glm_use_server_vad=_get_bool("LELAMP_GLM_USE_SERVER_VAD", False),
        agent_language=_get_str("LELAMP_AGENT_LANGUAGE", "zh-CN"),
        agent_opening_line=_get_str("LELAMP_AGENT_OPENING_LINE", "灯灯醒了。"),
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
        use_home_pose_relative=_get_bool("LELAMP_USE_HOME_POSE_RELATIVE", True),
        interpolation_duration=_get_float("LELAMP_INTERPOLATION_DURATION", 3.0),
        audio_user=_get_str(
            "LELAMP_AUDIO_USER",
            os.getenv("SUDO_USER") or os.getenv("USER") or "pi",
        ),
        audio_card_index=_get_int("LELAMP_AUDIO_CARD_INDEX", 2),
        console_enable_apm=_get_bool("LELAMP_CONSOLE_ENABLE_APM", False),
        console_speech_threshold_db=_get_float("LELAMP_CONSOLE_SPEECH_THRESHOLD_DB", -54.0),
        console_silence_duration_s=_get_float("LELAMP_CONSOLE_SILENCE_DURATION_S", 0.4),
        console_min_speech_duration_s=_get_float("LELAMP_CONSOLE_MIN_SPEECH_DURATION_S", 0.2),
        console_commit_cooldown_s=_get_float("LELAMP_CONSOLE_COMMIT_COOLDOWN_S", 0.75),
        console_output_suppression_s=_get_float("LELAMP_CONSOLE_OUTPUT_SUPPRESSION_S", 0.35),
        console_auto_calibrate=_get_bool("LELAMP_CONSOLE_AUTO_CALIBRATE", True),
        console_calibration_duration_s=_get_float("LELAMP_CONSOLE_CALIBRATION_DURATION_S", 1.6),
        console_calibration_margin_db=_get_float("LELAMP_CONSOLE_CALIBRATION_MARGIN_DB", 8.0),
        console_start_trigger_s=_get_float("LELAMP_CONSOLE_START_TRIGGER_S", 0.08),
        voice_state_path=_get_str("LELAMP_VOICE_STATE_PATH", "/tmp/lelamp-voice-state.json"),
    )


def build_realtime_model_config(settings: RuntimeSettings) -> dict[str, object]:
    kwargs: dict[str, object] = {"voice": settings.model_voice}

    if settings.model_name:
        kwargs["model"] = settings.model_name
    if settings.model_api_key:
        kwargs["api_key"] = settings.model_api_key
    if settings.model_base_url:
        kwargs["base_url"] = settings.model_base_url

    return kwargs


def build_realtime_model(settings: RuntimeSettings):
    if settings.model_provider == "qwen":
        from lelamp.qwen_realtime import QwenRealtimeModel

        return QwenRealtimeModel(settings=settings)

    if settings.model_provider == "glm":
        from lelamp.glm_realtime import GLMRealtimeModel

        return GLMRealtimeModel(settings=settings)

    return openai.realtime.RealtimeModel(**build_realtime_model_config(settings))
