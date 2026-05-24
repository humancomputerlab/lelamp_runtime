import asyncio
import base64
import contextlib
import os
import unittest
import warnings
import wave
from io import BytesIO
from unittest.mock import Mock
from unittest.mock import patch

from livekit import rtc
from livekit.plugins import openai

from lelamp.runtime_config import build_realtime_model, load_runtime_settings


class GLMRealtimeTests(unittest.TestCase):
    def test_build_realtime_model_returns_glm_adapter_for_glm_provider(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MODEL_PROVIDER": "glm",
                "MODEL_API_KEY": "test-key",
                "MODEL_BASE_URL": "https://open.bigmodel.cn/api/paas/v4/realtime",
                "MODEL_NAME": "glm-realtime",
                "MODEL_VOICE": "tongtong",
            },
            clear=True,
        ):
            settings = load_runtime_settings()
            llm = build_realtime_model(settings)

        self.assertEqual(llm.__class__.__name__, "GLMRealtimeModel")

    def test_build_realtime_model_for_glm_emits_no_audioop_deprecation(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MODEL_PROVIDER": "glm",
                "MODEL_API_KEY": "test-key",
                "MODEL_BASE_URL": "https://open.bigmodel.cn/api/paas/v4/realtime",
                "MODEL_NAME": "glm-realtime",
                "MODEL_VOICE": "tongtong",
            },
            clear=True,
        ):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                settings = load_runtime_settings()
                llm = build_realtime_model(settings)

        self.assertEqual(llm.__class__.__name__, "GLMRealtimeModel")
        self.assertFalse(
            [
                warning
                for warning in caught
                if issubclass(warning.category, DeprecationWarning)
                and "audioop" in str(warning.message)
            ]
        )

    def test_build_realtime_model_keeps_openai_for_openai_provider(self) -> None:
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

    def test_glm_session_payload_includes_beta_fields_and_uses_client_vad_defaults(self) -> None:
        from lelamp.glm_realtime import build_glm_session_payload

        with patch.dict(
            os.environ,
            {
                "MODEL_PROVIDER": "glm",
                "MODEL_API_KEY": "test-key",
                "MODEL_BASE_URL": "https://open.bigmodel.cn/api/paas/v4/realtime",
                "MODEL_NAME": "glm-realtime",
                "MODEL_VOICE": "tongtong",
            },
            clear=True,
        ):
            settings = load_runtime_settings()

        payload = build_glm_session_payload(
            settings,
            model=settings.model_name or "glm-realtime",
            voice=settings.model_voice,
            temperature=0.8,
            modalities=["text", "audio"],
        )

        self.assertEqual(payload["input_audio_format"], "wav")
        self.assertEqual(payload["output_audio_format"], "pcm")
        self.assertEqual(
            payload["beta_fields"],
            {
                "chat_mode": "audio",
                "tts_source": "e2e",
                "auto_search": False,
            },
        )
        self.assertNotIn("turn_detection", payload)
        self.assertNotIn("tool_choice", payload)

    def test_glm_tool_schema_injects_placeholder_for_no_arg_tools(self) -> None:
        from lelamp.glm_realtime import normalize_glm_tool_schema

        raw_schema = {
            "type": "function",
            "name": "get_available_recordings",
            "description": "List motions",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        }

        normalized = normalize_glm_tool_schema(raw_schema)

        self.assertIn("placeholder", normalized["parameters"]["properties"])
        self.assertEqual(normalized["parameters"]["required"], ["placeholder"])

    def test_glm_session_update_event_omits_turn_detection_by_default(self) -> None:
        async def _build_event():
            with patch.dict(
                os.environ,
                {
                    "MODEL_PROVIDER": "glm",
                    "MODEL_API_KEY": "test-key",
                    "MODEL_BASE_URL": "https://open.bigmodel.cn/api/paas/v4/realtime",
                    "MODEL_NAME": "glm-realtime",
                    "MODEL_VOICE": "tongtong",
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
        self.assertEqual(dumped["session"]["input_audio_format"], "wav")
        self.assertEqual(dumped["session"]["output_audio_format"], "pcm")
        self.assertNotIn("turn_detection", dumped["session"])
        self.assertEqual(
            dumped["session"]["beta_fields"],
            {
                "chat_mode": "audio",
                "tts_source": "e2e",
                "auto_search": False,
            },
        )

    def test_glm_session_update_event_can_enable_server_vad_when_requested(self) -> None:
        async def _build_event():
            with patch.dict(
                os.environ,
                {
                    "MODEL_PROVIDER": "glm",
                    "MODEL_API_KEY": "test-key",
                    "MODEL_BASE_URL": "https://open.bigmodel.cn/api/paas/v4/realtime",
                    "MODEL_NAME": "glm-realtime",
                    "MODEL_VOICE": "tongtong",
                    "LELAMP_GLM_USE_SERVER_VAD": "true",
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
        self.assertTrue(dumped["session"]["turn_detection"]["create_response"])
        self.assertTrue(dumped["session"]["turn_detection"]["interrupt_response"])

    def test_glm_generate_reply_resolves_without_metadata_echo(self) -> None:
        from livekit.plugins.openai.realtime import realtime_model as oai_rt

        async def _exercise_generate_reply() -> bool:
            with patch.dict(
                os.environ,
                {
                    "MODEL_PROVIDER": "glm",
                    "MODEL_API_KEY": "test-key",
                    "MODEL_BASE_URL": "https://open.bigmodel.cn/api/paas/v4/realtime",
                    "MODEL_NAME": "glm-realtime",
                    "MODEL_VOICE": "tongtong",
                },
                clear=True,
            ):
                settings = load_runtime_settings()
                llm = build_realtime_model(settings)

            session = llm.session()
            fut = session.generate_reply(instructions="灯灯醒了。")
            session._main_atask.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._main_atask

            session._handle_response_created(
                oai_rt.ResponseCreatedEvent.construct(
                    type="response.created",
                    response=oai_rt.Response.construct(id="resp_1", metadata=None),
                )
            )
            generation = await asyncio.wait_for(fut, timeout=0.1)
            return generation.user_initiated

        self.assertTrue(asyncio.run(_exercise_generate_reply()))

    def test_glm_generate_reply_resolves_when_response_metadata_field_is_absent(self) -> None:
        from livekit.plugins.openai.realtime import realtime_model as oai_rt

        async def _exercise_generate_reply() -> bool:
            with patch.dict(
                os.environ,
                {
                    "MODEL_PROVIDER": "glm",
                    "MODEL_API_KEY": "test-key",
                    "MODEL_BASE_URL": "https://open.bigmodel.cn/api/paas/v4/realtime",
                    "MODEL_NAME": "glm-realtime",
                    "MODEL_VOICE": "tongtong",
                },
                clear=True,
            ):
                settings = load_runtime_settings()
                llm = build_realtime_model(settings)

            session = llm.session()
            fut = session.generate_reply(instructions="灯灯醒了。")
            session._main_atask.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._main_atask

            session._handle_response_created(
                oai_rt.ResponseCreatedEvent.construct(
                    type="response.created",
                    response=oai_rt.Response.construct(id="resp_1"),
                )
            )
            generation = await asyncio.wait_for(fut, timeout=0.1)
            return generation.user_initiated

        self.assertTrue(asyncio.run(_exercise_generate_reply()))

    def test_glm_commit_audio_sends_buffered_wav_on_commit(self) -> None:
        async def _exercise_commit() -> list[object]:
            with patch.dict(
                os.environ,
                {
                    "MODEL_PROVIDER": "glm",
                    "MODEL_API_KEY": "test-key",
                    "MODEL_BASE_URL": "https://open.bigmodel.cn/api/paas/v4/realtime",
                    "MODEL_NAME": "glm-realtime",
                    "MODEL_VOICE": "tongtong",
                },
                clear=True,
            ):
                settings = load_runtime_settings()
                llm = build_realtime_model(settings)

            session = llm.session()
            session._main_atask.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._main_atask

            sent_events: list[object] = []
            session.send_event = sent_events.append  # type: ignore[method-assign]

            frame = rtc.AudioFrame(
                data=(b"\x01\x02" * 2400),
                samples_per_channel=2400,
                sample_rate=24000,
                num_channels=1,
            )
            session.push_audio(frame)
            session.push_audio(frame)
            self.assertEqual(sent_events, [])

            session.commit_audio()
            return sent_events

        sent_events = asyncio.run(_exercise_commit())
        self.assertEqual(len(sent_events), 2)
        append_event = sent_events[0]
        commit_event = sent_events[1]
        self.assertEqual(append_event.type, "input_audio_buffer.append")
        self.assertEqual(commit_event.type, "input_audio_buffer.commit")

        wav_bytes = base64.b64decode(append_event.audio)
        with wave.open(BytesIO(wav_bytes), "rb") as wav_file:
            self.assertEqual(wav_file.getframerate(), 16000)
            self.assertEqual(wav_file.getnchannels(), 1)
            self.assertEqual(wav_file.getsampwidth(), 2)
            self.assertEqual(wav_file.getnframes(), 3200)

    def test_glm_ignores_stale_response_done_from_cancelled_generation(self) -> None:
        from livekit.plugins.openai.realtime import realtime_model as oai_rt

        async def _exercise_stale_done() -> tuple[str | None, list[str]]:
            with patch.dict(
                os.environ,
                {
                    "MODEL_PROVIDER": "glm",
                    "MODEL_API_KEY": "test-key",
                    "MODEL_BASE_URL": "https://open.bigmodel.cn/api/paas/v4/realtime",
                    "MODEL_NAME": "glm-realtime",
                    "MODEL_VOICE": "tongtong",
                },
                clear=True,
            ):
                settings = load_runtime_settings()
                llm = build_realtime_model(settings)

            session = llm.session()
            session._main_atask.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._main_atask

            session._handle_response_created(
                oai_rt.ResponseCreatedEvent.construct(
                    type="response.created",
                    response=oai_rt.Response.construct(id="resp_new", metadata=None),
                )
            )
            session._handle_response_output_item_added(
                oai_rt.ResponseOutputItemAddedEvent.construct(
                    type="response.output_item.added",
                    response_id="resp_new",
                    output_index=0,
                    item=oai_rt.ConversationItem.construct(
                        id="item_new",
                        type="message",
                        role="assistant",
                        status="in_progress",
                        content=[{}],
                        object="realtime.item",
                    ),
                )
            )
            session._handle_response_done(
                oai_rt.ResponseDoneEvent.construct(
                    type="response.done",
                    response=oai_rt.Response.construct(id="resp_old", status="cancelled"),
                )
            )
            session._handle_response_text_delta(
                oai_rt.ResponseTextDeltaEvent.construct(
                    type="response.text.delta",
                    response_id="resp_new",
                    item_id="item_new",
                    output_index=0,
                    content_index=0,
                    delta="你好",
                )
            )

            message = session._current_generation.messages["item_new"]
            collected = [await asyncio.wait_for(message.text_ch.__anext__(), timeout=0.1)]
            return session._current_response_id, collected

        current_response_id, collected = asyncio.run(_exercise_stale_done())
        self.assertEqual(current_response_id, "resp_new")
        self.assertEqual(collected, ["你好"])

    def test_glm_replays_pending_turn_after_reconnect(self) -> None:
        async def _exercise_replay() -> list[object]:
            with patch.dict(
                os.environ,
                {
                    "MODEL_PROVIDER": "glm",
                    "MODEL_API_KEY": "test-key",
                    "MODEL_BASE_URL": "https://open.bigmodel.cn/api/paas/v4/realtime",
                    "MODEL_NAME": "glm-realtime",
                    "MODEL_VOICE": "tongtong",
                },
                clear=True,
            ):
                settings = load_runtime_settings()
                llm = build_realtime_model(settings)

            session = llm.session()
            session._main_atask.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._main_atask

            session._pending_committed_audio_b64 = "ZmFrZS13YXY="
            session._pending_response_event = {
                "type": "response.create",
                "event_id": "response_create_123",
            }

            sent_events: list[object] = []
            session.send_event = sent_events.append  # type: ignore[method-assign]
            session._replay_pending_turn_after_reconnect()
            return sent_events

        sent_events = asyncio.run(_exercise_replay())
        self.assertEqual(len(sent_events), 3)
        self.assertEqual(sent_events[0].type, "input_audio_buffer.append")
        self.assertEqual(sent_events[0].audio, "ZmFrZS13YXY=")
        self.assertEqual(sent_events[1].type, "input_audio_buffer.commit")
        self.assertEqual(sent_events[2]["type"], "response.create")

    def test_glm_does_not_replay_pending_turn_when_generation_is_active(self) -> None:
        async def _exercise_no_replay() -> list[object]:
            with patch.dict(
                os.environ,
                {
                    "MODEL_PROVIDER": "glm",
                    "MODEL_API_KEY": "test-key",
                    "MODEL_BASE_URL": "https://open.bigmodel.cn/api/paas/v4/realtime",
                    "MODEL_NAME": "glm-realtime",
                    "MODEL_VOICE": "tongtong",
                },
                clear=True,
            ):
                settings = load_runtime_settings()
                llm = build_realtime_model(settings)

            session = llm.session()
            session._main_atask.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._main_atask

            session._pending_committed_audio_b64 = "ZmFrZS13YXY="
            session._pending_response_event = {
                "type": "response.create",
                "event_id": "response_create_123",
            }
            session._current_generation = Mock()

            sent_events: list[object] = []
            session.send_event = sent_events.append  # type: ignore[method-assign]
            session._replay_pending_turn_after_reconnect()
            return sent_events

        sent_events = asyncio.run(_exercise_no_replay())
        self.assertEqual(sent_events, [])

    def test_glm_reports_input_transcription_failures_to_voice_telemetry(self) -> None:
        from livekit.plugins.openai.realtime import realtime_model as oai_rt

        async def _exercise_failure() -> None:
            with patch.dict(
                os.environ,
                {
                    "MODEL_PROVIDER": "glm",
                    "MODEL_API_KEY": "test-key",
                    "MODEL_BASE_URL": "https://open.bigmodel.cn/api/paas/v4/realtime",
                    "MODEL_NAME": "glm-realtime",
                    "MODEL_VOICE": "tongtong",
                },
                clear=True,
            ):
                settings = load_runtime_settings()
                llm = build_realtime_model(settings)

            session = llm.session()
            session._main_atask.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._main_atask

            with patch("lelamp.glm_realtime.get_voice_telemetry") as get_voice_telemetry:
                telemetry = Mock()
                get_voice_telemetry.return_value = telemetry
                session._handle_conversion_item_input_audio_transcription_failed(
                    oai_rt.ConversationItemInputAudioTranscriptionFailedEvent.construct(
                        type="conversation.item.input_audio_transcription.failed",
                        event_id="evt_1",
                        item_id="item_1",
                        content_index=0,
                        error={
                            "code": "asr_no_result",
                            "message": "no result",
                            "type": "invalid_request_error",
                        },
                    )
                )

            telemetry.update.assert_called_with(
                status="warning",
                last_asr_status="failed",
                last_asr_error_code="asr_no_result",
                last_result="ASR failed: asr_no_result",
                force=True,
            )

        asyncio.run(_exercise_failure())

    def test_glm_reports_final_reply_text_to_voice_telemetry(self) -> None:
        from livekit.plugins.openai.realtime import realtime_model as oai_rt

        async def _exercise_reply_text() -> None:
            with patch.dict(
                os.environ,
                {
                    "MODEL_PROVIDER": "glm",
                    "MODEL_API_KEY": "test-key",
                    "MODEL_BASE_URL": "https://open.bigmodel.cn/api/paas/v4/realtime",
                    "MODEL_NAME": "glm-realtime",
                    "MODEL_VOICE": "tongtong",
                },
                clear=True,
            ):
                settings = load_runtime_settings()
                llm = build_realtime_model(settings)

            session = llm.session()
            session._main_atask.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._main_atask

            session._handle_response_created(
                oai_rt.ResponseCreatedEvent.construct(
                    type="response.created",
                    response=oai_rt.Response.construct(id="resp_1", metadata=None),
                )
            )

            with patch("lelamp.glm_realtime.get_voice_telemetry") as get_voice_telemetry:
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

    def test_glm_sanitizes_stage_direction_reply_text_before_telemetry(self) -> None:
        from livekit.plugins.openai.realtime import realtime_model as oai_rt

        async def _exercise_reply_text() -> None:
            with patch.dict(
                os.environ,
                {
                    "MODEL_PROVIDER": "glm",
                    "MODEL_API_KEY": "test-key",
                    "MODEL_BASE_URL": "https://open.bigmodel.cn/api/paas/v4/realtime",
                    "MODEL_NAME": "glm-realtime",
                    "MODEL_VOICE": "tongtong",
                },
                clear=True,
            ):
                settings = load_runtime_settings()
                llm = build_realtime_model(settings)

            session = llm.session()
            session._main_atask.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._main_atask

            session._handle_response_created(
                oai_rt.ResponseCreatedEvent.construct(
                    type="response.created",
                    response=oai_rt.Response.construct(id="resp_1", metadata=None),
                )
            )

            with patch("lelamp.glm_realtime.get_voice_telemetry") as get_voice_telemetry:
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

    def test_glm_tools_update_event_sanitizes_schema_for_glm(self) -> None:
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
                        "MODEL_PROVIDER": "glm",
                        "MODEL_API_KEY": "test-key",
                        "MODEL_BASE_URL": "https://open.bigmodel.cn/api/paas/v4/realtime",
                        "MODEL_NAME": "glm-realtime",
                        "MODEL_VOICE": "tongtong",
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
        placeholder_schema = get_available_recordings_tool["parameters"]["properties"]["placeholder"]
        self.assertEqual(placeholder_schema["type"], "string")
        self.assertIn("description", placeholder_schema)
        self.assertEqual(get_available_recordings_tool["parameters"]["required"], ["placeholder"])

        paint_rgb_pattern_tool = next(tool for tool in tools if tool["name"] == "paint_rgb_pattern")
        colors_schema = paint_rgb_pattern_tool["parameters"]["properties"]["colors"]
        self.assertEqual(colors_schema["items"]["type"], "array")
        self.assertEqual(colors_schema["items"]["items"]["type"], "integer")


if __name__ == "__main__":
    unittest.main()
