"""The commands, one test per named outcome. AWS and the agent are faked at their seams; the menus are
real menu logic over an in-memory store."""

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import pathlib
import subprocess
import types
import unittest
from unittest import mock

from kiosk import commands, config, menu, states

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
    "model": "au.anthropic.claude-sonnet-4-6",
    "scenario": "kopi",
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
        self.versions = []
        self.writes = []

    def read_menus(self, scenario):
        return json.loads(json.dumps(self.menus))

    def write_menu(self, key, stall_menu):
        self.menus[key] = stall_menu
        self.writes.append(key)
        return f"v{len(self.log['changes']) + 1}"

    def read_log(self):
        return json.loads(json.dumps(self.log))

    def write_log(self, log):
        self.log = log

    def recent_versions(self, key, count):
        return self.versions[:count]


def make_demo(broken=False):
    demo = KOPI.Demo(SETTINGS, KOPI._load_scenario("kopi"))
    demo.__dict__["store"] = FakeStore(demo.scenario)  # cached_property: the fake stands in for S3
    demo.__dict__["deployed"] = OUTPUTS
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
    def up(self, before=states.State.NOT_DEPLOYED, **check):
        check = check or {"return_value": "account ********3333, region ap-southeast-2"}
        with (
            mock.patch.object(KOPI.preflight, "check", **check),
            mock.patch.object(KOPI, "_terraform") as terraform,
            mock.patch.object(KOPI.Demo, "state", return_value=before),
            mock.patch.object(KOPI.Demo, "forget_deployed"),  # the fakes stand in for what up would re-read
        ):
            demo = make_demo(broken=True)
            result, printed = quietly(KOPI.cmd_up, demo)
        return result, printed, terraform, demo

    def test_shows_the_target_deploys_then_seeds_healthy_menus(self):
        _, printed, terraform, demo = self.up()
        self.assertIn("Target: account ********3333, region ap-southeast-2", printed)
        self.assertEqual([call.args[0] for call in terraform.call_args_list], ["init", "apply"])
        self.assertIn("HEALTHY", printed)
        self.assertIn("Next: ./kopi safe", printed)
        self.assertEqual([entry["change_id"] for entry in demo.store.log["changes"]], ["PROMO-7"])

    def test_a_changed_config_reseeds(self):
        _, printed, _, _ = self.up(before=states.State.CONFIG_NOT_APPLIED)
        self.assertIn("RESET", printed)

    def test_rerunning_mid_run_leaves_a_broken_menu_broken(self):
        _, printed, _, demo = self.up(before=states.State.BROKEN)
        self.assertNotIn("RESET", printed)
        self.assertIn("BROKEN", printed)
        self.assertEqual(demo.store.log["changes"], [])

    def test_a_failed_preflight_creates_nothing(self):
        refusal = KOPI.preflight.PreflightFailed("no working AWS credentials")
        result, _, terraform, _ = self.up(side_effect=refusal)
        self.assertIs(result, refusal)
        terraform.assert_not_called()


class Status(unittest.TestCase):
    def status(self, demo, state):
        with mock.patch.object(KOPI.Demo, "state", return_value=state):
            return quietly(KOPI.cmd_status, demo)

    def test_running_shows_the_menus_state_and_the_next_step(self):
        _, printed = self.status(make_demo(broken=True), states.State.BROKEN)
        self.assertIn("State: BROKEN", printed)
        self.assertIn("Next:  ./kopi observe", printed)

    def test_not_deployed_never_reads_the_menus(self):
        demo = make_demo()
        demo.__dict__["store"] = None
        _, printed = self.status(demo, states.State.NOT_DEPLOYED)
        self.assertIn("State: NOT DEPLOYED", printed)
        self.assertIn("Next:  ./kopi up", printed)


class Gate(unittest.TestCase):
    def test_a_command_refused_by_the_table_touches_nothing(self):
        observe = mock.Mock(return_value=0)
        with (
            mock.patch.object(KOPI, "_load_config", return_value=SETTINGS),
            mock.patch.object(KOPI.Demo, "state", return_value=states.State.HEALTHY),
            mock.patch.dict(KOPI.COMMANDS, {"observe": observe}),
        ):
            result, printed = quietly(KOPI.main, ["kopi", "observe"])
        self.assertEqual(result, 2)
        self.assertIn("Next: ./kopi break", printed)
        observe.assert_not_called()

    def test_the_table_and_the_commands_match_exactly(self):
        self.assertEqual(set(KOPI.COMMANDS), set(states.ALLOWED))


