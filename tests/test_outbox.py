"""Dry-run tests for --outbox (MULTI_AGENT_ROLES_PLAN.md §6.4–6.5) in
bins/run-claude and bins/run-dsh.

The outbox is one project subdir, created on the host, bind-mounted rw at the
same absolute path after the project mount, the DSH state bind and the .git
remount, and before the /target volume; AGENT_OUTBOX names it. Paths outside
the project, the project itself, and the git/state dirs are rejected.

Run with:  make test-bins
       or:  python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BINS = Path(__file__).resolve().parents[1] / "bins"
LAUNCHERS = ("run-claude", "run-dsh")

PASSTHROUGH_KEYS = (
    "ZAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "GEMINI_API_KEY",
    "XAI_API_KEY",
    "PRIME_API_KEY",
)


class OutboxTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self._tmp.name))
        home = self.root / "home"
        home.mkdir()
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / ".git").mkdir()
        stubs = self.root / "stub-bin"
        stubs.mkdir()
        for engine in ("podman", "docker"):
            stub = stubs / engine
            stub.write_text("#!/bin/sh\nexit 0\n")
            stub.chmod(0o755)
        env = dict(os.environ, HOME=str(home))
        env["PATH"] = f"{stubs}{os.pathsep}{env.get('PATH', '')}"
        for key in PASSTHROUGH_KEYS:
            env.pop(key, None)
        self.env = env

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def launch(self, launcher: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(BINS / launcher), "--dry-run", *args],
            cwd=self.project, env=self.env, stdin=subprocess.DEVNULL,
            text=True, capture_output=True)

    def tokens(self, launcher: str, *args: str) -> list[str]:
        proc = self.launch(launcher, *args)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return shlex.split(proc.stdout)

    @staticmethod
    def values(tokens: list[str], option: str) -> list[str]:
        return [tokens[i + 1] for i, t in enumerate(tokens) if t == option]

    def test_outbox_created_mounted_and_exported(self) -> None:
        out = self.project / ".loop" / "chg-17"
        for launcher in LAUNCHERS:
            for spelling in (".loop/chg-17", "./.loop/../.loop/chg-17", str(out)):
                with self.subTest(launcher=launcher, spelling=spelling):
                    tokens = self.tokens(launcher, "--outbox", spelling)
                    self.assertTrue(out.is_dir())
                    self.assertIn(f"{out}:{out}:z", self.values(tokens, "-v"))
                    self.assertIn(f"AGENT_OUTBOX={out}", self.values(tokens, "-e"))

    def test_outbox_mount_order(self) -> None:
        out = self.project / ".loop" / "t1"
        git = self.project / ".git"
        dsh_state = self.project / ".dsh"
        for launcher in LAUNCHERS:
            for mode in ("rw", "ro", "overlay"):
                with self.subTest(launcher=launcher, mode=mode):
                    mounts = self.values(
                        self.tokens(launcher, "--mount", mode, "--outbox", ".loop/t1"), "-v")
                    i_out = mounts.index(f"{out}:{out}:z")
                    i_git = mounts.index(f"{git}:{git}:ro,z")
                    i_vol = next(i for i, m in enumerate(mounts) if m.endswith(":/target"))
                    self.assertEqual(i_out, i_git + 1)
                    self.assertEqual(i_vol, i_out + 1)
                    self.assertEqual(i_vol, len(mounts) - 1)
                    if launcher == "run-dsh" and mode != "rw":
                        self.assertLess(mounts.index(f"{dsh_state}:{dsh_state}:z"), i_out)

    def test_outbox_no_selinux(self) -> None:
        out = self.project / "out"
        for launcher in LAUNCHERS:
            with self.subTest(launcher=launcher):
                mounts = self.values(
                    self.tokens(launcher, "--no-selinux", "--outbox", "out"), "-v")
                self.assertIn(f"{out}:{out}", mounts)

    def test_outbox_absent_without_flag(self) -> None:
        for launcher in LAUNCHERS:
            with self.subTest(launcher=launcher):
                envs = self.values(self.tokens(launcher), "-e")
                self.assertFalse(any(e.startswith("AGENT_OUTBOX=") for e in envs))

    def test_outbox_contents_untouched(self) -> None:
        out = self.project / "out"
        out.mkdir()
        (out / "verdict.json").write_text("{}")
        self.tokens("run-claude", "--outbox", "out")
        self.assertEqual((out / "verdict.json").read_text(), "{}")

    def test_rejected_outboxes(self) -> None:
        outside = self.root / "elsewhere"
        outside.mkdir()
        (self.project / "escape").symlink_to(outside)
        (self.project / "afile").write_text("x")
        cases = {
            ".": "not the project itself",
            str(self.project): "not the project itself",
            "..": "outside the project",
            "../elsewhere": "outside the project",
            str(outside / "x"): "outside the project",
            "escape/x": "outside the project",
            ".git": "must not be inside",
            ".git/loop": "must not be inside",
            ".claude-container/x": "must not be inside",
            ".dsh": "must not be inside",
            ".dsh/roles/review/out": "must not be inside",
            "afile": "is not a directory",
        }
        for launcher in LAUNCHERS:
            for path, reason in cases.items():
                with self.subTest(launcher=launcher, path=path):
                    proc = self.launch(launcher, f"--outbox={path}")
                    self.assertEqual(proc.returncode, 2, proc.stdout)
                    self.assertEqual(proc.stdout, "")
                    self.assertIn(f"{launcher}: error: --outbox {path}:", proc.stderr)
                    self.assertIn(reason, proc.stderr)
        # Nothing was created outside the project by a rejected run.
        self.assertFalse((outside / "x").exists())

    def test_symlink_inside_project_is_fine(self) -> None:
        real = self.project / "results"
        real.mkdir()
        (self.project / "link").symlink_to(real)
        tokens = self.tokens("run-claude", "--outbox", "link/t1")
        out = real / "t1"
        self.assertTrue(out.is_dir())
        self.assertIn(f"{out}:{out}:z", self.values(tokens, "-v"))
        self.assertIn(f"AGENT_OUTBOX={out}", self.values(tokens, "-e"))

    def test_acp_invariants_with_outbox(self) -> None:
        for launcher, entry in (("run-claude", "claude-acp-entry"),
                                ("run-dsh", "dsh-acp-entry")):
            with self.subTest(launcher=launcher):
                tokens = self.tokens(launcher, "--acp", "--mount", "overlay",
                                     "--outbox", ".loop/t1", "--role", "review")
                self.assertEqual(tokens[:4], ["podman", "run", "--rm", "-i"])
                self.assertEqual(tokens[-1], entry)


if __name__ == "__main__":
    unittest.main()
