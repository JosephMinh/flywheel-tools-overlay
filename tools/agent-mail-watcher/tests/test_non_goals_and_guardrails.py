#!/usr/bin/env python3
"""
Regression tests for AMW v2 non-goals and preserved guardrails.

These tests pin the rules from `AMW-v2.md` that the beads gate must NOT change
and the existing safeguards that must remain authoritative on top of the gate.
A failure here means a refactor silently weakened a guardrail or accidentally
made the gate consult a field it was never meant to consult.

Run directly:

    python3 tools/agent-mail-watcher/tests/test_non_goals_and_guardrails.py

Non-goals covered:
- `ack_required` does not influence beads-gate decisions.
- `thread_id` does not influence beads-gate decisions.
- The decision function structurally cannot read message-shaped attributes.

Guardrails covered:
- `provider_identity_issue_reason`, `provider_identity_conflict_reason`,
  `resolve_bound_pane`, and `classify_pane_prompt_state_stable` remain
  importable from the watcher.
- The pre-v2 `suppressed-working-pane` event action name is still emitted
  literally in the watcher source.
- The live `status` JSON keeps surfacing `ownership_conflict_reason`,
  `provider_identity_issue_reason`, `wake_deliverable`, and `prompt_state`
  alongside the new beads-gate fields.
"""

from __future__ import annotations

import inspect
import json
import pathlib
import runpy
import subprocess
import unittest

WATCHER_DIR = pathlib.Path(__file__).resolve().parent.parent
WATCHER_PATH = WATCHER_DIR / "agent-mail-watcher"


def load_amw() -> dict:
    return runpy.run_path(str(WATCHER_PATH))


class TestAckRequiredAndThreadIdAreNonInfluential(unittest.TestCase):
    """Non-goal: the gate must not change behavior based on ack_required or thread_id."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.amw = load_amw()

    @property
    def normalize_importance(self):
        # Wrap dict lookup in a property so unittest does not bind the
        # function as a method (which would inject self as a positional arg).
        return self.amw["normalize_importance"]

    def test_normalize_importance_ignores_ack_required(self) -> None:
        with_ack = self.normalize_importance(
            {"importance": "normal", "ack_required": True}
        )
        without_ack = self.normalize_importance(
            {"importance": "normal", "ack_required": False}
        )
        no_field = self.normalize_importance({"importance": "normal"})
        self.assertEqual(
            with_ack,
            without_ack,
            "ack_required must not change normalize_importance output — that "
            "would break the AMW-v2.md non-goal that wake policy is decoupled "
            "from message acknowledgement",
        )
        self.assertEqual(with_ack, no_field)

    def test_normalize_importance_ignores_thread_id(self) -> None:
        with_thread = self.normalize_importance(
            {"importance": "high", "thread_id": "thread-abc-123"}
        )
        without_thread = self.normalize_importance({"importance": "high"})
        self.assertEqual(
            with_thread,
            without_thread,
            "thread_id must not change normalize_importance output — that "
            "would break the AMW-v2.md non-goal that wake policy is decoupled "
            "from threading",
        )

    def test_normalize_importance_only_reads_importance_key(self) -> None:
        # A message stuffed with extras should normalize the same way as a
        # bare {"importance": ...} message.
        bare = self.normalize_importance({"importance": "urgent"})
        stuffed = self.normalize_importance(
            {
                "importance": "urgent",
                "ack_required": True,
                "thread_id": "x",
                "subject": "y",
                "from": "z",
                "id": 12345,
                "body_md": "hello",
            }
        )
        self.assertEqual(
            bare,
            stuffed,
            "normalize_importance must read only the importance field; any "
            "other field influencing it would couple wake policy to a "
            "message attribute the plan explicitly excludes",
        )

    def test_beads_gate_decision_signature_takes_only_importance_and_work_state(
        self,
    ) -> None:
        decision = self.amw["beads_gate_decision"]
        sig = inspect.signature(decision)
        params = list(sig.parameters.keys())
        self.assertEqual(
            params,
            ["importance", "work_state"],
            "beads_gate_decision must take only (importance, work_state). "
            "Any extra parameter would let message attributes like "
            "ack_required/thread_id leak into the gate decision.",
        )

    def test_beads_gate_decision_is_pure_in_importance_and_work_state(self) -> None:
        # Calling the decision with the same (importance, work_state) twice
        # must yield the same result regardless of any environment around it.
        decision = self.amw["beads_gate_decision"]
        ProjectWorkState = self.amw["ProjectWorkState"]
        ws = ProjectWorkState(
            repo_root=None,
            source="canonical-project",
            available=True,
            open_count=0,
            ready_count=0,
            in_progress_count=0,
        )
        first = decision("normal", ws)
        second = decision("normal", ws)
        self.assertEqual(
            first,
            second,
            "beads_gate_decision must be deterministic for the same inputs; "
            "non-determinism would imply hidden state influencing the gate",
        )


class TestGuardrailFunctionsRemainImportable(unittest.TestCase):
    """Pre-v2 guardrail helpers must still be present in the watcher."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.amw = load_amw()

    def test_provider_identity_issue_reason_present(self) -> None:
        self.assertIn(
            "provider_identity_issue_reason",
            self.amw,
            "provider_identity_issue_reason was removed; provider-identity "
            "checks must remain authoritative regardless of the beads gate",
        )
        self.assertTrue(callable(self.amw["provider_identity_issue_reason"]))

    def test_provider_identity_conflict_reason_present(self) -> None:
        self.assertIn(
            "provider_identity_conflict_reason",
            self.amw,
            "provider_identity_conflict_reason was removed; shared-provider-"
            "thread conflict detection must survive the beads-gate work",
        )
        self.assertTrue(callable(self.amw["provider_identity_conflict_reason"]))

    def test_resolve_bound_pane_present(self) -> None:
        self.assertIn(
            "resolve_bound_pane",
            self.amw,
            "resolve_bound_pane was removed; pane ownership checks must "
            "remain authoritative",
        )
        self.assertTrue(callable(self.amw["resolve_bound_pane"]))

    def test_classify_pane_prompt_state_stable_present(self) -> None:
        self.assertIn(
            "classify_pane_prompt_state_stable",
            self.amw,
            "classify_pane_prompt_state_stable was removed; busy/working/"
            "idle classification must remain the source of truth for "
            "wake_deliverable",
        )
        self.assertTrue(callable(self.amw["classify_pane_prompt_state_stable"]))

    def test_deliver_prompt_to_live_pane_present(self) -> None:
        self.assertIn(
            "deliver_prompt_to_live_pane",
            self.amw,
            "deliver_prompt_to_live_pane was removed; busy suppression "
            "happens here, so it must remain after the beads gate work",
        )
        self.assertTrue(callable(self.amw["deliver_prompt_to_live_pane"]))


