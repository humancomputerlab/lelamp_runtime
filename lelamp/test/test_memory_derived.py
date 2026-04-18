from __future__ import annotations

from dataclasses import asdict

from lelamp.manager.base import ManagerSnapshot
from lelamp.memory.derived import DerivedMemoryStore


def test_profile_snapshot_round_trip(tmp_path):
    store = DerivedMemoryStore(tmp_path / "memory" / "default")
    payload = {
        "profile_summary": "User likes short teasing replies.",
        "preference_hints": ["keep replies short"],
        "scene_priors": {"greeting": ["warm_gradient"]},
        "banned_patterns": ["repeat_same_scene"],
        "updated_at_ms": 1776500000000,
    }

    store.write_snapshot("manager_snapshot.v1.json", payload)

    assert store.read_snapshot("manager_snapshot.v1.json") == payload


def test_manager_snapshot_exposes_structured_fields():
    snapshot = ManagerSnapshot(
        profile_summary="User likes short teasing replies.",
        preference_hints=["keep replies short"],
        scene_priors={"greeting": ["warm_gradient"]},
        banned_patterns=["repeat_same_scene"],
        updated_at_ms=1776500000000,
    )

    assert asdict(snapshot) == {
        "profile_summary": "User likes short teasing replies.",
        "preference_hints": ["keep replies short"],
        "scene_priors": {"greeting": ["warm_gradient"]},
        "banned_patterns": ["repeat_same_scene"],
        "updated_at_ms": 1776500000000,
    }
