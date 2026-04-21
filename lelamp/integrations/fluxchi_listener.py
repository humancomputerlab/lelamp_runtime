"""FluxChi → LeLamp harness-mode consumer.

订阅 FluxChi backend 的 `/ws/harness` WebSocket，按 ScenarioProfile 阈值映射成
dashboard action，经 HTTP POST 到本机 dashboard（默认 127.0.0.1:8765）驱动硬件。

设计约束（详见 ../docs/EXTERNAL_INTEGRATION_CN.md §8）：
- 只经 dashboard HTTP，不自建 AnimationService / RGBService
- 触发前读 /tmp/lelamp-voice-state.json，voice busy 时跳过本帧
- stale frame（ts > 5s）不触发，只用于显示
- 同 level 的动作 debounce（默认 30s），level 上跳立即覆盖

运行：
    uv run python -m lelamp.integrations.fluxchi_listener \\
        --ws ws://<mac-ip>:8000/ws/harness \\
        --profile lelamp/integrations/profiles/scene_a.yaml \\
        --dashboard http://127.0.0.1:8765

Demo 不需要真实 EMG 硬件：FluxChi 后端用 `--demo` 起，本 listener 就会收到
synthetic stamina 驱动下的触发。
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lelamp.breath_orchestrator import BreathOrchestrator, BreathPlan

log = logging.getLogger("fluxchi_listener")

# 下列两个依赖在运行时才 import，import 失败时给清晰报错
try:
    import websockets
except ImportError:  # pragma: no cover
    websockets = None  # type: ignore[assignment]

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]


DEFAULT_DASHBOARD = "http://127.0.0.1:8765"
DEFAULT_VOICE_STATE_PATH = "/tmp/lelamp-voice-state.json"
DEFAULT_STALE_SEC = 5.0
VOICE_STATE_STALE_SEC = 5.0
DEFAULT_DASHBOARD_IDLE_TIMEOUT_SEC = 5.0
DEFAULT_DASHBOARD_POLL_SEC = 0.05


# ─── Scenario profile ─────────────────────────────────────────


ACTION_TYPE_MOTION = "motion"  # 默认：solid 灯 + play recording（Scene A 样式）
ACTION_TYPE_BREATH = "breath"  # Scene B 样式：连续呼吸共振，不播动作


@dataclass
class LevelRule:
    """一个疲劳档位的触发规则。"""

    name: str  # "mild" / "moderate" / "severe" / "recovered"
    stamina_max: Optional[float] = None
    stamina_min: Optional[float] = None  # 仅用于 recovered
    perclos_ewma_min: Optional[float] = None
    dwell_sec: float = 0.0
    level3_required: List[str] = field(default_factory=list)
    trigger: Optional[str] = None  # e.g. "after_any_intervention"
    action_type: str = ACTION_TYPE_MOTION
    recording: Optional[str] = None
    followup_recording: Optional[str] = None
    rgb: Optional[Tuple[int, int, int]] = None
    breath: Optional[BreathPlan] = None  # 仅 action_type == "breath" 时使用
    urgency_rank: int = 0  # 0 none / 1 mild / 2 moderate / 3 severe / -1 recovered

    def matches(self, subject: Dict[str, Any], events: List[Dict[str, Any]]) -> bool:
        stamina = subject.get("stamina")
        perclos = subject.get("perclos_ewma")
        # recovered 是"升回来"场景
        if self.name == "recovered":
            return stamina is not None and self.stamina_min is not None and stamina >= self.stamina_min
        # 普通档位：stamina / perclos 任一维度达阈值即认为可触发
        hit = False
        if self.stamina_max is not None and stamina is not None and stamina <= self.stamina_max:
            hit = True
        if self.perclos_ewma_min is not None and perclos is not None and perclos >= self.perclos_ewma_min:
            hit = True
        if self.level3_required:
            kinds = {e.get("kind") for e in events}
            if not set(self.level3_required).issubset(kinds):
                return False
            hit = True
        return hit


@dataclass
class ScenarioProfile:
    name: str
    debounce_same_level_sec: float = 30.0
    level_upgrade_immediate: bool = True
    voice_gate_enabled: bool = True
    voice_gate_block_recent_asr_sec: float = 5.0
    levels: List[LevelRule] = field(default_factory=list)

    @classmethod
    def load_from_yaml(cls, path: Path) -> "ScenarioProfile":
        if yaml is None:
            raise RuntimeError("PyYAML not installed — `uv pip install pyyaml`")
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        debounce = data.get("debounce", {}) or {}
        voice_gate = data.get("voice_gate", {}) or {}
        levels: List[LevelRule] = []
        raw_levels = data.get("thresholds", {}) or {}
        # recovered 有单独的上升触发，和 mild/moderate/severe 不同
        urgency_rank_map = {"mild": 1, "moderate": 2, "severe": 3, "recovered": -1}
        for name, cfg in raw_levels.items():
            action = cfg.get("action", {}) or {}
            rgb = action.get("rgb")
            if rgb is not None:
                rgb = tuple(int(x) for x in rgb)
            action_type = str(action.get("type") or ACTION_TYPE_MOTION).lower()
            breath_plan: Optional[BreathPlan] = None
            if action_type == ACTION_TYPE_BREATH:
                breath_cfg = action.get("breath") or {}
                if not isinstance(breath_cfg, dict):
                    raise ValueError(f"level {name}: action.breath must be a mapping")
                # 只拿 BreathPlan 能识别的字段，忽略未来未知 key
                allowed = {
                    "duration_sec", "bpm_start", "bpm_end",
                    "kelvin_start", "kelvin_end",
                    "intensity_base", "intensity_peak",
                    "tick_hz", "name",
                }
                kwargs = {k: v for k, v in breath_cfg.items() if k in allowed}
                if "name" not in kwargs:
                    kwargs["name"] = f"{data.get('name', path.stem)}_{name}"
                breath_plan = BreathPlan(**kwargs)
            levels.append(LevelRule(
                name=name,
                stamina_max=cfg.get("stamina_max"),
                stamina_min=cfg.get("stamina_min"),
                perclos_ewma_min=cfg.get("perclos_ewma_min"),
                dwell_sec=float(cfg.get("dwell_sec", 0.0)),
                level3_required=list(cfg.get("level3_required", []) or []),
                trigger=cfg.get("trigger"),
                action_type=action_type,
                recording=action.get("express") or action.get("recording"),
                followup_recording=action.get("followup"),
                rgb=rgb,  # type: ignore[arg-type]
                breath=breath_plan,
                urgency_rank=urgency_rank_map.get(name, 0),
            ))
        # 按 urgency 降序，让 severe 比 mild 先匹配
        levels.sort(key=lambda lv: lv.urgency_rank, reverse=True)
        return cls(
            name=data.get("name", path.stem),
            debounce_same_level_sec=float(debounce.get("same_level_sec", 30.0)),
            level_upgrade_immediate=bool(debounce.get("level_upgrade_immediate", True)),
            voice_gate_enabled=bool(voice_gate.get("block_when_speaking", True)),
            voice_gate_block_recent_asr_sec=float(voice_gate.get("block_recent_asr_sec", 5.0)),
            levels=levels,
        )

    def decide(self, frame: Dict[str, Any]) -> Optional["DispatchDecision"]:
        subject = frame.get("subject") or {}
        events = frame.get("events") or []
        for rule in self.levels:
            if rule.trigger == "after_any_intervention":
                # recovered 型由外部状态机决定是否触发（本 v0 先不启用 recovered）
                continue
            if rule.matches(subject, events):
                return DispatchDecision(
                    level=rule.name,
                    urgency_rank=rule.urgency_rank,
                    action_type=rule.action_type,
                    recording=rule.recording,
                    followup_recording=rule.followup_recording,
                    rgb=rule.rgb,
                    breath=rule.breath,
                )
        return None


@dataclass
class DispatchDecision:
    level: str
    urgency_rank: int
    action_type: str = ACTION_TYPE_MOTION
    recording: Optional[str] = None
    followup_recording: Optional[str] = None
    rgb: Optional[Tuple[int, int, int]] = None
    breath: Optional[BreathPlan] = None


# ─── Voice gate ───────────────────────────────────────────────


class VoiceGate:
    def __init__(self, path: str = DEFAULT_VOICE_STATE_PATH,
                 stale_sec: float = VOICE_STATE_STALE_SEC,
                 enabled: bool = True):
        self.path = Path(path)
        self.stale_sec = stale_sec
        self.enabled = enabled

    def should_block(self) -> Tuple[bool, str]:
        if not self.enabled:
            return False, "disabled"
        if not self.path.exists():
            return False, "no_file"
        try:
            data = json.loads(self.path.read_text())
        except Exception as e:
            log.warning("voice state read failed: %s", e)
            return False, "read_error"
        updated_at_ms = data.get("updated_at_ms") or 0
        age_sec = max(0.0, time.time() - updated_at_ms / 1000.0)
        if age_sec > self.stale_sec:
            return False, f"stale_{age_sec:.1f}s"
        local = data.get("local_state") or data.get("status") or ""
        if local in {"user_speaking", "agent_speaking"}:
            return True, f"voice_{local}"
        asr_status = data.get("last_asr_status") or ""
        if asr_status in {"speaking", "active"}:
            return True, f"asr_{asr_status}"
        return False, "idle"


# ─── Dashboard client ─────────────────────────────────────────


class DashboardClient:
    """薄的 HTTP 客户端，只暴露当前 fluxchi_listener 需要的几个动作。

    对应 dashboard/api.py 里已有的路由：
      POST /api/actions/play       {"name": str}
      POST /api/actions/stop
      POST /api/lights/solid       {"red": int, "green": int, "blue": int}
      POST /api/lights/clear
    """

    def __init__(self, base_url: str = DEFAULT_DASHBOARD, timeout: float = 3.0):
        if httpx is None:
            raise RuntimeError("httpx not installed — `uv pip install httpx`")
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def actions(self) -> Dict[str, Any]:
        r = await self._client.get("/api/actions")
        r.raise_for_status()
        return r.json() if r.content else {}

    async def wait_until_idle(
        self,
        timeout_s: float = DEFAULT_DASHBOARD_IDLE_TIMEOUT_SEC,
        poll_s: float = DEFAULT_DASHBOARD_POLL_SEC,
    ) -> None:
        deadline = time.monotonic() + max(timeout_s, 0.0)
        while True:
            payload = await self.actions()
            if not payload.get("busy", False):
                return
            if time.monotonic() >= deadline:
                raise TimeoutError("dashboard stayed busy for too long")
            await asyncio.sleep(max(poll_s, 0.01))

    async def play(self, recording_name: str) -> Dict[str, Any]:
        r = await self._client.post("/api/actions/play", json={"name": recording_name})
        r.raise_for_status()
        return r.json() if r.content else {}

    async def solid(self, rgb: Tuple[int, int, int]) -> Dict[str, Any]:
        r = await self._client.post("/api/lights/solid",
                                    json={"red": int(rgb[0]), "green": int(rgb[1]), "blue": int(rgb[2])})
        r.raise_for_status()
        return r.json() if r.content else {}

    async def lights_clear(self) -> Dict[str, Any]:
        r = await self._client.post("/api/lights/clear")
        r.raise_for_status()
        return r.json() if r.content else {}


# ─── Main listener ────────────────────────────────────────────


class FluxChiStateListener:
    def __init__(
        self,
        ws_url: str,
        profile: ScenarioProfile,
        dashboard: DashboardClient,
        voice_gate: VoiceGate,
        stale_sec: float = DEFAULT_STALE_SEC,
        dry_run: bool = False,
    ):
        if websockets is None:
            raise RuntimeError("websockets not installed — `uv pip install websockets`")
        self.ws_url = ws_url
        self.profile = profile
        self.dashboard = dashboard
        self.voice_gate = voice_gate
        self.stale_sec = stale_sec
        self.dry_run = dry_run
        self._last_dispatch_ts: Dict[str, float] = {}
        self._last_level_rank: int = 0
        # Breath (Scene B) 支持：一次只跑一段，被新决策覆盖即 stop 旧的
        self._current_breath: Optional[BreathOrchestrator] = None
        self._breath_rgb_service: Any = None  # lazy-built ProxyRGBService

    def _get_breath_rgb_service(self) -> Any:
        """按需拿 motor_bus ProxyRGBService；没 sentinel 就抛。

        不允许 fallback_factory 直接 new RGBService——listener 是非 owner 消费者，
        硬件所有权属于 agent/motor_bus server。
        """
        if self._breath_rgb_service is not None:
            return self._breath_rgb_service
        from lelamp.motor_bus.client import build_rgb_service, current_sentinel, REQUIRE_RGB
        sentinel = current_sentinel(require=REQUIRE_RGB, probe_timeout=1.0)
        if sentinel is None:
            raise RuntimeError(
                "breath requires motor_bus sentinel; start the agent/motor_bus server first"
            )

        def _no_fallback():
            raise RuntimeError("breath refuses to bypass motor_bus; check agent is up")

        self._breath_rgb_service = build_rgb_service(_no_fallback)
        return self._breath_rgb_service

    def _breath_interrupt_reason(self) -> Optional[str]:
        """给 BreathOrchestrator 的 should_continue 钩子：voice busy 就中断。

        每 tick 读 /tmp/lelamp-voice-state.json；文件 stale (>5s) 视作 idle，
        不中断（避免 voice telemetry 断链时 breath 永远起不来）。
        """
        blocked, reason = self.voice_gate.should_block()
        if blocked:
            return reason
        return None

    def _start_breath(self, decision: "DispatchDecision") -> None:
        assert decision.breath is not None
        # 先停掉正在跑的那段（幂等）
        if self._current_breath is not None and self._current_breath.running:
            log.info("breath preempt: stopping previous %s", self._current_breath.plan.name)
            self._current_breath.stop()
        service = self._get_breath_rgb_service()
        orchestrator = BreathOrchestrator(
            decision.breath,
            service.handle_event,
            stop_clears=True,
            name=f"{decision.level}_{decision.breath.name}",
            should_continue=self._breath_interrupt_reason,
        )
        orchestrator.start()
        self._current_breath = orchestrator

    async def _play_when_idle(self, recording_name: str, *, retries: int = 2) -> None:
        for attempt in range(retries + 1):
            await self.dashboard.wait_until_idle()
            try:
                await self.dashboard.play(recording_name)
                return
            except Exception as exc:
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                if status_code != 409 or attempt >= retries:
                    raise
                log.debug("dashboard busy on play(%s), retrying attempt=%s", recording_name, attempt + 1)
                await asyncio.sleep(0.1)

    def shutdown(self) -> None:
        """shutdown hook：供 _main_async finally 调用，确保 breath 停掉。"""
        if self._current_breath is not None and self._current_breath.running:
            log.info("listener shutdown: stopping breath")
            self._current_breath.stop()

    async def run(self) -> None:
        backoff = 1.0
        while True:
            try:
                log.info("connecting %s ...", self.ws_url)
                async with websockets.connect(self.ws_url, ping_interval=20) as ws:
                    log.info("connected")
                    backoff = 1.0
                    async for message in ws:
                        try:
                            frame = json.loads(message)
                        except json.JSONDecodeError:
                            log.warning("bad frame: %s", message[:120])
                            continue
                        await self._handle(frame)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("ws session ended (%s); reconnect in %.1fs", e, backoff)
                try:
                    await asyncio.sleep(min(backoff, 30.0))
                finally:
                    backoff = min(backoff * 2, 30.0)

    async def _handle(self, frame: Dict[str, Any]) -> None:
        ts = frame.get("ts", 0.0)
        age = time.time() - ts
        if age > self.stale_sec:
            log.debug("drop stale frame age=%.1fs", age)
            return

        blocked, reason = self.voice_gate.should_block()
        if blocked:
            log.info("gated by voice (%s)", reason)
            return

        decision = self.profile.decide(frame)
        if decision is None:
            return
        if decision.action_type == ACTION_TYPE_MOTION and decision.recording is None:
            return
        if decision.action_type == ACTION_TYPE_BREATH and decision.breath is None:
            log.warning("breath decision missing plan; dropping level=%s", decision.level)
            return

        # Debounce: 同 level 30s 内不重复；level 上跳立即覆盖
        last_at = self._last_dispatch_ts.get(decision.level, 0.0)
        if (time.time() - last_at) < self.profile.debounce_same_level_sec:
            if not (self.profile.level_upgrade_immediate
                    and decision.urgency_rank > self._last_level_rank):
                log.debug("debounced level=%s", decision.level)
                return

        log.info(
            "dispatch level=%s type=%s recording=%s rgb=%s breath=%s",
            decision.level, decision.action_type, decision.recording, decision.rgb,
            decision.breath.name if decision.breath else None,
        )

        if self.dry_run:
            print(json.dumps({
                "dry_run": True,
                "level": decision.level,
                "action_type": decision.action_type,
                "recording": decision.recording,
                "followup": decision.followup_recording,
                "rgb": decision.rgb,
                "breath": dataclasses.asdict(decision.breath) if decision.breath else None,
                "frame_ts": ts,
            }))
        elif decision.action_type == ACTION_TYPE_BREATH:
            try:
                self._start_breath(decision)
            except Exception as e:
                log.error("breath dispatch failed: %s", e)
                return
        else:
            # Motion 路径：若先前正在跑 breath，先停掉再让动作走
            if self._current_breath is not None and self._current_breath.running:
                log.info("motion preempt: stopping breath %s",
                         self._current_breath.plan.name)
                self._current_breath.stop()
            try:
                await self.dashboard.wait_until_idle()
                if decision.rgb is not None:
                    await self.dashboard.solid(decision.rgb)
                    await self.dashboard.wait_until_idle()
                await self._play_when_idle(decision.recording)
                if decision.followup_recording:
                    await self.dashboard.wait_until_idle(timeout_s=10.0)
                    await self._play_when_idle(decision.followup_recording, retries=4)
            except Exception as e:
                log.error("dashboard dispatch failed: %s", e)
                return

        self._last_dispatch_ts[decision.level] = time.time()
        self._last_level_rank = decision.urgency_rank


# ─── CLI ──────────────────────────────────────────────────────


def _default_profile_path() -> Path:
    return Path(__file__).parent / "profiles" / "scene_a.yaml"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="FluxChi → LeLamp harness consumer")
    p.add_argument("--ws", default=os.environ.get("FLUXCHI_WS", "ws://127.0.0.1:8000/ws/harness"),
                   help="FluxChi backend harness websocket url")
    p.add_argument("--profile", type=Path, default=_default_profile_path(),
                   help="Scenario profile yaml path")
    p.add_argument("--dashboard", default=os.environ.get("LELAMP_DASHBOARD", DEFAULT_DASHBOARD),
                   help="Local LeLamp dashboard base url")
    p.add_argument("--voice-state", default=DEFAULT_VOICE_STATE_PATH,
                   help="Voice telemetry state file")
    p.add_argument("--no-voice-gate", action="store_true",
                   help="Disable voice gate (not recommended outside demo)")
    p.add_argument("--stale-sec", type=float, default=DEFAULT_STALE_SEC)
    p.add_argument("--dry-run", action="store_true",
                   help="Print decisions instead of POSTing to dashboard")
    p.add_argument("--log-level", default="INFO")
    return p


async def _main_async(args: argparse.Namespace) -> None:
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    profile_path = Path(args.profile)
    if not profile_path.exists():
        raise SystemExit(f"profile not found: {profile_path}")
    profile = ScenarioProfile.load_from_yaml(profile_path)
    log.info("loaded profile %s with %d levels", profile.name, len(profile.levels))

    voice_gate = VoiceGate(
        path=args.voice_state,
        enabled=not args.no_voice_gate,
    )
    dashboard = DashboardClient(args.dashboard)
    listener = FluxChiStateListener(
        ws_url=args.ws,
        profile=profile,
        dashboard=dashboard,
        voice_gate=voice_gate,
        stale_sec=args.stale_sec,
        dry_run=args.dry_run,
    )
    try:
        await listener.run()
    finally:
        listener.shutdown()
        await dashboard.aclose()


def main() -> None:
    args = build_parser().parse_args()
    try:
        asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
