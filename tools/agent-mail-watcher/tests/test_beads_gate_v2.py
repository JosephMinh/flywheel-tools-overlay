#!/usr/bin/env python3
"""
Unit tests for the AMW v2 beads-gate config + observability contracts.

These tests treat Config.load/to_json/shipped-default and the status/event
field shape as rollout contracts. A failure here means a refactor silently
weakened operator visibility or broke disabled-mode compatibility.

Run directly:

    python3 tools/agent-mail-watcher/tests/test_beads_gate_v2.py

Or via unittest discovery:

    python3 -m unittest discover -s tools/agent-mail-watcher/tests
"""

from __future__ import annotations

import json
import pathlib
import runpy
import subprocess
import tempfile
import unittest

WATCHER_DIR = pathlib.Path(__file__).resolve().parent.parent
WATCHER_PATH = WATCHER_DIR / "agent-mail-watcher"
SHIPPED_CONFIG_PATH = WATCHER_DIR / "config" / "config.json"


def load_amw() -> dict:
    """Execute the watcher script and return its module namespace."""
    return runpy.run_path(str(WATCHER_PATH))


class TestConfigSurface(unittest.TestCase):
    """Config.load and Config.to_json carry beads_gate_enabled across the surface."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.amw = load_amw()
        cls.Config = cls.amw["Config"]

    def _load_with(self, raw: str):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as handle:
            handle.write(raw)
            path = pathlib.Path(handle.name)
        try:
            return self.Config.load(path), path
        finally:
            pass

    def test_load_missing_flag_defaults_to_false(self) -> None:
        cfg, path = self._load_with("{}")
        try:
            self.assertFalse(
                cfg.beads_gate_enabled,
                "missing beads_gate_enabled must default to False so a missing "
                "flag never silently enables the policy",
            )
        finally:
            path.unlink()

    def test_load_explicit_true(self) -> None:
        cfg, path = self._load_with('{"beads_gate_enabled": true}')
        try:
            self.assertTrue(cfg.beads_gate_enabled)
        finally:
            path.unlink()

    def test_load_explicit_false(self) -> None:
        cfg, path = self._load_with('{"beads_gate_enabled": false}')
        try:
            self.assertFalse(cfg.beads_gate_enabled)
        finally:
            path.unlink()

    def test_to_json_emits_beads_gate_enabled(self) -> None:
        cfg, path = self._load_with('{"beads_gate_enabled": true}')
        try:
            payload = cfg.to_json()
            self.assertIn(
                "beads_gate_enabled",
                payload,
                "Config.to_json must surface beads_gate_enabled so status output "
                "and event logs can record the active rollout state",
            )
            self.assertTrue(payload["beads_gate_enabled"])
        finally:
            path.unlink()

    def test_to_json_roundtrip_preserves_default(self) -> None:
        cfg, path = self._load_with("{}")
        try:
            payload = cfg.to_json()
            self.assertIn("beads_gate_enabled", payload)
            self.assertFalse(payload["beads_gate_enabled"])
        finally:
            path.unlink()


class TestShippedConfigDefault(unittest.TestCase):
    """The shipped config.json declares the rollout default intentionally."""

    def test_shipped_config_carries_beads_gate_enabled_false(self) -> None:
        with SHIPPED_CONFIG_PATH.open() as handle:
            shipped = json.load(handle)
        self.assertIn(
            "beads_gate_enabled",
            shipped,
            "shipped config.json must declare beads_gate_enabled explicitly — "
            "the default must be intentional, not silent",
        )
        self.assertFalse(
            shipped["beads_gate_enabled"],
            "shipped default must be false for staged rollout (per AMW-v2.md "
            "Phase 11 / vgd.2.2)",
        )


class TestDisabledModeContracts(unittest.TestCase):
    """beads_gate_enabled=false preserves pre-v2 behavior; no work-state acquisition."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.amw = load_amw()

    def test_skip_disabled_constant_value(self) -> None:
        self.assertEqual(
            self.amw["POLICY_DECISION_SKIP_DISABLED"],
            "skip-policy-disabled",
            "the disabled-mode decision sentinel must remain stable so event "
            "logs and downstream consumers do not break on rename",
        )

    def test_disabled_event_fields_are_all_none(self) -> None:
        # work_state_event_fields(None) is the payload emitted when the gate is
        # disabled. All values must be None to prove no br read happened.
        event_fields = self.amw["work_state_event_fields"](None)
        expected_keys = {
            "work_state_repo_root",
            "work_state_source",
            "work_state_available",
            "work_state_open_count",
            "work_state_ready_count",
            "work_state_in_progress_count",
            "work_state_error",
        }
        self.assertEqual(
            set(event_fields.keys()),
            expected_keys,
            "work_state_event_fields must expose exactly the Phase 5 keys",
        )
        for key, value in event_fields.items():
            self.assertIsNone(
                value,
                f"disabled-mode event field {key} must be None — anything "
                "else would prove the gate touched br state it should not have",
            )

    def test_explanation_for_disabled_config(self) -> None:
        # beads_gate_status_explanation must produce a clear disabled-mode
        # message regardless of the work-state passed in (operators should
        # be able to read disabled-state visibility without combining fields).
        ProjectWorkState = self.amw["ProjectWorkState"]
        explain = self.amw["beads_gate_status_explanation"]

        class FakeConfig:
            beads_gate_enabled = False

        for ws in (
            ProjectWorkState(
                repo_root=None,
                source="canonical-project",
                available=True,
                open_count=0,
                ready_count=0,
                in_progress_count=0,
            ),
            ProjectWorkState.unavailable(
                repo_root=None, source="canonical-project", error="test"
            ),
        ):
            text = explain(FakeConfig(), ws)
            self.assertIn(
                "policy disabled",
                text.lower(),
                "disabled-mode explanation must mention policy disabled "
                "regardless of the underlying work_state",
            )


