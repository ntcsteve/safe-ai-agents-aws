"""The scenario is a real swap point: a different story, currency and tolerance runs the same journey with
no code change. If this fails, something in the code has learned about coffee."""

import json
import pathlib
import unittest
from unittest import mock

from kiosk import commands, menu
from tests.test_commands import KOPI, SETTINGS, VersionedStore, quietly

SCENARIO = pathlib.Path(__file__).resolve().parent / "fixtures" / "books.json"


class AnotherScenario(unittest.TestCase):
    def setUp(self):
        self.scenario = menu.load_scenario(json.loads(SCENARIO.read_text()))
        self.healthy = menu.apply_promo(menu.seed_menus(self.scenario), self.scenario)

    def test_healthy_menus_check_healthy(self):
        self.assertIs(menu.check_menus(self.healthy, self.scenario).state, menu.Health.HEALTHY)

    def test_the_fault_breaks_only_its_own_key(self):
        broken = menu.apply_fault(self.healthy, self.scenario)
        self.assertIs(menu.check_menus(broken, self.scenario).state, menu.Health.BROKEN)
        self.assertEqual(broken["menus/stall-fiction.json"]["items"][0]["price"], 15000)
        self.assertEqual(broken["menus/stall-travel.json"], self.healthy["menus/stall-travel.json"])

    def test_prices_format_in_the_scenarios_own_currency(self):
        self.assertEqual(menu.format_price(1500, self.scenario["currency"]), "$15.00")


class JourneyOnThreeStalls(unittest.TestCase):
    """Reset, break and recover make no assumption about how many stalls there are."""

    def setUp(self):
        self.demo = KOPI.Demo(SETTINGS, menu.load_scenario(json.loads(SCENARIO.read_text())))
        self.demo.__dict__["store"] = VersionedStore(self.demo.scenario)
        self.demo.store.menus["menus/stall-music.json"] = None  # a stall added after the last reset

    def test_reset_seeds_every_stall(self):
        result, printed = quietly(self.demo.reset)
        self.assertEqual(result, 0, printed)
        self.assertIn("HEALTHY", printed)

    def test_break_then_recover_touches_one_stall(self):
        quietly(self.demo.reset)
        others = json.dumps([self.demo.store.menus[f"menus/stall-{s}.json"] for s in ("travel", "music")])
        quietly(self.demo.fault)
        with mock.patch("builtins.input", return_value="ROLL BACK"):
            result, printed = quietly(commands.cmd_recover, self.demo)
        self.assertEqual(result, 0, printed)
        self.assertIn("Atlas is still Sale", printed)
        self.assertEqual(
            json.dumps([self.demo.store.menus[f"menus/stall-{s}.json"] for s in ("travel", "music")]), others
        )


if __name__ == "__main__":
    unittest.main()
