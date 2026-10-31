"""AgentCore Gateway target: the agent's two read-only tools over the menus bucket.

The Gateway calls this with the tool's arguments as the event and the tool name in the client
context. This module only fetches from S3 and hands the data to kiosk/investigation.py; it never
writes, and its IAM role could not if it tried. Every call, failed or not, logs one JSON line:
the receipt that joins the agent's request to the S3 versions it saw.
"""

import datetime
import json
import os
import pathlib

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from kiosk import investigation, menu

# Packaged as scenario.json at the root of the Lambda zip; tests point SCENARIO_FILE at the repo copy.
SCENARIO_FILE = os.environ.get("SCENARIO_FILE", str(pathlib.Path(__file__).resolve().parent.parent / "scenario.json"))
SCENARIO = menu.load_scenario(json.loads(pathlib.Path(SCENARIO_FILE).read_text()))
BUCKET = os.environ["MENU_BUCKET"]
LOG_KEY = "changes/maintenance-log.json"
s3 = boto3.client("s3")


def handler(event, context):
    custom = getattr(context.client_context, "custom", None) or {}
    tool = custom.get("bedrockAgentCoreToolName", "").split("___")[-1]  # Gateway prefixes "<target>___"
    receipt = {
        "tool": tool,
        "gateway_request_id": custom.get("bedrockAgentCoreAwsRequestId"),
        "lambda_request_id": context.aws_request_id,
        "inputs": event,
    }
    try:
        result = TOOLS[tool](event) if tool in TOOLS else {"error": f"unknown tool '{tool}'"}
    except investigation.ToolInputError as error:
        result = {"error": "invalid input", "detail": str(error)}
    except ClientError as error:  # e.g. AccessDenied: named, never mistaken for a missing menu
        result = {"error": "read failed", "detail": error.response["Error"]["Code"]}
    except (BotoCoreError, ValueError, KeyError) as error:  # network, or a menu that is not valid JSON
        result = {"error": "read failed", "detail": type(error).__name__}
    if "error" not in result:
        result["bucket"] = BUCKET  # so the agent's proposed recovery request names the real bucket
    receipt.update(state=result.get("state"), error=result.get("error"), version_ids=_version_ids(result))
    print(json.dumps({"receipt": receipt}))
    return result


def inspect_application(event):
    stall_id = investigation.require_stall(event.get("stall"), SCENARIO)
    current = _get(menu.menu_key(stall_id))
    if current is None:
        return investigation.inspect_application(stall_id, None, None, None, SCENARIO, _now())
    body, response = current
    return investigation.inspect_application(
        stall_id, body, response.get("VersionId"), _iso(response["LastModified"]), SCENARIO, _now()
    )


def inspect_recent_changes(event):
    stall_id = investigation.require_stall(event.get("stall"), SCENARIO)
    hours = investigation.require_window(event.get("hours"))
    key = menu.menu_key(stall_id)
    listing = s3.list_object_versions(Bucket=BUCKET, Prefix=key, MaxKeys=100)  # newest 100 is plenty
    entries = [v for v in listing.get("Versions", []) if v["Key"] == key]  # the prefix also matches key.bak
    entries += [{**m, "DeleteMarker": True} for m in listing.get("DeleteMarkers", []) if m["Key"] == key]
    # LastModified has 1 s resolution: on a tie, the current version goes first.
    entries.sort(key=lambda v: (v["LastModified"], v.get("IsLatest", False)), reverse=True)
    # One past the cap, so the tool can say "truncated" instead of guessing.
    versions = [_load_version(key, v) for v in entries[: investigation.MAX_VERSIONS + 1]]
    log = _get(LOG_KEY)
    return investigation.inspect_recent_changes(
        stall_id, versions, log[0] if log else {"changes": []}, hours, SCENARIO, _now()
    )


TOOLS = {"inspect_application": inspect_application, "inspect_recent_changes": inspect_recent_changes}


def _load_version(key, entry):
    if entry.get("DeleteMarker"):
        return {
            "version_id": entry["VersionId"],
            "last_modified": _iso(entry["LastModified"]),
            "is_latest": entry.get("IsLatest", False),
            "menu": None,
            "delete_marker": True,
        }
    body = s3.get_object(Bucket=BUCKET, Key=key, VersionId=entry["VersionId"])["Body"].read()
    return {
        "version_id": entry["VersionId"],
        "last_modified": _iso(entry["LastModified"]),
        "is_latest": entry.get("IsLatest", False),
        "menu": json.loads(body),
    }


def _get(key):
    """(parsed body, response), or None when the key does not exist. Other errors propagate."""
    try:
        response = s3.get_object(Bucket=BUCKET, Key=key)
    except ClientError as error:
        if error.response["Error"]["Code"] == "NoSuchKey":
            return None
        raise
    return json.loads(response["Body"].read()), response


def _version_ids(result):
    if "versions" in result:
        return [v["version_id"] for v in result["versions"]]
    return [result["version_id"]] if result.get("version_id") else []


def _now():
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def _iso(moment):
    return moment.astimezone(datetime.UTC).isoformat(timespec="seconds")
