"""The menu logic. Expected values are written out by hand, never recomputed the way the code does."""

import copy
import json
import pathlib
import unittest

from kiosk import menu

SCENARIO_PATH = pathlib.Path(__file__).resolve().parent.parent / "scenario" / "kopi.json"


def raw_scenario():
    return json.loads(SCENARIO_PATH.read_text())


def expected_menus(scenario):
    """The expected state: seed, then the approved promo."""
    return menu.apply_promo(menu.seed_menus(scenario), scenario)


class ScenarioValidation(unittest.TestCase):
    def test_shipped_scenario_loads(self):
        self.assertEqual(menu.load_scenario(raw_scenario())["name"], "kopi")

    def test_missing_field_is_named(self):
        raw = raw_scenario()
        del raw["currency"]
        with self.assertRaisesRegex(menu.ScenarioError, "currency"):
            menu.load_scenario(raw)

    def test_fault_item_must_exist(self):
        raw = raw_scenario()
        raw["fault"]["items"].append("latte")
        with self.assertRaisesRegex(menu.ScenarioError, r"fault\.items.*latte"):
            menu.load_scenario(raw)

    def test_promo_stall_must_differ_from_fault_stall(self):
        # This proves scoping: the promo must live outside the key being rolled back.
        raw = raw_scenario()
        raw["promo"]["stall"] = "kopi"
        raw["promo"]["item"] = "teh-tarik"
        with self.assertRaisesRegex(menu.ScenarioError, "promo.stall"):
            menu.load_scenario(raw)

    def test_price_must_be_positive_integer(self):
        raw = raw_scenario()
        raw["stalls"]["kopi"]["items"][0]["price"] = 25000.5
        with self.assertRaisesRegex(menu.ScenarioError, "kopi-susu.*price"):
            menu.load_scenario(raw)

    def test_factor_must_be_greater_than_one(self):
        raw = raw_scenario()
        raw["fault"]["factor"] = 1
        with self.assertRaisesRegex(menu.ScenarioError, "fault.factor"):
            menu.load_scenario(raw)


class ScenarioTolerance(unittest.TestCase):
    def test_shipped_tolerance_is_ten_minutes(self):
        self.assertEqual(menu.load_scenario(raw_scenario())["impact_tolerance_minutes"], 10)

    def test_tolerance_must_be_a_positive_whole_number(self):
        for bad in (0, -5, 2.5, "10", True):
            raw = raw_scenario()
            raw["impact_tolerance_minutes"] = bad
            with self.assertRaisesRegex(menu.ScenarioError, "impact_tolerance_minutes"):
                menu.load_scenario(raw)


class BuildMenus(unittest.TestCase):
    def setUp(self):
        self.scenario = menu.load_scenario(raw_scenario())

    def test_one_object_per_stall_at_known_keys(self):
        menus = menu.seed_menus(self.scenario)
        self.assertEqual(sorted(menus), ["menus/stall-kopi.json", "menus/stall-seduh.json"])

    def test_seed_is_deterministic(self):
        first = menu.encode_menu(menu.seed_menus(self.scenario)["menus/stall-kopi.json"])
        second = menu.encode_menu(menu.seed_menus(self.scenario)["menus/stall-kopi.json"])
        self.assertEqual(first, second)

    def test_seed_carries_currency_for_the_page(self):
        kopi = menu.seed_menus(self.scenario)["menus/stall-kopi.json"]
        self.assertEqual(kopi["currency"]["code"], "IDR")
        self.assertEqual(kopi["items"][0], {"id": "kopi-susu", "name": "Kopi Susu", "price": 25000})


class Check(unittest.TestCase):
    def setUp(self):
        self.scenario = menu.load_scenario(raw_scenario())

    def test_expected_menus_are_healthy(self):
        result = menu.check_menus(expected_menus(self.scenario), self.scenario)
        self.assertEqual(result.state, "HEALTHY")
        self.assertEqual(result.problems, [])

    def test_seed_without_promo_names_the_missing_change(self):
        result = menu.check_menus(menu.seed_menus(self.scenario), self.scenario)
        self.assertEqual(result.state, "BROKEN")
        self.assertEqual(result.problems, ["seduh/kopi-flight is 45000, expected 36000 (PROMO-7 not applied)"])

    def test_fault_breaks_exactly_three_items(self):
        faulted = menu.apply_fault(expected_menus(self.scenario), self.scenario)
        result = menu.check_menus(faulted, self.scenario)
        self.assertEqual(result.state, "BROKEN")
        self.assertEqual(
            result.problems,
            [
                "kopi/kopi-susu is 2500000, expected 25000",
                "kopi/kopi-hitam is 1800000, expected 18000",
                "kopi/es-kopi-gula-aren is 2800000, expected 28000",
            ],
        )

    def test_missing_object_is_broken(self):
        menus = expected_menus(self.scenario)
        menus["menus/stall-seduh.json"] = None
        result = menu.check_menus(menus, self.scenario)
        self.assertEqual(result.problems, ["missing menus/stall-seduh.json"])

    def test_missing_item_is_broken(self):
        menus = expected_menus(self.scenario)
        menus["menus/stall-kopi.json"]["items"].pop()
        result = menu.check_menus(menus, self.scenario)
        self.assertEqual(result.problems, ["kopi/teh-tarik is missing"])

    def test_unknown_item_is_broken(self):
        menus = expected_menus(self.scenario)
        menus["menus/stall-kopi.json"]["items"].append({"id": "latte", "name": "Latte", "price": 30000})
        result = menu.check_menus(menus, self.scenario)
        self.assertEqual(result.problems, ["kopi/latte is not on the expected menu"])


class Fault(unittest.TestCase):
    def setUp(self):
        self.scenario = menu.load_scenario(raw_scenario())

    def test_fault_does_not_touch_other_items_or_stalls(self):
        before = expected_menus(self.scenario)
        after = menu.apply_fault(copy.deepcopy(before), self.scenario)
        self.assertEqual(after["menus/stall-seduh.json"], before["menus/stall-seduh.json"])
        self.assertEqual(
            after["menus/stall-kopi.json"]["items"][3], {"id": "teh-tarik", "name": "Teh Tarik", "price": 20000}
        )

    def test_fault_does_not_mutate_its_input(self):
        before = expected_menus(self.scenario)
        snapshot = copy.deepcopy(before)
        menu.apply_fault(before, self.scenario)
        self.assertEqual(before, snapshot)

    def test_fault_twice_is_refused(self):
        once = menu.apply_fault(expected_menus(self.scenario), self.scenario)
        with self.assertRaisesRegex(menu.ChangeRefused, "MNT-142 refused: kopi-susu is 2500000, expected 25000.*reset"):
            menu.apply_fault(once, self.scenario)


class FormatPrice(unittest.TestCase):
    def setUp(self):
        self.idr = menu.load_scenario(raw_scenario())["currency"]

    def test_idr(self):
        self.assertEqual(menu.format_price(25000, self.idr), "Rp 25.000")

    def test_idr_after_fault(self):
        self.assertEqual(menu.format_price(2500000, self.idr), "Rp 2.500.000")

    def test_two_decimal_currency(self):
        myr = {"code": "MYR", "symbol": "RM ", "thousands": ",", "decimal": ".", "decimals": 2}
        self.assertEqual(menu.format_price(123450, myr), "RM 1,234.50")
        self.assertEqual(menu.format_price(5, myr), "RM 0.05")


if __name__ == "__main__":
    unittest.main()
