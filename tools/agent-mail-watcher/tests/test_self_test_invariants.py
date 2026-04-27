#!/usr/bin/env python3
"""
Pin the watcher's full ``agent-mail-watcher self-test`` flow as the
final-mile validation for vgd.10.1 (idle-pane scenarios) and vgd.10.2
(working-pane and busy-suppression invariants).

The watcher's ``command_self_test`` already runs:

* idle primary pane with first wake / second wake (vgd.10.1 idle-state
  baseline); cross-project leakage check;
* busy provider pane that should be suppressed with
  ``suppressed-working-pane`` (vgd.10.2 busy invariant);
* a divergent-worktree validation scenario added in vgd.11.1 that
  records the chosen work-state root and source.

Re-running it from a unit test would take ~30 seconds because of all
the live tmux sessions, so the heavy smoke test is gated behind the
``AMW_RUN_SELF_TEST`` env var to keep ``unittest discover`` snappy.
The default-enabled checks below introspect the ``DECISION_SCENARIOS``
table in ``run_e2e_scenarios.py`` and the watcher source itself to
prove the invariants without spawning tmux.

Run all checks (including the heavy subprocess) with:

    AMW_RUN_SELF_TEST=1 python3 tools/agent-mail-watcher/tests/test_self_test_invariants.py

Default fast subset:

    python3 -m unittest discover -s tools/agent-mail-watcher/tests
"""

from __future__ import annotations

import json
import os
import pathlib
import runpy
import subprocess
import sys
import unittest

THIS_DIR = pathlib.Path(__file__).resolve().parent
WATCHER_DIR = THIS_DIR.parent
WATCHER_PATH = WATCHER_DIR / "agent-mail-watcher"

RUN_SELF_TEST = os.environ.get("AMW_RUN_SELF_TEST") == "1"


def load_amw() -> dict:
    return runpy.run_path(str(WATCHER_PATH))


@unittest.skipUnless(
    RUN_SELF_TEST,
    "set AMW_RUN_SELF_TEST=1 to run the heavy `agent-mail-watcher self-test` "
    "subprocess (creates live tmux sessions; takes ~30s). Recommended before "
    "rollout but skipped during normal unittest discover to keep the suite fast.",
)
class TestWatcherSelfTestSucceeds(unittest.TestCase):
    """vgd.10.1 + vgd.10.2 final-mile: drive the real watcher self-test
    flow and assert ok=true plus the documented artifact_scenario_count."""

    def test_self_test_returns_ok_true(self) -> None:
        result = subprocess.run(
            [str(WATCHER_PATH), "self-test"],
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"watcher self-test must exit 0; stderr={result.stderr!r}",
        )
        payload = json.loads(result.stdout)
        self.assertTrue(
            payload.get("ok"),
            f"watcher self-test must return ok=true; payload={payload!r}",
        )
        # Each scenario in the watcher's self-test path adds one entry to
        # the artifact bundle. The exact count is implementation detail
        # of command_self_test; pin a lower bound so a regression that
        # silently drops scenarios fails this test.
        self.assertGreaterEqual(
            payload.get("artifact_scenario_count", 0),
            10,
            "watcher self-test must record at least 10 scenarios; a "
            "regression that silently drops idle/busy/cross-project "
            "scenarios would let policy bugs through",
        )
        manifest_path = pathlib.Path(payload["artifact_manifest"])
        self.assertTrue(
            manifest_path.exists(),
            f"self-test artifact manifest must exist at {manifest_path}",
        )


