"""The Clumio add-on, one test per named outcome. Clumio and Terraform are faked at their seams; the menus are
real menu logic. These tests live here so that deleting this folder removes them too."""

import json
import re
import subprocess
import unittest
from unittest import mock

from addons.clumio import backtrack
from kiosk import menu, recovery
from kiosk.s3_store import LOG_KEY
from tests.test_commands import KOPI, OUTPUTS, SETTINGS, VersionedStore, quietly

KEY = "menus/stall-kopi.json"
POINT = "2026-10-28T04:35:00Z"
GOOD_ENV = "CLUMIO_API_TOKEN=secret-token\nCLUMIO_API_BASE_URL=https://api.example.clumio.com\nCLUMIO_OU_ID=ou-1\n"


def settings():
    return backtrack.parse_settings(GOOD_ENV)


class Settings(unittest.TestCase):
    def test_reads_the_three_values_past_comments(self):
        parsed = backtrack.parse_settings("# mine\n\n" + GOOD_ENV)
        self.assertEqual((parsed.base_url, parsed.ou_id), ("https://api.example.clumio.com", "ou-1"))

    def test_the_ou_may_be_blank(self):
        text = GOOD_ENV.replace("CLUMIO_OU_ID=ou-1", "CLUMIO_OU_ID=")
        self.assertEqual(backtrack.parse_settings(text).ou_id, "")

    def test_the_token_never_appears_when_printed(self):
        self.assertNotIn("secret-token", repr(settings()))

    def test_a_blank_token_points_at_the_guide(self):
        with self.assertRaisesRegex(backtrack.ClumioRefused, "CLUMIO NOT SET UP.*CLUMIO_API_TOKEN"):
            backtrack.parse_settings(GOOD_ENV.replace("secret-token", ""))

    def test_any_other_key_is_refused_so_nothing_can_switch_aws_accounts(self):
        with self.assertRaisesRegex(backtrack.ClumioRefused, "AWS_PROFILE"):
            backtrack.parse_settings(GOOD_ENV + "AWS_PROFILE=other\n")

    def test_a_url_that_is_not_https_is_refused(self):
        with self.assertRaisesRegex(backtrack.ClumioRefused, "CLUMIO_API_BASE_URL"):
            backtrack.parse_settings(GOOD_ENV.replace("https://api", "http://api"))

    def test_a_missing_file_points_at_the_guide(self):
        with (
            mock.patch.object(backtrack.ENV_FILE.__class__, "exists", return_value=False),
            self.assertRaisesRegex(backtrack.ClumioRefused, "CLUMIO NOT SET UP.*addons/clumio/README.md"),
        ):
            backtrack.read_settings()


class Requests(unittest.TestCase):
    def test_preview_scopes_one_exact_key(self):
        self.assertEqual(
            backtrack.preview_request(KEY, POINT),
            {
                "type": "rollback",
                "point_in_time_recovery": {"restore_end_timestamp": POINT},
                "object_filters": {"object_key_matches": KEY, "is_latest_version_only": True},
            },
        )

    def test_rollback_restores_in_place_with_the_same_scope(self):
        self.assertEqual(
            backtrack.rollback_request("bkt", "env", "kopi-x", KEY, POINT),
            {
                "type": "rollback",
                "source": {
                    "bucket_id": "bkt",
                    "point_in_time_recovery": {"restore_end_timestamp": POINT},
                    "object_filters": {"object_key_matches": KEY, "is_latest_version_only": True},
                },
                "target": {"bucket_name": "kopi-x", "environment_id": "env"},
            },
        )


class ScopeGate(unittest.TestCase):
    def test_exactly_the_one_key_passes(self):
        self.assertEqual(backtrack.require_single_key([{"object_key": KEY}], KEY), {"object_key": KEY})

    def test_anything_else_is_wrong_scope(self):
        for objects in (
            [],
            [{"object_key": KEY}, {"object_key": "menus/stall-seduh.json"}],
            [{"object_key": f"{KEY}.bak"}],
        ):
            with self.subTest(objects=objects), self.assertRaisesRegex(backtrack.ClumioRefused, "WRONG SCOPE"):
                backtrack.require_single_key(objects, KEY)


