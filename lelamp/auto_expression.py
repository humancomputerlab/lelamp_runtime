from __future__ import annotations

import logging
from threading import Event, Lock, Thread
from time import time
from typing import Any, Callable

from lelamp.expression_engine import ExpressionStyle, dispatch_expression
from lelamp.voice_telemetry import get_voice_telemetry


logger = logging.getLogger(__name__)

_DEFAULT_DELAY_MS = 350
_DEFAULT_POLL_INTERVAL_S = 0.1

_GREETING_KEYWORDS = ("回来", "回来了", "我来了", "你好", "hi", "hello", "晚上好", "早上好")
_CARING_KEYWORDS = ("累", "困", "休息", "喝水", "咳", "头疼", "难受", "生病")
_CELEBRATE_KEYWORDS = ("赢了", "拿下", "mvp", "起飞", "三杀", "四杀", "五杀", "超神", "庆祝")
_HAPPY_KEYWORDS = ("哈哈", "嘿嘿", "牛", "厉害", "真棒", "稳", "舒服", "夸", "开心")
_WORRIED_KEYWORDS = ("烦", "气", "卡", "抢不过", "黏", "阴", "骂", "输了", "崩", "难打", "压力", "急")
_SHOCKED_KEYWORDS = ("卧槽", "我操", "靠", "天哪", "突然", "完了", "吓", "炸了", "别砸")
_CURIOUS_KEYWORDS = ("怎么", "啥", "什么", "是不是", "在吗", "听见", "听到", "？", "?")


def _now_ms() -> int:
    return int(time() * 1000)


def infer_expression_style(transcript: str | None) -> ExpressionStyle | None:
    text = (transcript or "").strip().lower()
    if not text:
        return None

    if any(keyword in text for keyword in _GREETING_KEYWORDS):
        return "greeting"
    if any(keyword in text for keyword in _CARING_KEYWORDS):
        return "caring"
    if any(keyword in text for keyword in _CELEBRATE_KEYWORDS):
        return "celebrate"
    if any(keyword in text for keyword in _HAPPY_KEYWORDS):
        return "happy"
    if any(keyword in text for keyword in _WORRIED_KEYWORDS):
        return "worried"
    if any(keyword in text for keyword in _SHOCKED_KEYWORDS):
        return "shocked"
    if any(keyword in text for keyword in _CURIOUS_KEYWORDS):
        return "curious"
    return "calm"


def pick_fallback_expression(
    *,
    snapshot: dict[str, Any],
    now_ms: int,
    handled_commit_at_ms: int,
    last_tool_dispatch_at_ms: int,
    delay_ms: int = _DEFAULT_DELAY_MS,
) -> tuple[int, ExpressionStyle | None] | None:
    commit_at_ms = int(snapshot.get("last_commit_at_ms") or 0)
    if commit_at_ms <= 0 or commit_at_ms <= handled_commit_at_ms:
        return None

    if now_ms < commit_at_ms + max(delay_ms, 0):
        return None

    if last_tool_dispatch_at_ms >= commit_at_ms:
        return (commit_at_ms, None)

    if snapshot.get("last_asr_status") not in (None, "ok"):
        return (commit_at_ms, None)

    style = infer_expression_style(snapshot.get("last_asr_text"))
    return (commit_at_ms, style)


class AutoExpressionController:
    def __init__(
        self,
        *,
        animation_service,
        get_animation_service_error,
        rgb_service,
        led_count: int,
        on_fallback_expression: Callable[..., None] | None = None,
        delay_ms: int = _DEFAULT_DELAY_MS,
        poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
    ) -> None:
        self._animation_service = animation_service
        self._get_animation_service_error = get_animation_service_error
        self._rgb_service = rgb_service
        self._led_count = led_count
        self._on_fallback_expression = on_fallback_expression
        self._delay_ms = max(int(delay_ms), 0)
        self._poll_interval_s = max(float(poll_interval_s), 0.05)
        self._stop_event = Event()
        self._state_lock = Lock()
        self._handled_commit_at_ms = 0
        self._last_tool_dispatch_at_ms = 0
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = Thread(target=self._run, name="lelamp-auto-expression", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def note_tool_dispatch(self) -> None:
        with self._state_lock:
            self._last_tool_dispatch_at_ms = _now_ms()

    def _dispatch_fallback(self, *, style: ExpressionStyle, trigger: str) -> None:
        started_ts_ms = _now_ms()
        ok = False
        error = None
        try:
            result = dispatch_expression(
                style=style,
                animation_service=self._animation_service,
                animation_service_error=self._get_animation_service_error(),
                rgb_service=self._rgb_service,
                led_count=self._led_count,
            )
            ok = True
            self.note_tool_dispatch()
            logger.debug(
                "Auto expression fallback dispatched",
                extra={"style": style, "result": result},
            )
        except Exception as exc:
            error = str(exc)
            logger.exception("Auto expression fallback failed", extra={"style": style})
        finally:
            ended_ts_ms = _now_ms()
            if self._on_fallback_expression is not None:
                try:
                    self._on_fallback_expression(
                        style=style,
                        trigger=trigger,
                        started_ts_ms=started_ts_ms,
                        ended_ts_ms=ended_ts_ms,
                        ok=ok,
                        error=error,
                    )
                except Exception:
                    logger.exception(
                        "Auto expression memory callback failed",
                        extra={"style": style},
                    )

    def _run(self) -> None:
        telemetry = get_voice_telemetry()
        while not self._stop_event.wait(self._poll_interval_s):
            with self._state_lock:
                handled_commit_at_ms = self._handled_commit_at_ms
                last_tool_dispatch_at_ms = self._last_tool_dispatch_at_ms

            decision = pick_fallback_expression(
                snapshot=telemetry.snapshot(),
                now_ms=_now_ms(),
                handled_commit_at_ms=handled_commit_at_ms,
                last_tool_dispatch_at_ms=last_tool_dispatch_at_ms,
                delay_ms=self._delay_ms,
            )
            if decision is None:
                continue

            commit_at_ms, style = decision
            with self._state_lock:
                if commit_at_ms <= self._handled_commit_at_ms:
                    continue
                self._handled_commit_at_ms = commit_at_ms

            if style is None:
                continue

            self._dispatch_fallback(
                style=style,
                trigger="voice_silence_timeout",
            )