class TestSuppressionEventVocabularyIsStable(unittest.TestCase):
    """Pre-v2 suppression action names must still be emitted by the watcher."""

    PRE_V2_ACTION_NAMES = (
        "suppressed-working-pane",
    )

    BEADS_GATE_ACTION_NAMES = (
        "suppress-no-open-beads",
        "suppress-no-ready-beads",
        "skip-policy-disabled",
        "skip-policy-unavailable",
    )

    def test_pre_v2_action_names_remain_in_watcher_source(self) -> None:
        # We want the literal strings to be findable so a refactor that
        # silently renamed them would fail this test.
        source = WATCHER_PATH.read_text(encoding="utf-8")
        for action in self.PRE_V2_ACTION_NAMES:
            self.assertIn(
                action,
                source,
                f"Pre-v2 event action {action!r} no longer appears in the "
                "watcher source. AMW-v2.md says these must remain intact "
                "under the beads gate.",
            )

    def test_beads_gate_action_names_remain_stable(self) -> None:
        source = WATCHER_PATH.read_text(encoding="utf-8")
        for action in self.BEADS_GATE_ACTION_NAMES:
            self.assertIn(
                action,
                source,
                f"Beads-gate event action {action!r} drifted; operators rely "
                "on these strings appearing literally in events.jsonl",
            )


