#!/usr/bin/env python3
"""
Artifact and state-file semantics for AMW v2 policy decisions.

These tests exercise the watcher's real ``scan_once`` persistence path against
temporary watcher roots and then inspect the resulting ``events.jsonl`` and
``state.json`` files. The goal is to prove the operator-facing semantics from
vgd.10.3 instead of only asserting helper return values.

Run directly:

    python3 tools/agent-mail-watcher/tests/test_policy_artifact_semantics.py

Or via unittest discovery:

    python3 -m unittest discover -s tools/agent-mail-watcher/tests
"""

from __future__ import annotations

import json
import pathlib
import runpy
import tempfile
import unittest
from unittest import mock

WATCHER_DIR = pathlib.Path(__file__).resolve().parent.parent
WATCHER_PATH = WATCHER_DIR / "agent-mail-watcher"


def load_amw() -> dict:
    return runpy.run_path(str(WATCHER_PATH))


class TestPolicyArtifactSemantics(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.amw = load_amw()
        cls.Config = cls.amw["Config"]

    def _make_config(
        self,
        root: pathlib.Path,
        *,
        beads_gate_enabled: bool,
        deferred_retry_seconds: float,
    ):
        return self.Config(
            config_path=root / "config.json",
            signals_dir=root / "signals",
            mailbox_root=root / "mailbox",
            state_path=root / "state.json",
            log_path=root / "events.jsonl",
            lock_path=root / "scan.lock",
            bindings_path=root / "bindings.json",
            poll_interval_seconds=0.1,
            deferred_retry_seconds=deferred_retry_seconds,
            max_retry_attempts=5,
            max_event_log_lines=1000,
            wake_prompt_template="{subject}",
            create_session_prefix="vgd103",
            shell_command="/bin/bash",
            auto_create_missing_panes=False,
            beads_gate_enabled=beads_gate_enabled,
            provider_specs=dict(self.amw["DEFAULT_PROVIDER_SPECS"]),
        )

    def _setup_signal_fixture(
        self,
        *,
        beads_gate_enabled: bool,
        deferred_retry_seconds: float,
        scenario: str,
        importance: str,
        message_id: int,
        agent_name: str,
    ) -> tuple[object, pathlib.Path]:
        root = pathlib.Path(tempfile.mkdtemp(prefix="vgd103-"))
        config = self._make_config(
            root,
            beads_gate_enabled=beads_gate_enabled,
            deferred_retry_seconds=deferred_retry_seconds,
        )
        workspace = root / "workspace"
        self.amw["setup_self_test_beads_repo"](workspace)
        self.amw["seed_self_test_beads_state"](workspace, scenario)
        project_slug = "vgd103-selftest"
        self.amw["setup_self_test_project"](
            mailbox_root=config.mailbox_root,
            project_slug=project_slug,
            project_key=str(workspace),
            agent_name=agent_name,
            program="codex-cli",
        )
        signal_path = (
            config.signals_dir / "projects" / project_slug / "agents" / f"{agent_name}.signal"
        )
        signal_path.parent.mkdir(parents=True, exist_ok=True)
        signal_path.write_text(
            json.dumps(
                {
                    "project": project_slug,
                    "agent": agent_name,
                    "message": {
                        "id": message_id,
                        "from": "Verifier",
                        "subject": f"artifact-semantics-{message_id}",
                        "importance": importance,
                    },
                }
            ),
            encoding="utf-8",
        )
        return config, signal_path

    def _scan_without_tmux(self, config) -> list[dict]:
        scan_once = self.amw["scan_once"]
        with mock.patch.dict(
            scan_once.__globals__,
            {
                "ensure_agent_mail_service_available": lambda _config: None,
                "scrub_tmux_global_environment": lambda: None,
                "cleanup_shadow_sessions": lambda _config: [],
                "reconcile_ntm_agent_mail": lambda _config, panes=None: None,
                "repair_conflicted_provider_panes": lambda _config, panes=None: list(
                    panes or []
                ),
                "reconcile_live_binding_artifacts": lambda _config, panes=None: None,
                "prune_inactive_bindings": lambda _config, panes=None: None,
                "list_tmux_panes": lambda: [],
            },
        ):
            return scan_once(config)

    def _logged_events(self, config) -> list[dict]:
        if not config.log_path.exists():
            return []
        return [
            json.loads(line)
            for line in config.log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _signal_state(self, config, signal_path: pathlib.Path) -> dict:
        state = self.amw["load_state"](config)
        return state["signals"][str(signal_path)]

    def test_policy_suppression_logs_work_state_and_stops_retrying(self) -> None:
        config, signal_path = self._setup_signal_fixture(
            beads_gate_enabled=True,
            deferred_retry_seconds=0.0,
            scenario="zero-open",
            importance="normal",
            message_id=101,
            agent_name="PolicySuppressTester",
        )

        first_events = self._scan_without_tmux(config)
        self.assertEqual(len(first_events), 1)
        event = first_events[0]
        self.assertEqual(
            event["action"],
            self.amw["POLICY_DECISION_SUPPRESS_NO_OPEN"],
        )
        self.assertTrue(event["delivered"])
        self.assertTrue(event["beads_gate_enabled"])
        self.assertEqual(
            event["beads_gate_decision"],
            self.amw["POLICY_DECISION_SUPPRESS_NO_OPEN"],
        )
        self.assertEqual(event["beads_gate_reason"], "no-open-beads")
        self.assertTrue(event["work_state_available"])
        self.assertEqual(event["work_state_source"], "canonical-project")
        self.assertEqual(event["work_state_open_count"], 0)
        self.assertEqual(event["work_state_ready_count"], 0)

        logged = self._logged_events(config)
        self.assertEqual(len(logged), 1)
        self.assertEqual(logged[0]["action"], self.amw["POLICY_DECISION_SUPPRESS_NO_OPEN"])

        first_state = self._signal_state(config, signal_path)
        self.assertTrue(first_state["delivered"])
        self.assertEqual(first_state["attempt_count"], 1)
        self.assertIn("processed_at", first_state)
        self.assertNotIn("queued_at", first_state)

        second_events = self._scan_without_tmux(config)
        self.assertEqual(
            second_events,
            [],
            "terminal policy suppression must not re-enter the retry loop",
        )
        second_state = self._signal_state(config, signal_path)
        self.assertEqual(second_state["attempt_count"], 1)

    def test_disabled_mode_event_makes_policy_bypass_explicit(self) -> None:
        config, _signal_path = self._setup_signal_fixture(
            beads_gate_enabled=False,
            deferred_retry_seconds=0.0,
            scenario="ready",
            importance="normal",
            message_id=202,
            agent_name="DisabledModeTester",
        )

        events = self._scan_without_tmux(config)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["action"], "deferred-no-owned-pane")
        self.assertFalse(event["delivered"])
        self.assertFalse(event["beads_gate_enabled"])
        self.assertEqual(
            event["beads_gate_decision"],
            self.amw["POLICY_DECISION_SKIP_DISABLED"],
        )
        self.assertEqual(event["beads_gate_reason"], "feature-flag-disabled")
        self.assertIsNone(event["work_state_repo_root"])
        self.assertIsNone(event["work_state_source"])
        self.assertIsNone(event["work_state_available"])
        self.assertIsNone(event["work_state_open_count"])
        self.assertIsNone(event["work_state_ready_count"])
        self.assertIsNone(event["work_state_in_progress_count"])
        self.assertIsNone(event["work_state_error"])

        logged = self._logged_events(config)
        self.assertEqual(logged[-1]["beads_gate_decision"], self.amw["POLICY_DECISION_SKIP_DISABLED"])

    def test_technical_defers_still_retry_and_remain_queued(self) -> None:
        config, signal_path = self._setup_signal_fixture(
            beads_gate_enabled=False,
            deferred_retry_seconds=0.0,
            scenario="ready",
            importance="normal",
            message_id=303,
            agent_name="RetrySemanticsTester",
        )

        first_events = self._scan_without_tmux(config)
        self.assertEqual(len(first_events), 1)
        self.assertEqual(first_events[0]["action"], "deferred-no-owned-pane")
        self.assertFalse(first_events[0]["delivered"])

        first_state = self._signal_state(config, signal_path)
        self.assertFalse(first_state["delivered"])
        self.assertEqual(first_state["attempt_count"], 1)
        self.assertIn("queued_at", first_state)
        self.assertNotIn("processed_at", first_state)

        second_events = self._scan_without_tmux(config)
        self.assertEqual(len(second_events), 1)
        self.assertEqual(second_events[0]["action"], "deferred-no-owned-pane")
        self.assertFalse(second_events[0]["delivered"])

        second_state = self._signal_state(config, signal_path)
        self.assertFalse(second_state["delivered"])
        self.assertEqual(second_state["attempt_count"], 2)
        self.assertEqual(
            second_state["queued_at"],
            first_state["queued_at"],
            "technical defers should stay queued while retries continue",
        )
        self.assertNotIn("processed_at", second_state)

        logged = self._logged_events(config)
        self.assertEqual(
            [event["action"] for event in logged],
            ["deferred-no-owned-pane", "deferred-no-owned-pane"],
        )


if __name__ == "__main__":
    unittest.main()
