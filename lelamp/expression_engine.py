from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ExpressionStyle = Literal[
    "caring",
    "worried",
    "sad",
    "happy",
    "curious",
    "shocked",
    "calm",
    "greeting",
    "celebrate",
]

EXPRESSION_STYLE_CHOICES: tuple[ExpressionStyle, ...] = (
    "caring",
    "worried",
    "sad",
    "happy",
    "curious",
    "shocked",
    "calm",
    "greeting",
    "celebrate",
)


@dataclass(frozen=True)
class ExpressionPlan:
    recording_name: str | None = None
    solid_rgb: tuple[int, int, int] | None = None
    pattern_rgb: list[tuple[int, int, int]] | None = None


def _build_rainbow_pattern(led_count: int) -> list[tuple[int, int, int]]:
    palette = (
        (255, 80, 80),
        (255, 170, 70),
        (255, 230, 90),
        (70, 255, 120),
        (70, 180, 255),
        (170, 120, 255),
    )
    count = max(1, led_count)
    return [palette[index % len(palette)] for index in range(count)]


def build_expression_plan(style: str, led_count: int) -> ExpressionPlan | None:
    if style == "caring":
        return ExpressionPlan(recording_name="shy", solid_rgb=(255, 200, 90))
    if style == "worried":
        return ExpressionPlan(recording_name="headshake", solid_rgb=(255, 170, 60))
    if style == "sad":
        return ExpressionPlan(recording_name="sad", solid_rgb=(255, 90, 60))
    if style == "happy":
        return ExpressionPlan(recording_name="happy_wiggle", solid_rgb=(70, 255, 120))
    if style == "curious":
        return ExpressionPlan(recording_name="curious", solid_rgb=(235, 240, 255))
    if style == "shocked":
        return ExpressionPlan(recording_name="shock", solid_rgb=(255, 255, 255))
    if style == "calm":
        return ExpressionPlan(recording_name="idle", solid_rgb=(255, 200, 135))
    if style == "greeting":
        return ExpressionPlan(recording_name="wake_up", solid_rgb=(255, 185, 95))
    if style == "celebrate":
        return ExpressionPlan(
            recording_name="happy_wiggle",
            pattern_rgb=_build_rainbow_pattern(led_count),
        )
    return None


def dispatch_expression(
    *,
    style: str,
    animation_service,
    animation_service_error: str | None,
    rgb_service,
    led_count: int,
) -> str:
    plan = build_expression_plan(style, led_count)
    if plan is None:
        available = ", ".join(EXPRESSION_STYLE_CHOICES)
        return f"Unknown expression style: {style}. Use one of: {available}"

    applied = False

    if plan.recording_name and animation_service_error is None:
        animation_service.dispatch("play", plan.recording_name)
        applied = True

    if plan.pattern_rgb and rgb_service is not None:
        rgb_service.dispatch("paint", plan.pattern_rgb)
        applied = True
    elif plan.solid_rgb and rgb_service is not None:
        rgb_service.dispatch("solid", plan.solid_rgb)
        applied = True

    if applied:
        return "expression_ok"

    if animation_service_error is not None:
        return f"Expression is unavailable: {animation_service_error}"
    if rgb_service is None:
        return "Expression is unavailable: RGB is disabled and no motion was dispatched"
    return "Expression is unavailable"
