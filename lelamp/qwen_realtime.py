from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any, cast

from livekit.plugins.openai.realtime import realtime_model as oai_rt

from lelamp.reply_sanitizer import sanitize_spoken_reply
from lelamp.runtime_config import RuntimeSettings
from lelamp.voice_telemetry import (
    configure_voice_telemetry,
    default_voice_telemetry,
    get_voice_telemetry,
)


_QWEN_INPUT_AUDIO_FORMAT = "pcm"
_QWEN_OUTPUT_AUDIO_FORMAT = "pcm"
_QWEN_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/api-ws/v1/realtime"
_QWEN_DEFAULT_MODEL = "qwen3.5-omni-flash-realtime"
_QWEN_DEFAULT_TRANSCRIPTION_MODEL = "qwen3-asr-flash-realtime"
_QWEN_GENERATE_REPLY_TIMEOUT_S = 12.0
_QWEN_CAPACITY_RETRY_DELAY_S = 5.0
_QWEN_INTERRUPTED_REPLY_MESSAGE = "generate_reply interrupted before response was created."
_QWEN_CAPACITY_REASON_FRAGMENTS = (
    "too many requests",
    "throttled",
    "capacity limits",
)


def build_qwen_turn_detection() -> oai_rt.TurnDetection:
    return oai_rt.TurnDetection(
        type="server_vad",
        threshold=0.5,
        prefix_padding_ms=300,
        silence_duration_ms=500,
        create_response=True,
        interrupt_response=True,
    )


def build_qwen_input_audio_transcription() -> oai_rt.InputAudioTranscription:
    return oai_rt.InputAudioTranscription(model=_QWEN_DEFAULT_TRANSCRIPTION_MODEL)


def build_qwen_session_payload(
    settings: RuntimeSettings,
    *,
    voice: str,
    modalities: list[str],
    instructions: str | None = None,
    tools: list[object] | None = None,
    input_audio_transcription: object | None = None,
    input_audio_noise_reduction: object | None = None,
    turn_detection: object = oai_rt.NOT_GIVEN,
    tool_choice: object | None = None,
    speed: float | None = None,
    tracing: object | None = None,
    max_response_output_tokens: int | str | None = None,
) -> dict[str, object]:
    del settings

    payload: dict[str, object] = {
        "voice": voice,
        "input_audio_format": _QWEN_INPUT_AUDIO_FORMAT,
        "output_audio_format": _QWEN_OUTPUT_AUDIO_FORMAT,
        "modalities": modalities,
    }

    if instructions:
        payload["instructions"] = instructions
    if tools is not None:
        payload["tools"] = tools
    if input_audio_transcription is not None:
        payload["input_audio_transcription"] = input_audio_transcription
    if input_audio_noise_reduction is not None:
        payload["input_audio_noise_reduction"] = input_audio_noise_reduction
    if turn_detection is not oai_rt.NOT_GIVEN:
        payload["turn_detection"] = turn_detection
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    if speed is not None:
        payload["speed"] = speed
    if tracing is not None:
        payload["tracing"] = tracing
    if max_response_output_tokens is not None:
        payload["max_response_output_tokens"] = max_response_output_tokens

    return payload


