"""Recovery, as pure logic: the recovery clock and the key's version history."""

import datetime

from kiosk import investigation


def recovery_clock(log, change_id, now, tolerance_minutes):
    """'3 min 12 s after MNT-142: within the 10 min tolerance', measured from the fault's logged time.
    None when the log has no usable entry for the change (e.g. after a reset): no clock, no claim."""
    elapsed = seconds_since(log, change_id, now)
    if elapsed is None:
        return None
    minutes, seconds = divmod(elapsed, 60)
    verdict = "within" if elapsed <= tolerance_minutes * 60 else "OVER"
    return f"{minutes} min {seconds} s after {change_id}: {verdict} the {tolerance_minutes} min tolerance"


def applied_at(log, change_id):
    """When the change was last logged as applied, or None."""
    applied = [entry.get("applied_at") for entry in log.get("changes", []) if entry.get("change_id") == change_id]
    return applied[-1] if applied else None


def seconds_since(log, change_id, now):
    """Whole seconds from the change's logged time to now, or None without a usable entry."""
    try:
        elapsed = datetime.datetime.fromisoformat(now) - datetime.datetime.fromisoformat(applied_at(log, change_id))
    except (TypeError, ValueError):
        return None
    return int(elapsed.total_seconds())


def faulted_version_ids(versions, stall_id, scenario):
    """The IDs of the versions that hold the fault."""
    return [v["version_id"] for v in versions if _version_label(v, stall_id, scenario) == "FAULTED"]


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
