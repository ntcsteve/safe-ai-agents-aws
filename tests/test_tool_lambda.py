"""The tool Lambda, against a stubbed S3 (botocore Stubber), so the
routing, error handling and receipts are tested where they live. Needs boto3:
  uv run --python 3.13 --with boto3 python -m unittest discover -s tests -t .
"""

import contextlib
import datetime
import importlib
import io
import json
import os
import pathlib
import types
import unittest

try:
    from botocore.stub import Stubber
except ImportError:  # the pure-logic tests still run without boto3
    Stubber = None

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUCKET = "kopi-test"
KEY = "menus/stall-kopi.json"
T0 = datetime.datetime(2026, 10, 28, 10, 5, tzinfo=datetime.UTC)


def context(tool, custom=True):
    client_context = types.SimpleNamespace(
        custom={"bedrockAgentCoreToolName": f"kiosk___{tool}", "bedrockAgentCoreAwsRequestId": "gw-1"}
        if custom
        else None
    )
    return types.SimpleNamespace(client_context=client_context, aws_request_id="lambda-1")


def body(data):
    from botocore.response import StreamingBody

    raw = json.dumps(data).encode()
    return StreamingBody(io.BytesIO(raw), len(raw))


@unittest.skipIf(Stubber is None, "boto3 not installed")
class ToolLambda(unittest.TestCase):
    def setUp(self):
        # Fake credentials and no profile: these tests must never reach a real account.
        self.saved_env = dict(os.environ)
        os.environ.pop("AWS_PROFILE", None)
        os.environ.update(
            AWS_ACCESS_KEY_ID="testing",
            AWS_SECRET_ACCESS_KEY="testing",
            AWS_DEFAULT_REGION="ap-southeast-2",
            MENU_BUCKET=BUCKET,
            SCENARIO_FILE=str(ROOT / "scenario" / "kopi.json"),
        )
        import kiosk.tool_lambda as tool_lambda

        self.tool = importlib.reload(tool_lambda)
        self.tool._now = lambda: "2026-10-28T10:10:00+00:00"  # just after T0, so the time window never ages out
        self.stub = Stubber(self.tool.s3)
        self.stub.activate()
        self.menu = json.loads((ROOT / "scenario" / "kopi.json").read_text())

    def tearDown(self):
        self.stub.deactivate()
        os.environ.clear()
        os.environ.update(self.saved_env)

    def call(self, tool, event, custom=True):
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            result = self.tool.handler(event, context(tool, custom))
        receipts = [
            json.loads(line)["receipt"] for line in printed.getvalue().splitlines() if line.startswith('{"receipt"')
        ]
        return result, receipts

    def kopi(self):
        return {"stall": "kopi", "title": "Kopi Corner", "items": self.menu["stalls"]["kopi"]["items"]}

    def test_inspect_application_reads_the_key_and_logs_a_receipt(self):
        self.stub.add_response(
            "get_object",
            {"Body": body(self.kopi()), "VersionId": "v1", "LastModified": T0},
            {"Bucket": BUCKET, "Key": KEY},
        )
        result, receipts = self.call("inspect_application", {"stall": "kopi"})
        self.assertEqual((result["state"], result["version_id"], result["bucket"]), ("HEALTHY", "v1", BUCKET))
        self.assertEqual(receipts[0]["tool"], "inspect_application")
        self.assertEqual(receipts[0]["gateway_request_id"], "gw-1")
        self.assertEqual(receipts[0]["version_ids"], ["v1"])

    def test_missing_object_is_reported(self):
        self.stub.add_client_error("get_object", service_error_code="NoSuchKey", http_status_code=404)
        result, _ = self.call("inspect_application", {"stall": "kopi"})
        self.assertEqual(result["state"], "MISSING")

    def test_access_denied_is_a_named_error_with_a_receipt(self):
        # Without s3:ListBucket, S3 answers a missing key with 403: that must not look like MISSING.
        self.stub.add_client_error("get_object", service_error_code="AccessDenied", http_status_code=403)
        result, receipts = self.call("inspect_application", {"stall": "kopi"})
        self.assertEqual(result, {"error": "read failed", "detail": "AccessDenied"})
        self.assertEqual(receipts[0]["error"], "read failed")

    def test_bad_input_is_rejected_without_touching_s3(self):
        result, receipts = self.call("inspect_application", {"stall": "bakso"})
        self.assertEqual(result["error"], "invalid input")
        self.assertIn("allowed: kopi, seduh", result["detail"])
        self.assertEqual(len(receipts), 1)

    def test_unknown_tool_and_missing_context_are_named(self):
        result, _ = self.call("drop_tables", {})
        self.assertEqual(result["error"], "unknown tool 'drop_tables'")
        result, _ = self.call("inspect_application", {"stall": "kopi"}, custom=False)
        self.assertEqual(result["error"], "unknown tool ''")

    def test_a_malformed_stall_still_leaves_a_receipt(self):
        result, receipts = self.call("inspect_application", {"stall": ["kopi"]})
        self.assertEqual(result["error"], "invalid input")
        self.assertEqual(len(receipts), 1)

    def test_recent_changes_orders_same_second_versions_latest_first(self):
        listing = {
            "Versions": [
                {"Key": KEY, "VersionId": "old", "LastModified": T0, "IsLatest": False},
                {"Key": KEY, "VersionId": "new", "LastModified": T0, "IsLatest": True},
                {"Key": "menus/stall-kopi.json.bak", "VersionId": "other", "LastModified": T0, "IsLatest": True},
            ],
        }
        self.stub.add_response("list_object_versions", listing, {"Bucket": BUCKET, "Prefix": KEY, "MaxKeys": 100})
        for version_id in ("new", "old"):
            self.stub.add_response(
                "get_object", {"Body": body(self.kopi())}, {"Bucket": BUCKET, "Key": KEY, "VersionId": version_id}
            )
        self.stub.add_client_error("get_object", service_error_code="NoSuchKey", http_status_code=404)  # no log yet
        result, receipts = self.call("inspect_recent_changes", {"stall": "kopi", "hours": 24})
        self.assertEqual([v["version_id"] for v in result["versions"]], ["new", "old"])  # .bak excluded
        self.assertEqual(receipts[0]["version_ids"], ["new", "old"])


if __name__ == "__main__":
    unittest.main()
