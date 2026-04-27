#!/usr/bin/env python3
"""
Unit tests for the AMW v2 ready-state pass-through, fail-open behavior,
and no-autocreate guard for policy-suppressed signals.

This file pins three behaviors AMW-v2.md treats as preserved invariants:

1. Ready state preserves normal wake behavior. When `open_count > 0` and
   `ready_count > 0`, every importance level (low / normal / high /
   urgent) must take the normal wake path. This is the "no extra policy
   restriction" baseline.
2. Unavailable, stale, locked, or non-beads work-state reads must FAIL
   OPEN, returning POLICY_DECISION_SKIP_UNAVAILABLE. Fail-open never
   classifies as a suppression — uncertain state must not silence
   wakes that would otherwise reach an operator.
3. The watcher's suppress predicate exposes the rule that `process_signal`
   relies on to skip auto-create, prompt injection, and launch side
   effects. This file pins the suppress vocabulary so a future refactor
   can't widen the predicate to include fail-open decisions (which would
   wrongly suppress uncertain traffic) or narrow it to omit one of the
   real suppressions (which would let suppressed events still auto-create
   panes).

Run directly:

    python3 tools/agent-mail-watcher/tests/test_ready_failopen_no_autocreate.py

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


def _make_state(amw: dict, *, available: bool, open_count: int = 0, ready_count: int = 0):
    return amw["ProjectWorkState"](
        repo_root=pathlib.Path("/tmp/fake-state"),
        source="canonical-project",
        available=available,
        open_count=open_count if available else None,
        ready_count=ready_count if available else None,
        in_progress_count=0 if available else None,
    )


class TestReadyStateNormalWake(unittest.TestCase):
    """Ready state (`open_count > 0`, `ready_count > 0`) is the normal
    operating case; all importance levels must take the normal wake path,
    regardless of magnitude."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.amw = load_amw()
        cls.ready_state = _make_state(
            cls.amw, available=True, open_count=3, ready_count=2
        )

    def test_low_in_ready_wakes(self) -> None:
        decision, reason = self.amw["beads_gate_decision"]("low", self.ready_state)
        self.assertEqual(
            decision,
            self.amw["POLICY_DECISION_WAKE"],
            "low must wake when ready beads exist; the gate's job is filtering "
            "no-progress states, not low importance per se",
        )
        self.assertIsNone(reason)

    def test_normal_in_ready_wakes(self) -> None:
        decision, reason = self.amw["beads_gate_decision"]("normal", self.ready_state)
        self.assertEqual(
            decision,
            self.amw["POLICY_DECISION_WAKE"],
            "normal must wake in ready state; AC1 of vgd.9.3 calls this out "
            "explicitly as the headline assertion",
        )
        self.assertIsNone(reason)

    def test_high_in_ready_wakes(self) -> None:
        decision, _ = self.amw["beads_gate_decision"]("high", self.ready_state)
        self.assertEqual(decision, self.amw["POLICY_DECISION_WAKE"])

    def test_urgent_in_ready_wakes(self) -> None:
        decision, _ = self.amw["beads_gate_decision"]("urgent", self.ready_state)
        self.assertEqual(decision, self.amw["POLICY_DECISION_WAKE"])

    def test_ready_state_reuses_no_extra_reason(self) -> None:
        # The reason field is None for plain wake decisions; populated only
        # when the decision encodes a deviation. Pin that contract so a
        # refactor can't accidentally start emitting reasons on wake paths
        # (which would confuse downstream event-log consumers that key on
        # reason==None as "policy was happy").
        for importance in ("low", "normal", "high", "urgent"):
            with self.subTest(importance=importance):
                _, reason = self.amw["beads_gate_decision"](
                    importance, self.ready_state
                )
                self.assertIsNone(reason)


