import unittest

from subscriber.core import DashboardState


class DashboardStateTest(unittest.TestCase):
    def test_alarm_raises_when_compartment_opens(self) -> None:
        state = DashboardState()
        events = state.update_compartment(3, True, "100")
        event_types = [event["type"] for event in events]

        self.assertIn("compartment_opened", event_types)
        self.assertIn("alarm_raised", event_types)
        self.assertTrue(state.snapshot()["alarm_active"])

    def test_alarm_clears_when_last_compartment_closes(self) -> None:
        state = DashboardState()
        state.update_compartment(1, True, "100")
        events = state.update_compartment(1, False, "110")
        event_types = [event["type"] for event in events]

        self.assertIn("compartment_closed", event_types)
        self.assertIn("alarm_cleared", event_types)
        self.assertFalse(state.snapshot()["alarm_active"])

    def test_monitoring_disable_suppresses_alarm(self) -> None:
        state = DashboardState()
        state.set_monitoring_enabled(False)
        state.update_compartment(2, True, "100")

        self.assertFalse(state.snapshot()["alarm_active"])

    def test_snapshot_update_changes_multiple_compartments(self) -> None:
        state = DashboardState()
        state.update_from_snapshot(
            [
                {"compartment_id": 1, "open": True},
                {"compartment_id": 2, "open": False},
                {"compartment_id": 3, "open": True},
            ],
            sequence=5,
            source_timestamp="999",
        )
        snapshot = state.snapshot()

        self.assertEqual(snapshot["open_count"], 2)
        self.assertEqual(snapshot["last_sequence"], 5)


if __name__ == "__main__":
    unittest.main()

