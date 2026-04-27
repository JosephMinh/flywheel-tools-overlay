#!/usr/bin/env python3
"""
Focused unit tests for normalize_importance() and beads_gate_decision()
so the severity matrix and defaulting rules are cheap to validate during
local iteration.

normalize_importance() is the gate's first line of defense against
unexpected importance strings: it lowercases, strips, and falls back to
`normal` when the input isn't in the canonical vocabulary. A regression
here (e.g. accidentally honoring "warning" or "critical" as a wake
trigger) would change which messages reach operators silently.

beads_gate_decision() takes (importance, work_state) and returns
(decision, reason). vgd.9.1 / vgd.9.2 / vgd.9.3 each cover a single
work-state slice in depth; this file covers the matrix as a single
table-driven test so a future reader can see the full contract on one
screen, with assertion messages that name the exact (importance,
work-state) cell that broke.

Run directly:

    python3 tools/agent-mail-watcher/tests/test_importance_and_policy.py

Or via unittest discovery:

    python3 -m unittest discover -s tools/agent-mail-watcher/tests
"""

from __future__ import annotations

import pathlib
import runpy
import unittest

WATCHER_DIR = pathlib.Path(__file__).resolve().parent.parent
WATCHER_PATH = WATCHER_DIR / "agent-mail-watcher"


def load_amw() -> dict:
    return runpy.run_path(str(WATCHER_PATH))


def _make_state(amw: dict, *, available: bool, open_count: int = 0, ready_count: int = 0):
    return amw["ProjectWorkState"](
        repo_root=pathlib.Path("/tmp/fake"),
        source="canonical-project",
        available=available,
        open_count=open_count if available else None,
        ready_count=ready_count if available else None,
        in_progress_count=0 if available else None,
    )


