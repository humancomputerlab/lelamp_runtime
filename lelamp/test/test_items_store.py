from __future__ import annotations

from lelamp.items.schema import build_item
from lelamp.items.store import ItemStore


def test_item_store_append_and_iter_round_trip(tmp_path):
    path = tmp_path / "items.jsonl"
    store = ItemStore(path)
    original = build_item(
        kind="conversation.reply",
        producer="speaker_realtime",
        session_id="sess_2026-04-19_20-00-00",
        payload={"text": "灯灯在。"},
        ts_ms=1776500000000,
        item_id="itm_1",
    )

    store.append(original)

    assert list(store.iter_items()) == [original]
