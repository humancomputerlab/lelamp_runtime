from dotenv import load_dotenv

load_dotenv()

import atexit
import logging
import subprocess

from livekit import agents
from livekit.agents import (
    AgentSession, 
    Agent, 
    RoomInputOptions,
    function_tool
)
from livekit.plugins import (
    noise_cancellation,
)
from lelamp.runtime_config import (
    RuntimeSettings,
    build_realtime_model,
    load_runtime_settings,
)
from lelamp.local_voice import install_console_audio_patch
from lelamp.audio_controls import build_amixer_volume_commands
from lelamp.voice_profile import (
    build_agent_instructions,
    build_startup_reply_instructions,
)
from lelamp.auto_expression import AutoExpressionController
from lelamp.expression_engine import ExpressionStyle, dispatch_expression
from lelamp.memory.runtime import bootstrap_agent_runtime
from lelamp.motor_bus.server import MotorBusServer
from lelamp.service.motors.animation_service import AnimationService
from lelamp.service.rgb.rgb_service import RGBService


STARTUP_WARM_RGB = (255, 170, 70)
logger = logging.getLogger(__name__)

# Agent Class
class LeLamp(Agent):
    def __init__(self, settings: RuntimeSettings | None = None) -> None:
        self.settings = settings or load_runtime_settings()
        super().__init__(instructions=build_agent_instructions(self.settings))
        self.animation_service_error: str | None = None
        self.auto_expression_controller: AutoExpressionController | None = None
        
        # Initialize and start services
        self.animation_service = AnimationService(
            port=self.settings.port,
            lamp_id=self.settings.lamp_id,
            fps=self.settings.fps,
            duration=self.settings.interpolation_duration,
            idle_recording=self.settings.idle_recording,
            home_recording=self.settings.home_recording,
            use_home_pose_relative=self.settings.use_home_pose_relative,
        )
        self.rgb_service: RGBService | None = None
        if self.settings.enable_rgb:
            self.rgb_service = RGBService(
                led_count=self.settings.led_count,
                led_pin=self.settings.led_pin,
                led_freq_hz=self.settings.led_freq_hz,
                led_dma=self.settings.led_dma,
                led_brightness=self.settings.led_brightness,
                led_invert=self.settings.led_invert,
                led_channel=self.settings.led_channel,
        )
        
        # Start services
        try:
            self.animation_service.start()
        except Exception as exc:
            self.animation_service_error = str(exc)
            logger.exception("Animation service failed to start")
        if self.rgb_service is not None:
            self.rgb_service.start()

        # Trigger wake up animation via animation service
        if self.animation_service_error is None:
            self.animation_service.dispatch("startup", self.settings.startup_recording)
        if self.rgb_service is not None:
            self.rgb_service.dispatch("solid", STARTUP_WARM_RGB)
        self._set_system_volume(self.settings.startup_volume)

    def _set_system_volume(self, volume_percent: int):
        """Internal helper to set system volume"""
        try:
            for command in build_amixer_volume_commands(
                audio_user=self.settings.audio_user,
                card_index=self.settings.audio_card_index,
                volume_percent=volume_percent,
            ):
                subprocess.run(command, capture_output=True, text=True, timeout=5)
        except Exception:
            pass  # Silently fail during initialization

    def _note_expression_tool_dispatch(self) -> None:
        if self.auto_expression_controller is not None:
            self.auto_expression_controller.note_tool_dispatch()

    @function_tool
    async def express(self, style: ExpressionStyle) -> str:
        """
        High-level emotion tool that pairs one existing motion with one matching light cue.
        Prefer this over low-level motion/light tools for normal emotional reactions.
        Use it directly for greetings, affection, teasing, worry, surprise, and celebration.
        Do not ask for confirmation first when the intent is clear.
        Do not narrate the tool call after using it.
        """
        print(f"LeLamp: express function called with style: {style}")
        try:
            result = dispatch_expression(
                style=style,
                animation_service=self.animation_service,
                animation_service_error=self.animation_service_error,
                rgb_service=self.rgb_service,
                led_count=self.settings.led_count,
            )
            if result == "expression_ok":
                self._note_expression_tool_dispatch()
            return result
        except Exception as e:
            return f"Error expressing style {style}: {str(e)}"

    @function_tool
    async def get_available_recordings(self) -> str:
        """
        List the currently available motion names.
        Use this only when you truly need to inspect valid motion names.
        Do not call this as a stalling step before obvious emotional motion.
        """
        print("LeLamp: get_available_recordings function called")
        try:
            recordings = self.animation_service.get_available_recordings()

            if recordings:
                result = f"Available recordings: {', '.join(recordings)}"
                return result
            else:
                result = "No recordings found."
                return result
        except Exception as e:
            result = f"Error getting recordings: {str(e)}"
            return result

    @function_tool
    async def play_recording(self, recording_name: str) -> str:
        """
        Trigger one existing body motion immediately.
        Use this proactively for emotional expression, reactions, greetings, demos, and emphasis.
        Do not ask for confirmation first when the intent is clear.
        Do not narrate the tool call after using it.
        Use only one valid existing recording name.
        """
        print(f"LeLamp: play_recording function called with recording_name: {recording_name}")
        try:
            if self.animation_service_error is not None:
                return f"Motion is unavailable: {self.animation_service_error}"

            # Send play event to animation service
            self.animation_service.dispatch("play", recording_name)
            self._note_expression_tool_dispatch()
            result = "motion_ok"
            return result
        except Exception as e:
            result = f"Error playing recording {recording_name}: {str(e)}"
            return result

    @function_tool
    async def set_rgb_solid(self, red: int, green: int, blue: int) -> str:
        """
        Set one solid light color immediately.
        Use this proactively for visible emotion, mood, emphasis, greetings, and reactions.
        Do not ask for confirmation first when the intent is clear.
        Do not narrate the tool call after using it.
        Args:
            red: 0-255
            green: 0-255
            blue: 0-255
        """
        print(f"LeLamp: set_rgb_solid function called with RGB({red}, {green}, {blue})")
        try:
            if self.rgb_service is None:
                return "RGB is disabled via LELAMP_ENABLE_RGB"

            # Validate RGB values
            if not all(0 <= val <= 255 for val in [red, green, blue]):
                return "Error: RGB values must be between 0 and 255"
            
            # Send solid color event to RGB service
            self.rgb_service.dispatch("solid", (red, green, blue))
            self._note_expression_tool_dispatch()
            result = "light_ok"
            return result
        except Exception as e:
            result = f"Error setting RGB color: {str(e)}"
            return result

    @function_tool
    async def paint_rgb_pattern(self, colors: list[list[int]]) -> str:
        """
        Set a multi-pixel light pattern immediately.
        Use this for richer visual emphasis when a solid color is not expressive enough.
        Do not ask for confirmation first when the intent is clear.
        Do not narrate the tool call after using it.
        Provide one RGB tuple per configured LED.
        """
        print(f"LeLamp: paint_rgb_pattern function called with {len(colors)} colors")
        try:
            if self.rgb_service is None:
                return "RGB is disabled via LELAMP_ENABLE_RGB"

            # Validate colors format
            if not isinstance(colors, list):
                return "Error: colors must be a list of RGB tuples"
            
            validated_colors = []
            for i, color in enumerate(colors):
                if not isinstance(color, (list, tuple)) or len(color) != 3:
                    return f"Error: color at index {i} must be a 3-element RGB tuple"
                if not all(isinstance(val, int) and 0 <= val <= 255 for val in color):
                    return f"Error: RGB values at index {i} must be integers between 0 and 255"
                validated_colors.append(tuple(color))
            
            # Send paint event to RGB service
            self.rgb_service.dispatch("paint", validated_colors)
            self._note_expression_tool_dispatch()
            result = "light_pattern_ok"
            return result
        except Exception as e:
            result = f"Error painting RGB pattern: {str(e)}"
            return result

    @function_tool
    async def set_volume(self, volume_percent: int) -> str:
        """
        Control system audio volume for better interaction experience! Use this when users ask 
        you to be louder, quieter, or set a specific volume level. Perfect for adjusting to 
        room conditions, user preferences, or creating dramatic audio effects during conversations.
        Use when someone says "turn it up", "lower the volume", "I can't hear you", or gives 
        specific volume requests. Great for being considerate of your environment!
        
        Args:
            volume_percent: Volume level as percentage (0-100). 0=mute, 50=half volume, 100=max
        """
        print(f"LeLamp: set_volume function called with volume: {volume_percent}%")
        try:
            # Validate volume range
            if not 0 <= volume_percent <= 100:
                return "Error: Volume must be between 0 and 100 percent"
            
            # Use the internal helper function
            self._set_system_volume(volume_percent)
            result = f"Set Line and Line DAC volume to {volume_percent}%"
            return result
                
        except subprocess.TimeoutExpired:
            result = "Error: Volume control command timed out"
            print(result)
            return result
        except FileNotFoundError:
            result = "Error: amixer command not found on system"
            print(result)
            return result
        except Exception as e:
            result = f"Error controlling volume: {str(e)}"
            print(result)
            return result