class TestStatusOutputPreservesGuardrailSignals(unittest.TestCase):
    """`status` JSON must keep surfacing pre-v2 guardrail fields alongside the new ones."""

    PRE_V2_PER_BINDING_KEYS = (
        "wake_deliverable",
        "prompt_state",
        "state_reason",
        "ownership_conflict_reason",
        "provider_identity_issue_reason",
        "alive",
    )

    def _run_status(self) -> dict:
        result = subprocess.run(
            [str(WATCHER_PATH), "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(
            result.returncode, 0, f"status command failed: {result.stderr!r}"
        )
        return json.loads(result.stdout)

    def test_pre_v2_per_binding_keys_remain_in_status(self) -> None:
        payload = self._run_status()
        binding_statuses = payload.get("binding_statuses", {})
        if not binding_statuses:
            self.skipTest(
                "no live bindings — pre-v2 field contract requires at least "
                "one binding to validate"
            )
        for binding_key, entry in binding_statuses.items():
            for required in self.PRE_V2_PER_BINDING_KEYS:
                self.assertIn(
                    required,
                    entry,
                    f"binding {binding_key!r} is missing pre-v2 guardrail "
                    f"field {required!r}; the AMW v2 work must not have "
                    "weakened operator visibility into existing safeguards",
                )


class TestStatusContractStableForBothGateStates(unittest.TestCase):
    """The set of status fields must be stable regardless of whether the gate is on or off.

    The new beads-gate fields appear unconditionally so operators can read
    disabled-mode visibility (per vgd.6.2 + vgd.6.3 contracts), and the
    pre-v2 fields appear unconditionally so the gate cannot accidentally
    hide a guardrail signal.
    """

    EXPECTED_KEY_UNION = frozenset(
        {
            # Pre-v2 guardrail signals (must remain).
            "wake_deliverable",
            "prompt_state",
            "state_reason",
            "ownership_conflict_reason",
            "provider_identity_issue_reason",
            "alive",
            # Beads-gate raw work-state.
            "work_state_repo_root",
            "work_state_source",
            "work_state_available",
            "work_state_open_count",
            "work_state_ready_count",
            "work_state_in_progress_count",
            "work_state_error",
            # Beads-gate policy booleans.
            "normal_wake_allowed",
            "high_wake_allowed",
            "urgent_wake_allowed",
            # Beads-gate explanation.
            "beads_gate_explanation",
        }
    )

    def test_each_binding_carries_full_union_field_set(self) -> None:
        result = subprocess.run(
            [str(WATCHER_PATH), "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(
            result.returncode, 0, f"status command failed: {result.stderr!r}"
        )
        payload = json.loads(result.stdout)
        binding_statuses = payload.get("binding_statuses", {})
        if not binding_statuses:
            self.skipTest("no live bindings to validate")
        for binding_key, entry in binding_statuses.items():
            missing = self.EXPECTED_KEY_UNION - set(entry.keys())
            self.assertFalse(
                missing,
                f"binding {binding_key!r} missing fields {sorted(missing)} "
                "from the pre-v2 + beads-gate union; both surfaces must be "
                "exposed unconditionally for operator triage",
            )


class TestEventPayloadSchemaPreservesGuardrailKeys(unittest.TestCase):
    """The Phase 5 work_state_event_fields helper must not strip pre-v2 keys.

    Phase 5 added work_state_* fields to the event payload but must not have
    silently dropped or renamed any pre-existing guardrail keys carried in
    the same dict by process_signal.
    """

    EVENT_HELPER_KEYS = frozenset(
        {
            "work_state_repo_root",
            "work_state_source",
            "work_state_available",
            "work_state_open_count",
            "work_state_ready_count",
            "work_state_in_progress_count",
            "work_state_error",
        }
    )

    def test_helper_does_not_collide_with_pre_v2_event_keys(self) -> None:
        # The pre-v2 event payload uses keys like binding_stale_reason,
        # ownership_conflict_reason, prompt_state. None of those should clash
        # with the work_state_* keys.
        amw = load_amw()
        helper_keys = set(amw["work_state_event_fields"](None).keys())
        pre_v2_event_keys = {
            "binding_stale_reason",
            "ownership_conflict_reason",
            "provider_identity_issue_reason",
            "prompt_state",
            "state_reason",
            "delivered",
            "deferred_reason",
            "selection_source",
            "session_id",
            "session_id_source",
        }
        collisions = helper_keys & pre_v2_event_keys
        self.assertFalse(
            collisions,
            f"work_state_event_fields collides with pre-v2 event keys: "
            f"{sorted(collisions)}. Phase 5 must not have renamed or "
            "shadowed an existing event-payload key.",
        )
        self.assertEqual(helper_keys, self.EVENT_HELPER_KEYS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
