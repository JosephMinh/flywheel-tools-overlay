import argparse
import importlib.machinery
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


MODULE_PATH = Path(__file__).with_name("ntm-bootstrap")
LOADER = importlib.machinery.SourceFileLoader("ntm_bootstrap_module", str(MODULE_PATH))
SPEC = importlib.util.spec_from_loader("ntm_bootstrap_module", LOADER)
assert SPEC and SPEC.loader
NTM_BOOTSTRAP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(NTM_BOOTSTRAP)


class HandlePostSpawnInteractionTests(unittest.TestCase):
    def test_inside_tmux_attaches_before_sending_prompt(self) -> None:
        events: list[str] = []
        project_dir = Path(tempfile.mkdtemp(prefix="ntm-bootstrap-test-"))

        def fake_attach(session: str, cwd: Path) -> None:
            self.assertEqual("demo", session)
            self.assertEqual(project_dir, cwd)
            events.append("attach")

        def fake_send(
            session: str,
            pane: int,
            template_name: str,
            plan_abs_path: Path,
            plan_display_name: str,
            cwd: Path,
            dry_run: bool,
        ) -> None:
            self.assertEqual("demo", session)
            self.assertEqual(1, pane)
            self.assertEqual("turn_plan_into_beads", template_name)
            self.assertEqual(project_dir / "Plan.md", plan_abs_path)
            self.assertEqual("Plan.md", plan_display_name)
            self.assertEqual(project_dir, cwd)
            self.assertFalse(dry_run)
            events.append("send")

        with (
            mock.patch.object(NTM_BOOTSTRAP, "inside_tmux", return_value=True),
            mock.patch.object(NTM_BOOTSTRAP, "attach_session", side_effect=fake_attach),
            mock.patch.object(NTM_BOOTSTRAP, "send_prompt", side_effect=fake_send),
        ):
            attached = NTM_BOOTSTRAP.handle_post_spawn_interaction(
                session="demo",
                project_dir=project_dir,
                auto_attach=True,
                pane=1,
                template_name="turn_plan_into_beads",
                plan_abs_path=project_dir / "Plan.md",
                plan_display_name="Plan.md",
            )

        self.assertTrue(attached)
        self.assertEqual(["attach", "send"], events)

    def test_auto_attach_policy_skips_noninteractive_json_mode(self) -> None:
        args = argparse.Namespace(no_spawn=False, dry_run=False, json=True)
        with mock.patch.object(NTM_BOOTSTRAP, "interactive_terminal_available", return_value=True):
            self.assertFalse(NTM_BOOTSTRAP.should_auto_attach(args))


def _fake_completed(stdout: str) -> SimpleNamespace:
    """Build a minimal fake of subprocess.CompletedProcess for run_command stubs."""
    return SimpleNamespace(stdout=stdout, stderr="", returncode=0)


class DetectPromptPaneTests(unittest.TestCase):
    """Pin the timing-race fix for `detect_prompt_pane`. Without polling, the
    function used to snapshot tmux immediately after `ntm spawn` returned,
    miss the not-yet-titled agent panes, and silently fall back to pane 0 —
    so the planning prompt landed in the user pane and the agent pane never
    received it. These tests pin the new behavior: poll until an agent pane
    shows its `__` title, raise on timeout, and only fall back to the lowest
    pane when no wait was requested (session-reuse path)."""

    def test_returns_min_agent_pane_immediately_when_present(self) -> None:
        # Mixed user + agent pane; agent pane already titled. No polling
        # needed — the original snapshot behavior keeps working.
        listing = (
            "0\tplain shell\n"
            "1\tdemo__cod_1\n"
            "2\tdemo__cc_1\n"
        )
        with mock.patch.object(
            NTM_BOOTSTRAP, "run_command", return_value=_fake_completed(listing)
        ) as run_mock:
            pane = NTM_BOOTSTRAP.detect_prompt_pane("demo")
        self.assertEqual(1, pane)
        # Single tmux call — no polling loop because the title was present.
        self.assertEqual(1, run_mock.call_count)

    def test_polls_and_returns_when_agent_title_appears_mid_wait(self) -> None:
        # First listing: no agent titles yet (codex/claude still booting).
        # Second listing: agent pane is now titled. Polling must pick that up.
        listings = [
            _fake_completed("0\tshell\n1\tshell\n"),
            _fake_completed("0\tshell\n1\tdemo__cod_1\n"),
        ]
        sleep_calls: list[float] = []
        with (
            mock.patch.object(NTM_BOOTSTRAP, "run_command", side_effect=listings),
            mock.patch.object(NTM_BOOTSTRAP.time, "sleep", side_effect=sleep_calls.append),
        ):
            pane = NTM_BOOTSTRAP.detect_prompt_pane(
                "demo", wait_seconds=10.0, poll_interval=0.1
            )
        self.assertEqual(1, pane)
        # Slept exactly once between the two list-panes calls.
        self.assertEqual([0.1], sleep_calls)

    def test_raises_on_timeout_when_wait_seconds_positive(self) -> None:
        # Every listing shows shell-only panes; the agent CLI never titled
        # itself. With wait_seconds > 0 this must raise, NOT silently
        # return pane 0 — otherwise the planning prompt would land in
        # the user pane and the symptom would re-appear.
        listing = _fake_completed("0\tshell\n1\tshell\n")

        def always_shell(*_args, **_kwargs):
            return listing

        with (
            mock.patch.object(NTM_BOOTSTRAP, "run_command", side_effect=always_shell),
            # time.monotonic must advance past the deadline; emulate that with
            # a fake clock that advances on each call.
            mock.patch.object(
                NTM_BOOTSTRAP.time,
                "monotonic",
                side_effect=[0.0, 0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0],
            ),
            mock.patch.object(NTM_BOOTSTRAP.time, "sleep", lambda _x: None),
        ):
            with self.assertRaises(NTM_BOOTSTRAP.BootstrapError) as cm:
                NTM_BOOTSTRAP.detect_prompt_pane(
                    "demo", wait_seconds=1.0, poll_interval=0.5
                )
        msg = str(cm.exception)
        # The error message must point operators at the right diagnosis:
        # the agent CLI failed to launch in its pane.
        self.assertIn("no agent-titled pane", msg)
        self.assertIn("ntm spawn", msg)
        self.assertIn("demo", msg)

    def test_falls_back_to_lowest_pane_when_wait_seconds_is_zero(self) -> None:
        # Session-reuse path: caller didn't ask for a wait, so the
        # original lowest-pane fallback still fires. This preserves the
        # `--no-spawn` / session-already-existed behavior.
        listing = _fake_completed("3\tshell\n7\tshell\n")
        with mock.patch.object(
            NTM_BOOTSTRAP, "run_command", return_value=listing
        ):
            pane = NTM_BOOTSTRAP.detect_prompt_pane("demo")
        self.assertEqual(3, pane)

    def test_raises_when_session_has_no_panes(self) -> None:
        # Defensive: empty tmux output means the session is gone or
        # malformed; surface it instead of returning a bogus index.
        with mock.patch.object(
            NTM_BOOTSTRAP,
            "run_command",
            return_value=_fake_completed(""),
        ):
            with self.assertRaises(NTM_BOOTSTRAP.BootstrapError) as cm:
                NTM_BOOTSTRAP.detect_prompt_pane("demo")
        self.assertIn("no panes found", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
