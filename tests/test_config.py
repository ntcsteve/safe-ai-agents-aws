"""config.env: three values, validated before any command runs, each refusal naming the key and the fix."""

import unittest

from kiosk import config

GOOD = """# Where this runs.
REGION=ap-southeast-2

MODEL=au.anthropic.claude-sonnet-4-6
SCENARIO=kopi
"""
SCENARIOS = {"kopi", "warung"}


def parse(text):
    return config.parse_config(text, SCENARIOS)


class Valid(unittest.TestCase):
    def test_reads_the_three_values_past_comments_and_blanks(self):
        self.assertEqual(
            parse(GOOD),
            config.Config(region="ap-southeast-2", model="au.anthropic.claude-sonnet-4-6", scenario="kopi"),
        )

    def test_any_region_and_scenario_in_the_right_shape_is_accepted(self):
        text = GOOD.replace("ap-southeast-2", "us-gov-west-1").replace("SCENARIO=kopi", "SCENARIO=warung")
        self.assertEqual(parse(text).region, "us-gov-west-1")
        self.assertEqual(parse(text).scenario, "warung")

    def test_terraform_variables_carry_exactly_the_three_values(self):
        self.assertEqual(
            parse(GOOD).terraform_vars(),
            ["-var=region=ap-southeast-2", "-var=model=au.anthropic.claude-sonnet-4-6", "-var=scenario=kopi"],
        )


class Refused(unittest.TestCase):
    def assert_refused(self, text, *named):
        with self.assertRaises(config.ConfigInvalid) as caught:
            parse(text)
        for word in named:
            self.assertIn(word, str(caught.exception))

    def test_a_missing_key(self):
        self.assert_refused(GOOD.replace("MODEL=au.anthropic.claude-sonnet-4-6\n", ""), "MODEL", "missing")

    def test_an_unknown_key_catches_a_typo(self):
        self.assert_refused(GOOD.replace("REGION=", "REGON="), "REGON", "unknown")

    def test_a_repeated_key_never_picks_one_silently(self):
        self.assert_refused(GOOD + "SCENARIO=warung\n", "SCENARIO", "twice")

    def test_a_line_that_is_not_key_equals_value(self):
        self.assert_refused(GOOD + "just some words\n", "just some words")

    def test_a_badly_shaped_region(self):
        self.assert_refused(GOOD.replace("ap-southeast-2", "Sydney"), "REGION", "Sydney")

    def test_a_badly_shaped_model(self):
        self.assert_refused(GOOD.replace("au.anthropic.claude-sonnet-4-6", "claude sonnet"), "MODEL", "claude sonnet")

    def test_a_scenario_with_no_file_lists_the_real_ones(self):
        self.assert_refused(GOOD.replace("SCENARIO=kopi", "SCENARIO=bakso"), "SCENARIO", "bakso", "kopi", "warung")

    def test_a_scenario_cannot_climb_out_of_the_folder(self):
        self.assert_refused(GOOD.replace("SCENARIO=kopi", "SCENARIO=../secrets"), "SCENARIO")

    def test_quotes_are_refused_not_kept_as_part_of_the_value(self):
        self.assert_refused(GOOD.replace("ap-southeast-2", '"ap-southeast-2"'), "REGION")


if __name__ == "__main__":
    unittest.main()
