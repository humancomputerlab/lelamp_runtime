import types
import unittest

from lelamp.integrations.fluxchi_listener import DispatchDecision, FluxChiStateListener, VoiceGate


class _ProfileStub:
    debounce_same_level_sec = 30.0
    level_upgrade_immediate = True

    def decide(self, _frame):
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


if __name__ == "__main__":
    unittest.main()
