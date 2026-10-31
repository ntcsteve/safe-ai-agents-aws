"""The commands, one test per named outcome. AWS and the agent are faked at their seams; the menus are
real menu logic over an in-memory store."""

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import pathlib
import types
import unittest
from unittest import mock

from kiosk import commands, config, menu

ROOT = pathlib.Path(__file__).resolve().parent.parent
SETTINGS = config.Config(region="ap-southeast-2", model="au.anthropic.claude-sonnet-4-6", scenario="kopi")
OUTPUTS = {
    "bucket": "kopi-test",
    "region": "ap-southeast-2",
    "tools_role_arn": "arn:aws:iam::000000000000:role/kopi-tools",
    "investigator_role_arn": "arn:aws:iam::000000000000:role/kopi-investigator",
    "gateway_arn": "arn:aws:bedrock-agentcore:ap-southeast-2:000000000000:gateway/kopi",
    "investigator_harness_arn": "arn:aws:bedrock-agentcore:ap-southeast-2:000000000000:harness/kopi",
    "tools_function": "kopi-tools",
    "url": "https://example.cloudfront.net/",
}


def _load_kopi():
    """The ./kopi script is a uv script without a .py name; load it as a module for its Demo."""
    loader = importlib.machinery.SourceFileLoader("kopi_script", str(ROOT / "kopi"))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader("kopi_script", loader))
    loader.exec_module(module)
    return module


KOPI = _load_kopi()


class FakeStore:
    """The S3 menu store in memory: healthy menus (seed plus the promo) and the change log."""

    def __init__(self, scenario):
        self.menus = menu.apply_promo(menu.seed_menus(scenario), scenario)
        self.log = {"changes": []}

    def read_menus(self, scenario):
        return json.loads(json.dumps(self.menus))

    def write_menu(self, key, stall_menu):
        self.menus[key] = stall_menu
        return f"v{len(self.log['changes']) + 1}"

    def read_log(self):
        return json.loads(json.dumps(self.log))

    def write_log(self, log):
        self.log = log

    def recent_versions(self, key, count):
        return []


def make_demo(broken=False):
    demo = KOPI.Demo(SETTINGS, KOPI._load_scenario("kopi"))
    demo.__dict__["store"] = FakeStore(demo.scenario)  # cached_property: the fake stands in for S3
    demo.__dict__["outputs"] = OUTPUTS
    if broken:
        demo.store.menus = menu.apply_fault(demo.store.menus, demo.scenario)
    return demo


