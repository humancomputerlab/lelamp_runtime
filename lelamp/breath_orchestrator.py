"""Breath orchestrator — 连续呼吸共振调制器（Scene B 的核心）。

用法场景（DEMO_PLAN §2，R4 pivot "shared-breath co-regulation"）：
    90 秒内，台灯 idle 呼吸从 12bpm 降到 4bpm、色温从 3000K 压到 2400K，不说话、不摇头。
    核心是"示范慢呼吸"，让用户跟着放松，而不是 Scene A 的"打断式提醒"。

设计约束（和 Scene A 一致，见 docs/EXTERNAL_INTEGRATION_CN.md §8）：
    - 不直接 new RGBService —— orchestrator 只接受一个 handle_event(event_type, payload) 可调对象
      （典型是 motor_bus.client.build_rgb_service() 返回的 ProxyRGBService）
    - 所以 orchestrator 进程没有硬件 ownership，全部通过 motor_bus 内部 loopback
    - 不经 dashboard HTTP —— 因为 dashboard 有 busy-lock，10Hz 连续调制会撞 409；
      motor_bus /rgb/solid 是 owner 自己起的 server，单调写 LED 带是安全的

编排：
    - duration_sec 秒内，以 tick_hz（默认 10Hz）连续推 solid RGB
    - 每 tick：
        1. 线性插值 bpm 和 kelvin（随时间放缓变暖）
        2. 按当前 bpm 积分相位（不是 phase = t*bpm；否则在 bpm 变化时会跳）
        3. breath = (1 - cos(2π·phase)) / 2 ∈ [0,1]，呼气起步
        4. intensity = base + (peak - base) · breath
        5. rgb = kelvin_to_rgb(current_kelvin) · intensity
    - 结束后默认调 handle_event("solid", (0,0,0)) 熄灯（stop_clears=True）

线程模型：
    - BreathOrchestrator 是 daemon Thread，start() 即跑
    - stop() 幂等、线程安全；wait_until_done(timeout) 阻塞式等
    - 所有错误被 log 掉但不会 crash runtime —— 一次 tick 失败不应该把整条呼吸打断
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Tuple

log = logging.getLogger("breath_orchestrator")


# ─── Color temperature helpers ────────────────────────────────


def kelvin_to_rgb(kelvin: float) -> Tuple[int, int, int]:
    """Tanner Helland 近似：色温（K）→ 8-bit sRGB 三元组。

    参考：https://tannerhelland.com/2012/09/18/convert-temperature-rgb-algorithm-code.html

    入参被夹到 [1000, 40000]K。本项目里典型区间是 2000K（很暖）到 6500K（日光白）。
    """
    k = max(1000.0, min(40000.0, float(kelvin)))
    t = k / 100.0

    # Red
    if t <= 66:
        r = 255.0
    else:
        r = 329.698727446 * ((t - 60.0) ** -0.1332047592)

    # Green
    if t <= 66:
        g = 99.4708025861 * math.log(t) - 161.1195681661
    else:
        g = 288.1221695283 * ((t - 60.0) ** -0.0755148492)

    # Blue
    if t >= 66:
        b = 255.0
    elif t <= 19:
        b = 0.0
    else:
        b = 138.5177312231 * math.log(t - 10.0) - 305.0447927307

    return (
        int(max(0.0, min(255.0, r))),
        int(max(0.0, min(255.0, g))),
        int(max(0.0, min(255.0, b))),
    )


def _apply_intensity(rgb: Tuple[int, int, int], intensity: float) -> Tuple[int, int, int]:
    i = max(0.0, min(1.0, float(intensity)))
    return (
        int(round(rgb[0] * i)),
        int(round(rgb[1] * i)),
        int(round(rgb[2] * i)),
    )


def _lerp(a: float, b: float, t: float) -> float:
    t = max(0.0, min(1.0, t))
    return a + (b - a) * t


# ─── Breath plan ──────────────────────────────────────────────


@dataclass
class BreathPlan:
    """一段呼吸共振的参数。

    典型 R4 配置：
        BreathPlan(duration_sec=90, bpm_start=12, bpm_end=4,
                   kelvin_start=3000, kelvin_end=2400,
                   intensity_base=0.35, intensity_peak=1.0)
    """

    duration_sec: float = 90.0
    bpm_start: float = 12.0
    bpm_end: float = 4.0
    kelvin_start: float = 3000.0
    kelvin_end: float = 2400.0
    intensity_base: float = 0.35   # 呼气谷底亮度倍率（不要到 0，否则像关灯）
    intensity_peak: float = 1.0    # 吸气峰值亮度倍率
    tick_hz: float = 10.0
    name: str = "shared_breath_v0"

    def __post_init__(self) -> None:
        if self.duration_sec <= 0:
            raise ValueError(f"duration_sec must be > 0, got {self.duration_sec}")
        if self.tick_hz <= 0:
            raise ValueError(f"tick_hz must be > 0, got {self.tick_hz}")
        if not (0.0 <= self.intensity_base <= 1.0):
            raise ValueError(f"intensity_base must be in [0,1], got {self.intensity_base}")
        if not (0.0 <= self.intensity_peak <= 1.0):
            raise ValueError(f"intensity_peak must be in [0,1], got {self.intensity_peak}")
        if self.intensity_peak < self.intensity_base:
            raise ValueError("intensity_peak must be >= intensity_base")
        if self.bpm_start <= 0 or self.bpm_end <= 0:
            raise ValueError("bpm_start / bpm_end must be > 0")


# ─── Orchestrator thread ──────────────────────────────────────


HandleEvent = Callable[[str, Any], None]


@dataclass
class BreathStats:
    """跑完以后可供回读的统计，主要给测试和日志用。"""

    ticks: int = 0
    dispatches: int = 0
    skipped: int = 0
    errors: int = 0
    fade_ticks: int = 0
    last_rgb: Optional[Tuple[int, int, int]] = None
    last_bpm: float = 0.0
    last_kelvin: float = 0.0
    last_phase: float = 0.0
    completed: bool = False
    interrupt_reason: Optional[str] = None  # voice gate / manual override / None


class BreathOrchestrator:
    """按 BreathPlan 在后台线程里连续推 solid RGB。

    handle_event 语义和 ProxyRGBService / RGBService 一致：
        handle_event("solid", (r, g, b))
    orchestrator 只会用 "solid" 事件；不会 paint / dispatch 其它种类。

    一次 orchestrator 只能 start() 一次。如果需要再放一段，new 一个实例。
    """

    def __init__(
        self,
        plan: BreathPlan,
        handle_event: HandleEvent,
        *,
        stop_clears: bool = True,
        dedupe: bool = True,
        name: Optional[str] = None,
        should_continue: Optional[Callable[[], Optional[str]]] = None,
        fade_out_sec: float = 2.0,
        interrupt_fade_sec: float = 0.6,
    ) -> None:
        """
        should_continue: 每 tick 调一次；返回 None = 继续，非 None 字符串 = 中断原因
            典型用法：listener 传 `lambda: voice_gate.should_block()[1]
            if voice_gate.should_block()[0] else None`，让呼吸在用户说话时立刻退出
        fade_out_sec: 自然跑完 duration 后，从最后一帧的 intensity 渐隐到 (0,0,0) 的秒数
            保留 kelvin 不变（warm 色温不变冷），只降 intensity。设 0 则退回硬切到 (0,0,0)
        interrupt_fade_sec: 被 should_continue / stop() 中断时，走的短 fade；比自然 fade 短
            这样尊重中断意图，同时避免观感上突然黑屏
        """
        self.plan = plan
        self._handle_event = handle_event
        self._stop_clears = stop_clears
        self._dedupe = dedupe
        self._should_continue = should_continue
        self._fade_out_sec = max(0.0, float(fade_out_sec))
        self._interrupt_fade_sec = max(0.0, float(interrupt_fade_sec))

        self._stop_event = threading.Event()
        self._done_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._started = False
        self._lock = threading.Lock()
        self._clear_sent = False
        self._last_rgb: Optional[Tuple[int, int, int]] = None
        self.stats = BreathStats()

        self._display_name = name or plan.name

    # --- public API -------------------------------------------

    def start(self) -> None:
        """启动后台线程。幂等：第二次 start() 会 log warning 并忽略。"""
        with self._lock:
            if self._started:
                log.warning("breath orchestrator %s already started, ignoring", self._display_name)
                return
            self._started = True
            self._thread = threading.Thread(
                target=self._run,
                name=f"breath-{self._display_name}",
                daemon=True,
            )
            self._thread.start()

    def stop(self, *, clear: Optional[bool] = None) -> None:
        """请求停止。幂等、线程安全。

        clear=True 会在停止后 dispatch("solid", (0,0,0)) 清灯；
        clear=None 用构造时的 stop_clears 默认值；clear=False 保留最后一帧。

        注：实际的 fade + 清灯是在后台线程里做的（让 fade 顺滑）；
        这个方法 join 线程，等到它把 fade 走完再返回。
        """
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            # 给 interrupt fade 留够时间，再加 200ms buffer
            thread.join(timeout=self._interrupt_fade_sec + 0.3)

        if clear is None:
            clear = self._stop_clears
        if clear:
            # 兜底：线程若超时还没清，这里直接 hard clear（已清会被 _clear_once 跳过）
            self._clear_once()

    def wait_until_done(self, timeout: Optional[float] = None) -> bool:
        """阻塞直到线程结束或 timeout。返回是否真的完成。"""
        return self._done_event.wait(timeout=timeout)

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    # --- internals --------------------------------------------

    def _safe_dispatch(self, rgb: Tuple[int, int, int]) -> bool:
        try:
            self._handle_event("solid", rgb)
            self.stats.dispatches += 1
            self.stats.last_rgb = rgb
            self._last_rgb = rgb
            return True
        except Exception as exc:  # noqa: BLE001
            self.stats.errors += 1
            log.warning("breath dispatch failed rgb=%s err=%s", rgb, exc)
            return False

    def _clear_once(self) -> bool:
        """Best-effort clear that preserves the documented idempotent stop() semantics."""
        with self._lock:
            if self._clear_sent:
                return False
            if self._safe_dispatch((0, 0, 0)):
                self._clear_sent = True
                return True
            return False

    def _fade_tail(self, fade_sec: float) -> None:
        """从最后一帧线性渐隐到 (0,0,0)，然后 _clear_once()。

        保留 kelvin 色温不变，只降 intensity —— 尾音应该是 warm 的 amber 淡出，
        不是突然从暖色切到冰冷的黑。fade_sec <= 0 时直接 clear。
        已经 clear 过（幂等）或 _last_rgb 是 None（从没 dispatch 过）时 skip fade。
        """
        if self._clear_sent:
            return
        last = self._last_rgb
        if last is None or fade_sec <= 0:
            self._clear_once()
            return
        steps = max(1, int(round(fade_sec * self.plan.tick_hz)))
        tick_period = 1.0 / self.plan.tick_hz
        for i in range(steps - 1, 0, -1):
            m = i / steps
            rgb = (
                int(round(last[0] * m)),
                int(round(last[1] * m)),
                int(round(last[2] * m)),
            )
            # fade 阶段不做 dedupe（帧数本来就少，每帧都要到硬件），不过相同值还是跳
            if rgb != self._last_rgb:
                if self._safe_dispatch(rgb):
                    self._last_rgb = rgb
                    self.stats.fade_ticks += 1
            # fade 不可被 stop_event 打断——必须走完才体面
            time.sleep(tick_period)
        self._clear_once()
        self.stats.fade_ticks += 1

    def _run(self) -> None:
        plan = self.plan
        tick_period = 1.0 / plan.tick_hz
        start_monotonic = time.monotonic()
        last_tick = start_monotonic
        phase = 0.0  # 0..1 之间单调增；周期末归 0

        last_rgb_sent: Optional[Tuple[int, int, int]] = None

        log.info(
            "breath start name=%s duration=%.1fs bpm %.1f→%.1f kelvin %d→%d",
            self._display_name,
            plan.duration_sec,
            plan.bpm_start,
            plan.bpm_end,
            int(plan.kelvin_start),
            int(plan.kelvin_end),
        )

        exit_reason: str = "natural"  # "natural" | "stop" | "interrupt"
        try:
            while not self._stop_event.is_set():
                # 每 tick 先查 should_continue（voice gate / 用户 override 之类）
                if self._should_continue is not None:
                    try:
                        reason = self._should_continue()
                    except Exception as exc:  # noqa: BLE001
                        log.warning("should_continue raised, treating as continue: %s", exc)
                        reason = None
                    if reason is not None:
                        log.info("breath interrupted by should_continue: %s", reason)
                        self.stats.interrupt_reason = str(reason)
                        exit_reason = "interrupt"
                        break

                now = time.monotonic()
                elapsed = now - start_monotonic
                if elapsed >= plan.duration_sec:
                    break

                dt = max(0.0, now - last_tick)
                last_tick = now

                t_norm = elapsed / plan.duration_sec
                current_bpm = _lerp(plan.bpm_start, plan.bpm_end, t_norm)
                current_kelvin = _lerp(plan.kelvin_start, plan.kelvin_end, t_norm)

                # 相位积分：d(phase)/dt = bpm / 60；bpm 在变，所以不能用 t*bpm/60
                phase = (phase + dt * current_bpm / 60.0) % 1.0

                # (1 - cos) / 2 起步在 0（呼气谷底），到 0.5 时是峰值；更像真实呼吸
                breath = 0.5 - 0.5 * math.cos(2.0 * math.pi * phase)
                intensity = _lerp(plan.intensity_base, plan.intensity_peak, breath)

                base_rgb = kelvin_to_rgb(current_kelvin)
                rgb = _apply_intensity(base_rgb, intensity)

                self.stats.ticks += 1
                self.stats.last_bpm = current_bpm
                self.stats.last_kelvin = current_kelvin
                self.stats.last_phase = phase

                # dedupe：dashboard/motor_bus 上游写 LED 带也要花时间，相邻 tick
                # 完全一样就不必再发。典型在非常低 bpm 的谷底会连续几个 tick 相同。
                if self._dedupe and rgb == last_rgb_sent:
                    self.stats.skipped += 1
                else:
                    if self._safe_dispatch(rgb):
                        last_rgb_sent = rgb

                # 让出线程；stop_event.wait 允许提前唤醒响应 stop()
                remaining = tick_period - (time.monotonic() - now)
                if remaining > 0:
                    self._stop_event.wait(timeout=remaining)

            if self._stop_event.is_set() and exit_reason == "natural":
                exit_reason = "stop"
            self.stats.completed = (exit_reason == "natural")
        finally:
            log.info(
                "breath end name=%s exit=%s ticks=%d dispatches=%d skipped=%d errors=%d completed=%s reason=%s",
                self._display_name,
                exit_reason,
                self.stats.ticks,
                self.stats.dispatches,
                self.stats.skipped,
                self.stats.errors,
                self.stats.completed,
                self.stats.interrupt_reason,
            )
            if self._stop_clears:
                # 所有 exit 路径都走 fade 尾音；自然完成用长 fade，打断用短 fade
                fade_sec = (
                    self._fade_out_sec
                    if exit_reason == "natural"
                    else self._interrupt_fade_sec
                )
                self._fade_tail(fade_sec)
            self._done_event.set()


__all__ = [
    "BreathPlan",
    "BreathOrchestrator",
    "BreathStats",
    "kelvin_to_rgb",
]
