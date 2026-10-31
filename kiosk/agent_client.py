"""Invoke an AgentCore harness and collect what the agent did: the tools it called and what it said."""

import dataclasses
import datetime
import json
import uuid

import boto3


class AgentStreamError(RuntimeError):
    """The harness reported an error mid-stream (throttling, validation, a model or tool failure)."""


@dataclasses.dataclass
class AgentRun:
    harness_arn: str
    session_id: str
    prompt: str
    started_at: str
    ended_at: str = ""
    tool_calls: list = dataclasses.field(default_factory=list)  # [{"name": ..., "input": ...}]
    text: str = ""
    stop_reason: str = ""

    @property
    def finished_normally(self):
        return self.stop_reason == "end_turn"

    def tool_names(self):
        return [call["name"] for call in self.tool_calls]


def ask(harness_arn, region, prompt, on_tool_call=None):
    """One invocation in a fresh session. Raises botocore errors to the caller, which names the fallback.
    on_tool_call(name, input) is called as each tool call completes, so a watcher sees the agent work."""
    client = boto3.client("bedrock-agentcore", region_name=region)
    run = AgentRun(harness_arn, f"kopi-{uuid.uuid4()}", prompt, _now())
    response = client.invoke_harness(
        harnessArn=harness_arn,
        runtimeSessionId=run.session_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
    )
    text, pending = [], {}  # pending: content block index -> tool call being streamed
    for event in response["stream"]:
        failure = next((name for name in event if name.endswith("Exception")), None)
        if failure:  # a failed run must never look like a short successful one
            raise AgentStreamError(f"{failure}: {event[failure].get('message', event[failure])}")
        if "contentBlockStart" in event:
            start = event["contentBlockStart"]
            tool_use = start.get("start", {}).get("toolUse")
            if tool_use:
                pending[start["contentBlockIndex"]] = {"name": tool_use.get("name", ""), "raw": ""}
                text = []  # text before a tool call is narration; the answer is what follows the last call
        elif "contentBlockDelta" in event:
            delta = event["contentBlockDelta"]
            if "text" in delta.get("delta", {}):
                text.append(delta["delta"]["text"])
            if "toolUse" in delta.get("delta", {}) and delta["contentBlockIndex"] in pending:
                pending[delta["contentBlockIndex"]]["raw"] += delta["delta"]["toolUse"].get("input", "")
        elif "contentBlockStop" in event and event["contentBlockStop"]["contentBlockIndex"] in pending:
            call = pending.pop(event["contentBlockStop"]["contentBlockIndex"])
            run.tool_calls.append({"name": call["name"], "input": _tool_input(call["raw"])})
            if on_tool_call:
                on_tool_call(call["name"], run.tool_calls[-1]["input"])
        elif "messageStop" in event:
            run.stop_reason = event["messageStop"].get("stopReason", "")
    run.text = "".join(text).strip()
    run.ended_at = _now()
    return run


def _tool_input(raw):
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {"unparsed": raw}


def _now():
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
