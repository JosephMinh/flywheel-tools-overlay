#!/usr/bin/env python3
"""
Scripted e2e runner for AMW v2 beads-gate scenarios.

This is the "one command before rollout" verification harness for the
beads-aware wake gate. It drives the watcher's real read path
(project_work_state_for_target), real policy
(beads_gate_decision / beads_gate_status_explanation), and real event
field projection (work_state_event_fields) over a matrix of beads
states crossed with severity importance levels — plus the explicit
disabled-flag, unavailable, fail-open-on-br-timeout, and
unowned-shell-only-target compatibility cases.

Each scenario records evidence into a per-run TestArtifactRecorder
bundle so a failing case is pinned to a named scenario directory with
expected/actual + work_state snapshots, ready for paste into a bug
report.

Usage:

    python3 tools/agent-mail-watcher/tests/run_e2e_scenarios.py

Exits 0 on full-suite pass, 1 if any scenario fails.

Why a separate runner instead of extending command_self_test:
- This stays runnable without tmux and finishes in seconds, so it can
  live inside CI / pre-rollout checks without flakiness.
- vgd.11.1 (MaroonSeal) is concurrently extending command_self_test
  for divergent-worktree validation; keeping this work in a separate
  file avoids file-level lock contention and keeps the e2e matrix
  expandable independent of the live-tmux self-test.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import pathlib
import runpy
import subprocess
import sys
import tempfile
from typing import Any
from unittest import mock

THIS_DIR = pathlib.Path(__file__).resolve().parent
WATCHER_DIR = THIS_DIR.parent
WATCHER_PATH = WATCHER_DIR / "agent-mail-watcher"
ARTIFACT_PARENT = THIS_DIR / "_e2e_scenarios"


def load_amw() -> dict:
    return runpy.run_path(str(WATCHER_PATH))


# ---------------------------------------------------------------------------
# Scenario specs
# ---------------------------------------------------------------------------
# (scenario_id, description, importance, fixture, expected_decision_const_name)
DECISION_SCENARIOS: list[tuple[str, str, str, str, str]] = [
    (
        "zero_open_low",
        "open_count=0 + low importance must suppress with no-open-beads",
        "low",
        "zero-open",
        "POLICY_DECISION_SUPPRESS_NO_OPEN",
    ),
    (
        "zero_open_normal",
        "open_count=0 + normal importance must suppress with no-open-beads",
        "normal",
        "zero-open",
        "POLICY_DECISION_SUPPRESS_NO_OPEN",
    ),
    (
        "zero_open_high",
        "open_count=0 + high importance MUST NOT break through (suppresses)",
        "high",
        "zero-open",
        "POLICY_DECISION_SUPPRESS_NO_OPEN",
    ),
    (
        "zero_open_urgent",
        "open_count=0 + urgent importance still wakes",
        "urgent",
        "zero-open",
        "POLICY_DECISION_WAKE",
    ),
    (
        "open_zero_ready_low",
        "open>0 + ready=0 + low must suppress with no-ready-beads",
        "low",
        "open-zero-ready",
        "POLICY_DECISION_SUPPRESS_NO_READY",
    ),
    (
        "open_zero_ready_normal",
        "open>0 + ready=0 + normal must suppress with no-ready-beads",
        "normal",
        "open-zero-ready",
        "POLICY_DECISION_SUPPRESS_NO_READY",
    ),
    (
        "open_zero_ready_high",
        "open>0 + ready=0 + high wakes (the explicit break-through case)",
        "high",
        "open-zero-ready",
        "POLICY_DECISION_WAKE",
    ),
    (
        "open_zero_ready_urgent",
        "open>0 + ready=0 + urgent wakes",
        "urgent",
        "open-zero-ready",
        "POLICY_DECISION_WAKE",
    ),
    (
        "ready_low",
        "ready state allows low to wake (no extra policy restriction)",
        "low",
        "ready",
        "POLICY_DECISION_WAKE",
    ),
    (
        "ready_normal",
        "ready state preserves pre-v2 wake behavior for normal",
        "normal",
        "ready",
        "POLICY_DECISION_WAKE",
    ),
    (
        "unavailable_no_beads_dir",
        "non-beads workspace must fail-open via SKIP_UNAVAILABLE",
        "normal",
        "unavailable",
        "POLICY_DECISION_SKIP_UNAVAILABLE",
    ),
    (
        "failopen_br_timeout",
        "br timeout against a beads workspace must fail-open via SKIP_UNAVAILABLE",
        "normal",
        "failopen-br-timeout",
        "POLICY_DECISION_SKIP_UNAVAILABLE",
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_workspace(
    amw: dict,
    fixture: str,
    root: pathlib.Path,
    *,
    instance: str,
) -> pathlib.Path:
    # Each scenario gets its own workspace dir so seeding is fresh and
    # repeatable; sharing a workspace across scenarios doubled bead
    # counts during the first run.
    workspace = root / f"ws-{instance}"
    if fixture == "unavailable":
        amw["setup_self_test_unavailable_workspace"](workspace)
        return workspace
    amw["setup_self_test_beads_repo"](workspace)
    if fixture in {"zero-open", "open-zero-ready", "ready"}:
        amw["seed_self_test_beads_state"](workspace, fixture)
    elif fixture == "failopen-br-timeout":
        # plain beads-enabled workspace; the br timeout is forced inside
        # run_decision_scenario's mock.patch context.
        amw["seed_self_test_beads_state"](workspace, "ready")
    else:
        raise ValueError(f"unknown fixture {fixture!r}")
    return workspace


def _make_minimal_config(amw: dict, *, beads_gate_enabled: bool) -> Any:
    # Construct a real Config dataclass with the smallest defaults that
    # let beads_gate_status_explanation execute. Default values are pulled
    # from the watcher's own Config.load() defaults to stay in sync.
    Config = amw["Config"]
    fields = {f.name for f in dataclasses.fields(Config)}
    kwargs: dict[str, Any] = {
        "config_path": pathlib.Path("/tmp/e2e-config.json"),
        "signals_dir": pathlib.Path("/tmp/e2e-signals"),
        "mailbox_root": pathlib.Path("/tmp/e2e-mailbox"),
        "state_path": pathlib.Path("/tmp/e2e-state.json"),
        "log_path": pathlib.Path("/tmp/e2e-events.jsonl"),
        "lock_path": pathlib.Path("/tmp/e2e-scan.lock"),
        "bindings_path": pathlib.Path("/tmp/e2e-bindings.json"),
        "poll_interval_seconds": 1.0,
        "deferred_retry_seconds": 30,
        "max_retry_attempts": 3,
        "max_event_log_lines": 1000,
        "wake_prompt_template": "{subject}",
        "create_session_prefix": "e2e",
        "shell_command": "/bin/bash",
        "auto_create_missing_panes": False,
        "beads_gate_enabled": beads_gate_enabled,
        "provider_specs": dict(amw["DEFAULT_PROVIDER_SPECS"]),
    }
    # Drop any keys the Config doesn't declare (forward-compat with future
    # field additions or removals).
    return Config(**{k: v for k, v in kwargs.items() if k in fields})


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------
def run_decision_scenario(
    amw: dict,
    recorder: Any,
    fixture_root: pathlib.Path,
    scenario_id: str,
    description: str,
    importance: str,
    fixture: str,
    expected_decision_name: str,
) -> tuple[bool, dict[str, Any]]:
    expected_decision = amw[expected_decision_name]
    workspace = _make_workspace(amw, fixture, fixture_root, instance=scenario_id)
    if fixture == "failopen-br-timeout":
        original_run = subprocess.run

        def fake_run(args, **kwargs):
            if args and args[0] == "br":
                raise subprocess.TimeoutExpired(args, 5.0)
            return original_run(args, **kwargs)

        with mock.patch.object(subprocess, "run", side_effect=fake_run):
            state = amw["project_work_state_for_target"](None, str(workspace))
    else:
        state = amw["project_work_state_for_target"](None, str(workspace))

    decision, reason = amw["beads_gate_decision"](importance, state)
    config = _make_minimal_config(amw, beads_gate_enabled=True)
    explanation = amw["beads_gate_status_explanation"](config, state)
    event_fields = amw["work_state_event_fields"](state)

    actual = {
        "decision": decision,
        "reason": reason,
        "explanation": explanation,
        "work_state": state.to_json(),
        "event_fields": event_fields,
    }
    expected = {
        "decision": expected_decision,
        "policy_check": "decision must equal expected_decision",
    }
    ok = decision == expected_decision
    actual["match"] = ok

    recorder.record_scenario(
        scenario_id,
        description,
        workspaces={"workspace": workspace},
        expected=expected,
        actual=actual,
        events=[
            {
                "action": "scenario-decision",
                "scenario": scenario_id,
                "importance": importance,
                "decision": decision,
                "reason": reason,
            }
        ],
    )
    return ok, actual


def run_disabled_scenario(
    amw: dict, recorder: Any, fixture_root: pathlib.Path
) -> tuple[bool, dict[str, Any]]:
    # Disabled-flag scenario: when beads_gate_enabled=false, the watcher
    # bypasses work-state acquisition entirely. Pin two consequences:
    # 1) work_state_event_fields(None) emits the full key set with all-None
    #    values (so events stay schema-stable in disabled mode);
    # 2) beads_gate_status_explanation reports "policy disabled" so
    #    operators reading status output can tell the difference between
    #    "gate is happy" and "gate is off".
    workspace = _make_workspace(amw, "ready", fixture_root, instance="disabled_flag")
    fields = amw["work_state_event_fields"](None)
    expected_keys = {
        "work_state_repo_root",
        "work_state_source",
        "work_state_available",
        "work_state_open_count",
        "work_state_ready_count",
        "work_state_in_progress_count",
        "work_state_error",
    }
    fields_keys_match = set(fields.keys()) == expected_keys
    fields_all_none = all(v is None for v in fields.values())

    config_off = _make_minimal_config(amw, beads_gate_enabled=False)
    state = amw["project_work_state_for_target"](None, str(workspace))
    explanation = amw["beads_gate_status_explanation"](config_off, state)
    explanation_signals_disabled = "disabled" in explanation.lower()

    actual = {
        "fields_keys": sorted(fields.keys()),
        "fields_keys_match": fields_keys_match,
        "fields_all_none": fields_all_none,
        "explanation": explanation,
        "explanation_signals_disabled": explanation_signals_disabled,
    }
    expected = {
        "fields_keys": sorted(expected_keys),
        "fields_all_none": True,
        "explanation_signals_disabled": True,
    }
    ok = fields_keys_match and fields_all_none and explanation_signals_disabled
    actual["match"] = ok

    recorder.record_scenario(
        "disabled_flag",
        "beads_gate_enabled=false bypasses work-state acquisition and "
        "explanation says `policy disabled`",
        workspaces={"workspace": workspace},
        expected=expected,
        actual=actual,
        events=[{"action": "scenario-disabled", "scenario": "disabled_flag"}],
    )
    return ok, actual


def run_unowned_shell_scenario(
    amw: dict, recorder: Any, fixture_root: pathlib.Path
) -> tuple[bool, dict[str, Any]]:
    # AMW-v2.md Phase 5 forbids policy-suppressed signals from
    # auto-creating panes for unowned shell-only targets. The watcher
    # enforces this by routing on a fixed `suppress_decisions` predicate
    # before any auto-create branch in process_signal.
    #
    # We can't reproduce the full process_signal flow without tmux, but
    # we CAN pin the predicate's classification: a zero-open + normal
    # signal must produce a decision that lives in the suppress set, so
    # the auto-create branch is unreachable downstream.
    workspace = _make_workspace(amw, "zero-open", fixture_root, instance="unowned_shell_only_target")
    suppress_decisions = frozenset(
        {
            amw["POLICY_DECISION_SUPPRESS_NO_OPEN"],
            amw["POLICY_DECISION_SUPPRESS_NO_READY"],
        }
    )
    state = amw["project_work_state_for_target"](None, str(workspace))
    decision, _ = amw["beads_gate_decision"]("normal", state)
    short_circuited = decision in suppress_decisions

    actual = {
        "decision": decision,
        "short_circuited_before_autocreate": short_circuited,
        "suppress_decisions": sorted(suppress_decisions),
    }
    expected = {
        "short_circuited_before_autocreate": True,
        "rationale": "policy suppression must short-circuit auto-create",
    }
    ok = short_circuited
    actual["match"] = ok

    recorder.record_scenario(
        "unowned_shell_only_target",
        "policy suppression must short-circuit auto-create for unowned "
        "shell-only target (predicate-level proof)",
        workspaces={"workspace": workspace},
        expected=expected,
        actual=actual,
        events=[
            {
                "action": "scenario-unowned-shell",
                "scenario": "unowned_shell_only_target",
                "decision": decision,
            }
        ],
    )
    return ok, actual


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="vgd.10.4 scripted e2e runner")
    parser.add_argument(
        "--keep-fixtures",
        action="store_true",
        help="leave the fixture beads repos in the artifact bundle (default: keep)",
    )
    args = parser.parse_args()  # currently informational

    amw = load_amw()
    run_id = (
        f"vgd-10-4-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    )
    ARTIFACT_PARENT.mkdir(parents=True, exist_ok=True)
    artifact_root = ARTIFACT_PARENT / run_id
    artifact_root.mkdir(parents=True, exist_ok=True)
    meta_dir = artifact_root / "_meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    config_path = meta_dir / "config.json"
    state_path = meta_dir / "state.json"
    log_path = meta_dir / "events.jsonl"
    config_path.write_text("{}", encoding="utf-8")
    state_path.write_text("{}", encoding="utf-8")
    log_path.write_text("", encoding="utf-8")

    recorder = amw["TestArtifactRecorder"](
        root=artifact_root,
        run_id=run_id,
        run_kind="vgd.10.4-scripted-e2e",
        config_path=config_path,
        state_path=state_path,
        log_path=log_path,
        metadata={"bead": "flywheel-tools-overlay-dev-vgd.10.4"},
    )

    fixture_root = pathlib.Path(tempfile.mkdtemp(prefix=f"{run_id}-"))

    failures: list[tuple[str, dict[str, Any]]] = []
    passes: list[str] = []

    def track(sid: str, ok: bool, actual: dict[str, Any]) -> None:
        if ok:
            passes.append(sid)
        else:
            failures.append((sid, actual))

    for sid, desc, importance, fixture, expected_dec in DECISION_SCENARIOS:
        ok, actual = run_decision_scenario(
            amw,
            recorder,
            fixture_root,
            sid,
            desc,
            importance,
            fixture,
            expected_dec,
        )
        track(sid, ok, actual)

    ok, actual = run_disabled_scenario(amw, recorder, fixture_root)
    track("disabled_flag", ok, actual)

    ok, actual = run_unowned_shell_scenario(amw, recorder, fixture_root)
    track("unowned_shell_only_target", ok, actual)

    total = len(DECISION_SCENARIOS) + 2
    overall_ok = not failures
    recorder.finalize(ok=overall_ok)

    print(f"=== vgd.10.4 scripted e2e: {run_id} ===")
    print(f"  total scenarios: {total}")
    print(f"  passed: {len(passes)}")
    print(f"  failed: {len(failures)}")
    print(f"  artifact root: {artifact_root}")
    print(f"  manifest: {recorder.manifest_path}")
    if failures:
        print("  failure detail:")
        for sid, blk in failures:
            print(
                f"    [{sid}] expected={blk.get('expected') or blk.get('match')!r} "
                f"actual={blk.get('actual') or blk.get('decision')!r}"
            )
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
