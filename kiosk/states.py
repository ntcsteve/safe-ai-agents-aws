"""The one state table: what is true now, and what each command may do about it.

Pure logic, no AWS. Every command in every state has a defined outcome, so running something out of
order answers with the next step instead of doing something surprising.
"""

import enum


class State(enum.StrEnum):
    NOT_DEPLOYED = "NOT DEPLOYED"
    REGION_CHANGED = "REGION CHANGED"
    CONFIG_NOT_APPLIED = "CONFIG NOT APPLIED"
    HEALTHY = "HEALTHY"
    BROKEN = "BROKEN"


RUNNING = {State.HEALTHY, State.BROKEN}

ALLOWED = {
    "status": set(State),
    # Not "down": it runs before this table, because the way out must never be blocked.
    "up": set(State) - {State.REGION_CHANGED},
    "clumio": {State.HEALTHY},  # never while broken: a sync taken now would capture the bad change
    "safe": RUNNING,
    "break": {State.HEALTHY},
    "observe": {State.BROKEN},
    "recover": {State.BROKEN},
    "check": RUNNING,
    "reset": RUNNING,
    "eval": {State.HEALTHY},
}

NEXT = {
    State.NOT_DEPLOYED: "./kopi up",
    State.REGION_CHANGED: "./kopi down, then ./kopi up",
    State.CONFIG_NOT_APPLIED: "./kopi up",
    State.HEALTHY: "./kopi break",
    State.BROKEN: "./kopi observe, then ./kopi recover",
}


def read_state(deployed, settings, hashes, read_is_healthy):
    """The state now. deployed: Terraform's outputs, or None. read_is_healthy runs only when the stack
    matches the files, so the menus are never judged against a scenario that is not live."""
    if deployed is None:
        return State.NOT_DEPLOYED
    if deployed["region"] != settings.region:
        return State.REGION_CHANGED
    if differences(deployed, settings, hashes):
        return State.CONFIG_NOT_APPLIED
    return State.HEALTHY if read_is_healthy() else State.BROKEN


def differences(deployed, settings, hashes):
    """What differs between the stack and the files, in the reader's words."""
    compared = (
        ("MODEL", deployed["model"], settings.model),
        ("SCENARIO", deployed["scenario"], settings.scenario),
        ("the scenario file", deployed["scenario_hash"], hashes["scenario_hash"]),
        ("the agent prompt", deployed["prompt_hash"], hashes["prompt_hash"]),
    )
    return [name for name, live, wanted in compared if live != wanted]


def refusal(command, state, detail):
    """Why this command cannot run now, and what to run instead."""
    if state is State.CONFIG_NOT_APPLIED:
        return f"CONFIG NOT APPLIED: {detail} differs from what is deployed. Next: {NEXT[state]}"
    if state is State.REGION_CHANGED:
        return f"REGION CHANGED: {detail}. Next: {NEXT[state]}"
    if state is State.NOT_DEPLOYED:
        return f"NOT DEPLOYED. Next: {NEXT[state]}"
    return f"NOT NOW: ./kopi {command} does not apply while the menus are {state}. Next: {NEXT[state]}"
