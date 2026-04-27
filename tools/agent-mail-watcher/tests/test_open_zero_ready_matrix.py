#!/usr/bin/env python3
"""
Unit tests for the AMW v2 open-but-zero-ready severity matrix.

When a project has `open_count > 0` and `ready_count == 0`, only `high`
and `urgent` may wake; `low` and `normal` must be suppressed terminally
with the `suppress-no-ready-beads` decision. This is the strictly weaker
gate than zero-open: there *is* work in flight, just nothing currently
unblocked, so high-importance pages should still get through.

This file pins that contract in three layers, parallel to
`test_zero_open_matrix.py`:

1. Pure-function decision matrix: `beads_gate_decision` returns
   `(POLICY_DECISION_SUPPRESS_NO_READY, "no-ready-beads")` for low/normal
   and `(POLICY_DECISION_WAKE, None)` for high/urgent.
2. Real-fixture integration: seed a temp beads repo into the
   `open-zero-ready` scenario via the vgd.8.2 helper, read it back
   through `project_work_state_for_target`, and confirm the full watcher
   read path produces the same matrix.
3. State distinguishability: prove the watcher routes open-but-zero-ready
   to `suppress-no-ready-beads` and NOT to `suppress-no-open-beads`,
   since these two states demand different operator responses (work
   exists vs. no work exists).

Run directly:

    python3 tools/agent-mail-watcher/tests/test_open_zero_ready_matrix.py

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


def _make_open_zero_ready_state(amw: dict, *, open_count: int = 1):
    """Construct an available ProjectWorkState in the open-but-zero-ready
    state without running br. open_count is parameterizable so we can pin
    that any positive open count triggers the same decision, not just 1."""
    return amw["ProjectWorkState"](
        repo_root=pathlib.Path("/tmp/fake-open-zero-ready"),
        source="canonical-project",
        available=True,
        open_count=open_count,
        ready_count=0,
        in_progress_count=0,
    )


class TestOpenZeroReadyPureDecision(unittest.TestCase):
    """beads_gate_decision must return the documented matrix for the
    open-but-zero-ready work-state, without any I/O or br subprocess."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.amw = load_amw()
        cls.state = _make_open_zero_ready_state(cls.amw, open_count=1)

    def test_low_in_open_zero_ready_suppresses(self) -> None:
        decision, reason = self.amw["beads_gate_decision"]("low", self.state)
        self.assertEqual(
            decision,
            self.amw["POLICY_DECISION_SUPPRESS_NO_READY"],
            "low must suppress in open-but-zero-ready: it's the most clearly "
            "non-urgent severity and the gate's primary purpose is filtering "
            "low-signal traffic when no work is unblocked",
        )
        self.assertEqual(reason, "no-ready-beads")

    def test_normal_in_open_zero_ready_suppresses(self) -> None:
        decision, reason = self.amw["beads_gate_decision"]("normal", self.state)
        self.assertEqual(
            decision,
            self.amw["POLICY_DECISION_SUPPRESS_NO_READY"],
            "normal must suppress in open-but-zero-ready: AMW-v2.md Phase 4 "
            "lists normal as one of the levels that must be blocked when "
            "open_count > 0 and ready_count == 0",
        )
        self.assertEqual(reason, "no-ready-beads")

    def test_high_in_open_zero_ready_wakes(self) -> None:
        decision, reason = self.amw["beads_gate_decision"]("high", self.state)
        self.assertEqual(
            decision,
            self.amw["POLICY_DECISION_WAKE"],
            "high must wake in open-but-zero-ready: this is exactly the case "
            "where high should break through, since there IS work but nothing "
            "is currently unblocked. AMW-v2.md Phase 4 explicitly contrasts "
            "this with zero-open, where high must NOT break through.",
        )
        self.assertIsNone(reason)

    def test_urgent_in_open_zero_ready_wakes(self) -> None:
        decision, reason = self.amw["beads_gate_decision"]("urgent", self.state)
        self.assertEqual(
            decision,
            self.amw["POLICY_DECISION_WAKE"],
            "urgent must wake in open-but-zero-ready, same as in any other "
            "available state — urgent is the universal wake survivor",
        )
        self.assertIsNone(reason)

    def test_unknown_importance_normalizes_and_suppresses(self) -> None:
        # Unknown importance normalizes to `normal` per AMW-v2.md Phase 4,
        # and `normal` suppresses in open-but-zero-ready, so an unrecognized
        # importance must NOT silently break through as a wake.
        decision, _ = self.amw["beads_gate_decision"]("nonsense", self.state)
        self.assertEqual(
            decision,
            self.amw["POLICY_DECISION_SUPPRESS_NO_READY"],
            "unknown importance must normalize to `normal` and follow the "
            "normal-in-open-zero-ready rule",
        )

    def test_higher_open_count_still_routes_to_no_ready(self) -> None:
        # The decision shouldn't depend on the magnitude of open_count, only
        # on the (open > 0, ready == 0) shape. Pin that with a larger count.
        big_state = _make_open_zero_ready_state(self.amw, open_count=42)
        decision, reason = self.amw["beads_gate_decision"]("normal", big_state)
        self.assertEqual(
            decision,
            self.amw["POLICY_DECISION_SUPPRESS_NO_READY"],
            "the policy is over the (open>0, ready==0) shape; magnitudes "
            "should not change which decision branch fires",
        )
        self.assertEqual(reason, "no-ready-beads")