class FakeClumio:
    """Clumio's API: the sync it reports, the version it previews, and a rollback that rewrites the bucket."""

    def __init__(self, demo, last_sync=POINT, previewed="v2", objects=None):
        self.demo, self.last_sync, self.previewed, self.objects = demo, last_sync, previewed, objects
        self.rolled_back = []

    def find_bucket(self, name):
        return "bkt", "env", self.last_sync

    def preview(self, bucket_id, body):
        return self.objects if self.objects is not None else [{"object_key": KEY, "version_id": self.previewed}]

    def rollback(self, body):
        self.rolled_back.append(body)
        good = next(v for v in self.demo.store.recent_versions(KEY, 50) if v["version_id"] == self.previewed)
        self.demo.store.write_menu(KEY, good["menu"])
        return "task-1"

    def wait(self, task_id, on_wait=None):
        return {"status": "completed"}


def broken_demo():
    """Reset once, so v2 is the good menu, then broken: v3 holds the fault."""
    demo = KOPI.Demo(SETTINGS, KOPI._load_scenario("kopi"))
    demo.__dict__["store"] = VersionedStore(demo.scenario)
    demo.__dict__["deployed"] = OUTPUTS
    quietly(demo.reset)
    quietly(demo.fault)
    return demo


class Recover(unittest.TestCase):
    def recover(self, demo, clumio=None, typed="ROLL BACK", active=True):
        clumio = clumio or FakeClumio(demo)
        with (
            mock.patch.object(backtrack, "read_settings", return_value=settings()),
            mock.patch.object(backtrack, "ClumioBacktrack", return_value=clumio),
            mock.patch.object(backtrack, "is_protected", return_value=active),
            mock.patch("builtins.input", return_value=typed),
        ):
            result, printed = quietly(backtrack.recover, demo)
        return result, printed, clumio

    def test_previews_then_rolls_back_one_object_after_roll_back(self):
        demo = broken_demo()
        result, printed, clumio = self.recover(demo)
        self.assertEqual(result, 0, printed)
        self.assertEqual(len(clumio.rolled_back), 1)
        self.assertEqual(clumio.rolled_back[0]["source"]["point_in_time_recovery"]["restore_end_timestamp"], POINT)
        self.assertLess(printed.index("Preview"), printed.index("RECOVERED"))
        self.assertIn("Kopi Flight is still 20% off", printed)
        self.assertIn("HEALTHY", printed)

    def test_the_restore_stops_the_clock_and_is_logged(self):
        demo = broken_demo()
        self.recover(demo)
        self.assertEqual(demo.store.log["changes"][-1]["change_id"], "RESTORE-MNT-142")

    def test_anything_but_roll_back_submits_nothing(self):
        result, printed, clumio = self.recover(broken_demo(), typed="no")
        self.assertEqual(result, 1)
        self.assertEqual(clumio.rolled_back, [])
        self.assertIn("PREVIEW ONLY", printed)

    def refused(self, *needle, **fake):
        demo = broken_demo()
        clumio = FakeClumio(demo, **fake)
        result, _, _ = self.recover(demo, clumio)
        self.assertIsInstance(result, recovery.RecoveryRefused)
        for word in needle:
            self.assertIn(word, str(result))
        self.assertEqual(clumio.rolled_back, [])

    def test_no_sync_yet(self):
        self.refused("CLUMIO NO SYNC", "./kopi recover", last_sync=None)

    def test_a_sync_that_captured_the_bad_change(self):
        self.refused("CLUMIO POINT BAD", "./kopi recover", previewed="v3")

    def test_a_previewed_version_s3_does_not_know_is_not_trusted(self):
        self.refused("CLUMIO POINT BAD", previewed="unknown")

    def test_a_preview_of_more_than_one_object(self):
        self.refused("WRONG SCOPE", objects=[{"object_key": KEY}, {"object_key": "menus/stall-seduh.json"}])

    def test_not_connected(self):
        result, _, clumio = self.recover(broken_demo(), active=False)
        self.assertIsInstance(result, recovery.RecoveryRefused)
        self.assertIn("./kopi clumio", str(result))
        self.assertEqual(clumio.rolled_back, [])

    def test_another_menu_changing_during_the_rollback_is_named(self):
        demo = broken_demo()
        clumio = FakeClumio(demo)
        real = clumio.rollback

        def rollback_and_touch(body):
            task = real(body)
            demo.store.write_menu("menus/stall-seduh.json", demo.store.menus["menus/stall-seduh.json"])
            return task

        clumio.rollback = rollback_and_touch
        result, printed, _ = self.recover(demo, clumio)
        self.assertEqual(result, 1)
        self.assertIn("WRONG SCOPE AFTER RESTORE: menus/stall-seduh.json", printed)
        self.assertNotIn(LOG_KEY, printed.split("WRONG SCOPE AFTER RESTORE")[1].split("\n")[0])


