"""First-pass synchronous manager implementation."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lelamp.runtime_config import RuntimeSettings


class GLMManager:
    def __init__(self, *, settings: "RuntimeSettings") -> None:
        self._settings = settings

    @property
    def settings(self) -> "RuntimeSettings":
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
        action_program = _action_program_for_text(text)
        scene_proposal = _scene_proposal_for_text(text) if action_program is None else None
        scene_priors = {}
        if action_program is not None:
            scene_priors = {action_program["intent"]: list(action_program["priors"])}
        elif scene_proposal is not None:
            scene_priors = {scene_proposal["intent"]: list(scene_proposal["priors"])}

        result = {
            "profile_summary": profile_summary,
            "preference_hints": [],
            "scene_priors": scene_priors,
            "banned_patterns": ["repeat same scene twice in a row"],
            "updated_at_ms": int(time.time() * 1000),
        }
        if action_program is not None:
            result["_action_program"] = {
                "summary": action_program["summary"],
                "program": action_program["program"],
                "source_item_id": (last_user_turn or {}).get("item_id"),
            }
        if scene_proposal is not None:
            result["_scene_proposal"] = {
                "summary": scene_proposal["summary"],
                "scene": scene_proposal["scene"],
                "source_item_id": (last_user_turn or {}).get("item_id"),
            }
        return result


def _action_program_for_text(text: str) -> dict[str, Any] | None:
    lowered = text.lower()
    if _contains_any(lowered, text, "抬头", "仰头", "look up", "往上看", "向上看"):
        return {
            "intent": "proud_look_up",
            "priors": ["cool gradient", "upward sweep"],
            "summary": "User requested a confident upward attention shift.",
            "program": {
                "version": "v2",
                "intent": "proud_look_up",
                "why": "User asked the lamp to look up.",
                "expression": {
                    "attention": "up",
                    "attitude": "confident",
                    "emotion": "warm",
                    "novelty": 0.62,
                },
                "style": {
                    "exaggeration": 0.74,
                    "smoothness": 0.38,
                    "tension": 0.52,
                    "tempo": 1.0,
                    "symmetry_break": 0.18,
                },
                "settle_policy": {
                    "return_to_home_bias": 0.55,
                    "preserve_attention_heading": True,
                },
                "phases": [
                    {
                        "name": "prepare",
                        "duration_ms": 140,
                        "easing": "ease_out",
                        "joints": {
                            "base_pitch": {"target": -0.12, "role": "lead"},
                            "elbow_pitch": {"target": 0.06, "role": "support"},
                        },
                    },
                    {
                        "name": "accent",
                        "duration_ms": 220,
                        "easing": "ease_in_out",
                        "joints": {
                            "base_pitch": {"target": 0.70, "role": "lead"},
                            "wrist_pitch": {"target": 0.14, "role": "support"},
                            "wrist_roll": {"target": 0.06, "role": "accent"},
                        },
                    },
                ],
                "lighting": {
                    "mode": "gradient",
                    "palette": [[120, 180, 255], [255, 255, 255]],
                },
            },
        }
    return None


def _scene_proposal_for_text(text: str) -> dict[str, Any] | None:
    lowered = text.lower()
    if _contains_any(lowered, text, "跳舞", "跳个舞", "舞给我看", "dance", "摇摆", "摇一摇"):
        return {
            "intent": "playful",
            "priors": ["sparkle palette", "happy wiggle"],
            "summary": "User requested a playful dance response.",
            "scene": {
                "body": [{"type": "gesture", "name": "happy", "intensity": 0.8, "repeats": 2}],
                "light": [{"type": "sparkle", "palette": [[255, 120, 40], [255, 220, 90], [70, 255, 120]]}],
            },
        }
    if _contains_any(lowered, text, "仰头", "仰个头", "抬头", "往上看", "向上看", "look up"):
        return {
            "intent": "look_up",
            "priors": ["cool gradient", "upward sweep"],
            "summary": "User requested an upward look response.",
            "scene": {
                "body": [{"type": "look", "direction": "up"}, {"type": "settle", "style": "soft"}],
                "light": [{"type": "gradient", "palette": [[120, 180, 255], [255, 255, 255]]}],
            },
        }
    if _contains_any(lowered, text, "低头", "往下看", "向下看", "look down"):
        return {
            "intent": "look_down",
            "priors": ["soft amber", "downward tilt"],
            "summary": "User requested a downward look response.",
            "scene": {
                "body": [{"type": "look", "direction": "down"}, {"type": "settle", "style": "soft"}],
                "light": [{"type": "solid", "rgb": [255, 190, 120]}],
            },
        }
    if _contains_any(lowered, text, "往左看", "向左看", "看左边", "look left"):
        return {
            "intent": "look_left",
            "priors": ["cool side sweep", "left glance"],
            "summary": "User requested a leftward look response.",
            "scene": {
                "body": [{"type": "look", "direction": "left"}, {"type": "settle", "style": "soft"}],
                "light": [{"type": "gradient", "palette": [[90, 170, 255], [200, 235, 255]]}],
            },
        }
    if _contains_any(lowered, text, "往右看", "向右看", "看右边", "look right"):
        return {
            "intent": "look_right",
            "priors": ["warm side sweep", "right glance"],
            "summary": "User requested a rightward look response.",
            "scene": {
                "body": [{"type": "look", "direction": "right"}, {"type": "settle", "style": "soft"}],
                "light": [{"type": "gradient", "palette": [[255, 180, 120], [255, 235, 200]]}],
            },
        }
    if _contains_any(lowered, text, "点头", "nod"):
        return {
            "intent": "affirm",
            "priors": ["warm nod", "amber highlight"],
            "summary": "User requested a nod response.",
            "scene": {
                "body": [{"type": "gesture", "name": "nod", "intensity": 0.55, "repeats": 1}],
                "light": [{"type": "solid", "rgb": [255, 175, 90]}],
            },
        }
    return None


def _contains_any(lowered: str, original: str, *needles: str) -> bool:
    return any(needle in lowered or needle in original for needle in needles)
