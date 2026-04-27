#!/usr/bin/env python3
"""
Unit tests for the AMW v2 zero-open severity matrix.

When a project has `open_count == 0`, only `urgent` messages may wake an
agent; `low`, `normal`, and `high` must be suppressed terminally with the
`suppress-no-open-beads` decision. AMW-v2.md Phase 4 calls this out
explicitly: `high` must NOT break through in the zero-open state.

This file pins that contract in three layers:

1. Pure-function decision matrix: `beads_gate_decision` returns
   `(POLICY_DECISION_SUPPRESS_NO_OPEN, "no-open-beads")` for non-urgent
   importance levels and `(POLICY_DECISION_WAKE, None)` for `urgent`.
2. Real-fixture integration: seed a temp beads repo into the `zero-open`
   scenario via the vgd.8.2 helper, read back through
   `project_work_state_for_target`, and confirm the full watcher read path
   produces the same matrix.
3. Action-code vocabulary: the literal string `suppress-no-open-beads`
   is the stable contract for event-log and status consumers; pin it so
   future refactors don't silently rename the action and break dashboards.

Run directly:

    python3 tools/agent-mail-watcher/tests/test_zero_open_matrix.py

Or via unittest discovery:

    python3 -m unittest discover -s tools/agent-mail-watcher/tests
"""

from __future__ import annotations

import pathlib
import runpy
import tempfile
import unittest

WATCHER_DIR = pathlib.Path(__file__).resolve().parent.parent
WATCHER_PATH = WATCHER_DIR / "agent-mail-watcher"


def load_amw() -> dict:
    return runpy.run_path(str(WATCHER_PATH))


def _make_work_state(amw: dict, *, available: bool, open_count: int, ready_count: int):
    """Construct a ProjectWorkState directly without running br."""
    return amw["ProjectWorkState"](
        repo_root=pathlib.Path("/tmp/fake-zero-open"),
        source="canonical-project",
        available=available,
        open_count=open_count if available else None,
        ready_count=ready_count if available else None,
        in_progress_count=0 if available else None,
    )