def quietly(call, *args):
    """Run a command, returning (result or the exception raised, printed text)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            result = call(*args)
        except Exception as error:  # the refusals are the behaviour under test
            result = error
    return result, out.getvalue()


def fake_iam(allowing):
    """An IAM client whose simulator allows exactly the (role name, action) pairs named."""

    def simulate(PolicySourceArn, ActionNames, ResourceArns):
        allowed = (PolicySourceArn.split("/")[-1], ActionNames[0]) in allowing
        return {"EvaluationResults": [{"EvalDecision": "allowed" if allowed else "implicitDeny"}]}

    return types.SimpleNamespace(simulate_principal_policy=simulate)


class Safe(unittest.TestCase):
    CONTROLS = {("kopi-tools", "s3:GetObject"), ("kopi-investigator", "bedrock-agentcore:InvokeGateway")}

    def safe(self, allowing):
        with mock.patch.object(commands.boto3, "client", return_value=fake_iam(allowing)):
            return quietly(commands.cmd_safe, make_demo())

    def test_boundary_holds_when_only_the_controls_are_allowed(self):
        result, printed = self.safe(self.CONTROLS)
        self.assertEqual(result, 0)
        self.assertIn("BOUNDARY HOLDS", printed)

    def test_a_write_allowed_breaks_the_boundary(self):
        result, printed = self.safe(self.CONTROLS | {("kopi-tools", "s3:PutObject")})
        self.assertEqual(result, 1)
        self.assertIn("BOUNDARY BROKEN", printed)

    def test_the_investigator_reading_data_directly_breaks_the_boundary(self):
        result, _ = self.safe(self.CONTROLS | {("kopi-investigator", "s3:GetObject")})
        self.assertEqual(result, 1)

    def test_a_control_denied_breaks_the_boundary(self):
        """An allowed control proves the simulator is really being asked."""
        result, _ = self.safe(set())
        self.assertEqual(result, 1)


class Up(unittest.TestCase):
    def up(self, **check):
        with (
            mock.patch.object(KOPI.preflight, "check", **check) as preflight_check,
            mock.patch.object(KOPI, "_terraform") as terraform,
        ):
            demo = make_demo(broken=True)
            result, printed = quietly(KOPI.cmd_up, demo)
        return result, printed, terraform, preflight_check, demo

    def test_shows_the_target_deploys_then_seeds_healthy_menus(self):
        _, printed, terraform, _, demo = self.up(return_value="account ********3333, region ap-southeast-2")
        self.assertIn("Target: account ********3333, region ap-southeast-2", printed)
        self.assertEqual([call.args[0] for call in terraform.call_args_list], ["init", "apply"])
        self.assertIn("HEALTHY", printed)
        self.assertIn("Next: ./kopi safe", printed)
        self.assertEqual([entry["change_id"] for entry in demo.store.log["changes"]], ["PROMO-7"])

    def test_a_failed_preflight_creates_nothing(self):
        refusal = KOPI.preflight.PreflightFailed("no working AWS credentials")
        result, _, terraform, _, _ = self.up(side_effect=refusal)
        self.assertIs(result, refusal)
        terraform.assert_not_called()


class Break(unittest.TestCase):
    def test_applies_the_change_and_logs_it(self):
        demo = make_demo()
        result, printed = quietly(demo.fault)
        self.assertEqual(result, 1)
        self.assertEqual(demo.store.log["changes"][-1]["change_id"], "MNT-142")
        self.assertIn("BROKEN", printed)

    def test_already_applied_refused(self):
        result, _ = quietly(make_demo(broken=True).fault)
        self.assertIsInstance(result, menu.ChangeRefused)


class Reset(unittest.TestCase):
    def test_restores_healthy_menus_and_a_fresh_log(self):
        demo = make_demo(broken=True)
        result, printed = quietly(demo.reset)
        self.assertEqual(result, 0)
        self.assertIn("HEALTHY", printed)
        self.assertEqual([entry["change_id"] for entry in demo.store.log["changes"]], ["PROMO-7"])


class Observe(unittest.TestCase):
    def run_of(self, stop_reason="end_turn"):
        return types.SimpleNamespace(
            text="FACTS: three prices are wrong",
            finished_normally=stop_reason == "end_turn",
            stop_reason=stop_reason,
            tool_calls=[],
            started_at="2026-10-28T14:46:31+00:00",
            ended_at="2026-10-28T14:46:44+00:00",
        )

    def observe(self, **agent):
        with (
            mock.patch.object(commands, "ask_agent", **agent),
            mock.patch.object(commands, "print_receipts") as receipts,
            mock.patch.object(commands, "_invoke_tool", return_value={}) as tools,
        ):
            result, printed = quietly(commands.cmd_observe, make_demo(broken=True))
        return result, printed, receipts, tools

    def test_shows_the_answer_then_the_receipts_then_the_state(self):
        result, printed, receipts, tools = self.observe(return_value=self.run_of())
        self.assertEqual(result, 0)
        receipts.assert_called_once()
        tools.assert_not_called()
        self.assertLess(printed.index("FACTS"), printed.index("check now"))
        self.assertIn("BROKEN", printed)

    def test_agent_stopped_early_is_named(self):
        _, printed, _, _ = self.observe(return_value=self.run_of("max_tokens"))
        self.assertIn("AGENT STOPPED EARLY", printed)

    def test_agent_down_shows_the_same_evidence_without_it(self):
        result, printed, receipts, tools = self.observe(side_effect=commands.AgentUnavailable("timeout"))
        self.assertEqual(result, 0)
        self.assertIn("AGENT UNAVAILABLE: timeout", printed)
        self.assertEqual(tools.call_count, 3)  # both stalls, then the recent changes
        receipts.assert_not_called()


if __name__ == "__main__":
    unittest.main()
