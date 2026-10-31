"""The agent's two read-only tools, as pure functions: observations in, observations out.

They never diagnose. They report what is there, what was expected, what changed, and what they
could not see, so the agent's findings rest on evidence a human can check. No AWS here: the
Lambda adapter fetches from S3 and passes the data in.

Everything returned crosses into the model's context, so it is treated like output to any other
interpreter: only allowlisted fields, only ids that match a strict pattern, and never free text
from the bucket (menu names, change titles) that could carry instructions.
"""

import datetime
import json
import re

from kiosk import menu

MAX_HOURS = 168  # one week: enough for any incident window, bounded for the agent
MAX_VERSIONS = 20  # newest in-window versions; older ones are reported as truncated
MAX_ITEMS = 50  # items per menu reported to the agent
MAX_LOG_ENTRIES = 10  # newest maintenance-log entries for the key
MAX_RESPONSE_BYTES = 8000  # keeps each tool result small in the model's context

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")  # item ids
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9._\-:+/]{1,64}$")  # change ids, keys, version ids, times
LOG_FIELDS = ("change_id", "key", "version_id", "applied_at")


class ToolInputError(ValueError):
    """The agent asked for something outside the tool's contract. The message names the limit."""


def require_stall(stall_id, scenario):
    if stall_id not in scenario["stalls"]:
        allowed = ", ".join(sorted(scenario["stalls"]))
        raise ToolInputError(f"unknown stall '{stall_id}'; allowed: {allowed}")
    return stall_id


def require_window(hours):
    # bool is an int in Python; "hours=True" is a malformed call, not one hour.
    if not isinstance(hours, int) or isinstance(hours, bool) or not 1 <= hours <= MAX_HOURS:
        raise ToolInputError(f"hours must be a whole number from 1 to {MAX_HOURS}")
    return hours


# --- inspect_application ----------------------------------------------------------------------


def inspect_application(stall_id, stall_menu, version_id, last_modified, scenario, observed_at):
    """The stall's current menu against the expected one, item by item (ids and prices only)."""
    result = {
        "stall": stall_id,
        "key": menu.menu_key(stall_id),
        "observed_at": observed_at,
        "version_id": version_id,
        "last_modified": last_modified,
        "truncated": False,
    }
    if stall_menu is None:
        return {**result, "state": "MISSING", "items": []}

    expected = menu.expected_prices(scenario)[stall_id]
    actual = {item.get("id"): item.get("price") for item in stall_menu.get("items", [])}
    items = [_expected_item(item_id, want, actual) for item_id, want in expected.items()]
    unexpected = [item_id for item_id in actual if item_id not in expected]
    items += [
        {"id": _safe_id(item_id), "price": _safe_price(actual[item_id]), "status": "UNEXPECTED"}
        for item_id in unexpected
    ]

    state = "HEALTHY" if all(i["status"] == "OK" for i in items) else "BROKEN"
    result.update(state=state, items=items[:MAX_ITEMS], truncated=len(items) > MAX_ITEMS)
    return _fit(result, "items")


def _expected_item(item_id, want, actual):
    if item_id not in actual:
        return {"id": item_id, "status": "MISSING", "expected": want}
    price = _safe_price(actual[item_id])
    return {"id": item_id, "price": price, "expected": want, "status": "OK" if price == want else "WRONG"}


# --- inspect_recent_changes -------------------------------------------------------------------


