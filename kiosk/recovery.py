"""Recovery, as pure logic: which version to restore and its scope gate, the recovery clock, the key's history."""

import datetime

from kiosk import investigation, menu


class RecoveryRefused(RuntimeError):
    """The restore would not do exactly what was asked. Nothing was written."""


def choose_restore(menus, versions, stall_id, scenario):
    """The newest good version of the broken menu, or RecoveryRefused. Scope is the safety promise: this
    restores one object, so it refuses unless that one object is exactly what is wrong.
    versions: [{version_id, last_modified, size, menu or None}], newest first."""
    key = menu.menu_key(stall_id)
    broken = [menu.menu_key(stall) for stall in scenario["stalls"] if _is_broken(menus, stall, scenario)]
    if broken != [key]:
        raise RecoveryRefused(
            f"WRONG SCOPE: this restores only {key}, but the broken menus are {broken}. "
            "Nothing written. Next: ./kopi reset restores every menu"
        )
    for version in versions:
        if _version_label(version, stall_id, scenario) == "GOOD":
            return version
    raise RecoveryRefused(
        f"NO GOOD VERSION: none of the last {len(versions)} versions of {key} is healthy. "
        "Nothing written. Next: ./kopi reset"
    )


def changed_keys(before, after):
    """The keys whose version differs between two snapshots, in order."""
    return [key for key in after if before.get(key) != after[key]]


def price_changes(current_menu, restored_menu):
    """[(item name, price now, price restored)] for each item the restore changes."""
    now = {item["id"]: item["price"] for item in (current_menu or {}).get("items", [])}
    return [
        (item["name"], now.get(item["id"]), item["price"])
        for item in restored_menu["items"]
        if now.get(item["id"]) != item["price"]
    ]


def _is_broken(menus, stall_id, scenario):
    stall_menu = menus.get(menu.menu_key(stall_id))
    return investigation.inspect_application(stall_id, stall_menu, None, None, scenario, None)["state"] != "HEALTHY"


def recovery_clock(log, change_id, now, tolerance_minutes):
    """'3 min 12 s after MNT-142: within the 10 min tolerance', measured from the fault's logged time.
    None when the log has no usable entry for the change (e.g. after a reset): no clock, no claim."""
    elapsed = seconds_since(log, change_id, now)
    if elapsed is None:
        return None
    minutes, seconds = divmod(elapsed, 60)
    verdict = "within" if elapsed <= tolerance_minutes * 60 else "OVER"
    return f"{minutes} min {seconds} s after {change_id}: {verdict} the {tolerance_minutes} min tolerance"


def restore_id(change_id):
    """The log's name for the restore of a change, which is what stops its clock."""
    return f"RESTORE-{change_id}"


def applied_at(log, change_id):
    """When the change was last logged as applied, or None."""
    applied = [entry.get("applied_at") for entry in log.get("changes", []) if entry.get("change_id") == change_id]
    return applied[-1] if applied else None


def seconds_since(log, change_id, now):
    """Whole seconds from the change's logged time to its restore, or to now if it is still live. None without a
    usable entry. A restore from before the latest fault belongs to an earlier run, so it does not stop this one."""
    try:
        started = datetime.datetime.fromisoformat(applied_at(log, change_id))
        ended = datetime.datetime.fromisoformat(now)
        restored = applied_at(log, restore_id(change_id))
        if restored and datetime.datetime.fromisoformat(restored) >= started:
            ended = datetime.datetime.fromisoformat(restored)
    except (TypeError, ValueError):
        return None
    return int((ended - started).total_seconds())


def is_good(version, stall_id, scenario):
    return _version_label(version, stall_id, scenario) == "GOOD"


def _version_label(version, stall_id, scenario):
    if version["menu"] is None:
        return "DELETED"
    state = investigation.inspect_application(
        stall_id, version["menu"], version["version_id"], version["last_modified"], scenario, version["last_modified"]
    )["state"]
    return "GOOD" if state == "HEALTHY" else "FAULTED"


def history_lines(versions, stall_id, scenario):
    """One line per version (newest first): GOOD, FAULTED or DELETED against the expected menu.
    versions: [{version_id, last_modified, size, menu or None}]."""
    lines = []
    for position, version in enumerate(versions):
        label = _version_label(version, stall_id, scenario)
        note = "  (kept in history, not deleted)" if position and label == "FAULTED" else ""
        when = "now" if position == 0 else "before"
        lines.append(
            f"  {when:<7} {label:<8} {version['size']:>3} B  {version['last_modified']}  {version['version_id']}{note}"
        )
    return lines
