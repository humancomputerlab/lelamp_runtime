"""Synchronous manager runtime loop."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lelamp.items.store import ItemStore
from lelamp.memory.derived import DerivedMemoryStore


class ManagerRuntime:
    SNAPSHOT_FILE = "manager_snapshot.v1.json"

    def __init__(self, *, manager, item_store_path: Path, derived_root: Path) -> None:
        self._manager = manager
        self._item_store = ItemStore(item_store_path)
        self._derived = DerivedMemoryStore(derived_root)

    def process_once(self, *, session_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        del session_id
        previous = self.load_snapshot()
        snapshot = self._manager.process(items=items, previous_snapshot=previous)
        self._derived.write_snapshot(self.SNAPSHOT_FILE, snapshot)
        return snapshot

    def load_snapshot(self) -> dict[str, Any] | None:
        return self._derived.read_snapshot(self.SNAPSHOT_FILE)