class TestZeroOpenPureDecision(unittest.TestCase):
    """beads_gate_decision must return the documented matrix for zero-open
    work-state, independent of any I/O or br subprocess."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.amw = load_amw()
        cls.zero_open = _make_work_state(
            cls.amw, available=True, open_count=0, ready_count=0
        )

    def test_low_in_zero_open_suppresses(self) -> None:
        decision, reason = self.amw["beads_gate_decision"]("low", self.zero_open)
        self.assertEqual(
            decision,
            self.amw["POLICY_DECISION_SUPPRESS_NO_OPEN"],
            "low must suppress in zero-open: low importance is the most clearly "
            "non-urgent severity and would be the first thing to leak through "
            "if the gate misfires",
        )
        self.assertEqual(reason, "no-open-beads")

    def test_normal_in_zero_open_suppresses(self) -> None:
        decision, reason = self.amw["beads_gate_decision"]("normal", self.zero_open)
        self.assertEqual(
            decision,
            self.amw["POLICY_DECISION_SUPPRESS_NO_OPEN"],
            "normal must suppress in zero-open: AMW-v2.md Phase 4 lists normal "
            "as one of the levels that must be blocked when open_count == 0",
        )
        self.assertEqual(reason, "no-open-beads")

    def test_high_in_zero_open_suppresses(self) -> None:
        decision, reason = self.amw["beads_gate_decision"]("high", self.zero_open)
        self.assertEqual(
            decision,
            self.amw["POLICY_DECISION_SUPPRESS_NO_OPEN"],
            "high MUST NOT break through in zero-open: AMW-v2.md Phase 4 calls "
            "this out explicitly. high is allowed only when open_count > 0 and "
            "ready_count == 0",
        )
        self.assertEqual(reason, "no-open-beads")

    def test_urgent_in_zero_open_wakes(self) -> None:
        decision, reason = self.amw["beads_gate_decision"]("urgent", self.zero_open)
        self.assertEqual(
            decision,
            self.amw["POLICY_DECISION_WAKE"],
            "urgent must wake even when open_count == 0: this is the only "
            "severity that survives the zero-open gate so completed projects "
            "can still receive critical pages",
        )
        self.assertIsNone(reason, "wake decisions emit None as the reason")

    def test_unknown_importance_normalizes_and_suppresses(self) -> None:
        # AMW-v2.md Phase 4 says unknown importance normalizes to `normal`,
        # which must suppress in zero-open. This guards against typo'd or
        # provider-injected importance values silently becoming wakes.
        decision, _ = self.amw["beads_gate_decision"]("nonsense", self.zero_open)
        self.assertEqual(
            decision,
            self.amw["POLICY_DECISION_SUPPRESS_NO_OPEN"],
            "unknown importance must normalize to `normal` and follow the "
            "normal-in-zero-open rule, not break through as a wake",
        )


class TestZeroOpenRealFixtureIntegration(unittest.TestCase):
    """Seed a temp beads repo into the `zero-open` scenario and run it back
    through the watcher's actual read path. Proves that the pure-decision
    matrix above matches what the watcher will see in production when br
    reports `summary.open_issues == 0`."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.amw = load_amw()
        cls.workspace = (
            pathlib.Path(tempfile.mkdtemp(prefix="vgd91-zero-open-")) / "ws"
        )
        cls.amw["setup_self_test_beads_repo"](cls.workspace)
        counts = cls.amw["seed_self_test_beads_state"](
            cls.workspace, "zero-open"
        )
        # vgd.8.2 already asserts these inside the helper; re-asserting here
        # makes the test surface a clear setUp failure rather than letting
        # later assertions fail with a confusing decision-mismatch.
        if counts != {"open_count": 0, "ready_count": 0, "in_progress_count": 0}:
            raise RuntimeError(f"seed counts mismatch: {counts}")

    def test_observer_read_returns_zero_open_available(self) -> None:
        state = self.amw["project_work_state_for_target"](
            None, str(self.workspace)
        )
        self.assertTrue(
            state.available,
            "seeded `zero-open` workspace must read back as available; "
            "if this fails, project_work_state_for_target lost the trusted "
            "zero counts and would wrongly fail-open instead of suppressing",
        )
        self.assertEqual(state.open_count, 0)
        self.assertEqual(state.ready_count, 0)

    def test_normal_against_real_fixture_suppresses_terminally(self) -> None:
        state = self.amw["project_work_state_for_target"](
            None, str(self.workspace)
        )
        decision, reason = self.amw["beads_gate_decision"]("normal", state)
        self.assertEqual(
            decision, self.amw["POLICY_DECISION_SUPPRESS_NO_OPEN"]
        )
        self.assertEqual(reason, "no-open-beads")

    def test_high_against_real_fixture_does_not_break_through(self) -> None:
        state = self.amw["project_work_state_for_target"](
            None, str(self.workspace)
        )
        decision, reason = self.amw["beads_gate_decision"]("high", state)
        self.assertEqual(
            decision,
            self.amw["POLICY_DECISION_SUPPRESS_NO_OPEN"],
            "high must not break through against a live br-backed zero-open "
            "fixture either",
        )
        self.assertEqual(reason, "no-open-beads")

    def test_urgent_against_real_fixture_wakes(self) -> None:
        state = self.amw["project_work_state_for_target"](
            None, str(self.workspace)
        )
        decision, reason = self.amw["beads_gate_decision"]("urgent", state)
        self.assertEqual(decision, self.amw["POLICY_DECISION_WAKE"])
        self.assertIsNone(reason)


class TestZeroOpenActionVocabularyContract(unittest.TestCase):
    """The literal action/reason strings are the stable contract for
    operators searching event logs and status output. Lock them down so a
    refactor that renames the action (e.g. to "no-beads-suppression")
    cannot silently break dashboards or watcher self-tests downstream."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.amw = load_amw()

    def test_suppress_no_open_constant_is_stable(self) -> None:
        self.assertEqual(
            self.amw["POLICY_DECISION_SUPPRESS_NO_OPEN"],
            "suppress-no-open-beads",
            "AMW-v2.md and vgd.9.1's acceptance criteria both reference the "
            "literal action string `suppress-no-open-beads`; renaming this "
            "without coordinating event-log consumers will break operator "
            "dashboards",
        )

    def test_zero_open_reason_is_stable(self) -> None:
        zero = _make_work_state(
            self.amw, available=True, open_count=0, ready_count=0
        )
        _, reason = self.amw["beads_gate_decision"]("normal", zero)
        self.assertEqual(
            reason,
            "no-open-beads",
            "Reason strings are also part of the operator-visible vocabulary "
            "and feed status explanations; pin them too",
        )


if __name__ == "__main__":
    unittest.main()