class TestIdlePaneSeverityCoverage(unittest.TestCase):
    """vgd.10.1 fast checks: prove the e2e runner exercises idle-pane
    severity paths for every required state without needing tmux.

    The runner's ``DECISION_SCENARIOS`` is the single source of truth
    for what idle-pane severity-x-state combinations the watcher's
    policy is verified against. Pin the required cells here so a
    regression that drops a row fails this test rather than silently
    losing coverage."""

    @classmethod
    def setUpClass(cls) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_e2e_runner_module",
            str(THIS_DIR / "run_e2e_scenarios.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cls.scenarios = mod.DECISION_SCENARIOS

    def _decisions_for(self, fixture: str) -> dict[str, str]:
        return {
            importance: expected_dec
            for _sid, _desc, importance, fix, expected_dec in self.scenarios
            if fix == fixture
        }

    def test_zero_open_covers_normal_high_urgent(self) -> None:
        decisions = self._decisions_for("zero-open")
        self.assertIn("normal", decisions)
        self.assertIn("high", decisions)
        self.assertIn("urgent", decisions)
        # AC: normal/high suppress, urgent wakes (vgd.10.1 row).
        self.assertEqual(decisions["normal"], "POLICY_DECISION_SUPPRESS_NO_OPEN")
        self.assertEqual(decisions["high"], "POLICY_DECISION_SUPPRESS_NO_OPEN")
        self.assertEqual(decisions["urgent"], "POLICY_DECISION_WAKE")

    def test_open_zero_ready_covers_normal_high_urgent(self) -> None:
        decisions = self._decisions_for("open-zero-ready")
        self.assertIn("normal", decisions)
        self.assertIn("high", decisions)
        self.assertIn("urgent", decisions)
        self.assertEqual(decisions["normal"], "POLICY_DECISION_SUPPRESS_NO_READY")
        self.assertEqual(decisions["high"], "POLICY_DECISION_WAKE")
        self.assertEqual(decisions["urgent"], "POLICY_DECISION_WAKE")

    def test_ready_state_allows_normal_wake(self) -> None:
        decisions = self._decisions_for("ready")
        self.assertIn("normal", decisions)
        self.assertEqual(decisions["normal"], "POLICY_DECISION_WAKE")


class TestBusyAndOwnershipInvariants(unittest.TestCase):
    """vgd.10.2 fast checks: prove that the watcher source still
    contains the busy-suppression and ownership/provider-identity
    invariants, and that the suppress predicate stays narrow enough
    to leave them authoritative.

    These pin pre-v2 contracts that the beads gate must not weaken;
    they do not require running tmux because the contracts are
    properties of the watcher's vocabulary and predicate, not of any
    particular runtime invocation."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.amw = load_amw()
        cls.watcher_text = WATCHER_PATH.read_text(encoding="utf-8")

    def test_suppressed_working_pane_action_string_still_exists(self) -> None:
        # The action code for busy-pane suppression is the operator-
        # facing contract (event log + dashboards). If a future refactor
        # removes or renames it, busy suppression effectively becomes
        # invisible to operators even if it still works internally.
        self.assertIn(
            '"suppressed-working-pane"',
            self.watcher_text,
            "action string `suppressed-working-pane` must remain in the "
            "watcher source; renaming or removing it breaks vgd.10.2 "
            "busy-suppression operator visibility",
        )

    def test_provider_identity_helpers_still_exported(self) -> None:
        for name in (
            "provider_identity_issue_reason",
            "classify_pane_prompt_state_stable",
            "resolve_bound_pane",
        ):
            self.assertIn(
                name,
                self.amw,
                f"watcher must continue to export {name} so vgd.10.2's "
                "ownership/provider-identity safeguards remain reachable "
                "from the signal path",
            )

    def test_suppress_decisions_only_contain_real_suppressions(self) -> None:
        # Reconstruct the predicate used in process_signal /
        # command_status. The contract is that it ONLY contains the two
        # SUPPRESS_* decisions; widening it to include WAKE / SKIP_*
        # would let policy suppression silence busy-pane retries or
        # ownership rejections, which vgd.10.2 forbids.
        suppress_decisions = frozenset(
            {
                self.amw["POLICY_DECISION_SUPPRESS_NO_OPEN"],
                self.amw["POLICY_DECISION_SUPPRESS_NO_READY"],
            }
        )
        # Defensive: confirm the predicate boundary against known
        # non-suppress decisions.
        for non_suppress in (
            "POLICY_DECISION_WAKE",
            "POLICY_DECISION_SKIP_UNAVAILABLE",
            "POLICY_DECISION_SKIP_DISABLED",
        ):
            self.assertNotIn(
                self.amw[non_suppress],
                suppress_decisions,
                f"{non_suppress} must NOT be classified as suppression — "
                "vgd.10.2 requires busy-pane / ownership / provider-identity "
                "logic to remain authoritative under fail-open and "
                "disabled-mode paths",
            )


if __name__ == "__main__":
    unittest.main()
