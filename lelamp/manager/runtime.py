"""Synchronous manager runtime loop."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from lelamp.items.projections import project_action_plan, project_scene_proposal
from lelamp.items.store import ItemStore
from lelamp.memory import ids as memory_ids
from lelamp.memory.derived import DerivedMemoryStore


class ManagerRuntime:
    SNAPSHOT_FILE = "manager_snapshot.v1.json"

    def __init__(self, *, manager, item_store_path: Path, derived_root: Path) -> None:
        self._manager = manager
        self._item_store = ItemStore(item_store_path)
        self._derived = DerivedMemoryStore(derived_root)

    def process_once(self, *, session_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        previous = self.load_snapshot()
        raw_output = dict(self._manager.process(items=items, previous_snapshot=previous))
        snapshot = _normalize_snapshot(raw_output)
        self._derived.write_snapshot(self.SNAPSHOT_FILE, snapshot)
        self._emit_scene_plan_items(
            session_id=session_id,
            items=items,
            raw_output=raw_output,
        )
        return snapshot

    def load_snapshot(self) -> dict[str, Any] | None:
        return self._derived.read_snapshot(self.SNAPSHOT_FILE)

    @property
    def snapshot_path(self) -> Path:
        return self._derived.root / self.SNAPSHOT_FILE

    def flush(self) -> dict[str, Any] | None:
        return self.load_snapshot()

    def _emit_scene_plan_items(
        self,
        *,
        session_id: str,
        items: list[dict[str, Any]],
        raw_output: dict[str, Any],
    ) -> None:
        proposal = raw_output.get("_scene_proposal")
        if not isinstance(proposal, dict):
            return

        scene = proposal.get("scene")
        if not isinstance(scene, dict):
            return

        summary = str(proposal.get("summary") or "").strip() or "Manager proposed a new scene."
        source_item_id = proposal.get("source_item_id")
        if not isinstance(source_item_id, str) or not source_item_id:
            latest_user_turn = _latest_user_turn(items)
            candidate = (latest_user_turn or {}).get("item_id")
            source_item_id = candidate if isinstance(candidate, str) and candidate else None

        fingerprint = _scene_fingerprint(scene)
        if self._has_existing_action_plan(
            session_id=session_id,
            source_item_id=source_item_id,
            fingerprint=fingerprint,
        ):
            return

        ts_ms = _proposal_ts_ms(proposal, items)
        scene_item = project_scene_proposal(
            session_id=session_id,
            summary=summary,
            scene=scene,
            source_item_id=source_item_id,
            fingerprint=fingerprint,
            ts_ms=ts_ms,
        )
        action_item = project_action_plan(
            session_id=session_id,
            summary=summary,
            scene=scene,
            source_item_id=source_item_id,
            scene_item_id=scene_item["item_id"],
            fingerprint=fingerprint,
            ts_ms=ts_ms,
        )
        self._item_store.append(scene_item)
        self._item_store.append(action_item)

    def _has_existing_action_plan(
        self,
        *,
        session_id: str,
        source_item_id: str | None,
        fingerprint: str,
    ) -> bool:
        for item in self._item_store.iter_session_items(session_id):
            if item.get("kind") != "action.plan":
                continue
            payload = item.get("payload") or {}
            if source_item_id and payload.get("source_item_id") == source_item_id:
                return True
            if payload.get("fingerprint") == fingerprint:
                return True
        return False


def _normalize_snapshot(raw_output: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile_summary": str(raw_output.get("profile_summary") or ""),
        "preference_hints": [str(value) for value in raw_output.get("preference_hints", [])],
        "scene_priors": {
            str(key): [str(value) for value in values]
            for key, values in dict(raw_output.get("scene_priors") or {}).items()
        },
        "banned_patterns": [str(value) for value in raw_output.get("banned_patterns", [])],
        "updated_at_ms": int(raw_output.get("updated_at_ms") or memory_ids.current_timestamp_ms()),
    }


def _latest_user_turn(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next(
        (item for item in reversed(items) if item.get("kind") == "conversation.user_turn"),
        None,
    )


def _proposal_ts_ms(proposal: dict[str, Any], items: list[dict[str, Any]]) -> int:
    try:
        explicit_ts_ms = proposal.get("ts_ms")
        if explicit_ts_ms is not None:
            return int(explicit_ts_ms)
    except (TypeError, ValueError):
        pass
    latest_ts_ms = (items[-1] if items else {}).get("ts_ms")
    if isinstance(latest_ts_ms, int):
        return latest_ts_ms
    return memory_ids.current_timestamp_ms()


def _scene_fingerprint(scene: dict[str, Any]) -> str:
    encoded = json.dumps(scene, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(encoded.encode("utf-8")).hexdigest()[:16]
