from __future__ import annotations

import asyncio
import base64
from copy import deepcopy
from io import BytesIO
from typing import Any, cast
from urllib.parse import urlparse, urlunparse
import wave

from livekit.plugins.openai.realtime import realtime_model as oai_rt
import numpy as np

from lelamp.reply_sanitizer import sanitize_spoken_reply
from lelamp.runtime_config import RuntimeSettings
from lelamp.voice_telemetry import configure_voice_telemetry, get_voice_telemetry


_GLM_INPUT_AUDIO_FORMAT = "wav"
_GLM_OUTPUT_AUDIO_FORMAT = "pcm"
_GLM_CHAT_MODE = "audio"
_GLM_TTS_SOURCE = "e2e"
_GLM_AUTO_SEARCH = False
_GLM_TOOL_LIMIT = 10
_GLM_INPUT_SAMPLE_RATE = 16000
_PCM16_DTYPE = np.dtype("<i2")


def build_glm_beta_fields(settings: RuntimeSettings) -> dict[str, object]:
    del settings
    return {
        "chat_mode": _GLM_CHAT_MODE,
        "tts_source": _GLM_TTS_SOURCE,
        "auto_search": _GLM_AUTO_SEARCH,
    }


def build_glm_session_payload(
    settings: RuntimeSettings,
    *,
    model: str,
    voice: str,
    temperature: float,
    modalities: list[str],
    instructions: str | None = None,
    tools: list[object] | None = None,
    input_audio_noise_reduction: object | None = None,
    turn_detection: object | None = None,
    speed: float | None = None,
    tracing: object | None = None,
    max_response_output_tokens: int | str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": model,
        "voice": voice,
        "input_audio_format": _GLM_INPUT_AUDIO_FORMAT,
        "output_audio_format": _GLM_OUTPUT_AUDIO_FORMAT,
        "modalities": modalities,
        "temperature": temperature,
        "beta_fields": build_glm_beta_fields(settings),
    }

    if instructions:
        payload["instructions"] = instructions
    if tools:
        payload["tools"] = tools
    if input_audio_noise_reduction is not None:
        payload["input_audio_noise_reduction"] = input_audio_noise_reduction
    if turn_detection is not None:
        payload["turn_detection"] = turn_detection
    if speed is not None:
        payload["speed"] = speed
    if tracing is not None:
        payload["tracing"] = tracing
    if max_response_output_tokens is not None:
        payload["max_response_output_tokens"] = max_response_output_tokens

    return payload


