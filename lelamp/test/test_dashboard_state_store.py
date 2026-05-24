import unittest
from copy import deepcopy
from unittest.mock import patch

from lelamp.dashboard import state_store
from lelamp.dashboard.state_store import DashboardStateStore


class DashboardStateStoreTests(unittest.TestCase):
    def test_snapshot_starts_with_unknown_sections(self) -> None:
        store = DashboardStateStore()
        snapshot = store.snapshot()

        self.assertEqual(snapshot["system"]["status"], "unknown")
        self.assertEqual(snapshot["motion"]["status"], "unknown")
        self.assertEqual(snapshot["light"]["status"], "unknown")
        self.assertEqual(snapshot["audio"]["status"], "unknown")
        self.assertEqual(snapshot["voice"]["status"], "unknown")
        self.assertEqual(snapshot["errors"], [])

    def test_snapshot_returns_deep_copy(self) -> None:
        store = DashboardStateStore()
        first = store.patch(
            "motion",
            {
                "available_recordings": ["home_safe"],
                "last_result": {"status": "ok"},
            },
        )

        first["motion"]["available_recordings"].append("wave")
        first["motion"]["last_result"]["status"] = "mutated"
        first["errors"].append({"code": "unexpected"})

        snapshot = store.snapshot()

        self.assertEqual(snapshot["motion"]["available_recordings"], ["home_safe"])
        self.assertEqual(snapshot["motion"]["last_result"], {"status": "ok"})
        self.assertEqual(snapshot["errors"], [])

    def test_patch_merges_nested_state_and_updates_timestamp(self) -> None:
        store = DashboardStateStore()
        first_update = store.snapshot()["system"]["last_update_ms"]

        snapshot = store.patch("motion", {"status": "idle", "current_recording": "home_safe"})

        self.assertEqual(snapshot["motion"]["status"], "idle")
        self.assertEqual(snapshot["motion"]["current_recording"], "home_safe")
        self.assertGreaterEqual(snapshot["system"]["last_update_ms"], first_update)

    def test_patch_copies_nested_values(self) -> None:
        store = DashboardStateStore()
        values = {
            "available_recordings": ["home_safe"],
            "last_result": {"status": "ok"},
        }

        store.patch("motion", values)
        values["available_recordings"].append("wave")
        values["last_result"]["status"] = "mutated"

        snapshot = store.snapshot()

        self.assertEqual(snapshot["motion"]["available_recordings"], ["home_safe"])
        self.assertEqual(snapshot["motion"]["last_result"], {"status": "ok"})

    def test_patch_with_updates_section_from_current_state(self) -> None:
        store = DashboardStateStore()
        store.patch("motion", {"status": "running", "current_recording": "startup"})

        snapshot = store.patch_with(
            "motion",
            lambda current: dict(current, status="idle", current_recording="home_safe"),
        )

        self.assertEqual(snapshot["motion"]["status"], "idle")
        self.assertEqual(snapshot["motion"]["current_recording"], "home_safe")

    def test_patch_with_copies_nested_values(self) -> None:
        store = DashboardStateStore()
        update = {
            "available_recordings": ["home_safe"],
            "last_result": {"status": "ok"},
        }

        store.patch_with("motion", lambda current: dict(current, **update))
        update["available_recordings"].append("wave")
        update["last_result"]["status"] = "mutated"

        snapshot = store.snapshot()

        self.assertEqual(snapshot["motion"]["available_recordings"], ["home_safe"])
        self.assertEqual(snapshot["motion"]["last_result"], {"status": "ok"})

    def test_record_error_deduplicates_by_code_and_source_and_refreshes_fields(self) -> None:
        store = DashboardStateStore()

        with patch("lelamp.dashboard.state_store._now_ms", side_effect=[100, 250]):
            store.record_error("motor.read_failed", "read timeout", "motors", "warning")
            snapshot = store.record_error("motor.read_failed", "recovered retry failed", "motors", "error")

        self.assertEqual(len(snapshot["errors"]), 1)
        self.assertTrue(snapshot["errors"][0]["active"])
        self.assertEqual(snapshot["errors"][0]["message"], "recovered retry failed")
        self.assertEqual(snapshot["errors"][0]["severity"], "error")
        self.assertEqual(snapshot["errors"][0]["first_seen_ms"], 100)
        self.assertEqual(snapshot["errors"][0]["last_seen_ms"], 250)

    def test_record_error_prepends_new_unique_errors(self) -> None:
        store = DashboardStateStore()

        store.record_error("motor.read_failed", "read timeout", "motors", "warning")
        snapshot = store.record_error("audio.device_missing", "device offline", "audio", "error")

        self.assertEqual(snapshot["errors"][0]["code"], "audio.device_missing")
        self.assertEqual(snapshot["errors"][1]["code"], "motor.read_failed")

    def test_record_error_promotes_existing_error_to_front_when_refreshed(self) -> None:
        store = DashboardStateStore()

        with patch("lelamp.dashboard.state_store._now_ms", side_effect=[100, 200, 300]):
            store.record_error("motor.read_failed", "read timeout", "motors", "warning")
            store.record_error("audio.device_missing", "device offline", "audio", "error")
            snapshot = store.record_error("motor.read_failed", "retry timeout", "motors", "error")

        self.assertEqual(snapshot["errors"][0]["code"], "motor.read_failed")
        self.assertEqual(snapshot["errors"][1]["code"], "audio.device_missing")
        self.assertEqual(snapshot["errors"][0]["first_seen_ms"], 100)
        self.assertEqual(snapshot["errors"][0]["last_seen_ms"], 300)

    def test_resolve_error_marks_matching_error_inactive(self) -> None:
        store = DashboardStateStore()

        with patch("lelamp.dashboard.state_store._now_ms", side_effect=[100, 250]):
            store.record_error("action.shutdown_pose", "motor unavailable", "motion", "error")
            snapshot = store.resolve_error("action.shutdown_pose", "motion")

        self.assertFalse(snapshot["errors"][0]["active"])
        self.assertEqual(snapshot["errors"][0]["last_seen_ms"], 250)

    def test_voice_warning_contributes_to_derived_system_status(self) -> None:
        store = DashboardStateStore()

        store.patch("voice", {"status": "warning", "local_state": "committing"})
        snapshot = store.reconcile_system({"status": "ready"})

        self.assertEqual(snapshot["system"]["status"], "warning")

    def test_mutating_exported_default_state_does_not_affect_new_store(self) -> None:
        original_default = deepcopy(state_store.DEFAULT_STATE)
        try:
            state_store.DEFAULT_STATE["system"]["status"] = "poisoned"
            state_store.DEFAULT_STATE["motion"]["available_recordings"].append("poisoned")
            state_store.DEFAULT_STATE["errors"].append({"code": "poisoned"})

            snapshot = DashboardStateStore().snapshot()
        finally:
            state_store.DEFAULT_STATE.clear()
            state_store.DEFAULT_STATE.update(original_default)

        self.assertEqual(snapshot["system"]["status"], "unknown")
        self.assertEqual(snapshot["motion"]["available_recordings"], [])
        self.assertEqual(snapshot["errors"], [])


if __name__ == "__main__":
    unittest.main()