class TestNormalizeImportance(unittest.TestCase):
    """normalize_importance must accept the canonical vocabulary verbatim,
    strip+lowercase tolerable variants, and fall back to `normal` for
    anything else (including missing/empty/None inputs)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.amw = load_amw()
        # staticmethod keeps Python's descriptor protocol from binding `self`
        # as the first argument when we call cls.normalize / self.normalize.
        cls.normalize = staticmethod(cls.amw["normalize_importance"])

    def test_canonical_values_pass_through(self) -> None:
        for value in ("low", "normal", "high", "urgent"):
            with self.subTest(importance=value):
                self.assertEqual(
                    self.normalize({"importance": value}),
                    value,
                    f"canonical importance {value!r} must pass through unchanged",
                )

    def test_uppercase_is_normalized(self) -> None:
        # Senders in different ecosystems format severities differently;
        # the watcher must accept "HIGH" and "Urgent" as the same as their
        # lowercase canonical forms rather than falling back to `normal`.
        self.assertEqual(self.normalize({"importance": "HIGH"}), "high")
        self.assertEqual(self.normalize({"importance": "Urgent"}), "urgent")
        self.assertEqual(self.normalize({"importance": "NoRmAl"}), "normal")

    def test_surrounding_whitespace_is_stripped(self) -> None:
        self.assertEqual(self.normalize({"importance": "  high "}), "high")
        self.assertEqual(self.normalize({"importance": "\turgent\n"}), "urgent")

    def test_none_message_returns_normal(self) -> None:
        # None is a real input shape: process_signal calls this with
        # message=None when the signal payload is malformed. Must not crash.
        self.assertEqual(
            self.normalize(None),
            "normal",
            "None message must default to `normal`, not raise",
        )

    def test_empty_dict_returns_normal(self) -> None:
        self.assertEqual(self.normalize({}), "normal")

    def test_missing_importance_key_returns_normal(self) -> None:
        # An older message format may omit the importance key entirely.
        self.assertEqual(
            self.normalize({"id": 1, "subject": "x"}),
            "normal",
            "missing importance key must default to `normal`, never `low`",
        )

    def test_empty_string_returns_normal(self) -> None:
        self.assertEqual(self.normalize({"importance": ""}), "normal")

    def test_whitespace_only_returns_normal(self) -> None:
        self.assertEqual(self.normalize({"importance": "   "}), "normal")

    def test_unknown_value_returns_normal(self) -> None:
        # Adjacent vocabularies shouldn't sneak through. AMW v2 is
        # deliberately narrow about which strings wake.
        for value in ("warning", "critical", "info", "debug", "important"):
            with self.subTest(importance=value):
                self.assertEqual(
                    self.normalize({"importance": value}),
                    "normal",
                    f"non-canonical importance {value!r} must normalize to "
                    "`normal`, never silently bypass the gate",
                )

    def test_numeric_importance_returns_normal(self) -> None:
        # str() conversion runs before lowercase; ensure non-string inputs
        # don't crash and don't accidentally match.
        self.assertEqual(self.normalize({"importance": 1}), "normal")
        self.assertEqual(self.normalize({"importance": 0}), "normal")
        self.assertEqual(self.normalize({"importance": True}), "normal")

    def test_none_importance_value_returns_normal(self) -> None:
        # Explicit None for the field, not a missing key.
        self.assertEqual(self.normalize({"importance": None}), "normal")

    def test_normalized_vocabulary_constant_is_stable(self) -> None:
        # Pin the canonical vocabulary so a future addition (e.g. "panic")
        # is a deliberate code change with reviewable test coverage rather
        # than an accidental constant tweak.
        self.assertEqual(
            self.amw["NORMALIZED_IMPORTANCE_VALUES"],
            ("low", "normal", "high", "urgent"),
            "NORMALIZED_IMPORTANCE_VALUES is the canonical severity "
            "vocabulary; expanding it requires explicit policy review",
        )


class TestPolicyDecisionMatrix(unittest.TestCase):
    """Table-driven sweep of beads_gate_decision over the full
    (importance, work-state) cross product. Each subtest names the
    exact cell so a failure points straight at the regression."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.amw = load_amw()
        cls.decide = staticmethod(cls.amw["beads_gate_decision"])
        cls.WAKE = cls.amw["POLICY_DECISION_WAKE"]
        cls.SUPP_NO_OPEN = cls.amw["POLICY_DECISION_SUPPRESS_NO_OPEN"]
        cls.SUPP_NO_READY = cls.amw["POLICY_DECISION_SUPPRESS_NO_READY"]
        cls.SKIP_UNAVAIL = cls.amw["POLICY_DECISION_SKIP_UNAVAILABLE"]

        cls.ready = _make_state(cls.amw, available=True, open_count=3, ready_count=2)
        cls.zero_open = _make_state(cls.amw, available=True, open_count=0, ready_count=0)
        cls.open_zero_ready = _make_state(cls.amw, available=True, open_count=2, ready_count=0)
        cls.unavailable = _make_state(cls.amw, available=False)

    def test_full_matrix(self) -> None:
        # cell layout: (state_name, work_state, {importance: (decision, reason)})
        cases = [
            (
                "ready",
                self.ready,
                {
                    "low": (self.WAKE, None),
                    "normal": (self.WAKE, None),
                    "high": (self.WAKE, None),
                    "urgent": (self.WAKE, None),
                },
            ),
            (
                "zero-open",
                self.zero_open,
                {
                    "low": (self.SUPP_NO_OPEN, "no-open-beads"),
                    "normal": (self.SUPP_NO_OPEN, "no-open-beads"),
                    "high": (self.SUPP_NO_OPEN, "no-open-beads"),
                    "urgent": (self.WAKE, None),
                },
            ),
            (
                "open-zero-ready",
                self.open_zero_ready,
                {
                    "low": (self.SUPP_NO_READY, "no-ready-beads"),
                    "normal": (self.SUPP_NO_READY, "no-ready-beads"),
                    "high": (self.WAKE, None),
                    "urgent": (self.WAKE, None),
                },
            ),
            (
                "unavailable",
                self.unavailable,
                {
                    "low": (self.SKIP_UNAVAIL, "work-state-unavailable"),
                    "normal": (self.SKIP_UNAVAIL, "work-state-unavailable"),
                    "high": (self.SKIP_UNAVAIL, "work-state-unavailable"),
                    "urgent": (self.SKIP_UNAVAIL, "work-state-unavailable"),
                },
            ),
        ]
        for state_name, state, importance_table in cases:
            for importance, (want_decision, want_reason) in importance_table.items():
                with self.subTest(state=state_name, importance=importance):
                    decision, reason = self.decide(importance, state)
                    self.assertEqual(
                        decision,
                        want_decision,
                        f"[{state_name}/{importance}] decision: "
                        f"got {decision!r}, expected {want_decision!r}",
                    )
                    self.assertEqual(
                        reason,
                        want_reason,
                        f"[{state_name}/{importance}] reason: "
                        f"got {reason!r}, expected {want_reason!r}",
                    )

    def test_unknown_importance_normalizes_to_normal_in_each_state(self) -> None:
        # When importance is unknown, the gate must behave as if the
        # message were `normal`. This is what protects the watcher from
        # silently treating "critical" as a `low`-equivalent escape hatch.
        for state_name, state, want in (
            ("ready", self.ready, (self.WAKE, None)),
            ("zero-open", self.zero_open, (self.SUPP_NO_OPEN, "no-open-beads")),
            ("open-zero-ready", self.open_zero_ready, (self.SUPP_NO_READY, "no-ready-beads")),
            ("unavailable", self.unavailable, (self.SKIP_UNAVAIL, "work-state-unavailable")),
        ):
            with self.subTest(state=state_name):
                decision, reason = self.decide("not-a-real-severity", state)
                self.assertEqual(
                    (decision, reason),
                    want,
                    f"[{state_name}/unknown] unknown importance must "
                    "normalize to `normal` and follow that row",
                )


class TestImportanceAndPolicyConsistency(unittest.TestCase):
    """Cross-check: the policy decision must be invariant to whether the
    caller passes a canonical importance string or a typed-but-equivalent
    value that normalize_importance would reduce to the same thing."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.amw = load_amw()
        cls.zero_open = _make_state(
            cls.amw, available=True, open_count=0, ready_count=0
        )

    def test_uppercase_high_is_treated_as_canonical_high(self) -> None:
        # High is the famous "must NOT break through in zero-open" rule.
        # If someone sends importance="HIGH" instead of "high", the gate
        # must still suppress — otherwise dashboards could be circumvented
        # by a single capital letter.
        decision_canonical, _ = self.amw["beads_gate_decision"](
            "high", self.zero_open
        )
        # beads_gate_decision normalizes internally; verify both forms match.
        decision_upper, _ = self.amw["beads_gate_decision"](
            "HIGH", self.zero_open
        )
        self.assertEqual(
            decision_canonical,
            decision_upper,
            "uppercase HIGH must produce the same decision as canonical "
            "high; otherwise the gate is bypassable via case",
        )
        self.assertEqual(
            decision_canonical, self.amw["POLICY_DECISION_SUPPRESS_NO_OPEN"]
        )


if __name__ == "__main__":
    unittest.main()