def normalize_glm_tool_schema(tool_desc: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(tool_desc)
    parameters = normalized.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {"type": "object"}

    properties = parameters.get("properties")
    if not isinstance(properties, dict) or not properties:
        parameters["properties"] = {
            "placeholder": {
                "type": "string",
                "description": "Optional placeholder for GLM realtime tool compatibility.",
            }
        }
        parameters["required"] = ["placeholder"]
    elif not parameters.get("required"):
        parameters.pop("required", None)

    normalized["parameters"] = parameters
    return normalized


def _glm_ws_url(base_url: str) -> str:
    if base_url.startswith("http"):
        base_url = base_url.replace("http", "ws", 1)

    parsed = urlparse(base_url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", parsed.query, ""))


def _resample_pcm16(audio_bytes: bytes, *, src_rate: int, dst_rate: int, channels: int) -> bytes:
    if src_rate == dst_rate or not audio_bytes:
        return audio_bytes

    samples = np.frombuffer(audio_bytes, dtype=_PCM16_DTYPE)
    frame_count = samples.size // channels
    if frame_count == 0:
        return b""

    samples = samples[: frame_count * channels].reshape(frame_count, channels)
    target_frame_count = max(1, round(frame_count * dst_rate / src_rate))
    if target_frame_count == frame_count:
        return samples.astype(_PCM16_DTYPE, copy=False).tobytes()

    source_positions = np.arange(frame_count, dtype=np.float64)
    target_positions = np.arange(target_frame_count, dtype=np.float64) * src_rate / dst_rate
    target_positions = np.clip(target_positions, 0, frame_count - 1)

    resampled = np.empty((target_frame_count, channels), dtype=np.int16)
    for channel_index in range(channels):
        channel = samples[:, channel_index].astype(np.float64, copy=False)
        channel_resampled = np.interp(target_positions, source_positions, channel)
        resampled[:, channel_index] = np.clip(np.rint(channel_resampled), -32768, 32767).astype(np.int16)

    return resampled.astype(_PCM16_DTYPE, copy=False).tobytes()


class GLMRealtimeModel(oai_rt.RealtimeModel):
    def __init__(self, *, settings: RuntimeSettings) -> None:
        self._glm_settings = settings
        configure_voice_telemetry(settings.voice_state_path)
        super().__init__(
            model=settings.model_name or "glm-realtime",
            voice=settings.model_voice,
            modalities=["text", "audio"],
            input_audio_transcription=None,
            turn_detection=oai_rt.DEFAULT_TURN_DETECTION if settings.glm_use_server_vad else None,
            tool_choice=None,
            api_key=settings.model_api_key,
            base_url=settings.model_base_url or "https://open.bigmodel.cn/api/paas/v4/realtime",
        )

    def session(self) -> "GLMRealtimeSession":
        sess = GLMRealtimeSession(self)
        self._sessions.add(sess)
        return sess


class GLMRealtimeSession(oai_rt.RealtimeSession):
    _realtime_model: GLMRealtimeModel

    def __init__(self, realtime_model: GLMRealtimeModel) -> None:
        super().__init__(realtime_model)
        self._pending_audio_bytes = bytearray()
        self._current_response_id: str | None = None
        self._pending_committed_audio_b64: str | None = None
        self._pending_response_event: dict[str, object] | None = None
        self.on("session_reconnected", self._replay_pending_turn_after_reconnect)

    def _glm_event(
        self,
        session_payload: dict[str, object],
        event_prefix: str,
    ) -> oai_rt.SessionUpdateEvent:
        return oai_rt.SessionUpdateEvent(
            type="session.update",
            session=oai_rt.session_update_event.Session.model_construct(**session_payload),
            event_id=oai_rt.utils.shortuuid(event_prefix),
        )

    def _current_glm_tools(
        self, tools: list[oai_rt.llm.FunctionTool | oai_rt.llm.RawFunctionTool] | None = None
    ) -> tuple[list[oai_rt.session_update_event.SessionTool], list[oai_rt.llm.FunctionTool | oai_rt.llm.RawFunctionTool]]:
        source_tools = tools
        if source_tools is None:
            source_tools = list(self._tools.function_tools.values())

        oai_tools: list[oai_rt.session_update_event.SessionTool] = []
        retained_tools: list[oai_rt.llm.FunctionTool | oai_rt.llm.RawFunctionTool] = []

        for tool in source_tools[:_GLM_TOOL_LIMIT]:
            if oai_rt.is_function_tool(tool):
                tool_desc = oai_rt.llm.utils.build_legacy_openai_schema(tool, internally_tagged=True)
            elif oai_rt.is_raw_function_tool(tool):
                tool_info = oai_rt.get_raw_function_info(tool)
                tool_desc = deepcopy(tool_info.raw_schema)
                tool_desc["type"] = "function"
            else:
                oai_rt.logger.error("GLM Realtime doesn't support this tool type", extra={"tool": tool})
                continue

            try:
                session_tool = oai_rt.session_update_event.SessionTool.model_validate(
                    normalize_glm_tool_schema(tool_desc)
                )
                oai_tools.append(session_tool)
                retained_tools.append(tool)
            except oai_rt.ValidationError:
                oai_rt.logger.error("GLM Realtime rejected tool schema", extra={"tool": tool_desc})

        return oai_tools, retained_tools

    def _session_payload(
        self,
        *,
        instructions: str | None | object = oai_rt.NOT_GIVEN,
        tools: list[oai_rt.session_update_event.SessionTool] | None | object = oai_rt.NOT_GIVEN,
    ) -> dict[str, object]:
        current_tools = None
        if tools is oai_rt.NOT_GIVEN:
            built_tools, _ = self._current_glm_tools()
            current_tools = built_tools or None
        else:
            current_tools = cast(list[oai_rt.session_update_event.SessionTool] | None, tools)

        current_instructions = self._instructions if instructions is oai_rt.NOT_GIVEN else cast(str | None, instructions)
        opts = self._realtime_model._opts

        return build_glm_session_payload(
            self._realtime_model._glm_settings,
            model=opts.model,
            voice=opts.voice,
            temperature=opts.temperature,
            modalities=cast(list[str], opts.modalities),
            instructions=current_instructions,
            tools=current_tools,
            input_audio_noise_reduction=opts.input_audio_noise_reduction,
            turn_detection=opts.turn_detection,
            speed=opts.speed,
            tracing=opts.tracing,
            max_response_output_tokens=opts.max_response_output_tokens,
        )

    async def _create_ws_conn(self) -> oai_rt.aiohttp.ClientWebSocketResponse:
        headers = {
            "User-Agent": "LiveKit Agents",
            "Authorization": f"Bearer {self._realtime_model._opts.api_key}",
        }
        url = _glm_ws_url(self._realtime_model._opts.base_url)
        return await oai_rt.asyncio.wait_for(
            self._realtime_model._ensure_http_session().ws_connect(url=url, headers=headers),
            self._realtime_model._opts.conn_options.timeout,
        )

    def _create_session_update_event(self) -> oai_rt.SessionUpdateEvent:
        return self._glm_event(self._session_payload(), "session_update_")

    def _create_tools_update_event(
        self, tools: list[oai_rt.llm.FunctionTool | oai_rt.llm.RawFunctionTool]
    ) -> oai_rt.SessionUpdateEvent:
        session_tools, _ = self._current_glm_tools(tools)
        return self._glm_event(self._session_payload(tools=session_tools), "tools_update_")

    async def update_tools(self, tools: list[oai_rt.llm.FunctionTool | oai_rt.llm.RawFunctionTool]) -> None:
        async with self._update_fnc_ctx_lock:
            session_tools, retained_tools = self._current_glm_tools(tools)
            self.send_event(self._glm_event(self._session_payload(tools=session_tools), "tools_update_"))
            self._tools = oai_rt.llm.ToolContext(retained_tools)

    async def update_instructions(self, instructions: str) -> None:
        self._instructions = instructions
        self.send_event(
            self._glm_event(
                self._session_payload(instructions=instructions),
                "instructions_update_",
            )
        )

    def generate_reply(
        self,
        *,
        user_input: oai_rt.NotGivenOr[str] = oai_rt.NOT_GIVEN,
        instructions: oai_rt.NotGivenOr[str] = oai_rt.NOT_GIVEN,
        tool_choice: oai_rt.NotGivenOr[oai_rt.llm.ToolChoice] = oai_rt.NOT_GIVEN,
        allow_interruptions: oai_rt.NotGivenOr[bool] = oai_rt.NOT_GIVEN,
    ) -> asyncio.Future[oai_rt.llm.GenerationCreatedEvent]:
        del user_input
        del tool_choice
        del allow_interruptions

        event_id = oai_rt.utils.shortuuid("response_create_")
        fut: asyncio.Future[oai_rt.llm.GenerationCreatedEvent] = asyncio.Future()
        self._response_created_futures[event_id] = fut

        event: dict[str, object] = {
            "type": "response.create",
            "event_id": event_id,
        }
        if oai_rt.is_given(instructions) and instructions:
            event["response"] = {"instructions": instructions}

        self._pending_response_event = deepcopy(event)
        self.send_event(event)

        def _on_timeout() -> None:
            if fut and not fut.done():
                fut.set_exception(oai_rt.llm.RealtimeError("generate_reply timed out."))

        handle = asyncio.get_event_loop().call_later(5.0, _on_timeout)
        fut.add_done_callback(lambda _: handle.cancel())
        return fut

    def push_audio(self, frame: oai_rt.rtc.AudioFrame) -> None:
        for f in self._resample_audio(frame):
            self._pending_audio_bytes.extend(f.data.tobytes())
            self._pushed_duration_s += f.duration

    def commit_audio(self) -> None:
        if self._pushed_duration_s <= 0.1 or not self._pending_audio_bytes:
            return

        wav_bytes = self._build_wav_bytes(bytes(self._pending_audio_bytes))
        audio_b64 = base64.b64encode(wav_bytes).decode("utf-8")
        self._pending_committed_audio_b64 = audio_b64
        self.send_event(
            oai_rt.InputAudioBufferAppendEvent(
                type="input_audio_buffer.append",
                audio=audio_b64,
            )
        )
        self.send_event(oai_rt.InputAudioBufferCommitEvent(type="input_audio_buffer.commit"))
        self._pending_audio_bytes.clear()
        self._pushed_duration_s = 0

    def clear_audio(self) -> None:
        self._pending_audio_bytes.clear()
        self._pending_committed_audio_b64 = None
        self._pending_response_event = None
        self.send_event(oai_rt.InputAudioBufferClearEvent(type="input_audio_buffer.clear"))
        self._pushed_duration_s = 0

    @staticmethod
    def _build_wav_bytes(audio_bytes: bytes) -> bytes:
        wav_audio = audio_bytes
        if oai_rt.SAMPLE_RATE != _GLM_INPUT_SAMPLE_RATE:
            wav_audio = _resample_pcm16(
                audio_bytes,
                src_rate=oai_rt.SAMPLE_RATE,
                dst_rate=_GLM_INPUT_SAMPLE_RATE,
                channels=oai_rt.NUM_CHANNELS,
            )

        wav_buffer = BytesIO()
        with wave.open(wav_buffer, "wb") as wav_file:
            wav_file.setnchannels(oai_rt.NUM_CHANNELS)
            wav_file.setsampwidth(2)
            wav_file.setframerate(_GLM_INPUT_SAMPLE_RATE)
            wav_file.writeframes(wav_audio)
        return wav_buffer.getvalue()

    def _handle_response_created(self, event: oai_rt.ResponseCreatedEvent) -> None:
        self._current_response_id = event.response.id
        assert event.response.id is not None, "response.id is None"
        get_voice_telemetry().update(
            status="running",
            local_state="replying",
            last_response_id=event.response.id,
            last_result="assistant response created",
            force=True,
        )

        self._current_generation = oai_rt._ResponseGeneration(
            message_ch=oai_rt.utils.aio.Chan(),
            function_ch=oai_rt.utils.aio.Chan(),
            messages={},
            _created_timestamp=oai_rt.time.time(),
            _done_fut=asyncio.Future(),
        )

        generation_ev = oai_rt.llm.GenerationCreatedEvent(
            message_stream=self._current_generation.message_ch,
            function_stream=self._current_generation.function_ch,
            user_initiated=False,
        )

        client_event_id: str | None = None
        metadata = getattr(event.response, "metadata", None)
        if isinstance(metadata, dict):
            client_event_id = metadata.get("client_event_id")

        if not client_event_id and self._response_created_futures:
            client_event_id = next(iter(self._response_created_futures))

        if client_event_id and (fut := self._response_created_futures.pop(client_event_id, None)):
            generation_ev.user_initiated = True
            fut.set_result(generation_ev)

        self.emit("generation_created", generation_ev)

    def _handle_response_output_item_added(self, event: oai_rt.ResponseOutputItemAddedEvent) -> None:
        if self._should_ignore_response_event(event.response_id, "response.output_item.added"):
            return
        super()._handle_response_output_item_added(event)

    def _handle_response_content_part_added(self, event: oai_rt.ResponseContentPartAddedEvent) -> None:
        if self._should_ignore_response_event(event.response_id, "response.content_part.added"):
            return
        super()._handle_response_content_part_added(event)

    def _handle_response_text_delta(self, event: oai_rt.ResponseTextDeltaEvent) -> None:
        if self._should_ignore_response_event(event.response_id, "response.text.delta"):
            return
        super()._handle_response_text_delta(event)

    def _handle_response_text_done(self, event: oai_rt.ResponseTextDoneEvent) -> None:
        if self._should_ignore_response_event(event.response_id, "response.text.done"):
            return
        sanitized_text = sanitize_spoken_reply(event.text)
        if sanitized_text != event.text:
            event = event.model_copy(update={"text": sanitized_text})
        super()._handle_response_text_done(event)
        get_voice_telemetry().update(
            status="running",
            local_state="replying",
            last_reply_text=sanitized_text,
            last_result="assistant reply ready",
            force=True,
        )

    def _handle_response_audio_transcript_delta(self, event: dict[str, Any]) -> None:
        if self._should_ignore_response_event(event.get("response_id"), "response.audio_transcript.delta"):
            return
        super()._handle_response_audio_transcript_delta(event)

    def _handle_response_audio_delta(self, event: oai_rt.ResponseAudioDeltaEvent) -> None:
        if self._should_ignore_response_event(event.response_id, "response.audio.delta"):
            return
        super()._handle_response_audio_delta(event)

    def _handle_response_audio_transcript_done(self, event: oai_rt.ResponseAudioTranscriptDoneEvent) -> None:
        if self._should_ignore_response_event(event.response_id, "response.audio_transcript.done"):
            return
        sanitized_transcript = sanitize_spoken_reply(event.transcript)
        if sanitized_transcript != event.transcript:
            event = event.model_copy(update={"transcript": sanitized_transcript})
        super()._handle_response_audio_transcript_done(event)
        get_voice_telemetry().update(
            status="running",
            local_state="replying",
            last_reply_text=sanitized_transcript,
            last_result="assistant audio transcript ready",
            force=True,
        )

    def _handle_response_audio_done(self, event: oai_rt.ResponseAudioDoneEvent) -> None:
        if self._should_ignore_response_event(event.response_id, "response.audio.done"):
            return
        super()._handle_response_audio_done(event)

    def _handle_response_output_item_done(self, event: oai_rt.ResponseOutputItemDoneEvent) -> None:
        if self._should_ignore_response_event(event.response_id, "response.output_item.done"):
            return
        super()._handle_response_output_item_done(event)

    def _handle_response_done(self, event: oai_rt.ResponseDoneEvent) -> None:
        response_id = event.response.id
        if self._should_ignore_response_event(response_id, "response.done"):
            return
        super()._handle_response_done(event)
        if response_id == self._current_response_id:
            get_voice_telemetry().update(
                status="ready",
                local_state="idle",
                last_result="assistant response finished",
                force=True,
            )
        if response_id == self._current_response_id:
            self._current_response_id = None
            self._pending_committed_audio_b64 = None
            self._pending_response_event = None

    def _handle_conversion_item_input_audio_transcription_completed(
        self,
        event: oai_rt.ConversationItemInputAudioTranscriptionCompletedEvent,
    ) -> None:
        super()._handle_conversion_item_input_audio_transcription_completed(event)
        get_voice_telemetry().update(
            status="running",
            local_state="replying",
            last_asr_status="ok",
            last_asr_error_code=None,
            last_asr_text=event.transcript,
            last_result="input transcription completed",
            force=True,
        )

    def _handle_conversion_item_input_audio_transcription_failed(
        self,
        event: oai_rt.ConversationItemInputAudioTranscriptionFailedEvent,
    ) -> None:
        super()._handle_conversion_item_input_audio_transcription_failed(event)
        error_code = getattr(event.error, "code", None) or "unknown"
        get_voice_telemetry().update(
            status="warning",
            last_asr_status="failed",
            last_asr_error_code=error_code,
            last_result=f"ASR failed: {error_code}",
            force=True,
        )

    def _should_ignore_response_event(self, response_id: str | None, event_type: str) -> bool:
        if response_id is None:
            return False
        if self._current_generation is None or self._current_response_id is None:
            oai_rt.logger.debug(
                "Ignoring GLM response event without active generation",
                extra={"event_type": event_type, "response_id": response_id},
            )
            return True
        if response_id == self._current_response_id:
            return False

        oai_rt.logger.debug(
            "Ignoring stale GLM response event",
            extra={
                "event_type": event_type,
                "response_id": response_id,
                "current_response_id": self._current_response_id,
            },
        )
        return True

    def _replay_pending_turn_after_reconnect(self, _event: object | None = None) -> None:
        if self._pending_response_event is None:
            return
        if self._current_generation is not None:
            oai_rt.logger.debug(
                "Skipping GLM pending turn replay because a generation is already active",
                extra={"current_response_id": self._current_response_id},
            )
            return

        if self._pending_committed_audio_b64:
            self.send_event(
                oai_rt.InputAudioBufferAppendEvent(
                    type="input_audio_buffer.append",
                    audio=self._pending_committed_audio_b64,
                )
            )
            self.send_event(oai_rt.InputAudioBufferCommitEvent(type="input_audio_buffer.commit"))

        self.send_event(deepcopy(self._pending_response_event))
        oai_rt.logger.info(
            "Replayed pending GLM turn after reconnect",
            extra={"has_audio": bool(self._pending_committed_audio_b64)},
        )

    def update_options(
        self,
        *,
        tool_choice: oai_rt.NotGivenOr[oai_rt.llm.ToolChoice | None] = oai_rt.NOT_GIVEN,
        voice: oai_rt.NotGivenOr[str] = oai_rt.NOT_GIVEN,
        temperature: oai_rt.NotGivenOr[float] = oai_rt.NOT_GIVEN,
        turn_detection: oai_rt.NotGivenOr[oai_rt.TurnDetection | None] = oai_rt.NOT_GIVEN,
        max_response_output_tokens: oai_rt.NotGivenOr[int | str | None] = oai_rt.NOT_GIVEN,
        input_audio_transcription: oai_rt.NotGivenOr[oai_rt.InputAudioTranscription | None] = oai_rt.NOT_GIVEN,
        input_audio_noise_reduction: oai_rt.NotGivenOr[oai_rt.InputAudioNoiseReduction | None] = oai_rt.NOT_GIVEN,
        speed: oai_rt.NotGivenOr[float] = oai_rt.NOT_GIVEN,
        tracing: oai_rt.NotGivenOr[oai_rt.Tracing | None] = oai_rt.NOT_GIVEN,
    ) -> None:
        del tool_choice
        del input_audio_transcription

        updated = False

        if oai_rt.is_given(voice):
            self._realtime_model._opts.voice = voice
            updated = True
        if oai_rt.is_given(temperature):
            self._realtime_model._opts.temperature = temperature
            updated = True
        if oai_rt.is_given(turn_detection):
            self._realtime_model._opts.turn_detection = turn_detection
            updated = True
        if oai_rt.is_given(max_response_output_tokens):
            self._realtime_model._opts.max_response_output_tokens = max_response_output_tokens  # type: ignore
            updated = True
        if oai_rt.is_given(input_audio_noise_reduction):
            self._realtime_model._opts.input_audio_noise_reduction = input_audio_noise_reduction
            updated = True
        if oai_rt.is_given(speed):
            self._realtime_model._opts.speed = speed
            updated = True
        if oai_rt.is_given(tracing):
            self._realtime_model._opts.tracing = cast(oai_rt.Tracing | None, tracing)
            updated = True

        if updated:
            self.send_event(self._glm_event(self._session_payload(), "options_update_"))