# Entry to the agent
async def entrypoint(ctx: agents.JobContext):
    settings = load_runtime_settings()
    memory_runtime = bootstrap_agent_runtime(settings)
    agent = LeLamp(settings=settings)
    if hasattr(agent, "animation_service") and hasattr(agent, "settings"):
        fallback_callback = getattr(
            memory_runtime,
            "note_auto_expression_fallback",
            None,
        )
        agent.auto_expression_controller = AutoExpressionController(
            animation_service=agent.animation_service,
            get_animation_service_error=lambda: agent.animation_service_error,
            rgb_service=agent.rgb_service,
            led_count=agent.settings.led_count,
            on_fallback_expression=fallback_callback,
        )
        agent.auto_expression_controller.start()

    motor_bus_server: MotorBusServer | None = None
    try:
        motor_bus_server = MotorBusServer(
            animation_service=agent.animation_service,
            get_animation_service_error=lambda: agent.animation_service_error,
            rgb_service=agent.rgb_service,
            led_count=agent.settings.led_count,
        )
        motor_bus_server.start()
        if motor_bus_server.is_ready():
            logger.info(
                "motor bus server ready on %s:%s; dashboard/CLI will route via proxy",
                motor_bus_server.host,
                motor_bus_server.port,
            )
        else:
            logger.warning("motor bus server failed to start; dashboard/CLI will contend for hardware")
    except Exception:
        logger.exception("motor bus server bootstrap failed")
        motor_bus_server = None

    memory_runtime.set_motor_bus_enabled(
        motor_bus_server.is_ready() if motor_bus_server is not None else False
    )

    async def _shutdown_callback(reason: str = "") -> None:
        del reason
        if agent.auto_expression_controller is not None:
            try:
                agent.auto_expression_controller.stop()
            except Exception:
                logger.exception("auto expression stop failed during shutdown")
        if motor_bus_server is not None:
            try:
                motor_bus_server.stop()
            except Exception:
                logger.exception("motor bus stop failed during shutdown")
        memory_runtime.close()

    ctx.add_shutdown_callback(_shutdown_callback)
    atexit.register(memory_runtime.close)
    if motor_bus_server is not None:
        atexit.register(motor_bus_server.stop)

    session_kwargs = {"llm": build_realtime_model(agent.settings)}
    should_install_console_audio_patch = (
        agent.settings.model_provider == "qwen"
        or (
            agent.settings.model_provider == "glm"
            and not agent.settings.glm_use_server_vad
        )
    )
    if should_install_console_audio_patch:
        install_console_audio_patch(
            enable_apm=agent.settings.console_enable_apm,
            speech_threshold_db=agent.settings.console_speech_threshold_db,
            silence_duration_s=agent.settings.console_silence_duration_s,
            min_speech_duration_s=agent.settings.console_min_speech_duration_s,
            commit_cooldown_s=agent.settings.console_commit_cooldown_s,
            speech_start_duration_s=agent.settings.console_start_trigger_s,
            output_suppression_s=agent.settings.console_output_suppression_s,
            auto_calibrate=agent.settings.console_auto_calibrate,
            calibration_duration_s=agent.settings.console_calibration_duration_s,
            calibration_margin_db=agent.settings.console_calibration_margin_db,
            voice_state_path=agent.settings.voice_state_path,
        )
        session_kwargs["turn_detection"] = "manual"

    session = AgentSession(**session_kwargs)
    install_session_listeners = getattr(memory_runtime, "install_session_listeners", None)
    if callable(install_session_listeners):
        install_session_listeners(
            session,
            model_provider=getattr(agent.settings, "model_provider", None),
            model_name=getattr(agent.settings, "model_name", None),
        )

    try:
        await session.start(
            room=ctx.room,
            agent=agent,
            room_input_options=RoomInputOptions(
                noise_cancellation=noise_cancellation.BVC(),
            ),
        )

        await session.generate_reply(
            instructions=build_startup_reply_instructions(agent.settings)
        )
    except Exception as exc:
        try:
            ctx.shutdown(reason=f"entrypoint_failed:{type(exc).__name__}")
        except Exception:
            logger.exception("job shutdown failed after entrypoint exception")
        raise

if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint, num_idle_processes=1))