class Connect(unittest.TestCase):
    def connect(self, seen):
        """seen: what each find_bucket call returns, in order; None means Clumio cannot see the bucket yet."""
        demo = KOPI.Demo(SETTINGS, KOPI._load_scenario("kopi"))
        demo.__dict__["deployed"] = OUTPUTS
        client = mock.Mock()
        client.find_bucket.side_effect = [
            backtrack.ClumioRefused("not seen") if s is None else ("bkt", "env", s) for s in seen
        ]
        clock = iter(range(0, 100000, 30))
        with (
            mock.patch.object(backtrack, "read_settings", return_value=settings()),
            mock.patch.object(backtrack, "ClumioBacktrack", return_value=client),
            mock.patch.object(backtrack, "_terraform") as terraform,
            mock.patch.object(backtrack.time, "sleep"),
            mock.patch.object(backtrack.time, "monotonic", side_effect=lambda: next(clock)),
        ):
            result, printed = quietly(backtrack.connect, demo)
        return result, printed, terraform

    def test_every_declared_variable_is_passed_on_each_apply(self):
        declared = set(re.findall(r'variable "(\w+)"', (backtrack.HERE / "variables.tf").read_text()))
        _, _, terraform = self.connect([None, ""])
        for apply in self.applies(terraform):
            self.assertEqual({arg.split("=")[1] for arg in apply[1:] if arg.startswith("-var=")}, declared)

    def applies(self, terraform):
        return [call.args for call in terraform.call_args_list if call.args[1] == "apply"]

    def test_first_connect_applies_waits_until_seen_then_protects(self):
        _, printed, terraform = self.connect([None, None, None, ""])
        first, second = self.applies(terraform)
        self.assertIn("-var=protect=false", first)
        self.assertIn("-var=protect=true", second)
        self.assertIn("Next: ./kopi", printed)
        self.assertIn("first sync", printed)

    def test_rerunning_never_removes_protection(self):
        """protect=false on a bucket already protected would unprotect it."""
        _, _, terraform = self.connect(["2026-10-30T18:50:00Z"])
        self.assertEqual(len(self.applies(terraform)), 1)
        self.assertIn("-var=protect=true", self.applies(terraform)[0])

    def test_a_bucket_never_seen_stops_at_the_bound_and_names_it(self):
        result, _, terraform = self.connect([None] * 500)
        self.assertIsInstance(result, recovery.RecoveryRefused)
        self.assertIn("CLUMIO BUCKET NOT SEEN", str(result))
        self.assertIn("Next: ./kopi clumio", str(result))
        self.assertEqual(len(self.applies(terraform)), 1)


class Status(unittest.TestCase):
    def status(self, found, protected=True):
        client = mock.Mock()
        client.find_bucket.side_effect = found if isinstance(found, Exception) else None
        client.find_bucket.return_value = found
        with (
            mock.patch.object(backtrack, "is_active", return_value=True),
            mock.patch.object(backtrack, "is_protected", return_value=protected),
            mock.patch.object(backtrack, "read_settings", return_value=settings()),
            mock.patch.object(backtrack, "ClumioBacktrack", return_value=client),
        ):
            return backtrack.status("kopi-x")

    def test_waiting_for_the_first_sync(self):
        line, next_step = self.status(("bkt", "env", None))
        self.assertIn("waiting for its first sync", line)
        self.assertNotIn("break", next_step)  # breaking now could put the fault into the first sync

    def test_connected_but_not_protected_never_says_to_wait(self):
        """Live, 1 Nov 2026: stopping at the second approval left it connected, and no sync would ever come."""
        line, next_step = self.status(("bkt", "env", None), protected=False)
        self.assertIn("not protected", line)
        self.assertEqual(next_step, "./kopi clumio")
        self.assertNotIn("waiting", line)

    def test_ready(self):
        line, next_step = self.status(("bkt", "env", "2026-10-30T18:50:00Z"))
        self.assertIn("last sync 2026-10-30T18:50:00Z", line)
        self.assertIn("./kopi recover --clumio", next_step)

    def test_an_api_failure_never_breaks_the_status(self):
        line, next_step = self.status(backtrack.ClumioRefused("HTTP 500"))
        self.assertIn("cannot reach", line)
        self.assertIsNone(next_step)

    def test_silent_when_the_add_on_is_not_in_use(self):
        with mock.patch.object(backtrack, "is_active", return_value=False):
            self.assertIsNone(backtrack.status("kopi-x"))


