"""Breath CLI — 跑一段呼吸共振。

典型用法（agent / motor_bus 正在跑）：
    uv run python -m lelamp.breath_run --duration 90 --bpm-start 12 --bpm-end 4 \\
        --kelvin-start 3000 --kelvin-end 2400

Dry-run（不碰硬件，打印每帧 RGB / bpm / kelvin 到 stdout，给 demo 录屏和单测用）：
    uv run python -m lelamp.breath_run --dry-run --duration 10

单次 breath 跑完程序就退出。Ctrl+C 中断会触发 stop() 并清灯。

设计约束：
    - 必须经 motor_bus 的 ProxyRGBService 写 LED 带（单 owner，不抢硬件）
    - 没有 motor_bus sentinel 时拒绝启动，除非 --dry-run 或 --allow-direct
    - --allow-direct 是应急手段（没 agent 跑时手动测试），会直接 new RGBService
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from typing import Any, Tuple

from .breath_orchestrator import BreathOrchestrator, BreathPlan

log = logging.getLogger("breath_run")


def _make_dry_run_handler(verbose: bool):
    start = time.monotonic()
    last_log = [0.0]

    def handle(event_type: str, payload: Any) -> None:
        if event_type != "solid":
            log.warning("dry-run got non-solid event_type=%s", event_type)
            return
        if not verbose:
            return
        now = time.monotonic()
        # 每 0.25s 打一行就够看了，避免 10Hz 刷屏
        if now - last_log[0] < 0.25:
            return
        last_log[0] = now
        r, g, b = payload
        print(f"[{now - start:6.2f}s] rgb=({r:3d},{g:3d},{b:3d})", flush=True)

    return handle


def _build_proxy_handler(allow_direct: bool):
    """尝试拿到一个 handle_event(event_type, payload) 可调对象。

    - 先走 motor_bus proxy（推荐，safe）
    - sentinel 缺席且 allow_direct=False → RuntimeError
    - sentinel 缺席且 allow_direct=True → 直接 new RGBService（应急手动测试）
    """
    from .motor_bus.client import build_rgb_service, current_sentinel, REQUIRE_RGB

    if not allow_direct:
        sentinel = current_sentinel(require=REQUIRE_RGB, probe_timeout=1.0)
        if sentinel is None:
            raise RuntimeError(
                "no live motor_bus sentinel — start the agent first, "
                "or pass --allow-direct / --dry-run"
            )

    def _fallback():
        if not allow_direct:
            # unreachable: current_sentinel() already returned non-None
            raise RuntimeError("fallback requested but --allow-direct not set")
        from .service.rgb.rgb_service import RGBService
        log.warning("--allow-direct: building raw RGBService; agent must NOT be running")
        svc = RGBService()
        svc.start()
        return svc

    service = build_rgb_service(_fallback)
    return service


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run a shared-breath co-regulation cycle on LeLamp.")
    p.add_argument("--duration", type=float, default=90.0, help="seconds, default 90")
    p.add_argument("--bpm-start", type=float, default=12.0)
    p.add_argument("--bpm-end", type=float, default=4.0)
    p.add_argument("--kelvin-start", type=float, default=3000.0)
    p.add_argument("--kelvin-end", type=float, default=2400.0)
    p.add_argument("--intensity-base", type=float, default=0.35)
    p.add_argument("--intensity-peak", type=float, default=1.0)
    p.add_argument("--tick-hz", type=float, default=10.0)
    p.add_argument("--name", type=str, default="shared_breath_cli")

    p.add_argument("--dry-run", action="store_true",
                   help="不连 motor_bus，每 ~250ms 打印一行 RGB 供肉眼验证")
    p.add_argument("--allow-direct", action="store_true",
                   help="motor_bus sentinel 缺席时，直接 new RGBService（应急手动测试）")
    p.add_argument("--no-clear", action="store_true",
                   help="结束时不熄灯，保留最后一帧")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    _setup_logging(args.log_level)

    plan = BreathPlan(
        duration_sec=args.duration,
        bpm_start=args.bpm_start,
        bpm_end=args.bpm_end,
        kelvin_start=args.kelvin_start,
        kelvin_end=args.kelvin_end,
        intensity_base=args.intensity_base,
        intensity_peak=args.intensity_peak,
        tick_hz=args.tick_hz,
        name=args.name,
    )

    handle: Any
    service = None
    if args.dry_run:
        handle = _make_dry_run_handler(verbose=True)
    else:
        try:
            service = _build_proxy_handler(allow_direct=args.allow_direct)
        except Exception as exc:  # noqa: BLE001
            log.error("cannot build RGB service: %s", exc)
            return 2
        handle = service.handle_event

    orchestrator = BreathOrchestrator(
        plan,
        handle,
        stop_clears=not args.no_clear,
        name=args.name,
    )

    def _sigint(signum, frame) -> None:  # noqa: ARG001
        log.info("SIGINT — stopping breath")
        orchestrator.stop()

    signal.signal(signal.SIGINT, _sigint)
    signal.signal(signal.SIGTERM, _sigint)

    orchestrator.start()
    try:
        # 等最多 duration + 5s buffer
        orchestrator.wait_until_done(timeout=plan.duration_sec + 5.0)
    finally:
        orchestrator.stop()
        if service is not None and args.allow_direct:
            try:
                service.stop()
            except Exception:  # noqa: BLE001
                pass

    stats = orchestrator.stats
    log.info(
        "done ticks=%d dispatches=%d skipped=%d errors=%d completed=%s",
        stats.ticks, stats.dispatches, stats.skipped, stats.errors, stats.completed,
    )
    return 0 if stats.completed else 1


if __name__ == "__main__":
    sys.exit(main())
