"""Recovery's pure parts: the clock and the key's history."""

import unittest

from kiosk import recovery

KEY = "menus/stall-kopi.json"


class RecoveryClock(unittest.TestCase):
    """ResOps: time to clean recovery, measured from the logged fault, against the impact tolerance."""

    LOG = {
        "changes": [
            {"change_id": "PROMO-7", "applied_at": "2026-10-28T04:38:57+00:00"},
            {"change_id": "MNT-142", "applied_at": "2026-10-28T04:39:00+00:00"},
        ]
    }

    def test_time_since_the_fault(self):
        clock = recovery.recovery_clock(self.LOG, "MNT-142", "2026-10-28T04:42:12+00:00", tolerance_minutes=10)
        self.assertEqual(clock, "3 min 12 s after MNT-142: within the 10 min tolerance")

    def test_over_tolerance_is_named(self):
        clock = recovery.recovery_clock(self.LOG, "MNT-142", "2026-10-28T04:49:01+00:00", tolerance_minutes=10)
        self.assertEqual(clock, "10 min 1 s after MNT-142: OVER the 10 min tolerance")

    def test_no_fault_in_the_log_means_no_clock(self):
        self.assertIsNone(
            recovery.recovery_clock(
                {"changes": [self.LOG["changes"][0]]}, "MNT-142", "2026-10-28T04:42:12+00:00", tolerance_minutes=10
            )
        )

    def test_a_malformed_fault_time_means_no_clock(self):
        log = {"changes": [{"change_id": "MNT-142", "applied_at": "not a time"}]}
        self.assertIsNone(recovery.recovery_clock(log, "MNT-142", "2026-10-28T04:42:12+00:00", tolerance_minutes=10))


class KeyHistory(unittest.TestCase):
    """The end of the receipts: the key's recent versions, judged against the expected menu, so the
    reader sees the restored good bytes and the fault kept in history."""

    def setUp(self):
        import json
        import pathlib

        from kiosk import menu

        self.scenario = menu.load_scenario(
            json.loads((pathlib.Path(__file__).resolve().parent.parent / "scenario" / "kopi.json").read_text())
        )
        self.good = menu.seed_menus(self.scenario)[KEY]
        self.bad = menu.apply_fault(menu.apply_promo(menu.seed_menus(self.scenario), self.scenario), self.scenario)[KEY]

    def test_restored_then_fault_kept(self):
        versions = [
            {"version_id": "v3", "last_modified": "2026-10-28T04:42:56Z", "size": 548, "menu": self.good},
            {"version_id": "v2", "last_modified": "2026-10-28T04:39:03Z", "size": 554, "menu": self.bad},
        ]
        self.assertEqual(
            recovery.history_lines(versions, "kopi", self.scenario),
            [
                "  now     GOOD     548 B  2026-10-28T04:42:56Z  v3",
                "  before  FAULTED  554 B  2026-10-28T04:39:03Z  v2  (kept in history, not deleted)",
            ],
        )

    def test_a_deleted_version_is_named(self):
        versions = [{"version_id": "d1", "last_modified": "2026-10-28T05:00:00Z", "size": 0, "menu": None}]
        self.assertEqual(
            recovery.history_lines(versions, "kopi", self.scenario),
            ["  now     DELETED    0 B  2026-10-28T05:00:00Z  d1"],
        )


if __name__ == "__main__":
    unittest.main()