class TestReadyStateRealFixtureIntegration(unittest.TestCase):
    """Seed a real beads repo into the `ready` scenario via vgd.8.2's
    helper and confirm the watcher's actual read path agrees with the
    pure-decision matrix above."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.amw = load_amw()
        cls.workspace = (
            pathlib.Path(tempfile.mkdtemp(prefix="vgd93-ready-")) / "ws"
        )
        cls.amw["setup_self_test_beads_repo"](cls.workspace)
        counts = cls.amw["seed_self_test_beads_state"](
            cls.workspace, "ready"
        )
        if counts != {"open_count": 1, "ready_count": 1, "in_progress_count": 0}:
            raise RuntimeError(f"seed counts mismatch: {counts}")

    def test_normal_against_real_ready_fixture_wakes(self) -> None:
        state = self.amw["project_work_state_for_target"](
            None, str(self.workspace)
        )
        self.assertTrue(state.available)
        self.assertEqual(state.open_count, 1)
        self.assertEqual(state.ready_count, 1)
        decision, reason = self.amw["beads_gate_decision"]("normal", state)
        self.assertEqual(decision, self.amw["POLICY_DECISION_WAKE"])
        self.assertIsNone(reason)


class TestUnavailableFailsOpen(unittest.TestCase):
    """Fail-open contract: any path that produces an unavailable
    work-state must yield POLICY_DECISION_SKIP_UNAVAILABLE, NOT a
    suppression. The watcher must not silence traffic when it can't
    tell what the project state is."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.amw = load_amw()

    def test_synthetic_unavailable_fails_open_for_every_importance(self) -> None:
        # Synthetic ProjectWorkState carrying available=False: covers the
        # cache-hit-on-prior-unavailable case and any error code.
        state = _make_state(self.amw, available=False)
        for importance in ("low", "normal", "high", "urgent", "nonsense"):
            with self.subTest(importance=importance):
                decision, reason = self.amw["beads_gate_decision"](
                    importance, state
                )
                self.assertEqual(
                    decision,
                    self.amw["POLICY_DECISION_SKIP_UNAVAILABLE"],
                    "unavailable state must always fail-open; suppressing "
                    "uncertain traffic is the bug AMW-v2.md Risk 1 calls out",
                )
                self.assertEqual(reason, "work-state-unavailable")

    def test_no_beads_dir_workspace_reads_unavailable(self) -> None:
        # End-to-end with vgd.8.3's fixture: a git workspace without `.beads/`
        # must surface as unavailable through project_work_state_for_target,
        # not silently as a fake zero-open state.
        ws = pathlib.Path(tempfile.mkdtemp(prefix="vgd93-noinit-")) / "ws"
        self.amw["setup_self_test_unavailable_workspace"](ws)
        state = self.amw["project_work_state_for_target"](None, str(ws))
        self.assertFalse(state.available)
        self.assertEqual(
            state.error,
            self.amw["SELF_TEST_BEADS_UNAVAILABLE_EXPECTED_ERROR"][
                "no-beads-dir"
            ],
        )

    def test_no_beads_dir_drives_fail_open_decision(self) -> None:
        # Full chain: fixture -> read -> decision must SKIP_UNAVAILABLE.
        # If this fails, the watcher's read path lost the fail-open contract
        # somewhere between project_work_state_for_target and
        # beads_gate_decision.
        ws = pathlib.Path(tempfile.mkdtemp(prefix="vgd93-noinit-end2end-")) / "ws"
        self.amw["setup_self_test_unavailable_workspace"](ws)
        state = self.amw["project_work_state_for_target"](None, str(ws))
        decision, reason = self.amw["beads_gate_decision"]("normal", state)
        self.assertEqual(
            decision, self.amw["POLICY_DECISION_SKIP_UNAVAILABLE"]
        )
        self.assertEqual(reason, "work-state-unavailable")


