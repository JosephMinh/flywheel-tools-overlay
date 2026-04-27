#!/usr/bin/env python3
"""
Rollout-verification stack regression test for vgd.12.1.

vgd.12.1 is the final pre-deploy gate: run unit + self-test + scripted
e2e + ``scan-once`` + ``status`` and capture the results for rollout
history. The point isn't just that those commands existed *once* on
the day the bead was closed — it's that they keep returning clean
JSON shapes through future refactors so the gate can be re-run before
every rollout.

This file pins the lightweight pieces of the gate as a unittest:

* The watcher exits 0 on ``status``, returns valid JSON, and exposes
  the rollout-relevant ``config.beads_gate_enabled`` field.
* The watcher exits 0 on ``scan-once`` and returns parseable JSON
  (the actual event content varies with whatever signals are queued
  on the running operator's machine; we assert the shape, not the
  content).
* The scripted e2e runner exits 0 with all 14 scenarios passing.
* py_compile of the watcher script succeeds.

The heavy ``self-test`` subprocess is gated behind
``AMW_RUN_SELF_TEST=1`` (matches the convention in
``test_self_test_invariants.py``) so ``unittest discover`` stays fast.

Captured rollout-history snapshot from the closing run of vgd.12.1
(2026-04-27T15:40Z, watcher main HEAD 0494559):

* ``unittest discover``: 119 OK + 1 skipped.
* ``run_e2e_scenarios.py``: 14/14 passed; bundle at
  ``tools/agent-mail-watcher/tests/_e2e_scenarios/vgd-10-4-20260427T153946Z/``.
* ``self-test``: ok=true, artifact_scenario_count=18; bundle at
  ``/home/ubuntu/.local/state/agent-mail-watcher-selftest/20260427T154009Z/artifacts/``.
* ``status``: ok JSON; config.beads_gate_enabled=false; 8 bindings
  tracked (staged rollout default per AMW-v2.md Phase 2).
* ``scan-once``: ok JSON; events=0 (clean queue at gate time).

Usage:

    python3 tools/agent-mail-watcher/tests/test_rollout_verification_stack.py

Or via discovery:

    python3 -m unittest discover -s tools/agent-mail-watcher/tests
"""

from __future__ import annotations

import json
import os
import pathlib
import py_compile
import subprocess
import sys
import unittest

THIS_DIR = pathlib.Path(__file__).resolve().parent
WATCHER_DIR = THIS_DIR.parent
WATCHER_PATH = WATCHER_DIR / "agent-mail-watcher"
E2E_RUNNER = THIS_DIR / "run_e2e_scenarios.py"

RUN_HEAVY = os.environ.get("AMW_RUN_SELF_TEST") == "1"


class TestWatcherCompiles(unittest.TestCase):
    """Sanity gate: watcher source must always compile cleanly."""

    def test_watcher_script_compiles(self) -> None:
        # py_compile.compile raises PyCompileError on syntax errors;
        # successful compile produces a __pycache__ entry we don't care
        # about here. doraise=True surfaces failures into the test.
        try:
            py_compile.compile(str(WATCHER_PATH), doraise=True)
        except py_compile.PyCompileError as exc:
            self.fail(f"watcher script must compile cleanly; got {exc}")


class TestStatusCommandContract(unittest.TestCase):
    """``agent-mail-watcher status`` must always return valid JSON
    that exposes the rollout-gate's required fields. This is the
    operator-facing pre-rollout check from AMW-v2.md Phase 9, and
    operators will key on these field names from dashboards."""

    @classmethod
    def setUpClass(cls) -> None:
        result = subprocess.run(
            [str(WATCHER_PATH), "status"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"`status` subprocess failed: rc={result.returncode}, "
                f"stderr={result.stderr[:300]!r}"
            )
        try:
            cls.payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"`status` returned non-JSON output: {exc}; "
                f"stdout[:300]={result.stdout[:300]!r}"
            )

    def test_status_payload_is_dict(self) -> None:
        self.assertIsInstance(self.payload, dict)

    def test_status_exposes_config_beads_gate_enabled(self) -> None:
        config_block = self.payload.get("config", {})
        self.assertIn(
            "beads_gate_enabled",
            config_block,
            "status output must surface config.beads_gate_enabled so "
            "rollout dashboards can tell the gate state without "
            "reading the watcher source",
        )
        self.assertIsInstance(config_block["beads_gate_enabled"], bool)


class TestScanOnceCommandContract(unittest.TestCase):
    """``agent-mail-watcher scan-once`` must exit 0 and return JSON.
    The event content varies with the operator's signal queue; we
    only pin the shape so the rollout gate's command call succeeds."""

    def test_scan_once_returns_parseable_json(self) -> None:
        result = subprocess.run(
            [str(WATCHER_PATH), "scan-once"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"scan-once must exit 0; stderr={result.stderr[:300]!r}",
        )
        try:
            json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            self.fail(
                f"scan-once must return valid JSON: {exc}; "
                f"stdout[:300]={result.stdout[:300]!r}"
            )


class TestE2eRunnerStaysGreen(unittest.TestCase):
    """The rollout gate is only meaningful if the e2e runner stays
    green. Subprocess it and pin its public surface."""

    def test_e2e_runner_exits_zero_with_all_scenarios_passing(self) -> None:
        result = subprocess.run(
            [sys.executable, str(E2E_RUNNER)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"e2e runner must exit 0; stderr={result.stderr[:300]!r}",
        )
        # The runner prints a one-line summary that operators paste
        # into rollout notes; pin it so a refactor can't silently
        # change the format.
        self.assertIn("total scenarios:", result.stdout)
        self.assertIn("passed: 14", result.stdout)
        self.assertIn("failed: 0", result.stdout)


@unittest.skipUnless(
    RUN_HEAVY,
    "set AMW_RUN_SELF_TEST=1 to run the heavy `agent-mail-watcher self-test` "
    "subprocess as part of the rollout gate (creates live tmux sessions; "
    "takes ~30s). Recommended pre-rollout, skipped during normal unittest "
    "discover.",
)
class TestSelfTestStaysGreenUnderRolloutGate(unittest.TestCase):
    """Pre-rollout heavy gate: the watcher's full self-test flow must
    return ok=true and produce an artifact bundle. Mirrors the
    ``self-test`` step of vgd.12.1's verification stack."""

    def test_self_test_returns_ok_with_artifact_bundle(self) -> None:
        result = subprocess.run(
            [str(WATCHER_PATH), "self-test"],
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"self-test must exit 0; stderr={result.stderr[:300]!r}",
        )
        payload = json.loads(result.stdout)
        self.assertTrue(payload.get("ok"))
        manifest_path = pathlib.Path(payload["artifact_manifest"])
        self.assertTrue(manifest_path.exists())


if __name__ == "__main__":
    unittest.main()
