"""./check must never say GREEN after a failed step. It runs for real here, with its tools faked on PATH,
so each step can be made to fail without planting a defect in the repo."""

import os
import pathlib
import stat
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FAKE_TOOL = """#!/bin/sh
case "$*" in *"$FAIL_STEP"*) echo "fake failure"; exit 1;; esac
echo "ℹ pass 1"
"""
STEPS = ["ruff check", "ruff format", "unittest", "--test", "fmt -check", "init", "validate"]


def run_check(fail_step):
    with tempfile.TemporaryDirectory() as folder:
        for tool in ("uv", "uvx", "node", "terraform"):
            path = pathlib.Path(folder) / tool
            path.write_text(FAKE_TOOL)
            path.chmod(path.stat().st_mode | stat.S_IEXEC)
        env = {**os.environ, "PATH": f"{folder}:/usr/bin:/bin", "FAIL_STEP": fail_step}
        return subprocess.run([str(ROOT / "check")], capture_output=True, text=True, env=env)


class Check(unittest.TestCase):
    def test_all_steps_passing_is_green(self):
        result = run_check("no step has this name")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("GREEN", result.stdout)

    def test_any_failed_step_stops_before_green(self):
        for step in STEPS:
            with self.subTest(step=step):
                result = run_check(step)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("GREEN", result.stdout)


if __name__ == "__main__":
    unittest.main()
