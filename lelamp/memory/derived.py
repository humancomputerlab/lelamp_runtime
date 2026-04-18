"""Atomic read/write helpers for derived memory artifacts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class DerivedMemoryStore:
    def __init__(self, user_dir: Path) -> None:
        self._root = Path(user_dir)
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def write_snapshot(self, filename: str, payload: Mapping[str, Any]) -> Path:
        path = self._root / filename
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(dict(payload), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)
        return path

    def read_snapshot(self, filename: str) -> dict[str, Any] | None:
        path = self._root / filename
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
