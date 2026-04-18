"""Projection helpers from runtime events into typed items."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from .schema import build_item


def _item_id(prefix: str) -> str:
    return f"itm_{prefix}_{uuid.uuid4().hex}"


def project_conversation_user_turn(*, session_id: str, text: str, ts_ms: int) -> dict[str, Any]:
    return build_item(
        kind="conversation.user_turn",
        producer="speaker_realtime",
        session_id=session_id,
        payload={"text": text},
        ts_ms=ts_ms,
        item_id=_item_id("user_turn"),
    )


def project_conversation_reply(*, session_id: str, text: str, ts_ms: int) -> dict[str, Any]:
    return build_item(
        kind="conversation.reply",
        producer="speaker_realtime",
        session_id=session_id,
        payload={"text": text},
        ts_ms=ts_ms,
        item_id=_item_id("reply"),
    )


def project_tool_invoke(
    *,
    session_id: str,
    tool_name: str,
    args: Mapping[str, Any],
    caller: str,
    invoke_id: str,
    ts_ms: int,
) -> dict[str, Any]:
    return build_item(
        kind="conversation.tool_invoke",
        producer="speaker_realtime",
        session_id=session_id,
        payload={
            "tool_name": tool_name,
            "args": dict(args),
            "caller": caller,
            "invoke_id": invoke_id,
        },
        ts_ms=ts_ms,
        item_id=_item_id("tool_invoke"),
    )


def project_tool_result(
    *,
    session_id: str,
    tool_name: str,
    args: Mapping[str, Any],
    caller: str,
    invoke_id: str,
    duration_ms: int | None,
    ok: bool,
    error: str | None,
    ts_ms: int,
) -> dict[str, Any]:
    return build_item(
        kind="conversation.tool_result",
        producer="speaker_realtime",
        session_id=session_id,
        payload={
            "tool_name": tool_name,
            "args": dict(args),
            "caller": caller,
            "invoke_id": invoke_id,
            "duration_ms": duration_ms,
            "ok": ok,
            "error": error,
        },
        ts_ms=ts_ms,
        item_id=_item_id("tool_result"),
    )
