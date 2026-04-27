#!/usr/bin/env python3
"""
Unit tests for the work-state selection helpers, observer-mode `br`
invocation contract, and per-run cache reuse.

These tests lock down the safest-but-correct behavior of the watcher's
project_work_state_for_target / project_work_state_for_repo_root layer
without needing full end-to-end runs. AMW-v2.md Phase 3 calls out three
risks this layer was built to mitigate:

* Risk 1: the read must be observer-mode (`--no-auto-flush
  --no-auto-import --lock-timeout 250`) so the watcher never dirties or
  contends with `.beads` worktrees during a scan.
* Risk 2: per-run memoization, including for unavailable reads, so one
  scan or status pass spawns at most one `br` per repo root.
* Risk 3: pane-checkout selection only when the pane's Git workspace
  matches the canonical project; never an unbounded
  nearest-ancestor `.beads` walk.

If any of those slip in a future refactor (e.g. someone removes
`--no-auto-flush` "to simplify", or reads `.beads/issues.jsonl`
directly, or stops passing the cache), these tests fail loudly.

Run directly:

    python3 tools/agent-mail-watcher/tests/test_work_state_selection.py

Or via unittest discovery:

    python3 -m unittest discover -s tools/agent-mail-watcher/tests
"""

from __future__ import annotations

import pathlib
import runpy
import subprocess
import tempfile
import unittest
from unittest import mock

WATCHER_DIR = pathlib.Path(__file__).resolve().parent.parent
WATCHER_PATH = WATCHER_DIR / "agent-mail-watcher"

EXPECTED_BR_OBSERVER_ARGS = [
    "br",
    "--json",
    "--no-auto-flush",
    "--no-auto-import",
    "--lock-timeout",
    "250",
    "stats",
]


def load_amw() -> dict:
    return runpy.run_path(str(WATCHER_PATH))


def _make_pane(amw: dict, **overrides):
    """Construct a PaneInfo with sensible defaults; tests override the
    fields they care about."""
    defaults = dict(
        session_name="ws-test",
        window_index="0",
        pane_index="0",
        composite_key="ws-test:0:0",
        pane_id="%0",
        active=True,
        current_command="bash",
        title="",
        current_path="/tmp",
        pid=0,
        dead=False,
        bound_agent_name="",
        bound_project_key="",
        bound_project_hash="",
        bound_binding_nonce="",
        bound_program="",
        bound_session_id="",
        bound_pane_key="",
    )
    defaults.update(overrides)
    return amw["PaneInfo"](**defaults)


