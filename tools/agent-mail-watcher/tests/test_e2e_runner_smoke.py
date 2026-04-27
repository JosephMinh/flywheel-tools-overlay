#!/usr/bin/env python3
"""
Smoke test that pins the vgd.10.4 / vgd.10.5 / vgd.10.6 e2e runner
contract:

1. The runner exits 0 with all 14 scenarios passing on a clean check-out.
2. The run produces a summary.json with the documented schema and a
   LATEST_RUN pointer file in the artifact parent dir.
3. Observer-mode reads leave the durable bead-state files
   (.beads/issues.jsonl, metadata.json, config.yaml) byte-identical
   even after many repeated calls — the read-only observer guarantee
   from AMW-v2.md Phase 3 Risk 1.

Run directly:

    python3 tools/agent-mail-watcher/tests/test_e2e_runner_smoke.py

Or via unittest discovery:

    python3 -m unittest discover -s tools/agent-mail-watcher/tests
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import runpy
import subprocess
import sys
import tempfile
import unittest

THIS_DIR = pathlib.Path(__file__).resolve().parent
WATCHER_DIR = THIS_DIR.parent
WATCHER_PATH = WATCHER_DIR / "agent-mail-watcher"
E2E_RUNNER = THIS_DIR / "run_e2e_scenarios.py"
ARTIFACT_PARENT = THIS_DIR / "_e2e_scenarios"

DURABLE_BEAD_STATE_FILES = ("issues.jsonl", "metadata.json", "config.yaml")


def load_amw() -> dict:
    return runpy.run_path(str(WATCHER_PATH))


def hash_durable_state(beads_dir: pathlib.Path) -> dict[str, str]:
    """Return sha256 of files the AMW-v2.md observer-mode contract pins
    as immutable across reads. Excludes `.beads/beads.db` because SQLite
    engine page-LSN updates on pure reads are a known engine behavior
    that does not mutate the bead graph itself; the contract's audit
    boundary is the JSONL+metadata source of truth."""
    out: dict[str, str] = {}
    for name in DURABLE_BEAD_STATE_FILES:
        path = beads_dir / name
        if path.exists():
            out[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


class TestE2eRunnerInvokesCleanly(unittest.TestCase):
    """Run the e2e runner as a subprocess and pin its public contract:
    exit code, scenario count, presence of summary.json + LATEST_RUN."""

    def test_runner_exits_zero_with_summary_and_latest_pointer(self) -> None:
        result = subprocess.run(
            [sys.executable, str(E2E_RUNNER)],
            capture_output=True,
            text=True,
            cwd=WATCHER_DIR.parent.parent,
            timeout=120,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"e2e runner must exit 0; stdout={result.stdout!r} stderr={result.stderr!r}",
        )
        # Stdout should report total scenarios = 14, passed = 14
        self.assertIn("total scenarios: 14", result.stdout)
        self.assertIn("passed: 14", result.stdout)
        self.assertIn("failed: 0", result.stdout)

        # LATEST_RUN pointer must exist and name a run dir that exists
        pointer = ARTIFACT_PARENT / "LATEST_RUN"
        self.assertTrue(
            pointer.exists(),
            "LATEST_RUN pointer file must exist after a run (vgd.10.5)",
        )
        run_id = pointer.read_text(encoding="utf-8").splitlines()[0].strip()
        run_root = ARTIFACT_PARENT / run_id
        self.assertTrue(
            run_root.is_dir(), f"LATEST_RUN names {run_id!r} but dir is missing"
        )

        # summary.json must exist and have the documented schema
        summary_path = run_root / "summary.json"
        self.assertTrue(
            summary_path.exists(),
            "summary.json must exist next to manifest.json (vgd.10.5 AC2)",
        )
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        for key in ("run_id", "ok", "passed_count", "failed_count", "scenarios", "inspect_first"):
            self.assertIn(
                key,
                summary,
                f"summary.json missing required key {key!r}",
            )
        self.assertTrue(summary["ok"], "all-green run must surface ok=True")
        self.assertEqual(summary["passed_count"], 14)
        self.assertEqual(summary["failed_count"], 0)

        # Each scenario row must carry first_look_paths so failures can be
        # diagnosed from summary.json alone (vgd.10.5).
        for row in summary["scenarios"]:
            self.assertIn("first_look_paths", row, f"row {row} missing first_look_paths")
            self.assertIn("scenario_dir", row["first_look_paths"])
            self.assertIn("scenario_manifest", row["first_look_paths"])


class TestReadOnlyObserverDurableState(unittest.TestCase):
    """Pin the AMW-v2.md Phase 3 Risk 1 contract: observer-mode reads
    must NOT mutate the durable bead-state source-of-truth files. The
    scope is intentionally narrow — only the JSONL + metadata files
    that the bead graph actually persists, not the SQLite cache the
    engine manages internally on pure reads."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.amw = load_amw()
        cls.workspace = (
            pathlib.Path(tempfile.mkdtemp(prefix="vgd-10-6-readonly-")) / "ws"
        )
        cls.amw["setup_self_test_beads_repo"](cls.workspace)
        cls.amw["seed_self_test_beads_state"](cls.workspace, "ready")

    def test_durable_state_unchanged_after_many_reads(self) -> None:
        before = hash_durable_state(self.workspace / ".beads")
        self.assertEqual(
            set(before),
            set(DURABLE_BEAD_STATE_FILES),
            f"expected all of {DURABLE_BEAD_STATE_FILES} in seeded fixture, "
            f"got {sorted(before)}",
        )

        # Hammer the observer paths: cached reads, uncached reads, both
        # entry points. None of these should mutate the source-of-truth files.
        cache: dict[str, object] = {}
        for _ in range(20):
            self.amw["project_work_state_for_target"](
                None, str(self.workspace), cache=cache
            )
        for _ in range(5):
            self.amw["project_work_state_for_repo_root"](
                self.workspace, cache=cache
            )

        after = hash_durable_state(self.workspace / ".beads")
        for name in DURABLE_BEAD_STATE_FILES:
            self.assertEqual(
                before[name],
                after[name],
                f".beads/{name} mutated by observer-mode reads — Risk 1 "
                "violated. Before/after sha256 differ.",
            )

    def test_uncached_unavailable_read_also_leaves_state_intact(self) -> None:
        # Defensive: the unavailable code path doesn't touch br at all,
        # but assert it also doesn't write anything to the workspace.
        before = hash_durable_state(self.workspace / ".beads")
        for _ in range(10):
            self.amw["project_work_state_for_target"](None, str(self.workspace))
        after = hash_durable_state(self.workspace / ".beads")
        self.assertEqual(before, after)


