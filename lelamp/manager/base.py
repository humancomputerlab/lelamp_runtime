"""Shared manager-side value objects."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ManagerSnapshot:
    profile_summary: str
    preference_hints: list[str]
    scene_priors: dict[str, list[str]]
    banned_patterns: list[str]
    updated_at_ms: int
