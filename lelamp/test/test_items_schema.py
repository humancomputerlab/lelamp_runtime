from __future__ import annotations

import pytest

from lelamp.items.schema import ITEM_SCHEMA, build_item, validate_item


def test_build_item_emits_envelope():
    item = build_item(
        kind="conversation.reply",
        producer="speaker_realtime",
        session_id="sess_2026-04-19_20-00-00",
        payload={"text": "灯灯在。"},
        ts_ms=1776500000000,
        item_id="itm_1",
    )

    assert item["schema"] == ITEM_SCHEMA
    assert item["kind"] == "conversation.reply"
    assert item["payload"]["text"] == "灯灯在。"
    validate_item(item)


def test_validate_item_rejects_unknown_kind():
    item = {
        "schema": ITEM_SCHEMA,
        "item_id": "itm_1",
        "ts_ms": 1776500000000,
        "session_id": "sess_2026-04-19_20-00-00",
        "kind": "unknown.kind",
        "producer": "speaker_realtime",
        "payload": {},
    }

    with pytest.raises(ValueError, match="unknown item kind"):
        validate_item(item)
