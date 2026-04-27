#!/usr/bin/env python3
"""
Unit tests for the AMW v2 artifact recorder helpers.

These tests pin the vgd.8.4 contract that automated validation can emit a
stable manifest plus enough watcher, workspace, and pane evidence to debug a
failure after the fact.

Run directly:

    python3 tools/agent-mail-watcher/tests/test_artifact_recorder.py

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


def load_amw() -> dict:
    return runpy.run_path(str(WATCHER_PATH))


class TestReadJsonl(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.amw = load_amw()

    def test_read_jsonl_returns_tail_and_invalid_line_markers(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vgd84-jsonl-") as tmpdir:
            path = pathlib.Path(tmpdir) / "events.jsonl"
            path.write_text('{"idx": 1}\nnot-json\n{"idx": 3}\n', encoding="utf-8")
            entries = self.amw["read_jsonl"](path, max_lines=2)
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0]["parse_error"], "invalid-jsonl-line")
            self.assertEqual(entries[0]["raw"], "not-json")
            self.assertEqual(entries[1]["idx"], 3)


class TestArtifactRecorder(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.amw = load_amw()
        cls.Config = cls.amw["Config"]
        cls.TestArtifactRecorder = cls.amw["TestArtifactRecorder"]

    def test_record_scenario_writes_manifest_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vgd84-artifacts-") as tmpdir:
            root = pathlib.Path(tmpdir)
            config_path = root / "config.json"
            state_path = root / "state.json"
            log_path = root / "events.jsonl"
            workspace = root / "workspace"
            artifacts_root = root / "artifacts"

            config_path.write_text("{}", encoding="utf-8")
            state_path.write_text(
                json.dumps({"signals": {"demo": {"delivered": True}}}),
                encoding="utf-8",
            )
            log_path.write_text(
                json.dumps({"agent_name": "WakeTester", "action": "prompted-live-pane"})
                + "\n"
                + json.dumps({"agent_name": "BusyTester", "action": "suppressed-working-pane"})
                + "\n",
                encoding="utf-8",
            )
            workspace.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)

            recorder = self.TestArtifactRecorder(
                root=artifacts_root,
                run_id="run-1",
                run_kind="unit-test",
                config_path=config_path,
                state_path=state_path,
                log_path=log_path,
                metadata={"suite": "artifact-recorder"},
            )

            globals_dict = recorder.record_scenario.__func__.__globals__
            original_capture = globals_dict["capture_pane_text"]
            original_status = globals_dict["watcher_status_command_outcome"]
            globals_dict["capture_pane_text"] = (
                lambda pane_id, scrollback_lines=120, visible_only=False: f"capture:{pane_id}"
            )
            globals_dict["watcher_status_command_outcome"] = lambda config: {
                "argv": ["status"],
                "returncode": 0,
                "stdout_json": {"binding_statuses": []},
            }
            try:
                config = self.Config.load(config_path)
                recorder.record_scenario(
                    "smoke",
                    "Write a comparable artifact bundle for one scenario.",
                    config=config,
                    workspaces={"primary": workspace},
                    pane_ids={"primary": "%1"},
                    expected={"action": "prompted-live-pane"},
                    actual={"prompt_visible": True},
                    events=[
                        {
                            "agent_name": "WakeTester",
                            "action": "prompted-live-pane",
                            "pane_id": "%1",
                            "delivered": True,
                        }
                    ],
                    extra={"note": "unit-test"},
                )
            finally:
                globals_dict["capture_pane_text"] = original_capture
                globals_dict["watcher_status_command_outcome"] = original_status

            manifest_path = artifacts_root / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertFalse(manifest["completed"])
            self.assertEqual(len(manifest["scenarios"]), 1)

            scenario = manifest["scenarios"][0]
            self.assertEqual(scenario["scenario_id"], "smoke")
            self.assertEqual(scenario["actual"]["event_count"], 1)
            self.assertEqual(
                scenario["actual"]["event_summary"][0]["action"],
                "prompted-live-pane",
            )

            command_outcomes_path = artifacts_root / scenario["paths"]["command_outcomes"]
            command_outcomes = json.loads(command_outcomes_path.read_text(encoding="utf-8"))
            self.assertIn("watcher_status", command_outcomes)
            self.assertIn("git_status", command_outcomes["workspaces"]["primary"])

            pane_manifest_path = artifacts_root / scenario["paths"]["pane_captures"]
            pane_manifest = json.loads(pane_manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(pane_manifest["primary"]["pane_id"], "%1")
            capture_path = artifacts_root / pane_manifest["primary"]["path"]
            self.assertEqual(capture_path.read_text(encoding="utf-8"), "capture:%1")

            watcher_state_path = artifacts_root / scenario["paths"]["watcher_state"]
            watcher_state = json.loads(watcher_state_path.read_text(encoding="utf-8"))
            self.assertIn("demo", watcher_state["signals"])

            recorder.finalize(ok=True)
            finalized = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertTrue(finalized["completed"])
            self.assertTrue(finalized["ok"])
            self.assertEqual(finalized["summary"]["scenario_count"], 1)
            self.assertEqual(finalized["summary"]["passed_scenarios"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
