"""Schema helpers for persisted manager items."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lelamp.memory.ids import validate_session_id

ITEM_SCHEMA = "lelamp.item.v1"
ITEM_KINDS = frozenset(
    {
        "perception.asr_commit",
        "conversation.user_turn",
        "conversation.reply",
        "conversation.tool_invoke",
        "conversation.tool_result",
        "memory.profile_update",
        "memory.episode_summary",
        "scene.proposal",
        "action.plan",
        "body.state_snapshot",
        "action.program",
        "action.critique",
        "action.compile_result",
        "execution.result",
        "execution.guardrail_reject",
    }
)


def build_item(
    *,
    kind: str,
    producer: str,
    session_id: str,
    payload: Mapping[str, Any],
    ts_ms: int,
    item_id: str,
) -> dict[str, Any]:
    item = {
        "schema": ITEM_SCHEMA,
        "item_id": item_id,
        "ts_ms": ts_ms,
        "session_id": session_id,
        "kind": kind,
        "producer": producer,
        "payload": dict(payload),
    }
    validate_item(item)
    return item


def validate_item(item: Mapping[str, Any]) -> None:
    if not isinstance(item, Mapping):
        raise ValueError("item must be a mapping")
    if item.get("schema") != ITEM_SCHEMA:
        raise ValueError(f"unknown item schema: {item.get('schema')!r}")

    item_id = item.get("item_id")
    if not isinstance(item_id, str) or not item_id:
        raise ValueError("item_id must be a non-empty string")

    ts_ms = item.get("ts_ms")
    if not isinstance(ts_ms, int) or isinstance(ts_ms, bool):
        raise ValueError("ts_ms must be an integer")

    session_id = item.get("session_id")
    if not isinstance(session_id, str) or not validate_session_id(session_id):
        raise ValueError(f"invalid session_id={session_id!r}")

    kind = item.get("kind")
    if kind not in ITEM_KINDS:
        raise ValueError(f"unknown item kind: {kind!r}")

    producer = item.get("producer")
    if not isinstance(producer, str) or not producer:
        raise ValueError("producer must be a non-empty string")

    payload = item.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be a mapping")
