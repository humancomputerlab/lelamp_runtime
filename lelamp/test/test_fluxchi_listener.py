import json
import tempfile
import types
import unittest
from pathlib import Path

from lelamp.integrations.fluxchi_listener import DispatchDecision, FluxChiStateListener, VoiceGate


class _ProfileStub:
    debounce_same_level_sec = 30.0
    level_upgrade_immediate = True
    global_budget_max = None
    global_budget_window_sec = 300.0

    def decide(self, _frame, *, post_intervention=False):
        assert post_intervention is False
        return DispatchDecision(
            level="moderate",
            urgency_rank=2,
            recording="headshake",
            rgb=(255, 170, 50),
        )


class _DashboardStub:
    def __init__(self) -> None:
        self.calls = []
        self._idle_waited_after_solid = False

    async def wait_until_idle(self, timeout_s=5.0, poll_s=0.05) -> None:
        self.calls.append(("wait_until_idle", timeout_s))
        self._idle_waited_after_solid = True

    async def solid(self, rgb):
        self.calls.append(("solid", rgb))
        self._idle_waited_after_solid = False
        return {"ok": True}

    async def play(self, recording_name):
        if not self._idle_waited_after_solid:
            raise AssertionError("play called before dashboard returned to idle after solid")
        self.calls.append(("play", recording_name))
        return {"ok": True}


class FluxChiListenerTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_waits_for_dashboard_idle_between_light_and_motion(self) -> None:
        dashboard = _DashboardStub()
        listener = FluxChiStateListener(
            ws_url="ws://example.test/ws/harness",
            profile=_ProfileStub(),
            dashboard=dashboard,
            voice_gate=VoiceGate(enabled=False),
            dry_run=False,
        )

        frame = {"ts": 9999999999.0, "subject": {}, "events": []}

        await listener._handle(frame)

        self.assertEqual(
            dashboard.calls,
            [
                ("wait_until_idle", 5.0),
                ("solid", (255, 170, 50)),
                ("wait_until_idle", 5.0),
                ("wait_until_idle", 5.0),
                ("play", "headshake"),
            ],
        )
        self.assertIn("moderate", listener._last_dispatch_ts)
        self.assertEqual(listener._last_level_rank, 2)


class VoiceGateTests(unittest.TestCase):
    def _write_state(self, path: Path, **data) -> None:
        payload = {"updated_at_ms": 9999999999999}
        payload.update(data)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_blocks_local_voice_runtime_states(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "voice.json"
            gate = VoiceGate(path=str(path), enabled=True, stale_sec=5.0)

            for state in ("listening", "committing", "replying"):
                self._write_state(path, local_state=state)
                blocked, reason = gate.should_block()
                self.assertTrue(blocked)
                self.assertEqual(reason, f"voice_{state}")

    def test_ignores_stale_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "voice.json"
            gate = VoiceGate(path=str(path), enabled=True, stale_sec=0.01)
            self._write_state(path, local_state="replying", updated_at_ms=1)
            blocked, reason = gate.should_block()
            self.assertFalse(blocked)
            self.assertTrue(reason.startswith("stale_"))


if __name__ == "__main__":
    unittest.main()