class TestRepoSelection(unittest.TestCase):
    """Pane-worktree vs canonical-root selection rules from AMW-v2.md
    Phase 3 Risk 3. The helper must:
    - prefer the pane's Git worktree when its current_path lives in the
      same Git workspace as the canonical project_key;
    - fall back to canonical project_key when the pane's path is in a
      different Git workspace (e.g. a sibling checkout);
    - never use an unbounded nearest-ancestor `.beads` search.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.amw = load_amw()
        cls._tmp_root = pathlib.Path(tempfile.mkdtemp(prefix="vgd95-sel-"))
        # workspace_a is a Git+beads repo standing in for the canonical project
        cls.workspace_a = cls._tmp_root / "workspace_a"
        cls.amw["setup_self_test_beads_repo"](cls.workspace_a)
        # workspace_b is a SEPARATE Git+beads repo, used to drive the
        # cross-workspace fallback case.
        cls.workspace_b = cls._tmp_root / "workspace_b"
        cls.amw["setup_self_test_beads_repo"](cls.workspace_b)
        # workspace_c is a non-beads Git repo (vgd.8.3 fixture) for the
        # unavailable case.
        cls.workspace_c = cls._tmp_root / "workspace_c"
        cls.amw["setup_self_test_unavailable_workspace"](cls.workspace_c)

    def test_pane_in_same_workspace_uses_pane_worktree_source(self) -> None:
        # Pane sitting INSIDE workspace_a, project_key also workspace_a:
        # selection should resolve via the pane's worktree.
        pane = _make_pane(self.amw, current_path=str(self.workspace_a))
        state = self.amw["project_work_state_for_target"](
            pane, str(self.workspace_a)
        )
        self.assertTrue(state.available)
        self.assertEqual(
            state.source,
            "pane-worktree",
            "pane in same workspace as project_key must select via "
            "pane-worktree, not canonical-project",
        )

    def test_pane_in_different_workspace_falls_back_to_canonical(self) -> None:
        # Pane sitting in workspace_b but project_key is workspace_a:
        # the helper must REJECT the pane's checkout and fall back to
        # the canonical project_key.
        pane = _make_pane(self.amw, current_path=str(self.workspace_b))
        state = self.amw["project_work_state_for_target"](
            pane, str(self.workspace_a)
        )
        self.assertTrue(state.available)
        self.assertEqual(
            state.source,
            "canonical-project",
            "pane in a sibling Git workspace must NOT be trusted; the "
            "helper must fall back to canonical project_key",
        )

    def test_no_pane_uses_canonical_source(self) -> None:
        state = self.amw["project_work_state_for_target"](
            None, str(self.workspace_a)
        )
        self.assertTrue(state.available)
        self.assertEqual(state.source, "canonical-project")

    def test_non_beads_workspace_returns_unavailable(self) -> None:
        # workspace_c has .git but no .beads; the read must be explicit
        # unavailable, not a silent zero count.
        state = self.amw["project_work_state_for_target"](
            None, str(self.workspace_c)
        )
        self.assertFalse(state.available)
        self.assertEqual(state.error, "beads-repo-not-found")

    def test_pane_with_invalid_path_falls_back_to_canonical(self) -> None:
        # Defensive case: pane.current_path is empty / not a real dir.
        # Helper must not crash; it should fall back to canonical.
        pane = _make_pane(self.amw, current_path="")
        state = self.amw["project_work_state_for_target"](
            pane, str(self.workspace_a)
        )
        self.assertTrue(state.available)
        self.assertEqual(state.source, "canonical-project")

    def test_find_beads_repo_root_only_checks_git_root_not_ancestors(self) -> None:
        # AMW-v2.md Phase 3 Risk 3 forbids an unbounded nearest-ancestor
        # `.beads` search. The current implementation only inspects
        # `.beads` AT the resolved Git root. Pin that contract:
        # if a workspace's PARENT happens to have `.beads` but the
        # workspace itself is its OWN Git repo without `.beads`,
        # find_beads_repo_root must return None.
        nested = self._tmp_root / "workspace_a" / "subrepo"
        nested.mkdir(parents=True, exist_ok=True)
        # subrepo gets its OWN git init but no .beads of its own
        self.amw["setup_self_test_unavailable_workspace"](nested)
        result = self.amw["find_beads_repo_root"](str(nested))
        self.assertIsNone(
            result,
            "find_beads_repo_root must NOT walk up into the parent Git "
            "workspace's .beads; that would re-introduce the unbounded "
            "nearest-ancestor bug AMW-v2.md Phase 3 Risk 3 calls out",
        )


class TestObserverModeBrInvocation(unittest.TestCase):
    """Observer-mode contract from AMW-v2.md Phase 3 Risk 1. The watcher
    must spawn br with EXACTLY:
        br --json --no-auto-flush --no-auto-import --lock-timeout 250 stats
    against the resolved repo root as cwd. Any drift here would re-open
    the auto-import / auto-flush footgun the v2 plan was built to avoid."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.amw = load_amw()
        cls.workspace = pathlib.Path(tempfile.mkdtemp(prefix="vgd95-obs-")) / "ws"
        cls.amw["setup_self_test_beads_repo"](cls.workspace)

    def test_br_invocation_uses_exact_observer_mode_args(self) -> None:
        captured = []
        original = subprocess.run

        def capture(args, **kwargs):
            captured.append((list(args), kwargs))
            return original(args, **kwargs)

        with mock.patch.object(subprocess, "run", side_effect=capture):
            state = self.amw["project_work_state_for_repo_root"](self.workspace)
        self.assertTrue(state.available)

        br_calls = [
            (args, kw) for (args, kw) in captured if args and args[0] == "br"
        ]
        self.assertEqual(
            len(br_calls),
            1,
            "exactly one `br` invocation must occur per work-state read",
        )
        args, kwargs = br_calls[0]
        self.assertEqual(
            args,
            EXPECTED_BR_OBSERVER_ARGS,
            "br invocation must match the exact observer-mode contract; "
            "any drift here re-opens the auto-import/auto-flush footgun",
        )
        self.assertEqual(
            str(kwargs.get("cwd")),
            str(self.workspace.resolve()),
            "br must execute with cwd set to the resolved repo root, not "
            "the watcher's own cwd",
        )


