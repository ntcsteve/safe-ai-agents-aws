"""The state table. Every command in every state has a defined outcome, tested here in full, so running
something out of order always answers with what to do next and never touches AWS."""

import unittest

from kiosk import config, states
from kiosk.states import State

SETTINGS = config.Config(region="ap-southeast-2", model="au.anthropic.claude-sonnet-4-6", scenario="kopi")
HASHES = {"scenario_hash": "aaa", "prompt_hash": "bbb"}
DEPLOYED = {
    "region": "ap-southeast-2",
    "model": "au.anthropic.claude-sonnet-4-6",
    "scenario": "kopi",
    **HASHES,
}
COMMANDS = ("status", "up", "clumio", "safe", "break", "observe", "recover", "check", "reset", "eval")


def read(deployed=DEPLOYED, is_healthy=True):
    def health():
        return is_healthy

    return states.read_state(deployed, SETTINGS, HASHES, health)


class ReadState(unittest.TestCase):
    def test_nothing_deployed(self):
        self.assertIs(read(deployed=None), State.NOT_DEPLOYED)

    def test_healthy_and_broken_come_from_the_menus(self):
        self.assertIs(read(is_healthy=True), State.HEALTHY)
        self.assertIs(read(is_healthy=False), State.BROKEN)

    def test_a_changed_model_scenario_name_or_file_is_not_applied(self):
        for change in ({"model": "other"}, {"scenario": "books"}, {"scenario_hash": "x"}, {"prompt_hash": "x"}):
            with self.subTest(change=change):
                self.assertIs(read({**DEPLOYED, **change}), State.CONFIG_NOT_APPLIED)

    def test_a_changed_region_is_its_own_state(self):
        self.assertIs(read({**DEPLOYED, "region": "us-east-1"}), State.REGION_CHANGED)

    def test_the_menus_are_never_read_while_the_config_is_not_applied(self):
        def health():
            raise AssertionError("read the menus against a scenario that is not deployed")

        self.assertIs(
            states.read_state({**DEPLOYED, "model": "other"}, SETTINGS, HASHES, health), State.CONFIG_NOT_APPLIED
        )
        self.assertIs(states.read_state(None, SETTINGS, HASHES, health), State.NOT_DEPLOYED)


class Drift(unittest.TestCase):
    def test_names_exactly_what_differs(self):
        names = states.differences({**DEPLOYED, "model": "other", "prompt_hash": "x"}, SETTINGS, HASHES)
        self.assertEqual(names, ["MODEL", "the agent prompt"])


class Gates(unittest.TestCase):
    def test_every_command_has_a_rule_for_every_state(self):
        self.assertEqual(set(states.ALLOWED), set(COMMANDS))

    def test_the_whole_table(self):
        allowed_in = {
            "status": set(State),
            "up": set(State) - {State.REGION_CHANGED},
            "clumio": {State.HEALTHY},
            "safe": {State.HEALTHY, State.BROKEN},
            "break": {State.HEALTHY},
            "observe": {State.BROKEN},
            "recover": {State.BROKEN},
            "check": {State.HEALTHY, State.BROKEN},
            "reset": {State.HEALTHY, State.BROKEN},
            "eval": {State.HEALTHY},
        }
        for command in COMMANDS:
            for state in State:
                with self.subTest(command=command, state=state):
                    self.assertEqual(state in states.ALLOWED[command], state in allowed_in[command])

    def test_every_refusal_says_what_to_run_next(self):
        for command in COMMANDS:
            for state in set(State) - states.ALLOWED[command]:
                with self.subTest(command=command, state=state):
                    self.assertIn("Next: ./kopi ", states.refusal(command, state, ""))

    def test_every_state_has_a_next_step(self):
        for state in State:
            self.assertTrue(states.NEXT[state].startswith("./kopi "))

    def test_refusals_name_the_state(self):
        self.assertIn("NOT DEPLOYED", states.refusal("break", State.NOT_DEPLOYED, ""))
        self.assertIn("CONFIG NOT APPLIED: MODEL differs", states.refusal("break", State.CONFIG_NOT_APPLIED, "MODEL"))
        self.assertIn("./kopi down", states.refusal("up", State.REGION_CHANGED, ""))
        self.assertIn("HEALTHY", states.refusal("observe", State.HEALTHY, ""))
        self.assertIn("./kopi break", states.refusal("observe", State.HEALTHY, ""))
        self.assertIn("BROKEN", states.refusal("break", State.BROKEN, ""))


if __name__ == "__main__":
    unittest.main()
