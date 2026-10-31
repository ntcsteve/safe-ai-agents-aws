"""The scenario is a real swap point: a different story, currency and tolerance runs the same journey with
no code change. If this fails, something in the code has learned about coffee."""

import json
import pathlib
import unittest

from kiosk import menu

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


if __name__ == "__main__":
    unittest.main()
