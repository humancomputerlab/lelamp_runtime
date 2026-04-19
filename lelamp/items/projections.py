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


def project_scene_proposal(
    *,
    session_id: str,
    summary: str,
    scene: Mapping[str, Any],
    source_item_id: str | None,
    fingerprint: str,
    ts_ms: int,
) -> dict[str, Any]:
    return build_item(
        kind="scene.proposal",
        producer="manager_sidecar",
        session_id=session_id,
        payload={
            "summary": summary,
            "scene": _normalize_json_like(scene),
            "source_item_id": source_item_id,
            "fingerprint": fingerprint,
        },
        ts_ms=ts_ms,
        item_id=_item_id("scene_proposal"),
    )


def project_action_plan(
    *,
    session_id: str,
    summary: str,
    scene: Mapping[str, Any],
    source_item_id: str | None,
    scene_item_id: str,
    fingerprint: str,
    ts_ms: int,
) -> dict[str, Any]:
    return build_item(
        kind="action.plan",
        producer="manager_sidecar",
        session_id=session_id,
        payload={
            "summary": summary,
            "scene": _normalize_json_like(scene),
            "source_item_id": source_item_id,
            "scene_item_id": scene_item_id,
            "fingerprint": fingerprint,
        },
        ts_ms=ts_ms,
        item_id=_item_id("action_plan"),
    )


def project_body_state_snapshot(
    *,
    session_id: str,
    snapshot: Mapping[str, Any],
    ts_ms: int,
) -> dict[str, Any]:
    return build_item(
        kind="body.state_snapshot",
        producer="action_executor",
        session_id=session_id,
        payload=_normalize_json_like(snapshot),
        ts_ms=ts_ms,
        item_id=_item_id("body_state_snapshot"),
    )


def project_action_program(
    *,
    session_id: str,
    summary: str,
    program: Mapping[str, Any],
    source_item_id: str | None,
    fingerprint: str,
    ts_ms: int,
) -> dict[str, Any]:
    return build_item(
        kind="action.program",
        producer="manager_sidecar",
        session_id=session_id,
        payload={
            "summary": summary,
            "program": _normalize_json_like(program),
            "source_item_id": source_item_id,
            "fingerprint": fingerprint,
        },
        ts_ms=ts_ms,
        item_id=_item_id("action_program"),
    )


def project_action_critique(
    *,
    session_id: str,
    action_item_id: str,
    critique: Mapping[str, Any],
    ts_ms: int,
) -> dict[str, Any]:
    return build_item(
        kind="action.critique",
        producer="manager_sidecar",
        session_id=session_id,
        payload={
            "action_item_id": action_item_id,
            "critique": _normalize_json_like(critique),
        },
        ts_ms=ts_ms,
        item_id=_item_id("action_critique"),
    )


def project_action_compile_result(
    *,
    session_id: str,
    action_item_id: str,
    result: Mapping[str, Any],
    ts_ms: int,
) -> dict[str, Any]:
    return build_item(
        kind="action.compile_result",
        producer="action_executor",
        session_id=session_id,
        payload={
            "action_item_id": action_item_id,
            "result": _normalize_json_like(result),
        },
        ts_ms=ts_ms,
        item_id=_item_id("action_compile_result"),
    )


def project_execution_result(
    *,
    session_id: str,
    action_item_id: str,
    compiled: Mapping[str, Any],
    motion_event_count: int,
    light_event_count: int,
    skipped_motion: bool,
    skipped_light: bool,
    ts_ms: int,
) -> dict[str, Any]:
    return build_item(
        kind="execution.result",
        producer="action_executor",
        session_id=session_id,
        payload={
            "action_item_id": action_item_id,
            "compiled": _normalize_json_like(compiled),
            "motion_event_count": motion_event_count,
            "light_event_count": light_event_count,
            "skipped_motion": skipped_motion,
            "skipped_light": skipped_light,
        },
        ts_ms=ts_ms,
        item_id=_item_id("execution_result"),
    )


def project_execution_guardrail_reject(
    *,
    session_id: str,
    action_item_id: str,
    reason: str,
    scene: Mapping[str, Any] | None,
    ts_ms: int,
) -> dict[str, Any]:
    return build_item(
        kind="execution.guardrail_reject",
        producer="action_executor",
        session_id=session_id,
        payload={
            "action_item_id": action_item_id,
            "reason": reason,
            "scene": _normalize_json_like(scene or {}),
        },
        ts_ms=ts_ms,
        item_id=_item_id("execution_guardrail_reject"),
    )


def _normalize_json_like(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _normalize_json_like(inner) for key, inner in value.items()}
    if isinstance(value, tuple):
        return [_normalize_json_like(inner) for inner in value]
    if isinstance(value, list):
        return [_normalize_json_like(inner) for inner in value]
    return value
