"""Tests for claude/claude-dev-entry outside the image.

The entry renders the managed settings and, for multi-agent runs, rewrites the
managed CLAUDE.md as the pristine base plus a "This run" section
(MULTI_AGENT_ROLES_PLAN.md §7.1). Without AGENT_* variables CLAUDE.md must be
left untouched, and nothing may ever reach stdout: the entry fronts the ACP
stdio server. CLAUDE_ENTRY_ETC_DIR / CLAUDE_ENTRY_SHARE_DIR point the script
at a temp tree instead of /etc/claude-code and /usr/local/share/claude.

Run with:  make test-bins
       or:  python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "claude" / "claude-dev-entry"
BASE = (ROOT / "APPEND_SYSTEM.md").read_text()
TEMPLATE = (ROOT / "claude" / "managed-settings.template.json").read_text()
HEADING = "## This run (overrides the mount description above)"


class ClaudeEntryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        # The image layout: etc holds the shipped CLAUDE.md, share the base
        # copy and the settings template.
        self.etc = root / "etc"
        self.share = root / "share"
        self.etc.mkdir()
        self.share.mkdir()
        (self.etc / "CLAUDE.md").write_text(BASE)
        (self.share / "CLAUDE.base.md").write_text(BASE)
        (self.share / "managed-settings.template.json").write_text(TEMPLATE)
        self.project = root / "project"
        self.project.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_entry(self, env: dict[str, str] | None = None,
                  command: tuple[str, ...] = ("true",)) -> subprocess.CompletedProcess[str]:
        full = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_")}
        full.update(CLAUDE_ENTRY_ETC_DIR=str(self.etc),
                    CLAUDE_ENTRY_SHARE_DIR=str(self.share))
        full.update(env or {})
        proc = subprocess.run(["bash", str(ENTRY), *command], cwd=self.project,
                              env=full, text=True, capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "", "the entry must never write to stdout")
        return proc

    def claude_md(self) -> str:
        return (self.etc / "CLAUDE.md").read_text()

    def section(self) -> str:
        text = self.claude_md()
        self.assertTrue(text.startswith(BASE), "the base text must come first, unchanged")
        rest = text[len(BASE):]
        self.assertTrue(rest.startswith(f"\n{HEADING}\n\n"), rest[:120])
        return rest[len(f"\n{HEADING}\n\n"):]

    def test_without_agent_vars_claude_md_untouched(self) -> None:
        marker = "shipped by the image\n"
        (self.etc / "CLAUDE.md").write_text(marker)
        before = (self.etc / "CLAUDE.md").stat().st_mtime_ns
        self.run_entry()
        self.assertEqual(self.claude_md(), marker)
        self.assertEqual((self.etc / "CLAUDE.md").stat().st_mtime_ns, before)
        # The managed settings are still rendered as before.
        settings = (self.etc / "managed-settings.json").read_text()
        self.assertNotIn("__PERMISSION_MODE__", settings)
        self.assertIn("bypassPermissions", settings)

    def test_explicit_rw_alone_adds_nothing(self) -> None:
        self.run_entry({"AGENT_MOUNT_MODE": "rw"})
        self.assertEqual(self.claude_md(), BASE)

    def test_role(self) -> None:
        self.run_entry({"AGENT_ROLE": "review"})
        self.assertEqual(self.section(), "- Role: `review`.\n")

    def test_read_only(self) -> None:
        self.run_entry({"AGENT_MOUNT_MODE": "ro"})
        section = self.section()
        self.assertIn("- Project mount: read-only.", section)
        self.assertIn(f"`{self.project}`", section)
        self.assertIn('"Read-only file system"', section)

    def test_overlay(self) -> None:
        self.run_entry({"AGENT_MOUNT_MODE": "overlay"})
        section = self.section()
        self.assertIn("- Project mount: throwaway overlay.", section)
        self.assertIn("discarded when the container stops and never reaches the host", section)

    def test_outbox_wording_depends_on_mode(self) -> None:
        out = f"{self.project}/.loop/t1"
        self.run_entry({"AGENT_MOUNT_MODE": "overlay", "AGENT_OUTBOX": out})
        self.assertIn(f"- Outbox: `{out}` is bind-mounted read-write", self.section())
        self.assertIn("only location under the project whose changes persist", self.section())
        self.run_entry({"AGENT_OUTBOX": out})
        self.assertIn(f"- Outbox: `{out}`", self.section())
        self.assertNotIn("only location", self.section())

    def test_everything_in_order(self) -> None:
        self.run_entry({"AGENT_ROLE": "review", "AGENT_MOUNT_MODE": "overlay",
                        "AGENT_OUTBOX": f"{self.project}/out"})
        lines = self.section().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].startswith("- Role:"))
        self.assertTrue(lines[1].startswith("- Project mount:"))
        self.assertTrue(lines[2].startswith("- Outbox:"))
        self.assertTrue(self.claude_md().endswith(".\n"))

    def test_rerun_is_idempotent(self) -> None:
        env = {"AGENT_ROLE": "review", "AGENT_MOUNT_MODE": "ro"}
        self.run_entry(env)
        first = self.claude_md()
        self.run_entry(env)
        self.assertEqual(self.claude_md(), first)
        self.assertEqual(first.count(HEADING), 1)

    def test_unknown_mode_warns_on_stderr(self) -> None:
        proc = self.run_entry({"AGENT_MOUNT_MODE": "tmpfs", "AGENT_ROLE": "x"})
        self.assertIn("unknown AGENT_MOUNT_MODE=tmpfs", proc.stderr)
        self.assertEqual(self.section(), "- Role: `x`.\n")

    def test_missing_base_warns_and_keeps_claude_md(self) -> None:
        (self.share / "CLAUDE.base.md").unlink()
        proc = self.run_entry({"AGENT_ROLE": "x"})
        self.assertIn("CLAUDE.base.md missing", proc.stderr)
        self.assertEqual(self.claude_md(), BASE)

    def test_execs_its_arguments(self) -> None:
        marker = self.project / "ran"
        self.run_entry({"AGENT_ROLE": "x"}, ("touch", str(marker)))
        self.assertTrue(marker.exists())


if __name__ == "__main__":
    unittest.main()
