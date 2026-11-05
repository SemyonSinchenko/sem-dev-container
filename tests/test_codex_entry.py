"""Tests for codex/codex-dev-entry and codex/codex-wrapper outside the image.

The entry renders /etc/codex/managed_config.toml: developer_instructions are
APPEND_SYSTEM.md plus, for multi-agent runs, the same "This run" section
claude-dev-entry writes into CLAUDE.md; the working directory is trusted. It
validates CODEX_PERMISSION_MODE and exports it (and INITIAL_AGENT_MODE for
codex-acp). Nothing may ever reach stdout: the entry fronts the ACP stdio
server. The wrapper maps the mode to `-c` overrides in front of the real
codex. CODEX_ENTRY_ETC_DIR / CODEX_ENTRY_SHARE_DIR / CODEX_WRAPPER_REAL_BIN
point the scripts at a temp tree instead of the image paths.

Run with:  make test-bins
       or:  python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "codex" / "codex-dev-entry"
WRAPPER = ROOT / "codex" / "codex-wrapper"
CLAUDE_ENTRY = ROOT / "claude" / "claude-dev-entry"
BASE = (ROOT / "APPEND_SYSTEM.md").read_text()
HEADING = "## This run (overrides the mount description above)"


class CodexEntryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.etc = self.root / "etc"
        self.share = self.root / "share"
        self.etc.mkdir()
        self.share.mkdir()
        (self.share / "APPEND_SYSTEM.md").write_text(BASE)
        self.project = self.root / "project"
        self.project.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def clean_env(self) -> dict[str, str]:
        return {k: v for k, v in os.environ.items()
                if not k.startswith(("AGENT_", "CODEX_", "INITIAL_AGENT_MODE"))}

    def run_entry(self, env: dict[str, str] | None = None,
                  command: tuple[str, ...] = ("true",),
                  cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        full = self.clean_env()
        full.update(CODEX_ENTRY_ETC_DIR=str(self.etc),
                    CODEX_ENTRY_SHARE_DIR=str(self.share))
        full.update(env or {})
        proc = subprocess.run(["bash", str(ENTRY), *command], cwd=cwd or self.project,
                              env=full, text=True, capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "", "the entry must never write to stdout")
        return proc

    def config(self) -> dict:
        return tomllib.loads((self.etc / "managed_config.toml").read_text())

    def instructions(self) -> str:
        return self.config()["developer_instructions"]

    def exported(self, env: dict[str, str] | None = None) -> dict[str, str]:
        out = self.root / "env.out"
        self.run_entry(env, ("sh", "-c", f'env > "{out}"'))
        return dict(line.split("=", 1) for line in out.read_text().splitlines()
                    if "=" in line)

    # --- managed config ---------------------------------------------------------

    def test_default_render(self) -> None:
        self.run_entry()
        cfg = self.config()
        self.assertEqual(cfg["developer_instructions"], BASE.rstrip("\n") + "\n")
        self.assertNotIn(HEADING, cfg["developer_instructions"])
        self.assertEqual(cfg["projects"], {str(self.project): {"trust_level": "trusted"}})
        self.assertIs(cfg["check_for_update_on_startup"], False)
        self.assertEqual(cfg["cli_auth_credentials_store"], "file")
        # Sandbox/approval keys in managed config become locking requirements.
        for key in ("sandbox_mode", "approval_policy", "approvals_reviewer"):
            self.assertNotIn(key, cfg)

    def test_explicit_rw_alone_adds_nothing(self) -> None:
        self.run_entry({"AGENT_MOUNT_MODE": "rw"})
        self.assertNotIn(HEADING, self.instructions())

    def test_section_matches_claude_entry(self) -> None:
        cases = [
            {"AGENT_ROLE": "review"},
            {"AGENT_MOUNT_MODE": "ro"},
            {"AGENT_MOUNT_MODE": "overlay", "AGENT_OUTBOX": f"{self.project}/.loop/t1"},
            {"AGENT_OUTBOX": f"{self.project}/out"},
            {"AGENT_ROLE": "impl", "AGENT_MOUNT_MODE": "tmpfs"},
        ]
        claude_etc = self.root / "claude-etc"
        claude_share = self.root / "claude-share"
        claude_etc.mkdir()
        claude_share.mkdir()
        (claude_share / "CLAUDE.base.md").write_text(BASE)
        for env in cases:
            with self.subTest(env=env):
                self.run_entry(env)
                full = self.clean_env()
                full.update(env, CLAUDE_ENTRY_ETC_DIR=str(claude_etc),
                            CLAUDE_ENTRY_SHARE_DIR=str(claude_share))
                subprocess.run(["bash", str(CLAUDE_ENTRY), "true"], cwd=self.project,
                               env=full, check=True, capture_output=True)
                claude_md = (claude_etc / "CLAUDE.md").read_text()
                self.assertEqual(self.instructions(), claude_md)
                self.assertEqual(self.instructions().count(HEADING), 1)

    def test_rerun_is_idempotent(self) -> None:
        env = {"AGENT_ROLE": "review", "AGENT_MOUNT_MODE": "ro"}
        self.run_entry(env)
        first = (self.etc / "managed_config.toml").read_text()
        self.run_entry(env)
        self.assertEqual((self.etc / "managed_config.toml").read_text(), first)

    def test_project_path_is_escaped(self) -> None:
        odd = self.root / 'we"ird\\dir'
        odd.mkdir()
        self.run_entry(cwd=odd)
        self.assertEqual(self.config()["projects"], {str(odd): {"trust_level": "trusted"}})

    def test_missing_base_warns_and_drops_instructions(self) -> None:
        (self.share / "APPEND_SYSTEM.md").unlink()
        proc = self.run_entry({"AGENT_ROLE": "x"})
        self.assertIn("APPEND_SYSTEM.md missing", proc.stderr)
        self.assertNotIn("developer_instructions", self.config())
        self.assertIn("projects", self.config())

    def test_toml_delimiter_in_base_is_refused(self) -> None:
        (self.share / "APPEND_SYSTEM.md").write_text("bad ''' text\n")
        proc = self.run_entry()
        self.assertIn("cannot be written as TOML", proc.stderr)
        self.assertNotIn("developer_instructions", self.config())

    # --- permission mode --------------------------------------------------------

    def test_mode_default_and_explicit(self) -> None:
        env = self.exported()
        self.assertEqual(env["CODEX_PERMISSION_MODE"], "agent-full-access")
        self.assertEqual(env["INITIAL_AGENT_MODE"], "agent-full-access")
        for mode in ("read-only", "workspace-write", "agent", "agent-full-access"):
            with self.subTest(mode=mode):
                env = self.exported({"CODEX_PERMISSION_MODE": mode})
                self.assertEqual(env["CODEX_PERMISSION_MODE"], mode)
                self.assertEqual(env["INITIAL_AGENT_MODE"], mode)

    def test_unknown_mode_falls_back_to_read_only(self) -> None:
        proc = self.run_entry({"CODEX_PERMISSION_MODE": "yolo"})
        self.assertIn("unknown CODEX_PERMISSION_MODE=yolo", proc.stderr)
        env = self.exported({"CODEX_PERMISSION_MODE": "yolo"})
        self.assertEqual(env["INITIAL_AGENT_MODE"], "read-only")

    def test_execs_its_arguments(self) -> None:
        marker = self.project / "ran"
        self.run_entry({"AGENT_ROLE": "x"}, ("touch", str(marker)))
        self.assertTrue(marker.exists())

    # --- wrapper ----------------------------------------------------------------

    def wrapper_args(self, mode: str | None) -> list[str]:
        fake = self.root / "real-codex"
        fake.write_text('#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done\n')
        fake.chmod(0o755)
        env = self.clean_env()
        env["CODEX_WRAPPER_REAL_BIN"] = str(fake)
        if mode is not None:
            env["CODEX_PERMISSION_MODE"] = mode
        proc = subprocess.run(["bash", str(WRAPPER), "resume", "--last"], env=env,
                              text=True, capture_output=True, check=True)
        self.assertEqual(proc.stderr, "")
        return proc.stdout.splitlines()

    def test_wrapper_maps_modes_to_overrides(self) -> None:
        expected = {
            None: ("danger-full-access", "never", "user"),
            "agent-full-access": ("danger-full-access", "never", "user"),
            "agent": ("workspace-write", "on-request", "auto_review"),
            "workspace-write": ("workspace-write", "on-request", "user"),
            "read-only": ("read-only", "on-request", "user"),
            "bogus": ("read-only", "on-request", "user"),
        }
        for mode, (sandbox, approval, reviewer) in expected.items():
            with self.subTest(mode=mode):
                self.assertEqual(self.wrapper_args(mode), [
                    "-c", f'sandbox_mode="{sandbox}"',
                    "-c", f'approval_policy="{approval}"',
                    "-c", f'approvals_reviewer="{reviewer}"',
                    "resume", "--last",
                ])


if __name__ == "__main__":
    unittest.main()