def inspect_recent_changes(stall_id, versions, log, hours, scenario, now):
    """Versions of the stall's menu in the window (newest first), the price changes between
    consecutive versions, and the maintenance-log entries for that key in the same window.

    versions: newest first, each {version_id, last_modified, is_latest, menu, delete_marker?}.
    One version older than the window is kept as the baseline, so the oldest change has a "from".
    """
    key = menu.menu_key(stall_id)
    since = _parse(now) - datetime.timedelta(hours=hours)
    in_window = [v for v in versions if _parse(v["last_modified"]) >= since]
    baseline = next((v for v in versions if _parse(v["last_modified"]) < since), None)

    truncated = len(in_window) > MAX_VERSIONS
    # Cap the window first, then add the baseline, so a full window still has its "from".
    chosen = in_window[:MAX_VERSIONS] + ([baseline] if baseline and not truncated else [])
    reported = [
        _describe_version(v, older, is_baseline=v is baseline)
        for v, older in zip(chosen, chosen[1:] + [None], strict=True)
    ]

    entries, malformed = _log_entries(log, key, since)
    limitations = [
        f"At most {MAX_VERSIONS} versions are shown, newest first.",
        "The maintenance log is written by operators: a change made outside it shows in versions but not in the log.",
        "Versions show what changed, not who made the change; S3 data events in CloudTrail would.",
    ]
    if malformed:
        limitations.append(f"{malformed} maintenance-log entries were malformed and are not shown.")
    result = {
        "stall": stall_id,
        "key": key,
        "window_hours": hours,
        "observed_at": now,
        "versions": reported,
        "maintenance_log": entries[:MAX_LOG_ENTRIES],
        "truncated": truncated or len(entries) > MAX_LOG_ENTRIES,
        "limitations": limitations,
    }
    return _fit(_fit(result, "maintenance_log"), "versions")


def _describe_version(current, older, is_baseline):
    described = {
        "version_id": _safe_token(current["version_id"]),
        "last_modified": _safe_token(current["last_modified"]),
        "is_latest": bool(current.get("is_latest")),
    }
    if is_baseline:
        described["baseline"] = True
    if current.get("delete_marker"):
        described.update(delete_marker=True, change="deleted")
    elif older is not None and older.get("delete_marker"):
        described["change"] = "restored"
    elif older is not None:
        described["price_changes"] = _price_changes(older["menu"], current["menu"])
    return described


def _price_changes(older_menu, newer_menu):
    if older_menu is None or newer_menu is None:
        return []
    before = {i.get("id"): i.get("price") for i in older_menu.get("items", [])}
    return [
        {"item": _safe_id(i.get("id")), "from": _safe_price(before.get(i.get("id"))), "to": _safe_price(i.get("price"))}
        for i in newer_menu.get("items", [])
        if before.get(i.get("id")) != i.get("price")
    ][:MAX_ITEMS]


def _log_entries(log, key, since):
    """Entries for this key inside the window, newest first, allowlisted fields only.
    Returns (entries, count of malformed entries skipped)."""
    entries, malformed = [], 0
    for entry in log.get("changes", []):
        if entry.get("key") != key:
            continue
        try:
            in_window = _parse(entry["applied_at"]) >= since
        except (KeyError, TypeError, ValueError):  # no time, not a string, naive, or not ISO 8601
            malformed += 1
            continue
        if in_window:
            entries.append({field: _safe_token(entry.get(field)) for field in LOG_FIELDS})
    entries.sort(key=lambda e: e["applied_at"], reverse=True)
    return entries, malformed


# --- Safety and size --------------------------------------------------------------------------


def _safe_id(value):
    return value if isinstance(value, str) and ID_PATTERN.match(value) else "<invalid id>"


def _safe_token(value):
    return value if isinstance(value, str) and TOKEN_PATTERN.match(value) else None


def _safe_price(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _fit(result, list_field):
    """Drop the last entries of one list until the result fits MAX_RESPONSE_BYTES, and say so."""
    while len(json.dumps(result)) > MAX_RESPONSE_BYTES and len(result[list_field]) > 1:
        result[list_field].pop()
        result["truncated"] = True
    return result


def _parse(timestamp):
    """ISO 8601 with a timezone. A naive time is rejected: it cannot be compared safely."""
    moment = datetime.datetime.fromisoformat(timestamp)
    if moment.tzinfo is None:
        raise ValueError(f"timestamp without a timezone: {timestamp}")
    return moment