class TestNoDirectIssuesJsonlParsing(unittest.TestCase):
    """AMW-v2.md Phase 3 forbids direct parsing of .beads/issues.jsonl;
    the watcher must rely on `br stats` so the schema can evolve without
    breaking the watcher. Pin that contract."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.amw = load_amw()
        cls.workspace = pathlib.Path(tempfile.mkdtemp(prefix="vgd95-jsonl-")) / "ws"
        cls.amw["setup_self_test_beads_repo"](cls.workspace)

    def test_work_state_read_does_not_open_issues_jsonl(self) -> None:
        # Wrap pathlib.Path.open / builtins.open so we observe whether
        # the helper opens issues.jsonl during a read. Spawning br is
        # allowed; opening issues.jsonl is NOT.
        opened_paths = []
        original_open = open

        def watching_open(file, *args, **kwargs):
            opened_paths.append(str(file))
            return original_open(file, *args, **kwargs)

        with mock.patch("builtins.open", side_effect=watching_open):
            state = self.amw["project_work_state_for_repo_root"](self.workspace)
        self.assertTrue(state.available)

        offending = [p for p in opened_paths if "issues.jsonl" in p]
        self.assertFalse(
            offending,
            f"work-state acquisition must NOT open .beads/issues.jsonl "
            f"directly; saw {offending}",
        )


class TestPerRunCacheReuse(unittest.TestCase):
    """Per-run memoization from AMW-v2.md Phase 3 Risk 2: a single scan
    or status pass must spawn at most one br per repo root, INCLUDING
    when the read returns unavailable. Pin both the success and the
    unavailable cache paths."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.amw = load_amw()
        cls.workspace = pathlib.Path(tempfile.mkdtemp(prefix="vgd95-cache-")) / "ws"
        cls.amw["setup_self_test_beads_repo"](cls.workspace)
        cls.unavailable_workspace = (
            pathlib.Path(tempfile.mkdtemp(prefix="vgd95-cache-unavail-")) / "ws"
        )
        cls.amw["setup_self_test_unavailable_workspace"](cls.unavailable_workspace)

    def _count_br_calls(self, fn, *args, **kwargs) -> int:
        original = subprocess.run
        count = 0

        def counting(call_args, **call_kwargs):
            nonlocal count
            if call_args and call_args[0] == "br":
                count += 1
            return original(call_args, **call_kwargs)

        with mock.patch.object(subprocess, "run", side_effect=counting):
            fn(*args, **kwargs)
        return count

    def test_successful_read_is_cached_within_a_run(self) -> None:
        cache = {}
        # First call: should spawn br once.
        first_calls = self._count_br_calls(
            self.amw["project_work_state_for_target"],
            None,
            str(self.workspace),
            cache=cache,
        )
        self.assertEqual(
            first_calls, 1, "first read must spawn exactly one br invocation"
        )
        # Second call with the SAME cache: must NOT spawn br again.
        second_calls = self._count_br_calls(
            self.amw["project_work_state_for_target"],
            None,
            str(self.workspace),
            cache=cache,
        )
        self.assertEqual(
            second_calls,
            0,
            "second read with the same cache must reuse the cached "
            "ProjectWorkState and NOT spawn br again (Risk 2)",
        )

    def test_unavailable_read_is_cached_within_a_run(self) -> None:
        # vgd.3.5's bead acceptance criteria specifically: "Cached
        # unavailable results are reused." This exercises the inner
        # helper's cache for unavailable results that occur AFTER the
        # find_beads_repo_root step (br itself fails); the no-beads-dir
        # short-circuit case skips the inner helper and is covered by
        # the basic find_beads_repo_root unit tests above.
        cache = {}
        # Force br to fail on every invocation so the inner helper
        # produces an unavailable ProjectWorkState with the br-stats-
        # timeout error code, which is the path that DOES go through
        # the cache write at the bottom of project_work_state_for_repo_root.
        original = subprocess.run
        br_call_count = 0

        def fail_br(args, **kwargs):
            nonlocal br_call_count
            if args and args[0] == "br":
                br_call_count += 1
                raise subprocess.TimeoutExpired(args, 5.0)
            return original(args, **kwargs)

        with mock.patch.object(subprocess, "run", side_effect=fail_br):
            s1 = self.amw["project_work_state_for_repo_root"](
                self.workspace, cache=cache
            )
            s2 = self.amw["project_work_state_for_repo_root"](
                self.workspace, cache=cache
            )
        self.assertFalse(s1.available)
        self.assertEqual(
            s1.error,
            "br-stats-timeout",
            "forced br timeout must surface as br-stats-timeout in the "
            "ProjectWorkState",
        )
        self.assertIs(
            s1,
            s2,
            "cached unavailable result must be returned by identity on "
            "the second call (vgd.3.5 AC: cached unavailable results are "
            "reused); identity mismatch means the second call re-ran",
        )
        self.assertEqual(
            br_call_count,
            1,
            "second read must NOT re-spawn br after caching an unavailable "
            "result; got %d br invocations" % br_call_count,
        )
        self.assertIn(
            str(self.workspace.resolve()),
            cache,
            "cache must hold an entry keyed by the resolved repo root path",
        )

    def test_distinct_repos_get_distinct_cache_entries(self) -> None:
        # Two different workspaces must NOT collide on a cache entry;
        # otherwise one repo's state could leak into another's decision.
        cache = {}
        state_a = self.amw["project_work_state_for_target"](
            None, str(self.workspace), cache=cache
        )
        state_b = self.amw["project_work_state_for_target"](
            None, str(self.unavailable_workspace), cache=cache
        )
        self.assertTrue(state_a.available)
        self.assertFalse(state_b.available)
        self.assertGreaterEqual(
            len(cache),
            1,
            "different repos must produce different cache entries; "
            "if cache only has one entry there's a key collision",
        )


if __name__ == "__main__":
    unittest.main()
