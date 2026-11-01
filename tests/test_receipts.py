"""The receipts join: every tool call the agent reports gets the receipt its Lambda logged. CloudWatch
stores a receipt seconds after the call (one took 8.1 s on 28 Oct 2026), so the join re-reads until
each call has one, for a bounded time, and names what is still missing or unmatched.

CloudWatch is faked at its seam (_receipts_between); the clock and sleep are faked so the bound is
tested without waiting."""

import contextlib
import io
import types
import unittest
from unittest import mock

from kiosk import commands

OUTPUTS = {"region": "ap-southeast-2", "tools_function": "kopi-tools"}
RUN = types.SimpleNamespace(
    started_at="2026-10-28T14:46:31+00:00",
    ended_at="2026-10-28T14:46:44+00:00",
    tool_calls=[
        {"name": "kiosk___inspect_application", "input": {"stall": "kopi"}},
        {"name": "kiosk___inspect_application", "input": {"stall": "seduh"}},
        {"name": "kiosk___inspect_recent_changes", "input": {"stall": "kopi", "hours": 1}},
    ],
)


def receipt(tool, stall):
    return {
        "tool": tool,
        "gateway_request_id": f"gw-{tool}-{stall}",
        "lambda_request_id": f"lambda-{tool}-{stall}",
        "inputs": {"stall": stall},
        "state": "BROKEN",
        "version_ids": ["v1"],
    }


APP_KOPI = receipt("inspect_application", "kopi")
APP_SEDUH = receipt("inspect_application", "seduh")
CHANGES = receipt("inspect_recent_changes", "kopi")


class FakeClock:
    """time.monotonic and time.sleep together: sleeping moves the clock, nothing waits."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def join(*reads, run=RUN):
    """Print the receipts for a run while CloudWatch returns each read in turn (the last one repeats).
    Returns (printed text, number of reads, seconds slept)."""
    reads = list(reads)
    count = 0

    def read(*_):
        nonlocal count
        count += 1
        return reads[min(count, len(reads)) - 1]

    clock, out = FakeClock(), io.StringIO()
    with (
        mock.patch.object(commands, "_receipts_between", side_effect=read),
        mock.patch.object(commands.time, "monotonic", clock.monotonic),
        mock.patch.object(commands.time, "sleep", clock.sleep),
        contextlib.redirect_stdout(out),
    ):
        commands.print_receipts(OUTPUTS, run)
    return out.getvalue(), count, clock.now


class Receipts(unittest.TestCase):
    def test_all_stored_prints_each_once_without_waiting(self):
        printed, reads, _ = join([APP_KOPI, APP_SEDUH, CHANGES])
        self.assertEqual(reads, 1)
        self.assertNotIn("Waiting", printed)
        self.assertNotIn("RECEIPTS", printed)
        self.assertEqual(printed.count("  tool    "), 3)

    def test_late_receipt_waits_then_prints_all(self):
        """28 Oct 2026: inspect_recent_changes was stored 8.1 s late and the output showed 2 of 3."""
        printed, reads, _ = join([APP_KOPI, APP_SEDUH], [APP_KOPI, APP_SEDUH], [APP_KOPI, APP_SEDUH, CHANGES])
        self.assertEqual(reads, 3)
        self.assertEqual(printed.count("Waiting for 1 receipt..."), 1)
        self.assertNotIn("RECEIPTS", printed)
        self.assertIn("inspect_recent_changes", printed)

    def test_never_stored_stops_at_the_bound_and_says_incomplete(self):
        printed, reads, slept = join([APP_KOPI])
        self.assertGreater(reads, 1)
        self.assertLessEqual(slept, commands.RECEIPT_WAIT_SECONDS)  # the terminal is held 15 s at most
        self.assertIn("Waiting for 2 receipts...", printed)
        self.assertIn("RECEIPTS INCOMPLETE: 1 of 3 tool calls have a receipt", printed)
        self.assertIn("missing inspect_application, inspect_recent_changes", printed)
        self.assertIn("Next: ./kopi observe starts a new run with its own receipts", printed)

    def test_receipt_for_no_reported_call_is_a_mismatch(self):
        """A tool ran that the agent did not report: the receipts, not the agent's words, are the record."""
        extra = receipt("inspect_recent_changes", "seduh")
        printed, reads, _ = join([APP_KOPI, APP_SEDUH, CHANGES, extra])
        self.assertEqual(reads, 1)
        self.assertIn("RECEIPTS MISMATCH: inspect_recent_changes has a receipt but no reported tool call", printed)
        self.assertNotIn("INCOMPLETE", printed)

    def test_no_tool_calls_needs_no_receipts(self):
        printed, reads, _ = join(
            [], run=types.SimpleNamespace(started_at=RUN.started_at, ended_at=RUN.ended_at, tool_calls=[])
        )
        self.assertEqual(reads, 1)
        self.assertNotIn("RECEIPTS", printed)


if __name__ == "__main__":
    unittest.main()