class VersionedStore(FakeStore):
    """FakeStore that keeps every write as a version, as the bucket does."""

    def __init__(self, scenario):
        super().__init__(scenario)
        self.history = {key: [json.loads(json.dumps(body))] for key, body in self.menus.items()}

    def write_menu(self, key, stall_menu):
        self.history[key].insert(0, json.loads(json.dumps(stall_menu)))
        return super().write_menu(key, stall_menu)

    def recent_versions(self, key, count):
        versions = [
            {"version_id": f"v{len(self.history[key]) - position}", "size": 1, "last_modified": "t", "menu": body}
            for position, body in enumerate(self.history[key])
        ]
        return versions[:count]


class Journey(unittest.TestCase):
    """The whole walk through the real main, Demo and state table. Only AWS and the agent are faked, because
    each part passing alone proved nothing once: ./check was GREEN with the state table not connected."""

    def setUp(self):
        demo = KOPI.Demo(SETTINGS, KOPI._load_scenario("kopi"))
        self.deployed = {**OUTPUTS, **demo.file_hashes()}
        self.store = VersionedStore(demo.scenario)
        allowed = {("kopi-tools", "s3:GetObject"), ("kopi-investigator", "bedrock-agentcore:InvokeGateway")}
        run = Observe().run_of()
        for patch in (
            mock.patch.object(KOPI, "read_deployed", side_effect=lambda: self.deployed),
            mock.patch.object(KOPI, "MenuStore", return_value=self.store),
            mock.patch.object(commands, "ask_agent", return_value=run),
            mock.patch.object(commands, "_receipts_between", return_value=[]),
            mock.patch.object(commands.boto3, "client", return_value=fake_iam(allowed)),
            mock.patch("builtins.input", return_value="ROLL BACK"),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def run_command(self, *args):
        return quietly(KOPI.main, ["kopi", *args])

    def test_the_readme_journey_in_order(self):
        for command, code, shown in (
            ("safe", 0, "BOUNDARY HOLDS"),
            ("break", 1, "Next: ./kopi observe"),
            ("observe", 0, "Next: ./kopi recover"),
            ("recover", 0, "RECOVERED"),
            ("reset", 0, "HEALTHY"),
        ):
            with self.subTest(command=command):
                result, printed = self.run_command(command)
                self.assertEqual(result, code, printed)
                self.assertIn(shown, printed)

    def test_out_of_order_is_refused_and_writes_nothing(self):
        for command, shown in (("observe", "./kopi break"), ("recover", "./kopi break")):
            with self.subTest(command=command):
                result, printed = self.run_command(command)
                self.assertEqual(result, 2)
                self.assertIn(shown, printed)
        self.assertEqual(self.store.writes, [])

    def test_bare_kopi_follows_the_journey(self):
        self.assertIn("Next:  ./kopi break", self.run_command()[1])
        self.run_command("break")
        self.assertIn("Next:  ./kopi observe", self.run_command()[1])
        self.run_command("recover")
        self.assertIn("State: HEALTHY", self.run_command()[1])

    def test_an_edited_file_stops_everything_but_up_and_down(self):
        self.deployed = {**self.deployed, "prompt_hash": "older"}
        result, printed = self.run_command("break")
        self.assertEqual(result, 2)
        self.assertIn("CONFIG NOT APPLIED: the agent prompt differs", printed)
        self.assertEqual(self.store.writes, [])


class Recover(unittest.TestCase):
    KEY = "menus/stall-kopi.json"

    def demo_with_history(self):
        """Broken now, with the fault as the newest version and the good menu before it."""
        demo = make_demo()
        good = demo.store.menus[self.KEY]
        demo.store.menus = menu.apply_fault(demo.store.menus, demo.scenario)
        demo.store.log["changes"] = [{"change_id": "MNT-142", "applied_at": "2026-10-28T04:39:00+00:00"}]
        demo.store.versions = [
            {
                "version_id": "bad",
                "size": 554,
                "last_modified": "2026-10-28T04:39:03Z",
                "menu": demo.store.menus[self.KEY],
            },
            {"version_id": "good", "size": 548, "last_modified": "2026-10-28T04:30:00Z", "menu": good},
        ]
        return demo

    def recover(self, demo, typed="ROLL BACK"):
        answer = {"side_effect": EOFError} if typed is None else {"return_value": typed}
        with mock.patch("builtins.input", **answer):
            return quietly(commands.cmd_recover, demo)

    def test_previews_then_restores_only_the_broken_menu(self):
        demo = self.demo_with_history()
        result, printed = self.recover(demo)
        self.assertEqual(result, 0)
        self.assertEqual(demo.store.writes, [self.KEY])
        self.assertLess(printed.index("Preview"), printed.index("RECOVERED"))
        self.assertIn("version good", printed)
        self.assertIn("Rp 2.500.000", printed)
        self.assertIn("HEALTHY", printed)

    def test_the_promo_on_the_other_menu_survives(self):
        demo = self.demo_with_history()
        other = json.dumps(demo.store.menus["menus/stall-seduh.json"])
        _, printed = self.recover(demo)
        self.assertEqual(json.dumps(demo.store.menus["menus/stall-seduh.json"]), other)
        self.assertIn("Kopi Flight is still 20% off", printed)

    def test_the_restore_is_logged_and_the_clock_stays(self):
        demo = self.demo_with_history()
        _, printed = self.recover(demo)
        self.assertEqual(demo.store.log["changes"][-1]["change_id"], "RESTORE-MNT-142")
        self.assertIn("after MNT-142", printed)

    def test_anything_but_roll_back_writes_nothing(self):
        for typed in ("", "yes", "roll back", None):
            with self.subTest(typed=typed):
                demo = self.demo_with_history()
                result, printed = self.recover(demo, typed)
                self.assertEqual(result, 1)
                self.assertEqual(demo.store.writes, [])
                self.assertIn("PREVIEW ONLY", printed)
                self.assertIn("Next: ./kopi recover", printed)

    def test_no_good_version_is_refused_before_asking(self):
        demo = self.demo_with_history()
        demo.store.versions = demo.store.versions[:1]
        with mock.patch("builtins.input") as ask:
            result, _ = quietly(commands.cmd_recover, demo)
        self.assertIsInstance(result, commands.recovery.RecoveryRefused)
        self.assertIn("NO GOOD VERSION", str(result))
        self.assertIn("Next: ./kopi reset", str(result))
        ask.assert_not_called()
        self.assertEqual(demo.store.writes, [])

    def test_a_second_broken_menu_is_refused_as_wrong_scope(self):
        demo = self.demo_with_history()
        demo.store.menus["menus/stall-seduh.json"]["items"][0]["price"] = 1
        with mock.patch("builtins.input") as ask:
            result, _ = quietly(commands.cmd_recover, demo)
        self.assertIsInstance(result, commands.recovery.RecoveryRefused)
        self.assertIn("WRONG SCOPE", str(result))
        self.assertIn("menus/stall-seduh.json", str(result))
        ask.assert_not_called()
        self.assertEqual(demo.store.writes, [])


class Teaching(unittest.TestCase):
    """Each step of the journey ends the same way: what happened, why it matters, what to run next."""

    def assert_teaches(self, printed, next_command):
        self.assertIn("Done: ", printed)
        self.assertIn("Why it matters: ", printed)
        self.assertIn(f"Next: {next_command}", printed)

    def test_safe(self):
        allowed = {("kopi-tools", "s3:GetObject"), ("kopi-investigator", "bedrock-agentcore:InvokeGateway")}
        with mock.patch.object(commands.boto3, "client", return_value=fake_iam(allowed)):
            _, printed = quietly(commands.cmd_safe, make_demo())
        self.assert_teaches(printed, "./kopi break")

    def test_a_broken_boundary_never_teaches_success(self):
        with mock.patch.object(commands.boto3, "client", return_value=fake_iam(set())):
            _, printed = quietly(commands.cmd_safe, make_demo())
        self.assertNotIn("Done: ", printed)

    def test_break(self):
        _, printed = quietly(commands.cmd_break, make_demo())
        self.assert_teaches(printed, "./kopi observe")
        self.assertIn("https://example.cloudfront.net/", printed)

    def test_recover(self):
        _, printed = Recover().recover(Recover().demo_with_history())
        self.assert_teaches(printed, "./kopi reset")
        self.assertIn("./kopi down", printed)

    def test_observe_counts_the_receipts_it_really_has(self):
        run = Observe().run_of()
        run.tool_calls = [{"name": "kiosk___inspect_application"}] * 2
        receipts = [{"tool": "inspect_application", "gateway_request_id": "g", "inputs": {}}] * 2
        with (
            mock.patch.object(commands, "ask_agent", return_value=run),
            mock.patch.object(commands, "_receipts_between", return_value=receipts),
        ):
            _, printed = quietly(commands.cmd_observe, make_demo(broken=True))
        self.assert_teaches(printed, "./kopi recover")
        self.assertIn("2 tool calls, 2 receipts", printed)

    def test_observe_never_claims_receipts_it_does_not_have(self):
        run = Observe().run_of()
        run.tool_calls = [{"name": "kiosk___inspect_application"}]
        with (
            mock.patch.object(commands, "ask_agent", return_value=run),
            mock.patch.object(commands, "print_receipts", return_value=False),
        ):
            _, printed = quietly(commands.cmd_observe, make_demo(broken=True))
        self.assertNotIn("receipts.", printed.split("Why it matters: ")[1].split("\n")[0])


class FakeLogs:
    """CloudWatch Logs with this stack's agent log group, and another project's that must survive."""

    def __init__(self):
        self.groups = [
            "/aws/bedrock-agentcore/runtimes/harness_kopi_investigator-abc123-DEFAULT",
            "/aws/bedrock-agentcore/runtimes/harness_acd_kopi_investigator-def456-DEFAULT",
        ]

    def describe_log_groups(self, logGroupNamePrefix, limit):
        return {"logGroups": [{"logGroupName": g} for g in self.groups if g.startswith(logGroupNamePrefix)][:limit]}

    def delete_log_group(self, logGroupName):
        self.groups.remove(logGroupName)


class Down(unittest.TestCase):
    def down(self, logs=None, deployed=OUTPUTS, **terraform):
        logs = logs or FakeLogs()
        with (
            mock.patch.object(KOPI, "read_deployed", return_value=deployed),
            mock.patch.object(KOPI, "_terraform", **terraform) as called,
            mock.patch.object(commands.boto3, "client", return_value=logs) as client,
        ):
            result, printed = quietly(KOPI.cmd_down)
        return result, printed, logs, called, client

    def test_removes_the_log_group_agentcore_created_and_nothing_else(self):
        _, printed, logs, _, _ = self.down()
        self.assertEqual(len(logs.groups), 1)
        self.assertIn("harness_acd_kopi_investigator", logs.groups[0])
        self.assertIn("1 agent log group(s)", printed)

    def test_running_it_twice_is_safe(self):
        logs = FakeLogs()
        self.down(logs)
        result, printed, _, _, _ = self.down(logs)
        self.assertEqual(result, None)
        self.assertIn("0 agent log group(s)", printed)

    def test_a_cancelled_destroy_leaves_the_logs_alone(self):
        cancelled = subprocess.CalledProcessError(1, "terraform")
        result, _, logs, _, _ = self.down(side_effect=cancelled)
        self.assertIs(result, cancelled)
        self.assertEqual(len(logs.groups), 2)

    def test_uses_what_is_deployed_whatever_config_env_says(self):
        with mock.patch.object(KOPI, "_load_config", side_effect=config.ConfigInvalid("REGON")):
            _, _, _, terraform, client = self.down()
        self.assertIn("-var=region=ap-southeast-2", terraform.call_args.args)
        self.assertIn("-var=model=au.anthropic.claude-sonnet-4-6", terraform.call_args.args)
        client.assert_called_once_with("logs", region_name="ap-southeast-2")

    def test_nothing_in_the_outputs_still_runs_destroy_from_config_env(self):
        """Outputs can be gone while resources remain, after a partial apply or destroy."""
        with mock.patch.object(KOPI, "_load_config", return_value=SETTINGS):
            _, _, _, terraform, _ = self.down(deployed=None)
        self.assertEqual(terraform.call_args.args[0], "destroy")

    def test_down_never_loads_the_scenario_or_reads_the_menus(self):
        """These fail on a typo or a deleted bucket, and down is the one way to stop the bill."""
        with (
            mock.patch.object(KOPI, "read_deployed", return_value=OUTPUTS),
            mock.patch.object(KOPI, "_terraform"),
            mock.patch.object(commands.boto3, "client", return_value=FakeLogs()),
            mock.patch.object(KOPI, "_load_config", side_effect=config.ConfigInvalid("REGON")),
            mock.patch.object(KOPI, "_load_scenario", side_effect=menu.ScenarioError("bad")) as scenario,
            mock.patch.object(KOPI.Demo, "state") as state,
        ):
            result, printed = quietly(KOPI.main, ["kopi", "down"])
        self.assertEqual(result, 0)
        self.assertIn("DOWN", printed)
        scenario.assert_not_called()
        state.assert_not_called()


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
