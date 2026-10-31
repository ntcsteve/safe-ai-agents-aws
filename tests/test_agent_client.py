"""A failed agent run must never look like a short successful one. The stream is faked at its seam."""

import unittest
from unittest import mock

from kiosk import agent_client

ANSWER = [
    {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "FACTS: three prices"}}},
    {"messageStop": {"stopReason": "end_turn"}},
]


def ask(stream):
    fake = mock.Mock()
    fake.invoke_harness.return_value = {"stream": stream}
    with mock.patch.object(agent_client.boto3, "client", return_value=fake):
        return agent_client.ask("arn:harness", "ap-southeast-2", "question")


class Stream(unittest.TestCase):
    def test_a_normal_run_returns_the_answer(self):
        run = ask(ANSWER)
        self.assertEqual(run.text, "FACTS: three prices")
        self.assertTrue(run.finished_normally)

    def test_a_failure_in_the_stream_raises_even_after_text(self):
        failed = [ANSWER[0], {"throttlingException": {"message": "slow down"}}]
        with self.assertRaisesRegex(agent_client.AgentStreamError, "slow down"):
            ask(failed)

    def test_a_run_that_stops_early_does_not_count_as_finished(self):
        run = ask([ANSWER[0], {"messageStop": {"stopReason": "max_tokens"}}])
        self.assertFalse(run.finished_normally)


if __name__ == "__main__":
    unittest.main()