class Disconnect(unittest.TestCase):
    def disconnect(self, **terraform):
        with (
            mock.patch.object(backtrack, "read_settings", return_value=settings()),
            mock.patch.object(
                backtrack, "_addon_outputs", return_value={"bucket": "kopi-x", "region": "ap-southeast-2"}
            ),
            mock.patch.object(backtrack, "_terraform", **terraform) as called,
        ):
            result, printed = quietly(backtrack.disconnect)
        return result, printed, called

    def test_destroys_from_what_the_add_on_recorded(self):
        _, _, terraform = self.disconnect()
        self.assertEqual(terraform.call_args.args[1], "destroy")
        self.assertIn("-var=bucket=kopi-x", terraform.call_args.args)

    def test_every_declared_variable_is_passed(self):
        """Live, 1 Nov 2026: protect was missing, so Terraform asked for it and took the answer to its next prompt."""
        declared = set(re.findall(r'variable "(\w+)"', (backtrack.HERE / "variables.tf").read_text()))
        _, _, terraform = self.disconnect()
        passed = {arg.split("=")[1] for arg in terraform.call_args.args[1:] if arg.startswith("-var=")}
        self.assertEqual(passed, declared)

    def test_a_failed_disconnect_stops_down_without_guessing_why(self):
        result, _, _ = self.disconnect(side_effect=subprocess.CalledProcessError(1, "terraform"))
        self.assertIsInstance(result, recovery.RecoveryRefused)
        self.assertIn("CLUMIO DISCONNECT FAILED", str(result))
        self.assertIn("Next: ./kopi down", str(result))


class Active(unittest.TestCase):
    def test_active_only_with_resources_in_the_state_file(self):
        fake = mock.Mock()
        for text, expected in (('{"resources": [{"type": "x"}]}', True), ('{"resources": []}', False)):
            with self.subTest(text=text):
                fake.exists.return_value = True
                fake.read_text.return_value = text
                with mock.patch.object(backtrack, "STATE_FILE", fake):
                    self.assertIs(backtrack.is_active(), expected)
        fake.exists.return_value = False
        with mock.patch.object(backtrack, "STATE_FILE", fake):
            self.assertFalse(backtrack.is_active())


class Protected(unittest.TestCase):
    def state(self, *modules):
        fake = mock.Mock()
        fake.exists.return_value = True
        fake.read_text.return_value = json.dumps({"resources": [{"module": m} for m in modules]})
        return mock.patch.object(backtrack, "STATE_FILE", fake)

    def test_only_the_protection_module_counts(self):
        with self.state("module.clumio_iam.module.aws_template"):
            self.assertFalse(backtrack.is_protected())
        with self.state("module.clumio_iam", "module.menus_protection[0]"):
            self.assertTrue(backtrack.is_protected())


class Wiring(unittest.TestCase):
    """Through the real main: the flag picks the path, and it never happens silently."""

    def run_main(self, *args):
        deployed = {**OUTPUTS, **KOPI.Demo(SETTINGS, KOPI._load_scenario("kopi")).file_hashes()}
        store = VersionedStore(KOPI._load_scenario("kopi"))
        store.menus = menu.apply_fault(store.menus, KOPI._load_scenario("kopi"))
        with (
            mock.patch.object(KOPI, "read_deployed", return_value=deployed),
            mock.patch.object(KOPI, "MenuStore", return_value=store),
            mock.patch.object(backtrack, "recover", return_value=0) as with_clumio,
            mock.patch.object(KOPI.commands, "cmd_recover", return_value=0) as with_s3,
        ):
            KOPI.COMMANDS["recover"] = KOPI.cmd_recover
            quietly(KOPI.main, ["kopi", *args])
        return with_clumio, with_s3

    def test_the_flag_picks_clumio(self):
        with_clumio, with_s3 = self.run_main("recover", "--clumio")
        with_clumio.assert_called_once()
        with_s3.assert_not_called()

    def test_no_flag_stays_on_s3(self):
        with_clumio, with_s3 = self.run_main("recover")
        with_clumio.assert_not_called()
        with_s3.assert_called_once()


if __name__ == "__main__":
    unittest.main()