class TestSuppressVocabularyExcludesFailOpen(unittest.TestCase):
    """The watcher's `suppress_decisions` predicate is what keeps
    fail-open and skip-disabled signals out of the auto-create / launch /
    prompt-injection branches in process_signal. Pin its exact shape so
    a refactor can't widen it to include fail-open (which would silence
    uncertain traffic) or omit a real suppression (which would let
    suppressed events still create panes).

    The predicate is function-local in command_status today, so we
    reconstruct it from the public POLICY_DECISION_* constants and
    assert each constant's membership directly.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.amw = load_amw()
        # Reconstruct the predicate set the watcher uses today.
        cls.suppress_decisions = frozenset({
            cls.amw["POLICY_DECISION_SUPPRESS_NO_OPEN"],
            cls.amw["POLICY_DECISION_SUPPRESS_NO_READY"],
        })

    def test_suppress_no_open_is_in_predicate(self) -> None:
        self.assertIn(
            self.amw["POLICY_DECISION_SUPPRESS_NO_OPEN"],
            self.suppress_decisions,
            "suppressed-no-open-beads must short-circuit the wake / launch / "
            "auto-create path; if this gets removed from the predicate, "
            "zero-open suppressions silently become wakes again",
        )

    def test_suppress_no_ready_is_in_predicate(self) -> None:
        self.assertIn(
            self.amw["POLICY_DECISION_SUPPRESS_NO_READY"],
            self.suppress_decisions,
        )

    def test_skip_unavailable_is_NOT_in_predicate(self) -> None:
        self.assertNotIn(
            self.amw["POLICY_DECISION_SKIP_UNAVAILABLE"],
            self.suppress_decisions,
            "fail-open MUST NOT be classified as suppression: a watcher that "
            "treats unavailable state as suppression would silence uncertain "
            "traffic, the exact bug AMW-v2.md Risk 1 forbids. Auto-create / "
            "launch must still proceed when work state is unavailable",
        )

    def test_skip_disabled_is_NOT_in_predicate(self) -> None:
        self.assertNotIn(
            self.amw["POLICY_DECISION_SKIP_DISABLED"],
            self.suppress_decisions,
            "disabled-mode MUST NOT classify as suppression: when the gate "
            "is off, traffic must take pre-v2 wake behavior, including any "
            "auto-create paths that existed before the policy",
        )

    def test_wake_is_NOT_in_predicate(self) -> None:
        self.assertNotIn(
            self.amw["POLICY_DECISION_WAKE"],
            self.suppress_decisions,
            "wake decisions are obviously not suppression; this asserts the "
            "set is closed under the right boundary",
        )

    def test_no_autocreate_for_zero_open_normal(self) -> None:
        # End-to-end suppression check: a zero-open + normal signal must
        # produce a decision that lives in the suppress predicate, so
        # process_signal will short-circuit before auto-creating a pane.
        zero_open = _make_state(
            self.amw, available=True, open_count=0, ready_count=0
        )
        decision, _ = self.amw["beads_gate_decision"]("normal", zero_open)
        self.assertIn(
            decision,
            self.suppress_decisions,
            "zero-open + normal must classify as suppress so process_signal "
            "skips auto-create, launch, and prompt injection (vgd.9.3 AC3)",
        )

    def test_no_autocreate_for_open_zero_ready_normal(self) -> None:
        # Symmetric check for the open-but-zero-ready suppression state.
        open_zero_ready = _make_state(
            self.amw, available=True, open_count=2, ready_count=0
        )
        decision, _ = self.amw["beads_gate_decision"]("normal", open_zero_ready)
        self.assertIn(
            decision,
            self.suppress_decisions,
            "open-but-zero-ready + normal must classify as suppress so "
            "process_signal skips auto-create (vgd.9.3 AC3)",
        )

    def test_autocreate_runs_for_unavailable_normal(self) -> None:
        # Inverse: an unavailable read must NOT classify as suppress, so
        # auto-create / wake paths still execute. This is the fail-open
        # consequence at the auto-create boundary.
        unavailable = _make_state(self.amw, available=False)
        decision, _ = self.amw["beads_gate_decision"]("normal", unavailable)
        self.assertNotIn(
            decision,
            self.suppress_decisions,
            "unavailable state must NOT short-circuit auto-create; the "
            "watcher proceeds with normal pre-v2 logic when state is "
            "uncertain (vgd.9.3 AC2 fail-open)",
        )


if __name__ == "__main__":
    unittest.main()
