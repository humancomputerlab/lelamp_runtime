import asyncio
import contextlib
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch

from livekit.plugins import openai

from lelamp.runtime_config import build_realtime_model, load_runtime_settings


class QwenRealtimeTests(unittest.TestCase):
    def test_smooth_animation_entrypoint_bootstraps_memory_before_lamp_init(self) -> None:
        import smooth_animation

        class FakeSession:
            async def start(self, **kwargs) -> None:
                return None

            async def generate_reply(self, instructions=None) -> None:
                return None

        async def _exercise() -> None:
            order: list[str] = []
            settings = SimpleNamespace(
                model_provider="qwen",
                glm_use_server_vad=False,
                console_enable_apm=False,
                console_speech_threshold_db=-48.0,
                console_silence_duration_s=0.4,
                console_min_speech_duration_s=0.25,
                console_commit_cooldown_s=1.0,
                console_start_trigger_s=0.18,
                console_output_suppression_s=0.6,
                console_auto_calibrate=False,
                console_calibration_duration_s=1.6,
                console_calibration_margin_db=8.0,
                voice_state_path="/tmp/test-voice-state.json",
                led_count=40,
            )
            fake_agent = SimpleNamespace(
                settings=settings,
                auto_expression_controller=None,
                animation_service="fake-animation-service",
                animation_service_error=None,
                rgb_service="fake-rgb-service",
            )
            fake_ctx = SimpleNamespace(
                room="test-room",
                add_shutdown_callback=lambda callback: None,
                shutdown=Mock(),
            )
            fake_memory_runtime = SimpleNamespace(
                enabled=True,
                set_motor_bus_enabled=lambda enabled: None,
                close=lambda: None,
            )

            def fake_bootstrap(_settings):
                order.append("memory")
                return fake_memory_runtime

            def fake_lamp(*args, **kwargs):
                order.append("lamp")
                return fake_agent

            with patch.object(
                smooth_animation,
                "load_runtime_settings",
                return_value=settings,
            ), patch.object(
                smooth_animation,
                "bootstrap_agent_runtime",
                side_effect=fake_bootstrap,
            ), patch.object(
                smooth_animation,
                "LeLamp",
                side_effect=fake_lamp,
            ), patch.object(
                smooth_animation,
                "build_realtime_model",
                return_value="fake-llm",
            ), patch.object(
                smooth_animation,
                "AgentSession",
                return_value=FakeSession(),
            ), patch.object(
                smooth_animation,
                "build_startup_reply_instructions",
                return_value="灯灯醒了。",
            ), patch.object(
                smooth_animation.noise_cancellation,
                "BVC",
                return_value="fake-noise-cancellation",
            ), patch.object(
                smooth_animation,
                "RoomInputOptions",
                side_effect=lambda **kwargs: kwargs,
            ):
                await smooth_animation.entrypoint(fake_ctx)

            self.assertEqual(order[:2], ["memory", "lamp"])

        asyncio.run(_exercise())

    def test_smooth_animation_entrypoint_registers_shutdown_callback_for_memory_close(self) -> None:
        import smooth_animation

        class FakeSession:
            async def start(self, **kwargs) -> None:
                return None

            async def generate_reply(self, instructions=None) -> None:
                return None

        async def _exercise() -> None:
            callbacks = []
            events: list[str] = []
            settings = SimpleNamespace(
                model_provider="qwen",
                glm_use_server_vad=False,
                console_enable_apm=False,
                console_speech_threshold_db=-48.0,
                console_silence_duration_s=0.4,
                console_min_speech_duration_s=0.25,
                console_commit_cooldown_s=1.0,
                console_start_trigger_s=0.18,
                console_output_suppression_s=0.6,
                console_auto_calibrate=False,
                console_calibration_duration_s=1.6,
                console_calibration_margin_db=8.0,
                voice_state_path="/tmp/test-voice-state.json",
                led_count=40,
            )
            fake_agent = SimpleNamespace(
                settings=settings,
                auto_expression_controller=SimpleNamespace(
                    stop=lambda: events.append("auto.stop")
                ),
            )
            fake_ctx = SimpleNamespace(
                room="test-room",
                add_shutdown_callback=lambda callback: callbacks.append(callback),
                shutdown=Mock(),
            )
            fake_memory_runtime = SimpleNamespace(
                enabled=True,
                set_motor_bus_enabled=lambda enabled: None,
                close=lambda: events.append("memory.close"),
            )

            with patch.object(
                smooth_animation,
                "load_runtime_settings",
                return_value=settings,
            ), patch.object(
                smooth_animation,
                "bootstrap_agent_runtime",
                return_value=fake_memory_runtime,
            ), patch.object(
                smooth_animation,
                "LeLamp",
                return_value=fake_agent,
            ), patch.object(
                smooth_animation,
                "build_realtime_model",
                return_value="fake-llm",
            ), patch.object(
                smooth_animation,
                "AgentSession",
                return_value=FakeSession(),
            ), patch.object(
                smooth_animation,
                "build_startup_reply_instructions",
                return_value="灯灯醒了。",
            ), patch.object(
                smooth_animation.noise_cancellation,
                "BVC",
                return_value="fake-noise-cancellation",
            ), patch.object(
                smooth_animation,
                "RoomInputOptions",
                side_effect=lambda **kwargs: kwargs,
            ):
                await smooth_animation.entrypoint(fake_ctx)

            self.assertEqual(len(callbacks), 1)
            await callbacks[0]("room_disconnect")
            self.assertEqual(events, ["auto.stop", "memory.close"])

        asyncio.run(_exercise())

    def test_smooth_animation_entrypoint_installs_memory_session_listeners(self) -> None:
        import smooth_animation

        class FakeSession:
            async def start(self, **kwargs) -> None:
                return None

            async def generate_reply(self, instructions=None) -> None:
                return None

        async def _exercise() -> None:
            settings = SimpleNamespace(
                model_provider="qwen",
                model_name="qwen3.5-omni-plus-realtime",
                glm_use_server_vad=False,
                console_enable_apm=False,
                console_speech_threshold_db=-48.0,
                console_silence_duration_s=0.4,
                console_min_speech_duration_s=0.25,
                console_commit_cooldown_s=1.0,
                console_start_trigger_s=0.18,
                console_output_suppression_s=0.6,
                console_auto_calibrate=False,
                console_calibration_duration_s=1.6,
                console_calibration_margin_db=8.0,
                voice_state_path="/tmp/test-voice-state.json",
                led_count=40,
            )
            fake_agent = SimpleNamespace(
                settings=settings,
                auto_expression_controller=None,
                animation_service="fake-animation-service",
                animation_service_error=None,
                rgb_service="fake-rgb-service",
            )
            fake_ctx = SimpleNamespace(
                room="test-room",
                add_shutdown_callback=lambda callback: None,
                shutdown=Mock(),
            )
            installed = []
            fake_memory_runtime = SimpleNamespace(
                enabled=True,
                install_session_listeners=lambda session, **kwargs: installed.append((session, kwargs)),
                set_motor_bus_enabled=lambda enabled: None,
                close=lambda: None,
            )

            with patch.object(
                smooth_animation,
                "load_runtime_settings",
                return_value=settings,
            ), patch.object(
                smooth_animation,
                "bootstrap_agent_runtime",
                return_value=fake_memory_runtime,
            ), patch.object(
                smooth_animation,
                "LeLamp",
                return_value=fake_agent,
            ), patch.object(
                smooth_animation,
                "build_realtime_model",
                return_value="fake-llm",
            ), patch.object(
                smooth_animation,
                "AgentSession",
                return_value=FakeSession(),
            ), patch.object(
                smooth_animation,
                "build_startup_reply_instructions",
                return_value="灯灯醒了。",
            ), patch.object(
                smooth_animation.noise_cancellation,
                "BVC",
                return_value="fake-noise-cancellation",
            ), patch.object(
                smooth_animation,
                "RoomInputOptions",
                side_effect=lambda **kwargs: kwargs,
            ):
                await smooth_animation.entrypoint(fake_ctx)

            assert len(installed) == 1
            assert installed[0][1] == {
                "model_provider": "qwen",
                "model_name": "qwen3.5-omni-plus-realtime",
            }

        asyncio.run(_exercise())

    def test_smooth_animation_entrypoint_wires_auto_expression_memory_callback(self) -> None:
        import smooth_animation

        class FakeSession:
            async def start(self, **kwargs) -> None:
                return None

            async def generate_reply(self, instructions=None) -> None:
                return None

        async def _exercise() -> None:
            constructed = []
            settings = SimpleNamespace(
                model_provider="qwen",
                model_name="qwen3.5-omni-plus-realtime",
                glm_use_server_vad=False,
                console_enable_apm=False,
                console_speech_threshold_db=-48.0,
                console_silence_duration_s=0.4,
                console_min_speech_duration_s=0.25,
                console_commit_cooldown_s=1.0,
                console_start_trigger_s=0.18,
                console_output_suppression_s=0.6,
                console_auto_calibrate=False,
                console_calibration_duration_s=1.6,
                console_calibration_margin_db=8.0,
                voice_state_path="/tmp/test-voice-state.json",
                led_count=40,
            )
            fake_agent = SimpleNamespace(
                settings=settings,
                auto_expression_controller=None,
                animation_service="fake-animation-service",
                animation_service_error=None,
                rgb_service="fake-rgb-service",
            )
            fake_ctx = SimpleNamespace(
                room="test-room",
                add_shutdown_callback=lambda callback: None,
                shutdown=Mock(),
            )
            fake_memory_runtime = SimpleNamespace(
                enabled=True,
                install_session_listeners=lambda session, **kwargs: None,
                note_auto_expression_fallback=lambda **kwargs: None,
                set_motor_bus_enabled=lambda enabled: None,
                close=lambda: None,
            )

            class FakeAutoExpressionController:
                def __init__(self, **kwargs) -> None:
                    constructed.append(kwargs)

                def start(self) -> None:
                    return None

                def stop(self) -> None:
                    return None

            with patch.object(
                smooth_animation,
                "load_runtime_settings",
                return_value=settings,
            ), patch.object(
                smooth_animation,
                "bootstrap_agent_runtime",
                return_value=fake_memory_runtime,
            ), patch.object(
                smooth_animation,
                "LeLamp",
                return_value=fake_agent,
            ), patch.object(
                smooth_animation,
                "AutoExpressionController",
                FakeAutoExpressionController,
            ), patch.object(
                smooth_animation,
                "build_realtime_model",
                return_value="fake-llm",
            ), patch.object(
                smooth_animation,
                "AgentSession",
                return_value=FakeSession(),
            ), patch.object(
                smooth_animation,
                "build_startup_reply_instructions",
                return_value="灯灯醒了。",
            ), patch.object(
                smooth_animation.noise_cancellation,
                "BVC",
                return_value="fake-noise-cancellation",
            ), patch.object(
                smooth_animation,
                "RoomInputOptions",
                side_effect=lambda **kwargs: kwargs,
            ):
                await smooth_animation.entrypoint(fake_ctx)

            assert len(constructed) == 1
            assert callable(constructed[0]["on_fallback_expression"])

        asyncio.run(_exercise())

    def test_smooth_animation_entrypoint_marks_motor_bus_enabled_from_server_state(self) -> None:
        import smooth_animation

        class FakeSession:
            async def start(self, **kwargs) -> None:
                return None

            async def generate_reply(self, instructions=None) -> None:
                return None

        async def _exercise() -> None:
            motor_flags = []
            settings = SimpleNamespace(
                model_provider="qwen",
                glm_use_server_vad=False,
                console_enable_apm=False,
                console_speech_threshold_db=-48.0,
                console_silence_duration_s=0.4,
                console_min_speech_duration_s=0.25,
                console_commit_cooldown_s=1.0,
                console_start_trigger_s=0.18,
                console_output_suppression_s=0.6,
                console_auto_calibrate=False,
                console_calibration_duration_s=1.6,
                console_calibration_margin_db=8.0,
                voice_state_path="/tmp/test-voice-state.json",
                led_count=40,
            )
            fake_agent = SimpleNamespace(
                settings=settings,
                auto_expression_controller=None,
                animation_service="fake-animation-service",
                animation_service_error=None,
                rgb_service="fake-rgb-service",
            )
            fake_ctx = SimpleNamespace(
                room="test-room",
                add_shutdown_callback=lambda callback: None,
                shutdown=Mock(),
            )
            fake_memory_runtime = SimpleNamespace(
                enabled=True,
                set_motor_bus_enabled=lambda enabled: motor_flags.append(enabled),
                close=lambda: None,
            )
            fake_motor_bus = SimpleNamespace(
                start=lambda: None,
                is_ready=lambda: True,
                host="127.0.0.1",
                port=8770,
                stop=lambda: None,
            )

            with patch.object(
                smooth_animation,
                "load_runtime_settings",
                return_value=settings,
            ), patch.object(
                smooth_animation,
                "bootstrap_agent_runtime",
                return_value=fake_memory_runtime,
            ), patch.object(
                smooth_animation,
                "LeLamp",
                return_value=fake_agent,
            ), patch.object(
                smooth_animation,
                "MotorBusServer",
                return_value=fake_motor_bus,
            ), patch.object(
                smooth_animation,
                "build_realtime_model",
                return_value="fake-llm",
            ), patch.object(
                smooth_animation,
                "AgentSession",
                return_value=FakeSession(),
            ), patch.object(
                smooth_animation,
                "build_startup_reply_instructions",
                return_value="灯灯醒了。",
            ), patch.object(
                smooth_animation.noise_cancellation,
                "BVC",
                return_value="fake-noise-cancellation",
            ), patch.object(
                smooth_animation,
                "RoomInputOptions",
                side_effect=lambda **kwargs: kwargs,
            ):
                await smooth_animation.entrypoint(fake_ctx)

            self.assertEqual(motor_flags, [True])

        asyncio.run(_exercise())

    def test_smooth_animation_entrypoint_shuts_job_down_when_session_start_fails(self) -> None:
        import smooth_animation

        class FailingSession:
            async def start(self, **kwargs) -> None:
                raise RuntimeError("boom")

            async def generate_reply(self, instructions=None) -> None:
                return None

        async def _exercise() -> None:
            settings = SimpleNamespace(
                model_provider="qwen",
                glm_use_server_vad=False,
                console_enable_apm=False,
                console_speech_threshold_db=-48.0,
                console_silence_duration_s=0.4,
                console_min_speech_duration_s=0.25,
                console_commit_cooldown_s=1.0,
                console_start_trigger_s=0.18,
                console_output_suppression_s=0.6,
                console_auto_calibrate=False,
                console_calibration_duration_s=1.6,
                console_calibration_margin_db=8.0,
                voice_state_path="/tmp/test-voice-state.json",
                led_count=40,
            )
            fake_agent = SimpleNamespace(settings=settings, auto_expression_controller=None)
            fake_ctx = SimpleNamespace(
                room="test-room",
                add_shutdown_callback=lambda callback: None,
                shutdown=Mock(),
            )
            fake_memory_runtime = SimpleNamespace(
                enabled=True,
                set_motor_bus_enabled=lambda enabled: None,
                close=lambda: None,
            )

            with patch.object(
                smooth_animation,
                "load_runtime_settings",
                return_value=settings,
            ), patch.object(
                smooth_animation,
                "bootstrap_agent_runtime",
                return_value=fake_memory_runtime,
            ), patch.object(
                smooth_animation,
                "LeLamp",
                return_value=fake_agent,
            ), patch.object(
                smooth_animation,
                "build_realtime_model",
                return_value="fake-llm",
            ), patch.object(
                smooth_animation,
                "AgentSession",
                return_value=FailingSession(),
            ), patch.object(
                smooth_animation,
                "build_startup_reply_instructions",
                return_value="灯灯醒了。",
            ), patch.object(
                smooth_animation.noise_cancellation,
                "BVC",
                return_value="fake-noise-cancellation",
            ), patch.object(
                smooth_animation,
                "RoomInputOptions",
                side_effect=lambda **kwargs: kwargs,
            ):
                with self.assertRaises(RuntimeError):
                    await smooth_animation.entrypoint(fake_ctx)

            fake_ctx.shutdown.assert_called_once()

        asyncio.run(_exercise())

    def test_smooth_animation_entrypoint_installs_console_patch_for_qwen(self) -> None:
        import smooth_animation

        class FakeSession:
            instances: list["FakeSession"] = []

            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs
                self.start_kwargs = None
                self.reply_instructions = None
                FakeSession.instances.append(self)

            async def start(self, **kwargs) -> None:
                self.start_kwargs = kwargs

            async def generate_reply(self, instructions=None) -> None:
                self.reply_instructions = instructions

        async def _exercise() -> None:
            settings = SimpleNamespace(
                model_provider="qwen",
                glm_use_server_vad=False,
                console_enable_apm=False,
                console_speech_threshold_db=-48.0,
                console_silence_duration_s=0.4,
                console_min_speech_duration_s=0.25,
                console_commit_cooldown_s=1.0,
                console_start_trigger_s=0.18,
                console_output_suppression_s=0.6,
                console_auto_calibrate=False,
                console_calibration_duration_s=1.6,
                console_calibration_margin_db=8.0,
                voice_state_path="/tmp/test-voice-state.json",
                led_count=40,
            )
            fake_agent = SimpleNamespace(
                settings=settings,
                animation_service="fake-animation-service",
                animation_service_error=None,
                rgb_service="fake-rgb-service",
                auto_expression_controller=None,
            )
            fake_ctx = SimpleNamespace(
                room="test-room",
                add_shutdown_callback=lambda callback: None,
                shutdown=Mock(),
            )

            with patch.object(smooth_animation, "LeLamp", return_value=fake_agent), patch.object(
                smooth_animation,
                "build_realtime_model",
                return_value="fake-llm",
            ), patch.object(
                smooth_animation,
                "AgentSession",
                FakeSession,
            ), patch.object(
                smooth_animation,
                "install_console_audio_patch",
            ) as install_patch, patch.object(
                smooth_animation,
                "build_startup_reply_instructions",
                return_value="灯灯醒了。",
            ), patch.object(
                smooth_animation.noise_cancellation,
                "BVC",
                return_value="fake-noise-cancellation",
            ), patch.object(
                smooth_animation,
                "RoomInputOptions",
                side_effect=lambda **kwargs: kwargs,
            ):
                await smooth_animation.entrypoint(fake_ctx)

            install_patch.assert_called_once_with(
                enable_apm=False,
                speech_threshold_db=-48.0,
                silence_duration_s=0.4,
                min_speech_duration_s=0.25,
                commit_cooldown_s=1.0,
                speech_start_duration_s=0.18,
                output_suppression_s=0.6,
                auto_calibrate=False,
                calibration_duration_s=1.6,
                calibration_margin_db=8.0,
                voice_state_path="/tmp/test-voice-state.json",
            )

        asyncio.run(_exercise())

        self.assertEqual(len(FakeSession.instances), 1)
        self.assertEqual(FakeSession.instances[0].kwargs["llm"], "fake-llm")
        self.assertEqual(FakeSession.instances[0].kwargs["turn_detection"], "manual")
        self.assertEqual(
            FakeSession.instances[0].start_kwargs["room_input_options"],
            {"noise_cancellation": "fake-noise-cancellation"},
        )
        self.assertEqual(FakeSession.instances[0].reply_instructions, "灯灯醒了。")

    def test_build_realtime_model_returns_qwen_adapter_for_qwen_provider(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MODEL_PROVIDER": "qwen",
                "MODEL_API_KEY": "test-key",
                "MODEL_BASE_URL": "https://dashscope.aliyuncs.com/api-ws/v1/realtime",
                "MODEL_NAME": "qwen3.5-omni-plus-realtime",
                "MODEL_VOICE": "Tina",
            },
            clear=True,
        ):
            settings = load_runtime_settings()
            llm = build_realtime_model(settings)

        self.assertEqual(llm.__class__.__name__, "QwenRealtimeModel")
        self.assertTrue(llm.capabilities.auto_tool_reply_generation)

    def test_qwen_session_payload_uses_pcm_and_allows_manual_turn_control(self) -> None:
        from lelamp.qwen_realtime import build_qwen_session_payload

        with patch.dict(
            os.environ,
            {
                "MODEL_PROVIDER": "qwen",
                "MODEL_API_KEY": "test-key",
                "MODEL_BASE_URL": "https://dashscope.aliyuncs.com/api-ws/v1/realtime",
                "MODEL_NAME": "qwen3.5-omni-plus-realtime",
                "MODEL_VOICE": "Tina",
            },
            clear=True,
        ):
            settings = load_runtime_settings()

        payload = build_qwen_session_payload(
            settings,
            voice=settings.model_voice,
            modalities=["text", "audio"],
            input_audio_transcription={"model": "qwen3-asr-flash-realtime"},
            turn_detection={
                "type": "server_vad",
                "threshold": 0.5,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 500,
                "create_response": True,
                "interrupt_response": True,
            },
            tool_choice="auto",
        )

        self.assertEqual(payload["input_audio_format"], "pcm")
        self.assertEqual(payload["output_audio_format"], "pcm")
        self.assertEqual(payload["tool_choice"], "auto")
        self.assertEqual(payload["input_audio_transcription"]["model"], "qwen3-asr-flash-realtime")
        self.assertEqual(payload["turn_detection"]["type"], "server_vad")
        self.assertNotIn("model", payload)

    def test_qwen_session_update_event_explicitly_disables_server_vad_by_default(self) -> None:
        async def _build_event():
            with patch.dict(
                os.environ,
                {
                    "MODEL_PROVIDER": "qwen",
                    "MODEL_API_KEY": "test-key",
                    "MODEL_BASE_URL": "https://dashscope.aliyuncs.com/api-ws/v1/realtime",
                    "MODEL_NAME": "qwen3.5-omni-plus-realtime",
                    "MODEL_VOICE": "Tina",
                },
                clear=True,
            ):
                settings = load_runtime_settings()
                llm = build_realtime_model(settings)

            session = llm.session()
            event = session._create_session_update_event()
            session._main_atask.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._main_atask
            return event

        event = asyncio.run(_build_event())
        dumped = event.model_dump(by_alias=True, exclude_unset=True, exclude_defaults=False)

        self.assertEqual(dumped["type"], "session.update")
        self.assertEqual(dumped["session"]["input_audio_format"], "pcm")
        self.assertEqual(dumped["session"]["output_audio_format"], "pcm")
        self.assertEqual(dumped["session"]["tool_choice"], "auto")
        self.assertIn("turn_detection", dumped["session"])
        self.assertIsNone(dumped["session"]["turn_detection"])
        self.assertNotIn("model", dumped["session"])

    def test_qwen_session_update_event_can_opt_into_server_vad(self) -> None:
        async def _build_event():
            with patch.dict(
                os.environ,
                {
                    "MODEL_PROVIDER": "qwen",
                    "MODEL_API_KEY": "test-key",
                    "MODEL_BASE_URL": "https://dashscope.aliyuncs.com/api-ws/v1/realtime",
                    "MODEL_NAME": "qwen3.5-omni-plus-realtime",
                    "MODEL_VOICE": "Tina",
                    "LELAMP_QWEN_USE_SERVER_VAD": "true",
                },
                clear=True,
            ):
                settings = load_runtime_settings()
                llm = build_realtime_model(settings)

            session = llm.session()
            event = session._create_session_update_event()
            session._main_atask.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._main_atask
            return event

        event = asyncio.run(_build_event())
        dumped = event.model_dump(by_alias=True, exclude_unset=True, exclude_defaults=False)

        self.assertEqual(dumped["session"]["turn_detection"]["type"], "server_vad")
        self.assertNotIn("model", dumped["session"])

    def test_qwen_keeps_openai_style_empty_tool_schema(self) -> None:
        import main
        import smooth_animation

        async def _build_event() -> dict[str, object]:
            with patch.object(
                smooth_animation, "AnimationService"
            ) as animation_service_cls, patch.object(
                smooth_animation, "RGBService"
            ) as rgb_service_cls:
                animation_service = Mock()
                animation_service.start.return_value = None
                animation_service.dispatch.return_value = None
                animation_service.get_available_recordings.return_value = ["idle"]
                animation_service_cls.return_value = animation_service

                rgb_service = Mock()
                rgb_service.start.return_value = None
                rgb_service.dispatch.return_value = None
                rgb_service_cls.return_value = rgb_service

                with patch.dict(
                    os.environ,
                    {
                        "MODEL_PROVIDER": "qwen",
                        "MODEL_API_KEY": "test-key",
                        "MODEL_BASE_URL": "https://dashscope.aliyuncs.com/api-ws/v1/realtime",
                        "MODEL_NAME": "qwen3.5-omni-plus-realtime",
                        "MODEL_VOICE": "Tina",
                        "LELAMP_ENABLE_RGB": "false",
                    },
                    clear=True,
                ):
                    settings = load_runtime_settings()
                    llm = build_realtime_model(settings)
                    lamp = main.LeLamp(settings=settings)

                session = llm.session()
                event = session._create_tools_update_event(lamp.tools)
                session._main_atask.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await session._main_atask
                return event.model_dump(by_alias=True, exclude_unset=True, exclude_defaults=False)

        dumped = asyncio.run(_build_event())
        tools = dumped["session"]["tools"]
        get_available_recordings_tool = next(
            tool for tool in tools if tool["name"] == "get_available_recordings"
        )

        self.assertEqual(get_available_recordings_tool["parameters"]["type"], "object")
        self.assertEqual(get_available_recordings_tool["parameters"]["properties"], {})
        self.assertEqual(get_available_recordings_tool["parameters"].get("required"), [])

    def test_qwen_expression_tool_exposes_small_style_enum(self) -> None:
        import main
        import smooth_animation

        async def _build_event() -> dict[str, object]:
            with patch.object(
                smooth_animation, "AnimationService"
            ) as animation_service_cls, patch.object(
                smooth_animation, "RGBService"
            ) as rgb_service_cls:
                animation_service = Mock()
                animation_service.start.return_value = None
                animation_service.dispatch.return_value = None
                animation_service.get_available_recordings.return_value = ["idle"]
                animation_service_cls.return_value = animation_service

                rgb_service = Mock()
                rgb_service.start.return_value = None
                rgb_service.dispatch.return_value = None
                rgb_service_cls.return_value = rgb_service

                with patch.dict(
                    os.environ,
                    {
                        "MODEL_PROVIDER": "qwen",
                        "MODEL_API_KEY": "test-key",
                        "MODEL_BASE_URL": "https://dashscope.aliyuncs.com/api-ws/v1/realtime",
                        "MODEL_NAME": "qwen3.5-omni-plus-realtime",
                        "MODEL_VOICE": "Tina",
                        "LELAMP_ENABLE_RGB": "true",
                    },
                    clear=True,
                ):
                    settings = load_runtime_settings()
                    llm = build_realtime_model(settings)
                    lamp = main.LeLamp(settings=settings)

                session = llm.session()
                event = session._create_tools_update_event(lamp.tools)
                session._main_atask.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await session._main_atask
                return event.model_dump(by_alias=True, exclude_unset=True, exclude_defaults=False)

        dumped = asyncio.run(_build_event())
        tools = dumped["session"]["tools"]
        express_tool = next(tool for tool in tools if tool["name"] == "express")
        style_schema = express_tool["parameters"]["properties"]["style"]

        self.assertEqual(style_schema["type"], "string")
        self.assertEqual(
            style_schema["enum"],
            ["caring", "worried", "sad", "happy", "curious", "shocked", "calm", "greeting", "celebrate"],
        )
        self.assertEqual(express_tool["parameters"]["required"], ["style"])

    def test_smooth_animation_lamp_stays_available_when_motion_start_fails(self) -> None:
        import smooth_animation

        class FailingAnimationService:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs

            def start(self) -> None:
                raise RuntimeError("Input voltage error!")

            def get_available_recordings(self) -> list[str]:
                return ["curious", "wake_up"]

            def dispatch(self, event_type: str, payload: str) -> None:
                raise AssertionError("dispatch should not be called when startup fails")

        class FakeRGBService:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs
                self.actions = []

            def start(self) -> None:
                return None

            def dispatch(self, event_type: str, payload) -> None:
                self.actions.append((event_type, payload))

        async def _exercise_motion_failure() -> tuple[str, str]:
            with patch.object(
                smooth_animation, "AnimationService", FailingAnimationService
            ), patch.object(smooth_animation, "RGBService", FakeRGBService), patch.dict(
                os.environ,
                {
                    "MODEL_PROVIDER": "qwen",
                    "MODEL_API_KEY": "test-key",
                    "MODEL_BASE_URL": "https://dashscope.aliyuncs.com/api-ws/v1/realtime",
                    "MODEL_NAME": "qwen3.5-omni-plus-realtime",
                    "MODEL_VOICE": "Tina",
                    "LELAMP_ENABLE_RGB": "true",
                },
                clear=True,
            ):
                lamp = smooth_animation.LeLamp(settings=load_runtime_settings())
                recordings = await lamp.get_available_recordings()
                play_result = await lamp.play_recording("curious")
                return recordings, play_result

        recordings, play_result = asyncio.run(_exercise_motion_failure())
        self.assertIn("Available recordings: curious, wake_up", recordings)
        self.assertIn("Motion is unavailable", play_result)
        self.assertIn("Input voltage error!", play_result)

    def test_qwen_reports_final_reply_text_to_voice_telemetry(self) -> None:
        from livekit.plugins.openai.realtime import realtime_model as oai_rt

        async def _exercise_reply_text() -> None:
            with patch.dict(
                os.environ,
                {
                    "MODEL_PROVIDER": "qwen",
                    "MODEL_API_KEY": "test-key",
                    "MODEL_BASE_URL": "https://dashscope.aliyuncs.com/api-ws/v1/realtime",
                    "MODEL_NAME": "qwen3.5-omni-plus-realtime",
                    "MODEL_VOICE": "Tina",
                },
                clear=True,
            ):
                settings = load_runtime_settings()
                llm = build_realtime_model(settings)

            session = llm.session()
            session._main_atask.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._main_atask

            session._response_created_futures["response_create_reply_text"] = asyncio.Future()
            session._handle_response_created(
                oai_rt.ResponseCreatedEvent.construct(
                    type="response.created",
                    response=oai_rt.Response.construct(
                        id="resp_1",
                        metadata={"client_event_id": "response_create_reply_text"},
                    ),
                )
            )

            with patch("lelamp.qwen_realtime.get_voice_telemetry") as get_voice_telemetry:
                telemetry = Mock()
                get_voice_telemetry.return_value = telemetry
                session._handle_response_text_done(
                    oai_rt.ResponseTextDoneEvent.construct(
                        type="response.text.done",
                        event_id="evt_2",
                        response_id="resp_1",
                        item_id="item_1",
                        output_index=0,
                        content_index=0,
                        text="你好，我在。",
                    )
                )

            telemetry.update.assert_called_with(
                status="running",
                local_state="replying",
                last_reply_text="你好，我在。",
                last_result="assistant reply ready",
                force=True,
            )

        asyncio.run(_exercise_reply_text())

    def test_qwen_sanitizes_stage_direction_reply_text_before_telemetry(self) -> None:
        from livekit.plugins.openai.realtime import realtime_model as oai_rt

        async def _exercise_reply_text() -> None:
            with patch.dict(
                os.environ,
                {
                    "MODEL_PROVIDER": "qwen",
                    "MODEL_API_KEY": "test-key",
                    "MODEL_BASE_URL": "https://dashscope.aliyuncs.com/api-ws/v1/realtime",
                    "MODEL_NAME": "qwen3.5-omni-plus-realtime",
                    "MODEL_VOICE": "Tina",
                },
                clear=True,
            ):
                settings = load_runtime_settings()
                llm = build_realtime_model(settings)

            session = llm.session()
            session._main_atask.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._main_atask

            session._response_created_futures["response_create_reply_text"] = asyncio.Future()
            session._handle_response_created(
                oai_rt.ResponseCreatedEvent.construct(
                    type="response.created",
                    response=oai_rt.Response.construct(
                        id="resp_1",
                        metadata={"client_event_id": "response_create_reply_text"},
                    ),
                )
            )

            with patch("lelamp.qwen_realtime.get_voice_telemetry") as get_voice_telemetry:
                telemetry = Mock()
                get_voice_telemetry.return_value = telemetry
                session._handle_response_text_done(
                    oai_rt.ResponseTextDoneEvent.construct(
                        type="response.text.done",
                        event_id="evt_2",
                        response_id="resp_1",
                        item_id="item_1",
                        output_index=0,
                        content_index=0,
                        text="那我给你亮个节奏灯，你弹你的，我跟着晃。不过别太吵。",
                    )
                )

            telemetry.update.assert_called_with(
                status="running",
                local_state="replying",
                last_reply_text="你弹你的，我陪着你。不过别太吵。",
                last_result="assistant reply ready",
                force=True,
            )

        asyncio.run(_exercise_reply_text())

    def test_motion_and_light_tools_return_quiet_success_markers(self) -> None:
        import main
        import smooth_animation

        class FakeAnimationService:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs
                self.actions: list[tuple[str, object]] = []

            def start(self) -> None:
                return None

            def dispatch(self, event_type: str, payload) -> None:
                self.actions.append((event_type, payload))

            def get_available_recordings(self) -> list[str]:
                return ["curious", "wake_up"]

        class FakeRGBService:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs
                self.actions: list[tuple[str, object]] = []

            def start(self) -> None:
                return None

            def dispatch(self, event_type: str, payload) -> None:
                self.actions.append((event_type, payload))

        async def _exercise_quiet_tool_results() -> tuple[str, str]:
            with patch.object(
                smooth_animation, "AnimationService", FakeAnimationService
            ), patch.object(
                smooth_animation, "RGBService", FakeRGBService
            ), patch.dict(
                os.environ,
                {
                    "MODEL_PROVIDER": "qwen",
                    "MODEL_API_KEY": "test-key",
                    "MODEL_BASE_URL": "https://dashscope.aliyuncs.com/api-ws/v1/realtime",
                    "MODEL_NAME": "qwen3.5-omni-flash-realtime",
                    "MODEL_VOICE": "Tina",
                    "LELAMP_ENABLE_RGB": "true",
                },
                clear=True,
            ):
                lamp = main.LeLamp(settings=load_runtime_settings())
                play_result = await lamp.play_recording("curious")
                light_result = await lamp.set_rgb_solid(1, 2, 3)
                return play_result, light_result

        play_result, light_result = asyncio.run(_exercise_quiet_tool_results())
        self.assertEqual(play_result, "motion_ok")
        self.assertEqual(light_result, "light_ok")

    def test_expression_tool_dispatches_motion_and_light_together(self) -> None:
        import main
        import smooth_animation

        class FakeAnimationService:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs
                self.actions: list[tuple[str, object]] = []

            def start(self) -> None:
                return None

            def dispatch(self, event_type: str, payload) -> None:
                self.actions.append((event_type, payload))

            def get_available_recordings(self) -> list[str]:
                return ["curious", "happy_wiggle", "wake_up"]

        class FakeRGBService:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs
                self.actions: list[tuple[str, object]] = []

            def start(self) -> None:
                return None

            def dispatch(self, event_type: str, payload) -> None:
                self.actions.append((event_type, payload))

        async def _exercise_expression_tool() -> tuple[str, list[tuple[str, object]], list[tuple[str, object]]]:
            with patch.object(
                smooth_animation, "AnimationService", FakeAnimationService
            ), patch.object(
                smooth_animation, "RGBService", FakeRGBService
            ), patch.dict(
                os.environ,
                {
                    "MODEL_PROVIDER": "qwen",
                    "MODEL_API_KEY": "test-key",
                    "MODEL_BASE_URL": "https://dashscope.aliyuncs.com/api-ws/v1/realtime",
                    "MODEL_NAME": "qwen3.5-omni-flash-realtime",
                    "MODEL_VOICE": "Tina",
                    "LELAMP_ENABLE_RGB": "true",
                },
                clear=True,
            ):
                lamp = main.LeLamp(settings=load_runtime_settings())
                result = await lamp.express("happy")
                return result, lamp.animation_service.actions, lamp.rgb_service.actions

        result, motion_actions, light_actions = asyncio.run(_exercise_expression_tool())
        self.assertEqual(result, "expression_ok")
        self.assertEqual(motion_actions[-1], ("play", "happy_wiggle"))
        self.assertEqual(light_actions[-1], ("solid", (70, 255, 120)))

    def test_qwen_ignores_late_response_created_after_future_is_done(self) -> None:
        from livekit.plugins.openai.realtime import realtime_model as oai_rt

        async def _exercise_late_response_created() -> None:
            with patch.dict(
                os.environ,
                {
                    "MODEL_PROVIDER": "qwen",
                    "MODEL_API_KEY": "test-key",
                    "MODEL_BASE_URL": "https://dashscope.aliyuncs.com/api-ws/v1/realtime",
                    "MODEL_NAME": "qwen3.5-omni-plus-realtime",
                    "MODEL_VOICE": "Tina",
                },
                clear=True,
            ):
                settings = load_runtime_settings()
                llm = build_realtime_model(settings)

            session = llm.session()
            session._main_atask.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._main_atask

            fut = asyncio.Future()
            fut.set_exception(oai_rt.llm.RealtimeError("generate_reply timed out."))
            _ = fut.exception()
            session._response_created_futures["response_create_late"] = fut

            with patch.object(session, "emit") as emit:
                session._handle_response_created(
                    oai_rt.ResponseCreatedEvent.construct(
                        type="response.created",
                        response=oai_rt.Response.construct(
                            id="resp_late",
                            metadata={"client_event_id": "response_create_late"},
                        ),
                    )
                )

            self.assertEqual(session._current_response_id, "resp_late")
            emit.assert_called()

        asyncio.run(_exercise_late_response_created())

    def test_qwen_interrupt_finishes_pending_generate_reply_before_response_created(self) -> None:
        from livekit.plugins.openai.realtime import realtime_model as oai_rt

        async def _exercise_interrupt() -> None:
            with patch.dict(
                os.environ,
                {
                    "MODEL_PROVIDER": "qwen",
                    "MODEL_API_KEY": "test-key",
                    "MODEL_BASE_URL": "https://dashscope.aliyuncs.com/api-ws/v1/realtime",
                    "MODEL_NAME": "qwen3.5-omni-plus-realtime",
                    "MODEL_VOICE": "Tina",
                },
                clear=True,
            ):
                settings = load_runtime_settings()
                llm = build_realtime_model(settings)

            session = llm.session()
            session._main_atask.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._main_atask

            future = session.generate_reply(instructions="灯灯醒了。")
            self.assertFalse(future.done())

            session.interrupt()
            await asyncio.sleep(0)

            self.assertTrue(future.done())
            self.assertFalse(session._response_created_futures)
            with self.assertRaises(oai_rt.llm.RealtimeError) as exc_info:
                future.result()
            self.assertIn("interrupted", str(exc_info.exception))

        asyncio.run(_exercise_interrupt())

    def test_qwen_detects_capacity_limited_close_frames(self) -> None:
        from lelamp.qwen_realtime import QwenRealtimeSession

        self.assertTrue(
            QwenRealtimeSession._is_capacity_limited_close(
                1011,
                "Too many requests. Your requests are being throttled due to system capacity limits. Please try again later.",
            )
        )
        self.assertFalse(
            QwenRealtimeSession._is_capacity_limited_close(
                1000,
                "Normal closure",
            )
        )

    def test_qwen_ignores_unmatched_response_created_when_manual_turns_are_enabled(self) -> None:
        from livekit.plugins.openai.realtime import realtime_model as oai_rt

        async def _exercise_unmatched_response() -> None:
            with patch.dict(
                os.environ,
                {
                    "MODEL_PROVIDER": "qwen",
                    "MODEL_API_KEY": "test-key",
                    "MODEL_BASE_URL": "https://dashscope.aliyuncs.com/api-ws/v1/realtime",
                    "MODEL_NAME": "qwen3.5-omni-plus-realtime",
                    "MODEL_VOICE": "Tina",
                },
                clear=True,
            ):
                settings = load_runtime_settings()
                llm = build_realtime_model(settings)

            session = llm.session()
            session._main_atask.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._main_atask

            with patch.object(session, "emit") as emit:
                session._handle_response_created(
                    oai_rt.ResponseCreatedEvent.construct(
                        type="response.created",
                        response=oai_rt.Response.construct(id="resp_orphan", metadata=None),
                    )
                )

            self.assertIsNone(session._current_response_id)
            emit.assert_not_called()

        asyncio.run(_exercise_unmatched_response())

    def test_qwen_ignores_empty_response_id_audio_transcript_done(self) -> None:
        from livekit.plugins.openai.realtime import realtime_model as oai_rt

        async def _exercise_empty_response_id() -> None:
            with patch.dict(
                os.environ,
                {
                    "MODEL_PROVIDER": "qwen",
                    "MODEL_API_KEY": "test-key",
                    "MODEL_BASE_URL": "https://dashscope.aliyuncs.com/api-ws/v1/realtime",
                    "MODEL_NAME": "qwen3.5-omni-plus-realtime",
                    "MODEL_VOICE": "Tina",
                },
                clear=True,
            ):
                settings = load_runtime_settings()
                llm = build_realtime_model(settings)

            session = llm.session()
            session._main_atask.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._main_atask

            session._response_created_futures["response_create_empty_id"] = asyncio.Future()
            session._handle_response_created(
                oai_rt.ResponseCreatedEvent.construct(
                    type="response.created",
                    response=oai_rt.Response.construct(
                        id="resp_live",
                        metadata={"client_event_id": "response_create_empty_id"},
                    ),
                )
            )

            with patch.object(
                oai_rt.RealtimeSession,
                "_handle_response_audio_transcript_done",
            ) as super_handler, patch("lelamp.qwen_realtime.get_voice_telemetry") as get_voice_telemetry:
                telemetry = Mock()
                get_voice_telemetry.return_value = telemetry
                session._handle_response_audio_transcript_done(
                    oai_rt.ResponseAudioTranscriptDoneEvent.construct(
                        type="response.audio_transcript.done",
                        event_id="evt_empty",
                        response_id="",
                        item_id="item_1",
                        output_index=0,
                        content_index=0,
                        transcript="",
                    )
                )

            self.assertEqual(session._current_response_id, "resp_live")
            super_handler.assert_not_called()
            telemetry.update.assert_not_called()

        asyncio.run(_exercise_empty_response_id())

    def test_qwen_suppresses_empty_input_transcription_turn(self) -> None:
        from livekit.plugins.openai.realtime import realtime_model as oai_rt

        async def _exercise_empty_input_transcription() -> None:
            with patch.dict(
                os.environ,
                {
                    "MODEL_PROVIDER": "qwen",
                    "MODEL_API_KEY": "test-key",
                    "MODEL_BASE_URL": "https://dashscope.aliyuncs.com/api-ws/v1/realtime",
                    "MODEL_NAME": "qwen3.5-omni-plus-realtime",
                    "MODEL_VOICE": "Tina",
                },
                clear=True,
            ):
                settings = load_runtime_settings()
                llm = build_realtime_model(settings)

            session = llm.session()
            session._main_atask.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._main_atask

            future = asyncio.Future()
            session._response_created_futures["response_create_empty_turn"] = future

            with patch.object(session, "emit") as emit, patch(
                "lelamp.qwen_realtime.get_voice_telemetry"
            ) as get_voice_telemetry:
                telemetry = Mock()
                get_voice_telemetry.return_value = telemetry

                session._handle_conversion_item_input_audio_transcription_completed(
                    oai_rt.ConversationItemInputAudioTranscriptionCompletedEvent.construct(
                        type="conversation.item.input_audio_transcription.completed",
                        item_id="item_user_turn",
                        content_index=0,
                        transcript="   ",
                    )
                )
                session._handle_response_created(
                    oai_rt.ResponseCreatedEvent.construct(
                        type="response.created",
                        response=oai_rt.Response.construct(
                            id="resp_empty_turn",
                            metadata={"client_event_id": "response_create_empty_turn"},
                        ),
                    )
                )

            self.assertTrue(future.done())
            with self.assertRaises(oai_rt.llm.RealtimeError) as exc_info:
                future.result()
            self.assertIn("empty input", str(exc_info.exception))
            self.assertIsNone(session._current_generation)
            self.assertIn("resp_empty_turn", session._ignored_response_ids)
            self.assertNotIn(
                "generation_created",
                [call.args[0] for call in emit.call_args_list if call.args],
            )
            telemetry.update.assert_any_call(
                status="ready",
                local_state="idle",
                last_asr_status="ok",
                last_asr_error_code=None,
                last_asr_text="   ",
                last_result="empty input ignored",
                force=True,
            )

            session._handle_response_done(
                oai_rt.ResponseDoneEvent.construct(
                    type="response.done",
                    response=oai_rt.Response.construct(
                        id="resp_empty_turn",
                        status="completed",
                        usage=None,
                    ),
                )
            )

            self.assertNotIn("resp_empty_turn", session._ignored_response_ids)

        asyncio.run(_exercise_empty_input_transcription())

    def test_qwen_maps_function_call_audio_to_synthetic_message(self) -> None:
        from livekit.plugins.openai.realtime import realtime_model as oai_rt

        async def _exercise_non_message_audio_delta() -> None:
            with patch.dict(
                os.environ,
                {
                    "MODEL_PROVIDER": "qwen",
                    "MODEL_API_KEY": "test-key",
                    "MODEL_BASE_URL": "https://dashscope.aliyuncs.com/api-ws/v1/realtime",
                    "MODEL_NAME": "qwen3.5-omni-plus-realtime",
                    "MODEL_VOICE": "Tina",
                },
                clear=True,
            ):
                settings = load_runtime_settings()
                llm = build_realtime_model(settings)

            session = llm.session()
            session._main_atask.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._main_atask

            session._response_created_futures["response_create_non_message"] = asyncio.Future()
            session._handle_response_created(
                oai_rt.ResponseCreatedEvent.construct(
                    type="response.created",
                    response=oai_rt.Response.construct(
                        id="resp_live",
                        metadata={"client_event_id": "response_create_non_message"},
                    ),
                )
            )
            session._handle_response_output_item_added(
                oai_rt.ResponseOutputItemAddedEvent.construct(
                    type="response.output_item.added",
                    response_id="resp_live",
                    output_index=0,
                    item=oai_rt.ConversationItem.construct(
                        id="item_fn",
                        type="function_call",
                        status="in_progress",
                        call_id="call_1",
                        name="play_recording",
                        arguments="",
                        object="realtime.item",
                    ),
                )
            )

            with patch.object(
                oai_rt.RealtimeSession,
                "_handle_response_audio_delta",
            ) as super_handler:
                session._handle_response_audio_delta(
                    oai_rt.ResponseAudioDeltaEvent.construct(
                        type="response.audio.delta",
                        event_id="evt_audio",
                        response_id="resp_live",
                        item_id="item_fn",
                        output_index=0,
                        content_index=0,
                        delta="AAAA",
                    )
                )

            super_handler.assert_called_once()
            self.assertEqual(
                super_handler.call_args.args[-1].item_id,
                "qwen-message-resp_live",
            )
            self.assertIn("qwen-message-resp_live", session._current_generation.messages)

        asyncio.run(_exercise_non_message_audio_delta())

    def test_qwen_keeps_linked_followup_response_in_same_generation(self) -> None:
        from livekit.plugins.openai.realtime import realtime_model as oai_rt

        async def _exercise_linked_followup_response() -> None:
            with patch.dict(
                os.environ,
                {
                    "MODEL_PROVIDER": "qwen",
                    "MODEL_API_KEY": "test-key",
                    "MODEL_BASE_URL": "https://dashscope.aliyuncs.com/api-ws/v1/realtime",
                    "MODEL_NAME": "qwen3.5-omni-plus-realtime",
                    "MODEL_VOICE": "Tina",
                },
                clear=True,
            ):
                settings = load_runtime_settings()
                llm = build_realtime_model(settings)

            session = llm.session()
            session._main_atask.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._main_atask

            session._response_created_futures["response_create_linked"] = asyncio.Future()
            session._handle_response_created(
                oai_rt.ResponseCreatedEvent.construct(
                    type="response.created",
                    response=oai_rt.Response.construct(
                        id="resp_primary",
                        metadata={"client_event_id": "response_create_linked"},
                    ),
                )
            )
            session._handle_response_output_item_added(
                oai_rt.ResponseOutputItemAddedEvent.construct(
                    type="response.output_item.added",
                    response_id="resp_primary",
                    output_index=0,
                    item=oai_rt.ConversationItem.construct(
                        id="item_shared",
                        type="message",
                        role="assistant",
                        status="in_progress",
                        content=[],
                        object="realtime.item",
                    ),
                )
            )
            session._handle_response_created(
                oai_rt.ResponseCreatedEvent.construct(
                    type="response.created",
                    response=oai_rt.Response.construct(
                        id="resp_followup",
                        metadata=None,
                    ),
                )
            )

            with patch.object(
                oai_rt.RealtimeSession,
                "_handle_response_output_item_done",
            ) as output_item_done_handler:
                session._handle_response_output_item_done(
                    oai_rt.ResponseOutputItemDoneEvent.construct(
                        type="response.output_item.done",
                        response_id="resp_followup",
                        output_index=0,
                        item=oai_rt.ConversationItem.construct(
                            id="item_shared",
                            type="function_call",
                            status="completed",
                            call_id="call_1",
                            name="play_recording",
                            arguments='{"name":"happy_wiggle"}',
                            object="realtime.item",
                        ),
                    )
                )

            output_item_done_handler.assert_called_once()
            self.assertEqual(output_item_done_handler.call_args.args[-1].response_id, "resp_followup")
            self.assertIsNotNone(session._current_generation)

            session._handle_response_done(
                oai_rt.ResponseDoneEvent.construct(
                    type="response.done",
                    response=oai_rt.Response.construct(
                        id="resp_followup",
                        status="completed",
                        usage=None,
                    ),
                )
            )
            self.assertIsNotNone(session._current_generation)

            session._handle_response_done(
                oai_rt.ResponseDoneEvent.construct(
                    type="response.done",
                    response=oai_rt.Response.construct(
                        id="resp_primary",
                        status="completed",
                        usage=None,
                    ),
                )
            )
            self.assertIsNone(session._current_generation)
            self.assertIsNone(session._current_response_id)

        asyncio.run(_exercise_linked_followup_response())

    def test_qwen_synthesizes_missing_message_item_id_for_audio_responses(self) -> None:
        from livekit.plugins.openai.realtime import realtime_model as oai_rt

        async def _exercise_missing_item_id() -> None:
            with patch.dict(
                os.environ,
                {
                    "MODEL_PROVIDER": "qwen",
                    "MODEL_API_KEY": "test-key",
                    "MODEL_BASE_URL": "https://dashscope.aliyuncs.com/api-ws/v1/realtime",
                    "MODEL_NAME": "qwen3.5-omni-plus-realtime",
                    "MODEL_VOICE": "Tina",
                },
                clear=True,
            ):
                settings = load_runtime_settings()
                llm = build_realtime_model(settings)

            session = llm.session()
            session._main_atask.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._main_atask

            session._response_created_futures["response_create_missing_item"] = asyncio.Future()
            session._handle_response_created(
                oai_rt.ResponseCreatedEvent.construct(
                    type="response.created",
                    response=oai_rt.Response.construct(
                        id="resp_live",
                        metadata={"client_event_id": "response_create_missing_item"},
                    ),
                )
            )
            session._handle_response_output_item_added(
                oai_rt.ResponseOutputItemAddedEvent.construct(
                    type="response.output_item.added",
                    response_id="resp_live",
                    output_index=0,
                    item=oai_rt.ConversationItem.construct(
                        type="message",
                        role="assistant",
                        status="in_progress",
                        content=[],
                        object="realtime.item",
                    ),
                )
            )

            self.assertIn("qwen-message-resp_live", session._current_generation.messages)

            with patch.object(
                oai_rt.RealtimeSession,
                "_handle_response_content_part_added",
            ) as content_part_handler, patch.object(
                oai_rt.RealtimeSession,
                "_handle_response_audio_delta",
            ) as audio_delta_handler, patch.object(
                oai_rt.RealtimeSession,
                "_handle_response_output_item_done",
            ) as output_item_done_handler:
                session._handle_response_content_part_added(
                    oai_rt.ResponseContentPartAddedEvent.construct(
                        type="response.content_part.added",
                        response_id="resp_live",
                        output_index=0,
                        content_index=0,
                        part=oai_rt.ConversationItemContent.construct(type="audio", text=""),
                    )
                )
                session._handle_response_audio_delta(
                    oai_rt.ResponseAudioDeltaEvent.construct(
                        type="response.audio.delta",
                        event_id="evt_audio",
                        response_id="resp_live",
                        output_index=0,
                        content_index=0,
                        delta="AAAA",
                    )
                )
                session._handle_response_output_item_done(
                    oai_rt.ResponseOutputItemDoneEvent.construct(
                        type="response.output_item.done",
                        response_id="resp_live",
                        output_index=0,
                        item=oai_rt.ConversationItem.construct(
                            type="message",
                            role="assistant",
                            status="completed",
                            content=[oai_rt.ConversationItemContent.construct(type="audio")],
                            object="realtime.item",
                        ),
                    )
                )

            self.assertEqual(content_part_handler.call_args.args[-1].item_id, "qwen-message-resp_live")
            self.assertEqual(audio_delta_handler.call_args.args[-1].item_id, "qwen-message-resp_live")
            self.assertEqual(
                output_item_done_handler.call_args.args[-1].item.id,
                "qwen-message-resp_live",
            )

        asyncio.run(_exercise_missing_item_id())

    def test_qwen_ignores_conversation_items_without_item_id(self) -> None:
        from livekit.plugins.openai.realtime import realtime_model as oai_rt

        async def _exercise_missing_conversation_item_id() -> None:
            with patch.dict(
                os.environ,
                {
                    "MODEL_PROVIDER": "qwen",
                    "MODEL_API_KEY": "test-key",
                    "MODEL_BASE_URL": "https://dashscope.aliyuncs.com/api-ws/v1/realtime",
                    "MODEL_NAME": "qwen3.5-omni-plus-realtime",
                    "MODEL_VOICE": "Tina",
                },
                clear=True,
            ):
                settings = load_runtime_settings()
                llm = build_realtime_model(settings)

            session = llm.session()
            session._main_atask.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._main_atask

            with patch.object(
                oai_rt.RealtimeSession,
                "_handle_conversion_item_created",
            ) as super_handler:
                session._handle_conversion_item_created(
                    oai_rt.ConversationItemCreatedEvent.construct(
                        type="conversation.item.created",
                        item=oai_rt.ConversationItem.construct(
                            type="message",
                            role="assistant",
                            status="in_progress",
                            content=[],
                            object="realtime.item",
                        ),
                    )
                )

            super_handler.assert_not_called()

        asyncio.run(_exercise_missing_conversation_item_id())

    def test_openai_provider_still_uses_openai_realtime_model(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MODEL_PROVIDER": "openai",
                "MODEL_API_KEY": "test-key",
                "MODEL_BASE_URL": "https://api.openai.com/v1",
                "MODEL_NAME": "gpt-realtime",
                "MODEL_VOICE": "ballad",
            },
            clear=True,
        ):
            settings = load_runtime_settings()
            llm = build_realtime_model(settings)

        self.assertIsInstance(llm, openai.realtime.RealtimeModel)


if __name__ == "__main__":
    unittest.main()