class TestOpenZeroReadyRealFixtureIntegration(unittest.TestCase):
    """Seed a temp beads repo into the `open-zero-ready` scenario via
    vgd.8.2's helper and run it back through the watcher's actual read
    path. Confirms the pure-decision matrix matches what the watcher
    will see when br reports `summary.open_issues > 0` and
    `summary.ready_issues == 0` (e.g. all open issues are deferred or
    blocked)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.amw = load_amw()
        cls.workspace = (
            pathlib.Path(tempfile.mkdtemp(prefix="vgd92-open-zero-ready-")) / "ws"
        )
        cls.amw["setup_self_test_beads_repo"](cls.workspace)
        counts = cls.amw["seed_self_test_beads_state"](
            cls.workspace, "open-zero-ready"
        )
        if counts != {"open_count": 1, "ready_count": 0, "in_progress_count": 0}:
            raise RuntimeError(f"seed counts mismatch: {counts}")

    def test_observer_read_returns_open_zero_ready_available(self) -> None:
        state = self.amw["project_work_state_for_target"](
            None, str(self.workspace)
        )
        self.assertTrue(
            state.available,
            "seeded open-zero-ready workspace must read back as available; "
            "if this fails, project_work_state_for_target lost the trusted "
            "non-zero open count and would wrongly fail-open",
        )
        self.assertEqual(state.open_count, 1)
        self.assertEqual(state.ready_count, 0)

    def test_normal_against_real_fixture_suppresses(self) -> None:
        state = self.amw["project_work_state_for_target"](
            None, str(self.workspace)
        )
        decision, reason = self.amw["beads_gate_decision"]("normal", state)
        self.assertEqual(
            decision, self.amw["POLICY_DECISION_SUPPRESS_NO_READY"]
        )
        self.assertEqual(reason, "no-ready-beads")

    def test_high_against_real_fixture_breaks_through(self) -> None:
        state = self.amw["project_work_state_for_target"](
            None, str(self.workspace)
        )
        decision, reason = self.amw["beads_gate_decision"]("high", state)
        self.assertEqual(
            decision,
            self.amw["POLICY_DECISION_WAKE"],
            "high must break through against the live br-backed "
            "open-but-zero-ready fixture",
        )
        self.assertIsNone(reason)

    def test_urgent_against_real_fixture_wakes(self) -> None:
        state = self.amw["project_work_state_for_target"](
            None, str(self.workspace)
        )
        decision, reason = self.amw["beads_gate_decision"]("urgent", state)
        self.assertEqual(decision, self.amw["POLICY_DECISION_WAKE"])
        self.assertIsNone(reason)


class TestOpenZeroReadyDistinguishability(unittest.TestCase):
    """The watcher must distinguish open-but-zero-ready from zero-open at
    the decision boundary, because the two states emit different action
    codes and have different operator semantics. Pinning this here guards
    against a future refactor that collapses both into a single
    "no progress" suppression."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.amw = load_amw()

    def test_open_zero_ready_uses_no_ready_action_not_no_open(self) -> None:
        state = _make_open_zero_ready_state(self.amw, open_count=1)
        decision, reason = self.amw["beads_gate_decision"]("normal", state)
        self.assertEqual(
            decision,
            self.amw["POLICY_DECISION_SUPPRESS_NO_READY"],
            "open-but-zero-ready must route to SUPPRESS_NO_READY, not "
            "SUPPRESS_NO_OPEN — operators interpret these differently and "
            "dashboards key on the distinct action strings",
        )
        self.assertNotEqual(
            decision, self.amw["POLICY_DECISION_SUPPRESS_NO_OPEN"]
        )
        self.assertEqual(reason, "no-ready-beads")

    def test_suppress_no_ready_constant_is_stable(self) -> None:
        # The action vocabulary is part of the operator-visible contract.
        # Pin the literal so a rename can't silently break dashboards.
        self.assertEqual(
            self.amw["POLICY_DECISION_SUPPRESS_NO_READY"],
            "suppress-no-ready-beads",
            "AMW-v2.md and vgd.9.2's acceptance criteria reference the "
            "literal action string `suppress-no-ready-beads`; renaming "
            "this without coordinating event-log consumers will break "
            "operator dashboards",
        )

    def test_high_in_open_zero_ready_does_NOT_route_to_suppress(self) -> None:
        # Defensive symmetry: the zero-open tests pin that high suppresses;
        # this test pins that the same `high` does NOT suppress in
        # open-but-zero-ready. A single rule confusion would flip both.
        state = _make_open_zero_ready_state(self.amw, open_count=1)
        decision, _ = self.amw["beads_gate_decision"]("high", state)
        self.assertNotEqual(
            decision,
            self.amw["POLICY_DECISION_SUPPRESS_NO_READY"],
            "high must NOT be classified as suppress-no-ready-beads in "
            "open-but-zero-ready: it's the explicit break-through case",
        )
        self.assertNotEqual(
            decision, self.amw["POLICY_DECISION_SUPPRESS_NO_OPEN"]
        )
        self.assertEqual(decision, self.amw["POLICY_DECISION_WAKE"])


if __name__ == "__main__":
    unittest.main()
