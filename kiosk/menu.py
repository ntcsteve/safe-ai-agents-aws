"""Kopi Kiosk's menu logic: the expected menu, the MNT-142 fault, and how prices read.

No AWS, no I/O. Everything that varies by event lives in the scenario file, so a new city or
menu is a new scenario, not a code change. Prices are integers in the currency's smallest
displayed unit (value x 10^decimals), so money never touches a float.
"""

import copy
import dataclasses
import enum
import json


class ScenarioError(ValueError):
    """The scenario file is unusable. The message names the field."""


class ChangeRefused(RuntimeError):
    """A change would land on a menu that is not in the state it was written for."""


class Health(enum.StrEnum):
    HEALTHY = "HEALTHY"
    BROKEN = "BROKEN"


@dataclasses.dataclass(frozen=True)
class CheckResult:
    state: Health
    problems: list[str]


def menu_key(stall_id):
    return f"menus/stall-{stall_id}.json"


# --- Scenario -------------------------------------------------------------------------------


def load_scenario(raw):
    """Validate a parsed scenario and return it. Raises ScenarioError naming the first bad field."""
    for field in ("name", "title", "currency", "stalls", "fault", "promo"):
        if field not in raw:
            raise ScenarioError(f"scenario is missing '{field}'")
    _validate_currency(raw["currency"])
    _validate_tolerance(raw.get("impact_tolerance_minutes"))
    _validate_stalls(raw["stalls"])
    _validate_fault(raw["fault"], raw["stalls"])
    _validate_promo(raw["promo"], raw["fault"], raw["stalls"])
    if "images" in raw:
        _validate_images(raw["images"], raw["stalls"])
    return raw


def _validate_currency(currency):
    for field in ("code", "symbol", "thousands", "decimal", "decimals"):
        if field not in currency:
            raise ScenarioError(f"currency is missing '{field}'")


def _validate_tolerance(minutes):
    # The service resilience indicator: how long the menu may stay wrong after a bad change.
    if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes <= 0:
        raise ScenarioError(f"impact_tolerance_minutes must be a positive whole number, got {minutes!r}")


def _validate_stalls(stalls):
    if not stalls:
        raise ScenarioError("stalls is empty")
    for stall_id, stall in stalls.items():
        if not stall.get("items"):
            raise ScenarioError(f"stalls.{stall_id}.items is empty")
        for item in stall["items"]:
            _require_price(item.get("price"), f"stalls.{stall_id}.{item.get('id')}.price")


def _validate_fault(fault, stalls):
    stall = _require_stall(stalls, fault.get("stall"), "fault.stall")
    if not fault.get("items"):
        raise ScenarioError("fault.items is empty")
    for item_id in fault["items"]:
        if item_id not in _item_ids(stall):
            raise ScenarioError(f"fault.items names '{item_id}', which is not on stall '{fault['stall']}'")
    if not isinstance(fault.get("factor"), int) or fault["factor"] <= 1:
        raise ScenarioError("fault.factor must be an integer greater than 1")


def _validate_promo(promo, fault, stalls):
    for field in ("change_id", "title", "label"):  # checked here, so a gap is never found after a write
        if not promo.get(field):
            raise ScenarioError(f"promo.{field} is missing")
    stall = _require_stall(stalls, promo.get("stall"), "promo.stall")
    # This proves scoping: rolling back the fault's key must leave the promo alone.
    if promo["stall"] == fault["stall"]:
        raise ScenarioError("promo.stall must differ from fault.stall, so the rollback scope excludes it")
    if promo.get("item") not in _item_ids(stall):
        raise ScenarioError(f"promo.item '{promo.get('item')}' is not on stall '{promo['stall']}'")
    _require_price(promo.get("price"), "promo.price")


def _validate_images(images, stalls):
    """Optional, presentation-only: file names under site/img/, keyed by stall and item."""
    for stall_id in images.get("stalls", {}):
        if stall_id not in stalls:
            raise ScenarioError(f"images.stalls names '{stall_id}', which is not a stall")
    all_items = set().union(*(_item_ids(stall) for stall in stalls.values()))
    for item_id in images.get("items", {}):
        if item_id not in all_items:
            raise ScenarioError(f"images.items names '{item_id}', which is not on any stall")


# --- Changes --------------------------------------------------------------------------------


def seed_menus(scenario):
    """One menu object per stall, at base prices."""
    return {
        menu_key(stall_id): {
            "stall": stall_id,
            "title": stall["title"],
            "currency": scenario["currency"],
            "items": [{"id": i["id"], "name": i["name"], "price": i["price"]} for i in stall["items"]],
        }
        for stall_id, stall in scenario["stalls"].items()
    }


