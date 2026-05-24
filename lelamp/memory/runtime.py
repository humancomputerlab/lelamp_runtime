"""Runtime-safe integration seam for H1 memory.

This module is the only place runtime hot paths should touch the memory
library directly.  It turns the file-backed writer/session APIs into a
small no-throw surface so voice-agent bootstrap can opt in without
risking Pi uptime.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from . import ids as _ids
from .selfcheck import run_selfcheck
from .session import SessionHandle, attach_or_create_session, start_agent_session
from .writer import MemoryWriter

_logger = logging.getLogger(__name__)

_DISABLE_ENV = "LELAMP_MEMORY_DISABLE"
_TRUE_VALUES = {"1", "true", "yes", "on"}


def _runtime_disabled() -> bool:
    value = os.environ.get(_DISABLE_ENV, "").strip().lower()
    return value in _TRUE_VALUES


@dataclass
class AgentMemoryRuntime:
    enabled: bool = False
    writer: Optional[MemoryWriter] = None
    session_handle: Optional[SessionHandle] = None
    _closed: bool = field(default=False, init=False, repr=False)
    _pending_user_text: Optional[str] = field(default=None, init=False, repr=False)
    _pending_user_ts_ms: Optional[int] = field(default=None, init=False, repr=False)
    _listeners_installed: bool = field(default=False, init=False, repr=False)
    _last_conversation_event_id: Optional[str] = field(default=None, init=False, repr=False)

    def set_motor_bus_enabled(self, enabled: Optional[bool]) -> None:
        if not self.enabled or self._closed or self.session_handle is None:
            return
        try:
            self.session_handle.set_motor_bus_enabled(enabled)
        except Exception:
            _logger.exception(
                "memory runtime: failed to patch motor_bus_enabled=%r",
                enabled,
            )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if not self.enabled or self.session_handle is None:
            return
        try:
            self.session_handle.close()
        except Exception:
            _logger.exception("memory runtime: failed to close session")

    def install_session_listeners(
        self,
        session: Any,
        *,
        model_provider: Optional[str] = None,
        model_name: Optional[str] = None,
    ) -> None:
        if (
            not self.enabled
            or self._closed
            or self.writer is None
            or self.session_handle is None
            or self._listeners_installed
            or not hasattr(session, "on")
        ):
            return

        def _on_user_input_transcribed(ev: Any) -> None:
            if not getattr(ev, "is_final", False):
                return
            transcript = str(getattr(ev, "transcript", "") or "").strip()
            if not transcript:
                return
            self._pending_user_text = transcript
            self._pending_user_ts_ms = _event_ts_ms(ev)

        def _on_conversation_item_added(ev: Any) -> None:
            item = getattr(ev, "item", None)
            if item is None or getattr(item, "role", None) != "assistant":
                return
            user_text = self._pending_user_text
            if not user_text:
                return
            assistant_text = _message_text(item)
            if not assistant_text:
                return

            assistant_ts_ms = _event_ts_ms(ev)
            user_ts_ms = self._pending_user_ts_ms
            duration_ms = None
            if assistant_ts_ms is not None and user_ts_ms is not None:
                duration_ms = max(0, assistant_ts_ms - user_ts_ms)

            record = self.writer.write_conversation(
                session_id=self.session_handle.session_id,
                source="voice_agent",
                user_text=user_text,
                assistant_text=assistant_text,
                user_text_lang=None,
                assistant_style=None,
                turn_duration_ms=duration_ms,
                model_provider=model_provider,
                model_name=model_name,
                ts_ms=assistant_ts_ms,
            )
            if isinstance(record, dict):
                event_id = record.get("event_id")
                if isinstance(event_id, str) and event_id:
                    self._last_conversation_event_id = event_id
            self._pending_user_text = None
            self._pending_user_ts_ms = None

        def _on_function_tools_executed(ev: Any) -> None:
            for call, output in getattr(ev, "zipped", lambda: [])():
                invoke_id = _ids.generate_invoke_id()
                args = _parse_tool_args(getattr(call, "arguments", ""))
                invoke_ts_ms = _object_ts_ms(call)
                result_ts_ms = _object_ts_ms(output)
                duration_ms = None
                if invoke_ts_ms is not None and result_ts_ms is not None:
                    duration_ms = max(0, result_ts_ms - invoke_ts_ms)
                ok = not bool(getattr(output, "is_error", False))
                error = None if ok else str(getattr(output, "output", "") or "")

                self.writer.write_function_tool(
                    session_id=self.session_handle.session_id,
                    source="voice_agent",
                    invoke_id=invoke_id,
                    phase="invoke",
                    tool_name=str(getattr(call, "name", "") or ""),
                    args=args,
                    caller="llm",
                    ts_ms=invoke_ts_ms,
                )
                self.writer.write_function_tool(
                    session_id=self.session_handle.session_id,
                    source="voice_agent",
                    invoke_id=invoke_id,
                    phase="result",
                    tool_name=str(getattr(call, "name", "") or ""),
                    args=args,
                    caller="llm",
                    duration_ms=duration_ms,
                    ok=ok,
                    error=error,
                    ts_ms=result_ts_ms,
                )

        session.on("user_input_transcribed", _guarded(_on_user_input_transcribed))
        session.on("conversation_item_added", _guarded(_on_conversation_item_added))
        session.on("function_tools_executed", _guarded(_on_function_tools_executed))
        self._listeners_installed = True

    def note_auto_expression_fallback(
        self,
        *,
        style: str,
        trigger: str,
        started_ts_ms: Optional[int],
        ended_ts_ms: Optional[int],
        ok: bool,
        error: Optional[str],
    ) -> None:
        if not self.enabled or self._closed or self.writer is None or self.session_handle is None:
            return
        try:
            invoke_id = _ids.generate_invoke_id()
            duration_ms = None
            if started_ts_ms is not None and ended_ts_ms is not None:
                duration_ms = max(0, ended_ts_ms - started_ts_ms)
            self.writer.write_fallback_expression(
                session_id=self.session_handle.session_id,
                source="voice_agent",
                style=style,
                trigger=trigger,
                linked_conversation_event_id=self._last_conversation_event_id,
                ts_ms=started_ts_ms,
            )
            self.writer.write_function_tool(
                session_id=self.session_handle.session_id,
                source="voice_agent",
                invoke_id=invoke_id,
                phase="invoke",
                tool_name="express",
                args={"style": style},
                caller="auto_expression",
                ts_ms=started_ts_ms,
            )
            self.writer.write_function_tool(
                session_id=self.session_handle.session_id,
                source="voice_agent",
                invoke_id=invoke_id,
                phase="result",
                tool_name="express",
                args={"style": style},
                caller="auto_expression",
                duration_ms=duration_ms,
                ok=ok,
                error=error,
                ts_ms=ended_ts_ms,
            )
        except Exception:
            _logger.exception(
                "memory runtime: failed to record auto-expression fallback",
            )


def _guarded(callback):
    def _wrapped(ev: Any) -> None:
        try:
            callback(ev)
        except Exception:
            _logger.exception("memory runtime: session listener failed")

    return _wrapped


def _event_ts_ms(ev: Any) -> Optional[int]:
    return _object_ts_ms(ev)


def _object_ts_ms(obj: Any) -> Optional[int]:
    created_at = getattr(obj, "created_at", None)
    if created_at is None:
        return None
    try:
        return int(float(created_at) * 1000)
    except (TypeError, ValueError):
        return None


def _message_text(item: Any) -> str:
    text = getattr(item, "text_content", None)
    if isinstance(text, str):
        return text.strip()
    content = getattr(item, "content", None)
    if isinstance(content, list):
        parts = [_content_part_text(part) for part in content]
        return " ".join(part for part in parts if part).strip()
    return ""


def _content_part_text(part: Any) -> str:
    if isinstance(part, str):
        return part.strip()
    if isinstance(part, dict):
        for key in ("text", "transcript"):
            value = part.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
    for attr in ("text_content", "text", "transcript"):
        value = getattr(part, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _parse_tool_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {"_raw": raw}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw}
    if isinstance(parsed, dict):
        return parsed
    return {"_raw": parsed}


def bootstrap_agent_runtime(settings, *, user_id: Optional[str] = None) -> AgentMemoryRuntime:
    if _runtime_disabled():
        return AgentMemoryRuntime(enabled=False)

    try:
        writer = MemoryWriter(user_id=user_id)
        run_selfcheck(writer)
        providers = []
        model_provider = getattr(settings, "model_provider", None)
        if isinstance(model_provider, str) and model_provider:
            providers.append(model_provider)
        session_handle = start_agent_session(
            writer,
            model_providers=providers,
        )
    except Exception:
        _logger.exception("memory runtime: bootstrap failed, degrading to no-op")
        return AgentMemoryRuntime(enabled=False)

    return AgentMemoryRuntime(
        enabled=True,
        writer=writer,
        session_handle=session_handle,
    )


def record_standalone_playback(
    *,
    source: str,
    initiator: str,
    action: str,
    recording_name: Optional[str] = None,
    rgb: Any = None,
    duration_ms: Optional[int] = None,
    ok: bool = True,
    error: Optional[str] = None,
    user_id: Optional[str] = None,
) -> None:
    if _runtime_disabled():
        return

    handle: Optional[SessionHandle] = None
    try:
        writer = MemoryWriter(user_id=user_id)
        run_selfcheck(writer)
        handle = attach_or_create_session(writer)
        writer.write_playback(
            session_id=handle.session_id,
            source=source,
            action=action,
            initiator=initiator,
            recording_name=recording_name,
            rgb=rgb,
            duration_ms=duration_ms,
            ok=ok,
            error=error,
        )
    except Exception:
        _logger.exception("memory runtime: failed to record standalone playback")
    finally:
        if handle is not None:
            try:
                handle.close()
            except Exception:
                _logger.exception("memory runtime: failed to close standalone session")
