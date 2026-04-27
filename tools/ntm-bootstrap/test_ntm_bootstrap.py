import argparse
import importlib.machinery
import importlib.util
import tempfile
import unittest
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