def apply_promo(menus, scenario):
    """PROMO-7, the approved change: one item at its promo price. Returns new menus."""
    promo = scenario["promo"]
    changed = copy.deepcopy(menus)
    _find_item(changed[menu_key(promo["stall"])], promo["item"])["price"] = promo["price"]
    return changed


def apply_fault(menus, scenario):
    """MNT-142, the bad change: multiply the named items by the factor. Returns new menus.

    All or nothing: every named item must be at its expected price first, so the fault
    never stacks (x100 twice) and never lands on a menu that is already off.
    """
    fault = scenario["fault"]
    expected = expected_prices(scenario)[fault["stall"]]
    changed = copy.deepcopy(menus)
    targets = [_find_item(changed[menu_key(fault["stall"])], item_id) for item_id in fault["items"]]
    for item in targets:
        if item["price"] != expected[item["id"]]:
            raise ChangeRefused(
                f"{fault['change_id']} refused: {item['id']} is {item['price']}, "
                f"expected {expected[item['id']]} before the change. Run reset first."
            )
    for item in targets:
        item["price"] *= fault["factor"]
    return changed


# --- Check ----------------------------------------------------------------------------------


def check_menus(menus, scenario):
    """Compare menus (key -> menu, or None if the object is missing) with the expected state.

    Expected = seed plus the approved promo. Any difference is a problem; any problem is BROKEN.
    """
    problems = []
    for stall_id, expected in expected_prices(scenario).items():
        current = menus.get(menu_key(stall_id))
        if current is None:
            problems.append(f"missing {menu_key(stall_id)}")
        else:
            problems += _stall_problems(stall_id, current, expected, scenario)
    return CheckResult(Health.BROKEN if problems else Health.HEALTHY, problems)


def _stall_problems(stall_id, current, expected, scenario):
    actual = {i["id"]: i["price"] for i in current["items"]}
    problems = []
    for item_id, want in expected.items():
        if item_id not in actual:
            problems.append(f"{stall_id}/{item_id} is missing")
        elif actual[item_id] != want:
            note = _promo_note(stall_id, item_id, actual[item_id], scenario)
            problems.append(f"{stall_id}/{item_id} is {actual[item_id]}, expected {want}{note}")
    problems += [f"{stall_id}/{item_id} is not on the expected menu" for item_id in actual if item_id not in expected]
    return problems


def _promo_note(stall_id, item_id, price, scenario):
    """Name the cause when the promo item still has its base price: the promo never landed."""
    promo = scenario["promo"]
    is_promo_item = (stall_id, item_id) == (promo["stall"], promo["item"])
    if is_promo_item and price == _base_price(scenario, stall_id, item_id):
        return f" ({promo['change_id']} not applied)"
    return ""


# --- Presentation ---------------------------------------------------------------------------


def format_price(amount, currency):
    """25000 in IDR -> 'Rp 25.000'; 123450 in a 2-decimal currency -> 'RM 1,234.50'."""
    decimals = currency["decimals"]
    whole, fraction = divmod(amount, 10**decimals)
    text = f"{whole:,}".replace(",", currency["thousands"])
    if decimals:
        text += currency["decimal"] + str(fraction).zfill(decimals)
    return currency["symbol"] + text


def encode_menu(menu):
    """Stable bytes for one menu object, so a reseed writes identical content."""
    return (json.dumps(menu, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


# --- Helpers --------------------------------------------------------------------------------


def expected_prices(scenario):
    """stall_id -> item_id -> price, with the promo applied."""
    expected = {
        stall_id: {i["id"]: i["price"] for i in stall["items"]} for stall_id, stall in scenario["stalls"].items()
    }
    promo = scenario["promo"]
    expected[promo["stall"]][promo["item"]] = promo["price"]
    return expected


def _base_price(scenario, stall_id, item_id):
    return next(i["price"] for i in scenario["stalls"][stall_id]["items"] if i["id"] == item_id)


def _find_item(stall_menu, item_id):
    for item in stall_menu["items"]:
        if item["id"] == item_id:
            return item
    raise ChangeRefused(f"{stall_menu['stall']}/{item_id} is missing. Run reset first.")


def _item_ids(stall):
    return {i["id"] for i in stall["items"]}


def _require_stall(stalls, stall_id, field):
    if stall_id not in stalls:
        raise ScenarioError(f"{field} names '{stall_id}', which is not a stall")
    return stalls[stall_id]


def _require_price(price, field):
    # bool is an int in Python; a price of True is a typo, not money.
    if not isinstance(price, int) or isinstance(price, bool) or price <= 0:
        raise ScenarioError(f"{field} must be a positive integer, got {price!r}")