class TestE2eSuiteFailOpenScenarios(unittest.TestCase):
    """Pin that the e2e suite includes at least one unavailable/locked/
    stale scenario that resolves to fail-open, satisfying vgd.10.6 AC3
    independent of whether the smoke runner subprocess is invoked."""

    @classmethod
    def setUpClass(cls) -> None:
        # Import the runner module via importlib so DECISION_SCENARIOS
        # is inspectable without a subprocess.
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_e2e_runner_module", str(E2E_RUNNER)
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cls.runner_mod = mod

    def test_at_least_one_failopen_scenario_exists(self) -> None:
        scenarios = self.runner_mod.DECISION_SCENARIOS
        failopen_ids = {
            sid
            for sid, _, _, fixture, expected_dec in scenarios
            if expected_dec == "POLICY_DECISION_SKIP_UNAVAILABLE"
        }
        self.assertGreaterEqual(
            len(failopen_ids),
            1,
            f"e2e suite must include at least one fail-open scenario; "
            f"got {sorted(failopen_ids)}",
        )

    def test_failopen_scenarios_cover_distinct_unavailable_paths(self) -> None:
        # Stronger: cover at least one no-beads-dir case AND at least one
        # br-error case. They exercise different branches of the
        # unavailable code path.
        scenarios = self.runner_mod.DECISION_SCENARIOS
        fixtures = {
            fixture
            for _, _, _, fixture, expected_dec in scenarios
            if expected_dec == "POLICY_DECISION_SKIP_UNAVAILABLE"
        }
        self.assertIn("unavailable", fixtures)
        self.assertIn("failopen-br-timeout", fixtures)


if __name__ == "__main__":
    unittest.main()