class TestEventPayloadFieldContract(unittest.TestCase):
    """Phase 5 event payload fields are present and well-shaped."""

    EXPECTED_WORK_STATE_KEYS = frozenset(
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

    @classmethod
    def setUpClass(cls) -> None:
        cls.amw = load_amw()

    def test_helper_returns_full_key_set_for_none(self) -> None:
        fields = self.amw["work_state_event_fields"](None)
        self.assertEqual(set(fields.keys()), self.EXPECTED_WORK_STATE_KEYS)

    def test_helper_returns_full_key_set_for_real_state(self) -> None:
        ProjectWorkState = self.amw["ProjectWorkState"]
        ws = ProjectWorkState(
            repo_root=pathlib.Path("/tmp/example"),
            source="canonical-project",
            available=True,
            open_count=5,
            ready_count=2,
            in_progress_count=1,
        )
        fields = self.amw["work_state_event_fields"](ws)
        self.assertEqual(set(fields.keys()), self.EXPECTED_WORK_STATE_KEYS)
        # repo_root must be stringified for JSON serializability.
        self.assertIsInstance(fields["work_state_repo_root"], str)
        self.assertEqual(fields["work_state_open_count"], 5)
        self.assertEqual(fields["work_state_ready_count"], 2)


class TestStatusFieldContract(unittest.TestCase):
    """Status JSON exposes the exact per-binding fields from AMW-v2.md Phase 6."""

    EXPECTED_PER_BINDING_KEYS = frozenset(
        {
            # Pre-existing technical wakeability signal — must remain.
            "wake_deliverable",
            # vgd.6.1 — raw work state.
            "work_state_repo_root",
            "work_state_source",
            "work_state_available",
            "work_state_open_count",
            "work_state_ready_count",
            "work_state_in_progress_count",
            "work_state_error",
            # vgd.6.2 — policy booleans.
            "normal_wake_allowed",
            "high_wake_allowed",
            "urgent_wake_allowed",
            # vgd.6.3 — human-readable explanation.
            "beads_gate_explanation",
        }
    )

    def _run_status(self) -> dict:
        result = subprocess.run(
            [str(WATCHER_PATH), "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"status command failed (rc={result.returncode}): {result.stderr!r}",
        )
        return json.loads(result.stdout)

    def test_top_level_config_block_exposes_beads_gate_enabled(self) -> None:
        payload = self._run_status()
        self.assertIn("config", payload)
        self.assertIn(
            "beads_gate_enabled",
            payload["config"],
            "status output must expose beads_gate_enabled in the config block "
            "so operators can see the active rollout state",
        )

    def test_each_binding_carries_required_fields(self) -> None:
        payload = self._run_status()
        binding_statuses = payload.get("binding_statuses", {})
        if not binding_statuses:
            self.skipTest(
                "no live bindings in this environment — per-binding field "
                "contract requires at least one binding to validate"
            )
        for key, entry in binding_statuses.items():
            missing = self.EXPECTED_PER_BINDING_KEYS - set(entry.keys())
            self.assertFalse(
                missing,
                f"binding {key!r} is missing required status fields: "
                f"{sorted(missing)}. AMW v2 status contract regressed.",
            )

    def test_wake_deliverable_separate_from_policy(self) -> None:
        # The Phase 6 acceptance criterion is that wake_deliverable retains
        # its technical meaning — operators distinguish "pane is reachable"
        # from "policy lets a wake through". We assert by structure: both
        # wake_deliverable and the *_wake_allowed booleans must coexist, so
        # the technical and policy signals stayed independent.
        payload = self._run_status()
        binding_statuses = payload.get("binding_statuses", {})
        if not binding_statuses:
            self.skipTest("no live bindings")
        for key, entry in binding_statuses.items():
            self.assertIn(
                "wake_deliverable",
                entry,
                f"binding {key!r} missing wake_deliverable; the technical "
                "signal must not have been collapsed into the policy booleans",
            )
            for boolean in ("normal_wake_allowed", "high_wake_allowed", "urgent_wake_allowed"):
                self.assertIn(
                    boolean,
                    entry,
                    f"binding {key!r} missing {boolean}; the policy boolean "
                    "must coexist with wake_deliverable",
                )


class TestExplanationDistinguishesStates(unittest.TestCase):
    """The human-readable explanation must distinguish each operator-visible state."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.amw = load_amw()

    def _explain(self, *, gate_enabled: bool, work_state) -> str:
        class FakeConfig:
            pass

        FakeConfig.beads_gate_enabled = gate_enabled
        return self.amw["beads_gate_status_explanation"](FakeConfig(), work_state)

    def test_disabled_state_is_distinguishable(self) -> None:
        ProjectWorkState = self.amw["ProjectWorkState"]
        ws = ProjectWorkState(
            repo_root=None,
            source="canonical-project",
            available=True,
            open_count=5,
            ready_count=2,
            in_progress_count=0,
        )
        text = self._explain(gate_enabled=False, work_state=ws)
        self.assertIn("disabled", text.lower())

    def test_unavailable_state_is_distinguishable(self) -> None:
        ProjectWorkState = self.amw["ProjectWorkState"]
        ws = ProjectWorkState.unavailable(
            repo_root=None, source="canonical-project", error="br-not-installed"
        )
        text = self._explain(gate_enabled=True, work_state=ws)
        self.assertIn("unavailable", text.lower())
        self.assertIn("br-not-installed", text)

    def test_zero_open_state_is_distinguishable(self) -> None:
        ProjectWorkState = self.amw["ProjectWorkState"]
        ws = ProjectWorkState(
            repo_root=None,
            source="canonical-project",
            available=True,
            open_count=0,
            ready_count=0,
            in_progress_count=0,
        )
        text = self._explain(gate_enabled=True, work_state=ws)
        self.assertIn("no open beads", text.lower())

    def test_zero_ready_state_is_distinguishable(self) -> None:
        ProjectWorkState = self.amw["ProjectWorkState"]
        ws = ProjectWorkState(
            repo_root=None,
            source="canonical-project",
            available=True,
            open_count=5,
            ready_count=0,
            in_progress_count=0,
        )
        text = self._explain(gate_enabled=True, work_state=ws)
        self.assertIn("no ready beads", text.lower())

    def test_ready_state_is_distinguishable(self) -> None:
        ProjectWorkState = self.amw["ProjectWorkState"]
        ws = ProjectWorkState(
            repo_root=None,
            source="canonical-project",
            available=True,
            open_count=5,
            ready_count=2,
            in_progress_count=0,
        )
        text = self._explain(gate_enabled=True, work_state=ws)
        self.assertIn("ready", text.lower())
        self.assertNotIn("disabled", text.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
