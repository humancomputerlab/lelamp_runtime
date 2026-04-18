"""Append-only JSONL store for typed items."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from .schema import validate_item


class ItemStore:
    def __init__(self, root: Path) -> None:
        self._path = Path(root)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def append(self, item: Mapping[str, Any]) -> None:
        validate_item(item)
        line = json.dumps(dict(item), ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())

    def iter_items(self) -> Iterator[dict[str, Any]]:
        if not self._path.exists():
            return
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                item = json.loads(line)
                validate_item(item)
                yield item

    def iter_session_items(self, session_id: str) -> Iterator[dict[str, Any]]:
        for item in self.iter_items():
            if item.get("session_id") == session_id:
                yield item
