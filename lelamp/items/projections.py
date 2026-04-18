"""Projection helpers from runtime events into typed items."""

from __future__ import annotations

from typing import Any

from .schema import build_item


def project_conversation_reply(*, session_id: str, text: str, ts_ms: int) -> dict[str, Any]:
    return build_item(
        kind="conversation.reply",
        producer="speaker_realtime",
        session_id=session_id,
        payload={"text": text},
        ts_ms=ts_ms,
        item_id=f"itm_reply_{ts_ms}",
    )