def normalize_qwen_tool_schema(tool_desc: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(tool_desc)
    parameters = normalized.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {"type": "object", "properties": {}, "required": []}

    properties = parameters.get("properties")
    if not isinstance(properties, dict):
        parameters["properties"] = {}

    required = parameters.get("required")
    if not isinstance(required, list):
        parameters["required"] = []

    normalized["parameters"] = parameters
    return normalized


class QwenRealtimeModel(oai_rt.RealtimeModel):
    def __init__(self, *, settings: RuntimeSettings) -> None:
        self._qwen_settings = settings
        initial_voice_state = default_voice_telemetry()
        initial_voice_state.update(
            status="ready",
            local_state="idle",
            last_result="qwen realtime initialized",
        )
        configure_voice_telemetry(settings.voice_state_path).update(
            **initial_voice_state,
            force=True,
        )
        super().__init__(
            model=settings.model_name or _QWEN_DEFAULT_MODEL,
            voice=settings.model_voice,
            modalities=["text", "audio"],
            temperature=settings.model_temperature,
            input_audio_transcription=build_qwen_input_audio_transcription(),
            turn_detection=build_qwen_turn_detection() if settings.qwen_use_server_vad else None,
            tool_choice="auto",
            api_key=settings.model_api_key,
            base_url=settings.model_base_url or _QWEN_DEFAULT_BASE_URL,
        )
        # Qwen can emit audio in the same response that carries a function call.
        # Let the provider own the follow-up instead of forcing LiveKit to schedule
        # an extra reply task after every tool execution.
        self._capabilities.auto_tool_reply_generation = True

    def session(self) -> "QwenRealtimeSession":
        sess = QwenRealtimeSession(self)
        self._sessions.add(sess)
        return sess


class QwenRealtimeSession(oai_rt.RealtimeSession):
    _realtime_model: QwenRealtimeModel

    def __init__(self, realtime_model: QwenRealtimeModel) -> None:
        super().__init__(realtime_model)
        self._current_response_id: str | None = None
        self._active_response_ids: set[str] = set()
        self._suppressed_client_event_ids: set[str] = set()
        self._ignored_response_ids: set[str] = set()
        self._synthetic_message_item_ids: dict[str, str] = {}
        self._non_message_item_ids: set[str] = set()

    def _suppress_pending_manual_response(self) -> str | None:
        if self._realtime_model._opts.turn_detection is not None:
            return None
        if not self._response_created_futures:
            return None
        client_event_id = next(iter(self._response_created_futures))
        self._suppressed_client_event_ids.add(client_event_id)
        return client_event_id

    def _qwen_event(
        self,
        session_payload: dict[str, object],
        event_prefix: str,
    ) -> oai_rt.SessionUpdateEvent:
        return oai_rt.SessionUpdateEvent(
            type="session.update",
            session=oai_rt.session_update_event.Session.model_construct(**session_payload),
            event_id=oai_rt.utils.shortuuid(event_prefix),
        )

    def _current_qwen_tools(
        self, tools: list[oai_rt.llm.FunctionTool | oai_rt.llm.RawFunctionTool] | None = None
    ) -> tuple[
        list[oai_rt.session_update_event.SessionTool],
        list[oai_rt.llm.FunctionTool | oai_rt.llm.RawFunctionTool],
    ]:
        source_tools = tools
        if source_tools is None:
            source_tools = list(self._tools.function_tools.values())

        qwen_tools: list[oai_rt.session_update_event.SessionTool] = []
        retained_tools: list[oai_rt.llm.FunctionTool | oai_rt.llm.RawFunctionTool] = []

        for tool in source_tools:
            if oai_rt.is_function_tool(tool):
                tool_desc = oai_rt.llm.utils.build_legacy_openai_schema(tool, internally_tagged=True)
            elif oai_rt.is_raw_function_tool(tool):
                tool_info = oai_rt.get_raw_function_info(tool)
                tool_desc = deepcopy(tool_info.raw_schema)
                tool_desc["type"] = "function"
            else:
                oai_rt.logger.error("Qwen Realtime doesn't support this tool type", extra={"tool": tool})
                continue

            try:
                session_tool = oai_rt.session_update_event.SessionTool.model_validate(
                    normalize_qwen_tool_schema(tool_desc)
                )
                qwen_tools.append(session_tool)
                retained_tools.append(tool)
            except oai_rt.ValidationError:
                oai_rt.logger.error("Qwen Realtime rejected tool schema", extra={"tool": tool_desc})

        return qwen_tools, retained_tools

    def _session_payload(
        self,
        *,
        instructions: str | None | object = oai_rt.NOT_GIVEN,
        tools: list[oai_rt.session_update_event.SessionTool] | None | object = oai_rt.NOT_GIVEN,
    ) -> dict[str, object]:
        current_tools = None
        if tools is oai_rt.NOT_GIVEN:
            built_tools, _ = self._current_qwen_tools()
            current_tools = built_tools or None
        else:
            current_tools = cast(list[oai_rt.session_update_event.SessionTool] | None, tools)

        current_instructions = self._instructions if instructions is oai_rt.NOT_GIVEN else cast(str | None, instructions)
        opts = self._realtime_model._opts

        input_audio_transcription_opts = opts.input_audio_transcription
        input_audio_transcription = (
            oai_rt.session_update_event.SessionInputAudioTranscription.model_validate(
                input_audio_transcription_opts.model_dump(
                    by_alias=True,
                    exclude_unset=True,
                    exclude_defaults=True,
                )
            )
            if input_audio_transcription_opts
            else None
        )

        turn_detection_opts = opts.turn_detection
        turn_detection = (
            oai_rt.session_update_event.SessionTurnDetection.model_validate(
                turn_detection_opts.model_dump(
                    by_alias=True,
                    exclude_unset=True,
                    exclude_defaults=True,
                )
            )
            if turn_detection_opts
            else None
        )

        tracing_opts = opts.tracing
        if isinstance(tracing_opts, oai_rt.TracingTracingConfiguration):
            tracing: oai_rt.session_update_event.SessionTracing | None = (
                oai_rt.session_update_event.SessionTracingTracingConfiguration.model_validate(
                    tracing_opts.model_dump(
                        by_alias=True,
                        exclude_unset=True,
                        exclude_defaults=True,
                    )
                )
            )
        else:
            tracing = tracing_opts

        tool_choice = oai_rt._to_oai_tool_choice(opts.tool_choice)

        return build_qwen_session_payload(
            self._realtime_model._qwen_settings,
            voice=opts.voice,
            modalities=cast(list[str], opts.modalities),
            instructions=current_instructions,
            tools=current_tools,
            input_audio_transcription=input_audio_transcription,
            input_audio_noise_reduction=opts.input_audio_noise_reduction,
            turn_detection=turn_detection,
            tool_choice=tool_choice,
            speed=opts.speed,
            tracing=tracing,
            max_response_output_tokens=opts.max_response_output_tokens,
        )

    async def _create_ws_conn(self) -> oai_rt.aiohttp.ClientWebSocketResponse:
        headers = {
            "User-Agent": "LiveKit Agents",
            "Authorization": f"Bearer {self._realtime_model._opts.api_key}",
        }
        url = oai_rt.process_base_url(
            self._realtime_model._opts.base_url,
            self._realtime_model._opts.model,
        )
        return await oai_rt.asyncio.wait_for(
            self._realtime_model._ensure_http_session().ws_connect(url=url, headers=headers),
            self._realtime_model._opts.conn_options.timeout,
        )

    def _create_session_update_event(self) -> oai_rt.SessionUpdateEvent:
        return self._qwen_event(self._session_payload(), "session_update_")

    def _create_tools_update_event(
        self, tools: list[oai_rt.llm.FunctionTool | oai_rt.llm.RawFunctionTool]
    ) -> oai_rt.SessionUpdateEvent:
        session_tools, _ = self._current_qwen_tools(tools)
        return self._qwen_event(self._session_payload(tools=session_tools), "tools_update_")

    async def update_tools(self, tools: list[oai_rt.llm.FunctionTool | oai_rt.llm.RawFunctionTool]) -> None:
        async with self._update_fnc_ctx_lock:
            session_tools, retained_tools = self._current_qwen_tools(tools)
            self.send_event(self._qwen_event(self._session_payload(tools=session_tools), "tools_update_"))
            self._tools = oai_rt.llm.ToolContext(retained_tools)

    async def update_instructions(self, instructions: str) -> None:
        self._instructions = instructions
        self.send_event(
            self._qwen_event(
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
        self.send_event(
            oai_rt.ResponseCreateEvent(
                type="response.create",
                event_id=event_id,
                response=oai_rt.Response(
                    instructions=instructions or None,
                    metadata={"client_event_id": event_id},
                ),
            )
        )

        def _on_timeout() -> None:
            if fut and not fut.done():
                fut.set_exception(oai_rt.llm.RealtimeError("generate_reply timed out."))

        handle = asyncio.get_event_loop().call_later(_QWEN_GENERATE_REPLY_TIMEOUT_S, _on_timeout)
        fut.add_done_callback(lambda _: handle.cancel())
        return fut

    def _fail_pending_response_created_futures(self, message: str) -> None:
        pending_items = list(self._response_created_futures.items())
        self._response_created_futures.clear()
        for _, fut in pending_items:
            if not fut.done():
                fut.set_exception(oai_rt.llm.RealtimeError(message))

    def interrupt(self) -> None:
        super().interrupt()
        self._fail_pending_response_created_futures(_QWEN_INTERRUPTED_REPLY_MESSAGE)

    @staticmethod
    def _is_capacity_limited_close(close_code: object, close_reason: object) -> bool:
        reason = str(close_reason or "").strip().lower()
        return int(close_code or 0) == 1011 and any(
            fragment in reason for fragment in _QWEN_CAPACITY_REASON_FRAGMENTS
        )

    async def _run_ws(self, ws_conn: oai_rt.aiohttp.ClientWebSocketResponse) -> None:
        closing = False

        @oai_rt.utils.log_exceptions(logger=oai_rt.logger)
        async def _send_task() -> None:
            nonlocal closing
            async for msg in self._msg_ch:
                try:
                    if isinstance(msg, oai_rt.BaseModel):
                        msg = msg.model_dump(
                            by_alias=True, exclude_unset=True, exclude_defaults=False
                        )

                    self.emit("openai_client_event_queued", msg)
                    await ws_conn.send_str(oai_rt.json.dumps(msg))

                    if oai_rt.lk_oai_debug:
                        msg_copy = msg.copy()
                        if msg_copy["type"] == "input_audio_buffer.append":
                            msg_copy = {**msg_copy, "audio": "..."}

                        oai_rt.logger.debug(f">>> {msg_copy}")
                except Exception:
                    break

            closing = True
            await ws_conn.close()

        @oai_rt.utils.log_exceptions(logger=oai_rt.logger)
        async def _recv_task() -> None:
            while True:
                msg = await ws_conn.receive()
                if msg.type in (
                    oai_rt.aiohttp.WSMsgType.CLOSED,
                    oai_rt.aiohttp.WSMsgType.CLOSE,
                    oai_rt.aiohttp.WSMsgType.CLOSING,
                ):
                    if closing:
                        return

                    if self._is_capacity_limited_close(msg.data, msg.extra):
                        reason = str(msg.extra or "").strip()
                        get_voice_telemetry().update(
                            status="error",
                            local_state="idle",
                            last_result=f"Qwen capacity limited: {reason or 'retry later'}",
                            force=True,
                        )
                        oai_rt.logger.warning(
                            "Qwen realtime capacity limited; backing off before reconnect",
                            extra={
                                "close_code": msg.data,
                                "close_reason": reason,
                                "retry_delay_s": _QWEN_CAPACITY_RETRY_DELAY_S,
                            },
                        )
                        await asyncio.sleep(_QWEN_CAPACITY_RETRY_DELAY_S)
                        raise oai_rt.APIConnectionError(
                            message=f"Qwen capacity limited: {reason or msg.data}"
                        )

                    raise oai_rt.APIConnectionError(
                        message="OpenAI S2S connection closed unexpectedly"
                    )

                if msg.type != oai_rt.aiohttp.WSMsgType.TEXT:
                    continue

                event = oai_rt.json.loads(msg.data)
                self.emit("openai_server_event_received", event)

                try:
                    if oai_rt.lk_oai_debug:
                        event_copy = event.copy()
                        if event_copy["type"] == "response.audio.delta":
                            event_copy = {**event_copy, "delta": "..."}

                        oai_rt.logger.debug(f"<<< {event_copy}")

                    if event["type"] == "input_audio_buffer.speech_started":
                        self._handle_input_audio_buffer_speech_started(
                            oai_rt.InputAudioBufferSpeechStartedEvent.construct(**event)
                        )
                    elif event["type"] == "input_audio_buffer.speech_stopped":
                        self._handle_input_audio_buffer_speech_stopped(
                            oai_rt.InputAudioBufferSpeechStoppedEvent.construct(**event)
                        )
                    elif event["type"] == "response.created":
                        self._handle_response_created(oai_rt.ResponseCreatedEvent.construct(**event))
                    elif event["type"] == "response.output_item.added":
                        self._handle_response_output_item_added(
                            oai_rt.ResponseOutputItemAddedEvent.construct(**event)
                        )
                    elif event["type"] == "response.content_part.added":
                        self._handle_response_content_part_added(
                            oai_rt.ResponseContentPartAddedEvent.construct(**event)
                        )
                    elif event["type"] == "conversation.item.created":
                        self._handle_conversion_item_created(
                            oai_rt.ConversationItemCreatedEvent.construct(**event)
                        )
                    elif event["type"] == "conversation.item.deleted":
                        self._handle_conversion_item_deleted(
                            oai_rt.ConversationItemDeletedEvent.construct(**event)
                        )
                    elif event["type"] == "conversation.item.input_audio_transcription.completed":
                        self._handle_conversion_item_input_audio_transcription_completed(
                            oai_rt.ConversationItemInputAudioTranscriptionCompletedEvent.construct(**event)
                        )
                    elif event["type"] == "conversation.item.input_audio_transcription.failed":
                        self._handle_conversion_item_input_audio_transcription_failed(
                            oai_rt.ConversationItemInputAudioTranscriptionFailedEvent.construct(**event)
                        )
                    elif event["type"] == "response.text.delta":
                        self._handle_response_text_delta(oai_rt.ResponseTextDeltaEvent.construct(**event))
                    elif event["type"] == "response.text.done":
                        self._handle_response_text_done(oai_rt.ResponseTextDoneEvent.construct(**event))
                    elif event["type"] == "response.audio_transcript.delta":
                        self._handle_response_audio_transcript_delta(event)
                    elif event["type"] == "response.audio.delta":
                        self._handle_response_audio_delta(
                            oai_rt.ResponseAudioDeltaEvent.construct(**event)
                        )
                    elif event["type"] == "response.audio_transcript.done":
                        self._handle_response_audio_transcript_done(
                            oai_rt.ResponseAudioTranscriptDoneEvent.construct(**event)
                        )
                    elif event["type"] == "response.audio.done":
                        self._handle_response_audio_done(
                            oai_rt.ResponseAudioDoneEvent.construct(**event)
                        )
                    elif event["type"] == "response.output_item.done":
                        self._handle_response_output_item_done(
                            oai_rt.ResponseOutputItemDoneEvent.construct(**event)
                        )
                    elif event["type"] == "response.done":
                        self._handle_response_done(oai_rt.ResponseDoneEvent.construct(**event))
                    elif event["type"] == "error":
                        self._handle_error(oai_rt.ErrorEvent.construct(**event))
                except Exception:
                    if event["type"] == "response.audio.delta":
                        event["delta"] = event["delta"][:10] + "..."
                    oai_rt.logger.exception("failed to handle event", extra={"event": event})

        tasks = [
            asyncio.create_task(_recv_task(), name="_recv_task"),
            asyncio.create_task(_send_task(), name="_send_task"),
        ]
        wait_reconnect_task: asyncio.Task | None = None
        if self._realtime_model._opts.max_session_duration is not None:
            wait_reconnect_task = asyncio.create_task(
                asyncio.sleep(self._realtime_model._opts.max_session_duration),
                name="_timeout_task",
            )
            tasks.append(wait_reconnect_task)
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

            for task in done:
                if task != wait_reconnect_task:
                    task.result()

            if wait_reconnect_task and wait_reconnect_task in done and self._current_generation:
                await self._current_generation._done_fut

        finally:
            await oai_rt.utils.aio.cancel_and_wait(*tasks)
            await ws_conn.close()

    def _handle_response_created(self, event: oai_rt.ResponseCreatedEvent) -> None:
        response_id = self._normalize_response_id(event.response.id)
        assert response_id is not None, "response.id is None"

        client_event_id: str | None = None
        metadata = getattr(event.response, "metadata", None)
        if isinstance(metadata, dict):
            client_event_id = metadata.get("client_event_id")

        is_linked_followup = (
            client_event_id is None
            and not self._response_created_futures
            and self._realtime_model._opts.turn_detection is None
            and self._current_generation is not None
            and bool(self._active_response_ids)
        )

        if client_event_id is None and not self._response_created_futures and not is_linked_followup:
            oai_rt.logger.debug(
                "Ignoring unmatched Qwen response.created without pending future",
                extra={"response_id": response_id},
            )
            return

        if client_event_id and client_event_id in self._suppressed_client_event_ids:
            self._suppressed_client_event_ids.discard(client_event_id)
            self._ignored_response_ids.add(response_id)
            fut = self._response_created_futures.pop(client_event_id, None)
            if fut is not None and not fut.done():
                fut.set_exception(oai_rt.llm.RealtimeError("ignored empty input transcription turn"))
            get_voice_telemetry().update(
                status="ready",
                local_state="idle",
                last_response_id=response_id,
                last_result="empty input ignored",
                force=True,
            )
            oai_rt.logger.debug(
                "Ignoring Qwen response created for suppressed empty input turn",
                extra={"response_id": response_id, "client_event_id": client_event_id},
            )
            return

        if is_linked_followup:
            self._active_response_ids.add(response_id)
            get_voice_telemetry().update(
                status="running",
                local_state="replying",
                last_response_id=response_id,
                last_result="assistant follow-up response created",
                force=True,
            )
            return

        self._current_response_id = response_id
        self._active_response_ids = {response_id}
        get_voice_telemetry().update(
            status="running",
            local_state="replying",
            last_response_id=response_id,
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

        if not client_event_id and self._response_created_futures:
            client_event_id = next(iter(self._response_created_futures))

        if client_event_id and (fut := self._response_created_futures.pop(client_event_id, None)):
            generation_ev.user_initiated = True
            if not fut.done():
                fut.set_result(generation_ev)

        self.emit("generation_created", generation_ev)

    @staticmethod
    def _normalize_response_id(response_id: str | None) -> str | None:
        if response_id is None:
            return None
        normalized = response_id.strip()
        return normalized or None

    def _should_ignore_response_event(self, response_id: str | None, event_type: str) -> bool:
        normalized_response_id = self._normalize_response_id(response_id)
        if normalized_response_id is None:
            oai_rt.logger.debug(
                "Ignoring Qwen response event without response_id",
                extra={"event_type": event_type, "response_id": response_id},
            )
            return True
        if normalized_response_id in self._ignored_response_ids:
            oai_rt.logger.debug(
                "Ignoring Qwen response event for suppressed empty input turn",
                extra={"event_type": event_type, "response_id": normalized_response_id},
            )
            return True
        if self._current_generation is None or not self._active_response_ids:
            oai_rt.logger.debug(
                "Ignoring Qwen response event without active generation",
                extra={"event_type": event_type, "response_id": normalized_response_id},
            )
            return True
        if normalized_response_id in self._active_response_ids:
            return False

        oai_rt.logger.debug(
            "Ignoring stale Qwen response event",
            extra={
                "event_type": event_type,
                "response_id": normalized_response_id,
                "current_response_id": self._current_response_id,
                "active_response_ids": sorted(self._active_response_ids),
            },
        )
        return True

    def _should_ignore_audio_item_event(
        self,
        *,
        response_id: str | None,
        item_id: str | None,
        event_type: str,
    ) -> bool:
        if self._should_ignore_response_event(response_id, event_type):
            return True
        if self._current_generation is None:
            return True
        if item_id in self._current_generation.messages:
            return False

        oai_rt.logger.debug(
            "Ignoring Qwen audio event for non-message item",
            extra={
                "event_type": event_type,
                "response_id": self._normalize_response_id(response_id),
                "item_id": item_id,
            },
        )
        return True

    def _synthetic_message_item_id(self, response_id: str | None) -> str | None:
        normalized_response_id = self._normalize_response_id(response_id)
        if normalized_response_id is None:
            return None

        synthetic_item_id = self._synthetic_message_item_ids.get(normalized_response_id)
        if synthetic_item_id is None:
            synthetic_item_id = f"qwen-message-{normalized_response_id}"
            self._synthetic_message_item_ids[normalized_response_id] = synthetic_item_id
        return synthetic_item_id

    def _message_item_id_for_message_event(
        self,
        *,
        response_id: str | None,
        item_id: str | None,
    ) -> str | None:
        if item_id:
            return item_id
        return self._synthetic_message_item_id(response_id)

    def _message_item_id_for_audio_event(
        self,
        *,
        response_id: str | None,
        item_id: str | None,
    ) -> str | None:
        if self._current_generation is None:
            return None
        if item_id is not None:
            if item_id in self._current_generation.messages:
                return item_id
            if item_id in self._non_message_item_ids:
                synthetic_item_id = self._synthetic_message_item_id(response_id)
                if synthetic_item_id is None:
                    return None
                if synthetic_item_id not in self._current_generation.messages:
                    self._ensure_message_generation(synthetic_item_id)
                return synthetic_item_id
            return None

        synthetic_item_id = self._synthetic_message_item_id(response_id)
        if synthetic_item_id is None:
            return None
        if synthetic_item_id not in self._current_generation.messages:
            self._ensure_message_generation(synthetic_item_id)
        return synthetic_item_id

    def _ensure_message_generation(self, item_id: str) -> None:
        assert self._current_generation is not None, "current_generation is None"

        if item_id in self._current_generation.messages:
            return

        item_generation = oai_rt._MessageGeneration(
            message_id=item_id,
            text_ch=oai_rt.utils.aio.Chan(),
            audio_ch=oai_rt.utils.aio.Chan(),
            modalities=asyncio.Future(),
        )
        if not self._realtime_model.capabilities.audio_output:
            item_generation.audio_ch.close()
            item_generation.modalities.set_result(["text"])

        self._current_generation.message_ch.send_nowait(
            oai_rt.llm.MessageGeneration(
                message_id=item_id,
                text_stream=item_generation.text_ch,
                audio_stream=item_generation.audio_ch,
                modalities=item_generation.modalities,
            )
        )
        self._current_generation.messages[item_id] = item_generation

    @staticmethod
    def _event_with_item_id(event: Any, item_id: str) -> Any:
        return event.model_copy(update={"item_id": item_id})

    @staticmethod
    def _event_with_nested_item_id(event: Any, item_id: str) -> Any:
        return event.model_copy(update={"item": event.item.model_copy(update={"id": item_id})})

    def _handle_conversion_item_created(self, event: oai_rt.ConversationItemCreatedEvent) -> None:
        if getattr(event.item, "id", None) is None:
            oai_rt.logger.debug(
                "Ignoring Qwen conversation item without item_id",
                extra={"event_type": "conversation.item.created"},
            )
            return
        super()._handle_conversion_item_created(event)

    def _handle_response_output_item_added(self, event: oai_rt.ResponseOutputItemAddedEvent) -> None:
        if self._should_ignore_response_event(event.response_id, "response.output_item.added"):
            return
        item_type = getattr(event.item, "type", None)
        item_id = getattr(event.item, "id", None)
        if item_type == "message":
            patched_item_id = self._message_item_id_for_message_event(
                response_id=event.response_id,
                item_id=item_id,
            )
            if patched_item_id is None:
                return
            if patched_item_id != item_id:
                event = self._event_with_nested_item_id(event, patched_item_id)
        elif item_id:
            self._non_message_item_ids.add(item_id)
        else:
            oai_rt.logger.debug(
                "Ignoring Qwen non-message output item without item_id",
                extra={
                    "event_type": "response.output_item.added",
                    "response_id": self._normalize_response_id(event.response_id),
                    "item_type": item_type,
                },
            )
            return
        super()._handle_response_output_item_added(event)

    def _handle_response_content_part_added(self, event: oai_rt.ResponseContentPartAddedEvent) -> None:
        if self._should_ignore_response_event(event.response_id, "response.content_part.added"):
            return
        patched_item_id = self._message_item_id_for_audio_event(
            response_id=event.response_id,
            item_id=event.item_id,
        )
        if patched_item_id is None:
            oai_rt.logger.debug(
                "Ignoring Qwen content part event without message item",
                extra={
                    "event_type": "response.content_part.added",
                    "response_id": self._normalize_response_id(event.response_id),
                    "item_id": event.item_id,
                },
            )
            return
        if patched_item_id != event.item_id:
            event = self._event_with_item_id(event, patched_item_id)
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

    def _handle_response_audio_transcript_done(self, event: oai_rt.ResponseAudioTranscriptDoneEvent) -> None:
        if self._should_ignore_response_event(event.response_id, "response.audio_transcript.done"):
            return
        patched_item_id = self._message_item_id_for_audio_event(
            response_id=event.response_id,
            item_id=event.item_id,
        )
        if patched_item_id is None:
            oai_rt.logger.debug(
                "Ignoring Qwen audio transcript event without message item",
                extra={
                    "event_type": "response.audio_transcript.done",
                    "response_id": self._normalize_response_id(event.response_id),
                    "item_id": event.item_id,
                },
            )
            return
        if patched_item_id != event.item_id:
            event = self._event_with_item_id(event, patched_item_id)
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

    def _handle_response_audio_transcript_delta(self, event: dict[str, Any]) -> None:
        response_id = event.get("response_id")
        if self._should_ignore_response_event(response_id, "response.audio_transcript.delta"):
            return
        patched_item_id = self._message_item_id_for_audio_event(
            response_id=response_id,
            item_id=event.get("item_id"),
        )
        if patched_item_id is None:
            oai_rt.logger.debug(
                "Ignoring Qwen audio transcript delta without message item",
                extra={
                    "event_type": "response.audio_transcript.delta",
                    "response_id": self._normalize_response_id(response_id),
                    "item_id": event.get("item_id"),
                },
            )
            return
        if patched_item_id != event.get("item_id"):
            event = dict(event)
            event["item_id"] = patched_item_id
        super()._handle_response_audio_transcript_delta(event)

    def _handle_response_audio_delta(self, event: oai_rt.ResponseAudioDeltaEvent) -> None:
        if self._should_ignore_response_event(event.response_id, "response.audio.delta"):
            return
        patched_item_id = self._message_item_id_for_audio_event(
            response_id=event.response_id,
            item_id=event.item_id,
        )
        if patched_item_id is None:
            oai_rt.logger.debug(
                "Ignoring Qwen audio event for non-message item",
                extra={
                    "event_type": "response.audio.delta",
                    "response_id": self._normalize_response_id(event.response_id),
                    "item_id": event.item_id,
                },
            )
            return
        if patched_item_id != event.item_id:
            event = self._event_with_item_id(event, patched_item_id)
        super()._handle_response_audio_delta(event)

    def _handle_response_audio_done(self, event: oai_rt.ResponseAudioDoneEvent) -> None:
        if self._should_ignore_response_event(event.response_id, "response.audio.done"):
            return
        patched_item_id = self._message_item_id_for_audio_event(
            response_id=event.response_id,
            item_id=event.item_id,
        )
        if patched_item_id is None:
            oai_rt.logger.debug(
                "Ignoring Qwen audio done event for non-message item",
                extra={
                    "event_type": "response.audio.done",
                    "response_id": self._normalize_response_id(event.response_id),
                    "item_id": event.item_id,
                },
            )
            return
        if patched_item_id != event.item_id:
            event = self._event_with_item_id(event, patched_item_id)
        super()._handle_response_audio_done(event)

    def _handle_response_output_item_done(self, event: oai_rt.ResponseOutputItemDoneEvent) -> None:
        if self._should_ignore_response_event(event.response_id, "response.output_item.done"):
            return
        item_type = getattr(event.item, "type", None)
        item_id = getattr(event.item, "id", None)
        if item_type == "message":
            patched_item_id = self._message_item_id_for_message_event(
                response_id=event.response_id,
                item_id=item_id,
            )
            if patched_item_id is None:
                return
            if patched_item_id != item_id:
                event = self._event_with_nested_item_id(event, patched_item_id)
        elif item_id is None:
            oai_rt.logger.debug(
                "Ignoring Qwen non-message output item done without item_id",
                extra={
                    "event_type": "response.output_item.done",
                    "response_id": self._normalize_response_id(event.response_id),
                    "item_type": item_type,
                },
            )
            return
        super()._handle_response_output_item_done(event)

    def _handle_response_done(self, event: oai_rt.ResponseDoneEvent) -> None:
        response_id = self._normalize_response_id(event.response.id)
        if response_id is not None and response_id in self._ignored_response_ids:
            self._ignored_response_ids.discard(response_id)
            self._synthetic_message_item_ids.pop(response_id, None)
            return
        if self._should_ignore_response_event(response_id, "response.done"):
            return
        if response_id is not None:
            self._active_response_ids.discard(response_id)
            self._synthetic_message_item_ids.pop(response_id, None)
        if self._active_response_ids:
            return
        super()._handle_response_done(event)
        get_voice_telemetry().update(
            status="ready",
            local_state="idle",
            last_result="assistant response finished",
            force=True,
        )
        self._current_response_id = None
        self._non_message_item_ids.clear()

    def _handle_conversion_item_input_audio_transcription_completed(
        self,
        event: oai_rt.ConversationItemInputAudioTranscriptionCompletedEvent,
    ) -> None:
        super()._handle_conversion_item_input_audio_transcription_completed(event)
        transcript = (event.transcript or "").strip()
        if not transcript and self._suppress_pending_manual_response() is not None:
            get_voice_telemetry().update(
                status="ready",
                local_state="idle",
                last_asr_status="ok",
                last_asr_error_code=None,
                last_asr_text=event.transcript,
                last_result="empty input ignored",
                force=True,
            )
            return
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
