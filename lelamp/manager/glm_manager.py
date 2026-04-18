"""First-pass synchronous manager implementation."""

from __future__ import annotations

import time
from typing import Any

from lelamp.runtime_config import RuntimeSettings


class GLMManager:
    def __init__(self, *, settings: RuntimeSettings) -> None:
        self._settings = settings

    @property
    def settings(self) -> RuntimeSettings:
        return self._settings

    def process(
        self,
        *,
        items: list[dict[str, Any]],
        previous_snapshot: dict[str, Any] | None,
    ) -> dict[str, Any]:
        del previous_snapshot
        last_user_turn = next(
            (item for item in reversed(items) if item.get("kind") == "conversation.user_turn"),
            None,
        )
        text = str((((last_user_turn or {}).get("payload") or {}).get("text", "")) or "").strip()
        profile_summary = f"Recent user theme: {text}".strip()
        return {
            "profile_summary": profile_summary,
            "preference_hints": [],
            "scene_priors": {},
            "banned_patterns": [],
            "updated_at_ms": int(time.time() * 1000),
        }
